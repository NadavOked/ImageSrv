"""‏#85 — גזירת החותם של מטעני האתחול של האימג' בזמן הקליטה.

מה שנבדק כאן הוא `agent/lib/postdeploy/bootca.sh`: הוא פותח את ה-ESP, מוצא את מטעני
האתחול, קורא מהם את טבלת התעודות של Authenticode, ומדפיס את שני שדות
המניפסט — ‏`boot_ca` ו-`boot_ca_error`.

**שלושה מצבים, ולא שניים.** רשימה = נגזר; ‏`[]` = המטענים נקראו ואין
עליהם חתימה כלל; ‏`null` + סיבה = לא ניתן היה לגזור. שדה חסר ושדה שנכשל
הם שני מצבים שונים — זה בדיוק הדפוס של `used_bytes=0` (‏#298), שאמר גם
"ריק" וגם "לא מדדנו".

ה-PEים כאן נבנים בייט-בייט בפייתון, ולכן ההיסטים שהסוכן מחשב נבדקים
באמת: מספר שגוי יקרא בייטים אחרים ויקבל תשובה אחרת. ‏`openssl` מוחלף
בכפיל בגבול המערכת (זה הכלי החיצוני, לא הלוגיקה), והפלט שהכפיל מחזיר
הוא **הפלט שנמדד בפועל** מ-`openssl 3.5.7` על `shimx64.efi` של דביאן 13.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agentlib import lib_path
from native import requires_native

REPO = Path(__file__).resolve().parent.parent
BOOTCA = REPO / "agent" / "lib" / "postdeploy" / "bootca.sh"


def find_bash() -> str | None:
    if os.name == "nt":
        for candidate in (
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files\Git\bin\bash.exe",
        ):
            if Path(candidate).exists():
                return candidate
    return shutil.which("bash")


BASH = find_bash()
pytestmark = requires_native(("bash", BASH))


def posix(p: Path | str) -> str:
    return str(p).replace("\\", "/")


def path_entry(p: Path | str) -> str:
    """נתיב שמתאים לשבת בתוך `PATH`.

    ‏`C:/...` אינו כזה: הנקודתיים הן **המפריד** של PATH, ולכן ערך כזה
    נקרא כשני רכיבים — `C` וגם `/Users/...` — וכל כלי בספרייה פשוט לא
    נמצא. ‏Git Bash מבין את הצורה `/c/...`.
    """
    text = posix(p)
    if len(text) > 1 and text[1] == ":":
        text = "/" + text[0].lower() + text[2:]
    return text


# --- פלט openssl שנמדד ------------------------------------------------------
# ‏/boot/efi/EFI/debian/shimx64.efi על שרת המעבדה, ‏2026-09-04. הקובץ נושא
# **שתי** רשומות WIN_CERTIFICATE — טבלה של 19360 בייט, 9792 ואז 9568 —
# והקושחה מריצה אותו אם אחת מהן מתחברת ל-`db`.

CHAIN_2011 = """\
subject=C=US, ST=Washington, L=Redmond, O=Microsoft Corporation, CN=Microsoft Windows UEFI Driver Publisher
issuer=C=US, ST=Washington, L=Redmond, O=Microsoft Corporation, CN=Microsoft Corporation UEFI CA 2011

subject=C=US, ST=Washington, L=Redmond, O=Microsoft Corporation, CN=Microsoft Corporation UEFI CA 2011
issuer=C=US, ST=Washington, L=Redmond, O=Microsoft Corporation, CN=Microsoft Corporation Third Party Marketplace Root
"""

CHAIN_2023 = """\
subject=C=US, ST=Washington, L=Redmond, O=Microsoft Corporation, CN=Microsoft UEFI CA 2023 signer
issuer=C=US, O=Microsoft Corporation, CN=Microsoft UEFI CA 2023

subject=C=US, O=Microsoft Corporation, CN=Microsoft UEFI CA 2023
issuer=C=US, O=Microsoft Corporation, CN=Microsoft RSA Devices Root CA 2021
"""

#: החתימה של `bootmgfw.efi` שהפילה את הלנובו (‏#61/#85): ה-`db` שלו
#: מכיל רק את תעודות 2011.
CHAIN_WIN_2023 = """\
subject=C=US, ST=Washington, L=Redmond, O=Microsoft Corporation, CN=Windows
issuer=C=US, O=Microsoft Corporation, CN=Windows UEFI CA 2023

