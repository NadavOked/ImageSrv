"""Validation, host discovery, and apply orchestration for the first-boot wizard."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from server import hostname_rule, ifaddr, login_guard, users
from server.atomicfile import write_atomic
from server.console_tls import ensure_console_cert
from server.interserver_auth import generate_self_signed

HOSTNAME_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
IFNAME_RE = re.compile(r"[A-Za-z0-9._-]+\Z")
FP_RE = re.compile(r"(?:[0-9A-F]{2}:){31}[0-9A-F]{2}\Z")
ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def strip_ansi(text: str) -> str:
    """Remove terminal CSI/OSC control sequences before exposing output to HTML."""
    return ANSI_RE.sub("", text)


def _ipv4(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).version == 4
    except ValueError:
        return False


def _netmask(value: str) -> bool:
    try:
        ipaddress.IPv4Network(f"0.0.0.0/{value}")
        return True
    except (ValueError, ipaddress.NetmaskValueError):
        return False


def list_interfaces(sys_net: Path = Path("/sys/class/net")) -> list[dict]:
    """Return real interfaces plus the current IPv4 data reported by ``ip -j``."""
    if not sys_net.is_dir():
        return []
    try:
        proc = subprocess.run(
            ["ip", "-j", "address", "show"], capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=5, check=True,
        )
        by_name = {row.get("ifname"): row for row in json.loads(proc.stdout)}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, TypeError):
        by_name = {}
    result: list[dict] = []
    for entry in sorted(sys_net.iterdir(), key=lambda p: p.name):
        if entry.name == "lo" or not IFNAME_RE.fullmatch(entry.name):
            continue
        row = by_name.get(entry.name, {})
        addresses = [
            f"{a.get('local')}/{a.get('prefixlen')}"
            for a in row.get("addr_info", [])
            if a.get("family") == "inet" and a.get("local")
        ]
        def read(name: str, default: str = "") -> str:
            try:
                return (entry / name).read_text(encoding="utf-8").strip()
            except OSError:
                return default
        operstate = read("operstate", str(row.get("operstate", "unknown"))).lower()
        carrier = read("carrier")
        if carrier == "1":
            link_label = "מחובר"
        elif operstate == "down" and not carrier:
            link_label = "לא מודלק"
        elif operstate == "up" and carrier == "0":
            link_label = "אין קישור"
        else:
            link_label = "מנותק"
        result.append({
            "name": entry.name,
            "mac": read("address"),
            "link": operstate,
            "carrier": carrier or None,
            "link_label": link_label,
            "addresses": addresses,
            "current_ip": addresses[0] if addresses else None,
        })
    return result


def read_fact(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def script_command(path: Path) -> list[str]:
    return [sys.executable, str(path)] if path.suffix.lower() == ".py" else ["bash", str(path)]


@dataclass(frozen=True)
class WizardConfig:
    role: str
    primary_url: str
    interface: str
    mode: str
    address: str
    netmask: str
    gateway: str
    dns: str
    hostname: str
    password: str
    password_confirm: str
    current_password: str = ""

    @classmethod
    def from_json(cls, body: dict) -> "WizardConfig":
        def value(name: str) -> str:
            raw = body.get(name, "")
            return raw.strip() if isinstance(raw, str) and name != "password" and name != "password_confirm" and name != "current_password" else (raw if isinstance(raw, str) else "")
        return cls(**{name: value(name) for name in cls.__dataclass_fields__})


def validate_config(config: WizardConfig, interface_names: set[str], *, rerun: bool = False) -> dict[str, str]:
    errors: dict[str, str] = {}
    if config.role not in {"standalone", "secondary"}:
        errors["role"] = "יש לבחור תפקיד שרת."
    if config.role == "secondary":
        if not config.primary_url:
            errors["primary_url"] = "חובה להזין את כתובת השרת הראשי עבור שרת משנה."
        else:
            parts = urlsplit(config.primary_url)
            try:
                port = parts.port
            except ValueError:
                port = None
            if (parts.scheme not in {"http", "https"} or not parts.hostname
                    or parts.username or parts.password or port != 8443
                    or not re.fullmatch(r"https?://[A-Za-z0-9._:/-]+", config.primary_url)):
                errors["primary_url"] = "כתובת השרת הראשי אינה תקינה — נדרשת כתובת מלאה שמתחילה ב-http:// או https:// וכוללת פורט 8443."
    if config.interface not in interface_names:
        errors["interface"] = "יש לבחור כרטיס רשת קיים מרשימת הכרטיסים."
    if config.mode not in {"dhcp", "static"}:
        errors["mode"] = "יש לבחור DHCP או כתובת סטטית."
    if config.mode == "static":
        if not _ipv4(config.address):
            errors["address"] = "כתובת ה-IP אינה תקינה (נדרש מבנה IPv4 תקין)."
        if not _netmask(config.netmask):
            errors["netmask"] = "מסכת הרשת אינה תקינה."
        if config.gateway and not _ipv4(config.gateway):
            errors["gateway"] = "כתובת השער אינה תקינה (נדרש מבנה IPv4 תקין)."
        dns_values = [v.strip() for v in config.dns.split(",") if v.strip()]
        if any(not _ipv4(v) for v in dns_values):
            errors["dns"] = "כתובת ה-DNS אינה תקינה (יש להפריד כתובות IPv4 בפסיק)."
    if not config.hostname:
        errors["hostname"] = "חובה להזין שם שרת."
    elif not HOSTNAME_RE.fullmatch(config.hostname):
        errors["hostname"] = "שם השרת יכול להכיל אותיות אנגליות קטנות, ספרות ומקפים בלבד; אסור להתחיל או לסיים במקף."
    elif hostname_rule.refusal(config.hostname):
        # ‏#1997: שם השרת הוא שם מחשב — עד 15 תווים, כמו בקונסולה ובמתקין.
        errors["hostname"] = hostname_rule.refusal(config.hostname, "שם השרת")
    try:
        users.validate_password(config.password)
    except ValueError as exc:
        errors["password"] = str(exc)
    if config.password != config.password_confirm:
        errors["password_confirm"] = "הסיסמאות שהזנת אינן תואמות."
    if rerun and not config.current_password:
        errors["current_password"] = "בהרצה חוזרת חובה להזין את סיסמת admin הקיימת."
    return errors


def check_primary(url: str, timeout: float = 5.0) -> dict:
    """Perform a real TCP/TLS handshake on 8443 and report measured evidence."""
    raw = url.strip()
    parts = urlsplit(raw if "://" in raw else f"https://{raw}")
    if not parts.hostname:
        return {"ok": False, "warning": True, "error": "כתובת השרת הראשי אינה תקינה.", "checked": "לא בוצעה בדיקה"}
    host, port = parts.hostname, 8443
    try:
        resolved = sorted({item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    except OSError as exc:
        return {"ok": False, "warning": True, "error": f"פתרון DNS נכשל: {exc}", "checked": f"DNS עבור {host}"}
    try:
        # Port 8443 requires a client certificate even before pairing. The probe
        # presents a one-use identity; it stores no key and performs no pairing.
        probe_cert, probe_key = generate_self_signed("imagectl-wizard-probe")
        with tempfile.TemporaryDirectory(prefix="imagectl-wizard-probe-") as tmp:
            cert_file, key_file = Path(tmp) / "probe.crt", Path(tmp) / "probe.key"
            cert_file.write_bytes(probe_cert)
            key_file.write_bytes(probe_key)
            if os.name == "posix":
                os.chmod(key_file, 0o600)
            with socket.create_connection((host, port), timeout=timeout) as plain:
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                context.minimum_version = ssl.TLSVersion.TLSv1_3
                context.load_cert_chain(cert_file, key_file)
                with context.wrap_socket(plain, server_hostname=host) as tls:
                    cert = tls.getpeercert(binary_form=True)
                    protocol = tls.version()
        digest = hashlib.sha256(cert).hexdigest().upper()
        fingerprint = ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        names = x509.load_der_x509_certificate(cert).subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        peer_name = names[0].value if names else host
        return {"ok": True, "warning": False, "name": peer_name, "version": protocol,
                "fingerprint": fingerprint, "resolved": resolved,
                "checked": f"TCP 8443 ו-TLS אל {host}"}
    except (OSError, ssl.SSLError) as exc:
        return {"ok": False, "warning": True,
                "error": f"התקשרות אל {raw} בפורט 8443 נכשלה. ודא כי חומת האש שבין האתרים פתוחה.",
                "reason": str(exc), "resolved": resolved, "checked": f"TCP 8443 ו-TLS אל {host}"}
    except Exception as exc:  # certificate generation/decoding is part of the check
        return {"ok": False, "warning": True,
                "error": "בדיקת TLS לשרת הראשי לא הצליחה לרוץ.",
                "reason": f"{type(exc).__name__}: {exc}", "resolved": resolved,
                "checked": f"TCP 8443 ו-TLS אל {host}"}


@dataclass
class WizardPaths:
    source_dir: Path = Path("/opt/imagectl-src")
    app_dir: Path = Path("/opt/imagectl")
    data_dir: Path = Path("/var/lib/imagectl")
    status_file: Path = Path("/etc/imagectl/firstboot.status")
    stamp_file: Path = Path("/var/lib/imagectl/.firstboot-done")
    installer: Path = Path("/opt/imagectl-src/install/setup-boot-server.sh")
    verify: Path = Path("/opt/imagectl/install/verify-boot-payload.sh")
    http_root: Path = Path("/srv/imagectl/boot")


@dataclass
class WizardState:
    paths: WizardPaths
    rerun: bool = False
    dev: bool = False
    interfaces_provider: Callable[[], list[dict]] = list_interfaces
    hostname_setter: Callable[[str], int] | None = None
    shutdown_for_handoff: Callable[[], None] | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _phase: str = field(default="ready", init=False)
    _output: list[str] = field(default_factory=list, init=False)
    _result: dict = field(default_factory=dict, init=False)
    _thread: threading.Thread | None = field(default=None, init=False)
    _attempts: int = field(default=0, init=False)
    _job: str | None = field(default=None, init=False)
    _client_ip: str = field(default="", init=False)

    def interfaces(self) -> list[dict]:
        return self.interfaces_provider()

    def initial(self, nic_fact: Path, role_fact: Path) -> dict:
        nics = self.interfaces()
        nic = read_fact(nic_fact)
        role = read_fact(role_fact)
        by_mac = {str(x.get("mac", "")).lower(): x["name"] for x in nics}
        default_if = by_mac.get(nic.get("mac", "").lower(), "")
        if not default_if and nic.get("interface") in {x["name"] for x in nics}:
            default_if = nic["interface"]
        selected_role = role.get("role", "standalone")
        if selected_role not in {"standalone", "secondary"}:
            selected_role = "standalone"
        return {"rerun": self.rerun, "interfaces": nics, "defaults": {
            "role": selected_role, "primary_url": role.get("primary", ""),
            "interface": default_if, "mode": nic.get("reason", "dhcp") if nic.get("reason") in {"dhcp", "static"} else "dhcp",
            "hostname": socket.gethostname().lower(),
        }}

    def progress(self) -> dict:
        with self._lock:
            return {"state": self._phase,
                    "output": strip_ansi("".join(self._output))[-100_000:],
                    "job": self._job, **self._result}

    def active_job(self) -> str | None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self._job
            return None

    def start_apply(self, config: WizardConfig, client_ip: str = "") -> str:
        with self._lock:
            if self._thread and self._thread.is_alive():
                assert self._job is not None
                return self._job
            self._attempts += 1
            self._client_ip = client_ip
            self._job = uuid.uuid4().hex
            self._phase, self._output, self._result = "validating", [], {}
            self._thread = threading.Thread(target=self._apply, args=(config,), name="wizard-apply", daemon=False)
            self._thread.start()
            return self._job

    def wait(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread:
            thread.join(timeout)

    def _append(self, text: str) -> None:
        with self._lock:
            self._output.append(text)

    def _set(self, phase: str, **result) -> None:
        with self._lock:
            self._phase = phase
            self._result.update(result)

    def _verify_current_admin(self, password: str) -> str | None:
        """None = הסיסמה אומתה; אחרת ההודעה למפעיל.

        ‏#1631: דרך ``login_guard``, עם אותו מפתח (משתמש+IP) של כניסת הקונסולה
        — ההרצה החוזרת אינה ערוץ ניחוש סיסמה בלי השהיה ונעילה, וכשלונות
        בשני הערוצים נספרים יחד."""
        wrong = "סיסמת admin הקיימת שגויה."
        db = self.paths.data_dir / "imagectl.db"
        if not db.is_file():
            return wrong
        ip = self._client_ip or "?"
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT username FROM users WHERE role = 'admin' AND is_builtin = 1"
                " AND disabled_at IS NULL ORDER BY created_at, username LIMIT 1"
            ).fetchone()
            if row is None:   # #1745: no one to check against is not a wrong password
                return "לא נמצא ב-DB מנהל ההתקנה (משתמש admin מקומי פעיל) — אין מול מי לאמת את הסיסמה."
            username = row["username"]
            try:
                login_guard.check(conn, username, ip)
            except login_guard.LoginBlocked as exc:
                return exc.message
            if users.verify(conn, username, password) == "admin":
                login_guard.clear(conn, username, ip)
                return None
            login_guard.record_failure(conn, username, ip)
            return wrong
        finally:
            conn.close()

    def _run(self, argv: list[str], *, password: str | None = None, handoff: bool = False) -> int:
        pass_fds: tuple[int, ...] = ()
        read_fd = write_fd = child_handoff = parent_handoff = None
        env = os.environ.copy()
        if password is not None and os.name == "posix":
            read_fd, write_fd = os.pipe()
            pass_fds += (read_fd,)
        if handoff and os.name == "posix":
            parent_sock, child_sock = socket.socketpair()
            parent_handoff, child_handoff = parent_sock, child_sock
            child_sock.set_inheritable(True)
            pass_fds += (child_sock.fileno(),)
            env["IMAGECTL_WIZARD_HANDOFF_FD"] = str(child_sock.fileno())
        popen_argv = argv
        if read_fd is not None:
            # Fixed wrapper, positional arguments only: map the inherited pipe
            # to fd 3 without preexec_fn (unsafe in this threaded server).
            popen_argv = ["bash", "-c", 'exec 3<&"$1"; shift; exec "$@"',
                          "imagectl-wizard-fd3", str(read_fd), *argv]
        kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT,
                  "stdin": subprocess.PIPE if password is not None and os.name != "posix" else subprocess.DEVNULL,
                  "text": True, "encoding": "utf-8", "errors": "replace", "env": env}
        if os.name == "posix":
            kwargs["pass_fds"] = pass_fds
        proc = subprocess.Popen(popen_argv, **kwargs)
        if read_fd is not None and write_fd is not None:
            os.close(read_fd)
            os.write(write_fd, password.encode("utf-8"))
            os.close(write_fd)
        elif password is not None and proc.stdin is not None:
            proc.stdin.write(password)
            proc.stdin.close()
        if child_handoff is not None:
            child_handoff.close()

        def handoff_worker() -> None:
            assert parent_handoff is not None
            try:
                signal = parent_handoff.recv(64)
                if signal.startswith(b"release-8081"):
                    self._set("handoff")
                    if self.shutdown_for_handoff:
                        self.shutdown_for_handoff()
                    parent_handoff.sendall(b"released\n")
            finally:
                parent_handoff.close()

        watcher = None
        if parent_handoff is not None:
            watcher = threading.Thread(target=handoff_worker, daemon=True)
            watcher.start()
        assert proc.stdout is not None
        for line in proc.stdout:
            self._append(line.replace(password, "[REDACTED]") if password else line)
        rc = proc.wait()
        if watcher:
            watcher.join(timeout=2)
        return rc

    def _status_done(self, config: WizardConfig) -> bool:
        self.paths.status_file.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(
            self.paths.status_file,
            f"state=done\ntimestamp={utc_now()}\nservers_if={config.interface}\nrole={config.role}\n",
        )
        return read_fact(self.paths.status_file).get("state") == "done"

    def _fail(self, state: str, message: str, config: WizardConfig) -> None:
        self.paths.status_file.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(
            self.paths.status_file,
            f"state={state}\ntimestamp={utc_now()}\nmessage={message}\nservers_if={config.interface}\nrole={config.role}\n",
        )
        self._set(state, error=message)

    def _apply(self, config: WizardConfig) -> None:
        try:
            refused = self._verify_current_admin(config.current_password) if self.rerun else None
            if refused:
                self._fail("validation-failed", refused, config)
                return
            self._set("hostname")
            if not self.dev:
                rc = (self.hostname_setter(config.hostname) if self.hostname_setter
                      else self._run(["hostnamectl", "set-hostname", config.hostname]))
                if rc:
                    self._fail("hostname-failed", "hostnamectl set-hostname נכשל.", config)
                    return
            argv = script_command(self.paths.installer) + ["--servers-if", config.interface,
                    "--servers-mode", config.mode]
            if config.mode == "static":
                argv += ["--servers-address", config.address, "--servers-netmask", config.netmask]
                if config.gateway:
                    argv += ["--servers-gateway", config.gateway]
                if config.dns:
                    argv += ["--servers-dns", config.dns]
            argv += ["--console-host", config.interface, "--storage-role", config.role]
            if config.role == "secondary":
                argv += ["--primary-url", config.primary_url]
            argv += ["--admin-user", "admin", "--admin-pass-fd", "3" if os.name == "posix" else "0"]
            if self.rerun or self._attempts > 1:
                argv.append("--force")
            self._set("installing")
            if self.dev:
                self._append("dev: fake setup-boot-server.sh completed\n")
                install_rc = 0
            else:
                install_rc = self._run(argv, password=config.password, handoff=True)
            if install_rc:
                self._fail("installer-failed", "setup-boot-server.sh נכשל; הפלט המלא מוצג למעלה.", config)
                return
            self._set("verifying")
            verify = script_command(self.paths.verify) + ["--app-dir", str(self.paths.app_dir),
                      "--http-root", str(self.paths.http_root), "--server-url", "http://127.0.0.1:8080"]
            verify_rc = 0 if self.dev else self._run(verify)
            if self.dev:
                self._append("dev: fake verify-boot-payload.sh completed\n")
            if verify_rc:
                self._fail("verify-failed", "verify-boot-payload.sh נכשל; ההתקנה אינה מסומנת כהושלמה.", config)
                return
            if self.dev:
                console_host = config.address if config.mode == "static" else ""
                if not console_host:
                    nic = next((row for row in self.interfaces() if row["name"] == config.interface), {})
                    console_host = str(nic.get("current_ip") or "").split("/", 1)[0]
            else:
                console_host = ifaddr.read_ipv4(config.interface)
            if not console_host or not _ipv4(console_host):
                self._fail("check-error", f"לא נקראה כתובת IPv4 תקינה לכרטיס {config.interface}; לא הונפקה תעודת קונסולה.", config)
                return
            tls = ensure_console_cert(self.paths.data_dir, console_host, hostname=config.hostname)
            if not FP_RE.fullmatch(tls.fingerprint_sha256):
                self._fail("check-error", "טביעת תעודת הקונסולה לא נקראה בצורה תקינה.", config)
                return
            if not self._status_done(config):
                self._fail("check-error", "firstboot.status נכתב אך לא נקרא בחזרה כ-state=done.", config)
                return
            self.paths.stamp_file.parent.mkdir(parents=True, exist_ok=True)
            self.paths.stamp_file.touch()
            console_url = f"https://{console_host}:8081"
            self._set("done", console_url=console_url, user="admin",
                      fingerprint=tls.fingerprint_sha256)
        except Exception as exc:  # visible terminal state; never turn an exception into success
            self._append(f"wizard: {type(exc).__name__}: {exc}\n")
            self._fail("check-error", f"האשף נכשל: {exc}", config)
