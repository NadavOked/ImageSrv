"""‏#153 — לשונית ההגדרות: העלאת תעודת TLS לקונסולה ושינוי שם המארח.

שני הנתיבים נוגעים במכונה (קבצי התעודה, ‏`hostnamectl`, ‏`systemctl
restart`). כמו ב-DHCP, כל מגע במכונה עובר דרך hooks מוזרקים — הבדיקות
כאן לעולם אינן מריצות `hostnamectl` או `systemd-run` אמיתיים.

ההצלחה נקבעת בראיה חיובית (עיקרון 5): תעודה נבדקת בטעינה האמיתית של
‏`ssl` לפני שהיא מחליפה את הקיימת, ושם מארח נחשב "שונה" רק כשהוא נקרא
בחזרה מהמכונה.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

pytest.importorskip("cryptography")
pytest.importorskip("fastapi")

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from server import console_tls

try:
    from fastapi.testclient import TestClient
except ImportError:                                   # pragma: no cover
    TestClient = None

NOW = datetime.datetime.now(datetime.timezone.utc)


def make_pair(cn="imagectl.college.ac.il", *, dns=("imagectl.college.ac.il",),
              not_before=None, not_after=None, issuer_cn=None):
    """תעודה + מפתח (PEM). ‏``issuer_cn`` שונה מ-CN = "חתומה" (לא עצמית)."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_cn or cn)])
    builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(not_before or NOW - datetime.timedelta(days=1))
               .not_valid_after(not_after or NOW + datetime.timedelta(days=365)))
    if dns:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(d) for d in dns]), critical=False)
    cert = builder.sign(key, hashes.SHA256())
    key_pem = key.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    return cert.public_bytes(serialization.Encoding.PEM), key_pem, key


# --- אימות התעודה (יחידה) -----------------------------------------------------


def test_a_matching_valid_pair_is_accepted_with_its_expiry():
    cert, key, _ = make_pair()
    info = console_tls.validate_upload(cert, key)
    assert info["not_after"].startswith(str((NOW + datetime.timedelta(days=365)).year))
    assert "imagectl.college.ac.il" in info["sans"]
    assert info["subject"] == "imagectl.college.ac.il"
    assert len(info["fingerprint_sha256"].split(":")) == 32


def test_a_key_of_another_cert_is_refused_by_name():
    cert, _key, _ = make_pair()
    _cert2, other_key, _ = make_pair()
    with pytest.raises(console_tls.UploadRefused, match="אינו תואם"):
        console_tls.validate_upload(cert, other_key)


def test_an_expired_cert_is_refused_with_the_date():
    cert, key, _ = make_pair(not_before=NOW - datetime.timedelta(days=400),
                             not_after=NOW - datetime.timedelta(days=2))
    with pytest.raises(console_tls.UploadRefused, match="פג"):
        console_tls.validate_upload(cert, key)


def test_a_cert_that_is_not_yet_valid_is_refused():
    cert, key, _ = make_pair(not_before=NOW + datetime.timedelta(days=3))
    with pytest.raises(console_tls.UploadRefused, match="עוד לא"):
        console_tls.validate_upload(cert, key)


@pytest.mark.parametrize("blob", [b"", b"not a certificate", b"\x30\x82\x01\x0a"])
def test_a_cert_that_is_not_pem_is_refused(blob):
    _cert, key, _ = make_pair()
    with pytest.raises(console_tls.UploadRefused, match="PEM"):
        console_tls.validate_upload(blob, key)


def test_a_key_that_is_not_pem_is_refused():
    cert, _key, _ = make_pair()
    with pytest.raises(console_tls.UploadRefused, match="PEM"):
        console_tls.validate_upload(cert, b"-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n")


def test_an_encrypted_key_is_refused_because_the_server_starts_unattended():
    cert, _key, key_obj = make_pair()
    encrypted = key_obj.private_bytes(serialization.Encoding.PEM,
                                      serialization.PrivateFormat.PKCS8,
                                      serialization.BestAvailableEncryption(b"secret"))
    with pytest.raises(console_tls.UploadRefused, match="מוצפן"):
        console_tls.validate_upload(cert, encrypted)


