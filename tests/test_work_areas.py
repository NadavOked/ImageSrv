"""סחיפת אזורי עבודה יתומים (‏#88).

הבדיקה המרכזית כאן היא לא שהיתומים נמחקים אלא שמה ש**אינו** מוכח יתום
שורד: אזור עבודה של משימה שרצה עכשיו, אזור עבודה שאין לו שורה בבסיס
הנתונים, וכל מה שאינו מתחיל בנקודה — כלומר האימג'ים עצמם.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from native import DECLARED_PREFIX

pytest.importorskip("fastapi")

from conftest import MANIFEST_256, write_image

from server.db import connect
from server.core.tasks import staging_dir
from server.library.work_areas import CAPTURE_AREA, human_bytes, plan, sweep

NOW = "2026-08-27T00:27:28+00:00"


@pytest.fixture()
def conn(tmp_path: Path):
    return connect(tmp_path / "imagesrv.db")


def add_task(conn, task_id: str, state: str) -> None:
    conn.execute(
        "INSERT INTO tasks (id, mac, type, disk, image_id, name, state,"
        " created_by, created_at, updated_at)"
        " VALUES (?, 'aa:bb:cc:00:00:10', 'capture', 'sda', 'img_a8df93',"
        " 'Windows 11 Base', ?, 'nadav', ?, ?)",
        (task_id, state, NOW, NOW),
    )
    conn.commit()


def make_area(root: Path, name: str, size: int = 4096) -> Path:
    area = root / name
    area.mkdir(parents=True)
    (area / "p1.esp.pcl.zst").write_bytes(b"x" * size)
    return area


def events(conn) -> dict[str, str]:
    return {row["event"]: row["detail"]
            for row in conn.execute("SELECT event, detail FROM journal")}


# --- מה שנסחף ----------------------------------------------------------------


def test_a_work_area_of_a_task_that_failed_is_swept(conn, tmp_path):
    """‏#88 עצמו: כך נראים חמשת היתומים שנמדדו על שרת המעבדה — שורת
    משימה במצב `failed`, ותיקייה שאיש לא ניקה."""
    root = tmp_path / "images"
    add_task(conn, "tsk_0930", "failed")
    make_area(root, ".capture-tsk_0930", size=2048)

    result = sweep(conn, root)

    assert result["swept"] == ["tsk_0930"]
    assert result["freed_bytes"] == 2048
    assert not (root / ".capture-tsk_0930").exists()


@pytest.mark.parametrize("state", ["done", "failed", "cancelled"])
def test_every_final_state_is_proof_enough(conn, tmp_path, state):
    root = tmp_path / "images"
    add_task(conn, "tsk_1b82", state)
    make_area(root, ".capture-tsk_1b82")
    assert sweep(conn, root)["swept"] == ["tsk_1b82"]


def test_the_journal_says_how_many_and_how_much_room(conn, tmp_path):
    """מחיקה שקטה של עשרות ג'יגה היא בדיוק מה שצריך להשאיר עקבות."""
    root = tmp_path / "images"
    for task_id, size in (("tsk_e265", 1024), ("tsk_13c0", 3072)):
        add_task(conn, task_id, "failed")
        make_area(root, f".capture-{task_id}", size=size)

    sweep(conn, root)

    detail = events(conn)["work_area_swept"]
    assert "swept=2" in detail
    assert "freed=4.0KB" in detail
    assert "tsk_13c0" in detail and "tsk_e265" in detail


def test_the_hebrew_journal_reads_it_out(conn, tmp_path):
    from server.library.images import ImageLibrary
    from server.console.journal_he import JournalTranslator

    root = tmp_path / "images"
    add_task(conn, "tsk_db79", "failed")
    make_area(root, ".capture-tsk_db79", size=5 * 1024 * 1024)
    sweep(conn, root)

    label, text = JournalTranslator(conn, ImageLibrary(root)).translate(
        "work_area_swept", events(conn)["work_area_swept"])
    assert label == "אזורי עבודה יתומים נמחקו"
    assert text == "1 אזורי עבודה · 5.0MB התפנו"


