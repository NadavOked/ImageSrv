"""‏#502: קליטה על 4Kn רושמת גיאומטריה לוגית, לא 512 קשיח.

הבאג המקורי: `capture.sh` הכפיל סקטורים ב-512 בארבעה מקומות וכתב
`"sector_size":512`. על NVMe 4Kn ‏(`logical_block_size=4096`) מחיצה של
238GB נרשמה כ-30GB, ובשחזור נבראה כך.

‏#670 מודד `logical_block_size` ומשתמש בו בגיאומטריית המחיצות, ב-swap,
ב-min_target ובשדה המניפסט. `_disk_bytes` נשאר `× 512` במכוון: sysfs
`/size` הוא תמיד יחידות 512, גם על 4Kn (מוסכמת קרנל, לא ניחוש).

ה-DoD המקורי ביקש סירוב בקליטה. #670 בחר מדידה במקום סירוב; השחזור
עדיין מסרב ל-`sector_size != 512` (`apply_gpt`), ולכן הנזק המקורי —
מחיצות שמינית מהאמת — אינו יכול לקרות על אימג' שנקלט אחרי #670.

‏#958 הוא הצד השני של אותו מטבע: אימג' 512n (כל הספרייה) על **יעד**
‏4Kn. ‏`apply_gpt` השווה את המניפסט ל-512 קשיח ולא ל**דיסק שנכתב**, ולכן
המספרים — סקטורים — היו נכתבים פי 8 שגויים. מכאן והלאה היעד נמדד
ב-`blockdev --getss` ומושווה ל-`sector_size`, ושוני = סירוב בקול לפני
‏`--zap-all`, עם שני המספרים בשדה `error` של היעד.
"""

from __future__ import annotations

import json

from native import requires_native
from test_agent import BASH, posix, sh
from test_capture_refusals import CURL_SINK, capture_run
from test_restore_evidence import (
    build_box, log_of, rc_of, restore_run, state_of, target_error, wrote,
)

pytestmark = requires_native(("bash", BASH))

SECTOR = 4096
WRONG = 512
LINUX_START, LINUX_SECS = 2048, 204800
SWAP_START, SWAP_SECS = 206848, 4096
KERNEL_SECTORS = 2_097_152

#: sgdisk עם מחיצת linux ומחיצת swap — שני אתרי `size_bytes` של #502.
LINUX_AND_SWAP = (
    "#!/bin/sh\n"
    'if [ "$1" = "-i" ]; then\n'
    '  case "$2" in\n'
    "    1)\n"
    "      echo 'Partition GUID code: 0FC63DAF-8483-4772-8E79-3D69D8477DE4'\n"
    "      echo 'Partition unique GUID: 4C7B1E00-0000-4000-8000-000000000002'\n"
    "      echo 'First sector: 2048'\n"
    "      echo 'Partition size: 204800 sectors'\n"
    "      echo 'Attribute flags: 0000000000000000'\n"
    "      echo \"Partition name: 'Linux'\"\n"
    "      ;;\n"
    "    2)\n"
    "      echo 'Partition GUID code: 0657FD6D-A4AB-43C4-84E5-0933C84B4F4F'\n"
    "      echo 'Partition unique GUID: 4C7B1E00-0000-4000-8000-000000000003'\n"
    "      echo 'First sector: 206848'\n"
    "      echo 'Partition size: 4096 sectors'\n"
    "      echo 'Attribute flags: 0000000000000000'\n"
    "      echo \"Partition name: 'swap'\"\n"
    "      ;;\n"
    "  esac\n"
    "  exit 0\n"
    "fi\n"
    "echo 'Disk identifier (GUID): 4C7B1E00-0000-4000-8000-000000000001'\n"
    "echo 'Number  Start (sector)    End (sector)  Size       Code  Name'\n"
    "echo '   1            2048          206847   100.0 MiB   8300  Linux'\n"
    "echo '   2          206848          210943    16.0 MiB   8200  swap'\n"
)


