"""#3064 (G7, S42/S43): **ה-SO_RCVBUF שניתן בפועל ומילת הפסק של המקלט מגיעים לשרת**, בדיווח `udp_drops`.

עד כאן המקלט של מנוע החתיכות ביקש 16MB, קיבל מהקרנל אולי 416KB, ואמר את זה רק ללוג שלו; ו"איך המעבר נגמר"
(`pass complete` / `stalled` / `no-stream`...) נקרא רק כקוד יציאה. השרת ראה "ended" ולא ידע להבחין "נגמר" מ"נתקע", ולא
ידע להסביר חתיכות שננטשו בחוצץ קטן.

- `chunkcast recv` מדפיס `rcvbuf <N>` (מה ש-`getsockopt` החזיר) -- נבדק מול ה-chunkcast האמיתי.
- `chunkloss.sh` מוסיף ל-`udp_drops.json`: `rcvbuf_bytes` = הקטן במעברי השחזור, `null` כשמעבר לא אמר (לא 0, לא מספר
  משוער); `pass_word` = איך המעבר האחרון נגמר, `null` בזמן מעבר ואם לא נאמרה מילה.
- `reports.well_formed_udp_drops` **שומר אותם כמו שנשלחו**, ושדה פגום אחד הוא `null` לבדו -- הוא אינו מוחק את המונים
  (ש-K7 קורא) והמונים הפגומים אינם מוחקים אותו.

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

import json
import re

import chunkcast_rig as C
from test_chunk_loss_3064 import NO_CARD, UNIT, _json, _unit, needs_cc   # noqa: F401 -- the shell harness of the stage-4 tests
from test_chunkcast_2533 import _pass, cc, img   # noqa: F401 -- cc and img are fixtures: the real chunkcast
from server.agent import reports


def _full(got, name):
    """the whole file the reporter would read, extras included."""
    text = dict(got)[name]
    return None if text == "none" else json.loads(text)


# --- the agent: what the receiver says about its socket and how its pass ended -----------------------------------------


def test_the_buffer_the_kernel_gave_and_the_way_the_pass_ended_ride_in_the_report(tmp_path):
    got = _unit(tmp_path, r'''
snmp 0 0; chunk_loss_mark; printf 'listening\nabandoned 0\nrcvbuf 425984\n' > "$RUN_DIR/recv.out"; chunk_loss_tell "$RUN_DIR/recv.out"; show during
printf 'abandoned 0\npass complete\n' >> "$RUN_DIR/recv.out"; chunk_loss_end "$RUN_DIR/recv.out"; show end
''')
    assert _full(got, "during") == {"rcvbuf": 0, "inerrors": 0, "abandoned": 0, **NO_CARD, "rcvbuf_bytes": 425984,
                                  "pass_word": None}
    assert _full(got, "end") == {"rcvbuf": 0, "inerrors": 0, "abandoned": 0, **NO_CARD, "rcvbuf_bytes": 425984,
                                 "pass_word": "complete"}
    assert "receive buffer granted=425984, ended: complete" in (tmp_path / "agent.log").read_text(encoding="utf-8")


def test_the_report_keeps_the_smallest_buffer_over_the_passes_and_the_word_of_the_last(tmp_path):
    got = _unit(tmp_path, r'''
snmp 0 0; chunk_loss_mark; printf 'listening\nabandoned 0\nrcvbuf 425984\npass stalled\n' > "$RUN_DIR/recv.out"; chunk_loss_end "$RUN_DIR/recv.out"; show end1
chunk_loss_mark; printf 'listening\nabandoned 0\nrcvbuf 33554432\n' > "$RUN_DIR/recv.out"; chunk_loss_tell "$RUN_DIR/recv.out"; show during2
printf 'pass ended\n' >> "$RUN_DIR/recv.out"; chunk_loss_end "$RUN_DIR/recv.out"; show end2
''')
    assert _full(got, "end1")["pass_word"] == "stalled"
    # a larger grant in a later pass does not hide the small one: the loss of pass 1 happened in 416KB
    assert _full(got, "during2")["rcvbuf_bytes"] == 425984 and _full(got, "during2")["pass_word"] is None
    assert _full(got, "end2")["rcvbuf_bytes"] == 425984 and _full(got, "end2")["pass_word"] == "ended"


def test_a_pass_that_did_not_say_is_not_read_for_the_whole_restore_never_a_size(tmp_path):
    """An older chunkcast (no `rcvbuf` line), or one that died before it: the buffer is unknown -- and one pass unknown makes
    the restore's smallest unknown, as a partial sum would read as "nothing was lost" (principle 5)."""
    got = _unit(tmp_path, r'''
snmp 0 0; chunk_loss_mark; printf 'listening\nabandoned 0\n' > "$RUN_DIR/recv.out"; chunk_loss_end "$RUN_DIR/recv.out"; show end1
chunk_loss_mark; printf 'listening\nabandoned 0\nrcvbuf 425984\npass complete\n' > "$RUN_DIR/recv.out"; chunk_loss_end "$RUN_DIR/recv.out"; show end2
''')
    assert _full(got, "end1")["rcvbuf_bytes"] is None and _full(got, "end1")["pass_word"] is None
    assert _full(got, "end2")["rcvbuf_bytes"] is None, "a later pass's grant filled in for the pass that did not say"
    assert _full(got, "end2")["pass_word"] == "complete"


