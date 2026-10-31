"""ייצוא וייבוא של אימג' כקובץ tar — גיבוי והעברה בין שרתים.

הבדיקות מפרקות את הפלט עם `tarfile` של פייתון: הכתיבה כאן ידנית, ולכן
חייבים להוכיח שהיא באמת ustar תקני ולא רק "נראה נכון".
"""

from __future__ import annotations

import io
import json
import re
import tarfile
import types
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.library import archive, images
from server.library.archive import ArchiveError, import_tar, tar_stream
from server.library.images import ImageLibrary

from conftest import MANIFEST_256, write_image


def build_tar(directory, arcname="img_7f3a91") -> bytes:
    return b"".join(tar_stream(directory, arcname))


# --- ייצוא -------------------------------------------------------------------


def test_the_stream_is_a_real_tar(tmp_path):
    write_image(tmp_path, MANIFEST_256)
    raw = build_tar(tmp_path / "img_7f3a91")

    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        names = sorted(tar.getnames())
        assert names == [
            "img_7f3a91/manifest.json",
            "img_7f3a91/p1.esp.pcl.zst",
            "img_7f3a91/p3.win.pcl.zst",
        ]
        content = tar.extractfile("img_7f3a91/p1.esp.pcl.zst").read()
        assert content == b"compressed-partition-bytes"
        manifest = json.loads(tar.extractfile("img_7f3a91/manifest.json").read())
        assert manifest["id"] == "img_7f3a91"


def test_the_archive_is_padded_to_whole_blocks(tmp_path):
    write_image(tmp_path, MANIFEST_256)
    raw = build_tar(tmp_path / "img_7f3a91")
    assert len(raw) % 512 == 0
    assert raw.endswith(b"\0" * 1024)          # שני בלוקים ריקים = סוף ארכיון


def test_a_long_name_is_refused_rather_than_truncated(tmp_path):
    write_image(tmp_path, MANIFEST_256)
    with pytest.raises(ArchiveError):
        list(tar_stream(tmp_path / "img_7f3a91", "x" * 120))


# --- ייבוא -------------------------------------------------------------------


def import_bytes(raw, images_root, existing=frozenset()):
    archive = images_root.parent / "upload.tar"
    archive.write_bytes(raw)
    return import_tar(archive, images_root, set(existing))


def test_a_round_trip_restores_the_image(tmp_path):
    source = tmp_path / "source"
    write_image(source, MANIFEST_256)
    raw = build_tar(source / "img_7f3a91")

    target = tmp_path / "target"
    target.mkdir()
    manifest = import_bytes(raw, target)
    assert manifest["id"] == "img_7f3a91"
    assert (target / "img_7f3a91" / "manifest.json").is_file()
    assert (target / "img_7f3a91" / "p3.win.pcl.zst").read_bytes() == b"compressed-partition-bytes"
    assert [p.name for p in target.iterdir()] == ["img_7f3a91"]   # אזור הביניים נוקה


def test_a_corrupted_partition_is_caught_on_import(tmp_path):
    """אימג' פגום חייב להיתפס כאן, לא באמצע שחזור מול כיתה."""
    source = tmp_path / "source"
    write_image(source, MANIFEST_256)
    (source / "img_7f3a91" / "p3.win.pcl.zst").write_bytes(b"tampered")
    raw = build_tar(source / "img_7f3a91")

    target = tmp_path / "target"
    target.mkdir()
    with pytest.raises(ArchiveError, match="sha256"):
        import_bytes(raw, target)
    assert list(target.iterdir()) == []               # שום דבר לא נשאר


def test_an_image_the_library_would_reject_is_refused_on_import(tmp_path):
    raw = tar_with_manifest(tmp_path, {"family": 999})
    target = tmp_path / "target"
    target.mkdir()

    with pytest.raises(ArchiveError, match="family must be 256 or 500"):
        import_bytes(raw, target)

    assert not (target / MANIFEST_256["id"]).exists()
    assert list(target.iterdir()) == []


@pytest.mark.parametrize("field", ["start_sector", "size_bytes"])
def test_zero_partition_geometry_is_refused_on_import(tmp_path, field):
    parts = json.loads(json.dumps(MANIFEST_256["partitions"]))
    parts[0][field] = 0
    # ‏used_bytes > size_bytes נפסל לפני הגיאומטריה (#1407); הוא מוסר כאן כדי
    # שהטסט ימשיך לבדוק את מה שבשמו — גיאומטריה אפסית.
    parts[0].pop("used_bytes")
    raw = tar_with_manifest(tmp_path, {"partitions": parts})
    target = tmp_path / "target"
    target.mkdir()

    with pytest.raises(ArchiveError, match="malformed geometry"):
        import_bytes(raw, target)

    assert list(target.iterdir()) == []


