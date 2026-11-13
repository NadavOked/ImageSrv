"""#2987 / #2988 (Nadav 12/11, cloner 2 at work): what a drawer says to the operator.

#2987 -- the failure sentences were the diagnosis itself: "/dev/sdb/", "RAID",
"host5=keep_firmware_settings, לא max_performance -- ייתכן שהכונן נרדם" on a disk
Nadav pulled by hand, and a console card that joined two disks in one paragraph
under "2 דיסק אדום". Now the agent makes one short sentence from the diagnosis
(``op_say``, agent/lib/core/opsay.sh): ``error_short`` on the wire beside
``error``, field 6 of a failed drawer's ``drawer=`` on the cloner's screen. The
diagnosis stays in ``error`` (the log, the hover, ata_cause.py), and the slumber
guess is written only when the port gave no sign of a pull.

#2988 -- a pulled disk or cable left the drawer on "כותב" ~30 s before anything
showed. ``drawer_stall_tick`` (agent/lib/restore/stall.sh) says ``waiting_disk`` /
``waiting_net`` within seconds, on evidence, logs a disruption that recovered,
and decides nothing.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from agentlib import agent_sources
from native import requires_native
from test_agent import BASH, posix, sh

pytestmark = requires_native(("bash", BASH))

PULLED = ("מחיצה 1: הכונן נעלם מהמערכת (נשלף או נותק) (מדיניות החשמל של SATA: "
          "host5=keep_firmware_settings, לא max_performance — ייתכן שהכונן נרדם)")
RAID_TAIL = "לא הצלחנו לבדוק אם /dev/sdb חבר במערך RAID: קריאת סוף הדיסק נכשלה — לא נגענו בדיסק"
#: what the operator must never read (the issue's list)
RAW = ("/dev/", "host", "RAID", "keep_firmware", "max_performance", "נרדם", "xxh64", "ntfsresize", " -- ",
       "fanout", "partclone", "rc=", "partition")


def _say_room(tmp_path: Path, total: int = 1000, written: int = 740) -> Path:
    run = tmp_path / "run"; t = run / "targets" / "sdb"; t.mkdir(parents=True)
    (t / "total").write_text(f"{total}\n", newline="\n")
    (t / "bytes.0").write_text(f"{written}\n", newline="\n")
    return run


def say(tmp_path: Path, reason: str, ctx: str = "", **kw) -> str:
    run = _say_room(tmp_path, **kw)
    out = sh(f"export RUN_DIR={posix(run)!r} LOG_FILE=/dev/null; "
             + agent_sources("common", "sysinfo", "progress", "failmark", "opsay")
             + f"op_say sdb {shlex.quote(reason)} {ctx}")
    return out.strip()


@pytest.mark.parametrize("reason, want", [
    (PULLED, "הדיסק נעלם באמצע הכתיבה (74%). בדקו שהמגירה סגורה והכבל מחובר, וכתבו אותו שוב בגל הבא."),
    (RAID_TAIL, "לא ניתן לקרוא את סוף הדיסק — לא נכתב. בדקו דיסק, כבל או מגירה."),
    ("לא הצלחנו לבדוק אם /dev/sdb חבר במערך RAID: blkid rc=4 — לא נגענו בדיסק",
     "לא ניתן לבדוק את הדיסק לפני הכתיבה — לא נכתב. בדקו דיסק, כבל או מגירה."),
    ("/dev/sdb חבר במערך RAID של Intel (isw_raid_member) — העבירו את ה-BIOS ל-AHCI; לא נגענו בדיסק",
     "הדיסק מוגדר כחלק ממערך דיסקים של Intel — לא נכתב. העבירו את ה-BIOS ל-AHCI."),
    ("מחיצה 1: הקרנל דחה כתיבה לכונן: I/O error, dev sdb, sector 5 op 0x1:(WRITE)",
     "הדיסק דחה כתיבה באמצע הכתיבה (74%). בדקו דיסק, כבל או מגירה, וכתבו אותו שוב בגל הבא."),
    ("partition 2: הכתיבה לדיסק נכשלה (fanout: stalled with no write progress; partclone rc=1)",
     "הדיסק לא עמד בקצב והוצא מהסבב (74%). בדקו דיסק, כבל או מגירה, וכתבו אותו שוב בגל הבא."),
    ("partition 2: הכתיבה לדיסק נכשלה (fanout: write error; partclone rc=1; Write failed)",
     "מחיצה 2: הכתיבה לדיסק נכשלה"),
    ("מחיצה 2: xxh64 של מה שהיעד קיבל (abcdef123456) שונה מהמקור (123456abcdef)",
     "מה שנכתב אינו זהה למקור — כתבו אותו שוב בגל הבא."),
    ("טבלת המחיצות הוגדלה, מערכת הקבצים לא: ntfsresize נכשל (rc=1)",
     "מערכת הקבצים לא הורחבה לכל הדיסק. אפשר להרחיב את הכונן ב-Windows בניהול הדיסקים."),
    ("מחיצה 3: mkswap נכשל", "מחיצה 3: הכתיבה נכשלה — הפרטים ביומן"),
    ("dd: error writing x: No space left on device", "הכתיבה נכשלה — הפרטים ביומן"),
    ("הדיסק במצב קריאה בלבד — לא נכתב", "הדיסק במצב קריאה בלבד — לא נכתב"),   # Hebrew already: as it is
])
def test_the_operator_reads_a_short_sentence_not_the_diagnosis(tmp_path, reason, want):
    got = say(tmp_path, reason)
    assert got == want
    assert not [w for w in RAW if w in got], got


def test_no_percent_that_was_not_measured_and_no_next_wave_on_a_local_copy(tmp_path):
    assert say(tmp_path, PULLED, total=0) == \
        "הדיסק נעלם באמצע הכתיבה. בדקו שהמגירה סגורה והכבל מחובר, וכתבו אותו שוב בגל הבא."
    assert say(tmp_path / "lc", PULLED, "lc").endswith("וכתבו אותו שוב.")


def test_the_report_carries_both_the_sentence_and_the_whole_diagnosis(tmp_path):
    run = _say_room(tmp_path)
    t = run / "targets" / "sdb"
    (t / "state").write_text("failed\n", newline="\n")
    (t / "error").write_text(PULLED + "\n", encoding="utf-8", newline="\n")
    (run / "state").write_text("partial\n", newline="\n")
    out = sh(f"export RUN_DIR={posix(run)!r} LOG_FILE=/dev/null; "
             + agent_sources("common", "sysinfo", "progress", "datareport", "opsay", "failmark", "stall")
             + "build_progress ses_1 aa:bb:cc:dd:ee:ff")
    target = json.loads(out)["targets"][0]
    assert target["error"] == PULLED   # unchanged: the hover, the journal, ata_cause.py's words
    assert target["error_short"].startswith("הדיסק נעלם באמצע הכתיבה (74%).")
    assert "stall" not in target


# --- the slumber guess: only with no sign of a pull (#1827 kept, #2987) ------------

def test_a_pull_the_port_saw_does_not_blame_the_power_policy(tmp_path):
    from test_lc_pulled_link_2045b import GONE, METAL, box
    from test_local_clone_agent import run
    b = box(tmp_path)
    (b / "kmsg").write_text("\n".join(METAL) + "\n")
    stubs = tmp_path / "stubs"; stubs.mkdir()
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "sata_lpm").write_text("host0=keep_firmware_settings\n", newline="\n")
    out = run(tmp_path,
              f'printf "#!/bin/sh\\ncat {posix(b / "kmsg")}\\n" > {posix(stubs)}/dmesg; '
              f'chmod 0755 {posix(stubs)}/dmesg; export PATH="{posix(stubs)}:$PATH"; '
              'target_init sdb 100; target_set sdb writing; sleep 4 & s=$!; '
              'drawer_watch "$s" 3 sdb; wait "$s"; echo "error=$(cat "$RUN_DIR/targets/sdb/error")"',
              timeout=60, SYSROOT=posix(b), WAIT_HELPER_S="2")
    assert out.returncode == 0, out.stderr
    got = dict(l.split("=", 1) for l in out.stdout.splitlines() if l.startswith("error="))
    assert got["error"] == f"מחיצה 3: {GONE}", got["error"]   # DevExch + link down: a pull, no "נרדם"


# --- #2988: standing is said in seconds ----------------------------------------------

def _stall_run(tmp_path: Path, steps: list[tuple[int, int, int, int | None]], carrier: str = "1",
               tail: str = "", source_ended: bool = False) -> dict:
    """steps: (now, sda bytes, sdb bytes, stream bytes or None = no stream yet). One tick each."""
    run = tmp_path / "run"
    for d in ("sda", "sdb"):
        (run / "targets" / d).mkdir(parents=True)
        (run / "targets" / d / "state").write_text("writing\n", newline="\n")
    net = tmp_path / "sys" / "class" / "net"
    (net / "eth0").mkdir(parents=True); (net / "eth0" / "carrier").write_text(carrier + "\n", newline="\n")
    (net / "lo").mkdir(); (net / "lo" / "carrier").write_text("1\n", newline="\n")   # lo never counts
    if source_ended:
        (run / "source.rc").write_text("0\n", newline="\n")
    ticks = "".join(
        f'step {now} {a} {b} {"" if src is None else src}; ' for now, a, b, src in steps)
    out = sh(f"export RUN_DIR={posix(run)!r} SYSFS_ROOT={posix(tmp_path / 'sys')!r} WAIT_STREAM_STALL_S=300; "
             + agent_sources("common", "sysinfo", "waits", "progress", "failmark", "stall")
             + 'date() { cat "$RUN_DIR/clock"; }; log() { echo "$*" >> "$RUN_DIR/log"; }; '   # common.sh sets its own LOG_FILE
             'step() { echo "$1" > "$RUN_DIR/clock"; echo "$2" > "$RUN_DIR/targets/sda/bytes.0"; '
             'echo "$3" > "$RUN_DIR/targets/sdb/bytes.0"; if [ -n "$4" ]; then echo "$4" > "$RUN_DIR/bytes.raw"; fi; '
             'drawer_stall_tick "sda sdb"; }; ' + ticks + tail)
    got = {"out": out, "log": (run / "log").read_text(encoding="utf-8") if (run / "log").exists() else ""}
    for d in ("sda", "sdb"):
        f = run / "targets" / d / "stall"
        got[d] = f.read_text().split() if f.exists() else None
    return got


#: sda stops taking bytes after 101; sdb and the stream go on -- its own disk stands
DISK = [(100, 10, 10, 20), (101, 20, 20, 40), (102, 20, 30, 60), (103, 20, 40, 80), (104, 20, 50, 100)]


def test_a_drawer_that_stops_while_the_stream_flows_is_waiting_for_its_disk(tmp_path):
    got = _stall_run(tmp_path, DISK, tail='stall_json sda; echo; stall_json sdb')
    assert got["sda"] == ["waiting_disk", "101", "150"]   # since its last byte; fanout's no-progress ceiling
    assert got["sdb"] is None
    assert got["out"].strip() == ',"stall":"waiting_disk","stall_s":3,"stall_limit_s":150'
    assert "sda: no bytes for 3s -- waiting_disk" in got["log"]


def test_it_moves_again_and_the_log_says_a_disruption_recovered(tmp_path):
    got = _stall_run(tmp_path, DISK + [(109, 30, 90, 180)])
    assert got["sda"] is None
    assert "sda: the bytes moved again after 8s (waiting_disk) -- a disruption that recovered" in got["log"]


def test_the_whole_stream_stopping_is_the_network(tmp_path):
    got = _stall_run(tmp_path, [(100, 10, 10, 20), (101, 20, 20, 40), (102, 20, 20, 40), (104, 20, 20, 40)])
    assert got["sda"] == ["waiting_net", "101", "300"] and got["sdb"] == ["waiting_net", "101", "300"]


def test_no_carrier_is_the_network_before_the_stream_ages(tmp_path):
    # the stream moved a moment after the drawers (1 s): no evidence either way -- unless the cable is out
    steps = [(100, 10, 10, 20), (101, 20, 20, 40), (102, 20, 20, 60), (104, 20, 20, 60)]
    assert _stall_run(tmp_path / "up", steps)["sda"] is None
    assert _stall_run(tmp_path / "down", steps, carrier="0")["sda"][0] == "waiting_net"


def test_a_sibling_held_by_the_stalled_disk_is_not_called_the_network(tmp_path):
    held = DISK + [(105, 20, 50, 100), (108, 20, 50, 100)]   # sda's ring is full: everything stops at 104
    got = _stall_run(tmp_path, held)
    assert got["sda"][0] == "waiting_disk"
    assert got["sdb"] is None


@pytest.mark.parametrize("steps, ended", [
    ([(100, 10, 10, 20), (110, 10, 10, 20)], False),           # the first byte's wait is not a stall
    ([(100, 10, 10, 20), (101, 20, 20, 40), (110, 20, 20, 40)], True),   # the stream ended: draining, not the network
    ([(100, 10, 10, None), (101, 20, 20, None), (110, 20, 20, None)], False),   # no stream counter: nothing to judge
])
def test_no_stall_without_evidence(tmp_path, steps, ended):
    got = _stall_run(tmp_path, steps, source_ended=ended)
    assert got["sda"] is None and got["sdb"] is None, got


def test_a_drawer_that_failed_is_no_longer_standing(tmp_path):
    got = _stall_run(tmp_path, DISK, tail='echo failed > "$RUN_DIR/targets/sda/state"; step 106 20 70 140; '
                                          'stall_json sda; echo end')
    assert got["sda"] is None and got["out"].strip().endswith("end") and '"stall"' not in got["out"]
    assert "sda: stood still 5s (waiting_disk), then failed" in got["log"]


def test_the_end_of_the_partition_clears_it(tmp_path):
    got = _stall_run(tmp_path, DISK, tail='drawer_stall_clear "sda sdb"')
    assert got["sda"] is None
    assert "sda: stood still 3s (waiting_disk), then the partition's stream ended" in got["log"]


def test_the_watcher_ticks_it_and_the_agent_and_the_kiosk_load_it():
    agent = Path(__file__).resolve().parents[1] / "agent"
    verdict = (agent / "lib/restore/verdict.sh").read_text(encoding="utf-8")
    watch = verdict[verdict.index("drawer_watch() {"):verdict.index("# --- #2200")]
    assert 'drawer_stall_tick "$3"' in watch and 'drawer_stall_clear "$3"' in watch
    main = (agent / "imagesrv-agent").read_text(encoding="utf-8")
    bridge = (agent / "lib/gui/guibridge.sh").read_text(encoding="utf-8")
    for lib in ("opsay.sh", "stall.sh"):
        assert f'. "$LIB_DIR/{lib}"' in main and f'. "$LIB_DIR/{lib}"' in bridge, lib


# --- the cloner's screen: field 6 and drawer_stall= ---------------------------------

@requires_native("jq", why="cloner_gui_state reads the image name with jq")
def test_the_cloner_screen_gets_the_sentence_and_the_standing_record(tmp_path):
    from test_cloner_gui import PORTS, PRELUDE, env, target
    run = tmp_path / "run"; run.mkdir(); gui = tmp_path / "gui"; gui.mkdir()
    (run / "manifest.json").write_text('{"name":"Office 2024"}', newline="\n")
    (run / "response.json").write_text('{"schema":1}', newline="\n")
    target(run, "sda", "writing", 100, 40, "5")
    target(run, "sdb", "failed", 100, 74, "")
    (run / "targets" / "sdb" / "error").write_text(PULLED + "\n", encoding="utf-8", newline="\n")
    (run / "targets" / "sda" / "stall").write_text("waiting_net 1000 300\n", newline="\n")
    lib = agent_sources("opsay", "stall")
    out = sh(env(run, gui) + PRELUDE + lib + PORTS + 'date() { echo 1007; }; cloner_gui_state')
    lines = (gui / "state").read_text(encoding="utf-8").splitlines()
    sdb = next(l for l in lines if l.startswith("drawer=") and l.split("|")[1] == "sdb").split("|")
    assert sdb[5] == "הדיסק נעלם באמצע הכתיבה (74%). בדקו שהמגירה סגורה והכבל מחובר, וכתבו אותו שוב בגל הבא."
    assert "drawer_stall=sda|waiting_net|7|300" in lines
    sda = next(l for l in lines if l.startswith("drawer=") and l.split("|")[1] == "sda").split("|")
    assert (sda[6], sda[7]) == ("0", "-1")   # it stands: rate 0 (measured still) and no ETA, at once -- not the last speed
    assert not [l for l in lines if l.startswith("drawer_stall=sdb")]
    assert out is not None


@requires_native("jq", why="directgui.sh reads the room with jq")
def test_the_build_machine_shows_the_same_on_a_direct_send(tmp_path):
    from test_build_direct_drawers_2849 import ROOM, STATION, records
    room = json.loads(json.dumps(ROOM))
    d = room["machines"][0]["drawer_list"]
    d[1].update(error=PULLED, error_short="הדיסק נעלם באמצע הכתיבה (74%).")
    d[0].update(stall="waiting_disk", stall_s=6, stall_limit_s=150)
    lines = records(tmp_path, STATION, room=room)
    assert "drawer=2|sda|failed|0|82587750957|הדיסק נעלם באמצע הכתיבה (74%).|-1|-1|2|SATA 1||" in lines
    sdc = next(l for l in lines if l.startswith("drawer=1|sdc"))
    assert sdc.split("|")[6:8] == ["0", "-1"], sdc   # it stands: rate 0, no ETA -- not the last speed
    assert lines[lines.index(sdc) + 1] == "drawer_stall=sdc|waiting_disk|6|150"


# --- the server passes it on, on evidence only ---------------------------------------

def test_the_room_passes_the_sentence_and_a_standing_drawer_only_while_it_writes():
    from server.rounds.room import _stall, _text
    assert _text("x") == "x" and _text("  ") is None and _text(None) is None and _text(5) is None
    ok = {"state": "writing", "stall": "waiting_net", "stall_s": 4, "stall_limit_s": 300}
    assert _stall(ok) == {"stall": "waiting_net", "stall_s": 4, "stall_limit_s": 300}
    assert _stall({**ok, "stall_limit_s": None})["stall_limit_s"] is None
    none = {"stall": None, "stall_s": None, "stall_limit_s": None}
    for bad in ({**ok, "state": "failed"}, {**ok, "stall": "waiting"}, {**ok, "stall_s": True},
                {**ok, "stall_s": -1}, {**ok, "stall_s": "4"}, {"state": "writing"}):
        assert _stall(bad) == none, bad
