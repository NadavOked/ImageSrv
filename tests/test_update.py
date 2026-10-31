"""כפתור "עדכן" בהגדרות השרת (‏#748) — בדיקת-עדכון ואפליקציה מול הריפו
הציבורי. אף פקודת git/systemd-run אמיתית לא רצה כאן: ה-hooks
(‏describe/ls_remote_tags/run_upgrade) מוזרקים דרך
``update_hooks``, ומחליפים לגמרי את server.update.default_hooks().
"""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

pytest.importorskip("fastapi")

from server import update as update_mod

try:
    from fastapi.testclient import TestClient
except ImportError:                                   # pragma: no cover
    TestClient = None


# --- יחידה: פרסור סמנטי, בלי שרת ---------------------------------------------


def test_semver_key_accepts_only_vxyz():
    assert update_mod.semver_key("v0.24.0") == (0, 24, 0)
    assert update_mod.semver_key("v0.24.0-3-gabc123") is None
    assert update_mod.semver_key("0.24.0") is None


def test_parse_remote_tags_dedupes_annotated_peel_and_sorts():
    out = (
        "abc\trefs/tags/v0.9.0\n"
        "def\trefs/tags/v0.10.0\n"
        "def\trefs/tags/v0.10.0^{}\n"
        "xyz\trefs/heads/main\n"
        "qrs\trefs/tags/not-a-version\n"
    )
    assert update_mod.parse_remote_tags(out) == ["v0.9.0", "v0.10.0"]


def test_base_version_strips_describe_suffix():
    assert update_mod._base_version("v0.24.0-3-gabc123") == "v0.24.0"
    assert update_mod._base_version("v0.24.0") == "v0.24.0"
    assert update_mod._base_version(None) is None


def test_display_version_prefers_highest_verifying_tag_at_head_and_caches():
    verified = []
    hooks = {
        "head": lambda _repo: "abc",
        "tags_at_head": lambda _repo: ["v9.0.0", "v8.0.0"],
        "verify_installed_tag": lambda _repo, tag: verified.append(tag) or tag == "v8.0.0",
        "describe": lambda _repo: "v9.0.0",
    }
    assert update_mod.display_version(hooks, "/repo") == ("v8.0.0", True)
    assert update_mod.display_version(hooks, "/repo") == ("v8.0.0", True)
    assert verified == ["v9.0.0", "v8.0.0"]


def test_display_version_falls_back_to_unsigned_describe_result():
    hooks = {
        "head": lambda _repo: "abc",
        "tags_at_head": lambda _repo: ["v9.0.0"],
        "verify_installed_tag": lambda _repo, _tag: False,
        "describe": lambda _repo: "v9.0.0",
    }
    assert update_mod.display_version(hooks, "/repo") == ("v9.0.0", False)
    assert update_mod.current_version(hooks, "/repo") == "v9.0.0"


def test_display_version_without_tags_keeps_existing_error_result():
    def no_tag(_repo):
        raise update_mod.VersionError("no-tag", "none")

    hooks = {"head": lambda _repo: "abc", "tags_at_head": lambda _repo: [],
             "verify_installed_tag": lambda *_: False, "describe": no_tag}
    assert update_mod.display_version(hooks, "/repo") == (None, False)


# --- check_update: הראיה תמיד מוזרקת -----------------------------------------


def test_check_update_reports_available_when_remote_is_newer():
    seen = []
    hooks = {
        "describe": lambda repo_dir: "v0.24.0",
        "ls_remote_tags": lambda url: (True, "x\trefs/tags/v0.25.0\n", ""),
        "verify_tag": lambda url, tag, repo_dir: seen.append((url, tag, repo_dir))
        or {"state": "signed", "detail": "imagectl-release SHA256:abc"},
    }
    result = update_mod.check_update(hooks, "/repo", "https://example/pub.git")
    assert result == {"current": "v0.24.0", "latest": "v0.25.0",
                      "available": True, "reason": None,
                      "signature": {"state": "signed", "detail": "imagectl-release SHA256:abc"}}
    assert seen == [("https://example/pub.git", "v0.25.0", "/repo")]


