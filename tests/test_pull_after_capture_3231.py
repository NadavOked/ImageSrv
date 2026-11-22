"""#3231: משיכה במנוע החתיכות של אימג' שהאינדקס שלו עוד לא נגזר.

המקביל של #3137 למשיכה. הסוכן מוריד את האינדקס (`GET …/index/<n>`) **אחרי** שטבלת המחיצות של היעד
נמחקה, ואימג' שנקלט זה עתה (או הועתק, יובא או שוחזר בלי ה-`.idx`) עוד אין לו כזה: 404, על דיסק ריק.
עכשיו `POST /agent/pulls` הוא השער: השרת גוזר את האינדקס מיד, ממתין לו, ו**אינו עונה "נפתח"** עד
שהוא מוכן. מה שנבדק, דרך השרת כולו:

- אינדקס שעוד נגזר: 503 `index_preparing` עם האחוזים, בלי משיכה ובלי שורה ב-`sessions`; הניסיון
  החוזר (כמו `curl --retry` של הסוכן) נפתח כשהגזירה מסתיימת, והאינדקס שהוא יורד הוא זה שנגזר.
- המתנה בתוך הבקשה: גזירה שמסתיימת בתוך `PULL_WAIT_S` נענית בבקשה אחת.
- גזירה שנכשלת: 409 `index_failed` עם הסיבה ("הדיסק לא נגע"), וניסיון חוזר אחרי התיקון גוזר מחדש.
- אין שינוי למי שאינו צריך את השער: אינדקס מוכן נפתח מיד ובלי גזירה (#3173: מנוע udpcast, שגם הוא
  לא נזקק לשער, נמחק).
- בלי הרשאה אין גזירה: בקשה שלא עברה כניסה אינה מפעילה עבודה על השרת.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

import chunk_image as C                                           # noqa: F401 -- the helpers' module
from conftest import MANIFEST_256
from server.rounds import indexprep
from test_server_room import CLONER1, room_server                 # noqa: F401 -- room_server is a fixture
from test_wave_after_capture_3137 import (                        # noqa: F401 -- derive is a fixture
    BLOB, IMAGE, STREAM, _events, _folder, _fresh_capture, _indexes, derive, wait_for)


def _pull(room_server, client="deploy", image=IMAGE):
    return room_server[client].post("/api/v1/agent/pulls", json={"mac": CLONER1, "image_id": image})


def _recapture(ctx):
    """A real recapture of the image: `capture.py` writes the manifest the agent uploaded, whole (it never carries
    `index_error`: only `record_index` writes that), and the files beside it. `_fresh_capture` re-signs the manifest it finds
    in the library, so the leftover of the damaged derivation is taken off first -- as the real manifest would not have it."""
    manifest_path = _folder(ctx) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for part in manifest["partitions"]:
        part.pop("index_error", None)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    _fresh_capture(ctx)


def _sessions(ctx) -> int:
    return ctx.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]


@pytest.fixture()
def chunk_engine(room_server, monkeypatch):
    """The chunk engine (#3173: the only one, nothing to turn on), and the pull waits 0.3 s at most: a derivation
    that is held answers "preparing"."""
    monkeypatch.setattr(indexprep, "PULL_WAIT_S", 0.3)
    return room_server


# --- the pull does not open before the index is ready ---------------------------------------------------


def test_a_pull_of_an_image_whose_index_is_not_derived_is_not_opened_and_says_how_far_it_is(chunk_engine, derive):
    ctx = chunk_engine["ctx"]
    _fresh_capture(ctx)
    derive.hold.clear()                                              # the derivation is under way, not done
    answer = _pull(chunk_engine)
    assert answer.status_code == 503, answer.text
    body = answer.json()
    assert body["code"] == "index_preparing" and body["ok"] is False, body
    assert body["error"].startswith("מכין את האימג' לשידור, ") and "נסו שוב" in body["error"], body
    assert answer.headers["Retry-After"] == "2"
    assert _sessions(ctx) == 0, "a pull was registered for an image whose index is still being derived"
    assert wait_for(lambda: len(derive.calls) >= 1), "the server did not start deriving the index"
    derive.hold.set()


def test_the_retry_opens_when_the_derivation_is_done_and_the_index_is_the_one_the_agent_downloads(chunk_engine, derive):
    """What `curl --retry` does: the same request again. Nothing is derived twice, and what the agent
    then downloads (`GET …/index/<n>`) is there -- the 404 that used to follow the erase."""
    ctx = chunk_engine["ctx"]
    _fresh_capture(ctx)
    derive.hold.clear()
    assert _pull(chunk_engine).status_code == 503
    derive.hold.set()
    assert wait_for(lambda: all(_indexes(ctx))), _indexes(ctx)
    answer = _pull(chunk_engine)
    assert answer.status_code == 200, answer.text
    assert answer.json()["image_id"] == IMAGE and _sessions(ctx) == 1
    for part in MANIFEST_256["partitions"]:
        if part.get("file"):
            served = chunk_engine["anon"].get(f"/api/v1/images/{IMAGE}/index/{part['index']}")
            assert served.status_code == 200, served.text
    assert len(derive.calls) == 2, derive.calls                      # the image's two partitions, once each
    assert len(_events(ctx, "room_index_wait")) == 1 and len(_events(ctx, "room_index_ready")) == 1


def test_a_derivation_that_ends_inside_the_wait_is_answered_by_the_same_request(room_server, derive, monkeypatch):
    """The server waits for the index before it answers: one request, no retry."""
    monkeypatch.setattr(indexprep, "PULL_WAIT_S", 20.0)
    ctx = room_server["ctx"]
    _fresh_capture(ctx)
    answer = _pull(room_server)
    assert answer.status_code == 200, answer.text
    assert all(_indexes(ctx)) and _sessions(ctx) == 1 and len(derive.calls) == 2, derive.calls


def test_two_pulls_of_the_same_image_derive_it_once(chunk_engine, derive):
    ctx = chunk_engine["ctx"]
    _fresh_capture(ctx)
    derive.hold.clear()
    assert _pull(chunk_engine).status_code == 503
    assert _pull(chunk_engine).status_code == 503
    assert len(_events(ctx, "room_index_wait")) == 1
    derive.hold.set()


# --- a derivation that fails fails the pull, by name, and the next try derives again --------------------


def test_a_derivation_that_fails_refuses_the_pull_with_its_reason_and_a_retry_derives_again(room_server, derive,
                                                                                         monkeypatch):
    """The file in the library is not what was captured: the pull is refused before it exists --
    nothing was written to any disk -- and says why. Once the file is right, the same request opens.

    The request that waits for the derivation gets the 409 of the gate. The derivation also writes what it found
    into the manifest (`index_error`), and from then on the image is refused by name before the gate is reached
    (`image_bound_to_another_machine`, "recapture") -- a refusal as good, and the second assertion below."""
    monkeypatch.setattr(indexprep, "PULL_WAIT_S", 20.0)          # one request waits the derivation out: no polling race
    chunk_engine = room_server
    ctx = chunk_engine["ctx"]
    damaged, _ = C.build(STREAM, damage=lambda k, chunk: chunk[:-1] + bytes([chunk[-1] ^ 1]) if k == 1 else chunk)
    _fresh_capture(ctx, damaged)
    answer = _pull(chunk_engine)
    assert answer.status_code == 409, answer.text
    body = answer.json()
    assert body["code"] == "index_failed" and body["error"].startswith("הכנת האימג' לשידור נכשלה"), body
    assert "is not the one the capture signed" in body["error"] and "המשיכה לא נפתחה והדיסק לא נגע" in body["error"]
    assert "הגל" not in body["error"], "the wording of a wave on a pull"
    assert _sessions(ctx) == 0
    assert any(body["error"] in d for d in _events(ctx, "pull_refused"))
    again = _pull(chunk_engine)               # the manifest now says the file is damaged: refused by name, before the gate
    assert again.status_code == 403 and "יש לקלוט את האימג' מחדש" in again.json()["error"], again.text
    assert _sessions(ctx) == 0

    assert any(part.get("index_error") for part in ctx.library.get(IMAGE)["partitions"]), "the derivation did not record what it found"
    _recapture(ctx)                                                  # a capture again: a manifest without it
    last = []
    assert wait_for(lambda: last.append(_pull(chunk_engine)) or last[-1].status_code == 200), last[-1].text
    assert _sessions(ctx) == 1 and all(_indexes(ctx))


def test_a_file_that_is_not_format_2_refuses_the_pull_by_name(room_server, derive, monkeypatch):
    monkeypatch.setattr(indexprep, "PULL_WAIT_S", 20.0)
    ctx = room_server["ctx"]
    _fresh_capture(ctx, b"not zstd frames at all")
    answer = _pull(room_server)
    assert answer.status_code == 409, answer.text
    assert answer.json()["code"] == "index_failed" and _sessions(ctx) == 0


def test_a_derivation_past_its_ceiling_refuses_the_pull_by_name(chunk_engine, derive, monkeypatch):
    monkeypatch.setattr(indexprep, "CEILING_BASE_S", 0.5)
    ctx = chunk_engine["ctx"]
    _fresh_capture(ctx)
    derive.hold.clear()                                              # it never ends by itself
    answer = None

    def refused():
        nonlocal answer
        answer = _pull(chunk_engine)
        return answer.status_code == 409
    assert wait_for(refused, 15), answer.text
    assert answer.json()["error"].startswith("הכנת האימג' לשידור לא הסתיימה תוך")
    assert "המשיכה לא נפתחה והדיסק לא נגע" in answer.json()["error"] and _sessions(ctx) == 0
    derive.hold.set()


# --- who does not need the gate ---------------------------------------------------------------------------


def test_an_image_whose_index_is_ready_opens_at_once_and_derives_nothing(chunk_engine, derive):
    ctx = chunk_engine["ctx"]
    _fresh_capture(ctx)
    assert wait_for(lambda: _pull(chunk_engine).status_code == 200)
    ctx.conn.execute("DELETE FROM sessions")
    ctx.conn.commit()
    derive.calls.clear()
    answer = _pull(chunk_engine)
    assert answer.status_code == 200 and derive.calls == [] and _sessions(ctx) == 1, answer.text


def test_a_pull_that_is_not_authorised_starts_no_derivation(chunk_engine, derive):
    """Nobody who is not let in makes the server read 40GB: the gate is after the login."""
    ctx = chunk_engine["ctx"]
    _fresh_capture(ctx)
    answer = _pull(chunk_engine, client="anon")
    assert answer.status_code in (401, 403), answer.text
    assert derive.calls == [] and _sessions(ctx) == 0
    assert _events(ctx, "room_index_wait") == []