def test_a_ca_signed_cert_is_reported_as_such_not_as_self_signed(tmp_path: Path):
    cert, key, _ = make_pair(issuer_cn="College CA")
    console_tls.install_upload(tmp_path, cert, key)
    tls = console_tls.ensure_console_cert(tmp_path, "10.0.0.1", "srv")
    assert tls.public_info()["mode"] == "ca-signed"


def test_the_generated_cert_is_still_self_signed(tmp_path: Path):
    tls = console_tls.ensure_console_cert(tmp_path, "10.0.0.1", "srv")
    assert tls.public_info()["mode"] == "self-signed"


# --- הנתיבים -------------------------------------------------------------------


class Machine:
    """המכונה המדומה: שם מארח, ותיעוד של כל מה שהשרת ביקש ממנה."""

    def __init__(self, hostname="imagectl-old"):
        self.name = hostname
        self.set_calls: list[str] = []
        self.restarts = 0
        self.set_error: str | None = None
        self.readback_lies = False
        self.restart_error: str | None = None

    def hooks(self) -> dict:
        def set_hostname(name):
            self.set_calls.append(name)
            if self.set_error:
                return self.set_error
            if not self.readback_lies:
                self.name = name
            return None

        def restart():
            if self.restart_error:
                return self.restart_error
            self.restarts += 1
            return None

        return {"hostname": lambda: self.name, "set_hostname": set_hostname,
                "restart_server": restart}


@pytest.fixture()
def host(tmp_path: Path, images_root: Path, clock):
    if TestClient is None:
        pytest.skip("fastapi is required")
    from server import users
    from server.app import create_app
    from test_sender import Recorder

    machine = Machine()
    data = tmp_path / "data"
    tls = console_tls.ensure_console_cert(data, "10.0.0.1", "imagectl-old")
    app = create_app(data, images_root, "http://10.44.12.10:8080", now_fn=clock,
                     sender_runner=Recorder(block=True), console_tls=tls,
                     host_hooks=machine.hooks())
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", check_policy=False)
    users.create(ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    from conftest import complete_console_login
    admin, deploy = TestClient(app), TestClient(app)
    complete_console_login(admin, ctx.conn, "noc", "admin-pass-123")
    assert deploy.post("/api/console/login",
                       json={"username": "labtech", "password": "deploy-pass-1"}).status_code == 200
    yield {"admin": admin, "deploy": deploy, "machine": machine, "tls": tls,
           "ctx": ctx, "dir": data / console_tls.CONSOLE_TLS_DIRNAME}
    ctx.sender.stop()


def open_round(ctx):
    ctx.conn.execute("INSERT OR IGNORE INTO groups (id, label, role) VALUES ('grp_X', 'X', 'cloner')")
    ctx.conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, "
        "wait_seconds, state, opened_by, created_at, last_join_at) "
        "VALUES ('ses_x', 'grp_X', 'img', 'LAB', 1, 30, 'running', 'noc', 'now', 1)")
    ctx.conn.commit()


def events(ctx, name):
    return [r[0] for r in ctx.conn.execute(
        "SELECT detail FROM journal WHERE event = ?", (name,)).fetchall()]


def upload(client, cert, key):
    return client.post("/api/console/tls",
                       json={"cert_pem": cert.decode(), "key_pem": key.decode()})


def test_tls_status_shows_the_running_cert_and_its_expiry(host):
    body = host["admin"].get("/api/console/tls").json()
    assert body["enabled"] is True
    assert body["on_disk"]["fingerprint_sha256"] == host["tls"].fingerprint_sha256
    assert body["on_disk"]["not_after"]
    assert body["pending_restart"] is False
    assert body["covers_hostname"] is True       # השם הנוכחי נכנס ל-SAN בהפעלה


def test_upload_replaces_the_pair_keeps_the_previous_and_restarts(host):
    old_cert = (host["dir"] / "console.crt").read_bytes()
    cert, key, _ = make_pair()
    r = upload(host["admin"], cert, key)
    assert r.status_code == 200, r.text
    assert r.json()["restarting"] is True
    assert (host["dir"] / "console.crt").read_bytes() == cert
    # write_credential_0600 כותב בלי O_BINARY — בווינדוס LF הופך ל-CRLF.
    assert (host["dir"] / "console.key").read_bytes().replace(bytes([13, 10]), bytes([10])) == key
    assert (host["dir"] / "console.crt.prev").read_bytes() == old_cert
    assert host["machine"].restarts == 1
    assert events(host["ctx"], "console_tls_uploaded")
    status = host["admin"].get("/api/console/tls").json()
    # התהליך עדיין רץ עם הישנה עד שהאתחול יקרה — וזה נאמר, לא מוסתר.
    assert status["pending_restart"] is True
    assert status["covers_hostname"] is False


