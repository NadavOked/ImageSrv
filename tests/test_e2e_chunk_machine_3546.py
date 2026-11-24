"""#3546: המכונה המדומה של שלב 9ג -- מה היא שואלת את השרת, מה היא מורידה, ומתי הסימולציה נכשלת בשמה.

`test_e2e_chunk_late_3546.py` בודק את `_fetch`. כאן שאר המכונה, בלי רשת ובלי שרת: `_post` (מה נשלח ב-`chunk-pass`),
`_chunk_wave` (האזנה, מעבר, `fetch`, `done`), `_wait_pass_sent`, `_index` ו-`_ledger`. השרת מדומה בסקריפט שעונה כמו
השרת האמיתי, והשעון מדומה, כך שתקרות של 90 ו-120 שניות נבדקות בלי להמתין.

מה שהסימולציה אומרת על עצמה הוא מה שנבדק כאן: תשובה בלתי צפויה, האזנה שלא נענתה, מעבר שלא שודר, וסבב שלא נסגר --
כולם **נכשלים בשם המכונה והמחיצה**, ואינם נתקעים או מדלגים. ו-`_ledger` מסרב ליומן שבו חתיכה אחת חסרה.

**בקרה שלילית:** ראו את גוף ה-PR -- מוטציות בקוד של השלב שמפילות את הטסטים על ההתנהגות.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from tools.e2e import harness, phase_chunk

MAC = "aa:bb:cc:00:00:32"
SOURCE = bytes(range(256)) * 4                        # 1024 בתים, כל בייט תלוי במקומו
INDEX3 = [(0, 400), (400, 400), (800, 224)]           # מחיצה 3: שלוש חתיכות
SOURCE1 = b"E" * 100
INDEX1 = [(0, 100)]                                   # מחיצה 1: חתיכה אחת
NAMES = {1: "p1.zst", 3: "p3.zst"}


def _index_text(rows):
    return ("# index v1 chunks=%d\n" % len(rows) + "\n".join(f"{off} {length} deadbeef" for off, length in rows) + "\n").encode()


class Clock:
    """שעון שמתקדם רק ב-`sleep`: תקרה של 120 שניות נחצית אחרי כמה שינות, בלי להמתין באמת."""

    def __init__(self):
        self.now, self.slept = 1000.0, []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class FakeServer:
    """מחליף את `Client` של המכונה: מגיש אינדקס וטווחי Range, ועונה ל-`chunk-pass` מסקריפט לכל מחיצה."""

    def __init__(self, script=None, indexes=None, chunk_status=200):
        self.base, self.console_base, self.headers = "http://sim", "http://sim-console", {}
        self.script = {part: list(answers) for part, answers in (script or {}).items()}
        self.indexes = {1: INDEX1, 3: INDEX3} if indexes is None else indexes
        self.chunk_status = chunk_status
        self.posts: list[dict] = []
        self.gets: list[str] = []

    def json(self, method, path, body=None):
        assert (method, path) == ("POST", "/api/v1/agent/chunk-pass"), (method, path)
        self.posts.append(body)
        if self.chunk_status != 200:
            return self.chunk_status, {"error": "no"}
        answers = self.script[body["part"]]
        if body["state"] == "listening":
            return 200, {"action": "receive"}
        if body["state"] == "fetching":
            return 200, {"action": "wait"}
        return 200, answers.pop(0) if len(answers) > 1 else answers[0]

    def request(self, method, path):
        self.gets.append(path)
        if "/index/" in path:
            part = int(path.rsplit("/", 1)[1])
            return (200, _index_text(self.indexes[part])) if part in self.indexes else (404, b"")
        first, last = self.headers["Range"].removeprefix("bytes=").split("-")
        source = SOURCE1 if NAMES[1] in path else SOURCE
        return 206, source[int(first):int(last) + 1]


@pytest.fixture()
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(phase_chunk, "time", fake)
    return fake


def _machine(monkeypatch, server, *, missed, pass_sent=lambda session, part: True):
    def make_client(base=None, console_base=None):
        http = FakeServer(indexes=server.indexes)
        http.base, http.console_base = base, console_base
        server.http = http
        return http
    monkeypatch.setattr(phase_chunk, "Client", make_client)
    m = phase_chunk.ChunkMachine(MAC, [{"dev": "sda", "serial": "S1"}, {"dev": "sdb", "serial": "S2"}], missed=missed,
                                 image_id="img", source={1: SOURCE1, 3: SOURCE}, file_names=NAMES)
    m.client = server
    m.pass_sent = pass_sent
    return m


# --- chunk-pass ---------------------------------------------------------------------------------------------------------


def test_listening_reports_no_ranges_and_ended_reports_what_is_held_for_every_disk(monkeypatch):
    server = FakeServer(script={3: [{"action": "done"}]})
    m = _machine(monkeypatch, server, missed={3: 1})
    m.held[3] = [[1, 2]]

    assert m._post({"id": "s1"}, 3, 1, "listening") == {"action": "receive"}
    m._post({"id": "s1"}, 3, 1, "ended")

    listening, ended = server.posts
    assert listening["mac"] == MAC and listening["session_id"] == "s1" and listening["part"] == 3 and listening["pass"] == 1
    assert [t["serial"] for t in listening["targets"]] == ["S1", "S2"]
    assert all(t["durable"] == [{"part": 3, "ranges": []}] for t in listening["targets"]), "האזנה: עוד לא עמיד דבר"
    assert all(t["durable"] == [{"part": 3, "ranges": [[1, 2]]}] for t in ended["targets"])
    assert ended["state"] == "ended" and ended["targets"][0]["failed"] is False


def test_a_chunk_pass_the_server_refused_fails_the_machine_by_name(monkeypatch):
    m = _machine(monkeypatch, FakeServer(chunk_status=409), missed={3: 1})
    m.held[3] = []
    with pytest.raises(RuntimeError, match=rf"{MAC}.*מחיצה 3.*ended.*409"):
        m._post({"id": "s1"}, 3, 1, "ended")


# --- הגל ---------------------------------------------------------------------------------------------------------------


def test_a_late_drawer_listens_gets_a_fetch_downloads_the_gap_and_is_then_done(monkeypatch, clock):
    server = FakeServer(script={1: [{"action": "done"}],
                                3: [{"action": "fetch", "pass": 2, "ranges": [[0, 0]], "poll": 8}, {"action": "done"}]})
    m = _machine(monkeypatch, server, missed={1: 0, 3: 1})

    m._chunk_wave({"id": "s1"}, [])

    assert m.index == {1: INDEX1, 3: INDEX3}
    assert m.actions == [(1, "done"), (3, "fetch"), (3, "done")]
    assert [(p["part"], p["pass"], p["state"]) for p in server.posts] == [
        (1, 1, "listening"), (1, 1, "ended"), (3, 1, "listening"), (3, 1, "ended"), (3, 2, "fetching"), (3, 2, "ended")]
    # מה שדווח כעמיד: מחיצה 1 כולה מההתחלה; מחיצה 3 -- רק ממה שפספסה ואילך, ורק אחרי ההורדה כולה
    assert server.posts[1]["targets"][0]["durable"] == [{"part": 1, "ranges": [[0, 0]]}]
    assert server.posts[3]["targets"][0]["durable"] == [{"part": 3, "ranges": [[1, 2]]}]
    assert server.posts[5]["targets"][0]["durable"] == [{"part": 3, "ranges": [[0, 2]]}]
    assert m.fetched == {3: [("0-0", True)]}
    assert clock.slept == [2.0], "ההשהיה אחרי fetch היא רבע מדגימת השרת (poll=8), ולא פחות מ-0.3"


def test_a_machine_that_missed_a_whole_part_holds_nothing_and_still_listens_first(monkeypatch, clock):
    server = FakeServer(script={3: [{"action": "done"}]})
    m = _machine(monkeypatch, server, missed={3: 3})          # פספסה את כל שלוש החתיכות

    m._chunk_wave({"id": "s1"}, [])

    assert m.held == {3: []}
    assert [p["state"] for p in server.posts] == ["listening", "ended"]
    assert all(t["durable"] == [{"part": 3, "ranges": []}] for t in server.posts[1]["targets"])


def test_a_wait_answer_sleeps_at_least_a_third_of_a_second_and_the_machine_keeps_asking(monkeypatch, clock):
    server = FakeServer(script={3: [{"action": "wait"}, {"action": "wait", "poll": 0}, {"action": "done"}]})
    m = _machine(monkeypatch, server, missed={3: 0})

    m._chunk_wave({"id": "s1"}, [])

    assert m.actions == [(3, "wait"), (3, "wait"), (3, "done")]
    assert clock.slept == [0.3, 0.3]


def test_a_listening_that_is_not_answered_with_receive_fails_by_name(monkeypatch, clock):
    server = FakeServer(script={3: [{"action": "done"}]})
    server.json = lambda method, path, body=None: (200, {"action": "wait"})
    m = _machine(monkeypatch, server, missed={3: 0})
    with pytest.raises(RuntimeError, match=rf"{MAC}.*האזנה למחיצה 3 נענתה"):
        m._chunk_wave({"id": "s1"}, [])


def test_an_answer_the_simulation_does_not_expect_fails_instead_of_looping(monkeypatch, clock):
    server = FakeServer(script={3: [{"action": "multicast"}]})
    m = _machine(monkeypatch, server, missed={3: 0})
    with pytest.raises(RuntimeError, match=rf"{MAC}.*מחיצה 3 נענתה.*multicast"):
        m._chunk_wave({"id": "s1"}, [])
    assert m.actions == [(3, "multicast")]


def test_a_part_that_never_closes_fails_after_two_minutes_with_the_last_answer(monkeypatch, clock):
    server = FakeServer(script={3: [{"action": "wait", "poll": 40}]})
    m = _machine(monkeypatch, server, missed={3: 0})
    with pytest.raises(RuntimeError, match=r"לא נסגרה תוך 120 שניות.*wait"):
        m._chunk_wave({"id": "s1"}, [])
    assert 12 <= len(clock.slept) <= 13 and set(clock.slept) == {10.0}, "ההמתנה נמדדה בשעון, לא בספירת סבבים"


def test_a_fetch_whose_bytes_are_not_the_sources_stops_the_wave_and_is_not_reported_held(monkeypatch, clock):
    server = FakeServer(script={3: [{"action": "fetch", "pass": 2, "ranges": [[0, 0]]}]})
    m = _machine(monkeypatch, server, missed={3: 1})
    monkeypatch.setattr(m, "source", {1: SOURCE1, 3: b"z" * 1024})
    with pytest.raises(RuntimeError, match="הבייטים אינם של המקור"):
        m._chunk_wave({"id": "s1"}, [])
    assert [p["state"] for p in server.posts] == ["listening", "ended"], "לא נשלח fetching על טווח שלא אומת"


# --- מעבר שודר, אינדקס ---------------------------------------------------------------------------------------------------


def test_the_machine_waits_for_the_server_to_send_the_pass_and_then_goes_on(monkeypatch, clock):
    asked = []

    def pass_sent(session, part):
        asked.append((session["id"], part))
        return len(asked) >= 4
    m = _machine(monkeypatch, FakeServer(), missed={3: 0}, pass_sent=pass_sent)

    m._wait_pass_sent({"id": "s1"}, 3)

    assert asked == [("s1", 3)] * 4 and clock.slept == [0.3, 0.3, 0.3]


def test_a_pass_the_server_never_sent_fails_after_ninety_seconds_by_name(monkeypatch, clock):
    m = _machine(monkeypatch, FakeServer(), missed={3: 0}, pass_sent=lambda session, part: False)
    with pytest.raises(RuntimeError, match=rf"{MAC}.*לא שידר את מחיצה 3 תוך 90 שניות"):
        m._wait_pass_sent({"id": "s1"}, 3)
    assert 89 <= sum(clock.slept) <= 91


def test_the_index_is_the_offset_and_length_of_every_chunk_after_the_header():
    server = FakeServer()
    assert phase_chunk._index(server, "img", 3) == INDEX3
    assert server.gets == ["/api/v1/images/img/index/3"]


def test_an_index_the_server_does_not_serve_fails_by_name_and_status():
    with pytest.raises(RuntimeError, match=r"מחיצה 7.*404"):
        phase_chunk._index(FakeServer(), "img", 7)


# --- יומן השרת ---------------------------------------------------------------------------------------------------------


def _ledger_ctx(tmp_path, rows):
    db_file = tmp_path / "imagesrv.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE chunk_ledger (serial TEXT, part INTEGER, ranges_json TEXT)")
    conn.executemany("INSERT INTO chunk_ledger VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return SimpleNamespace(data_dir=tmp_path)


def test_the_ledger_is_accepted_only_when_every_disk_holds_every_chunk_of_both_parts(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "passed", 0)
    ctx = _ledger_ctx(tmp_path, [("A", 1, "[[0, 0]]"), ("A", 3, "[[0, 2]]"), ("B", 1, "[[0,0]]"), ("B", 3, "[[0,2]]")])

    phase_chunk._ledger(ctx, {1: INDEX1, 3: INDEX3}, ["A", "B"])

    assert harness.passed == 2, "בדיקה אחת לכל מגירה, וכולן נספרו"


@pytest.mark.parametrize("rows", [
    [("A", 1, "[[0, 0]]"), ("A", 3, "[[0, 1]]")],         # חתיכה אחרונה של מחיצה 3 חסרה
    [("A", 1, "[[0, 0]]")],                               # מחיצה 3 לא נרשמה בכלל
    [("A", 3, "[[0, 2]]")],                               # מחיצה 1 לא נרשמה בכלל
    [("A", 1, "[[0, 0]]"), ("A", 3, "[[0, 0], [2, 2]]")],  # חור באמצע
    [],                                                   # המגירה לא ברישום
])
def test_a_ledger_with_a_missing_chunk_fails_the_simulation_by_serial(tmp_path, monkeypatch, rows):
    monkeypatch.setattr(harness, "passed", 0)
    ctx = _ledger_ctx(tmp_path, rows)
    with pytest.raises(SystemExit, match=r"FAILED: יומן השרת: A מחזיק"):
        phase_chunk._ledger(ctx, {1: INDEX1, 3: INDEX3}, ["A"])
    assert harness.passed == 0
