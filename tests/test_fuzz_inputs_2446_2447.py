"""‏#2446 ו-#2447 — ממצאי fuzz מ-04/11 (‏`lab/security-scan-1104/fable2/fuzz`).

* **#2446** — ‏`console/login.py:finish_login` החזיר 500 **לפני** האימות: גוף פגום
  (`{`, ‏`\\xff\\xfe`, ריק), גוף שאינו אובייקט, ושם/סיסמה שאינם מחרוזת. הנתיב חשוף
  גם בפורט הסוכן (הקיוסק), וכך גם `/api/v1/agent/login` עם שם שאינו מחרוזת.
  עכשיו ‏400 — בלי לגעת ב-`login_guard` ובלי לשנות כניסה תקינה.
* **#2447** — ‏`images.py`: ‏`_unsafe_agent_string` רקורסיבית בלי תקרה (‏description
  מקונן ~3000 — RecursionError); ‏role/fs מטיפוס list/dict — TypeError על `in set`;
  ו-`_scan_root` עטף רק את `json.loads`, כך שמניפסט אחד הפיל את סריקת הספרייה
  כולה. עכשיו `_validate` מחזיר סיבה, והסריקה מדלגת על הפגום בגלוי (עיקרון 5).

בקרה שלילית: עם `server/console/login.py`, ‏`server/agent/api.py`,
‏`server/library/images.py`, ‏`server/library/capture.py` ו-`server/rounds/direct.py`
של הקומיט הקודם — כל הטסטים כאן חוץ מהבקרות נופלים.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from conftest import MANIFEST_256, write_image
from server.library.images import ImageLibrary

GOOD = {"username": "noc", "password": "admin-pass-123"}


def _lenient(server) -> TestClient:
    return TestClient(server["app"], raise_server_exceptions=False)


def _failures(server) -> int:
    return server["ctx"].conn.execute(
        "SELECT COUNT(*) AS n FROM journal WHERE event IN ('login_failed', 'agent_login_failed')"
    ).fetchone()["n"]


# --- #2446 -------------------------------------------------------------------


@pytest.mark.parametrize("raw", [b"{", b"\xff\xfe", b"", b"[]", b'"x"', b"[" * 5000])
def test_a_broken_console_login_body_is_400(server, raw):
    response = _lenient(server).post("/api/console/login", content=raw,
                                     headers={"Content-Type": "application/json"})
    assert response.status_code == 400, (raw[:10], response.status_code, response.text[:200])
    assert _failures(server) == 0                       # לא הגיע לבדיקת הסיסמה


@pytest.mark.parametrize("field,value", [("username", {"k": "v"}), ("username", 7),
                                         ("username", ["noc"]), ("password", 5),
                                         ("password", {"k": "v"})])
def test_a_non_string_credential_is_400(server, field, value):
    response = _lenient(server).post("/api/console/login", json={**GOOD, field: value})
    assert response.status_code == 400, response.text
    assert _failures(server) == 0


@pytest.mark.parametrize("field,value", [("username", {"k": "v"}), ("password", 5)])
def test_the_agent_login_with_a_non_string_credential_is_400(server, field, value):
    body = {"mac": "b4:2e:99:07:1a:c4", "username": "labtech", "password": "x", field: value}
    response = _lenient(server).post("/api/v1/agent/login", json=body)
    assert response.status_code == 400 and response.json()["code"] == "bad_login", response.text
    assert _failures(server) == 0


def test_a_good_console_login_still_works(server):
    """בקרה: כניסה תקינה, וסיסמה שגויה עדיין 401 שנספר ב-`login_guard`."""
    client = TestClient(server["app"])
    assert client.post("/api/console/login", json=GOOD).status_code == 200
    assert client.post("/api/console/login", json={**GOOD, "password": "nope"}).status_code == 401
    assert _failures(server) == 1


# --- #2447 -------------------------------------------------------------------


def _deep(depth: int) -> object:
    value: object = "x"
    for _ in range(depth):
        value = [value]
    return value


def test_a_description_nested_3000_deep_is_a_reason_not_a_recursion_error():
    manifest = {**copy.deepcopy(MANIFEST_256), "description": _deep(3000)}
    problem = ImageLibrary._validate(manifest)
    assert isinstance(problem, str) and "nests deeper" in problem


@pytest.mark.parametrize("field", ["role", "fs"])
@pytest.mark.parametrize("value", [["windows"], {"k": "v"}])
def test_a_role_or_fs_that_is_not_a_string_is_a_reason(field, value):
    manifest = copy.deepcopy(MANIFEST_256)
    manifest["partitions"][0][field] = value
    problem = ImageLibrary._validate(manifest)
    assert isinstance(problem, str) and "role or filesystem" in problem


def test_the_real_manifest_still_validates():
    """בקרה: תקרת העומק אינה פוסלת מניפסט אמיתי."""
    assert ImageLibrary._validate(copy.deepcopy(MANIFEST_256)) is None


def test_one_broken_manifest_does_not_take_the_library_down(tmp_path: Path):
    """שני מניפסטים פגומים לצד אחד תקין: עומק שמפיל את הבודק (600, ‏json נקרא)
    ועומק שמפיל את `json.loads` עצמו (5000). הסריקה מחזירה את התקין, ומדווחת
    על השניים (`_report_invalid`) — לא קורסת ולא מסתירה."""
    root = tmp_path / "images"
    write_image(root, MANIFEST_256)
    for name, depth in (("img_aaaaaa", 600), ("img_bbbbbb", 5000)):
        folder = root / name
        folder.mkdir(parents=True)
        text = json.dumps({**copy.deepcopy(MANIFEST_256), "id": name, "description": "@"})
        text = text.replace('"@"', "[" * depth + '"x"' + "]" * depth)
        (folder / "manifest.json").write_text(text, encoding="utf-8")
    library = ImageLibrary(root)
    reported: list[str] = []
    library._invalid_reporter = lambda path, reason: reported.append(path.parent.name)
    assert set(library.scan()) == {MANIFEST_256["id"]}
    assert sorted(reported) == ["img_aaaaaa", "img_bbbbbb"]