def test_a_word_that_is_not_a_word_is_not_read(tmp_path):
    """The report is built by hand (no jq on the agent): only a lowercase word with hyphens goes into it."""
    got = _unit(tmp_path, r'''
snmp 0 0; chunk_loss_mark; printf 'rcvbuf 1000\npass Complete"}\n' > "$RUN_DIR/recv.out"; chunk_loss_end "$RUN_DIR/recv.out"; show a
chunk_loss_mark; printf 'rcvbuf 1000\npass no-stream\n' > "$RUN_DIR/recv.out"; chunk_loss_end "$RUN_DIR/recv.out"; show b
''')
    assert _full(got, "a")["pass_word"] is None
    assert _full(got, "b")["pass_word"] == "no-stream"


def test_a_new_restore_starts_without_the_last_ones_buffer(tmp_path):
    got = _unit(tmp_path, r'''
snmp 0 0; chunk_loss_mark; printf 'rcvbuf 425984\npass complete\n' > "$RUN_DIR/recv.out"; chunk_loss_end "$RUN_DIR/recv.out"
udp_drops_reset; ls "$RUN_DIR" | grep -c '^udp_drops.rcvbuf$' | sed 's/^/files /'
chunk_loss_mark; printf 'rcvbuf 33554432\n' > "$RUN_DIR/recv.out"; chunk_loss_tell "$RUN_DIR/recv.out"; show next
''')
    assert ("files", "0") in [tuple(line) for line in got]
    assert _full(got, "next")["rcvbuf_bytes"] == 33554432


# --- chunkcast itself: the "rcvbuf N" line ----------------------------------------------------------------------------


@needs_cc
def test_chunkcast_says_the_buffer_the_kernel_gave_and_it_is_the_one_in_its_log(cc, img, tmp_path):  # noqa: F811
    relay = C.Relay(1, lambda receiver, kind, chunk, seq, packet: [packet])
    try:
        (run,) = _pass(cc, img, relay, C.new_session(), [(tmp_path / "state", [img.blank(tmp_path / "a.disk")])],
                       tmp_path, 1)
    finally:
        relay.close()
    out = run.out.read_text().splitlines()
    said = [int(line.split()[1]) for line in out if re.fullmatch(r"rcvbuf \d+", line)]
    logged = int(re.search(r"receive buffer (\d+) bytes", run.err.read_text()).group(1))
    assert len(said) == 1 and said[0] == logged > 0, (said, logged)
    assert out.index(f"rcvbuf {said[0]}") < next(i for i, line in enumerate(out) if line.startswith("pass ")), out
    assert out[0] == "listening", "the agent waits for this line before it tells the server"


# --- the server: what is sent is kept, and a broken field is its own null ---------------------------------------------


def test_the_server_keeps_the_buffer_and_the_word_as_sent():
    got = reports.well_formed_udp_drops({"udp_drops": {"rcvbuf": 3, "inerrors": 0, "abandoned": 12,
                                                       "rcvbuf_bytes": 425984, "pass_word": "stalled"}})
    assert got == {"rcvbuf": 3, "inerrors": 0, "abandoned": 12, "rcvbuf_bytes": 425984, "pass_word": "stalled"}


