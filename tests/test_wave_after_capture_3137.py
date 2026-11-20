"""#3137: גל של מנוע החתיכות שנפתח מיד אחרי קליטה, לפני שהאינדקס נגזר.

עד התיקון הגל יצא לדרך, והמנוע הכשיל אותו אחרי 13 שניות ("אין אינדקס מאומת ... עדיין נבנה
אחרי הקליטה"), בזמן שהגזירה הייתה מסתיימת 30 שניות אחר כך (מעבדת ה-VM, 14/11). זה המסלול
הרגיל במכללה: קולטים ומיד מפיצים.

מה שנבדק כאן, דרך השרת כולו (פתיחת סבב בקונסולה, hello של משכפל, מסך החדר):

- הגל נשאר פתוח, השרת גוזר את האינדקס מיד, והגל יוצא כשהוא מוכן -- והמנוע מקבל אותו.
- גזירה שנכשלת מכשילה את הסבב בשמה, עם הסיבה, בלי שהגל יצא ובלי שולח.
- גזירה איטית: מסך החדר אומר "מכין את האימג' לשידור, X%", ו"התחל גל" מסורב באותו נוסח.
- גזירה שלא הסתיימה בתקרה מכשילה את הסבב בשמה.
- מעבר ברקע שכבר גוזר את האימג' אינו נפרס פעמיים: הגל ממתין לו.

האימג' אמיתי: קובצי מחיצה בפורמט 2 שנבנו ב-chunk_image.py (פריימים שמורים לפי RFC 8878),
וחתימת האינדקס במניפסט היא של הקובץ עצמו. הגזירה היא `chunkformat.derive_index` האמיתי,
עם פורס ה-Python של chunk_image (`STORED_DC`) במקום zstd -- כדי שהטסט ירוץ גם בלי zstd.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time

import pytest

pytest.importorskip("fastapi")

import chunk_image as C
import pcl_image as P
from conftest import MANIFEST_256, hello_body
from server.agent import readbackhash as RB
from server.library import chunkformat as F
from test_server_room import CLONER1, room, room_body, room_server   # noqa: F401 -- room_server is a fixture

IMAGE = "img_7f3a91"
ENGINES = ("udpcast", "chunk")
DC = {"cmd": C.STORED_DC, "frame_cmd": C.STORED_DC, "new": RB.PyXxh64}
# 1025 used blocks of 4KiB: two chunks, the last one short (as in test_chunk_format_2533)
STREAM = P.image(1030, list(range(0, 1000)) + list(range(1003, 1027)) + [1029], 4096)
BLOB, INDEX = C.build(STREAM)


def wait_for(check, seconds=30.0):
    """A ceiling, not a pause: it returns the moment `check` holds. The derivation is pure Python (`DC`), and in a full
    run at load 14 (1114d p4) the wave did not reach `running` in 10 s -- it takes 5 s on a quiet runner."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.05)
    return False


