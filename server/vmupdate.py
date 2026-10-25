"""עדכון אימג' בתוך VM על השרת — המנוע (‏#1334).

מחזור החיים של VM **אחד**: אימג' → דיסק עבודה (`vmdisk.build_disk`) →
‏QEMU/KVM → כיבוי → שמירה כגרסה חדשה (`vmdisk.capture_disk`) או ביטול.
העיצוב, ולמה כך: `docs/vm-update.md`. החוזה: `docs/interfaces.md` סעיף 33.

**הדיסק הוא מקור האמת** (עיקרון 3): כל מה שצריך כדי להמשיך אחרי אתחול
השרת יושב ב-`<ספרייה>/.vmupdate/` — ‏`state.json`, ‏`source-manifest.json`,
‏`disk.raw`. השרת שעולה וממצא שם VM "רץ" יודע שהוא **אינו** רץ (‏QEMU הוא
בן של השרת ומת איתו), ומסמן `interrupted` — הדיסק נשאר, מדליקים שוב או
מבטלים.

**הכרעות נדב 13/10** (‏#1334): מנהל בלבד · הגרסה החדשה = ברירת המחדל לסבב
חדש (`image_versions`) · אחרי כיבוי בכוח מדליקים שוב ומשיכים · 15 דק' לכיבוי
מסודר ואז מוצע כוח · דיסק שלא הוכרע נשמר 7 ימים ונמחק עם שורת יומן ·
מנהל שני צופה בלבד, ו"קח שליטה" היא **בקשה** שהשולט מאשר (60 ש' בלי תשובה
= סירוב; שולט שלא נראה 2 דק' = אישור אוטומטי) · כל גרסה יכולה להיות מקור.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os

import secrets
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import disk_reserve, vmdisk, vmnet
from .atomicfile import write_atomic
from .capture import validate_manifest_files
from .db import get_setting, journal, now_iso, set_setting, set_settings
from .images import (
    ImageLibrary, image_os, lineage_of, streamed_partitions, validate_display_name,
)

log = logging.getLogger("imagectl.vmupdate")

WORK_AREA = ".vmupdate"
GIB = 1 << 30
#: מה שנשאר לשרת עצמו מעבר לזיכרון ה-VM — השידור לכיתה, ה-DB, ה-page cache.
RAM_RESERVE_MIB = 4096
#: מרווח הגדילה של דיסק העבודה בזמן העדכון (Windows Update מוריד ופורס).
GROWTH_RESERVE_BYTES = 30 * GIB
#: הכרעה 4: אחרי כמה זמן בכיבוי מסודר מוצע כיבוי בכוח.
SHUTDOWN_GRACE_S = 15 * 60
#: הכרעה 5: כמה זמן נשמר דיסק עבודה שאיש לא הכריע עליו.
KEEP_UNDECIDED_S = 7 * 24 * 3600
#: הכרעה 6: שולט שלא נראה (‏status/מסך) כל כך הרבה — "איננו".
CONTROLLER_GONE_S = 120
#: הכרעה 6: בקשת שליטה שלא נענתה — סירוב.
REQUEST_TIMEOUT_S = 60
#: כל כמה זמן רץ הניקיון (תפוגת בקשות, תפוגת 7 הימים).
MAINTENANCE_S = 30

#: ‏(מפתח הגדרה, ברירת מחדל, מינימום, מקסימום).
SETTINGS = {
    "memory_mib": ("vmupdate:memory_mib", 16384, 1024, 1 << 20),
    "cpus": ("vmupdate:cpus", 4, 1, 256),
}

OVMF_DIR = Path("/usr/share/OVMF")
OVMF = {
    False: ("OVMF_CODE_4M.fd", "OVMF_VARS_4M.fd"),
    True: ("OVMF_CODE_4M.secboot.fd", "OVMF_VARS_4M.ms.fd"),
}
#: ‏#1468: הרשת של ה-VM — הכרטיס שהמפעיל בחר, ה-MAC שהשרת ייצר פעם אחת
#: (Windows רואה את אותו כרטיס בכל פתיחה, וה-DHCP של המכללה — כתובת שמורה),
#: וה-lease האחרון שנראה (לתצוגה ליד הבחירה).
NIC_KEY = "vmupdate:nic"
MAC_KEY = "vmupdate:mac"
LEASE_KEY = "vmupdate:lease"

#: מצבים שבהם יש תהליך או תהליכון שעובד — אין לגעת בדיסק.
BUSY = ("preparing", "running", "stopping", "saving")
#: מצבים שבהם ה-VM כבוי, הדיסק קיים, ומחכים להכרעה.
UNDECIDED = ("stopped", "failed")


class Refusal(Exception):
    """סירוב בשם: ‏HTTP status, ‏`reason` למכונה, `message` לאדם, ומספרים."""

    def __init__(self, status: int, reason: str, message: str, **numbers):
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.message = message
        self.numbers = numbers


# --- המכונה: מה שנבדק לפני הפעלה. בדיקות מחליפות ---------------------------------


class Machine:
    """שאלות על המארח. כל אחת מחזירה `None` כש**לא ניתן לבדוק** — וזה סירוב
    אצל הקורא, לא "בסדר" (עיקרון 5)."""

    def kvm(self) -> str | None:
        """None = ‏/dev/kvm קיים ונפתח לקריאה-כתיבה; אחרת הסיבה."""
        try:
            fd = os.open("/dev/kvm", os.O_RDWR | os.O_CLOEXEC)
        except FileNotFoundError:
            return "אין /dev/kvm — הווירטואליזציה כבויה ב-BIOS או שהמעבד אינו תומך"
        except OSError as exc:
            return f"/dev/kvm לא נפתח: {exc.strerror}"
        os.close(fd)
        return None

    def mem_available_mib(self) -> int | None:
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
        except (OSError, ValueError, IndexError):
            return None
        return None

    def cpu_count(self) -> int | None:
        return os.cpu_count()

    def free_bytes(self, path: Path) -> int | None:
        try:
            stat = os.statvfs(path)
        except (OSError, AttributeError):
            return None
        return stat.f_bavail * stat.f_frsize

    def which(self, name: str) -> str | None:
        return shutil.which(name, path=os.environ.get("PATH", "") + ":/usr/sbin:/sbin")

    def exists(self, path: Path) -> bool:
        return path.exists()

    def guestfs(self) -> bool:
        return importlib.util.find_spec("guestfs") is not None

    def vm_user(self) -> vmdisk.VmUser | None:
        try:
            import pwd   # noqa: PLC0415 — POSIX בלבד; תחנת הפיתוח היא ווינדוס
            entry = pwd.getpwnam(vmnet.VM_USER)
        except ImportError:
            return None
        except KeyError:
            return None
        return vmdisk.VmUser(entry.pw_uid, entry.pw_gid)

    # -- הרשת של ה-VM (‏#1468, ‏`vmnet`) --

    def firewall(self, uid: int, mac: str) -> str | None:
        return vmnet.apply(uid, mac)

    def firewall_off(self) -> str | None:
        return vmnet.remove()

    def nic_exists(self, name: str) -> bool:
        return vmnet.nic_exists(name)

    def stacked(self, name: str) -> set[str] | None:
        return vmnet.stacked(name)

    def macvtap(self, lower: str, mac: str) -> tuple[int | None, str | None]:
        """(fd פתוח של ‏/dev/tapN, None) או (None, הסיבה). ה-fd עובר ל-QEMU."""
        path, why = vmnet.macvtap_up(lower, mac)
        if path is None:
            return None, why
        try:
            fd = os.open(path, os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
        except OSError as exc:
            vmnet.macvtap_down()
            return None, f"{path} לא נפתח: {exc.strerror}"
        return fd, None

    def close_tap(self, fd: int) -> None:
        os.close(fd)

    def macvtap_down(self) -> str | None:
        return vmnet.macvtap_down()

    def watch_lease(self, mac: str, timeout: float, stop: Callable[[], bool],
                    on_ack: Callable[[str, str | None], None]) -> dict:
        return vmnet.watch_lease(vmnet.TAP_NAME, mac, timeout, stop, on_ack)

    def platform(self) -> str | None:
        return vmnet.detect_virt()


# --- QEMU ---------------------------------------------------------------------


def secure_boot_for(manifest: dict) -> bool:
    """‏SB כבוי רק כשיש **ראיה** שהמטענים אינם חתומים (`boot_ca == []`).
    ‏`null` ("לא נגזר") ורשימה — דלוק: אימג' Windows נבנה למכונה עם SB."""
    return manifest.get("boot_ca") != []