def test_4kn_capture_writes_logical_geometry_not_512(tmp_path):
    """ארבעת האתרים מ-#502: sector_size, מחיצה, swap, min_target.

    הערך השגוי (סקטורים × 512) נשלל במפורש — אחרת הטסט היה עובר גם
    על הקיבוע הישן אם המספרים היו מתלכדים במקרה.
    """
    source = bytes(SECTOR) + b"EFI PART" + bytes(SECTOR - 8)
    logical = tmp_path / "box/sys/block/sda/queue/logical_block_size"
    sizef = tmp_path / "box/sys/block/sda/size"
    box, run, out = capture_run(
        tmp_path,
        image=source,
        stubs={
            "sgdisk": LINUX_AND_SWAP,
            "curl": CURL_SINK,
            # ברירת המחדל `cat >/dev/null` קוראת stdin, וב-while-read
            # על parts.txt היא בולעת את מחיצת ה-swap. partclone האמיתי
            # קורא מ-`-s` ולא מהצינור של הלולאה.
            "partclone.dd": "#!/bin/sh\nexit 0\n",
        },
        shell_pre=(
            f"printf '4096\\n' > {posix(logical)!r}; "
            f"printf '{KERNEL_SECTORS}\\n' > {posix(sizef)!r}; "
            # ‏#1900: מקור 4Kn נדחה עכשיו לפני הבייט הראשון (השרת מקבל רק 512,
            # ‏test_capture_gates.py). כאן נשמר חישוב הגיאומטריה — השער עקוף.
            "capture_source_reason() { :; }; "
        ),
    )
    assert out.strip().endswith("rc=0"), (
        out + (box / "capture.out").read_text(encoding="utf-8", errors="replace")
    )
    written = json.loads((run / "new-manifest.json").read_text(encoding="utf-8"))
    by_role = {p["role"]: p for p in written["partitions"]}
    linux, swap = by_role["linux"], by_role["swap"]
    end = (SWAP_START + SWAP_SECS) * SECTOR
    need = end + 262_144  # #1171: עותק הגיבוי של ה-GPT, בלי עיגול

    assert written["sector_size"] == SECTOR
    assert written["sector_size"] != WRONG
    assert linux["size_bytes"] == LINUX_SECS * SECTOR
    assert linux["size_bytes"] != LINUX_SECS * WRONG
    assert linux["start_sector"] == LINUX_START
    assert swap["size_bytes"] == SWAP_SECS * SECTOR
    assert swap["size_bytes"] != SWAP_SECS * WRONG
    assert swap["start_sector"] == SWAP_START
    assert written["min_target_bytes"] == need
    assert written["min_target_bytes"] != (
        (SWAP_START + SWAP_SECS) * WRONG + 262_144
    )
    # sysfs /size הוא יחידות 512 גם על 4Kn — לא logical_block_size.
    assert written["source_disk_bytes"] == KERNEL_SECTORS * WRONG
    assert written["source_disk_bytes"] != KERNEL_SECTORS * SECTOR


def test_apply_gpt_refuses_4kn_instead_of_carving_by_512(tmp_path):
    """הנזק המקורי היה בשחזור. apply_gpt מסרב ל-4096 לפני sgdisk -n.

    בלי הסירוב, `_end=$((start + size_bytes / 512 - 1))` היה בורא מחיצה
    פי 8 מהפריסה הלוגית של אימג' 4Kn שנקלט אחרי #670. היעד כאן 4Kn גם
    הוא — הסקטורים **תואמים**, והסירוב הוא של הקוד שעדיין סופר ב-512.
    """
    box, run, prelude = build_box(tmp_path, target_ss="4096")
    out = sh(
        prelude
        + "json_get() { "
        "case \"$2\" in .sector_size) echo 4096 ;; .scheme) echo gpt ;;"
        " *) echo null ;; esac; }; "
        'apply_gpt sda m.json; echo "rc=$?"'
    )
    assert rc_of(out) == "rc=1", out
    log = log_of(run)
    assert "unsupported sector size: 4096" in log, log
    calls = box / "sgdisk.calls"
    recorded = calls.read_text(encoding="utf-8") if calls.exists() else ""
    assert "--zap-all" not in recorded
    assert " -n " not in f" {recorded} "


# --- ‏#958: הסקטור של היעד מול זה שהאימג' נקלט ממנו -------------------------


def sgdisk_calls(box) -> str:
    calls = box / "sgdisk.calls"
    return calls.read_text(encoding="utf-8") if calls.exists() else ""


def blockdev_calls(box) -> str:
    calls = box / "blockdev.calls"
    return calls.read_text(encoding="utf-8") if calls.exists() else ""


