"""‏#73: זרם שנקטע מדווח כזרם שנקטע, לא כאימג' פגום.

הכשל שהקובץ הזה נכתב נגדו אינו "המערכת לא דיווחה". היא דיווחה — והיא
דיווחה **סיבה ספציפית ושקרית**: ‏`sha256 mismatch on p3.windows.pcl.zst`
על מכונה ש-`fanout` שלה נהרג באמצע הזרם (‏OOM על 512MB, ‏#21). האימג'
בספרייה היה תקין לחלוטין; ה-sha חושב על בייטים חלקיים, ולכן הוא בהכרח
לא תאם. הטכנאי נשלח לחפש בספרייה קובץ פגום שאינו קיים.

זה עיקרון 5 בצורתו המסוכנת ביותר: לא היעדר בדיקה שנראה כהצלחה, אלא
**היעדר בדיקה שמתחזה לראיה חיובית**. ‏"בדקנו את הבייטים והם שגויים"
ו"לא היו לנו בייטים לבדוק" הם שני מצבים שונים, וקיפולם לאחד עולה שעות
אבחון בכל פעם.

ארבעה מצבים, ארבע הודעות — וביניהם **בקרה שלילית**: בייטים שבאמת
שגויים חייבים להמשיך להידווח כאי-התאמת sha256, אחרת התיקון רק החליף
שקר אחד באחר.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_timeouts import log_of, make_stubs, run_sh
from agentlib import lib_path

pytestmark = requires_native(("bash", BASH))

#: ‏8MB, ובכוונה לא פחות: חוצצי הצינורות של הקרנל הם 64KB כל אחד, ומטען
#: קטן מהם שלם *עובר* דרך ה-tee אל ה-sha גם כשהקורא שבקצה כבר מת. אז
#: ה-sha היה תואם, הכשל היה נראה אחרת לגמרי, והבדיקה הייתה בודקת מצב
#: שאינו המצב של #73. מטען גדול מהחוצצים נקטע באמת.
PAYLOAD = b"imagesrv" * (1024 * 1024)

#: ‏fanout מזויף שנהרג באמצע הזרם, בדיוק כמו ה-OOM killer ב-#21: הוא
#: בולע חלק מהבייטים, לא כותב שורת דוח לאף מגירה, ולא פותח אף fifo,
#: ויוצא ב-137. ה-`tee` שמעליו מקבל EPIPE, הזרם נקטע, וה-sha מחושב על
#: מה שהספיק לעבור — ולכן הוא בהכרח לא יתאים.
DYING_FANOUT = (
    '#!/bin/sh\n'
    'shift\n'                       # החוצץ
    'head -c 65536 > /dev/null\n'
    'exit 137\n'
)

#: ‏fanout שמסיים כשורה. מזין כל מגירה ומדווח עליה — הבסיס להשוואה.
GOOD_FANOUT = (
    '#!/bin/sh\n'
    'shift\n'
    'tmp="$(mktemp)"\n'
    'cat > "$tmp"\n'
    'fanout_eof "$tmp"\n'
    'for out; do cat "$tmp" > "$out" & done\n'
    'wait\n'
    'for out; do echo "$out ok"; done\n'
    'rm -f "$tmp"\n'
)

#: ‏#1438: fanout שמדווח ok על כל מגירה ולא כותב את ‏-S — המקבילה של
#: sha256sum שיצא בלי ערך, מאז שהגיבוב של החדר עבר לתוך fanout.
FANOUT_WITHOUT_HASH = GOOD_FANOUT.replace('fanout_eof "$tmp"\n', "", 1)
assert FANOUT_WITHOUT_HASH != GOOD_FANOUT, "the fake still hashes"


def drawer_run(tmp_path, script_tail: str, stubs: dict, disks=("sda", "sdb"),
               source: str | None = None) -> tuple[Path, str]:
    """מריץ ‏restore_partition_drawers אמיתי מול זרם ו-fanout מזויפים."""
    box = tmp_path / "box"
    run = box / "run"
    payload = box / "part.bin"
    box.mkdir(parents=True)
    payload.write_bytes(PAYLOAD)
    (run / "targets").mkdir(parents=True)

    if source is None:
        source = f'stream_source() {{ cat {posix(payload)!r}; }}; '
    out = run_sh(
        make_stubs(box / "stubs", stubs)
        + f"export TMPDIR={posix(tmp_path)!r} RUN_DIR={posix(run)!r} DEVROOT={posix(box)}/dev "
        "WAIT_POLL_S=1 WAIT_DRAWER_S=3 WAIT_HELPER_S=3 "
        "WAIT_STREAM_START_S=20 WAIT_STREAM_STALL_S=20; "
        f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/waits.sh; "
        f". {posix(AGENT)}/lib/core/progress.sh; . {posix(AGENT)}/lib/restore/restore.sh; "
        f". {posix(AGENT)}/lib/restore/drawers.sh; . {posix(AGENT)}/lib/restore/drawerfeed.sh; . {posix(AGENT)}/lib/restore/verdict.sh; . {posix(AGENT)}/lib/restore/failmark.sh; . {posix(AGENT)}/lib/restore/ntfsboot.sh; "
        + "".join(f"target_init {d} {len(PAYLOAD)}; " for d in disks)
        + source + script_tail
    )
    return run, out


def call(sha: str, disks=("sda", "sdb")) -> str:
    return ("restore_partition_drawers unicast http://s img 3 dd part.zst "
            f"{sha} '' {' '.join(disks)} > /dev/null 2>&1; " 'echo "rc=$?"')


def errors_of(run: Path, disks=("sda", "sdb")) -> dict[str, str]:
    out = {}
    for dev in disks:
        path = run / "targets" / dev / "error"
        out[dev] = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    return out


GOOD_SHA = hashlib.sha256(PAYLOAD).hexdigest()
#: ‏sha תקין לחלוטין בצורתו — ‏64 ספרות hex — ופשוט לא זה של הבייטים.
#: זו הבקרה השלילית: אי-התאמה אמיתית חייבת להמשיך להיקרא בשמה.
WRONG_SHA = "0" * 64


# --- הכשל של #73 עצמו --------------------------------------------------------


def test_a_fanout_that_was_killed_is_not_reported_as_a_corrupt_image(tmp_path):
    """‏#73 במלואו: ‏fanout שנהרג באמצע הזרם (‏rc=137, כמו ה-OOM killer).

    ה-sha אכן אינו תואם — הוא חושב על בייטים חלקיים — אבל זו תוצאה של
    הכשל ולא הכשל עצמו. ההודעה חייבת לדבר על הפצת הזרם, ו**אסור** לה
    להזכיר אי-התאמת sha256: זו הטענה שמפנה את האבחון לספרייה.
    """
    run, out = drawer_run(tmp_path, call(GOOD_SHA), {"fanout": DYING_FANOUT})
    assert out.strip().endswith("rc=1"), out

    errors = errors_of(run)
    for dev, error in errors.items():
        assert error, f"{dev} נכשלה בלי סיבה כתובה"
        assert "sha256" not in error, (
            f"{dev} עדיין מאשימה את ה-sha על בדיקה שלא נעשתה: {error}")
        assert "הפצת הזרם" in error, error
        # קוד היציאה עצמו בהודעה: זה מה שמפריד "נהרג" מ"יצא בשגיאה".
        assert "137" in error, error

    log = log_of(run)
    assert "sha256 mismatch" not in log and "אי-התאמת sha256" not in log
    assert "הפצת הזרם" in log


def test_a_stream_cut_at_the_source_is_reported_as_a_cut_stream(tmp_path):
    """המקור נפל באמצע (‏udp-receiver שפקע, ‏curl שנפל) ו-fanout סיים
    בשלום על מה שהגיע — הוא ראה EOF תקין ואין לו מה לדווח אחרת.

    ‏`$?` של צינור ב-POSIX sh הוא של האחרון בלבד, ולכן קוד היציאה של
    המקור לא היה קיים כאן כלל, והמצב הזה נבלע בענף ה-sha.
    """
    run, out = drawer_run(
        tmp_path, call(GOOD_SHA), {"fanout": GOOD_FANOUT},
        source="stream_source() { printf 'partial'; return 28; }; ",
    )
    assert out.strip().endswith("rc=1"), out

    for dev, error in errors_of(run).items():
        assert "הזרם נקטע" in error, f"{dev}: {error}"
        assert "sha256" not in error, f"{dev}: {error}"
        assert "28" in error, f"קוד היציאה של המקור אינו בהודעה: {error}"


def test_a_sha_that_was_never_computed_is_not_called_a_mismatch(tmp_path):
    """הזרם הגיע, ההפצה הצליחה — ורק חישוב ה-sha לא הניב ערך.

    ‏"לא הצלחנו לבדוק" ו"בדקנו והבייטים שגויים" הם שני מצבים, וזה בדיוק
    הקיפול שעיקרון 5 אוסר. המחיצה עדיין **נכשלת** — היעדר ראיה אינו
    היתר להמשיך — אבל היא נכשלת בשמה הנכון.
    """
    run, out = drawer_run(
        tmp_path, call(GOOD_SHA),
        {"fanout": FANOUT_WITHOUT_HASH},
    )
    assert out.strip().endswith("rc=1"), out

    for dev, error in errors_of(run).items():
        assert "לא חושב" in error, f"{dev}: {error}"
        assert "אי-התאמת" not in error, f"{dev}: {error}"


# --- הבקרה השלילית: אי-התאמה אמיתית נשארת אי-התאמה ---------------------------


def test_bytes_that_really_are_wrong_are_still_called_a_mismatch(tmp_path):
    """בלי זה התיקון הוא רק החלפת שקר בשקר.

    כאן הכול עבד — המקור יצא 0, ‏fanout דיווח על כל מגירה, ה-sha חושב
    ויש לו ערך — ופשוט אינו הערך שבמניפסט. זה **המצב היחיד** שבו מותר
    לומר "אי-התאמת sha256", וכאן הוא נאמר.
    """
    run, out = drawer_run(tmp_path, call(WRONG_SHA), {"fanout": GOOD_FANOUT})
    assert out.strip().endswith("rc=1"), out

    for dev, error in errors_of(run).items():
        assert "אי-התאמת sha256" in error, f"{dev}: {error}"
        assert "part.zst" in error, f"שם הקובץ חסר בהודעה: {error}"
        assert "לא חושב" not in error and "נקטע" not in error, f"{dev}: {error}"


def test_a_stream_that_arrives_whole_still_succeeds(tmp_path):
    """הבקרה השלילית השנייה: המסלול התקין לא נשבר בדרך.

    ה-sha התואם הוא הראיה החיובית שהזרם הגיע שלם, והוא נשאל ראשון —
    ולכן שום קוד יציאה מוזר במקור אינו יכול להפיל מחיצה שאומתה.
    """
    run, out = drawer_run(tmp_path, call(GOOD_SHA), {"fanout": GOOD_FANOUT})
    assert out.strip().endswith("rc=0"), out
    assert errors_of(run) == {"sda": "", "sdb": ""}
    states = {d: (run / "targets" / d / "state").read_text().strip()
              for d in ("sda", "sdb")}
    assert "failed" not in states.values(), states


# --- אותו הפרש בתחנה הבודדת --------------------------------------------------


def station_run(tmp_path, sha: str, stubs: dict) -> tuple[Path, str]:
    box = tmp_path / "box"
    run = box / "run"
    payload = box / "part.bin"
    box.mkdir(parents=True)
    payload.write_bytes(PAYLOAD)
    (run / "targets").mkdir(parents=True)

    out = run_sh(
        make_stubs(box / "stubs", stubs)
        + f"export TMPDIR={posix(tmp_path)!r} RUN_DIR={posix(run)!r} DEVROOT={posix(box)!r} "
        "WAIT_POLL_S=1 WAIT_HELPER_S=3 "
        "WAIT_STREAM_START_S=20 WAIT_STREAM_STALL_S=20; "
        f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/waits.sh; "
        f". {posix(AGENT)}/lib/core/progress.sh; . {posix(AGENT)}/lib/restore/restore.sh; . {posix(AGENT)}/lib/restore/ntfsboot.sh; . {posix(AGENT)}/lib/restore/drawers.sh; "   # #1906: restore_pipe
        f"target_init sda {len(PAYLOAD)}; "
        f'stream_source() {{ cat {posix(payload)!r}; }}; '
        "node_is_block() { true; }; "
        f"restore_partition unicast http://s img sda 2 dd p2.zst {sha} '' "
        '> /dev/null 2>&1; echo "rc=$?"'
    )
    return run, out


def test_the_single_station_also_separates_not_computed_from_not_matching(tmp_path):
    """אותו קיפול בדיוק היה במסלול התחנה הבודדת, ובאותה שורה."""
    # #1444: the station hashes inside fanout too -- "no -S" is the silent hash.
    run, out = station_run(tmp_path, GOOD_SHA, {"fanout": FANOUT_WITHOUT_HASH})
    assert out.strip().endswith("rc=1"), out
    log = log_of(run)
    assert "לא חושב" in log, log
    assert "sha256 mismatch" not in log, log


def test_the_single_station_still_reports_a_real_mismatch(tmp_path):
    """הבקרה השלילית של אותו מסלול."""
    run, out = station_run(tmp_path, WRONG_SHA, {})
    assert out.strip().endswith("rc=1"), out
    assert "sha256 mismatch" in log_of(run)


# --- הנעילה: אף מסלול לא משווה sha שלא נקרא ----------------------------------


@pytest.mark.parametrize("name", ["restore.sh", "drawers.sh"])
def test_no_sha_is_compared_before_it_is_known_to_be_a_digest(name):
    """הדפוס עצמו, ולא רק המופע: כל השוואת sha בסוכן עוברת קודם דרך
    ‏`is_sha256`. בלעדיה `_got` ריק שווה ל"לא תאם", וזה בדיוק הבאג —
    טסט שבודק רק את ההודעה היה עובר גם אם מישהו יוסיף השוואה שלישית."""
    source = (lib_path(name)).read_text(encoding="utf-8")
    assert "is_sha256" in source, f"{name} משווה sha בלי לוודא שהוא נקרא"


def test_the_drawers_ask_the_source_for_its_own_exit_code():
    """‏`$?` של צינור הוא של האחרון בלבד. בלי לכידה מפורשת של קוד היציאה
    של המקור, "הזרם נקטע" אינו מצב שאפשר בכלל לזהות — והוא נבלע בענף
    ה-sha, שהוא כל #73."""
    source = (AGENT / "lib" / "restore" / "drawers.sh").read_text(encoding="utf-8")
    assert "source.rc" in source
    assert 'echo "$?" > "$RUN_DIR/source.rc"' in source
