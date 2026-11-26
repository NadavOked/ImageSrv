"""#3695 (אפיק #3653): מחיקת אזור הביניים של קליטה שקטה היא אוטומטית (הכרעת נדב 18/11, אפשרות 2), וכשל במחיקה אינו נבלע.

- **נשאר כמו היום:** קליטה ששקטה נכשלת והאזור שלה נמחק, בלי שאלה.
- **מה שהשתנה:** `ignore_errors=True` יצא. כשהמחיקה נכשלת, המשימה עדיין `failed` (המכונה שקטה, וההחלטה היא של המוח), אבל נרשם אירוע
  `capture_staging_delete_failed` עם המשימה, התיקייה והשגיאה, והמכונה על השורה. "לא הצלחנו למחוק" אינו "נמחק" (עיקרון 5).
- **אזור שאינו קיים אינו כשל:** קליטה שלא עלה בה בייט אין מה למחוק, ואין אירוע.
- **ההכרעה רשומה:** `CG-S30` בקטלוג (קבוצה ג), עם הנימוק, ומצביעה על הקוד.

**בקרה שלילית:** `server/system/silent_tasks.py` ו-`server/console/journal_he.py` חוזרים לבסיס (`f6a63b169`): `_drop_staging` נעלם, ו-`ignore_errors=True`
חוזר ובולע את הכשל, ולכן אין שורת יומן והטסט ההתנהגותי נופל על assert של האירוע. טסטי הקטלוג נופלים בשמות `_drop_staging` / `CG-S30`.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.console import journal_he                      # noqa: E402
from server.core.tasks import staging_dir                  # noqa: E402
from server.system import silent_tasks                     # noqa: E402
from test_silent_task_2905 import (                        # noqa: E402,F401
    _events, _running_capture, _sweep, _task, _went_quiet,
)

EVENT = "capture_staging_delete_failed"
REPO = Path(__file__).resolve().parent.parent


def _rmtree_that_cannot_delete(path, ignore_errors=False, **_kw):
    """כמו `shutil.rmtree` כשהקובץ נעול: `ignore_errors` בולע, ובלעדיו נזרקת שגיאה. כך הטסט נופל על הקוד שבולע, לא על חריגה שהוא עצמו המציא."""
    if ignore_errors:
        return
    raise PermissionError(13, "Permission denied", str(path))


def test_a_failed_delete_is_journaled_and_the_task_is_still_failed(server, monkeypatch):
    ctx = server["ctx"]
    task_id = _running_capture(server)
    _went_quiet(ctx, task_id)
    monkeypatch.setattr(silent_tasks.shutil, "rmtree", _rmtree_that_cannot_delete)

    _sweep(ctx)

    assert _task(ctx, task_id)["state"] == "failed", "מחיקה שנכשלה אינה מחזירה את הקליטה לחיים"
    found = _events(ctx, EVENT)
    assert len(found) == 1, f"expected one {EVENT} row, got {found}"
    assert task_id in found[0] and "PermissionError" in found[0], found[0]
    assert staging_dir(ctx.library.root, task_id).exists(), "התיקייה באמת נשארה (הכשל אמיתי, לא רק נרשם)"
    row = ctx.conn.execute("SELECT machine FROM journal WHERE event = ?", (EVENT,)).fetchone()
    assert row["machine"], "האירוע נרשם בלי המכונה"


def test_a_delete_that_works_leaves_no_failure_row(server):
    ctx = server["ctx"]
    task_id = _running_capture(server)
    _went_quiet(ctx, task_id)

    _sweep(ctx)

    assert _task(ctx, task_id)["state"] == "failed"
    assert not staging_dir(ctx.library.root, task_id).exists(), "האזור נמחק אוטומטית, כמו היום"
    assert _events(ctx, EVENT) == []


def test_a_staging_area_that_does_not_exist_is_not_a_failure(server):
    ctx = server["ctx"]
    task_id = _running_capture(server)
    _went_quiet(ctx, task_id, files=False)
    folder = staging_dir(ctx.library.root, task_id)
    for path in sorted(folder.rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    folder.rmdir()
    # השעה האחרונה של הדיווח כבר ישנה; אין תיקייה, ולכן אין מה למחוק

    _sweep(ctx)

    assert _task(ctx, task_id)["state"] == "failed"
    assert _events(ctx, EVENT) == []


def test_the_event_has_a_hebrew_label_and_needs_attention():
    label = journal_he.EVENTS_HE.get(EVENT, "")
    assert any("א" <= ch <= "ת" for ch in label), f"אין תווית בעברית: {label!r}"
    assert journal_he.severity(EVENT) in ("warn", "err"), "כשל במחיקה חייב להופיע ב'דורש תשומת לב'"


def test_the_decision_is_in_the_catalog_and_points_at_the_code():
    with (REPO / "server" / "brain" / "catalog.toml").open("rb") as f:
        items = {it["id"]: it for it in tomllib.load(f)["item"]}
    item = items.get("CG-S30")
    assert item is not None, "ההכרעה של #3695 אינה רשומה בקטלוג"
    assert item["group"] == "ג" and item["layer"] == "invariants" and item["kind"] == "invariant"
    assert item["file"] == "server/system/silent_tasks.py" and item["symbol"] == "_drop_staging"
    text = (REPO / item["file"]).read_text(encoding="utf-8")
    assert item["symbol"] in text and item["anchor"] in text
    assert "ignore_errors" not in text.split("def _drop_staging", 1)[1].split("def sweep", 1)[0], \
        "`ignore_errors` חזר: כשל במחיקה נבלע שוב"
