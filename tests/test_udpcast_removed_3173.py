"""#3173 (נדב 21/11): udpcast הוסר מהשרת, ומנוע החתיכות הוא מנוע השידור היחיד.

מה שנבדק כאן הוא הצד של השרת, בלי רשת ובלי `chunkcast`:

- `engine_of` עונה `chunk` לכל ערך שמור; ערך שמור של המנוע הישן נרשם ביומן **פעם אחת** ואינו נמחק.
- ההגדרה `stream_engine` יצאה מהקונסולה: לא מוחזרת, ומי ששולח אותה מקבל 422 בשמו.
- סבב כיתה נדחה בגלוי ("סבב כיתה יגיע בגרסה 2") בשני הנתיבים שפותחים אותו, לפני כל בדיקה אחרת; סבב כיתה שנשאר מלפני השדרוג
  ומגיע ל-`running` נכשל באותו שם ואינו מפעיל שולח.
- מה שמנוע החתיכות לקח מהשולח הישן נמצא עליו: `max_bitrate`, `link()`, `gate`, `select_interface`.
- שורת הבריאות של היתום (#439) היא של `chunkcast send`, ולא של `chunkcast b3list`.
- שרת שאסור לו לשדר (`sender_simulate`) אינו מפעיל תהליך.

**בקרה שלילית** (עץ `/work/w3173-core-neg`: כל `server/` של `7ba2f2f03`, הטסט הזה מעליו) -- בגוף ה-PR. הייבוא
בראש הקובץ הוא רק של מה שקיים גם שם, והשמות החדשים (`chunkctx`, `note_retired`, ...) נקראים בתוך הטסטים: כך
הקובץ נאסף גם על הקוד הישן, והכשל שם הוא של התנהגות ולא של איסוף.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from conftest import _build_server, setup_classroom     # noqa: E402

from server import db                                   # noqa: E402
from server.agent import registry                       # noqa: E402
from server.rounds import chunksend, sessions           # noqa: E402
from server.system import health                        # noqa: E402

IMAGE = "img_7f3a91"
#: The words themselves, not the constants: on the code before #3173 the constants do not exist, and a test that
#: fails on an AttributeError says less than one that fails on what the server answered.
CLASS_ROUND_V2 = "סבב כיתה יגיע בגרסה 2"
RETIRED_EVENT = "stream_engine_retired"


class Spawned:
    """מה שה-runner המוזרק מחזיר: תהליך שכבר יצא."""

    pid, returncode = None, 0

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        return None

    def kill(self):
        return None


class Recorder:
    def __init__(self):
        self.commands: list[list[str]] = []

    def __call__(self, cmd):
        self.commands.append(list(cmd))
        return Spawned()


@pytest.fixture()
def built(tmp_path, images_root, clock):
    recorder = Recorder()
    bundle = _build_server(tmp_path, images_root, clock, recorder)
    yield bundle
    _stop(bundle["ctx"])


def _stop(ctx) -> None:
    ctx.chunk.stop()
    old = getattr(ctx, "sender", None)          # the negative control's tree still has the old sender: stop it too
    if old is not None:
        old.stop()


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "engine.db")
    yield c
    c.close()


def _events(c, event: str) -> list[str]:
    return [r["detail"] for r in c.execute("SELECT detail FROM journal WHERE event = ? ORDER BY id", (event,))]


# --- the engine ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("stored", [None, "chunk", "udpcast", "something-else", ""],
                         ids=["unset", "chunk", "the-removed-engine", "unknown", "empty"])
def test_the_engine_is_chunk_whatever_was_stored(conn, stored):
    if stored is not None:
        db.set_setting(conn, chunksend.SETTING, stored)
    assert db.get_setting(conn, chunksend.SETTING) == stored      # read back: the value under test is the stored one
    assert chunksend.engine_of(conn) == "chunk"


def test_a_stored_choice_of_the_removed_engine_is_said_once_and_kept(conn):
    db.set_setting(conn, chunksend.SETTING, "udpcast")
    assert chunksend.note_retired(conn, db.journal) is True
    assert chunksend.note_retired(conn, db.journal) is False      # the next start of the server: the line is already there
    lines = _events(conn, RETIRED_EVENT)
    assert len(lines) == 1 and "stream_engine=udpcast" in lines[0] and "לא נמחק" in lines[0]
    assert db.get_setting(conn, chunksend.SETTING) == "udpcast"   # the least destructive: the value stays


@pytest.mark.parametrize("stored", [None, "chunk", ""], ids=["unset", "chunk", "empty"])
def test_a_server_that_never_chose_the_removed_engine_is_told_nothing(conn, stored):
    if stored is not None:
        db.set_setting(conn, chunksend.SETTING, stored)
    assert chunksend.note_retired(conn, db.journal) is False
    assert _events(conn, RETIRED_EVENT) == []


def test_the_start_of_an_upgraded_server_says_it(tmp_path, images_root, clock):
    """The wiring: `create_runtime` calls `note_retired` -- a function nobody calls looks exactly like one that works."""
    (tmp_path / "data").mkdir()
    seed = db.connect(tmp_path / "data" / "imagesrv.db")            # the name `create_runtime` opens
    db.set_setting(seed, chunksend.SETTING, "udpcast")
    seed.close()
    bundle = _build_server(tmp_path, images_root, clock, Recorder())
    try:
        c = bundle["ctx"].conn
        assert db.get_setting(c, chunksend.SETTING) == "udpcast", "the seeded database is not the one the server opened"
        assert len(_events(c, RETIRED_EVENT)) == 1
    finally:
        _stop(bundle["ctx"])


# --- the console ----------------------------------------------------------------------------------------------------

def test_the_setting_left_the_console(built):
    admin, c = built["admin"], built["ctx"].conn
    stored = db.get_setting(c, chunksend.SETTING)
    assert "stream_engine" not in admin.get("/api/console/settings").json()
    for value in ("udpcast", "chunk"):
        answer = admin.post("/api/console/settings", json={"stream_engine": value})
        assert answer.status_code == 422, answer.text
        assert answer.json()["detail"] == chunksend.RETIRED_HE
    assert db.get_setting(c, chunksend.SETTING) == stored         # nothing was written
    # one refused key refuses the whole request: the valid key beside it is not written either
    before = admin.get("/api/console/settings").json()["cloner_beep"]
    flipped = "false" if before == "true" else "true"
    assert admin.post("/api/console/settings", json={"cloner_beep": flipped, "stream_engine": "chunk"}).status_code == 422
    assert admin.get("/api/console/settings").json()["cloner_beep"] == before


def test_the_overview_shows_the_only_engine(built):
    assert built["admin"].get("/api/console/overview").json()["sender"] is None       # nothing is sent: None, not a dead sender
    assert not hasattr(built["ctx"], "sender")


# --- a class round ---------------------------------------------------------------------------------------------------

def test_the_console_refuses_a_class_round_even_with_classrooms_on(built):
    made = setup_classroom(built, class_rounds=False)              # turns CLASSROOMS on, as v2 will; registers the class
    woken: list = []
    built["ctx"].store.on_opened = lambda *a, **kw: woken.append(a)
    answer = built["admin"].post("/api/console/sessions", json={"group_id": made["group"], "image_id": IMAGE})
    assert answer.status_code == 409, answer.text
    assert answer.json()["detail"] == CLASS_ROUND_V2 == sessions.CLASS_ROUND_V2
    assert built["ctx"].conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"] == 0
    assert woken == [] and built["recorder"].commands == []       # no WoL, no sender


def test_the_station_path_refuses_a_class_round_before_it_reads_anything(built):
    """`POST /api/v1/agent/sessions`: 409 by name before the body, the identity and the password are looked at --
    a road with nothing behind it does not check passwords (#1282)."""
    setup_classroom(built, class_rounds=False)
    answer = built["anon"].post("/api/v1/agent/sessions", json={})
    assert answer.status_code == 409, answer.text
    assert answer.json() == {"ok": False, "error": CLASS_ROUND_V2, "code": "class_round_v2"}
    assert built["ctx"].conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"] == 0


def test_the_slot_stays_free_for_the_cloner_room(built):
    """The refusal took nothing: the cloner room opens its wave right after it."""
    made = setup_classroom(built, class_rounds=False)
    assert built["admin"].post("/api/console/sessions",
                               json={"group_id": made["group"], "image_id": IMAGE}).status_code == 409
    wave = built["ctx"].store.open(registry.CLONERS_GROUP, IMAGE, "ROOM", 1, opened_by="noc")
    row = built["ctx"].conn.execute("SELECT state FROM sessions WHERE id = ?", (wave,)).fetchone()
    assert row["state"] == "open"


def test_a_class_round_left_from_before_the_upgrade_fails_by_name_when_it_starts(built):
    setup_classroom(built)                                          # the group the old round belongs to
    c, store = built["ctx"].conn, built["ctx"].store
    c.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state, opened_by,"
              " created_at, last_join_at, kind, started_at) VALUES ('ses_old1', 'grp_LAB1', ?, 'LAB1', 2, 300, 'running',"
              " 'noc', ?, 0, 'multicast', ?)", (IMAGE, db.now_iso(), db.now_iso()))
    c.commit()
    store.on_running({"id": "ses_old1", "image_id": IMAGE})
    row = c.execute("SELECT state, failed_reason FROM sessions WHERE id = 'ses_old1'").fetchone()
    assert (row["state"], row["failed_reason"]) == ("failed", CLASS_ROUND_V2)
    assert built["recorder"].commands == [], "a sender was started for a round that has no receiver"
    assert built["ctx"].chunk.status() is None
    assert any(CLASS_ROUND_V2 in line for line in _events(c, "send_failed"))