def _hello(room_server, joining=None):
    body = hello_body(CLONER1)
    if joining is not None:
        body["joining"] = joining
    body["disks"] = [{"dev": f"sd{chr(ord('a') + i)}", "size_bytes": 256060514304, "model": "Drawer SSD",
                      "serial": serial, "removable": False, "scheme": "gpt", "has_data": False, "port": i + 1}
                     for i, serial in enumerate(["S1", "S2"])]
    body["engines"] = list(ENGINES)
    response = room_server["anon"].post("/api/v1/agent/hello", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _folder(ctx):
    return ctx.library.file_path(IMAGE, next(p["file"] for p in MANIFEST_256["partitions"] if p.get("file"))).parent


def _fresh_capture(ctx, blob: bytes = BLOB):
    """What a capture leaves in the library: every partition file in format 2 and the index the agent
    signed in the manifest (`index_xxh64`) -- and nothing the server derives: no `.idx`, no `readback_*`."""
    folder = _folder(ctx)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    manifest = C.signed(manifest, {p["index"]: INDEX for p in manifest["partitions"] if p.get("file")})
    manifest["image_format"] = 2
    for part in manifest["partitions"]:
        if part.get("file"):
            (folder / part["file"]).write_bytes(blob)
            part["sha256"] = hashlib.sha256(blob).hexdigest()
            assert not (folder / (part["file"] + ".idx")).exists()
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def _indexes(ctx) -> list[bool]:
    folder = _folder(ctx)
    return [(folder / (p["file"] + ".idx")).exists() for p in MANIFEST_256["partitions"] if p.get("file")]


@pytest.fixture()
def derive(monkeypatch):
    """`chunkformat.derive_index` as it is, read with the Python decoder. `hold` blocks every derivation
    until it is set; `calls` counts them; `before(progress)` runs first (a derivation that has read part)."""
    real = F.derive_index

    class Derive:
        hold = threading.Event()
        calls: list = []
        before = None

        def __call__(self, path, raw, limit=None, stop=None, **kw):
            self.calls.append(path.name)
            if self.before is not None:
                self.before(kw.get("progress"))
            assert self.hold.wait(20), "the test never released the derivation"
            return real(path, raw, limit, stop, **{**kw, **DC})

    fake = Derive()
    fake.hold.set()
    monkeypatch.setattr(F, "derive_index", fake)
    return fake


def _open(room_server):
    assert room_server["admin"].post("/api/console/settings", json={"stream_engine": "chunk"}).status_code == 200
    opened = room_server["deploy"].post("/api/console/room", json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}))
    assert opened.status_code == 200, opened.text
    return opened.json()["wave_session_id"]


def _until_failed(room_server, seconds=10.0):
    """hello after hello (the room's pulse) until the round says it failed."""
    def failed():
        _hello(room_server, joining=False)
        return room(room_server["deploy"])["round"]["state"] == "failed"
    return wait_for(failed, seconds)


def _events(ctx, event):
    return [r["detail"] for r in ctx.conn.execute("SELECT detail FROM journal WHERE event = ?", (event,))]


# --- the wave waits, the server derives, the wave leaves --------------------------------------------


def test_a_wave_opened_right_after_a_capture_waits_for_the_index_and_then_leaves(room_server, derive):
    ctx = room_server["ctx"]
    _fresh_capture(ctx)
    derive.hold.clear()                                  # the derivation is under way, not done
    wave = _open(room_server)
    joined = _hello(room_server)
    again = _hello(room_server, joining=False)           # both drawers are ready: before the fix, the wave left here
    view = room(room_server["deploy"])["round"]
    assert view["wave_state"] == "open" and view["ready_drives"] == 2, view
    assert joined["session"]["state"] == "open" and again["session"]["state"] == "open"
    assert ctx.chunk.status() is None, "the engine was started for an image whose index is still being derived"
    preparing = view.get("preparing") or {}
    assert preparing.get("state") == "preparing" and preparing["he"].startswith("מכין את האימג' לשידור, "), view

    derive.hold.set()
    assert wait_for(lambda: all(_indexes(ctx))), _indexes(ctx)
    assert wait_for(lambda: _hello(room_server, joining=False)["session"]["state"] == "running")
    assert wait_for(lambda: (ctx.chunk.status() or {}).get("state") == "sending"), ctx.chunk.status()
    assert ctx.chunk.status()["error"] is None
    assert room(room_server["deploy"])["round"]["state"] == "active"
    assert len(_events(ctx, "room_index_wait")) == 1 and len(_events(ctx, "room_index_ready")) == 1
    ctx.chunk.stop(wave)


