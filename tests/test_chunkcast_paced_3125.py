"""#3125 (V4): מי האט את השידור -- `--kbps` (הרשת) או `--write-kbps` (הכוננים).

`chunkcast send` אומר בשורה משלו, `paced <wire_ms> <disk_ms>`, כמה זמן ישן בגלל כל אחת משתי התקרות: אחרי כל `sent`,
ופעם אחת לפני `done` (`agent/chunkcast.c`, `cc_pace`). שורה משלה ולא שדות ב-`sent`/`done`, כי אלה נקראות שלמות
(`chunklog._PROGRESS`, `fullmatch`). בשרת השורה היא התקדמות ולא משפט של השולח (`chunklog.log_tail`).

נשלח לכתובת unicast שאיש אינו מאזין לה: השולח אינו מחכה לאיש, וזה מה שנמדד כאן -- רק התקרות שלו.

**בקרה שלילית:** `agent/chunkcast.c` ו-`server/rounds/chunklog.py` חוזרים לבסיס. אין שורת `paced` (הטסטים של השולח
נופלים על ה-assert של השורה), ו-`log_tail` מחזיר `paced …` במקום המשפט (הטסט של השרת נופל).
"""

from __future__ import annotations

import pytest

import chunkcast_rig as C
from native import requires_native
from server.rounds import chunklog

needs_native = requires_native("gcc", "zstd", posix=True, why="chunkcast needs gcc, libzstd and zstd")


@pytest.fixture(scope="module")
def cc(tmp_path_factory):
    return C.build_binaries(tmp_path_factory.mktemp("paced-bin"))


def _send(cc, tmp_path, *, noisy: tuple[int, ...], kbps: int, write_kbps: int) -> tuple[list[str], int, int]:
    img = C.build(tmp_path / "img", cc / "fanout", kind="raw", noisy=noisy, chunks=1)
    run = C.send(cc, img, f"127.0.0.1:{C.free_port()}", C.new_session(), tag=tmp_path / "send",
                 kbps=kbps, write_kbps=write_kbps)
    assert run.wait() == 0, run.log()
    out = run.out.read_text().splitlines()
    assert out[-1].startswith("done ") and out[-2].startswith("paced "), out
    _word, wire, disk = out[-2].split(" ")
    return out, int(wire), int(disk)


@needs_native
def test_a_sender_held_back_by_the_drives_says_so(cc, tmp_path):
    """4MiB of a compressible chunk -- a few KB on the wire -- at 10MB/s of writes: ~420ms waited for the drives."""
    out, wire, disk = _send(cc, tmp_path, noisy=(), kbps=10_000_000, write_kbps=80_000)
    assert disk >= 250 and wire <= 50, out


@needs_native
def test_a_sender_held_back_by_the_wire_says_so(cc, tmp_path):
    """4MiB that do not compress at 10MB/s on the wire, and no ceiling the drives impose: ~420ms waited for the wire."""
    out, wire, disk = _send(cc, tmp_path, noisy=(0,), kbps=80_000, write_kbps=100_000_000)
    assert wire >= 250 and disk <= 50, out


def test_the_paced_line_is_progress_and_not_what_the_sender_said(tmp_path):
    path = tmp_path / "send.log"
    path.write_text("sent 3 900\npaced 10 20\nchunkcast: send: the file ended inside chunk 4\n"
                    "sent 4 1200\npaced 12 25\n", encoding="utf-8")
    assert chunklog.log_tail(path) == "chunkcast: send: the file ended inside chunk 4"
    path.write_text("sent 4 1200\npaced 12 25\ndone 4 1200 3000\n", encoding="utf-8")
    assert chunklog.log_tail(path) == ""
