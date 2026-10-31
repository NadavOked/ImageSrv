"""מחשב שפונה לשרת מרשת שאינה וילן ההפצה — שורה אדומה עד שהוא משתתק (#137).

זו **אינה** לולאה, ואינה נספרת כלולאה: מחוץ לווילן ההפצה השרת אינו
מגיש את שרשרת האתחול, ואין לו ראיה שהמכונה עברה בתפריט שלו (‏#42).
היא כן אירוע שצריך להיראות — או שנדב עומד ליד המחשב ומושך אימג'
בכוונה, או שמחשב מחובר לשקע הלא נכון והוא ייראה תקין ולא יעבוד.

**זו התראה ולא שער.** התשובה שהמכונה מקבלת אינה משתנה כהוא זה, וגם
מכונה שאינה מוכרת ממשיכה לקבל `known:false` ואתחול מהדיסק (עיקרון 1).

הבקרה השלילית היא חצי מהקובץ: מכונה מווילן ההפצה **אינה** מייצרת
שורה אדומה. בלי הכיוון הזה, "יש שורה אדומה" אינו אומר דבר.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from conftest import hello_body, setup_classroom              # noqa: E402

from server.agent import agent_loops  # noqa: E402
from server.net import foreign_vlan  # noqa: E402
from server.net.foreign_vlan import vlan_checks                   # noqa: E402

try:
    from fastapi.testclient import TestClient
except ImportError:                                   # pragma: no cover
    TestClient = None

T0 = "2026-08-29T14:00:00+00:00"

#: הכתובת שאיתה נוצר השרת — וילן ההפצה.
VLAN = "http://10.44.12.10:8080"
#: כתובת מקומית אחרת של אותו שרת: הרגל שלו ברשת המכללה.
OFF_VLAN = "http://10.10.10.8:8080"


def at(seconds: int) -> str:
    return (datetime.fromisoformat(T0)
            + timedelta(seconds=seconds)).isoformat(timespec="seconds")


@pytest.fixture()
def vlan_server(tmp_path: Path, images_root: Path, clock):
    """שרת מלא עם בדיקות בריאות מזויפות — אותה תשתית כמו ב-#112."""
    if TestClient is None:
        pytest.skip("fastapi is required")
    from test_sender import Recorder                           # noqa: PLC0415

    from server.console import users                                   # noqa: PLC0415
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
    app = create_app(tmp_path / "data", images_root, VLAN,
                     now_fn=clock, health_hooks=hooks,
                     sender_runner=Recorder(block=True))
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    admin = TestClient(app)
    assert admin.post("/api/console/login", json={
        "username": "noc", "password": "admin-pass-123"}).status_code == 200
    bundle = {"app": app, "ctx": ctx, "admin": admin, "anon": TestClient(app)}
    yield bundle
    ctx.sender.stop()


def hello_at(server: dict, base: str, mac: str) -> dict:
    """hello שמגיע על כתובת מקומית מסוימת של השרת."""
    response = server["anon"].post(base + "/api/v1/agent/hello",
                                   json=hello_body(mac))
    assert response.status_code == 200
    return response.json()


def rows(server: dict) -> dict:
    return {r["id"]: r for r in server["admin"].get("/api/console/health").json()}


def machine_rows(server: dict) -> dict:
    """רק שורות המחשבים, בלי שורת הסיכום."""
    return {k: v for k, v in rows(server).items() if k.startswith("off_vlan:")}


def journal_events(server: dict) -> list[str]:
    return [row["event"] for row in
            server["ctx"].conn.execute("SELECT event FROM journal")]


# --- הבקרה השלילית: מווילן ההפצה אין שורה אדומה ------------------------------


def test_a_machine_on_the_deployment_vlan_makes_no_red_row(vlan_server):
    """**הבדיקה שהמסך הזה עומד עליה.**

    כיתה שלמה מדברת עם השרת מווילן ההפצה כל הזמן. אם היא תופיע כאן,
    השורה האדומה תהיה דלוקה תמיד — וזה גרוע מלא להציג כלום.
    """
    ids = setup_classroom(vlan_server)

    for _ in range(3):
        hello_at(vlan_server, VLAN, ids["mac1"])

    assert machine_rows(vlan_server) == {}
    assert rows(vlan_server)["off_vlan"]["state"] == "ok"
    assert "off_vlan_contact" not in journal_events(vlan_server)


def test_a_scope_that_says_nothing_useful_makes_no_red_row(vlan_server):
    """ברירת המחדל של TestClient היא ("testserver", 80) — לא כתובת.

    ‏`off_deploy_vlan` מחזיר False בכל ספק, וזו בדיוק ההתנהגות שנדרשת
    כאן: "לא ידענו מאיזו רשת" אינו "מרשת זרה".
    """
    ids = setup_classroom(vlan_server)

    assert vlan_server["anon"].post(
        "/api/v1/agent/hello", json=hello_body(ids["mac1"])).status_code == 200

    assert machine_rows(vlan_server) == {}
    assert rows(vlan_server)["off_vlan"]["state"] == "ok"


# --- הכיוון החיובי: מרשת אחרת יש שורה אדומה, והיא אומרת מאיזו רשת -------------


def test_a_machine_from_another_network_is_red_and_names_the_network(vlan_server):
    ids = setup_classroom(vlan_server)

    hello_at(vlan_server, OFF_VLAN, ids["mac1"])

    screen = rows(vlan_server)
    assert screen["off_vlan"]["state"] == "bad"
    machine = screen[f"off_vlan:{ids['mac1']}"]
    assert machine["state"] == "bad"
    assert machine["label"] == "כיתה LAB1-05"        # כיתה ושם, לא MAC
    assert "10.10.10.8" in machine["detail"]         # מאיזו רשת פנתה
    assert "10.44.12.10" in machine["detail"]        # ומה מצופה
    assert "נראה לאחרונה" in machine["detail"]
    assert "off_vlan_contact" in journal_events(vlan_server)


