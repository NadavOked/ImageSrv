"""‏#2363 (המשך #1821): הנחת הדרייברים לא כותבת לכוורת Windows שלא סונכרנה.

כוורת regf "מלוכלכת" — רצף ראשי (offset 4) שונה מהמשני (offset 8) — היא
כוורת ש-Windows התחיל לכתוב אליה ולא סיים. בעלייה הבאה הוא משחזר אותה
מ-`<hive>.LOG1/LOG2`, מעל מה שנכתב, ו-commit של hivex מציב את שני המספרים
שווים — כך שהסימן נמחק והקריאה החוזרת "מאשרת" ערך שאולי לא ייכנס לתוקף.
‏#2362 סגר את זה לשם המחשב (`hostname.sh`); כאן שני הכותבים האחרים:

* ‏`postdeploy.sh` — ‏DevicePath בכוורת **SOFTWARE**. מלוכלכת = ‏`failed` בשמו,
  **והדיסק לא נגע** (לא הועתקה חבילה, לא נמחקה תיקייה, ‏SYSTEM לא נקרא);
* ‏`bootstorage.sh` — ‏Start=0 בכוורת **SYSTEM**. מלוכלכת = ‏`boot.state=failed`
  בשמו; החבילות כבר הונחו ונשארות `staged` (כמו כל כשל אחר של 5א).

כותרת שלא נקראה היא סירוב, לא "נקייה" (עיקרון 5). לוגים לא ריקים ליד כוורת
**נקייה** אינם סירוב — ‏Windows מתעלם מהם (מפרט regf של Suhanov, ראו #2362).
"""

from __future__ import annotations

from native import requires_native
from test_agent import BASH
from test_postdeploy import ENTRY, STOCK, SVC, SYSTEM_RAID, Box

pytestmark = requires_native(("bash", BASH))


def _hive(seq1: int, seq2: int) -> bytes:
    return b"regf" + seq1.to_bytes(4, "little") + seq2.to_bytes(4, "little") + bytes(500)


UMOUNTS = {"umount": 'echo umount "$@" >> "$FAKE_SERVER/umounts"; exit 0'}


def _writes(box: Box) -> list[str]:
    return [c for c in box.calls() if not c.startswith("-g ")]


def test_a_dirty_software_hive_stops_the_staging_before_the_disk_is_touched(tmp_path):
    box = Box(tmp_path, software_head=_hive(41, 40), stubs=UMOUNTS)
    _, result = box.stage("sda")
    assert result["state"] == "failed" and result["packages"] == [], result
    assert "SOFTWARE" in result["error"] and "לא סונכרנה" in result["error"], result
    assert "41/40" in result["error"], result
    assert "boot" not in result, "SYSTEM לא אמור להיבדק כשהחבילות לא הונחו"
    assert _writes(box) == [], f"נכתב לכוורת מלוכלכת: {_writes(box)}"
    assert box.device_path() == STOCK
    assert box.staged_files() == {}, "לא הועתקה חבילה"
    assert box.sysval(f"{SVC}/storahci/Start") == "3"
    assert (box.server / "umounts").read_text().count("umount") == 1, "הכשל משחרר את המאונט"


def test_a_software_header_that_does_not_read_is_unchecked_not_clean(tmp_path):
    """עיקרון 5: כותרת קצרה (אין שמונה בייטים אחרי `regf`) — סירוב, לא כתיבה."""
    box = Box(tmp_path, software_head=b"regf")
    _, result = box.stage("sda")
    assert result["state"] == "failed", result
    assert "מספרי הרצף של כוורת SOFTWARE" in result["error"], result
    assert _writes(box) == [] and box.staged_files() == {}


def test_a_dirty_system_hive_is_a_named_boot_failure_and_start_is_not_written(tmp_path):
    box = Box(tmp_path, system_head=_hive(41, 40))
    _, result = box.stage("sda")
    assert result["state"] == "staged", "החבילות הונחו — התוצאה שלהן נשארת"
    assert box.device_path() == f"{STOCK};{ENTRY}"
    boot = result["boot"]
    assert (boot["state"], boot["service"]) == ("failed", "storahci"), boot
    assert "SYSTEM" in boot["error"] and "לא סונכרנה" in boot["error"] and "41/40" in boot["error"], boot
    assert not [c for c in box.calls() if c.startswith("-d ")], box.calls()
    assert box.sysval(f"{SVC}/storahci/Start") == "3"


def test_a_dirty_system_hive_is_not_already_even_when_the_values_read_zero(tmp_path):
    """מה שנקרא מכוורת מלוכלכת אינו מה ש-Windows יראה אחרי השחזור מהלוג."""
    system = {**SYSTEM_RAID, f"{SVC}/storahci/Start": "0", f"{SVC}/storahci/StartOverride/0": "0"}
    box = Box(tmp_path, system=system, system_head=_hive(9, 8))
    _, result = box.stage("sda")
    assert result["boot"]["state"] == "failed", result


def test_a_system_header_that_does_not_read_is_unchecked_not_clean(tmp_path):
    box = Box(tmp_path, system_head=b"regf")
    _, result = box.stage("sda")
    assert result["boot"]["state"] == "failed", result
    assert "מספרי הרצף של כוורת SYSTEM" in result["boot"]["error"], result
    assert not [c for c in box.calls() if c.startswith("-d ")]


def test_logs_beside_clean_hives_do_not_block_either_write(tmp_path):
    box = Box(tmp_path, software_head=_hive(40, 40), system_head=_hive(40, 40))
    config = box.win / "Windows/System32/config"
    for name in ("SOFTWARE.LOG1", "SOFTWARE.LOG2", "SYSTEM.LOG1", "SYSTEM.LOG2"):
        (config / name).write_bytes(b"regf" + bytes(4092))
    _, result = box.stage("sda")
    assert result == {"state": "staged", "packages": ["intel-nic", "ahci-fix"],
                      "boot": {"state": "enabled", "service": "storahci"}}, result
