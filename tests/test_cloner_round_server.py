"""16/10 — a cloning machine opens a room round on its OWN drawers, from its
own screen, with no user name and password (Nadav: being at the machine is
the authorization).

The route is narrow on purpose (``server/cloner_round.py``): only a
registered cloner, identified like every agent route (#855/#997); only its
own attached, mapped drawers -- the body carries ports, never MACs; only an
image allowed for this machine; never while another round is active. It
opens and nothing else: no close, no start, no power-off. Each refusal below
is a role or input that must stay out.

Security review (16/10): it fails CLOSED -- identity checking off or not
installed is a refusal; the screen must be open (a single-use, expiring
nonce the agent asks for when it shows the screen); one attempt per machine
per RATE_S; drawers and images from the server's own records, never from
the request; every attempt journaled.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from conftest import MANIFEST_256, hello_body, write_image
from server import cloner_round, room
from server.db import set_setting
from test_machine_identity import (BUILD, MAC1, MAC2, identity_server,  # noqa: F401
                                   journal_details)

URL = "/api/v1/agent/cloner-round"
IMG_256 = "img_7f3a91"     # conftest MANIFEST_256
DRAWER = 256_060_514_304


def register(srv, mac: str, group: str, name: str) -> None:
    r = srv["admin"].post("/api/console/machines",
                          json={"mac": mac, "name": name, "group_id": group})
    assert r.status_code == 200, r.text


def drawers_hello(srv, mac: str = MAC1, serials=("S1", "S2", "S3")) -> None:
    """The cloner's hello with its drawers: port i+1, a serial, 256 GB."""
    body = hello_body(mac)
    body["disks"] = [
        {"dev": f"sd{chr(ord('a') + i)}", "size_bytes": DRAWER, "port": i + 1,
         "model": "Drawer SSD", "serial": s, "removable": False, "scheme": "gpt",
         "has_data": False}
        for i, s in enumerate(serials)]
    r = srv["station"].post("/api/v1/agent/hello", json=body)
    assert r.status_code == 200, r.text


def cloner(srv) -> None:
    register(srv, MAC1, "grp_CLONERS", "1")
    drawers_hello(srv)


class Now:
    t = 1000.0


@pytest.fixture(autouse=True)
def now(monkeypatch):
    """The route's clock (rate limit, nonce expiry), moved by the tests."""
    clock = Now()
    monkeypatch.setattr(cloner_round, "_now", lambda: clock.t)
    _CLOCKS[:] = [clock]
    yield clock
    _CLOCKS.clear()


def nonce(srv, client: str = "station", mac: str = MAC1):
    return srv[client].post(URL + "/nonce", json={"mac": mac})


def ask(srv, client: str = "station", fresh: bool = True, **body):
    """One open attempt, as the agent makes it: a nonce first (fresh=False:
    none is asked for), and the clock past the rate limit."""
    payload = {"mac": MAC1, "image_id": IMG_256, "ports": [1, 3], **body}
    if fresh and "nonce" not in body:
        got = nonce(srv, client, payload["mac"])
        if got.status_code == 200:
            payload["nonce"] = got.json()["nonce"]
    _advance(cloner_round.RATE_S + 1)
    return srv[client].post(URL, json=payload)


_CLOCKS: list = []


def _advance(seconds: float) -> None:
    for c in _CLOCKS:
        c.t += seconds


def no_round(srv) -> bool:
    return room.active_round(srv["ctx"].conn) is None


# --- what is allowed ---------------------------------------------------------------

def test_a_cloner_opens_a_round_on_its_own_chosen_drawers(identity_server):
    srv = identity_server
    cloner(srv)
    r = ask(srv)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True and r.json()["id"].startswith("room_")
    row = room.active_round(srv["ctx"].conn)
    assert json.loads(row["target_slots_json"]) == [{"mac": MAC1, "ports": [1, 3]}]
    assert row["target_drives"] == 2 and row["image_id"] == IMG_256
    assert row["opened_by"] == "מחשב שיכפול 1"
    # the machine's next hello is the ordinary round answer, for ports 1 and 3 only
    answer = srv["station"].post("/api/v1/agent/hello", json={
        **hello_body(MAC1), "disks": json.loads(srv["ctx"].conn.execute(
            "SELECT disks_json FROM net_devices WHERE mac = ?", (MAC1,)).fetchone()["disks_json"])}).json()
    assert answer["session"] is not None and answer["session"]["target_ports"] == [1, 3], answer


