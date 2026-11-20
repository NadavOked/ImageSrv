"""#2533, שלב 3: המקבל והשולח של מנוע החתיכות (`agent/chunkcast.c`).

מה שנבדק כאן הוא הכלל שהמנוע בנוי סביבו: חתיכה מגיעה לדיסק רק אחרי שה-hash
שלה תאם לאינדקס, ורק חתיכה שנכתבה ועברה flush נרשמת כ"עמידה". כל השאר —
חבילות שאבדו, חתיכה משובשת, מקלט שנעלם — מסתיים ב"חסר", ומושלם במעבר הבא.

המקלטים והשולח הם התהליכים האמיתיים. ביניהם יושב ממסר (`chunkcast_rig.Relay`)
שמאבד, מכפיל, משבש או הופך סדר של חבילות לפי זהותן, כך שכל ריצה מאבדת את אותן
חתיכות. ה"דיסק" הוא קובץ: `CHUNKCAST_ALLOW_FILE=1` מרשה זאת בטסט בלבד, והסוכן
אינו מגדיר אותו (נבדק למטה).
"""

from __future__ import annotations

import os
import signal
import socket
import time
from pathlib import Path

import pytest

import chunkcast_rig as C
from native import requires_native

REPO = Path(__file__).resolve().parent.parent
pytestmark = requires_native("gcc", "zstd", posix=True, why="chunkcast צריך gcc, libzstd ו-zstd")


@pytest.fixture(scope="module")
def cc(tmp_path_factory):
    return C.build_binaries(tmp_path_factory.mktemp("chunkcast-bin"))


@pytest.fixture(scope="module")
def img(cc, tmp_path_factory):
    """partclone, 4.5 חתיכות: 0 ו-2 אינן נדחסות (כ-2,900 חבילות כל אחת), השאר קצרות."""
    return C.build(tmp_path_factory.mktemp("img"), cc / "fanout", noisy=(0, 2))


def _clean(cc, image, tmp_path, targets, **kw):
    """שולח ומקבל אחד על loopback, בלי ממסר. מחזיר (המקלט, השולח, קובץ המצב)."""
    session, port, state = C.new_session(), C.free_port(), tmp_path / "state"
    r = C.recv(cc, image, targets, f"127.0.0.1:{port}", session, state, tag=tmp_path / "recv", **kw)
    s = C.send(cc, image, f"127.0.0.1:{port}", session, tag=tmp_path / "send")
    assert s.wait() == 0, s.log()
    return r, s, state


def test_a_pass_says_on_stdout_what_each_drive_was_too_slow_to_take_before_its_verdict(cc, img, tmp_path):
    """#3141/#3170: השרת מבחין בדיסק איטי מרשת שאבדה רק לפי מה שהמקלט עצמו אומר. השורה `skipped <node> <n>` לכל יעד,
    לפני `pass <word>`: בפס נקי -- 0, וזה נאמר (ראיה), לא חסר."""
    a, b = img.blank(tmp_path / "a.disk"), img.blank(tmp_path / "b.disk")
    r, _s, _state = _clean(cc, img, tmp_path, [a, b])
    assert r.wait() == 0, r.log()
    out = r.out.read_text().splitlines()
    assert [line for line in out if line.startswith("skipped ")] == [f"skipped {a} 0", f"skipped {b} 0"], out
    assert out[-1] == "pass complete" and out.index(f"skipped {b} 0") < len(out) - 1, out


def test_a_clean_pass_writes_the_partition_byte_for_byte_and_says_what_is_durable(cc, img, tmp_path):
    a, b = img.blank(tmp_path / "a.disk"), img.blank(tmp_path / "b.disk")
    r, _s, state = _clean(cc, img, tmp_path, [a, b])
    assert r.wait() == 0, r.log()
    assert "pass complete" in r.out.read_text(), r.log()
    want = img.expected()
    assert a.read_bytes() == want and b.read_bytes() == want
    assert C.state_of(state) == {str(t): {"alive": True, "durable": set(range(img.n)), "reason": ""}
                                 for t in (a, b)}


def test_a_raw_partition_is_written_at_k_times_4mib(cc, tmp_path):
    raw = C.build(tmp_path / "img", cc / "fanout", kind="raw", noisy=(1,), chunks=2.25, name="p2.msr.pcl.zst")
    disk = raw.blank(tmp_path / "raw.disk")
    r, _s, state = _clean(cc, raw, tmp_path, [disk])
    assert r.wait() == 0, r.log()
    assert disk.read_bytes() == raw.expected() == raw.data
    assert C.state_of(state)[str(disk)]["durable"] == {0, 1, 2}


def test_a_block_size_that_does_not_divide_4mib_puts_every_byte_in_its_block(cc, tmp_path):
    """חוזה הפורמט, סעיף 2: גבול חתיכה יכול ליפול באמצע בלוק."""
    odd = C.build(tmp_path / "img", cc / "fanout", bs=12288, noisy=(), chunks=2.3)
    assert (4 << 20) % odd.bs
    disk = odd.blank(tmp_path / "odd.disk")
    r, _s, _state = _clean(cc, odd, tmp_path, [disk])
    assert r.wait() == 0, r.log()
    assert disk.read_bytes() == odd.expected()


