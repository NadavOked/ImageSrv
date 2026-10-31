"""קצב סקירת הסרק של הסוכן — ‏issue #136.

שלושה מסלולים סקרו ב-`sleep 2` קבוע, וכל סקירה היא כתיבת DB בשרת.
הקצב האחיד היה שגוי לכל אחד מארבעת הפונים בנפרד, ומסיבה אחרת — ולכן
מה שנבדק כאן אינו "המספר קטן יותר" אלא **מהו הטריגר**, ומה עוצר את
ההתרחבות:

* **תחנה מחוץ לווילן ההפצה אינה סוקרת בכלל.** אין לה סבב, אין לה
  משימה, והזרם ממילא אינו מגיע לווילן שלה. פנייה אחת מספיקה.
* **סבב פתוח מקבע את הקצב המהיר.** ההפצה היא מולטיקאסט, והאפיון
  (§28) מכניס מצטרפים מאוחרים לסבב **הבא** — כלומר תחנה שתראה את
  ‏`running` באיחור של 15 שניות לא התעכבה, היא פספסה את הסבב. זה
  הסנכרון עצמו ולא סקירת סרק.
* **התקרה חייבת להישאר מתחת ל-`room.AWAKE_SECONDS` (=30).** מסך החדר
  קורא "דולק" מ-`last_seen` של מחשבי השיכפול, וזה הקורא היחיד של
  הדופק בכל השרת. סקירה איטית מ-30 שניות הייתה מהבהבת אותם.
* **האיפוס הוא שינוי בתשובת השרת**, לא טיימר ולא פעילות מקומית:
  מחשבי השיכפול נדלקים יחד עם מחשב הבנייה בסדר לא ידוע, ואיפוס
  מבוסס-זמן היה משאיר את מי שנדלק ראשון איטי בדיוק כשהעבודה מתחילה.

הסולם עצמו — 2 → 5 → 15 — נבדק כאן על ההתנהגות (מה נמסר ל-`sleep`),
ולא על קריאת הקבועים: הבדיקה מריצה את `poll.sh` האמיתי דרך bash עם
‏`sleep` מזויף, כמו שאר בדיקות הסוכן.
"""

from __future__ import annotations

import pytest

from native import requires_native

from test_agent import AGENT, BASH, posix, sh

# בלי bash אין כאן בדיקה בכלל. במקום שבו bash אמור להיות — כישלון, לא
# ירוק (#52); הדגל הוא שמכריע, בדיוק כמו בשאר בדיקות הסוכן.
pytestmark = requires_native(("bash", BASH))

#: מצב פתיחה של מחשב שיכפול ממתין — סוכן שאין לו עדיין מה לעשות.
IDLE = ("D_SCHEMA=1 D_KNOWN=true D_ROLE=cloner D_TASK=null "
        "D_SESSION_STATE=none D_SESSION_ID=null D_REQUIRE_LOGIN=null")


def run_poll(setup: str, script: str) -> str:
    """מריץ קטע מול `poll.sh` האמיתי, עם `sleep` שמדפיס במקום לישון."""
    return sh(
        f'. {posix(AGENT)}/lib/core/poll.sh; '
        'sleep() { printf "%s " "$1"; }; '
        f'{setup}; {script}'
    ).strip()


# --- הסולם -------------------------------------------------------------------


def test_the_ladder_climbs_two_five_fifteen_and_stops_there():
    """התקרה היא תקרה: הסקירה החמישית עדיין 15, לא 30."""
    out = run_poll(IDLE, "poll_sleep; poll_sleep; poll_sleep; poll_sleep; poll_sleep")
    assert out == "2 5 15 15 15"


def test_the_ceiling_is_fifteen_seconds():
    """הכרעה מודעת של נדב, ולא ברירת מחדל: קליטה שהוזמנה מהקונסולה
    יכולה להמתין עד התקרה עד שמחשב הבנייה ישאל שוב. 15 ולא 30."""
    out = run_poll(IDLE, 'echo "$POLL_FIRST $POLL_MID $POLL_MAX"')
    assert out == "2 5 15"


def test_the_ceiling_stays_under_the_awake_threshold():
    """הדופק של מחשבי השיכפול הוא ה-`last_seen` שלהם, ומסך החדר קורא
    אותו כ"דולק/כבוי". תקרה שתעבור את `AWAKE_SECONDS` תגרום להם
    להבהב — ולכן הקבוע בשרת הוא זה שקושר, לא מספר שנבחר בסוכן."""
    from server.rounds.room import AWAKE_SECONDS                   # noqa: PLC0415

    ceiling = int(run_poll(IDLE, 'echo "$POLL_MAX"'))
    assert ceiling * 2 <= AWAKE_SECONDS


def test_widen_never_passes_the_ceiling():
    out = run_poll(IDLE, 'poll_widen 2; poll_widen 5; poll_widen 15; poll_widen 99')
    assert out.split() == ["5", "15", "15", "15"]


# --- הטריגר: שינוי בתשובה, לא טיימר ------------------------------------------


