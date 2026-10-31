"""‏UPS דרך NUT — יומן בנפילת חשמל, כיבוי מסודר בסוללה נמוכה (‏#1973, נדב 22/10).

**שלושה מצבים, מהקונסולה:** אין UPS · ‏USB (ה-UPS מחובר לשרת; השרת מריץ את
הדרייבר ואת upsd של NUT על loopback) · רשת (שרת NUT אחר מחזיק את ה-UPS;
השרת רק קורא ממנו). בשני האחרונים השרת קורא את המשתנים **בפרוטוקול של NUT**
(‏`LIST VAR`, ‏TCP ‏3493) — אותו פרוטוקול ש-`upsc` מדבר, בלי לפרסר פלט של כלי.

**מה קורה:**

* ‏`OB` (על סוללה) — שורה ביומן: החשמל נפל. חזרה ל-`OL` — שורה: החשמל חזר.
* ‏`OB`+`LB` (סוללה נמוכה בזמן שהחשמל נפל), או ‏`FSD` (כיבוי כפוי מהשרת
  שמחזיק את ה-UPS) — עוצרים **בגלוי** את מה שרץ (‏`stop_all_work`: סבב
  שידור/משיכה נכשל עם הסיבה, סבב חדר נכשל, קליטה רצה נכשלת), ורק אז
  ‏`systemctl poweroff`.
* ‏`LB` בלי `OB` (הסוללה נטענת אחרי הפסקה) — אינו מכבה.

**עיקרון 5.** כיבוי רק על ראיה חיובית — קריאה שהצליחה ונשאה `LB`/`FSD`.
‏UPS שלא נקרא **אינו** מכבה את השרת: הוא שורה אדומה במסך הבריאות ושורה
ביומן במעבר, לא שקט. "לא נקרא" ו"על חשמל" הם שני מצבים.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from server.db import _write_lock, get_setting, journal, now_iso, set_settings, update_one, writing

log = logging.getLogger("imagesrv.ups")

MODE, TARGET = "ups_mode", "ups_target"
MODES = ("none", "usb", "net")
MODES_HE = {"none": "אין UPS", "usb": "USB — מחובר לשרת", "net": "רשת — שרת NUT"}
NUT_PORT = 3493
#: שם ה-UPS כשהוא מחובר לשרת עצמו (‏ups.conf שהשרת כותב).
USB_NAME = "imagesrv"
USB_TARGET = f"{USB_NAME}@127.0.0.1"
POLL_SECONDS = 10.0
READ_TIMEOUT = 5.0
NUT_DIR = Path("/etc/nut")
#: עדיין "אין שינוי" אחרי כמה זמן מהחלת USB — הדרייבר עולה בכמה שניות.
USB_SETTLE_SECONDS = 20

_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_HOST = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")
_VAR = re.compile(r'^VAR (\S+) (\S+) "(.*)"$')


class UpsError(RuntimeError):
    """הקריאה מה-UPS לא הצליחה — הסיבה בשמה."""


@dataclass(frozen=True)
class Target:
    name: str
    host: str
    port: int = NUT_PORT

    def __str__(self) -> str:
        port = "" if self.port == NUT_PORT else f":{self.port}"
        return f"{self.name}@{self.host}{port}"


def parse_target(value: object) -> Target:
    """‏`ups@host[:port]` — כמו ש-`upsc` מקבל. ‏ValueError עם הסיבה בעברית."""
    text = str(value or "").strip()
    name, at, rest = text.partition("@")
    if not at or not _NAME.match(name):
        raise ValueError("כתובת UPS: שם@שרת, למשל ups@10.0.0.5")
    host, colon, port = rest.rpartition(":") if rest.count(":") == 1 else (rest, "", "")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not _HOST.match(host):
            raise ValueError(f"כתובת UPS: שרת לא תקין: {host or '(ריק)'}") from None
    number = NUT_PORT
    if colon:
        if not port.isdigit() or not 0 < int(port) < 65536:
            raise ValueError(f"כתובת UPS: פורט לא תקין: {port}")
        number = int(port)
    return Target(name, host, number)


def configured(conn) -> tuple[str, Target | None]:
    """המצב והיעד השמורים. שמור פגום — "none" ו-None לא נקראים כאן כ"אין":
    הקורא (`UpsMonitor`) רואה מצב לא-none בלי יעד, ומדווח."""
    mode = get_setting(conn, MODE) or "none"
    if mode not in MODES:
        mode = "none"
    if mode == "usb":
        return mode, parse_target(USB_TARGET)
    if mode == "net":
        try:
            return mode, parse_target(get_setting(conn, TARGET))
        except ValueError:
            return mode, None
    return mode, None


def read_vars(target: Target, timeout: float = READ_TIMEOUT,
              connect=socket.create_connection) -> dict[str, str]:
    """‏`LIST VAR <ups>` מול upsd. ‏UpsError על כל תשובה שאינה רשימה שלמה."""
    try:
        sock = connect((target.host, target.port), timeout)
    except OSError as exc:
        raise UpsError(f"אין חיבור ל-{target.host}:{target.port}: {exc}") from exc
    with sock:
        try:
            sock.sendall(f"LIST VAR {target.name}\n".encode("ascii"))
            stream = sock.makefile("r", encoding="utf-8", errors="replace", newline="\n")
            first = stream.readline().rstrip("\n")
            if first.startswith("ERR "):
                raise UpsError(f"upsd ענה {first[4:]} על {target.name}")
            if first != f"BEGIN LIST VAR {target.name}":
                raise UpsError(f"תשובה לא צפויה מ-upsd: {first[:80]!r}")
            values: dict[str, str] = {}
            for line in stream:
                line = line.rstrip("\n")
                if line == f"END LIST VAR {target.name}":
                    try:
                        sock.sendall(b"LOGOUT\n")
                    except OSError:
                        pass
                    return values
                match = _VAR.match(line)
                if match and match.group(1) == target.name:
                    values[match.group(2)] = match.group(3).replace('\\"', '"')
        except OSError as exc:
            raise UpsError(f"הקריאה מ-{target.host} נקטעה: {exc}") from exc
    raise UpsError("upsd סגר את החיבור לפני סוף הרשימה")


@dataclass(frozen=True)
class Reading:
    status: tuple[str, ...]
    charge: int | None
    runtime_s: int | None

    @property
    def on_battery(self) -> bool:
        return "OB" in self.status

    @property
    def low(self) -> bool:
        """כיבוי: סוללה נמוכה **בזמן** שהחשמל נפל, או FSD מהשרת שמחזיק את ה-UPS."""
        return ("OB" in self.status and "LB" in self.status) or "FSD" in self.status


def _int(value: str | None) -> int | None:
    try:
        return int(float(value)) if value is not None else None
    except ValueError:
        return None


def reading(values: dict[str, str]) -> Reading:
    raw = values.get("ups.status")
    if not raw:
        raise UpsError("ה-UPS לא דיווח ups.status — אין ראיה על מצב החשמל")
    return Reading(tuple(raw.split()), _int(values.get("battery.charge")),
                   _int(values.get("battery.runtime")))


def describe(r: Reading) -> str:
    power = "על סוללה — החשמל נפל" if r.on_battery else "על חשמל"
    parts = [power]
    if r.charge is not None:
        parts.append(f"סוללה {r.charge}%")
    if r.runtime_s is not None:
        parts.append(f"~{max(1, r.runtime_s // 60)} דק' בסוללה")
    if "RB" in r.status:
        parts.append("ה-UPS מבקש להחליף סוללה")
    return " · ".join(parts)


# --- עצירת העבודה וכיבוי -----------------------------------------------------------

def stop_all_work(ctx, reason: str) -> list[str]:
    """עוצר **בגלוי** כל מה שרץ: כל אחד נכשל עם `reason`, ונרשם ביומן."""
    from server.rounds import room                     # noqa: PLC0415 — room מייבא הרבה
    conn = ctx.conn
    stopped: list[str] = []
    rows = conn.execute(
        "SELECT id, kind FROM sessions WHERE state IN ('open', 'running')").fetchall()
    for row in rows:
        sid = row["id"]
        if ctx.store.fail(sid, reason,
                          related=lambda sid=sid: room.fail_wave(conn, sid, reason, commit=False)):
            stopped.append(sid)
            journal(conn, "ups_work_stopped", f"{sid}: {reason}")
        if row["kind"] == "multicast":
            ctx.sender.stop(sid)
    for table, where, column in (("room_rounds", "state IN ('active', 'closing')", "failed_reason"),
                                 ("tasks", "state = 'running'", "error")):
        ids = [r["id"] for r in conn.execute(f"SELECT id FROM {table} WHERE {where}")]
        for item in ids:
            extra = ", closed_at = ?" if table == "room_rounds" else ", updated_at = ?"
            with _write_lock, writing(conn):    # #54: no orphaned write lock on a failed write
                done = update_one(conn, f"UPDATE {table} SET state = 'failed', {column} = ?{extra}"
                                        f" WHERE id = ? AND {where}", (reason, now_iso(), item))
            if done:
                stopped.append(item)
                journal(conn, "ups_work_stopped", f"{item}: {reason}")
    return stopped


def default_poweroff() -> None:
    subprocess.run(["systemctl", "poweroff"], check=True, timeout=30,
                   stdin=subprocess.DEVNULL, capture_output=True)


class UpsMonitor:
    """דוגם את ה-UPS כל `POLL_SECONDS`; רושם מעברים; מכבה בסוללה נמוכה."""

    def __init__(self, conn, *, stop_work: Callable[[str], list[str]],
                 poweroff: Callable[[], None] = default_poweroff,
                 read: Callable[[Target], dict[str, str]] = read_vars,
                 clock=time.monotonic) -> None:
        self.conn, self.stop_work, self.poweroff = conn, stop_work, poweroff
        self.read, self.clock = read, clock
        self.mode = "none"
        self.last: Reading | None = None
        self.read_at: float | None = None
        self.error: str | None = None
        self.shutdown_error: str | None = None
        self.shutting_down = False
        #: התהליכון והקונסולה (‏POST ‎/ups קורא מיד) — דגימה אחת בכל פעם,
        #: כדי שמעבר לא יירשם ביומן פעמיים.
        self._lock = threading.Lock()

    def tick(self) -> None:
        with self._lock:
            self._tick()

    def _tick(self) -> None:
        mode, target = configured(self.conn)
        if mode != self.mode:
            self.mode, self.last, self.error = mode, None, None
        if mode == "none":
            return
        try:
            if target is None:
                raise UpsError("כתובת ה-UPS השמורה אינה תקינה — הגדירו אותה מחדש")
            now = reading(self.read(target))
        except Exception as exc:  # noqa: BLE001 — כל כשל קריאה הוא "לא נקרא", בשמו
            if self.error is None:
                journal(self.conn, "ups_unreachable", f"{target or mode}: {exc}")
            self.error = str(exc)
            return
        if self.error is not None:
            journal(self.conn, "ups_reachable", f"{target}: {describe(now)}")
        self.error, self.read_at = None, self.clock()
        before = self.last
        self.last = now
        if now.on_battery and (before is None or not before.on_battery):
            journal(self.conn, "ups_on_battery", f"{target}: {describe(now)}")
        elif before is not None and before.on_battery and not now.on_battery:
            journal(self.conn, "ups_power_restored", f"{target}: {describe(now)}")
        if now.low:
            self._shutdown(target, now)

    def _shutdown(self, target: Target, now: Reading) -> None:
        reason = f"סוללת ה-UPS נמוכה — השרת נכבה ({describe(now)})"
        if not self.shutting_down:
            journal(self.conn, "ups_low_battery", f"{target}: {' '.join(now.status)} · {describe(now)}")
            self.shutting_down = True
        stopped = self.stop_work(reason)
        journal(self.conn, "ups_shutdown",
                f"נעצרו {len(stopped)}: {', '.join(stopped) or 'לא רץ דבר'} — מכבים")
        try:
            self.poweroff()
        except Exception as exc:  # noqa: BLE001 — כיבוי שנכשל נאמר, ונוסה שוב בדגימה הבאה
            self.shutdown_error = str(exc)
            journal(self.conn, "ups_shutdown_failed", str(exc))

    def run_forever(self, stop: threading.Event | None = None,
                    interval: float = POLL_SECONDS) -> None:
        stop = stop or threading.Event()
        while not stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 — תהליכון רקע: נאמר ביומן השרת
                log.exception("ups monitor tick failed")
            stop.wait(interval)

    def snapshot(self) -> dict:
        return {"mode": self.mode, "error": self.error, "shutdown_error": self.shutdown_error,
                "status": list(self.last.status) if self.last else None,
                "charge": self.last.charge if self.last else None,
                "runtime_s": self.last.runtime_s if self.last else None,
                "text": describe(self.last) if self.last else None,
                "age_s": int(self.clock() - self.read_at) if self.read_at is not None else None}

    def health(self) -> tuple[str, str]:
        mode, target = configured(self.conn)
        if mode == "none":
            return "not_applicable", "לא הוגדר UPS (הגדרות ← כללי)"
        if self.shutdown_error is not None:
            return "bad", f"סוללה נמוכה והכיבוי נכשל: {self.shutdown_error}"
        if mode != self.mode or (self.last is None and self.error is None):
            return "unknown", f"{target or MODES_HE[mode]}: טרם נקרא"
        if self.error is not None:
            return "bad", f"{target or MODES_HE[mode]}: לא נקרא — {self.error}"
        age = int(self.clock() - self.read_at) if self.read_at is not None else None
        if age is not None and age > 6 * POLL_SECONDS:
            return "bad", f"{target}: לא נקרא מזה {age} שניות"
        text = f"{target}: {describe(self.last)}"
        if self.last.low:
            return "bad", text + " · סוללה נמוכה — השרת נכבה"
        if self.last.on_battery or "RB" in self.last.status:
            return "warn", text
        return "ok", text


# --- הגדרה מהקונסולה ---------------------------------------------------------------

def render_usb() -> dict[str, str]:
    head = "# Managed by ImageSrv (#1973) — הגדרות → UPS\n"
    return {"nut.conf": head + "MODE=standalone\n",
            "ups.conf": head + f"[{USB_NAME}]\n\tdriver = usbhid-ups\n\tport = auto\n"
                        "\tdesc = \"ImageSrv UPS\"\n",
            "upsd.conf": head + f"LISTEN 127.0.0.1 {NUT_PORT}\n"}


def _systemctl(*args: str) -> None:
    done = subprocess.run(["systemctl", *args], capture_output=True, text=True, timeout=60,
                          stdin=subprocess.DEVNULL, check=False)
    if done.returncode:
        raise RuntimeError(f"systemctl {' '.join(args)}: {(done.stderr or done.stdout).strip()}")


def default_hooks() -> dict:
    def write(files: dict[str, str]) -> None:
        for name, text in files.items():
            (NUT_DIR / name).write_text(text, encoding="utf-8")

    def start_usb() -> None:
        # ‏nut-driver-enumerator קורא את ups.conf ויוצר nut-driver@imagesrv.
        _systemctl("restart", "nut-driver-enumerator.service")
        _systemctl("enable", "--now", "nut-server.service")
        _systemctl("restart", "nut-server.service")

    def stop_usb() -> None:
        _systemctl("disable", "--now", "nut-server.service")

    return {"write": write, "start_usb": start_usb, "stop_usb": stop_usb,
            "read": read_vars, "sleep": time.sleep}


def apply(conn, mode: str, target_text: object, hooks: dict, user: str) -> dict:
    """מאמת, מחיל (USB: קובצי NUT ושירות), שומר ורושם. מחזיר את הקריאה שאחרי.

    ‏ValueError = סירוב לפני שנגעו בדבר. ‏RuntimeError = ההחלה נכשלה — לא נשמר."""
    if mode not in MODES:
        raise ValueError("UPS: מצב לא מוכר — none, usb או net")
    target = parse_target(target_text) if mode == "net" else None
    old_mode, _ = configured(conn)
    if mode == "usb":
        hooks["write"](render_usb())
        hooks["start_usb"]()
    elif old_mode == "usb":
        hooks["write"]({"nut.conf": "# Managed by ImageSrv (#1973)\nMODE=none\n"})
        hooks["stop_usb"]()
    set_settings(conn, {MODE: mode, TARGET: str(target) if target else ""})
    journal(conn, "ups_config_changed",
            f"{MODES_HE[old_mode]} → {MODES_HE[mode]}" + (f" ({target})" if target else ""), user)
    if mode == "none":
        return {"read": None, "error": None}
    probe = parse_target(USB_TARGET) if mode == "usb" else target
    attempts = USB_SETTLE_SECONDS if mode == "usb" else 1
    error = None
    for attempt in range(attempts):
        try:
            return {"read": describe(reading(hooks["read"](probe))), "error": None}
        except Exception as exc:  # noqa: BLE001 — נאמר למפעיל, לא נבלע
            error = str(exc)
        if attempt + 1 < attempts:
            hooks["sleep"](1)
    return {"read": None, "error": error}