# --- #1277: חתימת התג החדש — שלושה מצבים, ו"לא נבדק" אינו "חתום" ------------------


def _check_with(verify_tag):
    hooks = {"describe": lambda repo_dir: "v0.24.0",
             "ls_remote_tags": lambda url: (True, "x\trefs/tags/v0.25.0\n", ""),
             "verify_tag": verify_tag}
    return update_mod.check_update(hooks, "/repo", "https://example/pub.git")


@pytest.mark.parametrize("state", ["signed", "unsigned", "unchecked"])
def test_check_update_carries_each_signature_state_as_is(state):
    result = _check_with(lambda *_: {"state": state, "detail": "d"})
    assert result["signature"] == {"state": state, "detail": "d"}
    assert result["available"] is True          # החתימה אינה מסתירה את הגרסה — השער הוא הסקריפט


def test_a_verifier_that_raised_is_unchecked_not_signed():
    def boom(*_):
        raise RuntimeError("cannot run")
    signature = _check_with(boom)["signature"]
    assert signature["state"] == "unchecked" and "cannot run" in signature["detail"]


@pytest.mark.parametrize("answer", [None, {}, {"state": "ok"}, {"state": "SIGNED"}, "signed"])
def test_an_unknown_verifier_answer_is_unchecked(answer):
    assert _check_with(lambda *_: answer)["signature"]["state"] == "unchecked"


def test_no_signature_is_checked_when_there_is_nothing_to_install():
    def never(*_):
        raise AssertionError("verify_tag ran with no newer tag")
    hooks = {"describe": lambda repo_dir: "v0.25.0",
             "ls_remote_tags": lambda url: (True, "x\trefs/tags/v0.25.0\n", ""),
             "verify_tag": never}
    assert "signature" not in update_mod.check_update(hooks, "/repo", "https://example/pub.git")


def test_check_update_not_available_when_up_to_date():
    hooks = {
        "describe": lambda repo_dir: "v0.24.0",
        "ls_remote_tags": lambda url: (True, "x\trefs/tags/v0.24.0\n", ""),
    }
    result = update_mod.check_update(hooks, "/repo", "https://example/pub.git")
    assert result["available"] is False


def test_check_update_timeout_is_classified_as_no_network(monkeypatch):
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["git", "ls-remote"], 20)

    monkeypatch.setattr(subprocess, "run", timeout)
    hooks = {"describe": lambda repo_dir: "v0.24.0",
             "ls_remote_tags": update_mod._ls_remote_tags}
    result = update_mod.check_update(hooks, "/repo", update_mod.PUBLIC_UPDATE_URL)
    assert result["available"] is False
    assert result["latest"] is None
    assert result["reason"] == "no_network"
    assert result["message"] == update_mod.NO_NETWORK_MESSAGE
    assert "timed out after 20 seconds" in result["detail"]


def test_check_update_dns_failure_is_classified_as_no_network(monkeypatch):
    """‏עיקרון 5: הרצה שנכשלה אינה 'אין גרסאות' — שני מסלולים נבדלים."""
    def dns_failure(cmd, **_kwargs):
        return subprocess.CompletedProcess(cmd, 128, "", "fatal: unable to access: Could not resolve host: github.com")

    monkeypatch.setattr(subprocess, "run", dns_failure)
    hooks = {"describe": lambda repo_dir: "v0.24.0",
             "ls_remote_tags": update_mod._ls_remote_tags}
    result = update_mod.check_update(hooks, "/repo", update_mod.PUBLIC_UPDATE_URL)
    assert result["available"] is False
    assert result["latest"] is None
    assert result["reason"] == "no_network"
    assert result["message"] == update_mod.NO_NETWORK_MESSAGE
    assert "Could not resolve host" in result["detail"]


