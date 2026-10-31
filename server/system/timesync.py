"""#1773: chrony client source, server ACLs, and measured clock status."""
from __future__ import annotations
import ipaddress, re, sqlite3, subprocess, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from server.db import get_setting, journal

SETTING = "ntp_servers"
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

def status(hooks: Hooks) -> dict:
    answer = {"synchronized": None, "server": None, "offset_ms": None, "checked_at": hooks["now"]()}
    try: tracking, sources = hooks["tracking"](), hooks["sources"]()
    except Exception: return answer
    selected = re.search(r"(?m)^\^\*\s+(\S+)", sources)
    answer["synchronized"] = _tracking_value(tracking, "Leap status") == "Normal" and selected is not None
    answer["server"] = selected.group(1) if selected else None
    answer["offset_ms"] = _offset_ms(_tracking_value(tracking, "System time"))
    return answer

def check_now(hooks: Hooks) -> dict:
    hooks["restart"]()
    for attempt in range(16):
        result = status(hooks)
        if result["synchronized"] is True: return result
        if attempt < 15: hooks["sleep"](1)
    raise TimeoutError("שרת הזמן לא ענה תוך 15 שניות")

def apply_on_start(conn: sqlite3.Connection, hooks: Hooks) -> None:
    value = configured(conn)
    try: apply(value, hooks)
    except Exception as exc: journal(conn, "timesync_apply_failed", f"{value or 'אוטומטי'}: {exc}")
    else: journal(conn, "timesync_applied", value or "אוטומטי")
