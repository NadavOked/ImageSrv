"""‏#1159: ‏`/api/console/health/live` אחרי התקנה מה-ISO.

השורש המקורי (19/09): ה-ISO ארז את העץ ב-`git archive`, בלי `.git`, ו-`git
describe` החזיר כלום → ‏`version: null`. ‏#1185 (‏3b0052f3) אורז מאז את ה-`.git`
של clone רדוד מהציבורי **בתג** (`git clone --branch <tag> --depth 1`), ושני
ה-`cp -a` בדרך (המתקין ל-/opt/imagesrv-src, ‏setup-boot-server.sh ל-/opt/imagesrv)
מעתיקים אותו כמו שהוא. הטסטים כאן מריצים **git אמיתי** על אותה צורה בדיוק —
ה-hooks של השרת אינם מוזרקים — כדי שהראיה תהיה הגרסה שנקראה, לא mock.

והחצי השני של ה-Issue: גרסה שלא נקראה אינה `null` שקט. היא `null` עם
‏`version_error` בשם, ושורת אזהרה ביומן (עיקרון 5).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from server import update as update_mod  # noqa: E402
from server.app import create_app  # noqa: E402

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
       "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"]


def _git(*args: str, cwd: Path) -> str:
    done = subprocess.run([*GIT, *args], cwd=str(cwd), capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, check=False, timeout=30)
    assert done.returncode == 0, f"git {' '.join(args)}: {done.stderr}"
    return done.stdout


@pytest.fixture
def no_parent_repo(tmp_path, monkeypatch):
    """‏git מטפס למעלה עד שהוא מוצא `.git`; התקרה כאן מבטיחה ש"לא ריפו"
    הוא באמת לא ריפו, גם אם tmp יושב בתוך עץ git כלשהו."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    return tmp_path


def _public_repo(root: Path) -> Path:
    """ריפו "ציבורי": תג מוער, אחריו תג קל, ואחריו קומיט בלי תג — כדי
    שה-clone בתג יוכיח שהוא מקבל את התג שלו ולא את הראש."""
    src = root / "public"
    src.mkdir()
    _git("init", "-q", cwd=src)
    (src / "VERSION_MARK").write_text("a\n", encoding="utf-8")
    _git("add", ".", cwd=src)
    _git("commit", "-q", "-m", "a", cwd=src)
    _git("tag", "-a", "v0.53.0", "-m", "release", cwd=src)
    _git("commit", "-q", "--allow-empty", "-m", "b", cwd=src)
    _git("tag", "v0.53.1", cwd=src)
    _git("commit", "-q", "--allow-empty", "-m", "c", cwd=src)
    return src


def _iso_install(root: Path, src: Path, tag: str) -> Path:
    """אותה שרשרת כמו ה-ISO: ‏build-iso.sh (clone רדוד בתג) → ‏bootstrap.sh
    (‏`cp -a` ל-/opt/imagesrv-src) → ‏setup-boot-server.sh (‏`cp -a` ל-/opt/imagesrv)."""
    clone = root / "public-src"
    _git("clone", "-q", "--branch", tag, "--depth", "1", src.resolve().as_uri(), str(clone), cwd=root)
    assert (clone / ".git" / "shallow").is_file(), "ה-clone אינו רדוד — הטסט אינו מודד את מה שה-ISO עושה"
    staged = root / "opt" / "imagesrv-src"
    shutil.copytree(clone, staged, symlinks=True)
    app_dir = root / "opt" / "imagesrv"
    shutil.copytree(staged, app_dir, symlinks=True)
    return app_dir


@pytest.mark.parametrize("tag", ["v0.53.0", "v0.53.1"], ids=["annotated", "lightweight"])
def test_health_live_reads_the_tag_from_a_shallow_clone_at_that_tag(
        no_parent_repo, images_root, clock, tag):
    app_dir = _iso_install(no_parent_repo, _public_repo(no_parent_repo), tag)
    app = create_app(no_parent_repo / "data", images_root, "http://10.44.12.10:8080",
                     now_fn=clock, repo_dir=app_dir)            # בלי update_hooks: git אמיתי
    r = TestClient(app).get("/api/console/health/live")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "version": tag, "version_error": None}


