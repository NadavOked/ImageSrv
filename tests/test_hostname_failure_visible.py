"""‏#856: כתיבת שם המחשב שנכשלה אחרי שחזור — גלויה בדיווח, לא ביומן בלבד.

‏`write_hostname` מחזיר קוד לא-אפסי ו-JSON של שגיאה (hive שלא נפתח, ext4
שלא מתעגן), ו-`name_this_machine` לכד רק את ה-stdout: השגיאה נכתבה
ל-`hostname.json` ב-tmpfs (נמחק באתחול, #106) ואז `done`. השרת והמפעיל
ראו תחנה שהצליחה. ההכרעה "Never fatal" נשארת — השחזור **לא** נכשל —
אבל "לא הצלחנו לכתוב שם" ו"הכול תקין" הם שני מצבים (עיקרון 5).

הצורה: היעד מסיים `done` **עם** `error` — אותו שדה שבו `finish_grow`
מדווח "done (grow deferred)" (#648) ו-shrink.sh את "המקור לא הוחזר
לגודלו" (#87) — ולכן השרת שומר (`session_members.error`) והקונסולה
מציגה בלי שדה חדש. ‏`write_hostname` האמיתי רץ, עם hivewrite מזויף
שהאימות שלו נכשל (הקריאה החוזרת מחזירה שם אחר).

בקרה שלילית — `git checkout origin/main -- agent/lib/hostname.sh` ואז
הקובץ הזה: הטסט הראשון חייב ליפול (‏`error` חסר), האחרים לעבור.
"""

from __future__ import annotations

import json
from pathlib import Path

from native import requires_native
from test_captured_stdout import HIVEWRITE_OK, posix, sh, windows_box
from test_smart import AGENT, BASH

pytestmark = requires_native(("bash", BASH))

#: hivewrite שכותב "בהצלחה" (rc=0) אבל הקריאה החוזרת מחזירה שם אחר —
#: בדיוק #33: כתיבה שלא כתבה. ‏write_hostname מזהה ומחזיר hive_write_failed.
HIVEWRITE_LIES = HIVEWRITE_OK.replace("echo LAB1-05", "echo OTHER")


def restore_done_station(tmp_path: Path, **override: str) -> tuple[str, Path]:
    """תחנה אחרי שחזור מוצלח: היעד `sda` כבר `done` (finish_grow), ואז
    ‏name_this_machine — כמו ב-`imagectl-agent`. ‏json_get נדרס (אין jq)
    רק לתשובת השרת; ‏`.error` נקרא מהקובץ בפועל."""
    script, run = windows_box(tmp_path, **override)
    (run / "response.json").write_text("{}", encoding="utf-8")
    script += (
        f'. {posix(AGENT)}/lib/progress.sh; '
        f'RESP={posix(run / "response.json")!r}; MAC=b4:2e:99:07:1a:c4; '
        'json_get() { case "$2" in *prefix) echo lab1;; *suffix) echo 05;; '
        '  .error) sed -n \'s/.*"error":"\\([^"]*\\)".*/\\1/p\' "$1";; *) echo null;; esac; }; '
        'target_init sda 4096; target_set sda done; '
    )
    return script, run


def target_error(run: Path) -> str:
    path = run / "targets" / "sda" / "error"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def final_report(run: Path, script: str) -> dict:
    out = sh(script + 'build_progress sess1 "$MAC"')
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_failed_hostname_write_ends_done_with_a_visible_warning(tmp_path):
    """**הפגם עצמו.** עד #856: `done` נקי, השגיאה ב-tmpfs בלבד."""
    script, run = restore_done_station(tmp_path, hivewrite=HIVEWRITE_LIES)
    out = sh(script + 'name_this_machine sda sess1; echo "rc=$?"')
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("rc=0"), out.stdout          # לא fatal
    assert (run / "state").read_text().strip() == "naming"         # #1935: done is the caller's, after the drivers and the mark
    assert (run / "targets/sda/state").read_text().strip() == "done"
    err = target_error(run)
    assert err.startswith("שם המחשב לא נכתב: "), err
    assert "עריכת הרג׳יסטרי נכשלה" in err, err                       # הסיבה מ-write_hostname
    # והשדה מגיע לדיווח שהשרת מקבל (סעיף 4) — done + error, JSON תקין.
    report = final_report(run, script + 'name_this_machine sda sess1 >/dev/null; ')
    (target,) = report["targets"]
    assert target["state"] == "done"
    assert target["error"] == err


def test_a_successful_hostname_write_ends_done_without_a_warning(tmp_path):
    """רדיוס הפגיעה: הצלחה נשארת `done` נקי — אין `error` ריק, אין שדה."""
    script, run = restore_done_station(tmp_path)
    out = sh(script + 'name_this_machine sda sess1')
    assert out.returncode == 0, out.stderr
    assert (run / "targets/sda/state").read_text().strip() == "done"
    assert target_error(run) == ""
    assert json.loads((run / "hostname.json").read_text(encoding="utf-8"))["ok"] is True
    report = final_report(run, script + 'name_this_machine sda sess1 >/dev/null; ')
    assert "error" not in report["targets"][0]


def test_the_hostname_warning_joins_an_earlier_grow_warning(tmp_path):
    """שתי אזהרות על אותו יעד — "grow deferred" (#648) ואז השם — שתיהן
    נשארות: האזהרה השנייה אינה דורסת את הראשונה."""
    script, run = restore_done_station(tmp_path, hivewrite=HIVEWRITE_LIES)
    out = sh(script
             + 'target_set sda done "הושלם, אך ההרחבה נדחתה: מחיצה 1 (ntfs); הרחבת מערכת הקבצים נכשלה"; '
             'name_this_machine sda sess1')
    assert out.returncode == 0, out.stderr
    err = target_error(run)
    assert "הושלם, אך ההרחבה נדחתה: מחיצה 1" in err, err
    assert "שם המחשב לא נכתב: עריכת הרג׳יסטרי נכשלה" in err, err


def test_no_prefix_means_no_name_and_no_warning(tmp_path):
    """סבב בלי קידומת/סיומת (השרת לא נתן שם) — לא נכתב שם ולא אזהרה: זו
    לא כתיבה שנכשלה, זו כתיבה שלא התבקשה."""
    script, run = restore_done_station(tmp_path, hivewrite=HIVEWRITE_LIES)
    out = sh(script + 'json_get() { echo null; }; name_this_machine sda sess1')
    assert out.returncode == 0, out.stderr
    assert target_error(run) == ""
    assert not (run / "hostname.json").exists()
