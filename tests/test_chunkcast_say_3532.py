"""#3532: מה שהערכים הקצרים של `--lead-ms` / `--tail-ms` (400/300, ברירות המחדל של השרת) אינם שוברים -- מול `chunkcast` האמיתי על loopback.

`chunkcast send` שולח את ה-ANNOUNCE `lead/200 + 1` פעמים ברווח 200ms לפני הנתונים, ואת ה-END `tail/100 + 1` פעמים ברווח 100ms
אחריהם. כאן נמדד מה שהמספרים האלה מבטיחים:

- **ארבע שליחות END, ושלוש ANNOUNCE** לפני הנתונים (הספירה עצמה: אם הבינארי ישנה את הרווחים, ההנמקה בהערה של
  `chunksend.DEFAULT_*` כבר אינה נכונה, והטסט נופל).
- **מקלט שחסרה לו חתיכה מסיים ב-END**, גם כשאבדו שלושה מארבעת ה-END -- ואם אבדו כולם הוא מחכה ל-`--stall-s` (`stalled`, יציאה 4).
  זה מה שהחיסכון מעמיד מול המחיר: END אחד נשאר בטווח.
- **מקלט שעלה אחרי ה-ANNOUNCE** (אחרי חלון ה-lead) עדיין מוצא את השולח, מה-keepalive (ANNOUNCE כל שנייה), והמשוב שלו מגיע
  לשולח: זה מה שאומר שה-lead הקצר אינו מסכן את המשוב.

הבינארי **לא שונה** ב-#3532, והטסטים האלה עוברים גם על הבסיס כשהדגלים נתונים: הם שומרים על ההנחה שהערכים נשענים עליה.
"""

from __future__ import annotations

import time

import pytest

import chunkcast_rig as C
from native import requires_native

pytestmark = requires_native("gcc", "zstd", posix=True, why="chunkcast צריך gcc, libzstd ו-zstd")

LEAD, TAIL = "400", "300"                       # chunksend.DEFAULT_LEAD_MS / DEFAULT_TAIL_MS, written here: the test is of the binary
SAY = ("--lead-ms", LEAD, "--tail-ms", TAIL)


@pytest.fixture(scope="module")
def cc(tmp_path_factory):
    return C.build_binaries(tmp_path_factory.mktemp("say-bin"))


@pytest.fixture(scope="module")
def img(cc, tmp_path_factory):
    return C.build(tmp_path_factory.mktemp("say-img"), cc / "fanout", noisy=(0, 2))


def one_pass(cc, img, tmp_path, rule, *, stall_s=5, send_extra=()):
    """A sender and one receiver through a relay that applies `rule`. Returns (receiver, its exit code, its state file,
    its disk, the types of the packets the relay saw, in order)."""
    seen: list[int] = []

    def watch(receiver, kind, chunk, seq, packet):
        seen.append(kind)
        return rule(kind, chunk, seq, packet)

    relay = C.Relay(1, watch)
    disk, state, session = img.blank(tmp_path / "a.disk"), tmp_path / "state", C.new_session()
    try:
        r = C.recv(cc, img, [disk], relay.listen(0), session, state, tag=tmp_path / "recv", stall_s=stall_s)
        s = C.send(cc, img, relay.addr, session, tag=tmp_path / "send", extra=(*SAY, *send_extra))
        assert s.wait() == 0, s.log()
        code = r.wait()
    finally:
        relay.close()
    return r, code, state, disk, seen


def test_the_sender_says_three_announces_before_the_data_and_four_ends_after_it(cc, img, tmp_path):
    r, code, _state, disk, seen = one_pass(cc, img, tmp_path, lambda kind, chunk, seq, packet: [packet])
    assert code == 0, r.log()
    assert disk.read_bytes() == img.expected()
    first_data = seen.index(C.DATA)
    assert seen[:first_data] == [C.ANNOUNCE] * 3, seen[:first_data]          # 400/200 + 1: at 0, 200 and 400ms
    assert seen[seen.index(C.END):].count(C.END) == 4, seen[-8:]              # 300/100 + 1: at 0, 100, 200 and 300ms
    assert seen[-4:] == [C.END] * 4, seen[-8:]