def _pass(cc, image, relay, session, receivers, tmp_path, n, chunks=None, file=None):
    """מעבר n דרך הממסר: מפעיל מקלט לכל (מצב, יעדים), משדר, ומחזיר את המקלטים אחרי שיצאו."""
    runs = [C.recv(cc, image, targets, relay.listen(i), session, state, tag=tmp_path / f"recv{i}-p{n}", pass_=n)
            for i, (state, targets) in enumerate(receivers)]
    s = C.send(cc, image, relay.addr, session, tag=tmp_path / f"send-p{n}", pass_=n, chunks=chunks, file=file)
    assert s.wait() == 0, s.log()
    for run in runs:
        assert run.wait() == 0, run.log()
    return runs


def test_lost_packets_leave_whole_chunks_missing_and_a_repair_pass_completes_them(cc, img, tmp_path):
    """חבילה אחת שאבדה = החתיכה כולה חסרה, ושום בית שלה לא נכתב. מעבר השלמה
    שמשדר רק את החסר משלים את הדיסק לאותם בייטים בדיוק."""
    lost = {0, 3}

    def rule(receiver, kind, chunk, seq, packet):
        return [] if kind == C.DATA and chunk in lost and seq == 0 else [packet]

    relay = C.Relay(1, rule)
    disk, state, session = img.blank(tmp_path / "a.disk"), tmp_path / "state", C.new_session()
    try:
        _pass(cc, img, relay, session, [(state, [disk])], tmp_path, 1)
        have = set(range(img.n)) - lost
        assert C.state_of(state)[str(disk)]["durable"] == have
        assert disk.read_bytes() == img.expected(have)        # nothing of a chunk that did not arrive whole
        relay.rule = lambda receiver, kind, chunk, seq, packet: [packet]
        seen_before = set(relay.chunks_seen[0])
        relay.chunks_seen[0].clear()
        (run,) = _pass(cc, img, relay, session, [(state, [disk])], tmp_path, 2, chunks=C.ranges_of(lost))
        assert seen_before >= set(range(img.n)) - lost and relay.chunks_seen[0] == lost   # the repair pass sent only those
    finally:
        relay.close()
    assert "pass complete" in run.out.read_text(), run.log()
    assert C.state_of(state)[str(disk)]["durable"] == set(range(img.n))
    assert disk.read_bytes() == img.expected()


def test_a_chunk_whose_hash_is_not_the_indexs_is_not_written(cc, img, tmp_path):
    """פריים תקין, באורך הנכון, עם תוכן אחר: חתיכה 2 בקובץ של השולח הוחלפה
    בחתיכה אקראית אחרת (אותו אורך דחוס — נתונים שאינם נדחסים). ה-zstd שלה
    תקין וה-checksum שלה תואם; רק ה-XXH64 מול האינדקס תופס אותה."""
    other = C.build(tmp_path / "other", cc / "fanout", noisy=(0, 2), salt=99)
    assert other.zlens[2] == img.zlens[2] and other.frame(2) != img.frame(2)
    forged = tmp_path / "forged.pcl.zst"
    body = bytearray(img.file.read_bytes())
    body[img.offsets[2]:img.offsets[2] + img.zlens[2]] = other.frame(2)
    forged.write_bytes(body)

    relay = C.Relay(1)
    disk, state, session = img.blank(tmp_path / "a.disk"), tmp_path / "state", C.new_session()
    try:
        (run,) = _pass(cc, img, relay, session, [(state, [disk])], tmp_path, 1, file=forged)
        have = set(range(img.n)) - {2}
        assert "chunk 2 does not match the index (wrong XXH64) -- not written" in run.err.read_text(), run.log()
        assert C.state_of(state)[str(disk)]["durable"] == have
        assert disk.read_bytes() == img.expected(have)        # the forged chunk reached no block
        _pass(cc, img, relay, session, [(state, [disk])], tmp_path, 2, chunks="2-2")
    finally:
        relay.close()
    assert disk.read_bytes() == img.expected()


def test_a_packet_damaged_on_the_wire_fails_its_chunk_only(cc, img, tmp_path):
    def rule(receiver, kind, chunk, seq, packet):
        if kind == C.DATA and chunk == 1 and seq == 0:
            return [packet[:40] + bytes([packet[40] ^ 0x55]) + packet[41:]]
        return [packet]

    relay = C.Relay(1, rule)
    disk, state, session = img.blank(tmp_path / "a.disk"), tmp_path / "state", C.new_session()
    try:
        (run,) = _pass(cc, img, relay, session, [(state, [disk])], tmp_path, 1)
    finally:
        relay.close()
    have = set(range(img.n)) - {1}
    assert "chunk 1 does not match the index" in run.err.read_text(), run.log()
    assert C.state_of(state)[str(disk)]["durable"] == have
    assert disk.read_bytes() == img.expected(have)


