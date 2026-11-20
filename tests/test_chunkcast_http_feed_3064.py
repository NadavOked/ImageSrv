"""#3064 (B5): `chunkcast recv --feed FIFO --feed-chunks FILE` -- HTTP בזמן הזרם, לאותו דיסק, דרך הכותב היחיד שלו.

עד כאן ההחלטה (`server/brain/parallelhttp.py`, #3363) הייתה בצל, והביצוע היה רק `apply` (הורדה **במקום** זרם). כאן הביצוע ב-C:
תהליכון `cc_feeder` קורא פריימים מ-FIFO בסדר האינדקס, כמו הלולאה של `cc_apply` (`rb_read_all`, ואחר כך `cc_verify` -- אורך,
XXH64 ו-BLAKE3 -- ואז הזנה). הוא **אינו נוגע בדיסק**: הוא מכניס לתור של הכותב של כל מגירה שחסרה לה החתיכה, ורק כשהתור ריק
מחציו, כדי שלזרם תמיד יהיה מקום. חתיכה נתפסת (`inflight`) לפני שנכתבת, ולכן הזרם וה-HTTP אינם כותבים אותה פעמיים.

**שער אחד לכולם:** חתיכה מזויפת ב-HTTP (פריים תקין באורך הנכון, תוכן אחר -- או **אותו** XXH64 ותוכן אחר, ש-BLAKE3 בלבד עוצר)
נדחית: לא בית ממנה בדיסק, ההזנה נגמרת (`bad-chunk`; מה שאחריה אינו נאמן), והזרם ממשיך ומביא את האמיתית.

**מתי נקראת הרשימה:** כשהבית הראשון מגיע ל-FIFO. ההחלטה של B5 באה **אחרי** ש-`recv` אמר `listening` (השרת מקבל קודם את `listening`),
ולכן ה-FIFO נפתח בהתחלה והרשימה נקראת רק כשה-curl מתחיל; הסוכן כותב אותה שלמה לפני כן.

**בקרה שלילית:** `agent/chunkcast.c` חוזר לבסיס: `--feed` אינו אופציה ("bad option", rc 2) -- כל טסט כאן נופל על קוד היציאה, על
קובץ היעד או על שורת `feed`.
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import pytest

import chunkcast_rig as C
import test_chunk_b3_3332 as B3
from native import requires_native
from test_chunkcast_2533 import cc, img   # noqa: F401 -- fixtures: the real chunkcast and its image (partclone, 4.5 chunks)

pytestmark = requires_native("gcc", "zstd", posix=True, why="chunkcast צריך gcc, libzstd ו-zstd")


def _fifo(tmp: Path) -> Path:
    import os

    path = tmp / "http.fifo"
    os.mkfifo(path)
    return path


def _list(tmp: Path, text: str, name: str = "feed.chunks") -> Path:
    path = tmp / name
    path.write_text(text + "\n", encoding="ascii")
    return path


def _feed(fifo: Path, img: C.Image, ks, frames: dict[int, bytes] | None = None) -> threading.Thread:
    """What the agent's curl does: the frames of `ks` into the FIFO, in ascending order. The open waits for a reader, and
    chunkcast opens the FIFO (read-write) before it says `listening`."""
    def run():
        with open(fifo, "wb") as f:
            for k in ks:
                f.write((frames or {}).get(k) or img.frame(k))
                f.flush()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _until(what: str, condition, seconds: float = 30.0) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            if condition():
                return
        except (FileNotFoundError, AssertionError, ValueError):
            pass                                     # the state file is written whole, by a rename; not there yet
        time.sleep(0.05)
    raise AssertionError(f"{what}: not within {seconds}s")


def _recv(cc, img, targets, tmp, state, session, port, fifo, listed, *, extra=(), start_s=30, stall_s=5):
    return C.recv(cc, img, targets, f"127.0.0.1:{port}", session, state, tag=tmp / "recv", start_s=start_s, stall_s=stall_s,
                  extra=("--feed", str(fifo), "--feed-chunks", str(listed), *extra))


def _says(run: C.Run) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in run.out.read_text().splitlines():
        word, *rest = line.split()
        out.setdefault(word, rest)
    return out


def _rx(run: C.Run) -> dict[str, int]:
    return {k: int(v) for k, v in (p.split("=") for p in _says(run)["rxstats"] if p.split("=")[1].isdigit())}


def test_http_beside_the_stream_writes_each_chunk_once_on_every_drawer(cc, img, tmp_path):
    """HTTP מביא 2-4 ל-**שתי** מגירות; רק אחרי שהן עמידות על שתיהן המשדר שולח את כל 0-4. חתיכות 2-4 מהזרם כבר מוחזקות
    ולכן אינן נכתבות שוב: מה שעבר אימות ונכתב דרך הזרם (`good`) ודרך ה-HTTP (`taken`) הוא בדיוק מספר החתיכות, כל אחת פעם
    אחת, ושתי המגירות שוות לאימג'."""
    a, b = img.blank(tmp_path / "a.disk"), img.blank(tmp_path / "b.disk")
    fifo, listed, state = _fifo(tmp_path), _list(tmp_path, "2-4"), tmp_path / "state"
    session, port = C.new_session(), C.free_port()
    r = _recv(cc, img, [a, b], tmp_path, state, session, port, fifo, listed)
    _feed(fifo, img, [2, 3, 4])
    _until("chunks 2-4 durable on both drawers",
           lambda: all(C.state_of(state)[str(t)]["durable"] >= {2, 3, 4} for t in (a, b)))
    s = C.send(cc, img, f"127.0.0.1:{port}", session, tag=tmp_path / "send")
    assert r.wait() == 0, r.log()
    s.wait()
    out = r.out.read_text().splitlines()
    assert out[-1] == "pass complete", out
    assert _says(r)["feed"] == ["ended", "3", "0", "0"], out
    assert _rx(r)["good"] == 2 and _rx(r)["good"] + 3 == img.n, "0-1 from the stream, 2-4 over HTTP: each chunk once"
    assert a.read_bytes() == b.read_bytes() == img.expected()


