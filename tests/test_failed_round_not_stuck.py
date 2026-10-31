"""17/10 (ברזל): סבב שנכשל אינו חוטף את מסך הבחירה, ובמסך הסבב — רק מה שנבחר.

מה קרה: מחשב שיכפול 2 פתח סבב "שחזור מהשרת", הסבב נכשל ("אף מחשב לא הצטרף
לשידור תוך 180 שניות") ונשאר הסבב הנראה של ‏`GET /api/console/room` עד שנדב
עצר אותו ידנית. בינתיים "שיכפול מהשרת" במחשב הבנייה הראה את מסך הבחירה
לשנייה וקפץ למסך הסבב החי של הסבב הכושל ("גל 1 משדר").

השורש לא היה בשרת: ‏`open_round` נחסם רק על `active` (‏`room.active_round`),
וסבב `failed` אינו חוסם פתיחה (‏#1126). השורש בשרשרת הגואי: ‏`guistate.sh`
כתב `round=` לכל `.round` שאינו null — גם `failed` — ו-`state.c` הפך כל
‏`round=` ל-`has_round=1`, כלומר למסך הסבב החי.

החוליות כאן:

* **השרת** — ב-`test_cloner_round_server.py`: סבב `failed` ואז פתיחה חדשה מהמסך
  של מחשב השיכפול מצליחה והסיבה נשארת על השורה הישנה; סבב פעיל עדיין חוסם (‏409).
* **הסוכן** (‏`guistate.sh`) — סבב `failed` אינו `round=` אלא `room_failed=<סיבה>`;
  סבב חי עם `target_slots` מציג רק את המשכפלים שבו, בלי בחירה (סבב ישן) — כולם.
* **הגואי** (‏`--png`, על המעבדה) — `room_failed=` היא שורה אדומה במסך הבחירה.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from native import requires_native
from test_class_deploy_switch import PRELUDE, STATION
from test_room_precheck_native_432_65 import _render
from test_cloner_gui import BASH, _build_gui, _count_color, _pkgconfig, _png_rgb, _token, posix, sh

C1, C2, C3 = "3c:52:82:a1:00:21", "3c:52:82:a1:00:22", "3c:52:82:a1:00:23"
REASON = "אף מחשב לא הצטרף לשידור תוך 180 שניות"


# --- הסוכן: guistate.sh --------------------------------------------------------------

def _drawer(port: int, serial: str) -> dict:
    return {"dev": f"sd{'abc'[port - 1]}", "port": port, "serial": serial, "model": "M",
            "size_bytes": 256060514304, "smart": "ok", "fresh": True, "selected": False}


def _machine(name: str, mac: str, serials: list[str]) -> dict:
    return {"name": name, "mac": mac, "awake": True, "joined": False, "fresh_drawers": len(serials),
            "state": None, "drawer_count": len(serials),
            "drawer_list": [_drawer(i + 1, s) for i, s in enumerate(serials)]}


def _room(round_: dict | None) -> dict:
    return {"round": round_, "group_label": "מחשבי שיכפול", "machines": [
        _machine("1", C1, ["S1", "S2"]), _machine("2", C2, ["S3", "S4"]), _machine("SOAK-1", C3, ["S5"])]}


def _round(**extra) -> dict:
    return {"id": "room_0e99b142", "state": "active", "failed_reason": None, "image_name": "office365",
            "wave_number": 1, "wave_state": "open", "written_drives": 0, "target_drives": 2,
            "ready_drives": 0, "remaining_drives": 2, "precheck": [], "missing": [],
            "target_slots": [{"mac": C2, "ports": [1, 2]}], **extra}


def gui_state_lines(tmp_path: Path, room: dict, mode: str = "room") -> list[str]:
    run = tmp_path / "run"; run.mkdir(parents=True)
    gui = tmp_path / "gui"; gui.mkdir()
    (run / "station.json").write_text(json.dumps(STATION), newline="\n")
    (run / "room.json").write_text(json.dumps(room), newline="\n")
    (gui / "mode").write_text(mode + "\n", newline="\n")
    out = sh(f'export RUN_DIR={posix(run)!r} GUI_DIR={posix(gui)!r} '
             f'SERVER=http://127.0.0.1:9 MAC=aa:bb:cc:dd:ee:ff IMAGESRV_TEST=1; '
             + PRELUDE
             + f'http_get() {{ cat {posix(run / "station.json")!r}; }}; '
             + f'gui_get() {{ case "$1" in room) cp {posix(run / "room.json")!r} "$2" ;; '
             + 'images) printf "[]" > "$2" ;; *) return 1 ;; esac; }; '
             + 'gui_state')
    assert out.returncode == 0, out.stderr
    return (gui / "state").read_text(encoding="utf-8").splitlines()


def _machines(lines: list[str]) -> list[str]:
    return [l.split("|")[1] for l in lines if l.startswith("machine=")]


@requires_native(("bash", BASH), "jq", why="gui_state בונה את המצב ב-jq")
@pytest.mark.parametrize("mode", ["room", "direct"])
def test_a_failed_round_is_a_line_not_a_live_round(tmp_path, mode):
    failed = _round(state="failed", failed_reason=REASON, wave_state="failed",
                    precheck=[{"text_he": "x"}], missing=[{"mac": C1, "name": "1", "reason": "off",
                                                          "can_poweroff": False, "text_he": "y"}])
    lines = gui_state_lines(tmp_path, _room(failed), mode)
    assert not [l for l in lines if l.startswith(("round=", "room_warn=", "room_missing="))], lines
    assert f"room_failed={REASON}" in lines, lines
    # מסך הבחירה — כל המשכפלים, לא רק אלה של הסבב שנכשל
    assert _machines(lines) == [C1, C2, C3], lines


@requires_native(("bash", BASH), "jq", why="gui_state בונה את המצב ב-jq")
def test_a_failed_round_with_no_reason_still_says_it_failed(tmp_path):
    lines = gui_state_lines(tmp_path, _room(_round(state="failed", failed_reason=None)))
    assert "room_failed=סיבה לא ידועה" in lines, lines
    assert not [l for l in lines if l.startswith("round=")], lines


@requires_native(("bash", BASH), "jq", why="gui_state בונה את המצב ב-jq")
def test_a_live_round_shows_only_the_cloners_it_targets(tmp_path):
    lines = gui_state_lines(tmp_path, _room(_round()))
    assert [l for l in lines if l.startswith("round=office365|")], lines
    assert not [l for l in lines if l.startswith("room_failed=")], lines
    assert _machines(lines) == [C2], lines
    # האינדקס של המגירות הוא של הרשימה המסוננת: מחשב 2 הוא 0, ואין 1/2
    drawers = [l.split("=", 1)[1].split("|")[:3] for l in lines if l.startswith("room_drawer=")]
    assert drawers == [["0", "1", "S3"], ["0", "2", "S4"]], drawers
    assert [l for l in lines if l.startswith("machine_drawers=")] == ["machine_drawers=0|2"], lines


@requires_native(("bash", BASH), "jq", why="gui_state בונה את המצב ב-jq")
def test_no_round_or_an_old_round_without_a_choice_shows_every_cloner(tmp_path):
    assert _machines(gui_state_lines(tmp_path / "idle", _room(None))) == [C1, C2, C3]
    old = gui_state_lines(tmp_path / "old", _room(_round(target_slots=None)))
    assert [l for l in old if l.startswith("round=")] and _machines(old) == [C1, C2, C3], old


@requires_native(("bash", BASH), "jq", why="gui_state בונה את המצב ב-jq")
def test_each_drawer_carries_its_own_target_not_the_machines_progress(tmp_path):
    """17/10 (ברזל): נבחרה רק מגירה 3 ונכתבה רק היא, והמסך הראה 16% גם על 2.
    ‏`room_drawer=` נושא את היעד של המגירה עצמה (שדות 16–17): ‏-1 וריק למגירה
    שאינה בגל, האחוז והמצב שלה למגירה שבגל — לא סכום המכונה."""
    room = _room(_round(wave_state="running", target_slots=[{"mac": C2, "ports": [2]}]))
    m2 = room["machines"][1]
    m2.update(joined=True, state="writing", bytes_written=16, bytes_total=100)
    m2["drawer_list"][1].update(state="writing", bytes_written=40, bytes_total=250, selected=True)
    lines = gui_state_lines(tmp_path, room)
    drawers = [l.split("=", 1)[1].split("|") for l in lines if l.startswith("room_drawer=")]
    assert [(d[2], d[15], d[16]) for d in drawers] == [("S3", "-1", ""), ("S4", "16", "writing")], drawers
    assert [l.split("|")[6] for l in lines if l.startswith("machine=")] == ["16"], lines


@requires_native(("bash", BASH), "jq", why="gui_state בונה את המצב ב-jq")
@pytest.mark.parametrize("wave", ["running", "closed"])
def test_the_round_record_carries_the_wave_state(tmp_path, wave):
    lines = gui_state_lines(tmp_path, _room(_round(wave_state=wave)))
    rnd = [l for l in lines if l.startswith("round=")]
    assert len(rnd) == 1 and rnd[0].split("|")[10] == wave, rnd


# --- הגואי ---------------------------------------------------------------------------

_GUI = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)

PICK = (f"machine=1|{C1}|1|0|2||-1|0|\n"
        "machine_drawers=0|2\n"
        "room_drawer=0|1|S1|M|256060514304|passed|1|0||||ok\n"
        "room_drawer=0|2|S2|M|256060514304|passed|1|0||||ok\n"
        "image=img_1|office365|\n"
        "message=x|y\n")          # the png pass draws every card; "message" needs one


def test_the_state_reader_knows_room_failed_and_never_makes_it_a_round():
    root = Path(__file__).resolve().parent.parent / "native-gui" / "src"
    state = (root / "state.c").read_text(encoding="utf-8")
    line = next(l for l in state.splitlines() if 'KEY("room_failed")' in l)
    assert "room_failed" in line and "has_round" not in line, line
    assert "הסבב הקודם נכשל: %s" in (root / "screens_room.c").read_text(encoding="utf-8")


@_GUI
def test_the_picker_says_the_last_round_failed_in_red(tmp_path):
    binary = _build_gui(tmp_path)
    _render(binary, PICK, tmp_path / "plain")
    _render(binary, PICK + f"room_failed={REASON}\n", tmp_path / "failed")
    for theme in ("light", "dark"):
        red = _token("--clr-danger-fg", theme)
        _w, _h, ch, px = _png_rgb(tmp_path / f"plain-room-{theme}.png")
        before = _count_color(px, ch, red)
        _w, _h, ch, px = _png_rgb(tmp_path / f"failed-room-{theme}.png")
        after = _count_color(px, ch, red)
        assert after - before > 300, (theme, before, after)


LIVE = (f"machine=2|{C2}|1|1|0|writing|80|1|\n"
        "machine_drawers=0|2\n"
        "room_drawer=0|1|S3|M|256060514304|passed|1|0||||ok{d1}\n"
        "room_drawer=0|2|S4|M|500107862016|passed|1|1||||ok{d2}\n"
        "round=office365|1|0|0|1|0|1|60|50000000|300{wave}\n"
        "message=x|y\n")


def _green(path: Path, theme: str) -> int:
    """The writing bars (lcbay.c, 17/10: the local clone's blue, --clr-action)."""
    _w, _h, ch, px = _png_rgb(path)
    return _count_color(px, ch, _token("--clr-action", theme), tol=6)


@_GUI
def test_the_live_grid_draws_each_drawer_by_its_own_target(tmp_path):
    """שלושה ציורים של אותה מכונה ב-80%: סוכן ישן (בלי שדות 16–17) — שתי
    המגירות 80%, כמו היום; חדש — רק מגירה 2 בגל (80%), ומגירה 1 בלי פס; וחדש
    שבו מגירה 2 ב-20%. הפסים הירוקים יורדים בכל צעד — כלומר האחוז הוא של
    המגירה, ולא של המכונה על כל מגירותיה."""
    binary = _build_gui(tmp_path)
    _render(binary, LIVE.format(d1="", d2="", wave=""), tmp_path / "old")
    _render(binary, LIVE.format(d1="||||-1|", d2="||||80|writing", wave="|running"), tmp_path / "own")
    _render(binary, LIVE.format(d1="||||-1|", d2="||||20|writing", wave="|running"), tmp_path / "low")
    for theme in ("light", "dark"):
        old = _green(tmp_path / f"old-room-live-{theme}.png", theme)
        own = _green(tmp_path / f"own-room-live-{theme}.png", theme)
        low = _green(tmp_path / f"low-room-live-{theme}.png", theme)
        assert old - own > 300 and own - low > 300, (theme, old, own, low)


@_GUI
def test_a_failed_wave_reads_as_failed(tmp_path):
    """17/10 (ברזל): הסבב נכשל והפס אמר "הגל משדר". גל `failed` כותב את
    הכותרת באדום (‏--clr-danger-fg, "הגל נכשל"); גל `running` — לא."""
    binary = _build_gui(tmp_path)
    _render(binary, LIVE.format(d1="", d2="", wave="|running"), tmp_path / "run")
    _render(binary, LIVE.format(d1="", d2="", wave="|failed"), tmp_path / "fail")
    for theme in ("light", "dark"):
        soft = _token("--clr-danger-fg", theme)
        _w, _h, ch, px = _png_rgb(tmp_path / f"run-room-live-{theme}.png")
        before = _count_color(px, ch, soft, tol=6)
        _w, _h, ch, px = _png_rgb(tmp_path / f"fail-room-live-{theme}.png")
        after = _count_color(px, ch, soft, tol=6)
        assert after - before > 300, (theme, before, after)


def test_only_a_running_wave_says_it_is_broadcasting():
    room_c = (Path(__file__).resolve().parent.parent / "native-gui" / "src" / "screens_room.c"
              ).read_text(encoding="utf-8")
    live = room_c[room_c.index("/* ---- renderLive"):]
    # #1331 (mockup v2, d19de45a): the label also drops "משדר" for a wave that
    # ended -- failed says "נכשל", ended says nothing, only running says "משדר".
    assert 'wave_failed ? "נכשל: " : wave_over ? "" : "משדר: "' in live
    assert 'wave_over = !s->wave_open && s->wave_state[0] && strcmp(s->wave_state, "running");' in live
    # 17/10 ("גרסה ב׳"): a failed wave's title, before any "משדר" title
    assert live.index('if (wave_failed) {') < live.index('"משדר ל-%d דיסקים')


# --- "הער": רק מה שנבחר (סעיפים 4–5, נדב 17/10) ----------------------------------
#
# ברזל 17/10: הכפתור "הער את מחשבי השיכפול" במסך הבחירה לא שלח דבר — הכרעת 16/10
# צבעה אותו אפור ו-`main.c` שלח `room-wake` רק כשיש סבב. וכשנשלח — השרת העיר את
# כל החדר (‏`wol_sent grp_CLONERS count=3`), כולל מחשב שיכפול 1 שאסור להעיר מרחוק;
# גם פתיחת סבב העירה את כולם, לא רק את מי שב-`target_slots`.

from test_room_target_slots_1275 import MAIN_C, _bridge, bridge_tools, function_body  # noqa: E402
from test_server_room import CLONER1, CLONER2, room_body, room_server  # noqa: E402,F401
from test_wol import macs_of  # noqa: E402


def test_opening_a_round_wakes_only_the_cloners_it_writes_to(room_server):
    opened = room_server["deploy"].post(
        "/api/console/room", json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}))
    assert opened.status_code == 200, opened.text
    assert macs_of(room_server["woken"]) == {CLONER1}


