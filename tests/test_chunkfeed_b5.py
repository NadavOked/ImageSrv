"""B5 (#3064): `chunkfeed.sh` -- ה-curl שמזין את `chunkcast recv --feed`, מאחורי מתג שכבוי, ורק כשהשרת אמר `fetch_parallel`.

רצים `chunkfeed.sh` האמיתי, `chunkcast recv` האמיתי (עם `--feed` ו-FIFO) ו-curl אמיתי, מול שרת HTTP בפייתון שעונה ל-Range של קובץ
המחיצה כמו שרת התמונות. מה שנבדק הוא מה שהמפעיל והשרת רואים: מה נכתב לדיסק, אילו בקשות יצאו, ומה היומן אומר.

**בקרה שלילית:** `agent/chunkcast.c`, `agent/lib/restore/chunkrecv.sh` ו-`agent/imagesrv-agent` חוזרים לבסיס, ו-`chunkfeed.sh` נמחק:
`chunkfeed` אינו נטען (ValueError ב-`agent_libs`) -- כל טסט כאן נופל, ו-`--feed` אינו אופציה ב-`chunkcast`.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import chunkcast_rig as C
from agentlib import agent_sources
from native import requires_native
from test_agent import BASH, posix
from test_chunkcast_2533 import cc, img   # noqa: F401 -- fixtures: the real chunkcast and its image (partclone, 4.5 chunks)

pytestmark = requires_native("gcc", "zstd", "curl", "jq", ("bash", BASH), posix=True,
                             why="chunkcast needs gcc and libzstd; the frames go over curl")


class ImageServer:
    """The image server's `files/<file>` with Range, over the partition file. `forge`: chunk -> the frame it is given instead."""

    def __init__(self, image: C.Image, forge: dict[int, bytes] | None = None):
        data = bytearray(image.file.read_bytes())
        for k, frame in (forge or {}).items():
            assert len(frame) == image.zlens[k]
            data[image.offsets[k]:image.offsets[k] + image.zlens[k]] = frame
        self.asked: list[str] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):          # noqa: N802
                outer.asked.append(self.headers.get("Range", ""))
                a, b = (int(x) for x in self.headers["Range"].split("=")[1].split("-"))
                body = bytes(data[a:b + 1])
                self.send_response(206)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):  # noqa: D401
                pass

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.http.server_address[1]
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def close(self):
        self.http.shutdown()


def run_pass(tmp: Path, cc_dir: Path, image: C.Image, answer: dict, server: ImageServer, *, switch: str | None = "1",
             start_s: int = 30) -> dict:
    """One receive pass of one drawer, as chunk_partition_drawers does it: prepare, recv, the answer to `listening`, the end."""
    run, chunk, dev = tmp / "run", tmp / "run" / "chunk", tmp / "dev"
    for d in (chunk, dev):
        d.mkdir(parents=True, exist_ok=True)
    target = image.blank(dev / "sda3")
    idx = chunk / "p3.idx"
    idx.write_bytes(image.idx.read_bytes())
    (tmp / "answer.json").write_text(json.dumps(answer))
    args = f"--index {posix(idx)} --index-xxh64 {image.index_xxh} --head {posix(image.head_z)}"
    port = C.free_port()
    env_switch = f"CHUNK_PARALLEL={switch} " if switch is not None else ""
    script = (f"export RUN_DIR={posix(run)!r} CHUNK_DIR={posix(chunk)!r} CHUNK_IDX={posix(idx)!r} HTTP_TIMEOUT=5 "
              f"WAIT_STREAM_STALL_S=5 WAIT_HELPER_S=10 CHUNKCAST_ALLOW_FILE=1 {env_switch}"
              f"PATH={posix(cc_dir)!r}:\"$PATH\"; " + agent_sources("common", "waits", "chunkfeed")
              + "CHUNK_FEED_ARGS=''; chunk_feed_prepare; echo \"args=$CHUNK_FEED_ARGS\"; "
              f"chunkcast recv --listen 127.0.0.1:{port} --session {C.new_session()} --part 3 --pass 1 {args} "
              f"--state {posix(chunk / 'p3.state')!r} --start-s {start_s} --stall-s 5 --sync-ms 100 $CHUNK_FEED_ARGS {posix(target)!r} "
              f"> {posix(chunk / 'recv.out')!r} 2>> \"$LOG_FILE\" & pid=$!; n=0; "
              f"until grep -qx listening {posix(chunk / 'recv.out')!r} 2>/dev/null || ! kill -0 $pid 2>/dev/null || [ $n -ge 300 ]; "
              "do sleep 0.1; n=$((n + 1)); done; "
              f"chunk_feed_start http://127.0.0.1:{server.port} img_1 p3.windows.pcl.zst 3 {posix(tmp / 'answer.json')!r}; "
              "wait $pid; echo \"rc=$?\"; "
              f"chunk_feed_end {posix(chunk / 'recv.out')!r}; echo \"left=$(ls {posix(chunk)!r} | tr '\\n' ' ')\"")
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True, encoding="utf-8", timeout=120,
                          stdin=subprocess.DEVNULL)
    out = proc.stdout + proc.stderr
    word = lambda key: next((ln.split("=", 1)[1] for ln in proc.stdout.splitlines() if ln.startswith(key + "=")), None)  # noqa: E731
    return {"rc": word("rc"), "args": word("args"), "left": word("left"), "out": out, "target": target.read_bytes(),
            "log": (run / "agent.log").read_text(encoding="utf-8") if (run / "agent.log").exists() else "",
            "recv": (chunk / "recv.out").read_text() if (chunk / "recv.out").exists() else ""}


def _feed_line(got: dict) -> list[str]:
    return next((ln.split()[1:] for ln in got["recv"].splitlines() if ln.startswith("feed ")), [])