subject=C=US, O=Microsoft Corporation, CN=Windows UEFI CA 2023
issuer=C=US, O=Microsoft Corporation, CN=Microsoft RSA Devices Root CA 2021
"""

NAME_2011 = "Microsoft Corporation UEFI CA 2011"
NAME_2023 = "Microsoft UEFI CA 2023"
NAME_WIN_2023 = "Windows UEFI CA 2023"

#: הסמן שהכפיל מחפש בתוך ה-blob כדי לדעת איזו שרשרת להחזיר. הוא יושב
#: בתחילת ה-blob, ולכן `dd` שיחתוך במקום הלא נכון פשוט לא ימצא אותו.
MARKERS = {b"CA2011": CHAIN_2011, b"CA2023": CHAIN_2023, b"WIN2023": CHAIN_WIN_2023}


# --- בניית PE סינתטי --------------------------------------------------------


def blob(marker: bytes, size: int = 512) -> bytes:
    """גוף חתימה מזויף: הסמן ואז מילוי, באורך קבוע."""
    return marker + b"\x00" * (size - len(marker))


def make_pe(
    blobs: list[bytes],
    *,
    magic: int = 0x20B,
    dirs: int = 16,
    cert_type: int = 2,
    declared_size: int | None = None,
) -> bytes:
    """‏PE מינימלי עם טבלת תעודות. ‏`blobs` ריק = קובץ בלי חתימה כלל."""
    lfanew = 128
    dos = bytearray(lfanew)
    dos[0:2] = b"MZ"
    dos[60:64] = lfanew.to_bytes(4, "little")

    coff = b"PE\x00\x00" + bytes(20)
    dirs_at = 112 if magic == 0x20B else 96          # PE32+ מול PE32
    opt = bytearray(dirs_at)
    opt[0:2] = magic.to_bytes(2, "little")
    opt[dirs_at - 4:dirs_at] = dirs.to_bytes(4, "little")

    head = bytes(dos) + coff + bytes(opt)
    table_at = len(head) + 8 * 16
    assert table_at % 8 == 0

    table = bytearray()
    for body in blobs:
        wlen = 8 + len(body)
        table += wlen.to_bytes(4, "little")
        table += (0x0200).to_bytes(2, "little")      # wRevision 2.0
        table += cert_type.to_bytes(2, "little")
        table += body
        while len(table) % 8:
            table += b"\x00"

    size = len(table) if declared_size is None else declared_size
    ddir = bytearray(8 * 16)
    ddir[32:36] = (table_at if size else 0).to_bytes(4, "little")
    ddir[36:40] = size.to_bytes(4, "little")
    return head + bytes(ddir) + bytes(table)


def put(root: Path, rel: str, data: bytes) -> None:
    path = root / rel.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


# --- ארגז הכלים: PATH מלא בעטיפות sh, כדי ש"אין openssl" יהיה אמיתי -------

TOOLS = ("dd", "od", "tr", "awk", "grep", "date", "mkdir", "rm", "cat", "sed",
         "mount", "umount")


def toolbox(tmp_path: Path, openssl: str | None) -> Path:
    """ספריית PATH יחידה. כל כלי הוא עטיפת sh ולא העתק בינארי — עטיפה
    עוברת גם ב-Git Bash, שבו העתקת exe מנתקת אותו מה-DLL שלו.

    ‏`openssl=None` פירושו שהכלי **באמת** אינו ב-PATH: זו הבקרה השלילית
    "בלי הכלי המתאים", והיא חייבת לתת "לא ניתן היה לגזור" ולא קריסה.
    """
    box = tmp_path / "toolbox"
    box.mkdir(exist_ok=True)
    for tool in TOOLS:
        real = shutil.which(tool)
        if not real:
            continue
        wrapper = box / tool
        wrapper.write_text(f'#!/bin/sh\nexec {posix(real)!r} "$@"\n', encoding="utf-8")
        wrapper.chmod(0o755)
    # קודם מוחקים: ‏tmp_path משותף לכמה הרצות באותו טסט, וכפיל ששרד
    # מהרצה קודמת היה הופך את "אין כלי" ל"יש כלי" — בשקט.
    tool = box / "openssl"
    tool.unlink(missing_ok=True)
    if openssl is not None:
        tool.write_text(openssl, encoding="utf-8", newline="\n")
        tool.chmod(0o755)
    return box


def fake_openssl() -> str:
    """כפיל שמחזיר את הפלט שנמדד, לפי הסמן שבתוך ה-blob שנחתך."""
    lines = ["#!/bin/sh", 'f=""',
             'while [ $# -gt 0 ]; do', '  case "$1" in -in) f="$2" ;; esac',
             '  shift', 'done']
    for marker, chain in MARKERS.items():
        lines.append(f'if grep -q {marker.decode()} "$f" 2>/dev/null; then')
        lines.append(f"cat << 'CHAIN_EOF'\n{chain}CHAIN_EOF")
        lines.append("exit 0")
        lines.append("fi")
    lines.append('echo "unable to load PKCS7 object" >&2')
    lines.append("exit 1")
    return "\n".join(lines) + "\n"


BROKEN_OPENSSL = "#!/bin/sh\necho 'unable to load PKCS7 object' >&2\nexit 1\n"


# --- הרצה --------------------------------------------------------------------


#: הכפיל, מחושב פעם אחת. `openssl=None` בהרצה = הכלי אינו ב-PATH כלל.
FAKE_OPENSSL = fake_openssl()

#: אותו סדר טעינה של `imagesrv-agent` — ‏`bootca.sh` נשען על
#: ‏`node_is_block` שב-restore.sh, וטעינה חלקית היתה בודקת קוד אחר.
SOURCE_CHAIN = "".join(
    f". {posix(lib_path(name))!r}; "
    for name in ("common.sh", "waits.sh", "sysinfo.sh", "restore.sh", "bootca.sh")
)


def run_boot_ca_json(tmp_path: Path, esp: Path | str = "", *,
                     openssl: str | None = FAKE_OPENSSL,
                     node: str = "/dev/fakeesp") -> dict:
    """מריץ `boot_ca_json` ומחזיר את שני השדות כ-dict מפוענח.

    התוצאה נטענת ב-`json.loads` ולא נבדקת כטקסט: מה שהסוכן מדפיס נכנס
    למניפסט כמו שהוא, וכל מה שאינו JSON תקין שם הוא אימג' שהשרת ידחה
    בסוף הקליטה — אחרי שכל הבייטים כבר עברו.
    """
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    box = toolbox(tmp_path, openssl)
    script = (
        f"export PATH={path_entry(box)!r}; "
        f"export RUN_DIR={posix(run_dir)!r}; "
        f"export ESPROOT={posix(esp)!r}; "
        + SOURCE_CHAIN
        + f"boot_ca_json {node!r}"
    )
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, cwd=str(REPO))
    assert proc.returncode == 0, f"boot_ca_json יצא {proc.returncode}\n{proc.stderr}"
    return json.loads("{" + proc.stdout.strip() + "}")


def run_leaf_issuer(tmp_path: Path, text: str) -> str:
    box = toolbox(tmp_path, None)
    script = (
        f"export PATH={path_entry(box)!r}; "
        f"export RUN_DIR={posix(tmp_path)!r}; "
        + SOURCE_CHAIN
        + "_leaf_issuer"
    )
    # ‏input בבייטים ולא ב-text: עם `text=True` ווינדוס מתרגם כל `\n` שנכתב
    # לצינור ל-`\r\n`, וה-`awk` של `_leaf_issuer` מוציא `CN=` משורה שנושאת
    # ‏`\r` בסופה — כלומר שם CA עם תו נסתר (#491, אותו כשל כמו #479). הפלט
    # מפוענח כאן, כדי שלא יעבור universal-newlines ויסתיר את מה שהתקבל.
    proc = subprocess.run([BASH, "-c", script], input=text.encode("utf-8"),
                          capture_output=True, cwd=str(REPO))
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return proc.stdout.decode("utf-8", "replace").strip()


# --- הממצא המרכזי: קובץ אחד, שתי חתימות -------------------------------------


def test_two_signatures_on_one_file_give_two_names(tmp_path):
    """‏`bootmgfw.efi`/`shimx64.efi` נושאים יותר מחתימה אחת, והקושחה
    מריצה אותם אם **אחת** מהן מתחברת ל-`db`. שם יחיד היה מסתיר קובץ
    שעולה מצוין ממכונה שמכירה דווקא את השני."""
    esp = tmp_path / "esp"
    put(esp, "/EFI/debian/shimx64.efi",
        make_pe([blob(b"CA2011"), blob(b"CA2023")]))
    out = run_boot_ca_json(tmp_path, esp)
    assert out["boot_ca"] == [NAME_2011, NAME_2023]
    assert out["boot_ca_error"] is None


def test_every_loader_on_the_esp_is_recorded(tmp_path):
    """‏ESP עם Windows וגם עם shim — שניהם נספרים, בלי לייחד את Windows."""
    esp = tmp_path / "esp"
    put(esp, "/EFI/Microsoft/Boot/bootmgfw.efi", make_pe([blob(b"WIN2023")]))
    put(esp, "/EFI/debian/shimx64.efi", make_pe([blob(b"CA2011")]))
    out = run_boot_ca_json(tmp_path, esp)
    assert sorted(out["boot_ca"]) == sorted([NAME_WIN_2023, NAME_2011])
    assert out["boot_ca_error"] is None


def test_the_same_ca_is_listed_once(tmp_path):
    """אותו CA על שני מטענים — שורה אחת. הרשימה היא קבוצה, לא היסטוריה."""
    esp = tmp_path / "esp"
    put(esp, "/EFI/debian/shimx64.efi", make_pe([blob(b"CA2011")]))
    put(esp, "/EFI/BOOT/bootx64.efi", make_pe([blob(b"CA2011")]))
    out = run_boot_ca_json(tmp_path, esp)
    assert out["boot_ca"] == [NAME_2011]


def test_the_lenovo_case_is_named(tmp_path):
    """מה ש-#85 קיים בשבילו: ‏tiny11 חתום ב-`Windows UEFI CA 2023`,
    וה-`db` של הלנובו מכיל רק את תעודות 2011. השם חייב לצאת מהקליטה
    כדי שאפשר יהיה לדעת את זה לפני שמוחקים כונן."""
    esp = tmp_path / "esp"
    put(esp, "/EFI/Microsoft/Boot/bootmgfw.efi", make_pe([blob(b"WIN2023")]))
    assert run_boot_ca_json(tmp_path, esp)["boot_ca"] == [NAME_WIN_2023]


# --- שלושת המצבים -----------------------------------------------------------


def test_an_unsigned_loader_is_an_empty_list_and_not_an_error(tmp_path):
    """נקרא, ואין עליו חתימה. זו תשובה — ואינה "לא הצלחנו לקרוא"."""
    esp = tmp_path / "esp"
    put(esp, "/EFI/BOOT/bootx64.efi", make_pe([]))
    out = run_boot_ca_json(tmp_path, esp)
    assert out["boot_ca"] == []
    assert out["boot_ca_error"] is None


@pytest.mark.parametrize("magic", [0x10B, 0x20B], ids=["PE32", "PE32+"])
def test_both_pe_flavours_parse(tmp_path, magic):
    esp = tmp_path / "esp"
    put(esp, "/EFI/BOOT/bootx64.efi", make_pe([blob(b"CA2011")], magic=magic))
    assert run_boot_ca_json(tmp_path, esp)["boot_ca"] == [NAME_2011]


# --- בקרה שלילית: כל כישלון אומר את שמו, ואף אחד אינו משמיט שדה -----------


def _both_fields_present(out: dict, needle: str) -> None:
    assert set(out) == {"boot_ca", "boot_ca_error"}, "שדה נשמט מהמניפסט"
    assert out["boot_ca"] is None, "null ולא רשימה חלקית"
    assert isinstance(out["boot_ca_error"], str) and out["boot_ca_error"]
    assert needle in out["boot_ca_error"], out["boot_ca_error"]


def test_an_image_with_no_esp_says_so(tmp_path):
    """אימג' לינוקס בלי ESP. ‏`null` וסיבה — לא שדה חסר ולא קריסה."""
    _both_fields_present(run_boot_ca_json(tmp_path, node=""), "ESP")