def test_wake_with_a_pick_wakes_only_the_pick_and_says_so_in_the_journal(room_server):
    r = room_server["deploy"].post("/api/console/room/wake", json={"macs": [CLONER2.upper()]})
    assert r.status_code == 200 and r.json()["sent"] == 1, r.text
    assert macs_of(room_server["woken"]) == {CLONER2}
    row = room_server["ctx"].conn.execute(
        "SELECT detail FROM journal WHERE event = 'wol_sent' ORDER BY id DESC").fetchone()
    assert row["detail"] == f"grp_CLONERS count=1 macs={CLONER2}"


@pytest.mark.parametrize("macs", [[], "aa:bb:cc:00:00:21", ["de:ad:be:ef:00:99"], [CLONER1, 7], None])
def test_a_pick_that_cannot_be_read_wakes_nobody(room_server, macs):
    """רשימה ריקה, לא רשימה, מכונה שאינה מחשב שיכפול רשום — 400, ולא "כל החדר"."""
    r = room_server["deploy"].post("/api/console/room/wake", json={"macs": macs})
    assert r.status_code == 400, r.text
    assert room_server["woken"] == []


@pytest.mark.parametrize("content", [b"not json", b'["aa:bb:cc:00:00:21"]',
                                     b'{"mac": ["aa:bb:cc:00:00:21"]}', b"{}"])
