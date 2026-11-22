"""#3168: **מה שהקרנל והכרטיס של המקלט זרקו, בגל חתיכות** -- `agent/lib/restore/chunkloss.sh`.

המקלט הישן ספר `RcvbufErrors` בלבד (וגם זה רק בנתיב udpcast); #3064 שלב 4 הביא את rcvbuf/inerrors ואת
`abandoned` לגל חתיכות. כאן נוספים שני המונים שמבדילים "הרשת איבדה" מ"המכונה לא עמדה בזה" ומאפשרים לדעת **איפה**:

- `softnet` -- עמודה 2 של `/proc/net/softnet_stat` (הקסה), סכום על כל המעבדים: תור הקלט של מעבד התמלא לפני ש-UDP ראה
  את החבילה.
- `nic` -- `ethtool -S $IFACE`: סכום מוני ה-drop של צד הקבלה שהכרטיס עצמו מחזיק (`NIC_DROP_NAMES`). `tx_dropped` ושאר
  המונים אינם נספרים.

כמו rcvbuf: הפרש בין תחילת המעבר לעכשיו, ועוד מה שהמעברים הקודמים של אותו שחזור איבדו. **מונה שלא נקרא הוא ``null``
לכל הסכום, לעולם לא 0** (עיקרון 5): אין ethtool או `IFACE`, ethtool שנכשל, דרייבר שאין בתשובתו אף שם מוכר, קובץ
`softnet_stat` חסר או פגום, ומונה שירד (הכרטיס אותחל) -- כולם "לא נקרא", ולא "הכרטיס לא זרק דבר".

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

import pytest

from native import requires_native
from server.agent import reports
from test_agent import BASH
from test_chunk_loss_3064 import _json, _unit

pytestmark = requires_native(("bash", BASH), why="the agent's shell")

#: a card and a CPU queue the scene can move: `softnet 0000000a 00000010` = one hex "dropped" per CPU,
#: `nic '<ethtool -S lines>' [rc]` = what `ethtool -S eth0` answers (and its exit code)
CARD = r'''
export IFACE=eth0 ETHTOOL="$RUN_DIR/ethtool"
softnet() { mkdir -p "$SYSROOT/proc/net"; : > "$SYSROOT/proc/net/softnet_stat"
    for _v in "$@"; do printf '0000beef %s 00000000\n' "$_v" >> "$SYSROOT/proc/net/softnet_stat"; done; }
printf '#!/bin/sh\ncat "$RUN_DIR/ethtool.out"\nexit "$(cat "$RUN_DIR/ethtool.rc")"\n' > "$ETHTOOL"; chmod +x "$ETHTOOL"
nic() { printf 'NIC statistics:\n%s\n' "$1" > "$RUN_DIR/ethtool.out"; echo "${2:-0}" > "$RUN_DIR/ethtool.rc"; }
'''


def _scene(tmp_path, script: str):
    return _unit(tmp_path, CARD + script)


def test_the_cpu_queue_and_the_card_are_counted_per_pass_and_added_up_over_the_restore(tmp_path):
    got = _scene(tmp_path, r'''
snmp 0 0; softnet 0000000a 00000010; nic '     rx_dropped: 3
     rx_missed_errors: 4
     tx_dropped: 99
     rx_packets: 1000'
chunk_loss_mark; printf 'abandoned 0\n' > "$RUN_DIR/recv.out"
softnet 0000001a 00000010; nic '     rx_dropped: 5
     rx_missed_errors: 4
     tx_dropped: 150
     rx_packets: 9000'
chunk_loss_tell "$RUN_DIR/recv.out"; show during1
chunk_loss_end "$RUN_DIR/recv.out"; show end1
softnet 0000001a 00000010; nic '     rx_dropped: 5
     rx_missed_errors: 4
     tx_dropped: 150
     rx_discards: 5'
chunk_loss_mark; printf 'abandoned 0\n' > "$RUN_DIR/recv.out"
softnet 0000001a 00000020; nic '     rx_dropped: 5
     rx_missed_errors: 4
     tx_dropped: 400
     rx_discards: 10'
chunk_loss_tell "$RUN_DIR/recv.out"; show during2
chunk_loss_end "$RUN_DIR/recv.out"; show end2
udp_drops_reset; show reset
softnet 0000001a 00000020; chunk_loss_mark; softnet 0000001b 00000020
chunk_loss_tell "$RUN_DIR/recv.out"; show fresh
''')
    # 0x1a - 0x0a = 16 on one CPU; rx_dropped +2 -- and tx_dropped (+51) is not a receive drop: not counted
    assert _json(got, "during1")["softnet"] == 16 and _json(got, "during1")["nic"] == 2
    assert (_json(got, "end1")["softnet"], _json(got, "end1")["nic"]) == (16, 2)
    # the second pass: +16 on the other CPU, rx_discards +5 -- on top of the first pass's
    assert (_json(got, "during2")["softnet"], _json(got, "during2")["nic"]) == (32, 7)
    assert (_json(got, "end2")["softnet"], _json(got, "end2")["nic"]) == (32, 7)
    # a new restore starts from nothing
    assert dict(got)["reset"] == "none"
    assert (_json(got, "fresh")["softnet"], _json(got, "fresh")["nic"]) == (1, 0)   # the card said: nothing new


def test_the_counters_are_read_in_hex_over_every_cpu(tmp_path):
    got = _scene(tmp_path, r'''
snmp 0 0; softnet 00000000 00000000 00000000; chunk_loss_mark
softnet 0000000a 000000ff 00000100; chunk_loss_tell "$RUN_DIR/recv.out"; show now
''')
    assert _json(got, "now")["softnet"] == 0xA + 0xFF + 0x100   # not 10+255+... read as decimal digits


@pytest.mark.parametrize(("before", "after"), [
    # ethtool is there and fails
    ("nic '     rx_dropped: 3' 1", ""),
    # ethtool answers, with no counter we know to be a receive drop: that is not "dropped nothing"
    ("nic '     rx_packets: 1000\n     tx_dropped: 4'", ""),
    # the card was reset during the pass: its counters went DOWN
    ("nic '     rx_dropped: 9'", "nic '     rx_dropped: 2'"),
    # no interface to ask
    ("nic '     rx_dropped: 3'; IFACE=", ""),
    # no ethtool at all
    ("nic '     rx_dropped: 3'; ETHTOOL=/nonexistent/ethtool", ""),
], ids=["ethtool-fails", "no-known-name", "counter-went-down", "no-interface", "no-ethtool"])
def test_a_card_that_could_not_be_read_is_null_never_zero(tmp_path, before, after):
    got = _scene(tmp_path, f'''
snmp 0 0; softnet 0000000a; {before}
chunk_loss_mark; softnet 0000000b; {after or ":"}; chunk_loss_tell "$RUN_DIR/recv.out"; show now
''')
    now = _json(got, "now")
    assert now["nic"] is None, now
    assert now["softnet"] == 1, now   # the one that was read is still said


@pytest.mark.parametrize("softnet_file", [None, "zzzz not hex zzzz 0\n", "\n"], ids=["missing", "not-hex", "empty"])
def test_a_cpu_queue_that_could_not_be_read_is_null_never_zero(tmp_path, softnet_file):
    write = "rm -f \"$SYSROOT/proc/net/softnet_stat\"" if softnet_file is None else \
        f"printf '{softnet_file}' > \"$SYSROOT/proc/net/softnet_stat\"".replace("\n", "\\n")
    got = _scene(tmp_path, f'''
snmp 0 0; softnet 0000000a; nic '     rx_dropped: 3'; chunk_loss_mark
{write}; nic '     rx_dropped: 4'; chunk_loss_tell "$RUN_DIR/recv.out"; show now
''')
    now = _json(got, "now")
    assert now["softnet"] is None, now
    assert now["nic"] == 1, now


def test_one_pass_unread_makes_the_whole_restore_unread(tmp_path):
    got = _scene(tmp_path, r'''
snmp 0 0; softnet 0000000a; nic '     rx_dropped: 3' 1
chunk_loss_mark; printf 'abandoned 0\n' > "$RUN_DIR/recv.out"; nic '     rx_dropped: 9' 1; chunk_loss_end "$RUN_DIR/recv.out"
nic '     rx_dropped: 9'; chunk_loss_mark; nic '     rx_dropped: 12'; chunk_loss_tell "$RUN_DIR/recv.out"; show later
''')
    # the first pass could not ask the card; the second could (+3) -- a sum of 3 would say "the card dropped 3 in all"
    assert _json(got, "later")["nic"] is None


def test_the_server_keeps_the_cpu_queue_and_the_card_beside_the_rest_and_absent_stays_absent():
    full = {"rcvbuf": 3, "inerrors": 0, "abandoned": 12, "softnet": 7, "nic": 0}
    assert reports.well_formed_udp_drops({"udp_drops": full}) == full
    # the card said nothing: null stays null (not read), it is not turned into 0
    unread = {**full, "nic": None}
    assert reports.well_formed_udp_drops({"udp_drops": unread}) == unread
    # a chunk wave from before this field: absent stays absent
    old = {"rcvbuf": 3, "inerrors": 0, "abandoned": 12}
    assert reports.well_formed_udp_drops({"udp_drops": old}) == old
    # a corrupt one is "not read" for everything it carried, never the zero of an earlier report
    for bad in (-1, "7", 1.5, True):
        got = reports.well_formed_udp_drops({"udp_drops": {**full, "softnet": bad}})
        assert got == {"rcvbuf": None, "inerrors": None, "abandoned": None, "softnet": None, "nic": None}, bad
