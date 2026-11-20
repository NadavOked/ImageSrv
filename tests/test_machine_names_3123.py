"""#3123 (נדב, 14/11): המפעיל נותן מספר, והשרת קובע את השם — ו"השאר דלוקים".

* ``3`` במשכפל ← ``cloner-3`` (מוצג ``משכפל-3``); בבנייה ← ``build-3`` (``בנייה-3``).
* מספר כפול נדחה, בכל מסלולי הרישום; שינוי תפקיד משנה את הקידומת ולא את המספר.
* שם ישן (לפני #3123) נשאר כמו שהוא — אין מיגרציה.
* ``POST /room/keep-on`` ו-``keep_on`` בפתיחת סבב: הנבחרים נשארים, השאר מתבקשים
  להיכבות, **מכונה באמצע סבב לא נכבית**, והיומן אומר מי כובה ולמה.
"""

from __future__ import annotations

import pytest
from conftest import hello_body

pytest.importorskip("fastapi")

from server.agent import registry                                    # noqa: E402
from server.core import machine_names                                # noqa: E402
from server.db import connect                                        # noqa: E402

from test_room_precheck_432_65 import _open, disk, drawers_hello     # noqa: E402
from test_server_room import CLONER1, CLONER2, room, room_server     # noqa: E402,F401 -- fixture

MAC_A = "aa:bb:cc:dd:ee:01"
MAC_B = "aa:bb:cc:dd:ee:02"
MAC_C = "aa:bb:cc:dd:ee:03"
CLONER3 = "aa:bb:cc:00:00:23"


# --- הפונקציות הטהורות -------------------------------------------------------

@pytest.mark.parametrize("text, number", [("3", 3), ("03", 3), (" 7 ", 7), ("99", 99), ("1", 1)])
def test_a_number_is_digits_between_1_and_99(text, number):
    assert machine_names.parse_number(text) == number


@pytest.mark.parametrize("text", ["0", "00", "100", "3.5", "-3", "3a", "", " ", "٣", "cloner-3", None, 3])
def test_anything_else_is_not_a_number(text):
    assert machine_names.parse_number(text) is None


def test_the_technical_name_is_ascii_and_the_display_name_is_hebrew():
    assert machine_names.technical("cloner", 3) == "cloner-3"
    assert machine_names.technical("build", 3) == "build-3"
    assert machine_names.display("cloner", 3) == "משכפל-3"
    assert machine_names.display("build", 3) == "בנייה-3"


def test_the_prefix_must_match_the_role_or_the_name_is_an_old_one():
    assert machine_names.number_of("cloner", "cloner-12") == 12
    assert machine_names.number_of("build", "build-12") == 12
    assert machine_names.number_of("build", "cloner-12") is None       # שם ישן במקרה
    assert machine_names.number_of("classroom", "cloner-12") is None
    assert machine_names.number_of("cloner", "HP-cloner-1") is None
    assert machine_names.number_of("cloner", "cloner-0") is None
    assert machine_names.number_of("cloner", "cloner-100") is None
    assert machine_names.label("cloner", "cloner-12") == "משכפל-12"
    assert machine_names.label("build", "cloner-12") == "cloner-12"    # מוצג כמו שהוא
    assert machine_names.label("cloner", "shich-1") == "shich-1"


@pytest.mark.parametrize("token, expected", [
    (3, True), ("3", True), ("03", True), ("משכפל-3", True), ("cloner-3", True),
    (4, False), ("4", False), ("בנייה-3", False), ("build-3", False), (True, False), (None, False), ("", False),
])
def test_a_machine_is_picked_by_number_or_by_name(token, expected):
    assert machine_names.match("cloner", token, "cloner-3") is expected


def test_a_number_does_not_pick_a_machine_with_an_old_name():
    assert machine_names.match("cloner", 1, "shich-1") is False
    assert machine_names.match("cloner", "shich-1", "shich-1") is True     # השם עצמו כן


# --- הרישום ------------------------------------------------------------------

@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    for gid, role in (("c", "cloner"), ("c2", "cloner"), ("b", "build"), ("k", "classroom")):
        c.execute("INSERT INTO groups (id, label, role) VALUES (?, ?, ?)", (gid, gid, role))
    c.commit()
    return c


