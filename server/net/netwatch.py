"""#3064 (N4, N8): השרת מקשיב לרשת ההפצה ורושם **מה שאינו שלנו** -- בלי לגעת בסוויץ'.

**האזנה פסיבית, אותה שכבה כמו `rogue_dhcp`:** AF_PACKET עם מסנן BPF בקרנל. המסנן **זורק כל מסגרת שיצאה מהכרטיס או
שנועדה לו**, ולכן הזרם שהשרת עצמו משדר (80 אלף חבילות בשנייה) ותעבורת ה-HTTP וה-DHCP שלו לעולם אינם מגיעים לפייתון.
מה שנשאר הוא בדיוק מה שאינו אנחנו: שידור, מולטיקאסט, והצפת יוניקאסט של הסוויץ'. מונה הקרנל (`PACKET_STATISTICS`) אומר
כמה עברו את המסנן, וקוראים בפייתון לכל היותר `MAX_SAMPLE` מסגרות לפרוסה -- הצפה אינה שורפת מעבד.

**מה זה נותן:** N8 (כמה תעבורה אחרת מגיעה לפורט של השרת, ומאילו MAC) ובנוסף רישום של N4 (`Querier.sense`, יש querier?).
N6 (הצפה לפורט שלא ביקש) אינו נראה מהשרת: הפורט שלו הוא זה ששולח. את N6 רואה רק המשכפל.

**מצב צל:** הכול נרשם דרך `l2sense.record` (``brain_decisions``, ``mode=shadow``), רק כשהמצב השתנה, ואיש אינו נשען עליו.
**עיקרון 5:** האזנה שנכשלה (אין הרשאה, הכרטיס לא קיים) היא `not_checked`, לא `quiet`. `quiet` = האזנה שהצליחה
ואפס מסגרות זרות עברו את המסנן.
"""

from __future__ import annotations

import collections
import ctypes
import logging
import socket
import struct
import threading
import time
from pathlib import Path
from typing import Callable

from server.brain import l2frames, l2sense
from server.net import dhcp_host

log = logging.getLogger("imagesrv.netwatch")

SLICE_SECONDS = 60.0
#: כמה פרוסות נשמרות ונספרות יחד (10 דקות).
KEEP = 10
#: מסגרות שנקראות בפייתון לפרוסה. מעבר לזה הקרנל סופר ואיננו קורא.
MAX_SAMPLE = 4000
RETRY_SECONDS = 10.0
GATE_RECHECK = 30.0
#: כמה MAC-ים נשמרים לפרוסה, וכמה מדווחים.
MAX_MACS = 16
TOP_MACS = 4
TOP_GROUPS = 8
RCVBUF = 131072
SNAP = 256

# מסנן "לא אליי ולא ממני". אותו מסנן כמו `prog_b` ב-`agent/netwatch.c`.
_LD_W, _LD_H, _JEQ, _JA, _RET = 0x20, 0x28, 0x15, 0x05, 0x06


def program(mac: bytes) -> list[tuple[int, int, int, int]]:
    """(code, jt, jf, k) -- הקפיצות יחסיות לפקודה הבאה. drop=9, accept=10."""
    d03 = int.from_bytes(mac[0:4], "big")
    d45 = int.from_bytes(mac[4:6], "big")
    return [
        (_LD_W, 0, 0, 0),        # 0 dst[0..3]
        (_JEQ, 0, 3, d03),       # 1 -> 2 | 5
        (_LD_H, 0, 0, 4),        # 2 dst[4..5]
        (_JEQ, 5, 0, d45),       # 3 -> 9 (drop) | 4
        (_JA, 0, 0, 0),          # 4 -> 5
        (_LD_W, 0, 0, 6),        # 5 src[0..3]
        (_JEQ, 0, 3, d03),       # 6 -> 7 | 10
        (_LD_H, 0, 0, 10),       # 7 src[4..5]
        (_JEQ, 0, 1, d45),       # 8 -> 9 (drop) | 10
        (_RET, 0, 0, 0),         # 9
        (_RET, 0, 0, SNAP),      # 10
    ]