def test_a_body_that_was_not_understood_wakes_nobody(room_server, content):
    """סקירת אבטחה (#1577): גוף שאינו JSON, JSON שאינו אובייקט, או אובייקט בלי `macs`
    ("mac" בשגיאת כתיב) החזירו 200 והעירו את כל החדר. רק **בלי גוף בכלל** — כל החדר."""
    r = room_server["deploy"].post("/api/console/room/wake", content=content,
                                   headers={"content-type": "application/json"})
    assert r.status_code == 400, r.text
    assert room_server["woken"] == []


def test_the_console_wake_without_a_pick_still_wakes_the_room(room_server):
    assert room_server["deploy"].post("/api/console/room/wake").status_code == 200
    assert macs_of(room_server["woken"]) == {CLONER1, CLONER2}


@requires_native(("bash", BASH), "jq", why="room_action בונה את הגוף ב-jq")
def test_the_bridge_posts_the_picked_cloners(tmp_path):
    rc, body = _bridge(tmp_path, f"room-wake\ncloner={CLONER1},{CLONER2}\n\n")
    assert rc == 0 and json.loads(body) == {"macs": [CLONER1, CLONER2]}, (rc, body)


@bridge_tools
@pytest.mark.parametrize("record", ["room-wake\n\n", "room-wake\ncloner=\n\n",
                                    "room-wake\ncloner=aa:bb;reboot\n\n"])
