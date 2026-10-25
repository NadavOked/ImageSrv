"""לולאת האתחול של סבב פתוח (‏#75) — עיקרון 1 כשהסוכן נכשל.

מכונה שהסוכן שלה מת לפני `hello` קוראת ל-`die_local`, ש-`reboot -f`
מסיים אותו. עם סבב פתוח התפריט הבא מחזיר `default=imagectl` עם
`timeout=0`, והמכונה נוחתת שוב באותו כישלון — לולאה אינסופית, מחזור
כל ~2 דקות, ששוחזרה על חומרה. ‏`hello` לא הגיע מעולם, ולכן בקשת
התפריט היא העדות היחידה שיש לשרת על המכונה הזו.

הבדיקות כאן הן משני צדי הקו: המחולל (טהור) ואז אותו דבר דרך HTTP —
בדיוק מה ש-GRUB עושה, ‏`GET /boot/menu?mac=...` בכל אתחול.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from conftest import hello_body, setup_classroom          # noqa: E402

from boot.grub_menu import AGENT, GrubConfig, LOCAL, decide, render  # noqa: E402
from server.bootguard import ATTEMPT_LIMIT                # noqa: E402

CONFIG = GrubConfig(server_base="http://10.44.12.10:8080")

OPEN_SESSION = {
    "schema": 1, "known": True, "role": "classroom",
    "group": {"id": "grp_LAB1", "label": "כיתה LAB1", "suffix": "05"},
    "task": None,
    "session": {"id": "ses_a91f", "state": "open", "image_id": "img_7f3a91",
                "prefix": "LAB1", "expected_clients": 30, "joined": 11,
                "starts_in_seconds": 134},
    "allowed_images": [], "ui": {"language": "he", "require_login": False},
}


# --- המחולל -------------------------------------------------------------------


def test_an_open_session_boots_the_agent_without_the_guard():
    """הבקרה: זו ההתנהגות הרגילה, וזו גם הלולאה כשהסוכן נכשל."""
    assert decide(OPEN_SESSION).action == AGENT
    text = render(OPEN_SESSION, CONFIG)
    assert "set default=imagectl" in text and "set timeout=0" in text


def test_the_guard_sends_an_open_session_machine_to_the_local_disk():
    answer = {**OPEN_SESSION, "boot_guard": "exhausted"}
    decision = decide(answer)
    assert decision.action == LOCAL
    assert decision.code == "boot-loop-guard"
    text = render(answer, CONFIG)
    assert "set default=local" in text
    assert "# decision: boot-loop-guard" in text
    text.encode("ascii")                       # פלט GRUB תמיד ASCII


def test_the_guard_beats_a_task_too():
    answer = {**OPEN_SESSION, "session": None,
              "task": {"id": "tsk_4b1e", "type": "capture", "disk": "sda"},
              "boot_guard": "exhausted"}
    assert decide(answer).action == LOCAL


def test_the_guard_leaves_the_build_machine_its_visible_menu():
    """מחשב הבנייה מגיע למסך הקליטה רק מהתפריט הגלוי (‏#29) — הדגל
    מוריד את המשימה, לא את התפריט."""
    answer = {**OPEN_SESSION, "role": "build", "session": None,
              "task": {"id": "tsk_4b1e", "type": "capture", "disk": "sda"},
              "boot_guard": "exhausted"}
    decision = decide(answer)
    assert decision.action == LOCAL and decision.show_menu is True
    # ‏#144: ערך אחד, ובמחשב הבנייה זהו הערך הרגיל — הוא זה שמגיע
    # ל-build_console. השחזור אינו נגרע ממנו, הוא מעולם לא היה הדרך שלו.
    assert decision.offer_agent is True and decision.offer_recovery is False


def test_the_guard_still_offers_recovery():
    """הטכנאי שעומד מול המכונה עדיין נכנס לשחזור ב-ESC."""
    decision = decide({**OPEN_SESSION, "boot_guard": "exhausted"})
    assert decision.offer_recovery is True


def test_an_unknown_guard_value_changes_nothing():
    """רק "exhausted". ערך אחר אינו מצב לא ברור שמפיל לדיסק — הוא
    פשוט לא הדגל הזה."""
    assert decide({**OPEN_SESSION, "boot_guard": "maybe"}).action == AGENT


# --- דרך השרת, כמו ש-GRUB עושה -----------------------------------------------


def menu(server, mac: str) -> str:
    response = server["anon"].get(f"/boot/menu?mac={mac}")
    assert response.status_code == 200
    return response.text


def open_session(server, expected=2):
    ids = setup_classroom(server, expected)
    response = server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": expected},
    )
    assert response.status_code == 200
    ids["session"] = response.json()["id"]
    return ids


def boot_until_local(server, mac: str, limit: int = 10) -> int:
    """מאתחל שוב ושוב כמו המכונה בלולאה, ומחזיר באיזה אתחול היא ירדה
    לדיסק. מחזיר 0 אם לא ירדה בכלל — כלומר הלולאה עדיין שם."""
    for attempt in range(1, limit + 1):
        if "set default=local" in menu(server, mac):
            return attempt
    return 0


def test_a_failing_machine_reaches_the_local_disk_with_a_session_open(server):
    """הבאג עצמו: סבב פתוח, המכונה מאתחלת שוב ושוב, ואף פעם לא
    מגיעה ל-hello. אחרי התקציב היא יורדת לדיסק."""
    ids = open_session(server)
    assert boot_until_local(server, ids["mac1"]) == ATTEMPT_LIMIT + 1
    # ומשם והלאה — נשארת שם.
    assert "set default=local" in menu(server, ids["mac1"])
    assert "# decision: boot-loop-guard" in menu(server, ids["mac1"])


def test_the_budget_is_per_machine(server):
    """מכונה אחת שנתקעה לא מורידה את הכיתה לדיסק."""
    ids = open_session(server)
    boot_until_local(server, ids["mac1"])
    assert "set default=imagectl" in menu(server, ids["mac2"])


def test_the_loop_reaches_the_console(server):
    """‏#64: כישלון שלא מגיע לקונסולה נראה כמו מחשב שלא נדלק."""
    ids = open_session(server)
    boot_until_local(server, ids["mac1"])

    journal = server["admin"].get("/api/console/journal").json()
    loops = [row for row in journal if row["event"] == "boot_loop_local"]
    assert len(loops) == 1                      # פעם אחת, ברגע המעבר
    assert loops[0]["label"] == "מחשב אתחל שוב ושוב — נשלח לדיסק המקומי"
    assert "05" in loops[0]["text"] and "LAB1" in loops[0]["text"]

    view = server["admin"].get("/api/console/overview").json()["session"]
    assert view["stuck"][ids["mac1"]] == {"attempts": ATTEMPT_LIMIT + 1,
                                          "blocked": True}
    # והיא אינה חברה בסבב — בלי הרשימה הזו לא היה לה זכר במסך.
    assert view["members"] == [] and view["joined"] == 0


def test_a_second_boot_is_shown_before_the_budget_runs_out(server):
    """אתחול שני הוא כבר חזרה, והמפעיל רואה אותה מיד."""
    ids = open_session(server)
    menu(server, ids["mac1"])
    view = server["admin"].get("/api/console/overview").json()["session"]
    assert view["stuck"] == {}                  # אתחול אחד הוא מסלול תקין
    menu(server, ids["mac1"])
    view = server["admin"].get("/api/console/overview").json()["session"]
    assert view["stuck"][ids["mac1"]] == {"attempts": 2, "blocked": False}


def test_a_new_session_is_a_new_budget(server):
    """הסבב הבא הוא הקשר חדש — המכונה מקבלת הזדמנות מלאה."""
    ids = open_session(server)
    boot_until_local(server, ids["mac1"])
    assert server["deploy"].post(
        f"/api/console/sessions/{ids['session']}/close",
        json={"confirm_name": "Office 2024 Standard"}).status_code == 200
    assert server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 2},
    ).status_code == 200
    assert "set default=imagectl" in menu(server, ids["mac1"])