def _suffix(conn, mac):
    return conn.execute("SELECT suffix FROM machines WHERE mac = ?", (mac,)).fetchone()["suffix"]


@pytest.mark.parametrize("typed", ["3", "03", " 3 "])
def test_a_cloner_number_becomes_the_cloner_name(conn, typed):
    registry.add_machine(conn, MAC_A, typed, "c", "t")
    assert _suffix(conn, MAC_A) == "cloner-3"


def test_a_build_number_becomes_the_build_name(conn):
    registry.add_machine(conn, MAC_A, "1", "b", "t")
    assert _suffix(conn, MAC_A) == "build-1"


def test_the_classroom_suffix_is_what_it_was(conn):
    registry.add_machine(conn, MAC_A, "5", "k", "t")
    assert _suffix(conn, MAC_A) == "05"


@pytest.mark.parametrize("typed", ["0", "00", "100", "1000"])
def test_a_bad_number_is_refused_in_words(conn, typed):
    with pytest.raises(ValueError) as exc:
        registry.add_machine(conn, MAC_A, typed, "c", "t")
    assert "99" in str(exc.value) and "1" in str(exc.value)
    assert conn.execute("SELECT COUNT(*) AS n FROM machines").fetchone()["n"] == 0


def test_a_number_made_of_digits_is_never_taken_as_a_free_hostname(conn):
    """"100" הוא hostname חוקי ב-#1997; אחרי #3123 הוא מספר שגוי, לא שם."""
    assert registry.normalize_name("cloner", "100") is None
    assert "99" in registry.name_error("cloner", "100")


def test_an_old_style_name_is_still_accepted_and_shown_as_it_is(conn):
    registry.add_machine(conn, MAC_A, "shich-1", "c", "t")
    assert _suffix(conn, MAC_A) == "shich-1"
    assert machine_names.label("cloner", "shich-1") == "shich-1"


def test_the_same_number_twice_is_refused_naming_the_holder(conn):
    registry.add_machine(conn, MAC_A, "3", "c", "t")
    with pytest.raises(ValueError) as exc:
        registry.add_machine(conn, MAC_B, "3", "c", "t")
    assert "3" in str(exc.value) and MAC_A in str(exc.value) and "משכפל-3" in str(exc.value)
    assert conn.execute("SELECT COUNT(*) AS n FROM machines").fetchone()["n"] == 1


def test_the_number_is_shared_by_every_cloner_group(conn):
    """קבוצת משכפלים נוספת ("חיפה") אינה סדרת מספרים נפרדת — cloner-3 הוא hostname אחד."""
    registry.add_machine(conn, MAC_A, "3", "c", "t")
    with pytest.raises(ValueError):
        registry.add_machine(conn, MAC_B, "3", "c2", "t")


def test_a_cloner_and_a_build_machine_may_share_a_number(conn):
    registry.add_machine(conn, MAC_A, "1", "c", "t")
    registry.add_machine(conn, MAC_B, "1", "b", "t")
    assert (_suffix(conn, MAC_A), _suffix(conn, MAC_B)) == ("cloner-1", "build-1")


def test_renaming_to_the_number_it_already_has_is_not_a_clash(conn):
    registry.add_machine(conn, MAC_A, "3", "c", "t")
    registry.update_machine(conn, MAC_A, "3", None, "t")
    assert _suffix(conn, MAC_A) == "cloner-3"


def test_renaming_to_a_taken_number_is_refused_and_changes_nothing(conn):
    registry.add_machine(conn, MAC_A, "3", "c", "t")
    registry.add_machine(conn, MAC_B, "4", "c", "t")
    with pytest.raises(ValueError):
        registry.update_machine(conn, MAC_B, "3", None, "t")
    assert _suffix(conn, MAC_B) == "cloner-4"


def test_an_old_name_can_be_replaced_by_a_number(conn):
    registry.add_machine(conn, MAC_A, "shich-1", "c", "t")
    registry.update_machine(conn, MAC_A, "2", None, "t")
    assert _suffix(conn, MAC_A) == "cloner-2"


