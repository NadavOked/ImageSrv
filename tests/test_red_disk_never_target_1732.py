"""#1732 (נדב 18/10, אחרי התייעצות עם איש מקצוע): דיסק **אדום** — SMART נכשל
(`fail`, ‏smart.sh) — **לעולם אינו יעד**, בשום מסלול. מותר רק כמקור: העתקה
ממנו, קליטה/העלאה לשרת, חילוץ. זה מחליף את "כתוב בכל זאת" באישור (#1580).

כל מסלול שכותב נבדק כאן מול דיסק אדום כיעד — והשרת והסוכן חוסמים גם כשהבקשה
מגיעה בכל זאת (מסך ישן, קונסולה ישנה, קריאה ישירה):

* **סבב חדר / סבב כיתה / משיכה מהשרת / הפצה ישירה בצד המקבל** — כולם עוברים
  ב-`smart_gate` (smartgate.sh): האדום מדולג בגלוי, בלי שאלה ובלי המתנה.
  (הגואי והמנגנון של השאלה הכתומה: test_smart.py, test_smart_warning_window.py.)
* **"שחזור לדיסק הזה"** (single_restore.sh) — מסורב לפני בייט, עם המשפט על הגואי.
* **שיכפול מקומי** (localclone.sh `lc_run`) — יעד אדום נכשל בשמו ואינו נכתב.
* **השרת והקונסולה** — test_red_disk_never_target_server_1732.py.

בקרה שלילית (במעבדה, משורש הריפו, על הענף):
  base=$(git merge-base HEAD origin/integration/v0760-rc6)
  git checkout "$base" -- agent/lib/smart/smart.sh agent/lib/smart/smartgate.sh agent/lib/restore/single_restore.sh \\
      agent/lib/gui/srgui.sh agent/lib/localclone/localclone.sh server/rounds/room.py server/disks/smart_ask.py server/rounds/room_console.py
  python3 -m pytest tests/test_red_disk_never_target_1732.py -q      # כל הטסטים שמסומנים RED נופלים
  git checkout HEAD -- agent/ server/
"""

from __future__ import annotations


from native import requires_native
from test_smart import (BASH, GUI_STUB, HEALTH_FAIL, HEALTH_OK, PRELUDE, asked, env, event, full,
                        make_smartctl, posix, preflight, sh)

RED = "SMART נכשל — לא כותבים על הדיסק הזה; אפשר להעתיק ממנו או לחלץ לשרת"
RLM = "‏"

bash_only = requires_native(("bash", BASH))


# --- הסוכן: השער של כל סבב (חדר, כיתה, משיכה, הפצה ישירה) -----------------------


@bash_only
def test_red_is_skipped_without_a_question_even_if_the_screen_would_say_rescue(tmp_path):
    """RED: על הקוד הישן הדיסק נשאל, ה-"rescue" של המסך נכתב (`written == "sda"`)."""
    written, run, err = preflight(tmp_path, full(HEALTH_FAIL), gui=["rescue", "rescue"])
    assert written == "", err
    assert asked(run) == [], err                               # no question, no 30-minute wait
    assert (run / "targets/sda/state").read_text().strip() == "skipped"
    assert (run / "targets/sda/error").read_text(encoding="utf-8").startswith(RED), err
    ev = event(run)
    assert (ev["smart"]["verdict"], ev["write_state"], ev["decision"]) == ("fail", "skipped", "skip"), ev


@bash_only
def test_the_gate_writes_the_healthy_sibling_and_never_the_red_one(tmp_path):
    """RED: smart_gate עם אדום שהמסך "מאשר" ושכן תקין — רק השכן נכתב, rc=0.
    על הקוד הישן הרשימה הייתה `sda sdb`."""
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir()
    for d in ("sda", "sdb"):
        (dev / d).write_bytes(b"\x00")
    (tmp_path / "red").mkdir(); (tmp_path / "ok").mkdir()
    sc = make_smartctl(tmp_path / "red", full(HEALTH_FAIL))
    ok = make_smartctl(tmp_path / "ok", full(HEALTH_OK))
    (run / "gui_answers").write_text("rescue\nrescue\n", newline="\n")
    # sda = red, sdb = healthy: the fake smartctl picks by the device it was given
    wrapper = tmp_path / "smartctl-by-dev"
    wrapper.write_text(f'#!/bin/sh\ncase "$*" in *sda*) exec {sc} "$@" ;; *) exec {ok} "$@" ;; esac\n', newline="\n")
    out = sh(f'chmod +x {posix(wrapper)!r}; ' + env(run, dev, smartctl=posix(wrapper)) + PRELUDE + GUI_STUB
             + 'smart_gate sess1 sda sdb; echo "rc=$?"')
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["sdb", "rc=0"], (out.stdout, out.stderr)
    assert not (run / "asked").exists(), "the healthy disk is not asked, and the red one never is"