def test_the_bridge_never_wakes_the_whole_room(tmp_path, record):
    rc, body = _bridge(tmp_path, record)
    assert rc != 0 and body is None, (rc, body)


def test_the_wake_click_sends_the_picked_cloners():
    """הלחיצה (וגם W) עוברת ל-`room_wake`, שתמיד שולח `cloner=`; בלי בחירה —
    הודעה ולא כלום. הכפתור במסך הבחירה אינו אפור."""
    assert re.search(r"case HIT_ROOM_WAKE:\s*room_wake\(a\);", MAIN_C)
    assert "if (a->st.has_round) emit1(\"room-wake\")" not in MAIN_C
    body = function_body(MAIN_C, "room_wake")
    emit = body.index('emit("room-wake")')
    assert body.index('emit_kv("cloner", macs)') > emit
    assert "if (!n) { toast(" in body[:emit]
    room_c = (Path(__file__).resolve().parent.parent / "native-gui" / "src" / "screens_room.c"
              ).read_text(encoding="utf-8")
    assert "{ BTN_PRIMARY, BTN_PLAIN, BTN_PLAIN }" in room_c
    assert "{ HIT_ROOM_OPEN, HIT_ROOM_WAKE, HIT_BACK }" in room_c


def test_an_off_machine_can_be_picked_and_select_all_picks_the_room():
    """נדב 17/10: שם של מחשב כבוי הוא כפתור (בוחר אותו להערה), ו"בחר / נקה הכל"
    בוחר גם את המחשבים עצמם — כבויים כלולים."""
    room_c = (Path(__file__).resolve().parent.parent / "native-gui" / "src" / "screens_room.c"
              ).read_text(encoding="utf-8")
    draw = function_body(room_c, "room_machine_draw")
    assert "HIT_ROOM_MACHINE_BASE + mi," in draw and "m->awake ? HIT_ROOM_MACHINE_BASE" not in draw
    sel = function_body(MAIN_C, "room_select_range")
    assert sel.index("room_pick_set(a, mac, val);") < sel.index("if (!m->awake) continue;")


