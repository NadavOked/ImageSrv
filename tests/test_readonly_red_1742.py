"""#1742 -- a read-only disk is red, and is red *before* the clone (Nadav 18/10).

D6 (fault injection, cloner 2, rc6, 18/10 08:13): ``blockdev --setro`` on drawer
3 before a pull. The station showed a visible failure ("dd: error writing
'/dev/sda': Operation not permitted"), but the journal recorded
``disk_failure ... cause=unclassified`` -- shown orange -- and, before the clone,
the disk was offered as a good target.

The fix rides #1732's mechanism, not a second one: a read-only disk is SMART's
own red -- verdict ``fail`` with the reason ``readonly`` -- so every screen and
gate that keys off ``fail`` (the hello's ``smart``, the cloner's idle line, the
local clone's inventory, the build station's ``dsmart``) treats it as red.

Both sides are tested here:

* **before** -- ``/sys/block/<d>/ro`` read on every call (not only at the idle
  probe), a failed read is "not checked", never "ok" (principle 5);
* **during** -- a write refused with EPERM/EROFS is ``cause=readonly`` on the
  server (``ata_cause.py``), and the agent paints that disk red from the
  server's memory, by serial.

The fakes follow the house pattern: fake one step (``smartctl``, sysfs), run the
real function, and read what the operator would see.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from server.disks import ata_cause, disk_failures
from server import db
from test_smart import BASH, HEALTH_OK, PRELUDE, env, full, make_smartctl, posix
from native import requires_native

REPO = Path(__file__).resolve().parent.parent
AGENT = REPO / "agent"

#: the exact line D6 put in front of the operator (18/10 08:13, cloner 2)
D6_ERROR = ("הדיסק לא מקבל כתיבה — התחלה (0 MiB): dd: error writing '/dev/sda': "
            "Operation not permitted")

bash = requires_native(("bash", BASH))


def sh(script: str) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
                          capture_output=True, text=True, cwd=str(REPO),
                          stdin=subprocess.DEVNULL, timeout=120)


def rig(tmp_path: Path, ro: str | None, serial: str = "S6PNNS0T123456"):
    """A run dir, a /dev with sda, a sysfs with sda's ``ro`` (None = no file at
    all -- the read fails) and serial, and a smartctl that says PASSED and
    leaves a mark every time it runs."""
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    dev = tmp_path / "dev"; dev.mkdir(exist_ok=True); (dev / "sda").write_bytes(b"\x00")
    blk = tmp_path / "sys" / "sys" / "block" / "sda"; (blk / "device").mkdir(parents=True, exist_ok=True)
    (blk / "device" / "serial").write_text(serial + "\n", newline="\n")
    if ro is not None:
        (blk / "ro").write_text(ro + "\n", newline="\n")
    mark = tmp_path / "smartctl.ran"
    sc = make_smartctl(tmp_path, full(HEALTH_OK))
    wrapper = tmp_path / "smartctl-marked"
    wrapper.write_text(f"#!/bin/sh\necho x >> {posix(mark)!r}\nexec {sc!r} \"$@\"\n", newline="\n")
    subprocess.run([BASH, "-c", f"chmod +x {posix(wrapper)!r}"], check=True,
                   stdin=subprocess.DEVNULL, timeout=30)
    return env(run, dev, sysroot=tmp_path / "sys", smartctl=posix(wrapper)) + PRELUDE, run, blk, mark


def set_ro(blk: Path, value: str) -> None:
    (blk / "ro").write_text(value + "\n", newline="\n")


# --- before the clone: /sys/block/<d>/ro ----------------------------------------

@bash
def test_a_read_only_disk_is_red_with_the_reason_and_smartctl_is_not_asked(tmp_path):
    base, run, _, mark = rig(tmp_path, ro="1")
    out = sh(base + "smart_probe sda; smart_field sda 2")
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["fail", "readonly"]
    assert not mark.exists(), "the read-only verdict needs no smartctl"


@bash
def test_a_writable_healthy_disk_is_still_ok(tmp_path):
    # the control: ro=0 and PASSED -- green, as before
    base, *_ = rig(tmp_path, ro="0")
    assert sh(base + "smart_probe sda").stdout.split() == ["ok"]


@bash
@pytest.mark.parametrize("ro", [None, "", "x"])
def test_an_unread_ro_is_not_checked_never_ok(tmp_path, ro):
    # principle 5: "could not read /sys/block/sda/ro" is not "writable"
    base, *_ = rig(tmp_path, ro=ro)
    out = sh(base + "smart_probe sda; smart_field sda 2")
    assert out.stdout.split() == ["unchecked", "ro_unread"], out.stderr


@bash
def test_setro_after_the_idle_probe_reaches_the_hello_before_the_pull(tmp_path):
    # D6's order: the cloner probed while idle (ok, cached), then blockdev --setro.
    # The hello and the idle screen read the cache only -- the read-only bit must
    # still get there, without another smartctl.
    base, run, blk, mark = rig(tmp_path, ro="0")
    assert sh(base + "smart_probe_idle sda; smart_hello_field sda").stdout.split() == ["ok"]
    ran = mark.read_text().count("x")
    set_ro(blk, "1")
    out = sh(base + "smart_hello_field sda; smart_field sda 2")
    assert out.stdout.split() == ["fail", "readonly"]
    assert mark.read_text().count("x") == ran, "the hello never runs smartctl"


@bash
def test_back_to_writable_drops_the_red_and_probes_again(tmp_path):
    base, run, blk, _ = rig(tmp_path, ro="1")
    assert sh(base + "smart_probe sda").stdout.split() == ["fail"]
    set_ro(blk, "0")
    assert sh(base + "smart_probe sda").stdout.split() == ["ok"]


@bash
def test_the_hello_carries_red_and_readonly(tmp_path):
    # the console and the server's round gate read these two fields (#1732: a
    # drawer whose hello said smart=fail is refused as a target)
    base, *_ = rig(tmp_path, ro="1")
    out = sh(base + "smart_probe sda >/dev/null; build_disk_entry sda")
    assert out.returncode == 0, out.stderr
    entry = json.loads(out.stdout)
    assert (entry["smart"], entry["smart_reason"]) == ("fail", "readonly")


@bash
def test_the_build_stations_disk_line_is_redrawn_when_ro_changes(tmp_path):
    # guistate.sh keeps one dsmart= line per disk (the serial is not read every
    # second); a line written while the disk was writable must not keep it green
    # after --setro, nor red after it is writable again. Faked: jq (the disk list
    # of station.json) and disk_port -- the rest is the real gui_disk_smart.
    base, run, blk, _ = rig(tmp_path, ro="0")
    gui = tmp_path / "gui"; gui.mkdir()
    (gui / "station.json").write_text("{}", newline="\n")
    run_it = (base + f". {posix(AGENT)}/lib/gui/guistate.sh; export GUI_DIR={posix(gui)!r}; "
              "jq() { echo sda; }; disk_port() { echo 3; }; gui_disk_smart")
    first = sh(run_it)
    assert first.stdout.split() == ["dsmart=sda|ok|ok|S6PNNS0T123456|3"], first.stderr
    set_ro(blk, "1")
    assert sh(run_it).stdout.split() == ["dsmart=sda|fail|readonly|S6PNNS0T123456|3"]
    set_ro(blk, "0")
    assert sh(run_it).stdout.split() == ["dsmart=sda|ok|ok|S6PNNS0T123456|3"]


# --- during the clone: EPERM/EROFS -> cause=readonly ---------------------------

@pytest.mark.parametrize("error", [
    D6_ERROR,
    "dd: error writing '/dev/sdb': Read-only file system",
])
def test_a_refused_write_is_readonly_not_unclassified(error):
    assert ata_cause.classify([], error) == "readonly"


def test_readonly_wins_over_line_noise_in_the_same_window():
    # EPERM is the write's own answer: it never reached the wire or the disk
    lines = ["ata3: limiting SATA link speed to 3.0 Gbps"]
    assert ata_cause.classify(lines, D6_ERROR) == "readonly"


def test_other_errors_are_classified_as_before():
    assert ata_cause.classify([], "dd: error writing '/dev/sda': Input/output error") == "unclassified"
    assert ata_cause.classify(["ata1.00: error: { UNC }"], "") == "disk"


@pytest.fixture()
def conn(tmp_path: Path):
    return db.connect(tmp_path / "t.db")   # as test_disk_failures.py


def test_the_journal_says_readonly_and_the_hello_returns_it(conn):
    target = {"dev": "sda", "serial": "S6PNNS0T123456", "port": 3, "ata_port": 3,
              "state": "failed", "error": D6_ERROR, "ata_log": []}
    row = disk_failures.record(conn, "sess-d6", "aa:bb:cc:dd:ee:02", target)
    assert row is not None and row["cause"] == "readonly"
    detail = conn.execute("SELECT detail FROM journal WHERE event = 'disk_failure'").fetchone()[0]
    assert "cause=readonly" in detail
    back = disk_failures.open_for(conn, "aa:bb:cc:dd:ee:02",
                                  [{"dev": "sda", "serial": "S6PNNS0T123456", "port": 3}])
    assert [f["cause"] for f in back] == ["readonly"], "a read-only failure colours the disk"


# --- the server's memory reaches the agent: red, by serial ---------------------

def _memory(run: Path, line: str) -> None:
    (run / "disk_failures").write_text("ok\n" + line + "\n", newline="\n")


@bash
def test_the_servers_readonly_memory_paints_the_same_disk_red(tmp_path):
    # after a reboot /sys/block/sda/ro is 0 again; the server remembers
    base, run, *_ = rig(tmp_path, ro="0")
    _memory(run, "S6PNNS0T123456|3|readonly")
    assert sh(base + "smart_probe sda; smart_field sda 2").stdout.split() == ["fail", "readonly"]


@bash
def test_a_new_disk_in_that_slot_is_not_read_only(tmp_path):
    # read-only belongs to the disk, not to the slot
    base, run, *_ = rig(tmp_path, ro="0", serial="NEWDISK0001")
    _memory(run, "S6PNNS0T123456|3|readonly")
    assert sh(base + "smart_probe sda; smart_field sda 2").stdout.split() == ["ok", "ok"]


@bash
def test_an_orange_cause_from_the_server_is_not_turned_red(tmp_path):
    base, run, *_ = rig(tmp_path, ro="0")
    _memory(run, "S6PNNS0T123456|3|cable")
    assert sh(base + "smart_probe sda").stdout.split() == ["ok"]


# --- the screens say why: "הדיסק במצב קריאה בלבד", not "SMART תקול" -------------

READONLY_HE = "הדיסק במצב קריאה בלבד"


@requires_native(("bash", BASH), "jq", why="gui_state קורא את /room ב-jq")
def test_the_build_stations_room_record_carries_why_red(tmp_path):
    # the build station picks the cloners' drawers from GET /room: the drawer's
    # smart_reason travels as room_drawer field 27, only when there is one (T8 keeps 23 fields without a linux object)
    from test_room_drivers_gui_contract import _drawer, _state
    red = {**_drawer(3, "S3"), "smart": "fail", "smart_reason": "readonly"}
    room = {"round": None, "machines": [{
        "name": "2", "mac": "78:ac:c0:9b:11:c2", "awake": True, "joined": False,
        "fresh_drawers": 2, "state": "idle", "drawer_count": 3,
        "drawer_list": [_drawer(1, "S1"), red]}]}
    rows = {r[1]: r for r in (line.split("=", 1)[1].split("|")
                              for line in _state(tmp_path, room, []) if line.startswith("room_drawer="))}
    assert (rows["3"][5], rows["3"][26]) == ("fail", "readonly")
    assert len(rows["1"]) == 23, "a green drawer has no reason and the record is as T8 wrote it"
    assert rows["3"][23:26] == ["", "", ""], "no linux object: 24-26 empty, not shifted"


CC = shutil.which("cc") or shutil.which("gcc")


def _c_function(name: str) -> str:
    text = (REPO / "native-gui" / "src" / "screens_rounds.c").read_text(encoding="utf-8")
    start = re.search(r"\nconst char \*" + name + r"\([^)]*\) \{", text)
    assert start, f"{name}() is not defined in screens_rounds.c"
    depth, i = 1, start.end()
    while depth:
        depth += (text[i] == "{") - (text[i] == "}")
        i += 1
    return text[start.start():i]


@requires_native(("cc", CC), why="the three functions are compiled alone, no cairo")
def test_the_native_screens_say_read_only_and_keep_the_smart_words(tmp_path):
    src = tmp_path / "t.c"
    src.write_text("#include <stdio.h>\n#include <string.h>\n"
                   "const char *smart_reason_text(const char *reason);\n"
                   + _c_function("smart_he") + _c_function("smart_reason_text")
                   + _c_function("smart_he_why")
                   + "\nint main(int c, char **v) { (void)c; puts(smart_he_why(v[1], v[2])); }\n",
                   encoding="utf-8", newline="\n")
    built = subprocess.run([CC, "-Wall", "-Wextra", "-o", str(tmp_path / "t"), str(src)],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
    assert built.returncode == 0, built.stderr

    def said(smart: str, reason: str) -> str:
        out = subprocess.run([str(tmp_path / "t"), smart, reason], capture_output=True,
                             text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=30)
        assert out.returncode == 0, out.stderr
        return out.stdout.strip()
    assert said("fail", "readonly") == READONLY_HE
    assert said("fail", "health_failed") == "SMART תקול", "a SMART red still says SMART"
    assert said("passed", "readonly") == "תקין", "only a red disk is read-only red"


NODE = shutil.which("node")


@requires_native(("node", NODE))
def test_the_console_says_read_only_in_red(tmp_path):
    js = (REPO / "server" / "static" / "console.js").read_text(encoding="utf-8")
    pick = [re.search(r"^const SMART_REASON_HE = .*$", js, re.M),
            re.search(r"^function smartF1Text\(reason\) \{.*?^\}", js, re.M | re.S),
            re.search(r"^const FAILURE_CAUSE_HE = .*$", js, re.M),
            re.search(r"^function linkSlow\(d\) \{.*?^\}", js, re.M | re.S),
            re.search(r"^function slotClass\(s\) \{.*?^\}", js, re.M | re.S)]
    assert all(pick), "a function this test reads is gone from console.js"
    probe = tmp_path / "probe.cjs"
    probe.write_text("\n".join(m.group(0) for m in pick) + """
