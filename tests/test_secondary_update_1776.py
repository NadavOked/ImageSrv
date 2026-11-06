"""‏#1776: עדכון שרת המשנה מהקונסולה של הראשי (נדב 19/10: "אני מתל אביב רוצה לעדכן את חיפה").

אותם שני שרתים אמיתיים של ``test_secondary_console_writes`` — ראשי ומשני על loopback, ערוץ
mTLS מאומת. ה-hooks של העדכון מזויפים בשני הצדדים (git/systemd-run אינם נוגעים במכונה);
כל שרת כותב את הסטטוס ל-DB **שלו**, ולכן "איפה זה רץ" נקרא משם.

מוכח: (א) ‏``POST /update/check`` ו-``POST /update/apply`` עוברים בערוץ ורצים **במשני**, בשם
``primary:<user>``, ולא בראשי; (ב) רשומת יומן בשני השרתים; (ג) סירוב החתימה של המשני
(``reason: signature``) מגיע לראשי ב-``GET /update/status``; (ד) סירובים של המשני עצמו
(כבוי, תג לא תקין) עוברים עם ה-detail; (ה) deploy → 403 והמשני לא נשאל; (ו) "חזור לגרסה"
נשאר מחוץ לרשימה; (ז) הבדיקה האיטית מטבעה מקבלת תקרה משלה ולא "לא ידוע אם בוצעה" אחרי 15 ש'.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("OpenSSL", reason="pyOpenSSL נדרש ל-mTLS הבין-שרתי (#740)")

from server import db
from server.interserver import api as interserver_api, auth as interserver_auth, storage_client
from server import update as update_mod
from test_secondary_console_relay import secondary                       # noqa: F401
from test_storage_transfer import paired                                  # noqa: F401

pytestmark = pytest.mark.skipif(
    not interserver_auth.can_export_keying_material(),
    reason="אין יכולת tls-exporter (pyOpenSSL) — channel-binding לא ניתן לאימות",
)

TAG = "v0.72.0"


@pytest.fixture(autouse=True)
def upgrades(monkeypatch):
    """hooks מזויפים לכל ``create_update_router`` שנבנה בטסט (גם הראשי וגם המשני)."""
    calls: list[str] = []
    hooks = {"describe": lambda repo_dir: "v0.71.0",
             "ls_remote_tags": lambda url: (True, f"abc\trefs/tags/{TAG}\n", ""),
             "verify_tag": lambda url, tag, repo_dir: {"state": "signed", "detail": "release SHA256:x"},
             "run_upgrade": lambda repo_dir, tag: calls.append(tag) or (True, "")}
    monkeypatch.setattr(update_mod, "default_hooks", lambda: dict(hooks))
    return calls


def _url(nid, path):
    return f"/api/console/storage-nodes/{nid}/console{path}"


def _journal(conn, event):
    return [(r["user"], r["detail"]) for r in conn.execute(
        "SELECT user, detail FROM journal WHERE event = ? ORDER BY id", (event,))]


def _enable(conn):
    db.set_setting(conn, update_mod.ENABLED_KEY, "true")


def test_apply_runs_on_the_secondary_in_its_own_path_and_is_journaled_on_both(
        server, secondary, paired, upgrades):
    _enable(secondary["conn"])
    resp = server["admin"].post(_url(paired, "/update/apply"), json={"tag": TAG})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "started": True, "tag": TAG}
    assert upgrades == [TAG], "השדרוג שוגר פעם אחת"
    sec = update_mod.get_status(secondary["conn"])
    assert sec["state"] == "running" and sec["tag"] == TAG and sec["by"] == "primary:noc"
    assert update_mod.get_status(server["ctx"].conn) is None, "הראשי לא התחיל עדכון של עצמו"
    assert _journal(secondary["conn"], "update_apply_started") == [("primary:noc", TAG)]
    assert _journal(secondary["conn"], "peer_write")[-1] == ("primary:noc", "POST /update/apply → 200")
    primary = _journal(server["ctx"].conn, "peer_write")
    assert primary[-1][0] == "noc" and "HAIFA" in primary[-1][1]
    assert "POST /update/apply → 200" in primary[-1][1]
    assert _journal(server["ctx"].conn, "update_apply_started") == []
    # ההתקדמות נקראת מהמשני, דרך אותו ערוץ
    status = server["admin"].get(_url(paired, "/update/status")).json()
    assert status["state"] == "running" and status["tag"] == TAG and status["by"] == "primary:noc"


def test_check_shows_the_secondarys_available_version_and_signature(server, secondary, paired):
    _enable(secondary["conn"])
    resp = server["admin"].post(_url(paired, "/update/check"), json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["current"] == "v0.71.0" and body["latest"] == TAG and body["available"] is True
    assert body["signature"]["state"] == "signed"
    assert update_mod.get_last_check(secondary["conn"])["latest"] == TAG
    assert update_mod.get_last_check(server["ctx"].conn) is None
    assert _journal(secondary["conn"], "update_check") == [
        ("primary:noc", f"current=v0.71.0 latest={TAG}")]
    info = server["admin"].get(_url(paired, "/update")).json()
    assert info["current"] == "v0.71.0" and info["last_check"]["latest"] == TAG


def test_a_signature_refusal_of_the_secondary_reaches_the_primary(server, secondary, paired):
    """מה ש-``server-upgrade.sh`` של המשני כותב כשהתג אינו חתום — כפי שהוא, בראשי."""
    _enable(secondary["conn"])
    assert server["admin"].post(_url(paired, "/update/apply"), json={"tag": TAG}).status_code == 200
    why = f"התג {TAG} לא אומת כחתום: no principal matched"
    update_mod._set_status(secondary["conn"], {
        "state": "failed", "tag": TAG, "stage": "verify", "reason": "signature",
        "detail": why, "error": why, "from": "v0.71.0", "by": "primary:noc"})
    status = server["admin"].get(_url(paired, "/update/status")).json()
    assert status["state"] == "failed" and status["reason"] == "signature"
    assert status["stage"] == "verify" and "לא אומת כחתום" in status["error"]


@pytest.mark.parametrize(("setup", "body", "code", "detail"), [
    (lambda conn: None, {"tag": TAG}, 404, "עדכון כבוי"),
    (_enable, {"tag": "latest"}, 400, "תג לא תקין"),
])
def test_a_refusal_of_the_secondary_reaches_the_page_with_its_detail(
        server, secondary, paired, upgrades, setup, body, code, detail):
    setup(secondary["conn"])
    resp = server["admin"].post(_url(paired, "/update/apply"), json=body)
    assert resp.status_code == code and detail in resp.json()["detail"]
    assert upgrades == []
    assert _journal(secondary["conn"], "peer_write")[-1] == ("primary:noc", f"POST /update/apply → {code}")


@pytest.mark.parametrize("path", ["/update/apply", "/update/check"])
def test_deploy_is_refused_and_the_secondary_is_not_asked(server, secondary, paired, upgrades, path):
    _enable(secondary["conn"])
    assert server["deploy"].post(_url(paired, path), json={"tag": TAG}).status_code == 403
    assert upgrades == []
    assert _journal(secondary["conn"], "peer_write") == []
    assert update_mod.get_status(secondary["conn"]) is None


@pytest.mark.parametrize(("method", "path", "allowed"), [
    ("POST", "/update/apply", True), ("POST", "/update/check", True),
    ("POST", "/update/revert", False), ("GET", "/update/apply", False),
    ("PUT", "/update/apply", False), ("POST", "/update/apply/x", False),
    ("POST", "/update", False), ("POST", "/update/backup", False),
])
def test_the_update_write_allowlist(method, path, allowed):
    assert interserver_api.console_write_allowed(method, path) is allowed


def test_revert_is_not_relayed(server, secondary, paired, upgrades):
    _enable(secondary["conn"])
    assert server["admin"].post(_url(paired, "/update/revert"), json={}).status_code == 404
    assert upgrades == [] and _journal(secondary["conn"], "peer_write") == []


@pytest.mark.parametrize(("path", "expected"), [
    ("/update/check", 180.0), ("/update/apply", 60.0),
    ("/settings", storage_client.REQUEST_TIMEOUT),
])
def test_the_relay_waits_for_the_slow_update_calls(server, secondary, paired, monkeypatch,
                                                    path, expected):
    _enable(secondary["conn"])
    seen = []
    original = storage_client.PinnedMTLSClient.send_answer

    def spy(self, *a, **k):
        seen.append(self.request_timeout)
        return original(self, *a, **k)
    monkeypatch.setattr(storage_client.PinnedMTLSClient, "send_answer", spy)
    body = {"tag": TAG} if path == "/update/apply" else {"session_wait_seconds": "420"}
    assert server["admin"].post(_url(paired, path), json=body).status_code == 200
    assert seen == [expected]


# --- ‏#1776, סעיף 2 במיון 06/11: משני ישן מדי — ההודעה אומרת מה לעשות ----------
#
# ‏20/10 על ברזל: ת"א (עם #1776) מול חיפה v0.75.0 קיבל "502 חיפה: הערוץ סירב (404):
# הפעולה אינה מורשית בערוץ הבין-שרתי". רשימת ההיתרים יושבת **במשני**, וגרסה ישנה
# אינה מכירה את /update/check. הראשי כבר בדק את הרשימה **שלו** לפני השליחה, ולכן
# 404 מהערוץ אומר שהמשני ישן ממנו. "משני ישן" מדומה כאן בסירוב של ``console_write``
# במשני — בדיוק מה ש-v0.75.0 מחזיר.

OLD_DETAIL = "הפעולה אינה מורשית בערוץ הבין-שרתי"     # המחרוזת של v0.75.0, כפי שנמדדה


def _old_secondary(monkeypatch, status=404, detail=OLD_DETAIL):
    def refuse(*_a, **_k):
        raise interserver_api.PairError(status, detail)
    monkeypatch.setattr(interserver_api, "console_write", refuse)


@pytest.mark.parametrize("path", ["/update/check", "/update/apply"])
def test_an_older_secondary_says_update_it_once_from_its_own_console(
        server, secondary, paired, upgrades, monkeypatch, path):
    _enable(secondary["conn"])
    _old_secondary(monkeypatch)
    resp = server["admin"].post(_url(paired, path), json={"tag": TAG})
    assert resp.status_code == 502, resp.text
    detail = resp.json()["detail"]
    assert "HAIFA" in detail, detail
    assert "שרת המשנה בגרסה ישנה מדי לעדכון מרחוק" in detail, detail
    assert "עדכנו אותו פעם אחת מהקונסולה שלו" in detail, detail
    assert OLD_DETAIL in detail, "התשובה של המשני נשארת כראיה (5א)"
    assert upgrades == []
    entry = _journal(server["ctx"].conn, "peer_write")[-1]
    assert entry[0] == "noc" and "ישנה מדי" in entry[1], entry
    node = server["ctx"].conn.execute(
        "SELECT last_error FROM storage_nodes WHERE id = ?", (paired,)).fetchone()
    assert "ישנה מדי" in node["last_error"]


def test_an_older_secondary_on_another_write_names_the_operation(
        server, secondary, paired, monkeypatch):
    _old_secondary(monkeypatch)
    resp = server["admin"].post(_url(paired, "/timezone"), json={"timezone": "Asia/Jerusalem"})
    assert resp.status_code == 502, resp.text
    detail = resp.json()["detail"]
    assert "בגרסה ישנה מדי לפעולה הזו מרחוק (POST /timezone)" in detail, detail
    assert "עדכנו אותו פעם אחת מהקונסולה שלו" in detail, detail


def test_a_channel_refusal_that_is_not_404_is_not_called_an_old_version(
        server, secondary, paired, monkeypatch):
    """רק 404 מהערוץ הוא "לא מכיר את הפעולה". ‏401 (טוקן) נשאר "הערוץ סירב"."""
    _enable(secondary["conn"])
    _old_secondary(monkeypatch, status=401, detail="טוקן שגוי")
    resp = server["admin"].post(_url(paired, "/update/check"), json={})
    assert resp.status_code == 502, resp.text
    detail = resp.json()["detail"]
    assert "הערוץ סירב (401): טוקן שגוי" in detail and "ישנה מדי" not in detail, detail


def test_the_secondary_itself_says_its_allowlist_may_be_older_than_the_primary(monkeypatch):
    """הצד של המשני: מי שקורא את הסירוב במשני (יומן, ‏curl) רואה את הסיבה הסבירה
    ואת התיקון — לא רק "אינה מורשית"."""
    status, _headers, body = interserver_api.run_console_write(
        None, "POST", "/update/revert", "", "primary:noc", b"")
    assert status == 404
    assert "עדכנו את שרת המשנה פעם אחת מהקונסולה שלו" in body["detail"], body
    monkeypatch.setattr(interserver_api, "authenticate_parent", lambda *_a, **_k: None)
    with pytest.raises(interserver_api.PairError) as err:
        interserver_api.console_write(
            SimpleNamespace(conn=None), object(), None, method="POST", path="/update/revert", query="",
            acting_user="noc", body=b"", token="t", protocol_version="2.1")
    assert err.value.status == 404
    assert "עדכנו את שרת המשנה פעם אחת מהקונסולה שלו" in err.value.detail