def test_check_update_no_tags_at_all_is_a_named_case():
    hooks = {
        "describe": lambda repo_dir: None,
        "ls_remote_tags": lambda url: (True, "", ""),
    }
    result = update_mod.check_update(hooks, "/repo", "https://example/pub.git")
    assert result["reason"] == "אין גרסאות (tags) בריפו הציבורי"


# --- שרת אמיתי (TestClient), הכול מוזרק --------------------------------------


@pytest.fixture()
def update_server(tmp_path: Path, images_root: Path, clock):
    if TestClient is None:
        pytest.skip("fastapi is required")
    from server.console import users
    from server.app import create_app

    state = {"current": "v0.24.0", "remote": "x\trefs/tags/v0.24.0\n",
             "run_ok": True}
    calls = {"fetch": 0, "checkout": 0, "run": 0, "run_args": None}

    hooks = {
        # ‏#1273: ``on_describe`` — וו לבדיקת המרוץ (עצירה בין השומר לכתיבת הסטטוס).
        "describe": lambda repo_dir: (state.get("on_describe") or (lambda: None))()
                                     or state["current"],
        "ls_remote_tags": lambda url: (True, state["remote"], ""),
        # Sentinels for the removed hooks: even if a caller supplies the old
        # names, applying must never run git inside the web process (#1193).
        "fetch_tags": lambda repo_dir: (calls.__setitem__("fetch", calls["fetch"] + 1)
                                        or (True, "")),
        "checkout": lambda repo_dir, tag: (calls.__setitem__("checkout", calls["checkout"] + 1)
                                           or (True, "")),
        # ‏#1277: בלי זה, ההוק האמיתי היה משכפל מהרשת בכל בדיקה שמוצאת גרסה.
        "verify_tag": lambda url, tag, repo_dir: {"state": "signed", "detail": "test"},
        "run_upgrade": lambda repo_dir, tag: (
            calls.__setitem__("run", calls["run"] + 1)
            or calls.__setitem__("run_args", (str(repo_dir), tag))
            or (state["run_ok"], state.get("run_err", ""))
        ),
    }
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     now_fn=clock, update_hooks=hooks, repo_dir="/repo")
    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    admin = TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    return {"admin": admin, "app": app, "state": state, "calls": calls}


def _enable(client):
    r = client.post("/api/console/settings", json={"update_enabled": True})
    assert r.status_code == 200


def test_me_reports_the_server_version_from_the_same_source_as_update(update_server):
    """‏#915: שורת הסטטוס בקונסולה מציגה את תג ה-git של העץ — מאותו hook
    ‏`describe` כמו `/update` — ולא `v0.9` קשיח ב-HTML. לכל משתמש מחובר,
    (הפצה — #1073 — לא נכנסת לקונסולה בכלל)."""
    from server.console import users
    admin, app, state = update_server["admin"], update_server["app"], update_server["state"]
    assert admin.get("/api/console/me").json()["version"] == "v0.24.0"
    assert admin.get("/api/console/me").json()["version_verified"] is False
    state["current"] = "v0.25.0-2-gabc123"          # העץ זז — נקרא מחדש, לא מונח
    assert admin.get("/api/console/me").json()["version"] == "v0.25.0-2-gabc123"
    users.create(app.state.ctx.conn, "dep", "deploy-pass-123", "deploy", by="test", check_policy=False)
    deploy = TestClient(app)
    # ‏#1073 (18/09): למשתמש הפצה אין קונסולה — הכניסה מסורבת, ואין שורת סטטוס.
    # (ב-`create_app` המאוחד של הטסטים הכניסה של הקיוסק קודמת — ולכן הבדיקה
    # היא על הנתיב: `console_only` חוסם גם עם עוגייה. בייצור — `create_console_app`.)
    deploy.post("/api/console/login", json={"username": "dep", "password": "deploy-pass-123"})
    assert deploy.get("/api/console/me").status_code == 403
    state["current"] = None                          # אין תג על העץ = null, לא מספר מומצא
    assert admin.get("/api/console/me").json()["version"] is None