def read_mac(interface: str, sys_net: str | Path = "/sys/class/net") -> bytes:
    """ה-MAC של הכרטיס. כרטיס שאינו קיים או כתובת שאינה MAC -- OSError/ValueError: "לא הצלחנו להאזין"."""
    text = Path(sys_net, interface, "address").read_text().strip()
    mac = bytes.fromhex(text.replace(":", ""))
    if len(mac) != 6:
        raise ValueError(f"כתובת הכרטיס {interface} אינה MAC: {text!r}")
    return mac


def open_tap(interface: str, mac: bytes) -> socket.socket:
    """סוקט AF_PACKET על הכרטיס בלבד, עם המסנן. protocol=0 ביצירה: כלום לא נכנס לפני שהמסנן והכרטיס נקבעו."""
    tap = socket.socket(dhcp_host.AF_PACKET, socket.SOCK_RAW, 0)
    try:
        ops = program(mac)
        buffer = ctypes.create_string_buffer(b"".join(struct.pack("HBBI", *op) for op in ops))
        tap.setsockopt(socket.SOL_SOCKET, dhcp_host.SO_ATTACH_FILTER, struct.pack("HP", len(ops), ctypes.addressof(buffer)))
        tap.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RCVBUF)
        tap.bind((interface, dhcp_host.ETH_P_ALL))
    except BaseException:
        tap.close()
        raise
    return tap


