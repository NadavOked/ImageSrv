"""#404 -- לשונית ISO: העלאה, רשימה ומחיקה של קובצי ISO (בלי אתחול ברשת).

מה נבדק, ולמה (עיקרון 6 ועיקרון 5):

* ISO נכנס לספרייה רק אחרי שהוכח שהוא ISO (מבנה ISO 9660 בסקטור 16) ושה-sha256
  שנקרא בחזרה מהדיסק הוא מה שהתקבל; קובץ אחר נדחה בגלוי ואינו נשמר;
* לא נשאר דבר על הדיסק אחרי דחייה (גם לא קובץ זמני);
* משתמש הפצה ואורח לא נוגעים (403/401), ומחיקה נקבעת מהדיסק;
* ה-ISO אינו מופיע כאימג' ואינו משבש את סריקת האימג'ים.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.console import isos as console_isos
from server.library import isos


def _iso(size: int = 100_000, fill: bytes = b"\xa5") -> bytes:
    """ISO מינימלי: סקטורי מערכת ריקים ואז Primary Volume Descriptor."""
    body = bytearray(fill * size)
    body[:isos.PVD_AT] = b"\0" * isos.PVD_AT
    body[isos.PVD_AT:isos.PVD_AT + 7] = b"\x01CD001\x01"
    return bytes(body)


def _upload(client, raw, name="Windows 11 Pro", folder="", filename="Win11.iso", **headers):
    query = {"name": name, "folder": folder, "filename": filename}
    return client.post("/api/console/isos/upload", params=query, content=raw, headers=headers)


def _root(server) -> Path:
    return Path(server["ctx"].library.root)


def _leftovers(server) -> list[str]:
    """כל מה שנשאר בשורש הספרייה מלבד האימג'ים וה-ISO שנכנסו."""
    return sorted(p.name for p in _root(server).iterdir() if p.name.startswith("."))


def _listed(server):
    return server["admin"].get("/api/console/isos").json()


def test_a_valid_iso_enters_the_library_with_the_sha256_of_its_bytes(server):
    raw = _iso(300_000)
    response = _upload(server["admin"], raw)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sha256"] == hashlib.sha256(raw).hexdigest()
    assert body["size"] == len(raw)
    assert body["name"] == "Windows 11 Pro" and body["filename"] == "Win11.iso"
    listed = _listed(server)["isos"]
    assert [m["id"] for m in listed] == [body["id"]]
    assert listed[0]["present"] is True and listed[0]["uploaded_by"] == "noc"
    stored = _root(server) / isos.DIRNAME / body["id"] / isos.FILE
    assert stored.read_bytes() == raw, "הקובץ בדיסק זהה בית-בבית למה שהועלה"
    assert _leftovers(server) == [], "אין קובץ זמני אחרי הצלחה"


def test_an_upload_in_many_chunks_larger_than_the_write_batch(server):
    raw = _iso(2_500_000)
    chunks = [raw[i:i + 700_000] for i in range(0, len(raw), 700_000)]
    response = _upload(server["admin"], iter(chunks))
    assert response.status_code == 200, response.text
    assert response.json()["sha256"] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("magic, looks", [(b"PK\x03\x04", "ZIP"), (b"\x1f\x8b", "gzip"), (b"", None)],
                         ids=["zip", "gzip", "zeros"])
def test_a_file_that_is_not_an_iso_is_refused_in_the_open_and_nothing_is_saved(server, magic, looks):
    response = _upload(server["admin"], magic + b"\0" * 400_000)
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "אינו ISO תקין" in detail and "ולא נשמר" in detail and "ISO 9660" in detail
    if looks:
        assert looks in detail
    assert _listed(server)["isos"] == []
    assert not (_root(server) / isos.DIRNAME).exists() or not list((_root(server) / isos.DIRNAME).iterdir())
    assert _leftovers(server) == [], "גם לא קובץ זמני"


def test_a_file_shorter_than_the_iso_header_is_refused(server):
    response = _upload(server["admin"], b"tiny")
    assert response.status_code == 400 and "אינו ISO תקין" in response.json()["detail"]
    assert _leftovers(server) == []


def test_an_empty_upload_is_refused(server):
    response = _upload(server["admin"], b"")
    assert response.status_code == 400
    assert _listed(server)["isos"] == [] and _leftovers(server) == []


def test_a_refused_stream_is_drained_so_the_browser_gets_the_answer(server):
    """הדחייה באה מהראש, אבל הזרם נקרא עד סופו -- אחרת הדפדפן רואה ניתוק ולא את הסיבה."""
    chunks = iter([b"PK\x03\x04" + b"\0" * 100_000] + [b"\0" * 1_000_000] * 5)
    response = _upload(server["admin"], chunks)
    assert response.status_code == 400 and "ZIP" in response.json()["detail"]


def test_an_expected_sha256_that_does_not_match_refuses_the_file(server):
    raw = _iso()
    wrong = hashlib.sha256(b"other").hexdigest()
    response = _upload(server["admin"], raw, **{"X-Expected-Sha256": wrong})
    assert response.status_code == 400 and wrong in response.json()["detail"]
    assert _listed(server)["isos"] == [] and _leftovers(server) == []


