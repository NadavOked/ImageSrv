"""First-boot web wizard (#910): validation, secret handoff, and terminal states."""

from __future__ import annotations

import http.client
import io
import json
import os
import re
import subprocess
import threading
import urllib.error
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.wizard.__main__ import main
from server.net import ifaddr
from server.wizard import bridge, core
from server.wizard.core import WizardConfig, WizardPaths, WizardState, list_interfaces, validate_config
from server.wizard.httpd import WizardHTTPServer

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "server" / "wizard" / "static"
NICS = [{"name": "eth-test", "mac": "02:00:00:00:00:01", "link": "up",
         "addresses": ["10.44.10.37/24"], "current_ip": "10.44.10.37/24"}]


def valid(**changes) -> WizardConfig:
    base = WizardConfig(
        role="standalone", primary_url="", interface="eth-test", mode="dhcp",
        address="", netmask="", gateway="", dns="", hostname="imagesrv-haifa",
        password="Imag3ctl!haifa", password_confirm="Imag3ctl!haifa",
    )
    return replace(base, **changes)


@pytest.mark.parametrize(("changes", "field", "message"), [
    ({"role": "secondary", "primary_url": ""}, "primary_url", "חובה להזין את כתובת השרת הראשי עבור שרת משנה."),
    ({"interface": "not-real"}, "interface", "יש לבחור כרטיס רשת קיים מרשימת הכרטיסים."),
    ({"mode": "static", "address": "999.1.1.1", "netmask": "255.255.255.0"}, "address", "כתובת ה-IP אינה תקינה (נדרש מבנה IPv4 תקין)."),
    ({"mode": "static", "address": "10.1.1.2", "netmask": "255.0.255.0"}, "netmask", "מסכת הרשת אינה תקינה."),
    ({"mode": "static", "address": "10.1.1.2", "netmask": "255.255.255.0", "gateway": "bad"}, "gateway", "כתובת השער אינה תקינה (נדרש מבנה IPv4 תקין)."),
    ({"mode": "static", "address": "10.1.1.2", "netmask": "255.255.255.0", "dns": "10.1.1.1,no"}, "dns", "כתובת ה-DNS אינה תקינה (יש להפריד כתובות IPv4 בפסיק)."),
    ({"hostname": "Bad_Name"}, "hostname", "שם השרת יכול להכיל אותיות אנגליות קטנות, ספרות ומקפים בלבד; אסור להתחיל או לסיים במקף."),
    ({"password": "short!1", "password_confirm": "short!1"}, "password", "הסיסמה חייבת להכיל לפחות 8 תווים"),
    ({"password_confirm": "Imag3ctl!other"}, "password_confirm", "הסיסמאות שהזנת אינן תואמות."),
])
def test_server_validation_uses_the_hebrew_screen_messages(changes, field, message) -> None:
    errors = validate_config(valid(**changes), {"eth-test"})
    assert errors[field] == message


def test_rerun_requires_existing_password_field() -> None:
    errors = validate_config(valid(current_password=""), {"eth-test"}, rerun=True)
    assert errors["current_password"] == "בהרצה חוזרת חובה להזין את סיסמת admin הקיימת."


def _paths(tmp_path: Path, installer: Path, verify: Path) -> WizardPaths:
    return WizardPaths(
        source_dir=REPO, app_dir=REPO, data_dir=tmp_path / "data",
        status_file=tmp_path / "etc" / "firstboot.status",
        stamp_file=tmp_path / "data" / ".firstboot-done",
        installer=installer, verify=verify, http_root=tmp_path / "boot",
    )


