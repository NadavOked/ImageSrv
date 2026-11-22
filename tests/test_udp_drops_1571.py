"""#1571 — מה הקרנל של מחשב השיכפול עצמו זרק בזמן זרם מולטיקאסט.

re-xmit שהשולח סופר יכול להיוולד ברשת או במקבל: `udp-receiver` שלא קיבל
מעבד בזמן ממלא את חוצץ הסוקט, והקרנל זורק (`RcvbufErrors`). הקצב האוטומטי
מוריד על re-xmits בלי לדעת ממי — ולכן הסוכן רושם את ההפרש סביב כל זרם.

שלושה מצבים, לא שניים (עיקרון 5): נמדד · לא נקרא (שורה שאומרת זאת, לא 0) ·
unicast (אין UDP — אין שורה).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from test_agent import AGENT, BASH, posix, sh

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
    rc 1 ושום מספר. "0 0" כאן היה אומר "בדקנו, אין drops"."""
    (tmp_path / "proc/net").mkdir(parents=True)
    if content is not None:
        (tmp_path / "proc/net/snmp").write_text(content, newline="\n")
    assert drops(tmp_path) == ("", 1)


# #3173: the per-partition line of the old receiver's room left with it; a chunk pass's own drops are
# chunkloss.sh's (test_chunk_loss_3064.py), read through the same udp_drops above.