# --- what the engine took over from the old sender ---------------------------------------------------------------------

def test_the_engine_carries_the_ceiling_the_link_and_the_gates(built, monkeypatch):
    from server.rounds import chunkctx
    engine = built["ctx"].chunk
    assert engine.max_bitrate is None                                # nobody set a ceiling: None, not a number
    assert engine.link() == {"interface": engine.interface, "speed_mbps": None, "why": "not_wired"}
    assert engine.gate is not None and engine.select_interface is not None    # wired by `create_runtime`
    assert engine.port == chunkctx.DEFAULT_PORTBASE + chunksend.PORT_OFFSET
    monkeypatch.setenv("IMAGESRV_MAX_BITRATE", "700m")
    assert chunkctx.env_max_bitrate() == "700m"
    monkeypatch.setenv("IMAGESRV_MAX_BITRATE", "")
    assert chunkctx.env_max_bitrate() is None
    read = chunkctx.link_of(lambda interface: {"interface": interface, "speed_mbps": 1000, "why": None}, "eth1")
    assert read == {"interface": "eth1", "speed_mbps": 1000, "why": None}


def test_a_server_that_must_not_send_spawns_nothing(tmp_path, images_root):
    from server.app import create_app
    from server.rounds import chunkctx
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:7712", sender_simulate=True,
                     health_hooks={"interfaces": lambda: []})
    engine = app.state.ctx.chunk
    try:
        assert engine.binary_check is None                           # no `chunkcast --version` either
        path = images_root / IMAGE / "p1.esp.pcl.zst"
        process = engine.runner(["chunkcast", "send", "--file", str(path)])
        assert isinstance(process, chunkctx.SimulatedSend)
        assert process.poll() == 0 and process.wait() == 0 and process.pid is None
    finally:
        _stop(app.state.ctx)