def test_when_the_server_says_fetch_parallel_one_curl_feeds_the_receiver_and_the_partition_is_written(cc, img, tmp_path):
    """הנתיב כולו, כמו ב-`chunk_partition_drawers`: `chunk_feed_prepare` עושה FIFO ומוסיף `--feed` ל-`chunkcast recv`, התשובה ל-`listening`
    נושאת `fetch_parallel` עם טווח אחד (0-4), ו-`chunk_feed_start` כותב את הרשימה שלמה ומפעיל curl אחד שמזין את ה-FIFO. כאן אין
    זרם, וההזנה לבדה משלימה: המעבר נגמר `complete` (rc=0), הדיסק שווה לאימג', והיומן אומר מה chunkcast אמר (`ended` 5 חתיכות)."""
    server = ImageServer(img)
    try:
        got = run_pass(tmp_path, cc, img, {"ok": True, "action": "receive", "pass": 1,
                                           "fetch_parallel": {"ranges": [[0, img.n - 1]]}}, server)
    finally:
        server.close()
    assert got["rc"] == "0", got["out"] + got["log"]
    assert "--feed" in got["args"] and "--feed-chunks" in got["args"], got["args"]
    assert got["target"] == img.expected()
    assert _feed_line(got) == ["ended", str(img.n), "0", "0"], got["recv"]
    assert "pass complete" in got["recv"], got["recv"]
    assert len(server.asked) == 1, "one range, one request: curl reads every range of the answer on one connection"
    assert f"chunkcast said 'feed ended {img.n} 0 0'" in got["log"], got["log"]
    assert "feed.fifo" not in got["left"] and "feed.pid" not in got["left"], got["left"]


def test_off_as_shipped_nothing_is_made_and_no_request_goes_out_whatever_the_server_says(cc, img, tmp_path):
    """בלי `CHUNK_PARALLEL=1` -- ברירת המחדל -- אין FIFO, אין `--feed`, ואף בקשה לא יוצאת גם כשהתשובה נושאת `fetch_parallel`."""
    server = ImageServer(img)
    try:
        got = run_pass(tmp_path, cc, img, {"ok": True, "action": "receive", "pass": 1, "fetch_parallel": {"ranges": [[0, 4]]}},
                       server, switch=None, start_s=3)
    finally:
        server.close()
    assert got["args"] == "" and server.asked == [], (got["args"], server.asked)
    assert got["rc"] == "3" and _feed_line(got) == [], "no stream and no feed: the pass ends as it did"
    assert got["target"] == img.expected(set())


@pytest.mark.parametrize("switch", ["0", "true", "yes", ""])
def test_only_one_turns_it_on(cc, img, tmp_path, switch):
    server = ImageServer(img)
    try:
        got = run_pass(tmp_path, cc, img, {"ok": True, "fetch_parallel": {"ranges": [[0, 4]]}}, server, switch=switch, start_s=3)
    finally:
        server.close()
    assert got["args"] == "" and server.asked == [], (got["args"], server.asked)


def test_on_but_the_server_said_nothing_means_no_curl_and_an_idle_feed(cc, img, tmp_path):
    """המתג דלוק ו-B5 לא החליט OPEN: התשובה בלי `fetch_parallel`. ה-FIFO קיים, ה-curl לא יוצא, ו-chunkcast אומר `idle`."""
    server = ImageServer(img)
    try:
        got = run_pass(tmp_path, cc, img, {"ok": True, "action": "receive", "pass": 1}, server, start_s=3)
    finally:
        server.close()
    assert "--feed" in got["args"] and server.asked == [], (got["args"], server.asked)
    assert _feed_line(got) == ["idle", "0", "0", "0"] and got["rc"] == "3", got["recv"]


@pytest.mark.parametrize("ranges,why", [([[3, 4], [0, 1]], "not ascending"), ([[0, 99]], "not in the index"),
                                        ([[0, "x"]], "not numbers"), ([], "empty")])
def test_ranges_the_index_does_not_hold_start_no_curl(cc, img, tmp_path, ranges, why):
    server = ImageServer(img)
    try:
        got = run_pass(tmp_path, cc, img, {"ok": True, "fetch_parallel": {"ranges": ranges}}, server, start_s=3)
    finally:
        server.close()
    assert server.asked == [] and _feed_line(got) == ["idle", "0", "0", "0"], (why, server.asked, got["recv"])
    assert got["target"] == img.expected(set()), why


def test_a_chunk_the_server_gets_wrong_is_refused_before_a_byte_of_it_is_written(cc, img, tmp_path):
    """השרת (או מה שבדרך) מוסר ב-HTTP חתיכה 2 מזויפת -- פריים תקין באורך הנכון, תוכן אחר. 0 ו-1 נכתבות (תקינות); 2 נדחית ב-
    `cc_verify` לפני כתיבה, ההזנה נגמרת `bad-chunk`, ואחריה אין דבר: חתיכות 3-4 אינן נכתבות. הדיסק: 0-1 בלבד."""
    other = C.build(tmp_path / "other", cc / "fanout", noisy=(0, 2), salt=99)
    assert other.zlens[2] == img.zlens[2] and other.frame(2) != img.frame(2)
    server = ImageServer(img, forge={2: other.frame(2)})
    try:
        got = run_pass(tmp_path, cc, img, {"ok": True, "fetch_parallel": {"ranges": [[0, 4]]}}, server, start_s=5)
    finally:
        server.close()
    assert _feed_line(got) == ["bad-chunk", "2", "0", "1"], got["recv"]
    assert got["target"] == img.expected({0, 1}), "not a byte of the forged chunk, and nothing after it"
    assert "chunk 2 does not match the index (wrong XXH64) -- not written" in got["out"] + got["log"], got["out"] + got["log"]
