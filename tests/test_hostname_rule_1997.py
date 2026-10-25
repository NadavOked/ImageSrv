"""‏#1997 (נדב, 22/10): שם מחשב או שרת = hostname באנגלית, עד 15 תווים.

הכלל אחד (`server/hostname_rule.py`) והוא נאכף בכל מקום שמגדירים בו שם:
מכונה (בנייה, שיכפול) בהוספה, בהדבקה ובעריכה; שם השרת (``POST /hostname``,
האשף, המתקין); ושם של שרת משנה ברישום ובעריכה. אותו כלל יושב בסוכן
(`agent/lib/hostname.sh`) ובמתקין (`installer/lib/answers.sh`), והטסטים כאן
משווים את שניהם לגרסת ה-Python על אותה רשימת שמות.

שם קיים שאינו חוקי (חיפה רשום "חיפה") **אינו נשבר**: השרת עולה, הרשומה
מוצגת, והשרת מסמן אותה (``name_problem`` / ``label_problem`` /
``server_name_problem``) כדי שהסקירה תציג אותה ב"דורש טיפול". אין מיגרציה
שמשנה שם לבד.

עברית נשארת מותרת בשם אימג', בשם תיקייה ובשם דיסק — טסט שומר על זה.
"""

from __future__ import annotations

import json
import os

import pytest

from native import requires_native
from server import hostname_rule, storage_nodes
from server.images import validate_display_name
from test_agent import AGENT, BASH, posix, sh
from test_installer_engine import SH, _answers, _command_shims, _machine, _run

RULE_WORDS = "אותיות אנגליות, ספרות ומקף"

VALID = ["A", "1", "PC3", "pc-3", "LAB1-05", "imagesrv-server", "a" * 15, "a-b-c", "BUILD1"]
INVALID = ["", "עמדה3", "חיפה", "PC 3", "PC_3", "PC.3", "-PC3", "PC3-", "-", "a" * 16,
           "srv1.college.ac.il", "10.0.0.1", "PC3\n", "Pé3"]


# --- הכלל עצמו -----------------------------------------------------------------

@pytest.mark.parametrize("name", VALID)
def test_a_hostname_is_accepted(name):
    assert hostname_rule.is_valid(name)
    assert hostname_rule.refusal(name) is None


@pytest.mark.parametrize("name", INVALID)
def test_a_name_that_cannot_be_a_hostname_is_refused_with_the_rule(name):
    assert not hostname_rule.is_valid(name)
    message = hostname_rule.refusal(name, "שם המחשב")
    assert RULE_WORDS in message and "15" in message


# --- עברית נשארת מותרת במה שאינו שם מחשב --------------------------------------

@pytest.mark.parametrize("name", ["אופיס 2024", "כיתה יב", "חיפה"])
def test_hebrew_is_still_allowed_in_image_and_folder_names(name):
    assert validate_display_name(name, "שם האימג'") == name


# --- הקונסולה: מכונות -------------------------------------------------------------

MAC = "aa:bb:cc:00:19:97"


def add(server, name: str, group: str = "grp_BUILD", mac: str = MAC):
    return server["admin"].post("/api/console/machines",
                                json={"mac": mac, "name": name, "group_id": group})


@pytest.mark.parametrize("group", ["grp_BUILD", "grp_CLONERS"])
@pytest.mark.parametrize("name", ["עמדה3", "PC 3", "PC_3", "PC.3", "-PC3", "PC3-"])
def test_a_machine_name_that_is_not_a_hostname_is_refused(server, group, name):
    r = add(server, name, group)
    assert r.status_code == 400, f"{name!r} נרשם ב-{group}"
    assert RULE_WORDS in r.json()["detail"]


@pytest.mark.parametrize("group", ["grp_BUILD", "grp_CLONERS"])
@pytest.mark.parametrize("name", ["PC3", "pc-3", "BUILD1", "1"])
def test_a_machine_name_that_is_a_hostname_is_accepted(server, group, name):
    assert add(server, name, group).status_code == 200, name


@pytest.mark.parametrize("group", ["grp_BUILD", "grp_CLONERS"])
def test_the_import_preview_names_the_rule(server, group):
    r = server["admin"].post("/api/console/machines/import", json={
        "group_id": group, "text": f"{MAC} עמדה3", "dry_run": True})
    assert RULE_WORDS in r.json()["preview"][0]["error"]


def test_renaming_a_machine_to_hebrew_is_refused(server):
    assert add(server, "PC3").status_code == 200
    r = server["admin"].put(f"/api/console/machines/{MAC}", json={"name": "עמדה3"})
    assert r.status_code == 400
    assert RULE_WORDS in r.json()["detail"]


