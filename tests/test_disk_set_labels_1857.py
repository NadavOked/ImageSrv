"""#1857 (mockup 04) -- the set and sticker ("קוריאים · xx-7") beside the disk, wherever the disk is shown.

The server already sent `disk_labels` in every hello answer and nothing read it. Nadav: "שאני ידע
דיסק מסט xx לא תקין ואותו להחליף" -- so the name has to be where the person standing at the
machine, or reading a red disk, can see it. Four layers, one test section each:

* **server**: `set_text` on the console's machine disks, on the open disk failures, on the round's
  drawer list; the journal's `disk_failure` lines name the set at READ time (a disk assigned after
  its failure is named in the old line too). A disk with no set: `None`, exactly as before.
* **agent** (`disklabels.sh`): the hello answer -> a file -> `cdisk_label=<dev>|<text>` records.
  An answer without the field, or one that cannot be read, is not "no sets" (principle 5).
* **state chain**: `cloner_gui_state` writes the records, in the kiosk's own load chain (`gui_libs`).
* **screens** (C, Testrunner only): the chip on the waiting cloner, the first words of the size line
  in a round; a disk with no set draws the card it always drew.

**Negative control** (bakara-shlilit; every production file this change touched put back as on
origin/main -- server/console/api.py, server/console/journal_he.py, server/disks/disk_sets.py,
server/rounds/room.py, server/static/console.js, agent/lib/gui/clonergui.sh,
agent/lib/gui/guibridge.sh, agent/imagesrv-agent, native-gui/src/*.{c,h}, and disklabels.sh
removed): every test below fails -- see the PR for the run.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from agentlib import flat_lib
from test_cloner_gui import (AGENT, BASH, GUI, IDLE_SGDISK, _build_gui, _pkgconfig, env, posix, sh,
                             target)

JQ = requires_native(("bash", BASH), "jq", why="disklabels.sh reads the hello answer with jq")
GUI_BUILD = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui is built on Linux only",
)

SETS = "/api/console/disk-sets"


# ----------------------------------------------------------------------------- server


pytest.importorskip("fastapi")
from test_room_precheck_432_65 import disk, drawers_hello, machine, room  # noqa: E402
from test_server_room import CLONER1, room_server  # noqa: E402,F401 -- fixture


def _assign(admin, *pairs):
    r = admin.post(SETS, json={"name": "קוריאים", "prefix": "xx", "students": 29, "instructor": True})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    r = admin.post(f"{SETS}/{sid}/assign", json={"items": [{"serial": s, "suffix": n} for s, n in pairs]})
    assert r.status_code == 200, r.text
    return sid


def test_the_machines_list_carries_the_set_text_of_each_disk_and_none_without_a_set(room_server):
    admin, anon = room_server["admin"], room_server["anon"]
    _assign(admin, ("S7", "7"))
    drawers_hello(anon, CLONER1, [disk("S7", 1), disk("S8", 2)])
    machines = admin.get("/api/console/machines").json()
    got = {d["serial"]: d["set_text"] for d in next(m for m in machines if m["mac"] == CLONER1)["disks"]}
    assert got == {"S7": "קוריאים · xx-7", "S8": None}, got


def test_the_rounds_drawer_list_carries_the_set_text(room_server):
    admin, anon = room_server["admin"], room_server["anon"]
    _assign(admin, ("S7", "7"))
    drawers_hello(anon, CLONER1, [disk("S7", 1), disk("S8", 2)])
    drawers = machine(room(admin), CLONER1)["drawer_list"]
    assert {d["serial"]: d["set_text"] for d in drawers} == {"S7": "קוריאים · xx-7", "S8": None}


def test_an_open_disk_failure_names_the_set_by_the_serial_of_the_record(room_server):
    admin, ctx = room_server["admin"], room_server["ctx"]
    _assign(admin, ("S7", "7"))
    for serial, port in (("S7", 1), ("S8", 2), (None, 3)):
        ctx.conn.execute("INSERT INTO disk_failures (session_id, mac, dev, serial, port, at, cause)"
                         " VALUES ('s', ?, ?, ?, ?, '2026-11-07T08:00:00+00:00', 'disk')",
                         (CLONER1, f"sd{port}", serial, port))
    ctx.conn.commit()
    rows = admin.get("/api/console/disk-failures").json()
    assert {r["serial"]: r["set_text"] for r in rows} == {"S7": "קוריאים · xx-7", "S8": None, None: None}


def test_the_journal_names_the_set_at_read_time_even_for_a_failure_written_before_the_assignment(room_server):
    from server.db import journal
    admin, ctx = room_server["admin"], room_server["ctx"]
    journal(ctx.conn, "disk_failure", f"{CLONER1} sda serial=S7 port=1 cause=disk", machine=CLONER1)
    journal(ctx.conn, "disk_failure", f"{CLONER1} sdb serial=? port=2 cause=disk", machine=CLONER1)
    journal(ctx.conn, "disk_failure_cleared", "#3 serial=S7 נוקה אוטומטית")

    def texts():
        return [r["text"] for r in admin.get("/api/console/journal?limit=50").json()
                if r["event"].startswith("disk_failure")]
    before = texts()
    assert before and not any("xx-7" in t for t in before), before
    _assign(admin, ("S7", "7"))                      # AFTER the failures were written
    after = texts()
    assert sum("קוריאים · xx-7" in t for t in after) == 2, after          # the failure and its clearing
    assert sum("xx-" in t for t in after) == 2, "a failure with no serial must not get a name"


def test_a_serial_that_is_not_a_string_is_not_looked_up():
    from server.disks import disk_sets
    assert disk_sets.text_of({"S7": {"text": "x"}}, ["S7"]) is None
    assert disk_sets.text_of({"S7": {"text": "x"}}, None) is None
    assert disk_sets.text_of({"S7": {"text": "x"}}, "S7") == "x"


# ----------------------------------------------------------------------------- the console


def test_the_console_shows_the_text_in_the_three_places_a_person_looks_for_a_disk_to_pull():
    js = (Path(__file__).resolve().parent.parent / "server/static/console.js").read_text(encoding="utf-8")
    boxes = js[js.index("function diskBoxesHtml"):js.index("function diskBrand")]
    assert 'UI.pill("info", d.set_text)' in boxes and "${setTag}" in boxes, "the machine drawer's disk box"
    failures = js[js.index("function diskFailuresCard"):js.index("async function clearDiskFailure")]
    assert "f.set_text" in failures and "d.set_text" in failures and "esc(setText)" in failures, "the red disks table"
    title = js[js.index("function diskTitle"):js.index("function slotHtml")]
    assert "d.set_text" in title, "the tooltip of every drawer"


# ----------------------------------------------------------------------------- the agent


def _labels(tmp_path: Path, responses: list[object], disks: str = "") -> tuple[str, str]:
    """`disk_labels_refresh` on every answer in turn (hello beats); then the records of <disks>
    (dev=serial pairs). Returns (the records, the kept file)."""
    run = tmp_path / "run"; run.mkdir()
    steps = []
    for i, body in enumerate(responses):
        f = tmp_path / f"r{i}.json"
        f.write_text(body if isinstance(body, str) else json.dumps(body, ensure_ascii=False),
                     encoding="utf-8", newline="\n")
        steps.append(f'cp {posix(f)!r} "$RUN_DIR/response.json"; disk_labels_refresh || :')
    cases = " ".join(f"{p.split('=')[0]}) echo {p.split('=')[1]};;" for p in disks.split())
    devs = " ".join(p.split("=")[0] for p in disks.split())
    out = sh(f'export RUN_DIR={posix(run)!r}; . {posix(AGENT)}/lib/hw/disklabels.sh; '
             f'disk_serial() {{ case "$1" in {cases} esac; }}; '
             + "; ".join(steps) + f'; disk_label_records "{devs}"')
    assert out.returncode == 0, out.stderr
    kept = run / "disk_labels"
    return out.stdout, (kept.read_text(encoding="utf-8") if kept.exists() else "")


SEVEN = {"S7": {"set": "קוריאים", "label": "xx-7", "text": "קוריאים · xx-7"}}


@JQ
def test_the_hello_answer_becomes_one_record_per_local_disk_in_a_set(tmp_path):
    out, _ = _labels(tmp_path, [{"disk_labels": {**SEVEN, "S9": {"text": "קוריאים · xx-INS"}}}],
                     "sda=S7 sdb=S8 sdc=S9 sdd=")
    assert out.splitlines() == ["cdisk_label=sda|קוריאים · xx-7", "cdisk_label=sdc|קוריאים · xx-INS"], \
        "keyed by the DEVICE of the local disk; no set / no serial = no record"


@JQ
def test_the_separators_of_the_record_format_are_folded_and_a_broken_entry_is_dropped(tmp_path):
    out, _ = _labels(tmp_path, [{"disk_labels": {"S7": {"text": "a|b\nc"}, "S8": {"text": 5}, "S9": "x"}}],
                     "sda=S7 sdb=S8 sdc=S9")
    assert out.splitlines() == ["cdisk_label=sda|a b c"]


@JQ
def test_an_answer_without_the_field_or_unreadable_is_not_no_sets(tmp_path):
    """A poll response, an older server, a half-written file: the last file stays (principle 5)."""
    out, kept = _labels(tmp_path, [{"disk_labels": SEVEN}, {"schema": 1}, "{broken", {"disk_labels": []}],
                        "sda=S7")
    assert out.splitlines() == ["cdisk_label=sda|קוריאים · xx-7"] and kept.startswith("ok\n")


@JQ
def test_an_empty_object_is_an_answer_and_clears_the_sets(tmp_path):
    out, kept = _labels(tmp_path, [{"disk_labels": SEVEN}, {"disk_labels": {}}], "sda=S7")
    assert out == "" and kept == "ok\n"


def test_no_file_yet_means_no_records_and_no_error(tmp_path):
    out = sh(f'export RUN_DIR={posix(tmp_path)!r}; . {posix(AGENT)}/lib/hw/disklabels.sh; '
             'disk_serial() { echo S7; }; disk_label_records "sda"')
    assert out.returncode == 0 and out.stdout == ""


def test_the_kiosk_load_chain_and_the_load_gate_source_disklabels():
    """#834: a lib missing from `gui_libs` is `not found` once a second on the metal -- and
    here it would be silent, because clonergui.sh asks `command -v` first."""
    bridge = (AGENT / "lib/gui/guibridge.sh").read_text(encoding="utf-8")
    chain = bridge[bridge.index("gui_libs() {"):bridge.index("\n}", bridge.index("gui_libs() {"))]
    assert '. "$LIB_DIR/disklabels.sh"' in chain
    assert chain.index("disklabels.sh") < chain.index("clonergui.sh")
    assert '. "$LIB_DIR/disklabels.sh"' in (AGENT / "imagesrv-agent").read_text(encoding="utf-8")


# ----------------------------------------------------------------------------- the state chain


def _kiosk_state(tmp_path: Path, response: str, disks: dict[str, str], round_dev: str | None = None) -> list[str]:
    """`cloner_gui_state` in the kiosk's own chain (`gui_libs`); returns the lines of the state file."""
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    box = tmp_path / "box"; box.mkdir()
    sysroot = tmp_path / "sysroot"
    (run / "smart").mkdir()
    (run / "response.json").write_text(response, encoding="utf-8", newline="\n")
    for dev in disks:
        (sysroot / "sys/block" / dev / "device").mkdir(parents=True)
        (sysroot / "sys/block" / dev / "size").write_text("1000\n", newline="\n")
        (sysroot / "sys/block" / dev / "ro").write_text("0\n", newline="\n")
        (sysroot / "sys/block" / dev / "device/model").write_text(f"Model {dev}\n", newline="\n")
        (run / "smart" / f"{dev}.v").write_text("ok ok 0 0 0 0\n", newline="\n")
    if round_dev:
        target(run, round_dev, "writing", 1000, 100, "100\n")
    serials = " ".join(f"{d}) echo {s};;" for d, s in disks.items() if s)
    ports = " ".join(f"{d}) echo {i + 1};;" for i, d in enumerate(disks))
    script = (
        f"mkdir -p {posix(box)}/bin\n"
        f"cat > {posix(box)}/bin/sgdisk <<'STUB'\n{IDLE_SGDISK.format(box=posix(box))}STUB\n"
        f"chmod 0755 {posix(box)}/bin/sgdisk\n"
        f'export PATH="$(cd {posix(box)}/bin && pwd):$PATH"\n'
        + env(run, gui)
        + f'export SYSROOT={posix(sysroot)!r} LIB_DIR={posix(flat_lib())!r}; '
        f'. {posix(AGENT)}/lib/gui/guibridge.sh; gui_libs; '
        f'disk_port() {{ case "$1" in {ports} esac; }}; '
        f'disk_serial() {{ case "$1" in {serials} esac; }}; '
        'json_get_join() { [ -s "$1" ] || return 1; echo ok; }; '
        'cloner_gui_state')
    out = sh(script)
    assert out.returncode == 0, out.stderr
    assert "not found" not in out.stderr, out.stderr
    return (gui / "state").read_text(encoding="utf-8").splitlines()