def test_a_cloner_is_never_sent_to_a_disk_it_does_not_have(server):
    """מחשב שיכפול עולה לסוכן גם בלי סבב (‏cloner-wait), אין לו הקשר
    תחום בזמן ואין לו מערכת מקומית — הוא אינו נספר (‏#17)."""
    mac = "aa:bb:cc:00:00:21"
    assert server["admin"].post("/api/console/machines", json={
        "mac": mac, "name": "shich-1", "group_id": "grp_CLONERS",
    }).status_code == 200
    assert boot_until_local(server, mac, limit=ATTEMPT_LIMIT + 4) == 0


def test_a_capture_task_has_its_own_budget(server):
    """אותה לולאה בדיוק במסלול הקליטה: משימה תקועה לא מאתחלת לנצח."""
    mac = "aa:bb:cc:00:00:10"
    assert server["admin"].post("/api/console/machines", json={
        "mac": mac, "name": "BUILD-PC", "group_id": "grp_BUILD",
    }).status_code == 200
    assert server["admin"].post("/api/console/tasks/capture", json={
        "mac": mac, "disk": "sda", "name": "Windows 11 Base", "folder": "Office",
    }).status_code == 200
    assert boot_until_local(server, mac) == ATTEMPT_LIMIT + 1


def test_an_attempt_that_cannot_be_counted_ends_at_the_local_disk(server):
    """עיקרון 5: "לא הצלחנו לספור" אינו "נספר אחת". כתיבה שלא נשמרה
    היא מצב לא ברור, וכל מצב לא ברור נגמר בדיסק המקומי."""
    ids = open_session(server)
    server["ctx"].conn.executescript(
        "CREATE TRIGGER swallow AFTER INSERT ON boot_attempts"
        " BEGIN DELETE FROM boot_attempts WHERE mac = NEW.mac; END;"
    )
    assert "set default=local" in menu(server, ids["mac1"])
    events = [row["event"] for row in
              server["admin"].get("/api/console/journal").json()]
    assert "boot_loop_unverified" in events