#: ‏`elevateprivileges` אינו כאן בכוונה: ‏`-run-with user=` מוריד הרשאות **אחרי**
#: שהסינון נטען, ו-`deny` חוסם את ה-setuid עצמו (נמדד ב-Testrunner: SIGSYS).
SANDBOX = "on,obsolete=deny,spawn=deny,resourcecontrol=deny"


def qemu_argv(*, workdir: Path, memory_mib: int, cpus: int, accel: str,
              secure_boot: bool, tap_fd: int, mac: str, ovmf_dir: Path = OVMF_DIR,
              run_as: str = vmnet.VM_USER) -> list[str]:
    """שורת הפקודה. הרשת היחידה: ה-fd של ה-macvtap שהשרת יצר (‏#1468), עם `mac`.
    **אין** `-netdev user` (slirp), ‏`bridge`, ‏`hostfwd`/`guestfwd`/`smb`, ואין
    `-vnc` על TCP. ‏QEMU רץ כ-`run_as` — ה-uid שחומת האש (`vmnet`) חוסמת."""
    if type(tap_fd) is not int or tap_fd < 3:
        raise ValueError(f"fd לא תקין ל-macvtap: {tap_fd!r}")
    if not vmnet.valid_mac(mac):
        raise ValueError(f"MAC לא תקין: {mac!r}")
    code, _vars = OVMF[secure_boot]
    machine = f"q35,accel={accel}" + (",smm=on" if secure_boot else "")
    argv = [
        "qemu-system-x86_64",
        "-name", "imagectl-vmupdate,process=imagectl-vmupdate",
        "-nodefaults", "-no-user-config",
        "-machine", machine,
        "-cpu", "host" if accel == "kvm" else "max",
        "-smp", str(cpus), "-m", str(memory_mib),
        "-rtc", "base=localtime",
    ]
    if secure_boot:
        argv += ["-global", "driver=cfi.pflash01,property=secure,value=on"]
    argv += [
        "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={ovmf_dir / code}",
        "-drive", f"if=pflash,format=raw,unit=1,file={workdir / 'vars.fd'}",
        "-drive", f"file={workdir / 'disk.raw'},format=raw,if=none,id=disk0,"
                  "discard=unmap,detect-zeroes=unmap",
        "-device", "ide-hd,drive=disk0,bus=ide.0,bootindex=1",
        # ‏#1468: ‏macvtap (bridge) על הכרטיס שנבחר. השרת פתח את ‏/dev/tapN ומעביר
        # את ה-fd (`pass_fds`) — QEMU אינו יוצר דבר ברשת בעצמו.
        "-netdev", f"tap,id=net0,fd={tap_fd}",
        # ‏romfile= ריק: בלי ROM של PXE. ‏Windows אינו צריך אותו, ו-OVMF לא
        # ינסה אתחול רשת — בוילן של המכללה זה היה DHCP/TFTP מול שרתים אמיתיים.
        "-device", f"e1000e,netdev=net0,mac={mac},romfile=",
        "-vga", "std",
        "-device", "qemu-xhci", "-device", "usb-tablet",
        # ‏force-shared: כמה צופים (השולט + מנהלים שצופים) על אותו מסך.
        "-vnc", f"unix:{workdir / 'vnc.sock'},share=force-shared",
        "-qmp", f"unix:{workdir / 'qmp.sock'},server=on,wait=off",
        "-run-with", f"user={run_as}",
        "-sandbox", SANDBOX,
    ]
    return argv


class QmpError(Exception):
    pass


