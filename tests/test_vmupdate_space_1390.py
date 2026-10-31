"""‏#1390 — בדיקת המקום של ה-VM לפי מה שנתפס בפועל.

ברזל, שרת ת"א 13/10: אימג' של 146.4GB דחוס סורב — "נדרשים 479.3GB (227.4 תפוס
במחיצות + 32.2 מרווח + 219.7 גרסה חדשה), פנויים 449.9". ‏`disk.raw` דליל (לוגי
500.1GB, ‏26GB תפוסים אחרי 9GB שחזור), והגרסה החדשה נכתבת דחוסה — לא פי 1.5.
וה-227.4 לא היה "תפוס": ‏`used_bytes` היה null ב-NTFS (‏#1368), והחישוב לקח את
גודל המחיצה בשקט וקרא לזה תפוס.
"""

from __future__ import annotations

import copy

import pytest

from conftest import write_image
from server.storage.disk_reserve import SYSTEM_RESERVE_BYTES
from test_vmupdate import SOURCE, FakeMachine, lab, refusal   # noqa: F401 — fixture

#: #1737: this module reasons about the system reserve -- it stays 10GiB here
#: (conftest `_host_free_space` sets it to 0 elsewhere).
pytestmark = pytest.mark.system_reserve

GB = 10**9
GIB = 1 << 30


def tel_aviv(*, measured: bool) -> dict:
    """הצורה של האימג' מ-13/10: ‏ESP מלא, שלוש מחיצות NTFS, ‏146.4GB דחוס."""
    manifest = copy.deepcopy(SOURCE)
    manifest.update(id="img_5790e4", name="Win11 Lab", total_compressed_bytes=int(146.4 * GB))
    esp = {**manifest["partitions"][0], "size_bytes": 100 * 1024**2, "used_bytes": 30 * 1024**2}
    # ‏#1407: גיאומטריה אמיתית — מחיצות עוקבות, בגודל של סקטורים שלמים.
    # בלי זה הספרייה מדלגת על המניפסט (חפיפה/לא מיושר) ואין מה לבדוק.
    start = esp["start_sector"] + esp["size_bytes"] // 512
    ntfs = []
    for index, size, used in ((2, int(0.5 * GB), int(0.1 * GB)), (3, int(226.3 * GB), int(190 * GB)),
                              (4, int(0.5 * GB), int(0.4 * GB))):
        size -= size % 512
        ntfs.append({**manifest["partitions"][1], "index": index, "start_sector": start,
                     "size_bytes": size, "used_bytes": used if measured else None,
                     "file": f"p{index}.pcl.zst"})
        start += size // 512
    manifest["partitions"] = [esp, *ntfs]
    return manifest


def start_or_refusal(lab, manifest, free):
    write_image(lab["root"], manifest)
    engine = lab["make"](FakeMachine(free=free))
    return engine, manifest["id"]


def test_the_tel_aviv_image_fits_on_the_tel_aviv_server(lab):
    """449.9GB פנויים: 227.4 (גודל המחיצות — שמרני) + 32.2 + 146.4 = 406 — נכנס."""
    engine, image = start_or_refusal(lab, tel_aviv(measured=False), int(449.9 * GB))
    engine._check_space(engine.library.get(image))   # אין סירוב


def test_the_new_version_is_counted_compressed_not_one_and_a_half_times(lab):
    manifest = tel_aviv(measured=True)
    used = sum(p["used_bytes"] for p in manifest["partitions"])
    # ‏#1737: ועוד הרזרבה למערכת — היא נוספת על מרווח הגדילה, לא בתוכו.
    need = used + 30 * GIB + manifest["total_compressed_bytes"] + SYSTEM_RESERVE_BYTES
    engine, image = start_or_refusal(lab, manifest, need - 1)
    exc = refusal(engine, image)
    assert exc.numbers == {"need_bytes": need, "free_bytes": need - 1}
    assert "תפוס במחיצות" in exc.message and "לא נמדד" not in exc.message
    exact = lab["make"](FakeMachine(free=need))
    exact._check_space(exact.library.get(image))   # בדיוק מה שצריך — אין סירוב


def test_partitions_without_used_bytes_count_their_full_size_and_say_so(lab):
    """לא ממציאים מספר (‏#1368): גודל המחיצה — שמרני — וההודעה אומרת היכן ולמה."""
    manifest = tel_aviv(measured=False)
    sizes = sum(p["size_bytes"] for p in manifest["partitions"][1:]) + manifest["partitions"][0]["used_bytes"]
    need = sizes + 30 * GIB + manifest["total_compressed_bytes"] + SYSTEM_RESERVE_BYTES
    engine, image = start_or_refusal(lab, manifest, 100 * GB)
    exc = refusal(engine, image)
    assert (exc.status, exc.reason) == (507, "disk_space")
    assert exc.numbers == {"need_bytes": need, "free_bytes": 100 * GB,
                           "unmeasured_partitions": ["p2", "p3", "p4"]}
    assert "גודל המחיצות" in exc.message and "תפוס במחיצות" not in exc.message
    assert "ב-p2, p3, p4 לא נמדד כמה תפוס" in exc.message and "הערכה שמרנית" in exc.message


def test_a_missing_used_bytes_is_not_a_measurement(lab):
    manifest = tel_aviv(measured=True)
    manifest["partitions"][2]["used_bytes"] = None
    engine, image = start_or_refusal(lab, manifest, 1 * GB)
    exc = refusal(engine, image)
    assert exc.numbers["unmeasured_partitions"] == ["p3"]


@pytest.mark.parametrize("bad", [-1, "84509376512", True])
def test_a_used_bytes_that_is_not_a_count_never_reaches_the_vm_update(lab, bad):
    """‏#1407 (77557596): ‏used_bytes שאינו מונה הוא מניפסט פגום — הספרייה
    דוחה אותו בשמו, והעדכון אינו מגיע לחשב עליו מקום."""
    from server.library.images import ImageLibrary

    manifest = tel_aviv(measured=True)
    manifest["partitions"][2]["used_bytes"] = bad
    assert ImageLibrary._validate(manifest) == "partition 3 has malformed used_bytes"
    engine, image = start_or_refusal(lab, manifest, 1 * GB)
    assert engine.library.get(image) is None
    exc = refusal(engine, image)
    assert exc.status == 404


# --- ‏#1737: גם השמירה — לא רק הפתיחה ------------------------------------------


@pytest.mark.parametrize("spare,saved", [(-1, False), (0, True)])
def test_saving_the_new_version_keeps_the_system_reserve(lab, spare, saved):
    """בין הפתיחה לשמירה עוברים ימים: דיסק העבודה גדל וקליטות נכנסו. הגרסה
    החדשה (כגודל המקור הדחוס) נכתבת רק אם אחריה נשארת הרזרבה; אחרת — כשל
    בשם, והדיסק נשמר להדלקה ולשמירה חוזרת אחרי שמפנים מקום."""
    from test_vmupdate import run_to_stopped, wait_for

    engine = run_to_stopped(lab)
    compressed = lab["library"].get("img_7f3a91")["total_compressed_bytes"]
    engine.machine._free = compressed + SYSTEM_RESERVE_BYTES + spare
    engine.save("noc", "Office 2024 v2")
    if saved:
        assert wait_for(engine, "saved")["result"]["image_id"]
        return
    status = wait_for(engine, "failed")
    assert status["error"]["reason"] == "disk_space" and status["disk_ready"] is True
    assert "נשמרים למערכת" in status["error"]["message"]