def test_a_tree_without_git_is_a_fixed_category_unauthenticated_and_the_detail_goes_to_log_and_admin(
        no_parent_repo, images_root, clock, caplog):
    """‏ה-ISO שלפני #1185 (או ‏`build-iso --source`): עץ בלי `.git`.
    ‏`/health/live` אינו דורש הזדהות — הוא מקבל קטגוריה קבועה בלבד, בלי
    נתיב ובלי stderr; הסיבה המלאה ביומן וב-`GET /update` של admin."""
    from server.console import users
    bare_tree = no_parent_repo / "opt" / "imagesrv"
    (bare_tree / "server").mkdir(parents=True)
    app = create_app(no_parent_repo / "data", images_root, "http://10.44.12.10:8080",
                     now_fn=clock, repo_dir=bare_tree)
    anon = TestClient(app)
    with caplog.at_level(logging.WARNING, logger="imagesrv.update"):
        r = anon.get("/api/console/health/live")
    body = r.json()
    assert body == {"ok": True, "version": None, "version_error": "not-a-git-tree"}
    assert "/" not in body["version_error"] and "\\" not in body["version_error"]
    for leak in ("not a git repository", "imagesrv", "git describe"):
        assert leak not in r.text.lower(), f"stderr/נתיב דלף ל-/health/live ללא הזדהות: {leak}"
    logged = [rec.getMessage() for rec in caplog.records if "version: not read" in rec.getMessage()]
    assert logged and "not a git repository" in logged[0].lower(), \
        "הסיבה המלאה חייבת להגיע ליומן — לא null שקט"

    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test",
                 is_builtin=True, check_policy=False)
    admin = TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    info = admin.get("/api/console/update").json()
    assert info["current"] is None
    assert "not a git repository" in info["current_error"].lower(), info["current_error"]
    assert anon.get("/api/console/update").status_code == 401     # הסיבה המלאה — admin בלבד


@pytest.mark.parametrize("stderr, category", [
    ("fatal: not a git repository (or any of the parent directories): .git", "not-a-git-tree"),
    ("fatal: detected dubious ownership in repository at '/opt/imagesrv'", "dubious-ownership"),
    ("fatal: No names found, cannot describe anything.", "no-tag"),
    ("fatal: No tags can describe 'abc123'.", "no-tag"),
    ("git אינו מותקן בשרת הזה — הותקן מ-ISO ישן (#1185)? התקנה מחדש מ-ISO עדכני", "git-missing"),
    ("Command '['git']' timed out after 10 seconds", "git-failed"),
    ("", "git-failed"),
])
def test_every_describe_failure_maps_to_a_fixed_category(monkeypatch, stderr, category):
    monkeypatch.setattr(update_mod, "_run", lambda *_a, **_k: (False, "", stderr))
    got = update_mod.read_version(update_mod.default_hooks(), "/opt/imagesrv")
    assert got.version is None and got.error == category
    assert got.error in update_mod.VERSION_ERRORS
    assert "/opt/imagesrv" in got.detail                              # המלא — ליומן/admin


def test_read_version_never_returns_null_without_a_category():
    """‏hook שמחזיר ריק, ו-hook שזורק חריגה זרה: שניהם גרסה `None` עם
    קטגוריה קבועה; גרסה שנקראה היא בלי שגיאה."""
    assert update_mod.read_version({"describe": lambda _d: "v0.53.0"}, "/r") == ("v0.53.0", None, None)

    empty = update_mod.read_version({"describe": lambda _d: None}, "/r")
    assert empty.version is None and empty.error == "no-tag" and empty.detail

    def boom(_d):
        raise OSError("unexpected at /r")
    raised = update_mod.read_version({"describe": boom}, "/r")
    assert raised == (None, "git-failed", "unexpected at /r")


def test_current_version_still_folds_a_failure_to_none(no_parent_repo):
    """‏`/me`, כפתור העדכון והגיבוי ממשיכים לקבל `None` — ‏`_describe` שזורק
    אינו מפיל אותם."""
    assert update_mod.current_version(update_mod.default_hooks(), no_parent_repo) is None
