"""#1773: chrony client source, server ACLs, and measured clock status."""
from __future__ import annotations
import ipaddress, re, sqlite3, subprocess, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from server.db import get_setting, journal, set_settings

SETTING = "ntp_servers"
#: #2765: a third source -- no time server at all. "1" = the operator set the time by
#: hand (the machine has no internet), and ``MANUAL_AT_SETTING`` is the UTC moment he did.
#: ``SETTING`` stays empty then: "automatic" and "manual" are different choices.
MANUAL_SETTING, MANUAL_AT_SETTING = "ntp_manual", "ntp_manual_at"
MODE_AUTO, MODE_ORG, MODE_MANUAL = "auto", "org", "manual"
#: after this many days the health row says how old the manual time is (still information, never a problem)
MANUAL_STALE_DAYS = 30
CONF_DIR = Path("/etc/chrony/conf.d")
CLIENT_CONF, SERVER_CONF = CONF_DIR / "imagesrv.conf", CONF_DIR / "imagesrv-server.conf"
MAIN_CONF = Path("/etc/chrony/chrony.conf")
DEBIAN_POOLS = "".join(f"pool {n}.debian.pool.ntp.org iburst\n" for n in range(4))
Hooks = dict[str, Callable]
_HOST = re.compile(r"^(?=.{1,253}\.?$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.?$")

def validate(value: object) -> str:
    parts = str(value or "").strip().split()
    if len(parts) > 4: raise ValueError(f"כתובת שרת זמן לא תקינה: {parts[4]}")
    for item in parts:
        try: ipaddress.ip_address(item)
        except ValueError:
            if not _HOST.fullmatch(item): raise ValueError(f"כתובת שרת זמן לא תקינה: {item}")
    return " ".join(parts)

def configured(conn: sqlite3.Connection) -> str:
    return validate(get_setting(conn, SETTING) or "")

def is_manual(conn: sqlite3.Connection) -> bool:
    return get_setting(conn, MANUAL_SETTING) == "1"

def manual_at(conn: sqlite3.Connection) -> str:
    """UTC ISO of the last manual setting; "" = not recorded (said as unknown, never guessed)."""
    return get_setting(conn, MANUAL_AT_SETTING) or ""

def mode(conn: sqlite3.Connection) -> str:
    if is_manual(conn): return MODE_MANUAL
    return MODE_ORG if configured(conn) else MODE_AUTO

_LOCAL_TIME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?$")

def parse_local(value: object, zone: str) -> int:
    """The operator's "YYYY-MM-DD HH:MM" (or datetime-local's ``T``) in the server's zone -> epoch seconds."""
    match = _LOCAL_TIME.fullmatch(str(value or "").strip())
    if not match: raise ValueError("תאריך ושעה בצורה 2026-11-09 14:32")
    year, month, day, hour, minute, second = (int(part or 0) for part in match.groups())
    if not 2000 <= year <= 2099: raise ValueError("השנה צריכה להיות בין 2000 ל-2099")
    try:
        from zoneinfo import ZoneInfo
        local = datetime(year, month, day, hour, minute, second, tzinfo=ZoneInfo(zone))
    except ValueError as exc: raise ValueError(f"תאריך או שעה לא קיימים: {exc}") from exc
    except Exception as exc: raise RuntimeError(f"אזור הזמן {zone} לא נקרא: {exc}") from exc
    return int(local.timestamp())

def local_text(iso: str, zone: str) -> str:
    """"DD/MM/YYYY HH:MM" in the server's zone; UTC (said) when the zone cannot be read."""
    try: moment = datetime.fromisoformat(iso)
    except ValueError: return iso
    if moment.tzinfo is None: moment = moment.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return moment.astimezone(ZoneInfo(zone)).strftime("%d/%m/%Y %H:%M")
    except Exception: return moment.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

def manual_age_days(iso: str, now: datetime) -> int | None:
    try: moment = datetime.fromisoformat(iso)
    except ValueError: return None
    if moment.tzinfo is None: moment = moment.replace(tzinfo=timezone.utc)
    return max(0, (now - moment).days)

def _run(command: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL, check=False)

def _read(command: list[str]) -> str:
    try: done = _run(command)
    except (OSError, subprocess.TimeoutExpired) as exc: raise RuntimeError(f"הפקודה לא רצה: {exc}") from exc
    if done.returncode: raise RuntimeError(f"הפקודה החזירה {done.returncode}: {(done.stderr or done.stdout).strip()}")
    return done.stdout

