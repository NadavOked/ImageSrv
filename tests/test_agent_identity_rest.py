"""‏#997 — שומר הזהות של #855 על **שאר** נתיבי הסוכן.

‏#855 סגר hello ו-progress. הנתיבים שנשארו לפי MAC מוצהר: ‏login, ‏pulls,
‏sessions (station.py), ‏disk-event, ‏shrink-open/close/note, ו-PUT המניפסט
החי (direct.py). ו-`manifest`/`files` — בלי זהות בכלל: הסוכן לא שולח בהם
MAC (‏`http_get` ב-`agent/lib/core/common.sh`), ולכן הזהות שם היא **הפוכה**:
כתובת המקור → חכירת ה-DHCP שיושבת עליה → ה-MAC של החכירה רשום ב-`machines`.
בלי שינוי בסוכן, בלי initrd חדש.

אותו שומר (`api.identity_gate` / `api.source_gate`), אותם שני קודים,
אותו מתג `identity_check`, ואותו עיקרון 5: "אין חכירה" = `identity_unverifiable`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from agentlib import flat_lib
from conftest import hello_body, setup_classroom
from server.core.tasks import TOKEN_HEADER, staging_dir
from test_capture import (PART_A, PART_B, make_task, manifest_for, sha,
                          setup_build_machine, task_token)
from test_direct_server import _live_manifest, _open_direct
from test_machine_identity import (BUILD, IMAGE, MAC1, STATION_IP, STRANGER_IP,  # noqa: F401
                                   hello, identity_server, journal_details,
                                   write_leases)
from test_server_room import CLONER1, cloner_hello
from test_shrink_records import SERIAL, layout

CREDS = {"username": "labtech", "password": "deploy-pass-1"}
#: כתובת שיש עליה חכירה — אבל של MAC שאינו רשום בשום קבוצה (מחשב נייד
#: של תלמיד שביקש DHCP בוילן ההפצה: `dhcp-range` מחלק לכולם).
LEASED_STRANGER_IP = "10.44.12.98"
UNREGISTERED_MAC = "de:ad:be:ef:00:97"


def refused_by_name(response, code: str = "identity_refused") -> None:
    assert response.status_code == 403, response.text
    body = response.json()
    assert body["ok"] is False and body["code"] == code, body


def journal_where(server, event: str) -> list[str]:
    return [detail for _user, detail in journal_details(server, event)]


# --- login -------------------------------------------------------------------


def test_login_from_another_address_is_refused_before_the_password_is_checked(identity_server):
    """זר אינו יכול אפילו לנסות סיסמאות בשם מכונה: 403 זהות, ובלי
    `agent_login`/`agent_login_failed` ביומן (לא נבדקה סיסמה)."""
    setup_classroom(identity_server)
    body = {**CREDS, "mac": MAC1}
    refused_by_name(identity_server["stranger"].post("/api/v1/agent/login", json=body))
    assert any("(login)" in d for d in journal_where(identity_server, "identity_refused"))
    assert journal_details(identity_server, "agent_login") == []
    assert journal_details(identity_server, "agent_login_failed") == []
    ok = identity_server["station"].post("/api/v1/agent/login", json=body)
    assert ok.status_code == 200 and ok.json()["role"] == "deploy"


def test_login_without_a_mac_is_bad_mac(identity_server):
    response = identity_server["station"].post("/api/v1/agent/login", json=CREDS)
    assert response.status_code == 400 and response.json()["code"] == "bad_mac"


# --- pulls -------------------------------------------------------------------


def test_a_pull_from_another_address_is_refused_and_opens_nothing(identity_server):
    setup_classroom(identity_server)
    body = {"mac": MAC1, "image_id": IMAGE, **CREDS}
    refused_by_name(identity_server["stranger"].post("/api/v1/agent/pulls", json=body))
    assert identity_server["ctx"].conn.execute(
        "SELECT COUNT(*) AS n FROM sessions").fetchone()["n"] == 0
    assert journal_details(identity_server, "pull_refused") == []
    ok = identity_server["station"].post("/api/v1/agent/pulls", json=body)
    assert ok.status_code == 200 and ok.json()["kind"] == "unicast"


def test_a_pull_by_a_mac_without_a_lease_is_unverifiable(identity_server):
    ids = setup_classroom(identity_server)
    body = {"mac": ids["mac2"], "image_id": IMAGE, **CREDS}
    refused_by_name(identity_server["station"].post("/api/v1/agent/pulls", json=body),
                    "identity_unverifiable")


# --- sessions (station.py) ---------------------------------------------------


def _open_body(mac: str) -> dict:
    return {**CREDS, "mac": mac, "group_id": "grp_LAB1", "image_id": IMAGE}


def test_a_round_opened_from_another_address_is_refused(identity_server):
    setup_classroom(identity_server)
    refused_by_name(identity_server["stranger"].post(
        "/api/v1/agent/sessions", json=_open_body(MAC1)))
    assert identity_server["ctx"].conn.execute(
        "SELECT COUNT(*) AS n FROM sessions").fetchone()["n"] == 0
    assert journal_details(identity_server, "agent_login_failed") == []
    ok = identity_server["station"].post("/api/v1/agent/sessions", json=_open_body(MAC1))
    assert ok.status_code == 200 and ok.json()["prefix"] == "LAB1"


def test_a_round_opened_without_a_mac_is_bad_mac(identity_server):
    setup_classroom(identity_server)
    body = _open_body(MAC1)
    del body["mac"]
    response = identity_server["station"].post("/api/v1/agent/sessions", json=body)
    assert response.status_code == 400 and response.json()["code"] == "bad_mac"


# --- disk-event --------------------------------------------------------------


def _event(mac: str) -> dict:
    return {"session_id": "ses_x", "mac": mac, "disk": "sda", "port": 1,
            "serial": "S1", "smart": {"verdict": "ok", "reason": ""},
            "write_state": "pending", "decision": None}


def test_a_disk_event_from_another_address_is_refused_and_not_stored(identity_server):
    setup_classroom(identity_server)
    refused_by_name(identity_server["stranger"].post("/api/v1/agent/disk-event",
                                                     json=_event(MAC1)))
    assert identity_server["ctx"].conn.execute(
        "SELECT COUNT(*) AS n FROM disk_events").fetchone()["n"] == 0
    ok = identity_server["station"].post("/api/v1/agent/disk-event", json=_event(MAC1))
    assert ok.status_code == 200, ok.text


def test_a_disk_event_with_a_malformed_mac_is_still_bad_mac(identity_server):
    response = identity_server["stranger"].post("/api/v1/agent/disk-event",
                                                json=_event("zz"))
    assert response.status_code == 400 and response.json()["code"] == "bad_mac"


# --- shrink-open / close / note ----------------------------------------------


def test_shrink_records_from_another_address_are_refused(identity_server):
    setup_classroom(identity_server)
    station, stranger = identity_server["station"], identity_server["stranger"]
    refused_by_name(stranger.post("/api/v1/agent/shrink-open", json=layout(mac=MAC1)))
    assert identity_server["admin"].get("/api/console/shrink-records").json() == []
    rid = station.post("/api/v1/agent/shrink-open", json=layout(mac=MAC1)).json()["id"]

    note = {"mac": MAC1, "serial": SERIAL, "id": rid, "note": "why"}
    refused_by_name(stranger.post("/api/v1/agent/shrink-note", json=note))
    close = {"mac": MAC1, "serial": SERIAL, "id": rid}
    refused_by_name(stranger.post("/api/v1/agent/shrink-close", json=close))
    (rec,) = identity_server["admin"].get("/api/console/shrink-records").json()
    assert rec["id"] == rid and not rec.get("note")

    assert station.post("/api/v1/agent/shrink-note", json=note).status_code == 200
    assert station.post("/api/v1/agent/shrink-close", json=close).status_code == 200
    assert identity_server["admin"].get("/api/console/shrink-records").json() == []


def test_shrink_close_and_note_without_a_mac_are_bad_mac(identity_server):
    station = identity_server["station"]
    for path, body in (("shrink-close", {"serial": SERIAL}),
                       ("shrink-note", {"serial": SERIAL, "note": "why"})):
        response = station.post(f"/api/v1/agent/{path}", json=body)
        assert response.status_code == 400 and response.json()["code"] == "bad_mac", path


# --- manifest / files: הזהות ההפוכה — כתובת → חכירה → מכונה רשומה ------------


def test_manifest_and_files_need_a_lease_of_a_registered_machine(identity_server):
    """הסוכן אינו שולח MAC כאן. שלוש כתובות: בלי חכירה בכלל → לא ניתן
    לבדוק; חכירה של MAC לא רשום → סירוב; חכירה של מכונה רשומה → 200."""
    setup_classroom(identity_server)
    write_leases(identity_server["leases"],
                 {MAC1: STATION_IP, BUILD: STATION_IP,
                  UNREGISTERED_MAC: LEASED_STRANGER_IP})
    leased_stranger = identity_server["app"]
    from fastapi.testclient import TestClient
    leased_stranger = TestClient(leased_stranger, client=(LEASED_STRANGER_IP, 40009))

    manifest, part = f"/api/v1/images/{IMAGE}/manifest", f"/api/v1/images/{IMAGE}/files/p1.esp.pcl.zst"
    refused_by_name(identity_server["stranger"].get(manifest), "identity_unverifiable")
    refused_by_name(identity_server["stranger"].get(part), "identity_unverifiable")
    refused_by_name(leased_stranger.get(manifest), "identity_refused")
    refused_by_name(leased_stranger.get(part), "identity_refused")
    rows = journal_where(identity_server, "identity_refused")
    assert any(UNREGISTERED_MAC in d and LEASED_STRANGER_IP in d and "(manifest)" in d
               for d in rows), rows

    ok = identity_server["station"].get(manifest)
    assert ok.status_code == 200 and ok.json()["id"] == IMAGE
    assert identity_server["station"].get(part).status_code == 200


def test_switching_the_check_off_opens_manifest_and_pulls_to_everyone(identity_server):
    setup_classroom(identity_server)
    assert identity_server["admin"].post(
        "/api/console/settings", json={"identity_check": False}).status_code == 200
    stranger = identity_server["stranger"]
    assert stranger.get(f"/api/v1/images/{IMAGE}/manifest").status_code == 200
    assert stranger.post("/api/v1/agent/pulls",
                         json={"mac": MAC1, "image_id": IMAGE, **CREDS}).status_code == 200
    assert stranger.post("/api/v1/agent/disk-event", json=_event(MAC1)).status_code == 200


def test_an_unreadable_leases_file_closes_manifest_by_name(identity_server):
    setup_classroom(identity_server)
    identity_server["leases"].unlink()
    refused_by_name(identity_server["station"].get(f"/api/v1/images/{IMAGE}/manifest"),
                    "identity_unverifiable")


# --- GET state/drivers: הסוכן שולח ?mac= אחרי שקיבל חכירה -------------------


@pytest.mark.parametrize("path,where", [
    (f"/api/v1/agent/state?mac={MAC1}", "state"),
    (f"/api/v1/agent/drivers?mac={MAC1}", "drivers"),
])
def test_agent_reads_are_gated_by_the_declared_mac(identity_server, path, where):
    setup_classroom(identity_server)
    refused_by_name(identity_server["stranger"].get(path))
    assert any(f"({where})" in d for d in journal_where(identity_server,
                                                         "identity_refused"))
    assert identity_server["station"].get(path).status_code == 200


@pytest.mark.parametrize("path", [
    f"/api/v1/agent/state?mac={MAC1}",
    f"/api/v1/agent/drivers?mac={MAC1}",
])
def test_agent_reads_are_unverifiable_without_a_lease(identity_server, path):
    setup_classroom(identity_server)
    write_leases(identity_server["leases"], {})
    refused_by_name(identity_server["station"].get(path), "identity_unverifiable")


@pytest.mark.parametrize("path", [
    f"/api/v1/agent/state?mac={MAC1}",
    f"/api/v1/agent/drivers?mac={MAC1}",
])
def test_switching_identity_off_opens_agent_reads(identity_server, path):
    setup_classroom(identity_server)
    assert identity_server["admin"].post(
        "/api/console/settings", json={"identity_check": False}).status_code == 200
    assert identity_server["stranger"].get(path).status_code == 200


# --- capture: האסימון וגם זהות הרשת, לפני קריאת הגוף -------------------------


ACCEPTED = {"files": 200, "manifest": 202}


def _capture_request(server, client, task_id, kind):
    headers = {TOKEN_HEADER: task_token(server, task_id)}
    if kind == "files":
        return client.put(f"/api/v1/capture/{task_id}/files/p1.esp.pcl.zst",
                          content=PART_A, headers=headers)
    folder = staging_dir(server["ctx"].library.root, task_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "p1.esp.pcl.zst").write_bytes(PART_A)
    (folder / "p3.windows.pcl.zst").write_bytes(PART_B)
    # ‏#1470: מה שההעלאה הייתה רושמת — ה-sha256 שחושב בזמן הקבלה.
    (folder / "p1.esp.pcl.zst.upload-sha256").write_text(sha(PART_A), encoding="ascii")
    (folder / "p3.windows.pcl.zst.upload-sha256").write_text(sha(PART_B), encoding="ascii")
    return client.put(f"/api/v1/capture/{task_id}/manifest",
                      content=json.dumps(manifest_for()).encode(), headers=headers)


@pytest.mark.parametrize("kind,where", [
    ("files", "capture-files"), ("manifest", "capture-manifest")])
def test_capture_with_the_task_token_still_needs_the_task_macs_lease(
        identity_server, kind, where):
    setup_build_machine(identity_server, BUILD)
    created = make_task(identity_server, BUILD).json()
    refused_by_name(_capture_request(identity_server, identity_server["stranger"],
                                     created["id"], kind))
    assert any(f"({where})" in d for d in journal_where(identity_server,
                                                         "identity_refused"))
    # ‏#1470: המניפסט עונה 202 — התקבל, והאימות רץ ברקע.
    assert _capture_request(identity_server, identity_server["station"],
                            created["id"], kind).status_code == ACCEPTED[kind]


@pytest.mark.parametrize("kind", ["files", "manifest"])
def test_capture_is_unverifiable_when_the_task_mac_has_no_lease(identity_server, kind):
    ids = setup_classroom(identity_server)
    created = make_task(identity_server, ids["mac2"]).json()
    refused_by_name(_capture_request(identity_server, identity_server["station"],
                                     created["id"], kind), "identity_unverifiable")


@pytest.mark.parametrize("kind", ["files", "manifest"])
def test_switching_identity_off_opens_capture_with_a_valid_token(identity_server, kind):
    setup_build_machine(identity_server, BUILD)
    created = make_task(identity_server, BUILD).json()
    assert identity_server["admin"].post(
        "/api/console/settings", json={"identity_check": False}).status_code == 200
    assert _capture_request(identity_server, identity_server["stranger"],
                            created["id"], kind).status_code == ACCEPTED[kind]


# --- direct.py: המניפסט החי --------------------------------------------------


def test_the_live_manifest_needs_the_token_and_the_identity(identity_server):
    """אסימון נכון מכתובת זרה → 403 זהות, והמשימה **לא** נכשלת; מכתובת
    החכירה → 200. וה-GET של המניפסט החי עובר באותו שומר הפוך."""
    setup_build_machine(identity_server, BUILD)
    write_leases(identity_server["leases"],
                 {MAC1: STATION_IP, BUILD: STATION_IP, CLONER1: STATION_IP})
    assert hello(identity_server["station"], BUILD).status_code == 200
    assert identity_server["admin"].post("/api/console/machines", json={
        "mac": CLONER1, "name": "shich-1", "group_id": "grp_CLONERS"}).status_code == 200
    cloner_hello(identity_server["station"], CLONER1, ["S1", "S2"])
    opened = _open_direct(identity_server["admin"], [{"mac": CLONER1, "ports": [1, 2]}])
    assert opened.status_code == 200, opened.text
    task_id = opened.json()["task_id"]
    token = identity_server["ctx"].conn.execute(
        "SELECT token FROM tasks WHERE id = ?", (task_id,)).fetchone()["token"]
    payload = json.dumps(_live_manifest()).encode()

    forged = identity_server["stranger"].put(
        f"/api/v1/direct/{task_id}/manifest", content=payload, headers={TOKEN_HEADER: token})
    refused_by_name(forged)
    state = identity_server["ctx"].conn.execute(
        "SELECT state FROM tasks WHERE id = ?", (task_id,)).fetchone()["state"]
    assert state in ("pending", "running"), state
    live = f"/api/v1/images/{opened.json()['image_id']}/manifest"
    assert identity_server["station"].get(live).status_code == 404     # טרם הגיע

    genuine = identity_server["station"].put(
        f"/api/v1/direct/{task_id}/manifest", content=payload, headers={TOKEN_HEADER: token})
    assert genuine.status_code == 200, genuine.text
    refused_by_name(identity_server["stranger"].get(live), "identity_unverifiable")
    assert identity_server["station"].get(live).status_code == 200


# --- הסוכן: מה הוא שולח היום -------------------------------------------------


def test_the_agent_sends_a_mac_on_every_gated_post_and_none_on_manifest_gets():
    """הראיה שההכרעה על manifest/files נכונה: ‏`http_get` ב-common.sh אינו
    מוסיף MAC ולא כותרת, וכל POST שנחסם לפי MAC מוצהר אכן נושא אותו."""
    root = flat_lib()
    common = (root / "common.sh").read_text(encoding="utf-8")
    assert "http_get() {" in common and "mac" not in common.split("http_get() {")[1].split("}")[0].lower()
    # recovery.sh and classround.sh (the text login and the text class round)
    # were removed on 11/10 -- GUI only; their POSTs went with them.
    for name, needle in (("pull.sh", '"mac":"%s"'), ("smartgate.sh", '"mac":"%s"'),
                         ("shrinkmem.sh", '"mac":"%s"')):
        assert needle in (root / name).read_text(encoding="utf-8"), name
