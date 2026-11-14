"""מפת המגע: בליעות בסוכן שאיש לא החליט לפיהן — עכשיו שורה ביומן.

כל אחת הייתה `2>/dev/null` או `|| :` בלי שום עקבה (עיקרון 5). ההתנהגות
עצמה לא משתנה: מה שנכשל עדיין נכשל באותה צורה, ומה שהצליח עדיין מצליח.
מה שנוסף הוא הראיה.

* B-DSK-06 (`failmark.sh` `wipe_written_gpt`): השומר שלא מוחק GPT של דיסק
  שלא הוכח שהוא הדיסק שנכתב — נכון — אבל הדיסק נשאר עם טבלה תקינה מעל
  נתונים חלקיים, ואיש לא ידע.
* B-DSK-43 (`hostname.sh`): `umount` בנתיבי הכשל של כתיבת השם.
* B-DSK-47 (`bootca.sh`): `umount` של ה-ESP אחרי קריאת החתימות.
* B-SYS-05 (`usbclone.sh`): `umount` של הדיסק-און-קי לפני `reboot -f`.
* B-SYS-13 (`attended.sh`): פעימה שלא נענתה — פעם אחת, לא בכל פעימה.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import hygiene
from agentlib import flat_lib
from native import requires_native
from test_agent import AGENT, BASH, CLEAN_HIVE, _stub, posix, sh
from test_bootca import FAKE_OPENSSL, NAME_2011, SOURCE_CHAIN, blob, make_pe, path_entry, put, toolbox

pytestmark = requires_native(("bash", BASH))


# --- B-DSK-06: a GPT that the guard does not wipe is said --------------------------


def _wipe(tmp_path: Path, written: str | None, now: str, same: bool) -> tuple[str, str, str]:
    run = tmp_path / "run"
    (run / "targets" / "sda").mkdir(parents=True)
    if written is not None:
        (run / "targets" / "sda" / "write_started").write_text(written, newline="\n")
    src = " ".join(f". {posix(flat_lib())}/{n}.sh;" for n in ("common", "sysinfo", "diskinfo", "failmark"))
    out = sh(f"export RUN_DIR={posix(run)!r} DEVROOT=/nonexistent IMAGESRV_TEST=1; {src} "
             f"disk_serial() {{ printf %s {now!r}; }}; "
             f"target_same() {{ {'return 0' if same else 'return 1'}; }}; "
             'sgdisk() { echo "SGDISK $*" >> "$RUN_DIR/trace"; }; blockdev() { :; }; '
             'wipe_written_gpt sda "restore failure"; echo "rc=$?"')
    log = run / "agent.log"
    trace = run / "trace"
    return (out.strip().splitlines()[-1], log.read_text(encoding="utf-8") if log.exists() else "",
            trace.read_text(encoding="utf-8") if trace.exists() else "")


def test_a_wipe_the_guard_skips_for_another_serial_is_said(tmp_path):
    last, log, trace = _wipe(tmp_path, "S6PNNS0T", "OTHER-1", same=True)
    assert last == "rc=1" and "SGDISK" not in trace, (last, trace)
    assert "GPT NOT wiped after restore failure" in log, log
    assert "'S6PNNS0T'" in log and "'OTHER-1'" in log, log


def test_a_wipe_the_guard_skips_for_no_serial_is_said(tmp_path):
    """#1427: דיסק בלי serial אינו נמחק — ועכשיו גם נאמר שנשאר חלקי."""
    last, log, trace = _wipe(tmp_path, "", "", same=True)
    assert last == "rc=1" and "SGDISK" not in trace, (last, trace)
    assert "GPT NOT wiped" in log and "partial image" in log, log


def test_a_wipe_the_guard_skips_for_another_slot_is_said(tmp_path):
    last, log, trace = _wipe(tmp_path, "S6PNNS0T", "S6PNNS0T", same=False)
    assert last == "rc=1" and "SGDISK" not in trace, (last, trace)
    assert "GPT NOT wiped" in log, log


def test_nothing_written_is_nothing_to_say(tmp_path):
    """שומר: בלי write_started הריצה לא כתבה כלום (#1409: תוכנית שנדחתה) —
    אין דיסק חלקי, ואין שורה שתבהיל."""
    last, log, trace = _wipe(tmp_path, None, "S6PNNS0T", same=True)
    assert last == "rc=1" and "SGDISK" not in trace, (last, trace)
    assert "GPT NOT wiped" not in log, log


