"""Storage Nodes — enrollment מוקשח, הצד המקומי של המשני (#740, tracer 2.1).

**היקף הקובץ הזה.** הדגם הנעול של #740 נשען על ערוץ mTLS 1.3 עם
channel-binding לפי RFC 9266 ועל SPKI-pin. שכבת ה-TLS הזאת דורשת
``pyOpenSSL``/``cryptography`` ואת ה-exporter של OpenSSL — שאינם בספריית
התקן ואינם מותקנים על תחנת הפיתוח (ראו דוח ה-PR). הבדיקות כאן מכסות את
כל מה ש**אינו** תלוי ב-TLS: הפרימיטיבים, חלון ה-pairing, והצד המקומי של
המשני (פתיחת חלון + הצגת קוד + break-glass) על השרת המלא. הבדיקות
הבין-שרתיות (שני מאזיני TLS על loopback, sender-constraint, channel
binding) הן שלב נפרד שממתין להכרעת התלות.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from server import db, interserver_auth, storage_nodes

try:
    from fastapi.testclient import TestClient
except Exception:                                             # noqa: BLE001
    TestClient = None


UTC = timezone.utc


def test_pairing_certificate_has_both_eku_roles_and_keeps_identity_1815(tmp_path):
    from cryptography import x509
    from cryptography.x509.oid import ExtendedKeyUsageOID

    conn = db.connect(tmp_path / "identity.db")
    try:
        ident = storage_nodes.ensure_identity(conn, tmp_path / "node",
                                              host_sans=["127.0.0.1"])
        cert = x509.load_pem_x509_certificate(ident["cert_pem"])
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        assert set(eku) == {ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH}
        assert cert.not_valid_after_utc - cert.not_valid_before_utc == (
            timedelta(days=3650) + interserver_auth.NOT_BEFORE_BACKDATE)
        loaded = storage_nodes.ensure_identity(conn, tmp_path / "node")
        assert loaded["cert_pem"] == ident["cert_pem"]
        assert loaded["key_pem"] == ident["key_pem"]
        assert loaded["server_spki"] == ident["server_spki"]
    finally:
        conn.close()


def test_default_generator_certificate_supports_both_mtls_roles_1815():
    from cryptography import x509
    from cryptography.x509.oid import ExtendedKeyUsageOID

    cert_pem, _ = interserver_auth.generate_self_signed("pairing-peer")
    cert = x509.load_pem_x509_certificate(cert_pem)
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert set(eku) == {ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH}


# --- (א) פרימיטיבים ללא-TLS: קוד, טוקן, URL, loopback -----------------------

def test_pairing_code_roundtrips_and_normalizes():
    code = interserver_auth.generate_pairing_code()
    salt, digest = interserver_auth.hash_pairing_code(code)
    assert interserver_auth.verify_pairing_code(code, salt, digest)
    # נורמליזציה: רישיות ומקפים אינם משנים.
    assert interserver_auth.verify_pairing_code(
        code.replace("-", "").lower(), salt, digest)


def test_pairing_code_wrong_is_rejected():
    salt, digest = interserver_auth.hash_pairing_code("ABC-DEF-GHJ")
    assert not interserver_auth.verify_pairing_code("ABC-DEF-GHK", salt, digest)


def test_token_verify_matches_and_mismatches():
    token = interserver_auth.generate_token()
    assert len(bytes.fromhex(token)) == 32          # 256 סיביות
    stored = interserver_auth.hash_token(token)
    assert interserver_auth.verify_token(token, stored)
    assert not interserver_auth.verify_token(interserver_auth.generate_token(), stored)


def test_parse_interserver_url_accepts_strict_https():
    host, port, path = interserver_auth.parse_interserver_url(
        "https://10.20.0.12:8443/api/interserver/v1")
    assert (host, port, path) == ("10.20.0.12", 8443, "/api/interserver/v1")


@pytest.mark.parametrize("url", [
    "http://10.20.0.12:8443/api",          # לא https
    "https://10.20.0.12/api",              # פורט חסר
    "https://u:p@10.20.0.12:8443/api",     # אישורים
    "https://10.20.0.12:8443/api?x=1",     # query
    "https://10.20.0.12:8443/api#frag",    # fragment
    "ftp://10.20.0.12:8443",               # סכמה זרה
])
def test_parse_interserver_url_rejects_unsafe(url):
    with pytest.raises(interserver_auth.InterserverURLError):
        interserver_auth.parse_interserver_url(url)


@pytest.mark.parametrize("host,expected", [
    ("127.0.0.1", True), ("127.5.5.5", True), ("::1", True),
    ("::ffff:127.0.0.1", True),
    ("10.0.0.5", False), ("192.168.1.1", False), ("", False), (None, False),
    ("::ffff:10.0.0.1", False),
    # ‏#860: קידומת "127." אינה כתובת — רק כתובת loopback אמיתית עוברת.
    ("127.evil", False), ("127.0.0.1.9", False), ("127.", False),
])
def test_is_loopback(host, expected):
    assert interserver_auth.is_loopback(host) is expected


def test_redact_hides_secrets():
    token = interserver_auth.generate_token()
    line = f"Authorization: Bearer {token} code=ABC-DEF-GHJ"
    out = interserver_auth.redact_secrets(line)
    assert token not in out
    assert "ABC-DEF-GHJ" not in out
    assert "[redacted]" in out


def test_redact_hides_a_bare_pairing_code():
    # ‏#860: הקוד בצורתו (XXX-XXX-XXX) מוסתר גם כשאין לפניו `code=` —
    # למשל בהודעת שגיאה שמצטטת את מה שהמפעיל הקליד.
    code = interserver_auth.generate_pairing_code()
    out = interserver_auth.redact_secrets(f"pairing failed for {code} at 10.0.0.5")
    assert code not in out
    assert "10.0.0.5" in out                      # מה שאינו סוד נשאר


@pytest.mark.parametrize("actual,expected", [
    ("", ""), (None, None), ("", "ab12"), ("ab12", ""), (None, "ab12"),
])
def test_verify_pinned_spki_value_empty_is_never_a_match(actual, expected):
    # ‏#860: ריק מול ריק "עבר" — pin שטרם נשמר מול begin בלי server_spki.
    assert interserver_auth.verify_pinned_spki_value(actual, expected) is False


def test_verify_pinned_spki_value_matches_case_insensitively():
    assert interserver_auth.verify_pinned_spki_value("AB12", "ab12") is True
    assert interserver_auth.verify_pinned_spki_value("ab12", "ab13") is False


def test_node_id_from_spki_rejects_empty():
    # ‏#860: "sn_" בלי SPKI הוא מזהה בלי מקור — לא ערך.
    with pytest.raises(ValueError):
        interserver_auth.node_id_from_spki("")


# --- (ב) חלון ה-pairing ברמת ה-DB: קוד, תפוגה, ניסיונות ---------------------

@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "win.db")


def test_window_open_then_correct_code_consumes(conn):
    code = interserver_auth.open_pairing_window(conn)
    assert interserver_auth.pairing_window_status(conn)["open"] is True
    assert interserver_auth.verify_and_consume_code(conn, code) is True
    # חד-פעמי: אחרי צריכה החלון סגור, ואותו קוד לא עובד שוב.
    assert interserver_auth.pairing_window_status(conn)["open"] is False
    assert interserver_auth.verify_and_consume_code(conn, code) is False


def test_window_required_no_window_rejects(conn):
    # אין חלון פתוח כלל — חומר קוד תקין נדחה.
    assert interserver_auth.verify_and_consume_code(conn, "ABC-DEF-GHJ") is False


def test_window_wrong_code_rejected(conn):
    interserver_auth.open_pairing_window(conn)
    assert interserver_auth.verify_and_consume_code(conn, "ZZZ-ZZZ-ZZZ") is False
    # קוד שגוי בודד אינו סוגר את החלון (עוד יש ניסיונות).
    assert interserver_auth.pairing_window_status(conn)["open"] is True


def test_window_expired_code_rejected(conn):
    now = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)
    code = interserver_auth.open_pairing_window(conn, ttl_seconds=180, now=now)
    later = now + timedelta(seconds=181)
    assert interserver_auth.verify_and_consume_code(conn, code, now=later) is False
    assert interserver_auth.pairing_window_status(conn, now=later)["open"] is False


def test_window_attempts_exhausted_closes(conn):
    interserver_auth.open_pairing_window(conn, max_attempts=3)
    for _ in range(3):
        assert interserver_auth.verify_and_consume_code(conn, "ZZZ-ZZZ-ZZZ") is False
    # אחרי מיצוי הניסיונות החלון נסגר — גם הקוד הנכון כבר לא יתקבל.
    assert interserver_auth.pairing_window_status(conn)["open"] is False


def test_window_status_never_returns_code(conn):
    interserver_auth.open_pairing_window(conn)
    status = interserver_auth.pairing_window_status(conn)
    assert "code" not in status and "code_hash" not in status


def test_open_window_rejects_ttl_outside_locked_range(conn):
    with pytest.raises(ValueError):
        interserver_auth.open_pairing_window(conn, ttl_seconds=60)     # < 120
    with pytest.raises(ValueError):
        interserver_auth.open_pairing_window(conn, ttl_seconds=601)    # > 600 (16/10: DCUI F4)


# --- (ב') מרוץ ב-verify_and_consume_code (#745) -----------------------------
#
# שני מרוצים על read-modify-write לא-אטומי. הבקרה השלילית דטרמיניסטית:
# ``verify_pairing_code`` מוחלף בגרסה שמשהה 0.2s, כך שכל התהליכונים
# מספיקים לקרוא את החלון לפני שהראשון צורך/מפחית. הקוד המתוקן עוטף את
# כל המסלול בנעילה אחת, ולכן ההשהיה מתרחשת **בתוך** הנעילה — מסורג,
# ולא במקביל.

def _run_in_parallel(fn, n):
    """מריץ את ``fn`` ב-n תהליכונים שמתחילים יחד (barrier). מחזיר
    ‏(תוצאות, שגיאות) לפי אינדקס."""
    barrier = threading.Barrier(n)
    results: list = [None] * n
    errors: list = [None] * n

    def worker(i):
        barrier.wait()
        try:
            results[i] = fn()
        except BaseException as exc:                             # noqa: BLE001
            errors[i] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results, errors


def _install_slow_verify(monkeypatch):
    real = interserver_auth.verify_pairing_code

    def slow(*args, **kwargs):
        time.sleep(0.2)                 # מרחיב את חלון ה-read-modify-write
        return real(*args, **kwargs)

    monkeypatch.setattr(interserver_auth, "verify_pairing_code", slow)


def test_concurrent_correct_code_consumed_exactly_once(conn, monkeypatch):
    # #745(א): קוד חד-פעמי נצרך פעם אחת בלבד גם תחת בקשות מקבילות.
    code = interserver_auth.open_pairing_window(conn)
    _install_slow_verify(monkeypatch)
    n = 6
    results, errors = _run_in_parallel(
        lambda: interserver_auth.verify_and_consume_code(conn, code), n)
    assert errors == [None] * n, errors
    assert results.count(True) == 1, results          # לא יותר מ-enrollment אחד
    assert interserver_auth.pairing_window_status(conn)["open"] is False


def test_concurrent_wrong_codes_all_counted(conn, monkeypatch):
    # #745(ב): חמישה ניחושים שגויים מקבילים נספרים כולם — המונה מתאפס
    # והחלון נסגר, ולא נשאר פתוח עם attempts_remaining=4.
    interserver_auth.open_pairing_window(conn, max_attempts=5)
    _install_slow_verify(monkeypatch)
    n = 5
    results, errors = _run_in_parallel(
        lambda: interserver_auth.verify_and_consume_code(conn, "ZZZ-ZZZ-ZZZ"), n)
    assert errors == [None] * n, errors
    assert all(r is False for r in results), results
    assert interserver_auth.pairing_window_status(conn)["open"] is False


# --- (ג) קובץ אישורים 0600 --------------------------------------------------

def test_credential_write_read_roundtrip(tmp_path):
    path = tmp_path / "node.cred"
    token = interserver_auth.generate_token()
    interserver_auth.write_credential_0600(path, token)
    assert interserver_auth.load_credential(path) == token


def test_credential_tmp_symlink_is_not_followed(tmp_path):
    # ‏#860: שם הקובץ הזמני קבוע (`.<name>.tmp`). symlink בשם הזה שמפנה
    # החוצה היה מקבל את הטוקן (O_TRUNC עוקב אחרי קישור). עכשיו הקישור
    # מוסר ו-O_EXCL|O_NOFOLLOW פותח קובץ חדש — היעד נשאר כפי שהיה.
    path = tmp_path / "node.cred"
    target = tmp_path / "attacker.target"
    target.write_text("UNTOUCHED", encoding="utf-8")
    try:
        os.symlink(target, tmp_path / ".node.cred.tmp")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink אינו זמין כאן (ווינדוס בלי הרשאה): {exc}")
    interserver_auth.write_credential_0600(path, "SECRET-TOKEN")
    assert target.read_text(encoding="utf-8") == "UNTOUCHED"
    assert interserver_auth.load_credential(path) == "SECRET-TOKEN"


def test_credential_stale_tmp_from_a_crash_is_replaced(tmp_path):
    # ‏O_EXCL לבדו היה הופך שארית של ריצה שקרסה לחסימה קבועה של הרישום.
    path = tmp_path / "node.cred"
    (tmp_path / ".node.cred.tmp").write_text("half-written", encoding="utf-8")
    interserver_auth.write_credential_0600(path, "tok")
    assert interserver_auth.load_credential(path) == "tok"
    assert not (tmp_path / ".node.cred.tmp").exists()


@pytest.mark.skipif(os.name != "posix", reason="הרשאות 0600 ובעלות הן POSIX")
def test_credential_file_is_0600(tmp_path):
    path = tmp_path / "node.cred"
    interserver_auth.write_credential_0600(path, "tok")
    assert os.stat(path).st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name != "posix", reason="הרשאות פתוחות נדחות ב-POSIX")
def test_credential_rejects_open_permissions(tmp_path):
    path = tmp_path / "node.cred"
    interserver_auth.write_credential_0600(path, "tok")
    os.chmod(path, 0o644)
    with pytest.raises(interserver_auth.CredentialFileError):
        interserver_auth.load_credential(path)


@pytest.mark.skipif(os.name != "posix", reason="TOCTOU של symlink ו-O_NOFOLLOW הם POSIX")
def test_credential_toctou_symlink_swap_not_followed(tmp_path, monkeypatch):
    # #747 (בקרה שלילית): מדמה תוקף שמחליף את הקובץ המאומת ב-symlink
    # **בין** האימות ל-open. הקוד הישן (islink→lstat→open) אימת את הקובץ
    # הרגיל ואז פתח את ה-symlink שהוחלף — וקרא את סוד התוקף. התיקון פותח
    # עם O_NOFOLLOW ומאמת על ה-fd, ואינו קורא lstat כלל — ולכן אינו נפתח
    # לחלון הזה.
    path = tmp_path / "node.cred"
    interserver_auth.write_credential_0600(path, "LEGIT-TOKEN")
    target = tmp_path / "attacker.target"
    target.write_text("STOLEN-SECRET", encoding="utf-8")
    os.chmod(target, 0o600)

    real_lstat = os.lstat

    def swapping_lstat(p, *args, **kwargs):
        st = real_lstat(p, *args, **kwargs)          # מצב הקובץ הרגיל המאומת
        if os.fspath(p) == os.fspath(path):
            os.unlink(path)                          # התוקף פועל בחלון ה-TOCTOU
            os.symlink(target, path)
        return st

    monkeypatch.setattr(os, "lstat", swapping_lstat)
    result = interserver_auth.load_credential(path)
    assert result != "STOLEN-SECRET"                 # ה-symlink לא נעקב
    assert result == "LEGIT-TOKEN"


# --- (ד) הצד המקומי של המשני על השרת המלא -----------------------------------

def _local_client(server, host="127.0.0.1", user="noc", pw="admin-pass-123"):
    """‏TestClient עם כתובת peer מפורשת, מחובר. loopback כברירת מחדל."""
    from conftest import complete_console_login
    client = TestClient(server["app"], client=(host, 40000))
    complete_console_login(client, server["ctx"].conn, user, pw)
    return client


def _make_secondary(server):
    db.set_setting(server["ctx"].conn, storage_nodes.ROLE_KEY, "secondary")


def test_secondary_local_pairing_happy_path(server):
    _make_secondary(server)
    client = _local_client(server)
    resp = client.post("/api/console/storage-pairing-window")
    assert resp.status_code == 200
    body = resp.json()
    assert body["protocol_version"] == interserver_auth.PROTOCOL_VERSION
    assert body["node_id"] and body["code"]
    # GET לעולם אינו מחזיר את הקוד.
    status = client.get("/api/console/storage-pairing-window").json()
    assert status["open"] is True and "code" not in status


def test_pairing_window_deploy_forbidden(server):
    _make_secondary(server)
    client = _local_client(server, user="labtech", pw="deploy-pass-1")
    assert client.post("/api/console/storage-pairing-window").status_code == 403


def test_pairing_window_remote_peer_forbidden(server):
    _make_secondary(server)
    client = _local_client(server, host="10.0.0.5")
    assert client.post("/api/console/storage-pairing-window").status_code == 403


def test_pairing_window_standalone_rejected(server):
    # ברירת המחדל של השרת היא standalone — חלון pairing מקומי אינו לו.
    client = _local_client(server)
    assert client.post("/api/console/storage-pairing-window").status_code == 409


def test_pairing_window_revoke_first_when_parent_exists(server):
    _make_secondary(server)
    conn = server["ctx"].conn
    conn.execute(
        "INSERT INTO parent_credentials (singleton, parent_id, token_hash,"
        " bound_cert_ref, pinned_parent_spki, protocol_version, enrolled_at)"
        " VALUES (1, 'p1', ?, 'cert:1', 'spki:1', '2.1', ?)",
        (interserver_auth.hash_token("t"), db.now_iso()),
    )
    conn.commit()
    client = _local_client(server)
    resp = client.post("/api/console/storage-pairing-window")
    assert resp.status_code == 409
    assert "נתקו" in resp.json()["detail"]


def test_unbind_parent_clears_and_closes(server):
    _make_secondary(server)
    conn = server["ctx"].conn
    conn.execute(
        "INSERT INTO parent_credentials (singleton, parent_id, token_hash,"
        " bound_cert_ref, pinned_parent_spki, protocol_version, enrolled_at)"
        " VALUES (1, 'p1', ?, 'cert:1', 'spki:1', '2.1', ?)",
        (interserver_auth.hash_token("t"), db.now_iso()),
    )
    conn.commit()
    client = _local_client(server)
    assert client.delete("/api/console/storage-parent").status_code == 200
    assert storage_nodes.has_parent(conn) is False
    # ואז אפשר לפתוח חלון חדש (revoke-first הוסר).
    assert client.post("/api/console/storage-pairing-window").status_code == 200


# --- (ה) פרימיטיבי ה-TLS: SPKI, cert-ref, pin, exporter (#740) --------------

def test_spki_and_cert_ref_are_stable_and_distinct():
    cert, _key = interserver_auth.generate_self_signed("n1", ip_sans=["127.0.0.1"])
    spki = interserver_auth.spki_sha256(cert)
    ref = interserver_auth.certificate_ref(cert)
    assert len(bytes.fromhex(spki)) == 32 and len(bytes.fromhex(ref)) == 32
    # SPKI (מפתח) ו-cert-ref (תעודה שלמה) שונים זה מזה.
    assert spki != ref
    # יציב: אותה תעודה → אותה טביעה, מ-PEM ומ-DER כאחד.
    der = interserver_auth._to_der(cert)
    assert interserver_auth.spki_sha256(der) == spki
    assert interserver_auth.node_id_from_spki(spki) == "sn_" + spki[:16]


def test_verify_pinned_spki_matches_and_rejects():
    cert, _ = interserver_auth.generate_self_signed("n1", ip_sans=["127.0.0.1"])
    other, _ = interserver_auth.generate_self_signed("n2", ip_sans=["127.0.0.1"])
    spki = interserver_auth.spki_sha256(cert)
    assert interserver_auth.verify_pinned_spki(cert, spki) is True
    assert interserver_auth.verify_pinned_spki(cert, spki.upper()) is True   # case
    assert interserver_auth.verify_pinned_spki(other, spki) is False


def test_channel_binding_capability_present_with_pyopenssl():
    # ‏[ממצא #740] היכולת מגיעה מ-pyOpenSSL, לא מ-stdlib ssl — ולכן היא
    # קיימת גם על 3.12, וה-exporter אינו מדולג כאן.
    assert interserver_auth.can_export_keying_material() is True


# --- (ו) דגלי ה-capability של #740 ------------------------------------------

def _caps(client):
    resp = client.get("/api/console/me")
    assert resp.status_code == 200
    return resp.json()["capabilities"]


def test_enroll_capability_standalone_admin_true_even_with_zero_nodes(server):
    caps = _caps(server["admin"])
    assert caps["enroll_secondary"] is True         # אין צורך במשני קיים
    assert caps["open_local_pairing"] is False       # לא משני
    # ‏#1073: ל-deploy אין /me בכלל — הקונסולה סגורה בפניו.
    assert server["deploy"].get("/api/console/me").status_code == 403


def test_local_pairing_capability_only_on_secondary_admin(server):
    _make_secondary(server)
    caps = _caps(server["admin"])
    assert caps["open_local_pairing"] is True
    assert caps["enroll_secondary"] is False         # משני אינו מוסיף ילדים
    assert server["deploy"].get("/api/console/me").status_code == 403   # #1073


# --- (ז) שומרי הכניסה הבין-שרתית בשכבת ה-service (#740) ----------------------
#
# ‏peer מזויף (TlsPeer) + conn של משני עם זהות — כדי לבדוק כל שומר בלי
# להרים TLS חי. ה-handshake האמיתי נבדק ב-test_interserver_tls.py.

from server import interserver_api                           # noqa: E402


def _secondary(tmp_path):
    conn = db.connect(tmp_path / "sec.db")
    db.set_setting(conn, storage_nodes.ROLE_KEY, "secondary")
    ident = storage_nodes.ensure_identity(conn, tmp_path / "sec")
    return conn, ident


def _peer(cert_pem, exporter=b"E" * 32, ver="TLSv1.3"):
    der = interserver_auth._to_der(cert_pem) if cert_pem else None
    return interserver_api.TlsPeer(ver, der, exporter)


def _do_pair(conn, ident, primary_cert, *, exporter=b"E" * 32,
             expected_spki=None, protocol="2.1"):
    handles = {}
    code = interserver_auth.open_pairing_window(conn)
    peer = _peer(primary_cert, exporter=exporter)
    begin = interserver_api.pair_begin(conn, handles, peer, {
        "protocol_version": protocol,
        "expected_secondary_spki": expected_spki or ident["server_spki"],
        "primary_id": "primary-1",
    })
    comp = interserver_api.pair_complete(conn, handles, peer, {
        "handle": begin["handle"], "code": code, "protocol_version": protocol,
    })
    return begin, comp, handles


def test_ingress_pair_then_ping_happy(tmp_path):
    conn, ident = _secondary(tmp_path)
    primary, _ = interserver_auth.generate_self_signed("primary", ip_sans=["127.0.0.1"])
    _begin, comp, _ = _do_pair(conn, ident, primary)
    assert len(bytes.fromhex(comp["token"])) == 32
    assert storage_nodes.parent_last_contact(conn) is None      # #1564: pairing alone is not contact
    res = interserver_api.ping(conn, _peer(primary), token=comp["token"],
                               protocol_version="2.1")
    assert res["ok"] is True and res["role"] == "secondary"
    stamp = storage_nodes.parent_last_contact(conn)
    assert stamp is not None
    assert abs((datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds()) < 60
    # ...and the DCUI reads that same stamp from the DB (#1564)
    from server.dcui import view
    assert view.collect_extras(conn).parent_last_contact == stamp
    # הטוקן והקוד אינם ב-DB בטקסט גלוי.
    dump = "".join(str(r) for r in conn.execute(
        "SELECT * FROM parent_credentials").fetchall())
    assert comp["token"] not in dump


def test_a_secondary_paired_before_1564_is_upgraded_to_unknown_contact(tmp_path):
    """#1564 migration: a parent_credentials row from before the column existed.
    Read as-is it is still paired (contact unknown, not "not paired yet");
    after db.connect the column exists and is NULL -- unknown, never green --
    and the next authenticated request stamps it."""
    from server.dcui import view
    conn, ident = _secondary(tmp_path)
    primary, _ = interserver_auth.generate_self_signed("primary", ip_sans=["127.0.0.1"])
    _begin, comp, _ = _do_pair(conn, ident, primary)
    conn.execute("ALTER TABLE parent_credentials DROP COLUMN last_contact_at")
    conn.commit()
    old = view.collect_extras(conn)
    assert old.has_parent is True and old.parent_last_contact is None
    conn.close()

    conn = db.connect(tmp_path / "sec.db")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(parent_credentials)")}
    assert "last_contact_at" in cols
    assert storage_nodes.has_parent(conn) is True
    assert storage_nodes.parent_last_contact(conn) is None
    interserver_api.ping(conn, _peer(primary), token=comp["token"], protocol_version="2.1")
    assert storage_nodes.parent_last_contact(conn) is not None


def test_ingress_requires_tls13(tmp_path):
    conn, ident = _secondary(tmp_path)
    primary, _ = interserver_auth.generate_self_signed("p", ip_sans=["127.0.0.1"])
    peer = _peer(primary, ver="TLSv1.2")
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.pair_begin(conn, {}, peer, {
            "protocol_version": "2.1", "expected_secondary_spki": ident["server_spki"]})
    assert e.value.status == 421


def test_ingress_wrong_protocol_version_rejected(tmp_path):
    conn, ident = _secondary(tmp_path)
    primary, _ = interserver_auth.generate_self_signed("p", ip_sans=["127.0.0.1"])
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.pair_begin(conn, {}, _peer(primary), {
            "protocol_version": "9.9", "expected_secondary_spki": ident["server_spki"]})
    assert e.value.status == 426


def test_ingress_mutual_spki_mismatch_before_code(tmp_path):
    conn, ident = _secondary(tmp_path)
    primary, _ = interserver_auth.generate_self_signed("p", ip_sans=["127.0.0.1"])
    interserver_auth.open_pairing_window(conn)              # חלון פתוח
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.pair_begin(conn, {}, _peer(primary), {
            "protocol_version": "2.1", "expected_secondary_spki": "00" * 32})
    assert e.value.status == 400
    # החלון עדיין פתוח — נדחה לפני שהקוד נגע במשהו.
    assert interserver_auth.pairing_window_status(conn)["open"] is True


def test_ingress_role_standalone_rejected(tmp_path):
    conn = db.connect(tmp_path / "sa.db")                   # ברירת מחדל standalone
    ident = storage_nodes.ensure_identity(conn, tmp_path / "sa")
    primary, _ = interserver_auth.generate_self_signed("p", ip_sans=["127.0.0.1"])
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.pair_begin(conn, {}, _peer(primary), {
            "protocol_version": "2.1", "expected_secondary_spki": ident["server_spki"]})
    assert e.value.status == 409


def test_ingress_window_required(tmp_path):
    conn, ident = _secondary(tmp_path)
    primary, _ = interserver_auth.generate_self_signed("p", ip_sans=["127.0.0.1"])
    handles = {}
    peer = _peer(primary)
    begin = interserver_api.pair_begin(conn, handles, peer, {
        "protocol_version": "2.1", "expected_secondary_spki": ident["server_spki"]})
    # אין חלון פתוח → complete עם קוד "כלשהו" נדחה.
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.pair_complete(conn, handles, peer, {
            "handle": begin["handle"], "code": "ABC-DEF-GHJ", "protocol_version": "2.1"})
    assert e.value.status == 401
    assert storage_nodes.parent_last_contact(conn) is None


def test_ingress_wrong_code_rejected(tmp_path):
    conn, ident = _secondary(tmp_path)
    primary, _ = interserver_auth.generate_self_signed("p", ip_sans=["127.0.0.1"])
    handles = {}
    interserver_auth.open_pairing_window(conn)
    peer = _peer(primary)
    begin = interserver_api.pair_begin(conn, handles, peer, {
        "protocol_version": "2.1", "expected_secondary_spki": ident["server_spki"]})
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.pair_complete(conn, handles, peer, {
            "handle": begin["handle"], "code": "ZZZ-ZZZ-ZZZ", "protocol_version": "2.1"})
    assert e.value.status == 401


def test_ingress_channel_binding_begin_a_complete_b(tmp_path):
    conn, ident = _secondary(tmp_path)
    primary, _ = interserver_auth.generate_self_signed("p", ip_sans=["127.0.0.1"])
    handles = {}
    code = interserver_auth.open_pairing_window(conn)
    # begin על session A (exporter A)
    begin = interserver_api.pair_begin(conn, handles, _peer(primary, exporter=b"A" * 32), {
        "protocol_version": "2.1", "expected_secondary_spki": ident["server_spki"]})
    # complete על session B (exporter B) — אותה תעודה, ערוץ אחר → נדחה.
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.pair_complete(conn, handles, _peer(primary, exporter=b"B" * 32), {
            "handle": begin["handle"], "code": code, "protocol_version": "2.1"})
    assert e.value.status == 400


def test_ingress_single_parent_second_pair_rejected(tmp_path):
    conn, ident = _secondary(tmp_path)
    primary, _ = interserver_auth.generate_self_signed("p", ip_sans=["127.0.0.1"])
    _do_pair(conn, ident, primary)                          # אב ראשון נרשם
    other, _ = interserver_auth.generate_self_signed("p2", ip_sans=["127.0.0.1"])
    with pytest.raises(interserver_api.PairError) as e:
        _do_pair(conn, ident, other)
    assert e.value.status == 409


def test_ingress_ping_wrong_token_rejected(tmp_path):
    conn, ident = _secondary(tmp_path)
    primary, _ = interserver_auth.generate_self_signed("p", ip_sans=["127.0.0.1"])
    _do_pair(conn, ident, primary)
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.ping(conn, _peer(primary),
                             token=interserver_auth.generate_token(),
                             protocol_version="2.1")
    assert e.value.status == 401
    assert storage_nodes.parent_last_contact(conn) is None


def test_ingress_ping_console_cookie_rejected(tmp_path):
    conn, ident = _secondary(tmp_path)
    primary, _ = interserver_auth.generate_self_signed("p", ip_sans=["127.0.0.1"])
    _begin, comp, _ = _do_pair(conn, ident, primary)
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.ping(conn, _peer(primary), token=comp["token"],
                             protocol_version="2.1", has_console_cookie=True)
    assert e.value.status == 401


def test_ingress_ping_post_pin_spki_mismatch_hard_fail(tmp_path):
    conn, ident = _secondary(tmp_path)
    primary, _ = interserver_auth.generate_self_signed("p", ip_sans=["127.0.0.1"])
    _begin, comp, _ = _do_pair(conn, ident, primary)
    # מצב "ה-SPKI של הראשי השתנה": משנים את ה-pin השמור → 401 קשה, בלי TOFU.
    with db._write_lock, db.writing(conn):
        conn.execute("UPDATE parent_credentials SET pinned_parent_spki = ?"
                     " WHERE singleton = 1", ("00" * 32,))
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.ping(conn, _peer(primary), token=comp["token"],
                             protocol_version="2.1")
    assert e.value.status == 401
    assert storage_nodes.parent_last_contact(conn) is None


# --- (ז*) הבקרה השלילית המכריעה: RFC 8705 sender-constraint -----------------

def test_ingress_ping_sender_constraint_decisive(tmp_path, monkeypatch):
    """טוקן נכון + תעודת-לקוח **אחרת** נדחה (401). וההוכחה שהכריכה היא
    שמגינה: מנטרלים אך ורק את בדיקת הכריכה (``_peer_bound``) — ואז אותה
    בקשה בדיוק **מצליחה**; מחזירים — ושוב 401. בלי זה, טוקן גנוב היה מספיק."""
    conn, ident = _secondary(tmp_path)
    primary, _ = interserver_auth.generate_self_signed("p", ip_sans=["127.0.0.1"])
    _begin, comp, _ = _do_pair(conn, ident, primary)
    attacker, _ = interserver_auth.generate_self_signed("thief", ip_sans=["127.0.0.1"])

    # עם הכריכה: טוקן נכון, תעודה אחרת → 401 (sender-constraint).
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.ping(conn, _peer(attacker), token=comp["token"],
                             protocol_version="2.1")
    assert e.value.status == 401
    assert storage_nodes.parent_last_contact(conn) is None

    # מנטרלים את הכריכה בלבד (אימות הטוקן נשאר) → הטוקן הגנוב עובר.
    monkeypatch.setattr(interserver_api, "_peer_bound", lambda *a: True)
    leaked = interserver_api.ping(conn, _peer(attacker), token=comp["token"],
                                  protocol_version="2.1")
    assert leaked["ok"] is True                             # הבאג משוחזר
    monkeypatch.undo()

    # מחזירים את הכריכה → אותה בקשה שוב 401.
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.ping(conn, _peer(attacker), token=comp["token"],
                             protocol_version="2.1")
    assert e.value.status == 401


# --- (ז**) #1122: חידוש תעודה בראשי — TOFU על המפתח, לא על התעודה ----------
#
# ‏TLS 1.3 מוכיח ב-CertificateVerify שהפונה מחזיק את המפתח הפרטי של התעודה
# שהציג. לכן תעודה **חדשה על אותו מפתח** (SPKI זהה) היא אותו ראשי, ומאמצים
# אותה — אבל רק אחרי שהטוקן אומת, ועם שורת יומן. מפתח **אחר** נשאר 401 קשה,
# בשם (``peer_cert_changed``) כדי שהקונסולה בראשי תגיד למנהל מה לעשות.

PEER_CERT_CHANGED_DETAIL = "התעודה של הראשי השתנתה — יש לבצע רישום מחדש"


def renew_cert(cert_pem: bytes, key_pem: bytes) -> bytes:
    """תעודה חדשה על **אותו** מפתח: SPKI זהה, טביעת תעודה שונה — מה שחידוש
    תעודה בראשי מייצר. נבנית כאן ולא בקוד הייצור, כדי שהבקרה השלילית תיכשל
    על ההתנהגות (401) ולא על חתימה חסרה."""
    import datetime
    from ipaddress import ip_address
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    key = load_pem_private_key(key_pem, password=None)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "renewed")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName(
                [x509.IPAddress(ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    renewed = cert.public_bytes(serialization.Encoding.PEM)
    assert interserver_auth.spki_sha256(renewed) == interserver_auth.spki_sha256(cert_pem)
    assert (interserver_auth.certificate_ref(renewed)
            != interserver_auth.certificate_ref(cert_pem))
    return renewed


def _bound_ref(conn) -> str:
    return conn.execute("SELECT bound_cert_ref FROM parent_credentials"
                        " WHERE singleton = 1").fetchone()["bound_cert_ref"]


def _renewals(conn) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM journal"
                        " WHERE event = 'storage_parent_cert_renewed'").fetchone()["n"]


def test_ingress_renewed_cert_on_the_same_key_is_adopted(tmp_path):
    conn, ident = _secondary(tmp_path)
    primary, key = interserver_auth.generate_self_signed("p", ip_sans=["127.0.0.1"])
    _begin, comp, _ = _do_pair(conn, ident, primary)
    renewed = renew_cert(primary, key)

    res = interserver_api.ping(conn, _peer(renewed), token=comp["token"],
                               protocol_version="2.1")
    assert res["ok"] is True
    # הכריכה עברה לתעודה החדשה; ה-pin (המפתח) לא זז; יומן אחד.
    assert _bound_ref(conn) == interserver_auth.certificate_ref(renewed)
    assert conn.execute("SELECT pinned_parent_spki FROM parent_credentials"
                        " WHERE singleton = 1").fetchone()["pinned_parent_spki"]         == interserver_auth.spki_sha256(primary)
    assert _renewals(conn) == 1
    # ‏ping שני עם אותה תעודה — כבר כרוכה, בלי יומן נוסף.
    interserver_api.ping(conn, _peer(renewed), token=comp["token"], protocol_version="2.1")
    assert _renewals(conn) == 1


def test_ingress_renewed_cert_with_a_wrong_token_is_not_adopted(tmp_path):
    """הטוקן נבדק **לפני** האימוץ: תעודה חדשה על המפתח הנכון בלי הטוקן היא
    401 "טוקן שגוי", והכריכה הישנה נשארת כפי שהיא."""
    conn, ident = _secondary(tmp_path)
    primary, key = interserver_auth.generate_self_signed("p", ip_sans=["127.0.0.1"])
    _do_pair(conn, ident, primary)
    before = _bound_ref(conn)
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.ping(conn, _peer(renew_cert(primary, key)),
                             token=interserver_auth.generate_token(),
                             protocol_version="2.1")
    assert e.value.status == 401 and getattr(e.value, "code", None) is None
    assert _bound_ref(conn) == before
    assert _renewals(conn) == 0


def test_ingress_changed_key_is_named_peer_cert_changed(tmp_path):
    """מפתח אחר (ראשי שהוחלף, או גנב עם הטוקן) → 401 **בשם**, בלי אימוץ."""
    conn, ident = _secondary(tmp_path)
    primary, _ = interserver_auth.generate_self_signed("p", ip_sans=["127.0.0.1"])
    _begin, comp, _ = _do_pair(conn, ident, primary)
    other, _ = interserver_auth.generate_self_signed("p2", ip_sans=["127.0.0.1"])
    before = _bound_ref(conn)
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.ping(conn, _peer(other), token=comp["token"],
                             protocol_version="2.1")
    assert e.value.status == 401
    assert e.value.detail == PEER_CERT_CHANGED_DETAIL
    assert getattr(e.value, "code", None) == "peer_cert_changed"
    assert _bound_ref(conn) == before
    assert _renewals(conn) == 0


# --- (ח) שכבת ה-app: routes + דחיית עוגייה דרך TestClient --------------------

def _interserver_app(conn, ident, peer_holder):
    class _Ctx:
        pass
    ctx = _Ctx(); ctx.conn = conn; ctx.data_dir = None
    return interserver_api.create_interserver_app(ctx, lambda _req: peer_holder["peer"])


def test_app_pair_and_ping_over_routes(tmp_path):
    if TestClient is None:
        pytest.skip("fastapi required")
    conn, ident = _secondary(tmp_path)
    primary, _ = interserver_auth.generate_self_signed("p", ip_sans=["127.0.0.1"])
    holder = {"peer": _peer(primary)}
    app = _interserver_app(conn, ident, holder)
    client = TestClient(app)
    code = interserver_auth.open_pairing_window(conn)
    begin = client.post("/api/interserver/v1/pair-begin", json={
        "protocol_version": "2.1", "expected_secondary_spki": ident["server_spki"],
        "primary_id": "p1"})
    assert begin.status_code == 200
    comp = client.post("/api/interserver/v1/pair-complete", json={
        "handle": begin.json()["handle"], "code": code, "protocol_version": "2.1"})
    assert comp.status_code == 200
    token = comp.json()["token"]
    ok = client.get("/api/interserver/v1/ping", headers={
        "Authorization": f"Bearer {token}", "ImageCtl-Protocol-Version": "2.1"})
    assert ok.status_code == 200 and ok.json()["ok"] is True


def test_app_ping_rejects_console_cookie(tmp_path):
    if TestClient is None:
        pytest.skip("fastapi required")
    conn, ident = _secondary(tmp_path)
    primary, _ = interserver_auth.generate_self_signed("p", ip_sans=["127.0.0.1"])
    holder = {"peer": _peer(primary)}
    app = _interserver_app(conn, ident, holder)
    _begin, comp, _ = _do_pair(conn, ident, primary)
    client = TestClient(app)
    client.cookies.set("imagectl_session", "whatever")
    resp = client.get("/api/interserver/v1/ping", headers={
        "Authorization": f"Bearer {comp['token']}", "ImageCtl-Protocol-Version": "2.1"})
    assert resp.status_code == 401


# --- (ט) צד הראשי: enroll_node ורישום דרך הקונסולה --------------------------

def test_enroll_node_inserts_trust_material(server):
    conn = server["ctx"].conn
    user = ("noc", "admin")
    rid = storage_nodes.enroll_node(
        conn, user, label="BRANCH-B", base_url="https://sec.example:8443/api",
        node_id="sn_aaaaaaaaaaaaaaaa", pinned_spki="aa" * 32, client_cert_ref="bb" * 32,
        credential_ref="/data/secondaries/sn_aaaaaaaaaaaaaaaa.token", protocol_version="2.1")
    row = conn.execute("SELECT * FROM storage_nodes WHERE id = ?", (rid,)).fetchone()
    assert row["node_id"] == "sn_aaaaaaaaaaaaaaaa" and row["pinned_spki"] == "aa" * 32
    assert row["protocol_version"] == "2.1"


def test_enroll_route_writes_0600_token_and_row(server, monkeypatch, tmp_path):
    """מסלול הקונסולה: pairing (מדומה) → קובץ טוקן → רשומה. הטוקן אינו חוזר."""
    from server import storage_client
    admin = _local_client(server)                           # הראשי standalone; admin
    # מזייפים את השיחה הבין-שרתית — הצד הזה נבדק אמיתי ב-test_interserver_tls.
    fake_token = interserver_auth.generate_token()
    # ‏#883: המזהה נגזר מה-SPKI המוצמד (``sn_`` + 16 הקסה), לא מההצהרה.
    derived = interserver_auth.node_id_from_spki("cc" * 32)
    monkeypatch.setattr(storage_client, "pair_secondary", lambda *a, **k: {
        "token": fake_token, "parent_id": "p", "secondary_id": derived,
        "secondary_spki": "cc" * 32})
    resp = admin.post("/api/console/storage-nodes/enroll", json={
        "url": "https://10.20.0.30:8443/api/interserver/v1",
        "label": "BRANCH-C", "code": "ABC-DEF-GHJ",
        "expected_secondary_spki": "cc" * 32, "protocol_version": "2.1"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["node_id"] == derived
    assert fake_token not in resp.text                      # הטוקן לא חוזר
    row = server["ctx"].conn.execute(
        "SELECT credential_ref, pinned_spki FROM storage_nodes WHERE node_id = ?",
        (derived,)).fetchone()
    assert row["pinned_spki"] == "cc" * 32
    # הקובץ נכתב, ומכיל את הטוקן (רק שם — לא ב-DB/תשובה).
    from pathlib import Path
    assert Path(row["credential_ref"]).read_text().strip() == fake_token


def test_enroll_route_rejects_non_https_url(server, monkeypatch):
    admin = _local_client(server)
    resp = admin.post("/api/console/storage-nodes/enroll", json={
        "url": "http://10.20.0.30:8443/api", "label": "x", "code": "A",
        "expected_secondary_spki": "cc" * 32, "protocol_version": "2.1"})
    assert resp.status_code == 400


def test_enroll_route_wrong_protocol_version_rejected(server):
    admin = _local_client(server)
    resp = admin.post("/api/console/storage-nodes/enroll", json={
        "url": "https://10.20.0.30:8443/api", "label": "x", "code": "A",
        "expected_secondary_spki": "cc" * 32, "protocol_version": "1.0"})
    assert resp.status_code == 426


# --- (י) ‏#883: זהות המשני נגזרת מה-SPKI המוצמד, לא מהצהרת ה-JSON ---------
#
# ‏begin מחזיר ``secondary_id`` — הצהרה עצמית של המשני. עד #883 היא הפכה
# ל-``node_id`` ברשומה **ולשם קובץ הטוקן** (``secondaries/{id}.token``):
# משני מאומת-אך-עוין (או מפעיל שטעה בכתובת) יכול היה לכתוב קובץ 0600
# **מחוץ** לתיקייה, כמשתמש השרת. הבקרה השלילית: על הקוד הישן
# ``secondary_id="../x"`` יצר ``<data>/x.token`` — מחוץ ל-``secondaries/``.

def _fake_pair_begin(monkeypatch, *, secondary_id, spki, token=None):
    """מזייף רק את שיחת ה-TLS (begin+complete) של ``PinnedMTLSClient`` —
    כדי שהשוואת הזהות ב-``pair()`` עצמו תרוץ אמיתית."""
    from server import storage_client
    token = token or interserver_auth.generate_token()
    calls = []

    def fake_request(self, method, path, *, body=None, headers=None):
        calls.append(path)
        if path == "/pair-begin":
            return 200, {}, {"handle": "h1", "secondary_id": secondary_id,
                             "secondary_spki": spki, "server_nonce": "n",
                             "protocol_version": "2.1"}
        return 200, {}, {"token": token, "parent_id": "p", "protocol_version": "2.1"}

    monkeypatch.setattr(storage_client.PinnedMTLSClient, "_request", fake_request)
    monkeypatch.setattr(storage_client.PinnedMTLSClient, "open", lambda self: None)
    monkeypatch.setattr(storage_client.PinnedMTLSClient, "close", lambda self: None)
    return calls, token


def test_enroll_traversal_secondary_id_rejected_before_code_and_no_file(
        server, monkeypatch, tmp_path):
    """‏secondary_id="../x" → 400, לא נוצר שום קובץ, והקוד לא נשלח (אין
    pair-complete) — המשני לא נקשר לאב על סמך הצהרה שסותרת את המפתח."""
    admin = _local_client(server)
    spki = "cc" * 32
    calls, token = _fake_pair_begin(monkeypatch, secondary_id="../x", spki=spki)
    data_dir = tmp_path / "data"
    resp = admin.post("/api/console/storage-nodes/enroll", json={
        "url": "https://10.20.0.30:8443/api/interserver/v1",
        "label": "BRANCH-C", "code": "ABC-DEF-GHJ",
        "expected_secondary_spki": spki, "protocol_version": "2.1"})
    assert resp.status_code == 400, resp.text
    assert calls == ["/pair-begin"]                  # הקוד לא נשלח
    # שום קובץ טוקן — לא בתיקייה ולא מחוצה לה (הבקרה השלילית: main כתב
    # ``<data>/x.token``).
    written = [p for p in data_dir.rglob("*") if p.is_file()
               and p.read_text(errors="replace").strip() == token]
    assert written == []
    assert not (data_dir / "x.token").exists()
    assert server["ctx"].conn.execute(
        "SELECT COUNT(*) AS n FROM storage_nodes").fetchone()["n"] == 0


def test_enroll_node_id_is_derived_from_pinned_spki(server, monkeypatch, tmp_path):
    """המזהה ברשומה ובשם הקובץ הוא ``node_id_from_spki(expected_spki)`` —
    גם כשהמשני מצהיר על אותו ערך (המקרה התקין, כמו ImageCtl-Server2)."""
    admin = _local_client(server)
    spki = "dd" * 32
    derived = interserver_auth.node_id_from_spki(spki)
    _calls, token = _fake_pair_begin(monkeypatch, secondary_id=derived, spki=spki)
    resp = admin.post("/api/console/storage-nodes/enroll", json={
        "url": "https://10.20.0.31:8443/api/interserver/v1",
        "label": "BRANCH-D", "code": "ABC-DEF-GHJ",
        "expected_secondary_spki": spki, "protocol_version": "2.1"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["node_id"] == derived
    row = server["ctx"].conn.execute(
        "SELECT node_id, credential_ref FROM storage_nodes").fetchone()
    assert row["node_id"] == derived
    cred = Path(row["credential_ref"])
    assert cred.name == f"{derived}.token" and cred.parent.name == "secondaries"
    assert cred.read_text().strip() == token


def test_enroll_declared_id_differing_from_key_is_a_loud_mismatch(
        server, monkeypatch, tmp_path):
    """הצהרה שונה מהנגזרת — גם בפורמט תקין — היא סתירה בין המפתח לזהות,
    ונכשלת בקול (400) לפני שליחת הקוד; לא מתוקנת בשקט."""
    admin = _local_client(server)
    calls, _token = _fake_pair_begin(monkeypatch, secondary_id="sn_0000000000000000",
                                     spki="ee" * 32)
    resp = admin.post("/api/console/storage-nodes/enroll", json={
        "url": "https://10.20.0.32:8443/api/interserver/v1",
        "label": "x", "code": "A", "expected_secondary_spki": "ee" * 32,
        "protocol_version": "2.1"})
    assert resp.status_code == 400
    assert calls == ["/pair-begin"]


@pytest.mark.parametrize("bad", ["../x", "sn_", "sn_ZZZZ", "", "sn_ab/cd", "x" * 40])
def test_enroll_node_rejects_malformed_node_id(server, bad):
    """שכבת ה-service: ``enroll_node`` דוחה מזהה שאינו ``^sn_[0-9a-f]{16}$``."""
    with pytest.raises(ValueError):
        storage_nodes.enroll_node(
            server["ctx"].conn, ("noc", "admin"), label="x",
            base_url="https://s:8443/api", node_id=bad, pinned_spki="aa" * 32,
            client_cert_ref="bb" * 32, credential_ref="/x", protocol_version="2.1")


def test_a_primary_with_secondaries_can_enroll_another_one(conn):
    """נדב 18/10 (לשונית "שרתי משנה" בהגדרות): `enroll_secondary` תלוי בתפקיד ולא במספר המשניים —
    ראשי שכבר יש לו משני (פעיל או מושבת) ממשיך להוסיף עוד; משני וממשתמש deploy — לא."""
    from server import storage_nodes
    admin = ("noc", "admin")
    assert storage_nodes.can_enroll_secondary(conn, "admin") is True        # הראשון: אין משני עדיין
    for i in range(2):
        storage_nodes.enroll_node(conn, admin, label=f"BRANCH-{i}", base_url=f"https://10.0.0.{i + 2}:8443",
                                  node_id=f"sn_{i:016x}", pinned_spki="aa" * 32, client_cert_ref="c",
                                  credential_ref="r", protocol_version="2.1")
    assert storage_nodes.enabled_node_count(conn) == 2
    assert storage_nodes.can_enroll_secondary(conn, "admin") is True        # ראשי עם משניים
    assert storage_nodes.can_enroll_secondary(conn, "deploy") is False
    conn.execute("UPDATE storage_nodes SET disabled_at = 'now'")
    conn.commit()
    assert storage_nodes.can_enroll_secondary(conn, "admin") is True        # גם כשכולם מושבתים
    db.set_setting(conn, storage_nodes.ROLE_KEY, storage_nodes.ROLE_SECONDARY)
    assert storage_nodes.can_enroll_secondary(conn, "admin") is False       # משני אינו מוסיף ילדים
