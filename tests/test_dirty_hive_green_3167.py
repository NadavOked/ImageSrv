"""#3167 (Nadav 14/11, option A) -- a name written into an image that was not shut down clean is a note, not a problem.

Metal, 14/11: the cloner left drawers with no computer name, orange, and stayed on ("המחשב נשאר דלוק כי
יש דיסק עם בעיה") -- the SYSTEM hive of the image was not synced (#1821 refused to write). The data was
fine; FOG and Clonezilla write the name all the same. Now `write_hostname` writes it, reads it back as
for a clean hive, and answers ok with a `note` (test_hostname_dirty_hive_1821.py). What this file holds:

* the round, end to end in sh: the real `write_hostname` on a dirty hive -> `name_drawers` -> the note
  in targets/<dev>/note, no `error` -> `round_beep_result` beeps success -> `end_countdown` counts 20 s
  and powers off -- a note only is a green round;
* the station path (`name_this_machine`): the same note, no `error`;
* the wire (`target_extra_json` -> `note`), the screen record (`drawer_note=`), the cloner's row (green,
  the note in its line), the server (`drawer_list[].note`, the journal's `target_note`, info) and the
  console's card (a grey line, not orange);
* a dual-boot disk keeps the Windows side's note.

Negative control (in the PR): every production file this touched back to the base -- the round holds
(FINISH 601, not 20), the drawer carries `error`, and there is no `note` anywhere.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from agentlib import agent_sources
from console_src import console_js
from native import requires_native
from test_agent import AGENT, BASH, posix
from test_captured_stdout import windows_box
from test_end_hold_2024 import HARNESS
from test_end_of_task_1560 import bash, q, trace_of
from test_hostname_dirty_hive_1821 import HIVEWRITE_LOGGED, NOTE, _hive
from test_readback_progress_2046 import cloner, driver, pytestmark_gui  # noqa: F401  (driver is a fixture)

needs_jq = requires_native(("bash", BASH), ("jq", shutil.which("jq")))


def _lib(name: str) -> str:
    """A library by its repo path -- drawernames.sh, roundbeep.sh, idle.sh... as imagesrv-agent loads them."""
    from agentlib import lib_path
    return f". {posix(lib_path(name))}; "


def _dirty_box(tmp_path: Path) -> tuple[str, Path]:
    """windows_box (the real write_hostname, hivewrite logged) with a hive whose sequences differ."""
    script, run = windows_box(tmp_path, hivewrite=HIVEWRITE_LOGGED)
    (run / "win" / "Windows" / "System32" / "config" / "SYSTEM").write_bytes(_hive(41, 40))
    (run / "lc" / "run").mkdir(parents=True, exist_ok=True)
    return script, run


def _target(run: Path, dev: str, **files: str) -> Path:
    t = run / "targets" / dev
    t.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (t / name).write_text(text + "\n", encoding="utf-8", newline="\n")
    return t


# --- the agent: a note only is a green round ----------------------------------------------


@needs_jq
def test_a_round_whose_only_note_is_the_dirty_hive_beeps_success_and_powers_off_in_20s(tmp_path):
    script, run = _dirty_box(tmp_path)
    t = _target(run, "sdb", state="done", readback="sampled")
    resp = run / "response.json"
    resp.write_text(json.dumps({"session": {"hostnames": {"1": "LAB1-05"}, "hostname_os": None}}),
                    encoding="utf-8", newline="\n")
    script += (
        f"export RESP={q(resp)} LC={q(run / 'lc')} MAC=aa:bb:cc:dd:ee:ff SERVER=http://127.0.0.1:9 IMAGESRV_TEST=0; "
        + _lib("jsonq.sh") + _lib("progress.sh") + _lib("attended.sh") + _lib("idle.sh") + _lib("lcwait.sh")
        + _lib("roundbeep.sh") + _lib("drawernames.sh") + HARNESS
        + 'event_beep() { echo "BEEP $1" >> "$RUN_DIR/trace"; }; disk_port() { echo 1; }; assert_target() { return 0; }; '
        + 'name_drawers sdb; round_beep_result sdb; end_countdown drawers "drawers complete (1 of 1)" 1 1 sdb; echo "rc=$?"'
    )
    out = bash(script, timeout=120)
    assert out.stdout.strip().endswith("rc=0"), out.stdout + out.stderr
    assert not (t / "error").exists(), (t / "error").read_text(encoding="utf-8")
    assert (t / "note").read_text(encoding="utf-8").strip() == NOTE
    assert json.loads((t / "hostname.json").read_text(encoding="utf-8"))["ok"] is True
    assert trace_of(run) == ["BEEP success", "FINISH 20"], trace_of(run)   # green: no hold, the count, off
    at, kind, done, total, held = (run / "seen").read_text().strip().split("|")
    assert (kind, done, total, held) == ("drawers", "1", "1", "0") and int(at) > 0


@needs_jq
def test_the_station_path_keeps_the_note_and_no_error(tmp_path):
    script, run = _dirty_box(tmp_path)
    t = _target(run, "sda", state="done", readback="sampled")
    resp = run / "response.json"
    resp.write_text(json.dumps({"session": {"prefix": "lab1"}, "group": {"suffix": "05"}}),
                    encoding="utf-8", newline="\n")
    script += (f"export RESP={q(resp)}; " + _lib("jsonq.sh") + _lib("progress.sh")
               + 'assert_target() { return 0; }; name_this_machine sda ses_x; echo "rc=$?"')
    out = bash(script, timeout=120)
    assert out.stdout.strip().endswith("rc=0"), out.stdout + out.stderr
    assert not (t / "error").exists(), (t / "error").read_text(encoding="utf-8")
    assert (t / "note").read_text(encoding="utf-8").strip() == NOTE


@needs_jq
def test_the_report_carries_the_note_beside_no_error(tmp_path):
    run = tmp_path / "run"
    _target(run, "sdb", state="done", note=NOTE)
    _target(run, "sdc", state="done")
    script = (f"export RUN_DIR={q(run)} LOG_FILE={q(run / 'agent.log')}; "
              + f". {posix(AGENT)}/lib/core/common.sh; " + agent_sources("jsonq", "sysinfo", "failmark", "progress", "datareport")
              + 'printf "{%s}\\n" "$(target_extra_json sdb | sed "s/^,//")"; printf "{%s}\\n" "$(target_extra_json sdc | sed "s/^,//")"')
    out = bash(script)
    assert out.returncode == 0, out.stderr
    sdb, sdc = (json.loads(line) for line in out.stdout.strip().splitlines())
    assert sdb.get("note") == NOTE and "error" not in sdb, sdb
    assert "note" not in sdc, sdc


@needs_jq
def test_the_cloner_screen_gets_the_drawer_note_record_after_that_drawer(tmp_path):
    from test_cloner_gui import PORTS, PRELUDE, env, sh, target
    run, gui = tmp_path / "run", tmp_path / "gui"
    run.mkdir(); gui.mkdir()
    (run / "manifest.json").write_text('{"name":"Office 2024"}', newline="\n")
    (run / "response.json").write_text('{"schema":1}', newline="\n")
    for dev in ("sda", "sdb"):
        target(run, dev, "done", 100, 100, "")
    (run / "targets" / "sda" / "note").write_text(NOTE + "\n", encoding="utf-8", newline="\n")
    out = sh(env(run, gui) + PRELUDE + PORTS + "cloner_gui_state")
    assert out.returncode == 0, out.stderr
    lines = (gui / "state").read_text(encoding="utf-8").splitlines()
    notes = [n for n, line in enumerate(lines) if line.startswith("drawer_note=")]
    assert [lines[n] for n in notes] == [f"drawer_note=sda|{NOTE}"], lines
    assert any(lines[k].startswith("drawer=1|sda|done|") for k in range(notes[0])), lines
    assert all("|" + NOTE not in line for line in lines if line.startswith("drawer=")), "not field 6 (error)"


def test_a_dual_boot_disk_keeps_the_windows_sides_note(tmp_path):
    from test_hostname_linux_dual import LINUX_PLAN, WIN_PLAN, box, name
    script, run = box(tmp_path, [WIN_PLAN, LINUX_PLAN], default_etc=True)
    (run / "win/Windows/System32/config/SYSTEM").write_bytes(_hive(41, 40))
    rc, result = name(script)
    assert (rc, result) == (0, {"ok": True, "hostname": "LAB1-05", "method": "offline-registry+etc-hostname",
                                "note": NOTE, "note_code": "hive_dirty"}), result


# --- the cloner's GUI: green, the note in its line ---------------------------------------

def _gui_requirements():
    from test_cloner_gui import _pkgconfig
    return requires_native(
        ("cc", shutil.which("cc") or shutil.which("gcc")),
        ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
        why="native-gui נבנה על המעבדה בלבד")


@_gui_requirements()
def test_the_cloner_row_with_a_note_is_green_and_says_the_note(tmp_path):
    from test_cloner_gui import _build_gui, _count_color, _png_rgb
    from test_room_precheck_native_432_65 import _render
    from test_room_screens_lc_design import CDISKS, _drawer
    binary = _build_gui(tmp_path)
    done = _drawer(1, "sda", "done", 1) + _drawer(3, "sdc", "done", 1)
    _render(binary, "cloner=office365\n" + CDISKS + done + "message=x|y\n", tmp_path / "plain")
    _render(binary, "cloner=office365\n" + CDISKS + done + f"drawer_note=sdc|{NOTE}\nmessage=x|y\n", tmp_path / "note")
    # the old road (#1821's refusal put the reason in `error`, field 6): the measure does see orange
    _render(binary, "cloner=office365\n" + CDISKS + _drawer(1, "sda", "done", 1)
            + _drawer(3, "sdc", "done", 1, "שם המחשב לא נכתב: x") + "message=x|y\n", tmp_path / "error")

    def orange(name: str) -> int:   # theme.c .warn (light) -- a done row's warning (#943)
        _w, _h, ch, px = _png_rgb(tmp_path / f"{name}-cloner-light.png")
        return _count_color(px, ch, (0xC2, 0x54, 0x00), tol=40)
    assert orange("error") > orange("plain") + 100, (orange("error"), orange("plain"))
    assert abs(orange("note") - orange("plain")) < 60, (orange("note"), orange("plain"))


@pytestmark_gui
def test_the_note_is_said_after_the_rows_own_words(driver, tmp_path):
    """The row keeps its own words and its pill ("אומת" / "הושלם"); the note comes after them."""
    import test_readback_progress_2046 as T
    rows = [("done", "1000|1000|verified|-1", ""), ("done", "", "")]
    state = cloner(rows, "rb_mode=full\n") + f"drawer_note=sdb|{NOTE}\ndrawer_note=sdc|{NOTE}\n"
    _, said = T.show(driver, tmp_path, state, "note")
    assert f"אומת — זהה לאימג׳ · {NOTE}" in said, said
    assert f"הושלם · {NOTE}" in said, said
    assert "אומת" in said and "הושלם" in said, said   # the pills


# --- the server and the console ------------------------------------------------------------

MAC = "78:ac:c0:9b:31:67"


def _t(dev: str, **kw) -> dict:
    return {"dev": dev, "state": "done", "bytes_written": 1, "bytes_total": 1, **kw}


def test_the_journal_says_the_note_once_as_info(tmp_path):
    from server import db
    from server.agent import reports
    from server.console import journal_he
    conn = db.connect(tmp_path / "imagesrv.db")
    conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds,"
        " state, opened_by, created_at, last_join_at, started_at)"
        " VALUES ('ses_3167', 'grp_CLONERS', 'img_x', 'p', 1, 300, 'running', 't', ?, 0, ?)",
        (db.now_iso(), db.now_iso()))
    conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at)"
                 " VALUES ('ses_3167', ?, 'writing', ?)", (MAC, db.now_iso()))
    conn.commit()
    targets = [_t("sdb", note=NOTE), _t("sdc"), _t("sdd", state="writing", note="x")]
    for _ in range(2):
        assert reports.ingest(conn, {"session_id": "ses_3167", "mac": MAC, "state": "done", "targets": targets})["ok"]
    rows = [r["detail"] for r in conn.execute("SELECT detail FROM journal WHERE event = 'target_note'")]
    assert rows == [f"{MAC} sdb in ses_3167 — {NOTE}"], rows
    assert journal_he.severity("target_note") == "info"
    assert journal_he.EVENTS_HE["target_note"] == "הדיסק הושלם, עם הערה"


def test_drawer_list_carries_the_note_of_a_done_drawer_only(tmp_path):
    from server import db
    from server.rounds import room
    conn = db.connect(tmp_path / "imagesrv.db")
    db.net_seen(conn, MAC, "10.0.0.7", disks_json=json.dumps(
        [{"dev": d, "serial": f"S-{d}", "port": n} for n, d in enumerate(("sdb", "sdc", "sdd"), 1)]))
    member = {"targets_json": json.dumps([_t("sdb", note=NOTE), _t("sdc"),
                                          _t("sdd", state="writing", note=NOTE)])}
    got = {d["dev"]: d.get("note") for d in room.drawer_list(conn, MAC, set(), member)}
    assert got == {"sdb": NOTE, "sdc": None, "sdd": None}, got


NODE = shutil.which("node")


@requires_native(("node", NODE))
def test_the_console_card_says_the_note_in_grey_not_orange(tmp_path):
    js = console_js()
    fn = re.search(r"^function roundDskHtml\(m, s\) \{.*?^\}", js, re.M | re.S)
    assert fn, "roundDskHtml is gone from console.js"
    probe = tmp_path / "probe.cjs"
    probe.write_text(
        "const esc = (x) => String(x);\n"
        "const slotLabel = () => 'דיסק 1', rowLabel = () => 'x', slotClass = () => 'ok', stallText = () => '', drawerPulled = () => false;\n"
        "const roomOperator = () => false, encodeId = (x) => x, driversTone = () => 'muted', driversOutcomeText = () => '';\n"
        "const readbackLine = () => '', stagesLine = () => '', bayEmphasis = () => ' bay-emphasis ok', diskTitle = () => '';\n"
        + fn.group(0) + f"""
const d = (extra) => ({{ state: "done", bytes_written: 1, bytes_total: 1, ...extra }});
console.log(JSON.stringify({{
  note: roundDskHtml({{}}, {{ n: 1, live: true, disk: d({{ note: {json.dumps(NOTE)} }}) }}),
  plain: roundDskHtml({{}}, {{ n: 1, live: true, disk: d({{}}) }}),
}}));
""", encoding="utf-8", newline="\n")
    out = subprocess.run([NODE, str(probe)], capture_output=True, text=True, encoding="utf-8",
                         stdin=subprocess.DEVNULL, timeout=60)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert f'<span class="dline">{NOTE}</span>' in got["note"], got
    assert "dline warn" not in got["note"], got
    assert NOTE not in got["plain"], got
