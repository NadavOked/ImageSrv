"""‏#433 — רשומת Boot#### לווינדוס המשוחזר על UEFI (`agent/lib/postdeploy/bootentry.sh`).

שחזור מחיצות משחזר דיסק, לא NVRAM: הרשומה שהקושחה מעלה ממנה יושבת על
הלוח. ‏`ensure_boot_entry` רץ על ה-sh האמיתי עם efibootmgr מזויף שמחזיק
"NVRAM" בקובץ: מה שנבדק הוא **מה נקרא בחזרה** (‏`efibootmgr -v`) ולא קוד
היציאה של הכתיבה — ‏UEFI מתיר לקושחה להשמיט רשומה, ולכן הזיוף יודע גם
"לכתוב בהצלחה" בלי שהרשומה תופיע. שלושת המצבים שהבריף ביקש: אין
‏efivars (‏Legacy — דילוג בשם), אין רשומה (נוצרת ונקראת בחזרה + BootNext),
יש רשומה (משתמשים בה, בלי כפילות).

‏`SYSROOT` הוא תפר הבדיקה של efivars: בלעדיו הבדיקה הייתה מדברת עם
ה-NVRAM האמיתי של מכונת הבדיקות.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_captured_stdout import stub

pytestmark = requires_native(("bash", BASH))

ESP_UUID = "3a7f1c2e-9b4d-4e6f-8a10-2b3c4d5e6f70"
ESP = f"1|C12A7328-F81F-11D2-BA4B-00A0C93EC93B|esp|vfat|2048|104857600|p1|y|false|{ESP_UUID.upper()}|"
WIN = "2|EBD0A0A2-B9E5-4433-87C0-68B6B72699C7|windows|ntfs|206848|64424509440|p2|y|true|9a9a-1|"
LINUX = "2|0FC63DAF-8483-4772-8E79-3D69D8477DE4|linux|ext4|206848|64424509440|p2|y|true|9a9a-2|"
PXE = "Boot0001* UEFI PXEv4 (MAC:B42E99071AC4)\tPciRoot(0x0)/Pci(0x1,0x0)/MAC(b42e99071ac4,1)/IPv4(0.0.0.00.0.0.0,0,0)"
#: רשומה כפי שווינדוס עצמו כותב אותה — הנתיב באותיות גדולות.
WINDOWS_OWN = (f"Boot0000* Windows Boot Manager\tHD(1,GPT,{ESP_UUID},0x800,0x32000)"
               "/File(\\EFI\\MICROSOFT\\BOOT\\BOOTMGFW.EFI)WINDOWS.........")

#: efibootmgr מזויף. ה-NVRAM הוא הקובץ $FAKE_NVRAM (שורות Boot####, ואולי
#: שורת BootNext). ‏-C מוסיף רשומה — או, תחת FAKE_CREATE=drop, יוצא 0 בלי
#: להוסיף (הקושחה השמיטה); ‏-n קובע BootNext — או, תחת FAKE_NEXT=drop, לא.
EFIBOOTMGR = r'''
echo "$*" >> "$FAKE_NVRAM.calls"
case "$1" in
  -C)
    [ "${FAKE_CREATE:-ok}" = fail ] && { echo "Could not prepare Boot variable: No space left on device" >&2; exit 5; }
    shift; while [ $# -gt 0 ]; do case "$1" in -d) dev=$2;; -p) part=$2;; -L) label=$2;; -l) loader=$2;; esac; shift 2; done
    if [ "${FAKE_CREATE:-ok}" != drop ]; then
      n=$(grep -c '^Boot[0-9A-F]' "$FAKE_NVRAM")
      printf 'Boot%04X* %s\tHD(%s,GPT,%s,0x800,0x32000)/File(%s)\n' "$n" "$label" "$part" "$FAKE_PARTUUID" "$loader" >> "$FAKE_NVRAM"
    fi
    cat "$FAKE_NVRAM"; exit 0 ;;
  -n)
    [ "${FAKE_NEXT:-ok}" = drop ] || { grep -v '^BootNext:' "$FAKE_NVRAM" > "$FAKE_NVRAM.tmp"; { echo "BootNext: $2"; cat "$FAKE_NVRAM.tmp"; } > "$FAKE_NVRAM"; }
    cat "$FAKE_NVRAM"; exit 0 ;;
  -v|"")
    cat "$FAKE_NVRAM"; exit 0 ;;
  *) echo "unexpected efibootmgr $*" >&2; exit 64 ;;
esac
'''

BLKID = r'''
[ "$1" = -s ] && [ "$2" = PARTUUID ] || exit 2
[ -n "${FAKE_PARTUUID:-}" ] || exit 2
echo "$FAKE_PARTUUID"
'''


class Box:
    def __init__(self, tmp_path: Path, *, plan: tuple[str, ...] = (ESP, WIN), efivars: bool = True,
                 nvram: tuple[str, ...] = (PXE,), env: str = ""):
        self.run = tmp_path / "run"
        (self.run / "targets" / "sda").mkdir(parents=True)
        (self.run / "targets" / "sda" / "state").write_text("done\n")
        self.sys = tmp_path / "sys"
        if efivars:
            vars_dir = self.sys / "sys/firmware/efi/efivars"
            vars_dir.mkdir(parents=True)
            (vars_dir / "BootOrder-8be4df61-93ca-11d2-aa0d-00e098032b8c").write_bytes(b"\x07\x00\x00\x00\x01\x00")
        self.nvram = tmp_path / "nvram"
        self.nvram.write_text("".join(line + "\n" for line in nvram), newline="\n")
        stub_dir = tmp_path / "stubs"; stub_dir.mkdir()
        rows = "".join(r + "\n" for r in plan)
        (tmp_path / "plan").write_text(rows, newline="\n")
        self.script = (stub(stub_dir / "efibootmgr", EFIBOOTMGR) + stub(stub_dir / "blkid", BLKID)
                       + f'export PATH="$(cd {posix(stub_dir)!r} && pwd):$PATH"; '
                       f'export RUN_DIR={posix(self.run)!r} DEVROOT={posix(self.run)!r}/dev '
                       f'LOG_FILE={posix(self.run / "agent.log")!r} SYSROOT={posix(self.sys)!r} '
                       f'LIB_DIR={posix(AGENT)!r}/lib FAKE_NVRAM={posix(self.nvram)!r} '
                       f'FAKE_PARTUUID={ESP_UUID} {env}; '
                       f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/jsonq.sh; '
                       f'. {posix(AGENT)}/lib/restore/restore.sh; . {posix(AGENT)}/lib/core/progress.sh; '
                       f'. {posix(AGENT)}/lib/postdeploy/bootentry.sh; '
                       f'manifest_plan() {{ cat {posix(tmp_path / "plan")!r}; }}; ')

    def ensure(self) -> dict:
        out = subprocess.run(
            [BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + self.script
             + 'ensure_boot_entry sda /dev/null; echo "rc=$?"'],
            capture_output=True, text=True, encoding="utf-8",
            cwd=str(AGENT.parent), stdin=subprocess.DEVNULL,
        )
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip().endswith("rc=0"), "ensure_boot_entry לעולם אינו נכשל: " + out.stdout
        return json.loads((self.run / "bootentry.json").read_text(encoding="utf-8"))

    def nvram_lines(self) -> list[str]:
        return self.nvram.read_text().splitlines()

    def calls(self) -> list[str]:
        path = Path(str(self.nvram) + ".calls")
        return path.read_text().splitlines() if path.exists() else []

    def target_error(self) -> str | None:
        p = self.run / "targets/sda/error"
        return p.read_text(encoding="utf-8").strip() if p.exists() else None

    def target_state(self) -> str:
        return (self.run / "targets/sda/state").read_text().strip()


# --- שלושת המצבים ------------------------------------------------------------


def test_no_efivars_is_skipped_by_name_not_failed(tmp_path):
    """‏Legacy BIOS (מחשבי השיכפול, #391): אין API, ואומרים זאת."""
    box = Box(tmp_path, efivars=False)
    assert box.ensure() == {"state": "skipped", "entry": "", "reason": "no_efivars"}
    assert box.calls() == [] and box.target_error() is None


def test_a_missing_entry_is_created_read_back_and_made_boot_next(tmp_path):
    box = Box(tmp_path)
    result = box.ensure()
    assert result == {"state": "created", "entry": "0001"}
    lines = box.nvram_lines()
    assert any(f"HD(1,GPT,{ESP_UUID}," in l and "\\EFI\\Microsoft\\Boot\\bootmgfw.efi" in l for l in lines)
    assert "BootNext: 0001" in lines
    # ‏--create-only: ‏BootOrder לא נגעו בו — ‏PXE נשאר ראשון ברשת הזאת.
    assert any(c.startswith("-C ") for c in box.calls())
    assert not any(c.startswith("-c ") or "-o " in c for c in box.calls())
    assert box.target_error() is None and box.target_state() == "done"


def test_an_existing_entry_is_reused_and_no_duplicate_is_written(tmp_path):
    """הרשומה של ווינדוס עצמו, באותיות גדולות — NVRAM סופי, וכל שחזור
    שהיה מוסיף רשומה היה ממלא אותו."""
    box = Box(tmp_path, nvram=(WINDOWS_OWN, PXE))
    assert box.ensure() == {"state": "present", "entry": "0000"}
    assert not any(c.startswith("-C") for c in box.calls())
    assert sum("Windows Boot Manager" in l for l in box.nvram_lines()) == 1
    assert "BootNext: 0000" in box.nvram_lines()


# --- הראיה היא הקריאה בחזרה, לא קוד היציאה -----------------------------------


def test_a_create_that_exits_zero_but_does_not_read_back_is_a_failure(tmp_path):
    """‏R17: הקושחה רשאית להשמיט רשומה שאינה לרוחה. כתיבה אינה ראיה."""
    box = Box(tmp_path, env="FAKE_CREATE=drop")
    result = box.ensure()
    assert result["state"] == "failed" and "בקריאה בחזרה" in result["error"]
    assert box.target_state() == "done"                     # השחזור עצמו הושלם
    assert box.target_error() and "Boot####" in box.target_error()


def test_a_create_that_fails_is_a_failure_with_the_rc(tmp_path):
    box = Box(tmp_path, env="FAKE_CREATE=fail")
    result = box.ensure()
    assert result["state"] == "failed" and "rc=5" in result["error"]


def test_a_boot_next_that_does_not_read_back_is_a_failure_even_with_the_entry(tmp_path):
    box = Box(tmp_path, env="FAKE_NEXT=drop")
    result = box.ensure()
    assert result["state"] == "failed" and "BootNext" in result["error"]
    assert "created" in result["error"]                     # הרשומה כן נוצרה — ונאמר


def test_an_entry_for_another_disk_does_not_count(tmp_path):
    """אותו נתיב, ESP אחר (PARTUUID שונה) — זו רשומה של דיסק אחר, לא שלנו."""
    other = WINDOWS_OWN.replace(ESP_UUID, "00000000-0000-0000-0000-000000000000")
    box = Box(tmp_path, nvram=(other, PXE))
    assert box.ensure()["state"] == "created"


def test_a_failure_keeps_an_earlier_target_error(tmp_path):
    box = Box(tmp_path, env="FAKE_CREATE=drop")
    (box.run / "targets/sda/error").write_text("שם המחשב לא נכתב: x\n", encoding="utf-8", newline="\n")
    box.ensure()
    assert box.target_error().startswith("שם המחשב לא נכתב: x | ")


# --- מה לא נוגעים בו ----------------------------------------------------------


def test_a_linux_image_is_skipped_by_name(tmp_path):
    box = Box(tmp_path, plan=(ESP, LINUX))
    assert box.ensure() == {"state": "skipped", "entry": "", "reason": "not_windows"}
    assert box.calls() == []


def test_a_manifest_without_an_esp_is_a_failure_not_a_guess(tmp_path):
    box = Box(tmp_path, plan=(WIN,))
    result = box.ensure()
    assert result["state"] == "failed" and "ESP" in result["error"]
    assert box.calls() == []


def test_a_partuuid_that_cannot_be_read_is_a_failure(tmp_path):
    box = Box(tmp_path, env="FAKE_PARTUUID=")
    result = box.ensure()
    assert result["state"] == "failed" and "PARTUUID" in result["error"]
    assert box.calls() == []


def test_an_empty_efivars_directory_is_a_mount_that_failed(tmp_path):
    """‏init אומר "efivarfs would not mount" ומשאיר תיקייה ריקה — זו לא
    קושחה בלי משתנים, זו קריאה שנכשלה; לא כותבים לתוך כלום."""
    box = Box(tmp_path, efivars=False)
    (box.sys / "sys/firmware/efi/efivars").mkdir(parents=True)
    assert box.ensure()["reason"] == "no_efivars"


# --- החיבור למסלול השחזור -------------------------------------------------------


def test_the_stage_hook_runs_the_boot_entry_after_the_drivers():
    post = (AGENT / "lib" / "postdeploy" / "postdeploy.sh").read_text(encoding="utf-8")
    body = post[post.index("stage_drivers() {") : post.index("_stage_drivers() {")]
    assert body.index('_stage_drivers "$@"') < body.index('ensure_boot_entry "$@"')
    assert "done" not in body.split("return 0")[0].split('ensure_boot_entry "$@"')[1]   # #1935: done is the caller's
    # הטעינה היא ברשימה המפורשת של imagesrv-agent (test_every_lib_file_is_loaded_and_packed),
    # לפני postdeploy.sh שקורא ל-ensure_boot_entry — לא self-sourcing מתוך lib.
    main = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    assert main.index('"$LIB_DIR/bootentry.sh"') < main.index('"$LIB_DIR/postdeploy.sh"')


def test_the_builder_packs_efibootmgr():
    builder = (AGENT.parent / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    assert "efibootmgr" in builder[builder.index("BINARIES=(") : builder.index(")", builder.index("BINARIES=("))]
    assert "efibootmgr" in builder[builder.index("apt-get install") :]


@pytest.mark.parametrize("flag", ["-c ", " -o "])
def test_boot_order_is_never_rewritten(flag):
    """‏`-c` בלי ‏`-C` מקדים את הרשומה ל-BootOrder — וברשת הזאת PXE חייב
    להישאר ראשון, אחרת התחנה לא חוזרת ל-ImageSrv לעולם."""
    text = (AGENT / "lib" / "postdeploy" / "bootentry.sh").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.lstrip().startswith("efibootmgr"):
            assert flag not in line, line