def test_the_index_the_wave_waited_for_is_the_one_the_agent_downloads(room_server, derive):
    """The wave leaves only on an index the agent can take: `GET …/index/<n>` serves it, and its
    XXH64 is the one the capture signed -- the same check chunkrecv.sh makes before it listens."""
    ctx = room_server["ctx"]
    _fresh_capture(ctx)
    wave = _open(room_server)
    _hello(room_server)
    assert wait_for(lambda: _hello(room_server, joining=False)["session"]["state"] == "running")
    for part in MANIFEST_256["partitions"]:
        if part.get("file"):
            served = room_server["anon"].get(f"/api/v1/images/{IMAGE}/index/{part['index']}")
            assert served.status_code == 200 and served.text == INDEX
    ctx.chunk.stop(wave)


# --- a derivation that fails fails the round, by name ------------------------------------------------


def test_a_derivation_that_fails_fails_the_round_with_its_reason_and_nothing_is_sent(room_server, derive):
    """The file in the library is not what was captured (one byte of chunk 1): the index derived from it is
    not the one the capture signed. The round fails before the wave leaves, and says why."""
    ctx = room_server["ctx"]
    damaged, _ = C.build(STREAM, damage=lambda k, chunk: chunk[:-1] + bytes([chunk[-1] ^ 1]) if k == 1 else chunk)
    _fresh_capture(ctx, damaged)
    _open(room_server)
    _hello(room_server)
    assert _until_failed(room_server), room(room_server["deploy"])["round"]
    reason = room(room_server["deploy"])["round"]["failed_reason"]
    assert reason.startswith("הכנת האימג' לשידור נכשלה") and "is not the one the capture signed" in reason, reason
    assert "עדיין נבנה" not in reason
    assert ctx.chunk.status() is None and room_server["recorder"].commands == []
    assert any(reason in d for d in _events(ctx, "room_failed"))


def test_a_file_that_is_not_format_2_frames_fails_the_round_by_name(room_server, derive):
    ctx = room_server["ctx"]
    _fresh_capture(ctx, b"not zstd frames at all")
    _open(room_server)
    _hello(room_server)
    assert _until_failed(room_server)
    reason = room(room_server["deploy"])["round"]["failed_reason"]
    assert reason.startswith("הכנת האימג' לשידור נכשלה") and "אין אינדקס מאומת" not in reason, reason
    assert ctx.chunk.status() is None


# --- a slow derivation: the state is shown --------------------------------------------------------------


def test_a_slow_derivation_shows_how_far_it_is_and_start_now_says_so(room_server, derive):
    ctx = room_server["ctx"]
    _fresh_capture(ctx)
    derive.hold.clear()
    derive.before = lambda progress: progress(1, 2)        # the first partition is half read
    wave = _open(room_server)
    _hello(room_server)
    _hello(room_server, joining=False)
    assert wait_for(lambda: (room(room_server["deploy"])["round"].get("preparing") or {}).get("percent", 0) > 0)
    preparing = room(room_server["deploy"])["round"].get("preparing")
    # two partition files of the same size; one half read = a quarter of the image
    assert preparing == {"state": "preparing", "percent": 25, "he": "מכין את האימג' לשידור, 25%"}, preparing
    refused = room_server["deploy"].post("/api/console/room/start")
    assert refused.status_code == 409 and "מכין את האימג' לשידור, 25%" in refused.json()["detail"], refused.text
    assert room(room_server["deploy"])["round"]["wave_state"] == "open" and ctx.chunk.status() is None

    derive.before = None
    derive.hold.set()
    assert wait_for(lambda: _hello(room_server, joining=False)["session"]["state"] == "running")
    assert wait_for(lambda: (ctx.chunk.status() or {}).get("state") == "sending"), ctx.chunk.status()
    ctx.chunk.stop(wave)


def test_a_derivation_past_its_ceiling_fails_the_round_by_name(room_server, derive, monkeypatch):
    from server.rounds import indexprep
    monkeypatch.setattr(indexprep, "CEILING_BASE_S", 0.5)
    ctx = room_server["ctx"]
    _fresh_capture(ctx)
    derive.hold.clear()                                     # it never ends by itself
    _open(room_server)
    _hello(room_server)
    assert _until_failed(room_server)
    reason = room(room_server["deploy"])["round"]["failed_reason"]
    assert reason.startswith("הכנת האימג' לשידור לא הסתיימה תוך") and "דבר לא שודר" in reason, reason
    assert ctx.chunk.status() is None
    derive.hold.set()