def test_a_changed_answer_resets_the_ladder():
    """הנימוק של נדב: מחשב שיכפול שנדלק לפני מחשב הבנייה התרחב ל-15
    בזמן שחיכה. ברגע שהתשובה השתנתה — הוא חוזר להיות מהיר.

    האיפוס נבדק כאן על שינוי שאינו סבב פתוח (‏`closed`), כדי שהטריגר
    שנמדד יהיה **השינוי** ולא הבלם של הסבב הפתוח.
    """
    out = run_poll(
        IDLE,
        "poll_sleep; poll_sleep; poll_sleep; "      # 2 5 15 — התרחב עד התקרה
        'D_SESSION_STATE=closed; poll_sleep; '      # התשובה השתנתה — איפוס
        "poll_sleep"                                # ומכאן שוב מטפס
    )
    assert out == "2 5 15 2 5"


def test_a_new_session_id_in_the_same_state_also_resets():
    """סבב אחר באותו מצב הוא תשובה אחרת, לא אותה תשובה."""
    out = run_poll(
        IDLE + " D_SESSION_STATE=running D_SESSION_ID=ses_1",
        "poll_sleep; poll_sleep; D_SESSION_ID=ses_2; poll_sleep"
    )
    assert out == "2 5 2"


# --- הבלם: סבב פתוח מקבע את הקצב המהיר ---------------------------------------


def test_an_open_round_never_widens():
    """ההפצה היא מולטיקאסט. תחנה שתראה את `running` באיחור של 15
    שניות **מפספסת את הסבב** — האפיון (§28) מכניס אותה לסבב הבא.
    זה הסנכרון עצמו, ולכן ההתרחבות אסורה שם, גם אם התשובה זהה."""
    out = run_poll(
        IDLE + " D_SESSION_STATE=open D_SESSION_ID=ses_1",
        "poll_sleep; poll_sleep; poll_sleep; poll_sleep; poll_sleep"
    )
    assert out == "2 2 2 2 2"


def test_a_round_that_opened_mid_backoff_pulls_the_rate_back_down():
    """מכונה שהתרחבה עד התקרה בזמן שלא קרה כלום חוזרת מיד לקצב
    המהיר כשהסבב נפתח — ונשארת שם כל עוד הוא פתוח."""
    out = run_poll(
        IDLE,
        "poll_sleep; poll_sleep; poll_sleep; "      # 2 5 15
        'D_SESSION_STATE=open D_SESSION_ID=ses_1; '
        "poll_sleep; poll_sleep; poll_sleep"
    )
    assert out == "2 5 15 2 2 2"


def test_the_ladder_returns_once_the_round_is_no_longer_open():
    """הבלם הוא על "פתוח" בלבד. סבב שנסגר אינו מקבע דבר."""
    out = run_poll(
        IDLE + " D_SESSION_STATE=open D_SESSION_ID=ses_1",
        "poll_sleep; poll_sleep; D_SESSION_STATE=closed; "
        "poll_sleep; poll_sleep; poll_sleep"
    )
    assert out == "2 2 2 5 15"


def test_a_task_that_arrived_resets_the_ladder():
    """המחיר של ה-backoff מוגבל לתקרה אחת: המשימה מתגלה באיחור של עד
    15 שניות, ומאותו רגע המכונה שוב מהירה."""
    out = run_poll(
        IDLE,
        "poll_sleep; poll_sleep; poll_sleep; poll_sleep; "
        'D_TASK=\'{"id":"tsk_1"}\'; poll_sleep'
    )
    assert out == "2 5 15 15 2"


def test_the_cosmetic_counters_are_not_part_of_the_signature():
    """‏joined ו-starts_in_seconds משתנים בכל שנייה בסבב פתוח. אילו
    היו בחתימה, ההתרחבות הייתה מתאפסת תמיד ו-#136 היה חוזר בשקט."""
    source = (AGENT / "lib" / "core" / "poll.sh").read_text(encoding="utf-8")
    body = source[source.index("poll_signature()"):source.index("poll_widen()")]
    code = "\n".join(ln for ln in body.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "joined" not in code
    assert "starts_in_seconds" not in code


def test_nothing_in_the_ladder_reads_a_clock():
    """הטריגר הוא התשובה, ולכן אין ב-`poll.sh` שעון בכלל — לא `date`
    ולא `SECONDS`. זו הבדיקה שמונעת חזרה שקטה ל-backoff מבוסס-זמן."""
    source = (AGENT / "lib" / "core" / "poll.sh").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in source.splitlines() if not ln.lstrip().startswith("#"))
    for clock in ("date ", "$SECONDS", "uptime"):
        assert clock not in code, f"‏poll.sh קורא שעון: {clock}"


