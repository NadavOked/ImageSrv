"""‏#703 (tracer 5): הקונסולה (8081) מוגשת ב-HTTPS עם תעודה חתומה-עצמית.

מה שנבדק כאן, כהתנהגות:
- הפעלה ראשונה עם ‎--console-host‏ שאינו loopback **יוצרת** תעודה+מפתח
  ב-``<data_dir>/console-tls/`` — המפתח 0600, ה-SAN הוא כרטיס הניהול
  ושם המארח — והפעלה שנייה **טוענת את אותה תעודה** (אותה טביעת אצבע).
- ‏``--console-tls off`` מתקבל רק על loopback; על כרטיס ניהול — סירוב
  בשם שני הדגלים, לא נפילה שקטה ל-HTTP.
- ‏``main`` מוליך את התעודה **למאזיני הקונסולה בלבד** (כולל המאזין
  הכפול על 127.0.0.1, #904) — לא לסוכן (8080).
- עוגיית ההתחברות נושאת ``Secure`` תחת TLS, ולא בלעדיו (שומר מפני
  תיקון-יתר שהיה שובר את הרצת הפיתוח על http://127.0.0.1).
- ‏``/me`` מדווח ``tls`` עם טביעת האצבע (לשורת הסטטוס), ומסך הפורטים
  קורא ל-8081 ‏HTTPS.
- **לחיצת-יד אמיתית**: ‏uvicorn עם התעודה שנוצרה, לקוח stdlib שסומך רק
  עליה — 200. זו הראיה ש-``ssl_certfile``/``ssl_keyfile`` של stdlib
  מקבלים את המפתח (EC P-256, PKCS8) שהמחולל המשותף מפיק.
"""

from __future__ import annotations

import os
import re
import socket
import ssl
import stat
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from cryptography import x509
from cryptography.x509.oid import NameOID

import server.main
from server import console_tls
from test_console_bind import run_main
from tools.e2e import harness

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None

FP_RE = re.compile(r"^([0-9A-F]{2}:){31}[0-9A-F]{2}$")


# ---------- התעודה על הדיסק ----------

def test_first_start_creates_cert_and_0600_key_with_the_management_sans(tmp_path: Path):
    tls = console_tls.ensure_console_cert(tmp_path / "data", "10.0.0.1", "imagectl-srv")

    assert tls.cert_path == tmp_path / "data" / "console-tls" / "console.crt"
    assert tls.cert_path.is_file() and tls.key_path.is_file()
    assert b"BEGIN CERTIFICATE" in tls.cert_path.read_bytes()
    assert b"PRIVATE KEY" in tls.key_path.read_bytes()
    cert = x509.load_pem_x509_certificate(tls.cert_path.read_bytes())
    common_names = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    assert [item.value for item in common_names] == ["imagesrv-console"]
    if os.name == "posix":
        assert stat.S_IMODE(tls.key_path.stat().st_mode) == 0o600
    assert set(tls.sans) == {"10.0.0.1", "imagectl-srv"}
    assert FP_RE.match(tls.fingerprint_sha256), tls.fingerprint_sha256


@pytest.mark.parametrize("ahead_hours", [3, 14])
def test_a_cert_issued_while_the_rtc_ran_ahead_is_valid_once_the_clock_is_corrected(
        tmp_path: Path, ahead_hours: int):
    """‏#2139 (rc3, Hyper-V 24/10): שעון חומרה בשעון מקומי — 3 שעות קדימה
    עד ש-chrony מתקן. תעודה שנוצרה בחלון הזה נבדקת בשעון המתוקן (וב-UTC+14,
    ההיסט הגדול ביותר), כמו שדפדפן בודק אותה: היא כבר בתוקף, ולא בעוד שעות."""
    import datetime
    tls = console_tls.ensure_console_cert(tmp_path / "data", "10.68.0.82", "rc3-hv")
    issuing_clock = datetime.datetime.now(datetime.timezone.utc)   # the clock that ran ahead
    corrected = issuing_clock - datetime.timedelta(hours=ahead_hours)
    info = console_tls.validate_upload(tls.cert_pem, tls.key_pem, now=corrected)
    assert info["fingerprint_sha256"] == tls.fingerprint_sha256
    cert = x509.load_pem_x509_certificate(tls.cert_pem)
    assert cert.not_valid_after_utc - issuing_clock > datetime.timedelta(days=3640)   # the term itself unchanged