def test_a_word_chunkcast_did_not_have_yesterday_is_kept(monkeypatch):
    """Not a closed list: a new word of a newer chunkcast is not "not read"."""
    got = reports.well_formed_udp_drops({"udp_drops": {"rcvbuf": 0, "inerrors": 0, "pass_word": "no-disk-left"}})
    assert got["pass_word"] == "no-disk-left"


def test_not_read_stays_null_and_absent_stays_absent():
    sent = reports.well_formed_udp_drops({"udp_drops": {"rcvbuf": 3, "inerrors": 0, "abandoned": 1,
                                                        "rcvbuf_bytes": None, "pass_word": None}})
    assert sent == {"rcvbuf": 3, "inerrors": 0, "abandoned": 1, "rcvbuf_bytes": None, "pass_word": None}
    old = reports.well_formed_udp_drops({"udp_drops": {"rcvbuf": 3, "inerrors": 0, "abandoned": 1}})
    assert old == {"rcvbuf": 3, "inerrors": 0, "abandoned": 1}, "an older agent: no fields, not nulls"


def test_a_broken_field_is_its_own_null_and_does_not_take_the_counters_down():
    broken = reports.well_formed_udp_drops({"udp_drops": {"rcvbuf": 3, "inerrors": 0, "abandoned": 1,
                                                          "rcvbuf_bytes": -5, "pass_word": "../etc"}})
    assert broken == {"rcvbuf": 3, "inerrors": 0, "abandoned": 1, "rcvbuf_bytes": None, "pass_word": None}
    for bad in (True, "425984", 1.5, [1]):
        got = reports.well_formed_udp_drops({"udp_drops": {"rcvbuf": 3, "inerrors": 0, "rcvbuf_bytes": bad}})
        assert got == {"rcvbuf": 3, "inerrors": 0, "rcvbuf_bytes": None}, bad


def test_broken_counters_do_not_take_the_facts_beside_them_down():
    got = reports.well_formed_udp_drops({"udp_drops": {"rcvbuf": -1, "inerrors": 0, "rcvbuf_bytes": 425984,
                                                       "pass_word": "complete"}})
    assert got == {"rcvbuf": None, "inerrors": None, "rcvbuf_bytes": 425984, "pass_word": "complete"}


def test_a_udpcast_report_keeps_what_the_socket_got_beside_the_drops():
    """The same two keys that udpcast streams carry (rcvbuf.sh): kept, since the buffer explains the loss there too."""
    got = reports.well_formed_udp_drops({"udp_drops": {"rcvbuf": 37, "inerrors": 40,
                                                       "rcvbuf_asked": 16777216, "rcvbuf_bytes": 425984}})
    assert got == {"rcvbuf": 37, "inerrors": 40, "rcvbuf_asked": 16777216, "rcvbuf_bytes": 425984}


def test_a_value_that_is_not_an_object_is_all_unread_with_no_facts():
    assert reports.well_formed_udp_drops({"udp_drops": "x"}) == {"rcvbuf": None, "inerrors": None}
    assert reports.well_formed_udp_drops({}) is None


def test_the_report_reaches_the_member_row_and_the_wave_drops(tmp_path):
    """Through `reports.ingest` (the progress endpoint's body): stored in `session_members.udp_drops_json`, and a
    later report without the fields leaves them (COALESCE) -- the buffer does not vanish with a quiet report."""
    from server import db
    conn = db.connect(tmp_path / "imagesrv.db")
    mac = "78:ac:c0:9b:18:85"
    conn.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state,"
                 " opened_by, created_at, last_join_at, started_at) VALUES ('ses_g7', 'grp_CLONERS', 'img', 'p', 1, 300,"
                 " 'running', 't', ?, 0, ?)", (db.now_iso(), db.now_iso()))
    conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at) VALUES ('ses_g7', ?, 'writing', ?)",
                 (mac, db.now_iso()))
    conn.commit()
    drops = {"rcvbuf": 2, "inerrors": 0, "abandoned": 1, "rcvbuf_bytes": 425984, "pass_word": "stalled"}
    assert reports.ingest(conn, {"session_id": "ses_g7", "mac": mac, "state": "writing", "targets": [],
                                 "udp_drops": drops})["ok"]
    read = lambda: json.loads(conn.execute("SELECT udp_drops_json FROM session_members").fetchone()[0])   # noqa: E731
    assert read() == drops
    assert reports.ingest(conn, {"session_id": "ses_g7", "mac": mac, "state": "writing", "targets": []})["ok"]
    assert read() == drops