def test_the_journal_says_which_cloner_opened_it(identity_server):
    srv = identity_server
    cloner(srv)
    assert ask(srv).status_code == 200
    lines = journal_details(srv, "room_open_cloner")
    assert len(lines) == 1, lines
    user, detail = lines[0]
    assert user == "מחשב שיכפול 1"
    assert "נפתח ממחשב שיכפול 1" in detail and MAC1 in detail and "מגירות 1,3" in detail
    # the round itself is journaled as every room round, in the machine's name
    assert [u for u, _d in journal_details(srv, "room_open")] == ["מחשב שיכפול 1"]


def test_another_cloner_is_not_written(identity_server):
    srv = identity_server
    cloner(srv)
    register(srv, MAC2, "grp_CLONERS", "2")
    assert ask(srv).status_code == 200
    assert room._selected_ports(room.active_round(srv["ctx"].conn), MAC2) == set()


def test_a_foreign_mac_in_the_body_is_ignored_the_target_is_the_caller(identity_server):
    """The body has no MAC list to smuggle: target_slots is built from the
    verified caller alone, whatever else the body carries."""
    srv = identity_server
    cloner(srv)
    register(srv, MAC2, "grp_CLONERS", "2")
    r = ask(srv, target_slots=[{"mac": MAC2, "ports": [1]}])
    assert r.status_code == 200, r.text
    assert json.loads(room.active_round(srv["ctx"].conn)["target_slots_json"]) == \
        [{"mac": MAC1, "ports": [1, 3]}]


# --- who is refused ----------------------------------------------------------------

def test_another_address_claiming_the_cloner_is_refused(identity_server):
    srv = identity_server
    cloner(srv)
    r = ask(srv, client="stranger")
    assert r.status_code == 403 and r.json()["code"] == "identity_refused", r.text
    assert no_round(srv)


def test_a_mac_with_no_lease_here_is_refused(identity_server):
    """MAC2 is a registered cloner -- but the caller's address is not its lease."""
    srv = identity_server
    cloner(srv)
    register(srv, MAC2, "grp_CLONERS", "2")
    r = ask(srv, mac=MAC2)
    assert r.status_code == 403 and r.json()["code"].startswith("identity_"), r.text
    assert no_round(srv)


def test_an_unregistered_mac_is_refused(identity_server):
    srv = identity_server
    r = ask(srv)
    assert r.status_code == 403 and r.json()["code"] == "unknown_mac", r.text
    assert no_round(srv)


@pytest.mark.parametrize("mac,group", [(BUILD, "grp_BUILD"), (MAC1, "grp_LAB1")])
def test_a_build_machine_or_a_student_station_is_refused(identity_server, monkeypatch, mac, group):
    from server import capabilities
    monkeypatch.setattr(capabilities, "CLASSROOMS", True)   # a classroom group needs v1.2 on
    srv = identity_server
    register(srv, MAC2, "grp_CLONERS", "2")          # a cloner exists: the refusal is about the caller
    if group == "grp_LAB1":
        assert srv["admin"].post("/api/console/groups", json={
            "id": "grp_LAB1", "label": "כיתה LAB1", "role": "classroom"}).status_code == 200
    register(srv, mac, group, "05")
    drawers_hello(srv, mac)
    r = ask(srv, mac=mac)
    assert r.status_code == 403 and r.json()["code"] == "not_a_cloner", r.text
    assert no_round(srv)
    assert journal_details(srv, "cloner_round_refused")


# --- what is refused -----------------------------------------------------------------

@pytest.mark.parametrize("ports", [[], None, ["1"], [True], "1,3", [1.5]])
def test_a_malformed_drawer_choice_is_refused(identity_server, ports):
    srv = identity_server
    cloner(srv)
    r = ask(srv, ports=ports)
    assert r.status_code == 400 and r.json()["code"] == "bad_ports", r.text
    assert no_round(srv)


@pytest.mark.parametrize("ports", [[4], [1, 1], [9]])
def test_a_drawer_that_is_not_attached_or_mapped_is_refused(identity_server, ports):
    srv = identity_server
    cloner(srv)
    r = ask(srv, ports=ports)
    assert r.status_code == 400 and r.json()["code"] == "bad_round", r.text
    assert no_round(srv)


def test_a_drawer_with_no_serial_is_refused_by_name(identity_server):
    srv = identity_server
    register(srv, MAC1, "grp_CLONERS", "1")
    drawers_hello(srv, serials=("S1", "", "S3"))
    r = ask(srv, ports=[2])
    assert r.status_code == 400 and "serial" in r.json()["error"], r.text
    assert no_round(srv)


