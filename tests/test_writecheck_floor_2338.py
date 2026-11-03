"""#2338 (Nadav 03/11) -- the write check's speed floor is a console setting.

Until now ``WRITECHECK_MIN_MBPS`` was 50 MB/s fixed in the agent (#1366): on
metal 03/11 (cloner 2, room_2c946f3b) a TOSHIBA DT01ACA0 measured 33 MB/s and
went red by it. Now it is ``settings.writecheck_min_mbps`` (admin only, an
integer 10-500, anything else 400 before a key is written -- as #1794), the
server puts it in the manifest the agent pulls (as ``readback_mode``), and the
agent takes its floor from there. Missing or bad there (an old server, a
manifest that did not read) = 50 and a line in the agent's log: never 0, never
"no floor". The relative check (#2037) does not change, and the failure still names
the floor in effect.

Negative control: ``server/disks/writefloor.py`` removed and
``server/agent/api.py``, ``server/console/api.py``,
``server/console/journal_he.py``, ``agent/lib/restore/writecheck.sh`` and
``agent/lib/restore/restore.sh`` back to the base -> the setting is refused as
unknown, the manifest has no floor, and a 45 MB/s disk under a 40 floor is red.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
from test_smart import BASH
from test_writecheck_speed_1366 import verdict

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
KEY = "writecheck_min_mbps"
LABEL = "סף מהירות כתיבה לדיסק"

needs_agent = requires_native(("bash", BASH), ("jq", shutil.which("jq")))


# --- the server: the setting, its range, 400 before a write, the default ------


def test_unset_reads_50(server):
    assert server["admin"].get("/api/console/settings").json()[KEY] == "50"


def test_an_admin_sets_40_and_it_is_journaled(server):
    admin, ctx = server["admin"], server["ctx"]
    assert admin.post("/api/console/settings", json={KEY: "40"}).status_code == 200
    assert admin.get("/api/console/settings").json()[KEY] == "40"
    details = [r["detail"] for r in ctx.conn.execute(
        "SELECT detail FROM journal WHERE event = 'setting_change' ORDER BY id")]
    assert f"{KEY}=40" in details, details


@pytest.mark.parametrize("edge", ["10", "500", 10, 500])
def test_the_range_edges_are_taken(server, edge):
    admin = server["admin"]
    assert admin.post("/api/console/settings", json={KEY: edge}).status_code == 200
    assert admin.get("/api/console/settings").json()[KEY] == str(edge)


@pytest.mark.parametrize("bad", ["9", "501", "0", "-40", "040", "40.5", 40.5, "abc", "", True, None, [40]])
def test_anything_else_is_400_and_nothing_is_written(server, bad):
    """The whole body is refused before any key: min_free_gb beside it is not stored either."""
    admin = server["admin"]
    assert admin.post("/api/console/settings", json={KEY: "40", "min_free_gb": "20"}).status_code == 200
    r = admin.post("/api/console/settings", json={"min_free_gb": "30", KEY: bad})
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == f"{LABEL}: מספר שלם 10–500 (MB/s)"
    after = admin.get("/api/console/settings").json()
    assert (after[KEY], after["min_free_gb"]) == ("40", "20"), after


def test_a_deploy_user_cannot_set_it(server):
    assert server["deploy"].post("/api/console/settings", json={KEY: "40"}).status_code == 403
    assert server["admin"].get("/api/console/settings").json()[KEY] == "50"


def test_a_corrupt_stored_value_reads_50_never_0(server):
    from server.db import set_settings                          # noqa: PLC0415
    from server.disks import writefloor                         # noqa: PLC0415
    set_settings(server["ctx"].conn, {KEY: "zero"})
    assert writefloor.mbps(server["ctx"].conn) == 50
    assert server["admin"].get("/api/console/settings").json()[KEY] == "50"


def test_the_manifest_the_agent_pulls_carries_the_floor(server):
    anon, admin = server["anon"], server["admin"]
    assert anon.get("/api/v1/images/img_7f3a91/manifest").json()[KEY] == 50
    assert admin.post("/api/console/settings", json={KEY: "40"}).status_code == 200
    served = anon.get("/api/v1/images/img_7f3a91/manifest").json()
    assert served[KEY] == 40 and isinstance(served[KEY], int), served


def test_the_server_default_is_the_agents_own():
    """Twins: the server's 50 and the agent's fallback must not drift apart."""
    from server.disks import writefloor                         # noqa: PLC0415
    sh = (REPO / "agent/lib/restore/writecheck.sh").read_text(encoding="utf-8")
    m = re.search(r'^WRITECHECK_MIN_MBPS="\$\{WRITECHECK_MIN_MBPS:-(\d+)\}"$', sh, re.M)
    assert m, "the agent's fallback floor is not where it was"
    assert int(m.group(1)) == writefloor.DEFAULT == 50


# --- the agent: the floor from the manifest, or 50 and a log line -------------


@needs_agent
def test_a_floor_of_40_lets_a_45_disk_pass(tmp_path):
    r = verdict(tmp_path, {"sda": 45}, manifest={KEY: 40})
    assert r["sda"] == ("writing", "ok", "") and r["rc"] == 0, r
    assert "write check floor: 40 MB/s" in r["log"], r["log"]


@needs_agent
def test_under_a_floor_of_40_the_message_names_40(tmp_path):
    r = verdict(tmp_path, {"sda": 35}, manifest={KEY: 40})
    assert r["sda"] == ("failed", "slow", "איטי — 35 MB/s, מתחת לסף 40 MB/s"), r


@needs_agent
def test_no_floor_in_the_manifest_is_50_and_a_log_line(tmp_path):
    """An old server: the field is not there. 50, said in the log -- the 45 disk is red."""
    r = verdict(tmp_path, {"sda": 45}, manifest={"id": "img_x"})
    assert r["sda"] == ("failed", "slow", "איטי — 45 MB/s, מתחת לסף 50 MB/s"), r
    assert "writecheck_min_mbps null in the manifest is not 10-500 -- 50 MB/s" in r["log"], r["log"]


@needs_agent
@pytest.mark.parametrize("bad", ["0", "-40", "5", "501", '"40"', "40.5", "true", "[40]", "1e9"])
def test_a_bad_floor_is_50_never_0_and_a_log_line(tmp_path, bad):
    r = verdict(tmp_path, {"sda": 45}, manifest='{"%s": %s}' % (KEY, bad))
    assert r["sda"] == ("failed", "slow", "איטי — 45 MB/s, מתחת לסף 50 MB/s"), (bad, r)
    assert "in the manifest is not 10-500 -- 50 MB/s" in r["log"], r["log"]


@needs_agent
def test_an_unreadable_manifest_is_50_and_a_log_line(tmp_path):
    r = verdict(tmp_path, {"sda": 45}, manifest="{not json")
    assert r["sda"][0:2] == ("failed", "slow") and "מתחת לסף 50 MB/s" in r["sda"][2], r
    assert "writecheck_min_mbps unread in the manifest is not 10-500 -- 50 MB/s" in r["log"], r["log"]


@needs_agent
def test_the_relative_check_does_not_change_with_the_floor(tmp_path):
    """#2037 stays: 120 against SSDs at 400/380 is red under any floor."""
    r = verdict(tmp_path, {"sda": 120, "sdb": 400, "sdc": 380}, manifest={KEY: 10})
    assert r["sda"][0:2] == ("failed", "slow"), r
    assert r["sda"][2] == "איטי — 120 MB/s, פחות מחצי מ-390 MB/s של שאר המגירות מאותו סוג (מוצק)"


def test_both_callers_hand_the_verdict_their_manifest():
    """The round (write_check_round) and the single restore (run_restore, $5)."""
    wc = (REPO / "agent/lib/restore/writecheck.sh").read_text(encoding="utf-8")
    rs = (REPO / "agent/lib/restore/restore.sh").read_text(encoding="utf-8")
    assert 'write_speed_verdict "$_wr_m" "$@"' in wc
    assert 'write_speed_verdict "$5" "$2"' in rs
