"""‏#1304 — מסך ההמתנה של מחשב השיכפול לפי מוקאפ A ("חזית המארז").

שני צדדים:

* **הסוכן** (‏`clonergui.sh`): קובץ המצב של המשכפל נושא עכשיו
  ‏`hello_age=`/`hello_rc=` (‏`gui_hello_state`, ‏`guistate.sh`). בלעדיהם
  המסך לא יכול לדעת אם השרת עונה — ועל משכפל אמיתי הוא היה תקוע לעד
  ב"טרם התקבלה ראיית hello". ‏hello.state פגום אינו מקפיא את התקדמות
  המגירות: רשומות ה-hello פשוט חסרות, והמסך אומר "לא ידוע".
* **הגואי** (‏`screens_standby.c`): שורת מצב אחת (נקודה ירוקה / X אדום /
  נקודה אפורה לפי ה-hello), כלוב המגירות (חריץ מפוספס לריק, פס כתום/אדום
  למגירה מסומנת), ו-IP/MAC/hello בשורת הסטטוס. הטסטים קוראים פיקסלים
  במקומות שהמוקאפ קובע, בצבעי הטוקנים של הקונסולה.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from native import requires_native
from test_cloner_gui import (BASH, GUI, PRELUDE, PORTS, _build_gui, _pkgconfig, _png, _png_rgb,
                             _token, env, posix, sh, target)

_GUI = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)
_SH = requires_native(("bash", BASH))


# --- הסוכן: hello בקובץ המצב של המשכפל ---------------------------------------


def _cloner_state(tmp_path: Path, hello: str | None) -> list[str]:
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    target(run, "sda", "waiting", 100, 0, "")
    if hello is not None:
        (run / "hello.state").write_text(hello + "\n", newline="\n")
    out = sh(env(run, gui) + PRELUDE + PORTS + "cloner_gui_state")
    assert out.returncode == 0, out.stderr
    return (gui / "state").read_text().splitlines()


@_SH
def test_the_cloner_state_carries_the_last_hello(tmp_path):
    lines = _cloner_state(tmp_path, f"{int(time.time()) - 5}|200")
    ages = [l for l in lines if l.startswith("hello_age=")]
    assert ages and 5 <= int(ages[0].split("=", 1)[1]) <= 60, lines
    assert "hello_rc=200" in lines, lines


@_SH
def test_a_hello_with_no_response_is_an_empty_code_not_zero(tmp_path):
    lines = _cloner_state(tmp_path, f"{int(time.time())}|")
    assert "hello_rc=" in lines, lines


@_SH
def test_a_bad_hello_file_drops_only_the_hello_records(tmp_path):
    """פגום = בלי רשומות hello (המסך: "לא ידוע"), והמגירות ממשיכות להתעדכן."""
    lines = _cloner_state(tmp_path, "yesterday|200")
    assert not [l for l in lines if l.startswith("hello_")], lines
    assert any(l.startswith("drawer=1|sda|waiting|") for l in lines), lines


# --- הגואי: המסך עצמו ----------------------------------------------------------

HEAD = ("machine_name=BUILD-01\nserver_name=imagectl-ta\nserver_addr=10.44.0.1\n"
        "server_version=v0.59.2\nagent_version=v0.59.2\nmessage=x|y\n")
TWO = ("cslot_name=1|מגירה עליונה\n"
       "cdisk=1|sda|Samsung SSD 870 EVO|500107862016|passed|\n"
       "cdisk=2|sdb|WDC WD5000AAKX|500107862016|fail|\n")


def _keep_fresh(path: Path) -> None:
    """state.c draws "הסוכן לא מעדכן מצב" when st_mtime is more than 10s behind
    time(NULL). --png measures that once, after fontconfig, so under load one
    of two renders crossed the line and the pictures differed on the toast's
    top row (y=740 at 1280x800) as well as in the status bar (#2110). An hour
    ahead stays fresh for the whole run; utime(now) does not. Same as #2099."""
    ahead = time.time() + 3600
    os.utime(path, (ahead, ahead))


def _render(tmp_path: Path, name: str, body: str, size: str = "1280x800"):
    binary = tmp_path / "gui-bin"
    if not binary.exists():
        _build_gui(tmp_path)
    # written after the build (#1322): the build itself used to age the file
    # past 10s before the first pixel. The stamp still has to lead the clock,
    # because fontconfig runs before state_poll (#2110).
    state = tmp_path / f"{name}.txt"
    state.write_text(HEAD + body, encoding="utf-8", newline="\n")
    _keep_fresh(state)
    _png(binary, tmp_path / name, state, size)
    return {t: _png_rgb(tmp_path / f"{name}-standby-{t}.png") for t in ("light", "dark")}


def _count(img, box, rgb, tol=10) -> int:
    w, _h, ch, px = img
    x0, y0, x1, y1 = box
    n = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            i = (y * w + x) * ch
            if all(abs(px[i + k] - rgb[k]) <= tol for k in range(3)):
                n += 1
    return n


# 1280x800: the status line's mark sits at x 1122 (980 wide, centred: 150..1130),
# around y 110 -- standby-a-*.png
MARK = (1104, 88, 1140, 132)


@_GUI
def test_the_status_line_says_what_the_last_hello_proves(tmp_path):
    """200 לפני 4 שניות = נקודה ירוקה; אין תשובה = X אדום, בלי ירוק;
    אף hello עדיין = לא ירוק ולא אדום (עיקרון 5: "לא ידוע" ≠ "מחובר")."""
    ok = _render(tmp_path, "ok", "hello_age=4\nhello_rc=200\n" + TWO)
    off = _render(tmp_path, "off", "hello_age=6\nhello_rc=\n" + TWO)
    unknown = _render(tmp_path, "unk", "hello_age=\nhello_rc=\n" + TWO)
    for t in ("light", "dark"):
        green, red = _token("--clr-success", t), _token("--clr-danger", t)
        assert _count(ok[t], MARK, green) >= 120, t
        assert _count(ok[t], MARK, red) == 0, t
        assert _count(off[t], MARK, red) >= 25, t           # the X's two strokes
        assert _count(off[t], MARK, green) == 0, t
        assert _count(unknown[t], MARK, green) == 0 and _count(unknown[t], MARK, red) == 0, t


@_GUI
def test_a_stale_200_is_not_connected(tmp_path):
    """200 לפני 10 דקות אינו "מחובר" — לולאת ה-hello עצרה."""
    stale = _render(tmp_path, "stale", "hello_age=600\nhello_rc=200\n" + TWO)
    assert _count(stale["light"], MARK, _token("--clr-danger")) >= 25
    assert _count(stale["light"], MARK, _token("--clr-success")) == 0


@_GUI
def test_the_drawers_stay_on_screen_when_the_server_is_down(tmp_path):
    """"השרת לא עונה" אינו מסתיר את הכלוב: מגירה 2 (נכשלה בשיכפול הקודם —
    כתום מאז #1580) עדיין מסומנת — מאז #2050 (מוקאפ 07) במסגרת כתומה של 3px
    מכל הצדדים, לא בפס בקצה אחד."""
    off = _render(tmp_path, "off", "hello_age=6\nhello_rc=\n" + TWO.replace("|fail|", "|failed_last|cable"))
    # bay 2: y 348..479, x 165..1114; the frame is x 165..167 / 1112..1114, y 348..350 / 477..479 (dimmed .72)
    w, _h, ch, px = off["light"]
    warn = _token("--clr-warning")
    i = (415 * w + 1113) * ch
    got = tuple(px[i:i + 3])
    assert got != (255, 255, 255) and abs(got[0] - warn[0]) < 70 and got[2] < 150, got
    for edge in ((1112, 360, 1115, 470), (165, 360, 168, 470)):   # right and left: a frame, not a side tab
        assert _count(off["light"], edge, got, tol=4) >= 3 * 100, edge
    for edge in ((300, 348, 1000, 351), (300, 477, 1000, 480)):   # top and bottom
        assert _count(off["light"], edge, got, tol=4) >= 3 * 650, edge


@_GUI
def test_bays_flag_by_the_872_colours_and_the_empty_one_is_hatched(tmp_path):
    ok = _render(tmp_path, "ok", "hello_age=4\nhello_rc=200\n" + TWO)   # #1580: bay 2 is SMART fail = red
    for t in ("light", "dark"):
        img = ok[t]
        red = _token("--clr-danger", t)
        # bay 2: SMART fail = red (#1580) -- #2050: a 3px frame on both sides and the soft red inside it
        for edge in ((1112, 360, 1115, 470), (165, 360, 168, 470)):
            assert _count(img, edge, red, tol=6) >= 3 * 100, (t, edge)
        assert _count(img, (1100, 360, 1112, 470), _token("--clr-danger-soft", t), tol=2) >= 12 * 100, t
        assert _count(img, (1111, 218, 1115, 330), red, tol=6) == 0, t          # bay 1: passed, no frame
        assert _count(img, (165, 218, 168, 330), red, tol=6) == 0, t
        # bay 3 (empty): the face is hatched -- two tones, each over a good share of it
        face = (240, 500, 990, 610)
        w, _h, ch, px = img
        tones: dict[tuple, int] = {}
        for y in range(face[1], face[3], 3):
            for x in range(face[0], face[2], 3):
                i = (y * w + x) * ch
                k = tuple(px[i:i + 3]); tones[k] = tones.get(k, 0) + 1
        top = sorted(tones.values(), reverse=True)[:2]
        assert len(top) == 2 and top[1] > sum(tones.values()) * 0.25, (t, top)


@_GUI
def test_a_disk_in_a_fourth_port_gets_a_fourth_bay_that_fits_at_4_3(tmp_path):
    """שלוש מגירות כברירת מחדל (#1170), רביעית כשיש בה דיסק; ב-1024x768 הכלוב
    מתכווץ ואינו נוגע בשורת הסטטוס."""
    body = ("hello_age=4\nhello_rc=200\n" + TWO.replace("|fail|", "|passed|")
            + "cdisk=4|sdd|ST1000DM010|1000204886016|passed|\n")
    four = _render(tmp_path, "four", body, "1024x768")
    hair = _token("--clr-border")
    w, h, ch, px = four["light"]
    col = 300   # through every bay's face
    rows = [y for y in range(48, h - 36) if _count(four["light"], (col, y, col + 1, y + 1), hair, tol=6)]
    runs = [y for i, y in enumerate(rows) if i == 0 or rows[i - 1] != y - 1]
    assert len(runs) == 2 * 4 + 2, runs          # four bays (top+bottom) + the cage's two edges
    porcelain = _token("--clr-bg")
    assert _count(four["light"], (0, h - 36 - 8, w, h - 36), porcelain, tol=2) == w * 8


def _bays_drawn(img) -> int:
    """Bays in the cage, counted like the fourth-bay test: hairline runs down a
    column through every bay's face, two per bay plus the cage's two edges."""
    hair = _token("--clr-border")
    _w, h, _ch, _px = img
    rows = [y for y in range(48, h - 36) if _count(img, (300, y, 301, y + 1), hair, tol=6)]
    runs = [y for i, y in enumerate(rows) if i == 0 or rows[i - 1] != y - 1]
    assert len(runs) >= 2 and len(runs) % 2 == 0, runs
    return (len(runs) - 2) // 2


@_GUI
def test_the_cage_has_exactly_the_cloners_drawer_count(tmp_path):
    """#1565: the agent writes drawer_count= (#1315) and the screen draws that
    many bays -- 2 on cloner 1, not a ghost third. Without the record the
    legacy three stay; a disk in a port past the count still gets its bay."""
    body = "hello_age=4\nhello_rc=200\n" + TWO.replace("|fail|", "|passed|")
    third = "cdisk=3|sdc|Crucial MX500|1000204886016|passed|\n"
    cases = {
        "legacy": (body, 3),
        "two": ("drawer_count=2\n" + body, 2),
        "four": ("drawer_count=4\n" + body, 4),
        "two-plus-disk": ("drawer_count=2\n" + body + third, 3),
    }
    got = {name: _bays_drawn(_render(tmp_path, name, text, "1024x768")["light"])
           for name, (text, _want) in cases.items()}
    assert got == {name: want for name, (_text, want) in cases.items()}


@_GUI
def test_the_status_bar_shows_this_machines_ip_and_mac(tmp_path):
    """IP/MAC בשורת הסטטוס (מ-`--ip`/`--mac`), במקום רשימת המקשים."""
    st = _render(tmp_path, "a", "hello_age=4\nhello_rc=200\n" + TWO)
    state = tmp_path / "a.txt"
    env2 = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"))
    _keep_fresh(state)   # the first render ages a stamp of "now" past 10s; the toast is not the IP
    run = subprocess.run([str(tmp_path / "gui-bin"), "--png", posix(tmp_path / "b"), "--size", "1280x800",
                          "--mac", "3C:52:82:A1:00:1F", "--ip", "10.99.99.99", "--state", posix(state)],
                         capture_output=True, text=True, timeout=120, env=env2, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    other = _png_rgb(tmp_path / "b-standby-light.png")
    w, _h, ch, a = st["light"]
    b = other[3]
    bar = [(y, x) for y in range(766, 800) for x in range(0, 640)
           if a[(y * w + x) * ch:(y * w + x) * ch + 3] != b[(y * w + x) * ch:(y * w + x) * ch + 3]]
    above = [(y, x) for y in range(48, 760, 2) for x in range(0, 1280, 2)
             if a[(y * w + x) * ch:(y * w + x) * ch + 3] != b[(y * w + x) * ch:(y * w + x) * ch + 3]]
    assert len(bar) > 40 and not above, (len(bar), above[:5])