def test_second_start_loads_the_same_cert_not_a_new_one(tmp_path: Path):
    """הדפדפן מאשר פעם אחת — תעודה שמתחלפת בכל הפעלה הייתה מבקשת אישור
    בכל בוקר, ומרגילה את המפעיל ללחוץ "המשך" בלי לקרוא."""
    first = console_tls.ensure_console_cert(tmp_path / "data", "10.0.0.1", "srv")
    cert_bytes = first.cert_path.read_bytes()
    second = console_tls.ensure_console_cert(tmp_path / "data", "10.0.0.1", "srv")

    assert second.fingerprint_sha256 == first.fingerprint_sha256
    assert second.cert_path.read_bytes() == cert_bytes


def test_self_signed_cert_is_extended_when_console_address_changes(tmp_path: Path):
    first = console_tls.ensure_console_cert(tmp_path / "data", "10.0.0.1", "srv")
    second = console_tls.ensure_console_cert(tmp_path / "data", "10.0.0.2", "srv")

    assert second.regenerated is True
    assert second.previous_fingerprint_sha256 == first.fingerprint_sha256
    assert second.fingerprint_sha256 != first.fingerprint_sha256
    assert set(second.sans) == {"10.0.0.2", "srv"}
    assert second.cert_path.with_name("console.crt.prev").read_bytes() != second.cert_path.read_bytes()
    assert second.key_path.with_name("console.key.prev").is_file()


def test_uploaded_self_signed_cert_is_never_regenerated_for_a_new_host(tmp_path: Path):
    from server.interserver_auth import generate_self_signed

    data = tmp_path / "data"
    cert, key = generate_self_signed("operator-cert", dns_sans=["imagectl.college.local"])
    console_tls.install_upload(data, cert, key)
    before = console_tls.fingerprint_sha256(cert)

    loaded = console_tls.ensure_console_cert(data, "10.0.0.8", "srv")

    assert loaded.fingerprint_sha256 == before
    assert loaded.regenerated is False
    assert not loaded.cert_path.with_name(console_tls.GENERATED_MARKER).exists()


def test_half_an_identity_is_refused_not_regenerated(tmp_path: Path):
    """תעודה בלי מפתח: יצירה מחדש שקטה הייתה מחליפה תעודה שדפדפנים כבר
    אישרו; הכשל נאמר בשמו."""
    tls = console_tls.ensure_console_cert(tmp_path / "data", "10.0.0.1", "srv")
    tls.key_path.unlink()

    with pytest.raises(console_tls.ConsoleTLSError, match="תעודה בלי מפתח"):
        console_tls.ensure_console_cert(tmp_path / "data", "10.0.0.1", "srv")


# ---------- הדגל ----------

def test_tls_off_is_refused_off_loopback_and_named():
    with pytest.raises(console_tls.ConsoleTLSError) as exc:
        console_tls.check_tls_flag("off", "10.0.0.1")
    assert "--console-tls" in str(exc.value) and "--console-host" in str(exc.value)
    console_tls.check_tls_flag("off", "127.0.0.1")        # loopback — מותר
    console_tls.check_tls_flag("on", "10.0.0.1")           # TLS — תמיד מותר