def _legacy_machine(conn, name: str, group: str = "grp_BUILD", mac: str = MAC) -> None:
    """שם שנרשם לפני #1997 — ישירות ל-DB, כי הרישום כבר מסרב לו."""
    conn.execute("INSERT INTO machines (mac, suffix, group_id, added_at)"
                 " VALUES (?, ?, ?, '2026-10-01T00:00:00')", (mac, name, group))
    conn.commit()


def test_a_legacy_hebrew_machine_is_listed_and_flagged_not_renamed(server):
    _legacy_machine(server["ctx"].conn, "מחשב בנייה")
    assert add(server, "PC4", mac="aa:bb:cc:00:19:98").status_code == 200
    rows = {m["mac"]: m for m in server["admin"].get("/api/console/machines").json()}
    legacy = rows[MAC]
    assert legacy["suffix"] == "מחשב בנייה", "אין מיגרציה שמשנה את השם"
    assert "מחשב בנייה" in legacy["name_problem"] and RULE_WORDS in legacy["name_problem"]
    assert rows["aa:bb:cc:00:19:98"]["name_problem"] is None


def test_the_server_starts_on_a_database_with_legacy_names(server, tmp_path, images_root, clock):
    """הרשומות הישנות אינן חוסמות עלייה: אפליקציה חדשה על אותו DB קוראת אותן."""
    from server.app import create_app
    from test_sender import Recorder
    conn = server["ctx"].conn
    _legacy_machine(conn, "עמדה 1", group="grp_CLONERS")
    conn.execute("INSERT INTO storage_nodes (id, label, base_url, credential_ref, enrolled_at)"
                 " VALUES ('n1', 'חיפה', 'https://10.0.0.8:8443', 'r', '2026-10-01')")
    conn.commit()
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     now_fn=clock, sender_runner=Recorder(block=True),
                     health_hooks={"interfaces": lambda: []})
    try:
        row = app.state.ctx.conn.execute(
            "SELECT suffix FROM machines WHERE mac = ?", (MAC,)).fetchone()
        assert row["suffix"] == "עמדה 1"
        label = app.state.ctx.conn.execute(
            "SELECT label FROM storage_nodes WHERE id = 'n1'").fetchone()["label"]
        assert label == "חיפה", "אין מיגרציה שמשנה את שם שרת המשנה"
    finally:
        app.state.ctx.sender.stop()


def test_a_legacy_name_can_be_fixed_from_the_link(server):
    """הקישור ב"דורש טיפול" פותח את שינוי השם — והשינוי לשם חוקי עובר."""
    _legacy_machine(server["ctx"].conn, "מחשב בנייה")
    r = server["admin"].put(f"/api/console/machines/{MAC}", json={"name": "PC3"})
    assert r.status_code == 200, r.text
    rows = {m["mac"]: m for m in server["admin"].get("/api/console/machines").json()}
    assert rows[MAC]["suffix"] == "PC3" and rows[MAC]["name_problem"] is None


def test_hebrew_is_still_allowed_in_a_folder_and_a_disk_name(server):
    admin = server["admin"]
    assert admin.post("/api/console/folders", json={"name": "כיתות"}).status_code == 200
    assert add(server, "PC3", "grp_CLONERS").status_code == 200
    r = admin.put(f"/api/console/machines/{MAC}/slot-names",
                  json={"slot": "sata:1", "name": "דיסק מערכת"})
    assert r.status_code == 200, r.text


# --- שם השרת ------------------------------------------------------------------------

def test_me_flags_a_server_name_that_is_not_a_hostname(server, monkeypatch):
    from server import ports
    monkeypatch.setattr(ports.socket, "gethostname", lambda: "שרת-ראשי")
    me = server["admin"].get("/api/console/me").json()
    assert me["server_name"] == "שרת-ראשי"
    assert RULE_WORDS in me["server_name_problem"]
    monkeypatch.setattr(ports.socket, "gethostname", lambda: "imagesrv-ta")
    assert server["admin"].get("/api/console/me").json()["server_name_problem"] is None


def test_the_wizard_refuses_a_server_name_longer_than_15():
    from test_wizard import valid
    from server.wizard.core import validate_config
    errors = validate_config(valid(hostname="imagesrv-haifa-1"), {"eth-test"})
    assert RULE_WORDS in errors["hostname"]
    assert "hostname" not in validate_config(valid(hostname="imagesrv-haifa"), {"eth-test"})


# --- שרת משנה ----------------------------------------------------------------------

ADMIN = ("noc", "admin")


def _enroll(conn, label: str):
    return storage_nodes.enroll_node(
        conn, ADMIN, label=label, base_url="https://10.0.0.8:8443", node_id="sn_" + "1" * 16,
        pinned_spki="aa" * 32, client_cert_ref="c", credential_ref="r", protocol_version="2.1")


