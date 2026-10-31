"""hivewrite — כתיבת ערך בודד ל-hive בלי לדרוס את שכניו (#33).

הבדיקות מקמפלות את agent/hivewrite.c ומריצות אותו מול hive אמיתי
(‏tests/fixtures/system-mini.hiv, נוצר עם make_fixture.py על Windows).
הנקודה הקריטית אינה "הערך נכתב" אלא "שאר ערכי המפתח שרדו" — ‏setval
של hivexsh היה מוחק את כל הגדרות ה-TCP/IP של המכונה המשוחזרת.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "agent" / "hivewrite.c"
FIXTURE = REPO / "tests" / "fixtures" / "system-mini.hiv"

PARAMS = r"ControlSet001\Services\Tcpip\Parameters"
COMPUTERNAME = r"ControlSet001\Control\ComputerName\ComputerName"

# ‏#33 — שם המחשב ב-Windows. חבילה שדילגה כאן נראתה בדיוק כמו חבילה
# שעברה, וזה מה שהחביא את הבאג חודשים (#52).
pytestmark = requires_native(
    "gcc", "hivexget", paths=("/usr/include/hivex.h",), posix=True,
    why="hivewrite צריך gcc, ‏libhivex-dev ו-hivexget",
)


@pytest.fixture(scope="module")
def hivewrite(tmp_path_factory):
    binary = tmp_path_factory.mktemp("build") / "hivewrite"
    subprocess.run(
        ["gcc", "-O2", "-Wall", "-Wextra", "-Werror",
         "-o", str(binary), str(SOURCE), "-lhivex"],
        check=True, stdin=subprocess.DEVNULL,
    )
    return binary


@pytest.fixture
def hive(tmp_path):
    dst = tmp_path / "SYSTEM"
    shutil.copyfile(FIXTURE, dst)
    return dst


def run(binary, *args):
    return subprocess.run(
        [str(binary), *map(str, args)], capture_output=True, text=True, stdin=subprocess.DEVNULL
    )


def hivexget(hive, path, name):
    result = subprocess.run(
        ["hivexget", str(hive), path, name], capture_output=True, text=True, stdin=subprocess.DEVNULL
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def value_listing(hive, path):
    """כל ערכי המפתח, שורה לערך — הבסיס להשוואת 'מי שרד'."""
    result = subprocess.run(
        ["hivexget", str(hive), path], capture_output=True, text=True, stdin=subprocess.DEVNULL
    )
    assert result.returncode == 0, result.stderr
    return sorted(result.stdout.splitlines())


def test_writes_both_tcpip_names(hivewrite, hive):
    result = run(hivewrite, hive, PARAMS,
                 "Hostname", "LAB1-05", "NV Hostname", "LAB1-05")
    assert result.returncode == 0, result.stderr
    assert hivexget(hive, PARAMS, "Hostname") == "LAB1-05"
    assert hivexget(hive, PARAMS, "NV Hostname") == "LAB1-05"


def test_every_sibling_value_survives(hivewrite, hive):
    """הסיבה שהעוזר קיים: המפתח מלא הגדרות TCP/IP שאסור לאבד."""
    before = value_listing(hive, PARAMS)
    result = run(hivewrite, hive, PARAMS,
                 "Hostname", "LAB1-05", "NV Hostname", "LAB1-05")
    assert result.returncode == 0, result.stderr
    after = value_listing(hive, PARAMS)

    changed = {line for line in before if "TEST-WIN11" in line}
    assert len(changed) == 2, "the fixture lost its expected shape"
    assert sorted(set(before) - changed
                  | {line.replace("TEST-WIN11", "LAB1-05") for line in changed}
                  ) == after


def test_single_value_key(hivewrite, hive):
    result = run(hivewrite, hive, COMPUTERNAME, "ComputerName", "LAB1-05")
    assert result.returncode == 0, result.stderr
    assert hivexget(hive, COMPUTERNAME, "ComputerName") == "LAB1-05"


def test_missing_key_fails_and_leaves_the_hive_alone(hivewrite, hive):
    original = FIXTURE.read_bytes()
    result = run(hivewrite, hive,
                 r"ControlSet001\Control\ComputerName\ActiveComputerName",
                 "ComputerName", "LAB1-05")
    assert result.returncode != 0
    assert "not found" in result.stderr
    assert hive.read_bytes() == original


def test_non_ascii_value_is_refused_before_commit(hivewrite, hive):
    original = FIXTURE.read_bytes()
    result = run(hivewrite, hive, PARAMS, "Hostname", "שם-בעברית")
    assert result.returncode != 0
    assert hive.read_bytes() == original


def test_dangling_pair_is_a_usage_error(hivewrite, hive):
    result = run(hivewrite, hive, PARAMS, "Hostname")
    assert result.returncode != 0
    assert "usage" in result.stderr


def test_get_reads_string_and_dword(hivewrite, hive):
    """‏-g הוא מסלול האימות של הסוכן — hivexget הוא wrapper ל-hivexsh
    שאינו רץ ב-initramfs, ולכן הקריאה-חזרה חייבת לעבור באותו בינארי."""
    result = run(hivewrite, "-g", hive, PARAMS, "Hostname")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "TEST-WIN11"
    result = run(hivewrite, "-g", hive, "Select", "Current")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"


def test_get_reads_back_what_set_wrote(hivewrite, hive):
    assert run(hivewrite, hive, PARAMS,
               "Hostname", "LAB1-05", "NV Hostname", "LAB1-05").returncode == 0
    result = run(hivewrite, "-g", hive, PARAMS, "NV Hostname")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "LAB1-05"


def test_get_missing_value_fails(hivewrite, hive):
    result = run(hivewrite, "-g", hive, PARAMS, "NoSuchValue")
    assert result.returncode != 0
    assert "not found" in result.stderr

# --- #720: -x כותב REG_EXPAND_SZ — DevicePath מכיל %SystemRoot% ---------------


def lsval(hive, path) -> str:
    """הפלט של `lsval` ב-hivexsh — פורמט regedit, שבו הסוג נראה:
    ‏`"Name"="text"` הוא REG_SZ ו-`"Name"=str(2):"…"` הוא REG_EXPAND_SZ (כך
    ‏hivexsh מדפיס אותו — נמדד במעבדה 16/09; `hex(2):` היה ניחוש בלי libhivex).
    ‏hivexget הוא wrapper ל-hivexsh, ולכן hivexsh קיים בכל מקום שהטסט רץ."""
    script = f"cd {path}\nlsval\n"
    result = subprocess.run(
        ["hivexsh", str(hive)], input=script, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_x_writes_an_expand_string_and_plain_writes_a_plain_one(hivewrite, hive):
    """בלי -x הערך הוא REG_SZ (כמו תמיד); עם -x הוא REG_EXPAND_SZ, ו-`-g`
    קורא את שניהם. זה מה שמאפשר ל-postdeploy.sh להוסיף ל-DevicePath
    בלי לשבור את `%SystemRoot%\inf` שכבר בו."""
    value = r"%SystemRoot%\inf;%SystemRoot%\..\ImageSrv\Drivers"
    assert run(hivewrite, "-x", hive, PARAMS, "DevicePath", value).returncode == 0
    assert run(hivewrite, hive, PARAMS, "PlainPath", value).returncode == 0
    listing = lsval(hive, PARAMS)
    assert '"DevicePath"=str(2):' in listing, listing
    assert f'"PlainPath"="{value}"' in listing.replace("\\\\", "\\"), listing
    for name in ("DevicePath", "PlainPath"):
        result = run(hivewrite, "-g", hive, PARAMS, name)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == value


def test_x_alone_is_a_usage_error(hivewrite, hive):
    result = run(hivewrite, "-x", hive, PARAMS, "DevicePath")
    assert result.returncode != 0 and "usage" in result.stderr
    result = run(hivewrite, "-x", "-g", hive, PARAMS, "DevicePath")
    assert result.returncode != 0 and "usage" in result.stderr

# --- #1331: -d כותב REG_DWORD — ‏Start של שירות אחסון in-box ------------------

STORAHCI = r"ControlSet001\Services\storahci"


def test_d_writes_a_dword_and_every_sibling_survives(hivewrite, hive):
    """‏Start=0 בתוך מפתח שמחזיק גם Type/ErrorControl/Group/ImagePath —
    אותה סיבה כמו ב-#33: ‏setval של hivexsh היה מוחק את השכנים."""
    before = value_listing(hive, STORAHCI)
    assert any(line.startswith('"Start"=dword:00000003') for line in before), before
    result = run(hivewrite, "-d", hive, STORAHCI, "Start", "0")
    assert result.returncode == 0, result.stderr
    after = value_listing(hive, STORAHCI)
    assert sorted(line for line in before if not line.startswith('"Start"')) == sorted(
        line for line in after if not line.startswith('"Start"'))
    assert '"Start"=dword:00000000' in after, after
    back = run(hivewrite, "-g", hive, STORAHCI, "Start")
    assert back.returncode == 0 and back.stdout.strip() == "0", back.stderr


def test_d_writes_a_value_named_zero_under_start_override(hivewrite, hive):
    key = STORAHCI + r"\StartOverride"
    assert run(hivewrite, "-g", hive, key, "0").stdout.strip() == "3"
    assert run(hivewrite, "-d", hive, key, "0", "0").returncode == 0
    assert run(hivewrite, "-g", hive, key, "0").stdout.strip() == "0"
    assert '"0"=dword:00000000' in lsval(hive, key)


@pytest.mark.parametrize("bad", ["-1", "0x10", " 7", "+3", "", "4294967296", "12345678901"])
def test_d_refuses_anything_but_a_plain_decimal_dword_before_commit(hivewrite, hive, bad):
    original = FIXTURE.read_bytes()
    result = run(hivewrite, "-d", hive, STORAHCI, "Start", bad)
    assert result.returncode != 0
    assert hive.read_bytes() == original


def test_d_alone_and_d_with_g_are_usage_errors(hivewrite, hive):
    result = run(hivewrite, "-d", hive, STORAHCI, "Start")
    assert result.returncode != 0 and "usage" in result.stderr
    result = run(hivewrite, "-d", "-g", hive, STORAHCI, "Start")
    assert result.returncode != 0 and "usage" in result.stderr