@pytest.mark.parametrize("image", ["img_nothere", ""])
def test_an_image_not_allowed_for_this_machine_is_refused(identity_server, image):
    # #1863: an image too big for the drawers is not "not allowed" -- it is
    # refused by size, "fit" (test_the_console_and_the_cloner_screen_decide_alike_by_size).
    srv = identity_server
    cloner(srv)
    r = ask(srv, image_id=image)
    assert r.status_code in (400, 403), r.text
    assert r.json()["code"] in ("image_not_allowed", "bad_image"), r.text
    assert no_round(srv)


def test_no_second_round_while_one_is_active(identity_server):
    srv = identity_server
    cloner(srv)
    assert ask(srv).status_code == 200
    first = room.active_round(srv["ctx"].conn)["id"]
    r = ask(srv, ports=[2])
    assert r.status_code == 409 and r.json()["code"] == "round_conflict", r.text
    assert room.active_round(srv["ctx"].conn)["id"] == first


def test_a_failed_round_does_not_block_the_next_one(identity_server):
    """17/10 (ברזל): סבב שנכשל ("אף מחשב לא הצטרף לשידור תוך 180 שניות") אינו
    חוסם פתיחה חדשה מהמסך של מחשב השיכפול, והסבב החדש הוא הנראה. הכשל אינו
    נמחק (עיקרון 4): השורה הישנה נשארת `failed` עם הסיבה."""
    srv = identity_server
    ctx = srv["ctx"]
    cloner(srv)
    assert ask(srv).status_code == 200
    row = room.active_round(ctx.conn)
    reason = "אף מחשב לא הצטרף לשידור תוך 180 שניות"
    wave = row["wave_session_id"]
    # כמו on_sender_event ב-app.py על send_failed: הגל והסבב נכשלים יחד
    assert ctx.store.fail(wave, reason, related=lambda: room.fail_wave(
        ctx.conn, wave, reason, commit=False))
    assert room.visible_round(ctx.conn)["state"] == "failed"

    r = ask(srv, ports=[2])
    assert r.status_code == 200, r.text
    assert r.json()["id"] != row["id"]
    assert room.visible_round(ctx.conn)["id"] == r.json()["id"]
    old = ctx.conn.execute("SELECT state, failed_reason FROM room_rounds WHERE id = ?",
                           (row["id"],)).fetchone()
    assert (old["state"], old["failed_reason"]) == ("failed", reason)


def test_the_route_opens_and_nothing_else(identity_server):
    """No close, no start, no power-off from the agent port without a session."""
    srv = identity_server
    cloner(srv)
    assert ask(srv).status_code == 200
    for path in (URL + "/close", URL + "/start"):
        assert srv["station"].post(path, json={"mac": MAC1}).status_code in (404, 405)
    for path in ("/api/console/room/close", "/api/console/room/start", "/api/console/room/wake"):
        assert srv["station"].post(path, json={"confirm_name": "x"}).status_code == 401, path
    assert room.active_round(srv["ctx"].conn) is not None


# --- fails closed (security review, 16/10) -------------------------------------------------

def test_identity_checking_off_is_a_refusal_not_a_pass(identity_server):
    srv = identity_server
    cloner(srv)
    set_setting(srv["ctx"].conn, "identity_check", "false")
    assert nonce(srv).status_code == 403
    r = ask(srv, nonce="x" * 32)
    assert r.status_code == 403 and r.json()["code"] == "identity_unverifiable", r.text
    assert no_round(srv)
    assert any("identity check is off" in d for _u, d in journal_details(srv, "cloner_round_refused"))


def test_a_server_with_no_identity_guard_installed_refuses(server):
    """The plain test server has no leases (ctx.leases is None): every other agent
    route lets that through; this one does not."""
    register(server, MAC1, "grp_CLONERS", "1")
    r = server["anon"].post(URL + "/nonce", json={"mac": MAC1})
    assert r.status_code == 403 and r.json()["code"] == "identity_unverifiable", r.text
    r = server["anon"].post(URL, json={"mac": MAC1, "nonce": "x", "image_id": IMG_256, "ports": [1]})
    assert r.status_code == 403 and room.active_round(server["ctx"].conn) is None


def test_a_lease_mismatch_gets_no_nonce(identity_server):
    srv = identity_server
    cloner(srv)
    r = nonce(srv, client="stranger")
    assert r.status_code == 403 and r.json()["code"] == "identity_refused", r.text