# --- אין רגרסיה במה שעובד -----------------------------------------------------


def test_hello_still_joins_and_resets_the_timer(server):
    """ההצטרפות היא ב-hello, והשומר יושב רק בתפריט. גם מכונה שהתקציב
    שלה נגמר תצטרף אם היא בכל זאת מדברת."""
    ids = open_session(server, expected=3)
    clock = server["clock"]

    joined = server["anon"].post("/api/v1/agent/hello",
                                 json=hello_body(ids["mac1"])).json()
    assert joined["session"]["joined"] == 1
    first = joined["session"]["starts_in_seconds"]

    clock.advance(100)
    later = server["anon"].post("/api/v1/agent/hello",
                                json=hello_body(ids["mac2"])).json()
    assert later["session"]["joined"] == 2
    # ‏hello מדווח את הטיימר כפי שהיה לפני שהוא עצמו הצטרף; מה שנשמר
    # הוא המאופס, וזה מה שהמסך מראה.
    assert server["admin"].get("/api/console/overview").json()[
        "session"]["starts_in_seconds"] == first

    # והתפריט לא צירף אף אחד, גם אחרי שהתקציב נגמר.
    boot_until_local(server, "b4:2e:99:07:1a:c4")
    assert server["admin"].get(
        "/api/console/overview").json()["session"]["joined"] == 2


def test_a_machine_that_finished_is_not_counted_and_not_offered_the_session(server):
    """‏done לא מקבל את הסבב שוב (session:null), ואינו נספר בשומר הלולאה:
    אין לו הקשר תחום בזמן (task/session), בדיוק כמו cloner-wait.

    ⚠️ מ-#641 (היפוך #140) תחנת כיתה בלי משימה עולה ישר ל-ImageCtl,
    ולכן גם מכונה שסיימה שחזור מקבלת `default=imagectl` ולא
    `default=local` — השרת אינו מבדיל "סיימה" מ"מעולם לא קיבלה משימה"
    (שתיהן session:null). מה שנשמר כאן הוא **השומר**: הלולאה אינה סופרת
    אותה (`context is None`), ולכן `stuck` ריק ואין `boot_loop_local`.
    """
    ids = open_session(server, expected=1)
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    assert server["anon"].post("/api/v1/agent/progress", json={
        "session_id": ids["session"], "mac": ids["mac1"], "state": "done",
        "targets": [{"dev": "sda", "bytes_written": 57982058496,
                     "bytes_total": 57982058496, "state": "done"}],
    }).json()["ok"]

    for _ in range(ATTEMPT_LIMIT + 3):
        assert "set default=imagectl" in menu(server, ids["mac1"])
    view = server["admin"].get("/api/console/overview").json()["session"]
    assert view["stuck"] == {}
    events = [row["event"] for row in
              server["admin"].get("/api/console/journal").json()]
    assert "boot_loop_local" not in events