@JQ
def test_the_cloner_state_file_carries_the_label_idle_and_in_a_round(tmp_path):
    resp = json.dumps({"schema": 1, "disk_labels": SEVEN}, ensure_ascii=False)
    for name, round_dev in (("idle", None), ("round", "sda")):
        d = tmp_path / name; d.mkdir()
        lines = _kiosk_state(d, resp, {"sda": "S7", "sdb": "S8"}, round_dev=round_dev)
        assert [l for l in lines if l.startswith("cdisk_label=")] == ["cdisk_label=sda|קוריאים · xx-7"], lines
        assert any(l.startswith("drawer=") for l in lines) == (round_dev is not None), name


@JQ
@pytest.mark.parametrize("body", ['{"schema":1}', '{"schema":1,"disk_labels":{}}'])
def test_a_state_with_no_sets_has_no_label_records_at_all(tmp_path, body):
    lines = _kiosk_state(tmp_path, body, {"sda": "S7"})
    assert not [l for l in lines if l.startswith("cdisk_label")], lines


# ----------------------------------------------------------------------------- the screens

SIZE = 500_107_862_016
IDLE = f"""message=x|y
disk_probe=drives
drawer_count=3
cdrawer_slot=1|SATA 0|
cdrawer_slot=2|SATA 1|
cdrawer_slot=3|SATA 2|
cdisk=1|sda|Samsung SSD 870 EVO|{SIZE}|passed||6.0|6.0||1|SATA 0
cdisk=2|sdb|WDC WD5000AAKX|{SIZE}|fail|pending_sectors|6.0|6.0||2|SATA 1
cused=sda|{SIZE}|214748364800
cused=sdb|{SIZE}|-1
cdisk_label=sda|קוריאים · xx-7
cdisk_label=sdb|קוריאים · xx-12
"""
ROUND = f"""cloner=Win11 Lab 2026
drawer_count=2
cdrawer_slot=1|SATA 0|
cdrawer_slot=2|SATA 1|
drawer=1|sda|done|85899345920|85899345920||-1|-1|1|SATA 0
drawer=2|sdb|writing|40000000000|85899345920||98000000|300|2|SATA 1
cused=sda|{SIZE}|214748364800
cused=sdb|{SIZE}|-1
cdisk_label=sda|קוריאים · xx-7
updated=1792249500
message=x|y
"""