# --- מה ששורד ----------------------------------------------------------------


def test_a_work_area_of_a_live_task_survives_the_sweep(conn, tmp_path):
    """הבדיקה שהמשימה כולה נשענת עליה: משימה פתוחה עשויה לחזור אחרי
    אתחול — המכונה מקבלת אותה שוב ב-hello וממשיכה להעלות."""
    root = tmp_path / "images"
    add_task(conn, "tsk_alive", "running")
    add_task(conn, "tsk_soon", "pending")
    make_area(root, ".capture-tsk_alive")
    make_area(root, ".capture-tsk_soon")

    result = sweep(conn, root)

    assert result["swept"] == []
    assert (root / ".capture-tsk_alive" / "p1.esp.pcl.zst").is_file()
    assert (root / ".capture-tsk_soon" / "p1.esp.pcl.zst").is_file()
    assert dict(result["kept"]) == {
        ".capture-tsk_alive": "המשימה עדיין running",
        ".capture-tsk_soon": "המשימה עדיין pending",
    }


def test_a_work_area_with_no_row_at_all_is_kept_not_swept(conn, tmp_path):
    """עיקרון 5: היעדר שורה אינו ראיה שהמשימה מתה אלא ראיה שה-DB הזה
    אינו מכיר אותה — ‏--data-dir אחר, בסיס משוחזר מגיבוי."""
    root = tmp_path / "images"
    make_area(root, ".capture-tsk_unknown")

    result = sweep(conn, root)

    assert result["swept"] == []
    assert (root / ".capture-tsk_unknown").is_dir()
    assert result["kept"] == [(".capture-tsk_unknown",
                               "אין שורה למשימה הזאת בבסיס הנתונים")]
    assert "tsk_unknown" in events(conn)["work_area_kept"]


def test_an_unknown_task_state_is_not_assumed_dead(conn, tmp_path):
    root = tmp_path / "images"
    add_task(conn, "tsk_odd", "paused-by-a-future-version")
    make_area(root, ".capture-tsk_odd")

    assert sweep(conn, root)["swept"] == []
    assert (root / ".capture-tsk_odd").is_dir()


def test_an_import_staging_area_is_reported_and_left_alone(conn, tmp_path):
    """לייבוא אין רשומת משימה בשום מקום, ולכן אין ראיה חיובית שהוא מת —
    רק ההסקה "התהליך שלנו חדש", שנשברת מול שרת שני על אותו שורש."""
    root = tmp_path / "images"
    make_area(root, ".import-4b1e77a2")

    result = sweep(conn, root)

    assert result["swept"] == []
    assert (root / ".import-4b1e77a2" / "p1.esp.pcl.zst").is_file()
    assert result["kept"] == [(".import-4b1e77a2",
                               "אזור עבודה שאין לו רשומת משימה")]


# --- מה שאסור לגעת בו --------------------------------------------------------


def test_nothing_without_a_leading_dot_is_ever_considered(conn, tmp_path):
    """עיקרון 3: הדיסק הוא מקור האמת לאימג'ים. שם משימה מת בטבלה לא
    יגרור מחיקה של תיקיית אימג' ששמה מזכיר אותו."""
    root = tmp_path / "images"
    write_image(root, MANIFEST_256)
    add_task(conn, "tsk_0930", "failed")
    (root / "capture-tsk_0930").mkdir()          # בלי נקודה בהתחלה
    (root / "img_7f3a91" / "notes.txt").write_text("x", encoding="utf-8")

    result = sweep(conn, root)

    assert result["swept"] == [] and result["kept"] == []
    assert (root / "img_7f3a91" / "manifest.json").is_file()
    assert (root / "capture-tsk_0930").is_dir()