const out = {
  ro: smartF1Text("readonly"),
  f1: smartF1Text("pending_sectors"),
  cause: FAILURE_CAUSE_HE.readonly,
  memRed: slotClass({ disk: { smart: "ok" }, fail: { cause: "readonly" } }),
  memCable: slotClass({ disk: { smart: "ok" }, fail: { cause: "cable" } }),
};
console.log(JSON.stringify(out));
""", encoding="utf-8", newline="\n")
    run = subprocess.run([NODE, str(probe)], capture_output=True, text=True, encoding="utf-8",
                         stdin=subprocess.DEVNULL, timeout=60)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert out["ro"] == READONLY_HE and "F1" not in out["ro"]
    assert out["f1"].startswith("ה-BIOS יעצור ב-F1"), "the SMART reds keep their words"
    assert out["cause"] == READONLY_HE
    assert (out["memRed"], out["memCable"]) == ("err", "warn"), "read-only memory is red, a cable orange"


# --- after #1735 (#1732 on rc6): every refusal says why --------------------------
#
# #1732 refuses a red disk on every path with "SMART נכשל — …". A read-only disk is
# that red, but it did not fail SMART: the refusal says "הדיסק במצב קריאה בלבד".
# Faked: smart_probe (it stores the reason as the real one does -- _smart_store).

SMART_RED = "SMART נכשל — לא כותבים על הדיסק הזה; אפשר להעתיק ממנו או לחלץ לשרת"
RO_PROBE = 'smart_probe() { case "$1" in {ro}) _smart_store "$1" fail readonly; echo fail ;; *) echo ok ;; esac; }; '


@bash
@pytest.mark.parametrize("probe,said", [
    (RO_PROBE.replace("{ro}", "sda"), READONLY_HE),
    ('smart_probe() { _smart_store "$1" fail health_failed; echo fail; }; ', SMART_RED + " (בדיקת הבריאות נכשלה)"),   # #1861: the reason in words
])
def test_the_round_gate_skips_a_read_only_disk_in_its_words(tmp_path, probe, said):
    from test_smart import preflight
    written, run, err = preflight(tmp_path, full(HEALTH_OK), extra=probe)
    assert written == "", err
    assert (run / "targets/sda/state").read_text().strip() == "skipped"
    assert (run / "targets/sda/error").read_text(encoding="utf-8").strip() == said, err


@bash
def test_the_local_clone_fails_a_read_only_target_in_its_words(tmp_path):
    # as test_red_disk_never_target_1732's: sdb red, the source sdc; lc_run stops after the targets
    from test_local_clone_agent import run
    script = (
        'LC="$RUN_DIR/lc"; mkdir -p "$LC/inv" "$LC/measure"; '
        'for d in sda sdb sdc; do echo "x|$d" > "$LC/inv/$d.line"; done; echo sdc > "$LC/source"; : > "$LC/measure/sdc"; '
        'disk_serial() { printf "S-%s" "$1"; }; '
        'lc_inv_field() { case "$2" in 4) printf "S-%s" "$1" ;; 12) echo 1 ;; *) echo ;; esac; }; '
        'lc_verdict_for() { echo "fits||"; }; lc_measure() { :; }; '
        + RO_PROBE.replace("{ro}", "sdb") +
        'lc_source_parts() { LC_ERROR=stopped-here; return 1; }; '
        'lc_end() { kill "$_lr_beat" 2>/dev/null; }; ata_capture() { :; }; '
        'lc_run sdc "sda sdb" 0; '
        'echo "sdb=$(cat "$RUN_DIR/targets/sdb/state")|$(cat "$RUN_DIR/targets/sdb/error")"'
    )
    out = run(tmp_path, script)
    assert out.returncode == 0, out.stderr
    line = next(x for x in out.stdout.splitlines() if x.startswith("sdb="))
    assert line == f"sdb=failed|{READONLY_HE} — לא נכתב", (line, out.stderr)


@requires_native(("bash", BASH), "jq")
def test_restore_to_this_disk_refuses_a_read_only_disk_in_its_words(tmp_path):
    from test_single_restore_progress_gui import body, cards, restore
    run_restore = ('run_restore() { echo STARTED >> "$RUN_DIR/trace"; return 0; }; '
                   f'. {posix(AGENT)}/lib/smart/smart.sh; ' + RO_PROBE.replace("{ro}", "sda"))
    r = restore(tmp_path, run_restore)
    assert "STARTED" not in r["trace"], r["out"]
    last = body(cards(r["seen"])[-1]) if r["seen"] else ""
    assert last == f"message=השחזור לא התחיל — לא נכתב דבר|‏{READONLY_HE} · המחשב יופעל מחדש\n", (last, r["out"])
    assert "TEST-REBOOT: restore: sda is read-only" in r["out"], r["out"]


@requires_native(("cc", CC), why="the functions are compiled alone, no cairo")
def test_the_native_refusal_and_the_failed_last_words_name_read_only(tmp_path):
    # red_refusal_he: the local clone's verdict line and the refusal window's lead;
    # reason_words: the window's reason of an orange failed_last whose cause is readonly
    dlg = (REPO / "native-gui" / "src" / "smartdlg.c").read_text(encoding="utf-8")
    words = re.search(r"\nstatic void reason_words\([^)]*\) \{.*?\n\}\n", dlg, re.S)
    assert words, "reason_words() is gone from smartdlg.c"
    src = tmp_path / "t.c"
    src.write_text("#include <stdio.h>\n#include <string.h>\n"
                   "const char *smart_reason_text(const char *reason);\n"
                   + _c_function("smart_he") + _c_function("smart_reason_text") + _c_function("red_refusal_he")
                   + words.group(0)
                   + "\nint main(int c, char **v) { (void)c; char o[256];\n"
                     "  if (!strcmp(v[1], \"refusal\")) puts(red_refusal_he(v[2]));\n"
                     "  else { reason_words(v[2], v[3], o, sizeof o); puts(o); } }\n",
                   encoding="utf-8", newline="\n")
    built = subprocess.run([CC, "-Wall", "-Wextra", "-o", str(tmp_path / "t"), str(src)],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
    assert built.returncode == 0, built.stderr

    def said(*args: str) -> str:
        out = subprocess.run([str(tmp_path / "t"), *args], capture_output=True, text=True,
                             encoding="utf-8", stdin=subprocess.DEVNULL, timeout=30)
        assert out.returncode == 0, out.stderr
        return out.stdout.strip()
    assert said("refusal", "readonly") == READONLY_HE
    assert said("refusal", "health_failed") == SMART_RED
    assert said("words", "failed_last", "readonly") == "נכשל בשיכפול הקודם — סיבה: " + READONLY_HE
    assert said("words", "failed_last", "cable") == "נכשל בשיכפול הקודם — סיבה: כבל/חריץ"


def test_the_native_screens_use_the_reason_not_a_fixed_smart_sentence():
    # the drawing code (cairo) is built only on the lab; what decides the words is read here
    src = REPO / "native-gui" / "src"
    local = (src / "screens_local.c").read_text(encoding="utf-8")
    dlg = (src / "smartdlg.c").read_text(encoding="utf-8")
    state = (src / "state.c").read_text(encoding="utf-8")
    standby = (src / "screens_standby.c").read_text(encoding="utf-8")
    assert "red_refusal_he(d->smart_reason)" in local, "the local clone's verdict line of a red target"
    assert 'red_refusal_he(d->readonly ? "readonly" : "")' in dlg, "the refusal window's lead"
    assert 'd->readonly = reason && !strcmp(reason, "readonly")' in dlg
    assert SMART_RED not in local and SMART_RED not in dlg, "one sentence, in red_refusal_he"
    # failed_last (the server remembers the slot): readonly is a known cause, not "לא סווג"
    assert re.search(r'failed_last"\) \? strcmp\(cause, "cable"\) && strcmp\(cause, "disk"\) && strcmp\(cause, "readonly"\)', state)
    assert '"סיבה: הדיסק במצב קריאה בלבד"' in standby