class Slice:
    """פרוסה אחת: מה שנספר, בצורת ה-JSON של `agent/netwatch.c` (כדי ש-`l2sense.n8` יקרא את שניהם)."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.passed: int | None = None
        self.drops: int | None = None
        self.sampled = 0
        self.classes = dict.fromkeys(l2frames.CLASSES, 0)
        self.groups: collections.Counter[str] = collections.Counter()
        self.macs: collections.Counter[str] = collections.Counter()
        self.macs_more = 0

    def add(self, frame: bytes) -> None:
        cls, group = l2frames.classify(frame)
        self.sampled += 1
        self.classes[cls] += 1
        if group is not None:
            self.groups[group] += 1
        mac = l2frames.source_mac(frame)
        if mac is not None:
            if mac in self.macs or len(self.macs) < MAX_MACS:
                self.macs[mac] += 1
            else:
                self.macs_more += 1


def combine(slices: list[Slice]) -> dict | None:
    """הפרוסות שנשמרו כאובייקט אחד (צורת `netwatch.c`). ``None`` = אין אף פרוסה שהסתיימה."""
    if not slices:
        return None
    classes = dict.fromkeys(l2frames.CLASSES, 0)
    groups: collections.Counter[str] = collections.Counter()
    macs: collections.Counter[str] = collections.Counter()
    for s in slices:
        for k, v in s.classes.items():
            classes[k] += v
        groups.update(s.groups)
        macs.update(s.macs)
    # מונה הקרנל שנכשל בפרוסה אחת = "לא נקרא" לכולן (עיקרון 5): סכום חלקי היה נראה כמו "עבר מעט"
    read = all(s.passed is not None for s in slices)
    return {"v": 1, "window_s": int(sum(s.seconds for s in slices)), "promisc": False,
            "passed": sum(s.passed for s in slices) if read else None,
            "kernel_drops": sum(s.drops for s in slices) if read else None,
            "sampled": sum(s.sampled for s in slices), "classes": classes,
            "groups": [{"group": g, "n": n} for g, n in groups.most_common(TOP_GROUPS)],
            "macs": [{"mac": m, "n": n} for m, n in macs.most_common(TOP_MACS)]}


class Watch:
    """האזנה לכרטיס אחד. `run_forever` בתהליכון daemon (main בלבד).

    `gate()` -- None כשצריך להאזין, אחרת הסיבה בעברית. `querier` -- ה-`Querier` של אותו כרטיס, ל-N4."""

    def __init__(self, conn, interface: str, *, gate: Callable[[], str | None] = lambda: None, querier=None,
                 engine_group: Callable[[], str | None] = lambda: None,
                 mac: Callable[[], bytes] | None = None, opener=open_tap, clock=time.monotonic) -> None:
        self.conn, self.interface, self.gate, self.querier = conn, interface, gate, querier
        self.engine_group = engine_group
        self.mac = mac or (lambda: read_mac(interface))
        self.opener, self.clock = opener, clock
        self.slices: collections.deque[Slice] = collections.deque(maxlen=KEEP)
        self.refused: str | None = None
        self.error: str | None = None
        self._tap = None

    # --- הלולאה -------------------------------------------------------------

    def step(self, stop: threading.Event) -> None:
        """פרוסה אחת: שער, סוקט, קריאה חוסמת עד סוף הפרוסה או עד `MAX_SAMPLE`, ורישום."""
        self.refused = self.gate()
        if self.refused is not None:
            self.close()
            self.error = None
            stop.wait(GATE_RECHECK)
            return
        if self._tap is None:
            self._tap = self.opener(self.interface, self.mac())
        self.error = None
        sl = Slice(SLICE_SECONDS)
        started = self.clock()
        end = started + SLICE_SECONDS
        while not stop.is_set() and sl.sampled < MAX_SAMPLE and self.clock() < end:
            self._tap.settimeout(max(end - self.clock(), 0.001))
            try:
                frame = self._tap.recv(65535)
            except (socket.timeout, TimeoutError):
                break
            sl.add(frame)
        if not stop.is_set():
            stop.wait(max(end - self.clock(), 0.0))     # הצפה: הקרנל סופר, אנחנו לא קוראים
        sl.seconds = self.clock() - started
        sl.passed, sl.drops = self._read_stats()
        self.slices.append(sl)
        self.record()

    def _read_stats(self) -> tuple[int | None, int | None]:
        try:
            packets, drops = struct.unpack("II", self._tap.getsockopt(dhcp_host.SOL_PACKET, dhcp_host.PACKET_STATISTICS, 8))
        except (OSError, struct.error):
            return None, None
        return packets, drops

    def close(self) -> None:
        tap, self._tap = self._tap, None
        if tap is not None:
            tap.close()

    def run_forever(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        while not stop.is_set():
            try:
                self.step(stop)
            except Exception as exc:  # noqa: BLE001 -- תהליכון רקע: נאמר, לא נבלע
                self.close()
                if str(exc) != self.error:
                    log.warning("netwatch on %s: %s", self.interface, exc)
                self.error = str(exc) or type(exc).__name__
                self.record()
                stop.wait(RETRY_SECONDS)
        self.close()

    # --- החושים -------------------------------------------------------------

    def senses(self) -> list[l2sense.Sense]:
        group = self.engine_group()
        out = []
        if self.querier is not None:
            out.append(self.querier.sense())
        if self.refused is not None:
            out.append(l2sense.Sense("N8", l2sense.NOT_CHECKED, f"השרת: ההאזנה כבויה: {self.refused}", {}))
        elif self.error is not None:
            out.append(l2sense.Sense("N8", l2sense.NOT_CHECKED, f"השרת: ההאזנה ל-{self.interface} נכשלה: {self.error}", {}))
        else:
            data = combine(list(self.slices))
            out.append(l2sense.n8(data, engine_group=group, who="השרת") if data is not None else
                       l2sense.Sense("N8", l2sense.NOT_CHECKED, "השרת: עוד לא הסתיימה פרוסת האזנה", {}))
        return out

    def record(self) -> None:
        try:
            for sense in self.senses():
                l2sense.record(self.conn, sense, ("network", self.interface))
        except Exception:  # noqa: BLE001 -- חוש שנפל אינו מפיל את ההאזנה
            log.exception("netwatch: the senses of %s were not recorded", self.interface)
