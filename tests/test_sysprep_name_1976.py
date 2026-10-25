"""‏#1976: אימג' שעבר sysprep — ה-specialize דורס את השם שכתבנו ב-hive.

אימג' כללי (`sysprep /generalize`) מריץ באתחול הראשון את שלב ה-specialize,
והוא קובע את שם המחשב מקובץ התשובות: ‏`ComputerName` = `*`, או בלי
`ComputerName` בכלל, הוא שם אקראי — מעל מה ש-`hostname.sh` כתב ל-SYSTEM.
התיקון (`agent/lib/unattend.sh`): כשמצב האימג' (`Setup\\State\\ImageState`
בכוורת SOFTWARE) הוא GENERALIZE_RESEAL, השם נכתב גם לקובץ התשובות שה-
Setup יקרא. אימג' שלא עבר sysprep — הקובץ לא נגעים בו, וגם לא נוצר.

הראיה היא הקובץ עצמו אחרי הכתיבה, מפוענח כ-XML: ‏`ComputerName` יושב
**בתוך** רכיב Shell-Setup של שלב ה-specialize — לא סתם מחרוזת בקובץ.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from native import requires_native
from test_captured_stdout import box, only_json, sh
from test_smart import BASH

pytestmark = requires_native(("bash", BASH))

NS = "{urn:schemas-microsoft-com:unattend}"
GENERALIZED = "IMAGE_STATE_GENERALIZE_RESEAL_TO_OOBE"

#: ‏hivewrite מזויף: ‏ImageState מ-$IMAGE_STATE (ריק = הקריאה נכשלת),
#: הארכיטקטורה AMD64, וכל קריאה אחרת — השם שנכתב (אימות ה-hive עובר).
HIVEWRITE = """echo "$*" >> "$RUN_DIR/hivewrites"
if [ "$1" = "-g" ]; then
  case "$*" in
    *ImageState*) [ -n "$IMAGE_STATE" ] || exit 2; echo "$IMAGE_STATE";;
    *PROCESSOR_ARCHITECTURE*) echo AMD64;;
    *Select*Current*) echo 1;;
    *) echo LAB1-05;;
  esac