def test_the_ceiling_grows_with_the_image():
    from server.rounds import indexprep
    small, large = indexprep.ceiling_s(1_700_000_000), indexprep.ceiling_s(18_000_000_000)
    assert indexprep.ceiling_s(0) == indexprep.CEILING_BASE_S
    # #3137: 1.7GB was derived in 43 s on the VM lab -- the ceiling is well above it, and grows with the size
    assert 43 * 4 < small < large and large - small == pytest.approx((18e9 - 1.7e9) / indexprep.CEILING_FLOOR_BPS)


# --- one derivation at a time ----------------------------------------------------------------------------


def test_a_wave_waits_for_the_background_pass_that_already_derives_its_image(room_server, derive):
    """The background pass (`readbackhash.fill`) holds the image: the wave does not read it a second time,
    and leaves once the pass is done."""
    ctx = room_server["ctx"]
    _fresh_capture(ctx)
    assert RB.claim(IMAGE, ctx.library.get(IMAGE))          # the background pass is deriving it right now
    try:
        _open(room_server)
        _hello(room_server)
        _hello(room_server, joining=False)
        time.sleep(1.0)                                      # the wave's preparation polls the claim meanwhile
        assert derive.calls == [] and room(room_server["deploy"])["round"]["wave_state"] == "open"
        values, problems = RB.derive(ctx.library.get(IMAGE))   # what the pass does, and then writes
        assert problems == [] and ctx.library.record_index(IMAGE, values)
    finally:
        RB.release(IMAGE)
    assert wait_for(lambda: _hello(room_server, joining=False)["session"]["state"] == "running")
    assert len(derive.calls) == 2, derive.calls             # the pass's two partitions, and nothing more
    ctx.chunk.stop(ctx.chunk.status()["session_id"])


def test_the_background_pass_leaves_an_image_a_wave_is_deriving(tmp_path, derive):
    from server.library.images import ImageLibrary
    from conftest import write_image
    write_image(tmp_path, C.signed({**MANIFEST_256, "id": "img_3137aa"}, {3: INDEX}))
    (tmp_path / "img_3137aa" / "p3.win.pcl.zst").write_bytes(BLOB)
    lib = ImageLibrary(tmp_path)
    assert RB.claim("img_3137aa", lib.get("img_3137aa"))
    try:
        assert RB.fill(lib) == [] and derive.calls == []
    finally:
        RB.release("img_3137aa")
    assert [sorted(r["written"]) for r in RB.fill(lib)] == [[3]] and derive.calls == ["p3.win.pcl.zst"]


def test_the_derivation_reports_how_much_of_the_file_it_has_read(tmp_path):
    path = tmp_path / "p3.win.pcl.zst"
    path.write_bytes(BLOB)
    seen = []
    got = F.derive_index(path, False, None, None, progress=lambda done, total: seen.append((done, total)), **DC)
    assert got.render() == INDEX
    assert seen and all(t == seen[0][1] for _, t in seen) and seen[-1][0] == seen[0][1] == len(STREAM)
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)


# --- a udpcast wave needs no index and waits for nothing -------------------------------------------------


def test_with_the_switch_off_the_wave_does_not_wait_for_an_index(room_server, derive):
    ctx = room_server["ctx"]
    _fresh_capture(ctx)
    derive.hold.clear()
    opened = room_server["deploy"].post("/api/console/room", json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}))
    assert opened.status_code == 200, opened.text
    _hello(room_server)
    _hello(room_server, joining=False)
    view = room(room_server["deploy"])["round"]
    assert view["wave_state"] == "running" and view.get("preparing") is None and derive.calls == []
    derive.hold.set()