def test_duplicates_and_reversed_order_write_nothing_wrong_and_end_whole(cc, img, tmp_path):
    """כל חבילה מגיעה פעמיים, ובסדר הפוך בחלונות של 7. מה שנכתב הוא בדיוק מה שעמיד --
    שום בית של חתיכה אחרת, ושום חתיכה פעמיים במקום לא נכון -- והדיסק מסתיים שלם.
    עומס כפול על loopback יכול לאבד חבילה אמיתית (נמדד על ה-testrunner, 12/11): מה
    שחסר אז מושלם במעבר השלמה, כמו בגל אמיתי, ואינו כישלון של הטסט."""
    relay = C.Relay(1, lambda receiver, kind, chunk, seq, packet: [packet, packet], reverse=7)
    disk, state, session = img.blank(tmp_path / "a.disk"), tmp_path / "state", C.new_session()
    try:
        (run,) = _pass(cc, img, relay, session, [(state, [disk])], tmp_path, 1)
        assert "duplicate" in run.err.read_text() and " 0 duplicate" not in run.err.read_text(), run.log()
        have = C.state_of(state)[str(disk)]["durable"]
        assert disk.read_bytes() == img.expected(have)
        missing = set(range(img.n)) - have
        if missing:
            relay.rule, relay.reverse = (lambda receiver, kind, chunk, seq, packet: [packet]), 0
            _pass(cc, img, relay, session, [(state, [disk])], tmp_path, 2, chunks=C.ranges_of(missing))
    finally:
        relay.close()
    assert C.state_of(state)[str(disk)]["durable"] == set(range(img.n))
    assert disk.read_bytes() == img.expected()


def test_a_stream_of_another_session_is_not_ours(cc, img, tmp_path):
    """שולח אחר באותו וילן (מספר סשן אחר): שום דבר לא נכתב, והמקלט אומר שהזרם לא הגיע."""
    port, disk, state = C.free_port(), img.blank(tmp_path / "a.disk"), tmp_path / "state"
    r = C.recv(cc, img, [disk], f"127.0.0.1:{port}", C.new_session(), state, tag=tmp_path / "recv", start_s=2)
    s = C.send(cc, img, f"127.0.0.1:{port}", C.new_session(), tag=tmp_path / "send")
    assert s.wait() == 0, s.log()
    assert r.wait() == 3, r.log()
    assert "pass no-stream" in r.out.read_text()
    assert disk.read_bytes() == img.expected(set())
    assert C.state_of(state)[str(disk)]["durable"] == set()


def test_a_stream_of_another_image_under_our_session_writes_nothing_and_is_named(cc, img, tmp_path):
    """אותו מספר סשן, אימג' אחר (אותם אורכי פריימים בחתיכות שאינן נדחסות): ההכרזה
    הסותרת אינה עוצרת את המקלט (חבילה מזויפת אחת לא תפיל אותו), אבל אף חתיכה לא
    עוברת את האינדקס, שום דבר לא נכתב, והמקלט אומר איזה זרם זה היה."""
    other = C.build(tmp_path / "other", cc / "fanout", noisy=(0, 2), salt=5)
    port, disk, state, session = C.free_port(), img.blank(tmp_path / "a.disk"), tmp_path / "state", C.new_session()
    r = C.recv(cc, img, [disk], f"127.0.0.1:{port}", session, state, tag=tmp_path / "recv")
    s = C.send(cc, other, f"127.0.0.1:{port}", session, tag=tmp_path / "send")
    assert s.wait() == 0, s.log()
    assert r.wait() == 7, r.log()
    assert "pass another-index" in r.out.read_text() and "names another index -- ignored" in r.err.read_text()
    assert disk.read_bytes() == img.expected(set())
    assert C.state_of(state)[str(disk)]["durable"] == set()


def test_one_forged_announce_does_not_stop_a_receiver(cc, img, tmp_path):
    """הרעיון מ-filecast (Receiver.cpp, "ignoring conflicting announcement"): הכרזה
    סותרת נזרקת. הזרם האמיתי ממשיך, והדיסק שלם."""
    port, disk, state, session = C.free_port(), img.blank(tmp_path / "a.disk"), tmp_path / "state", C.new_session()
    r = C.recv(cc, img, [disk], f"127.0.0.1:{port}", session, state, tag=tmp_path / "recv")
    forged = (C.PKT_HEAD.pack(C.MAGIC, C.ANNOUNCE, C.VERSION, 3, int(session, 16))
              + (1).to_bytes(4, "big") + (img.n + 7).to_bytes(8, "big") + bytes(8))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(forged, ("127.0.0.1", port))
    s = C.send(cc, img, f"127.0.0.1:{port}", session, tag=tmp_path / "send")
    assert s.wait() == 0, s.log()
    assert r.wait() == 0, r.log()
    assert "names another index -- ignored" in r.err.read_text()
    assert disk.read_bytes() == img.expected()


