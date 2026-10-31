"""#1203: the server screen in Hebrew, drawn by the native GUI.

Two halves, one contract (docs/interfaces.md s31, "the graphical screen"):

* ``server/dcui/view.py`` + ``gui.py`` -- Python stays root and the only state
  machine; it turns the Runtime into frames and the GUI's tokens into keys.
  Everything here runs on any machine.
* ``native-gui/src/dcui.c`` + ``screens_dcui.c`` -- paints a frame and reads
  the keyboard (forced to English).  Built and rendered on the Linux lab only.

The approved mockup (Nadav 12/10, C:\\ImageCtl-Lab\\design-approved\\dcui) is
``server/dcui/samples.py``: the same data through the real path.
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import sys
import textwrap
import time
import zlib
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from native import requires_native
from server.console import users
from server.db import connect
from server.dcui import actions, app, gui, samples, view
from server.dcui.data import CommandResult, DeployRound, NicInfo, ServiceState

REPO = Path(__file__).resolve().parent.parent
GUI_DIR = REPO / "native-gui"
KEYS = gui.KEYS
TODAY = samples.TODAY


def _records(frame: str) -> list[list[str]]:
    assert frame.endswith("\n\n"), "a frame ends with an empty line"
    return [line.split("\t") for line in frame.rstrip("\n").split("\n")]


def _first(frame: str, kind: str) -> list[str]:
    return next(rec for rec in _records(frame) if rec[0] == kind)


def _all(frame: str, kind: str) -> list[list[str]]:
    return [rec for rec in _records(frame) if rec[0] == kind]


def _row(frame: str, label: str) -> list[str]:
    return next(rec for rec in _records(frame) if rec[0] in ("row", "fprow") and rec[1] == label)


def _render(runtime) -> str:
    return view.render(runtime, samples.NOW, samples.NOW, samples.CLOCK, TODAY, 47)


def _runtime(snapshot=samples.PRIMARY, extras=samples.PRIMARY_EXTRAS, **state):
    return samples._runtime(snapshot, extras, **state)


# ---- the data mapping: what the screen says about the server ----------------------

def test_a_healthy_primary_is_green_with_every_tile():
    frame = _render(_runtime())
    assert _first(frame, "hero")[1:3] == ["ok", "השרת תקין"]
    assert _first(frame, "sb")[1:3] == ["ok", "תקין"]
    assert [rec[1] for rec in _all(frame, "tile")] == [
        "שירותים", "רשת", "שרתים והפצה", "גישה לקונסולה"]
    assert _first(frame, "tile")[2:4] == ["ok", "3 מתוך 3"]
    assert _row(frame, "dnsmasq")[6:8] == ["ok", "פועל"]
    assert _row(frame, "טביעת אצבע")[3].startswith("6F:8E:63")
    assert not _all(frame, "prob")


def test_a_failed_service_turns_the_screen_red_with_what_impact_and_todo():
    snapshot = replace(samples.PRIMARY, services=dict(samples.UP) | {
        "dnsmasq": ServiceState("failed", "enabled")})
    frame = _render(_runtime(snapshot))
    hero = _first(frame, "hero")
    assert hero[1] == "bad" and "בעיה אחת" in hero[2]
    prob = _first(frame, "prob")
    assert prob[1] == "bad" and "dnsmasq" in prob[3] and "נכשל" in prob[3]
    assert prob[4] and "מה לעשות" in prob[5] and prob[6:8] == ["F2", "הגדרות"]
    row = _row(frame, "dnsmasq")
    assert row[6:9] == ["bad", "נכשל", "bad"]
    assert _first(frame, "tile")[2:4] == ["bad", "1 נכשל"]
    assert _first(frame, "sb")[1:3] == ["bad", "דורש טיפול · 1"]


def test_an_unchecked_service_is_not_reported_as_running():
    """Principle 5: "could not check" is its own state, never "פועל"."""
    snapshot = replace(samples.PRIMARY, services=dict(samples.UP) | {
        "nftables": ServiceState("unknown", "unknown")})
    frame = _render(_runtime(snapshot))
    assert _row(frame, "nftables")[6:8] == ["warn", "לא נבדק"]
    assert _first(frame, "tile")[2] == "warn"


def test_no_deploy_network_is_grey_on_an_unpaired_secondary_and_red_on_a_primary():
    """Nadav 12/10: "לא נדרש עדיין" on a secondary nobody paired yet;
    "דורש טיפול" on a primary, which cannot clone without it."""
    waiting = _render(_runtime(samples.SECONDARY, view.Extras()))
    assert _row(waiting, "dnsmasq")[6:8] == ["off", "לא נדרש עדיין"]
    assert _first(waiting, "hero")[1:3] == ["wait", "ממתין לחיבור מהשרת הראשי"]
    assert _first(waiting, "sb")[1:3] == ["info", "ממתין לחיבור מהראשי"]
    assert not _all(waiting, "prob")

    primary = replace(samples.PRIMARY, deploy=None, services=samples.SECONDARY.services)
    frame = _render(_runtime(primary))
    assert _row(frame, "dnsmasq")[6:8] == ["bad", "דורש טיפול"]
    assert _row(frame, "רשת הפצה")[6:8] == ["bad", "דורש טיפול"]
    assert any(rec[3] == "רשת ההפצה לא הוגדרה" for rec in _all(frame, "prob"))
    assert _first(frame, "hero")[1] == "bad"


def test_a_paired_secondary_without_contact_is_never_green():
    deploy = NicInfo("ens34", "10.45.0.1/16", "static", "up", "1000")
    snapshot = replace(samples.SECONDARY, services=dict(samples.UP), deploy=deploy)
    extras = view.Extras(has_parent=True, primary_url="https://10.20.10.5:8443")
    frame = _render(_runtime(snapshot, extras))
    hero = _first(frame, "hero")
    assert hero[1] == "warn" and "מגע לא ידוע" in hero[2] and "✓" not in hero[2]
    assert _row(frame, "שרת ראשי")[6:8] == ["warn", "מצומד · מגע לא ידוע"]
    assert _first(frame, "sb")[1:3] == ["warn", "מצומד · מגע לא ידוע"]


def test_a_fresh_parent_contact_is_green_and_a_stale_one_is_orange():
    deploy = NicInfo("ens34", "10.45.0.1/16", "static", "up", "1000")
    snapshot = replace(samples.SECONDARY, services=dict(samples.UP), deploy=deploy)
    fresh_at = (TODAY.astimezone() - timedelta(minutes=4)).isoformat()
    extras = view.Extras(has_parent=True, primary_url="https://10.20.10.5:8443",
                         parent_last_contact=fresh_at)
    frame = _render(_runtime(snapshot, extras))
    assert _first(frame, "hero")[1] == "ok"
    assert _row(frame, "שרת ראשי")[6:8] == ["ok", "מחובר · מגע לפני 4 דק'"]
    assert _first(frame, "sb")[1:3] == ["ok", "מחובר · מגע לפני 4 דק'"]

    stale_at = (TODAY.astimezone() - timedelta(minutes=11)).isoformat()
    stale = replace(extras, parent_last_contact=stale_at)
    frame = _render(_runtime(snapshot, stale))
    assert _first(frame, "hero")[1] == "warn"
    assert _row(frame, "שרת ראשי")[6] == "warn"
    assert _row(frame, "שרת ראשי")[7] == f"מצומד · אין מגע מאז {(TODAY - timedelta(minutes=11)):%H:%M}"
    assert _first(frame, "sb")[1] == "warn"


def test_a_paired_secondary_still_needs_its_deploy_network():
    deploy = NicInfo("ens34", "10.45.0.1/16", "static", "up", "1000")
    snapshot = replace(samples.SECONDARY, services=dict(samples.UP), deploy=deploy)
    extras = view.Extras(has_parent=True, primary_url="https://10.20.10.5:8443")
    # paired, it distributes to its own network -- so it needs one now
    frame = _render(_runtime(replace(snapshot, deploy=None), extras))
    assert any(rec[3] == "רשת ההפצה לא הוגדרה" for rec in _all(frame, "prob"))


def test_certificate_validity_from_its_not_after():
    near = replace(samples.PRIMARY, certificate_not_after="Oct 18 12:00:00 2026 GMT")
    frame = _render(_runtime(near))
    assert _row(frame, "טביעת אצבע")[6:9] == ["warn", "פגה בקרוב", "warn"]
    assert any("בעוד 6 ימים" in rec[3] for rec in _all(frame, "prob"))
    gone = replace(samples.PRIMARY, certificate_not_after="Oct  1 12:00:00 2026 GMT")
    assert _row(_render(_runtime(gone)), "טביעת אצבע")[6:8] == ["bad", "פגה"]
    unreadable = replace(samples.PRIMARY, fingerprint="unavailable (certificate read failed)")
    assert _row(_render(_runtime(unreadable)), "טביעת אצבע")[6:8] == ["bad", "לא נקראה"]


def test_management_network_without_an_address_is_a_problem():
    nic = NicInfo("ens33", "", "dhcp", "up", "1000")
    snapshot = replace(samples.PRIMARY, management=nic, no_address=True)
    frame = _render(_runtime(snapshot))
    assert _row(frame, "רשת ניהול")[6:8] == ["bad", "אין כתובת"]
    assert any("אין כתובת" in rec[3] for rec in _all(frame, "prob"))
    assert _row(frame, "כתובת")[3] == "אין כתובת"


def test_speed_and_latin_values_are_isolated_inside_hebrew():
    """"10 Gb/s" in an RTL sentence comes out "Gb/s 10" without an isolate."""
    assert view.speed_text("10000") == "10 Gb/s"
    assert view.speed_text("2500") == "2.5 Gb/s"
    assert view.speed_text("100") == "100 Mb/s"
    assert view.speed_text("-1") == view.speed_text("unknown") == ""
    sub = view.nic_row("רשת ניהול", samples.PRIMARY.management).sub
    assert f"{view.FSI}10 Gb/s{view.PDI}" in sub


def test_a_running_round_shows_on_the_main_screen_and_in_f12():
    running = replace(samples.PRIMARY, deploy_round=DeployRound(
        "s", "office365", "running", 12, 14, "2026-10-12T20:02:00+03:00"))
    frame = _render(_runtime(running))
    assert _row(frame, "סבב הפצה")[6:8] == ["info", "רץ"]
    assert _first(frame, "sb")[1:3] == ["warn", "סבב הפצה רץ"]
    power = _render(_runtime(running, screen="power"))
    alert = _first(power, "d.alert")[1]
    assert "office365" in alert and "12 מתוך 14" in alert


def test_record_fields_never_carry_a_separator_from_the_data():
    snapshot = replace(samples.PRIMARY, hostname="evil\thost\nname")
    frame = _render(_runtime(snapshot))
    assert _first(frame, "hdr")[2] == "evil host name"
    assert all(len(rec) >= 2 for rec in _records(frame))


def test_backend_messages_are_translated_and_unknown_ones_kept():
    assert view.he("Wrong password.") == "סיסמה שגויה."
    assert view.he("Authentication locked for 12 more seconds.") == "הכניסה נעולה עוד 12 שניות."
    assert view.he("something new") == "something new"


# ---- the password dialog --------------------------------------------------------

def _db(tmp_path):
    conn = connect(tmp_path / "imagesrv.db")
    users.create(conn, "admin", "before", "admin", by="test",
                 is_builtin=True, check_policy=False)
    return conn


def _live(tmp_path, conn, **snapshot):
    runtime = app.Runtime(tmp_path, conn, replace(samples.PRIMARY, **snapshot),
                          last_key_at=time.monotonic())
    runtime.extras = samples.PRIMARY_EXTRAS
    return runtime


def _press(runtime, *keys):
    for key in keys:
        app._handle_key(runtime, key, KEYS)


def test_the_frame_carries_the_password_length_never_the_password(tmp_path):
    runtime = _live(tmp_path, _db(tmp_path))
    _press(runtime, KEYS.KEY_F2, *"Secr3t!x")
    frame = view.render(runtime, time.monotonic(), time.time(), "20:17", TODAY)
    assert "Secr3t" not in frame
    field = [rec for rec in _all(frame, "d.field") if rec[1] == "סיסמה"][0]
    assert field[2:6] == ["8", "password", "1", "1"]
    assert _first(frame, "lang")[1] == "שדה באנגלית בלבד"
    assert "Caps Lock: כבוי" in _first(frame, "d.hint")[3]
    runtime.caps = True
    frame = view.render(runtime, time.monotonic(), time.time(), "20:17", TODAY)
    assert "Caps Lock: מופעל" in _first(frame, "d.hint")[3]


def test_wrong_passwords_lock_the_dialog_and_the_frame_says_so(tmp_path):
    runtime = _live(tmp_path, _db(tmp_path))
    for attempt in ("x1", "x2"):
        _press(runtime, KEYS.KEY_F2, *attempt, "\n")
    frame = view.render(runtime, time.monotonic(), time.time(), "20:17", TODAY)
    assert "ניסיון 2 מתוך 3" in _first(frame, "d.error")[1]
    _press(runtime, *"x3", "\n")
    assert runtime.screen == "main"
    frame = view.render(runtime, time.monotonic(), time.time(), "20:17", TODAY)
    assert "30" in _first(frame, "notice")[2] or "נעולה" in _first(frame, "notice")[2]


# ---- F12: the focus starts on Cancel -----------------------------------------------

def test_f12_focus_starts_on_cancel_and_a_stray_enter_runs_nothing(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    calls = []
    real = actions.power_action
    monkeypatch.setattr(actions, "power_action", lambda conn, choice: real(
        conn, choice, lambda argv: calls.append(argv) or CommandResult(0, "", "")))
    runtime = _live(tmp_path, conn)
    _press(runtime, KEYS.KEY_F12, *"before", "\n")
    assert runtime.screen == "power" and runtime.power_focus == "cancel"
    frame = view.render(runtime, time.monotonic(), time.time(), "20:17", TODAY)
    buttons = _all(frame, "d.btn")
    assert [(b[1], b[4]) for b in buttons] == [("ביטול", "1"), ("אתחל עכשיו", "0")]
    _press(runtime, "\n")
    assert calls == [] and runtime.screen == "main"
    _press(runtime, KEYS.KEY_F12, *"before", "\n", KEYS.KEY_LEFT, "\t")
    frame = view.render(runtime, time.monotonic(), time.time(), "20:17", TODAY)
    assert [(b[1], b[4]) for b in _all(frame, "d.btn")] == [("ביטול", "0"), ("כבה עכשיו", "1")]
    _press(runtime, KEYS.KEY_BTAB, "\t", "\n")
    assert calls == [["systemctl", "poweroff"]]


# ---- the pipe between Python and the GUI --------------------------------------------

def test_gui_tokens_become_the_same_keys_as_curses():
    assert gui.parse_token("KEY F2\n") == ("key", KEYS.KEY_F2)
    assert gui.parse_token("KEY ENTER") == ("key", "\n")
    assert gui.parse_token("KEY BACKTAB") == ("key", KEYS.KEY_BTAB)
    assert gui.parse_token("CHAR 61") == ("key", "a")
    assert gui.parse_token("CHAR 20") == ("key", " ")
    assert gui.parse_token("CAPS 1") == ("caps", True)
    assert gui.parse_token("READY 1280x800") == ("ready", "1280x800")
    for junk in ("KEY F7", "CHAR zz", "CHAR 1f", "", "HELLO", "CAPS 2"):
        assert gui.parse_token(junk) is None, junk


def test_no_binary_or_no_screen_falls_back_to_text_with_the_reason(tmp_path):
    assert "not found" in gui.run(tmp_path, candidates=(tmp_path / "missing",))
    (tmp_path / "dri").mkdir()
    assert "no framebuffer" in gui.display_problem(tmp_path)
    (tmp_path / "dri" / "card0").write_text("")
    assert gui.display_problem(tmp_path) == ""


FAKE_GUI = textwrap.dedent('''
    import io, sys
    stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8")
    out = open(sys.argv[1], "w", encoding="utf-8")
    print("READY 1280x800", flush=True)
    def frame():
        lines = []
        for line in iter(stdin.readline, ""):
            if line == "\\n":
                return "".join(lines)
            lines.append(line)
        return None
    first = frame()
    out.write(first.split("\\n")[0] + "\\n")
    print("KEY F2", flush=True)
    second = frame()
    out.write(second.split("\\n")[0] + "\\n")
    out.write("auth" if "סיסמת מנהל" in second else "no dialog")
    out.close()
''')


def test_a_session_with_a_real_child_drives_the_state_machine(tmp_path):
    """Session + serve over real pipes: the first frame goes out, the child's
    F2 opens the password dialog, the next frame shows it, and the child's
    exit ends the loop with a reason (the caller's fallback)."""
    script = tmp_path / "fake_gui.py"
    script.write_text(FAKE_GUI, encoding="utf-8")
    seen = tmp_path / "seen.txt"
    session = gui.Session([sys.executable, str(script), str(seen)])
    assert session.wait_ready(10) == ""
    runtime = _live(tmp_path, _db(tmp_path))
    runtime.last_refresh = time.monotonic() + 3600     # no host refresh in a test
    why = gui.serve(runtime, session, max_loops=200)
    assert runtime.screen == "auth"
    assert "graphic screen stopped" in why
    text = seen.read_text(encoding="utf-8")
    assert text.startswith("frame\tmain\nframe\tmain\n") and text.endswith("auth")


# ---- the native half (lab) --------------------------------------------------------

def _pkgconfig(*pkgs: str) -> bool:
    if not shutil.which("pkg-config"):
        return False
    return subprocess.run(["pkg-config", "--exists", *pkgs],
                          stdin=subprocess.DEVNULL).returncode == 0


NATIVE = requires_native(
    ("bash", shutil.which("bash")),
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("make", shutil.which("make")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    posix=True, why="the native GUI is built and rendered on Linux",
)
CFLAGS = "-O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE"


def _cc(tmp_path: Path, out: str, sources: str) -> Path:
    binary = tmp_path / out
    command = (
        f'set -e; cd "$1"; cc {CFLAGS} $(pkg-config --cflags pangocairo cairo libdrm) '
        f'-o "$2" {sources} $(pkg-config --libs pangocairo cairo libdrm) -lm'
    )
    proc = subprocess.run(["bash", "-c", command, "_", str(GUI_DIR), str(binary)],
                          capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    if proc.returncode != 0 or "warning" in proc.stderr:
        # rc6: a module fixture that fails shows up as N ERRORs; the compiler's own
        # lines say why, so they are the message -- whole, and in a file beside it.
        log = tmp_path / f"{out}.build.log"
        log.write_text(f"rc={proc.returncode}\n{proc.stderr}{proc.stdout}", encoding="utf-8")
        why = "failed" if proc.returncode != 0 else "warned (the GUI builds warning-free)"
        pytest.fail(f"cc {why}, rc={proc.returncode}; full log: {log}\n{proc.stderr}{proc.stdout}",
                    pytrace=False)
    return binary


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("dcui-gui")
    return _cc(tmp, "gui", "$(make -s -f Makefile print-gui-src)")


def _png(path: Path, rows: int = 0):
    """(width, height, px(x, y)) -- only the first <rows> rows are unfiltered
    (pure Python is slow; every check here looks near the top), 0 = none."""
    raw = path.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    pos, width, height, packed = 8, 0, 0, b""
    while pos < len(raw):
        length = struct.unpack(">I", raw[pos:pos + 4])[0]
        kind, data = raw[pos + 4:pos + 8], raw[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, color = struct.unpack(">IIBB", data[:10])
            assert depth == 8 and color in (2, 6)
            channels = 3 if color == 2 else 4
        elif kind == b"IDAT":
            packed += data
    data, stride = zlib.decompress(packed), width * channels
    pixels, prev, at = [], bytearray(stride), 0
    for _ in range(min(rows, height)):
        kind, line = data[at], bytearray(data[at + 1:at + 1 + stride])
        at += 1 + stride
        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            up, ul = prev[i], prev[i - channels] if i >= channels else 0
            if kind == 1: line[i] = (line[i] + left) & 255
            elif kind == 2: line[i] = (line[i] + up) & 255
            elif kind == 3: line[i] = (line[i] + (left + up) // 2) & 255
            elif kind == 4:
                p = left + up - ul
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - ul)
                line[i] = (line[i] + (left if pa <= pb and pa <= pc else up if pb <= pc else ul)) & 255
        pixels.append(bytes(line))
        prev = line
    return width, height, lambda x, y: tuple(pixels[y][x * channels:x * channels + 3])


def _hex(value: int) -> tuple[int, int, int]:
    return value >> 16 & 255, value >> 8 & 255, value & 255


LIGHT = {"porcelain": 0xEEF1F5, "ok": 0x318700, "danger": 0xE12200, "info": 0x0079B8,
         "indigo": 0x0079B8, "header": 0x25333D}
DARK = {"porcelain": 0x151A1F, "ok": 0x5FBD7A, "danger": 0xDC6B6B, "info": 0x61B5B8,
        "header": 0x26343F}


@pytest.fixture(scope="module")
def renders(built, tmp_path_factory):
    out = tmp_path_factory.mktemp("dcui-png")
    paths = {}
    for frame in samples.write(out / "frames"):
        name = frame.stem
        for size in ("1280x800", "1024x768"):
            prefix = out / f"{name}-{size}"
            proc = subprocess.run([str(built), "--screen", "dcui", "--png", str(prefix),
                                   "--size", size, "--state", str(frame)],
                                  capture_output=True, text=True, timeout=120,
                                  stdin=subprocess.DEVNULL)
            if proc.returncode != 0:
                pytest.fail(f"render {name} {size} failed, rc={proc.returncode}:\n"
                            f"{proc.stderr}{proc.stdout}", pytrace=False)
            for theme in ("light", "dark"):
                paths[(name, size, theme)] = Path(f"{prefix}-{theme}.png")
    return paths


@NATIVE
def test_every_frame_renders_at_both_sizes_in_both_themes(renders):
    assert len(renders) == len(samples.runtimes()) * 4
    for (name, size, theme), path in renders.items():
        width, height, px = _png(path, rows=5)
        assert f"{width}x{height}" == size, path
        tokens = LIGHT if theme == "light" else DARK
        assert px(width // 2, 4) == _hex(tokens["header"]), (path, "header")


@NATIVE
def test_the_hero_glyph_colour_follows_the_state(renders):
    """The disc right of the title: green ok, red needs-attention, blue waiting
    -- sampled inside the disc, above the glyph's stroke."""
    cases = {"1-primary-ok": "ok", "2-primary-problem": "danger", "3-secondary-waiting": "info",
             "3b-secondary-connected": "ok"}
    for name, token in cases.items():
        for theme, tokens in (("light", LIGHT), ("dark", DARK)):
            _w, _h, px = _png(renders[(name, "1280x800", theme)], rows=130)
            bad = name.startswith("2")
            cx, cy = (1196, 118) if bad else (1195, 121)
            assert px(cx, cy - 20) == _hex(tokens[token]), (name, theme, px(cx, cy - 20))


@NATIVE
def test_f12_dialog_draws_the_focus_ring_on_the_focused_button(renders):
    """6-power: the ring round "ביטול" (indigo), none round the red button;
    6b (Tab pressed): the red button has the red ring and "ביטול" none."""
    def ring_pixels(name, box, colour):
        _w, _h, px = _png(renders[(name, "1280x800", "light")], rows=box[3])
        x0, y0, x1, y1 = box
        return sum(px(x, y) == _hex(colour) for x in range(x0, x1) for y in range(y0, y1))
    cancel, act = (800, 545, 945, 615), (680, 545, 815, 615)
    assert ring_pixels("6-power", cancel, LIGHT["indigo"]) > 100
    assert ring_pixels("6b-power-focus-act", cancel, LIGHT["indigo"]) == 0
    assert ring_pixels("6b-power-focus-act", (640, 545, 812, 615), LIGHT["danger"]) > \
        ring_pixels("6-power", (640, 545, 812, 615), LIGHT["danger"])


DRIVER = r'''
#include <stdio.h>
#include <string.h>
#include "dcui.h"
static void tok(Event e, int caps) {
    char out[32];
    if (dcui_token(&e, caps, out, sizeof out)) printf("%s\n", out); else printf("-\n");
}
int main(void) {
    /* a Hebrew-layout key still sends its US letter: the keyboard is English here */
    tok((Event){ .type = UIEV_CHAR, .ch = 'a', .he = 0x05E9 }, 0);
    tok((Event){ .type = UIEV_CHAR, .ch = 'a', .he = 0x05E9 }, 1);
    tok((Event){ .type = UIEV_CHAR, .ch = 'A', .he = 'A' }, 1);
    tok((Event){ .type = UIEV_CHAR, .ch = '!', .he = '!' }, 1);
    tok((Event){ .type = UIEV_KEY, .key = KEYSYM_F12 }, 0);
    tok((Event){ .type = UIEV_KEY, .key = KEYSYM_BACKTAB }, 0);
    tok((Event){ .type = UIEV_KEY, .key = KEYSYM_LAYOUT }, 0);
    tok((Event){ .type = UIEV_KEY, .key = KEYSYM_F7 }, 0);
    tok((Event){ .type = UIEV_KEY, .key = KEYSYM_CAPS, .caps = 1 }, 1);
    DcuiFrame f; memset(&f, 0, sizeof f);
    char l1[] = "frame\tmain", l2[] = "hdr\tשרת ראשי\tTA\t\tv1\t20:17\t", l3[] = "key\tF2\tהגדרות\t1", l4[] = "";
    dcui_feed(&f, l1); dcui_feed(&f, l2); dcui_feed(&f, l3);
    int done = dcui_feed(&f, l4);
    printf("done=%d host=%s url=[%s] version=%s clock=%s admin=[%s] keys=%d lock=%d\n",
           done, f.host, f.url, f.version, f.clock, f.admin, f.nkeys, f.keys[0].lock);
    /* 16/10: F4 on a secondary, the pairing code box and the yellow alert */
    tok((Event){ .type = UIEV_KEY, .key = KEYSYM_F4 }, 0);
    char m1[] = "frame\tmain", m2[] = "dlg\tplain\t680\tT\tL",
         m3[] = "d.code\tK7M4-X9QD\tleft\t09:42\twarn", m4[] = "d.alert\tA\twarn",
         m5[] = "d.alert\tB", m6[] = "";
    dcui_feed(&f, m1); dcui_feed(&f, m2); dcui_feed(&f, m3); dcui_feed(&f, m4); dcui_feed(&f, m5);
    dcui_feed(&f, m6);
    printf("nel=%d code=%d %s/%s/%s/%s alert=%d %s/%s/[%s]\n", f.nel, f.el[0].type == DEL_CODE,
           f.el[0].a, f.el[0].b, f.el[0].c, f.el[0].d, f.el[1].type == DEL_ALERT,
           f.el[1].a, f.el[1].c, f.el[2].c);
    return 0;
}
'''


@NATIVE
def test_keys_are_forced_to_english_and_records_keep_empty_fields(tmp_path):
    (tmp_path / "driver.c").write_text(DRIVER, encoding="utf-8")
    # dcui.c and what it draws with -- not main.c (the driver has its own main)
    sources = (f"{tmp_path / 'driver.c'} -Isrc src/dcui.c src/screens_dcui.c src/backend.c "
               "src/input.c src/text.c src/draw.c src/theme.c src/brand.c")
    binary = _cc(tmp_path, "driver", sources)
    out = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30,
                         stdin=subprocess.DEVNULL).stdout.splitlines()
    assert out[:9] == ["CHAR 61", "CHAR 41", "CHAR 61", "CHAR 21", "KEY F12", "KEY BACKTAB",
                       "-", "-", "CAPS 1"]
    assert out[9] == "done=1 host=TA url=[] version=v1 clock=20:17 admin=[] keys=1 lock=1"
    assert out[10] == "KEY F4"
    assert out[11] == "nel=3 code=1 K7M4-X9QD/left/09:42/warn alert=1 A/warn/[]"


@NATIVE
def test_the_pairing_code_box_and_the_yellow_alert_are_drawn(built, tmp_path):
    """16/10: an element the GUI does not know is skipped silently -- so the
    frame without ``d.code`` (and the alert without ``warn``) must render
    differently, or the box/colour is not drawn at all."""
    frames = samples.frames()

    def render(name: str, text: str) -> bytes:
        path = tmp_path / f"{name}.frame"
        path.write_text(text, encoding="utf-8", newline="\n")
        proc = subprocess.run([str(built), "--screen", "dcui", "--png", str(tmp_path / name),
                               "--size", "1024x768", "--state", str(path)],
                              capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
        assert proc.returncode == 0, proc.stderr
        return (tmp_path / f"{name}-light.png").read_bytes()

    full = frames["13-pair-code"]
    without = "".join(line for line in full.splitlines(True) if not line.startswith("d.code\t"))
    assert render("code", full) != render("no-code", without)
    yellow = frames["13b-pair-code-no-address"]
    red = re.sub(r"^(d\.alert\t[^\n]*)\twarn$", r"\1", yellow, flags=re.M)
    assert red != yellow
    assert render("yellow", yellow) != render("red", red)


@NATIVE
def test_dcui_png_needs_a_frame_file(built, tmp_path):
    proc = subprocess.run([str(built), "--screen", "dcui", "--png", str(tmp_path / "x")],
                          capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    assert proc.returncode == 2 and "--state" in proc.stderr


# ---- #1363: a tile is never cut through a row --------------------------------------

def _three_problems() -> str:
    """The sample with two problems plus imagesrv-server failed: three cards
    in the hero, the case that left the tiles a sliver at 1280x768."""
    two = samples.runtimes()["2-primary-problem"]
    snapshot = replace(two.snapshot, services=dict(two.snapshot.services)
                       | {"imagesrv-server": ServiceState("failed", "enabled")})
    runtime = samples._runtime(snapshot, samples.PRIMARY_EXTRAS)
    return view.render(runtime, samples.NOW, samples.NOW, samples.CLOCK, TODAY, 47)


def _render_light(built, tmp_path: Path, name: str, text: str, size: str) -> Path:
    path = tmp_path / f"{name}-{size}.frame"
    path.write_text(text, encoding="utf-8", newline="\n")
    proc = subprocess.run([str(built), "--screen", "dcui", "--png", str(tmp_path / f"{name}-{size}"),
                           "--size", size, "--state", str(path)],
                          capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    return tmp_path / f"{name}-{size}-light.png"


def _tile_boxes(png: Path) -> list[tuple[int, int, int, int]]:
    """(left, top, right, bottom) of every tile, found in the pixels: a column
    5px inside each outer edge crosses the hero and the tiles, and the
    porcelain between them separates the boxes. The last two boxes of each
    column are the tiles (the hero, when there is one, is the first)."""
    width, height, px = _png(png, rows=10 ** 6)
    bg = _hex(LIGHT["porcelain"])
    probe = height - 40 - 30                                   # inside the bottom row of tiles
    left = next(x for x in range(width) if px(x, probe) != bg)
    right = next(x for x in range(width - 1, -1, -1) if px(x, probe) != bg)
    boxes = []
    for edge, step in ((left, 1), (right, -1)):
        col, runs, start = edge + 5 * step, [], None
        for y in range(49, height - 40):
            inside = px(col, y) != bg
            if inside and start is None:
                start = y
            elif not inside and start is not None:
                runs.append((start, y - 1)); start = None
        for top, bottom in runs[-2:]:
            mid, x = (top + bottom) // 2, col
            while px(x + step, mid) != bg:
                x += step
            boxes.append((min(edge, x), top, max(edge, x), bottom))
    return boxes


@NATIVE
@pytest.mark.parametrize("size", ["1280x768", "1024x768"])
@pytest.mark.parametrize("problems", [2, 3])
def test_no_tile_row_is_cut_by_the_tile_edge(built, tmp_path, size, problems):
    """#1363: ESXi's 1280x768 with problem cards in the hero left the tiles too
    short, and their clip cut the last row in half (nftables, without its
    state). A row is drawn whole or not at all: the 6px band just above every
    tile's bottom border is plain surface (the tile keeps 8px under its last
    row; a cut row runs into the border)."""
    text = samples.frames()["2-primary-problem"] if problems == 2 else _three_problems()
    png = _render_light(built, tmp_path, f"p{problems}", text, size)
    boxes = _tile_boxes(png)
    assert len(boxes) == 4, boxes                              # the probe saw every tile
    _w, _h, px = _png(png, rows=10 ** 6)
    surface = _hex(0xFFFFFF)
    for left, _top, right, bottom in boxes:
        cut = [(x, y) for y in range(bottom - 7, bottom - 1) for x in range(left + 10, right - 9)
               if px(x, y) != surface]
        assert not cut, (png.name, (left, bottom), cut[:5])


@NATIVE
def test_rows_that_do_not_fit_are_named_not_dropped(built, tmp_path):
    """#1363, principle 5: a row the tile has no room for is not drawn -- and
    the tile says so, naming it. Changing the hidden row's *value* leaves the
    picture as it was (it is not drawn), changing its *label* does not (it is
    named, with its state, in the tile's last line)."""
    frame = _three_problems()
    row = next(line for line in frame.splitlines() if line.startswith("row\tnftables\t"))
    renamed = frame.replace(row, row.replace("nftables", "nftablez", 1))
    revalued = frame.replace(row, row.replace("חומת האש של השרת", "ערך אחר לגמרי", 1))
    assert renamed != frame and revalued != frame
    base = _render_light(built, tmp_path, "base", frame, "1280x768").read_bytes()
    assert _render_light(built, tmp_path, "revalued", revalued, "1280x768").read_bytes() == base
    assert _render_light(built, tmp_path, "renamed", renamed, "1280x768").read_bytes() != base
