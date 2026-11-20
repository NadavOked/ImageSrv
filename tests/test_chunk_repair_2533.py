"""#2533, שלב 3: שלב ההשלמה — ההכרעה (`server/rounds/repair.py`).

מה חסר לכל דיסק נגזר מהיומן שהמשכפל דיווח (טווחים מצטברים). חתיכה שחסרה לשני
מחשבים ומעלה משודרת שוב לכולם; חתיכה שחסרה למחשב אחד — הוא מוריד אותה ב-HTTP.
דיסק שלא הושלם בתקרה נכשל בשמו, ולעולם אינו "מדולג".
"""

from __future__ import annotations

import pytest

from server.brain import repairrule
from server.rounds import chunkledger, repair


def test_missing_is_what_the_ledger_does_not_hold():
    assert repair.missing(10, []) == [(0, 9)]
    assert repair.missing(10, [(0, 9)]) == []
    assert repair.missing(10, [(0, 3), (6, 7)]) == [(4, 5), (8, 9)]
    assert repair.missing(0, []) == []
    assert repair.count([(4, 5), (8, 9)]) == 4
    assert repair.as_text([(0, 3), (7, 7)]) == "0-3,7-7"


def test_the_ledger_only_grows():
    """דיווח ישן שהגיע באיחור אינו מוחק דבר (חוזה 9.2: איחוד בלבד)."""
    assert repair.union([(0, 5)], [(0, 2)]) == [(0, 5)]
    assert repair.union([(0, 2)], [(3, 5), (9, 9)]) == [(0, 5), (9, 9)]


@pytest.mark.parametrize("raw", [
    None, "0-3", [[3, 1]], [[0, 10]], [[-1, 2]], [[0, 2], [2, 4]], [[0, 2], [3, 4]],
    [[4, 5], [0, 1]], [[0, True]], [[0]], [[0.0, 3]],
])
def test_ranges_that_are_not_the_contracts_are_refused_whole(raw):
    """חוזה 9.1: סגורים, ממוינים, בלי חפיפה ובלי צמודים, בתוך 0..N-1. לא "מתקנים"."""
    assert repair.parse_ranges(raw, 10) is None


def test_well_formed_ranges_are_taken_as_they_are():
    assert repair.parse_ranges([[0, 3], [5, 5], [7, 9]], 10) == [(0, 3), (5, 5), (7, 9)]
    assert repair.parse_ranges([], 10) == []


def test_a_chunk_two_machines_lack_is_sent_again_to_everyone_once():
    plan = repair.plan(10, {"a": {"A1": [(0, 9)]},
                            "b": {"B1": [(0, 4), (7, 9)]},
                            "c": {"C1": [(0, 4), (7, 9)]}}, repairs_done=0)
    assert plan.multicast == [(5, 6)]
    assert plan.receive == {"b", "c"} and plan.fetch == {} and plan.failed == {}
    assert not plan.done


def test_a_chunk_one_machine_lacks_is_fetched_by_it_alone():
    plan = repair.plan(10, {"a": {"A1": [(0, 9)]}, "b": {"B1": [(0, 6), (9, 9)]}}, repairs_done=0)
    assert plan.multicast == [] and plan.receive == set()
    assert plan.fetch == {"b": [(7, 8)]}


def test_a_receiver_is_a_machine_not_a_disk():
    """חבילה שאבדה אבדה לכל המגירות של אותו משכפל: שלושה דיסקים שחסרה להם אותה
    חתיכה הם מקלט אחד, וההורדה שלו ממלאת את שלושתם."""
    plan = repair.plan(10, {"a": {"A1": [(0, 9)]},
                            "b": {"B1": [(0, 8)], "B2": [(0, 8)], "B3": [(0, 7)]}}, repairs_done=0)
    assert plan.multicast == []
    assert plan.fetch == {"b": [(8, 9)]}


def test_a_machine_gets_one_action_a_round_and_its_own_gap_waits_for_the_next():
    first = repair.plan(10, {"a": {"A1": [(0, 0), (2, 4), (6, 9)]},        # lacks 1 and 5
                             "b": {"B1": [(0, 4), (6, 9)]}}, repairs_done=0)  # lacks 5
    assert first.multicast == [(5, 5)] and first.receive == {"a", "b"} and first.fetch == {}
    after = repair.plan(10, {"a": {"A1": [(0, 0), (2, 9)]}, "b": {"B1": [(0, 9)]}}, repairs_done=1)
    assert after.multicast == [] and after.fetch == {"a": [(1, 1)]}