def _ensure_confdir() -> None:
    text = MAIN_CONF.read_text(encoding="utf-8")
    # The managed include is the single source of upstreams. Debian's stock
    # pool (and DHCP sourcedir, if present) would otherwise stay active beside
    # an organisation source because chrony includes are additive. Debian's
    # `makestep 1 3` goes too (#1814): MAKESTEP in CLIENT_CONF is the one rule.
    text = re.sub(r"(?m)^(\s*)(pool|server|peer|sourcedir|makestep)\s+(.+)$",
                  r"\1# ImageSrv manages sources: \2 \3", text)
    if not re.search(r"(?m)^\s*confdir\s+/etc/chrony/conf\.d\s*(?:#.*)?$", text):
        text = text.rstrip() + "\n\n# Managed by ImageSrv (#1773)\nconfdir /etc/chrony/conf.d\n"
    MAIN_CONF.write_text(text, encoding="utf-8")

#: #1814: step whenever the offset is over 1 s, at every update -- not only the first
#: three (Debian's `makestep 1 3`). A VM resumed from suspend or a reverted snapshot is
#: minutes off; slewing at chrony's 500 ppm takes ~33 h per minute, and TOTP allows +-30 s.
MAKESTEP = "makestep 1.0 -1\n"

def _write_client(value: str) -> None:
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    lines = "".join(f"server {item} iburst\n" for item in value.split()) if value else DEBIAN_POOLS
    CLIENT_CONF.write_text("# Managed by ImageSrv — time source (#1773)\n" + MAKESTEP + lines,
                           encoding="utf-8")

def _write_manual() -> None:
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    CLIENT_CONF.write_text("# Managed by ImageSrv — time source (#2765): manual, no time server\n", encoding="utf-8")

def _set_time(epoch: int) -> bool:
    """Sets the system clock, proves it by reading it back (rule 5), then writes the hardware
    clock. True = the hardware clock too; False = only the running clock (no RTC to write)."""
    _read(["date", "-u", "-s", f"@{epoch}"])
    if abs(time.time() - epoch) > 5: raise RuntimeError("השעה לא נקבעה: השעון נקרא בחזרה בערך אחר")
    try: _read(["hwclock", "--systohc"])
    except RuntimeError: return False
    return True

def _write_server(networks: list[str]) -> None:
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["# Managed by ImageSrv — NTP service per NIC (#1773)"]
    if networks:
        versions = {ipaddress.ip_network(n, strict=False).version for n in networks}
        lines += ["port 123"]
        if 4 in versions: lines.append("bindaddress 0.0.0.0")
        if 6 in versions: lines.append("bindaddress ::")
        lines += ["bindcmdaddress 127.0.0.1", "bindcmdaddress ::1"]
        lines += [f"allow {network}" for network in networks]
    else: lines += ["port 0", "bindcmdaddress 127.0.0.1", "bindcmdaddress ::1"]
    SERVER_CONF.write_text("\n".join(lines) + "\n", encoding="utf-8")

def _restart() -> None: _read(["systemctl", "restart", "chrony"])

