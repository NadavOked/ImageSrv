"""‏evdev/uinput מפייתון, למעבדה בלבד (#949).

שני צדדים של אותו חוזה: ‏`agent/monitor.c` יוצר התקן uinput, ו-
‏`native-gui/src/input.c` קורא אותו מ-`/dev/input/event*`. הטסטים צריכים
לעמוד בשני הקצוות — לקרוא את ההתקן שהמוניטור יצר, וליצור התקן שהגואי
יקרא — בלי ספרייה חיצונית (‏`python-evdev` אינו במעבדה ואינו בייצור).

הכול x86_64 לינוקס: ‏`struct input_event` הוא 24 בייטים (‏timeval של שני
‏long), ‏`input_absinfo` 24, ‏`uinput_setup` 92, ‏`uinput_abs_setup` 28. מספרי
ה-ioctl מחושבים כמו `_IOC` בקרנל (‏dir<<30 | size<<16 | type<<8 | nr).
"""

from __future__ import annotations

import os
import select
import struct
import time
from pathlib import Path

try:
    import fcntl
except ImportError:                              # ווינדוס: המודול נטען, הטסטים מדלגים בהצהרה
    fcntl = None  # type: ignore[assignment]

EV_SYN, EV_KEY, EV_REL, EV_ABS = 0, 1, 2, 3
SYN_REPORT = 0
REL_X, REL_Y = 0, 1
ABS_X, ABS_Y = 0, 1
BTN_LEFT, BTN_TOUCH = 0x110, 0x14A

INPUT_EVENT = struct.Struct("qqHHi")            # timeval(2×long) type code value
ABSINFO = struct.Struct("iiiiii")               # value min max fuzz flat resolution
UINPUT_SETUP = struct.Struct("HHHH80sI")        # input_id, name[80], ff_effects_max
UINPUT_ABS_SETUP = struct.Struct("Hxxiiiiii")   # code, pad, absinfo

_IOC_WRITE, _IOC_READ = 1, 2


def _ioc(direction: int, kind: str, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord(kind) << 8) | nr


def EVIOCGBIT(ev_type: int, size: int) -> int:
    return _ioc(_IOC_READ, "E", 0x20 + ev_type, size)


def EVIOCGABS(code: int) -> int:
    return _ioc(_IOC_READ, "E", 0x40 + code, ABSINFO.size)


UI_DEV_CREATE = _ioc(0, "U", 1, 0)
UI_DEV_DESTROY = _ioc(0, "U", 2, 0)
UI_DEV_SETUP = _ioc(_IOC_WRITE, "U", 3, UINPUT_SETUP.size)
UI_ABS_SETUP = _ioc(_IOC_WRITE, "U", 4, UINPUT_ABS_SETUP.size)
UI_SET_EVBIT = _ioc(_IOC_WRITE, "U", 100, 4)
UI_SET_KEYBIT = _ioc(_IOC_WRITE, "U", 101, 4)
UI_SET_RELBIT = _ioc(_IOC_WRITE, "U", 102, 4)
UI_SET_ABSBIT = _ioc(_IOC_WRITE, "U", 103, 4)

UINPUT_PATH = "/dev/uinput"


def uinput_available() -> bool:
    return fcntl is not None and os.access(UINPUT_PATH, os.W_OK)


def find_event_device(name: str, timeout: float = 5.0) -> Path:
    """‏`/dev/input/eventN` של ההתקן ששמו `name` — הגבוה ביותר, כלומר
    החדש ביותר, אם נשארה שארית מריצה שנהרגה. לא נמצא בזמן = כישלון בשם."""
    deadline = time.monotonic() + timeout
    while True:
        matches = []
        for sys_dir in Path("/sys/class/input").glob("event*"):
            try:
                if (sys_dir / "device" / "name").read_text().strip() == name:
                    matches.append(int(sys_dir.name[len("event"):]))
            except OSError:
                continue
        if matches:
            node = Path(f"/dev/input/event{max(matches)}")
            if node.exists():
                return node
        assert time.monotonic() < deadline, f"no /dev/input/event* named {name!r}"
        time.sleep(0.05)