def test_without_openssl_it_says_it_could_not(tmp_path):
    """הכלי אינו ב-PATH כלל — הבקרה השלילית השנייה שהמשימה דורשת.
    אותו ESP בדיוק נגזר כשהכלי כן שם, ולכן מה שהשתנה הוא הכלי ולא הקלט."""
    esp = tmp_path / "esp"
    put(esp, "/EFI/BOOT/bootx64.efi", make_pe([blob(b"CA2011")]))
    assert run_boot_ca_json(tmp_path, esp)["boot_ca"] == [NAME_2011]
    _both_fields_present(run_boot_ca_json(tmp_path, esp, openssl=None), "openssl")


def test_an_openssl_that_fails_is_not_an_unsigned_image(tmp_path):
    """הכלי קיים ונופל. "לא הצלחנו לקרוא" אינו "אין חתימה" (עיקרון 5)."""
    esp = tmp_path / "esp"
    put(esp, "/EFI/BOOT/bootx64.efi", make_pe([blob(b"CA2011")]))
    out = run_boot_ca_json(tmp_path, esp, openssl=BROKEN_OPENSSL)
    _both_fields_present(out, "/EFI/BOOT/bootx64.efi")


def test_a_node_that_is_not_a_block_device_is_refused(tmp_path):
    """בלי ESPROOT, ה-node חייב להיות התקן בלוקים. ‏`mount` על קובץ רגיל
    מקים לו `loop` מאחורי הגב — לעגן משהו אחר ולקרוא ממנו חתימה גרוע
    מלומר "לא ידענו"."""
    plain = tmp_path / "not-a-device"
    plain.write_bytes(b"\0" * 64)
    out = run_boot_ca_json(tmp_path, esp="", node=posix(plain))
    _both_fields_present(out, "התקן בלוקים")