def test_a_receiver_that_vanished_carries_on_from_its_state(cc, img, tmp_path):
    """המקלט נהרג (SIGKILL) אחרי שחלק מהחתיכות עמידות. מקלט חדש עם אותו קובץ
    מצב מקבל במעבר ההשלמה רק את מה שחסר, והדיסק שלם."""
    gate = {"open": {0, 1}}

    def rule(receiver, kind, chunk, seq, packet):
        if kind == C.END:                       # the first pass never ends for it: it is killed mid-stream
            return [packet] if len(gate["open"]) == img.n else []
        return [packet] if kind != C.DATA or chunk in gate["open"] else []

    relay = C.Relay(1, rule)
    disk, state, session = img.blank(tmp_path / "a.disk"), tmp_path / "state", C.new_session()
    try:
        r = C.recv(cc, img, [disk], relay.listen(0), session, state, tag=tmp_path / "recv1", stall_s=60)
        s = C.send(cc, img, relay.addr, session, tag=tmp_path / "send1")
        end = time.monotonic() + 20
        while not (state.exists() and C.state_of(state)[str(disk)]["durable"] == {0, 1}):
            assert time.monotonic() < end and r.process.poll() is None, r.log()
            time.sleep(0.05)
        r.process.send_signal(signal.SIGKILL)
        r.process.wait(10)
        s.wait()
        assert C.state_of(state)[str(disk)]["durable"] == {0, 1}      # what the dead receiver had flushed
        gate["open"] = set(range(img.n))
        relay.chunks_seen[0].clear()
        missing = set(range(img.n)) - {0, 1}
        (run,) = _pass(cc, img, relay, session, [(state, [disk])], tmp_path, 2, chunks=C.ranges_of(missing))
        assert relay.chunks_seen[0] == missing
    finally:
        relay.close()
    assert "pass complete" in run.out.read_text(), run.log()
    assert disk.read_bytes() == img.expected()


def test_one_target_that_cannot_be_written_does_not_stop_its_neighbour(cc, img, tmp_path):
    good, small = img.blank(tmp_path / "good.disk"), img.blank(tmp_path / "small.disk", short=8192)
    r, _s, state = _clean(cc, img, tmp_path, [good, small])
    assert r.wait() == 0, r.log()
    got = C.state_of(state)
    assert got[str(good)] == {"alive": True, "durable": set(range(img.n)), "reason": ""}
    assert got[str(small)]["alive"] is False and "too small" in got[str(small)]["reason"]
    assert good.read_bytes() == img.expected()
    assert small.read_bytes() == bytes([C.GARBAGE]) * (img.device_bytes - 8192)


def test_a_file_is_not_a_target_outside_a_test(cc, img, tmp_path):
    """#51: צומת שאינו התקן בלוקים הוא קובץ רגיל — כתיבה אליו עוברת כל hash."""
    disk, state = img.blank(tmp_path / "a.disk"), tmp_path / "state"
    env = {k: v for k, v in os.environ.items() if k != "CHUNKCAST_ALLOW_FILE"}
    run = C._spawn([str(cc / "chunkcast"), "recv", "--listen", f"127.0.0.1:{C.free_port()}", "--session",
                    C.new_session(), "--part", "3", "--pass", "1", *C.image_args(img), "--state", str(state),
                    "--start-s", "5", str(disk)], tmp_path / "recv", env)
    assert run.wait() == 5, run.log()
    assert C.state_of(state)[str(disk)]["alive"] is False
    assert disk.read_bytes() == img.expected(set())


def test_the_agent_never_allows_a_file_as_a_target():
    hits = [str(p.relative_to(REPO)) for p in (REPO / "agent").rglob("*")
            if p.is_file() and p.name != "chunkcast.c" and b"CHUNKCAST_ALLOW_FILE" in p.read_bytes()]
    hits += [str(p.relative_to(REPO)) for p in (REPO / "tools").rglob("*.sh")
             if b"CHUNKCAST_ALLOW_FILE" in p.read_bytes()]
    assert hits == []


@pytest.mark.parametrize("damage", ["xxh", "gap", "chunk_bytes", "tail"])
def test_an_index_that_is_not_the_manifests_or_not_consistent_is_refused(cc, img, tmp_path, damage):
    """חוזה הפורמט, סעיפים 4–5: לפני שמאמינים לשורה אחת באינדקס."""
    disk, state = img.blank(tmp_path / "a.disk"), tmp_path / "state"
    lines = img.idx.read_text().splitlines()
    index_xxh = img.index_xxh
    if damage == "xxh":
        index_xxh = "0" * 16
    else:
        if damage == "gap":
            off, zlen, h = lines[2].split()
            lines[2] = f"{int(off) + 1} {zlen} {h}"
        elif damage == "chunk_bytes":
            lines[0] = lines[0].replace(" 4194304 ", " 1048576 ")
        else:
            lines.append(lines[-1])
        bad = tmp_path / "bad.idx"
        bad.write_text("\n".join(lines) + "\n")
        index_xxh = C.xxh64(cc / "fanout", bad.read_bytes())
        img = C.Image(**{**img.__dict__, "idx": bad})
    run = C._spawn([str(cc / "chunkcast"), "recv", "--listen", f"127.0.0.1:{C.free_port()}", "--session",
                    C.new_session(), "--part", "3", "--pass", "1", *C.image_args(img, index_xxh),
                    "--state", str(state), str(disk)], tmp_path / "recv")
    assert run.wait() == 2, run.log()
    assert ("not the manifest's index" if damage == "xxh" else "not a format-2 index") in run.err.read_text()
    assert disk.read_bytes() == img.expected(set()) and not state.exists()