def reissue(client):
    return client.post("/api/console/tls/reissue")


def test_reissue_makes_a_new_self_signed_pair_keeps_the_previous_and_restarts(host):
    """נדב 17/10: "הנפקת תעודה חדשה" — עצמית, ה-SAN נשמר, הזוג הקודם ב-.prev, ואתחול."""
    old_cert = (host["dir"] / "console.crt").read_bytes()
    old_sans = set(console_tls.cert_sans(old_cert))
    r = reissue(host["admin"])
    assert r.status_code == 200, r.text
    assert r.json()["restarting"] is True
    new_cert = (host["dir"] / "console.crt").read_bytes()
    assert new_cert != old_cert
    assert (host["dir"] / "console.crt.prev").read_bytes() == old_cert
    assert (host["dir"] / "console.key.prev").exists()
    assert old_sans <= set(console_tls.cert_sans(new_cert))
    assert console_tls.cert_details(new_cert)["subject"] == console_tls.COMMON_NAME == "imagesrv-console"
    assert console_tls.cert_details(new_cert)["self_signed"] is True
    assert host["machine"].restarts == 1
    assert events(host["ctx"], "console_tls_reissued")
    # התהליך עדיין רץ עם הישנה עד האתחול — נאמר, לא מוסתר.
    assert host["admin"].get("/api/console/tls").json()["pending_restart"] is True


def test_reissue_refuses_a_ca_signed_cert_and_touches_nothing(host):
    cert, key, _ = make_pair(issuer_cn="College CA")
    console_tls.install_upload(host["dir"].parent, cert, key)
    r = reissue(host["admin"])
    assert r.status_code == 400
    assert "חתומה" in r.json()["detail"]
    assert (host["dir"] / "console.crt").read_bytes() == cert
    assert host["machine"].restarts == 0
    assert events(host["ctx"], "console_tls_refused")
    assert not events(host["ctx"], "console_tls_reissued")


def test_reissue_waits_while_a_round_is_running(host):
    open_round(host["ctx"])
    before = (host["dir"] / "console.crt").read_bytes()
    assert reissue(host["admin"]).status_code == 409
    assert (host["dir"] / "console.crt").read_bytes() == before
    assert host["machine"].restarts == 0


def test_reissue_restart_that_was_not_scheduled_is_a_failure(host):
    host["machine"].restart_error = "systemd-run: not found"
    r = reissue(host["admin"])
    assert r.status_code == 500 and "נכתבה" in r.json()["detail"]


def test_a_refused_upload_touches_nothing(host):
    before = (host["dir"] / "console.crt").read_bytes()
    cert, _key, _ = make_pair()
    _c, other_key, _ = make_pair()
    r = upload(host["admin"], cert, other_key)
    assert r.status_code == 400
    assert "אינו תואם" in r.json()["detail"]
    assert (host["dir"] / "console.crt").read_bytes() == before
    assert not (host["dir"] / "console.crt.prev").exists()
    assert host["machine"].restarts == 0
    assert events(host["ctx"], "console_tls_refused")


def test_upload_waits_while_a_round_is_running(host):
    open_round(host["ctx"])
    before = (host["dir"] / "console.crt").read_bytes()
    cert, key, _ = make_pair()
    r = upload(host["admin"], cert, key)
    assert r.status_code == 409
    assert (host["dir"] / "console.crt").read_bytes() == before
    assert host["machine"].restarts == 0


def test_a_restart_that_was_not_scheduled_is_a_failure_not_a_success(host):
    host["machine"].restart_error = "systemd-run: not found"
    cert, key, _ = make_pair()
    r = upload(host["admin"], cert, key)
    assert r.status_code == 500
    assert "systemd-run" in r.json()["detail"]
    assert "נכתבה" in r.json()["detail"]        # המצב האמיתי: על הדיסק, לא בשימוש