def test_a_receiver_that_lacks_a_chunk_ends_on_the_one_end_that_was_not_lost(cc, img, tmp_path):
    ends = {"n": 0}

    def rule(kind, chunk, seq, packet):
        if kind == C.DATA and chunk == 2 and seq == 0:
            return []                                                   # chunk 2 is not complete here: only END ends it
        if kind == C.END:
            ends["n"] += 1
            return [] if ends["n"] <= 3 else [packet]                   # the first three of four are lost
        return [packet]

    began = time.monotonic()
    r, code, state, _disk, _seen = one_pass(cc, img, tmp_path, rule, stall_s=20)
    assert ends["n"] == 4
    assert code == 0 and "pass 1 ended" in r.err.read_text(), r.log()
    assert time.monotonic() - began < 15, "it waited for the stall, not for the END"
    assert C.state_of(state)[next(iter(C.state_of(state)))]["durable"] == set(range(img.n)) - {2}


def test_with_every_end_lost_the_receiver_that_lacks_a_chunk_waits_for_its_stall_limit(cc, img, tmp_path):
    """The other side of the same rule: it is the END that ends it, which is why four of them are sent."""
    def rule(kind, chunk, seq, packet):
        if (kind == C.DATA and chunk == 2 and seq == 0) or kind == C.END:
            return []
        return [packet]

    r, code, _state, _disk, _seen = one_pass(cc, img, tmp_path, rule, stall_s=2)
    assert code == 4 and "pass 1 stalled" in r.err.read_text(), r.log()


def test_a_single_end_is_what_tail_zero_sends_and_one_loss_is_enough_to_stall(cc, img, tmp_path):
    """`--tail-ms 0` (one END): the same lost first END that four ENDs survive stalls the receiver. The number is the margin."""
    ends = {"n": 0}

    def rule(kind, chunk, seq, packet):
        if kind == C.DATA and chunk == 2 and seq == 0:
            return []
        if kind == C.END:
            ends["n"] += 1
            return [] if ends["n"] == 1 else [packet]                   # only the first END is lost
        return [packet]

    r, code, _state, _disk, seen = one_pass(cc, img, tmp_path, rule, stall_s=2, send_extra=("--tail-ms", "0"))
    assert ends["n"] == 1, seen
    assert code == 4, r.log()


def test_a_receiver_that_came_up_after_the_announces_still_finds_the_sender_and_its_feedback_arrives(cc, tmp_path_factory, tmp_path):
    """The sender starts alone; the receiver comes up once the lead window is over (the first `rate` line is the first thing
    the sender says while it streams). It learns the sender from the keepalive ANNOUNCE (once a second), and the sender's
    feedback loop hears it: a `rate` line that is neither `no-feedback` nor `missing`."""
    slow = C.build(tmp_path_factory.mktemp("say-slow"), cc / "fanout", kind="raw", chunks=8, noisy=tuple(range(8)))
    control, session = C.free_port(), C.new_session()
    addr = f"127.0.0.1:{C.free_port()}"
    start = 400_000
    s = C.send(cc, slow, addr, session, tag=tmp_path / "send", kbps=40_000, write_kbps=start,
               extra=(*SAY, "--control-port", str(control), "--rate-min", "200000", "--rate-max", "800000"))
    r = None
    try:
        s.wait_for("rate ")                                             # past the lead: the data is going out
        r = C.recv(cc, slow, [slow.blank(tmp_path / "late.disk")], addr, session, tmp_path / "late.state",
                   tag=tmp_path / "late", stall_s=20, extra=("--control-port", str(control)))
        assert s.wait() == 0, s.log()
        assert r.wait() == 0, r.log()
    finally:
        for run in (s, r):
            if run is not None and run.process.poll() is None:
                run.process.kill()
    reasons = [line.split()[2] for line in s.out.read_text().splitlines() if line.startswith("rate ")]
    assert "no-feedback" in reasons, reasons                            # it did start alone
    assert any(reason in ("increase", "hold", "decrease") for reason in reasons), reasons
