"""‏#1269 שלב 2 — החוזה בין הגואי הנייטיב לסוכן: דרייברים לפי דיסק יעד.

שתי החוליות של הסוכן, בלי מהדר ובלי שרת:

* ‏`gui_state` (‏`agent/lib/gui/guistate.sh`) כותב לקובץ המצב את רשימת הדגמים
  מ-`GET /api/console/room/driver-models` — ‏`driver_models=<n>` ושורת
  ‏`driver_model=<vendor>|<model>` לכל דגם — ו**`driver_models=-1`** כשהרשימה
  לא נקראה. ‏"לא נקרא" ו"אין דגמים" הם שני מצבים (עיקרון 5): המתג מושבת
  בשניהם, אבל עם סיבה אחרת. ולכל מגירה בסבב — תוצאת הנחת הדרייברים
  (‏`drawer_list[].drivers`) בשדות 9–10 של `room_drawer=`.
* ‏`gui_records`/`gui_dispatch` (‏`agent/lib/gui/guibridge.sh`) מתרגמים את רשומת
  ‏`room-open` / `direct-open` עם `drivers=` לגוף ה-POST: ‏`driver_models`,
  ובסבב מהספרייה גם `target_slots` — השרת קושר דגם רק לדיסק שנבחר כיעד.
  בלי `drivers=` (המתג כבוי) הגוף **זהה** למה שהיה לפני #1269.

אין כאן "אוטומטי": החומרה שזוהתה היא של המשכפל, לא של המכונה שתקבל את
הדיסק (‏interfaces.md סעיף 17) — דגם מפורש או "בלי".

הצד של ה-C (הפרסר, המסך, הרשומה שנפלטת) — ב-`test_room_drivers_native.py`.

**בקרה שלילית:** על הבסיס (‏feat/1269-drivers-per-disk) ‏`gui_state` אינו כותב
‏`driver_models` כלל, ‏`room_drawer=` נגמר בשדה 8, ו-`gui_records` דוחה את
המפתח `drivers=` כרשומה פגומה — כל הטסטים כאן נופלים על התנהגות.
"""

from __future__ import annotations

import json
from pathlib import Path

from native import requires_native
from test_class_deploy_switch import PRELUDE, STATION
from test_cloner_gui import AGENT, BASH, posix, sh

MAC1 = "a0:48:1c:8a:18:40"
MAC2 = "78:ac:c0:9b:11:c2"

MODELS = [
    {"vendor": "HP", "model": "HP EliteDesk 800 G6", "packages": ["g6-all"]},
    {"vendor": "LENOVO", "model": "ThinkCentre M70q Gen 3",
     "packages": ["m70q-chipset", "m70q-nic"]},
]


def _drawer(port: int, serial: str, drivers=None) -> dict:
    return {"dev": "sd" + "abc"[port - 1], "port": port, "serial": serial, "model": "870 EVO",
            "size_bytes": 256060514304, "smart": "passed", "fresh": True,
            "selected": True, "drivers": drivers}


ROOM_LIVE = {
    "round": {"image_name": "office365", "wave_number": 1, "wave_state": "running",
              "written_drives": 0, "target_drives": 3, "ready_drives": 0,
              "remaining_drives": 3, "elapsed_s": 10, "rate_bps": None, "eta_s": None},
    "machines": [{
        "name": "CLONE-01", "mac": MAC1, "awake": True, "joined": True,
        "fresh_drawers": 3, "state": "done", "drawer_count": 3,
        "drawer_list": [
            _drawer(1, "S1", {"state": "staged", "packages": ["m70q-chipset", "m70q-nic"]}),
            _drawer(2, "S2", {"state": "failed", "packages": [],
                              "error": "the server has no driver package for the chosen model"}),
            _drawer(3, "S3", None),
        ]}]}