def test_changing_the_role_changes_the_prefix_and_keeps_the_number(conn):
    registry.add_machine(conn, MAC_A, "3", "c", "t")
    registry.update_machine(conn, MAC_A, None, "b", "t")
    assert _suffix(conn, MAC_A) == "build-3"
    registry.update_machine(conn, MAC_A, None, "c", "t")
    assert _suffix(conn, MAC_A) == "cloner-3"


def test_changing_the_role_into_a_taken_number_is_refused(conn):
    registry.add_machine(conn, MAC_A, "3", "c", "t")
    registry.add_machine(conn, MAC_B, "3", "b", "t")
    with pytest.raises(ValueError):
        registry.update_machine(conn, MAC_A, None, "b", "t")
    row = conn.execute("SELECT suffix, group_id FROM machines WHERE mac = ?", (MAC_A,)).fetchone()
    assert (row["suffix"], row["group_id"]) == ("cloner-3", "c")


def test_moving_between_groups_of_the_same_role_keeps_the_name(conn):
    registry.add_machine(conn, MAC_A, "3", "c", "t")
    registry.update_machine(conn, MAC_A, None, "c2", "t")
    assert _suffix(conn, MAC_A) == "cloner-3"


def test_the_paste_takes_numbers_and_flags_a_number_that_appears_twice(conn):
    lines = registry.parse_paste(f"{MAC_A} 1\n{MAC_B} 2\n{MAC_C} 1", "cloner")
    assert [l.suffix for l in lines] == ["cloner-1", "cloner-2", "cloner-1"]
    assert lines[2].error and "שורה 1" in lines[2].error


def test_the_import_refuses_a_number_a_registered_machine_holds_and_saves_the_rest(conn):
    registry.add_machine(conn, MAC_A, "1", "c", "t")
    saved, rejected = registry.import_lines(
        conn, "c", registry.parse_paste(f"{MAC_B} 1\n{MAC_C} 2", "cloner"), "t")
    assert saved == 1 and len(rejected) == 1
    assert MAC_A in rejected[0].error and rejected[0].mac == MAC_B
    assert _suffix(conn, MAC_C) == "cloner-2"


# --- מה שהקונסולה רואה --------------------------------------------------------

def _add(admin, mac, name, group="grp_CLONERS"):
    return admin.post("/api/console/machines", json={"mac": mac, "name": name, "group_id": group})


def test_the_api_refuses_a_duplicate_number_with_the_reason(room_server):
    admin = room_server["admin"]
    assert _add(admin, CLONER3, "3").status_code == 200
    r = _add(admin, "aa:bb:cc:00:00:24", "3")
    assert r.status_code == 400 and "משכפל-3" in r.json()["detail"], r.text


def test_the_machine_list_carries_the_number_and_the_display_name(room_server):
    admin = room_server["admin"]
    assert _add(admin, CLONER3, "3").status_code == 200
    assert _add(admin, "aa:bb:cc:00:00:30", "2", "grp_BUILD").status_code == 200
    rows = {m["mac"]: m for m in admin.get("/api/console/machines").json()}
    assert (rows[CLONER3]["suffix"], rows[CLONER3]["number"], rows[CLONER3]["display_name"]) == ("cloner-3", 3, "משכפל-3")
    assert (rows["aa:bb:cc:00:00:30"]["number"], rows["aa:bb:cc:00:00:30"]["display_name"]) == (2, "בנייה-2")
    # שם ישן: מוצג כמו היום, בלי מספר
    assert (rows[CLONER1]["suffix"], rows[CLONER1]["number"], rows[CLONER1]["display_name"]) == ("shich-1", None, "shich-1")


def test_the_room_carries_the_number_and_the_name_stays_technical(room_server):
    admin = room_server["admin"]
    assert _add(admin, CLONER3, "3").status_code == 200
    by_mac = {m["mac"]: m for m in room(room_server["deploy"])["machines"]}
    m = by_mac[CLONER3]
    assert (m["name"], m["number"], m["display_name"]) == ("cloner-3", 3, "משכפל-3")   # name = confirm_name
    assert by_mac[CLONER1]["number"] is None and by_mac[CLONER1]["display_name"] == "shich-1"


def test_the_journal_names_a_numbered_machine_by_its_display_name(room_server):
    from server.console import journal_he
    admin, ctx = room_server["admin"], room_server["ctx"]
    assert _add(admin, CLONER3, "3").status_code == 200
    text = journal_he.JournalTranslator(ctx.conn, ctx.library)._names(f"{CLONER3} poweroff")
    assert text.startswith("משכפל-3 · "), text