@pytest.mark.parametrize("how", ["none", "wrong", "expired", "reused", "other_machine"])
def test_without_the_screens_live_nonce_nothing_opens(identity_server, now, how):
    srv = identity_server
    cloner(srv)
    if how == "none":
        r = ask(srv, fresh=False)
    elif how == "wrong":
        nonce(srv)
        r = ask(srv, nonce="0" * 32)
    elif how == "expired":
        n = nonce(srv).json()["nonce"]
        now.t += cloner_round.NONCE_TTL_S + 1
        r = ask(srv, nonce=n)
    elif how == "reused":
        n = nonce(srv).json()["nonce"]
        first = ask(srv, nonce=n, ports=[9])               # spends it, refused for the port
        assert first.status_code == 400, first.text
        r = ask(srv, nonce=n)
    else:                                                  # a nonce is the machine's own
        register(srv, BUILD, "grp_CLONERS", "9")
        drawers_hello(srv, BUILD)
        n = nonce(srv, mac=BUILD).json()["nonce"]
        r = ask(srv, nonce=n)
    assert r.status_code == 403 and r.json()["code"] == "bad_nonce", r.text
    assert no_round(srv)


def test_one_attempt_per_machine_in_rate_s(identity_server, now):
    srv = identity_server
    cloner(srv)
    n1 = nonce(srv).json()["nonce"]
    r1 = srv["station"].post(URL, json={"mac": MAC1, "nonce": n1, "image_id": IMG_256, "ports": [9]})
    assert r1.status_code == 400, r1.text
    n2 = nonce(srv).json()["nonce"]
    r2 = srv["station"].post(URL, json={"mac": MAC1, "nonce": n2, "image_id": IMG_256, "ports": [1]})
    assert r2.status_code == 429 and r2.json()["code"] == "rate_limited", r2.text
    assert no_round(srv)
    now.t += cloner_round.RATE_S
    r3 = srv["station"].post(URL, json={"mac": MAC1, "nonce": n2, "image_id": IMG_256, "ports": [1]})
    assert r3.status_code == 200, r3.text


def test_drawers_and_images_come_from_the_server_not_the_request(identity_server):
    """A body that brings its own disks does not make port 4 attached, nor a big image fit."""
    srv = identity_server
    cloner(srv)
    fake = [{"dev": "sdd", "port": 4, "serial": "X4", "size_bytes": 10 ** 13, "removable": False}]
    r = ask(srv, ports=[4], disks=fake, drawer_slots=["sata:4"])
    assert r.status_code == 400 and r.json()["code"] == "bad_round", r.text
    # #1863: the size is decided against the saved drawers (256 GB), not the body's 10 TB
    big = {**MANIFEST_256, "id": "img_b16400", "name": "Big", "family": 500,
           "source_disk_bytes": 500_107_862_016, "min_target_bytes": 500_107_862_016,
           "partitions": [MANIFEST_256["partitions"][0],
                          {**MANIFEST_256["partitions"][1], "size_bytes": 400_000_000_000}]}
    write_image(srv["ctx"].library.root, big)
    r = ask(srv, image_id=big["id"], disks=fake)
    assert r.status_code == 409 and r.json()["code"] == "fit", r.text
    assert r.json()["error"] == "דיסק 1 במחשב 1 הוא 256GB, האימג' צריך 401GB", r.text
    assert no_round(srv)


def test_every_attempt_is_journaled(identity_server):
    srv = identity_server
    cloner(srv)
    ask(srv, ports=[9])
    ask(srv, fresh=False)
    assert ask(srv).status_code == 200
    refused = [d for _u, d in journal_details(srv, "cloner_round_refused")]
    assert len(refused) == 2 and "bad_round" in refused[0] and "bad_nonce" in refused[1], refused
    assert len(journal_details(srv, "room_open_cloner")) == 1


# --- #1863: one fit decision, by size, for the console and the cloner screen ---------
#
# VCLONE-2 in the lab (20/10): two 64 GiB drawers and one 40 GiB drawer. test2
# (img_8da804) was captured from a 500 GB disk -- `family` 500 -- and shrunk to
# a ~60.6 GB layout. The console opened it on the 64 GiB drawers; the cloner
# screen refused it with "image_not_allowed", because `allowed_for_disks` also
# filtered by family. Nadav, 20/10: the decision is the real size only
# (`required_bytes` + min_free against the chosen drawer), as the console does
# (#953); the family is a display label.