def test_main_refuses_tls_off_on_a_management_nic(tmp_path: Path, monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        run_main(tmp_path, monkeypatch,
                 "--console-host", "10.0.0.1", "--console-tls", "off")
    assert exc.value.code == 2
    err = capsys.readouterr().err
    # ‏"unrecognized arguments: --console-tls" של argparse גם יוצא 2 ומזכיר
    # את הדגל — הראיה שהסירוב הוא **שלנו** היא ההסבר על loopback.
    assert "--console-tls" in err and "loopback" in err, err


# ---------- main → uvicorn ----------

def test_main_gives_the_cert_to_every_console_listener_and_nothing_else(
        tmp_path: Path, monkeypatch):
    """ברירת המחדל היא TLS דלוק. שני מאזיני הקונסולה (כרטיס הניהול +
    ‏127.0.0.1, ‏#904) מקבלים את אותה תעודה מ-``data_dir``; הסוכן והקיוסק
    אינם משתנים (הסוכן הוא http בהכרח — GRUB בלי TLS)."""
    configs = run_main(tmp_path, monkeypatch, "--console-host", "10.0.0.1")

    console = configs["console"]
    assert sorted(c["host"] for c in console) == ["10.0.0.1", "127.0.0.1"]
    canonical = tmp_path / "data" / "console-tls" / "console.crt"
    # #2095: the listeners load the pinned copy of the bytes /me was computed from
    served = canonical.parent / "serving" / "console.crt"
    for cfg in console:
        assert Path(cfg["ssl_certfile"]) == served
        assert Path(cfg["ssl_keyfile"]) == served.with_name("console.key")
    assert served.read_bytes() == canonical.read_bytes()
    assert served.with_name("console.key").read_bytes() == canonical.with_name("console.key").read_bytes()
    for cfg in configs["agent"]:
        assert "ssl_certfile" not in cfg and "ssl_keyfile" not in cfg


def test_main_tls_off_on_loopback_serves_plain_http(tmp_path: Path, monkeypatch):
    configs = run_main(tmp_path, monkeypatch, "--console-tls", "off")

    assert [c["host"] for c in configs["console"]] == ["127.0.0.1"]
    assert "ssl_certfile" not in configs["console"][0]
    assert not (tmp_path / "data" / "console-tls").exists()


def test_e2e_harness_runs_the_console_on_loopback_without_tls(
        tmp_path: Path, monkeypatch):
    """ה-e2e מדבר עם הקונסולה ב-http (לקוח urllib פשוט) — מותר לו כי
    הקונסולה שלו על 127.0.0.1. הדגל חייב גם להתפרס ב-``server.main``
    (#201: argv שנבלע נראה כמו "השרת לא עלה")."""
    captured: list[list[str]] = []
    monkeypatch.setattr(subprocess, "Popen",
                        lambda cmd, **kw: captured.append(list(cmd)) or object())
    harness.start_server(tmp_path)

    args = server.main.build_parser().parse_args(captured[0][3:])
    assert args.console_tls == "off" and args.console_host == "127.0.0.1"
    console_tls.check_tls_flag(args.console_tls, args.console_host)   # מותר


# ---------- הקונסולה: עוגייה, /me, פורטים ----------

def _console(tmp_path: Path, images_root: Path, clock, tls):
    if TestClient is None:
        pytest.skip("fastapi is required")
    from server import users
    from server.app import create_console_app, create_runtime
    from test_console_ports import SS_DNSMASQ
    from test_sender import Recorder

    hooks = {"ss": lambda: SS_DNSMASQ, "unit_active": lambda name: "active",
             "http_get": lambda url: 200, "http_size": lambda url: (200, 1),
             "http_text": lambda url: (200, ""), "interfaces": lambda: [],
             "tftp_root": lambda: tmp_path, "udp_sender_pids": lambda: []}
    # אפליקציית הקונסולה **האמיתית** (8081), לא המעטפת המשולבת: במעטפת
    # ה-``/login`` של allowlist הסוכן (http) נרשם ראשון ומצל על זה של
    # הקונסולה, ועוגיית הסוכן בצדק אינה Secure.
    rt = create_runtime(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                        now_fn=clock, health_hooks=hooks, console_tls=tls,
                        sender_runner=Recorder(block=True))
    app = create_console_app(rt)
    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    base = "https://testserver" if tls else "http://testserver"
    client = TestClient(app, base_url=base)
    login = client.post("/api/console/login",
                        json={"username": "noc", "password": "admin-pass-123"})
    assert login.status_code == 200, login.text
    return client, login


def test_login_cookie_is_secure_under_tls(tmp_path: Path, images_root: Path, clock):
    tls = console_tls.ensure_console_cert(tmp_path / "data", "10.0.0.1", "srv")
    client, login = _console(tmp_path, images_root, clock, tls)

    cookie = login.headers["set-cookie"]
    assert "secure" in cookie.lower(), cookie
    assert "httponly" in cookie.lower()
    assert client.get("/api/console/me").status_code == 200     # העוגייה חוזרת


def test_login_cookie_is_not_secure_without_tls(tmp_path: Path, images_root: Path, clock):
    """‏--console-tls off על 127.0.0.1: עוגיית Secure על http הייתה נזרקת
    על ידי הדפדפן, והמפתח לא היה מצליח להיכנס לעולם."""
    _client, login = _console(tmp_path, images_root, clock, None)
    assert "secure" not in login.headers["set-cookie"].lower()


def test_me_reports_the_fingerprint_for_the_status_bar(tmp_path: Path, images_root: Path, clock):
    tls = console_tls.ensure_console_cert(tmp_path / "data", "10.0.0.1", "srv")
    client, _login = _console(tmp_path, images_root, clock, tls)

    me = client.get("/api/console/me").json()
    assert me["tls"] == {"mode": "self-signed",
                         "fingerprint_sha256": tls.fingerprint_sha256}


def test_me_reports_no_tls_when_off(tmp_path: Path, images_root: Path, clock):
    client, _login = _console(tmp_path, images_root, clock, None)
    assert client.get("/api/console/me").json()["tls"] is None


def test_ports_screen_calls_8081_https(tmp_path: Path, images_root: Path, clock):
    client, _login = _console(tmp_path, images_root, clock, None)
    rows = {r["id"]: r for r in client.get("/api/console/ports").json()}
    assert rows["http_console"]["name"] == "HTTPS"
    assert rows["http_console"]["port"] == "8081"
    assert "HTTPS" in rows["http_console"]["note"] or "https" in rows["http_console"]["note"]


# ---------- לחיצת-יד אמיתית ----------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_uvicorn_serves_https_with_the_generated_cert(tmp_path: Path):
    """‏stdlib ssl בצד השרת (uvicorn) ובצד הלקוח, שסומך **רק** על התעודה
    שנוצרה ומאמת את שם המארח מול ה-IP SAN — 200. לקוח בלי התעודה נכשל
    בלחיצת-היד (השומר מפני "עבר כי לא אימת")."""
    uvicorn = pytest.importorskip("uvicorn")
    tls = console_tls.ensure_console_cert(tmp_path / "data", "127.0.0.1", "srv")

    async def app(scope, receive, send):
        assert scope["type"] == "http"
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"tls-ok"})

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning",
        **tls.uvicorn_kwargs()))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            thread.join(0.05)
        assert server.started, "uvicorn לא עלה"

        trusted = ssl.create_default_context(cafile=str(tls.cert_path))
        with urllib.request.urlopen(f"https://127.0.0.1:{port}/", timeout=5,
                                    context=trusted) as resp:
            assert resp.status == 200 and resp.read() == b"tls-ok"

        untrusted = ssl.create_default_context()
        with pytest.raises(Exception) as exc:
            urllib.request.urlopen(f"https://127.0.0.1:{port}/", timeout=5,
                                   context=untrusted)
        assert "CERTIFICATE_VERIFY_FAILED" in str(exc.value)

        # ‏HTTP רגיל על מאזין ה-TLS אינו מקבל דף — fail-closed (uvicorn
        # סוגר/מחזיר שגיאה; העיקר: לא 200 ולא הפניה).
        with pytest.raises(Exception):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5)
    finally:
        server.should_exit = True
        thread.join(5)


