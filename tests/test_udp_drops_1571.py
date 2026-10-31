"""‏#1571 — מה הקרנל של מחשב השיכפול עצמו זרק בזמן זרם מולטיקאסט.

‏re-xmit שהשולח סופר יכול להיוולד ברשת או במקבל: ‏`udp-receiver` שלא קיבל
מעבד בזמן ממלא את חוצץ הסוקט, והקרנל זורק (`RcvbufErrors`). הקצב האוטומטי
מוריד על re-xmits בלי לדעת ממי — ולכן הסוכן רושם את ההפרש סביב כל זרם.

שלושה מצבים, לא שניים (עיקרון 5): נמדד · לא נקרא (שורה שאומרת זאת, לא 0) ·
unicast (אין UDP — אין שורה).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from test_agent import AGENT, BASH, posix, sh
from test_drawer_progress import COUNTING_FANOUT
from test_timeouts import make_stubs, run_sh

pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required")

HEADER = ("Udp: InDatagrams NoPorts InErrors OutDatagrams RcvbufErrors "
          "SndbufErrors InCsumErrors IgnoredMulti MemErrors\n")


def snmp(rcvbuf: int, inerrors: int) -> str:
    # הצורה כפי שנקראה מ-/proc/net/snmp על ה-Testrunner (דביאן 13, 17/10).
    return ("Ip: Forwarding DefaultTTL\nIp: 1 64\n" + HEADER
            + f"Udp: 5906 67 {inerrors} 18638 {rcvbuf} 0 0 183362 0\n")


def drops(sysroot: Path) -> tuple[str, int]:
    out = sh(f"export SYSROOT={posix(sysroot)!r}; . {posix(AGENT)}/lib/core/common.sh 2>/dev/null; "
             f". {posix(AGENT)}/lib/core/waits.sh; v=$(udp_drops); rc=$?; "
             'printf "%s|%s" "$v" "$rc"')
    value, rc = out.rsplit("|", 1)
    return value, int(rc)


def test_the_counters_are_read_by_name_not_by_position(tmp_path):
    (tmp_path / "proc/net").mkdir(parents=True)
    (tmp_path / "proc/net/snmp").write_text(snmp(41, 43), newline="\n")
    assert drops(tmp_path) == ("41 43", 0)


@pytest.mark.parametrize("content", [None, "", HEADER, HEADER + "Udp: x y z\n",
                                     "Udp: InDatagrams NoPorts\nUdp: 1 2\n"])
def test_not_read_is_not_zero(tmp_path, content):
    """קובץ חסר, ריק, בלי שורת ערכים, ערכים לא מספריים, או בלי העמודות —
    ‏rc 1 ושום מספר. ‏"0 0" כאן היה אומר "בדקנו, אין drops"."""
    (tmp_path / "proc/net").mkdir(parents=True)
    if content is not None:
        (tmp_path / "proc/net/snmp").write_text(content, newline="\n")
    assert drops(tmp_path) == ("", 1)


def _stream(tmp_path: Path, mode: str, before: str | None, after: str | None) -> str:
    """מחיצה אחת לשתי מגירות במסלול המגירות האמיתי; ‏stream_source מזויף
    מחליף את /proc/net/snmp באמצע הזרם — כאילו הקרנל זרק בזמן הזרם."""
    box = tmp_path / "box"
    run = box / "run"
    sysroot = box / "root"
    payload = box / "part.bin"
    (sysroot / "proc/net").mkdir(parents=True)
    run.mkdir(parents=True)
    payload.write_bytes(b"imagesrv" * 8192)
    sha = hashlib.sha256(payload.read_bytes()).hexdigest()
    snmp_file = sysroot / "proc/net/snmp"
    if before is not None:
        snmp_file.write_text(before, newline="\n")
    later = box / "snmp.after"
    if after is not None:
        later.write_text(after, newline="\n")
    swap = (f"cp {posix(later)!r} {posix(snmp_file)!r}; " if after is not None
            else f"rm -f {posix(snmp_file)!r}; ")
    for dev in ("sda", "sdb"):
        (run / "targets" / dev).mkdir(parents=True)
    run_sh(
        make_stubs(box / "stubs", {"fanout": COUNTING_FANOUT})
        + f"export RUN_DIR={posix(run)!r} DEVROOT={posix(box)!r} SYSROOT={posix(sysroot)!r} "
        "WAIT_POLL_S=1 WAIT_DRAWER_S=10 WAIT_HELPER_S=10 "
        "WAIT_STREAM_START_S=20 WAIT_STREAM_STALL_S=20; "
        f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/waits.sh; "
        f". {posix(AGENT)}/lib/core/progress.sh; . {posix(AGENT)}/lib/restore/restore.sh; "
        f". {posix(AGENT)}/lib/restore/drawers.sh; . {posix(AGENT)}/lib/restore/drawerfeed.sh; . {posix(AGENT)}/lib/restore/verdict.sh; "
        f". {posix(AGENT)}/lib/restore/failmark.sh; . {posix(AGENT)}/lib/restore/ntfsboot.sh; "
        "target_init sda 131072; target_init sdb 131072; "
        f"stream_source() {{ {swap}cat {posix(payload)!r}; }}; "
        f"restore_partition_drawers {mode} http://s img 3 dd part.zst "
        f"{sha} '' sda sdb > {posix(box)}/pipe.out 2>&1; "
        'echo "rc=$?"'
    )
    return (run / "agent.log").read_text(encoding="utf-8")


def test_a_multicast_stream_logs_what_this_machine_dropped(tmp_path):
    log = _stream(tmp_path, "multicast", snmp(100, 110), snmp(137, 150))
    assert "partition 3: udp drops on this machine rcvbuf=37 inerrors=40" in log, log


def test_counters_that_vanish_mid_stream_say_not_read(tmp_path):
    log = _stream(tmp_path, "multicast", snmp(100, 110), None)
    assert "partition 3: udp drops not read" in log, log
    assert "rcvbuf=" not in log, log


def test_unicast_has_no_udp_line(tmp_path):
    log = _stream(tmp_path, "unicast", snmp(100, 110), snmp(137, 150))
    assert "udp drops" not in log, log