def qmp(path: Path, command: str, timeout: float = 5.0) -> dict:
    """פקודת QMP אחת: greeting → ‏qmp_capabilities → הפקודה. אירועים
    באמצע מדולגים. כל כשל — QmpError בשם."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(path))
            stream = sock.makefile("rwb")

            def reply() -> dict:
                while True:
                    line = stream.readline()
                    if not line:
                        raise QmpError("QMP סגר את החיבור")
                    msg = json.loads(line)
                    if "return" in msg or "error" in msg or "QMP" in msg:
                        return msg

            reply()
            for cmd in ("qmp_capabilities", command):
                stream.write(json.dumps({"execute": cmd}).encode() + b"\n")
                stream.flush()
                msg = reply()
                if "error" in msg:
                    raise QmpError(f"{cmd}: {msg['error'].get('desc', msg['error'])}")
            return msg
    except (OSError, ValueError) as exc:
        raise QmpError(f"QMP לא ענה: {exc}") from exc


# --- הגדרות -----------------------------------------------------------------------


def read_settings(conn) -> dict[str, int]:
    out = {}
    for name, (key, default, low, high) in SETTINGS.items():
        raw = get_setting(conn, key)
        try:
            value = int(raw) if raw is not None else default
        except ValueError:
            value = default
        out[name] = value if low <= value <= high else default
    return out


def write_settings(conn, body: dict) -> dict[str, int]:
    pairs = {}
    for name, value in body.items():
        if name not in SETTINGS:
            raise ValueError(f"הגדרה לא מוכרת: {name}")
        key, _default, low, high = SETTINGS[name]
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{name} חייב להיות מספר שלם בין {low} ל-{high}")
        pairs[key] = str(value)
    set_settings(conn, pairs)
    return read_settings(conn)


def _gb(n: int | float) -> str:
    return f"{n / 1e9:.1f} GB"


@dataclass
class _Progress:
    stage: str = ""
    done_bytes: int = 0
    total_bytes: int | None = None
    partition: int = 0
    partitions: int = 0


# --- המנוע ----------------------------------------------------------------------


class _Tap:
    """ה-fd של ה-macvtap בתוך `with` — נסגר ביציאה מהבלוק בכל מסלול.

    ‏CodeQL (‏py/file-not-closed) אינו רואה סגירה דרך `machine.close_tap(fd)`
    — קריאה דרך אובייקט מוזרק — ודיווח על ה-fd מ-`_net_up` כ"לא נסגר".
    ‏`with` על עוטף הוא הצורה שהוא מזהה; הסגירה עצמה נשארת `close_tap`,
    כדי שהטסטים ימשיכו להזריק `Machine` מזויף."""

    def __init__(self, machine, fd: int) -> None:
        self.machine, self.fd = machine, fd

    def __enter__(self) -> int:
        return self.fd

    def __exit__(self, *_exc) -> None:
        self.machine.close_tap(self.fd)


class VmUpdate:
    """VM אחד בכל פעם. כל המתודות הציבוריות בטוחות לקריאה מכל תהליכון."""

    def __init__(self, conn, library: ImageLibrary, *, host: vmdisk.Host | None = None,
                 machine: Machine | None = None,
                 deploy_interface: Callable[[], str | None] = lambda: None,
                 allow_tcg: bool = False, ovmf_dir: Path = OVMF_DIR,
                 ram_reserve_mib: int = RAM_RESERVE_MIB,
                 growth_reserve_bytes: int = GROWTH_RESERVE_BYTES,
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen,
                 qmp_call: Callable[[Path, str], dict] = qmp,
                 now: Callable[[], float] = time.time,
                 maintenance_every: float | None = MAINTENANCE_S,
                 lease_timeout: float = vmnet.LEASE_TIMEOUT_S):
        self.conn = conn
        self.library = library
        self.host = host or vmdisk.Host()
        self.machine = machine or Machine()
        self.deploy_interface = deploy_interface
        #: ‏TCG (אמולציה, פי 10–50 איטי) — דגל בדיקות בלבד, לא נגיש מה-API.
        self.allow_tcg = allow_tcg
        self.ovmf_dir = Path(ovmf_dir)
        self.ram_reserve_mib = ram_reserve_mib
        self.growth_reserve_bytes = growth_reserve_bytes
        self._popen = popen
        self._qmp = qmp_call
        self._now = now
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._threads: list[threading.Thread] = []
        self._progress = _Progress()
        #: הכרעה 6 — מתי כל מנהל נראה לאחרונה (‏status/מסך). בזיכרון בלבד:
        #: נוכחות אינה שורדת אתחול, ושולט שלא נראה מאז האתחול "איננו".
        self._seen: dict[str, float] = {}
        #: מי צופה עכשיו במסך (חיבור WebSocket פתוח) — לפס "גם X צופה ב-VM".
        self._viewers: dict[str, dict] = {}
        #: ביטול בזמן ההכנה — נבדק בין מחיצות ובכל חתיכה של הזרם.
        self._cancel = False
        self._every = maintenance_every
        self._ticker: threading.Thread | None = None
        self._guarded_at = 0.0
        #: ‏#1468 (סקירת אבטחה 16/10): טעינת הטבלה ופירוק הרשת לעולם אינם חופפים.
        #: ‏`_net_live` דלוק מ-`_net_up` שהצליח ועד ש-`_net_down` מתחיל, ונבדק תחת
        #: אותו מנעול שבו נטענת הטבלה — טעינה אחרי פירוק אינה אפשרית.
        self._net_lock = threading.Lock()
        self._net_live = False
        self.lease_timeout = lease_timeout
        self._state = self._recover()
        self.maintenance()
        self._ensure_ticker()

    # -- מצב ----------------------------------------------------------------

    @property
    def workdir(self) -> Path:
        return self.library.root / WORK_AREA

    def _idle(self) -> dict:
        return {"state": "idle"}

    def _recover(self) -> dict:
        """מה שעל הדיסק אחרי אתחול. ‏QEMU מת עם השרת — "רץ" אינו רץ."""
        path = self.workdir / "state.json"
        if not self.workdir.exists():
            return self._idle()
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(state, dict) or "state" not in state:
                raise ValueError("אין שדה state")
        except (OSError, ValueError) as exc:
            return {"state": "failed", "disk_ready": False, "decided_at_ts": None,
                    "undecided_since_ts": self._now(),
                    "error": {"reason": "state_unreadable",
                              "message": f"אזור העבודה {WORK_AREA} קיים אבל מצבו לא נקרא ({exc}) — בטלו"}}
        was = state["state"]
        if was in BUSY:
            state.update({
                "state": "failed",
                # הדיסק שלם רק אם ה-VM עצמו נקטע; הכנה ושמירה שנקטעו — לא ידוע.
                "disk_ready": was in ("running", "stopping"),
                "undecided_since_ts": self._now(),
                "control_request": None,
                "error": {"reason": "interrupted",
                          "message": f"השרת הופעל מחדש כשהעדכון היה במצב '{was}'"},
            })
            try:
                self._qmp(self.workdir / "qmp.sock", "quit")   # QEMU שנשאר יתום
            except QmpError:
                pass   # הצפוי: ‏QEMU מת עם השרת. המצב כבר אומר "נקטע".
            if was in ("running", "stopping"):
                # ה-macvtap וכלל הכתובת נשארו מהתהליך שמת (#1468)
                why = self._net_down()
                if why:
                    state["warnings"] = list(state.get("warnings") or []) + [why]
            self._save_state(state)
        return state

    def clear_leftovers(self) -> None:
        """‏#1649: בעליית השרת — שאריות של שמירה שנקטעה באמצע כיווץ (העותק
        `imagectl-vmsave`, ה-loop-ים, ‏`shrink.cow`) מנוקות ונקראות בחזרה, כדי
        שביטול או שמירה לא ייתקלו ב-loop שנשאר מחובר. כשל = אזהרה במצב, שורה
        ביומן ובלוג — לא נבלע, וגם לא מפיל את עליית השרת. כשיש VM בפעולה בתהליך
        הזה (‏BUSY) — לא נוגעים: העותק שלו חי."""
        with self._lock:
            if self._state.get("state") in BUSY or not self.workdir.exists():
                return
            try:
                vmdisk.clear_leftovers(self.host, self.workdir)
            except vmdisk.DiskError as exc:
                log.error("vmsave leftovers not cleared: %s", exc.message)
                journal(self.conn, "vmupdate_leftover_failed", exc.message)
                self._set(warnings=list(self._state.get("warnings") or []) + [exc.message])

    def _save_state(self, state: dict) -> None:
        if not self.workdir.exists():
            return
        write_atomic(self.workdir / "state.json", json.dumps(state, ensure_ascii=False, indent=2))

    def _set(self, **changes) -> None:
        with self._lock:
            self._state.update(changes)
            if self._state.get("state") in UNDECIDED and not self._state.get("undecided_since_ts"):
                self._state["undecided_since_ts"] = self._now()
            if self._state.get("state") not in UNDECIDED:
                self._state["undecided_since_ts"] = None
            self._save_state(self._state)

    def touch(self, user: str) -> None:
        """המנהל הזה נמצא כאן (‏status, מסך) — הכרעה 6."""
        with self._lock:
            self._seen[user] = self._now()

    def viewer_join(self, user: str, address: str) -> str:
        """מסך נפתח: מי, מאיפה, ומתי. מחזיר אסימון ל-`viewer_leave`."""
        token = secrets.token_hex(8)
        with self._lock:
            self._viewers[token] = {"user": user, "address": address, "since": now_iso()}
        self.touch(user)
        return token

    def viewer_leave(self, token: str) -> None:
        with self._lock:
            self._viewers.pop(token, None)

    def _viewer_list(self, user: str | None) -> list[dict]:
        """מי צופה: ‏`id` אטום (גיבוב של האסימון — לא האסימון עצמו), ‏`you` למי
        ששואל, ‏`controller` לשולט. השולט ראשון, אחר כך לפי שעת ההצטרפות."""
        controller = self._state.get("controller")
        rows = [{"id": hashlib.sha256(token.encode()).hexdigest()[:12], **v,
                 "controller": v["user"] == controller, "you": bool(user) and v["user"] == user}
                for token, v in self._viewers.items()]
        return sorted(rows, key=lambda v: (not v["controller"], v["since"], v["user"]))

    def status(self, user: str | None = None) -> dict:
        if user:
            self.touch(user)
        self.maintenance()
        now = self._now()
        with self._lock:
            state = dict(self._state)
            prog = self._progress
            state["viewers"] = (self._viewer_list(user)
                                if state.get("state") in ("running", "stopping") else [])
        running = state.get("state") in ("running", "stopping")
        booted = state.pop("booted_at_ts", None)
        stopped = state.pop("stopped_at_ts", None)
        asked = state.pop("shutdown_requested_ts", None)
        since = state.pop("undecided_since_ts", None)
        state.pop("decided_at_ts", None)
        state["runtime_seconds"] = (int((now if running or not stopped else stopped) - booted)
                                    if booted else None)
        state["force_offered"] = bool(state.get("state") == "stopping" and asked
                                      and now - asked >= SHUTDOWN_GRACE_S)
        state["shutdown_waited_seconds"] = (int(now - asked)
                                            if state.get("state") == "stopping" and asked else None)
        state["expires_in_seconds"] = (max(0, int(since + KEEP_UNDECIDED_S - now))
                                       if since and state.get("state") in UNDECIDED else None)
        request = state.get("control_request")
        if request:
            state["control_request"] = {"by": request["by"],
                                        "seconds_left": max(0, int(request["at"] + REQUEST_TIMEOUT_S - now))}
        controller = state.get("controller")
        state["controller_present"] = (controller is not None
                                       and now - self._seen.get(controller, -1e18) < CONTROLLER_GONE_S)
        state["you_control"] = bool(user and controller == user)
        state["progress"] = ({"stage": prog.stage, "done_bytes": prog.done_bytes,
                              "total_bytes": prog.total_bytes, "partition": prog.partition,
                              "partitions": prog.partitions}
                             if state.get("state") in ("preparing", "saving") else None)
        net = state.get("network")
        if isinstance(net, dict):   # ‏#1468: הכתובת שה-VM קיבל, או כמה נשאר לחכות לה
            net = dict(net)
            since_ts = net.pop("since_ts", None)
            net["seconds_left"] = (max(0, int(since_ts + self.lease_timeout - now))
                                   if running and net.get("state") == "waiting" and since_ts
                                   else None)
            state["network"] = net
        if state.get("state") != "idle":
            disk = self.workdir / "disk.raw"
            try:
                allocated = disk.stat().st_blocks * 512 if disk.exists() else None
            except (OSError, AttributeError):
                allocated = None
            state["resources"] = {"memory_mib": state.get("memory_mib"),
                                  "cpus": state.get("cpus"), "accel": state.get("accel"),
                                  "disk_bytes": state.get("disk_bytes"),
                                  "disk_allocated_bytes": allocated}
        return state

    # -- תחזוקה: בקשות שליטה שפגו, ‏7 ימים ----------------------------------

    def _ensure_ticker(self) -> None:
        """לולאת התחזוקה רצה רק כשיש עדכון פתוח, ונגמרת כשאין — שרת בלי
        VM (וכל טסט שבונה אפליקציה) אינו מחזיק תהליכון שמתעורר לחינם."""
        with self._lock:
            if (not self._every or self._state.get("state") == "idle"
                    or (self._ticker is not None and self._ticker.is_alive())):
                return
            self._ticker = threading.Thread(target=self._tick, args=(self._every,),
                                            daemon=True, name="vmupdate-maintenance")
            self._ticker.start()

    def _tick(self, every: float) -> None:   # pragma: no cover — לולאת רקע
        while True:
            time.sleep(every)
            with self._lock:
                if self._state.get("state") in ("idle", "saved"):
                    self._ticker = None
                    return
            try:
                self.maintenance(guard=True)
            except Exception:   # noqa: BLE001 — הלולאה אינה מתה על כשל אחד
                log.exception("vmupdate maintenance failed")

    def _guard_network(self, force: bool = False) -> None:
        """בזמן שה-VM רץ, הבידוד נטען שוב בכל סבב תחזוקה. ‏`nft -f` של חומת
        האש הראשית (המתקין, הקונסולה כשמדליקים DHCP) מתחיל ב-`flush ruleset`
        ומוחק גם את הטבלה שלנו — וה-VM היה ממשיך לרוץ בלי גבולות. טבלה שלא
        נטענה מחדש = ה-VM נכבה בכוח, בשם (עיקרון 5)."""
        with self._lock:
            if self._state.get("state") not in ("running", "stopping"):
                return
            # לא בכל `status` (הממשק שואל כל שתי שניות): פעם בסבב תחזוקה.
            if not force and self._now() - self._guarded_at < MAINTENANCE_S:
                return
            self._guarded_at = self._now()
            mac = self._state.get("vm_mac")
            user = self.machine.vm_user()
            proc = self._proc
        with self._net_lock:
            if not self._net_live:
                return   # הרשת כבר מתפרקת (או לא עלתה) — אין מה לטעון מחדש
            why = ("אין MAC רשום ל-VM" if not mac else
                   "משתמש ה-VM אינו קיים" if user is None else
                   self.machine.firewall(user.uid, mac))
        if why is None:
            return
        journal(self.conn, "vmupdate_firewall_lost",
                f"{self._state.get('image_id')}: {why} — ה-VM נכבה")
        with self._lock:
            self._set(forced=True, state="stopping")
            self._state["warnings"] = list(self._state.get("warnings") or []) + [
                f"ה-VM נכבה בכוח: בידוד הרשת לא נטען מחדש ({why})"]
        try:
            self._qmp(self.workdir / "qmp.sock", "quit")
        except QmpError:
            if proc is not None:
                proc.kill()

    def maintenance(self, *, guard: bool = False) -> None:
        """‏`guard=True` — טעינת הבידוד מחדש עכשיו, בלי לחכות לסבב (לולאת
        הרקע ובדיקות)."""
        self._guard_network(force=guard)
        now = self._now()
        with self._lock:
            request = self._state.get("control_request")
            if request and now - request["at"] >= REQUEST_TIMEOUT_S:
                self._state["control_request"] = None
                self._state["control_last"] = {"by": request["by"], "outcome": "timeout"}
                journal(self.conn, "vmupdate_control_timeout",
                        f"{request['by']} ← {self._state.get('controller')}", request["by"])
                self._save_state(self._state)
            since = self._state.get("undecided_since_ts")
            if (self._state.get("state") in UNDECIDED and since
                    and now - since >= KEEP_UNDECIDED_S and self.workdir.exists()):
                image = self._state.get("image_id", "")
                try:
                    disk = self.workdir / "disk.raw"
                    if disk.exists():
                        vmdisk.detach_all(self.host, disk)
                    shutil.rmtree(self.workdir, ignore_errors=True)
                except vmdisk.DiskError as exc:
                    log.warning("expired work disk not removed: %s", exc.message)
                    return
                if self.workdir.exists():
                    return
                journal(self.conn, "vmupdate_expired",
                        f"{image} — דיסק עבודה שלא הוכרע 7 ימים נמחק")
                self._state = self._idle()

    # -- שליטה (הכרעה 6) ---------------------------------------------------

    def _require_control(self, user: str) -> None:
        controller = self._state.get("controller")
        if controller is not None and controller != user:
            raise Refusal(403, "view_only",
                          f"{controller} עובד ב-VM — אתם בצפייה בלבד. בקשו שליטה כדי לפעול")

    def controls(self, user: str) -> bool:
        with self._lock:
            return self._state.get("controller") == user

    def request_control(self, user: str) -> dict:
        now = self._now()
        with self._lock:
            if self._state.get("state") == "idle":
                raise Refusal(409, "state", "אין עדכון פתוח")
            controller = self._state.get("controller")
            if controller == user:
                return self.status(user)
            if self._state.get("control_request"):
                raise Refusal(409, "control_pending",
                              f"{self._state['control_request']['by']} כבר ביקש שליטה")
            present = (controller is not None
                       and now - self._seen.get(controller, -1e18) < CONTROLLER_GONE_S)
            if not present:
                self._state.update(controller=user, controller_since=now_iso(),
                                   control_request=None,
                                   control_last={"by": user, "outcome": "auto"})
                journal(self.conn, "vmupdate_control_auto",
                        f"{user} ← {controller} (לא נראה {CONTROLLER_GONE_S} ש')", user)
            else:
                self._state["control_request"] = {"by": user, "at": now}
                journal(self.conn, "vmupdate_control_request", f"{user} → {controller}", user)
            self._save_state(self._state)
        self.touch(user)
        return self.status(user)

    def answer_control(self, user: str, approve: bool) -> dict:
        with self._lock:
            request = self._state.get("control_request")
            if self._state.get("controller") != user:
                raise Refusal(403, "view_only", "רק מי שעובד ב-VM עונה על בקשת שליטה")
            if not request:
                raise Refusal(409, "control_pending", "אין בקשת שליטה ממתינה")
            self._state["control_request"] = None
            if approve:
                self._state.update(controller=request["by"], controller_since=now_iso(),
                                   control_last={"by": request["by"], "outcome": "approved"})
                journal(self.conn, "vmupdate_control_granted", f"{user} → {request['by']}", user)
            else:
                self._state["control_last"] = {"by": request["by"], "outcome": "denied"}
                journal(self.conn, "vmupdate_control_denied", f"{user} ✗ {request['by']}", user)
            self._save_state(self._state)
        return self.status(user)

    # -- תנאים מוקדמים ----------------------------------------------------

    def _tools(self, manifest: dict | None, secure_boot: bool) -> None:
        need = ["qemu-system-x86_64", "losetup", "sgdisk", "blockdev", "blkid", "zstd", "jq",
                "ntfsresize", "ntfsfix", "partclone.dd", "sh", "ip", "nft", "setpriv", "chown",
                "dmsetup"]
        if manifest is not None:
            need += sorted({vmdisk.partclone_for_fs(p.get("fs") or "")
                            for p in streamed_partitions(manifest)})
        missing = [t for t in dict.fromkeys(need) if self.machine.which(t) is None]
        missing += [str(self.ovmf_dir / f) for f in OVMF[secure_boot]
                    if not self.machine.exists(self.ovmf_dir / f)]
        if not self.machine.guestfs():
            missing.append("python3-guestfs")
        if missing:
            raise Refusal(503, "missing_tool", "חסרים בשרת כלים להפעלת VM: " + ", ".join(missing),
                          missing=missing)
        if self.machine.vm_user() is None:
            raise Refusal(503, "vm_user", f"משתמש המערכת {vmnet.VM_USER} אינו קיים — "
                                          "ה-VM רץ רק תחתיו (המתקין יוצר אותו)")

    def host_problem(self) -> str | None:
        """‏#1372: האם המארח מוכן ל-VM — אותן בדיקות שההפעלה מסרבת לפיהן
        (`_tools`, לשני מצבי Secure Boot), בלי אימג'. ‏None = מוכן; אחרת מה חסר,
        במשפט אחד. לעליית השרת, למסך הבריאות ולסטטוס העדכון (`server/vmhost.py`)."""
        missing: list[str] = []
        for secure_boot in (False, True):
            try:
                self._tools(None, secure_boot)
            except Refusal as exc:
                if exc.reason == "missing_tool":
                    missing += [m for m in exc.numbers.get("missing", []) if m not in missing]
        problems = ["חסרים בשרת כלים להפעלת VM: " + ", ".join(missing)] if missing else []
        if self.machine.vm_user() is None:
            problems.append(f"משתמש המערכת {vmnet.VM_USER} אינו קיים")
        return " · ".join(problems) or None

    def _check_vm(self, settings: dict[str, int]) -> tuple[str, str]:
        """‏KVM, זיכרון, ליבות, רשת. מחזיר (accel, כרטיס היציאה)."""
        why = self.machine.kvm()
        if why is not None:
            if not self.allow_tcg:
                raise Refusal(503, "no_kvm", f"אין תמיכה בווירטואליזציה בשרת: {why}")
            accel = "tcg"
        else:
            accel = "kvm"
        avail = self.machine.mem_available_mib()
        if avail is None:
            raise Refusal(503, "memory", "לא ניתן לקרוא כמה זיכרון פנוי בשרת")
        need = settings["memory_mib"] + self.ram_reserve_mib
        if avail < need:
            raise Refusal(
                503, "memory",
                f"אין מספיק זיכרון: ה-VM צריך {settings['memory_mib'] / 1024:.1f} GB ועוד "
                f"{self.ram_reserve_mib / 1024:.1f} GB לשרת, זמינים {avail / 1024:.1f} GB",
                vm_mib=settings["memory_mib"], reserve_mib=self.ram_reserve_mib,
                available_mib=avail)
        cores = self.machine.cpu_count()
        if cores is None:
            raise Refusal(503, "cpus", "לא ניתן לקרוא כמה ליבות יש בשרת")
        if settings["cpus"] >= cores:
            raise Refusal(503, "cpus", f"ל-VM הוגדרו {settings['cpus']} ליבות ובשרת {cores} — "
                                       "נדרשת לפחות ליבה אחת לשרת", vm_cpus=settings["cpus"],
                          server_cpus=cores)
        nic = get_setting(self.conn, NIC_KEY)
        if not nic:
            raise Refusal(409, "vm_nic_unset",
                          "לא נבחר כרטיס לעדכוני VM — בחרו אותו ברשת › כרטיסי רשת, ובקשו "
                          "מה-IT כתובת שמורה ל-MAC שמוצג שם")
        self._vm_nic_check(nic)
        return accel, nic

    # -- הרשת של ה-VM (‏#1468) ---------------------------------------------------

    def _vm_nic_check(self, name: object) -> None:
        """כרטיס שה-VM יכול לשבת עליו: קיים, ואינו כרטיס ההפצה ואינו מעליו/מתחתיו
        (חבר ב-bond, הורה/ילד VLAN). כרטיס הניהול **מותר** (נדב 16/10: רשת הניהול
        יכולה להיות גם רשת הכיתות)."""
        if not vmnet.valid_ifname(name) or name in ("lo", vmnet.TAP_NAME):
            raise Refusal(400, "vm_nic_invalid", f"שם כרטיס לא תקין: {name!r}")
        if not self.machine.nic_exists(name):
            raise Refusal(404, "vm_nic_missing", f"הכרטיס {name} אינו קיים בשרת", interface=name)
        deploy = self.deploy_interface()
        if not deploy:
            return
        if name == deploy:
            raise Refusal(409, "vm_nic_is_deploy",
                          f"{name} הוא כרטיס ההפצה — ה-VM לעולם אינו מחובר לרשת שמשדרת "
                          "לכיתות. בחרו כרטיס אחר (כרטיס הניהול מותר)", interface=name)
        related = self.machine.stacked(name)
        if related is None:
            raise Refusal(503, "vm_nic_unchecked",
                          f"לא ניתן לבדוק אם {name} קשור לכרטיס ההפצה {deploy} (bond/VLAN) — "
                          "ה-VM לא יחובר אליו", interface=name)
        if deploy in related:
            raise Refusal(409, "vm_nic_is_deploy",
                          f"{name} קשור לכרטיס ההפצה {deploy} (bond/VLAN) — ה-VM לעולם אינו "
                          "מחובר לרשת שמשדרת לכיתות. בחרו כרטיס אחר", interface=name)

    def ensure_mac(self) -> str:
        """ה-MAC של ה-VM — נוצר **פעם אחת** ונשמר; אותו MAC אחרי אתחול ועדכון."""
        with self._lock:
            mac = get_setting(self.conn, MAC_KEY)
            if vmnet.valid_mac(mac):
                return mac
            fresh = vmnet.new_mac()
            set_setting(self.conn, MAC_KEY, fresh)
            if get_setting(self.conn, MAC_KEY) != fresh:
                raise Refusal(503, "vm_mac", "ה-MAC של ה-VM לא נשמר ב-DB")
            journal(self.conn, "vmupdate_mac_created",
                    f"{fresh}" + (f" (הקודם לא תקין: {mac!r})" if mac else ""))
            return fresh

    def _lease_record(self) -> dict | None:
        try:
            value = json.loads(get_setting(self.conn, LEASE_KEY) or "null")
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    def network_view(self) -> dict:
        """מה שמסך הכרטיסים מציג ליד הבחירה."""
        nic = get_setting(self.conn, NIC_KEY) or None
        mac = self.ensure_mac()
        lease = self._lease_record()
        virt = self.machine.platform()
        problem = None
        if nic:
            try:
                self._vm_nic_check(nic)
            except Refusal as exc:
                problem = {"code": exc.reason, "error": exc.message}
        return {"interface": nic, "mac": mac, "deploy_interface": self.deploy_interface(),
                "lease": lease if lease and lease.get("mac") == mac else None,
                "platform": virt, "hint": vmnet.platform_hint(virt), "problem": problem,
                "lease_timeout_seconds": int(self.lease_timeout)}

    def set_network(self, name: object, user: str) -> dict:
        """‏`name=None` מבטל את הבחירה (ה-VM לא יודלק עד שייבחר כרטיס)."""
        if name is not None:
            self._vm_nic_check(name)
        before = get_setting(self.conn, NIC_KEY) or None
        set_setting(self.conn, NIC_KEY, name or "")
        if (get_setting(self.conn, NIC_KEY) or None) != name:
            raise Refusal(503, "vm_nic_unsaved", "הבחירה לא נשמרה ב-DB")
        journal(self.conn, "vmupdate_network_set", f"{before or '—'} → {name or '—'}", user)
        return self.network_view()

    def _net_up(self, nic: str) -> int:
        """‏macvtap + חומת האש — לפני **כל** הדלקה. מחזיר את ה-fd ל-QEMU."""
        user = self.machine.vm_user()
        if user is None:
            raise Refusal(503, "vm_user", f"משתמש המערכת {vmnet.VM_USER} אינו קיים")
        mac = self.ensure_mac()
        fd, why = self.machine.macvtap(nic, mac)
        if fd is None:
            raise Refusal(503, "macvtap",
                          f"החיבור של ה-VM לכרטיס {nic} לא נוצר — ה-VM לא יודלק: {why}. "
                          + vmnet.platform_hint(self.machine.platform()), interface=nic)
        with self._net_lock:
            why = self.machine.firewall(user.uid, mac)
            self._net_live = why is None
        if why is not None:
            self.machine.close_tap(fd)
            self.machine.macvtap_down()
            raise Refusal(503, "firewall", f"בידוד הרשת של ה-VM לא נטען — ה-VM לא יודלק: {why}")
        self._guarded_at = self._now()
        return fd

    def _net_down(self) -> str | None:
        """אחרי ש-QEMU יצא: ה-macvtap והטבלה יורדים. כשל נרשם בשמו. תחת `_net_lock`,
        ו-`_net_live` כבה **לפני** הפירוק — טעינה מקבילה (`_guard_network`) כבר לא
        תחזיר את הטבלה אחריו."""
        with self._net_lock:
            self._net_live = False
            problems = [w for w in (self.machine.macvtap_down(), self.machine.firewall_off())
                        if w]
        if not problems:
            return None
        message = "פירוק הרשת של ה-VM לא הושלם: " + " · ".join(problems)
        journal(self.conn, "vmupdate_net_teardown_failed", message)
        return message

    def _check_space(self, manifest: dict) -> None:
        """‏#1390: מה הפתיחה והשמירה **תופסות בפועל**, לא הגודל הלוגי.

        ‏`disk.raw` דליל: ‏partclone כותב רק בלוקים בשימוש, ולכן הוא תופס את
        ‏`used_bytes` של המחיצות (ועוד מה ש-Windows Update יכתוב — המרווח), לא את
        גודל הדיסק (נמדד 13/10: לוגי 500.1GB, ‏26GB תפוסים אחרי 9GB שחזור).
        הגרסה החדשה נכתבת לצד הישנה, דחוסה — כגודל המקור הדחוס.

        מחיצה בלי `used_bytes` (‏#1368: ‏null ב-NTFS) — לא ממציאים מספר: נספר
        גודל המחיצה המלא, וההודעה אומרת שזו הערכה שמרנית ובאילו מחיצות."""
        free = self.machine.free_bytes(self.library.root)
        if free is None:
            raise Refusal(503, "disk_space", "לא ניתן לקרוא כמה מקום פנוי בספרייה")
        used, unmeasured = 0, []
        for part in streamed_partitions(manifest):
            value = part.get("used_bytes")
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                used += value
            else:
                used += int(part.get("size_bytes") or 0)
                unmeasured.append(f"p{part.get('index', '?')}")
        compressed = int(manifest.get("total_compressed_bytes") or 0)
        # ‏#1737: הרזרבה למערכת נוספת על מרווח הגדילה — הוא לדיסק של ה-VM, היא לשרת.
        keep = disk_reserve.reserve()
        need = used + self.growth_reserve_bytes + compressed + keep
        if free < need:
            label = "גודל המחיצות" if unmeasured else "תפוס במחיצות"
            note = (f" · ב-{', '.join(unmeasured)} לא נמדד כמה תפוס — נספר גודל המחיצה המלא "
                    "(הערכה שמרנית: הדיסק הזמני דליל ותופס רק מה שנכתב)") if unmeasured else ""
            raise Refusal(
                507, "disk_space",
                f"אין מקום בשרת: נדרשים {_gb(need)} ({label} {_gb(used)} + מרווח לעדכונים "
                f"{_gb(self.growth_reserve_bytes)} + גרסה חדשה {_gb(compressed)} + "
                f"רזרבה למערכת {_gb(keep)}), "
                f"פנויים {_gb(free)}{note}", need_bytes=need, free_bytes=free,
                **({"unmeasured_partitions": unmeasured} if unmeasured else {}))

    # -- פעולות -----------------------------------------------------------

    def start(self, image_id: str, user: str) -> dict:
        settings = read_settings(self.conn)
        with self._lock:
            if self._state.get("state") not in ("idle", "saved") or self.workdir.exists():
                raise Refusal(409, "busy",
                              f"כבר פתוח עדכון של {self._state.get('image_name') or 'אימג'} "
                              f"({self._state.get('state')}) — שמרו או בטלו אותו קודם",
                              image_id=self._state.get("image_id"),
                              controller=self._state.get("controller"))
            manifest = self.library.get(image_id)
            if manifest is None:
                raise Refusal(404, "image", "אין אימג' כזה בספרייה")
            if not manifest.get("_available", True) or not manifest.get("_dir"):
                raise Refusal(409, "image", "האימג' לא זמין — המיקום שלו אינו נגיש")
            try:
                vmdisk.check_openable(manifest)
            except vmdisk.DiskError as exc:
                raise Refusal(400, exc.reason, exc.message) from exc
            secure_boot = secure_boot_for(manifest)
            self._tools(manifest, secure_boot)
            accel, egress = self._check_vm(settings)
            self._check_space(manifest)
            self.workdir.mkdir(parents=True)
            self._cancel = False
            self._progress = _Progress(stage="disk")
            self._state = {
                "state": "preparing", "image_id": image_id, "image_name": manifest["name"],
                "image_version": manifest.get("version") or 1,
                "lineage": lineage_of(manifest), "started_by": user,
                "controller": user, "controller_since": now_iso(), "control_request": None,
                "control_last": None, "created_at": now_iso(), "accel": accel,
                "egress_interface": egress, "vm_mac": self.ensure_mac(),
                "network": None, "secure_boot": secure_boot,
                "memory_mib": settings["memory_mib"], "cpus": settings["cpus"],
                "disk_ready": False, "forced": False, "warnings": [], "error": None,
                "result": None,
            }
            self._save_state(self._state)
            journal(self.conn, "vmupdate_start", f"{image_id} accel={accel}", user)
            self._spawn(self._prepare, manifest, user)
            self._ensure_ticker()
        self.touch(user)
        return self.status(user)

    def _spawn(self, target, *args) -> None:
        thread = threading.Thread(target=target, args=args, daemon=True,
                                  name=f"vmupdate-{target.__name__}")
        self._threads.append(thread)
        thread.start()

    def _bytes(self, n: int) -> None:
        with self._lock:
            if self._cancel:
                raise vmdisk.DiskError("cancelled", "ההכנה בוטלה")
            self._progress.done_bytes += n

    def _prepare(self, manifest: dict, user: str) -> None:
        total = sum(self._file_size(manifest, p) for p in streamed_partitions(manifest))
        with self._lock:
            self._progress = _Progress(stage="disk", total_bytes=total or None,
                                       partitions=len(streamed_partitions(manifest)))
        try:
            built = vmdisk.build_disk(self.host, manifest, Path(manifest["_dir"]), self.workdir,
                                      self._bytes)
            template = self.ovmf_dir / OVMF[self._state["secure_boot"]][1]
            shutil.copyfile(template, self.workdir / "vars.fd")
            self._set(disk_bytes=built.disk_bytes, warnings=built.warnings, disk_ready=True)
            self._boot(user)
        except Refusal as exc:
            self._fail(exc.reason, exc.message, disk_ready=True, user=user)
        except vmdisk.DiskError as exc:
            if exc.reason == "cancelled":
                self._finish_cancel(user)
            else:
                self._fail(exc.reason, exc.message, disk_ready=False, user=user)
        except Exception as exc:   # noqa: BLE001 — כל כשל נרשם בשם, לא נבלע
            log.exception("vmupdate prepare failed")
            self._fail("internal", f"הכנת הדיסק נכשלה: {exc}", disk_ready=False, user=user)

    @staticmethod
    def _file_size(manifest: dict, part: dict) -> int:
        try:
            from .images import guarded_image_path
            path = guarded_image_path(manifest["_dir"], part["file"])
            return path.stat().st_size if path is not None else 0
        except (OSError, KeyError, TypeError):
            return 0

    def _fail(self, reason: str, message: str, *, disk_ready: bool, user: str = "") -> None:
        with self._lock:
            journal(self.conn, "vmupdate_failed",
                    f"{self._state.get('image_id')} {reason}: {message}", user)
            self._set(state="failed", error={"reason": reason, "message": message},
                      disk_ready=disk_ready)

    def _boot(self, user: str) -> None:
        nic = self._state["egress_interface"]
        mac = self.ensure_mac()
        fd = self._net_up(nic)
        try:
            # ה-fd נסגר ביציאה מה-with בשני המסלולים — ל-QEMU יש עותק משלו
            # (‏pass_fds), ובחריגה הוא נסגר לפני `_net_down`, כמו קודם.
            with _Tap(self.machine, fd):
                argv = qemu_argv(workdir=self.workdir, memory_mib=self._state["memory_mib"],
                                 cpus=self._state["cpus"], accel=self._state["accel"],
                                 secure_boot=self._state["secure_boot"], tap_fd=fd, mac=mac,
                                 ovmf_dir=self.ovmf_dir)
                for stale in ("qmp.sock", "vnc.sock"):
                    (self.workdir / stale).unlink(missing_ok=True)
                with (self.workdir / "qemu.log").open("ab") as logfile:
                    proc = self._popen(argv, stdin=subprocess.DEVNULL, stdout=logfile,
                                       stderr=logfile,
                                       preexec_fn=_lower_priority if os.name == "posix" else None,
                                       close_fds=True, pass_fds=(fd,))
        except BaseException:
            self._net_down()
            raise
        with self._lock:
            self._proc = proc
            self._state.update(state="running", booted_at=now_iso(), booted_at_ts=self._now(),
                               stopped_at=None, stopped_at_ts=None, forced=False, error=None,
                               shutdown_requested_ts=None, vm_mac=mac,
                               network={"interface": nic, "mac": mac, "state": "waiting",
                                        "address": None, "server": None, "at": None,
                                        "since_ts": self._now()})
            # ‏#1468: היומן לפני ש-`running` נראה ב-status() (כמו ב-_fail) — מי שראה
            # `running` רואה גם את השורה. אחרי השחרור קורא יכול היה להקדים אותה.
            journal(self.conn, "vmupdate_running", f"{self._state['image_id']} {nic} {mac}", user)
            self._set()
        self._spawn(self._watch, proc)
        self._spawn(self._lease, proc, nic, mac)

    def _lease(self, proc: subprocess.Popen, nic: str, mac: str) -> None:
        """הראיה שה-VM קיבל כתובת: DHCPACK תקף ל-MAC שלו (`vmnet.watch_lease`). ההאזנה
        נמשכת כל זמן שה-VM רץ — חידוש עם כתובת אחרת מתעדכן במסך. בלי ACK תוך
        `lease_timeout` — ה-VM נכבה וההודעה אומרת למה, עם הרמז לפלטפורמה. הכתובת
        **מוצגת** בלבד: הבידוד במארח הוא לפי MAC, ואינו נשען על ה-DHCP."""
        def on_ack(address: str, server: str | None) -> None:
            with self._lock:
                if self._proc is not proc:
                    return
                net = dict(self._state.get("network") or {})
                if net.get("state") == "leased" and net.get("address") == address:
                    return   # חידוש לאותה כתובת — אין מה לעדכן
                record = {"address": address, "server": server, "mac": mac,
                          "interface": nic, "at": now_iso()}
                # הרשומה והיומן לפני שהמצב נראה החוצה — מי שרואה "leased" רואה גם אותם
                set_setting(self.conn, LEASE_KEY, json.dumps(record))
                journal(self.conn, "vmupdate_lease",
                        f"{mac} ← {address} (DHCP {server or '?'}) · {nic}")
                net.update(state="leased", address=address, server=server, at=record["at"])
                self._set(network=net)

        result = self.machine.watch_lease(mac, self.lease_timeout,
                                          lambda: self._proc is not proc, on_ack)
        if self._proc is not proc or result.get("address"):
            return   # ה-VM כבה (אחרי lease או לפניו) — אין למי לסרב
        message = vmnet.no_lease_message(result, nic, mac, self.lease_timeout,
                                         self.machine.platform())
        journal(self.conn, "vmupdate_no_lease", message)
        self._refuse_net(proc, "no_lease", message)

    def _refuse_net(self, proc: subprocess.Popen, reason: str, message: str) -> None:
        """ה-VM נכבה בכוח, ו-`_watch` מסמן `failed` עם הסיבה (הדיסק שלם)."""
        with self._lock:
            if self._proc is not proc:
                return
            net = dict(self._state.get("network") or {})
            if reason == "no_lease":
                net["state"] = "no_lease"
            self._state["net_error"] = {"reason": reason, "message": message}
            self._set(network=net, forced=True, state="stopping")
        try:
            self._qmp(self.workdir / "qmp.sock", "quit")
        except QmpError:
            proc.kill()

    def _watch(self, proc: subprocess.Popen) -> None:
        rc = proc.wait()
        down = self._net_down()
        with self._lock:
            if self._proc is not proc:
                return
            self._proc = None
            was = self._state.get("state")
            forced = self._state.get("forced")
            net_error = self._state.pop("net_error", None)
            self._state.update(stopped_at=now_iso(), stopped_at_ts=self._now())
            if down:
                self._state["warnings"] = list(self._state.get("warnings") or []) + [down]
            if net_error:
                self._state.update(state="failed", disk_ready=True, error=net_error)
            elif rc != 0 and not forced and was == "running":
                tail = self._qemu_log_tail()
                self._state.update(state="failed", disk_ready=True,
                                   error={"reason": "qemu_exit",
                                          "message": f"QEMU יצא בקוד {rc}: {tail}"})
            else:
                self._state.update(state="stopped")
            # היומן לפני שהמצב נראה החוצה: מי שראה "stopped" ומיד שמר, רואה
            # ביומן את הכיבוי לפני השמירה.
            journal(self.conn, "vmupdate_stopped",
                    f"{self._state.get('image_id')} rc={rc}" + (" forced" if forced else ""))
            self._set()

    def _qemu_log_tail(self) -> str:
        try:
            lines = (self.workdir / "qemu.log").read_text(encoding="utf-8",
                                                          errors="replace").strip().splitlines()
        except OSError:
            return "(אין יומן QEMU)"
        return lines[-1] if lines else "(יומן QEMU ריק)"

    def _require(self, allowed: tuple[str, ...], what: str) -> None:
        state = self._state.get("state")
        if state not in allowed:
            raise Refusal(409, "state", f"לא ניתן {what} כשהעדכון במצב '{state}'")

    def shutdown(self, user: str) -> dict:
        with self._lock:
            self._require_control(user)
            self._require(("running",), "לכבות")
            try:
                self._qmp(self.workdir / "qmp.sock", "system_powerdown")
            except QmpError as exc:
                raise Refusal(502, "qmp", f"בקשת הכיבוי לא הגיעה ל-VM: {exc}") from exc
            self._set(state="stopping", shutdown_requested_ts=self._now())
        journal(self.conn, "vmupdate_shutdown", self._state.get("image_id", ""), user)
        return self.status(user)

    def force_off(self, user: str) -> dict:
        with self._lock:
            self._require_control(user)
            self._require(("running", "stopping"), "לכבות בכוח")
            proc = self._proc
            self._set(forced=True, state="stopping")
            try:
                self._qmp(self.workdir / "qmp.sock", "quit")
                how = "qmp"
            except QmpError:
                if proc is not None:
                    proc.kill()
                how = "kill"
        journal(self.conn, "vmupdate_force_off", f"{self._state.get('image_id', '')} {how}", user)
        if proc is not None:
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
        return self.status(user)

    def boot(self, user: str) -> dict:
        """הדלקה חוזרת על אותו דיסק — גם אחרי כיבוי בכוח (הכרעה 3) וגם
        כשחוזרים לדיסק שלא הוכרע (הכרעה 5)."""
        with self._lock:
            self._require_control(user)
            state = self._state.get("state")
            if not (state == "stopped" or (state == "failed" and self._state.get("disk_ready"))):
                raise Refusal(409, "state", f"לא ניתן להדליק כשהעדכון במצב '{state}'")
            secure_boot = bool(self._state.get("secure_boot"))
            self._tools(None, secure_boot)
            accel, egress = self._check_vm({"memory_mib": self._state["memory_mib"],
                                            "cpus": self._state["cpus"]})
            self._state.update(accel=accel, egress_interface=egress)
            if not (self.workdir / "vars.fd").exists():
                shutil.copyfile(self.ovmf_dir / OVMF[secure_boot][1], self.workdir / "vars.fd")
            journal(self.conn, "vmupdate_boot", self._state.get("image_id", ""), user)
            self._boot(user)
        return self.status(user)

    def save(self, user: str, name: str, description: str = "") -> dict:
        name = validate_display_name(name, "שם הגרסה")
        if not isinstance(description, str) or len(description) > 500:
            raise Refusal(400, "description", "התיאור חייב להיות טקסט עד 500 תווים")
        with self._lock:
            self._require_control(user)
            state = self._state.get("state")
            if not (state == "stopped" or (state == "failed" and self._state.get("disk_ready"))):
                raise Refusal(409, "state", f"לא ניתן לשמור כשהעדכון במצב '{state}' — כבו קודם")
            user_ids = self.machine.vm_user()
            if user_ids is None:
                raise Refusal(503, "vm_user", f"משתמש המערכת {vmnet.VM_USER} אינו קיים")
            self._progress = _Progress(stage="capture")
            self._set(state="saving", error=None)
        journal(self.conn, "vmupdate_save", f"{self._state.get('image_id')} \"{name}\"", user)
        self._spawn(self._save, user, name, description, user_ids)
        return self.status(user)

    def _save(self, user: str, name: str, description: str, ids: vmdisk.VmUser) -> None:
        out = self.workdir / "out"
        shutil.rmtree(out, ignore_errors=True)
        try:
            source = json.loads((self.workdir / "source-manifest.json").read_text(encoding="utf-8"))
            self._check_save_space(source)

            def on_partition(count: int, total: int) -> None:
                with self._lock:
                    self._progress.stage = "stream"
                    self._progress.partition, self._progress.partitions = count, total

            captured = vmdisk.capture_disk(self.host, self.workdir, out, self._bytes,
                                           on_partition, user=ids)
            new_id = self._new_id()
            manifest = vmdisk.compose_manifest(source, captured, new_id=new_id, name=name,
                                               description=description, user=user, now=now_iso())
            with self._lock:
                self._progress.stage = "verify"
            problem = validate_manifest_files(manifest, out) or ImageLibrary._validate(manifest)
            if problem:
                raise vmdisk.DiskError("verify", f"הגרסה החדשה לא עברה את בדיקות הקליטה: {problem}")
            write_atomic(out / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            target = self.library.root / new_id
            with self._lock:
                if target.exists() or self.library.get(new_id) is not None:
                    raise vmdisk.DiskError("verify", f"המזהה {new_id} כבר קיים בספרייה")
                out.rename(target)
            result = {"image_id": new_id, "name": name, "version": manifest["version"],
                      "previous_id": manifest["previous_id"], "lineage": manifest["lineage"],
                      "os": image_os(manifest)}
            journal(self.conn, "vmupdate_saved",
                    f"{new_id} \"{name}\" v{manifest['version']} from {manifest['previous_id']}", user)
            with self._lock:
                shutil.rmtree(self.workdir, ignore_errors=True)
                gone = not self.workdir.exists()
                self._state = {"state": "saved", "image_id": source.get("id"),
                               "image_name": source.get("name"), "result": result,
                               "warnings": ([] if gone else
                                            [f"דיסק העבודה לא נמחק — {WORK_AREA} נשאר בספרייה"]),
                               "error": None}
        except vmdisk.DiskError as exc:
            shutil.rmtree(out, ignore_errors=True)
            # ‏#1491: השמירה אינה כותבת לדיסק (הכיווץ על עותק) — כל כשל בשמו משאיר
            # אותו שלם: מדליקים שוב, מתקנים ושומרים.
            self._fail(exc.reason, exc.message, disk_ready=True, user=user)
        except Exception as exc:   # noqa: BLE001
            log.exception("vmupdate save failed")
            shutil.rmtree(out, ignore_errors=True)
            self._fail("internal", f"השמירה נכשלה: {exc}", disk_ready=False, user=user)

    def _check_save_space(self, source: dict) -> None:
        """‏#1737: בין הפתיחה לשמירה עוברים ימים — דיסק העבודה גדל, קליטות נכנסו.
        הגרסה החדשה נכתבת לספרייה רק אם אחריה נשארת הרזרבה למערכת. הגודל שלה
        מוערך כגודל המקור הדחוס, כמו בפתיחה (`_check_space`). כשל = ‏`disk_ready`
        נשאר: מפנים מקום ושומרים שוב."""
        free = self.machine.free_bytes(self.library.root)
        if free is None:
            raise vmdisk.DiskError("disk_space", "לא ניתן לקרוא כמה מקום פנוי בספרייה")
        compressed = int(source.get("total_compressed_bytes") or 0)
        refused = disk_reserve.refusal(free, compressed, "שמירת הגרסה החדשה")
        if refused is not None:
            raise vmdisk.DiskError("disk_space", refused)

    def _new_id(self) -> str:
        existing = set(self.library.scan())
        for _ in range(64):
            candidate = "img_" + secrets.token_hex(3)
            if candidate not in existing and not (self.library.root / candidate).exists():
                return candidate
        raise vmdisk.DiskError("verify", "לא נמצא מזהה פנוי לגרסה החדשה")

    def _finish_cancel(self, user: str) -> None:
        """ההכנה בוטלה: הדיסק החלקי נמחק, והמצב חוזר ל-idle. כלום לא נגע באימג'."""
        disk = self.workdir / "disk.raw"
        try:
            if disk.exists():
                vmdisk.detach_all(self.host, disk)
        except vmdisk.DiskError as exc:
            self._fail(exc.reason, exc.message, disk_ready=False, user=user)
            return
        shutil.rmtree(self.workdir, ignore_errors=True)
        with self._lock:
            image = self._state.get("image_id", "")
            self._cancel = False
            if self.workdir.exists():
                self._set(state="failed", disk_ready=False,
                          error={"reason": "discard", "message": "דיסק העבודה לא נמחק במלואו"})
                return
            self._state = self._idle()
        journal(self.conn, "vmupdate_discard", f"{image} (בוטל בהכנה)", user)

    def precheck(self, image_id: str) -> dict:
        """מה ש-`start` היה מסרב לו — בלי להתחיל (חלון ההתחלה: אין KVM, אין
        מקום, VM אחר פתוח). ‏{"ok": true} או הסירוב בשמו ובמספריו."""
        try:
            with self._lock:
                if self._state.get("state") not in ("idle", "saved") or self.workdir.exists():
                    raise Refusal(409, "busy", "כבר פתוח עדכון של "
                                  f"{self._state.get('image_name') or 'אימג'}",
                                  image_id=self._state.get("image_id"),
                                  image_name=self._state.get("image_name"),
                                  controller=self._state.get("controller"),
                                  state=self._state.get("state"))
            manifest = self.library.get(image_id)
            if manifest is None:
                raise Refusal(404, "image", "אין אימג' כזה בספרייה")
            try:
                vmdisk.check_openable(manifest)
            except vmdisk.DiskError as exc:
                raise Refusal(400, exc.reason, exc.message) from exc
            settings = read_settings(self.conn)
            self._tools(manifest, secure_boot_for(manifest))
            accel, egress = self._check_vm(settings)
            self._check_space(manifest)
        except Refusal as exc:
            return {"ok": False, "code": exc.reason, "error": exc.message, "details": exc.numbers}
        return {"ok": True, "accel": accel, "egress_interface": egress,
                "vm_mac": self.ensure_mac(), "secure_boot": secure_boot_for(manifest),
                "disk_bytes": vmdisk.disk_bytes_for(manifest), **settings}

    def discard(self, user: str) -> dict:
        with self._lock:
            self._require_control(user)
            state = self._state.get("state")
            if state == "preparing":
                # ‏"ביטול" בחלון ההכנה (המוקאפ): התהליכון עוצר בחתיכה הבאה.
                self._cancel = True
                return self.status(user)
            if state in BUSY:
                raise Refusal(409, "state", f"לא ניתן לבטל כשהעדכון במצב '{state}' — כבו קודם")
            image = self._state.get("image_id", "")
            if self.workdir.exists():
                disk = self.workdir / "disk.raw"
                if disk.exists():
                    try:
                        vmdisk.detach_all(self.host, disk)
                    except vmdisk.DiskError as exc:
                        raise Refusal(500, exc.reason, exc.message) from exc
                shutil.rmtree(self.workdir, ignore_errors=True)
                if self.workdir.exists():
                    raise Refusal(500, "discard", f"דיסק העבודה לא נמחק במלואו ({WORK_AREA})")
            self._state = self._idle()
        journal(self.conn, "vmupdate_discard", image, user)
        return self.status(user)

    def vnc_socket(self) -> Path | None:
        with self._lock:
            if self._state.get("state") not in ("running", "stopping"):
                return None
        path = self.workdir / "vnc.sock"
        return path if path.exists() else None

    def join(self, timeout: float = 30) -> None:
        """לבדיקות: ממתין לתהליכוני הרקע."""
        for thread in list(self._threads):
            thread.join(timeout)


def _lower_priority() -> None:   # pragma: no cover — רץ בתהליך הבן
    """‏nice 10 ל-QEMU: השרת והשידור לכיתה קודמים לו."""
    os.nice(10)
