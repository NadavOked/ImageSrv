"""חדר השיכפולים: כמה כל מגירה כתבה, ומה המחשב מדווח בסוף.

שני באגים שהיו מונחים באותו קובץ ומספרים את אותו סיפור — מה שקורה לכל
מגירה בנפרד נבלע בדרך לרמת המחשב:

- #25: `pv` אחד לפני הכותב המקבילי מודד את הזרם של המכונה. זה מספר
  אחד לשלוש מגירות, ולכן הוא לא מדידה של אף אחת מהן — והמונה של כל
  מגירה נשאר ריק. הפסים בקונסולה עמדו על 0% לאורך סבבים שלמים.
- #67: `_any_alive` אמת גם על מגירה אחת ששרדה מתוך שלוש, ומחשב
  שאיבד מגירה דיווח `done` — בדיוק כמו מחשב שכל מגירותיו נכתבו.

הבדיקות רצות את הסקריפטים עצמם, לא חיקוי שלהם.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_agent import BASH, posix, sh
from agentlib import agent_sources

pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required")

REPO = Path(__file__).resolve().parent.parent
STATION = REPO / "server" / "static" / "station"

#: fanout מזויף. הוא עושה בדיוק שני דברים כמו הבינארי האמיתי: מזין כל
#: fifo, וכותב לכל יעד מונה בייטים משלו ב-`<fifo>.bytes`. מה שהוא עושה
#: *אחרת* בכוונה — נותן למגירה השנייה מספר אחר — הוא כל העניין: מונה
#: שאינו יכול להיות שונה בין המגירות אינו מדידה שלהן. ההתנהגות
#: האמיתית של המונה נבדקת מול הבינארי עצמו ב-test_fanout.py.
COUNTING_FANOUT = (
    '#!/bin/sh\n'
    'shift\n'                       # החוצץ
    'tmp="$(mktemp)"\n'
    'cat > "$tmp"\n'
    'fanout_eof "$tmp"\n'
    'size=$(wc -c < "$tmp" | tr -d " ")\n'
    'for out; do cat "$tmp" > "$out" & done\n'
    'wait\n'
    'half=$((size / 2))\n'
    'first=1\n'
    'for out; do\n'
    '  if [ "$first" = 1 ]; then echo "$size" > "$out.bytes"; first=0\n'
    '  else echo "$half" > "$out.bytes"; fi\n'
    '  echo "$out ok"\n'
    'done\n'
    'rm -f "$tmp"\n'
)


def drawer_box(tmp_path, disks=("sda", "sdb", "sdc"), failed=()) -> tuple[Path, str]:
    """run_restore_drawers מסביב לזרם מזויף: מה שנבדק כאן הוא רק המצב
    שהמחשב מדווח בסוף, לפי מה שקרה לכל מגירה. הכתיבה עצמה (טבלה,
    הרחבה, זרם) מוחלפת בפונקציות ריקות — היא נבדקת במקומות אחרים."""
    run = tmp_path / "run"
    run.mkdir()
    prelude = (
        f"export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev SYSROOT={posix(run)!r}/sys; "   # #3908: the host's /sys is not the test's (an HDD there is refused by writecheck.sh)
        + agent_sources("common", "sysinfo", "diskinfo", "waits", "progress", "restore", "expand", "grow", "ntfsunhib", "expandshare", "writemark", "writeback", "drawers", "verdict", "failmark", "ntfsboot", "writecheck")   # #2272: imagesrv-agent's order
        + "log() { :; }; disk_fits() { return 0; }; plan_gate() { return 0; }; apply_gpt() { return 0; }; "
        "write_check() { return 0; }; write_speed_verdict() { return 0; }; readback_drawers() { :; }; "
        "expand_last() { return 0; }; grow_expanded() { return 0; }; writemark_clear() { return 0; }; "   # #1798: the table is stubbed, so is its mark
        "boot_hidden_disk() { return 0; }; "   # #2015: the hidden sectors have their own file (test_ntfs_hidden_sectors_2015.py)
        "manifest_plan() { echo '1|G|win|ntfs|2048|1024|p1.zst|sha|false|UG|'; }; "
        f"FAILED={' '.join(failed)!r}; "
        "restore_partition_drawers() { "
        'for _f in $FAILED; do target_set "$_f" failed "I/O error"; done; return 0; }; '
        + "".join(f"target_init {d} 100; " for d in disks)
    )
    return run, prelude


@pytest.mark.parametrize(("failed", "state", "rc"), [
    ((), "done", 0),
    (("sdb",), "partial", 0),
    (("sda", "sdb"), "partial", 0),
    (("sda", "sdb", "sdc"), "failed", 1),
])
def test_the_machine_state_says_how_many_drawers_survived(tmp_path, failed, state, rc):
    """#67: שלושה מצבים ברמת המחשב, לא שניים.

    לפני התיקון שתי השורות האמצעיות החזירו `done` — מגירה אחת ששרדה
    מתוך שלוש נראתה בדיוק כמו שלוש מתוך שלוש, והמחשב יצא מהחדר עם כונן
    ריק בתוכו. `partial` מסיים את הגל כמו `done` (rc=0) אבל אינו נספר
    כהצלחה מלאה.
    """
    # #1935: run_restore_drawers משאיר מילה לא-סופית (`verifying`) כשמשהו
    # שרד — המילה הסופית (`done`/`partial`) נכתבת ב-do_restore_drawers רק אחרי
    # הדרייברים והסימן; השרת נועל חבר שדיווח מצב סופי. `failed` נכתב כאן.
    run, prelude = drawer_box(tmp_path, failed=failed)
    out = sh(prelude + 'run_restore_drawers chunk http://s img m.json '
             'sda sdb sdc; echo "rc=$?"; echo "machine=$(_machine_state "sda sdb sdc")"')
    assert out.strip().splitlines()[-2] == f"rc={rc}", out
    assert out.strip().splitlines()[-1] == f"machine={state}", out
    assert (run / "state").read_text().strip() == ("failed" if state == "failed" else "verifying")


def test_a_partial_machine_still_names_the_drawer_that_failed(tmp_path):
    """עיקרון 4 בשתי הרמות בבת אחת: המחשב אומר "לא הכול", והמגירה
    אומרת מי ולמה. אחת בלי השנייה אינה כשל גלוי."""
    run, prelude = drawer_box(tmp_path, failed=("sdb",))
    sh(prelude + "run_restore_drawers chunk http://s img m.json sda sdb sdc; "
       '_machine_state "sda sdb sdc" > "$RUN_DIR/state"; '   # do_restore_drawers' last word (#1935)
       "build_progress ses_1 aa:bb:cc:00:00:21 > $RUN_DIR/progress.json")

    report = json.loads((run / "progress.json").read_text(encoding="utf-8"))
    assert report["state"] == "partial"
    states = {t["dev"]: t["state"] for t in report["targets"]}
    assert states == {"sda": "done", "sdb": "failed", "sdc": "done"}
    assert [t for t in report["targets"] if t["dev"] == "sdb"][0]["error"]


@pytest.mark.parametrize("vanishes", [False, True])
def test_progress_when_rejoin_file_vanishes(tmp_path, vanishes):
    run = tmp_path / "run"
    target = run / "targets" / "sdb"
    target.mkdir(parents=True)
    (target / "state").write_text("writing", encoding="utf-8")
    rejoin = target / "rejoin"
    record = {"part": 1, "missing": 2, "chunks": 3, "away_s": None, "step": "http"}
    rejoin.write_text(json.dumps(record), encoding="utf-8")
    prelude = f"RUN_DIR={posix(run)!r}; REJOIN={posix(rejoin)!r}; "
    if vanishes:
        # Delete after a successful file check, or just before a direct read.
        prelude += r'''
function [ {
    if builtin [ "$*" = "! -s $REJOIN ]" ] && builtin [ -s "$REJOIN" ]; then
        rm "$REJOIN" || exit 1
        return 1
    fi
    builtin [ "$@"
}
cat() {
    if [ "$1" = "$REJOIN" ]; then rm "$REJOIN" || return 1; fi
    command cat "$@"
}
'''
    report = json.loads(sh(
        prelude + agent_sources("sysinfo", "failmark", "progress", "drawerplug")
        + 'target_same() { return 1; }; build_progress ses_1 aa:bb:cc:00:00:21'
    ))
    drawer, = report["targets"]
    assert drawer["dev"] == "sdb"
    if vanishes:
        assert not rejoin.exists()
        assert "rejoin" not in drawer
    else:
        assert drawer["rejoin"] == record


# --- המסך --------------------------------------------------------------------


def test_the_web_station_screen_is_gone():
    """#1247: מסך התחנה בדפדפן הוסר; מצב 'partial' (#67) מוצג בגואי
    הנייטיב ובקונסולה, לא בדף שאין מי שפותח."""
    assert not STATION.exists()