def test_an_esp_without_a_loader_says_so(tmp_path):
    esp = tmp_path / "esp"
    (esp / "EFI").mkdir(parents=True)
    _both_fields_present(run_boot_ca_json(tmp_path, esp), "מטען אתחול")


def test_a_partial_read_is_never_a_short_list(tmp_path):
    """שני מטענים, אחד מהם בלתי-קריא. רשימה בת שם אחד היתה נראית שלמה,
    וה-CA שלא נקרא הוא בדיוק זה שהמכונה אולי כן מכירה."""
    esp = tmp_path / "esp"
    put(esp, "/EFI/debian/shimx64.efi", make_pe([blob(b"CA2011")]))
    put(esp, "/EFI/BOOT/bootx64.efi", b"this is not a PE file at all")
    out = run_boot_ca_json(tmp_path, esp)
    _both_fields_present(out, "/EFI/BOOT/bootx64.efi")


def test_garbage_in_place_of_a_loader_does_not_hang_or_crash(tmp_path):
    """קובץ שה-`e_lfanew` שלו הוא זבל: חסם השפיות מונע `dd` עם skip ענק."""
    esp = tmp_path / "esp"
    put(esp, "/EFI/BOOT/bootx64.efi", b"MZ" + b"\xff" * 200)
    _both_fields_present(run_boot_ca_json(tmp_path, esp), "/EFI/BOOT/bootx64.efi")


