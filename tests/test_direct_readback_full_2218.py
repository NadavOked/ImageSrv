"""#2218 -- a direct send in sample mode is read back in full, against the live manifest.

Metal 30/10 (room_30255e5c, HP-BUILD-2 sda -> cloner 2, ports 3 and 5): in sample
mode the cloner fetches the server's list (``GET /images/<live id>/readback/<n>``),
but a direct send never puts an image in the library, so there is no list: 404,
``readback_list_not_fetched`` twice, and both healthy targets came out orange --
"not verified". Every direct send would.

Decision 03/11: in a direct send, sample mode becomes full -- every chunk, against
the ``readback_xxh64`` the build machine's pass 1 already put in the live manifest
(``rb_capture_json``, readbacklc.sh). No new upload path, no new mode. The price:
a whole read of what was written, in a direct send only. The journal says the
mode was switched, and why. The agent is unchanged: it takes the mode from the
manifest it pulls (``rb_mode``), and ``full`` against ``readback_xxh64`` is its
existing path (test_readback_agent_1805.py) -- a healthy target is ``verified``.

Negative control: ``server/agent/api.py`` and ``server/console/journal_he.py``
back to the base -> the live manifest says ``sample`` and nothing is journaled.
"""

from __future__ import annotations

from conftest import MANIFEST_256
from test_direct_server import _put_manifest, _setup, room_server  # noqa: F401
from test_server_room import CLONER1, cloner_hello

from server.console import journal_he


def _switches(ctx) -> list[str]:
    return [r["detail"] for r in ctx.conn.execute(
        "SELECT detail FROM journal WHERE event = 'readback_mode_switched' ORDER BY id")]


def test_a_live_manifest_in_sample_mode_is_served_as_full_and_journaled(room_server):
    opened = _setup(room_server)
    anon, ctx = room_server["anon"], room_server["ctx"]
    assert _put_manifest(room_server, opened["task_id"]).status_code == 200
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    served = anon.get(f"/api/v1/images/{opened['image_id']}/manifest")
    assert served.status_code == 200
    assert served.json()["readback_mode"] == "full", served.json()
    switches = _switches(ctx)
    assert len(switches) == 1, switches
    assert opened["image_id"] in switches[0] and "sample" in switches[0] and "full" in switches[0], switches


def test_the_switch_has_hebrew_words_in_the_journal():
    assert journal_he.EVENTS_HE["readback_mode_switched"] == (
        "האימות אחרי הכתיבה עבר ממדגם למלא — בהפצה ישירה אין לשרת רשימת בדיקה")


def test_full_already_is_not_a_switch(room_server):
    """The setting says full: served full, and nothing was switched."""
    opened = _setup(room_server)
    anon, admin, ctx = room_server["anon"], room_server["admin"], room_server["ctx"]
    assert admin.post("/api/console/settings", json={"readback_full": "true"}).status_code == 200
    assert _put_manifest(room_server, opened["task_id"]).status_code == 200
    assert anon.get(f"/api/v1/images/{opened['image_id']}/manifest").json()["readback_mode"] == "full"
    assert _switches(ctx) == []


def test_a_library_image_in_sample_mode_stays_sample(room_server):
    """Guard: only the live manifest is switched -- the library keeps its sample."""
    anon, ctx = room_server["anon"], room_server["ctx"]
    assert anon.get(f"/api/v1/images/{MANIFEST_256['id']}/manifest").json()["readback_mode"] == "sample"
    assert _switches(ctx) == []