def test_an_expected_sha256_that_matches_is_accepted_in_either_case(server):
    raw = _iso()
    right = hashlib.sha256(raw).hexdigest().upper()
    assert _upload(server["admin"], raw, **{"X-Expected-Sha256": right}).status_code == 200


def test_a_malformed_expected_sha256_is_refused_before_any_byte_is_stored(server):
    response = _upload(server["admin"], _iso(), **{"X-Expected-Sha256": "not-a-hash"})
    assert response.status_code == 400 and _listed(server)["isos"] == []


def test_a_write_that_does_not_read_back_the_same_is_not_saved(server, monkeypatch):
    """עיקרון 6: ה-sha256 נקרא בחזרה מהדיסק. ערך שונה = לא נכנס לספרייה."""
    monkeypatch.setattr(isos, "file_sha256", lambda path: "0" * 64)
    response = _upload(server["admin"], _iso())
    assert response.status_code == 500 and "אימות sha256 אחרי הכתיבה נכשל" in response.json()["detail"]
    assert _listed(server)["isos"] == [] and _leftovers(server) == []


def test_not_enough_room_refuses_before_writing(server, monkeypatch):
    monkeypatch.setattr(console_isos, "_free", lambda root: 1024)
    response = _upload(server["admin"], _iso())
    assert response.status_code == 507
    assert _listed(server)["isos"] == [] and _leftovers(server) == []


def test_room_that_cannot_be_measured_is_a_refusal_not_permission(server, monkeypatch):
    def broken(root):
        raise OSError("no such device")
    monkeypatch.setattr(console_isos, "_free", broken)
    response = _upload(server["admin"], _iso(), )
    # #2543: the OS text goes to the server log; the operator reads our sentence.
    assert response.status_code == 507 and "הפרטים ביומן השרת" in response.json()["detail"]
    assert "no such device" not in response.json()["detail"]


def test_a_second_iso_with_the_same_name_is_refused_and_the_first_is_untouched(server):
    first = _upload(server["admin"], _iso(fill=b"\x01"), name="Windows 11")
    assert first.status_code == 200
    second = _upload(server["admin"], _iso(fill=b"\x02"), name="windows 11")
    assert second.status_code == 409
    listed = _listed(server)["isos"]
    assert [m["id"] for m in listed] == [first.json()["id"]]
    assert _leftovers(server) == []


def test_a_display_name_that_breaks_the_rule_is_refused_not_cleaned(server):
    response = _upload(server["admin"], _iso(), name="../evil")
    assert response.status_code == 400 and _listed(server)["isos"] == []


def test_the_original_filename_is_display_only_and_never_a_path(server):
    response = _upload(server["admin"], _iso(), filename="C:\\evil\\..\\..\\x/Win11.iso")
    assert response.status_code == 200
    assert response.json()["filename"] == "Win11.iso"
    stored = [p.name for p in (_root(server) / isos.DIRNAME / response.json()["id"]).iterdir()]
    assert sorted(stored) == [isos.FILE, isos.META]


def test_only_an_admin_touches_the_iso_library(server):
    raw = _iso()
    uploaded = _upload(server["admin"], raw).json()
    for client, expected in ((server["deploy"], 403), (server["anon"], 401)):
        assert client.get("/api/console/isos").status_code == expected
        assert _upload(client, raw, name="Other").status_code == expected
        assert client.post(f"/api/console/isos/{uploaded['id']}/delete").status_code == expected
        assert client.post("/api/console/iso-folders", json={"name": "Tools"}).status_code == expected
    assert [m["id"] for m in _listed(server)["isos"]] == [uploaded["id"]], "דבר לא השתנה"


def test_delete_removes_the_files_and_is_judged_from_the_disk(server):
    uploaded = _upload(server["admin"], _iso()).json()
    directory = _root(server) / isos.DIRNAME / uploaded["id"]
    assert directory.is_dir()
    assert server["admin"].post(f"/api/console/isos/{uploaded['id']}/delete").status_code == 200
    assert not directory.exists()
    assert _listed(server)["isos"] == []
    again = server["admin"].post(f"/api/console/isos/{uploaded['id']}/delete")
    assert again.status_code == 404


def test_a_delete_that_leaves_the_files_is_a_failure_not_success(server, monkeypatch):
    uploaded = _upload(server["admin"], _iso()).json()
    monkeypatch.setattr(shutil, "rmtree", lambda path, *a, **k: None)
    response = server["admin"].post(f"/api/console/isos/{uploaded['id']}/delete")
    assert response.status_code == 500 and "לא הושלמה" in response.json()["detail"]
    assert [m["id"] for m in _listed(server)["isos"]] == [uploaded["id"]]
    journal = [r["event"] for r in server["ctx"].conn.execute("SELECT event FROM journal")]
    assert "iso_delete" not in journal, "היומן רק אחרי מחיקה שקרתה באמת"


