"""#2533, שלב 3: גל שלם של מנוע החתיכות, מקצה לקצה, בלי ברזל.

המנוע של השרת (`chunksend.ChunkEngine`) אמיתי, `chunkcast send` / `recv` / `apply`
אמיתיים, והדיסקים קבצים. בין השולח למקלטים יושב ממסר שמאבד חבילות לפי זהותן.
מה שמדומה כאן הוא רק הסוכן: לולאת פייתון שעושה מה ש-`chunkrecv.sh` עושה —
מפעילה מקלט, מדווחת `listening` ו-`ended`, ומבצעת את התשובה (`receive` / `fetch` /
`done`). את ה-shell עצמו הטסט הזה **אינו** מריץ.

שלוש הדרישות שנבדקות כאן יחד:
- חבילות שאבדו מושלמות, והדיסקים זהים בית-בית למה שהיה אמור להיכתב.
- חתיכה שחסרה לשני מחשבים משודרת שוב פעם אחת; חתיכה שחסרה למחשב אחד — הוא מוריד.
- מקלט שנעלם באמצע המעבר וחזר ממשיך ממה שכבר עמיד אצלו, ומשלים.
"""

from __future__ import annotations

import signal
import subprocess
import threading
import time
from pathlib import Path

import pytest

import chunkcast_rig as C
from native import requires_native
from server.rounds import chunksend

pytestmark = requires_native("gcc", "zstd", posix=True, why="chunkcast צריך gcc, libzstd ו-zstd")

SID = "wave-1"
PART = 3


@pytest.fixture(scope="module")
def cc(tmp_path_factory):
    return C.build_binaries(tmp_path_factory.mktemp("chunkround-bin"))


@pytest.fixture(scope="module")
def img(cc, tmp_path_factory):
    return C.build(tmp_path_factory.mktemp("img"), cc / "fanout", noisy=(0, 2))


class Lib:
    def __init__(self, image):
        self.image = image

    def get(self, image_id):
        return {"id": image_id, "partitions": [{"index": PART, "file": self.image.file.name,
                                                "index_xxh64": self.image.index_xxh, "readback_chunks": self.image.n}]}

    def file_path(self, image_id, name):
        return self.image.file


class Wave:
    """המנוע האמיתי, עם `chunkcast send` אמיתי שמשדר אל הממסר במקום אל הקבוצה."""

    def __init__(self, cc, image, relay, macs, tmp_path, monkeypatch):
        monkeypatch.setattr(chunksend, "POLL_S", 0.02)
        monkeypatch.setattr(chunksend, "ENDED_GRACE_S", 0)
        self.cc, self.img, self.relay, self.tmp, self.events, self.commands = cc, image, relay, tmp_path, [], []
        self.passes: list[set[int]] = []        # what the relay forwarded in each multicast pass
        self.errors: list[BaseException] = []
        self.engine = chunksend.ChunkEngine(
            Lib(image), live_members=lambda sid: list(macs), lost_wait=lambda: 8, write_floor=lambda: 50,
            runner=self._run, max_wait=15, start_timeout=15, wave_bitrate=lambda session: "80m",
            ledger=C.MemLedger(),
            on_event=lambda event, detail, sid: self.events.append((event, detail)))
        for mac in macs:
            self.engine.note_agent(mac, ["udpcast", "chunk"])
        self.lists: list[str | None] = []
        assert self.engine.start({"id": SID, "image_id": "img"}) is None

    def _run(self, cmd):
        for seen in self.relay.chunks_seen:
            seen.clear()
        real = [str(self.cc / "chunkcast"), *cmd[1:], "--lead-ms", "200", "--tail-ms", "500"]
        real[real.index("--to") + 1] = self.relay.addr
        self.commands.append(cmd)
        self.lists.append(open(cmd[cmd.index("--chunks-file") + 1], encoding="ascii").read().strip()
                          if "--chunks-file" in cmd else None)
        tag = self.tmp / f"send{len(self.commands)}"
        with tag.with_suffix(".out").open("wb") as out, tag.with_suffix(".err").open("wb") as err:
            return subprocess.Popen(real, stdout=out, stderr=err, stdin=subprocess.DEVNULL)

    def tell(self, mac, pass_, state, disks, state_file):
        durable = C.state_of(state_file) if state_file.exists() else {}
        return self.engine.report(SID, mac, {"part": PART, "pass": pass_, "state": state, "targets": [
            {"serial": serial, "write_mbps": 100,
             "durable": [{"part": PART, "ranges": _ranges(durable.get(str(path), {}).get("durable", set()))}]}
            for serial, path in disks.items()]})

    def agent(self, mac, receiver, disks, state_file, first_pass=None):
        """מה ש-`chunkrecv.sh` עושה למחיצה אחת. `first_pass`: מה קורה במעבר 1 במקום מקלט רגיל."""
        try:
            pass_, action, answer = 1, "receive", {}
            while action != "done":
                if action == "receive" and pass_ == 1 and first_pass is not None:
                    first_pass()
                elif action == "receive":
                    run = C.recv(self.cc, self.img, list(disks.values()), self.relay.listen(receiver),
                                 self.engine.session_fields(SID)["chunk"]["key"], state_file,
                                 tag=self.tmp / f"{mac}-p{pass_}", pass_=pass_, part=PART, stall_s=10)
                    assert self.tell(mac, pass_, "listening", disks, state_file)["action"] == "receive"
                    assert run.wait() in (0, 4), run.log()
                elif action == "fetch":
                    for lo, hi in answer["ranges"]:
                        frames = b"".join(self.img.frame(k) for k in range(lo, hi + 1))   # what a Range gives
                        got = C.apply(self.cc, self.img, list(disks.values()), state_file, lo, hi - lo + 1, frames,
                                      tag=self.tmp / f"{mac}-fetch{pass_}-{lo}", part=PART)
                        assert got.process.returncode == 0, got.log()
                else:
                    raise AssertionError(f"{mac}: the server answered {answer}")
                end = time.monotonic() + 30
                while True:
                    answer = self.tell(mac, pass_, "ended", disks, state_file)
                    assert answer["ok"] and "fail" not in answer, answer
                    if answer["action"] != "wait":
                        break
                    assert time.monotonic() < end, f"{mac} is still told to wait"
                    time.sleep(0.1)
                action, pass_ = answer["action"], answer.get("pass", pass_)
        except BaseException as exc:   # noqa: BLE001 -- a thread's failure is the test's
            self.errors.append(exc)

    def run(self, agents):
        threads = [threading.Thread(target=self.agent, args=a, daemon=True) for a in agents]
        for t in threads:
            t.start()
        try:
            for t in threads:
                t.join(120)
            assert not self.errors, self.errors
            assert not any(t.is_alive() for t in threads)
            end = time.monotonic() + 5
            while self.engine.status()["state"] != "done" and time.monotonic() < end:
                time.sleep(0.05)
            assert self.engine.status()["state"] == "done", (self.engine.status(), self.events)
        finally:
            self.engine.close()