def test_the_disk_this_run_wrote_is_still_wiped(tmp_path):
    last, log, trace = _wipe(tmp_path, "S6PNNS0T", "S6PNNS0T", same=True)
    assert last == "rc=0", (last, log)
    assert "SGDISK --zap-all /nonexistent/sda" in trace, trace
    assert "wiped primary and backup GPT" in log and "NOT wiped" not in log, log


# --- B-DSK-43: the name's failure paths say when the disk stays mounted ------------


def test_a_failure_path_umount_that_fails_is_said(tmp_path):
    """הנתיב של `no_controlset`, עם `umount` שנכשל. התוצאה לא משתנה (אותו
    קוד), ועכשיו היומן אומר שהדיסק נשאר מעוגן."""
    box = tmp_path / "box"
    stub_dir = box / "stubs"
    stub_dir.mkdir(parents=True)
    run = box / "run"
    hive = run / "win" / "Windows" / "System32" / "config"
    hive.mkdir(parents=True)
    (hive / "SYSTEM").write_bytes(CLEAN_HIVE)
    hivewrite_body = ('if [ "$1" = "-g" ]; then\n'
                      '  case "$*" in *Current*) exit 3;; *) echo LAB1-05; exit 0;; esac\n'
                      'fi\nexit 0')
    stubs = (_stub(stub_dir / "ntfs-3g", "exit 0") + _stub(stub_dir / "hivewrite", hivewrite_body)
             + _stub(stub_dir / "umount", "echo 'umount: target is busy' >&2; exit 32"))
    plan = "1|EBD0A0A2-B9E5-4433-87C0-68B6B72699C7|windows|ntfs|4096|1|p1|y|false|u"
    out = sh(stubs
             + f'export PATH="$(cd {posix(stub_dir)!r} && pwd):$PATH"; '
             f'export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev; '
             f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/restore/restore.sh; '
             f'. {posix(AGENT)}/lib/postdeploy/hostname.sh; . {posix(AGENT)}/lib/postdeploy/unattend.sh; '
             f"manifest_plan() {{ printf '%s\\n' {plan!r}; }}; "
             'partition_node() { echo /dev/fake1; }; '
             'write_hostname sda /dev/null LAB1-05 || true')
    result = json.loads(out.strip().splitlines()[-1])
    assert result["ok"] is False and result["code"] == "no_controlset", result
    log = (run / "agent.log").read_text(encoding="utf-8")
    assert "failed (rc=32) -- disk left mounted" in log, log
    assert "target is busy" in log, log


# --- B-DSK-47: the ESP that stays mounted after the signature read ----------------


def test_an_esp_left_mounted_is_said_and_the_signers_still_read(tmp_path):
    esp = tmp_path / "esp"
    put(esp, "/EFI/BOOT/bootx64.efi", make_pe([blob(b"CA2011")]))
    run = tmp_path / "run"
    run.mkdir()
    box = toolbox(tmp_path, FAKE_OPENSSL)
    script = (f"export PATH={path_entry(box)!r}:/usr/bin:/bin; export RUN_DIR={posix(run)!r}; "
              "export ESPROOT=''; " + SOURCE_CHAIN
              + "node_is_block() { return 0; }; "
              f'mount() {{ eval "_m=\\${{$#}}"; cp -R {posix(esp)!r}/. "$_m"/; }}; '
              "umount() { echo 'umount: busy' >&2; return 1; }; "
              "boot_ca_json /dev/fakeesp")
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    assert json.loads("{" + proc.stdout.strip() + "}")["boot_ca"] == [NAME_2011]
    log = (run / "agent.log").read_text(encoding="utf-8")
    assert "boot_ca: WARNING:" in log and "(ro) left mounted" in log, log


# --- B-SYS-05: the stick that is not unmounted before reboot -f -------------------