@bash_only
def test_an_orange_disk_is_still_written(tmp_path):
    """שומר מפני תיקון-יתר: כתום (נכשל בשיכפול הקודם, SMART תקין) נשאר יעד.
    ‏#2042 (נדב 23/10): בלי שאלה (עד 23/10 — "נסה שוב")."""
    written, run, err = preflight(tmp_path, full(HEALTH_OK), gui=["rescue"], failed=("sda",))
    assert written == "sda" and asked(run) == [], err


# --- הסוכן: "שחזור לדיסק הזה" -----------------------------------------------------


@requires_native(("bash", BASH), "jq")
def test_restore_to_this_disk_refuses_a_red_disk_before_a_byte(tmp_path):
    """RED: על הקוד הישן run_restore רץ (STARTED בקובץ) והשחזור הושלם."""
    from test_single_restore_progress_gui import body, cards, restore
    from test_smart import AGENT
    run_restore = ('run_restore() { echo STARTED >> "$RUN_DIR/trace"; return 0; }; '
                   f'. {posix(AGENT)}/lib/smart/smart.sh; smart_probe() {{ echo "${{SMART_V:-ok}}"; }}; ')
    r = restore(tmp_path, run_restore, env="SMART_V=fail")
    assert "STARTED" not in r["trace"], r["out"]
    last = body(cards(r["seen"])[-1]) if r["seen"] else ""
    assert last == f"message=השחזור לא התחיל — לא נכתב דבר|{RLM}{RED} · המחשב יופעל מחדש\n", (last, r["out"])
    assert "TEST-REBOOT: restore: sda failed SMART" in r["out"] and r["rc"] == 86, r["out"]


@requires_native(("bash", BASH), "jq")
def test_restore_to_this_disk_goes_on_when_the_disk_is_not_red(tmp_path):
    """שומר (עובר בשני המצבים): "לא נבדק" אינו "תקול" (עיקרון 5) — נכתב."""
    from test_single_restore_progress_gui import restore
    from test_smart import AGENT
    run_restore = ('run_restore() { echo STARTED >> "$RUN_DIR/trace"; target_set "$2" done; return 0; }; '
                   f'. {posix(AGENT)}/lib/smart/smart.sh; smart_probe() {{ echo "${{SMART_V:-ok}}"; }}; ')
    # The fake GUI must acknowledge success and exit, not spend 600 poll
    # iterations alive while restore() waits for every background process.
    r = restore(tmp_path, run_restore, env="SMART_V=unchecked GUI_UNTIL_DONE=1")
    assert "STARTED" in r["trace"], r["out"]
    assert "RC=0" in r["out"] and r["rc"] == 0, r["out"]
    assert "השחזור הושלם" in r["seen"], r["out"]


# --- הסוכן: שיכפול מקומי ------------------------------------------------------------


@bash_only
def test_local_clone_never_writes_a_red_target(tmp_path):
    """RED: sda תקין, sdb אדום, המקור sdc. lc_run נעצר אחרי בדיקת היעדים
    (lc_source_parts מזויף שנכשל). על הקוד הישן sdb עבר את הבדיקה והגיע לשלב
    המקור כמו sda; עכשיו הוא נכשל בשמו לפני כל דבר."""
    from test_local_clone_agent import run
    script = (
        'LC="$RUN_DIR/lc"; mkdir -p "$LC/inv" "$LC/measure"; '
        'for d in sda sdb sdc; do echo "x|$d" > "$LC/inv/$d.line"; done; echo sdc > "$LC/source"; : > "$LC/measure/sdc"; '
        'disk_serial() { printf "S-%s" "$1"; }; '
        'lc_inv_field() { case "$2" in 4) printf "S-%s" "$1" ;; 12) echo 1 ;; *) echo ;; esac; }; '
        'lc_verdict_for() { echo "fits||"; }; lc_measure() { :; }; '
        'smart_probe() { case "$1" in sdb) echo fail ;; *) echo ok ;; esac; }; '
        'lc_source_parts() { echo "PARTS" >> "$RUN_DIR/trace"; LC_ERROR=stopped-here; return 1; }; '
        'lc_end() { kill "$_lr_beat" 2>/dev/null; echo END >> "$RUN_DIR/trace"; }; ata_capture() { :; }; '
        'lc_run sdc "sda sdb" 0; '
        'for d in sda sdb; do echo "$d=$(cat "$RUN_DIR/targets/$d/state")|$(cat "$RUN_DIR/targets/$d/error")"; done'
    )
    out = run(tmp_path, script)
    assert out.returncode == 0, out.stderr
    got = dict(line.split("=", 1) for line in out.stdout.splitlines() if line[:3] in ("sda", "sdb"))
    assert got["sdb"] == f"failed|{RED} — לא נכתב", (got, out.stderr)
    assert got["sda"].startswith("failed|stopped-here"), got   # the healthy one went on to the source step