@pytest.fixture(scope="module")
def binary(tmp_path_factory) -> Path:
    return _build_gui(tmp_path_factory.mktemp("gui1857"))


def words(binary: Path, tmp: Path, state: str, card: str, theme: str = "light") -> list[str]:
    """Every string the screen laid out for <card>, bidi marks stripped."""
    log = tmp / f"text-{card}.log"; log.write_text("")
    sf = tmp / "state.txt"; sf.write_text(state, encoding="utf-8", newline="\n")
    cmd = [str(binary), "--png", posix(tmp / "p"), "--size", "1280x800", "--mac", "3C:52:82:A1:00:1F",
           "--ip", "10.10.10.31", "--state", posix(sf)]
    run = subprocess.run(cmd, capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL,
                         env=dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"), IMAGESRV_GUI_TEXTLOG=posix(log)))
    assert run.returncode == 0, run.stderr
    out, on = [], False
    for l in log.read_text(encoding="utf-8").splitlines():
        if l.startswith("@@ "):
            on = l == f"@@ {card}-{theme}"
        elif on:
            out.append(re.sub("[‎‏⁦-⁩]", "", l))
    assert out, f"no text logged for {card}-{theme}"
    return out


@GUI_BUILD
def test_the_waiting_cloner_draws_the_set_and_sticker_as_a_chip_beside_the_drawers_name(binary, tmp_path):
    w = words(binary, tmp_path, IDLE, "standby")
    assert "קוריאים · xx-7" in w and "קוריאים · xx-12" in w, w
    assert "SATA 0" in w and "6.0 Gb/s" in w, "the name and the link keep their place"


@GUI_BUILD
def test_a_disk_with_no_set_draws_exactly_what_it_drew_before(binary, tmp_path):
    with_sets = words(binary, tmp_path, IDLE, "standby")
    without = words(binary, tmp_path, "\n".join(l for l in IDLE.splitlines() if not l.startswith("cdisk_label")) + "\n",
                    "standby")
    assert not [l for l in without if "xx-" in l], without
    assert [l for l in with_sets if "xx-" not in l] == without, "only the chips were added"


@GUI_BUILD
def test_a_round_leads_the_size_line_with_the_set_and_sticker(binary, tmp_path):
    w = words(binary, tmp_path, ROUND, "cloner")
    line = [l for l in w if "קוריאים · xx-7" in l]
    assert len(line) == 1 and "500 GB" in line[0] and line[0].index("xx-7") < line[0].index("500 GB"), w
    assert "500 GB" in w, "the other drawer has no set: its size line is what it was"
    assert not [l for l in w if "xx-" in l and l not in line], w


@GUI_BUILD
@pytest.mark.parametrize("junk", ["cdisk_label=|קוריאים · xx-9", "cdisk_label=sda|", "cdisk_label=sda"])
def test_a_label_record_that_cannot_be_read_draws_no_chip_and_does_not_crash(binary, tmp_path, junk):
    state = "\n".join(l for l in IDLE.splitlines() if not l.startswith("cdisk_label")) + "\n" + junk + "\n"
    w = words(binary, tmp_path, state, "standby")
    assert "SATA 0" in w
    assert not [l for l in w if l.startswith("קוריאים")], w