# ---------- #2095: /me = the certificate that is served ----------

def _served_fingerprint(port: int) -> str:
    import hashlib
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        with context.wrap_socket(raw, server_hostname="127.0.0.1") as tls_sock:
            der = tls_sock.getpeercert(binary_form=True)
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def test_the_reported_fingerprint_is_the_one_served_after_the_file_changes(tmp_path: Path):
    """ISO rc1 (24/10): ESXi served B9:F7... and /me said 90:9A...: a listener
    built after the address arrived (#1088) read console.crt from disk, which
    the DCUI had re-issued after the server computed its fingerprint. The
    fingerprint is now what a TLS client is shown, whatever happens to the
    canonical file after the start (here: re-issued for a new address)."""
    uvicorn = pytest.importorskip("uvicorn")
    data = tmp_path / "data"
    tls = console_tls.ensure_console_cert(data, "127.0.0.1", "srv")
    kwargs = tls.uvicorn_kwargs()                                   # server start
    rewritten = console_tls.ensure_console_cert(data, "10.0.0.9", "srv")  # another writer
    assert rewritten.fingerprint_sha256 != tls.fingerprint_sha256   # the file did change

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="warning", **kwargs))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            thread.join(0.05)
        assert server.started, "uvicorn did not start"
        assert _served_fingerprint(port) == tls.public_info()["fingerprint_sha256"]
    finally:
        server.should_exit = True
        thread.join(5)


