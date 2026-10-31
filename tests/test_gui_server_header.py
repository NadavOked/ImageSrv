"""‏#1252 (נמדד על ברזל 10/10, ‏HP-BUILD מול השרת החדש ב-v0.59.2): כותרת
הגואי הנייטיב הציגה "שרת imagesrv-srv · 10.10.10.1" ו-"גרסה v0.48.1",
ותחתית מסך הכניסה "imagesrv-srv · v0.48.1 · 10.10.10.1" — מחרוזות מוקאפ
מקודדות ב-`screens.c`. בפועל השרת היה TA, ‏10.44.0.1, ‏v0.59.2. טכנאי
שרואה v0.48.1 חושב שהמכונה עלתה מ-initrd ישן (עיקרון 5).

שלוש החוליות:

* ‏`gui_state` (‏`agent/lib/gui/guistate.sh`) כותב `server_name=` ו-
  ‏`server_version=` מ-`GET /api/v1/agent/state` (‏`null` → ריק), ו-
  ‏`server_addr=` מ-`imagesrv.server` (‏`$SERVER`) — גם בתמונת הכשל וגם
  במסך המשכפל, כי את הכתובת הסוכן יודע בלי השרת.
* ‏`state.c` מפרסר אותן, ו-`screens.c` מצייר אותן בכותרת, בתחתית הכניסה
  ובשורת ההמתנה. ערך ריק מצויר "לא ידוע" — לא מספר. שם המכונה, שנפל
  ל-"BUILD-01"/"CLONER-02" מומצאים, נופל עכשיו גם הוא ל-"לא ידוע", ו-"2"
  נמחק מ-"מחשב שיכפול 2".
* צד השרת — ‏`test_station_server_identity.py`.

**בקרה שלילית:** על הקוד הישן `gui_state` אינו כותב אף אחת מהרשומות,
‏`gui_server_addr` אינו קיים, המחרוזות המקודדות במקור, ושני מצבים עם
שרת שונה מצוירים זהים — כל הטסטים נופלים.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from native import requires_native
from test_class_deploy_switch import AGENT, STATION, state_records
from test_cloner_gui import BASH, GUI, _build_gui, _pkgconfig, _png, posix, sh

SRC = GUI / "src"

#: מחרוזות המוקאפ שהגיעו לייצור. ‏install.c מחוץ לרשימה: ה-`install_demo`
#: שלו הוא נתוני `--png` בלבד, ולא מסך שרץ מול שרת.
MOCKUP = ("imagesrv-srv", "10.10.10.1", "v0.48.1", "CLONER-02")


def test_no_mockup_server_values_in_the_screens():
    """רץ בכל מקום (גם בווינדוס)."""
    for path in sorted(SRC.glob("screens*.c")):
        text = path.read_text(encoding="utf-8")
        for literal in MOCKUP:
            assert literal not in text, f"{path.name}: mockup value {literal!r}"
    assert "מחשב שיכפול 2" not in (SRC / "screens.c").read_text(encoding="utf-8")


@pytest.mark.parametrize("server, addr", [
    ("http://10.44.0.1:8080", "10.44.0.1"),
    ("https://ta.college.ac.il:8443/x", "ta.college.ac.il"),
    ("http://10.10.10.8", "10.10.10.8"),
    ("", ""),
])
@requires_native(("bash", BASH), why="הסוכן הוא POSIX sh")
def test_the_address_is_the_host_of_imagesrv_server(server, addr):
    out = sh(f'SERVER={server!r}; . {posix(AGENT)}/lib/gui/guistate.sh; gui_server_addr')
    assert out.returncode == 0, out.stderr
    assert out.stdout == f"server_addr={addr}\n"


@requires_native(("bash", BASH), "jq", why="gui_state בונה את המצב ב-jq")
def test_gui_state_writes_the_server_name_address_and_version(tmp_path):
    rows = state_records(
        tmp_path, None, env="SERVER=http://10.44.0.1:8080",
        station={**STATION, "server_name": "TA", "server_version": "v0.59.2"})
    assert "server_name=TA" in rows, rows
    assert "server_version=v0.59.2" in rows, rows
    assert "server_addr=10.44.0.1" in rows, rows


@requires_native(("bash", BASH), "jq", why="gui_state בונה את המצב ב-jq")
def test_an_unread_version_is_an_empty_record_not_a_number(tmp_path):
    """‏`server_version: null` (התג לא נקרא) ושרת ישן בלי השדות כלל — שתיהן
    רשומה ריקה, שהמסך מצייר "לא ידוע"."""
    null = state_records(tmp_path / "null", None,
                         station={**STATION, "server_name": "TA", "server_version": None})
    assert "server_version=" in null, null
    old = state_records(tmp_path / "old", None, station=STATION)
    assert "server_name=" in old and "server_version=" in old, old


def _png_state(path: Path, extra: str) -> Path:
    path.write_text(
        "disk=/dev/sda|Samsung SSD|500107862016|1|0\n"
        "machine_name=HP-BUILD\n"
        # --png renders every card; "message" needs msg_title or it falls to the menu.
        "message=x|y\n" + extra,
        encoding="utf-8", newline="\n")
    return path


@requires_native(
    ("bash", BASH),
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)
def test_the_header_login_foot_and_standby_line_draw_the_state(tmp_path):
    """שלושה מצבים: שרת ידוע (TA), שרת אחר, ובלי רשומות שרת כלל — מול
    מצב שבו הערכים הם המחרוזת "לא ידוע" עצמה.

    * ידוע ≠ אחר: הכרטיסים מציירים את המצב, לא מחרוזת קבועה (על הקוד
      הישן הם זהים — המחרוזת מקודדת).
    * חסר == "לא ידוע" מפורש: ערך שלא ידוע מצויר בדיוק "לא ידוע".
    * חסר ≠ ידוע: הנפילה אינה לערך כלשהו שנראה אמיתי."""
    binary = _build_gui(tmp_path)
    states = {
        "ta": "server_name=TA\nserver_addr=10.44.0.1\nserver_version=v0.59.2\n",
        "other": "server_name=HAIFA\nserver_addr=10.45.0.1\nserver_version=v0.60.0\n",
        "none": "",
        "said": "server_name=לא ידוע\nserver_version=לא ידוע\n",
    }
    for key, extra in states.items():
        _png(binary, tmp_path / key, _png_state(tmp_path / f"{key}.txt", extra))
    for card in ("menu", "login", "standby"):
        for theme in ("light", "dark"):
            png = {k: (tmp_path / f"{k}-{card}-{theme}.png").read_bytes() for k in states}
            assert png["ta"] != png["other"], f"{card}/{theme}: the server is not read from the state"
            assert png["none"] == png["said"], f"{card}/{theme}: unknown is not drawn as 'לא ידוע'"
            assert png["none"] != png["ta"], f"{card}/{theme}: unknown draws like a known server"