def _state(tmp_path: Path, room: dict, models: list | None) -> list[str]:
    """‏gui_state במצב `room` מול room.json קבוע; ‏`models=None` = הקריאה של
    רשימת הדגמים נכשלה (‏gui_get מחזיר 1, כמו HTTP שאינו 200)."""
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    (run / "station.json").write_text(json.dumps(STATION), newline="\n")
    (run / "room.json").write_text(json.dumps(room), newline="\n")
    (run / "images.json").write_text("[]", newline="\n")
    if models is not None:
        (run / "models.json").write_text(json.dumps(models), newline="\n")
    (gui / "mode").write_text("room\n", newline="\n")
    getter = (
        'gui_get() { case "$1" in '
        f'room) cp {posix(run / "room.json")!r} "$2" ;; '
        f'images) cp {posix(run / "images.json")!r} "$2" ;; '
        f'room/driver-models) cp {posix(run / "models.json")!r} "$2" ;; '
        '*) return 1 ;; esac; }; '
    )
    script = (
        f'export RUN_DIR={posix(run)!r} GUI_DIR={posix(gui)!r} '
        f'SERVER=http://127.0.0.1:9 MAC=aa:bb:cc:dd:ee:ff IMAGESRV_TEST=1; '
        + PRELUDE
        + f'http_get() {{ cat {posix(run / "station.json")!r}; }}; '
        + getter
        + 'gui_state'
    )
    out = sh(script)
    assert out.returncode == 0, out.stderr
    return (gui / "state").read_text(encoding="utf-8").splitlines()


JQ = requires_native(("bash", BASH), "jq", why="gui_state והגשר בונים JSON ב-jq")


@JQ
def test_gui_state_lists_the_library_models_for_the_switch(tmp_path):
    lines = _state(tmp_path, {"round": None, "machines": []}, MODELS)
    assert "driver_models=2" in lines
    assert [line for line in lines if line.startswith("driver_model=")] == [
        "driver_model=HP|HP EliteDesk 800 G6",
        "driver_model=LENOVO|ThinkCentre M70q Gen 3",
    ]


@JQ
def test_an_empty_library_is_zero_models_not_unread(tmp_path):
    lines = _state(tmp_path, {"round": None, "machines": []}, [])
    assert "driver_models=0" in lines
    assert not [line for line in lines if line.startswith("driver_model=")]


@JQ
def test_an_unread_model_list_is_minus_one_and_the_room_screen_still_draws(tmp_path):
    """‏HTTP שאינו 200 על רשימת הדגמים אינו מפיל את מסך החדר כולו (‏message=),
    ואינו נכתב כ"אין דגמים" — ‏`-1`, והמתג אומר שהרשימה לא נקראה."""
    lines = _state(tmp_path, {"round": None, "machines": []}, None)
    assert "driver_models=-1" in lines
    assert not [line for line in lines if line.startswith("driver_models=0")]
    assert not [line for line in lines if line.startswith("message=")]


@JQ
def test_a_model_name_cannot_break_the_record(tmp_path):
    """‏`|` ושורה חדשה אינם יכולים לעבור בקובץ המצב — הם מוחלפים ברווח, כמו
    בכל רשומה, כך ש-`<vendor>|<model>` נשאר שני שדות בדיוק."""
    lines = _state(tmp_path, {"round": None, "machines": []},
                   [{"vendor": "A|B", "model": "M\nN", "packages": ["x"]}])
    assert "driver_model=A B|M N" in lines


@JQ
def test_room_drawer_carries_the_drivers_outcome_of_each_drawer(tmp_path):
    lines = _state(tmp_path, ROOM_LIVE, MODELS)
    rows = {r[1]: r for r in (line.split("=", 1)[1].split("|")
                              for line in lines if line.startswith("room_drawer="))}
    assert len(rows) == 3, rows
    # [8:10]: field 11 is the slot name (#704, test_slot_names_704_gui.py)
    assert rows["1"][8:10] == ["staged", "m70q-chipset, m70q-nic"]
    assert rows["2"][8:10] == ["failed", "the server has no driver package for the chosen model"]
    assert rows["3"][8:10] == ["", ""], "drivers null = the step did not run -- never no_match"
    assert rows["1"][:8] == ["0", "1", "S1", "870 EVO", "256060514304", "passed", "1", "1"], \
        "the first eight fields keep their meaning"


# --- הגשר: רשומה → גוף POST ---------------------------------------------------


def _bridge(tmp_path: Path, record: str) -> dict | None:
    """מריץ את `gui_records` האמיתי על רשומה אחת; מחזיר את גוף ה-POST, או
    ‏None כששום דבר לא נשלח. רק התעבורה מוחלפת (console_post, http_get)."""
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    gui = tmp_path / "gui"; gui.mkdir(exist_ok=True)
    posted = run / "posted.json"
    posted.unlink(missing_ok=True)
    (run / "record").write_bytes(record.encode("utf-8"))
    script = (
        f'export RUN_DIR={posix(run)!r} GUI_DIR={posix(gui)!r} LOG_FILE={posix(run / "log")!r} '
        f'SERVER=http://127.0.0.1:9 MAC=c8:d9:d2:0b:fe:32 IMAGESRV_TEST=1; '
        f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/jsonq.sh; '
        f'. {posix(AGENT)}/lib/gui/roomflow.sh; '
        f'. {posix(AGENT)}/lib/gui/guibridge.sh; '
        'gui_role() { GUI_ROLE=admin; }; '
        'http_get() { printf \'{"disks":[{"dev":"sda","removable":false}]}\'; }; '
        f'console_post() {{ cp "$2" {posix(posted)!r}; printf 200; }}; '
        f'gui_records < {posix(run / "record")!r}; true'
    )
    out = sh(script)
    assert out.returncode == 0, out.stderr
    return json.loads(posted.read_text(encoding="utf-8")) if posted.exists() else None