def test_a_stick_not_unmounted_before_the_reboot_is_said(tmp_path):
    """`reboot`, `sync` ו-`umount` מזויפים כפונקציות **וגם** כקבצים ב-PATH,
    כדי שאף נתיב לא יגיע ל-reboot אמיתי. בלי IMAGESRV_TEST, כדי שהשורה תרוץ."""
    run = tmp_path / "run"
    run.mkdir()
    fake = tmp_path / "bin"
    fake.mkdir()
    for name in ("reboot", "poweroff", "sync"):
        (fake / name).write_text(f"#!/bin/sh\necho FAKE-{name}\n", newline="\n")
        (fake / name).chmod(0o755)
    src = " ".join(f". {posix(flat_lib())}/{n}.sh;" for n in ("common", "usbclone"))
    out = sh(f'export PATH={posix(fake)!r}:"$PATH" RUN_DIR={posix(run)!r} USB_DATA_DIR=/media/stick; '
             f"{src} reboot() {{ echo FAKE-reboot; }}; sync() {{ :; }}; "
             "umount() { echo 'umount: /media/stick: target is busy' >&2; return 32; }; "
             "_lc_req() { :; }; IMAGESRV_TEST=0 lc_reboot")
    assert "FAKE-reboot" in out, out
    log = (run / "agent.log").read_text(encoding="utf-8")
    assert "/media/stick not unmounted before the reboot" in log, log
    assert "target is busy" in log, log


# --- B-SYS-13: a beat that is not answered, said once ------------------------------


def test_unanswered_beats_are_said_once_and_their_return_once(tmp_path):
    """שלוש פעימות שלא נענו, ואז תשובות. שורה אחת כשהפסיקו לענות, ושורה אחת
    כשחזרו — לא שורה לכל פעימה (המסך שהאדם קורא אינו גולל)."""
    run = tmp_path / "run"
    run.mkdir()
    script = (f'export RUN_DIR={posix(run)!r} ATTENDED_BEAT_S=1; '
              f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/attended.sh; '
              'attended_hello() { n=$(cat "$RUN_DIR/n" 2>/dev/null || echo 0); n=$((n + 1)); '
              'echo "$n" > "$RUN_DIR/n"; [ "$n" -gt 3 ]; }; '
              'attended_start q; sleep 6; attended_stop; cat "$RUN_DIR/n"')
    proc = hygiene.run_group([BASH, "-c", script], capture_output=True, text=True, timeout=40,
                             stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    assert int(proc.stdout.strip().splitlines()[-1]) >= 4, proc.stdout
    log = (run / "agent.log").read_text(encoding="utf-8") if (run / "agent.log").exists() else ""
    assert log.count("a beat to the server was not answered") == 1, log
    assert log.count("the server answers the beat again (3 missed)") == 1, log


# --- B-NET-01 / B-SYS-01: init, before there is a log ------------------------------


def test_a_loopback_that_does_not_come_up_is_said_and_init_carries_on(tmp_path):
    """הלולאה האמיתית של init (`test_init_network._run`) עם `ip link set lo up`
    שנכשל. השורה בקונסולה וב-agent.log, והכרטיס עדיין נבחר כמו קודם."""
    from test_init_network import _make_net, _run
    net = tmp_path / "net"
    _make_net(net, {"lo": 1, "eth0": 1})
    extra = ('ip() { case "$*" in "link set lo up") echo "RTNETLINK answers: no lo" >&2; return 2 ;; '
             'link*) echo "ip $*" >> "$LOG.ip" ;; esac; }')
    proc, _elapsed, _log = _run(net, "eth0", extra=extra)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "IFACE=eth0" in proc.stdout, proc.stdout
    assert "WARNING: lo did not come up" in proc.stdout, proc.stdout
    agent_log = (tmp_path / "run" / "agent.log").read_text(encoding="utf-8")
    assert "WARNING: lo did not come up (B-NET-01)" in agent_log, agent_log


def _mount_check(root: Path) -> str:
    line = next(ln for ln in (AGENT / "init").read_text(encoding="utf-8").splitlines()
                if ln.startswith("[ -r /proc/uptime ]"))
    for path in ("/proc/uptime", "/sys/class", "/dev/null"):
        line = line.replace(path, posix(root) + path)
    return sh(line + "; echo end")


def test_missing_pseudo_filesystems_are_said(tmp_path):
    assert "WARNING: /proc, /sys or /dev is missing" in _mount_check(tmp_path)


def test_present_pseudo_filesystems_say_nothing(tmp_path):
    """שומר: עם /proc, /sys ו-/dev במקומם — אין שורה. (`/dev/null` כאן קובץ
    רגיל, ולכן הבדיקה מזייפת אותו בהתקן התווים האמיתי.)"""
    (tmp_path / "proc").mkdir()
    (tmp_path / "proc" / "uptime").write_text("1.0 1.0\n", newline="\n")
    (tmp_path / "sys" / "class").mkdir(parents=True)
    (tmp_path / "dev").mkdir()
    try:
        os.symlink("/dev/null", tmp_path / "dev" / "null")
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks here -- runs on the testrunner")
    assert _mount_check(tmp_path).strip() == "end"