def test_nothing_missing_is_done():
    plan = repair.plan(10, {"a": {"A1": [(0, 9)]}, "b": {"B1": [(0, 9)], "B2": [(0, 9)]}}, repairs_done=0)
    assert plan.done and plan.failed == {}


def test_a_disk_that_missed_most_of_the_first_pass_with_no_sign_it_was_slow_is_completed():
    """#3141 (הכרעת נדב (א), 14/11): חוסר הוא לא ראיה לדיסק איטי. 60% שלא הגיעו -- ניתוק רשת, חבילות שאבדו -- מושלמים מהשרת.
    עד כאן "יותר מחצי חסר" היה "לא עמד בקצב השידור", והניתוק של ארבע דקות במעבדה הכשיל דיסק תקין ומחק לו את ה-GPT."""
    plan = repair.plan(1000, {"a": {"A1": [(0, 998)]},
                              "b": {"B1": [(0, 399)], "B2": [(0, 999)]}}, repairs_done=0)
    assert plan.failed == {}
    assert plan.multicast == [(999, 999)] and plan.receive == {"a", "b"}     # b's own 400..998 wait for the next round


def test_a_drive_slower_than_the_minimum_is_not_failed_it_completes_like_any_gap():
    """#3170 מול #3295 (נדב 15/11, אפשרות ב'): "ההפצה בקצב של המהירים. האיטי מפספס חתיכות ומשלים אותן אחר כך". 600 מתוך
    1,000 החלקים שהגיעו לא נכתבו -- כ-40MB/s מתוך 100 שהזרם ביקש, מתחת ל-50 -- ואין לו כשל משלו: הוא **אינו נכשל**, נרשם
    ב-`plan.slow` ליומן, ומה שחסר לו משלים כמו כל חוסר (כאן: החתיכה האחרונה משותפת, והשאר ממתין לסבב הבא)."""
    plan = repair.plan(1000, {"a": {"A1": [(0, 998)]}, "b": {"B1": [(0, 399)], "B2": [(0, 999)]}}, repairs_done=0,
                       skipped={"b": {"B1": 600, "B2": 0}}, asked_mbps=100, floor_mbps=50)
    assert plan.failed == {}
    assert set(plan.slow) == {("b", "B1")}
    why = plan.slow[("b", "B1")]
    assert "פיגר" in why and "כ-40MB/s" in why and "(50MB/s)" in why and "אינו נכשל" in why, why
    assert plan.multicast == [(999, 999)] and plan.receive == {"a", "b"}      # b's own 400..998 wait for the next round
    assert plan.lost_in_net == {}                    # all it lacks is what it skipped: nothing was lost on the way


def test_the_hephaestus_wave_a_700m_wave_and_a_drive_at_66_under_74_is_completed_not_failed():
    """הפייסטוס, ברזל 15/11: גל של 700m (~87.5MB/s), ה-APS כתב בכ-66MB/s מול סף של 74. עד כאן "לא עמד בקצב" -- נכשל, החדר
    פתח גל 2 והעיר את המשכפל עד 6 פעמים. עכשיו: מושלם, ואין כשל שיפתח גל."""
    plan = repair.plan(8000, {"aps": {"S1": [(0, 6059)]}, "other": {"S2": [(0, 7999)]}}, repairs_done=0,
                       skipped={"aps": {"S1": 1940}}, asked_mbps=87.5, floor_mbps=74)
    assert plan.failed == {} and set(plan.slow) == {("aps", "S1")}
    assert plan.fetch == {"aps": [(6060, 7999)]}                     # the rest comes over HTTP (or a neighbour), alone


def test_the_same_drive_above_a_lower_minimum_is_completed():
    plan = repair.plan(1000, {"b": {"B1": [(0, 399)]}}, repairs_done=0,
                       skipped={"b": {"B1": 600}}, asked_mbps=100, floor_mbps=30)
    assert plan.failed == {} and plan.fetch == {"b": [(400, 999)]}


def test_what_the_drive_did_not_skip_is_counted_as_lost_on_the_network():
    """#3141: 314 חסרים, 0 מהם של הדיסק -- כולם לא הגיעו (הנתונים מהמעבדה, 211 מתוך 525)."""
    plan = repair.plan(525, {"m": {"S": [(0, 210)]}}, repairs_done=0, skipped={"m": {"S": 0}},
                       asked_mbps=108, floor_mbps=74)
    assert plan.failed == {} and plan.fetch == {"m": [(211, 524)]}
    assert plan.lost_in_net == {"m": 314}


