"""‏#1386 "בטל דיסק זה" באמצע גל — צד הסוכן.

מגירה אחת יוצאת מזרם חי, והשאר ממשיכות. המנגנון של #1603: ה-zstd של
המגירה נהרג, ה-fifo מאבד את הקורא, ‏fanout מקבל EPIPE ומפיל רק אותה. אחר
כך טבלת המחיצות נמחקת (ראשית וגיבוי — ‏`sgdisk --zap-all`), והמגירה מסומנת
‏`cancelled` — לא `failed`: בלי ראיית ATA, ולכן בלי disk_failure בשרת, ובסבב
הבא היא ירוקה.

כמו ב-#1598, ה-`restore_partition_drawers` האמיתי מול **fanout האמיתי**.
המגירה שמבוטלת היא מגירה **איטית-אך-חיה** — בדיוק מה שנדב רוצה לשחרר
(13/10): בלי הביטול היא מעכבת את הזרם כולו (back-pressure), והבדיקה פוקעת.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_cloner_gui import bridge_setup, sh
from test_drawer_pull_isolation_1598 import GOOD_SHA, PAYLOAD, fanout_dir  # noqa: F401 — fixture
from test_timeouts import log_of, make_stubs, run_sh
from agentlib import flat_lib

JQ = shutil.which("jq")
requires_jq = pytest.mark.skipif(JQ is None, reason="jq אינו מותקן — הביטוי לא היה רץ בכלל")
requires_gcc = requires_native("gcc", posix=True, why="fanout האמיתי צריך gcc ו-fifo")

#: ‏partclone מזויף. ל-sda — כותב הכול. ל-sdb — מגירה איטית: בולעת 2MB,
#: ומי שמבקש את הביטול הוא "המפעיל" (קובץ הבקשה, כמו שהגשר/לולאת הדיווח
#: כותבים אותו), ואז ממשיכה לקרוא 64K כל 0.3 ש' — עד EOF.
PARTCLONE = r"""#!/bin/sh
node=
while [ $# -gt 0 ]; do case "$1" in -O) node=$2; shift 2 ;; *) shift ;; esac; done
case "$node" in
  */sdb3)
    head -c 2097152 > /dev/null
    [ "$ASK" = 1 ] && { printf 'על ידי nadav\n' > "$RUN_DIR/targets/sdb/cancel"; date +%s > "$BOX/asked"; }
    while n=$(head -c 65536 | wc -c) && [ "$n" -gt 0 ]; do sleep 0.3; done
    exit 1 ;;
  *) cat > "$node" ;;