def test_an_absurd_table_offset_is_refused_and_not_read(tmp_path):
    """היסט uint32 מופרך שולח `dd bs=1` לקרוא ג'יגה-בייט בייט-בייט.
    התשובה היא "לא הצלחנו לקרוא", ולא קליטה שנתקעה."""
    pe = bytearray(make_pe([blob(b"CA2011")]))
    entry4 = 128 + 24 + 112 + 4 * 8          # DOS + PE/COFF + optional, ואז ערך 4
    pe[entry4:entry4 + 4] = (4_000_000_000).to_bytes(4, "little")
    esp = tmp_path / "esp"
    put(esp, "/EFI/BOOT/bootx64.efi", bytes(pe))
    _both_fields_present(run_boot_ca_json(tmp_path, esp), "/EFI/BOOT/bootx64.efi")


def test_fewer_than_five_data_directories_carries_no_table(tmp_path):
    """ערך 4 הוא הספרייה החמישית. קובץ שמצהיר על פחות אינו נושא טבלה,
    ולקרוא שם בכל זאת הוא לקרוא בייטים של משהו אחר."""
    esp = tmp_path / "esp"
    put(esp, "/EFI/BOOT/bootx64.efi", make_pe([blob(b"CA2011")], dirs=4))
    _both_fields_present(run_boot_ca_json(tmp_path, esp), "/EFI/BOOT/bootx64.efi")


