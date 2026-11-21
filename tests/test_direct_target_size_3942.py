"""#3942 -- a direct send shrinks to the smallest drawer chosen, or refuses before
the first byte is read, by that drawer's name.

Metal 20/11 (``room_13e6b4ec``, LabRoot on ``41cd662d4``): HP-BUILD-2's 500GB
Samsung 860 EVO (C: expanded to 499GB, 76.6GB used) to a 256GB drawer on
cloner 2. The tail of #1202/#1171 was aimed at the end of the SOURCE disk,
because ``CAPTURE_TARGET_BYTES`` -- the floor the fifth gate of capture.sh reads
-- was set by nobody. Pass 1 read and cut the whole disk for ten minutes, and
only then did the drawer refuse: "תוכנית השחזור פגומה (מחיצה 3: חורגת מסוף
הדיסק)". The same source to a 512GB drawer (``room_7059da0c``) went through.

Now the server hands the build machine the smallest drawer chosen
(``task.direct.target_bytes`` / ``target_name``, interface 3,
``direct.target_floor``), and pass 1 ends its layout ``SHRINK_TAIL_BYTES``
before THAT drawer's end: the shortfall is taken from the tail, on the RAM
view, after the same "כווץ ושלח" (``_shrink_direct_target``, shrinkplan.sh).
A layout that cannot shrink to it is refused by the fifth gate at once --
before the question and before partclone -- with the drawer's name. A drawer
whose size is unknown is not "no limit": the server's journal and the agent's
log both say it, and which drawer.

The agent part runs the real ``capture_disk`` with the stubs and the 20/09
layout of ``test_tail_shift_1202.py`` (via ``test_direct_tail_1171.py``), then
the real ``direct_send_run`` with the harness of ``test_directsend.py``; the
server part runs the real API of ``test_direct_server.py``.

**Negative control:** see the PR (bakara-shlilit) -- every production file of
this change back to its base: agent/lib/capture/capture.sh, directsend.sh,
agent/lib/shrink/shrinkplan.sh and server/rounds/direct.py.
"""

from __future__ import annotations

import json

from native import requires_native
from test_agent import BASH, posix
from test_capture_refusals import refusal_reason
from test_direct_server import BUILD, _build_hello, _open_direct, _register_build
from test_direct_tail_1171 import NO_ANSWER, YES, _capture, _ok, _pcl_reads
from test_directsend import _answer, direct_run
from test_server_room import CLONER1, room_server  # noqa: F401 -- the fixture
from test_shrink_on_capture import manifest, order, part, source_writes
from test_tail_shift_1202 import NEARLY_FULL_MIN, REC_END, SOURCE_DISK_BYTES, TAIL, WIN_BYTES

from conftest import hello_body
from server.library.images import required_bytes
from server.rounds import room

#: The agent tests run the real shell; the server tests need none.
SHELL = requires_native(("bash", BASH))
MIB = 1 << 20
#: A drawer 56GB smaller than the 20/09 source -- what the layout must shrink into.
DRAWER = 200_000_000_000
NAME = "דיסק 5 במחשב משכפל-2"


def _floor(target=DRAWER, name=NAME) -> dict:
    return {"CAPTURE_TARGET_BYTES": str(target), "CAPTURE_TARGET_NAME": name}