# --- "השאר דלוקים" -------------------------------------------------------------

def _power_requests(ctx) -> dict[str, str]:
    return {r["mac"]: r["action"] for r in ctx.conn.execute("SELECT mac, action FROM power_requests")}


def _journal(ctx, event):
    return [(r["detail"], r["machine"], r["user"]) for r in ctx.conn.execute(
        "SELECT detail, machine, user FROM journal WHERE event = ? ORDER BY id", (event,))]


@pytest.fixture()
def three(room_server):
    """שלושה משכפלים ממוספרים 1-3 (שני הישנים, shich-1/2, נשארים בחדר)."""
    admin = room_server["admin"]
    ms = {n: f"aa:bb:cc:00:01:0{n}" for n in (1, 2, 3)}
    for n, mac in ms.items():
        assert _add(admin, mac, str(n)).status_code == 200
    return {**room_server, "ms": ms}


def test_keep_on_powers_off_everyone_not_chosen_and_nobody_chosen(three):
    admin, ctx, ms = three["admin"], three["ctx"], three["ms"]
    r = admin.post("/api/console/room/keep-on", json={"keep": [1]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [m["name"] for m in body["kept"]] == ["משכפל-1"]
    assert {m["mac"] for m in body["off"]} == {ms[2], ms[3], CLONER1, CLONER2}
    assert body["requested"] == 4 and body["busy"] == []
    assert _power_requests(ctx) == {ms[2]: "poweroff", ms[3]: "poweroff", CLONER1: "poweroff", CLONER2: "poweroff"}


@pytest.mark.parametrize("keep", [["משכפל-2"], ["cloner-2"], [2], ["2"], ["02"], [" 2 "]])
def test_a_machine_is_kept_by_display_name_technical_name_or_number(three, keep):
    r = three["admin"].post("/api/console/room/keep-on", json={"keep": keep})
    assert r.status_code == 200, r.text
    assert [m["mac"] for m in r.json()["kept"]] == [three["ms"][2]]
    assert three["ms"][2] not in _power_requests(three["ctx"])


def test_a_machine_is_kept_by_mac_and_an_old_name_by_its_name(three):
    r = three["admin"].post("/api/console/room/keep-on", json={"keep": [three["ms"][3], "shich-1"]})
    assert r.status_code == 200, r.text
    assert {m["mac"] for m in r.json()["kept"]} == {three["ms"][3], CLONER1}


@pytest.mark.parametrize("keep", [[], None, "משכפל-1", 1, [1, "משכפל-9"], [1, 4], [{"x": 1}], ["shich-9"]])
def test_a_list_that_cannot_be_read_powers_off_nothing(three, keep):
    """בקשה שלא הובנה אינה "כבה את כולם" (עיקרון 5): 400, ואף בקשת כיבוי לא נרשמה."""
    r = three["admin"].post("/api/console/room/keep-on", json={"keep": keep})
    assert r.status_code == 400, r.text
    assert _power_requests(three["ctx"]) == {}
    assert _journal(three["ctx"], "room_keep_on_off") == []


def test_a_number_does_not_pick_an_old_named_machine(three):
    r = three["admin"].post("/api/console/room/keep-on", json={"keep": [1, "shich-2"]})
    assert r.status_code == 200
    assert three["ms"][1] in {m["mac"] for m in r.json()["kept"]} and CLONER2 in {m["mac"] for m in r.json()["kept"]}


def test_the_request_rides_the_next_hello_of_the_machine_and_only_of_the_machines_to_stop(three):
    anon, ms = three["anon"], three["ms"]
    assert three["admin"].post("/api/console/room/keep-on", json={"keep": [1]}).status_code == 200
    stopped = anon.post("/api/v1/agent/hello", json=hello_body(ms[2])).json()
    kept = anon.post("/api/v1/agent/hello", json=hello_body(ms[1])).json()
    assert stopped.get("power_action") == "poweroff"
    assert kept.get("power_action") is None


def test_the_journal_says_who_was_powered_off_and_why(three):
    ctx, ms = three["ctx"], three["ms"]
    assert three["admin"].post("/api/console/room/keep-on", json={"keep": [1, 3]}).status_code == 200
    off = _journal(ctx, "room_keep_on_off")
    assert {machine for _, machine, _ in off} == {ms[2], CLONER1, CLONER2}
    assert all(user == "noc" for _, _, user in off)
    assert all("נשארים דלוקים: משכפל-1, משכפל-3" in detail and "השאר דלוקים" in detail for detail, _, _ in off)
    (summary,) = _journal(ctx, "room_keep_on")
    assert "כיבוי התבקש: 3" in summary[0]


def test_a_machine_in_the_middle_of_a_round_is_not_powered_off(three):
    """משכפל שהצטרף לגל הפתוח (או רץ) אינו נכבה גם אם לא נבחר — ונרשם ביומן כמי שדולג."""
    deploy, admin, anon, ctx, ms = three["deploy"], three["admin"], three["anon"], three["ctx"], three["ms"]
    drawers_hello(anon, ms[1], [disk("T1", 1)])
    drawers_hello(anon, ms[2], [disk("T2", 1)])
    _open(deploy, target=2, slots=[{"mac": ms[1], "ports": [1]}, {"mac": ms[2], "ports": [1]}])
    drawers_hello(anon, ms[1], [disk("T1", 1)])      # מצטרף לגל
    drawers_hello(anon, ms[2], [disk("T2", 1)])
    r = admin.post("/api/console/room/keep-on", json={"keep": [3]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert {m["mac"]: m["reason"] for m in body["busy"]} == {ms[1]: "in_round", ms[2]: "in_round"}
    assert {m["mac"] for m in body["off"]} == {CLONER1, CLONER2}
    assert _power_requests(ctx) == {CLONER1: "poweroff", CLONER2: "poweroff"}
    busy = _journal(ctx, "room_keep_on_busy")
    assert {machine for _, machine, _ in busy} == {ms[1], ms[2]} and all("באמצע סבב" in d for d, _, _ in busy)


def test_a_machine_copying_locally_is_not_powered_off(three):
    anon, ms = three["anon"], three["ms"]
    body = hello_body(ms[2])
    body["local_clone"] = "copying"
    assert anon.post("/api/v1/agent/hello", json=body).status_code == 200
    r = three["admin"].post("/api/console/room/keep-on", json={"keep": [1]})
    assert r.status_code == 200, r.text
    assert [(m["mac"], m["reason"]) for m in r.json()["busy"]] == [(ms[2], "local_copy")]
    assert ms[2] not in _power_requests(three["ctx"])


def test_a_finished_wave_does_not_hold_its_machines(three):
    """זה המקרה של #3064: הגל הראשון נסגר, ומי שכתב בו כבר אינו "באמצע סבב"."""
    ctx, ms = three["ctx"], three["ms"]
    ctx.conn.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds,"
                     " state, opened_by, created_at, last_join_at) VALUES"
                     " ('s_old', 'grp_CLONERS', 'img', 'ROOM', 1, 1, 'closed', 't', '2026-11-14T00:00:00+00:00', 0)")
    ctx.conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at) VALUES"
                     " ('s_old', ?, 'done', '2026-11-14T00:00:00+00:00')", (ms[2],))
    ctx.conn.commit()
    r = three["admin"].post("/api/console/room/keep-on", json={"keep": [1]})
    assert r.status_code == 200 and r.json()["busy"] == []
    assert ms[2] in _power_requests(ctx)


def test_only_an_admin_may_ask_like_the_power_off_of_the_whole_room(three):
    assert three["anon"].post("/api/console/room/keep-on", json={"keep": [1]}).status_code in (401, 403)
    assert three["deploy"].post("/api/console/room/keep-on", json={"keep": [1]}).status_code == 403
    assert _power_requests(three["ctx"]) == {}
    assert three["admin"].post("/api/console/room/keep-on", json={"keep": [1]}).status_code == 200
    assert _journal(three["ctx"], "room_keep_on")[-1][2] == "noc"


def test_a_deploy_user_cannot_power_machines_off_by_opening_a_round(three):
    """הקיוסק נכנס ל-`POST /room`; `keep_on` שם נדחה לפני שהסבב נפתח, ושום כיבוי לא נרשם."""
    deploy, anon, ctx, ms = three["deploy"], three["anon"], three["ctx"], three["ms"]
    drawers_hello(anon, ms[1], [disk("T1", 1)])
    r = deploy.post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 1,
        "target_slots": [{"mac": ms[1], "ports": [1]}], "keep_on": [1]})
    assert r.status_code == 403, r.text
    assert room(deploy)["round"] is None and _power_requests(ctx) == {}


