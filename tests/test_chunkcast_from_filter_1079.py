"""#1079 / #2485: המקלט של מנוע החתיכות (`chunkcast recv`) מקבל זרם רק מהשרת של הסבב (`--from`).

הכרעת נדב 14/11: בלי מפתח לכל סבב. מי שעל וילן ההפצה ושולח לקבוצה המולטיקאסט לא צריך להתחזות לשרת
כדי להפיל סבב -- מספיק ש-END מזויף, או ANNOUNCE של אינדקס אחר, יגיעו למקלט. לכן המקלט מסנן לפי כתובת המקור,
לפני שהוא קורא את הכותרת של החבילה: מקור זר לא מתחיל זרם, לא מאפס את שעון ההמתנה, ולא סותר דבר.

המקלט והשולח הם התהליכים האמיתיים. "השרת" הוא הממסר של `chunkcast_rig`, שיוצא מ-127.0.0.2 או מ-127.0.0.1 לפי
הבדיקה, והתוקף הוא שקע שנקשר ל-127.0.0.2 ושולח חבילות עם הסשן הנכון (כל loopback של לינוקס הוא כתובת מקומית).

חבילה זרה אינה נכתבת **בשום מקרה**: גם בלי `--from` חתיכה נכנסת לדיסק רק אחרי XXH64 מול האינדקס. המסנן הוא על
זמינות -- שהסבב לא ייפול בגלל חבילה אחת -- ולכן הבדיקות כאן הן על מה שקורה לסבב, לא רק על הדיסק.
"""

from __future__ import annotations

import socket
import struct
import time
from pathlib import Path

import pytest

import chunkcast_rig as C
from native import requires_native

pytestmark = requires_native("gcc", "zstd", posix=True, why="chunkcast צריך gcc, libzstd ו-zstd")

SERVER, ATTACKER = "127.0.0.1", "127.0.0.2"
PART = 3


@pytest.fixture(scope="module")
def cc(tmp_path_factory):
    return C.build_binaries(tmp_path_factory.mktemp("chunkcast-bin"))


@pytest.fixture(scope="module")
def img(cc, tmp_path_factory):
    """partclone, 4.5 חתיכות; רק אחת אינה נדחסת, כדי שהמעבר יהיה של שנייה ולא של חמש."""
    return C.build(tmp_path_factory.mktemp("img"), cc / "fanout", noisy=(0,))


def _stray(run) -> list[int]:
    """השורות `stray <N>` שהמקלט אמר ב-stdout (הסוכן מעביר את האחרונה אל השרת)."""
    return [int(line.split()[1]) for line in run.out.read_text().splitlines() if line.startswith("stray ")]


