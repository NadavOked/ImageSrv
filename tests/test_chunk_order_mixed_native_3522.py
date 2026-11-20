"""#3522: מה ש-`mixed` עושה כשהוא ברירת המחדל, על `chunkcast` האמיתי ועל הסוכן האמיתי.

`mixed` משדר חתיכות כבדות וקלות לסירוגין, ולכן חתיכה שהמקלט החמיץ אינה "מה שבא אחרי נקודה מסוימת": מגירה שמצטרפת באמצע
מעבר חסרה לה קבוצה **מפוזרת** (כבדה, קלה, כבדה...), ולא סיומת. כל מה שנשען על "החסר הוא סיומת" היה נשבר. נבדק כאן שלא:

1. מגירה שמצטרפת באמצע מעבר `mixed` (הממסר מאבד לה את החתיכות הראשונות לפי **סדר השידור**, לא לפי אינדקס) חסרה לה קבוצה
   שאינה רצף, והשולח באמת שידר ב-`mixed` (הסדר על החוט שווה לאורקל); מעבר השלמה שני, גם הוא `mixed`, על הרשימה שלה, משלים
   אותה שלמה בייט-בייט.
2. אותה מגירה, אבל ההשלמה היא ב-HTTP (`chunkcast apply --chunks-file`, הפריימים בסדר עולה): שלמה.
3. השלמה ממגירה שכנה (`neighborfill`) ומשכפל שכן (`peerfill`) של קבוצה מפוזרת: שלמה.
4. גל שלם עם שרת אמיתי, `chunkcast` אמיתי בשולח ובמקלטים וסוכן אמיתי (`test_chunk_e2e_2533.py`), בלי אף הגדרה: כל מעבר של
   השרת נושא `--order mixed` (גם ההשלמה), הסדר על החוט אינו סדר הקובץ, וכל יעד שווה למקור.

**בקרה שלילית:** ``server/rounds/chunksend.py`` ו-``server/app.py`` חוזרים לבסיס: הטסט המלא (4) נופל על ה-assert של
`--order` (אין `--order` בפקודה), ובמקביל הסדר על החוט עולה. הטסטים של 1-3 אינם תלויים בקובצי הייצור שנגעו בהם (הם מריצים את
`chunkcast` ואת הסוכן כמו שהם) -- הם שומרים על ההנחה, כמו `test_chunkcast_two_receivers_3181.py`, ונופלים אם החסר שמ-`mixed`
אינו ניתן להשלמה.
"""

from __future__ import annotations

import pytest

import chunkcast_rig as C
from native import requires_native
from test_agent import BASH
from test_chunk_e2e_2533 import CLONER_A, CLONER_B, LOST, _after, _round, stack      # noqa: F401 -- stack הוא fixture
from test_neighborfill_3181 import fill
from test_peerfill_b29 import ID, Neighbour, chunkcut, cut, needy                       # noqa: F401 -- chunkcut ו-cut הם fixtures

pytestmark = requires_native("gcc", "zstd", "jq", "curl", ("bash", BASH), posix=True,
                             why="chunkcast האמיתי, שרת אמיתי והסוכן האמיתי")

JOIN_AFTER = 6                      # the drawer joins after this many chunks went by on the wire
#: 5MB/s on the wire: the relay is a Python thread, and on a loaded queue (five runs at once, 16/11) a faster stream lost real
#: packets for both receivers -- a loss of this rig, so what is asserted about the lack is "at least the join set", never "exactly".
KBPS = 40_000


@pytest.fixture(scope="module")
def cc(tmp_path_factory):
    return C.build_binaries(tmp_path_factory.mktemp("order-bin"))


@pytest.fixture(scope="module")
def img(cc, tmp_path_factory):
    """12 chunks of a raw partition, 0-5 heavy (incompressible) and 6-11 light: mixed puts them 0,6,1,7,2,8,..."""
    return C.build(tmp_path_factory.mktemp("order-img"), cc / "fanout", kind="raw", chunks=12, noisy=(0, 1, 2, 3, 4, 5))