def test_a_missing_partition_file_is_caught(tmp_path):
    source = tmp_path / "source"
    write_image(source, MANIFEST_256)
    (source / "img_7f3a91" / "p1.esp.pcl.zst").unlink()
    raw = build_tar(source / "img_7f3a91")

    target = tmp_path / "target"
    target.mkdir()
    with pytest.raises(ArchiveError, match="חסר קובץ מחיצה"):
        import_bytes(raw, target)


def test_importing_a_duplicate_id_is_refused(tmp_path):
    source = tmp_path / "source"
    write_image(source, MANIFEST_256)
    raw = build_tar(source / "img_7f3a91")
    target = tmp_path / "target"
    target.mkdir()
    with pytest.raises(ArchiveError, match="כבר קיים"):
        import_bytes(raw, target, existing={"img_7f3a91"})


def test_the_image_is_invisible_until_every_sha256_matched(tmp_path, monkeypatch):
    """‏#71: לאורך כל חלון האימות — דקות על אימג' אמיתי — האימג' שמיובא
    אינו בספרייה. אימג' שנראה בספרייה הוא אימג' שמישהו יבחר לסבב."""
    source = tmp_path / "source"
    write_image(source, MANIFEST_256)
    raw = build_tar(source / "img_7f3a91")
    target = tmp_path / "target"
    target.mkdir()

    seen = []
    real_sha256 = archive._sha256

    def spy(path):
        seen.append(set(ImageLibrary(target).scan()))
        return real_sha256(path)

    monkeypatch.setattr(archive, "_sha256", spy)
    import_bytes(raw, target)

    assert seen and all(ids == set() for ids in seen)
    assert set(ImageLibrary(target).scan()) == {"img_7f3a91"}   # ורק אחריו


def test_import_hashes_the_bytes_at_their_final_placement(tmp_path, monkeypatch):
    source = tmp_path / "source"
    write_image(source, MANIFEST_256)
    raw = build_tar(source / MANIFEST_256["id"])

    library = tmp_path / "target"
    library.mkdir()

    real_rename = Path.rename
    race_happened = False

    def overwrite_immediately_before_placement(self, destination):
        nonlocal race_happened
        destination = Path(destination)

        if (
            destination == library / MANIFEST_256["id"]
            and self.parent.name.startswith(archive.STAGING_PREFIX)
        ):
            # Simulate an overlapping writer after the old hash check.
            (self / "p3.win.pcl.zst").write_bytes(b"changed-after-verification")
            race_happened = True

        return real_rename(self, destination)

    monkeypatch.setattr(Path, "rename", overwrite_immediately_before_placement)

    with pytest.raises(ArchiveError, match="sha256"):
        import_bytes(raw, library)

    assert race_happened
    assert ImageLibrary(library).scan() == {}
    assert not (library / MANIFEST_256["id"]).exists()


def test_an_import_never_displaces_a_verified_image_with_the_same_id(tmp_path):
    """המזהה תפוס בספרייה, בתיקייה ששמה אינו המזהה — עותק שהועבר ביד,
    למשל. אז `images_root/<id>` פנוי, והצילום שנלקח לפני האימות כבר לא
    מעודכן: בלי בדיקה טרייה הייבוא נוחת לצד המאומת ודוחק אותו."""
    source = tmp_path / "source"
    write_image(source, MANIFEST_256)
    raw = build_tar(source / "img_7f3a91")

    target = tmp_path / "target"
    write_image(target / "moved", MANIFEST_256)
    with pytest.raises(ArchiveError, match="כבר קיים"):
        import_bytes(raw, target, existing=frozenset())

    library = ImageLibrary(target)
    assert library.get("img_7f3a91")["_dir"] == str(target / "moved" / "img_7f3a91")
    assert [p.name for p in target.iterdir()] == ["moved"]


def test_an_interrupted_import_leaves_nothing_that_reads_as_an_image(tmp_path, monkeypatch):
    """הפסקת חשמל באמצע האימות: הניקוי לא הספיק לרוץ. מה שנשאר על הדיסק
    הוא הצהרה — ותיקייה חלקית עם manifest.json מצהירה שמונח כאן אימג'."""
    source = tmp_path / "source"
    write_image(source, MANIFEST_256)
    raw = build_tar(source / "img_7f3a91")
    target = tmp_path / "target"
    target.mkdir()

    def power_cut(path):
        raise KeyboardInterrupt("power cut")

    monkeypatch.setattr(archive, "_sha256", power_cut)
    monkeypatch.setattr(archive, "shutil", types.SimpleNamespace(rmtree=lambda *a, **k: None))
    with pytest.raises(KeyboardInterrupt):
        import_bytes(raw, target)

    assert list(target.rglob("manifest.json")) == []
    assert ImageLibrary(target).scan() == {}


