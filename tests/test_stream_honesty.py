"""#73: זרם שנקטע מדווח כזרם שנקטע, לא כאימג' פגום.

הכשל שהקובץ הזה נכתב נגדו אינו "המערכת לא דיווחה". היא דיווחה — והיא
דיווחה **סיבה ספציפית ושקרית**: `sha256 mismatch on p3.windows.pcl.zst`
על מכונה ש-`fanout` שלה נהרג באמצע הזרם (OOM על 512MB, #21). האימג'
בספרייה היה תקין לחלוטין; ה-sha חושב על בייטים חלקיים, ולכן הוא בהכרח
לא תאם. הטכנאי נשלח לחפש בספרייה קובץ פגום שאינו קיים.

זה עיקרון 5 בצורתו המסוכנת ביותר: לא היעדר בדיקה שנראה כהצלחה, אלא
**היעדר בדיקה שמתחזה לראיה חיובית**. "בדקנו את הבייטים והם שגויים"
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
from test_agent import BASH, posix
from test_timeouts import log_of, make_stubs, run_sh
from agentlib import agent_sources, lib_path

pytestmark = requires_native(("bash", BASH))

#: 8MB, ובכוונה לא פחות: חוצצי הצינורות של הקרנל הם 64KB כל אחד, ומטען
#: קטן מהם שלם *עובר* דרך ה-tee אל ה-sha גם כשהקורא שבקצה כבר מת. אז
#: ה-sha היה תואם, הכשל היה נראה אחרת לגמרי, והבדיקה הייתה בודקת מצב
#: שאינו המצב של #73. מטען גדול מהחוצצים נקטע באמת.
PAYLOAD = b"imagesrv" * (1024 * 1024)

#: fanout מזויף שנהרג באמצע הזרם, בדיוק כמו ה-OOM killer ב-#21: הוא
#: בולע חלק מהבייטים, לא כותב שורת דוח לאף מגירה, ולא פותח אף fifo,
#: ויוצא ב-137. ה-`tee` שמעליו מקבל EPIPE, הזרם נקטע, וה-sha מחושב על
#: מה שהספיק לעבור — ולכן הוא בהכרח לא יתאים.
DYING_FANOUT = (
    '#!/bin/sh\n'
    'shift\n'                       # החוצץ
    'head -c 65536 > /dev/null\n'
    'exit 137\n'
)

#: fanout שמסיים כשורה. מזין כל מגירה ומדווח עליה — הבסיס להשוואה.
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

#: #1438: fanout שמדווח ok על כל מגירה ולא כותב את -S — המקבילה של
#: sha256sum שיצא בלי ערך, מאז שהגיבוב של החדר עבר לתוך fanout.
FANOUT_WITHOUT_HASH = GOOD_FANOUT.replace('fanout_eof "$tmp"\n', "", 1)
assert FANOUT_WITHOUT_HASH != GOOD_FANOUT, "the fake still hashes"


GOOD_SHA = hashlib.sha256(PAYLOAD).hexdigest()
#: sha תקין לחלוטין בצורתו — 64 ספרות hex — ופשוט לא זה של הבייטים.
#: זו הבקרה השלילית: אי-התאמה אמיתית חייבת להמשיך להיקרא בשמה.
WRONG_SHA = "0" * 64


# --- הכשל של #73 עצמו --------------------------------------------------------


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
        + agent_sources("common", "sysinfo", "diskinfo", "waits", "progress", "failmark", "restore", "ntfsboot", "writeback", "drawers")   # #1906: restore_pipe; #2272: imagesrv-agent's order
        + f"target_init sda {len(PAYLOAD)}; "
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


@pytest.mark.parametrize("name", ["restore.sh"])
def test_no_sha_is_compared_before_it_is_known_to_be_a_digest(name):
    """הדפוס עצמו, ולא רק המופע: כל השוואת sha בסוכן עוברת קודם דרך
    `is_sha256`. בלעדיה `_got` ריק שווה ל"לא תאם", וזה בדיוק הבאג —
    טסט שבודק רק את ההודעה היה עובר גם אם מישהו יוסיף השוואה שלישית."""
    source = (lib_path(name)).read_text(encoding="utf-8")
    assert "is_sha256" in source, f"{name} משווה sha בלי לוודא שהוא נקרא"
