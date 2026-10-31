"""‏#1457: קליטת push במשני — חילוץ ו-sha256 **תוך כדי** הזרם.

לפני #1457 המשני כתב את כל הגוף ל-``tmp*.transfer``, אחר כך ``extractall``
העתיק אותו לאזור הביניים, ואז ``_sha256`` קרא כל מחיצה שוב — כ-3× קלט/פלט
אחרי הבייט האחרון, בזמן שהראשי ממתין בתקרה קבועה (``VERIFY_MIN_RATE``).
ראשי שנכשל בתקרה בזמן שהמשני עוד מכניס את האימג' = שני שרתים שמספרים
סיפור שונה (עיקרון 5). והמקום הפנוי שנדרש היה פי 2.

מה שמוכח כאן, בשכבת ה-service של המשני (``receive_image``):
(א) בזמן שהגוף עוד מגיע, המחיצה כבר באזור הביניים ואין קובץ ``.transfer``;
(ב) אחרי הבייט האחרון אין קריאה שנייה של המחיצות (``_sha256`` לא נקרא);
(ג) בדיקת המקום הפנוי דורשת פי 1 ולא פי 2;
(ד) ארכיון זדוני/שבור נדחה בשמו, הגוף נקרא עד סופו (הסיבה מגיעה לראשי
    כתשובה, לא כ"נותק"), ושום דבר לא נשאר בספרייה;
(ה) גוף ארוך מהמוצהר אחרי ארכיון תקין — האימג' אינו נשאר בספרייה.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from conftest import MANIFEST_256, PARTITION_BYTES, write_image
from server.library import archive
from server.storage import disk_reserve, storage_locations
from server.interserver import interserver_api
from server.library.images import ImageLibrary

IMAGE = MANIFEST_256["id"]


class _Ctx:
    def __init__(self, library):
        self.conn = None
        self.library = library


@pytest.fixture()
def secondary(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    root.mkdir()
    events = []
    monkeypatch.setattr(interserver_api, "journal",
                        lambda _conn, event, *_a: events.append(event))
    return _Ctx(ImageLibrary(root)), root, events


def _body(tmp_path: Path) -> bytes:
    src = tmp_path / "src"
    write_image(src, MANIFEST_256)
    return b"".join(archive.tar_stream(src / IMAGE, IMAGE))


def _plan(root: Path, length: int):
    return interserver_api.ReceivePlan(image_id=IMAGE, content_length=length, root=root)


def _pieces(data: bytes, size: int = 512):
    return [data[i:i + size] for i in range(0, len(data), size)]


# --- (א)+(ב) חילוץ תוך כדי, בלי קובץ זמני ובלי קריאה שנייה ---------------------

def test_partition_is_in_staging_before_the_body_ends_and_no_temp_file(secondary, tmp_path):
    ctx, root, events = secondary
    body = _body(tmp_path)
    tail = archive.BLOCK * 2                   # סוף הארכיון: שני בלוקים של אפסים
    seen = {}

    def chunks():
        for piece in _pieces(body[:-tail]):
            yield piece
        # הבייטים האחרונים עוד לא נשלחו — מה כבר על הדיסק של המשני?
        seen["transfer"] = [p.name for p in root.rglob("*") if p.suffix == ".transfer"]
        seen["staged"] = [p.read_bytes() for p in root.glob(".import-*/*/*")
                          if p.name != "manifest.json"]
        yield body[-tail:]

    result = interserver_api.receive_image(ctx, _plan(root, len(body)), chunks())
    assert result["ok"] is True and result["id"] == IMAGE
    assert seen["transfer"] == []
    assert seen["staged"] and all(b == PARTITION_BYTES for b in seen["staged"])
    assert IMAGE in ctx.library.scan()
    assert events == ["storage_image_received"]
    assert [p.name for p in root.iterdir()] == [IMAGE]


def test_no_second_read_of_the_partitions_after_the_last_byte(secondary, tmp_path,
                                                               monkeypatch):
    """ה-sha256 חושב על הבייטים בזמן הכתיבה; קריאה חוזרת של הקבצים (``_sha256``)
    אחרי הזרם היא בדיוק העבודה ש-#1457 מוריד מהמשני."""
    ctx, root, _ = secondary
    body = _body(tmp_path)

    def reread(_path):
        raise AssertionError("המחיצה נקראה שוב מהדיסק אחרי הזרם")

    monkeypatch.setattr(archive, "_sha256", reread)
    result = interserver_api.receive_image(ctx, _plan(root, len(body)), _pieces(body, 4096))
    assert result["ok"] is True
    assert IMAGE in ctx.library.scan()