@pytest.mark.parametrize("label", ["חיפה", "Haifa server", "haifa_1", "imagesrv-haifa-01"])
def test_a_secondary_label_that_is_not_a_hostname_is_refused(server, label):
    with pytest.raises(ValueError, match=RULE_WORDS):
        _enroll(server["ctx"].conn, label)


def test_a_secondary_label_that_is_a_hostname_is_enrolled_and_cannot_be_renamed_to_hebrew(server):
    conn = server["ctx"].conn
    rid = _enroll(conn, "imagesrv-haifa")
    with pytest.raises(ValueError, match=RULE_WORDS):
        storage_nodes.edit_node(conn, rid, ADMIN, label="חיפה")
    r = server["admin"].put(f"/api/console/storage-nodes/{rid}", json={"label": "חיפה"})
    assert r.status_code == 400 and RULE_WORDS in r.json()["detail"]
    storage_nodes.edit_node(conn, rid, ADMIN, label="HAIFA")
    assert conn.execute("SELECT label FROM storage_nodes").fetchone()["label"] == "HAIFA"


def test_the_enroll_route_refuses_a_hebrew_label_before_pairing(server, monkeypatch):
    """הסירוב לפני ה-pairing: אחרת המשני כבר קשור והרשומה נופלת אחריו."""
    from server import storage_client
    calls = []
    monkeypatch.setattr(storage_client, "pair_secondary", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(storage_client, "fetch_server_spki", lambda *a, **k: calls.append(2))
    admin = server["admin"]
    r = admin.post("/api/console/storage-nodes/enroll", json={
        "url": "https://10.20.0.30:8443/api/interserver/v1", "label": "חיפה",
        "code": "ABC-DEF-GHJ", "expected_secondary_spki": "cc" * 32, "protocol_version": "2.1"})
    assert r.status_code == 400 and RULE_WORDS in r.json()["detail"]
    r = admin.post("/api/console/storage-nodes/enroll/preview", json={
        "url": "https://10.20.0.30:8443", "label": "חיפה"})
    assert r.status_code == 400 and RULE_WORDS in r.json()["detail"]
    assert calls == [], "לא פנו לשרת המשנה"


def test_a_legacy_hebrew_secondary_is_listed_and_flagged(server):
    conn = server["ctx"].conn
    conn.execute("INSERT INTO storage_nodes (id, label, base_url, credential_ref, enrolled_at)"
                 " VALUES ('n1', 'חיפה', 'https://10.0.0.8:8443', 'r', '2026-10-01')")
    conn.commit()
    nodes = server["admin"].get("/api/console/storage-nodes").json()
    assert [n["label"] for n in nodes] == ["חיפה"]
    assert "חיפה" in nodes[0]["label_problem"] and RULE_WORDS in nodes[0]["label_problem"]


# --- אותו כלל בסוכן ובמתקין ------------------------------------------------------

def _agent_refuses(tmp_path, name: str) -> bool:
    run = tmp_path / f"run{abs(hash(name))}"
    run.mkdir()
    out = sh(f'export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev; '
             f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/hostname.sh; '
             f'write_hostname sda /nonexistent.json {name!r} || true')
    result = json.loads(out.strip().splitlines()[-1])
    assert result["ok"] is False, result      # חוקי נופל אחר כך על המניפסט שאינו קיים
    return result["code"] == "bad_hostname"


#: עברית ותו מבוטא נשארים מחוץ להשוואה מול המעטפת: ‏`${#x}` וטווחי ה-glob שם
#: תלויים ב-locale, וזו שאלה על המעטפת ולא על הכלל. השורה החדשה — ‏`read`.
SHELL_NAMES = VALID + [n for n in INVALID if n.isascii() and "\n" not in n]


@requires_native(("bash", BASH))
@pytest.mark.parametrize("name", SHELL_NAMES)
def test_the_agent_applies_the_same_rule(tmp_path, name):
    assert _agent_refuses(tmp_path, name) is (not hostname_rule.is_valid(name)), name


@pytest.mark.skipif(SH is None or os.name == "nt", reason="the installer engine runs on Linux")
@pytest.mark.parametrize("name", [n for n in SHELL_NAMES if n])   # ריק = "missing answer", לא הכלל
def test_the_installer_applies_the_same_rule(tmp_path, name):
    box = _machine(tmp_path)
    _command_shims(tmp_path, box["env"])
    got = _run(box, _answers(tmp_path / "answers", hostname=name), "--dry-run")
    assert ("invalid-hostname" in got.stderr) is (not hostname_rule.is_valid(name)), (name, got.stderr)
