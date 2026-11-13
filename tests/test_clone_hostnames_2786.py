"""#2786 (נדב 09/11) — שם מחשב לכל דיסק בסבב חדר השיכפול (השרת).

הקונסולה מחשבת את השמות ושולחת שם לכל `(mac, port)` שנבחר; השרת מאמת
(הכלל של Windows ולינוקס יחד — אנגלית, ספרות ומקף, עד 15, לא כולו
ספרות), שומר ברמת ה-round, ומוסר לכל משכפל את השמות של החריצים שלו
(`session.hostnames`). בלי שמות — הסבב בדיוק כמו קודם (`null`).

הכול כאן עובר דרך ה-API (פתיחה, hello, הגדרות), כדי שהבקרה השלילית תיפול
על התנהגות ולא על ייבוא.
"""

from __future__ import annotations

import copy

import pytest

pytest.importorskip("fastapi")

from conftest import LINUX_GUID, MANIFEST_256, write_image          # noqa: E402
from test_server_room import (CLONER1, CLONER2, cloner_hello,        # noqa: E402,F401
                              room, room_server)

SLOTS = [{"mac": CLONER1, "ports": [1, 2]}, {"mac": CLONER2, "ports": [1]}]
NAMES = [{"mac": CLONER1, "port": 1, "name": "100-INS"},
         {"mac": CLONER1, "port": 2, "name": "100-01"},
         {"mac": CLONER2, "port": 1, "name": "100-02"}]

DUAL_ID = "img_2d7860"


def _cloners(room_server):
    anon = room_server["anon"]
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    cloner_hello(anon, CLONER2, ["S3", "S4"])


def _open(client, hostnames=None, *, image="img_7f3a91", target=3, slots=SLOTS, raw=False):
    body = {"image_id": image, "target_drives": target, "target_slots": slots}
    if hostnames is not None or raw:
        body["hostnames"] = hostnames
    return client.post("/api/console/room", json=body)


def _dual(room_server) -> None:
    manifest = copy.deepcopy(MANIFEST_256)
    manifest.update(id=DUAL_ID, name="Win+Ubuntu")
    manifest["partitions"].append({
        "index": 4, "type_guid": LINUX_GUID, "role": "linux", "fs": "ext4",
        "start_sector": 498749440, "size_bytes": 512 * 2**20, "used_bytes": 2**28,
        "file": "p4.linux.pcl.zst", "sha256": "cc" * 32, "expandable": False})
    write_image(room_server["ctx"].library.root, manifest)


# --- בלי שמות: הסבב כמו קודם ------------------------------------------------


def test_a_round_without_names_is_unchanged(room_server):
    _cloners(room_server)
    assert _open(room_server["deploy"]).status_code == 200
    assert room(room_server["deploy"])["round"]["hostnames"] is None
    session = cloner_hello(room_server["anon"], CLONER1, ["S1", "S2"])["session"]
    assert session["hostnames"] is None and session["hostname_os"] is None


# --- השמות נשמרים, וכל משכפל מקבל את של החריצים שלו --------------------------


def test_each_cloner_gets_the_name_of_each_of_its_drawers(room_server):
    _cloners(room_server)
    answer = _open(room_server["deploy"], {"names": NAMES})
    assert answer.status_code == 200, answer.json()
    assert room(room_server["deploy"])["round"]["hostnames"] == {"names": NAMES, "os": None}
    anon = room_server["anon"]
    one = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]
    two = cloner_hello(anon, CLONER2, ["S3", "S4"])["session"]
    assert one["hostnames"] == {"1": "100-INS", "2": "100-01"}
    assert two["hostnames"] == {"1": "100-02"}, "חריץ 2 של משכפל 2 לא נבחר — אין לו שם"
    events = room_server["admin"].get("/api/console/journal").json()
    opened = next(e for e in events if e["event"] == "room_open")
    assert "names=100-INS..100-02" in opened["text"]


def test_the_mac_is_canonical_and_the_order_of_the_list_does_not_matter(room_server):
    _cloners(room_server)
    shuffled = [{**NAMES[2], "mac": CLONER2.upper()}, NAMES[1], NAMES[0]]
    assert _open(room_server["deploy"], {"names": shuffled}).status_code == 200
    assert room(room_server["deploy"])["round"]["hostnames"]["names"] == NAMES


# --- הכלל: מה ש-Windows ולינוקס מקבלים יחד ---------------------------------