# --- (ג) מקום פנוי: פי 1 ---------------------------------------------------------

@pytest.mark.system_reserve   # #1737: the real 10GiB (conftest _host_free_space)
def test_free_space_precheck_needs_the_length_once(secondary, monkeypatch):
    """עם חילוץ ישיר אין עותק שני על הדיסק: ``length + margin`` מספיק בדיוק,
    ובית אחד פחות — 507 (לא "ננסה")."""
    length = 70 * 1024 ** 3
    # ‏#1737: ועוד הרזרבה למערכת — האימג' לא נכנס אם ישאיר פחות ממנה.
    need = length + interserver_api.FREE_SPACE_MARGIN + disk_reserve.SYSTEM_RESERVE_BYTES
    ctx = secondary[0]

    def precheck(free):
        monkeypatch.setattr(interserver_api.shutil, "disk_usage",
                            lambda _p: type("U", (), {"free": free, "total": free})())
        return interserver_api.receive_image_precheck(
            ctx, None, token="t", protocol_version="2.1",
            image_id=IMAGE, content_length=length)

    monkeypatch.setattr(interserver_api, "authenticate_parent", lambda *a, **k: None)
    # #1719: the precheck reads the target's storage_locations row. This ctx has
    # no DB (conn=None): no row for loc_local = the library root, the path the
    # code keeps for an isolated context. The rule checked here is the space.
    monkeypatch.setattr(storage_locations, "get", lambda _conn, _loc: None)
    plan = precheck(need)
    assert plan.content_length == length
    with pytest.raises(interserver_api.PairError) as e:
        precheck(need - 1)
    assert e.value.status == 507
    assert str(need) in e.value.detail


# --- (ד) ארכיון זדוני/שבור: נדחה בשמו, נקרא עד הסוף, לא נשאר כלום -------------

def _tar(entries) -> bytes:
    """``entries`` = [(name, bytes | "symlink" | ("type", bytes))] → tar בזיכרון."""
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for name, data in entries:
            info = tarfile.TarInfo(name)
            if isinstance(data, tuple):
                info.type, payload = data
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
            elif data == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def _manifest_bytes(tmp_path: Path) -> bytes:
    src = tmp_path / "m"
    write_image(src, MANIFEST_256)
    return (src / IMAGE / "manifest.json").read_bytes()


def _partition_names() -> list[str]:
    return [p["file"] for p in MANIFEST_256["partitions"] if p.get("file")]


