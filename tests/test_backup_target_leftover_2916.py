"""‏#2916 §5 — פירוק יעד גיבוי רשת ישן שנכשל אינו נעלם.

**התרחיש:** יעד SMB שנשמר לפני #1956, והמפעיל בוחר "ברירת מחדל". ‏`configure` מחליף את
הרשומה (`set_settings`) ורק אחר כך מפרק (`_leave_network`: ‏umount, ‏fstab, ‏credentials)
ומחזיר רק **אזהרות**. ‏`umount` נכשל (busy) — האזהרה מוצגת פעם אחת, ואחרי ההחלפה `load`
כבר לא מסמן `legacy`: העיגון וקובץ ה-credentials נשארים, ואין דרך לנסות שוב.

**התיקון:** כשל בפירוק נשמר עם היעד החדש (`legacy_left`) ו-`load` ממשיך להציג `legacy`
(כתום, "בוחרים יעד בהגדרות כדי לנתק אותו") עד שבחירת יעד נוספת מצליחה לפרק הכול.
"""

from __future__ import annotations

import json

import pytest

from server.storage import locations as sl
from server.storage.locations import FAILED
from test_backup_target_1956 import (TARGET, _real_point, _saved_share, _set,  # noqa: F401
                                     _settings, bk)


def _target_view(bk) -> dict:
    return bk["admin"].get("/api/console/settings").json()["backup_target"]


@pytest.mark.parametrize("step", ["umount", "fstab"])
def test_a_teardown_that_failed_is_shown_and_retried_until_it_succeeds(bk, step):
    fake = bk["fake"]
    _saved_share(bk)
    if step == "umount":
        fake.umount_status = FAILED
    else:
        fake.fstab_status = FAILED
    r = _set(bk, "default")
    assert r.status_code == 200, r.text
    assert any("נכשל" in w and "היעד הישן" in w for w in r.json()["warnings"]), r.json()

    # the old destination is still shown -- not gone with the replaced record
    got = _target_view(bk)
    assert got["type"] == "default" and got["legacy"]["type"] == "smb", got
    assert got["legacy"]["display"] == "//10.44.10.20/dbbackup"

    # still failing: still shown (retrying does not lose it)
    assert _set(bk, "default").status_code == 200
    assert _target_view(bk)["legacy"]["type"] == "smb"

    # fixed: choosing a destination again finishes the teardown and the mark goes away
    fake.umount_status = sl.OK
    fake.fstab_status = sl.OK
    fake.umounts.clear()
    fake.creds_removes.clear()
    r = _set(bk, "default")
    assert r.status_code == 200, r.text
    assert not [w for w in r.json()["warnings"] if "היעד הישן" in w]
    assert _real_point(bk) in fake.umounts
    assert all(e["target"] != _real_point(bk) for e in fake.fstab[-1])
    assert sl.creds_path("backup") in fake.creds_removes
    assert "legacy" not in _target_view(bk)
    assert "legacy_left" not in json.loads(_settings(bk)[TARGET])


def test_a_teardown_that_succeeded_leaves_no_mark_behind(bk, monkeypatch):
    """השומר מפני תיקון-יתר: פירוק שהצליח אינו משאיר `legacy_left`, וה-`legacy` נעלם."""
    from server.system import backup_target
    monkeypatch.setattr(backup_target, "same_disk", lambda a, b: False)
    _saved_share(bk)
    r = _set(bk, "default")
    assert r.status_code == 200, r.text
    assert not [w for w in r.json()["warnings"] if "היעד הישן" in w]
    assert "legacy" not in _target_view(bk)
    assert "legacy_left" not in json.loads(_settings(bk)[TARGET])