def test_opening_a_round_with_keep_on_keeps_the_round_machines_and_stops_the_rest(three):
    deploy, anon, ctx, ms = three["admin"], three["anon"], three["ctx"], three["ms"]
    drawers_hello(anon, ms[1], [disk("T1", 1)])
    body = {"image_id": "img_7f3a91", "target_drives": 1,
            "target_slots": [{"mac": ms[1], "ports": [1]}], "keep_on": [2]}
    r = deploy.post("/api/console/room", json=body)
    assert r.status_code == 200, r.text
    result = r.json()["keep_on"]
    # 1 = המשכפל של הסבב (לא נבחר, ולא נכבה), 2 = נבחר; השאר — כבויים
    assert {m["mac"] for m in result["kept"]} == {ms[1], ms[2]}
    assert {m["mac"] for m in result["off"]} == {ms[3], CLONER1, CLONER2}
    assert set(_power_requests(ctx)) == {ms[3], CLONER1, CLONER2}
    assert "סבב" in _journal(ctx, "room_keep_on")[-1][0]


def test_a_keep_on_list_that_cannot_be_read_opens_no_round(three):
    deploy, anon, ctx, ms = three["admin"], three["anon"], three["ctx"], three["ms"]
    drawers_hello(anon, ms[1], [disk("T1", 1)])
    body = {"image_id": "img_7f3a91", "target_drives": 1,
            "target_slots": [{"mac": ms[1], "ports": [1]}], "keep_on": ["משכפל-9"]}
    r = deploy.post("/api/console/room", json=body)
    assert r.status_code == 400, r.text
    assert room(deploy)["round"] is None
    assert _power_requests(ctx) == {}


