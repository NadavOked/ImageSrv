"""‏#432 + #65 בגואי הנייטיב — מחשב הבנייה (הכרעת נדב 05/09: "הפלט אמור להיות
במחשב בנייה") ומסך ההמתנה של המשכפל (#1304).

החוליות:

* **הסוכן** (‏`guistate.sh`): ‏`round.precheck` → ‏`room_warn=`, ‏`round.missing` →
  ‏`room_missing=`, ו-`link_state` כשדה 12 של `room_drawer=`.
* **הגשר** (‏`guibridge.sh` → ‏`room_poweroff` ב-`roomflow.sh`): ‏`room-poweroff`
  עם `cloner=`/`confirm=` שולח POST ל-`/room/missing/<mac>/poweroff`; MAC פגום —
  כלום לא נשלח.
* **הגואי** (‏`--png`, על המעבדה בלבד): הקופסה הכתומה במסך הגל החי, שני חלונות
  האישור (התחל בכל זאת — כתום; כבה אותו — אדום), ומגירה איטית במסך ההמתנה.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from native import requires_native
from test_agent import AGENT
from test_class_deploy_switch import PRELUDE, STATION
from test_cloner_gui import (BASH, GUI, _build_gui, _count_color, _pkgconfig,
                             _png_rgb, _routing_error, _token, posix, sh)
from agentlib import flat_lib

C1, C2 = "3c:52:82:a1:00:21", "3c:52:82:a1:00:22"
WARN = "CLONE-01 · דיסק 2: קישור 1.5 מתוך 6.0 Gb/s — לבדוק כבל או מגירה"
MISSING = "CLONE-02: אין דיסק חדש בדיסק 3"


def _drawer(port, serial, link_state="ok"):
    return {"dev": f"sd{'abc'[port - 1]}", "port": port, "serial": serial, "model": "M",
            "size_bytes": 256060514304, "smart": "ok", "fresh": True, "selected": True,
            "link_gbps": 1.5 if link_state == "slow" else 6.0, "link_max_gbps": 6.0,
            "link_state": link_state}


ROOM = {"round": {"image_name": "img", "wave_number": 1, "wave_state": "open",
                  "written_drives": 0, "target_drives": 5, "ready_drives": 4, "remaining_drives": 5,
                  "precheck": [{"mac": C1, "port": 2, "text_he": WARN}],
                  "missing": [{"mac": C2, "name": "CLONE-02", "reason": "drawers", "ports": [3],
                               "awake": True, "joined": True, "can_poweroff": True, "text_he": MISSING},
                              {"mac": "3c:52:82:a1:00:23", "name": None, "reason": "off", "ports": [],
                               "awake": False, "joined": False, "can_poweroff": False,
                               "text_he": "CLONE-03 כבוי | או לא מחובר"}]},
        "group_label": "מחשבי שיכפול", "machines": [
            {"name": "CLONE-01", "mac": C1, "awake": True, "joined": True, "fresh_drawers": 2,
             "state": "waiting", "drawer_count": 2,
             "drawer_list": [_drawer(1, "S1"), _drawer(2, "S2", "slow")]},
            {"name": "CLONE-02", "mac": C2, "awake": True, "joined": True, "fresh_drawers": 2,
             "local_copy": True,
             "state": "waiting", "drawer_count": 3,
             "drawer_list": [_drawer(1, "S3", "unknown"), _drawer(2, "S4")]}]}


def gui_state_lines(tmp_path: Path) -> list[str]:
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    (run / "station.json").write_text(json.dumps(STATION), newline="\n")
    (run / "room.json").write_text(json.dumps(ROOM), newline="\n")
    (gui / "mode").write_text("direct\n", newline="\n")
    out = sh(f'export RUN_DIR={posix(run)!r} GUI_DIR={posix(gui)!r} '
             f'SERVER=http://127.0.0.1:9 MAC=aa:bb:cc:dd:ee:ff IMAGESRV_TEST=1; '
             + PRELUDE
             + f'http_get() {{ cat {posix(run / "station.json")!r}; }}; '
             + f'gui_get() {{ cp {posix(run / "room.json")!r} "$2"; }}; '
             + 'gui_state')
    assert out.returncode == 0, out.stderr
    return (gui / "state").read_text(encoding="utf-8").splitlines()


@requires_native(("bash", BASH), "jq", why="gui_state בונה את המצב ב-jq")
def test_gui_state_carries_the_warnings_the_missing_and_the_link(tmp_path):
    lines = gui_state_lines(tmp_path)
    assert f"room_warn={WARN}" in lines, lines
    missing = [l for l in lines if l.startswith("room_missing=")]
    assert missing[0] == f"room_missing={C2}|CLONE-02|drawers|1|{MISSING}|0", missing
    # ‏null שם → ריק; ‏"|" בטקסט אינו שובר את הרשומה
    assert missing[1] == "room_missing=3c:52:82:a1:00:23||off|0|CLONE-03 כבוי   או לא מחובר|0", missing
    machine = next(l for l in lines if l.startswith(f"machine=CLONE-02|{C2}|"))
    assert machine.endswith("|1"), machine
    states = {l.split("|")[1]: l.split("|")[11] for l in lines if l.startswith("room_drawer=0|")}
    assert states == {"1": "ok", "2": "slow"}, lines


# --- הגשר ---------------------------------------------------------------------


def _bridge(tmp_path: Path, record: str) -> tuple[int, str | None, str | None]:
    run = tmp_path / "run"
    (run / "gui").mkdir(parents=True)
    rec = tmp_path / "record"
    rec.write_bytes(record.encode("utf-8"))
    lib = posix(flat_lib())
    script = f"""
        RUN_DIR={posix(run)}; GUI_DIR={posix(run)}/gui
        . {lib}/common.sh
        LOG_FILE={posix(run)}/agent.log
        . {lib}/roomflow.sh
        . {lib}/guibridge.sh
        gui_role() {{ GUI_ROLE=deploy; }}
        console_post() {{ printf '%s' "$1" > {posix(run)}/path; cp "$2" {posix(run)}/posted.json; printf 200; }}
        console_say() {{ :; }}
        sleep() {{ :; }}
        gui_records < {posix(rec)}
        rc=$?
        [ -s "$GUI_DIR/error" ] && rc=1
        exit $rc
    """
    sf = tmp_path / "t.sh"
    sf.write_text(script, encoding="utf-8")
    r = subprocess.run([BASH, str(sf)], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=60)
    path = run / "path"
    posted = run / "posted.json"
    return (r.returncode, path.read_text() if path.exists() else None,
            posted.read_text(encoding="utf-8") if posted.exists() else None)


@requires_native(("bash", BASH), "jq", "awk")
def test_the_bridge_posts_power_off_for_the_missing_machine(tmp_path):
    rc, path, body = _bridge(tmp_path, f"room-poweroff\ncloner={C2}\nconfirm=CLONE-02\n\n")
    assert rc == 0 and path == f"room/missing/{C2}/poweroff", (rc, path)
    assert json.loads(body) == {"confirm_name": "CLONE-02", "confirm_local_copy": False}


@requires_native(("bash", BASH), "jq", "awk")
def test_the_bridge_marks_only_an_explicit_local_copy_confirmation(tmp_path):
    record = f"room-poweroff\ncloner={C2}\nconfirm=CLONE-02\nlocal_copy=1\n\n"
    rc, path, body = _bridge(tmp_path, record)
    assert rc == 0 and path == f"room/missing/{C2}/poweroff", (rc, path)
    assert json.loads(body) == {"confirm_name": "CLONE-02", "confirm_local_copy": True}

    rc, path, _ = _bridge(tmp_path / "bad", record.replace("local_copy=1", "local_copy=true"))
    assert rc != 0 and path is None


@requires_native(("bash", BASH), "jq", "awk")
def test_the_bridge_can_confirm_room_poweroff_over_local_copies(tmp_path):
    record = "room-poweroff-all\nconfirm=מחשבי שיכפול\nlocal_copy=1\n\n"
    rc, path, body = _bridge(tmp_path, record)
    assert rc == 0 and path == "room/poweroff", (rc, path)
    assert json.loads(body) == {"confirm_name": "מחשבי שיכפול", "confirm_local_copy": True}


@requires_native(("bash", BASH), "jq", "awk")
def test_a_malformed_machine_posts_nothing(tmp_path):
    for bad in ("", "3C:52:82:A1:00:22", "x/../../users", "3c:52;reboot"):
        rc, path, _ = _bridge(tmp_path / str(abs(hash(bad))), f"room-poweroff\ncloner={bad}\nconfirm=x\n\n")
        assert path is None and rc != 0, bad


@requires_native(("bash", BASH), "jq", "awk")
def test_wake_and_start_still_reach_their_endpoints(tmp_path):
    """‏room-wake/room-start אוחדו לזרוע אחת ב-guibridge (קיר 300) — אותה פעולה."""
    # 17/10: room-wake carries the picked cloners (cloner=); without them it posts
    # nothing (test_failed_round_not_stuck.py)
    for token, extra in (("room-start", ""), ("room-wake", "cloner=3c:52:82:a1:00:21\n")):
        rc, path, _ = _bridge(tmp_path / token, f"{token}\n{extra}\n")
        assert path == f"room/{token[5:]}", (token, path)


# --- הגואי ----------------------------------------------------------------------

_GUI = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)

BASE = (f"machine=CLONE-01|{C1}|1|1|2|waiting|-1|0|\n"
        "machine_drawers=0|2\n"
        "room_drawer=0|1|S1|M|256060514304|passed|1|1||||ok\n"
        "room_drawer=0|2|S2|M|256060514304|passed|1|1||||{link}\n"
        f"machine=CLONE-02|{C2}|1|1|2|waiting|-1|0|\n"
        "machine_drawers=1|3\n"
        "room_drawer=1|1|S3|M|256060514304|passed|1|1||||unknown\n"
        "round=img|1|1|0|5|4|5|-1|-1|-1\n"
        "message=x|y\n")
EXTRA = (f"room_warn={WARN}\n"
         f"room_missing={C2}|CLONE-02|drawers|1|{MISSING}|0\n"
         "room_missing=3c:52:82:a1:00:23|CLONE-03|off|0|CLONE-03 כבוי או לא מחובר — לא הצטרף לגל|0\n")

LOCAL_COPY = ("room_label=מחשבי שיכפול\n"
              f"room_missing={C2}|CLONE-02|drawers|1|{MISSING}|1\n")


def _render(binary: Path, state_text: str, prefix: Path, size: str = "1280x800") -> None:
    state = prefix.with_suffix(".txt")
    state.write_text(state_text, encoding="utf-8", newline="\n")
    env2 = dict(os.environ)
    if (GUI / "fonts.conf").exists():
        env2["FONTCONFIG_FILE"] = str(GUI / "fonts.conf")
    run = subprocess.run([str(binary), "--png", posix(prefix), "--size", size, "--mac", C1,
                          "--ip", "10.10.10.31", "--state", posix(state)],
                         capture_output=True, text=True, timeout=180, env=env2,
                         stdin=subprocess.DEVNULL)
    assert run.returncode == 0, _routing_error(run.stderr)


def _count(path: Path, token: str, theme: str = "light") -> int:
    _w, _h, ch, px = _png_rgb(path)
    return _count_color(px, ch, _token(token, theme))


@_GUI
def test_the_live_room_draws_the_orange_box_and_a_slow_slot(tmp_path):
    """אותו מצב פעמיים — עם הרשומות ובלעדיהן, וקישור `ok` מול `slow`: הקופסה
    הכתומה (‏warning-soft) והפס הכתום של המגירה האיטית מוסיפים פיקסלים."""
    binary = _build_gui(tmp_path)
    _render(binary, BASE.format(link="ok"), tmp_path / "plain")
    _render(binary, BASE.format(link="slow") + EXTRA, tmp_path / "pre")
    for theme in ("light", "dark"):
        soft_before = _count(tmp_path / f"plain-room-live-{theme}.png", "--clr-warning-soft", theme)
        soft_after = _count(tmp_path / f"pre-room-live-{theme}.png", "--clr-warning-soft", theme)
        assert soft_after - soft_before > 5000, (theme, soft_before, soft_after)
        w_before = _count(tmp_path / f"plain-room-live-{theme}.png", "--clr-warning", theme)
        w_after = _count(tmp_path / f"pre-room-live-{theme}.png", "--clr-warning", theme)
        assert w_after - w_before > 300, (theme, w_before, w_after)


@_GUI
def test_without_warnings_or_missing_there_is_no_box(tmp_path):
    """הבקרה ההפוכה: גל **רץ** עם אותן רשומות בדיוק — אין קופסה (היא רק לגל
    פתוח, לפני "התחל"). אותו מצב עם ובלי `room_warn=`/`room_missing=` — אותה
    תמונה; בגל פתוח אותו זוג נבדל באלפי פיקסלים (הטסט הקודם)."""
    binary = _build_gui(tmp_path)
    running = BASE.format(link="ok").replace("round=img|1|1|", "round=img|1|0|")
    _render(binary, running, tmp_path / "a")
    _render(binary, running + EXTRA, tmp_path / "b")
    for token in ("--clr-warning-soft", "--clr-warning"):
        a = _count(tmp_path / "a-room-live-light.png", token)
        b = _count(tmp_path / "b-room-live-light.png", token)
        assert abs(a - b) < 50, (token, a, b)


@_GUI
def test_the_two_dialogs_route_and_carry_their_colours(tmp_path):
    """‏"התחל בכל זאת" כתום (‏BTN_WARN — שואל, לא חוסם), "כבה אותו" אדום (‏BTN_DANGER)."""
    binary = _build_gui(tmp_path)
    _render(binary, BASE.format(link="slow") + EXTRA, tmp_path / "d")
    for theme in ("light", "dark"):
        start = tmp_path / f"d-room-start-warn-{theme}.png"
        off = tmp_path / f"d-room-poweroff-{theme}.png"
        assert _count(off, "--clr-danger-fill", theme) - _count(start, "--clr-danger-fill", theme) > 400, theme
        assert _count(start, "--clr-warning", theme) > _count(off, "--clr-warning", theme), theme


def test_local_copy_poweroff_uses_the_owner_words_and_sends_confirmation():
    root = Path(__file__).resolve().parent.parent
    screens = (root / "native-gui" / "src" / "screens.c").read_text(encoding="utf-8")
    main = (root / "native-gui" / "src" / "main.c").read_text(encoding="utf-8")
    state = (root / "native-gui" / "src" / "state.c").read_text(encoding="utf-8")
    warning = "המחשב באמצע שיכפול מקומי — כיבוי עכשיו ישאיר את דיסק היעד שבור ולא שמיש. לכבות בכל זאת?"
    plural = "%d מחשבים באמצע שיכפול מקומי — הדיסקים שלהם יישארו שבורים. לכבות בכל זאת?"
    assert warning in screens and 'snprintf(ok,sizeof ok,"כבה בכל זאת")' in screens
    assert plural in screens and "CONFIRM_ROOM_POWEROFF_ALL" in screens
    assert "BTN_DANGER,HIT_CONFIRM_OK" in screens
    assert 'emit_kv("local_copy", "1")' in main
    assert 'm->local_copy = !strcmp(f[9], "1")' in state
    assert 's->room_missing[i].local_copy = nf == 6' in state


@_GUI
def test_local_copy_power_dialogs_render_the_red_owner_warning(tmp_path):
    """The normal and dangerous states differ visibly, and both dangerous
    dialogs keep the destructive red action used by the native confirm pattern."""
    binary = _build_gui(tmp_path)
    state = BASE.format(link="ok") + LOCAL_COPY
    _render(binary, state, tmp_path / "copy")
    for theme in ("light", "dark"):
        normal = tmp_path / f"copy-room-poweroff-{theme}.png"
        single = tmp_path / f"copy-room-poweroff-local-copy-{theme}.png"
        plural = tmp_path / f"copy-room-poweroff-all-{theme}.png"
        assert normal.read_bytes() != single.read_bytes(), theme
        assert single.read_bytes() != plural.read_bytes(), theme
        for warning in (single, plural):
            assert _count(warning, "--clr-danger-fill", theme) > 400, (theme, warning)


STANDBY = ("hello_age=4\nhello_rc=200\nmachine_name=CLONE-01\nserver_name=imagesrv-ta\n"
           "cdisk=1|sda|Samsung SSD 870 EVO|500107862016|passed||6.0|6.0\n"
           "cdisk=2|sdb|Samsung SSD 870 EVO|500107862016|passed||{link}|6.0\n"
           "message=x|y\n")


@_GUI
def test_the_standby_bay_turns_orange_on_a_slow_link(tmp_path):
    binary = _build_gui(tmp_path)
    _render(binary, STANDBY.format(link="6.0"), tmp_path / "fast")
    _render(binary, STANDBY.format(link="1.5"), tmp_path / "slow")
    _render(binary, STANDBY.format(link="null"), tmp_path / "unknown")
    for theme in ("light", "dark"):
        fast = _count(tmp_path / f"fast-standby-{theme}.png", "--clr-warning", theme)
        slow = _count(tmp_path / f"slow-standby-{theme}.png", "--clr-warning", theme)
        unknown = _count(tmp_path / f"unknown-standby-{theme}.png", "--clr-warning", theme)
        assert slow - fast > 300, (theme, fast, slow)
        assert abs(unknown - fast) < 150, ("unknown is not slow", theme, fast, unknown)