def test_a_symlink_named_like_a_work_area_is_not_followed(conn, tmp_path):
    """קישור אינו תיקייה. בלי הבדיקה הזאת `rmtree` על קישור לתיקיית
    אימג' היה מוחק את מה שבצדו השני."""
    root = tmp_path / "images"
    write_image(root, MANIFEST_256)
    add_task(conn, "tsk_0930", "failed")
    try:
        (root / ".capture-tsk_0930").symlink_to(root / "img_7f3a91",
                                                target_is_directory=True)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip(
                f"{DECLARED_PREFIX}: אין הרשאת SeCreateSymbolicLinkPrivilege"
            )
        raise

    result = sweep(conn, root)

    assert result["swept"] == []
    assert result["kept"] == [(".capture-tsk_0930", "אינו תיקייה רגילה")]
    assert (root / "img_7f3a91" / "manifest.json").is_file()


def test_a_plain_file_named_like_a_work_area_is_not_swept(conn, tmp_path):
    root = tmp_path / "images"
    root.mkdir(parents=True)
    add_task(conn, "tsk_0930", "failed")
    (root / ".capture-tsk_0930").write_text("not a directory", encoding="utf-8")

    assert sweep(conn, root)["swept"] == []
    assert (root / ".capture-tsk_0930").is_file()


def test_a_delete_that_did_not_finish_is_not_counted_as_freed(conn, tmp_path,
                                                              monkeypatch):
    """עיקרון 5 על המחיקה עצמה: "לא הצלחנו למחוק" אינו "נמחק". ההצלחה
    נקבעת בקריאה חוזרת מהדיסק, לא בהיעדר חריגה."""
    root = tmp_path / "images"
    add_task(conn, "tsk_stuck", "failed")
    make_area(root, ".capture-tsk_stuck")
    monkeypatch.setattr("server.library.work_areas.shutil.rmtree",
                        lambda path: None)          # "הצליח", ולא מחק כלום

    result = sweep(conn, root)

    assert result["swept"] == [] and result["freed_bytes"] == 0
    assert result["kept"] == [(".capture-tsk_stuck", "המחיקה לא הושלמה")]
    assert "tsk_stuck" in events(conn)["work_area_kept"]


def test_a_missing_library_root_is_not_an_error(conn, tmp_path):
    assert plan(conn, tmp_path / "nope") == ([], [])


# --- הצמדות למה שהקליטה באמת כותבת --------------------------------------------


def test_the_sweep_recognises_the_directory_capture_actually_creates(tmp_path):
    """‏`staging_dir` ו-`CAPTURE_AREA` הם שני עותקים של אותה מוסכמת שם.
    אם אחד ישתנה, הסחיפה תפסיק לזהות יתומים — בשקט."""
    area = staging_dir(tmp_path, "tsk_0930")
    match = CAPTURE_AREA.match(area.name)
    assert match is not None and match.group(1) == "tsk_0930"


def test_human_bytes_reads_like_a_person_wrote_it():
    assert human_bytes(0) == "0B"
    assert human_bytes(533) == "533B"
    assert human_bytes(12202760) == "11.6MB"
    assert human_bytes(1478000000) == "1.4GB"


# --- דרך עליית השרת האמיתית ---------------------------------------------------


def test_the_server_sweeps_when_it_comes_up(tmp_path, images_root):
    """‏#88 מקצה לקצה: היתום נמחק בעליית השרת, האימג'ים לא נגעו, והשורה
    ביומן אומרת כמה נמחק וכמה מקום התפנה."""
    from server.app import create_app

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    before = connect(data_dir / "imagesrv.db")
    add_task(before, "tsk_0930", "failed")
    add_task(before, "tsk_live", "running")
    make_area(images_root, ".capture-tsk_0930", size=7168)
    make_area(images_root, ".capture-tsk_live")

    app = create_app(data_dir, images_root, "http://10.44.12.10:8080")

    assert not (images_root / ".capture-tsk_0930").exists()
    assert (images_root / ".capture-tsk_live" / "p1.esp.pcl.zst").is_file()
    assert {"img_7f3a91", "img_2c8e04"} <= set(app.state.ctx.library.scan())

    detail = events(app.state.ctx.conn)["work_area_swept"]
    assert "swept=1" in detail and "freed=7.0KB" in detail
