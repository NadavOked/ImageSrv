"""#1928 (S4, 20/10): a round the server closed because the image's partition
file was gone told the cloner only "השרת סגר את הסבב -- הכתיבה הופסקה". The
operator could not know the image itself was missing, and could try again
and again. Now the reason travels: the sender names the image, the session's
failed_reason rides on the `not_open` answer, and the drawer's error says it.

And the lock: an image a running pull (unicast) uses is refused deletion,
as the broadcast and the room round already were.
"""

from __future__ import annotations

import shutil

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix, sh
from test_closed_session_report import _payload, _session
from test_directsend import JQ_STUB
from test_drawer_progress import drawer_box
from conftest import setup_classroom
from test_sender import MANIFEST_256, Recorder, free_ports, wait_for, write_image  # noqa: F401 (fixture)
from test_timeouts import make_stubs, run_sh
from test_server_room import CLONER1, cloner_hello, room_body, room_server  # noqa: F401 (fixture)

from server.agent import reports
from server.library.images import ImageLibrary
from server.rounds.sender import SenderEngine

pytest.importorskip("fastapi")

REASON = "קובץ מחיצה חסר באימג' img_8da804: p4.recovery.pcl.zst — האימג' חסר או פגום בשרת"


# --- the server ------------------------------------------------------------------

def test_the_sender_names_the_image_and_says_it_is_missing(tmp_path, free_ports):  # noqa: F811
    write_image(tmp_path, MANIFEST_256)
    (tmp_path / MANIFEST_256["id"] / "p3.win.pcl.zst").unlink()
    engine = SenderEngine(ImageLibrary(tmp_path), runner=Recorder())
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: engine.status()["state"] == "failed")
    error = engine.status()["error"]
    assert "img_7f3a91" in error and "p3.win.pcl.zst" in error and "חסר או פגום" in error, error


def test_a_failed_round_answers_not_open_with_its_reason(server):
    mac = _session(server, "ses_gone")
    conn = server["app"].state.ctx.conn
    assert server["ctx"].store.fail("ses_gone", REASON)
    answer = reports.ingest(conn, _payload("ses_gone", mac))
    assert answer["code"] == "not_open" and answer["reason"] == REASON, answer


def test_a_closed_round_has_no_reason_to_give(server):
    mac = _session(server, "ses_shut", state="closed")
    answer = reports.ingest(server["app"].state.ctx.conn, _payload("ses_shut", mac))
    assert answer["code"] == "not_open" and "reason" not in answer, answer


def test_an_image_a_running_pull_uses_cannot_be_deleted(server):
    admin, ctx = server["admin"], server["ctx"]
    info = setup_classroom(server)
    ctx.store.open(group_id=info["group"], image_id="img_7f3a91", prefix="PULL", expected_clients=1,
                   opened_by="test", roster=[info["mac1"]], kind="unicast")
    r = admin.post("/api/console/images/img_7f3a91/delete", json={"confirm_name": "Office 2024 Standard"})
    assert r.status_code == 409, r.text
    assert ctx.library.get("img_7f3a91") is not None


def test_an_image_no_round_uses_is_still_deleted(server):
    """The guard of the guard: the lock does not refuse everything."""
    r = server["admin"].post("/api/console/images/img_7f3a91/delete",
                             json={"confirm_name": "Office 2024 Standard"})
    assert r.status_code == 200, r.text


# --- the agent -------------------------------------------------------------------

@requires_native(("bash", BASH))
def test_the_reporter_keeps_the_reason_before_the_mark(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    stubs = {} if shutil.which("jq") else {"jq": JQ_STUB}
    (tmp_path / "reply.json").write_text('{"ok":false,"code":"not_open","reason":"' + REASON + '"}',
                                         encoding="utf-8", newline="\n")
    out = run_sh(
        make_stubs(tmp_path / "stubs", stubs)
        + f"export RUN_DIR={posix(run)!r}; "
        f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/jsonq.sh; . {posix(AGENT)}/lib/core/progress.sh; "
        "build_progress() { echo '{}'; }; "
        f"http_post_json() {{ cat {posix(tmp_path / 'reply.json')!r}; return 22; }}; "
        'progress_loop ses_1 78:ac:c0:9b:11:c2 http://s; echo "rc=$?"')
    assert out.strip().endswith("rc=0"), out
    assert (run / "session-closed").read_text().strip() == "ses_1"
    assert (run / "session-closed.why").read_text(encoding="utf-8").strip() == REASON


@requires_native(("bash", BASH))
@pytest.mark.parametrize("why, want", [
    (REASON, f"השרת סגר את הסבב: {REASON} -- הכתיבה הופסקה"),
    (None, "השרת סגר את הסבב -- הכתיבה הופסקה"),          # no reason given: the old words
])
def test_the_drawer_says_why_the_server_closed_the_round(tmp_path, why, want):
    run, prelude = drawer_box(tmp_path, disks=("sda",))
    (run / "session-closed").write_text("ses_1\n")
    if why is not None:
        (run / "session-closed.why").write_text(why + "\n", encoding="utf-8", newline="\n")
    out = sh(prelude + 'run_restore_drawers multicast http://s img m.json sda; echo "rc=$?"')
    assert out.strip().endswith("rc=1"), out
    assert (run / "targets" / "sda" / "error").read_text(encoding="utf-8").strip() == want


def test_a_new_round_forgets_the_last_reason():
    agent = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    body = agent[agent.index("do_restore_drawers() {"):agent.index("do_restore() {")]
    assert '"$RUN_DIR/session-closed.why"' in body.split("progress_loop", 1)[0]


# --- #1928 (FI O1, 21/10 03:55): the operator stopped the round ------------------
# "עצירת הסבב" from the console mid-wave; the cloner said only "מחיצה 3: השרת סגר
# את הסבב -- הכתיבה הופסקה" -- the operator at the cloner could not know a person
# at the console stopped it. A wave closed by a signed-in user now says who.

def test_a_room_round_the_operator_stopped_says_who_stopped_it(room_server):  # noqa: F811
    deploy, anon = room_server["deploy"], room_server["anon"]
    assert deploy.post("/api/console/room",
                       json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]})).status_code == 200
    wave = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    assert deploy.post("/api/console/room/close",
                       json={"confirm_name": "Office 2024 Standard"}).json()["ok"] is True
    r = anon.post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": CLONER1, "state": "writing",
        "targets": [{"dev": "sda", "bytes_written": 1, "bytes_total": 2, "state": "writing"}]})
    assert r.status_code == 200, r.text
    answer = r.json()
    assert answer["code"] == "not_open" and answer.get("reason") == "המפעיל labtech עצר את הסבב", answer


def test_a_session_a_user_closed_says_who(server):
    """The console's own "stop" (any session) carries the user."""
    mac = _session(server, "ses_by_hand")
    assert server["ctx"].store.close("ses_by_hand", "nadav")
    answer = reports.ingest(server["app"].state.ctx.conn, _payload("ses_by_hand", mac))
    assert answer["code"] == "not_open" and answer.get("reason") == "המפעיל nadav עצר את הסבב", answer


def test_a_session_the_server_closed_by_itself_gives_no_reason(server):
    """The guard: the server closing a wave by itself (a finished wave,
    `store.close(..., "")`) gives no reason -- the old words stay, nothing is made up."""
    mac = _session(server, "ses_by_server")
    assert server["ctx"].store.close("ses_by_server", "")
    answer = reports.ingest(server["app"].state.ctx.conn, _payload("ses_by_server", mac))
    assert answer["code"] == "not_open" and "reason" not in answer, answer
