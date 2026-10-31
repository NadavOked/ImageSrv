"""#2068/#2084 (מוקאפ 09, נדב 24/10): מסך כשל עושה את הצעד הבא בכפתור באותו מסך.

"מסך כשל צריך להסתפק במה שאפשר לעשות בלי לעבור למסך אחר." לכן:

* אף טקסט במסך כשל לא שולח "לאיש IT" או "לטכנאי" -- מי שעומד מול המסך הוא
  איש ה-IT. במקומם משפט שאומר מה קרה, וכפתור (`fail_btn=`) שעושה את הצעד.
* אין קופסת "פרטים טכניים" (הלוג המלא במגירת המכונה בקונסולה).
* דיסק נקרא "מגירה N · דיסק N", אף פעם לא `sdc`.
* הכפתור כותב `fail.action` ליד קובץ המצב, והסוכן (`hold.sh hold_action`) מחליט:
  כיבוי, אתחול, חזרה להמתנה (משכפל), דיווח עכשיו, SMART של המקור, Windows.

הבדיקות התנהגותיות: הסוכן האמיתי (bash) כותב את הרשומות ומבצע את הפעולות מול
פונקציות מערכת מזויפות; הגואי האמיתי מצייר את הכפתורים (מעבדה בלבד).
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import pytest

from test_agent import AGENT, BASH, posix
from test_failure_screen_1569 import GUI_BUILD, run_agent, values
from test_cloner_gui import _build_gui, _count_color, _png, _png_rgb, _token

pytestmark = pytest.mark.skipif(BASH is None, reason="bash נדרש לסוכן ה-POSIX")

#: מה שאסור שיופיע במסך כשל (#2068, ‏#2084)
FORBIDDEN = ("פנו ל-IT", "לאיש IT", "לטכנאי", "טכנאי", "פרטים טכניים")
#: הפעולות שהסוכן יודע לבצע (hold.sh hold_action) -- כפתור שאינו כאן לא עושה דבר
ACTIONS = {"off", "retry", "reboot", "standby", "beat", "smart", "windows"}

HOLD_LINES = [
    ("all drawers skipped", "cloner"), ("no drawer completed", "cloner"),
    ("the disk was skipped -- nothing written", "build"), ("restore did not complete", "build"),
    ("restore cancelled, but the partition table was not wiped", "build"),
    ("capture did not complete", "build"), ("capture did not complete, and the server was not told", "build"),
    ("direct deployment did not complete", "build"), ("the work finished but the server never confirmed it", "build"),
    ("something nobody wrote a case for", "build"),
]


def _screen_text(recs) -> str:
    return "\n".join(v for k, v in recs if k != "fail_code")   # fail_code is not drawn (#2068)


@pytest.mark.parametrize("english,role", HOLD_LINES)
def test_every_hold_screen_has_a_button_and_never_sends_to_it(tmp_path, english, role):
    r = run_agent(tmp_path, f'ui_error_hold {shlex.quote(english)} hold_beat', env=f"D_ROLE={role}",
                  targets={"sda": ("failed", "write: short write"), "sdz": ("failed", "write: I/O error")})
    text = _screen_text(r["recs"])
    assert values(r["recs"], "fail"), r["out"]
    for bad in FORBIDDEN:
        assert bad not in text, (english, bad, text)
    btns = values(r["recs"], "fail_btn")
    assert btns, f"מסך כשל בלי כפתור: {english}"
    for b in btns:
        action, label = b.split("|", 1)
        assert action in ACTIONS and label, b
    assert "sdz" not in text and "sda" not in text, text   # #2084: a disk by its drawer, never its device


@pytest.mark.parametrize("code", ["network_server", "source_ntfs", "source_bitlocker", "source_dynamic",
                                  "source_no_esp", "source_disk", "unknown"])
def test_every_capture_reason_has_a_button_and_never_sends_to_it(tmp_path, code):
    r = run_agent(tmp_path, f'echo {code} > "$RUN_DIR/capture.reason"; ui_error_hold "capture did not complete" hold_beat',
                  env="D_ROLE=build", targets={"sda": ("failed", "x")})
    text = _screen_text(r["recs"])
    for bad in FORBIDDEN:
        assert bad not in text, (code, bad)
    assert values(r["recs"], "fail_btn"), code


def test_the_drawers_screen_is_the_mockup(tmp_path):
    """מוקאפ 09 סעיף 2: המשפט, השורה האפורה, "חזרה להמתנה לסבב" (Enter) ו"כבה את המחשב"."""
    r = run_agent(tmp_path, 'ui_error_hold "no drawer completed" hold_beat', env="D_ROLE=cloner",
                  targets={"sda": ("failed", "write: short write"), "sdb": ("failed", "x")})
    assert values(r["recs"], "fail_step") == [
        "הסיבה של כל מגירה כתובה ברשימה. בדקו שהדיסקים יושבים חזק ושהכבלים מחוברים.",
        'המחשב נשאר דלוק ומדווח לשרת. "חזרה להמתנה" מצרף אותו לסבב הבא בלי לאתחל.']
    assert values(r["recs"], "fail_btn") == ["standby|חזרה להמתנה לסבב", "off|כבה את המחשב"]


def test_die_local_offers_reboot_now_and_says_when(tmp_path):
    r = run_agent(tmp_path, 'die_local "server unreachable"', env="D_ROLE=build")
    assert values(r["recs"], "fail_step") == ["המחשב יאתחל בעוד 10 שניות, והשרת יחליט מאיפה יעלה."]
    assert values(r["recs"], "fail_btn") == ["reboot|אתחל עכשיו"]


def test_the_failure_texts_in_the_sources_never_send_to_it():
    for path in ("lib/gui/failscreen.sh", "lib/gui/srgui.sh", "lib/restore/single_restore.sh"):
        code = "\n".join(ln for ln in (AGENT / path).read_text(encoding="utf-8").splitlines()
                         if not ln.lstrip().startswith("#"))
        for bad in ("פנו ל-IT", "לטכנאי", "עד שמגיע טכנאי"):
            assert bad not in code, (path, bad)


# --- הסוכן מבצע את הכפתור ----------------------------------------------------------------

STUBS = (
    'safe_poweroff() { echo "POWEROFF $1"; return ${OFF_RC:-0}; }; reboot() { echo "REBOOT $*"; }; sync() { :; }; '
    'boot_next_local() { echo "${BOOT_NEXT:-skipped:אין efivars}"; case "${BOOT_NEXT:-}" in set:*) return 0;; esac; return 1; }; '
    'smart_probe() { echo "${SMART_V:-ok}"; }; smart_field() { echo pending_sectors; }; '
    '. ' + shlex.quote(posix(AGENT / "lib/smart/smart.sh")) + ' 2>/dev/null; '
    'smart_probe() { echo "${SMART_V:-ok}"; }; smart_field() { echo pending_sectors; }; '
)


def _press(tmp_path: Path, action: str, *, role: str = "build", env: str = "", english: str = "capture did not complete"):
    """הסוכן האמיתי מצייר את המסך, הגואי "לוחץ" (fail.action), ו-hold_action אחד רץ."""
    script = (f'ui_error_hold {shlex.quote(english)} hold_beat >/dev/null; mkdir -p "$RUN_DIR/gui"; '
              f'echo {action} > "$RUN_DIR/gui/fail.action"; {env} {STUBS} hold_action; echo "ACTION rc=$?"')
    return run_agent(tmp_path, script, env=f"D_ROLE={role}", targets={"sda": ("failed", "x")})


def test_power_off_powers_off_and_a_refusal_is_said_on_the_screen(tmp_path):
    r = _press(tmp_path, "off")
    assert "POWEROFF failure screen: power off" in r["out"] and "ACTION rc=1" in r["out"], r["out"]
    assert not (r["run"] / "gui" / "fail.action").exists(), "הלחיצה לא נלקחה"
    r = _press(tmp_path / "b", "off", env="OFF_RC=1;")
    assert any("Wake-on-LAN" in v for v in values(r["recs"], "fail_step")), "כיבוי שסורב לא נאמר במסך"


def test_retry_and_reboot_reboot(tmp_path):
    for i, a in enumerate(("retry", "reboot")):
        assert "REBOOT -f" in _press(tmp_path / str(i), a)["out"]


def test_back_to_waiting_leaves_the_hold_on_a_cloner_only(tmp_path):
    r = _press(tmp_path, "standby", role="cloner", english="no drawer completed")
    assert "ACTION rc=0" in r["out"], r["out"]
    assert not (r["run"] / "fail.state").exists(), "המסך נשאר אחרי החזרה להמתנה"
    r = _press(tmp_path / "b", "standby", role="build")
    assert "ACTION rc=1" in r["out"] and (r["run"] / "fail.state").exists()


def test_report_now_beats_at_once(tmp_path):
    assert "ACTION rc=2" in _press(tmp_path, "beat", english="the work finished but the server never confirmed it")["out"]


def test_check_the_disk_says_its_smart_on_the_same_screen(tmp_path):
    r = _press(tmp_path, "smart", env="SMART_V=fail;")
    steps = values(r["recs"], "fail_step")
    assert steps[-1].startswith("דיסק המקור: SMART תקול"), steps
    r = _press(tmp_path / "ok", "smart")
    assert values(r["recs"], "fail_step")[-1] == "SMART של דיסק המקור תקין."


def test_start_windows_sets_bootnext_or_says_why_not(tmp_path):
    r = _press(tmp_path, "windows", env="BOOT_NEXT=set:Boot0001;")
    assert "REBOOT -f" in r["out"], r["out"]
    r = _press(tmp_path / "b", "windows")
    assert "REBOOT" not in r["out"]
    assert "אין efivars" in values(r["recs"], "fail_step")[-1]   # principle 5: "could not" is said


def test_die_local_reboots_at_once_on_the_button(tmp_path):
    """‏`fail_fatal` מחכה FAIL_SHOW_S שניות -- "אתחל עכשיו" מקצר את ההמתנה."""
    fs = AGENT / "lib" / "gui" / "failscreen.sh"
    script = (f'. {shlex.quote(posix(fs))}; RUN_DIR={shlex.quote(posix(tmp_path))}; mkdir -p "$RUN_DIR/gui"; '
              'FAIL_SHOW_S=30; fail_screen() { :; }; echo reboot > "$RUN_DIR/gui/fail.action"; '
              'start=$(date +%s); fail_fatal x; echo "WAITED $(( $(date +%s) - start ))"')
    out = subprocess.run([BASH, "-c", script], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60).stdout
    assert "WAITED 0" in out or "WAITED 1" in out, out


# --- הגואי מצייר את הכפתורים (מעבדה) ------------------------------------------------------


@GUI_BUILD
def test_the_gui_draws_the_buttons_of_the_records(tmp_path):
    """אותן רשומות, עם ובלי `fail_btn=`: עם הכפתורים יש כפתור ראשי מלא (‏--clr-action),
    ובלעדיהם אין. גואי שמתעלם מ-fail_btn מצייר את שתיהן זהות."""
    binary = _build_gui(tmp_path)
    r = run_agent(tmp_path, 'ui_error_hold "no drawer completed" hold_beat', env="D_ROLE=cloner",
                  targets={"sda": ("failed", "write: short write"), "sdb": ("failed", "x")})
    full = (r["run"] / "fail.state").read_text(encoding="utf-8") + "message=x|y\nhello_age=4\nhello_rc=200\n"
    with_b, without = tmp_path / "with.txt", tmp_path / "without.txt"
    with_b.write_text(full, encoding="utf-8", newline="\n")
    without.write_text("".join(ln + "\n" for ln in full.splitlines() if not ln.startswith("fail_btn=")),
                       encoding="utf-8", newline="\n")
    _png(binary, tmp_path / "a", with_b)
    _png(binary, tmp_path / "b", without)
    w, h, ch, pa = _png_rgb(tmp_path / "a-failure-light.png")
    _, _, _, pb = _png_rgb(tmp_path / "b-failure-light.png")
    action = _token("--clr-action")
    assert _count_color(pa, ch, action) - _count_color(pb, ch, action) > 3000, "אין כפתור ראשי מלא"
    assert not (tmp_path / "a-failure-open-light.png").exists(), "#2068: אין עוד פרטים טכניים מתקפלים"