# --- הקונסולה: סבב אחד = שורה אחת (נדב 17/10, room_855f6a13) ------------------------

def test_the_round_names_its_wave_and_the_wave_counts_only_the_rounds_cloners(room_server):
    """‏`/overview.room.wave_session_id` מזהה את `/overview.session` של אותו סבב (הקונסולה
    מציגה שורה אחת), ו-`expected_clients` של הגל הוא מחשבי הסבב — לא "1/3 מחשבים"."""
    admin = room_server["admin"]
    assert admin.post("/api/console/room", json=room_body(
        room_server, 2, {CLONER1: ["S1", "S2"]})).status_code == 200
    o = admin.get("/api/console/overview").json()
    assert o["room"]["wave_session_id"] and o["room"]["wave_session_id"] == o["session"]["id"]
    assert o["session"]["expected_clients"] == 1, o["session"]


def test_a_cloner_of_another_cloner_group_can_be_woken_by_its_mac(room_server):
    """סקירת אבטחה (LOW, #1577): הבדיקה לפי תפקיד הקבוצה (`cloner`) ולא לפי `grp_CLONERS`,
    כדי ש"הער" ימשיך לעבוד כשהחדר כולל כל קבוצת שיכפול (#1555), בכל סדר מיזוג. מכונה
    בקבוצה שאינה שיכפול — 400."""
    ctx, deploy = room_server["ctx"], room_server["deploy"]
    other = "aa:bb:cc:00:00:31"
    ctx.conn.execute("INSERT INTO groups (id, label, role, sort) VALUES ('grp_CLONERS_B', 'ב', 'cloner', 9)")
    ctx.conn.execute("INSERT INTO groups (id, label, role, sort) VALUES ('grp_LAB9', 'כיתה', 'classroom', 10)")
    ctx.conn.execute("INSERT INTO machines (mac, suffix, group_id, added_at) VALUES (?, 'B1', 'grp_CLONERS_B', '2026-10-17')", (other,))
    ctx.conn.execute("INSERT INTO machines (mac, suffix, group_id, added_at) VALUES ('aa:bb:cc:00:00:41', 'L1', 'grp_LAB9', '2026-10-17')")
    ctx.conn.commit()
    r = deploy.post("/api/console/room/wake", json={"macs": [other]})
    assert r.status_code == 200 and r.json()["sent"] == 1, r.text
    assert macs_of(room_server["woken"]) == {other}
    assert deploy.post("/api/console/room/wake", json={"macs": ["aa:bb:cc:00:00:41"]}).status_code == 400