def _write_fake(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def _rerun_over_http(tmp_path: Path, admin_name: str = "admin"):
    """אשף בהרצה חוזרת (‏--rerun) מול DB אמיתי עם admin, דרך ה-HTTP — כך
    שגם הכתובת שה-handler מוסר לבדיקה נבדקת. מחזיר (state, apply, close)."""
    from server import db as server_db
    from server.console import users

    paths = _paths(tmp_path, tmp_path / "unused", tmp_path / "unused2")
    paths.data_dir.mkdir(parents=True)
    conn = server_db.connect(paths.data_dir / "imagesrv.db")
    users.create(conn, admin_name, "Curr3nt!admin", "admin", by="test",
                 check_policy=False, is_builtin=True)
    conn.close()
    state = WizardState(paths, rerun=True, dev=True, interfaces_provider=lambda: NICS)
    nic_fact, role_fact = tmp_path / "nic", tmp_path / "role"
    nic_fact.write_text("", encoding="utf-8")
    role_fact.write_text("", encoding="utf-8")
    server = WizardHTTPServer(("127.0.0.1", 0), state, nic_fact, role_fact)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def apply(password: str) -> dict:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            conn.request("POST", "/api/wizard/apply",
                         json.dumps(valid(current_password=password).__dict__).encode("utf-8"),
                         {"Content-Type": "application/json"})
            response = conn.getresponse()
            assert response.status == 202, response.read()
            response.read()
        finally:
            conn.close()
        state.wait(10)
        return state.progress()

    def close() -> None:
        server.shutdown()
        server.server_close()

    return state, apply, close


def test_rerun_guesses_go_through_login_guard(tmp_path: Path) -> None:
    """‏#1631: בהרצה החוזרת `_verify_current_admin` בדק את סיסמת admin בלי
    `login_guard` — ערוץ ניחוש בלי השהיה ובלי נעילה. אחרי חמישה כשלונות
    מאותה כתובת גם הסיסמה הנכונה נדחית עד תום ההשהיה, וההתקנה לא רצה."""
    _state, apply, close = _rerun_over_http(tmp_path)
    try:
        for _ in range(5):
            got = apply("wrong-guess")
            assert got["state"] == "validation-failed", got
        got = apply("Curr3nt!admin")
        assert got["state"] == "validation-failed", "הסיסמה הנכונה התקבלה מיד אחרי חמישה ניחושים"
        assert "נסה שוב" in got["error"], got
        assert "fake setup-boot-server.sh completed" not in got["output"], "ההתקנה רצה למרות שהאימות נחסם"
    finally:
        close()


def test_rerun_with_the_right_password_still_runs_the_installer(tmp_path: Path) -> None:
    """שומר מהצד השני של #1631: ‏login_guard אינו חוסם את המנהל עצמו."""
    _state, apply, close = _rerun_over_http(tmp_path)
    try:
        got = apply("Curr3nt!admin")
        assert got["state"] != "validation-failed", got
        assert "fake setup-boot-server.sh completed" in got["output"], "ההתקנה לא רצה עם הסיסמה הנכונה"
    finally:
        close()


def test_rerun_authenticates_the_chosen_installer_admin(tmp_path: Path) -> None:
    """#1745: the installer admin is not necessarily literally named ``admin``."""
    _state, apply, close = _rerun_over_http(tmp_path, "nadav")
    try:
        got = apply("Curr3nt!admin")
        assert got["state"] != "validation-failed", got
        assert "fake setup-boot-server.sh completed" in got["output"]
    finally:
        close()


def test_installer_gets_password_on_fd_not_argv(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ifaddr, "read_ipv4", lambda _name: "10.44.10.37")
    installer = tmp_path / "fake-installer.py"
    verify = tmp_path / "fake-verify.py"
    record = tmp_path / "installer-record.json"
    _write_fake(installer, """import json, os, pathlib, sys
fd = int(sys.argv[sys.argv.index('--admin-pass-fd') + 1])
password = os.read(fd, 4096).decode()
pathlib.Path(__file__).with_name('installer-record.json').write_text(
    json.dumps({'argv': sys.argv[1:], 'password': password}), encoding='utf-8')
""")
    _write_fake(verify, "import sys\nsys.exit(0)\n")
    state = WizardState(_paths(tmp_path, installer, verify), dev=False,
                        interfaces_provider=lambda: NICS,
                        hostname_setter=lambda _hostname: 0)
    state.start_apply(valid())
    state.wait(20)
    assert state.progress()["state"] == "done"
    got = json.loads(record.read_text(encoding="utf-8"))
    assert got["password"] == "Imag3ctl!haifa"
    assert "Imag3ctl!haifa" not in got["argv"]
    assert got["argv"][-4:] == ["--admin-user", "admin", "--admin-pass-fd", "3" if os.name == "posix" else "0"]
    assert got["argv"][:4] == ["--servers-if", "eth-test", "--servers-mode", "dhcp"]


@pytest.mark.parametrize("mode", ["dhcp", "static"])
@pytest.mark.parametrize("dev", [False, True])
def test_no_answers_console_cert_uses_selected_nic_ip(tmp_path: Path, monkeypatch, mode, dev) -> None:
    """#2106: inspect the real certificate, not just the arguments to TLS setup."""
    from cryptography import x509
    from ipaddress import ip_address

    state = WizardState(_paths(tmp_path, tmp_path / "unused", tmp_path / "unused2"), dev=dev,
                        interfaces_provider=lambda: NICS,
                        hostname_setter=lambda _hostname: 0)
    monkeypatch.setattr(state, "_run", lambda *args, **kwargs: 0)
    lookups = []

    def run_ip(argv, **kwargs):
        lookups.append(argv)
        return SimpleNamespace(stdout=json.dumps([
            {"ifname": "other-nic", "addr_info": [{"family": "inet", "local": "192.0.2.99"}]},
            {"ifname": "eth-test", "addr_info": [{"family": "inet", "local": "10.44.10.37"}]},
        ]))

    monkeypatch.setattr(ifaddr.subprocess, "run", run_ip)
    state._apply(valid(mode=mode, address="10.44.10.37" if mode == "static" else "",
                       netmask="255.255.255.0" if mode == "static" else ""))
    got = state.progress()
    assert got["state"] == "done", got
    cert_path = state.paths.data_dir / "console-tls" / "console.crt"
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert ip_address("10.44.10.37") in sans.get_values_for_type(x509.IPAddress)
    assert "eth-test" not in sans.get_values_for_type(x509.DNSName)
    assert "imagesrv-haifa" in sans.get_values_for_type(x509.DNSName)
    assert got["console_url"] == "https://10.44.10.37:8081"
    assert lookups == ([] if dev else [["ip", "-json", "-4", "addr", "show", "dev", "eth-test"]])
    assert state.paths.stamp_file.exists()
    assert core.read_fact(state.paths.status_file)["state"] == "done"


@pytest.mark.parametrize("failure", ["no-address", "invalid-address", "invalid-json", "command-failed"])
def test_no_answers_console_cert_lookup_failure_is_visible(tmp_path: Path, monkeypatch, failure) -> None:
    """#2106: failed resolution must not issue a bogus SAN or mark setup done."""
    state = WizardState(_paths(tmp_path, tmp_path / "unused", tmp_path / "unused2"),
                        interfaces_provider=lambda: NICS,
                        hostname_setter=lambda _hostname: 0)
    monkeypatch.setattr(state, "_run", lambda *args, **kwargs: 0)

    def run_ip(argv, **kwargs):
        if failure == "command-failed":
            raise subprocess.CalledProcessError(1, argv)
        if failure == "invalid-json":
            return SimpleNamespace(stdout="not JSON")
        addresses = [{"family": "inet", "local": "eth-test"}] if failure == "invalid-address" else []
        return SimpleNamespace(stdout=json.dumps([{"ifname": "eth-test", "addr_info": addresses}]))

    monkeypatch.setattr(ifaddr.subprocess, "run", run_ip)
    state._apply(valid())
    got = state.progress()
    assert got["state"] == "check-error", got
    assert got["error"]
    if failure in {"no-address", "invalid-address"}:
        assert "eth-test" in got["error"]
        assert "IPv4" in got["error"]
    else:
        assert "ip -json addr" in got["error"]
    assert core.read_fact(state.paths.status_file)["state"] == "check-error"
    assert "console_url" not in got
    assert "fingerprint" not in got
    assert not state.paths.stamp_file.exists()
    assert not (state.paths.data_dir / "console-tls").exists()


def test_failing_installer_sets_named_state_and_never_writes_stamp(tmp_path: Path) -> None:
    state = WizardState(_paths(tmp_path, tmp_path / "unused", tmp_path / "unused2"),
                        dev=True, interfaces_provider=lambda: NICS)
    state.dev = False
    state._run = lambda argv, **kwargs: 7 if "setup-boot-server" in " ".join(argv) or "unused" in " ".join(argv) else 0  # type: ignore[method-assign]
    state.start_apply(valid())
    state.wait(5)
    assert state.progress()["state"] == "installer-failed"
    assert not state.paths.stamp_file.exists()
    assert "state=installer-failed" in state.paths.status_file.read_text(encoding="utf-8")


def test_apply_is_accepted_immediately_is_idempotent_and_progress_keeps_finish_payload(tmp_path: Path) -> None:
    state = WizardState(_paths(tmp_path, tmp_path / "unused", tmp_path / "unused2"),
                        dev=True, interfaces_provider=lambda: NICS)
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def fake_apply(_config: WizardConfig) -> None:
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(5), "ה-hook של ההתקנה לא שוחרר"
        state._set("done", console_url="https://10.44.10.37:8081", user="admin",
                   fingerprint="AA:" * 31 + "AA")

    state._apply = fake_apply  # type: ignore[method-assign]
    nic_fact, role_fact = tmp_path / "nic", tmp_path / "role"
    nic_fact.write_text("", encoding="utf-8")
    role_fact.write_text("", encoding="utf-8")
    server = WizardHTTPServer(("127.0.0.1", 0), state, nic_fact, role_fact)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    body = json.dumps(valid().__dict__).encode("utf-8")

    def request(method: str, path: str, payload: bytes | None = None) -> tuple[int, dict]:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=1)
        try:
            headers = {"Content-Type": "application/json"} if payload is not None else {}
            conn.request(method, path, body=payload, headers=headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    try:
        status, first = request("POST", "/api/wizard/apply", body)
        assert status == 202 and first["job"]
        assert entered.wait(1)
        second_body = json.dumps({**valid().__dict__, "interface": "changed-while-running"}).encode("utf-8")
        status, second = request("POST", "/api/wizard/apply", second_body)
        assert status == 202
        assert second["job"] == first["job"]
        assert calls == 1
        release.set()
        state.wait(2)
        status, finished = request("GET", "/api/wizard/progress")
        assert status == 200
        assert finished == {
            "ok": True, "state": "done", "output": "", "job": first["job"],
            "console_url": "https://10.44.10.37:8081", "user": "admin",
            "fingerprint": "AA:" * 31 + "AA",
        }
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        serving.join(2)


def test_progress_strips_ansi_before_exposing_output(tmp_path: Path) -> None:
    state = WizardState(_paths(tmp_path, tmp_path / "unused", tmp_path / "unused2"),
                        dev=True, interfaces_provider=lambda: NICS)
    state._append("plain \x1b[31mred\x1b[0m \x1b]8;;https://example.invalid\x07link\x1b]8;;\x07\n")
    output = state.progress()["output"]
    assert output == "plain red link\n"
    assert "\x1b" not in output


def test_nic_status_distinguishes_admin_down_no_link_and_carrier(tmp_path: Path, monkeypatch) -> None:
    for name, operstate, carrier in (
        ("admin-down", "down", None),
        ("no-link", "up", "0"),
        ("connected", "down", "1"),
    ):
        nic = tmp_path / name
        nic.mkdir()
        (nic / "operstate").write_text(operstate, encoding="utf-8")
        (nic / "address").write_text(f"02:00:00:00:00:0{len(list(nic.iterdir()))}", encoding="utf-8")
        if carrier is not None:
            (nic / "carrier").write_text(carrier, encoding="utf-8")
    monkeypatch.setattr(core.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="[]"))
    found = {nic["name"]: nic for nic in list_interfaces(tmp_path)}
    assert found["admin-down"]["link_label"] == "לא מודלק"
    assert found["no-link"]["link_label"] == "אין קישור"
    assert found["connected"]["link_label"] == "מחובר"


