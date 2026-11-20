"""#3907: סירוב HDD לפני כתיבה אינו "הכתיבה נעצרה, הדיסק במצב לא תקין".

`write_check` מסרב ליעד HDD כש"תמיכה ב-HDD" כבויה *לפני* הכתיבה הראשונה
(`hdd_target_gate`; ובדיקה נפרדת ב-test_agent_hdd_x16.py שאף כתיבה לא
מושגת). מסך הכישלון של השחזור אמר בכל זאת "הכתיבה לדיסק נעצרה לפני
שהסתיימה … הדיסק במצב לא תקין". עכשיו הסירוב משאיר ראיה חיובית
(`targets/<dev>/refused_before_write`) והמסך אומר שלא נכתב דבר, שהדיסק לא
שונה, ומה הסיבה (ההגדרה). כשל כתיבה אמיתי — בלי הסימון — נשאר כפי שהיה.

בקרה שלילית: `git checkout <base> -- agent/lib/gui/failscreen.sh
agent/lib/restore/writecheck.sh agent/lib/core/progress.sh`.
"""

from __future__ import annotations

import shutil

from agentlib import flat_lib
from native import requires_native
from test_smart import BASH, posix, sh
from test_writecheck_speed_1366 import LIBS

pytestmark = requires_native(("bash", BASH), ("jq", shutil.which("jq")))

REFUSED = "HDD — תמיכה ב-HDD כבויה בהגדרות"


def screen(tmp_path, *, refuse: bool) -> list[str]:
    """The real write_check on an HDD (switch off) -- or, for the contrast, a plain
    failed write of the same disk -- then the real fail_texts for the restore case."""
    (tmp_path / "sysroot/sys/block/sda/queue").mkdir(parents=True)
    (tmp_path / "sysroot/sys/block/sda/queue/rotational").write_text("1\n", newline="\n")
    (tmp_path / "sysroot/proc").mkdir(parents=True)   # target_init's last line cuts /proc/uptime: absent = rc=1 on Linux
    (tmp_path / "sysroot/proc/uptime").write_text("1.00 1.00\n", newline="\n")
    (tmp_path / "run/targets/sda").mkdir(parents=True)
    manifest = tmp_path / "m.json"
    manifest.write_text("{}")
    (tmp_path / "run/response.json").write_text('{"hdd_support": false}')
    prelude = "".join(f". {posix(flat_lib())}/{n}.sh; " for n in LIBS) + f". {posix(flat_lib())}/failscreen.sh; "
    step = (f'write_check sda {posix(manifest)!r} >/dev/null; echo "rc=$?"; ' if refuse
            else 'target_set sda failed "נכתבו 1 מתוך 3 מחיצות"; ')
    r = sh(f'export SYSROOT={posix(tmp_path / "sysroot")!r} RUN_DIR={posix(tmp_path / "run")!r} '
           f'DEVROOT={posix(tmp_path / "dev")!r}; {prelude}{step}'
           'fail_case "restore did not complete"; fail_texts "$_fc" "$(fail_reason)"; '
           'printf "%s\\n" "$_ft_title" "$_ft_why" "$_ft_safe" "$_ft_do"')
    assert r.returncode == 0, r.stderr
    return r.stdout.splitlines()


def test_a_refusal_before_the_write_says_nothing_was_written_and_why(tmp_path):
    out = screen(tmp_path, refuse=True)
    assert out[0] == "rc=1", out
    title, why, safe, do = out[1:]
    assert title == "השחזור לא התחיל — לא נכתב דבר"
    assert why == f"הדיסק נדחה לפני הכתיבה: {REFUSED}."
    assert safe == "הדיסק לא שונה — המערכת שבו שלמה."
    assert "לא נכתב דבר" in do
    assert not any("במצב לא תקין" in line or "נעצרה" in line for line in out), out


def test_a_real_write_failure_keeps_its_old_words(tmp_path):
    """The contrast: no refusal mark, so the screen is the one it always was."""
    title, why, safe, _do = screen(tmp_path, refuse=False)
    assert title == "השחזור נכשל"
    assert why.startswith("הכתיבה לדיסק נעצרה לפני שהסתיימה: נכתבו 1 מתוך 3 מחיצות")
    assert "במצב לא תקין" in safe


def test_the_next_job_does_not_inherit_the_refusal(tmp_path):
    """target_init clears the mark: a later, different failure on this disk is not "untouched"."""
    out = screen(tmp_path, refuse=True)
    assert out[0] == "rc=1"
    mark = tmp_path / "run/targets/sda/refused_before_write"
    assert mark.exists()
    prelude = "".join(f". {posix(flat_lib())}/{n}.sh; " for n in LIBS)
    r = sh(f'export SYSROOT={posix(tmp_path / "sysroot")!r} RUN_DIR={posix(tmp_path / "run")!r}; {prelude}'
           'target_init sda 4096')
    assert r.returncode == 0, r.stderr
    assert not mark.exists()


def test_the_gate_leaves_the_mark_only_where_it_refuses(tmp_path):
    """hdd_support on: the gate lets the disk through and leaves no mark."""
    (tmp_path / "sysroot/sys/block/sda/queue").mkdir(parents=True)
    (tmp_path / "sysroot/sys/block/sda/queue/rotational").write_text("1\n", newline="\n")
    (tmp_path / "run/targets/sda").mkdir(parents=True)
    (tmp_path / "m.json").write_text("{}")
    (tmp_path / "run/response.json").write_text('{"hdd_support": true}')
    prelude = "".join(f". {posix(flat_lib())}/{n}.sh; " for n in LIBS)
    r = sh(f'export SYSROOT={posix(tmp_path / "sysroot")!r} RUN_DIR={posix(tmp_path / "run")!r}; {prelude}'
           f'hdd_target_gate {posix(tmp_path / "m.json")!r} sda; echo "rc=$?"')
    assert "rc=0" in r.stdout, (r.stdout, r.stderr)
    assert not (tmp_path / "run/targets/sda/refused_before_write").exists()