def test_a_room_wave_admits_a_cloner_of_another_cloner_group_in_its_roster(room_server):
    """מנהל השחרור (עץ משולב עם #1555): ה-`roster` של גל החדר נבדק מול `grp_CLONERS`
    בלבד, ולכן סבב שבחר משכפל מקבוצת שיכפול אחרת נכשל ב-409 "מחשב שאינו רשום בקבוצה".
    הבדיקה לפי תפקיד: כל משכפל — כן; מחשב כיתה — לא."""
    from server.rounds.sessions import SessionError        # noqa: PLC0415
    ctx = room_server["ctx"]
    other = "aa:bb:cc:00:00:31"
    ctx.conn.execute("INSERT INTO groups (id, label, role, sort) VALUES ('grp_HAIFA', 'חיפה', 'cloner', 9)")
    ctx.conn.execute("INSERT INTO groups (id, label, role, sort) VALUES ('grp_LAB9', 'כיתה', 'classroom', 10)")
    ctx.conn.execute("INSERT INTO machines (mac, suffix, group_id, added_at) VALUES (?, 'h1', 'grp_HAIFA', '2026-10-17')", (other,))
    ctx.conn.execute("INSERT INTO machines (mac, suffix, group_id, added_at) VALUES ('aa:bb:cc:00:00:41', 'L1', 'grp_LAB9', '2026-10-17')")
    ctx.conn.commit()
    with pytest.raises(SessionError, match="אינו רשום"):
        ctx.store.open("grp_CLONERS", "img_7f3a91", "ROOM", 1, "test", roster=["aa:bb:cc:00:00:41"])
    wave = ctx.store.open("grp_CLONERS", "img_7f3a91", "ROOM", 2, "test", roster=[CLONER1, other])
    assert ctx.store.in_roster(ctx.conn.execute("SELECT * FROM sessions WHERE id = ?", (wave,)).fetchone(), other)