def default_hooks() -> Hooks:
    return {"ensure_confdir": _ensure_confdir, "write": _write_client, "write_server": _write_server,
            "write_manual": _write_manual, "set_time": _set_time,
            "restart": _restart, "tracking": lambda: _read(["chronyc", "-n", "tracking"]),
            "sources": lambda: _read(["chronyc", "-n", "sources"]), "sleep": time.sleep,
            "now": lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")}

def apply(value: str, hooks: Hooks) -> dict:
    value = validate(value)
    try: hooks["ensure_confdir"](); hooks["write"](value); hooks["restart"]()
    except Exception as exc: raise RuntimeError(str(exc)) from exc
    result = status(hooks)
    if result["synchronized"] is None: raise RuntimeError("מצב סנכרון השעון לא נקרא לאחר השינוי")
    return result

def apply_manual(epoch: int, hooks: Hooks) -> dict:
    """#2765: no time server; the time is the operator's. chrony is rewritten without sources and
    restarted FIRST -- a chrony that still had a source could step the clock back after we set it."""
    try: hooks["ensure_confdir"](); hooks["write_manual"](); hooks["restart"]()
    except Exception as exc: raise RuntimeError(str(exc)) from exc
    try: rtc = hooks["set_time"](epoch)
    except Exception as exc: raise RuntimeError(str(exc)) from exc
    return {"set_at": datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds"),
            "rtc_written": rtc, "status": status(hooks)}

def apply_server(networks: list[str], hooks: Hooks) -> None:
    checked = [str(ipaddress.ip_network(n, strict=False)) for n in networks]
    hooks["ensure_confdir"](); hooks["write_server"](list(dict.fromkeys(checked))); hooks["restart"]()

def _tracking_value(text: str, key: str) -> str | None:
    match = re.search(rf"(?mi)^\s*{re.escape(key)}\s*:\s*(.+?)\s*$", text)
    return match.group(1).strip() if match else None

def _offset_ms(value: str | None) -> float | None:
    """chronyc's "System time": positive = the clock is fast (ahead), negative = slow."""
    match = re.match(r"([+-]?[\d.]+)\s+(seconds?|milliseconds?|microseconds?)\b(?:\s+(fast|slow)\b)?",
                     value or "")
    if not match: return None
    number, unit = float(match.group(1)), match.group(2).lower()
    sign = -1 if (match.group(3) or "").lower() == "slow" else 1
    return sign * number * (1000 if unit.startswith("second") else .001 if unit.startswith("micro") else 1)

#: #2796: one row of `chronyc -n sources` -- name, LastRx (seconds, or m/h/d/y) and "Last
#: sample": the number left of the brackets is the measured offset, adjusted for slews since.
#: A source never heard from has LastRx "-" and does not match. Units per chronyc: ns/us/ms/s.
_SAMPLE = re.compile(r"(?m)^[\^=#][*+\-?x~]\s+(\S+)\s+\d+\s+\d+\s+[0-7]+\s+(\d+)([mhdy]?)\s+"
                     r"([+-]?\d+(?:\.\d+)?)(ns|us|ms|s)\[")
_SAMPLE_MS = {"ns": 1e-6, "us": 1e-3, "ms": 1.0, "s": 1000.0}
_AGE_S = {"": 1, "m": 60, "h": 3600, "d": 86400, "y": 31536000}

def _last_sample(sources: str) -> dict | None:
    """#2796: the freshest real measurement, from any source -- chosen or not.

    Unsynchronised, tracking's "System time" is chrony's estimate from its last clock update
    (it stayed -0.019 ms while a resumed VM was 352 s behind); a source's last sample is what
    the wire said. Sign as chronyc documents it -- positive = the local clock is ahead -- the
    same as ``offset_ms``. None = no source has a sample."""
    best = None
    for match in _SAMPLE.finditer(sources):
        age = int(match.group(2)) * _AGE_S[match.group(3)]
        if best is None or age < best["age_s"]:
            best = {"server": match.group(1), "age_s": age,
                    "offset_ms": float(match.group(4)) * _SAMPLE_MS[match.group(5)]}
    return best

def status(hooks: Hooks) -> dict:
    answer = {"synchronized": None, "server": None, "offset_ms": None, "last_sample": None,
              "checked_at": hooks["now"]()}
    try: tracking, sources = hooks["tracking"](), hooks["sources"]()
    except Exception: return answer
    selected = re.search(r"(?m)^\^\*\s+(\S+)", sources)
    answer["synchronized"] = _tracking_value(tracking, "Leap status") == "Normal" and selected is not None
    answer["server"] = selected.group(1) if selected else None
    answer["offset_ms"] = _offset_ms(_tracking_value(tracking, "System time"))
    answer["last_sample"] = _last_sample(sources)
    return answer

def check_now(hooks: Hooks) -> dict:
    hooks["restart"]()
    for attempt in range(16):
        result = status(hooks)
        if result["synchronized"] is True: return result
        if attempt < 15: hooks["sleep"](1)
    raise TimeoutError("שרת הזמן לא ענה תוך 15 שניות")

def apply_on_start(conn: sqlite3.Connection, hooks: Hooks) -> None:
    if is_manual(conn):   # #2765: the source is "none" -- the time itself is kept by the clock, not set again
        try: hooks["ensure_confdir"](); hooks["write_manual"](); hooks["restart"]()
        except Exception as exc: journal(conn, "timesync_apply_failed", f"ידני: {exc}")
        else: journal(conn, "timesync_applied", "ידני — בלי שרת זמן")
        return
    value = configured(conn)
    try: apply(value, hooks)
    except Exception as exc: journal(conn, "timesync_apply_failed", f"{value or 'אוטומטי'}: {exc}")
    else: journal(conn, "timesync_applied", value or "אוטומטי")

def save(conn: sqlite3.Connection, value: str, set_at: str | None = None) -> None:
    """One transaction: the server list, and the manual flag with the moment it was set
    (``set_at`` None = not manual; the flag goes). Read back before it counts as saved."""
    if set_at is None:
        set_settings(conn, {SETTING: value}, remove=(MANUAL_SETTING, MANUAL_AT_SETTING))
    else:
        set_settings(conn, {SETTING: "", MANUAL_SETTING: "1", MANUAL_AT_SETTING: set_at})
    if is_manual(conn) != (set_at is not None) or get_setting(conn, SETTING) != (value if set_at is None else ""):
        raise RuntimeError("מקור השעה לא נקרא בחזרה כפי שנכתב")