def _need(target: int) -> int:
    """What the tail takes from C: -- the shortfall to the drawer plus 64MiB, in whole MiB."""
    short = TAIL - ((target // 512) * 512 - REC_END * 512)
    return -(-short // MIB) * MIB


# --- agent, pass 1: shrinks to the drawer, or refuses at once -----------------------------

@SHELL
def test_a_source_that_shrinks_into_the_drawer_goes_through(tmp_path):
    """C: has room: the tail takes what the drawer lacks, on the view, after the yes.
    Fails at the base -- the tail stops 64MiB before the SOURCE's end and the
    fifth gate refuses (rc=1)."""
    box, run, out = _capture(tmp_path, WIN_BYTES // 2, answer=YES, env=_floor())
    _ok(box, run, out)
    steps, dev = order(run), posix(box / "dev")
    assert f"ntfsresize -s {WIN_BYTES - _need(DRAWER)} --no-progress-bar {dev}/mapper/lc-src-3" in steps, steps
    assert source_writes(steps, box) == [], source_writes(steps, box)
    m = manifest(run)
    assert part(m, 3)["shrunk_from_bytes"] == WIN_BYTES
    assert required_bytes(m) == m["min_target_bytes"] <= DRAWER - TAIL + 262144, m["min_target_bytes"]
    assert m["target_floor_bytes"] == DRAWER
    log = (run / "agent.log").read_text(encoding="utf-8", errors="replace")
    assert f"before the smallest target drawer ({DRAWER} bytes, {NAME})" in log, log[-1500:]


@SHELL
def test_a_source_that_cannot_shrink_into_the_drawer_is_refused_before_a_byte_by_name(tmp_path):
    """C: nearly full (500MB free): no partition can give 56GB. No question, no
    view, no partclone -- the fifth gate refuses with the numbers and the drawer.
    Fails at the base on the name (the refusal itself was there, the floor was not set)."""
    box, run, out = _capture(tmp_path, NEARLY_FULL_MIN, answer=YES, env=_floor())
    reason = refusal_reason(box, run, out)
    assert "הפריסה גדולה מכונן היעד" in reason and f"({NAME}) מחזיק {DRAWER}" in reason, reason
    steps = order(run)
    assert "asked" not in steps, steps
    assert not [s for s in steps if s.startswith(("ntfsresize -s", "dmsetup", "modprobe"))], steps
    assert _pcl_reads(run) == [], _pcl_reads(run)


@SHELL
def test_not_confirmed_the_full_layout_is_refused_before_a_byte(tmp_path):
    """Nobody said "כווץ ושלח" (no GUI here): nothing is shrunk -- Nadav 17/10 --
    and the full layout does not fit the drawer: refused at once, not after ten minutes."""
    box, run, out = _capture(tmp_path, WIN_BYTES // 2, answer=NO_ANSWER, env=_floor())
    reason = refusal_reason(box, run, out)
    assert f"({NAME}) מחזיק {DRAWER}" in reason, reason
    assert _pcl_reads(run) == [] and not [s for s in order(run) if s.startswith("ntfsresize -s")], order(run)


@SHELL
def test_a_drawer_larger_than_the_source_keeps_the_tail_of_1171(tmp_path):
    """A guard (passes at the base too): the source's own end is the nearer one."""
    box, run, out = _capture(tmp_path, WIN_BYTES // 2, answer=YES, env=_floor(SOURCE_DISK_BYTES + 10 ** 9))
    _ok(box, run, out)
    shrinks = [s for s in order(run) if s.startswith("ntfsresize -s ")]
    assert shrinks == [f"ntfsresize -s {WIN_BYTES - TAIL} --no-progress-bar {posix(box / 'dev')}/mapper/lc-src-3"], shrinks


# --- agent: direct_send_run reads the floor from the task ---------------------------------

def _with(target_bytes, target_name) -> dict:
    answer = _answer()
    answer["task"]["direct"].update({"target_bytes": target_bytes, "target_name": target_name})
    return answer


def _run(tmp_path, answer):
    pre = f'cp {posix(tmp_path / "box" / "answer.json")!r} "$RESP"; '   # the answer that carried the task
    return direct_run(tmp_path, answer=answer, pre=pre)


@SHELL
def test_the_task_floor_reaches_pass_1_and_refuses_before_any_read(tmp_path):
    """A drawer smaller than this layout: pass 1 refuses with its name, and
    partclone never ran. Fails at the base: the floor was not read, rc=0."""
    box, run, out = _run(tmp_path, _with(512, NAME))
    assert out.strip().endswith("rc=1"), out
    error = (run / "targets" / "sda" / "error").read_text(encoding="utf-8")
    assert f"({NAME}) מחזיק 512" in error, error
    assert not (box / "pcl.count").exists() or (box / "pcl.count").read_text().strip() in ("", "0")


@SHELL
def test_an_unknown_drawer_size_is_said_and_names_the_drawer(tmp_path):
    """Principle 5: unknown is not "no limit". The send goes on (each drawer checks
    its own fit, disk_fits), and the log says so. Fails at the base: no such line."""
    box, run, out = _run(tmp_path, _with(None, NAME))
    assert out.strip().endswith("rc=0"), out
    log = (run / "agent.log").read_text(encoding="utf-8", errors="replace")
    assert f"the smallest target drawer's size is unknown ({NAME})" in log, log[-1500:]
    assert manifest(run)["target_floor_bytes"] is None


# --- server: task.direct carries the smallest drawer chosen -------------------------------

def _drawers(client, mac, sizes: dict[int, int | None]):
    body = hello_body(mac)
    body["disks"] = [{"dev": f"sd{chr(ord('a') + port - 1)}", "port": port, "model": "Drawer SSD",
                      "serial": f"S{port}", "removable": False, "scheme": "gpt", "has_data": False,
                      **({"size_bytes": size} if size is not None else {})}
                     for port, size in sizes.items()]
    assert client.post("/api/v1/agent/hello", json=body).status_code == 200


def _label(room_server, mac, port) -> str:
    row = room_server["ctx"].conn.execute("SELECT suffix FROM machines WHERE mac = ?", (mac,)).fetchone()
    return f"דיסק {port} במחשב {room._room_label(row['suffix'])}"


def _journal(room_server) -> str:
    row = room_server["ctx"].conn.execute(
        "SELECT detail FROM journal WHERE event = 'room_open_direct' ORDER BY rowid DESC LIMIT 1").fetchone()
    return row["detail"]


def test_the_server_hands_the_smallest_chosen_drawer(room_server):
    """512GB and 256GB chosen, a 128GB one not: the floor is the 256GB, by name.
    Fails at the base: task.direct has no target_bytes."""
    admin, anon = room_server["admin"], room_server["anon"]
    _register_build(room_server)
    _drawers(anon, CLONER1, {1: 512_110_190_592, 2: 256_060_514_304, 3: 128_035_676_160})
    assert _open_direct(admin, [{"mac": CLONER1, "ports": [1, 2]}]).status_code == 200
    direct = _build_hello(anon, BUILD)["task"]["direct"]
    assert direct["target_bytes"] == 256_060_514_304
    assert direct["target_name"] == _label(room_server, CLONER1, 2)
    assert "target_bytes=256060514304" in _journal(room_server)


def test_a_chosen_drawer_without_a_size_is_unknown_by_name(room_server):
    """Principle 5: a drawer that did not report its size makes the floor unknown --
    never the smallest of the others -- and the open's journal says which."""
    admin, anon = room_server["admin"], room_server["anon"]
    _register_build(room_server)
    _drawers(anon, CLONER1, {1: 256_060_514_304, 2: None})
    assert _open_direct(admin, [{"mac": CLONER1, "ports": [1, 2]}]).status_code == 200
    direct = _build_hello(anon, BUILD)["task"]["direct"]
    assert direct["target_bytes"] is None
    assert direct["target_name"] == _label(room_server, CLONER1, 2)
    assert f"target_bytes=unknown ({_label(room_server, CLONER1, 2)})" in _journal(room_server)