def test_a_head_frame_that_is_not_the_indexs_is_refused(cc, img, tmp_path):
    other = C.build(tmp_path / "other", cc / "fanout", noisy=(), chunks=4.5, bs=8192)
    assert other.head_z.read_bytes() != img.head_z.read_bytes()
    mixed = C.Image(**{**img.__dict__, "head_z": other.head_z})
    disk, state = img.blank(tmp_path / "a.disk"), tmp_path / "state"
    run = C._spawn([str(cc / "chunkcast"), "recv", "--listen", f"127.0.0.1:{C.free_port()}", "--session",
                    C.new_session(), "--part", "3", "--pass", "1", *C.image_args(mixed), "--state", str(state),
                    str(disk)], tmp_path / "recv")
    assert run.wait() == 2, run.log()
    assert "the head frame" in run.err.read_text() and "is refused" in run.err.read_text()


def test_apply_writes_the_frames_an_http_range_gave(cc, img, tmp_path):
    """השלמה למקלט בודד: הפריימים של חתיכות 1–2, כפי ש-`Range` מחזיר אותם, דרך אותו שער."""
    disk, state = img.blank(tmp_path / "a.disk"), tmp_path / "state"
    run = C.apply(cc, img, [disk], state, 1, 2, img.frame(1) + img.frame(2), tag=tmp_path / "apply")
    assert run.process.returncode == 0, run.log()
    assert C.state_of(state)[str(disk)]["durable"] == {1, 2}
    assert disk.read_bytes() == img.expected({1, 2})


def test_apply_does_not_write_a_frame_that_is_not_the_indexs(cc, img, tmp_path):
    other = C.build(tmp_path / "other", cc / "fanout", noisy=(0, 2), salt=99)
    disk, state = img.blank(tmp_path / "a.disk"), tmp_path / "state"
    run = C.apply(cc, img, [disk], state, 1, 2, img.frame(1) + other.frame(2), tag=tmp_path / "apply")
    assert run.process.returncode == 8, run.log()
    assert C.state_of(state)[str(disk)]["durable"] == {1}         # the good one before it stays
    assert disk.read_bytes() == img.expected({1})
    short = C.apply(cc, img, [disk], state, 3, 1, img.frame(3)[:-1], tag=tmp_path / "short")
    assert short.process.returncode == 8, short.log()
    assert disk.read_bytes() == img.expected({1})


def test_a_state_of_another_image_is_refused_not_merged(cc, img, tmp_path):
    other = C.build(tmp_path / "other", cc / "fanout", noisy=(), salt=3)
    disk, state = img.blank(tmp_path / "a.disk"), tmp_path / "state"
    first = C.apply(cc, other, [disk], state, 0, 1, other.frame(0), tag=tmp_path / "first")
    assert first.process.returncode == 0, first.log()
    before = disk.read_bytes()
    run = C.apply(cc, img, [disk], state, 1, 1, img.frame(1), tag=tmp_path / "second")
    assert run.process.returncode == 2, run.log()
    assert "of another image or partition" in run.err.read_text()
    assert disk.read_bytes() == before


def test_multicast_on_this_host(cc, img, tmp_path):
    """קבוצת מולטיקאסט אמיתית, TTL 0: החבילות אינן יוצאות מהמכונה."""
    port, disk, state, session = C.free_port(), img.blank(tmp_path / "a.disk"), tmp_path / "state", C.new_session()
    group = f"239.255.{port >> 8 & 255}.{port & 255 or 1}:{port}"
    r = C.recv(cc, img, [disk], group, session, state, tag=tmp_path / "recv")
    s = C.send(cc, img, group, session, tag=tmp_path / "send", extra=("--ttl", "0"))
    assert s.wait() == 0, s.log()
    assert r.wait() == 0, r.log()
    assert disk.read_bytes() == img.expected()


FLUSH_FAILS = """
#define _GNU_SOURCE
#include <errno.h>
int fdatasync(int fd) { (void)fd; errno = EIO; return -1; }
int fsync(int fd) { (void)fd; errno = EIO; return -1; }
"""


def test_a_chunk_is_not_durable_before_a_flush_that_succeeded(cc, img, tmp_path):
    """חוזה הפורמט 9.1, כלל 1: רק אחרי fsync שהצליח. כאן כל flush נכשל (EIO): החתיכות
    נכתבו למטמון, אבל אף אחת אינה נרשמת כעמידה, והיעד נכשל בשמו — לא "הושלם"."""
    import subprocess
    src, so = tmp_path / "noflush.c", tmp_path / "noflush.so"
    src.write_text(FLUSH_FAILS, encoding="ascii")
    built = subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", "-o", str(so), str(src)],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert built.returncode == 0, built.stderr
    port, disk, state, session = C.free_port(), img.blank(tmp_path / "a.disk"), tmp_path / "state", C.new_session()
    r = C.recv(cc, img, [disk], f"127.0.0.1:{port}", session, state, tag=tmp_path / "recv",
               env={**C.ENV, "LD_PRELOAD": str(so)})
    s = C.send(cc, img, f"127.0.0.1:{port}", session, tag=tmp_path / "send")
    assert s.wait() == 0, s.log()
    assert r.wait() == 5, r.log()
    got = C.state_of(state)[str(disk)]
    assert got["alive"] is False and "flush" in got["reason"]
    assert got["durable"] == set(), "a chunk was reported durable though no flush ever succeeded"