def has_bit(fd: int, ev_type: int, code: int) -> bool:
    """‏EVIOCGBIT: האם ההתקן מצהיר על `code` בסוג `ev_type` (‏0 = סוגי אירועים)."""
    buf = bytearray(128)
    fcntl.ioctl(fd, EVIOCGBIT(ev_type, len(buf)), buf)
    return bool(buf[code // 8] >> (code % 8) & 1)


def absinfo(fd: int, code: int) -> tuple[int, int, int]:
    """‏(value, minimum, maximum) של ציר מוחלט."""
    buf = bytearray(ABSINFO.size)
    fcntl.ioctl(fd, EVIOCGABS(code), buf)
    value, minimum, maximum, _fuzz, _flat, _res = ABSINFO.unpack(buf)
    return value, minimum, maximum


def read_events(fd: int, timeout: float) -> list[tuple[int, int, int]]:
    """כל האירועים שהגיעו עד `timeout` שניות של שקט: ‏[(type, code, value)]."""
    events: list[tuple[int, int, int]] = []
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return events
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            return events
        try:
            raw = os.read(fd, INPUT_EVENT.size * 64)
        except BlockingIOError:
            continue
        for offset in range(0, len(raw) - INPUT_EVENT.size + 1, INPUT_EVENT.size):
            _sec, _usec, ev_type, code, value = INPUT_EVENT.unpack_from(raw, offset)
            events.append((ev_type, code, value))


class UinputDevice:
    """התקן uinput מזויף שהטסט יוצר — הצד שהגואי קורא.

    ‏`abs=(max_x, max_y)` = מצביע מוחלט (‏ABS_X/ABS_Y + BTN_LEFT); ‏`touch=True`
    מוסיף BTN_TOUCH (מסך מגע); ‏`rel=True` = עכבר יחסי (‏REL_X/REL_Y + BTN_LEFT)."""

    def __init__(self, name: str, *, abs: tuple[int, int] | None = None,
                 touch: bool = False, rel: bool = False, keys: tuple[int, ...] = ()):
        self.name = name
        self.fd = os.open(UINPUT_PATH, os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        # #1918: `keys` = a keys-only device (the ACPI "Power Button": KEY_POWER and nothing else)
        for code in keys or (BTN_LEFT,):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
        if touch:
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, BTN_TOUCH)
        if abs is not None:
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_ABS)
            for code, maximum in ((ABS_X, abs[0]), (ABS_Y, abs[1])):
                fcntl.ioctl(self.fd, UI_SET_ABSBIT, code)
                fcntl.ioctl(self.fd, UI_ABS_SETUP,
                            UINPUT_ABS_SETUP.pack(code, 0, 0, maximum, 0, 0, 0))
        if rel:
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_REL)
            fcntl.ioctl(self.fd, UI_SET_RELBIT, REL_X)
            fcntl.ioctl(self.fd, UI_SET_RELBIT, REL_Y)
        setup = UINPUT_SETUP.pack(0x06, 0x1D6B, 0x0949, 1, name.encode(), 0)  # BUS_VIRTUAL
        fcntl.ioctl(self.fd, UI_DEV_SETUP, setup)
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        self.node = find_event_device(name)

    def emit(self, *events: tuple[int, int, int]) -> None:
        """שולח את האירועים ואחריהם SYN_REPORT, בכתיבה אחת."""
        frame = b"".join(INPUT_EVENT.pack(0, 0, t, c, v) for t, c, v in events)
        frame += INPUT_EVENT.pack(0, 0, EV_SYN, SYN_REPORT, 0)
        written = os.write(self.fd, frame)
        assert written == len(frame), f"short uinput write {written}/{len(frame)}"

    def close(self) -> None:
        if self.fd >= 0:
            try:
                fcntl.ioctl(self.fd, UI_DEV_DESTROY)
            finally:
                os.close(self.fd)
                self.fd = -1

    def __enter__(self) -> "UinputDevice":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