def test_update_disabled_by_default(update_server):
    """סעיף 3 בהאצלה: המתג כבוי כברירת מחדל, בלי שנגענו בו."""
    settings = update_server["admin"].get("/api/console/settings").json()
    assert settings.get("update_enabled") in (None, "false")


def test_switch_off_blocks_check_and_apply_with_404(update_server):
    admin = update_server["admin"]
    assert admin.post("/api/console/update/check").status_code == 404
    assert admin.post("/api/console/update/apply",
                      json={"tag": "v0.25.0"}).status_code == 404


def test_info_shows_current_version_even_when_switch_is_off(update_server):
    info = update_server["admin"].get("/api/console/update").json()
    assert info["current"] == "v0.24.0"
    assert info["enabled"] is False
    assert "server_name" not in info


def test_check_returns_available_once_enabled(update_server):
    admin = update_server["admin"]
    _enable(admin)
    update_server["state"]["remote"] = "x\trefs/tags/v0.25.0\n"
    result = admin.post("/api/console/update/check").json()
    # ‏#1000: התשובה היא המסמך שנשמר — עם חותמת `at` של השרת.
    assert result.pop("at")
    assert result == {"current": "v0.24.0", "latest": "v0.25.0",
                      "available": True, "reason": None,
                      "signature": {"state": "signed", "detail": "test"}}


def test_apply_rejects_bad_tag_format(update_server):
    admin = update_server["admin"]
    _enable(admin)
    r = admin.post("/api/console/update/apply",
                   json={"tag": "not-a-tag"})
    assert r.status_code == 400
    assert update_server["calls"]["fetch"] == 0


def test_apply_does_not_require_a_confirm_name(update_server):
    admin = update_server["admin"]
    _enable(admin)
    r = admin.post("/api/console/update/apply", json={"tag": "v0.25.0"})
    assert r.status_code == 200
    assert update_server["calls"]["run"] == 1


def test_apply_blocked_409_when_a_round_is_open(update_server):
    admin = update_server["admin"]
    _enable(admin)
    conn = update_server["app"].state.ctx.conn
    image_id = admin.get("/api/console/images").json()[0]["id"]
    conn.execute("INSERT INTO groups (id, label, role) VALUES ('g', 'g', 'classroom')")
    conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients,"
        " wait_seconds, state, opened_by, created_at, last_join_at, kind)"
        " VALUES ('s1', 'g', ?, 'ROOM', 2, 300, 'running', 'nadav', ?, 0.0, 'multicast')",
        (image_id, "2026-09-16T00:00:00+00:00"))
    conn.commit()
    r = admin.post("/api/console/update/apply",
                   json={"tag": "v0.25.0"})
    assert r.status_code == 409
    assert update_server["calls"]["run"] == 0


def test_apply_never_runs_git_and_starts_script_with_tag_and_repo(update_server):
    admin = update_server["admin"]
    _enable(admin)
    r = admin.post("/api/console/update/apply",
                   json={"tag": "v0.25.0"})
    assert r.status_code == 200
    assert r.json()["started"] is True
    assert update_server["calls"]["fetch"] == 0
    assert update_server["calls"]["checkout"] == 0
    assert update_server["calls"]["run"] == 1
    assert update_server["calls"]["run_args"] == ("/repo", "v0.25.0")
    assert admin.get("/api/console/update").json()["previous"] == "v0.24.0"

    status = admin.get("/api/console/update/status").json()
    assert status["state"] == "running"
    assert status["tag"] == "v0.25.0"
    # השרת עדיין לא "עלה מחדש" בתוך הבדיקה — ``current`` נשאר הישן,
    # ולכן טרם אומת. זה בדיוק ההבדל בין 'הופעל' ל'אומת' (סעיף 5, #748).
    assert status["verified"] is False