WRITE_HANGS = """
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <unistd.h>
ssize_t pwrite(int fd, const void *buf, size_t n, off_t off) {
    static ssize_t (*real)(int, const void *, size_t, off_t);
    char link[64], path[4096];
    const char *hang = getenv("CHUNK_TEST_HANG");
    if (!real) real = dlsym(RTLD_NEXT, "pwrite");
    snprintf(link, sizeof link, "/proc/self/fd/%d", fd);
    ssize_t len = readlink(link, path, sizeof path - 1);
    if (hang && len > 0 && (path[len] = 0, strcmp(path, hang) == 0)) for (;;) sleep(3600);
    return real(fd, buf, n, off);
}
"""


def test_a_drive_that_stops_taking_writes_fails_alone_and_the_pass_still_ends(cc, img, tmp_path):
    """כמו ב-fanout (#674, #1796): מגירה שהכתיבה אליה לא חוזרת נכשלת בשמה אחרי התקרה,
    והשכנה שלה מסיימת. המקלט אינו נתקע איתה: הוא כותב את הפסק ויוצא."""
    import subprocess
    src, so = tmp_path / "hang.c", tmp_path / "hang.so"
    src.write_text(WRITE_HANGS, encoding="ascii")
    built = subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", "-o", str(so), str(src),
                            "-ldl"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert built.returncode == 0, built.stderr
    good, stuck = img.blank(tmp_path / "good.disk"), img.blank(tmp_path / "stuck.disk")
    port, state, session = C.free_port(), tmp_path / "state", C.new_session()
    # 5s, not less: on a loaded test host a healthy flush took over 0.7s, and the good drive was failed with the stuck one
    env = {**C.ENV, "LD_PRELOAD": str(so), "CHUNK_TEST_HANG": str(stuck), "CHUNKCAST_WRITE_STALL_MS": "5000"}
    r = C.recv(cc, img, [good, stuck], f"127.0.0.1:{port}", session, state, tag=tmp_path / "recv", env=env)
    s = C.send(cc, img, f"127.0.0.1:{port}", session, tag=tmp_path / "send")
    assert s.wait() == 0, s.log()
    assert r.wait(60) == 0, r.log()
    got = C.state_of(state)
    assert got[str(good)] == {"alive": True, "durable": set(range(img.n)), "reason": ""}
    assert got[str(stuck)]["alive"] is False and "not waited for" in got[str(stuck)]["reason"]
    assert good.read_bytes() == img.expected()


def test_a_repair_list_far_larger_than_an_argument_is_read_from_its_file(cc, img, tmp_path):
    """E2BIG: רשימה של אלפי טווחים אינה נכנסת לארגומנט אחד. כאן הקובץ מכיל את אותו טווח
    200,000 פעם (מעל 1MB), והשולח קורא אותו ומשדר."""
    port, disk, state, session = C.free_port(), img.blank(tmp_path / "a.disk"), tmp_path / "state", C.new_session()
    r = C.recv(cc, img, [disk], f"127.0.0.1:{port}", session, state, tag=tmp_path / "recv")
    s = C.send(cc, img, f"127.0.0.1:{port}", session, tag=tmp_path / "send", chunks=",".join(["1-1"] * 200_000))
    assert s.wait() == 0, s.log()
    assert r.wait() == 0, r.log()
    assert C.state_of(state)[str(disk)]["durable"] == {1}


# --- the adversarial review of #3063 (13/11) -------------------------------------------


def _hang_library(tmp_path):
    import subprocess
    src, so = tmp_path / "hang.c", tmp_path / "hang.so"
    src.write_text(WRITE_HANGS, encoding="ascii")
    built = subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", "-o", str(so), str(src),
                            "-ldl"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert built.returncode == 0, built.stderr
    return so


def test_one_apply_takes_a_whole_list_of_scattered_ranges(cc, tmp_path):
    """סקירה, ממצא 3: השלמה של חוסר מפוזר הייתה `curl` ו-`chunkcast apply` לכל טווח. עכשיו תהליך אחד
    מקבל את כל הרשימה, והפריימים באים אחד אחרי השני. כל חתיכה נבדקת מול האינדקס לפני הכתיבה, כמו קודם."""
    img = C.build(tmp_path / "img", cc / "fanout", chunks=12, noisy=(1, 6))
    disk, other, state = img.blank(tmp_path / "a.disk"), img.blank(tmp_path / "b.disk"), tmp_path / "state"
    wanted = [1, 3, 4, 6, 9, 11]
    run = C.apply(cc, img, [disk, other], state, 0, 0, b"".join(img.frame(k) for k in wanted),
                  tag=tmp_path / "apply", chunks="1-1,3-4,6-6,9-9,11-11")
    assert run.process.returncode == 0, run.log()
    assert C.state_of(state)[str(disk)]["durable"] == set(wanted) == C.state_of(state)[str(other)]["durable"]
    assert disk.read_bytes() == img.expected(set(wanted)) == other.read_bytes()
    assert "pass applied" in run.out.read_text() and "6 of 6 chunks taken" in run.err.read_text()


def _apply_cost(err: Path) -> tuple[int, int, int]:
    """(chunks taken, flushes, state writes) from the summary line apply itself says."""
    import re
    said = re.search(r"(\d+) of \d+ chunks taken from the stream, (\d+) flushes, (\d+) state writes", err.read_text())
    assert said, err.read_text()
    return tuple(map(int, said.groups()))