def test_a_start_before_the_lease_keeps_the_certificate(tmp_path: Path):
    """The server passes 127.0.0.1 while the DHCP lease has not arrived. That
    re-issued the certificate on every boot where the server came up first
    (ESXi 24/10: three fingerprints in two boots). Loopback is no new address."""
    data = tmp_path / "data"
    first = console_tls.ensure_console_cert(data, "10.0.0.1", "srv")
    again = console_tls.ensure_console_cert(data, "127.0.0.1", "srv")
    assert again.regenerated is False
    assert again.fingerprint_sha256 == first.fingerprint_sha256
    assert not first.cert_path.with_name("console.crt.prev").exists()


def test_the_dcui_shows_the_served_certificate_and_never_reissues_it(tmp_path: Path, monkeypatch):
    """The DCUI called ensure_console_cert with the address it saw -- a second
    writer: the certificate on disk changed under the running server, and the
    DCUI showed a fingerprint /me did not (ESXi + Hyper-V, 24/10).
    #2148: "served" is what the listener presents (tests/test_console_fp_one_2148.py);
    here nothing listens, so the file on disk is not shown either."""
    from server.dcui import data as dcui_data
    from server.dcui.data import CommandResult, collect_snapshot
    monkeypatch.setattr(dcui_data, "CONSOLE_LOCAL", ("127.0.0.1", _free_port()))

    data = tmp_path / "data"
    data.mkdir()
    tls = console_tls.ensure_console_cert(data, "127.0.0.1", "srv")   # server up before the lease
    tls.uvicorn_kwargs()
    canonical = tls.cert_path.read_bytes()
    status = tmp_path / "firstboot.status"
    status.write_text("state=done\nservers_if=ens33\n", encoding="utf-8")
    nic = tmp_path / "net" / "ens33"
    nic.mkdir(parents=True)
    for name, value in (("address", "00:50:56:01:02:01"), ("carrier", "1"),
                        ("operstate", "up"), ("speed", "1000")):
        (nic / name).write_text(value, encoding="ascii")

    def runner(argv):
        if argv[:4] == ["ip", "-j", "address", "show"]:
            return CommandResult(0, '[{"ifname":"ens33","operstate":"UP","addr_info":'
                                    '[{"family":"inet","local":"10.10.10.8","prefixlen":24}]}]')
        return CommandResult(1, "", "unavailable")

    snapshot = collect_snapshot(data, runner=runner, sys_net=tmp_path / "net",
                                status_file=status, override_file=tmp_path / "none",
                                repo_dir=tmp_path / "src", manifest_file=tmp_path / "none.json")
    assert snapshot.fingerprint.startswith("unavailable"), snapshot.fingerprint
    assert tls.cert_path.read_bytes() == canonical
    assert not tls.cert_path.with_name("console.crt.prev").exists()