def test_upload_is_refused_when_the_console_runs_without_tls(tmp_path: Path, images_root: Path, clock):
    from server import users
    from server.app import create_app
    from conftest import complete_console_login
    from test_sender import Recorder

    machine = Machine()
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080", now_fn=clock,
                     sender_runner=Recorder(block=True), host_hooks=machine.hooks())
    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test", check_policy=False)
    admin = TestClient(app)
    complete_console_login(admin, app.state.ctx.conn, "noc", "admin-pass-123")
    assert admin.get("/api/console/tls").json() == {"enabled": False}
    cert, key, _ = make_pair()
    assert upload(admin, cert, key).status_code == 409
    assert machine.restarts == 0
    app.state.ctx.sender.stop()


@pytest.mark.parametrize("method,path", [("get", "/api/console/tls"), ("post", "/api/console/tls"), ("post", "/api/console/tls/reissue"),
                                         ("get", "/api/console/hostname"),
                                         ("post", "/api/console/hostname")])
def test_deploy_user_gets_403(host, method, path):
    r = getattr(host["deploy"], method)(path, **({"json": {}} if method == "post" else {}))
    assert r.status_code == 403


def test_hostname_status_reads_the_machine(host):
    assert host["admin"].get("/api/console/hostname").json()["hostname"] == "imagectl-old"


@pytest.mark.parametrize("given,applied", [
    ("imagectl", "imagectl"), ("imagectl-tlv", "imagectl-tlv"),
    ("IMAGECTL-TA", "imagectl-ta"), ("1srv", "1srv"), ("a" * 15, "a" * 15),
])
def test_a_valid_hostname_is_applied_read_back_and_restarts(host, given, applied):
    r = host["admin"].post("/api/console/hostname", json={"hostname": given})
    assert r.status_code == 200, r.text
    assert r.json()["hostname"] == applied
    assert host["machine"].set_calls == [applied]
    assert host["machine"].restarts == 1
    assert events(host["ctx"], "hostname_changed") == [f"imagectl-old → {applied}"]


#: ‏#1997 (נדב, 22/10): שם השרת הוא שם מחשב — עד 15, בלי נקודות. עד כאן RFC 1123
#: (‏"srv1.college.ac.il" ו-63 תווים עברו).
@pytest.mark.parametrize("bad", [
    "", " ", "-abc", "abc-", "a_b", "has space", "a" * 16, "a" * 64, "שרת", "a..b", ".abc",
    "abc.", "10.0.0.1", "ab$c", ".".join(["a" * 63] * 4) + "a", "srv1.college.ac.il",
])
def test_an_invalid_hostname_is_refused_before_touching_the_machine(host, bad):
    r = host["admin"].post("/api/console/hostname", json={"hostname": bad})
    assert r.status_code == 400, (bad, r.text)
    assert "אותיות אנגליות, ספרות ומקף" in r.json()["detail"]
    assert host["machine"].set_calls == []
    assert host["machine"].restarts == 0


def test_the_same_hostname_is_not_a_change(host):
    r = host["admin"].post("/api/console/hostname", json={"hostname": "imagectl-old"})
    assert r.status_code == 400
    assert host["machine"].set_calls == []


def test_hostnamectl_failure_is_reported_and_nothing_restarts(host):
    host["machine"].set_error = "hostnamectl: Access denied"
    r = host["admin"].post("/api/console/hostname", json={"hostname": "imagectl-new"})
    assert r.status_code == 500
    assert "Access denied" in r.json()["detail"]
    assert host["machine"].restarts == 0
    assert events(host["ctx"], "hostname_change_failed")


def test_a_name_that_does_not_read_back_is_not_a_success(host):
    """‏hostnamectl החזיר 0 אך המכונה עדיין קוראת לעצמה בשם הישן — "לא
    הצלחנו לאמת" אינו "השתנה" (עיקרון 5)."""
    host["machine"].readback_lies = True
    r = host["admin"].post("/api/console/hostname", json={"hostname": "imagectl-new"})
    assert r.status_code == 500
    assert "imagectl-old" in r.json()["detail"]
    assert host["machine"].restarts == 0
    assert not events(host["ctx"], "hostname_changed")


def test_hostname_change_waits_while_a_round_is_running(host):
    open_round(host["ctx"])
    r = host["admin"].post("/api/console/hostname", json={"hostname": "imagectl-new"})
    assert r.status_code == 409
    assert host["machine"].set_calls == []