esac
"""

STUBS = {
    "zstd": "#!/bin/sh\nexec cat\n",
    "partclone.dd": PARTCLONE,
    "dmesg": "#!/bin/sh\nexit 0\n",
    "sgdisk": '#!/bin/sh\necho "$*" >> "$BOX/sgdisk.calls"\n',
    "blockdev": "#!/bin/sh\nexit 0\n",
    "fanout": None,
}

LIBS = ("common", "sysinfo", "waits", "jsonq", "progress", "restore", "drawers", "drawerfeed", "verdict",
        "cancel", "failmark", "ntfsboot")


def room(tmp_path: Path, fanout_dir: Path, ask: int, pre: str = "",  # noqa: F811
         serial_b: str = "SER-B") -> tuple[Path, Path, str, float]:
    box = tmp_path / "box"
    run, dev = box / "run", box / "dev"
    (run / "targets").mkdir(parents=True)
    dev.mkdir()
    (box / "proc").mkdir()
    (box / "proc" / "uptime").write_text("100.00 50.00\n")
    for d, serial in (("sda", "SER-A"), ("sdb", serial_b)):
        sysd = box / "sys" / "block" / d / "device"
        sysd.mkdir(parents=True)
        (sysd / "state").write_text("running\n")
        (sysd / "serial").write_text(serial + "\n")
    (box / "part.bin").write_bytes(PAYLOAD)
    env = (f"export BOX={posix(box)!r} ASK={ask} SYSROOT={posix(box)!r} "
           f"RUN_DIR={posix(run)!r} DEVROOT={posix(dev)!r} TMPDIR={posix(tmp_path)!r} "
           "FANOUT_BUFFER=2097152 WAIT_POLL_S=1 WAIT_DRAWER_S=10 WAIT_HELPER_S=5 "
           "WAIT_STREAM_START_S=20 WAIT_STREAM_STALL_S=12; ")
    libs = "".join(f". {posix(flat_lib())}/{n}.sh; " for n in LIBS)
    started = time.monotonic()
    out = run_sh(
        make_stubs(box / "stubs", STUBS)
        + f'export PATH="{posix(fanout_dir)}:$PATH"\n'
        + env + libs
        + "target_init sda 1; target_init sdb 1; target_set sda writing; target_set sdb writing; "
        'echo SER-A > "$RUN_DIR/targets/sda/write_started"; '
        'echo SER-B > "$RUN_DIR/targets/sdb/write_started"; '
        + pre
        + f"stream_source() {{ cat {posix(box / 'part.bin')!r}; }}; "
        + f"restore_partition_drawers unicast http://s img 3 dd part.zst {GOOD_SHA} '' sda sdb"
        " > /dev/null 2>&1; " 'echo "rc=$?"; echo "machine=$(_machine_state "sda sdb")"; '
        f'pkill -9 -f {posix(box)!r} 2>/dev/null; :',
        timeout=90,
    )
    return box, run, out, time.monotonic() - started


def target(run: Path, dev: str, name: str) -> str:
    path = run / "targets" / dev / name
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


@requires_gcc
def test_a_cancelled_drawer_leaves_the_live_stream_and_the_other_finishes(tmp_path, fanout_dir):  # noqa: F811
    """בלי הביטול: sdb האיטית מעכבת את הזרם (16MB ב-~200KB/s), והוא פוקע.
    עם הביטול: היא בחוץ תוך שניות, sda מקבלת כל בייט, ו-sdb **בוטלה** — לא נכשלה."""
    box, run, out, elapsed = room(tmp_path, fanout_dir, ask=1)
    log = log_of(run)
    assert "rc=0" in out, out + log
    assert "machine=done" in out, out
    assert hashlib.sha256((box / "dev" / "sda3").read_bytes()).hexdigest() == GOOD_SHA
    assert target(run, "sda", "state") != "failed" and target(run, "sda", "error") == ""
    assert target(run, "sdb", "state") == "cancelled", target(run, "sdb", "error") + log
    assert target(run, "sdb", "error") == "", "the GPT was wiped: no error"
    assert not (run / "targets" / "sdb" / "ata_log.json").exists(), "a cancel is not a disk failure"
    calls = (box / "sgdisk.calls").read_text()
    assert calls.count("--zap-all") == 1 and "sdb" in calls and "sda" not in calls, calls
    assert "cancelled על ידי nadav" in log
    asked = int((box / "asked").read_text())
    assert int(target(run, "sdb", "cancelled_at")) - asked <= 6
    assert elapsed < 25, elapsed


@requires_gcc
def test_a_cancel_asked_before_the_partition_feeds_nobody_and_kills_nothing(tmp_path, fanout_dir):  # noqa: F811
    """בקשה שהגיעה בלי זרם רץ (לפני מחיצה, בין שתיים): המגירה לא מוזנת כלל,
    ה-GPT נמחק, ואף תהליך לא נהרג לפי קובץ pid ישן."""
    pre = ('printf "ממסך מחשב השיכפול\\n" > "$RUN_DIR/targets/sdb/cancel"; '
           'echo 999999 > "$RUN_DIR/targets/sdb/reader.pid"; ')
    box, run, out, _ = room(tmp_path, fanout_dir, ask=0, pre=pre)
    assert "rc=0" in out and "machine=done" in out, out + log_of(run)
    assert target(run, "sdb", "state") == "cancelled"
    assert not (box / "dev" / "sdb3").exists(), "a cancelled drawer is not fed"
    assert "its fifo's reader was not stopped" not in log_of(run), "nothing to kill between partitions"
    assert "--zap-all" in (box / "sgdisk.calls").read_text()


@requires_gcc
def test_a_disk_that_cannot_be_identified_keeps_its_gpt_and_says_so(tmp_path, fanout_dir):  # noqa: F811
    """עיקרון 5: GPT שלא נמחק נאמר — לא "נמחק". הסידורי השתנה (לא הדיסק
    שהריצה כתבה, #1427) → לא נוגעים בו, והשגיאה על המגירה אומרת זאת."""
    box, run, out, _ = room(tmp_path, fanout_dir, ask=1, serial_b="OTHER")
    assert target(run, "sdb", "state") == "cancelled", out + log_of(run)
    assert "לא נמחקה" in target(run, "sdb", "error")
    assert not (box / "sgdisk.calls").exists()


def _agent(tmp_path: Path, script: str) -> subprocess.CompletedProcess:
    run = tmp_path / "run"
    for d, st in (("sda", "writing"), ("sdb", "writing"), ("sdc", "done")):
        (run / "targets" / d).mkdir(parents=True, exist_ok=True)
        (run / "targets" / d / "state").write_text(st + "\n")
    libs = "".join(f". {posix(flat_lib())}/{n}.sh; " for n in LIBS)
    return subprocess.run([BASH, "-c", f"export RUN_DIR={posix(run)!r}; {libs}{script}"],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)


@requires_jq
def test_the_progress_answer_asks_only_drawers_that_are_writing(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "progress.reply").write_text(
        '{"ok":true,"cancel":[{"dev":"sdb","who_he":"על ידי nadav"},'
        '{"dev":"sdc","who_he":"x"},{"dev":"../sda","who_he":"x"}]}', encoding="utf-8")
    out = _agent(tmp_path, "cancel_from_reply; cancel_from_reply; echo rc=$?")
    assert "rc=0" in out.stdout, out.stderr
    assert target(run, "sdb", "cancel") == "על ידי nadav"
    assert not (run / "targets" / "sdc" / "cancel").exists(), "a done drawer has nothing to cancel"
    assert not (run / "targets" / "sda" / "cancel").exists()
    assert log_of(run).count("cancel asked") == 1, "asked once, not every two seconds"


def test_the_machine_state_counts_a_cancel_as_neither_written_nor_failed(tmp_path):
    cases = {("done", "cancelled"): "done", ("cancelled", "cancelled"): "partial",
             ("failed", "cancelled"): "failed", ("done", "failed"): "partial"}
    for (a, b), want in cases.items():
        script = (f'echo {a} > "$RUN_DIR/targets/sda/state"; echo {b} > "$RUN_DIR/targets/sdb/state"; '
                  '_machine_state "sda sdb"; _any_alive "sda sdb"; echo alive=$?')
        out = _agent(tmp_path, script).stdout.split()
        assert out[0] == want, (a, b, out)
        assert out[1] == f"alive={0 if 'done' in (a, b) else 1}", (a, b, out)


def test_the_cloner_screen_asks_without_a_sign_in_and_only_on_the_cloner(tmp_path):
    setup, gui = bridge_setup(tmp_path, "cloner")
    run = tmp_path / "run"
    (run / "targets" / "sdb").mkdir(parents=True)
    (run / "targets" / "sdb" / "state").write_text("writing\n")
    setup += f". {posix(AGENT)}/lib/restore/cancel.sh; "
    out = sh(setup + 'token="drawer-cancel|sdb"; gui_dispatch; echo "rc=$?"; gui_dispatch; echo "rc=$?"')
    assert out.stdout.split() == ["rc=0", "rc=0"], out.stderr   # asked again = 0, never the handoff's 2
    assert target(run, "sdb", "cancel") == "ממסך מחשב השיכפול"
    assert "GUI_ROLE_CALLED" not in out.stderr
    (run / "targets" / "sdb" / "cancel").unlink()
    (tmp_path / "b").mkdir()
    setup2, _ = bridge_setup(tmp_path / "b", "build")
    out = sh(setup2 + f'export RUN_DIR={posix(run)!r}; . {posix(AGENT)}/lib/restore/cancel.sh; '
             'token="drawer-cancel|sdb"; gui_dispatch; echo "rc=$?"')
    assert "rc=1" in out.stdout and not (run / "targets" / "sdb" / "cancel").exists()


def test_the_libs_load_cancel_sh_where_its_writers_run():
    agent = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    bridge = (AGENT / "lib" / "gui" / "guibridge.sh").read_text(encoding="utf-8")
    assert '. "$LIB_DIR/cancel.sh"' in agent, "the progress loop's reader"
    libs = bridge[bridge.index("gui_libs() {"):]
    assert '. "$LIB_DIR/cancel.sh"' in libs[:libs.index("\n}\n")], "the cloner screen's writer"


@requires_jq
def test_the_build_machine_posts_the_cancel_with_its_session(tmp_path):
    """‏X / "בטל…" במחשב הבנייה: אותו POST כמו הקונסולה, בסשן המחובר (role
    admin/deploy — `room_operator` בשרת). ‏dev פגום — לא נשלח כלום; 409 — הסיבה
    של השרת על המסך."""
    setup, gui = bridge_setup(tmp_path, "build")
    run = tmp_path / "run"
    stub = ('gui_role() { GUI_ROLE=deploy; return 0; }; '
            'console_post() { echo "$1" >> "$RUN_DIR/posted"; cp "$2" "$RUN_DIR/body.json"; '
            '  printf "%s" "${REPLY_BODY:-{\\"ok\\":true}}" > "$3"; echo "${REPLY_CODE:-200}"; }; ')
    out = sh(setup + stub + 'token=room-drawer-cancel; cloner=aa:bb:cc:00:00:21; dev=sdb; gui_dispatch; echo "rc=$?"')
    assert "rc=0" in out.stdout, out.stderr
    assert (run / "posted").read_text().split() == ["room/drawer/cancel"]
    assert '"mac": "aa:bb:cc:00:00:21"' in (run / "body.json").read_text() and '"dev": "sdb"' in (run / "body.json").read_text()
    out = sh(setup + stub + 'token=room-drawer-cancel; cloner=aa:bb:cc:00:00:21; dev="sdb;x"; gui_dispatch; echo "rc=$?"')
    assert "rc=1" in out.stdout and (run / "posted").read_text().split() == ["room/drawer/cancel"]
    out = sh(setup + stub + "export REPLY_CODE=409 REPLY_BODY='{\"detail\":\"הדיסק אינו כותב עכשיו\"}'; "
             'token=room-drawer-cancel; cloner=aa:bb:cc:00:00:21; dev=sdb; room_drawer_cancel "$cloner" "$dev"; echo "rc=$?"')
    assert "rc=1" in out.stdout
    assert "הדיסק אינו כותב עכשיו" in (gui / "error").read_text(encoding="utf-8")