def test_apply_verified_true_once_current_version_matches_the_target(update_server):
    admin = update_server["admin"]
    _enable(admin)
    admin.post("/api/console/update/apply",
              json={"tag": "v0.25.0"})
    # מדמה את מה שה-restart של תוכנית השדרוג עושה בפועל: השרת עולה
    # מחדש על העץ שכבר עבר checkout, ו-``describe`` קורא את התג החדש.
    update_server["state"]["current"] = "v0.25.0"
    status = admin.get("/api/console/update/status").json()
    assert status["verified"] is True
    assert status["state"] == "done"


def test_script_launch_failure_is_reported(update_server):
    admin = update_server["admin"]
    _enable(admin)
    update_server["state"]["run_ok"] = False
    admin.post("/api/console/update/apply",
              json={"tag": "v0.25.0"})
    assert update_server["calls"]["fetch"] == 0
    assert update_server["calls"]["checkout"] == 0
    assert update_server["calls"]["run"] == 1
    status = admin.get("/api/console/update/status").json()
    assert status["state"] == "failed"


def test_revert_without_a_previous_version_is_404(update_server):
    admin = update_server["admin"]
    _enable(admin)
    r = admin.post("/api/console/update/revert", json={})
    assert r.status_code == 404


def test_revert_uses_the_version_saved_before_the_last_apply(update_server):
    admin = update_server["admin"]
    _enable(admin)
    admin.post("/api/console/update/apply",
              json={"tag": "v0.25.0"})
    update_server["state"]["current"] = "v0.25.0"   # אחרי ה-restart המדומה
    r = admin.post("/api/console/update/revert", json={})
    assert r.status_code == 200, r.text
    assert r.json()["tag"] == "v0.24.0"
    # הסקריפט המנותק קיבל את הגרסה **הקודמת** — זה מה שמוריד גרסה בפועל.
    assert update_server["calls"]["run"] == 2
    assert update_server["calls"]["run_args"] == ("/repo", "v0.24.0")
    # החזרה הפיכה בעצמה: הגרסה שממנה חזרנו היא עכשיו "הקודמת" (#1192).
    assert admin.get("/api/console/update").json()["previous"] == "v0.25.0"
    status = admin.get("/api/console/update/status").json()
    assert status["state"] == "running" and status["tag"] == "v0.24.0"
    assert status["from"] == "v0.25.0"


def test_revert_needs_no_typed_name_and_ignores_a_stale_one(update_server):
    """‏#1778 (הכרעת #1270): "חזור ל-…" הוא חלון אישור עם כפתור אדום, בלי הקלדה —
    כמו "החל עדכון" (#1192), כי גם החזרה הפיכה. השרת אינו בודק `confirm_name`:
    קונסולה ישנה ששולחת שם שהתיישן (POST /hostname אחרי טעינת /me) אינה נדחית
    ב-403 "השם שהוקלד אינו תואם" על שם שאיש לא הקליד."""
    admin = update_server["admin"]
    _enable(admin)
    admin.post("/api/console/update/apply", json={"tag": "v0.25.0"})
    update_server["state"]["current"] = "v0.25.0"
    r = admin.post("/api/console/update/revert", json={"confirm_name": "not-this-server"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "started": True, "tag": "v0.24.0"}
    assert update_server["calls"]["run_args"] == ("/repo", "v0.24.0")


def test_revert_without_a_body_is_accepted(update_server):
    """הקונסולה שולחת `post("/update/revert")` — גוף ריק הוא `{}` (post() ב-console.js)."""
    admin = update_server["admin"]
    _enable(admin)
    admin.post("/api/console/update/apply", json={"tag": "v0.25.0"})
    update_server["state"]["current"] = "v0.25.0"
    r = admin.post("/api/console/update/revert")
    assert r.status_code == 200, r.text
    assert r.json()["tag"] == "v0.24.0"


def test_revert_keeps_its_guards_404_when_off_409_when_a_round_is_open(update_server):
    """הסרת ההקלדה אינה מסירה את השומרים: מתג כבוי = 404, סבב פתוח = 409, ואף
    אחד מהם אינו מגיע ל-systemd-run."""
    admin = update_server["admin"]
    _enable(admin)
    admin.post("/api/console/update/apply", json={"tag": "v0.25.0"})
    update_server["state"]["current"] = "v0.25.0"
    conn = update_server["app"].state.ctx.conn
    image_id = admin.get("/api/console/images").json()[0]["id"]
    conn.execute("INSERT INTO groups (id, label, role) VALUES ('g', 'g', 'classroom')")
    conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients,"
        " wait_seconds, state, opened_by, created_at, last_join_at, kind)"
        " VALUES ('s1', 'g', ?, 'ROOM', 2, 300, 'running', 'nadav', ?, 0.0, 'multicast')",
        (image_id, "2026-09-16T00:00:00+00:00"))
    conn.commit()
    assert admin.post("/api/console/update/revert", json={}).status_code == 409
    assert admin.post("/api/console/settings", json={"update_enabled": False}).status_code == 200
    assert admin.post("/api/console/update/revert", json={}).status_code == 404
    assert update_server["calls"]["run"] == 1