@pytest.mark.parametrize("how", ["xxh64", "blake3"])
def test_a_forged_chunk_over_http_is_refused_and_the_stream_brings_the_real_one(cc, img, tmp_path, how):
    """אותו שער כמו בזרם. חתיכה 2 ב-HTTP מזויפת: פריים תקין באורך הנכון עם תוכן אחר (`xxh64`: ה-XXH64 שלו שונה), או **עם אותו
    XXH64** (`blake3`: רק רשימת ה-BLAKE3 עוצרת). לא בית ממנה בדיסק, ההזנה נגמרת בלי שהכניסה ולו חתיכה (`bad-chunk`; ההיא היא
    הראשונה ברשימה, ומה שאחריה אינו נאמן), והזרם מביא את האמיתית: המגירה שווה לאימג'."""
    a = img.blank(tmp_path / "a.disk")
    if how == "xxh64":
        other = C.build(tmp_path / "other", cc / "fanout", noisy=(0, 2), salt=99)
        assert other.zlens[2] == img.zlens[2] and other.frame(2) != img.frame(2)
        forged, extra, said = other.frame(2), (), "wrong XXH64"
    else:
        forged, extra, said = B3.forged_chunk(cc, img, 2), B3.b3_args(cc, img, tmp_path), "wrong BLAKE3"
    fifo, listed, state = _fifo(tmp_path), _list(tmp_path, "2-4"), tmp_path / "state"
    session, port = C.new_session(), C.free_port()
    r = _recv(cc, img, [a], tmp_path, state, session, port, fifo, listed, extra=extra)
    _feed(fifo, img, [2, 3, 4], {2: forged})
    _until("the forged chunk refused", lambda: f"chunk 2 does not match the index ({said}) -- not written" in r.err.read_text())
    assert a.read_bytes() == img.expected(set()), "not a byte of the forged chunk on the drawer"
    s = C.send(cc, img, f"127.0.0.1:{port}", session, tag=tmp_path / "send")
    assert r.wait() == 0, r.log()
    s.wait()
    assert _says(r)["feed"] == ["bad-chunk", "0", "0", "1"], r.out.read_text()
    assert r.out.read_text().splitlines()[-1] == "pass complete"
    assert a.read_bytes() == img.expected(), "the stream brought the real chunk, and the rest"


def test_http_alone_completes_the_pass_and_says_what_it_fed(cc, img, tmp_path):
    """בלי זרם בכלל: ההזנה לבדה כותבת את כל המחיצה, והמעבר נגמר `complete` -- כותב יחיד לכל מגירה, ואימות מלא."""
    a, b = img.blank(tmp_path / "a.disk"), img.blank(tmp_path / "b.disk")
    fifo, listed, state = _fifo(tmp_path), _list(tmp_path, f"0-{img.n - 1}"), tmp_path / "state"
    r = _recv(cc, img, [a, b], tmp_path, state, C.new_session(), C.free_port(), fifo, listed)
    _feed(fifo, img, range(img.n))
    assert r.wait() == 0, r.log()
    assert r.out.read_text().splitlines()[-1] == "pass complete", r.log()
    assert _says(r)["feed"] == ["ended", str(img.n), "0", "0"], r.out.read_text()
    assert a.read_bytes() == b.read_bytes() == img.expected()
    assert C.state_of(state)[str(a)]["durable"] == set(range(img.n)), "durable, after the flush"


def test_the_bytes_of_a_chunk_that_is_not_needed_are_read_all_the_same(cc, img, tmp_path):
    """0-1 כבר עמידות (מעבר קודם, בקובץ המצב). ב-HTTP הפריימים 0-4 כולם: ההזנה קוראת את הבתים של 0 ו-1 ומדלגת עליהן
    (`skipped`), ולכן 2-4 מתיישרות ועוברות אימות. אילו הבתים של המדולגות לא נקראו, 2 הייתה נבדקת על ההתחלה של 0 ונדחית."""
    a = img.blank(tmp_path / "a.disk")
    state = tmp_path / "state"
    first = C.apply(cc, img, [a], state, 0, 2, img.frame(0) + img.frame(1), tag=tmp_path / "apply")
    assert first.process.returncode == 0, first.log()
    fifo, listed = _fifo(tmp_path), _list(tmp_path, f"0-{img.n - 1}")
    r = _recv(cc, img, [a], tmp_path, state, C.new_session(), C.free_port(), fifo, listed)
    _feed(fifo, img, range(img.n))
    assert r.wait() == 0, r.log()
    assert _says(r)["feed"] == ["ended", "3", "2", "0"], r.out.read_text()
    assert a.read_bytes() == img.expected()