def mixed(img, indices=None):
    """The order `--order mixed` sends, from the contract (interfaces.md) and not from the sender: the chunks of the pass sorted by
    (zlen, index); the lighter half and the heavier half each in index order; interleaved, the heavy one first."""
    by_size = sorted(range(img.n) if indices is None else indices, key=lambda k: (img.zlens[k], k))
    half = len(by_size) // 2
    light, heavy = sorted(by_size[:half]), sorted(by_size[half:])
    return [k for j, h in enumerate(heavy) for k in ([h, light[j]] if j < len(light) else [h])]


class Late:
    """A relay with two receivers; the second is a drawer that joins in the middle of pass 1: until `JOIN_AFTER` chunks went by
    (counted in the order the sender put them on the wire), it is sent no data. Decided by what a packet IS, never by when."""

    def __init__(self):
        self.wires: dict[int, list[int]] = {1: [], 2: []}
        self.current = 1
        self.late = True
        self.relay = C.Relay(2, self.rule)

    def rule(self, receiver, kind, chunk, seq, packet):
        wire = self.wires[self.current]
        if kind == C.DATA and receiver == 0 and chunk not in wire:      # receiver 0 is asked first for every packet
            wire.append(chunk)
        if self.late and self.current == 1 and kind == C.DATA and receiver == 1 and chunk in wire[:JOIN_AFTER]:
            return []
        return [packet]


def _first_pass(cc, img, tmp_path, late):
    """The round's receiver (a) and the drawer (b) on one session; a mixed pass. Returns (b's disk, b's state, a's disk, a's state)."""
    session = C.new_session()
    a, b = img.blank(tmp_path / "a.disk"), img.blank(tmp_path / "b.disk")
    a_state, b_state = tmp_path / "a.state", tmp_path / "b.state"
    ra = C.recv(cc, img, [a], late.relay.listen(0), session, a_state, tag=tmp_path / "ra1")
    rb = C.recv(cc, img, [b], late.relay.listen(1), session, b_state, tag=tmp_path / "rb1")
    s = C.send(cc, img, late.relay.addr, session, tag=tmp_path / "s1", kbps=KBPS, extra=("--order", "mixed"))
    assert s.wait() == 0, s.log()
    assert ra.wait() == 0, ra.log()
    assert rb.wait() == 0, rb.log()
    return session, a, a_state, b, b_state


def _missing_of(img, b, b_state, late):
    """What the drawer lacks after pass 1 -- and the proof that this is the case the test is about: not a suffix, not a prefix."""
    held = C.state_of(b_state)[str(b)]["durable"]
    missing = set(range(img.n)) - held
    assert late.wires[1] == mixed(img), "the sender did not send in the mixed order"
    assert missing >= set(late.wires[1][:JOIN_AFTER]), (missing, late.wires[1])      # more only if the rig lost a packet
    assert missing != set(range(JOIN_AFTER)), "the lack is a prefix: this order is not what the test is about"
    return missing


def test_a_drawer_that_joins_in_the_middle_of_a_mixed_pass_is_completed_whole_by_a_second_mixed_pass(cc, img, tmp_path):
    late = Late()
    try:
        session, _a, _a_state, b, b_state = _first_pass(cc, img, tmp_path, late)
        missing = _missing_of(img, b, b_state, late)
        assert b.read_bytes() == img.expected(set(range(img.n)) - missing)      # nothing of a chunk that did not come whole

        late.current, late.late = 2, False                                       # the completion pass: everyone hears
        rb = C.recv(cc, img, [b], late.relay.listen(1), session, b_state, tag=tmp_path / "rb2", pass_=2)
        s = C.send(cc, img, late.relay.addr, session, tag=tmp_path / "s2", pass_=2, chunks=C.ranges_of(missing),
                   kbps=KBPS, extra=("--order", "mixed"))
        assert s.wait() == 0, s.log()
        assert rb.wait() == 0, rb.log()
        assert late.wires[2] == mixed(img, missing)                              # only the lack, and in mixed order
        assert "pass complete" in rb.out.read_text(), rb.log()
    finally:
        late.relay.close()
    assert C.state_of(b_state)[str(b)]["durable"] == set(range(img.n))
    assert b.read_bytes() == img.expected()


