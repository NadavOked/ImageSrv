"""‏#872: שגיאות CRC (מאפיין 199) כהפרש בסבב, לא כמונה מצטבר.

המונה המצטבר מספר על ההיסטוריה של הכבלים (137–55,242 על כל 6 דיסקי
הכיתה, 15/09) ולא על הדיסק. מה שאומר משהו על הסבב הזה הוא בכמה הוא עלה
בו: 199 נקרא לפני הכתיבה (המטמון של smart_probe) ופעם אחת אחריה, וההפרש
מגיע לדוח הסיום כ-`crc_delta`. הכלל של עיקרון 5: **חסר = לא נמדד**, לא 0.

‏smartctl מזויף שקורא את הפלט שלו מקובץ, כדי שהבדיקה תוכל להחליף אותו
בין הקריאה הראשונה לשנייה — בדיוק מה שדיסק עם כבל רועע עושה.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from native import requires_native
from test_smart import BASH, HEALTH_OK, REPO, AGENT, attrs, env, full, posix, sh
from agentlib import flat_lib

pytestmark = requires_native(("bash", BASH))

PRELUDE = (
    f'. {posix(AGENT)}/lib/core/common.sh; '
    f'. {posix(AGENT)}/lib/core/waits.sh; '   # #1367: capped_run, the smartctl ceiling
    f'. {posix(AGENT)}/lib/hw/sysinfo.sh; '
    f'. {posix(AGENT)}/lib/core/progress.sh; '
    f'. {posix(AGENT)}/lib/smart/smart.sh; '
    # #1921: the knowledge tables judge the disk -- without them smart_probe says no_table
    f'export SMART_KB={posix(flat_lib())}/data; . {posix(AGENT)}/lib/smart/smartjudge.sh; . {posix(AGENT)}/lib/smart/hwissues.sh; '
    f'. {posix(AGENT)}/lib/smart/crcdelta.sh; '
)


def box(tmp_path: Path, crc_before: int) -> tuple[str, Path]:
    """‏RUN_DIR עם יעד sda שאותחל, smartctl מזויף שמדפיס את `smart.body`
    ויוצא ב-`smart.rc`, ומטמון SMART שנקרא ממנו (crc_before)."""
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir(); (dev / "sda").write_bytes(b"\x00")
    b = tmp_path / "box"; b.mkdir()
    (b / "smart.body").write_text(full(HEALTH_OK, crc=crc_before), newline="\n")
    (b / "smart.rc").write_text("0\n", newline="\n")
    sc = b / "smartctl"
    sc.write_text(f'#!/bin/sh\ncat "{posix(b)}/smart.body"\nexit "$(cat "{posix(b)}/smart.rc")"\n',
                  newline="\n")
    subprocess.run([BASH, "-c", f'chmod +x {posix(sc)!r}'], check=True,
                   stdin=subprocess.DEVNULL)
    # ‏#1742: ‏/sys/block/sda/ro = 0 -- כתיב; ro שלא נקרא היה "לא נבדק", לא "ok"
    blk = tmp_path / "sysro" / "sys" / "block" / "sda"; blk.mkdir(parents=True)
    (blk / "ro").write_text("0\n", newline="\n")
    base = env(run, dev, sysroot=tmp_path / "sysro", smartctl=posix(sc)) + PRELUDE
    out = sh(base + 'target_init sda 1000; smart_probe sda')
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stderr
    return base, tmp_path


def set_after(tmp_path: Path, crc: int | None = None, rc: int = 0) -> None:
    b = tmp_path / "box"
    if crc is not None:
        (b / "smart.body").write_text(full(HEALTH_OK, crc=crc), newline="\n")
    (b / "smart.rc").write_text(f"{rc}\n", newline="\n")


def delta_file(tmp_path: Path) -> Path:
    return tmp_path / "run" / "targets" / "sda" / "crc_delta"


def test_a_rise_during_the_round_is_the_delta_not_the_counter(tmp_path):
    """‏55,242 לפני, 55,247 אחרי → 5. המונה עצמו (55K) אינו מעניין; מה
    שמעניין הוא שהכבל הפיל 5 בסבב הזה. בקרה שלילית: על main אין קובץ."""
    base, t = box(tmp_path, 55242)
    set_after(t, crc=55247)
    out = sh(base + 'crc_delta_measure sda')
    assert out.returncode == 0, out.stderr
    assert delta_file(t).read_text().strip() == "5"


def test_no_rise_is_a_measured_zero(tmp_path):
    base, t = box(tmp_path, 137)
    out = sh(base + 'crc_delta_measure sda')
    assert out.returncode == 0, out.stderr
    assert delta_file(t).read_text().strip() == "0"


def test_a_second_read_that_cannot_open_the_disk_writes_nothing(tmp_path):
    # rc=2 = bit 1 = הפתיחה נכשלה (גם דרך SAT). לא נמדד ≠ 0 (עיקרון 5).
    base, t = box(tmp_path, 137)
    set_after(t, rc=2)
    out = sh(base + 'crc_delta_measure sda')
    assert out.returncode == 0, out.stderr
    assert not delta_file(t).exists()
    assert "not measured" in out.stdout


def test_a_disk_failed_reads_bit_3_is_still_an_answer(tmp_path):
    # rc=8 = bit 3 = "הדיסק נכשל" — תשובה, לא כשל פתיחה; המונה נקרא.
    base, t = box(tmp_path, 10)
    set_after(t, crc=12, rc=8)
    sh(base + 'crc_delta_measure sda')
    assert delta_file(t).read_text().strip() == "2"


def test_without_a_baseline_nothing_is_written(tmp_path):
    """המטמון של דיסק `unchecked` (אין smartctl) נושא 0 שאינו מדידה — ומכאן
    "הפרש" מול קריאה אמיתית היה המונה המצטבר בתחפושת."""
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir(); (dev / "sda").write_bytes(b"\x00")
    base = env(run, dev, smartctl="/nonexistent/smartctl") + PRELUDE
    out = sh(base + 'target_init sda 1000; smart_probe sda >/dev/null; crc_delta_measure sda')
    assert out.returncode == 0, out.stderr
    assert not delta_file(tmp_path).exists()
    assert "no baseline" in out.stdout


def test_crc_delta_after_measures_and_keeps_the_exit_code_of_the_write(tmp_path):
    """הצורה שבה הסוכן קורא לזה בתחנה: `{ run_restore ...; crc_delta_after
    sda; }` — הכשל של הכתיבה נשמר (rc=3) והמדידה קרתה."""
    base, t = box(tmp_path, 100)
    set_after(t, crc=101)
    out = sh(base + 'if { sh -c "exit 3"; crc_delta_after sda; }; then echo ok; else echo "rc=$?"; fi')
    assert out.stdout.strip().endswith("rc=3"), out.stdout
    assert delta_file(t).read_text().strip() == "1"
    out = sh(base + 'if { true; crc_delta_after sda; }; then echo ok; else echo "rc=$?"; fi')
    assert out.stdout.strip().endswith("ok"), out.stdout


def test_the_report_carries_the_delta_only_when_it_was_measured(tmp_path):
    """‏build_progress: ‏sda נמדד (5) → `crc_delta: 5`; ‏sdb לא נמדד → השדה
    **חסר**, לא 0. בקרה שלילית: על main השדה חסר גם ל-sda."""
    run = tmp_path / "run"
    for dev in ("sda", "sdb"):
        t = run / "targets" / dev
        t.mkdir(parents=True)
        (t / "state").write_text("done")
        (t / "base").write_text("1000")
        (t / "bytes.raw").write_text("")
        (t / "total").write_text("1000")
    (run / "targets" / "sda" / "crc_delta").write_text("5\n")
    out = sh(f'export RUN_DIR={posix(run)!r}; '
             f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/progress.sh; '
             'build_progress ses_1 aa:bb:cc:dd:ee:ff')
    report = json.loads(out.stdout)
    by_dev = {t["dev"]: t for t in report["targets"]}
    assert by_dev["sda"]["crc_delta"] == 5
    assert "crc_delta" not in by_dev["sdb"]


def test_target_init_clears_a_delta_from_the_previous_round(tmp_path):
    base, t = box(tmp_path, 100)
    delta_file(t).write_text("7\n")
    sh(base + 'target_init sda 1000')
    assert not delta_file(t).exists()


def test_the_agent_loads_the_lib_and_measures_on_both_restore_paths():
    """‏imagesrv-agent הוא 300 שורות בדיוק: הטעינה והקריאות יושבות בשורות
    קיימות. בלי הטעינה — `crc_delta_measure: not found` על הברזל."""
    src = (REPO / "agent" / "imagesrv-agent").read_text(encoding="utf-8")
    assert '. "$LIB_DIR/crcdelta.sh"' in src
    assert 'crc_delta_measure $_disks' in src          # חדר השיכפולים
    assert 'crc_delta_after "$_disk"' in src           # תחנה בודדת