def _ranges(chunks):
    return [[int(lo), int(hi)] for lo, hi in (r.split("-") for r in C.ranges_of(chunks).split(",") if r)]


def test_lost_chunks_are_repaired_one_resend_for_two_machines_http_for_one(cc, tmp_path, monkeypatch):
    """a קיבל הכול. b איבד את 1 ואת 3, c איבד את 3 ואת 4 — במעבר הראשון בלבד.
    3 חסרה לשניים: משודרת שוב פעם אחת. 1 ו-4 חסרות למחשב אחד כל אחת: הוא מוריד."""
    # #4073: keep the loopback stream small so the relay's deliberate drops define the loss pattern.
    img = C.build(tmp_path / "img", cc / "fanout", noisy=())
    lost = {1: {1, 3}, 2: {3, 4}}
    seen_pass = {"n": 1}

    def rule(receiver, kind, chunk, seq, packet):
        if kind == C.ANNOUNCE:
            seen_pass["n"] = int.from_bytes(packet[16:20], "big")
        if kind == C.DATA and seen_pass["n"] == 1 and chunk in lost.get(receiver, ()) and seq == 0:
            return []
        return [packet]

    relay = C.Relay(3, rule)
    disks = {"a": {"A1": img.blank(tmp_path / "a1.disk")},
             "b": {"B1": img.blank(tmp_path / "b1.disk"), "B2": img.blank(tmp_path / "b2.disk")},
             "c": {"C1": img.blank(tmp_path / "c1.disk")}}
    try:
        wave = Wave(cc, img, relay, list(disks), tmp_path, monkeypatch)
        wave.run([(mac, i, disks[mac], tmp_path / f"{mac}.state") for i, mac in enumerate(disks)])
    finally:
        relay.close()

    want = img.expected()
    for mac in disks:
        for path in disks[mac].values():
            assert path.read_bytes() == want, f"{path.name} is not the image"
    assert wave.lists == [None, "3-3"], wave.events                         # one resend, and only of chunk 3
    status = wave.engine.status()
    assert status["multicast_chunks"] == 1 and status["fetched_chunks"] == 2, wave.events
    assert [e for e, _d in wave.events if e in ("send_failed", "chunk_disk_failed")] == []
    assert [e for e, _d in wave.events].count("send_done") == 1


def test_a_receiver_that_vanished_mid_pass_and_came_back_finishes_from_what_it_had(cc, img, tmp_path, monkeypatch):
    """המקלט של c נהרג (SIGKILL) אחרי ששלוש חתיכות עמידות אצלו. הוא חוזר, מדווח את
    מה שיש לו, ומשלים את השאר; a אינו מחכה לו ואינו נכתב שוב."""
    def rule(receiver, kind, chunk, seq, packet):
        if receiver == 1 and (kind == C.END or (kind == C.DATA and chunk > 2)):
            return []                           # c stops hearing the stream after chunk 2
        return [packet]

    relay = C.Relay(2, rule)
    disks = {"a": {"A1": img.blank(tmp_path / "a1.disk")}, "c": {"C1": img.blank(tmp_path / "c1.disk")}}
    c_state = tmp_path / "c.state"
    try:
        wave = Wave(cc, img, relay, list(disks), tmp_path, monkeypatch)

        def c_first_pass():
            run = C.recv(cc, img, list(disks["c"].values()), relay.listen(1),
                         wave.engine.session_fields(SID)["chunk"]["key"], c_state,
                         tag=tmp_path / "c-p1", pass_=1, part=PART, stall_s=60)
            wave.tell("c", 1, "listening", disks["c"], c_state)
            end = time.monotonic() + 30
            while not (c_state.exists() and C.state_of(c_state)[str(disks["c"]["C1"])]["durable"] == {0, 1, 2}):
                assert time.monotonic() < end and run.process.poll() is None, run.log()
                time.sleep(0.05)
            run.process.send_signal(signal.SIGKILL)
            run.process.wait(10)

        wave.run([("a", 0, disks["a"], tmp_path / "a.state", None),
                  ("c", 1, disks["c"], c_state, c_first_pass)])
    finally:
        relay.close()

    assert disks["a"]["A1"].read_bytes() == img.expected() and disks["c"]["C1"].read_bytes() == img.expected()
    assert wave.lists == [None]                                             # one machine lacked them: no resend
    assert wave.engine.status()["fetched_chunks"] == 2
    assert Path(c_state).exists() and C.state_of(c_state)[str(disks["c"]["C1"])]["durable"] == set(range(img.n))