def run_with_broken_dedup_awk(tmp_path: Path, esp: Path) -> dict:
    """כמו `run_boot_ca_json`, אבל ה-`awk` שכותב את רשימת החותמים המנוכה
    (‏bootca.sh:213) נכשל — בדיוק תרחיש ה-tmpfs המלא של #506.

    העטיפה נופלת **רק** כשמגיע אליה קובץ `.raw` (הארגומנט של קריאת
    ה-dedup), ולכן ה-`awk` של `_leaf_issuer` (קורא stdin, בלי ארגומנט
    קובץ) ו-`json_escape` ממשיכים לעבוד. משתנה אחד בכל בדיקה: אותו ESP
    בדיוק נגזר כשהכתיבה מצליחה, ומה שהשתנה הוא כישלון הכתיבה בלבד.
    """
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    box = toolbox(tmp_path, FAKE_OPENSSL)
    real_awk = shutil.which("awk")
    assert real_awk, "awk אינו זמין להרצת הבקרה"
    (box / "awk").write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do\n'
        "  case \"$a\" in\n"
        "    *.raw) echo 'awk: write error: No space left on device' >&2; exit 2 ;;\n"
        "  esac\n"
        "done\n"
        f"exec {posix(real_awk)!r} \"$@\"\n",
        encoding="utf-8", newline="\n")
    (box / "awk").chmod(0o755)
    script = (
        f"export PATH={path_entry(box)!r}; "
        f"export RUN_DIR={posix(run_dir)!r}; "
        f"export ESPROOT={posix(esp)!r}; "
        + SOURCE_CHAIN
        + "boot_ca_json '/dev/fakeesp'"
    )
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, cwd=str(REPO))
    assert proc.returncode == 0, f"boot_ca_json יצא {proc.returncode}\n{proc.stderr}"
    return json.loads("{" + proc.stdout.strip() + "}")


def test_a_failed_signer_list_write_is_not_an_unsigned_image(tmp_path):
    """‏#506 — ה-`awk` שכותב את רשימת החותמים נכשל (tmpfs מלא). קובץ ריק
    שיוצא 0 נקרא כ-`[]` + `error:null` — "נקרא, המטען אינו חתום" — במקום
    "לא הצלחנו לקרוא". על אימג' ווינדוס זו מסקנה הפוכה מהאמת (עיקרון 5)."""
    esp = tmp_path / "esp"
    put(esp, "/EFI/debian/shimx64.efi", make_pe([blob(b"CA2011"), blob(b"CA2023")]))
    # ראיה חיובית שהקלט עצמו נגזר כשהכתיבה מצליחה — כך שמה שהשתנה הוא הכתיבה.
    assert run_boot_ca_json(tmp_path, esp)["boot_ca"] == [NAME_2011, NAME_2023]
    _both_fields_present(run_with_broken_dedup_awk(tmp_path, esp), "רשימת החותמים")


def test_a_table_of_a_type_we_do_not_read_is_not_unsigned(tmp_path):
    """‏wCertificateType שאינו 2 אינו Authenticode. טבלה שיש בה תוכן
    ולא הוצאנו ממנה חותם היא כישלון קריאה, לא "אין חתימה"."""
    esp = tmp_path / "esp"
    put(esp, "/EFI/BOOT/bootx64.efi", make_pe([blob(b"CA2011")], cert_type=1))
    _both_fields_present(run_boot_ca_json(tmp_path, esp), "/EFI/BOOT/bootx64.efi")


# --- בחירת תעודת העלה -------------------------------------------------------


@pytest.mark.parametrize("chain,expected", [
    (CHAIN_2011, NAME_2011),
    (CHAIN_2023, NAME_2023),
    (CHAIN_WIN_2023, NAME_WIN_2023),
])
def test_the_ca_is_the_issuer_of_the_leaf(tmp_path, chain, expected):
    """לא הראשונה בשקית ולא השורש: ה-`db` מחזיק את תעודת הביניים,
    והעלה הוא זה שנחתם בה."""
    assert run_leaf_issuer(tmp_path, chain) == expected