def test_path_traversal_in_an_archive_is_refused(tmp_path):
    """ארכיון מגיע מבחוץ — נתיב שיוצא מהתיקייה הוא ניסיון כתיבה לשרת."""
    archive = tmp_path / "evil.tar"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo("../../etc/passwd")
        info.size = 4
        tar.addfile(info, io.BytesIO(b"root"))
    target = tmp_path / "target"
    target.mkdir()
    with pytest.raises(ArchiveError, match="לא בטוח"):
        import_tar(archive, target, set())


def test_a_symlink_in_an_archive_is_refused(tmp_path):
    archive = tmp_path / "link.tar"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo("img_x/evil")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/shadow"
        tar.addfile(info)
    target = tmp_path / "target"
    target.mkdir()
    with pytest.raises(ArchiveError, match="אינו קובץ"):
        import_tar(archive, target, set())


# --- מה שהמניפסט מבקש, ולא מה שה-tar מכיל (#110) -----------------------------
#
# ‏`_safe_members` שומר על החילוץ, ושתי הבדיקות שמעליו מוכיחות זאת. שני
# השדות כאן נקראים מ**המניפסט** — קובץ שהמעלה כתב — ומצטרפים לנתיב אחרי
# שהחילוץ כבר הסתיים, ולכן הם מסלול שני פנימה שאותה שמירה אינה מכסה.


def tar_with_manifest(tmp_path, changes: dict, arcname="img_7f3a91") -> bytes:
    """ארכיון שהמניפסט שלו נערך ידנית — ככה נראה קובץ שמישהו בנה."""
    source = tmp_path / "source"
    if not source.exists():
        write_image(source, MANIFEST_256)
    folder = source / "img_7f3a91"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    manifest.update(changes)
    (folder / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return b"".join(tar_stream(folder, arcname))


def outside_part(name: str, sha: str = "cc" * 32) -> dict:
    part = dict(MANIFEST_256["partitions"][0])
    part["file"] = name
    part["sha256"] = sha
    return part


@pytest.mark.parametrize("bad_id", [
    "../evil", "../../srv/tftp/pxelinux.cfg", "img_7f3a91/../../evil",
    "evil", "img_ZZZZZZ", "",
])
def test_a_manifest_id_that_is_not_an_id_is_refused_by_name(tmp_path, bad_id):
    """המזהה שבמניפסט הוא יעד ה-`rename`. ‏`../evil` בו הוא כתיבה אל
    מחוץ לשורש הספרייה, לכל מקום שתהליך השרת רשאי לכתוב אליו."""
    raw = tar_with_manifest(tmp_path, {"id": bad_id})
    target = tmp_path / "target"
    target.mkdir()

    # מאז #1407 ‏`_validate` של הספרייה דוחה את המזהה בשמו, לפני הבדיקה
    # הנפרדת של archive.py — התכונה (נדחה, ושום דבר לא נכתב) לא השתנתה.
    with pytest.raises(ArchiveError, match="מניפסט לא תקין: 'image id or name is malformed'"):
        import_bytes(raw, target)

    # לא רק שנזרקה חריגה: שום דבר לא נכתב, לא בשורש ולא מחוצה לו.
    assert list(target.iterdir()) == []
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "source", "target", "upload.tar"]


def test_the_library_root_holds_even_if_the_id_rule_is_widened(tmp_path, monkeypatch):
    """החגורה השנייה, לבדה: הכלל הורחב עד שהוא מקבל הכל, והיעד עדיין
    חייב לנחות בתוך השורש. ‏`target.exists()` אינו גבול — היעד כאן
    אינו קיים, וזה בדיוק המקרה שהוא מכשיר."""
    monkeypatch.setattr(images, "IMAGE_ID", re.compile(r"[^\x00]*"))
    raw = tar_with_manifest(tmp_path, {"id": "../evil"})
    target = tmp_path / "target"
    target.mkdir()

    with pytest.raises(ArchiveError, match="יוצא משורש הספרייה"):
        import_bytes(raw, target)

    assert not (tmp_path / "evil").exists()
    assert list(target.iterdir()) == []


def test_a_partition_path_that_leaves_the_image_folder_is_never_read(tmp_path,
                                                                     monkeypatch):
    """‏`part["file"]` נקרא מהמניפסט ולא מחברי ה-tar. קריאה בלבד — אבל
    ‏`_sha256` על נתיב שהמעלה בחר הוא אורקל על תוכן הדיסק של השרת."""
    secret = tmp_path / "secret.bin"
    secret.write_bytes(b"server-side-secret")
    raw = tar_with_manifest(
        tmp_path, {"partitions": [outside_part("../../../secret.bin")]})
    target = tmp_path / "target"
    target.mkdir()

    touched = []
    monkeypatch.setattr(archive, "_sha256", lambda path: touched.append(path) or "")

    with pytest.raises(ArchiveError, match="unsafe file name"):
        import_bytes(raw, target)

    assert touched == []                       # הקובץ שמחוץ לתיקייה לא נקרא
    assert secret.read_bytes() == b"server-side-secret"
    assert list(target.iterdir()) == []


