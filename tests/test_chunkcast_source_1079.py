"""הכרעת נדב 14/11 (#1079 F6, #2485): `chunkcast recv` מקבל חבילות רק מכתובת המקור
של השרת (`--from`, מהוראת הגל), ו-`chunkcast send` נקשר אליה.

חבילה ממקור אחר נספרת, נאמרת פעם אחת בשמה, ונזרקת לפני שהכותרת שלה נקראת: היא אינה
פותחת זרם, אינה מאפסת את שעון השתיקה, וה-ANNOUNCE שלה אינו סותר דבר — #2485 מצא
ש-ANNOUNCE סותר אחד לפני החתיכה הראשונה מסיים את המעבר ב-`another-index`
(`chunkrecv.sh` מכשיל את המגירה). כתובת מקור אינה חתימה: זיוף שלה נעצר בוילן ובסוויץ'.

על loopback: 127.0.0.1 הוא "השרת", ו-127.0.0.2 (גם הוא של lo בלינוקס) הוא המחשב הזר.
"""

from __future__ import annotations

import re
import socket

import pytest

import chunkcast_rig as C
from native import requires_native

pytestmark = requires_native("gcc", "zstd", posix=True, why="chunkcast צריך gcc, libzstd ו-zstd")

SERVER, STRANGER = "127.0.0.1", "127.0.0.2"


@pytest.fixture(scope="module")
def cc(tmp_path_factory):
    return C.build_binaries(tmp_path_factory.mktemp("chunkcast-bin"))


@pytest.fixture(scope="module")
def img(cc, tmp_path_factory):
    return C.build(tmp_path_factory.mktemp("img"), cc / "fanout", noisy=(0, 2))


def _stray(err: str) -> int:
    found = re.findall(r"(\d+) from another source", err)
    assert found, err
    return int(found[-1])


def test_the_servers_own_stream_is_taken_whole(cc, img, tmp_path):
    port, disk, state, session = C.free_port(), img.blank(tmp_path / "a.disk"), tmp_path / "state", C.new_session()
    r = C.recv(cc, img, [disk], f"{SERVER}:{port}", session, state, tag=tmp_path / "recv", extra=("--from", SERVER))
    s = C.send(cc, img, f"{SERVER}:{port}", session, tag=tmp_path / "send", extra=("--from", SERVER))
    assert s.wait() == 0, s.log()
    assert r.wait() == 0, r.log()
    err = r.err.read_text()
    assert "pass complete" in r.out.read_text() and f"only from {SERVER}" in err, r.log()
    assert _stray(err) == 0
    assert disk.read_bytes() == img.expected()


def test_a_stream_from_another_source_writes_nothing_starts_nothing_and_is_named_once(cc, img, tmp_path):
    """אותו סשן, אותו אימג', אותו פורט — ומקור אחר. no-stream: שום חבילה שלו לא נחשבה."""
    port, disk, state, session = C.free_port(), img.blank(tmp_path / "a.disk"), tmp_path / "state", C.new_session()
    r = C.recv(cc, img, [disk], f"{SERVER}:{port}", session, state, tag=tmp_path / "recv", start_s=3,
               extra=("--from", SERVER))
    s = C.send(cc, img, f"{SERVER}:{port}", session, tag=tmp_path / "send", extra=("--from", STRANGER))
    assert s.wait() == 0, s.log()
    assert r.wait() == 3, r.log()
    err = r.err.read_text()
    assert "pass no-stream" in r.out.read_text(), r.log()
    assert err.count(f"a packet from {STRANGER}, not the server's {SERVER} -- dropped") == 1, err
    assert _stray(err) > 100                  # כל החבילות נספרו — לא רק הראשונה
    assert disk.read_bytes() == img.expected(set())
    assert C.state_of(state)[str(disk)]["durable"] == set()


def test_a_forged_announce_from_another_source_is_not_even_read(cc, img, tmp_path):
    """#2485: הכרזה סותרת עם הסשן הנכון, ממחשב זר, לפני הזרם האמיתי — אינה נקראת כלל."""
    port, disk, state, session = C.free_port(), img.blank(tmp_path / "a.disk"), tmp_path / "state", C.new_session()
    r = C.recv(cc, img, [disk], f"{SERVER}:{port}", session, state, tag=tmp_path / "recv", extra=("--from", SERVER))
    forged = (C.PKT_HEAD.pack(C.MAGIC, C.ANNOUNCE, C.VERSION, 3, int(session, 16))
              + (1).to_bytes(4, "big") + (img.n + 7).to_bytes(8, "big") + bytes(8))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((STRANGER, 0))
        sock.sendto(forged, (SERVER, port))
    s = C.send(cc, img, f"{SERVER}:{port}", session, tag=tmp_path / "send", extra=("--from", SERVER))
    assert s.wait() == 0, s.log()
    assert r.wait() == 0, r.log()
    err = r.err.read_text()
    assert "names another index" not in err and _stray(err) == 1, err
    assert disk.read_bytes() == img.expected()


def test_a_from_that_is_not_an_address_is_refused_before_anything(cc, img, tmp_path):
    disk, state = img.blank(tmp_path / "a.disk"), tmp_path / "state"
    run = C._spawn([str(cc / "chunkcast"), "recv", "--listen", f"{SERVER}:{C.free_port()}", "--session",
                    C.new_session(), "--part", "3", "--pass", "1", *C.image_args(img), "--state", str(state),
                    "--from", "server.example", str(disk)], tmp_path / "recv")
    assert run.wait() == 2 and "--from 'server.example' is not an IPv4 address" in run.err.read_text()
    assert disk.read_bytes() == img.expected(set())


@pytest.mark.parametrize("source,said", [("server.example", "is not an IPv4 address"),
                                          ("192.0.2.1", "cannot send from 192.0.2.1")])
def test_a_sender_that_cannot_send_from_the_named_source_does_not_send(cc, img, tmp_path, source, said):
    """192.0.2.1 (TEST-NET-1) אינה כתובת של המארח: bind נכשל, ושום חבילה לא יוצאת ממקור אחר."""
    s = C.send(cc, img, f"{SERVER}:{C.free_port()}", C.new_session(), tag=tmp_path / "send", extra=("--from", source))
    assert s.wait() == 2 and said in s.err.read_text(), s.log()
