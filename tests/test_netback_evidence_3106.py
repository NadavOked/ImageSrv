"""#3106: "the network is back" is said on the server's answer, not on a drawer that moved.

Metal 13/11 (cloner 2, the chunk engine): after the second cable pull the card sent ARP and took nothing in
(#3105), and the "network back" tune -- with its green bar -- was heard all the same. The outage state
(roundbeep.sh) ended on the age of the newest drawer movement (moved.<dev>), or of a new receive (beep.rx):
local evidence only. A drawer flushing what came before the outage, or a new pass, read as "back".

The fix:
- common.sh, http_post_json: a request the server answered stamps $RUN_DIR/net.heard (the agent's reports,
  progress_loop, every 2 s). A request that failed stamps nothing, and its exit code is curl's, as before.
- roundbeep.sh, _rb_stream_age: while an outage stands, the stream's age is the last byte's before it
  (beep.netsince) until the server answered at or after the moment the outage was declared (beep.lostat).
  Then the drawers' movement counts again, and "back" is heard once.

Negative control: common.sh and roundbeep.sh of lab/vm-1113g (e08f8322) -- the tests of the bug fail
("back" on a move alone, and on an answer older than the outage; no stamp on an answer). The guards (an
answer after the outage, then a move = "back" once; curl's exit code) pass in both states.
"""

from __future__ import annotations

from test_beeps_2067 import expected, frequencies, log_lines, prelude, run_ash

STREAMING = """
    command mkdir -p "$RUN_DIR/targets/sda" "$GUI_DIR"
    echo writing > "$RUN_DIR/targets/sda/state"; echo writing > "$RUN_DIR/state"; echo 1 > "$RUN_DIR/bytes.raw"
    t=1000; date() { echo "$t"; }
    moved() { echo "$t 1" > "$GUI_DIR/moved.sda"; }     # the kiosk saw the drawer's counter move (cloner_gui_pace)
    heard() { echo "$1" > "$RUN_DIR/net.heard"; }        # what http_post_json stamps on an answer
    tick() { stream_beep_tick; wait; stream_net_record >> "$RUN_DIR/recs"; echo -- >> "$RUN_DIR/recs"; }
    moved; tick                                          # 1000: the stream flows
    t=1040; tick                                         # 40 s without a move: the outage (netlost)
"""


def _records(tmp_path):
    return [r for r in (tmp_path / "recs").read_text(encoding="utf-8").split("--\n")]


def test_a_drawer_that_moves_with_no_answer_from_the_server_is_not_the_network_back(tmp_path):
    run_ash(prelude(tmp_path) + STREAMING + """
        t=1041; moved; tick                              # chunkcast flushes what came before the cut
        t=1045; moved; tick
        t=1050; moved; tick
    """)
    assert frequencies(tmp_path) == expected("netlost"), log_lines(tmp_path)
    assert (tmp_path / "beep.net").read_text().strip() == "lost"
    assert _records(tmp_path)[-2] == "net=lost|1000|120\n"   # the red bar stays, from the last byte before the cut


def test_an_answer_older_than_the_outage_is_not_the_network_back(tmp_path):
    run_ash(prelude(tmp_path) + STREAMING + """
        heard 1039                                       # the last report answered before the outage was declared
        t=1041; moved; tick
    """)
    assert frequencies(tmp_path) == expected("netlost"), log_lines(tmp_path)
    assert (tmp_path / "beep.net").read_text().strip() == "lost"


def test_the_servers_answer_since_the_outage_and_a_move_is_back_once(tmp_path):
    """The guard, in both states: a report answered after the cut, the drawer moves -- "back" once, the green bar."""
    run_ash(prelude(tmp_path) + STREAMING + """
        t=1043; heard 1043; moved; tick
        t=1044; moved; tick
    """)
    assert frequencies(tmp_path) == expected("netlost") + expected("netback"), log_lines(tmp_path)
    assert _records(tmp_path)[-3] == "net=back|43|1043\n"   # away from the last byte (1000) to the first after (1043)
    assert not (tmp_path / "beep.lostat").exists()


def _post(tmp_path, curl_rc):
    return run_ash(prelude(tmp_path) + f"""
        . "$LIB_DIR/common.sh"; date() {{ echo 1234; }}; curl() {{ echo answer; return {curl_rc}; }}
        echo '{{}}' > "$RUN_DIR/body.json"
        http_post_json http://srv/api/v1/agent/progress "$RUN_DIR/body.json"; echo "rc=$?"
    """)


def test_a_request_the_server_answered_stamps_the_moment(tmp_path):
    out = _post(tmp_path, 0)
    assert out.split() == ["answer", "rc=0"], out
    assert (tmp_path / "net.heard").read_text().strip() == "1234"


def test_a_request_that_failed_stamps_nothing_and_keeps_curls_code(tmp_path):
    """The guard of the exit code, in both states: 7 (no connection) stays 7, and no answer is no stamp."""
    out = _post(tmp_path, 7)
    assert out.split() == ["answer", "rc=7"], out
    assert not (tmp_path / "net.heard").exists()
