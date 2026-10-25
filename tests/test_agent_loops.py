"""מחשבים שנופלים לסוכן בלולאה — התסמין של דיסק שלא עולה (‏#112).

מכונה בלי משימה ובלי סבב מקבלת `set default=local`. `hello` ממנה הוא
ראיה חיובית שהסוכן רץ אצלה — כלומר שהשרשור לדיסק המקומי נכשל.

שני דברים נבדקים כאן באותה מידת רצינות: שהמכונה התקועה **מופיעה**,
ושתחנה שיש לה סיבה להגיע **אינה מופיעה כלל**. מסך שמתמלא בכל סבב הוא
מסך שאיש לא קורא, וזה גרוע מלא להציג כלום — כי הוא *נראה* כמו ניטור.

הזמן מוזרק, לא נישן: ל-`note` ול-`current` יש פרמטר `now`, וכל בדיקה
שתלויה בעשר דקות השתיקה מזיזה אותו במקום להמתין.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sqlite3

import pytest

pytest.importorskip("fastapi")

from conftest import hello_body, setup_classroom              # noqa: E402

from server import agent_loops                                # noqa: E402
from server.agent_loops import loop_checks                    # noqa: E402

try:
    from fastapi.testclient import TestClient
except ImportError:                                   # pragma: no cover
    TestClient = None

T0 = "2026-08-29T14:00:00+00:00"


def at(seconds: int) -> str:
    """חותמת יחסית ל-T0, בפורמט שהשרת כותב."""
    return (datetime.fromisoformat(T0)
            + timedelta(seconds=seconds)).isoformat(timespec="seconds")


@pytest.fixture()
def loop_server(tmp_path: Path, images_root: Path, clock):
    """שרת מלא עם בדיקות בריאות מזויפות — ועם ה-ctx ביד.

    כמו `health_server` ב-test_server_health, ובנוסף שולח מזויף: פתיחת
    סבב כאן מבשילה במסלולים אמיתיים, ואסור שתפעיל `udp-sender` (‏#79).
    """
    if TestClient is None:
        pytest.skip("fastapi is required")
    from test_sender import Recorder                           # noqa: PLC0415

    from server import users                                   # noqa: PLC0415
    from server.app import create_app                          # noqa: PLC0415

    tftp = tmp_path / "tftp"
    (tftp / "grub").mkdir(parents=True)
    for name in ("bootx64.efi", "grubx64.efi", "grub/grub.cfg"):
        (tftp / name).write_bytes(b"x")
    hooks = {
        "ss": lambda: "",
        "unit_active": lambda name: "active",
        "http_get": lambda url: 200,
        # ‏/boot/vmlinuz ו-/boot/initrd.img (#333) — מוזרקים כאן כדי
        # שהבדיקה לא תפנה לרשת אמיתית.
        "http_size": lambda url: (200, 31_000_000),
        "interfaces": lambda: [],
        "tftp_root": lambda: tftp,
    }
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     now_fn=clock, health_hooks=hooks,
                     sender_runner=Recorder(block=True))
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    users.create(ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    admin, deploy, anon = TestClient(app), TestClient(app), TestClient(app)
    assert admin.post("/api/console/login", json={
        "username": "noc", "password": "admin-pass-123"}).status_code == 200
    assert deploy.post("/api/console/login", json={
        "username": "labtech", "password": "deploy-pass-1"}).status_code == 200
    bundle = {"app": app, "ctx": ctx, "admin": admin, "deploy": deploy,
              "anon": anon, "clock": clock}
    yield bundle
    ctx.sender.stop()


def hello(server: dict, mac: str) -> dict:
    response = server["anon"].post("/api/v1/agent/hello", json=hello_body(mac))
    assert response.status_code == 200
    return response.json()


def stuck_answer(server: dict, mac: str) -> dict:
    """התשובה שהשרת באמת נותן למכונה הזאת, ולוח נקי אחריה.

    בדיקות שמזריקות שעון אינן יכולות לחיות לצד שורה שנרשמה בשעון
    האמיתי של מכונת הבדיקה — הן היו נשענות על שעת היום שבה הן רצות.
    """
    answer = hello(server, mac)
    conn = server["ctx"].conn
    conn.execute("DELETE FROM agent_loops")
    conn.execute("DELETE FROM journal WHERE event = 'agent_loop'")
    conn.commit()
    return answer


def rows(server: dict) -> dict:
    return {r["id"]: r for r in server["admin"].get("/api/console/health").json()}


def loop_rows(server: dict) -> dict:
    """רק שורות המחשבים, בלי שורת הסיכום."""
    return {k: v for k, v in rows(server).items() if k.startswith("agent_loop:")}


def journal_events(server: dict) -> list[str]:
    return [row["event"] for row in
            server["ctx"].conn.execute("SELECT event FROM journal")]


# --- הגדרת "גמור", סעיפים 1-2: מופיעה, בשורה אחת, עם מונה שעולה ---------------


def test_a_machine_that_reaches_the_agent_with_no_work_is_on_the_screen(loop_server):
    """סעיף 1: מחזור אחד מספיק כדי שהמכונה תופיע."""
    ids = setup_classroom(loop_server)
    answer = hello(loop_server, ids["mac1"])
    # התנאי המוקדם: השרת באמת שלח אותה לדיסק המקומי.
    assert answer["task"] is None and answer["session"] is None

    screen = rows(loop_server)
    assert screen["agent_loops"]["state"] == "bad"
    machine = screen[f"agent_loop:{ids['mac1']}"]
    assert machine["label"] == "כיתה LAB1-05"       # כיתה ושם, לא MAC
    assert machine["state"] == "bad"
    assert "1 פעמים" in machine["detail"]
    assert "נראה לאחרונה" in machine["detail"]


def test_the_same_machine_is_one_row_with_a_rising_counter(loop_server):
    """סעיף 2: שורה אחת למחשב — לא שורה לכל hello."""
    ids = setup_classroom(loop_server)
    for _ in range(3):
        hello(loop_server, ids["mac1"])
    machines = loop_rows(loop_server)
    assert list(machines) == [f"agent_loop:{ids['mac1']}"]
    assert "3 פעמים" in machines[f"agent_loop:{ids['mac1']}"]["detail"]


def test_two_stuck_machines_are_two_rows(loop_server):
    ids = setup_classroom(loop_server)
    hello(loop_server, ids["mac1"])
    hello(loop_server, ids["mac2"])
    assert len(loop_rows(loop_server)) == 2
    assert "2 מחשבים" in rows(loop_server)["agent_loops"]["detail"]


# --- הגדרת "גמור", סעיף 4: מי שיש לו סיבה אינו מופיע כלל ---------------------


def test_a_station_in_an_open_session_never_shows_up(loop_server):
    """**הבדיקה שהמסך הזה עומד עליה.**

    תחנה שמדברת עם הסוכן בזמן סבב פתוח היא התנהגות תקינה לגמרי. אם
    היא תופיע כאן, המסך יתמלא בכל סבב ואיש לא יקרא אותו.
    """
    ids = setup_classroom(loop_server)
    assert loop_server["deploy"].post("/api/console/sessions", json={
        "group_id": ids["group"], "image_id": "img_7f3a91",
        "prefix": "LAB1", "expected_clients": 2,
    }).status_code == 200

    for _ in range(3):
        hello(loop_server, ids["mac1"])
    assert loop_rows(loop_server) == {}
    assert rows(loop_server)["agent_loops"]["state"] == "ok"
    assert "agent_loop" not in journal_events(loop_server)


def test_a_machine_outside_the_roster_is_not_shown_either(loop_server):
    """הנקודה העיוורת שננעלה במודע.

    סבב עם בחירת מחשבים: מי שלא נבחר מקבל דיסק מקומי בדיוק כמו מחשב
    בלי סבב — ואם השרשור שלו נכשל, לא נדע. בזמן סבב לא נספור אותו,
    כי אז המסך היה מתמלא בכל סבב. פחות רגישות, בתמורה למסך שאפשר
    להאמין לו.
    """
    ids = setup_classroom(loop_server)
    assert loop_server["deploy"].post("/api/console/sessions", json={
        "group_id": ids["group"], "image_id": "img_7f3a91", "prefix": "LAB1",
        "expected_clients": 1, "macs": [ids["mac2"]],
    }).status_code == 200
    answer = hello(loop_server, ids["mac1"])
    assert answer["session"] is None            # לא הוזמנה — דיסק מקומי
    assert loop_rows(loop_server) == {}


def test_a_machine_with_a_task_never_shows_up(loop_server):
    """יש לה משימה — השרת שלח אותה לסוכן, וההגעה מוסברת."""
    mac = "aa:bb:cc:00:00:10"
    assert loop_server["admin"].post("/api/console/machines", json={
        "mac": mac, "name": "BUILD-PC", "group_id": "grp_BUILD",
    }).status_code == 200
    assert loop_server["admin"].post("/api/console/tasks/capture", json={
        "mac": mac, "disk": "sda", "name": "Windows 11 Base", "folder": "Office",
    }).status_code == 200
    hello(loop_server, mac)
    assert loop_rows(loop_server) == {}


def test_a_cloner_waiting_on_the_agent_is_not_a_loop(loop_server):
    """ברירת המחדל של מחשב שיכפול היא מסך ההמתנה של הסוכן (`cloner-wait`,
    ‏#17). ‏hello ממנו בלי סבב הוא המצב התקין שלו, לא תקלה."""
    mac = "aa:bb:cc:00:00:21"
    assert loop_server["admin"].post("/api/console/machines", json={
        "mac": mac, "name": "shich-1", "group_id": "grp_CLONERS",
    }).status_code == 200
    for _ in range(3):
        hello(loop_server, mac)
    assert loop_rows(loop_server) == {}


def test_a_build_machine_waiting_for_a_capture_order_is_not_a_loop(loop_server):
    """הבקרה השלילית של #134, שנמדדה על חומרה: 33 פניות ב-66 שניות.

    מחשב בנייה בלי משימה מקבל ``local`` **עם תפריט גלוי** — זו הכרעה
    מכוונת (#140): הוא לא עולה לסוכן מעצמו, אבל אדם שעומד מולו בוחר
    בכך (זרימה 13.1). ואז `agent/lib/decide.sh` שולח אותו ל-
    ``build_console`` להמתין לפקודת קליטה.

    שתי טבלאות ההחלטה נכונות כל אחת בפני עצמה, והגלאי פירש את הפער
    ביניהן כתקלה.
    """
    mac = "aa:bb:cc:00:00:31"
    assert loop_server["admin"].post("/api/console/machines", json={
        "mac": mac, "name": "BUILD-PC", "group_id": "grp_BUILD",
    }).status_code == 200
    for _ in range(5):
        hello(loop_server, mac)
    assert loop_rows(loop_server) == {}


def test_a_hello_from_outside_the_deploy_vlan_is_not_a_loop(loop_server):
    """מחוץ לווילן ההפצה השרת אינו מגיש את שרשרת האתחול, ואין לו ראיה
    שהמכונה עברה בתפריט שלו. שם סוכן הוא בדרך כלל אשף השחזור שאדם
    הפעיל בכוונה (‏#42)."""
    ids = setup_classroom(loop_server)
    ctx = loop_server["ctx"]
    answer = {"schema": 1, "known": True, "role": "classroom",
              "group": {"id": ids["group"], "label": "כיתה LAB1", "suffix": "05"},
              "task": None, "session": None}
    assert agent_loops.unexplained(
        ctx.conn, ctx.store, answer, off_vlan=True) is False
    assert agent_loops.unexplained(
        ctx.conn, ctx.store, answer, off_vlan=False) is True


# --- הגדרת "גמור", סעיף 5: מכונה לא רשומה ------------------------------------


def test_an_unregistered_machine_shows_up_by_mac(loop_server):
    """מחשב זר שנופל לסוכן שוב ושוב הוא בדיוק מה שהמפעיל רוצה לדעת."""
    mac = "de:ad:be:ef:00:99"
    hello(loop_server, mac)
    hello(loop_server, mac)
    machine = loop_rows(loop_server)[f"agent_loop:{mac}"]
    assert machine["label"] == mac                  # אין לו שם, ומוצג ה-MAC
    assert "2 פעמים" in machine["detail"]


# --- הגדרת "גמור", סעיף 3: עשר דקות שתיקה, ושעון מוזרק ------------------------


def test_the_row_survives_nine_minutes_and_drops_after_eleven(loop_server):
    ctx = loop_server["ctx"]
    ids = setup_classroom(loop_server)
    answer = stuck_answer(loop_server, ids["mac1"])
    agent_loops.note(ctx.conn, ctx.store, ids["mac1"], answer, now=T0)

    assert [r["mac"] for r in agent_loops.current(ctx.conn, now=at(540))] \
        == [ids["mac1"]]
    assert agent_loops.current(ctx.conn, now=at(599)) != []
    assert agent_loops.current(ctx.conn, now=at(601)) == []


def test_the_counter_drops_with_the_row(loop_server):
    """מכונה שחוזרת אחרי שתיקה מתחילה ספירה חדשה — לא ממשיכה מונה ישן."""
    ctx = loop_server["ctx"]
    ids = setup_classroom(loop_server)
    answer = stuck_answer(loop_server, ids["mac1"])
    for offset in (0, 120, 240):
        agent_loops.note(ctx.conn, ctx.store, ids["mac1"], answer, now=at(offset))
    assert agent_loops.current(ctx.conn, now=at(240))[0]["hits"] == 3

    # שתיקה ארוכה, ואז המכונה חוזרת: לולאה חדשה, ספירה מאפס.
    assert agent_loops.note(
        ctx.conn, ctx.store, ids["mac1"], answer, now=at(1200)) == 1
    fresh = agent_loops.current(ctx.conn, now=at(1200))[0]
    assert fresh["hits"] == 1 and fresh["first_at"] == at(1200)


def test_how_long_it_has_been_silent_is_measured_not_guessed(loop_server):
    ctx = loop_server["ctx"]
    ids = setup_classroom(loop_server)
    answer = stuck_answer(loop_server, ids["mac1"])
    agent_loops.note(ctx.conn, ctx.store, ids["mac1"], answer, now=T0)
    assert agent_loops.current(ctx.conn, now=at(180))[0]["silent_seconds"] == 180
    assert "לפני 3 דק'" in loop_checks(
        agent_loops.current(ctx.conn, now=at(180)))[1]["detail"]


# --- עיקרון 5: ירידה מהרשימה אינה "נפתר", ו"לא נקרא" אינו "נקי" ---------------


def test_the_journal_keeps_the_event_after_the_row_is_gone(loop_server):
    """סעיף 6: השורה חיה, היומן הוא הארכיון — הוא מה שמאפשר לזהות
    דיסק גוסס ("שלוש פעמים השבוע") אחרי שהמסך כבר נקי."""
    ctx = loop_server["ctx"]
    ids = setup_classroom(loop_server)
    answer = stuck_answer(loop_server, ids["mac1"])
    for offset in (0, 1200, 2400):
        agent_loops.note(ctx.conn, ctx.store, ids["mac1"], answer, now=at(offset))

    assert agent_loops.current(ctx.conn, now=at(4000)) == []       # השורה ירדה
    # אירוע אחד לכל לולאה, ולא לכל hello: שלוש לולאות → שלוש שורות.
    assert journal_events(loop_server).count("agent_loop") == 3


def test_leaving_the_screen_is_never_recorded_as_healed(loop_server):
    """אין אירוע "נרפא". יש רק היעדר אירוע, ואסור לקפל אותו להצלחה."""
    ctx = loop_server["ctx"]
    ids = setup_classroom(loop_server)
    answer = stuck_answer(loop_server, ids["mac1"])
    agent_loops.note(ctx.conn, ctx.store, ids["mac1"], answer, now=T0)
    before = journal_events(loop_server)

    assert agent_loops.current(ctx.conn, now=at(3600)) == []
    assert journal_events(loop_server) == before      # השתיקה לא כתבה כלום


def test_a_list_that_could_not_be_read_is_red_and_not_empty(loop_server):
    """מסך ריק כי השאילתה נפלה נראה בדיוק כמו מסך ריק כי הכול תקין."""
    unreadable = loop_checks(None)
    assert len(unreadable) == 1 and unreadable[0]["state"] == "bad"
    assert "לא נקראה" in unreadable[0]["detail"]

    quiet = loop_checks([])
    assert quiet[0]["state"] == "ok"
    # השורה הירוקה אומרת מה נמדד, ולא "אין מחשבים תקועים".
    assert "10 הדקות האחרונות" in quiet[0]["detail"]
    assert "כבוי" in quiet[0]["detail"]


def test_an_unreadable_session_state_is_not_an_innocent_machine(loop_server):
    """"לא הצלחנו לקרוא את מצב הסבב" אינו "אין סבב" — ולכן המכונה אינה
    מוצגת כחשודה, ואינה נספרת."""
    ctx = loop_server["ctx"]
    ids = setup_classroom(loop_server)
    answer = stuck_answer(loop_server, ids["mac1"])

    class Broken:
        def active_for_group(self, group_id):
            raise RuntimeError("הטבלה לא נקראה")

    assert agent_loops.unexplained(
        ctx.conn, Broken(), answer, off_vlan=False) is None
    assert agent_loops.note(
        ctx.conn, Broken(), ids["mac1"], answer, now=at(60)) is None
    assert agent_loops.current(ctx.conn, now=at(60)) == []


def test_monitoring_never_breaks_the_hello_it_watches(loop_server):
    """עיקרון 1: זו תצוגה בלבד. גם כשהספירה נופלת, ה-hello נענה כרגיל."""
    ctx = loop_server["ctx"]
    ids = setup_classroom(loop_server)
    ctx.conn.execute("DROP TABLE agent_loops")
    ctx.conn.commit()

    answer = hello(loop_server, ids["mac1"])          # לא מתפוצץ
    assert answer["known"] is True and answer["session"] is None


# --- הרשאות: המסך הזה הוא של המנהל ------------------------------------------


def test_a_deploy_user_cannot_read_the_health_screen(loop_server):
    assert loop_server["deploy"].get("/api/console/health").status_code == 403


# --- #1873: deletion while the previously registered agent is running --------


@pytest.mark.parametrize("group", ["grp_BUILD", "grp_CLONERS"])
@pytest.mark.parametrize("hello_before_delete", [False, True])
def test_deleted_running_agent_has_bounded_grace(
        loop_server, monkeypatch, group, hello_before_delete):
    from server import console_api, db

    mac = "aa:bb:cc:00:18:73"
    admin = loop_server["admin"]
    ctx = loop_server["ctx"]
    moment = [T0]
    # Also freeze journal() on the old implementation for the negative control.
    monkeypatch.setattr(db, "now_iso", lambda: moment[0])
    monkeypatch.setattr(console_api, "now_iso", lambda: moment[0])
    monkeypatch.setattr(agent_loops, "now_iso", lambda: moment[0])
    assert admin.post("/api/console/machines", json={
        "mac": mac, "name": "deleted-pc", "group_id": group,
    }).status_code == 200
    boot = loop_server["anon"].get(f"/boot/menu?mac={mac}")
    assert boot.status_code == 200 and "set default=imagectl" in boot.text
    if hello_before_delete:
        assert hello(loop_server, mac)["known"] is True
    # Exercise normalization too: the journal must contain the canonical MAC.
    assert admin.delete(f"/api/console/machines/{mac.upper()}").status_code == 200
    deletion = ctx.conn.execute(
        "SELECT ts, detail FROM journal WHERE event = 'machine_delete'"
    ).fetchone()
    assert dict(deletion) == {"ts": T0, "detail": mac}

    for offset in (0, 2, 300, 599):
        moment[0] = at(offset)
        answer = hello(loop_server, mac)
        assert answer["known"] is False and answer["task"] is None
        assert answer["session"] is None and "unregistered_screen" not in answer
        assert loop_rows(loop_server) == {}
        assert "agent_loop" not in journal_events(loop_server)

    # A failed repeat deletion cannot extend the grace period either.
    assert admin.delete(f"/api/console/machines/{mac}").status_code == 404
    moment[0] = at(600)
    hello(loop_server, mac)
    assert agent_loops.current(ctx.conn)[0]["hits"] == 1
    assert loop_rows(loop_server)[f"agent_loop:{mac}"]["state"] == "bad"
    assert journal_events(loop_server).count("agent_loop") == 1


def test_deletion_grace_does_not_hide_a_foreign_machine(loop_server):
    ids = setup_classroom(loop_server)
    assert loop_server["admin"].delete(
        f"/api/console/machines/{ids['mac1']}").status_code == 200
    stranger = "aa:bb:cc:00:18:74"
    assert loop_server["admin"].delete(
        f"/api/console/machines/{stranger}").status_code == 404
    for _ in range(3):
        assert hello(loop_server, stranger)["known"] is False
    loops = agent_loops.current(loop_server["ctx"].conn)
    assert len(loops) == 1 and loops[0]["mac"] == stranger and loops[0]["hits"] == 3
    assert journal_events(loop_server).count("agent_loop") == 1


def test_deletion_grace_does_not_hide_a_reregistered_machine(loop_server):
    ids = setup_classroom(loop_server)
    mac = ids["mac1"]
    admin = loop_server["admin"]
    assert admin.delete(f"/api/console/machines/{mac}").status_code == 200
    assert admin.post("/api/console/machines", json={
        "mac": mac, "name": "05", "group_id": ids["group"],
    }).status_code == 200
    assert hello(loop_server, mac)["known"] is True
    assert agent_loops.current(loop_server["ctx"].conn)[0]["hits"] == 1


def test_failed_deletion_record_rolls_back_the_machine_delete(loop_server):
    ids = setup_classroom(loop_server)
    ctx = loop_server["ctx"]
    ctx.conn.execute(
        "CREATE TRIGGER reject_machine_delete BEFORE INSERT ON journal"
        " WHEN NEW.event = 'machine_delete'"
        " BEGIN SELECT RAISE(ABORT, 'deletion record unavailable'); END"
    )
    ctx.conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="deletion record unavailable"):
        loop_server["admin"].delete(f"/api/console/machines/{ids['mac1']}")
    assert ctx.conn.execute(
        "SELECT 1 FROM machines WHERE mac = ?", (ids["mac1"],)
    ).fetchone() is not None
    assert "machine_delete" not in journal_events(loop_server)


def test_unreadable_deletion_history_is_logged_as_unverified(loop_server, caplog):
    ctx = loop_server["ctx"]

    class BrokenHistory:
        def execute(self, sql, parameters=()):
            if "FROM journal" in sql:
                raise sqlite3.OperationalError("deletion history unavailable")
            return ctx.conn.execute(sql, parameters)

    answer = {"schema": 1, "known": False, "task": None, "session": None}
    assert agent_loops.note(
        BrokenHistory(), ctx.store, "aa:bb:cc:00:18:75", answer, now=T0) is None
    assert "was not recorded" in caplog.text
    assert "deletion history unavailable" in caplog.text
    assert agent_loops.current(ctx.conn, now=T0) == []