def test_the_order_in_the_bag_does_not_decide(tmp_path):
    """אותה שקית בסדר הפוך נותנת אותו שם — הזיהוי אינו "הראשונה"."""
    blocks = [b for b in CHAIN_2011.strip().split("\n\n")]
    assert run_leaf_issuer(tmp_path, "\n\n".join(reversed(blocks)) + "\n") == NAME_2011


def test_a_bag_with_no_single_leaf_says_nothing(tmp_path):
    """שתי תעודות שאף אחת מהן אינה עלה — שקית שאיננו יודעים לקרוא.
    שתיקה כאן הופכת בהמשך ל-`null` וסיבה, ולא לניחוש."""
    both_are_issuers = (
        "subject=CN=A\nissuer=CN=B\n\nsubject=CN=B\nissuer=CN=A\n"
    )
    assert run_leaf_issuer(tmp_path, both_are_issuers) == ""


# --- הצמדה למניפסט ולקליטה --------------------------------------------------


def test_a_silent_boot_ca_still_leaves_a_valid_manifest(tmp_path):
    """הבקרה השלילית של החיווט עצמו: ‏`boot_ca_json` שלא הדפיס דבר —
    ‏lib שלא נטען, למשל — היה מייצר `"os":"",,` כלומר מניפסט שאינו
    ‏JSON, **אחרי** שכל הבייטים כבר עברו. כך נראה כישלון שמתגלה בסוף
    שעה של קריאת דיסק ואין לו שום קשר נראה לסיבה.
    """
    from test_capture_refusals import ONE_PARTITION, capture_run
    from test_image_fit import CURL_SINK

    box, run, out = capture_run(
        tmp_path, stubs={"sgdisk": ONE_PARTITION, "curl": CURL_SINK},
        shell_pre="boot_ca_json() { :; }; ")
    assert out.strip().endswith("rc=0"), out
    written = json.loads((run / "new-manifest.json").read_text(encoding="utf-8"))
    assert written["boot_ca"] is None
    assert isinstance(written["boot_ca_error"], str) and written["boot_ca_error"]


def test_capture_writes_both_fields_into_the_manifest():
    """שני השדות יוצאים מ-`capture_disk` עצמו, ולא רק מהפונקציה."""
    text = (REPO / "agent" / "lib" / "capture" / "capture.sh").read_text(encoding="utf-8")
    assert "_bootca=$(boot_ca_json " in text
    assert '"os":"%s",%s,' in text, "השדות אינם בשורת ה-printf של המניפסט"


def test_a_boot_ca_failure_never_fails_a_capture():
    """התעודה היא מידע, לא שער: אין ב-bootca.sh מסלול שמפיל קליטה."""
    text = BOOTCA.read_text(encoding="utf-8")
    assert "_capture_failed" not in text
    assert "die_local" not in text


def test_the_interfaces_document_says_why_boot_ca_is_a_list():
    """‏`docs/interfaces.md` הוא מקור האמת למה שעובר בין רכיבים — וכאן
    הוא צריך לשאת גם את **הנימוק**, לא רק את המבנה. בלי הנימוק השדה
    נראה כמו רשימה מיותרת, ומישהו יחזיר אותו למחרוזת: קובץ PE נושא
    יותר מחתימה אחת, והקושחה מקבלת אותו אם אחת מהן מתחברת ל-`db`.
    """
    doc = (REPO / "docs" / "interfaces.md").read_text(encoding="utf-8")
    assert '"boot_ca"' in doc, "השדה עצמו אינו מתועד"
    assert '"boot_ca_error"' in doc, "שדה הסיבה אינו מתועד"
    assert "#85" in doc, "השינוי אינו מקושר ל-Issue"
    assert "WIN_CERTIFICATE" in doc, "המדידה שהכריעה על רשימה אינה במסמך"


def test_the_paths_follow_the_boot_menu():
    """אותם נתיבים שהתפריט משרשר אליהם — אחרת נגזור חתימה של קובץ
    שלא ירוץ, או נחמיץ את זה שכן."""
    from boot.grub_menu import LOCAL_BOOT_PATHS

    listed = set(BOOTCA.read_text(encoding="utf-8").replace('"', " ").split())
    missing = [p for p in LOCAL_BOOT_PATHS if p not in listed]
    assert not missing, f"נתיבי אתחול שהתפריט מנסה ו-bootca.sh לא: {missing}"
