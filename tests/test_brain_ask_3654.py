"""#3654 (אפיק #3653): **ערוץ אחד שבו המוח שואל את המשתמש** (`server/brain/ask.py`).

הכרעת נדב 18/11: שני מחליטים בלבד -- המוח, והמשתמש בדברים מסוימים. הערוץ הוא המנגנון היחיד של "המשתמש":
שאלה (נושא, אפשרויות, ברירת מחדל בטוחה, תקרת המתנה, מי רשאי לענות), תשובה שנרשמת ב-`brain_decisions`, ופקיעה
לברירת המחדל כשאין תשובה.

מה נבדק:
- **פקיעה אינה תשובה:** שאלה שאיש לא ענה עליה מחזירה את ברירת המחדל הבטוחה, נרשמת ``EXPIRED``, ואף שורת ``ANSWERED``
  לא נכתבת; תשובה אחרי הפקיעה נדחית.
- **פעולה הרסנית לעולם אינה ברירת מחדל:** ``Spec`` כזה נדחה, ו-``resolution`` מחזיר אפשרות הרסנית רק לתשובה של אדם.
- **שאלה פתוחה שורדת הפעלה מחדש** (חיבור חדש ל-DB, ומנגנון סגירת היתומים של הזיכרון לא נוגע בה).
- תשובה נרשמת עם מי, מתי ומה; תשובה שנייה, תשובה של תפקיד שאינו רשאי ואפשרות שאינה ברשימה -- נדחות בשם.
- שאלה פתוחה אחת לכל (נקודה, ישות, סבב); כשל כתיבה = ``None``, לא שאלה בחצי.

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

from server import db
from server.brain import ask, memory

NOW = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
MAC = "aa:bb:cc:00:00:01"

WIPE = ask.Option("wipe", "למחוק את המידע הישן", destructive=True, verb_he="מחיקת המידע",
                  consequence_he="המידע הישן יימחק ולא יהיה אפשר לשחזר אותו")
SKIP = ask.Option("skip", "לדלג ולהשאיר את הדיסק")
SPEC = ask.Spec(point="T1", title_he="נמצא מידע RAID ישן על הדיסק", options=(WIPE, SKIP), safe_default="skip", ttl_s=600)


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "ask.db")
    yield c


def later(seconds: float) -> datetime:
    return NOW + timedelta(seconds=seconds)


def decisions(conn, rule="T1"):
    return [dict(r) for r in conn.execute("SELECT * FROM brain_decisions WHERE rule = ? ORDER BY id", (rule,))]


def verdicts(conn, rule="T1"):
    return [d["verdict"] for d in decisions(conn, rule)]


def opened(conn, spec=SPEC, **kw):
    kw.setdefault("entity", ("machine", MAC))
    kw.setdefault("now", NOW)
    return ask.open_ask(conn, spec, **kw)


# --- ההגדרה ---------------------------------------------------------------------------------------------------------


def test_a_destructive_safe_default_is_refused_when_the_question_is_built():
    with pytest.raises(ValueError, match="destructive"):
        ask.Spec(point="T1", title_he="?", options=(WIPE, SKIP), safe_default="wipe", ttl_s=600)


@pytest.mark.parametrize("bad", [
    dict(safe_default="nothing"),                         # לא אחת האפשרויות
    dict(options=(SKIP,)),                                # פחות משתיים
    dict(options=(SKIP, ask.Option("skip", "שוב"))),      # מזהה כפול
    dict(options=(SKIP, ask.Option("Wipe It", "x"))),     # מזהה לא תקין
    dict(ttl_s=ask.MIN_TTL_S - 1), dict(ttl_s=ask.MAX_TTL_S + 1), dict(ttl_s=True),
    dict(who=()), dict(who=("admin", "deploy")),          # תפקיד שאינו בערוץ
    dict(title_he="  "), dict(point="bad point"),
])
def test_a_malformed_question_is_refused_when_it_is_built(bad):
    fields = dict(point="T1", title_he="?", options=(WIPE, SKIP), safe_default="skip", ttl_s=600)
    fields.update(bad)
    with pytest.raises(ValueError):
        ask.Spec(**fields)


# --- מוקאפ 18/11: פועל, תוויות, עמודות --------------------------------------------------------------------------------


@pytest.mark.parametrize("option", [
    ask.Option("wipe", "למחוק", destructive=True, consequence_he="יימחק"),                 # בלי פועל: הכפתור האדום אילם
    ask.Option("wipe", "למחוק", destructive=True, verb_he="מחיקת המידע"),                   # בלי השלכה: המסך לא אומר מה זה עולה
    ask.Option("wipe", "למחוק", destructive=True, verb_he="  ", consequence_he="יימחק"),
    ask.Option("wipe", "ת" * (ask.MAX_LABEL + 1)),                                           # תווית ארוכה מ-36
    ask.Option("wipe", "למחוק", verb_he="פ" * (ask.MAX_VERB + 1)),
])
def test_an_option_the_mockup_cannot_draw_is_refused_when_the_question_is_built(option):
    with pytest.raises(ValueError):
        ask.Spec(point="T1", title_he="?", options=(option, SKIP), safe_default="skip", ttl_s=600)


def test_a_label_of_exactly_36_characters_and_a_plain_option_without_a_verb_are_fine():
    ask.Spec(point="T1", title_he="?", options=(ask.Option("a", "ת" * ask.MAX_LABEL), SKIP), safe_default="skip", ttl_s=600)


def test_the_view_carries_each_options_verb_and_cost_and_the_number_of_columns(conn):
    ask_id = opened(conn)
    got = ask.open_asks(conn, NOW)[0]
    assert got["id"] == ask_id and got["columns"] == 1 and got["ttl_s"] == 600
    wipe = next(o for o in got["options"] if o["id"] == "wipe")
    assert (wipe["verb_he"], wipe["consequence_he"], wipe["outcome_he"]) == ("מחיקת המידע", WIPE.consequence_he, "")
    skip = next(o for o in got["options"] if o["id"] == "skip")
    assert skip["verb_he"] == "" and skip["destructive"] is False


@pytest.mark.parametrize("count, columns", [(2, 1), (5, 1), (6, 2), (8, 2)])
def test_more_than_five_options_are_two_columns(conn, count, columns):
    options = tuple(ask.Option(f"o{i}", f"אפשרות {i}") for i in range(count))
    spec = ask.Spec(point="T9", title_he="הרבה", options=options, safe_default="o0", ttl_s=600)
    opened(conn, spec)
    assert ask.open_asks(conn, NOW)[0]["columns"] == columns


def test_a_row_written_before_the_verb_field_still_reads_with_empty_fields(conn):
    ask_id = opened(conn)
    conn.execute("UPDATE brain_asks SET options_json = ? WHERE id = ?", (json.dumps(
        [{"id": "wipe", "label_he": "למחוק", "destructive": True}, {"id": "skip", "label_he": "לדלג", "destructive": False}]),
        ask_id))
    conn.commit()
    assert ask.open_asks(conn, NOW)[0]["options"][0]["verb_he"] == ""


# --- היסטוריה של 30 יום -----------------------------------------------------------------------------------------------


def _closed(conn, how, *, at, mac=MAC, kind="machine", by="noc", role=ask.ADMIN, point="T1", title=None):
    spec = SPEC if title is None else ask.Spec(point=point, title_he=title, options=(WIPE, SKIP), safe_default="skip",
                                                 ttl_s=600)
    ask_id = ask.open_ask(conn, spec, entity=(kind, mac), now=at)
    if how == "answered":
        ask.answer(conn, ask_id, "wipe", by=by, role=role, now=at + timedelta(seconds=30))
    elif how == "cancelled":
        ask.cancel(conn, ask_id, "הסבב נסגר לפני שנענתה", at + timedelta(seconds=30))
    else:
        ask.expire_due(conn, at + timedelta(seconds=700))
    return ask_id


def test_the_history_is_the_last_30_days_newest_first_and_says_how_many_there_were(conn):
    old = _closed(conn, "answered", at=NOW - timedelta(days=29, hours=23), mac="aa:bb:cc:00:00:09")
    mid = _closed(conn, "expired", at=NOW - timedelta(days=3), mac="aa:bb:cc:00:00:08")
    new = _closed(conn, "answered", at=NOW - timedelta(hours=1))
    got = ask.history(conn, now=NOW)
    assert [a["id"] for a in got["items"]] == [new, mid, old] and got["total"] == 3 and got["days"] == 30
    week = ask.history(conn, days=7, now=NOW)
    assert [a["id"] for a in week["items"]] == [new, mid] and week["total"] == 2     # total = בטווח שנבחר, בלי הסינון
    assert ask.history(conn, days=400, now=NOW)["days"] == 30           # אין טווח מעל מה שנשמר


def test_a_question_closed_more_than_30_days_ago_is_dropped_but_a_fresh_or_open_one_stays(conn):
    ancient = _closed(conn, "answered", at=NOW - timedelta(days=40), mac="aa:bb:cc:00:00:07")
    fresh = _closed(conn, "answered", at=NOW - timedelta(days=2))
    long_spec = ask.Spec(point="T5", title_he="פתוחה", options=(WIPE, SKIP), safe_default="skip", ttl_s=86400)
    still_open = opened(conn, long_spec, now=NOW - timedelta(hours=1))
    ask.expire_due(conn, NOW)               # הניקוי רץ עם שעון הפקיעה, לא בקריאה נפרדת
    ids = {r[0] for r in conn.execute("SELECT id FROM brain_asks")}
    assert ids == {fresh, still_open} and ancient not in ids


@pytest.mark.parametrize("kw, expected", [
    ({"state": "answered"}, {"a_admin", "a_machine", "img"}), ({"state": "expired"}, {"exp"}), ({"state": "cancelled"}, {"can"}),
    ({"who": "admin"}, {"a_admin", "img"}), ({"who": "machine"}, {"a_machine"}), ({"who": "none"}, {"exp", "can"}),
    ({"kind": "image"}, {"img"}), ({"q": "dana"}, {"img"}), ({"q": "דיסק מיוחד"}, {"exp"}), ({"q": "100%"}, set()),
    ({"state": "answered", "who": "machine"}, {"a_machine"}), ({}, {"a_admin", "a_machine", "exp", "can", "img"}),
])
def test_the_history_filters_by_result_who_answered_what_about_and_text(conn, kw, expected):
    ids = {"a_admin": _closed(conn, "answered", at=NOW - timedelta(hours=5), by="noc"),
           "a_machine": _closed(conn, "answered", at=NOW - timedelta(hours=4), by=MAC, role=ask.MACHINE, point="T2",
                                title="שאלה שנייה"),
           "exp": _closed(conn, "expired", at=NOW - timedelta(hours=3), point="T7", title="דיסק מיוחד נמצא"),
           "can": _closed(conn, "cancelled", at=NOW - timedelta(hours=2), mac="aa:bb:cc:00:00:05"),
           "img": _closed(conn, "answered", at=NOW - timedelta(hours=1), kind="image", mac="office365", by="dana")}
    got = ask.history(conn, now=NOW, **kw)
    assert {a["id"] for a in got["items"]} == {ids[k] for k in expected} and got["total"] == 5


@pytest.mark.parametrize("kw", [{"state": "open"}, {"state": "nonsense"}, {"who": "deploy"}, {"kind": "planet"}])
def test_a_filter_that_is_not_understood_is_refused_not_read_as_everything(conn, kw):
    _closed(conn, "answered", at=NOW - timedelta(hours=1))
    with pytest.raises(ValueError):
        ask.history(conn, now=NOW, **kw)


def test_a_cancelled_question_says_why_and_the_other_states_do_not(conn):
    can = _closed(conn, "cancelled", at=NOW - timedelta(hours=2))
    ans = _closed(conn, "answered", at=NOW - timedelta(hours=1), mac="aa:bb:cc:00:00:06")
    notes = {a["id"]: a["closed_note_he"] for a in ask.history(conn, now=NOW)["items"]}
    assert notes == {can: "הסבב נסגר לפני שנענתה", ans: None}


def test_the_history_counts_what_was_answered_and_what_expired_today(conn):
    today = NOW.astimezone().replace(hour=0, minute=1, second=0, microsecond=0).astimezone(timezone.utc)
    now = today + timedelta(hours=2)
    _closed(conn, "answered", at=today + timedelta(minutes=5), mac="aa:bb:cc:00:00:04")
    _closed(conn, "expired", at=today + timedelta(minutes=10), mac="aa:bb:cc:00:00:03")
    _closed(conn, "answered", at=today - timedelta(days=1), mac="aa:bb:cc:00:00:02")
    assert ask.history(conn, now=now)["today"] == {"answered": 1, "expired": 1}


# --- פתיחה ---------------------------------------------------------------------------------------------------------


def test_opening_a_question_records_it_in_brain_decisions_and_keeps_it_open(conn):
    ask_id = opened(conn, round_id="room_1")
    [row] = decisions(conn)
    assert (row["action"], row["verdict"], row["mode"], row["entity_kind"], row["entity_key"], row["round_id"]) == (
        "ASK", "ASKED", "shadow", "machine", MAC, "room_1")
    assert json.loads(row["evidence_json"])[0] == ask_id
    assert row["outcome"] is not None, "the opening row must not be closed as 'the server restarted' by memory"
    [item] = ask.open_asks(conn, NOW)
    assert (item["id"], item["state"], item["safe_default"], item["expires_in_s"]) == (ask_id, "open", "skip", 600)
    assert [o["id"] for o in item["options"]] == ["wipe", "skip"] and item["options"][0]["destructive"] is True


def test_one_open_question_per_point_machine_and_round(conn):
    first = opened(conn)
    assert opened(conn, now=later(5)) == first
    assert len(ask.open_asks(conn, later(5))) == 1 and verdicts(conn) == ["ASKED"]
    other_machine = opened(conn, entity=("machine", "aa:bb:cc:00:00:02"))
    other_round = opened(conn, round_id="room_2")
    assert len({first, other_machine, other_round}) == 3


def test_a_question_that_expired_does_not_block_the_next_one(conn):
    first = opened(conn)
    second = opened(conn, now=later(601))
    assert second != first
    assert [r["state"] for r in conn.execute("SELECT state FROM brain_asks ORDER BY id")] == ["expired", "open"]


def test_a_question_that_could_not_be_opened_is_none_not_half_opened(conn):
    assert opened(conn, entity=("nothing", "x")) is None
    assert opened(conn, mode="maybe") is None
    assert conn.execute("SELECT COUNT(*) FROM brain_asks").fetchone()[0] == 0 and decisions(conn) == []
    conn.execute("DROP TABLE brain_asks")
    assert opened(conn) is None               # הכתיבה נכשלה: הקורא פועל כמו כשאין תשובה, לא ממתין


# --- תשובה ---------------------------------------------------------------------------------------------------------


def test_an_answer_is_recorded_with_who_when_and_what_next_to_the_brains_decision(conn):
    ask_id = opened(conn)
    got = ask.answer(conn, ask_id, "wipe", by="nadav", role="admin", now=later(30))
    assert (got["state"], got["answer"], got["answered_by"], got["answered_role"], got["closed_at"]) == (
        "answered", "wipe", "nadav", "admin", later(30).isoformat(timespec="seconds"))
    assert verdicts(conn) == ["ASKED", "ANSWERED"]
    closing = decisions(conn)[1]
    assert "nadav" in closing["reason"] and "למחוק את המידע הישן" in closing["reason"] and closing["outcome"] is not None
    assert (closing["entity_kind"], closing["entity_key"]) == ("machine", MAC)
    res = ask.resolution(conn, ask_id, later(31))
    assert (res.state, res.option, res.by_user) == ("answered", "wipe", True)


def test_a_second_answer_does_not_overwrite_the_first(conn):
    ask_id = opened(conn)
    ask.answer(conn, ask_id, "skip", by="nadav", role="admin", now=later(10))
    with pytest.raises(ask.AskError) as raised:
        ask.answer(conn, ask_id, "wipe", by=MAC, role="machine", now=later(11))
    assert (raised.value.code, raised.value.status) == ("closed", 409)
    assert ask.resolution(conn, ask_id, later(12)).option == "skip" and verdicts(conn) == ["ASKED", "ANSWERED"]


def test_two_people_answering_at_once_leave_exactly_one_answer(tmp_path):
    path = tmp_path / "race.db"
    first = db.connect(path)
    ask_id = opened(first)
    results, barrier = [], threading.Barrier(2)

    def go(option, name):
        mine = db.connect(path)
        barrier.wait()
        try:
            results.append(ask.answer(mine, ask_id, option, by=name, role="admin", now=later(5))["answer"])
        except ask.AskError as exc:
            results.append(exc.code)
        finally:
            mine.close()

    threads = [threading.Thread(target=go, args=args) for args in (("wipe", "a"), ("skip", "b"))]
    [t.start() for t in threads]
    [t.join(10) for t in threads]
    assert sorted(results, key=str).count("closed") == 1 and len(results) == 2
    assert verdicts(first).count("ANSWERED") == 1


@pytest.mark.parametrize("option, role, code, status", [
    ("wipe", "machine", "forbidden", 403),        # השאלה הזו רק למנהל
    ("format_everything", "admin", "bad_option", 400),
    ("", "admin", "bad_option", 400),
])
def test_an_answer_from_the_wrong_role_or_outside_the_list_is_refused_by_name(conn, option, role, code, status):
    spec = ask.Spec(point="T1", title_he="?", options=(WIPE, SKIP), safe_default="skip", ttl_s=600, who=("admin",))
    ask_id = opened(conn, spec)
    with pytest.raises(ask.AskError) as raised:
        ask.answer(conn, ask_id, option, by="x", role=role, now=later(1))
    assert (raised.value.code, raised.value.status) == (code, status)
    assert ask.resolution(conn, ask_id, later(2)).state == "open" and verdicts(conn) == ["ASKED"]


def test_an_answer_to_a_question_that_does_not_exist_is_unknown(conn):
    with pytest.raises(ask.AskError) as raised:
        ask.answer(conn, 4242, "skip", by="nadav", role="admin", now=NOW)
    assert (raised.value.code, raised.value.status) == ("unknown", 404)


# --- פקיעה: ברירת המחדל הבטוחה, ולא תשובה -----------------------------------------------------------------------------


def test_a_question_nobody_answers_returns_the_safe_default_and_is_recorded_as_expired_not_answered(conn):
    ask_id = opened(conn)
    assert ask.resolution(conn, ask_id, later(599)).state == "open"
    res = ask.resolution(conn, ask_id, later(600))
    assert (res.state, res.option, res.by_user) == ("expired", "skip", False)
    assert verdicts(conn) == ["ASKED", "EXPIRED"], "an unanswered question must not be folded into an answer"
    closing = decisions(conn)[1]
    assert "פקיעה, לא תשובה" in closing["reason"] and "לא נענתה" in closing["outcome"]
    row = conn.execute("SELECT * FROM brain_asks WHERE id = ?", (ask_id,)).fetchone()
    assert (row["state"], row["answer"], row["answered_by"]) == ("expired", None, None)


def test_the_expiry_clock_closes_what_is_due_once_and_leaves_the_rest(conn):
    due = opened(conn)
    live = opened(conn, entity=("machine", "aa:bb:cc:00:00:02"), now=later(300))
    assert ask.expire_due(conn, later(601)) == 1 and ask.expire_due(conn, later(602)) == 0
    assert ask.resolution(conn, due, later(602)).state == "expired" and ask.resolution(conn, live, later(602)).state == "open"
    assert verdicts(conn).count("EXPIRED") == 1


def test_an_answer_that_arrives_after_the_expiry_is_refused_and_the_default_stands(conn):
    ask_id = opened(conn)
    with pytest.raises(ask.AskError) as raised:
        ask.answer(conn, ask_id, "wipe", by="nadav", role="admin", now=later(601))
    assert raised.value.code == "closed" and "פקעה" in raised.value.message_he
    res = ask.resolution(conn, ask_id, later(602))
    assert (res.state, res.option, res.by_user) == ("expired", "skip", False)
    assert "ANSWERED" not in verdicts(conn)


def test_a_destructive_option_is_performed_only_on_a_users_answer(conn):
    expired, withdrawn, answered = opened(conn), opened(conn, round_id="r2"), opened(conn, round_id="r3")
    ask.cancel(conn, withdrawn, "הדיסק הוצא", NOW)
    ask.answer(conn, answered, "wipe", by="nadav", role="admin", now=later(5))
    got = {name: ask.resolution(conn, i, later(601)) for name, i in
           (("expired", expired), ("cancelled", withdrawn), ("answered", answered))}
    assert {k: (v.option, v.by_user) for k, v in got.items()} == {
        "expired": ("skip", False), "cancelled": ("skip", False), "answered": ("wipe", True)}


def test_a_stored_destructive_safe_default_is_never_performed(conn):
    ask_id = opened(conn)
    conn.execute("UPDATE brain_asks SET safe_default = 'wipe' WHERE id = ?", (ask_id,))
    conn.commit()
    res = ask.resolution(conn, ask_id, later(601))
    assert (res.state, res.option, res.by_user) == ("expired", None, False)
    assert "שום פעולה לא בוצעה" in decisions(conn)[-1]["outcome"]


def test_an_unknown_question_has_no_resolution_which_is_not_the_default(conn):
    assert ask.resolution(conn, 99, NOW) is None


def test_cancelling_returns_the_default_and_is_recorded(conn):
    ask_id = opened(conn)
    assert ask.cancel(conn, ask_id, "המכונה כבתה", later(5)) is True and ask.cancel(conn, ask_id, "שוב", later(6)) is False
    assert verdicts(conn) == ["ASKED", "CANCELLED"] and "המכונה כבתה" in decisions(conn)[1]["reason"]
    assert ask.resolution(conn, ask_id, later(7)).option == "skip"


# --- הפעלה מחדש ----------------------------------------------------------------------------------------------------


def test_an_open_question_survives_a_restart_of_the_server(tmp_path, monkeypatch):
    path = tmp_path / "restart.db"
    first = db.connect(path)
    ask_id = opened(first)
    first.close()
    # תהליך חדש: הזיכרון סוגר כל החלטה פתוחה שנשארה מתהליך קודם. השאלה חיה, ושורת ה-ASK שלה אינה נסגרת כ"הופעל מחדש"
    monkeypatch.setattr(memory, "_STARTED", (NOW + timedelta(minutes=1)).isoformat(timespec="seconds"))
    monkeypatch.setattr(memory, "_orphans_closed", False)
    second = db.connect(path)
    [item] = ask.open_asks(second, later(120))
    assert item["id"] == ask_id and item["expires_in_s"] == 600 - 120
    memory.record(second, mode="shadow", rule="X", action="ASK", verdict="ASKED", reason="r")   # מפעיל את סגירת היתומים
    [opening] = decisions(second)
    assert opening["outcome"] != memory.ORPHAN_HE and opening["outcome_at"] is not None
    ask.answer(second, ask_id, "skip", by="nadav", role="admin", now=later(130))
    assert verdicts(second) == ["ASKED", "ANSWERED"]


# --- מה המכונה רואה ------------------------------------------------------------------------------------------------


def test_a_machine_sees_its_own_open_questions_and_what_became_of_the_closed_ones(conn):
    mine = opened(conn)
    opened(conn, entity=("machine", "aa:bb:cc:00:00:02"))
    assert [a["id"] for a in ask.for_machine(conn, MAC, later(10))] == [mine]
    ask.answer(conn, mine, "wipe", by="nadav", role="admin", now=later(20))
    [seen] = ask.for_machine(conn, MAC, later(60))
    assert (seen["state"], seen["effective"], seen["by_user"]) == ("answered", "wipe", True)
    assert ask.for_machine(conn, MAC, later(20 + ask.RECENT_CLOSED_S + 1)) == []


def test_an_expired_question_tells_the_machine_the_default_and_that_nobody_answered(conn):
    opened(conn)
    [seen] = ask.for_machine(conn, MAC, later(700))
    assert (seen["state"], seen["effective"], seen["by_user"], seen["answer"]) == ("expired", "skip", False, None)


def test_a_question_the_machine_may_not_answer_is_not_sent_to_it(conn):
    spec = ask.Spec(point="T1", title_he="?", options=(WIPE, SKIP), safe_default="skip", ttl_s=600, who=("admin",))
    opened(conn, spec)
    assert ask.for_machine(conn, MAC, later(1)) == []
    assert len(ask.open_asks(conn, later(1))) == 1


def test_the_console_line_for_an_ask_row_is_hebrew_and_says_who_it_is_about(conn):
    opened(conn)
    ask.expire_due(conn, later(700))
    lines = [d["line_he"] for d in reversed(memory.decisions(conn, rule="T1"))]
    assert lines[0].startswith(f"משכפל {MAC} → שאלה למשתמש: נשאלה") and "פקעה בלי תשובה" in lines[1]


# --- החיווט: שעון החדר סוגר את מה שהגיע זמנו, בלי שמישהו שואל ----------------------------------------------------------


def test_the_room_clock_expires_a_due_question_with_nobody_asking(tmp_path):
    """פונקציה שקיימת ואינה נקראת נראית בדיוק כמו פונקציה שעובדת."""
    import time

    from fastapi.testclient import TestClient

    from conftest import MANIFEST_256, Clock, write_image
    from server.app import create_app

    images = tmp_path / "images"
    write_image(images, MANIFEST_256)
    app = create_app(tmp_path / "data", images, "http://10.44.12.10:8080", now_fn=Clock(), room_clock_interval=0.02)
    ctx = app.state.ctx
    started = datetime.now(timezone.utc) - timedelta(hours=1)
    ask_id = opened(ctx.conn, now=started)            # נפתחה לפני שעה, ותקרתה עשר דקות
    expired = False
    try:
        with TestClient(app):                         # __enter__ מריץ את ה-lifespan (השעון)
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if ctx.conn.execute("SELECT state FROM brain_asks WHERE id = ?", (ask_id,)).fetchone()["state"] == "expired":
                    expired = True
                    break
                time.sleep(0.1)
    finally:
        ctx.sender.stop()
    assert expired, "the room clock never expired the due question"
    assert verdicts(ctx.conn) == ["ASKED", "EXPIRED"]