# --- the orphan check of the health page (#439) --------------------------------------------------------------------------

def _proc(root, pid: int, argv: list[str]) -> None:
    (root / str(pid)).mkdir()
    (root / str(pid) / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")


def test_the_orphan_scan_finds_a_sending_chunkcast_and_nothing_else(tmp_path):
    _proc(tmp_path, 4321, ["/usr/local/bin/chunkcast", "send", "--to", "239.84.82.1:30201"])
    _proc(tmp_path, 4400, ["chunkcast", "send", "--file", "/srv/images/x/p1.img.zst"])
    _proc(tmp_path, 4500, ["chunkcast", "b3list", "/srv/images/x/p1.img.zst"])      # the index derivation: not a sender
    _proc(tmp_path, 4600, ["python3", "-m", "server.main"])
    _proc(tmp_path, 4700, ["not-chunkcast", "send"])
    (tmp_path / "self").mkdir()                                       # not a pid
    assert health.live_chunk_sender_pids(tmp_path) == [4321, 4400]


def test_a_scan_that_read_nothing_is_not_an_empty_list(tmp_path):
    assert health.live_chunk_sender_pids(tmp_path / "absent") is None       # no /proc: "not checked", never "not running"
    assert health.live_chunk_sender_pids(tmp_path) is None                  # a /proc with no process read


@pytest.mark.parametrize("pids,live,state", [([], False, "ok"), ([7], True, "warn"), ([7], False, "bad"), (None, False, "bad")],
                         ids=["not-running", "inside-a-round", "orphan", "not-checked"])
def test_the_health_row_is_the_chunk_senders(pids, live, state):
    row = health.sender_check(pids, live)
    assert (row["id"], row["state"]) == ("chunk_sender", state)
    assert "chunkcast" in row["label"]
