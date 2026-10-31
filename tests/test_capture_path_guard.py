"""גבול הנתיב בקליטה — שער ה-CodeQL הציבורי, 16/10 (py/path-injection).

שני קלטים מהרשת הלימודית הופכים לנתיב בשרת: מזהה המשימה (נתיב ה-URL)
ושם קובץ המחיצה (נתיב ה-URL של ההעלאה, ושדה `file` במניפסט). כאן נבדק
שכל צורה של יציאה מהתיקייה — ‏`../`, נתיב מוחלט, לוכסן הפוך, קידוד URL
ושורה חדשה בסוף השם — נדחית, בצורת השגיאה הקיימת, ולא נכתב דבר מחוץ
לאזור הביניים.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.library import capture  # noqa: E402
from server.core.tasks import TOKEN_HEADER, staging_dir  # noqa: E402

from test_capture import (  # noqa: E402
    PART_A, PART_B, make_task, manifest_for, setup_build_machine, task_token,
)

BAD_TASK_IDS = ["..", "../x", "../../etc", "a/b", "a\\b", "/etc", "C:\\x", "C:x",
                "tsk_1\x00", "", ".capture-x", "tsk 1", "tsk_1\n"]

BAD_FILE_NAMES = ["../p1.esp.pcl.zst", "../../p1.esp.pcl.zst", "/etc/p1.esp.pcl.zst",
                  "..\\p1.esp.pcl.zst", "sub/p1.esp.pcl.zst", "C:\\p1.esp.pcl.zst",
                  "p1.esp.pcl.zst\n", "p1.esp.pcl.zst\x00", ".", "..", ""]


# --- היחידות: מה שבונה את הנתיב ---------------------------------------------


@pytest.mark.parametrize("task_id", BAD_TASK_IDS)
def test_staging_dir_refuses_a_task_id_that_is_not_one_path_component(tmp_path, task_id):
    with pytest.raises(ValueError):
        staging_dir(tmp_path, task_id)


def test_staging_dir_is_a_direct_child_of_the_library_root(tmp_path):
    area = staging_dir(tmp_path, "tsk_0930")
    assert area.name == ".capture-tsk_0930"
    assert area.parent == tmp_path.resolve()


@pytest.mark.parametrize("name", BAD_FILE_NAMES)
def test_staged_file_refuses_anything_but_a_partition_file_in_the_folder(tmp_path, name):
    with pytest.raises(ValueError):
        capture.staged_file(tmp_path, name)


def test_staged_file_is_a_direct_child_of_the_folder(tmp_path):
    path = capture.staged_file(tmp_path, "p3.windows.pcl.zst")
    assert path == tmp_path.resolve() / "p3.windows.pcl.zst"


# --- דרך ה-HTTP: ההעלאה --------------------------------------------------------

ENCODED_NAMES = [
    "..%2Fp1.esp.pcl.zst",            # ‏../ מקודד
    "%2E%2E%2Fp1.esp.pcl.zst",        # גם הנקודות מקודדות
    "%2Fetc%2Fp1.esp.pcl.zst",        # נתיב מוחלט מקודד
    "..%5Cp1.esp.pcl.zst",            # לוכסן הפוך (Windows)
    "C:%5Cp1.esp.pcl.zst",            # כונן
    "p1.esp.pcl.zst%0A",              # שורה חדשה בסוף — ‏`$` של re.match היה מקבל אותה
    "..",
]


#: מטען שאינו קיים בשום מקום אחר — כך "נכתב משהו מחוץ לאזור הביניים" נבדק
#: לפי התוכן ולא לפי שם (בספריית הבדיקה כבר יש `p1.esp.pcl.zst` אמיתיים).
PROBE = b"path-guard-probe-1016"


def _probe_written_anywhere(root: Path) -> list[str]:
    """כל קובץ מתחת ל-tmp_path שהמטען נכתב אליו."""
    return [str(p) for p in root.rglob("*")
            if p.is_file() and p.stat().st_size == len(PROBE) and p.read_bytes() == PROBE]


@pytest.mark.parametrize("name", ENCODED_NAMES)
def test_an_upload_name_that_leaves_the_folder_is_refused(server, images_root, name):
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    r = server["anon"].put(f"/api/v1/capture/{created['id']}/files/{name}",
                           content=PROBE,
                           headers={TOKEN_HEADER: task_token(server, created["id"])})
    # 404 = הנתיב המפוענח כבר אינו רכיב אחד, ואף route אינו תואם.
    # ‏400 = הגיע ל-endpoint ונדחה ברשימה הלבנה — בהודעה הקיימת שלה.
    assert r.status_code in (400, 404), (r.status_code, r.text)
    if r.status_code == 400:
        assert r.json()["detail"] == "unexpected partition file name"
    assert _probe_written_anywhere(images_root.parent) == []


@pytest.mark.parametrize("task_id", ["..%2F..%2Fx", "%2E%2E", "..%5Cx", "tsk_x%0A"])
def test_a_task_id_that_leaves_the_library_is_refused(server, images_root, task_id):
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    r = server["anon"].put(f"/api/v1/capture/{task_id}/files/p1.esp.pcl.zst",
                           content=PROBE,
                           headers={TOKEN_HEADER: task_token(server, created["id"])})
    assert r.status_code == 404, (r.status_code, r.text)
    assert _probe_written_anywhere(images_root.parent) == []
    assert list(images_root.glob(".capture-*")) == []


# --- דרך ה-HTTP: המניפסט --------------------------------------------------------


@pytest.mark.parametrize("bad", ["../p1.esp.pcl.zst", "/tmp/p1.esp.pcl.zst",
                                 "..\\p1.esp.pcl.zst", "p1.esp.pcl.zst\n"])
def test_a_manifest_file_name_that_leaves_the_folder_is_refused(server, images_root, bad):
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    head = {TOKEN_HEADER: task_token(server, created["id"])}
    anon = server["anon"]
    for name, data in {"p1.esp.pcl.zst": PART_A, "p3.windows.pcl.zst": PART_B}.items():
        assert anon.put(f"/api/v1/capture/{created['id']}/files/{name}",
                        content=data, headers=head).status_code == 200
    manifest = manifest_for()
    manifest["partitions"][0]["file"] = bad
    r = anon.put(f"/api/v1/capture/{created['id']}/manifest",
                 content=json.dumps(manifest).encode(), headers=head)
    # שלוש השכבות שכבר קיימות, כל אחת בהודעה שלה: ‏`_validate` של הספרייה
    # (שם לא בטוח / תו בקרה) ו-`SAFE_FILE` של הקליטה.
    assert r.status_code == 400, (r.status_code, r.text)
    assert r.json()["detail"].startswith((
        "unexpected partition file name", "partition 1 has an unsafe file name",
        "a string field contains a control")), r.text
    assert not (images_root / created["image_id"]).exists()