fi
exit 0"""

SHELL = ('<component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" '
         'publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS" '
         'xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State" '
         'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">')
OOBE = (f'    <settings pass="oobeSystem">\n        {SHELL}\n            <OOBE>\n'
        '                <HideEULAPage>true</HideEULAPage>\n            </OOBE>\n'
        '        </component>\n    </settings>\n')


def answer(specialize: str = "<ComputerName>*</ComputerName>", *, with_pass: bool = True) -> str:
    """קובץ תשובות כפי ש-Windows SIM שומר אותו (כולל CRLF)."""
    spec = (f'    <settings pass="specialize">\n        {SHELL}\n'
            f'            {specialize}\n'
            '            <TimeZone>Israel Standard Time</TimeZone>\n'
            '        </component>\n    </settings>\n') if with_pass else ""
    text = ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<unattend xmlns="urn:schemas-microsoft-com:unattend">\n'
            f'{spec}{OOBE}</unattend>\n')
    return text.replace("\n", "\r\n")


def windows(tmp_path: Path, state: str, files: dict[str, str | bytes]) -> tuple[str, Path]:
    run = tmp_path / "run"
    win = run / "win"
    (win / "Windows/System32/config").mkdir(parents=True)
    for hive in ("SYSTEM", "SOFTWARE"):
        (win / "Windows/System32/config" / hive).write_text("hive", encoding="utf-8")
    for rel, body in files.items():
        target = win / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            target.write_bytes(body)
        else:
            target.write_text(body, encoding="utf-8", newline="")
    script, run = box(tmp_path, {"ntfs-3g": "exit 0", "umount": "exit 0", "hivewrite": HIVEWRITE})
    return f"export IMAGE_STATE={state!r}; " + script, win


def name(script: str) -> dict:
    out = sh(script + "write_hostname sda /dev/null LAB1-05")
    return only_json(out)


def specialize_names(path: Path) -> list[str]:
    """כל ComputerName שה-specialize יקרא: בתוך Shell-Setup של השלב הזה."""
    root = ET.fromstring(path.read_bytes())
    names = []
    for settings in root.findall(f"{NS}settings"):
        if settings.get("pass") != "specialize":
            continue
        for comp in settings.findall(f"{NS}component"):
            if comp.get("name") == "Microsoft-Windows-Shell-Setup":
                names += [e.text for e in comp.findall(f"{NS}ComputerName")]
    return names


def all_names(path: Path) -> list[str]:
    return [e.text for e in ET.fromstring(path.read_bytes()).iter(f"{NS}ComputerName")]


# --- אימג' שעבר sysprep ------------------------------------------------------

def test_a_star_computername_gets_the_name_with_a_minimal_edit(tmp_path):
    """המקרה של ה-Issue: ‏`*` ב-Panther. רק הערך משתנה — CRLF ושאר הקובץ נשארים."""
    original = answer()
    script, win = windows(tmp_path, GENERALIZED, {"Windows/Panther/unattend.xml": original})
    result = name(script)
    assert result["ok"] is True, result
    f = win / "Windows/Panther/unattend.xml"
    assert specialize_names(f) == ["LAB1-05"]
    assert f.read_bytes() == original.replace("<ComputerName>*</ComputerName>",
                                              "<ComputerName>LAB1-05</ComputerName>").encode()


def test_an_answer_file_without_computername_gets_one_in_its_shell_setup(tmp_path):
    script, win = windows(tmp_path, GENERALIZED, {
        "Windows/Panther/unattend.xml": answer("<RegisteredOwner>College</RegisteredOwner>")})
    assert name(script)["ok"] is True
    f = win / "Windows/Panther/unattend.xml"
    assert specialize_names(f) == ["LAB1-05"]
    spec = [s for s in ET.fromstring(f.read_bytes()).findall(f"{NS}settings")
            if s.get("pass") == "specialize"][0]
    # לא רכיב Shell-Setup שני באותו שלב — זה קובץ ש-Setup עלול לסרב לו.
    assert len(spec.findall(f"{NS}component")) == 1
    assert spec.find(f"{NS}component/{NS}TimeZone").text == "Israel Standard Time"


def test_an_answer_file_without_a_specialize_pass_gets_one(tmp_path):
    script, win = windows(tmp_path, GENERALIZED, {
        "Windows/Panther/unattend.xml": answer(with_pass=False)})
    assert name(script)["ok"] is True
    f = win / "Windows/Panther/unattend.xml"
    assert specialize_names(f) == ["LAB1-05"]
    comp = ET.fromstring(f.read_bytes()).find(f"{NS}settings[@pass='specialize']/{NS}component")
    assert comp.get("processorArchitecture") == "amd64"
    # שלב ה-oobeSystem שהיה שם נשאר כמו שהוא.
    assert ET.fromstring(f.read_bytes()).find(f"{NS}settings[@pass='oobeSystem']") is not None


def test_a_generalized_image_without_an_answer_file_gets_one(tmp_path):
    """‏sysprep בלי /unattend: אין קובץ — וה-specialize נותן שם אקראי."""
    script, win = windows(tmp_path, GENERALIZED, {})
    assert name(script)["ok"] is True
    assert specialize_names(win / "Windows/Panther/unattend.xml") == ["LAB1-05"]


def test_the_file_setup_reads_is_the_one_edited(tmp_path):
    """רק ב-System32\\Sysprep (באות גדולה, כמו ש-Windows שומר): נערך שם, ולא נוצר
    קובץ ב-Panther שהיה מסתיר אותו — וגם את שאר ההגדרות שבו."""
    script, win = windows(tmp_path, "IMAGE_STATE_GENERALIZE_RESEAL_TO_AUDIT",
                          {"Windows/System32/Sysprep/Unattend.xml": answer()})
    assert name(script)["ok"] is True
    assert specialize_names(win / "Windows/System32/Sysprep/Unattend.xml") == ["LAB1-05"]
    assert not (win / "Windows/Panther/unattend.xml").exists()


def test_every_computername_in_the_file_gets_the_name(tmp_path):
    script, win = windows(tmp_path, GENERALIZED, {
        "Windows/Panther/unattend.xml": answer("<ComputerName />")})
    assert name(script)["ok"] is True
    assert all_names(win / "Windows/Panther/unattend.xml") == ["LAB1-05"]


# --- אימג' בלי sysprep: אין שינוי --------------------------------------------

def test_an_image_without_sysprep_is_not_touched(tmp_path):
    original = answer()
    script, win = windows(tmp_path, "IMAGE_STATE_COMPLETE",
                          {"Windows/Panther/unattend.xml": original})
    assert name(script)["ok"] is True
    assert (win / "Windows/Panther/unattend.xml").read_bytes() == original.encode()


def test_an_image_without_sysprep_gets_no_answer_file(tmp_path):
    script, win = windows(tmp_path, "IMAGE_STATE_COMPLETE", {})
    assert name(script)["ok"] is True
    assert not (win / "Windows/Panther").exists()


# --- "לא בדקנו" אינו "אין sysprep" (עיקרון 5) --------------------------------

def test_an_unreadable_image_state_is_a_named_failure(tmp_path):
    script, win = windows(tmp_path, "", {"Windows/Panther/unattend.xml": answer()})
    result = name(script)
    assert result["ok"] is False and result["code"] == "unattend_failed", result
    assert "ImageState" in result["error"]
    # ה-hive עצמו נכתב — הכשל נוקב במה שלא נעשה, לא מבטל את מה שכן.
    assert "ComputerName LAB1-05" in (win.parent / "hivewrites").read_text(encoding="utf-8")


def test_a_utf16_answer_file_is_a_named_failure_not_a_garbled_one(tmp_path):
    original = b"\xff\xfe" + answer().encode("utf-16-le")
    script, win = windows(tmp_path, GENERALIZED, {"Windows/Panther/unattend.xml": original})
    result = name(script)
    assert result["ok"] is False and "UTF-16" in result["error"], result
    assert (win / "Windows/Panther/unattend.xml").read_bytes() == original