@JQ
def test_room_open_without_drivers_posts_the_selection_only(tmp_path):
    # #1275 (owner 11/10): no ticked disk = nothing is posted (never "every drawer")
    assert _bridge(tmp_path, "room-open\nimage=img_1\ntarget=6\n\n") is None
    body = _bridge(tmp_path, f"room-open\nimage=img_1\ntarget=6\nslots={MAC1}@1\n\n")
    assert body == {"image_id": "img_1", "target_drives": 6,
                    "target_slots": [{"mac": MAC1, "ports": [1]}]}, "switch off: no driver_models"


@JQ
def test_room_open_with_drivers_adds_the_chosen_disks_and_their_models(tmp_path):
    record = ("room-open\nimage=img_1\ntarget=6\n"
              f"slots={MAC1}@1,2/{MAC2}@3\n"
              f"drivers={MAC1}|1|LENOVO|ThinkCentre M70q Gen 3|{MAC2}|3|HP|HP EliteDesk 800 G6\n\n")
    body = _bridge(tmp_path, record)
    assert body == {
        "image_id": "img_1", "target_drives": 6,
        "target_slots": [{"mac": MAC1, "ports": [1, 2]}, {"mac": MAC2, "ports": [3]}],
        "driver_models": [
            {"mac": MAC1, "port": 1, "vendor": "LENOVO", "model": "ThinkCentre M70q Gen 3"},
            {"mac": MAC2, "port": 3, "vendor": "HP", "model": "HP EliteDesk 800 G6"},
        ],
    }, "disk 2 of CLONE-01 is a target with no drivers ('בלי') -- in target_slots, not in driver_models"


@JQ
def test_direct_open_with_drivers_adds_driver_models_to_the_same_body(tmp_path):
    body = _bridge(tmp_path, f"direct-open\nslots={MAC1}@1,2\ndrivers={MAC1}|2|HP|HP EliteDesk 800 G6\n\n")
    assert body == {
        "source": {"kind": "build_disk", "mac": "c8:d9:d2:0b:fe:32", "disk": "sda"},
        "target_slots": [{"mac": MAC1, "ports": [1, 2]}],
        "driver_models": [{"mac": MAC1, "port": 2, "vendor": "HP", "model": "HP EliteDesk 800 G6"}],
    }
    body = _bridge(tmp_path, f"direct-open\nslots={MAC1}@1,2\n\n")
    assert body == {"source": {"kind": "build_disk", "mac": "c8:d9:d2:0b:fe:32", "disk": "sda"},
                    "target_slots": [{"mac": MAC1, "ports": [1, 2]}]}, "switch off = the #715 body"


@JQ
def test_a_malformed_drivers_value_posts_nothing(tmp_path):
    bad = [
        f"room-open\nimage=img_1\ntarget=6\nslots={MAC1}@1\ndrivers={MAC1}|1|LENOVO\n\n",   # 3 fields
        f"room-open\nimage=img_1\ntarget=6\nslots={MAC1}@1\ndrivers={MAC1}|x|HP|G6\n\n",    # port
        f"room-open\nimage=img_1\ntarget=6\nslots={MAC1}@1\ndrivers=$(touch BAD)|1|HP|G6\n\n",
        f"room-open\nimage=img_1\ntarget=6\nslots={MAC1}@1\ndrivers={MAC1}|1||G6\n\n",      # no vendor
        f"room-open\nimage=img_1\ntarget=6\ndrivers={MAC1}|1|HP|G6\n\n",                    # no slots
        f"direct-open\nslots={MAC1}@1\ndrivers={MAC1}|1|HP\n\n",
    ]
    for record in bad:
        assert _bridge(tmp_path, record) is None, record
    assert not (Path(__file__).resolve().parent.parent / "BAD").exists()
