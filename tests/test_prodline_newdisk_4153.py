"""#4153: the simulated pass must stay open until the returning receiver finishes."""

from test_prodline_newdisk_3181 import BACK, DONE, RECEIVE1, _line, _plan, pytestmark


def test_returning_receiver_finishes_before_the_pass_ends(tmp_path):
    got = _line(tmp_path, [RECEIVE1, DONE], f"{BACK} NF_TRACE=1 SIDE_S=5", _plan("HTTP", []))
    trace = got["trace"]
    assert trace[-1] == "rc=0", got
    assert trace.index("SIDE-END") < trace.index("PASS-END"), trace
