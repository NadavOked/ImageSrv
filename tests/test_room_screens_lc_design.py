"""נדב 17/10 ("גרסה ב׳, לא גרסה א׳ שפחות יפה"): מסך ההפצה במחשב הבנייה ומסך
"שיכפול במקביל" במחשב השיכפול בעיצוב מסך ההתקדמות של השיכפול המקומי.

המוקאפ המאושר: ‏`docs/design/native/room-screen-1017/mockup.html` (#1578). שורת
היעד של השיכפול המקומי (‏`tbay`) היא עכשיו רכיב משותף (‏`lcbay.c`), ושני המסכים
מציירים בה כל דיסק: כותב, ממתין להכרעה, דולג, נכשל, הושלם — במילים, לא
רק בצבע. ביום הברזל המסך הראה "ממתינה לשידור" בזמן שהמכונה חיכתה לתשובה, וסבב
שנכשל אמר רק "אין פעולה במסך הזה".

הבדיקות: רינדור `--png` של כל מצב (בהיר) וספירת הצבעים של המצב; ומקור — שלושת
המסכים מציירים שורה דרך `lc_bay_draw` ולא עותק משלהם.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from native import requires_native
from test_cloner_gui import _build_gui, _count_color, _pkgconfig, _png_rgb, _token
from test_room_precheck_native_432_65 import _render

SRC = Path(__file__).resolve().parent.parent / "native-gui" / "src"

_GUI = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)

G = 1_000_000_000
CDISKS = ("cdisk=1|sda|Samsung SSD 870 EVO|500107862016|ok||6.0|6.0||1|SATA 0\n"
          "cdisk=2|sdb|WDC WD5000AAKX|500107862016|ok||6.0|6.0||2|SATA 1\n"
          "cdisk=3|sdc|Crucial MX500|500107862016|ok||6.0|6.0||3|SATA 2\n")


def _drawer(port: int, dev: str, state: str, done: float, error: str = "") -> str:
    return (f"drawer={port}|{dev}|{state}|{int(done * 19.5 * G)}|{int(19.5 * G)}|{error}|"
            f"{68_000_000 if state == 'writing' else -1}|{120 if state == 'writing' else -1}|{port}|SATA {port - 1}\n")


CLONER = {
    "writing": _drawer(1, "sda", "writing", .63) + _drawer(3, "sdc", "writing", .41),
    # #2347: the SMART prompt is gone (#2044); a drawer's own question (#1808/#1898) keeps the orange row
    "asking": _drawer(1, "sda", "waiting", 0) + _drawer(2, "sdb", "asking", 0, "מטא-נתוני RAID ישנים על הדיסק"),
    "skipped": _drawer(1, "sda", "writing", .48) + _drawer(2, "sdb", "skipped", 0, "המפעיל דילג"),
    "failed": _drawer(1, "sda", "done", 1) + _drawer(2, "sdb", "failed", .15, "queue overflow: target lost bytes"),
    "done": _drawer(1, "sda", "done", 1) + _drawer(3, "sdc", "done", 1),
}


def _cloner_png(binary, tmp_path: Path, state: str) -> Path:
    _render(binary, "cloner=office365\n" + CDISKS + CLONER[state] + "message=x|y\n", tmp_path / state)
    return tmp_path / f"{state}-cloner-light.png"


def _count(path: Path, token: str, tol: int = 6) -> int:
    _w, _h, ch, px = _png_rgb(path)
    return _count_color(px, ch, _token(token), tol=tol)


@_GUI
def test_the_cloner_rows_say_each_state(tmp_path):
    binary = _build_gui(tmp_path)
    png = {state: _cloner_png(binary, tmp_path, state) for state in CLONER}
    blue = {s: _count(p, "--clr-action") for s, p in png.items()}
    red = {s: _count(p, "--clr-danger") for s, p in png.items()}
    # two disks writing draw two blue bars; one writing and one skipped -- one,
    # and the skipped row has no bar fill at all
    assert blue["writing"] > blue["skipped"] + 1000, blue
    # a drawer's question: the row is outlined orange and says so in a pill --
    # not "ממתינה לשידור" in grey (metal 17/10)
    assert _count(png["asking"], "--clr-warning") > _count(png["writing"], "--clr-warning") + 1000
    # failed: a red bar, LED, edge, pill; done: green bars and no red
    assert red["failed"] > 1000 and red["done"] < 100, red
    assert _count(png["done"], "--clr-success") > _count(png["writing"], "--clr-success") + 1000
    # the round that ended with a failure says it in its red title
    assert _count(png["failed"], "--clr-danger-fg") > _count(png["done"], "--clr-danger-fg") + 300


BUILD_B = ("room_label=מחשבי שיכפול\n"
           "machine=1|3c:52:82:a1:00:21|1|1|0|writing|20|1|\nmachine_drawers=0|2\n"
           "room_drawer=0|1|S1|Samsung SSD 870 EVO|500107862016|passed|0|1||||ok||||37|writing\n"
           "room_drawer=0|2|S2|WDC WD5000AAKX|500107862016|passed|0|1||||ok||||-1|skipped\n"
           "{second}"
           "round=office365|1|0|0|4|0|4|192|68000000|300|running\nmessage=x|y\n")
SECOND = ("machine=2|3c:52:82:a1:00:22|1|1|0|writing|48|1|sdb: queue overflow: target lost bytes\n"
          "machine_drawers=1|3\n"
          "room_drawer=1|2|S4|Samsung SSD 860 EVO|250059350016|passed|0|1||||ok||||16|failed\n"
          "room_drawer=1|3|S5|Crucial MX500|500107862016|passed|0|1||||ok||||81|writing\n")


@_GUI
def test_the_build_screen_draws_a_cage_per_cloner_of_the_round(tmp_path):
    """גרסה ב׳: שני מחשבים — שני כלובים זה לצד זה; הכלוב השני (משמאל) מוסיף
    שורה שנכשלה באדום."""
    binary = _build_gui(tmp_path)
    _render(binary, BUILD_B.format(second=""), tmp_path / "one")
    _render(binary, BUILD_B.format(second=SECOND), tmp_path / "two")
    one, two = tmp_path / "one-room-live-light.png", tmp_path / "two-room-live-light.png"
    assert _count(two, "--clr-danger") > _count(one, "--clr-danger") + 500
    # side by side: the left half has the second cage's red row, the single cage's does not
    def left_red(path: Path) -> int:
        w, _h, ch, px = _png_rgb(path)
        red = _token("--clr-danger")
        return sum(1 for i in range(0, len(px), ch)
                   if (i // ch) % w < w // 2 and all(abs(px[i + k] - red[k]) <= 6 for k in range(3)))
    assert left_red(two) > left_red(one) + 300


def test_the_three_screens_draw_the_same_row():
    lcrun = (SRC / "screens_lcrun.c").read_text(encoding="utf-8")
    room = (SRC / "screens_room.c").read_text(encoding="utf-8")
    cloner = (SRC / "screens_cloner.c").read_text(encoding="utf-8")
    for name, text in (("screens_lcrun.c", lcrun), ("screens_room.c", room), ("screens_cloner.c", cloner)):
        assert "lc_bay_draw(a, cr," in text, name
        assert "lc_hero(a, cr," in text, name
    # no second copy of the row: the drawer number / "מגירה" are drawn in lcbay.c only
    for name, text in (("screens_lcrun.c", lcrun), ("screens_room.c", room), ("screens_cloner.c", cloner)):
        assert not re.search(r'"מגירה", 0, DIR_RTL', text), name
    assert "src/lcbay.c" in (SRC.parent / "Makefile").read_text(encoding="utf-8")
