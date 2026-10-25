"""שני סירובים של פריסת הדיסק בקליטה — לפני בדיקת המקום ולפני הבייט הראשון.

‏#1824: דיסק דינמי (LDM על GPT). מחיצות LDM אינן Basic Data, ‏`_partition_role`
קרא להן `data`, ו-`partclone.dd` העתיק חצי ממערך דינמי כאילו היה דיסק — בלי
סירוב, בלי שם מחשב ובלי כיווץ.

‏#1822: ‏Windows בדיסק שאין בו ESP (ה-ESP על דיסק אחר). הקליטה "הצליחה",
והמשוחזרות לא עלו. ‏Windows על GPT עולה רק מ-ESP — ולכן סירוב. דיסק נתונים
ולינוקס בלי ESP (‏bios_grub) — נקלטים כמו קודם (מה שאסור לשבור ב-Issue).

הסיבה נמדדת במה שהקונסולה מקבלת (`targets[].error`), והקוד — במה שמסך הכשל
קורא (`capture.reason`, ‏#2052).
"""

from __future__ import annotations

import pytest

from native import requires_native
from test_agent import BASH
from test_capture_gates import CURL_LOG, _blkid_fs
from test_capture_refusals import capture_run, refusal_reason

pytestmark = requires_native(("bash", BASH))

ESP = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
MSR = "E3C9E316-0B5C-4DB8-817D-F92DF00215AE"
BASIC_DATA = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
BIOS_GRUB = "21686148-6449-6E6F-744E-656564454649"
LINUX = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"
LDM_METADATA = "5808C8AA-7E8F-42E0-85D2-E1E90434CFB3"
LDM_DATA = "AF9B60A0-1431-4F62-BC68-3311714A69AD"

#: בדיקת המקום עונה "refused" — מי שהגיע אליה עבר את שערי הפריסה.
CURL_STOP_AT_SPACE = CURL_LOG.replace('"status":"ok","reason":"space checked"',
                                      '"status":"refused","reason":"STOP-AT-SPACE"')
#: ‏ntfs-3g שמעגן "Windows": ‏Windows\\System32 בנקודת העיגון (‏_system_role).
NTFS3G_WINDOWS = '#!/bin/sh\nfor a; do m="$a"; done\nmkdir -p "$m/Windows/System32"\n'
#: ‏ntfs-3g שמעגן מחיצת נתונים: אין Windows\\System32.
NTFS3G_DATA = "#!/bin/sh\nexit 0\n"
UMOUNT_OK = '#!/bin/sh\nfor a; do m="$a"; done\nrm -rf "$m/Windows"\nexit 0\n'
PROBE_CLEAN = "#!/bin/sh\nexit 0\n"


def _sgdisk(parts: list[tuple[int, str]]) -> str:
    """‏sgdisk שמדפיס את הטבלה `parts` (‏index, ‏type GUID), ‏2048 סקטורים לכל אחת."""
    body = ['#!/bin/sh', 'if [ "$1" = "-i" ]; then', '  case "$2" in']
    for i, guid in parts:
        first = 2048 * i
        body += [f'  {i})',
                 f'    echo "Partition GUID code: {guid} (x)"',
                 f'    echo "Partition unique GUID: 4C7B1E00-0000-4000-8000-00000000000{i}"',
                 f'    echo "First sector: {first} (at x)"',
                 '    echo "Partition size: 2048 sectors (1.0 MiB)"',
                 '    echo "Attribute flags: 0000000000000000"', '    ;;']
    body += ['  esac', '  exit 0', 'fi',
             'echo "Disk identifier (GUID): 4C7B1E00-0000-4000-8000-000000000001"',
             'echo "Number  Start (sector)    End (sector)  Size       Code  Name"']
    body += [f'echo "   {i}   {2048 * i}   {2048 * i + 2047}   1.0 MiB   0700  x"' for i, _ in parts]
    return "\n".join(body) + "\n"


def _run(tmp_path, parts, *, ntfs3g=NTFS3G_WINDOWS, fs=None):
    stubs = {"sgdisk": _sgdisk(parts), "curl": CURL_STOP_AT_SPACE,
             "ntfs-3g": ntfs3g, "umount": UMOUNT_OK, "ntfs-3g.probe": PROBE_CLEAN}
    if fs:
        stubs["blkid"] = _blkid_fs(fs)
    return capture_run(tmp_path, stubs=stubs)