def test_wizard_client_polls_with_backoff_and_never_turns_apply_fetch_into_failure() -> None:
    js = (STATIC / "wizard.js").read_text(encoding="utf-8")
    assert "const retryDelays = [1000, 2000, 5000]" in js
    assert "const reconnectLimitMs = 5 * 60 * 1000" in js
    assert 'status.textContent = "מתחבר מחדש…"' in js
    assert 'response.status === 404' in js
    assert 'if (response.status === 404) return {handoff: true}' in js
    assert "setInterval" not in js
    apply = js.split("async function next()", 1)[1].split("function finish", 1)[0]
    assert 'finish({state: "check-error"' not in apply
    assert "catch (_) {\n    reconnecting();" in apply
    assert "scheduleProgress(0)" in apply


def test_wizard_source_has_password_eyes_hidden_footer_and_operator_progress() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "wizard.js").read_text(encoding="utf-8")
    assert html.count('class="password-eye"') == 2
    assert 'data-password="password"' in html
    assert 'data-password="password_confirm"' in html
    assert "מה קורה עכשיו" in html and "פרטים טכניים" in html
    assert '<details open>' not in html
    assert 'footer.hidden = typeof which !== "number" || which === 6' in js


def test_finish_fingerprint_can_wrap_inside_the_card() -> None:
    css = (STATIC / "wizard.css").read_text(encoding="utf-8")
    assert "#fingerprint{overflow-wrap:anywhere;word-break:break-all}" in css