def _trickle(fifo: Path, img: C.Image, k: int, release: threading.Event) -> threading.Thread:
    """Begins frame k and goes silent: the first bytes of it, then nothing until `release`."""
    def run():
        with open(fifo, "wb") as f:
            f.write(img.frame(k)[:1000])
            f.flush()
            release.wait(60)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_a_feed_that_never_begins_is_idle_and_holds_nothing(cc, img, tmp_path):
    """ה-FIFO פתוח ואף בית לא הגיע (ה-curl לא התחיל: ההחלטה לא באה, או שהמתג כבוי): אין זמן שרץ, ההזנה `idle`, והמעבר נגמר
    `no-stream` לפי `--start-s`, בקוד היציאה של היום (rc=3)."""
    a = img.blank(tmp_path / "a.disk")
    fifo, listed = _fifo(tmp_path), _list(tmp_path, "2-4")
    r = _recv(cc, img, [a], tmp_path, tmp_path / "state", C.new_session(), C.free_port(), fifo, listed, start_s=4, stall_s=2)
    assert r.wait() == 3, r.log()
    assert _says(r)["feed"] == ["idle", "0", "0", "0"], r.out.read_text()
    assert a.read_bytes() == img.expected(set())


def test_a_feed_that_goes_silent_once_begun_ends_by_the_stall_ceiling_and_never_holds_the_pass(cc, img, tmp_path):
    """הבית הראשון הגיע ואז ה-curl נפל: ההזנה נגמרת `stalled` אחרי `--stall-s` (כאן 2), והמעבר ממשיך לתקרות שלו."""
    a = img.blank(tmp_path / "a.disk")
    fifo, listed, release = _fifo(tmp_path), _list(tmp_path, "2-4"), threading.Event()
    r = _recv(cc, img, [a], tmp_path, tmp_path / "state", C.new_session(), C.free_port(), fifo, listed, start_s=6, stall_s=2)
    _trickle(fifo, img, 2, release)
    try:
        assert r.wait() == 3, r.log()
    finally:
        release.set()
    assert _says(r)["feed"] == ["stalled", "0", "0", "0"], r.out.read_text()
    assert a.read_bytes() == img.expected(set())


def test_a_list_that_is_not_a_list_ends_the_feed_and_not_the_pass(cc, img, tmp_path):
    """ה-FIFO והרשימה נקראים כשהבית הראשון מגיע (ההחלטה באה אחרי `listening`). רשימה פגומה = `bad-list`: שום חתיכה לא נבדקת ולא
    נכתבת מ-HTTP, והזרם ממשיך ומשלים לבדו."""
    a = img.blank(tmp_path / "a.disk")
    fifo, listed, release = _fifo(tmp_path), _list(tmp_path, "x-y"), threading.Event()
    state, session, port = tmp_path / "state", C.new_session(), C.free_port()
    r = _recv(cc, img, [a], tmp_path, state, session, port, fifo, listed)
    _trickle(fifo, img, 2, release)
    _until("the feed refused its list", lambda: "is not a list of chunk ranges -- the HTTP feed ends" in r.err.read_text())
    s = C.send(cc, img, f"127.0.0.1:{port}", session, tag=tmp_path / "send")
    try:
        assert r.wait() == 0, r.log()
    finally:
        release.set()
    s.wait()
    assert _says(r)["feed"] == ["bad-list", "0", "0", "0"], r.out.read_text()
    assert a.read_bytes() == img.expected()


@pytest.mark.parametrize("which", ["feed-alone", "list-alone", "not-a-fifo"])
def test_the_options_go_together_and_the_feed_must_be_a_fifo_or_nothing_starts(cc, img, tmp_path, which):
    a = img.blank(tmp_path / "a.disk")
    plain = tmp_path / "plain"
    plain.write_text("x")
    fifo, listed = _fifo(tmp_path), _list(tmp_path, "2-4")
    feed = {"feed-alone": ["--feed", str(fifo)], "list-alone": ["--feed-chunks", str(listed)],
            "not-a-fifo": ["--feed", str(plain), "--feed-chunks", str(listed)]}[which]
    done = subprocess.run([str(cc / "chunkcast"), "recv", "--listen", f"127.0.0.1:{C.free_port()}", "--session", C.new_session(),
                           "--part", "3", "--pass", "1", *C.image_args(img), "--state", str(tmp_path / "state"), *feed, str(a)],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL, env=C.ENV, timeout=30)
    assert done.returncode == 2 and "listening" not in done.stdout, (done.stdout, done.stderr)