def test_a_round_opens_as_before_without_keep_on(three):
    deploy, anon, ctx, ms = three["deploy"], three["anon"], three["ctx"], three["ms"]
    drawers_hello(anon, ms[1], [disk("T1", 1)])
    r = deploy.post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 1, "target_slots": [{"mac": ms[1], "ports": [1]}]})
    assert r.status_code == 200 and "keep_on" not in r.json()
    assert _power_requests(ctx) == {}


# --- מה שהמפעיל קורא בטקסטים: שם התצוגה; מה שמזהה נשאר יציב ----------------------

def test_the_round_initiator_is_the_display_name_for_a_numbered_cloner_and_as_before_for_an_old_name():
    from server.rounds import cloner_round
    numbered = {"suffix": "cloner-3", "mac": MAC_A}
    old = {"suffix": "shich-1", "mac": MAC_A}
    assert cloner_round.initiator(numbered) == "משכפל-3"
    assert cloner_round._origin(numbered) == "על ידי משכפל-3"
    assert cloner_round.initiator(old) == "מחשב שיכפול shich-1"
    assert cloner_round._origin(old) == "ממחשב שיכפול shich-1"


def test_the_fit_refusal_names_the_machine_as_the_operator_reads_it_and_identifies_it_by_mac(three):
    from server.rounds import room
    ctx, ms = three["ctx"], three["ms"]
    from conftest import room_slots
    room_slots(ctx.conn, {ms[1]: ["T1"], CLONER1: ["T2"]}, size_bytes=100 * 10**9)
    floor = room.disk_floor(ctx.conn)
    assert floor["name"] in ("משכפל-1", "shich-1") and floor["mac"] in (ms[1], CLONER1)
    only_new = room.disk_floor(ctx.conn, [{"mac": ms[1], "ports": [1]}])
    assert (only_new["mac"], only_new["name"]) == (ms[1], "משכפל-1")
    only_old = room.disk_floor(ctx.conn, [{"mac": CLONER1, "ports": [1]}])
    assert (only_old["mac"], only_old["name"]) == (CLONER1, "shich-1")