def test_a_fetch_is_flushed_and_its_state_saved_in_batches_not_a_range_at_a_time(cc, tmp_path):
    """ממצא 3, נמדד בשתי הדרכים על אותם 24 טווחים של חתיכה אחת. **לפני:** `chunkcast apply` לכל טווח --
    24 תהליכים, וכל אחד טוען את המצב, מסנכרן לדיסק וכותב את קובץ המצב. **עכשיו:** תהליך אחד לכל
    הרשימה, וה-flush וקובץ המצב לפי זמן. המספרים הם של התהליך עצמו (שורת הסיכום שלו); הדיסק זהה."""
    import warnings
    chunks = 48                                 # 24 ranges, not more: this test shares its host with tests that time packets
    img = C.build(tmp_path / "img", cc / "fanout", chunks=chunks, noisy=())
    wanted = list(range(0, chunks, 2))                                        # 24 ranges of one chunk

    old_disk, old_state = img.blank(tmp_path / "old.disk"), tmp_path / "old.state"
    began, old_flushes, old_writes = time.monotonic(), 0, 0
    for k in wanted:                                                          # the way it was: a process a range
        run = C.apply(cc, img, [old_disk], old_state, k, 1, img.frame(k), tag=tmp_path / "one")
        assert run.process.returncode == 0, run.log()
        taken, flushes, writes = _apply_cost(run.err)
        old_flushes, old_writes = old_flushes + flushes, old_writes + writes
    old_took = time.monotonic() - began

    disk, state = img.blank(tmp_path / "new.disk"), tmp_path / "new.state"
    began = time.monotonic()
    run = C.apply(cc, img, [disk], state, 0, 0, b"".join(img.frame(k) for k in wanted), tag=tmp_path / "apply",
                  chunks=",".join(f"{k}-{k}" for k in wanted), extra=("--sync-ms", "500"), seconds=180)
    took = time.monotonic() - began
    assert run.process.returncode == 0, run.log()
    taken, flushes, writes = _apply_cost(run.err)

    assert taken == 24 and C.state_of(state)[str(disk)]["durable"] == set(wanted) == C.state_of(old_state)[str(old_disk)]["durable"]
    assert disk.read_bytes() == old_disk.read_bytes() == img.expected(set(wanted))
    assert old_flushes >= 24 and old_writes >= 24, (old_flushes, old_writes)   # the old way: one of each a range, at least
    assert flushes * 4 <= old_flushes and writes * 4 <= old_writes, \
        f"one process: {flushes} flushes, {writes} state writes; a process a range: {old_flushes}, {old_writes}"
    warnings.warn(UserWarning(
        f"#2533 measured: 24 scattered ranges through chunkcast apply -- a process a range: {old_took:.1f}s, "
        f"{old_flushes} flushes, {old_writes} state writes; one process: {took:.1f}s, {flushes} flushes, {writes} state writes"))


def test_apply_gives_a_drive_that_stopped_taking_writes_the_same_ceiling_as_a_receiver(cc, tmp_path):
    """סקירה, ממצא 4: בהורדה לא הייתה תקרת כתיבה. מגירה שהכתיבה אליה אינה חוזרת נכשלת בשמה אחרי
    התקרה (`CHUNKCAST_WRITE_STALL_MS`, כמו במקלט), והשכנה שלה מקבלת את כל החתיכות. התהליך אינו נתקע.

    12 חתיכות, ולא פחות: המגירה התקועה מחזיקה חתיכה אחת בכתיבה ועוד 8 בתור שלה. רק מהעשירית ההורדה
    עצמה מחכה לה -- ושם היא חייבת את התקרה, אחרת היא מחכה לנצח."""
    big = C.build(tmp_path / "img", cc / "fanout", chunks=12, noisy=())
    so = _hang_library(tmp_path)
    good, stuck, state = big.blank(tmp_path / "good.disk"), big.blank(tmp_path / "stuck.disk"), tmp_path / "state"
    env = {**C.ENV, "LD_PRELOAD": str(so), "CHUNK_TEST_HANG": str(stuck), "CHUNKCAST_WRITE_STALL_MS": "3000"}
    run = C.apply(cc, big, [good, stuck], state, 0, big.n, b"".join(big.frame(k) for k in range(big.n)),
                  tag=tmp_path / "apply", env=env, seconds=60)
    assert run.process.returncode == 0, run.log()
    got = C.state_of(state)
    assert got[str(good)] == {"alive": True, "durable": set(range(big.n)), "reason": ""}
    assert got[str(stuck)]["alive"] is False and "not waited for" in got[str(stuck)]["reason"]
    assert good.read_bytes() == big.expected()