@pytest.mark.parametrize("bad, fragment", [
    ("A" * 16, "15"),                       # NetBIOS
    ("100 01", "אותיות אנגליות"),
    ("100_01", "אותיות אנגליות"),
    ("100.01", "אותיות אנגליות"),
    ("כיתה-01", "אותיות אנגליות"),
    ("-10001", "לא מתחיל"),
    ("10001-", "לא מתחיל"),
    ("10001", "כולו ספרות"),                 # Windows אוסר; לינוקס היה מקבל
    ("", "ריק"),
])
def test_a_name_windows_or_linux_would_refuse_blocks_the_round(room_server, bad, fragment):
    _cloners(room_server)
    names = [NAMES[0], {**NAMES[1], "name": bad}, NAMES[2]]
    answer = _open(room_server["deploy"], {"names": names})
    assert answer.status_code == 400
    assert fragment in answer.json()["detail"], answer.json()
    assert room(room_server["deploy"])["round"] is None, "שום סבב לא נפתח"


@pytest.mark.parametrize("good", ["A" * 15, "LAB-A-01", "7735-30-01", "100-INS", "a1"])
def test_a_name_both_accept_opens(room_server, good):
    _cloners(room_server)
    names = [NAMES[0], {**NAMES[1], "name": good}, NAMES[2]]
    assert _open(room_server["deploy"], {"names": names}).status_code == 200


def test_a_target_without_a_name_blocks_the_round(room_server):
    """שדה ריק במצב פרטני חוסם (הכרעה 2 במוקאפ) — גם מול ה-API."""
    _cloners(room_server)
    answer = _open(room_server["deploy"], {"names": NAMES[:2]})
    assert answer.status_code == 400
    assert "חסר שם מחשב ל-1 דיסקים" in answer.json()["detail"]
    assert room(room_server["deploy"])["round"] is None


@pytest.mark.parametrize("names, fragment", [
    ([*NAMES, {"mac": CLONER2, "port": 2, "name": "100-03"}], "אינו בין דיסקי היעד"),
    ([*NAMES, NAMES[0]], "שני שמות"),
    ([{**NAMES[0], "port": "1"}, *NAMES[1:]], "אינה תקינה"),
    ("100", "אינה תקינה"),
])
def test_a_malformed_choice_is_refused_by_name(room_server, names, fragment):
    _cloners(room_server)
    hostnames = names if isinstance(names, str) else {"names": names}
    answer = _open(room_server["deploy"], hostnames)
    assert answer.status_code == 400
    assert fragment in answer.json()["detail"]


def test_names_in_a_round_of_more_than_one_wave_are_refused(room_server):
    """דיסקים של גל נוסף היו מקבלים את אותם שמות — נחסם בגלוי, לא נכתב כפול."""
    _cloners(room_server)
    answer = _open(room_server["deploy"], {"names": NAMES}, target=6)
    assert answer.status_code == 400
    assert "גל אחד" in answer.json()["detail"]


def test_names_are_refused_in_a_direct_round_not_dropped(room_server):
    from test_direct_server import BUILD, _register_build   # noqa: PLC0415
    _cloners(room_server)
    _register_build(room_server)
    answer = room_server["admin"].post("/api/console/room", json={
        "source": {"kind": "build_disk", "mac": BUILD, "disk": "sda"},
        "target_slots": [{"mac": CLONER1, "ports": [1, 2]}],
        "hostnames": {"names": NAMES[:2]}})
    assert answer.status_code == 400
    assert "רק בסבב מהספרייה" in answer.json()["detail"]


# --- אימג' עם שתי מערכות הפעלה --------------------------------------------


def test_a_dual_os_image_takes_the_os_choice_and_the_agent_gets_it(room_server):
    _cloners(room_server)
    _dual(room_server)
    answer = _open(room_server["deploy"], {"names": NAMES, "os": ["windows"]}, image=DUAL_ID)
    assert answer.status_code == 200, answer.json()
    session = cloner_hello(room_server["anon"], CLONER1, ["S1", "S2"])["session"]
    assert session["hostname_os"] == ["windows"]


def test_both_systems_is_the_default_null(room_server):
    _cloners(room_server)
    _dual(room_server)
    assert _open(room_server["deploy"], {"names": NAMES, "os": ["linux", "windows"]},
                 image=DUAL_ID).status_code == 200
    assert room(room_server["deploy"])["round"]["hostnames"]["os"] is None