def test_a_512_image_on_a_4kn_target_is_refused_before_zap_all(tmp_path):
    """הבאג של #958: מניפסט 512 (כל הספרייה) על NVMe 4Kn. עד כאן
    ‏`apply_gpt` השוותה את המניפסט ל-512 קשיח — לא לדיסק — והייתה
    ממשיכה ל---zap-all ולטבלה שכל מספר בה פי 8 שגוי. ההודעה נושאת את
    **שני** המספרים: "512" לבד אינו אבחנה."""
    box, run, prelude = build_box(tmp_path, target_ss="4096")
    out = sh(prelude + 'apply_gpt sda m.json; _rc=$?; echo "err=$PLAN_ERROR"; echo "rc=$_rc"')
    assert rc_of(out) == "rc=1", out
    assert "err=" in out and "512" in out and "4096" in out, out
    assert "לא ניתן לשחזר בלי המרה" in out, out
    log = log_of(run)
    assert "512" in log and "4096" in log, log
    recorded = sgdisk_calls(box)
    assert "--zap-all" not in recorded, recorded
    assert " -n " not in f" {recorded} ", recorded


def test_the_target_error_names_both_sector_sizes_end_to_end(tmp_path):
    """מקצה לקצה דרך `run_restore`: הסירוב נוחת בשדה `error` של היעד
    (מסלול הכשל של #106), המצב `failed`, ואף מחיצה לא נכתבה."""
    _box, run, prelude = build_box(tmp_path, target_ss="4096")
    out = sh(restore_run(prelude))
    assert rc_of(out) == "rc=1", out
    assert state_of(run) == "failed"
    err = target_error(run)
    assert "512" in err and "4096" in err, err
    assert "לא ניתן לשחזר בלי המרה" in err, err
    assert wrote(run) == [], "מחיצה נכתבה על דיסק בגודל סקטור אחר"


def test_a_matching_sector_size_is_measured_and_then_written(tmp_path):
    """‏512 מול 512 ממשיך לכתוב — והראיה ש**נמדד** ולא נמנע היא הקריאה
    ל-`blockdev --getss` עצמה, לפני ה---zap-all."""
    box, run, prelude = build_box(tmp_path, target_ss="512")
    out = sh(prelude + 'apply_gpt sda m.json; echo "rc=$?"')
    assert rc_of(out) == "rc=0", out + log_of(run)
    assert "--getss" in blockdev_calls(box), blockdev_calls(box)
    assert "--zap-all" in sgdisk_calls(box)


def test_an_unreadable_target_sector_size_is_a_refusal_not_a_pass(tmp_path):
    """עיקרון 5: ‏blockdev שנכשל אינו "הסקטור תואם". "לא הצלחנו לבדוק"
    הוא מצב משלו, והוא נעצר לפני בייט בדיוק כמו אי-התאמה."""
    box, run, prelude = build_box(tmp_path, target_ss=None)
    out = sh(prelude + 'apply_gpt sda m.json; echo "rc=$?"')
    assert rc_of(out) == "rc=1", out
    assert "לא ניתן לקרוא את גודל הסקטור" in log_of(run), log_of(run)
    assert "--zap-all" not in sgdisk_calls(box)


NO_SECTOR_SIZE = (
    "json_get() { "
    "case \"$2\" in .sector_size) echo null ;; .scheme) echo gpt ;;"
    " *) echo null ;; esac; }; "
)


def test_an_old_manifest_without_sector_size_is_read_as_512(tmp_path):
    """מניפסט בלי `sector_size` = 512: כל קליטה לפני #670 כתבה 512
    קשיח, ולכן זו לא הנחה אלא מה שהשדה החסר אמר בפועל. על יעד 512
    ממשיכים; על 4Kn מסרבים, וההודעה אומרת 512 ולא "null"."""
    box, run, prelude = build_box(tmp_path, target_ss="512")
    out = sh(prelude + NO_SECTOR_SIZE + 'apply_gpt sda m.json; echo "rc=$?"')
    assert rc_of(out) == "rc=0", out + log_of(run)
    assert "--zap-all" in sgdisk_calls(box)

    box, run, prelude = build_box(tmp_path / "4kn", target_ss="4096")
    out = sh(prelude + NO_SECTOR_SIZE + 'apply_gpt sda m.json; echo "err=$PLAN_ERROR"')
    assert "512" in out and "4096" in out and "null" not in out, out
    assert "--zap-all" not in sgdisk_calls(box)