@pytest.mark.parametrize("taken, skipped, asked, floor, slow", [
    (400, 600, 100, 50, True),       # ~40 < 50
    (400, 100, 100, 50, False),      # ~80
    (0, 10, 100, 50, True),          # took nothing that came
    (400, 0, 100, 50, False),        # skipped nothing: not slow, whatever is missing
    (400, None, 100, 50, False),     # not said: no evidence (principle 5b) -- completed, not failed
    (400, 600, None, 50, False),     # no rate asked: no number to judge by
    (400, 600, 100, None, False),
])
def test_slowness_is_judged_only_on_what_the_receiver_said_the_drive_skipped(taken, skipped, asked, floor, slow):
    assert (repair.slow_reason(taken, skipped, asked, floor) is not None) is slow


def test_the_first_pass_ceiling_is_only_the_first_passs():
    """מקלט שנעלם וחזר אחרי כמה סבבים אינו נכשל בגלל הסף של המעבר הראשון."""
    plan = repair.plan(1000, {"b": {"B1": [(0, 399)]}}, repairs_done=1,
                       skipped={"b": {"B1": 600}}, asked_mbps=100, floor_mbps=50)
    assert plan.failed == {} and plan.fetch == {"b": [(400, 999)]} and plan.lost_in_net == {}


def test_a_disk_still_missing_after_the_last_repair_fails_by_name_never_skipped():
    durable = {"a": {"A1": [(0, 9)]}, "b": {"B1": [(0, 8)]}}
    before = repair.plan(10, durable, repairs_done=repairrule.MAX_ROUNDS - 1)
    assert before.failed == {} and before.fetch == {"b": [(9, 9)]}
    plan = repair.plan(10, durable, repairs_done=repairrule.MAX_ROUNDS)
    assert set(plan.failed) == {("b", "B1")}
    assert f"אחרי {repairrule.MAX_ROUNDS} ניסיונות" in plan.failed[("b", "B1")]
    assert plan.done                                # nothing left to send: the disk is failed, not waited for


def test_the_split_reads_range_ends_not_every_chunk():
    """אימג' של 186GB הוא כ-47,500 חתיכות; ההכרעה אינה לולאה עליהן."""
    n = 50_000_000
    plan = repair.plan(n, {"a": {"A1": [(0, n - 2)]}, "b": {"B1": [(0, n - 3)]}}, repairs_done=1)
    assert plan.multicast == [(n - 1, n - 1)] and plan.receive == {"a", "b"}


def test_missing_does_not_trust_its_caller_to_have_sorted_the_ledger():
    """המשלים של היומן (`chunkledger.missing`) מניח רשימה מנורמלת, וטועה בשקט על אחרת."""
    assert repair.missing(10, [(6, 7), (0, 3), (2, 4)]) == [(5, 5), (8, 9)]
    assert repair.missing(10, [(5, 9), (0, 4)]) == []


def test_a_report_is_capped_by_what_a_partition_can_hold_not_by_a_chosen_number(tmp_path):
    """דיסק שחסרה לו כל חתיכה שנייה (50%, התקרה של שלב ההשלמה) מדווח `(chunks + 1) // 2` טווחים.
    הדיווח מתקבל, נרשם ביומן האמיתי ונקרא ממנו; רשימה ארוכה מזה אינה יכולה להיות תקינה."""
    from server import db
    chunks = 20_000
    half = [[2 * i, 2 * i] for i in range(chunks // 2)]                     # 10,000 ranges
    assert repair.parse_ranges(half, chunks) == [(2 * i, 2 * i) for i in range(chunks // 2)]
    assert repair.parse_ranges(half + [[chunks, chunks]], chunks) is None
    assert repair.parse_ranges([[i, i] for i in range(0, 15, 2)], 15) is not None      # 8 of 15: odd sizes too
    conn = db.connect(tmp_path / "imagesrv.db")
    manifest = {"partitions": [{"index": 3, "index_xxh64": "a" * 16, "readback_chunks": chunks}]}
    chunkledger.record(conn, round_id="ses_1", mac="aa:bb:cc:00:00:21", serial="S1", size_bytes=256060514304,
                       image_id="img_aaaaaa", manifest=manifest, durable=[{"part": 3, "ranges": half}])
    row = chunkledger.of_disk(conn, "S1", 256060514304)[3]
    assert len(row["ranges"]) == chunks // 2
    assert repair.missing(chunks, row["ranges"]) == [(2 * i + 1, 2 * i + 1) for i in range(chunks // 2)]