def test_the_menu_still_does_not_join_anyone(server):
    ids = open_session(server)
    for _ in range(ATTEMPT_LIMIT + 2):
        menu(server, ids["mac1"])
    assert server["admin"].get(
        "/api/console/overview").json()["session"]["joined"] == 0


# --- מי רשאי להגדיל את המונה (‏#536) ------------------------------------------
#
# ‏`GET /boot/menu?mac=<MAC>` מקבל את ה-MAC **מהשאילתה**, ‏`ATTEMPT_LIMIT`
# הוא 3, וארבע בקשות בלי גוף ובלי עוגייה גמרו את התקציב של תחנת כיתה
# חיה — השומר שנועד להציל אותה מלולאה הוא זה שהוציא אותה מהסבב. ‏MAC
# בשאילתה אינו זהות; מה שכן אפשר לאמת הוא על **איזו** מכתובות השרת
# הבקשה התקבלה, וזו בדיוק העמדה של #42: מחוץ לווילן ההפצה השרת אינו
# מגיש את שרשרת האתחול, ולכן אין לו ראיה שהמכונה עברה בתפריט שלו.

#: כתובת וילן ההפצה — זו שאיתה נוצר השרת ב-conftest.
VLAN = "http://10.44.12.10:8080"
#: כתובת מקומית אחרת של אותו שרת: הרגל שלו ברשת המכללה.
OFF_VLAN = "http://10.10.10.8:8080"


def menu_at(server, base: str, mac: str) -> str:
    """בקשת תפריט שהתקבלה על כתובת מקומית מסוימת של השרת."""
    response = server["anon"].get(f"{base}/boot/menu?mac={mac}")
    assert response.status_code == 200
    return response.text


def counted(server, mac: str) -> int:
    """כמה אתחולים נספרו על ה-MAC הזה — **המצב עצמו**, לא קוד התשובה."""
    row = server["ctx"].conn.execute(
        "SELECT attempts FROM boot_attempts WHERE mac = ?", (mac,)).fetchone()
    return int(row["attempts"]) if row else 0


def test_menu_requests_from_another_network_are_not_counted_as_boots(server):
    """הבאג עצמו: ארבע בקשות GET מרשת שאינה וילן ההפצה."""
    ids = open_session(server)

    bodies = [menu_at(server, OFF_VLAN, ids["mac1"])
              for _ in range(ATTEMPT_LIMIT + 1)]

    assert counted(server, ids["mac1"]) == 0
    # והתשובה עצמה לא השתנתה: זו התראה ולא שער (#137), והתחנה שבאמת
    # מושכת תפריט מרשת אחרת (תרחיש 3, ‏#39) ממשיכה לקבל את הסבב שלה.
    assert all("set default=imagectl" in body for body in bodies)
    events = [row["event"] for row in
              server["admin"].get("/api/console/journal").json()]
    assert "boot_loop_local" not in events


def test_the_station_keeps_its_whole_budget_after_the_forged_requests(server):
    """הנזק שנמנע: אחרי אותן ארבע בקשות התחנה עדיין מקבלת שלושה
    אתחולים מלאים על וילן ההפצה, ורק הרביעי שלה יורד לדיסק."""
    ids = open_session(server)
    for _ in range(ATTEMPT_LIMIT + 1):
        menu_at(server, OFF_VLAN, ids["mac1"])

    for attempt in range(1, ATTEMPT_LIMIT + 1):
        assert "set default=imagectl" in menu_at(server, VLAN, ids["mac1"])
        assert counted(server, ids["mac1"]) == attempt
    assert "set default=local" in menu_at(server, VLAN, ids["mac1"])