def test_a_fetch_that_is_stopped_while_it_waits_for_a_frame_says_so_and_keeps_what_it_has(cc, img, tmp_path):
    """הסבב נסגר באמצע הורדה: SIGTERM מגיע כש-apply מחכה לפריים הבא. הוא אינו ממשיך לחכות לזרם —
    הוא מסנכרן את מה שכתב, כותב את המצב ואומר `stopped` (קוד 6)."""
    import os
    import signal
    import subprocess
    disk, state = img.blank(tmp_path / "a.disk"), tmp_path / "state"
    read_end, write_end = os.pipe()
    with (tmp_path / "apply.out").open("wb") as out, (tmp_path / "apply.err").open("wb") as err:
        process = subprocess.Popen(
            [str(cc / "chunkcast"), "apply", "--part", "3", *C.image_args(img), "--state", str(state),
             "--first", "0", "--count", str(img.n), "--sync-ms", "100", str(disk)],
            stdin=read_end, stdout=out, stderr=err, env=C.ENV)
    os.close(read_end)
    try:
        os.write(write_end, img.frame(0))                       # one frame, and then nothing
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not (state.exists() and 0 in C.state_of(state)[str(disk)]["durable"]):
            time.sleep(0.05)
        process.send_signal(signal.SIGTERM)
        assert process.wait(20) == 6, (tmp_path / "apply.err").read_text()
    finally:
        os.close(write_end)
        if process.poll() is None:
            process.kill()
    assert "pass stopped" in (tmp_path / "apply.out").read_text()
    assert C.state_of(state)[str(disk)]["durable"] == {0} and disk.read_bytes() == img.expected({0})


def test_apply_says_how_many_chunks_it_took_while_no_flush_has_come_back(cc, img, tmp_path):
    """#3149 (מעבדת ה-VM 14/11): `apply` לקח 2,569 חתיכות מהזרם בתוך 342 שניות, ובקובץ המצב שלו היו ארבע כתיבות בלבד --
    ה-flush של דיסק thin איטי מעכב את מה שעמיד דקות. הסוכן מדווח לשרת את מה שנלקח (`<state>.taken`), כראיית חיים. כאן ה-flush
    המחזורי רחוק (`--sync-ms` 600000): שתי חתיכות נלקחות, המספר שלהן בקובץ, ובקובץ המצב עוד אין דבר עמיד. בסוף -- כולן."""
    import subprocess
    disk, state = img.blank(tmp_path / "a.disk"), tmp_path / "state"
    taken = Path(f"{state}.taken")
    read_end, write_end = os.pipe()
    with (tmp_path / "apply.out").open("wb") as out, (tmp_path / "apply.err").open("wb") as err:
        process = subprocess.Popen(
            [str(cc / "chunkcast"), "apply", "--part", "3", *C.image_args(img), "--state", str(state),
             "--first", "0", "--count", str(img.n), "--sync-ms", "600000", str(disk)],
            stdin=read_end, stdout=out, stderr=err, env=C.ENV)
    os.close(read_end)
    try:
        os.write(write_end, img.frame(0) + img.frame(1))        # two frames, and then nothing: apply waits for the third
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not (taken.exists() and taken.read_text().strip() == "2"):
            time.sleep(0.05)
        assert taken.exists() and taken.read_text().strip() == "2", (tmp_path / "apply.err").read_text()
        assert not state.exists() or C.state_of(state)[str(disk)]["durable"] == set(), "a flush had come back: this is not the slow drive"
        for k in range(2, img.n):
            os.write(write_end, img.frame(k))
        assert process.wait(30) == 0, (tmp_path / "apply.err").read_text()
    finally:
        os.close(write_end)
        if process.poll() is None:
            process.kill()
    assert taken.read_text().strip() == str(img.n)
    assert C.state_of(state)[str(disk)]["durable"] == set(range(img.n)) and disk.read_bytes() == img.expected()


def test_chunkcast_says_its_version_which_is_how_the_server_knows_it_runs(cc):
    import subprocess
    done = subprocess.run([str(cc / "chunkcast"), "--version"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert (done.returncode, done.stdout) == (0, f"chunkcast {C.VERSION}\n")


def test_a_packet_of_another_protocol_version_is_not_taken_and_is_named(cc, img, tmp_path):
    """סקירה, ממצא 8: הבית שהיה `flags` הוא גרסת הפרוטוקול. חבילה של הסשן שלנו בגרסה אחרת (שרת
    ואימג' סוכן משתי גרסאות) אינה נכתבת ואינה נספרת כזרם; המקלט אומר זאת בשמו, והזרם התקין ממשיך."""
    port, disk, state, session = C.free_port(), img.blank(tmp_path / "a.disk"), tmp_path / "state", C.new_session()
    r = C.recv(cc, img, [disk], f"127.0.0.1:{port}", session, state, tag=tmp_path / "recv")
    newer = (C.PKT_HEAD.pack(C.MAGIC, C.END, C.VERSION + 1, 3, int(session, 16))
             + (1).to_bytes(4, "big") + (0).to_bytes(8, "big") + bytes(8))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        for _ in range(3):
            s.sendto(newer, ("127.0.0.1", port))              # an END of pass 1: would end the pass at once
    time.sleep(0.5)
    assert r.process.poll() is None, "a packet of another protocol version ended the pass\n" + r.log()
    s2 = C.send(cc, img, f"127.0.0.1:{port}", session, tag=tmp_path / "send")
    assert s2.wait() == 0, s2.log()
    assert r.wait() == 0, r.log()
    assert disk.read_bytes() == img.expected()
    assert f"protocol version {C.VERSION + 1} (this is {C.VERSION})" in r.err.read_text()