def test_cli_refuses_existing_install_without_rerun(tmp_path: Path, capsys) -> None:
    stamp = tmp_path / ".firstboot-done"
    stamp.touch()
    assert main(["--data-dir", str(tmp_path), "--stamp-file", str(stamp)]) == 2
    assert "--rerun" in capsys.readouterr().err


def test_html_has_six_approved_titles_and_forbidden_wording_is_absent() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for title in ("תפקיד השרת במערכת", "הגדרת רשת הניהול", "שם השרת",
                  "חשבון הניהול", "סיכום לפני החלה", "סיום ההתקנה"):
        assert title in html
    joined = "\n".join(p.read_text(encoding="utf-8") for p in STATIC.iterdir() if p.suffix in {".html", ".js"})
    assert "SSH" not in joined
    assert "עצמאי" not in joined


def test_wizard_static_files_have_no_external_resources() -> None:
    for path in STATIC.iterdir():
        if path.suffix not in {".html", ".js", ".css"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"(?:src|href)=[\"']https?://", text), path


def test_check_primary_endpoint_returns_measured_success_and_named_failure(tmp_path, monkeypatch) -> None:
    state = WizardState(_paths(tmp_path, tmp_path / "unused", tmp_path / "unused2"),
                        dev=True, interfaces_provider=lambda: NICS)
    nic_fact, role_fact = tmp_path / "nic", tmp_path / "role"
    nic_fact.write_text("", encoding="utf-8")
    role_fact.write_text("", encoding="utf-8")
    server = WizardHTTPServer(("127.0.0.1", 0), state, nic_fact, role_fact)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()

    def post() -> dict:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=1)
        try:
            body = json.dumps({"primary_url": "https://primary:8443"}).encode()
            conn.request("POST", "/api/wizard/check-primary", body,
                         {"Content-Type": "application/json"})
            response = conn.getresponse()
            assert response.status == 200
            return json.loads(response.read())
        finally:
            conn.close()

    try:
        monkeypatch.setattr("server.wizard.httpd.check_primary", lambda _url: {
            "ok": True, "name": "primary", "version": "TLSv1.3", "fingerprint": "AA:BB",
        })
        assert post()["fingerprint"] == "AA:BB"
        monkeypatch.setattr("server.wizard.httpd.check_primary", lambda _url: {
            "ok": False, "error": "התקשרות לשרת הראשי בפורט 8443 נכשלה. ודא כי חומת האש שבין האתרים פתוחה.",
        })
        assert post()["ok"] is False
    finally:
        server.shutdown(); server.server_close(); serving.join(2)