def _space_was_checked(run) -> bool:
    log = run / "curl.log"
    return log.exists() and "preflight" in log.read_text(encoding="utf-8")


def _code(run) -> str:
    return (run / "capture.reason").read_text(encoding="utf-8").strip()


# --- #1824: דיסק דינמי -----------------------------------------------------------


@pytest.mark.parametrize("ldm", [LDM_METADATA, LDM_DATA, LDM_DATA.lower()])
def test_a_dynamic_disk_is_refused_before_the_space_check(tmp_path, ldm):
    box, run, out = _run(tmp_path, [(1, ESP), (2, MSR), (3, ldm)])
    reason = refusal_reason(box, run, out)
    assert "דיסק דינמי" in reason and "Basic" in reason and "מחיצה 3" in reason, reason
    assert "/dev/" not in reason, reason
    assert _code(run) == "source_dynamic"
    assert not _space_was_checked(run), "בדיקת המקום רצה לפני הסירוב"


def test_an_unknown_non_ldm_type_is_still_data_and_reaches_the_space_check(tmp_path):
    """מה שאסור לשבור (#1824): ‏GUID לא מוכר שאינו LDM נשאר data."""
    box, run, out = _run(tmp_path, [(1, ESP), (2, "11111111-2222-3333-4444-555555555555")])
    assert "STOP-AT-SPACE" in refusal_reason(box, run, out)
    assert _space_was_checked(run)


# --- #1822: ‏Windows בלי ESP --------------------------------------------------------


def test_windows_on_a_disk_without_an_esp_is_refused_before_the_space_check(tmp_path):
    box, run, out = _run(tmp_path, [(1, MSR), (2, BASIC_DATA)], fs="ntfs")
    reason = refusal_reason(box, run, out)
    assert "Windows" in reason and "EFI" in reason and "מחיצה 2" in reason, reason
    assert "/dev/" not in reason, reason
    assert _code(run) == "source_no_esp"
    assert not _space_was_checked(run), "בדיקת המקום רצה לפני הסירוב"


def test_windows_with_its_esp_on_the_same_disk_is_not_refused(tmp_path):
    box, run, out = _run(tmp_path, [(1, ESP), (2, MSR), (3, BASIC_DATA)], fs="ntfs")
    assert "STOP-AT-SPACE" in refusal_reason(box, run, out)
    assert _space_was_checked(run)


def test_a_data_disk_without_an_esp_is_still_captured(tmp_path):
    """‏Basic Data בלי Windows\\System32 — דיסק נתונים, נקלט (#1822, "מה אסור")."""
    box, run, out = _run(tmp_path, [(1, BASIC_DATA)], ntfs3g=NTFS3G_DATA, fs="ntfs")
    assert "STOP-AT-SPACE" in refusal_reason(box, run, out)
    assert _space_was_checked(run)


def test_a_bios_linux_disk_without_an_esp_is_still_captured(tmp_path):
    box, run, out = _run(tmp_path, [(1, BIOS_GRUB), (2, LINUX)])
    assert "STOP-AT-SPACE" in refusal_reason(box, run, out)
    assert _space_was_checked(run)


def test_a_windows_check_that_could_not_mount_is_a_refusal_not_a_pass(tmp_path):
    """עיקרון 5: "לא הצלחנו לבדוק אם זה Windows" אינו "זה לא Windows"."""
    box, run, out = _run(tmp_path, [(1, BASIC_DATA)], ntfs3g="#!/bin/sh\nexit 1\n", fs="ntfs")
    reason = refusal_reason(box, run, out)
    assert "לא הצלחנו לעגן" in reason, reason
    assert _code(run) == "source_disk"
    assert not _space_was_checked(run)


# --- מסך הכשל: מה עושים, בלי "פנו ל-IT" -------------------------------------------


@pytest.mark.parametrize("code,expected", [("source_dynamic", "Basic"), ("source_no_esp", "EFI")])
def test_the_failure_screen_says_what_to_do_for_each_refusal(code, expected):
    from test_capture_failure_texts import capture_texts
    _, *steps = capture_texts("סיבת הכשל.", code)
    assert expected in steps[0], steps   # #2068: the sentence, then the buttons
    assert "פנו ל-IT" not in " ".join(steps) and "כבל" not in " ".join(steps), steps
