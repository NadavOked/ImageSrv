"""‏#1812: NFS נשאר `soft`, עם תקרת זמן כתובה בקוד ולא של הקרנל.

ב-NFS שמעוגן `soft`, ‏`write()` מחזיר הצלחה ל-page cache, והכשל מול הנאס
מגיע רק ב-`fsync`. מי שתופס אותו הוא הכותב: כל נתיב שמכניס אימג' לספרייה
עושה fsync לכל קובץ לפני הפרסום, וכשל fsync הוא כשל בשם (#1813,
`server/atomicfile.py`, ‏`tests/test_library_fsync_1813.py`).

הטסטים נלקחו מ-PR #1836 (`tests/test_durable_writes_1813.py` שם); חלק
ה-#1813 של אותו קובץ לא נכלל, כי #1813 כבר מומש בבסיס בדרך אחרת.
"""

import json

import pytest

pytest.importorskip("fastapi")

from server import storage_locations  # noqa: E402


def test_nfs_keeps_soft_with_an_explicit_ceiling_and_never_hard():
    """הכרעה: `soft` (תקיעה מסתיימת בשגיאה) + fsync בצד הכותב, ותקרה כתובה."""
    opts = storage_locations._nfs_opts({}).split(",")
    assert "soft" in opts and "hard" not in opts, opts
    assert "timeo=600" in opts and "retrans=2" in opts, (
        opts, "תקרת הזמן של בקשה תקועה אינה כתובה — היא של הקרנל, לא שלנו")
    assert opts[0] == "vers=4.1"
    readonly = storage_locations._nfs_opts({"readonly": True, "version": "3"}).split(",")
    assert readonly[0] == "vers=3" and "ro" in readonly and "timeo=600" in readonly


def test_the_fstab_line_carries_the_same_nfs_ceiling(monkeypatch):
    row = {"id": "loc_nas", "type": "nfs", "state": "connected",
           "mount_point": "/var/lib/imagectl/storage/nas",
           "params_json": json.dumps({"server": "10.44.10.20", "export": "/pool/images"})}
    monkeypatch.setattr(storage_locations, "list_rows", lambda _conn: [row])
    # ‏#1956: the backup target joins the same block from the settings -- none here (no DB).
    from server import backup_target
    monkeypatch.setattr(backup_target, "fstab_entry", lambda _conn: None)
    entries = storage_locations.fstab_entries(None)
    assert len(entries) == 1
    assert entries[0]["opts"] == "vers=4.1,soft,timeo=600,retrans=2,_netdev,nofail"