GIB = 1 << 30
VCLONE2 = (64 * GIB, 64 * GIB, 40 * GIB)
TEST2 = {
    **MANIFEST_256, "id": "img_8da804", "name": "test2", "family": 500,
    "source_disk_bytes": 500_107_862_016, "min_target_bytes": 500_107_862_016,
    "partitions": [MANIFEST_256["partitions"][0],
                   {**MANIFEST_256["partitions"][1], "size_bytes": 60_000_000_000,
                    "used_bytes": 30_000_000_000}],
}


def vclone2(srv, sizes=VCLONE2) -> dict:
    """test2 in the library, and cloner "1" whose last hello reported these
    drawers (ports 1..). Returns that hello's answer."""
    write_image(srv["ctx"].library.root, TEST2)
    register(srv, MAC1, "grp_CLONERS", "1")
    body = hello_body(MAC1)
    body["disks"] = [
        {"dev": f"sd{chr(ord('a') + i)}", "size_bytes": size, "port": i + 1,
         "model": "Virtual disk", "serial": f"V{i + 1}", "removable": False,
         "scheme": "gpt", "has_data": False}
        for i, size in enumerate(sizes)]
    r = srv["station"].post("/api/v1/agent/hello", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def console_decides(srv, ports) -> tuple[bool, str | None]:
    """(opened, reason) from the console's room screen. An opened round is
    failed at once, so the cloner screen is then asked about the same room."""
    r = srv["admin"].post("/api/console/room", json={
        "image_id": TEST2["id"], "target_drives": len(ports),
        "target_slots": [{"mac": MAC1, "ports": ports}]})
    if r.status_code != 200:
        return False, r.json()["detail"]
    ctx = srv["ctx"]
    wave = room.active_round(ctx.conn)["wave_session_id"]
    assert ctx.store.fail(wave, "test", related=lambda: room.fail_wave(
        ctx.conn, wave, "test", commit=False))
    return True, None


@pytest.mark.parametrize("ports", [[1], [1, 2], [3], [2, 3]])
def test_the_console_and_the_cloner_screen_decide_alike_by_size(identity_server, ports):
    srv = identity_server
    vclone2(srv)
    console = console_decides(srv, ports)
    r = ask(srv, image_id=TEST2["id"], ports=ports)
    screen = (r.status_code == 200, None if r.status_code == 200 else r.json()["error"])
    assert screen == console, r.text
    # and it is the size decision: ~60.6 GB goes on 64 GiB, not on 40 GiB
    assert console[0] is (3 not in ports)
    if not console[0]:
        assert r.status_code == 409 and r.json()["code"] == "fit", r.text
        assert r.json()["error"] == "דיסק 3 במחשב 1 הוא 43GB, האימג' צריך 61GB"


def test_an_image_from_a_500_disk_is_offered_where_a_drawer_holds_it(identity_server):
    """The list the cloner screen and the build machine show: by size, not family."""
    srv = identity_server
    assert TEST2["id"] in vclone2(srv)["allowed_images"]
    state = srv["station"].get("/api/v1/agent/state", params={"mac": MAC1}).json()
    assert TEST2["id"] in [i["id"] for i in state["allowed_images"]]


def test_an_image_that_fits_no_drawer_is_not_offered(identity_server):
    srv = identity_server
    assert TEST2["id"] not in vclone2(srv, sizes=(40 * GIB,))["allowed_images"]


@pytest.mark.parametrize("used", [50_000_000_000, None], ids=["10GB-free", "unmeasured"])
def test_the_offered_list_counts_the_minimum_free_space(identity_server, used):
    """"Fits" in the list is the console's number: the layout AND min_free_gb free
    on C: (#1354). A 256-family image whose ~60.6 GB layout fits a 64 GiB drawer,
    with only 10 GB free on C:, needs ~72 GB with the default 20 -- not offered;
    C: never measured (#1494) cannot be checked -- not offered either (principle
    5). With the rule off (0) both are."""
    srv = identity_server
    tight = {**TEST2, "id": "img_7e1600", "name": "tight", "family": 256,
             "partitions": [TEST2["partitions"][0],
                            {**TEST2["partitions"][1], "used_bytes": used}]}
    write_image(srv["ctx"].library.root, tight)
    assert tight["id"] not in vclone2(srv, sizes=(64 * GIB,))["allowed_images"]
    set_setting(srv["ctx"].conn, "min_free_gb", "0")
    answer = srv["station"].post("/api/v1/agent/hello", json={
        **hello_body(MAC1), "disks": json.loads(srv["ctx"].conn.execute(
            "SELECT disks_json FROM net_devices WHERE mac = ?", (MAC1,)).fetchone()["disks_json"])}).json()
    assert tight["id"] in answer["allowed_images"]