def _attacker(port: int, packets: list[bytes]) -> None:
    """חבילות מכתובת 127.0.0.2 אל המקלט שמקשיב ב-127.0.0.1:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind((ATTACKER, 0))
        for packet in packets:
            s.sendto(packet, (SERVER, port))


def _head(kind: int, session: str) -> bytes:
    return C.PKT_HEAD.pack(C.MAGIC, kind, C.VERSION, PART, int(session, 16))


def _forged_end(session: str, pass_: int = 1) -> bytes:
    """END עם הסשן והמעבר הנכונים: מה שהמקלט מבין כ"המעבר נגמר"."""
    return _head(C.END, session) + struct.pack(">IQ", pass_, 0)


def _forged_announce(session: str, img: C.Image, pass_: int = 1) -> bytes:
    """ANNOUNCE שאומר שהזרם הזה הוא של אינדקס אחר (n+1 חתיכות)."""
    return _head(C.ANNOUNCE, session) + struct.pack(">IQQ", pass_, img.n + 1, 0xDEADBEEF)


def _pass(cc, img, tmp_path, *, relay_source, recv_from):
    """מעבר אחד דרך ממסר שיוצא מ-`relay_source`; המקלט מקבל `--from recv_from` (או כלום). מחזיר (מקלט, דיסק, מצב)."""
    relay = C.Relay(1, source=relay_source)
    disk, state, session = img.blank(tmp_path / "a.disk"), tmp_path / "state", C.new_session()
    extra = ("--from", recv_from) if recv_from else ()
    try:
        r = C.recv(cc, img, [disk], relay.listen(0), session, state, tag=tmp_path / "recv", start_s=4, extra=extra)
        s = C.send(cc, img, relay.addr, session, tag=tmp_path / "send")
        assert s.wait() == 0, s.log()
        rc = r.wait()
    finally:
        relay.close()
    return r, rc, disk, state


def test_a_stream_from_another_source_is_never_written_and_is_counted(cc, img, tmp_path):
    """הסבב של השרת (127.0.0.1) נשמע מול זרם שלם מ-127.0.0.2. המקלט לא התחיל זרם ("no-stream", rc 3), הדיסק כפי
    שהיה, והמספר שנזרק נאמר ב-stdout ולא רק ביומן."""
    r, rc, disk, state = _pass(cc, img, tmp_path, relay_source=ATTACKER, recv_from=SERVER)
    assert rc == 3, r.log()
    assert disk.read_bytes() == img.blank(tmp_path / "untouched.disk").read_bytes()
    assert C.state_of(state)[str(disk)]["durable"] == set()
    (said,) = _stray(r)[-1:]
    assert said > 0, r.log()
    log = r.log()
    assert f"not the server's {SERVER}" in log and "pass 1 no-stream: 0 packets" in log, log
    assert f"{said} from another source" in log, log


def test_the_stream_from_the_server_is_written_and_nothing_stray_is_counted(cc, img, tmp_path):
    r, rc, disk, state = _pass(cc, img, tmp_path, relay_source=ATTACKER, recv_from=ATTACKER)
    assert rc == 0, r.log()
    assert disk.read_bytes() == img.expected()
    assert C.state_of(state)[str(disk)]["durable"] == set(range(img.n))
    assert _stray(r) == [0, 0], r.out.read_text()           # said after "listening" and at the end: 0 is evidence
    assert f"only from {ATTACKER}" in r.log()


def test_without_from_every_source_is_taken_as_before_and_no_count_is_said(cc, img, tmp_path):
    """סוכן ישן מול שרת חדש, או שרת בלי כתובת: ההתנהגות של היום. אין שורת `stray` -- "לא נקרא" אינו 0."""
    r, rc, disk, _state = _pass(cc, img, tmp_path, relay_source=ATTACKER, recv_from=None)
    assert rc == 0, r.log()
    assert disk.read_bytes() == img.expected()
    assert _stray(r) == [] and "only from" not in r.log(), r.log()


def test_forged_end_and_announce_from_another_source_do_not_touch_the_pass(cc, img, tmp_path):
    """השורש של #2485: תוקף שלא התחזה לשרת, רק שלח לקבוצה. END מזויף היה מסיים את המעבר לפני שהזרם התחיל,
    ו-ANNOUNCE של אינדקס אחר היה מכשיל אותו כ"הזרם אינו של האימג'". עכשיו המקלט עומד, ומקבל את הזרם של השרת."""
    relay = C.Relay(1, source=SERVER)
    disk, state, session = img.blank(tmp_path / "a.disk"), tmp_path / "state", C.new_session()
    try:
        r = C.recv(cc, img, [disk], relay.listen(0), session, state, tag=tmp_path / "recv", start_s=20,
                   extra=("--from", SERVER))
        _attacker(relay.ports[0], [_forged_end(session)] * 5 + [_forged_announce(session, img)] * 3)
        end = time.monotonic() + 1.0                  # a receiver that took it would have ended long before
        while time.monotonic() < end:
            assert r.process.poll() is None, f"the forged packets ended the pass\n{r.log()}"
            time.sleep(0.05)
        s = C.send(cc, img, relay.addr, session, tag=tmp_path / "send")
        assert s.wait() == 0, s.log()
        assert r.wait() == 0, r.log()
    finally:
        relay.close()
    assert disk.read_bytes() == img.expected()
    assert _stray(r)[-1] == 8, r.out.read_text()      # every one of them, counted
    assert "names another index" not in r.log(), r.log()


def test_the_same_forged_end_does_end_a_pass_that_has_no_from(cc, img, tmp_path):
    """הבקרה של הבדיקה שלמעלה: התוקף שלי אמיתי. בלי `--from` אותו END מסיים את המעבר בלי שנכתב בית (rc 0, "ended")."""
    relay = C.Relay(1, source=SERVER)
    disk, state, session = img.blank(tmp_path / "a.disk"), tmp_path / "state", C.new_session()
    try:
        r = C.recv(cc, img, [disk], relay.listen(0), session, state, tag=tmp_path / "recv", start_s=20)
        _attacker(relay.ports[0], [_forged_end(session)])
        assert r.wait() == 0, r.log()
    finally:
        relay.close()
    assert disk.read_bytes() == img.blank(tmp_path / "untouched.disk").read_bytes()
    assert C.state_of(state)[str(disk)]["durable"] == set()


@pytest.mark.parametrize("bad", ["not-an-address", "1.2.3", "::1", "239.1.1.1.1"])
def test_a_from_that_is_not_an_ipv4_address_is_refused_by_name(cc, img, tmp_path, bad):
    run = C._spawn([str(cc / "chunkcast"), "recv", "--listen", "127.0.0.1:9", "--session", C.new_session(),
                    "--part", str(PART), "--pass", "1", *C.image_args(img), "--state", str(tmp_path / "state"),
                    "--from", bad, str(img.blank(tmp_path / "a.disk"))], tmp_path / "recv")
    assert run.wait() == 2 and f"--from '{bad}' is not an IPv4 address" in run.log(), run.log()
    assert not Path(tmp_path / "state").exists()          # nothing was opened, let alone written