# --- תחנה מחוץ לווילן ההפצה: אין לולאה --------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", "off"),      # סבב פתוח ובכל זאת נדרשת כניסה = לא בווילן
        ("false", "on"),      # תחנה בווילן ההפצה בסבב פתוח
        ("null", "on"),       # השרת לא אמר — ממשיכים כמו היום
        ("", "on"),
    ],
)
def test_off_vlan_is_read_from_the_answer_with_positive_evidence(value, expected):
    """ראיה חיובית בלבד. עצירה בטעות של תחנה אמיתית גרועה בהרבה
    מסקירה מיותרת, ולכן כל ערך שאינו "true" ממשיך לסקור."""
    out = run_poll(
        f'{IDLE}; D_REQUIRE_LOGIN={value!r}',
        'if poll_off_deploy_vlan; then echo off; else echo on; fi'
    )
    assert out == expected


def wait_open_branch() -> str:
    """הענף האמיתי מתוך `imagesrv-agent` — לא העתק שלו.

    בדיקה שמריצה עותק של הקוד בודקת את העותק. כאן נחתך הענף מהקובץ
    שנארז ב-initramfs, וכל שינוי בו עובר דרך הבדיקה הזאת.
    """
    lines = (AGENT / "imagesrv-agent").read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == "wait_open)")
    end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == ";;")
    return "\n".join(lines[start + 1:end])


def run_wait_open(require_login: str) -> str:
    """מריץ את הענף עם דמויות במקום כל מה שנוגע בעולם."""
    branch = wait_open_branch()
    return sh(
        f'. {posix(AGENT)}/lib/core/poll.sh; '
        'die_local() { echo "DIE:$1"; exit 0; }; '
        'ui_waiting_draw() { echo DRAW; }; '
        'json_get() { echo null; }; '
        'poll_sleep() { echo POLL; }; '
        f'RESP=/dev/null; {IDLE}; D_SESSION_STATE=open; '
        f'D_REQUIRE_LOGIN={require_login!r}; '
        f'{branch}'
    ).strip().splitlines()


def test_a_station_on_the_class_network_does_not_poll_at_all():
    """הבקרה השלילית של החלק החשוב ביותר: לא backoff — פשוט לא לולאה.

    לפני התיקון הענף הזה צייר מסך המתנה ונרדם לשתי שניות, לנצח.
    """
    out = run_wait_open("true")
    assert out[0].startswith("DIE:"), f"התחנה לא נשלחה לדיסק המקומי: {out}"
    assert "POLL" not in out, "תחנה מחוץ לווילן ההפצה עדיין סוקרת בלולאה"
    assert "DRAW" not in out, "מסך המתנה לסבב שלא יגיע לווילן הזה"


def test_the_waiting_station_never_slows_down_across_iterations():
    """הענף האמיתי, ארבעה סיבובים, ‏`poll_sleep` האמיתי — והקצב נשאר 2.

    זו הדרישה שאחריה נדב חזר: לא "backoff עדין" בתחנה ממתינה אלא
    **אין backoff**. ארבעה סיבובים כי אחרי שלושה הסולם כבר היה בתקרה.
    """
    branch = wait_open_branch()
    out = sh(
        f'. {posix(AGENT)}/lib/core/poll.sh; '
        'die_local() { echo "DIE:$1"; exit 9; }; '
        'ui_waiting_draw() { :; }; json_get() { echo null; }; '
        'sleep() { printf "%s " "$1"; }; '
        f'RESP=/dev/null; {IDLE}; '
        'D_SESSION_STATE=open D_SESSION_ID=ses_1 D_REQUIRE_LOGIN=false; '
        f'for _i in 1 2 3 4; do\n{branch}\ndone'
    ).strip()
    assert out == "2 2 2 2"


def test_a_station_on_the_deployment_vlan_keeps_waiting():
    """הצד השני של אותו משפט — וזה תרחיש ה-QA הקריטי: תחנה בסבב פתוח
    ממשיכה לצייר ולסקור, ולכן ממשיכה להצטרף ולאפס את הטיימר."""
    out = run_wait_open("false")
    assert out == ["DRAW", "POLL"]


# --- אין `sleep 2` קבוע שנשאר מאחור ------------------------------------------


def test_no_idle_branch_sleeps_a_flat_two_seconds():
    """המסלולים עברו ל-`poll_sleep`. ‏`sleep 2` היחיד שנשאר הוא
    בנתיב הכישלון של send_hello — ניסיון חוזר, לא סקירת סרק.

    ארבעה `poll_sleep`: wait_open, wait_poll, build_console, ומסלול
    ה-recovery כשהגואי הנייטיב פעיל — אז לולאת ה-hello ממשיכה לרוץ.
    בלי גואי ה-recovery מסתיים בדיסק המקומי (11/10, אין אשף טקסט)."""
    source = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    flat = [ln.strip() for ln in source.splitlines() if ln.strip() == "sleep 2"]
    assert len(flat) == 1, f"נשארו {len(flat)} השהיות קבועות של שתי שניות"

    loop = source[source.index("# --- main loop"):]
    assert loop.count("poll_sleep") == 4


def test_the_agent_loads_the_poll_library():
    source = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    assert '. "$LIB_DIR/poll.sh"' in source