@pytest.mark.parametrize("name", [
    "../../../secret.bin", "../manifest.json", "/etc/shadow", "sub/../../escape",
])
def test_every_shape_of_an_escaping_partition_path_is_refused(tmp_path, name):
    raw = tar_with_manifest(tmp_path, {"partitions": [outside_part(name)]})
    target = tmp_path / "target"
    target.mkdir()
    with pytest.raises(ArchiveError, match="unsafe file name"):
        import_bytes(raw, target)
    assert list(target.iterdir()) == []


def test_the_refusal_says_the_same_thing_whether_the_file_is_there_or_not(tmp_path):
    """הודעת שגיאה היא ערוץ. אם היא מבדילה בין "קיים" ל"לא קיים",
    הייבוא הוא כלי לגלות מה מונח על השרת — בלי לכתוב דבר."""
    (tmp_path / "here.bin").write_bytes(b"x")
    said = []
    for index, name in enumerate(("../../../here.bin", "../../../nowhere.bin")):
        raw = tar_with_manifest(tmp_path, {"partitions": [outside_part(name)]})
        target = tmp_path / f"target{index}"
        target.mkdir()
        with pytest.raises(ArchiveError) as caught:
            import_bytes(raw, target)
        said.append(str(caught.value))
    # מאז #1407 הדחייה היא של `_validate`, והיא אינה מצטטת את השם כלל
    assert said[0] == said[1] == "מניפסט לא תקין: 'partition 1 has an unsafe file name'"


def test_a_well_formed_import_is_untouched_by_the_new_rules(tmp_path):
    """הבקרה הישרה: המזהה שהשרת מייצר, וקבצי המחיצות במקומם."""
    source = tmp_path / "source"
    write_image(source, MANIFEST_256)
    target = tmp_path / "target"
    target.mkdir()
    assert import_bytes(build_tar(source / "img_7f3a91"), target)["id"] == "img_7f3a91"
    assert (target / "img_7f3a91" / "manifest.json").is_file()


def test_an_archive_without_a_manifest_is_refused(tmp_path):
    archive = tmp_path / "plain.tar"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo("img_x/data.bin")
        info.size = 3
        tar.addfile(info, io.BytesIO(b"abc"))
    target = tmp_path / "target"
    target.mkdir()
    with pytest.raises(ArchiveError, match="manifest"):
        import_tar(archive, target, set())


# --- דרך ה-API ---------------------------------------------------------------


def test_download_and_upload_through_the_console(server, images_root):
    admin = server["admin"]
    raw = admin.get("/api/console/images/img_7f3a91/download")
    assert raw.status_code == 200
    assert raw.headers["content-type"] == "application/x-tar"
    assert "img_7f3a91.tar" in raw.headers["content-disposition"]

    # מוחקים ומעלים בחזרה — הדרך שבה מעבירים אימג' בין שרתים.
    admin.post("/api/console/images/img_7f3a91/delete",
               json={"confirm_name": "Office 2024 Standard"})
    assert "img_7f3a91" not in {m["id"] for m in admin.get("/api/console/images").json()}

    restored = admin.post("/api/console/images/upload", content=raw.content)
    assert restored.status_code == 200
    assert restored.json()["name"] == "Office 2024 Standard"
    assert "img_7f3a91" in {m["id"] for m in admin.get("/api/console/images").json()}


def test_uploading_over_an_existing_image_is_refused(server):
    admin = server["admin"]
    raw = admin.get("/api/console/images/img_7f3a91/download").content
    response = admin.post("/api/console/images/upload", content=raw)
    assert response.status_code == 400
    assert "כבר קיים" in response.json()["detail"]


def test_upload_is_admin_only_and_download_is_console_only(server):
    """ההורדה אינה admin_only — אבל היא קונסולה (#1073: deploy מקבל 403
    `deploy_no_console`, לא 403 של admin_only). admin מוריד."""
    assert server["admin"].get(
        "/api/console/images/img_7f3a91/download").status_code == 200
    refused = server["deploy"].get("/api/console/images/img_7f3a91/download")
    assert refused.status_code == 403 and "מחשב הבנייה" in refused.json()["detail"]
    assert server["deploy"].post(
        "/api/console/images/upload", content=b"x" * 1024).status_code == 403


def test_an_empty_upload_is_an_orderly_error(server):
    response = server["admin"].post("/api/console/images/upload", content=b"")
    assert response.status_code == 400