@pytest.mark.parametrize("case, reason", [
    ("traversal", "נתיב לא בטוח"),
    ("symlink", "אינו קובץ"),
    ("duplicate", "כפול"),
    ("missing_partition", "חסר קובץ מחיצה"),
    ("two_roots", "תיקיית אימג' אחת"),
    ("bad_sha", "sha256"),
    ("sparse", "sparse"),
    ("contiguous", "sparse"),
])
def test_bad_archive_is_refused_by_name_drained_and_leaves_nothing(secondary, tmp_path,
                                                                   case, reason):
    ctx, root, events = secondary
    manifest = _manifest_bytes(tmp_path)
    parts = [(f"{IMAGE}/{name}", PARTITION_BYTES) for name in _partition_names()]
    entries = {
        "traversal": [(f"{IMAGE}/../../evil", b"x")] + parts,
        "symlink": [(f"{IMAGE}/link", "symlink")] + parts,
        "duplicate": parts[:1] + parts[:1],
        "missing_partition": [(f"{IMAGE}/manifest.json", manifest)] + parts[1:],
        "two_roots": [(f"{IMAGE}/manifest.json", manifest), ("other/x", b"x")] + parts,
        "bad_sha": [(f"{IMAGE}/manifest.json", manifest)]
                   + [(n, b"Y" + PARTITION_BYTES[1:]) for n, _ in parts],
        # ‏isfile() מקבל את שניהם; sparse נפרש לבייטים רבים מאלה שנשלחו.
        "sparse": [(f"{IMAGE}/manifest.json", manifest)]
                  + [(n, (tarfile.GNUTYPE_SPARSE, d)) for n, d in parts],
        "contiguous": [(f"{IMAGE}/manifest.json", manifest)]
                      + [(n, (tarfile.CONTTYPE, d)) for n, d in parts],
    }[case]
    body = _tar(entries)
    pieces = _pieces(body)
    consumed = []

    def chunks():
        for piece in pieces:
            consumed.append(len(piece))
            yield piece

    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.receive_image(ctx, _plan(root, len(body)), chunks())
    assert e.value.status == 400
    assert reason in e.value.detail, e.value.detail
    assert "נקטעה" not in e.value.detail
    assert sum(consumed) == len(body)           # הגוף נקרא עד סופו
    assert list(root.iterdir()) == []
    assert not (tmp_path / "evil").exists()
    assert events == []


def test_truncated_inside_a_partition_is_named_as_truncation(secondary, tmp_path):
    """קטיעה באמצע קובץ נראית ל-tarfile כמו ארכיון שבור — אבל הסיבה שמגיעה
    לראשי היא הקטיעה, עם המספרים (עיקרון 4)."""
    ctx, root, _ = secondary
    body = _body(tmp_path)
    cut = body.index(PARTITION_BYTES) + 5
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.receive_image(ctx, _plan(root, len(body)), [body[:cut]])
    assert "נקטעה" in e.value.detail
    assert str(cut) in e.value.detail and str(len(body)) in e.value.detail
    assert list(root.iterdir()) == []


# --- (ה) גוף ארוך מהמוצהר ------------------------------------------------------

def test_body_longer_than_declared_does_not_leave_the_image(secondary, tmp_path):
    ctx, root, events = secondary
    body = _body(tmp_path)
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.receive_image(ctx, _plan(root, len(body)),
                                      [body, b"\0" * archive.BLOCK])
    assert "ארוך" in e.value.detail
    assert ctx.library.scan() == {}
    assert list(root.iterdir()) == []
    assert events == []


def test_bytes_written_are_capped_at_the_declared_length(tmp_path):
    """חגורה מעבר לספירת הקורא: סך מה שנכתב לדיסק לא עובר את ``max_bytes``
    (האורך המוצהר, שעליו נבדק המקום הפנוי) — גם ממקור שאינו מוגבל."""
    body = _body(tmp_path)
    root = tmp_path / "lib2"
    root.mkdir()
    with pytest.raises(archive.ArchiveError) as e:
        archive.import_tar_stream(io.BytesIO(body), root, set(),
                                  max_bytes=len(PARTITION_BYTES) - 1)
    assert str(len(PARTITION_BYTES) - 1) in str(e.value)
    assert list(root.iterdir()) == []


def test_sparse_member_is_refused_by_the_browser_upload_path_too(tmp_path):
    """‏``_safe_name`` משותף — גם ``import_tar`` (העלאה מהדפדפן) מסרב לו."""
    archive_path = tmp_path / "s.tar"
    archive_path.write_bytes(_tar([(f"{IMAGE}/manifest.json", _manifest_bytes(tmp_path))]
                                  + [(f"{IMAGE}/{n}", (tarfile.GNUTYPE_SPARSE, PARTITION_BYTES))
                                     for n in _partition_names()]))
    root = tmp_path / "lib3"
    root.mkdir()
    with pytest.raises(archive.ArchiveError, match="sparse"):
        archive.import_tar(archive_path, root, set())
    assert list(root.iterdir()) == []
