"""#3064, שלב 2 -- המתג של המוח כהגדרה בקונסולה (`GET/POST /api/console/settings`).

`brain_veto_mode` (off / shadow / enforce): ברירת מחדל כשלא נקבע (shadow) -- הערך שבתוקף, לא "לא נקבע"; כתיבה רק למנהל;
שורת יומן עברית; ומה שנכתב כאן הוא מה שהמוח קורא (`veto.mode`).

**ספים טכניים אינם הגדרות** (נדב 14/11 בערב: "הספים בהגדרות כנראה מפריעים למוח. הוא צריך לשלוט בהם, והם לא צריכים להיות
בהגדרות"): `brain_suspect_seconds` (KT2), `brain_stalled_seconds`, `brain_hold_ceiling_seconds`, `brain_memory_min_returns`
ו-`brain_memory_max_age_days` אינם ב-`GET`, הכתיבה אליהם נדחית "הגדרה לא מוכרת", ערך שנשאר שמור ב-DB אינו נקרא ואינו
נמחק, וביומן שורה אחת שאומרת זאת. מה שנשאר: המתג, ותקרת רשת ההפצה (`brain_net_ceiling_mbps`, הגבול העליון: נדב אמר שהוא בהגדרות).

**בקרה שלילית:** הוצאת `*brain_veto.KEYS` מ-`WRITE_SETTINGS` ב-`console/api.py` מפילה את טסטי המתג; החזרת קריאה של הגדרה
ל-`veto.config` מפילה את טסט ההתעלמות.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from server.brain import recall, veto

def test_unset_mode_reads_as_its_default_not_as_unset(server):
    assert server["admin"].get("/api/console/settings").json()["brain_veto_mode"] == "shadow" == veto.DEFAULT


@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
def test_the_mode_is_saved_read_back_and_is_what_the_veto_reads(server, mode):
    admin, ctx = server["admin"], server["ctx"]
    assert admin.post("/api/console/settings", json={"brain_veto_mode": mode}).status_code == 200
    assert admin.get("/api/console/settings").json()["brain_veto_mode"] == mode
    assert veto.mode(ctx.conn) == mode


@pytest.mark.parametrize("bad", ["on", "ENFORCE", "Shadow", "true", "", " shadow", True, 1, None])
def test_an_unknown_mode_is_refused_and_nothing_is_stored(server, bad):
    admin = server["admin"]
    assert admin.post("/api/console/settings", json={"brain_veto_mode": "enforce"}).status_code == 200
    r = admin.post("/api/console/settings", json={"brain_veto_mode": bad})
    assert r.status_code == 400 and "off, shadow או enforce" in r.json()["detail"]
    assert admin.get("/api/console/settings").json()["brain_veto_mode"] == "enforce"


def test_deploy_cannot_write_them(server):
    assert server["deploy"].post("/api/console/settings", json={"brain_veto_mode": "enforce"}).status_code == 403
    assert server["admin"].get("/api/console/settings").json()["brain_veto_mode"] == "shadow"


def test_the_journal_says_it_in_hebrew_with_the_mode_in_words(server):
    admin = server["admin"]
    admin.post("/api/console/settings", json={"brain_veto_mode": "enforce"})
    rows = admin.get("/api/console/journal", params={"event": "setting_change"}).json()
    texts = [r["text"] for r in rows]
    assert "המוח: וטו על פעולות הרסניות: פעיל (חוסם)" in texts, texts


# --- ספים טכניים יצאו מההגדרות (נדב 14/11 בערב) -------------------------------------------------------------------

#: המפתח -> (ערך שהמפעיל יכול היה לשמור, ערך ההתחלה בקוד כפי שמוצג ביומן)
RETIRED = {"brain_suspect_seconds": ("100", "30"), "brain_stalled_seconds": ("500", "180"),
           "brain_hold_ceiling_seconds": ("600", "1800"), "brain_memory_min_returns": ("7", "3"),
           "brain_memory_max_age_days": ("30", "90")}


#: התוויות של כל חמשת המפתחות (חלקן ב-`recall`)
LABELS = {**veto.LABELS_HE, **recall.LABELS_HE}


def test_the_retired_settings_are_exactly_the_five_technical_thresholds():
    assert set(veto.RETIRED) == set(RETIRED)
    assert veto.KEYS == ("brain_veto_mode",)


@pytest.mark.parametrize("key", sorted(RETIRED))
def test_a_technical_threshold_is_no_longer_a_setting(server, key):
    """אינו ב-`GET`, והכתיבה אליו נדחית בלי לשמור דבר -- גם בערך שהיה חוקי אתמול."""
    admin = server["admin"]
    assert key not in admin.get("/api/console/settings").json()
    r = admin.post("/api/console/settings", json={key: RETIRED[key][0]})
    assert r.status_code == 400 and r.json()["detail"] == f"הגדרה לא מוכרת: {key}"
    assert key not in admin.get("/api/console/settings").json()


def test_the_network_ceiling_stays_a_setting(server):
    """הגבול העליון (נדב 14/11): המוח מודד ומתאים בתוכו, והוא נשאר במסך."""
    admin = server["admin"]
    assert "brain_net_ceiling_mbps" in admin.get("/api/console/settings").json()
    assert admin.post("/api/console/settings", json={"brain_net_ceiling_mbps": "600"}).status_code == 200


def test_values_the_operator_saved_before_are_ignored_not_deleted_and_the_journal_says_so(server):
    from server.brain import decide
    from server.db import get_setting, set_setting
    ctx = server["ctx"]
    for key, (saved, _shown) in RETIRED.items():
        set_setting(ctx.conn, key, saved)
    assert veto.config(ctx.conn) == decide.Config(lost_s=veto.config(ctx.conn).lost_s)    # the code's start values, nothing saved is read
    assert sorted(veto.note_retired(ctx.conn)) == sorted(RETIRED)
    for key, (saved, shown) in RETIRED.items():
        assert get_setting(ctx.conn, key) == saved                                  # never deleted
        [row] = [r for r in ctx.conn.execute("SELECT detail FROM journal WHERE event = 'brain_setting_retired'")
                 if f"({saved})" in r["detail"] and LABELS[key] in r["detail"]]
        assert f"בקוד הוא {shown} " in row["detail"] and "לא נמחק" in row["detail"], row["detail"]
    veto.note_retired(ctx.conn)                                                     # the next start: the same values, the same lines, once
    assert ctx.conn.execute("SELECT COUNT(*) FROM journal WHERE event = 'brain_setting_retired'").fetchone()[0] == 5
    set_setting(ctx.conn, "brain_suspect_seconds", "45")                           # a different saved value is news
    veto.note_retired(ctx.conn)
    assert ctx.conn.execute("SELECT COUNT(*) FROM journal WHERE event = 'brain_setting_retired'").fetchone()[0] == 6


def test_nothing_saved_nothing_noted(server):
    ctx = server["ctx"]
    assert veto.note_retired(ctx.conn) == []
    assert ctx.conn.execute("SELECT COUNT(*) FROM journal WHERE event = 'brain_setting_retired'").fetchone()[0] == 0


def test_a_journal_that_cannot_be_written_does_not_stop_the_server_from_starting(server, monkeypatch):
    from server.db import set_setting
    ctx = server["ctx"]
    set_setting(ctx.conn, "brain_suspect_seconds", "100")
    monkeypatch.setattr(veto, "journal", lambda *a, **k: 1 / 0)
    assert veto.note_retired(ctx.conn) == []                                    # nothing noted, and nothing raised


def test_the_server_notes_it_when_it_starts(tmp_path, images_root, clock, monkeypatch):
    """`create_app` קורא ל-`note_retired`: ערך שמור במסד שנפתח מחדש נרשם בלי שמישהו קרא לפונקציה."""
    from conftest import _build_server
    from server.db import connect, get_setting, set_setting
    from test_sender import Recorder
    from server.rounds import sender as sender_module
    monkeypatch.setattr(sender_module, "port_holders", lambda port: [])
    data = tmp_path / "data"
    data.mkdir()
    pre = connect(data / "imagesrv.db")
    set_setting(pre, "brain_suspect_seconds", "77")
    pre.close()
    bundle = _build_server(tmp_path, images_root, clock, Recorder(block=True))
    conn = bundle["ctx"].conn
    try:
        assert get_setting(conn, "brain_suspect_seconds") == "77"
        assert conn.execute("SELECT COUNT(*) FROM journal WHERE event = 'brain_setting_retired'").fetchone()[0] == 1
    finally:
        bundle["ctx"].sender.stop()