def test_an_unregistered_machine_shows_up_by_mac(vlan_server):
    mac = "de:ad:be:ef:00:99"

    answer = hello_at(vlan_server, OFF_VLAN, mac)

    # עיקרון 1 לא נשבר: היא עדיין לא מוכרת ועדיין עולה מהדיסק.
    assert answer["known"] is False and answer["task"] is None
    machine = machine_rows(vlan_server)[f"off_vlan:{mac}"]
    assert machine["label"] == mac
    assert machine["state"] == "bad"


def test_the_same_machine_is_one_row_with_a_rising_counter(vlan_server):
    ids = setup_classroom(vlan_server)

    for _ in range(3):
        hello_at(vlan_server, OFF_VLAN, ids["mac1"])

    machines = machine_rows(vlan_server)
    assert list(machines) == [f"off_vlan:{ids['mac1']}"]
    assert "3 פניות" in machines[f"off_vlan:{ids['mac1']}"]["detail"]


def test_two_machines_from_another_network_are_two_rows(vlan_server):
    ids = setup_classroom(vlan_server)

    hello_at(vlan_server, OFF_VLAN, ids["mac1"])
    hello_at(vlan_server, OFF_VLAN, ids["mac2"])

    assert len(machine_rows(vlan_server)) == 2
    assert "2 מחשבים" in rows(vlan_server)["off_vlan"]["detail"]


# --- תצוגה חיה: השורה יורדת אחרי חלון השתיקה ---------------------------------


def test_the_row_survives_nine_minutes_and_drops_after_eleven(vlan_server):
    conn = vlan_server["ctx"].conn
    foreign_vlan.note(conn, "b4:2e:99:07:1a:c4", {"server": ("10.10.10.8", 8080)},
                      off_vlan=True, now=T0)

    assert [r["mac"] for r in foreign_vlan.current(conn, now=at(540))] \
        == ["b4:2e:99:07:1a:c4"]
    assert foreign_vlan.current(conn, now=at(599)) != []
    assert foreign_vlan.current(conn, now=at(601)) == []


def test_a_machine_that_comes_back_after_silence_starts_a_new_count(vlan_server):
    conn = vlan_server["ctx"].conn
    scope = {"server": ("10.10.10.8", 8080)}

    assert foreign_vlan.note(conn, "aa:aa:aa:aa:aa:aa", scope,
                             off_vlan=True, now=T0) == 1
    assert foreign_vlan.note(conn, "aa:aa:aa:aa:aa:aa", scope,
                             off_vlan=True, now=at(60)) == 2
    assert foreign_vlan.note(conn, "aa:aa:aa:aa:aa:aa", scope,
                             off_vlan=True, now=at(1200)) == 1


def test_the_silence_window_is_the_one_the_health_screen_already_uses():
    """חלון אחד למסך, לא שניים שיסטו זה מזה."""
    assert foreign_vlan.SILENCE_SECONDS == agent_loops.SILENCE_SECONDS


# --- עיקרון 5: "לא נקרא" אינו "נקי", ו"השתתק" אינו "נפתר" --------------------


def test_a_list_that_was_not_read_is_red_and_not_empty():
    row = vlan_checks(None, "10.44.12.10")[0]

    assert row["state"] == "bad"
    assert "לא נקראה" in row["detail"]


def test_the_green_row_says_what_was_measured_and_not_that_all_is_well():
    row = vlan_checks([], "10.44.12.10")[0]

    assert row["state"] == "ok"
    assert "10 הדקות האחרונות" in row["detail"]
    assert "כבוי" in row["detail"]


def test_a_broken_table_is_a_red_row_on_the_screen(vlan_server):
    conn = vlan_server["ctx"].conn
    conn.execute("DROP TABLE off_vlan_contacts")
    conn.commit()

    assert rows(vlan_server)["off_vlan"]["state"] == "bad"


# --- זו התראה, לא שער --------------------------------------------------------


def test_monitoring_never_breaks_the_hello_it_watches(vlan_server):
    """גם כשהספירה נופלת, ה-hello נענה כרגיל ובאותה תשובה בדיוק."""
    ids = setup_classroom(vlan_server)
    conn = vlan_server["ctx"].conn
    conn.execute("DROP TABLE off_vlan_contacts")
    conn.commit()

    answer = hello_at(vlan_server, OFF_VLAN, ids["mac1"])

    assert answer["known"] is True and answer["session"] is None


def test_the_answer_from_another_network_is_untouched(vlan_server):
    """אותה תשובה בדיוק משני צידי הרשת — חוץ מהכניסה שנדרשה כבר ב-#42."""
    ids = setup_classroom(vlan_server)

    on = hello_at(vlan_server, VLAN, ids["mac1"])
    off = hello_at(vlan_server, OFF_VLAN, ids["mac1"])

    assert off["known"] == on["known"]
    assert off["task"] == on["task"] is None
    assert off["session"] == on["session"] is None
    assert off["allowed_images"] == on["allowed_images"]


def test_the_loop_row_stays_out_of_it(vlan_server):
    """‏`agent_loops` לא השתנה: פנייה מרשת אחרת אינה לולאה, ואינה נספרת
    כאחת. שתי השורות נפרדות במסך ובקוד."""
    ids = setup_classroom(vlan_server)

    hello_at(vlan_server, OFF_VLAN, ids["mac1"])

    screen = rows(vlan_server)
    assert screen["agent_loops"]["state"] == "ok"
    assert f"agent_loop:{ids['mac1']}" not in screen
    assert screen[f"off_vlan:{ids['mac1']}"]["state"] == "bad"