@pytest.mark.parametrize("os_choice", [[], ["mac"], "windows", ["windows", "windows"]])
def test_no_system_or_an_unknown_one_is_refused(room_server, os_choice):
    _cloners(room_server)
    _dual(room_server)
    answer = _open(room_server["deploy"], {"names": NAMES, "os": os_choice}, image=DUAL_ID)
    assert answer.status_code == 400
    assert "מערכת הפעלה" in answer.json()["detail"]


def test_an_os_choice_on_a_single_os_image_is_refused(room_server):
    _cloners(room_server)
    answer = _open(room_server["deploy"], {"names": NAMES, "os": ["windows"]})
    assert answer.status_code == 400
    assert "רק לאימג' עם Windows ולינוקס" in answer.json()["detail"]


# --- ההגדרות: ריפוד וסיומת המרצה ------------------------------------------


def test_the_settings_default_to_two_digits_and_ins_and_reach_the_room_view(room_server):
    values = room_server["admin"].get("/api/console/settings").json()
    assert (values["clone_name_pad"], values["clone_name_instructor"]) == ("2", "INS")
    assert room(room_server["deploy"])["clone_names"] == {"pad": 2, "instructor": "INS", "max_len": 15}


def test_the_settings_are_written_and_the_operator_sees_them(room_server):
    admin = room_server["admin"]
    assert admin.post("/api/console/settings", json={
        "clone_name_pad": "3", "clone_name_instructor": "TEACH"}).status_code == 200
    assert room(room_server["deploy"])["clone_names"] == {"pad": 3, "instructor": "TEACH", "max_len": 15}


@pytest.mark.parametrize("key, value", [
    ("clone_name_pad", "0"), ("clone_name_pad", "5"), ("clone_name_pad", "x"),
    ("clone_name_instructor", ""), ("clone_name_instructor", "INS-"),
    ("clone_name_instructor", "מרצה"), ("clone_name_instructor", "ABCDEFGHI"),
    ("clone_name_instructor", "I S"),
])
def test_a_bad_setting_is_refused(room_server, key, value):
    answer = room_server["admin"].post("/api/console/settings", json={key: value})
    assert answer.status_code == 400, answer.json()
    assert room(room_server["deploy"])["clone_names"] == {"pad": 2, "instructor": "INS", "max_len": 15}


def test_the_deploy_role_cannot_write_the_settings(room_server):
    answer = room_server["deploy"].post("/api/console/settings", json={"clone_name_pad": "3"})
    assert answer.status_code == 403


# --- אותו כלל בקונסולה ובשרת ---------------------------------------------------

PARITY = ["A", "1", "12345", "100-01", "100-INS", "a" * 15, "a" * 16, "-PC", "PC-", "PC 3",
          "PC_3", "PC.3", "כיתה-1", "Pé3", "", "7735-30-01", "0x1F", "--", "A-1"]

_NODE_RULE = r"""
const src = require('fs').readFileSync(process.argv[1], 'utf8');
const max = src.match(/const NM_MAX = (\d+)/);
const body = src.slice(src.indexOf('function cloneNameRefusal('), src.indexOf('\n}\n', src.indexOf('function cloneNameRefusal(')) + 2);
if (!max || !body.startsWith('function')) { console.log('NOT-FOUND'); process.exit(3); }
const rule = new Function(`const NM_MAX = ${max[1]}; ${body}; return cloneNameRefusal;`)();
console.log(JSON.stringify(JSON.parse(process.argv[2]).map((n) => rule(n) !== '')));
"""


def test_the_console_refuses_exactly_what_the_server_refuses():
    """הכלל אחד: `cloneNameRefusal` (console.js) מול `computer_name_refusal` על אותם שמות."""
    import json                                                      # noqa: PLC0415
    import shutil                                                    # noqa: PLC0415
    import subprocess                                                # noqa: PLC0415
    from pathlib import Path                                         # noqa: PLC0415

    from server.core import hostname_rule                            # noqa: PLC0415
    node = shutil.which("node")
    assert node, "Node is required (test_operator_javascript)"
    console = Path(__file__).resolve().parent.parent / "server" / "static" / "console.js"
    out = subprocess.run([node, "-e", _NODE_RULE, str(console), json.dumps(PARITY)],
                         capture_output=True, text=True, encoding="utf-8",
                         stdin=subprocess.DEVNULL, timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr
    refused_js = json.loads(out.stdout)
    refused_py = [hostname_rule.computer_name_refusal(n) is not None for n in PARITY]
    assert refused_js == refused_py, list(zip(PARITY, refused_js, refused_py))