def test_the_same_drawer_is_completed_whole_over_http_in_ascending_order(cc, img, tmp_path):
    """The HTTP completion (`_chunk_fetch`, `chunkcast apply --chunks-file`) takes the frames of the list in ascending order
    whatever order the multicast pass used: what is missing is a set."""
    late = Late()
    try:
        _session, _a, _a_state, b, b_state = _first_pass(cc, img, tmp_path, late)
        missing = _missing_of(img, b, b_state, late)
    finally:
        late.relay.close()
    wanted = sorted(missing)
    run = C.apply(cc, img, [b], b_state, 0, 0, b"".join(img.frame(k) for k in wanted), tag=tmp_path / "apply",
                  chunks=C.ranges_of(missing))
    assert run.process.returncode == 0, run.log()
    assert C.state_of(b_state)[str(b)]["durable"] == set(range(img.n))
    assert b.read_bytes() == img.expected()


SCATTERED = "0-0,2-2,4-4"             # what a drawer that joined a mixed pass late lacks: not a run


def test_a_scattered_lack_is_filled_from_the_neighbour_drawers(tmp_path, cc, cut):
    img, _disk = cut
    got = fill(tmp_path, cc, cut, {"sdb": {"ledger": ["0-0", "2-2", "4-4"]}, "sdc": {"ledger": ["0-4"]}}, missing=SCATTERED,
               env={"NF_BATCH": "1", "NF_CAP_MIBS": "500"})
    assert got["rc"] == "rc=0", got["out"] + got["log"]
    assert got["left"] == [], got["left"]
    assert got["target"] == img.expected({0, 2, 4}), "the chunks of the lack, at their own places, and nothing else"
    assert got["read"]["sdb"] <= {0, 2, 4} and got["read"]["sdc"] <= {0, 2, 4}, got["read"]


def test_a_scattered_lack_is_filled_from_a_neighbour_cloner(tmp_path, cc, cut):
    img, _disk = cut
    peer = Neighbour(img, tmp_path)
    try:
        got = needy(tmp_path, cc, cut, peer.port, missing=SCATTERED, instruction={"ranges": [[0, 0], [2, 2], [4, 4]]})
    finally:
        peer.close()
    assert got["rc"] == "rc=0", got["out"] + got["log"]
    assert got["left"] == [] and got["target"] == img.expected({0, 2, 4}), got["left"]
    assert got["result"] == f"{ID} ok", got["result"]


def test_a_real_wave_with_nothing_set_sends_mixed_in_every_pass_and_every_target_is_the_source(stack):
    """The server's own command, `chunkcast` on both sides, the agent's real shell (`test_chunk_e2e_2533.py`): two cloners lose
    the same chunks in the first pass, so partition 3 has a completion pass beside its first. No setting and no environment."""
    _image, wave, runs = _round(stack, {CLONER_A: ["E2E-A1"], CLONER_B: ["E2E-B1"]})
    repairs = _after(stack, wave, runs)
    assert repairs and f"multicast={len(LOST)} to 2 fetch={{}}" in repairs[0], repairs
    lines = (stack.tmp / "sends.log").read_text().splitlines()
    sends = [line.split() for line in lines if line.startswith("send ")]
    assert len(sends) >= 3, lines                     # partition 1, partition 3, and partition 3's completion pass
    for words in sends:
        assert words.count("--order") == 1 and words[words.index("--order") + 1] == "mixed", words
    assert any("--chunks-file" in words for words in sends), lines        # the completion pass is one of them
    first = stack.wire[(3, 1)]
    assert sorted(first) == list(range(stack.win.n)) and first != sorted(first), first
    again = stack.wire[(3, 2)]
    assert sorted(again) == sorted(LOST), again