@pytest.mark.parametrize("bad_id", ["../images", "iso_zzzzzz", "iso_ab12cd/../..", "img_aabbcc"])
def test_a_delete_with_a_malformed_id_never_reaches_the_disk(server, bad_id):
    (_root(server) / "keep.txt").write_text("x")
    response = server["admin"].post(f"/api/console/isos/{bad_id}/delete")
    assert response.status_code in (404, 405)
    assert (_root(server) / "keep.txt").exists()
    assert any((_root(server)).glob("*/manifest.json"))


def test_folders_organise_the_isos_like_the_images(server):
    admin = server["admin"]
    assert admin.post("/api/console/iso-folders", json={"name": "Windows"}).status_code == 200
    assert admin.post("/api/console/iso-folders", json={"name": "Windows"}).status_code == 409
    assert admin.post("/api/console/iso-folders", json={"name": "bad/name"}).status_code == 400
    assert _upload(admin, _iso(), folder="Nope").status_code == 400, "תיקייה שלא קיימת אינה נוצרת בהעלאה"
    uploaded = _upload(admin, _iso(), folder="Windows").json()
    assert uploaded["folder"] == "Windows"
    folders = {f["name"]: f["isos"] for f in _listed(server)["folders"]}
    assert folders == {"Windows": 1}
    assert admin.post("/api/console/iso-folders/Windows/delete").status_code == 409
    admin.post(f"/api/console/isos/{uploaded['id']}/delete")
    assert admin.post("/api/console/iso-folders/Windows/delete").status_code == 200
    assert _listed(server)["folders"] == []
    assert admin.post("/api/console/iso-folders/Windows/delete").status_code == 404


def test_a_folder_named_only_in_a_record_is_still_listed(server):
    """הדיסק הוא מקור האמת: תיקייה שרק הרשומה מצביעה עליה נראית ברשימה."""
    uploaded = _upload(server["admin"], _iso()).json()
    meta_path = _root(server) / isos.DIRNAME / uploaded["id"] / isos.META
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["folder"] = "Restored"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    assert {f["name"]: f["isos"] for f in _listed(server)["folders"]} == {"Restored": 1}


def test_a_missing_or_resized_file_is_listed_as_not_present(server):
    uploaded = _upload(server["admin"], _iso()).json()
    stored = _root(server) / isos.DIRNAME / uploaded["id"] / isos.FILE
    stored.write_bytes(b"short")
    assert _listed(server)["isos"][0]["present"] is False
    stored.unlink()
    assert _listed(server)["isos"][0]["present"] is False


def test_a_broken_record_is_skipped_and_does_not_hide_the_others(server):
    good = _upload(server["admin"], _iso()).json()
    broken = _root(server) / isos.DIRNAME / "iso_000000"
    broken.mkdir()
    (broken / isos.META).write_text("{not json", encoding="utf-8")
    foreign = _root(server) / isos.DIRNAME / "iso_111111"
    foreign.mkdir()
    (foreign / isos.META).write_text(json.dumps({"id": "iso_222222", "name": "x", "size": 1, "sha256": "a"}))
    assert [m["id"] for m in _listed(server)["isos"]] == [good["id"]]


def test_an_iso_is_not_an_image_and_does_not_disturb_the_image_scan(server):
    before = sorted(server["ctx"].library.scan())
    _upload(server["admin"], _iso())
    assert sorted(server["ctx"].library.scan()) == before
    assert [i["id"] for i in server["admin"].get("/api/console/images").json()] == before


def test_the_journal_records_who_uploaded_and_deleted(server):
    uploaded = _upload(server["admin"], _iso(), name="Win11").json()
    server["admin"].post(f"/api/console/isos/{uploaded['id']}/delete")
    rows = server["ctx"].conn.execute(
        "SELECT event, detail, user FROM journal WHERE event LIKE 'iso\\_%' ESCAPE '\\' ORDER BY id").fetchall()
    assert [r["event"] for r in rows] == ["iso_upload", "iso_delete"]
    assert uploaded["id"] in rows[0]["detail"] and uploaded["sha256"] in rows[0]["detail"]
    assert {r["user"] for r in rows} == {"noc"}


def test_an_abandoned_upload_file_is_one_the_daily_sweep_knows():
    """הקובץ הזמני נושא את תבנית ההעלאה של הסחיפה היומית (#1737) -- שרת שמת באמצע לא משאיר ג'יגות לנצח."""
    from server.library import work_areas
    assert work_areas.UPLOAD_AREA.match(".upload-abc_123.upload")
    from server.console.library import UPLOAD_PREFIX
    assert UPLOAD_PREFIX == ".upload-"


def test_head_refusal_accepts_only_the_iso_9660_descriptor():
    assert isos.head_refusal(_iso(40_000)[:isos.HEAD_BYTES]) is None
    flipped = bytearray(_iso(40_000)[:isos.HEAD_BYTES])
    flipped[isos.PVD_AT + 1] = ord("X")
    assert isos.head_refusal(bytes(flipped)) is not None
    assert isos.head_refusal(b"") is not None