def test_native_bridge_interfaces_and_apply_are_line_oriented(monkeypatch, capsys) -> None:
    monkeypatch.setattr(bridge, "_request", lambda path, body=None: {
        "state": {"ok": True, "rerun": False, "defaults": {
            "role": "standalone", "interface": "eth-test", "mode": "dhcp",
            "hostname": "imagesrv-server", "primary_url": "",
        }, "interfaces": [{"name": "eth-test", "model": "Intel I350",
            "mac": "02:00:00:00:00:01", "link_label": "מחובר",
            "current_ip": "10.44.10.37/24", "source": "DHCP"}]},
        "apply": {"ok": True, "job": "job-1"},
    }[path])
    monkeypatch.setattr(bridge.sys, "stdin", io.StringIO("{}"))
    assert bridge.main(["interfaces"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "role=standalone" in lines
    assert "nic=eth-test|Intel I350|02:00:00:00:00:01|מחובר|10.44.10.37/24|DHCP||||" in lines
    monkeypatch.setattr(bridge.sys, "stdin", io.StringIO(json.dumps(valid().__dict__)))
    assert bridge.main(["apply"]) == 0
    assert "job=job-1" in capsys.readouterr().out.splitlines()


def test_native_bridge_returns_nonzero_when_wizard_is_down(monkeypatch, capsys) -> None:
    monkeypatch.setattr(bridge, "_request", lambda *_args, **_kwargs:
                        (_ for _ in ()).throw(urllib.error.URLError("connection refused")))
    monkeypatch.setattr(bridge.sys, "stdin", io.StringIO("{}"))
    assert bridge.main(["progress"]) == 1
    assert "URLError" in capsys.readouterr().err


def test_native_bridge_progress_404_uses_local_finish_evidence(monkeypatch, capsys) -> None:
    monkeypatch.setattr(bridge, "_request", lambda *_args, **_kwargs: {"_http_status": 404})
    monkeypatch.setattr(bridge, "_finish", lambda: print("state=done\nfingerprint=AA:BB"))
    monkeypatch.setattr(bridge.sys, "stdin", io.StringIO("{}"))
    assert bridge.main(["progress"]) == 0
    assert capsys.readouterr().out.splitlines() == ["state=done", "fingerprint=AA:BB"]


def test_the_wizard_header_is_the_imagesrv_logo() -> None:
    """#1302: the web installer shows ImageSrv -- the compact lockup inline
    (an explicit height: a viewBox-only SVG collapses), not the old "I" mark."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "<title>ImageSrv — אשף התקנה</title>" in html
    header = html[html.index('<header class="top">'):html.index("</header>")]
    assert 'aria-label="ImageSrv"' in header and 'height="28"' in header
    assert '<g fill="#0079B8"><rect x="0" y="26.78" width="9" height="9" rx="1"/>' in header
    assert "ImageCtl" not in html
