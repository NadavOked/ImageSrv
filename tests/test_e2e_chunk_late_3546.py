"""#3546: שלב 9ג של הסימולציה -- המגירה המאחרת מורידה ב-HTTP בדיוק את מה שהשרת ביקש ובודקת אותו מול המקור.

הסימולציה המלאה (`tools/e2e_simulation.py`) צריכה שרת, gcc ו-`chunkcast`; כאן הלוגיקה של המכונה המדומה בלי רשת:
טווח שירד שווה לטווח בקובץ המקור ← החתיכות נרשמות כעמידות; טווח שירד אחר ← הסימולציה נכשלת בשמו,
ולא ממשיכה כאילו המגירה הושלמה.

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

import pytest

from tools.e2e import phase_chunk

SOURCE = bytes(range(256)) * 4                       # 1024 בתים, כל בייט תלוי במקומו
INDEX = [(0, 400), (400, 400), (800, 224)]           # (היסט, אורך) של שלוש חתיכות


class FakeClient:
    """מחליף את `Client` של הסימולציה: עונה ל-Range מהמקור, או מחזיר בייטים שגויים."""

    wrong = False

    def __init__(self, base=None, console_base=None):
        self.base, self.console_base, self.headers = base, console_base, {}

    def request(self, method, path):
        first, last = self.headers["Range"].removeprefix("bytes=").split("-")
        body = SOURCE[int(first):int(last) + 1]
        return 206, (b"x" + body[1:] if FakeClient.wrong else body)


def machine(monkeypatch, wrong: bool) -> tuple[phase_chunk.ChunkMachine, list]:
    FakeClient.wrong = wrong
    monkeypatch.setattr(phase_chunk, "Client", FakeClient)
    m = phase_chunk.ChunkMachine("aa:bb:cc:00:00:32", [], missed={3: 1}, image_id="img", source={3: SOURCE},
                                 file_names={3: "p3.zst"})
    m.index[3], m.held[3] = INDEX, [[1, 2]]
    posted: list = []
    monkeypatch.setattr(m, "_post", lambda session, part, rnd, state, **more: posted.append((part, rnd, state, list(m.held[part]))))
    return m, posted


def test_the_range_the_server_asked_for_is_fetched_checked_and_then_held(monkeypatch):
    m, posted = machine(monkeypatch, wrong=False)
    m._fetch({"id": "s"}, 3, 2, [[0, 0]])
    assert m.fetched == {3: [("0-0", True)]}
    assert posted == [(3, 2, "fetching", [[0, 2]])], "the chunk is reported durable only after its bytes were checked"


def test_bytes_that_are_not_the_sources_fail_the_simulation_by_name(monkeypatch):
    m, posted = machine(monkeypatch, wrong=True)
    with pytest.raises(RuntimeError, match="הבייטים אינם של המקור"):
        m._fetch({"id": "s"}, 3, 2, [[0, 0]])
    assert posted == [] and m.held[3] == [[1, 2]], "a wrong range must not be reported as held"