def test_deploy_user_cannot_reach_update_endpoints(update_server, images_root, clock, tmp_path):
    from server.console import users
    from server.app import create_app
    app = update_server["app"]
    users.create(app.state.ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    deploy = TestClient(app)
    deploy.post("/api/console/login",
               json={"username": "labtech", "password": "deploy-pass-1"})
    assert deploy.get("/api/console/update").status_code == 403
    assert deploy.post("/api/console/update/check").status_code == 403


def test_a_missing_git_binary_is_named_not_errno_2(monkeypatch):
    """‏#1185: השרת הראשון מה-ISO הציג "[Errno 2] No such file or directory:
    'git'". הכלי החסר נקרא בשמו, עם הכיוון לתיקון."""
    from server import update
    def boom(*a, **k):
        raise FileNotFoundError(2, "No such file or directory", "git")
    monkeypatch.setattr(update.subprocess, "run", boom)
    ok, out, err = update._run(["git", "describe", "--tags"], timeout=5)
    assert not ok and out == ""
    assert "git אינו מותקן" in err and "#1185" in err
    assert "Errno" not in err


# --- ‏#1273: לחיצה כפולה על "החל" ---------------------------------------------

#: מה ש-systemd-run מדפיס כשהיחידה הקודמת עוד חיה (systemd 257, דביאן 13; וגם
#: הנוסח הישן "already exists").
UNIT_EXISTS = ("Failed to start transient service unit: Unit imagesrv-upgrade.service "
               "was already loaded or has a fragment file.")


def test_second_apply_while_running_is_409_and_the_first_stays_running(update_server):
    """הלחיצה השנייה נדחית לפני שמגיעה ל-systemd-run, וה-running של הראשונה
    אינו נדרס ב-failed."""
    admin = update_server["admin"]
    _enable(admin)
    assert admin.post("/api/console/update/apply", json={"tag": "v0.25.0"}).status_code == 200
    second = admin.post("/api/console/update/apply", json={"tag": "v0.25.0"})
    assert second.status_code == 409, second.text
    assert "v0.25.0" in second.json()["detail"]
    assert update_server["calls"]["run"] == 1
    status = admin.get("/api/console/update/status").json()
    assert status["state"] == "running" and status["tag"] == "v0.25.0"
    assert "error" not in status


def test_revert_while_an_update_is_running_is_409(update_server):
    admin = update_server["admin"]
    _enable(admin)
    admin.post("/api/console/update/apply", json={"tag": "v0.25.0"})
    r = admin.post("/api/console/update/revert", json={})
    assert r.status_code == 409, r.text
    assert update_server["calls"]["run"] == 1
    assert admin.get("/api/console/update/status").json()["state"] == "running"


def test_apply_after_a_verified_update_is_allowed_again(update_server):
    """‏running שאומת (השרת עלה על התג) הוא done — לא חוסם את העדכון הבא."""
    admin = update_server["admin"]
    _enable(admin)
    admin.post("/api/console/update/apply", json={"tag": "v0.25.0"})
    update_server["state"]["current"] = "v0.25.0"
    r = admin.post("/api/console/update/apply", json={"tag": "v0.26.0"})
    assert r.status_code == 200, r.text
    assert update_server["calls"]["run"] == 2


def test_unit_already_exists_is_409_and_never_written_as_failed(update_server):
    """יחידת שדרוג קיימת = עדכון רץ, לא עדכון שנכשל (עיקרון 5): הסטטוס חוזר
    למה שהיה, ולא נכתב failed."""
    admin = update_server["admin"]
    _enable(admin)
    update_server["state"]["run_ok"] = False
    update_server["state"]["run_err"] = UNIT_EXISTS
    r = admin.post("/api/console/update/apply", json={"tag": "v0.25.0"})
    assert r.status_code == 409, r.text
    assert update_server["calls"]["run"] == 1
    assert admin.get("/api/console/update/status").json()["state"] != "failed"


def test_any_other_launch_failure_is_still_failed(update_server):
    admin = update_server["admin"]
    _enable(admin)
    update_server["state"]["run_ok"] = False
    update_server["state"]["run_err"] = "Failed to connect to bus: No such file or directory"
    admin.post("/api/console/update/apply", json={"tag": "v0.25.0"})
    status = admin.get("/api/console/update/status").json()
    assert status["state"] == "failed" and "Failed to connect to bus" in status["error"]


def test_a_launch_failure_answers_with_the_real_error_not_the_409_of_a_running_unit(update_server):
    """‏#2160: כשל הפעלה שאינו "היחידה כבר רצה" נכתב failed — והתשובה לבקשה
    אמרה בכל זאת 409 "יחידת השדרוג כבר רצה" (``return`` בלי ערך = None = "לא
    התחיל"). המפעיל קיבל הסבר הפוך מהאמת, באותה בקשה שבה הסטטוס כבר היה failed."""
    admin = update_server["admin"]
    _enable(admin)
    update_server["state"]["run_ok"] = False
    update_server["state"]["run_err"] = "Failed to connect to bus: No such file or directory"
    r = admin.post("/api/console/update/apply", json={"tag": "v0.25.0"})
    assert r.status_code == 500, r.text
    assert "Failed to connect to bus" in r.json()["detail"]
    assert "כבר רצה" not in r.json()["detail"]
    assert admin.get("/api/console/update/status").json()["state"] == "failed"


def test_two_simultaneous_applies_start_the_script_once(update_server):
    """שתי בקשות שמגיעות יחד (thread pool): השומר והכתיבה של running אטומיים —
    השנייה ממתינה ומקבלת 409, ולא עוברת את השומר לפני שהראשונה כתבה."""
    import threading
    admin = update_server["admin"]
    _enable(admin)
    state = update_server["state"]
    entered, release, calls = threading.Event(), threading.Event(), []

    def on_describe():
        calls.append(1)
        # קריאת הגרסה הראשונה של הבקשה הראשונה — תמיד לפני כתיבת running:
        # בתוך השומר (עם התיקון), או ישר לפני הכתיבה (בלי שומר בכלל)
        if len(calls) == 1:
            entered.set()
            release.wait(10)
    state["on_describe"] = on_describe
    results = {}

    def go(key):
        results[key] = admin.post("/api/console/update/apply", json={"tag": "v0.25.0"}).status_code
    first = threading.Thread(target=go, args=("first",))
    first.start()
    assert entered.wait(10), "the first apply never reached the version read"
    second = threading.Thread(target=go, args=("second",))
    second.start()
    second.join(1.0)          # בלי נעילה השנייה כבר סיימה כאן
    release.set()
    first.join(10)
    second.join(10)
    assert results == {"first": 200, "second": 409}, results
    assert update_server["calls"]["run"] == 1