def test_the_deployment_vlan_is_still_counted(server):
    """הבקרה לכיוון השני: השער אינו "לעולם לא לספור". על וילן ההפצה
    השומר של #75 עובד בדיוק כמו קודם, אחרת התיקון הוא ביטול השומר."""
    ids = open_session(server)

    for _ in range(ATTEMPT_LIMIT):
        assert "set default=imagectl" in menu_at(server, VLAN, ids["mac1"])
    assert "set default=local" in menu_at(server, VLAN, ids["mac1"])
    assert counted(server, ids["mac1"]) == ATTEMPT_LIMIT + 1


# --- מי רשאי לכתוב את ה-IP של מכונה (‏#585) -----------------------------------
#
# ‏`build_answer` פותח ב-`net_seen`, ומסלול התפריט קורא לו. לכן
# ‏`GET /boot/menu?mac=<MAC>` — בקשה בלי גוף, בלי עוגייה, מכל פונה —
# כותב את ה-`ip` ואת ה-`last_seen` של אותה מכונה בטבלת `net_devices`.
# מכונה כבויה נראית חיה, הכתובת המוצגת אינה שלה, וכל בינוי זהות עתידי
# על "הכתובת שהשרת מכיר עבור ה-MAC" מעגלי — התוקף כותב אותה בעצמו.
# אותה משפחה כמו #536: ‏MAC בשאילתה אינו זהות, והרישום מותנה ב**כתובת
# שעליה הבקשה התקבלה**, לא ב-MAC מהשאילתה.


def net_row(server, mac: str):
    """שורת `net_devices` של ה-MAC — **המצב עצמו**. ‏None = לא נכתבה."""
    return server["ctx"].conn.execute(
        "SELECT ip, last_seen FROM net_devices WHERE mac = ?", (mac,)).fetchone()


def test_a_menu_from_another_network_does_not_record_the_machine(server):
    """הבאג עצמו: בקשות תפריט מרשת שאינה וילן ההפצה, למכונה שמעולם
    לא דיברה — אסור שיכתבו לה שורה בטבלת ההתקנים."""
    ids = open_session(server)
    assert net_row(server, ids["mac1"]) is None        # עוד לא דיברה

    bodies = [menu_at(server, OFF_VLAN, ids["mac1"]) for _ in range(3)]

    assert net_row(server, ids["mac1"]) is None
    # והתשובה עצמה לא השתנתה — התחנה שמושכת תפריט מרשת אחרת (תרחיש 3,
    # ‏#39) ממשיכה לקבל את הסבב שלה (עיקרון 1, התראה ולא שער).
    assert all("set default=imagectl" in body for body in bodies)


def test_a_menu_on_the_deployment_vlan_still_records(server):
    """הבקרה לכיוון השני: הרישום אינו מבוטל. תפריט שהתקבל על וילן
    ההפצה כותב את המכונה כמו היום, אחרת התיקון שבר את רשימת ההתקנים."""
    ids = open_session(server)
    assert net_row(server, ids["mac1"]) is None

    menu_at(server, VLAN, ids["mac1"])

    row = net_row(server, ids["mac1"])
    assert row is not None and row["last_seen"]


def test_hello_records_the_machine_even_from_another_network(server):
    """‏hello הוא POST שבו המכונה מדווחת על עצמה, ולכן הוא רושם תמיד —
    גם מחוץ לווילן ההפצה (שם `foreign_vlan` מתריע בנפרד). ‏#585 מפריד
    בין hello לתפריט, לא מפסיק את הרישום."""
    ids = open_session(server)
    assert net_row(server, ids["mac1"]) is None

    response = server["anon"].post(f"{OFF_VLAN}/api/v1/agent/hello",
                                   json=hello_body(ids["mac1"]))
    assert response.status_code == 200

    row = net_row(server, ids["mac1"])
    assert row is not None and row["ip"] == "10.44.12.187"   # ה-ip מגוף ה-hello
