"""B29: השלמה ממשכפל שכן -- `peerfill_run` (המשכפל החסר), `peer_serve_take` (השכן) ו-`chunkcast serve --until-s
--read-mibs`. **השכן אינו נאמן**, והטסטים כאן הם קודם כול על זה.

רצים `peerfill.sh` ו-`neighborfill.sh` האמיתיים, `chunkcast apply` ו-`chunkcast serve` האמיתיים ו-curl האמיתי, מול
"דיסקים" שהם קבצים (CHUNKCAST_ALLOW_FILE), על אימג' שנחתך ב-chunkcut האמיתי. השכן הזדוני הוא שרת HTTP בפייתון שעונה
כמו `chunkcast serve`, אבל שולח בחתיכה 2 פריים מזויף **באורך הנכון**: zstd תקין של 4MiB אחרים, ואחריו frame מסוג skippable
שמשלים בדיוק לאורך שבאינדקס. מזויף רק `blockdev` (אין ריקון מטמון לקובץ). רשימת ה-BLAKE3 של המחיצה (תנאי 1 של אגיס,
#3332) היא האמיתית, ב-`$CHUNK_IMAGE_ARGS` כמו ש-`chunkb3.sh` שם אותה; עטיפה דקה רושמת את ה-argv של chunkcast. השער הוא
XXH64 ו-BLAKE3 מול האינדקס, הרשימה וה-head שהגיעו מהשרת; חתיכה מזויפת **עם אותו XXH64** נעצרת רק ב-BLAKE3.

**בקרה שלילית:** `agent/lib/restore/peerfill.sh`, `neighborfill.sh`, `agent/chunkcast.c`, `agent/imagesrv-agent` ו-
`tests/agentlib.py` (NEEDS) חוזרים לבסיס. `peerfill` אינו נטען (ValueError ב-`agent_libs`, או "command not found"),
ו-`serve` אינו מכיר `--until-s`/`--read-mibs` (bad option) -- כל טסט כאן נופל ב-AssertionError על מה שנכתב לדיסק, על
קוד היציאה או על ה-argv.
"""

from __future__ import annotations

import json
import os
import stat
import struct
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import chunkcast_rig as C
import test_chunk_b3_3332 as B3
from agentlib import agent_sources
from native import requires_native
from test_agent import BASH, posix
from test_chunkcast_stdin_3161 import cut_disk

pytestmark = requires_native("gcc", "zstd", "curl", ("bash", BASH), posix=True,
                             why="chunkcast needs gcc and libzstd; the frames go over curl")

TOKEN = "0123456789abcdef0123456789abcdef"
ID = "a1b2c3d4e5f60718"
HDR = "X-Imagesrv-Serve-Token"

FAKE_BLOCKDEV = """#!/bin/sh
echo "blockdev $*" >> "$RUN_DIR/trace"
"""


@pytest.fixture(scope="module")
def cc(tmp_path_factory):
    return C.build_binaries(tmp_path_factory.mktemp("pf-bin"))


@pytest.fixture(scope="module")
def chunkcut(tmp_path_factory):
    binary = tmp_path_factory.mktemp("pf-cut") / "chunkcut"
    built = subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-pthread", "-o", str(binary),
                            str(C.REPO / "agent/chunkcut.c"), "-l:libzstd.so.1"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert built.returncode == 0, built.stderr
    return binary


@pytest.fixture(scope="module", params=["partclone", "raw"])
def cut(request, cc, chunkcut, tmp_path_factory):
    return cut_disk(tmp_path_factory.mktemp(f"pf-{request.param}"), cc, chunkcut, request.param)


@pytest.fixture(scope="module")
def pcl(cc, chunkcut, tmp_path_factory):
    return cut_disk(tmp_path_factory.mktemp("pf-pcl"), cc, chunkcut, "partclone")


def forged_frame(tmp: Path, img: C.Image, k: int) -> bytes:
    """A frame for chunk k that a hostile neighbour sends: valid zstd of OTHER bytes of the chunk's length, padded with a
    skippable frame to exactly the index's length -- so the stream stays aligned and only the content check stops it."""
    size = min(C.CH, len(img.data) - k * C.CH)
    body = C._frame(tmp, b"\x41" * size, 3)
    pad = img.zlens[k] - len(body)
    assert pad >= 8, "the forged frame must fit in the real one's length"
    return body + struct.pack("<II", 0x184D2A50, pad - 8) + b"\0" * (pad - 8)


class Neighbour:
    """A neighbour cloner as the network shows it: answers "Range: bytes=A-B" of whole frames with the token, 403
    without it. `forge` = the chunks it lies about."""

    def __init__(self, img: C.Image, tmp: Path, forge: set[int] = frozenset(), frames: dict[int, bytes] | None = None):
        frames = {k: (forged_frame(tmp, img, k) if k in forge else img.frame(k)) for k in range(img.n)} | (frames or {})
        self.asked: list[tuple[str, str | None, str]] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):          # noqa: N802
                outer.asked.append((self.client_address[0], self.headers.get(HDR), self.headers.get("Range", "")))
                if self.headers.get(HDR) != TOKEN:
                    self.send_response(403); self.send_header("Content-Length", "0"); self.end_headers(); return
                a, b = (int(x) for x in self.headers["Range"].split("=")[1].split("-"))
                ks = [k for k in range(img.n) if img.offsets[k] >= a and img.offsets[k] + img.zlens[k] - 1 <= b]
                body = b"".join(frames[k] for k in ks)
                self.send_response(206); self.send_header("Content-Length", str(len(body))); self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):  # noqa: D401
                pass

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.http.server_address[1]
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def close(self):
        self.http.shutdown()


def _wrapper(bin_: Path, cc: Path) -> None:
    """`chunkcast` as the agent calls it: every argv recorded, and the real one run with it."""
    (bin_ / "chunkcast").write_text(
        f"#!{BASH}\n"
        'echo "$*" >> "$RUN_DIR/cc.argv"\n'
        f'exec {posix(cc / "chunkcast")!r} "$@"\n', newline="\n")
    (bin_ / "chunkcast").chmod(0o755)


def needy(tmp: Path, cc: Path, cut, port: int, *, missing="0-4", b3=True, instruction: dict | None = None,
          env: dict | None = None) -> dict:
    """The cloner that lacks: drawer sda, its list, the server's instruction, and one `peerfill_run`."""
    img, _disk = cut
    run, dev, bin_ = tmp / "run", tmp / "dev", tmp / "bin"
    for d in (run / "chunk", dev, bin_):
        d.mkdir(parents=True, exist_ok=True)
    idx = run / "chunk" / "p3.idx"
    idx.write_bytes(img.idx.read_bytes())
    target = img.blank(dev / "sda3")
    (run / "targets" / "sda").mkdir(parents=True)
    (run / "targets" / "sda" / "state").write_text("writing\n")
    (bin_ / "blockdev").write_text(FAKE_BLOCKDEV, newline="\n")
    (bin_ / "blockdev").chmod(0o755)
    _wrapper(bin_, cc)
    lst = tmp / "missing"
    lst.write_text(missing + "\n")
    ins = {"id": ID, "dev": "sda", "part": 3, "ip": "127.0.0.1", "port": port, "me": "127.0.0.1", "token": TOKEN,
           "ranges": [[0, img.n - 1]], "rate_mibs": 100, "until_s": 120, **(instruction or {})}
    (tmp / "ins.json").write_text(json.dumps(ins))
    args = f"--index {posix(idx)} --index-xxh64 {img.index_xxh} " + \
        ("--raw" if img.kind == "raw" else f"--head {posix(img.head_z)}")
    if b3:                                    # #3332: as chunkb3.sh leaves it -- the file in CHUNK_DIR, the flags in the args
        listed, digest = B3.b3_list(cc, img, run / "chunk")
        args += f" --b3-file {posix(listed)} --b3-file-hash {digest}"
    script = (f"export RUN_DIR={posix(run)!r} DEVROOT={posix(dev)!r} CHUNK_DIR={posix(run / 'chunk')!r} "
              f"CHUNK_IDX={posix(idx)!r} CHUNK_IMAGE_ARGS={args!r} NF_LEVEL=3 NF_PORT={C.free_port()} NF_BATCH=1 "
              f"CHUNKCAST_ALLOW_FILE=1 PATH={posix(bin_)!r}:{posix(cc)!r}:\"$PATH\"; "
              + agent_sources("common", "waits", "restore", "smart", "neighborfill", "peerfill")
              + f"peerfill_run sda {posix(lst)!r} {posix(tmp / 'ins.json')!r}; echo \"rc=$?\"")
    began = time.monotonic()
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True, encoding="utf-8", timeout=240,
                          stdin=subprocess.DEVNULL, env={**os.environ, **(env or {})})
    pf = run / "chunk" / "pf.sda"
    read = lambda name: (pf / name).read_text() if (pf / name).exists() else ""   # noqa: E731
    return {"rc": proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "", "out": proc.stdout + proc.stderr,
            "log": (run / "agent.log").read_text(encoding="utf-8") if (run / "agent.log").exists() else "",
            "left": lst.read_text().split(), "target": target.read_bytes(), "result": read("result").strip(),
            "argv": (run / "cc.argv").read_text() if (run / "cc.argv").exists() else "", "took": time.monotonic() - began,
            "curl_left": (pf / "peer.curl").exists()}


# --- the neighbour is not trusted ------------------------------------------------------------------------------------


def test_a_forged_chunk_from_a_neighbour_is_refused_and_stays_missing(tmp_path, cc, cut):
    """הלב של B29: השכן מוסר 0-4, אבל חתיכה 2 שלו מזויפת (zstd תקין של תוכן אחר, באורך המדויק). `apply` של המשכפל
    החסר, עם האינדקס של השרת, דוחה אותה לפני שנכתב בית: במקומה במגירה נשאר מה שהיה, היא נשארת ברשימה ל-HTTP, וה-result
    לשרת הוא `bad` -- כדי שהשכן יוצא מהשיבוצים. `apply` עוצר בחתיכה הראשונה שהוא דוחה (מה שאחריה באותו זרם אינו נאמן,
    `chunkcast.c`): 0 ו-1 נכתבות ונקראות בחזרה, ו-2-4 נשארות ל-HTTP."""
    img, _ = cut
    peer = Neighbour(img, tmp_path, forge={2})
    try:
        got = needy(tmp_path, cc, cut, peer.port)
    finally:
        peer.close()
    assert got["rc"] == "rc=1", got["out"] + got["log"]
    assert got["left"] == ["2-4"], got["left"]
    assert got["target"] == img.expected({0, 1}), "not a byte of the forged chunk on the drawer"
    assert got["result"].startswith(f"{ID} bad"), got["result"]
    assert "--b3-file" in got["argv"] and "p3.b3" in got["argv"], got["argv"]
    assert all(tok == TOKEN for _ip, tok, _r in peer.asked) and peer.asked, peer.asked


def test_no_blake3_list_means_nothing_is_read_from_the_neighbour(tmp_path, cc, pcl):
    """תנאי 1 של אגיס: XXH64 לבדו אינו עומד מול שכן זדוני. בלי רשימת BLAKE3 של המחיצה -- אף בקשה לא יוצאת לשכן, הרשימה
    נשארת כולה ל-HTTP, ו-result הוא `none` (לא `bad`: השכן אינו אשם)."""
    peer = Neighbour(pcl[0], tmp_path)
    try:
        got = needy(tmp_path, cc, pcl, peer.port, b3=False)
    finally:
        peer.close()
    assert got["rc"] == "rc=1" and got["left"] == ["0-4"], got["out"] + got["log"]
    assert peer.asked == [], peer.asked
    assert got["result"].startswith(f"{ID} none"), got["result"]
    assert got["target"] == pcl[0].expected(set())


def test_a_chunk_with_the_indexs_xxh64_and_other_bytes_from_a_neighbour_is_stopped_by_blake3(tmp_path, cc, pcl):
    """אגיס, 15/11: השכן שולח בחתיכה 2 תוכן אחר עם ה-XXH64 שבאינדקס, באותו אורך פריים. XXH64 אינו עוצר אותו (נבדק);
    BLAKE3 עוצר: לא בית ממנו במגירה, 2-4 נשארות ל-HTTP (apply עוצר בחתיכה הראשונה שהוא דוחה), והשכן `bad`."""
    img, _ = pcl
    peer = Neighbour(img, tmp_path, frames={2: B3.forged_chunk(cc, img, 2)})
    try:
        got = needy(tmp_path, cc, pcl, peer.port)
    finally:
        peer.close()
    said = got["out"] + got["log"]
    assert "chunk 2 does not match the index (wrong BLAKE3) -- not written" in said, said
    assert got["left"] == ["2-4"], got["left"]
    assert got["target"] == img.expected({0, 1}), "not a byte of the forged chunk on the drawer"
    assert got["result"].startswith(f"{ID} bad"), got["result"]


@pytest.mark.parametrize("bad", [{"ip": "0.0.0.0"}, {"ip": "127.0.0.1; reboot"}, {"me": "localhost"},
                                 {"port": 80}, {"port": "47960 -o /tmp/x"}, {"token": "short"},
                                 {"token": "Z" * 32}, {"rate_mibs": 500}, {"until_s": 0}, {"part": 4}])
def test_an_instruction_that_is_not_the_servers_shape_is_refused_before_anything_is_asked(tmp_path, cc, pcl, bad):
    """כל מה שנכנס ל-curl או לקובץ נבדק קודם: IPv4 ולא 0.0.0.0, פורט 1024-65535, אסימון של 32 hex, קצב 1-100, תפוגה,
    ומחיצה שהאינדקס שלה הוא שכאן. הוראה אחרת = rc=2, הרשימה כמו שהייתה, ואין בקשה לשכן."""
    peer = Neighbour(pcl[0], tmp_path)
    try:
        got = needy(tmp_path, cc, pcl, peer.port, instruction=bad)
    finally:
        peer.close()
    assert got["rc"] == "rc=2", got["out"] + got["log"]
    assert got["left"] == ["0-4"] and peer.asked == []
    assert "the instruction is refused" in got["log"], got["log"]


def test_a_neighbour_that_stops_sending_fails_within_its_ceiling(tmp_path, cc, pcl):
    """תנאי 5: שכן שתקוע (עונה 206 ולא שולח) נחשב נכשל לפי speed-limit (כאן 1MiB/s ל-2 ש'), לא תוקע את המשכפל.
    הרשימה נשארת ל-HTTP, ו-result הוא `bad`."""
    import socket

    ls = socket.socket()
    ls.bind(("127.0.0.1", 0))
    ls.listen(4)
    held = []

    def hang():
        while True:
            try:
                c, _ = ls.accept()
            except OSError:
                return
            held.append(c)
            c.recv(4096)
            c.sendall(b"HTTP/1.1 206 Partial Content\r\nContent-Length: 99999999\r\n\r\n")

    threading.Thread(target=hang, daemon=True).start()
    try:
        got = needy(tmp_path, cc, pcl, ls.getsockname()[1], env={"PF_SPEED_S": "2"})
    finally:
        ls.close()
        for c in held:
            c.close()
    assert got["rc"] == "rc=1" and got["left"] == ["0-4"], got["out"] + got["log"]
    assert got["result"].startswith(f"{ID} bad"), got["result"]
    assert got["took"] < 60, got["took"]


def test_the_token_goes_in_a_file_and_never_in_argv_or_the_log(tmp_path, cc, pcl):
    """האסימון עובר ל-curl ב-`-K` מקובץ 0600, לא ב-argv (ps מראה אותו) ולא ביומן; והקובץ נמחק בסוף."""
    peer = Neighbour(pcl[0], tmp_path)
    try:
        got = needy(tmp_path, cc, pcl, peer.port)
    finally:
        peer.close()
    assert got["rc"] == "rc=0", got["out"] + got["log"]
    assert TOKEN not in got["argv"] and TOKEN not in got["log"] and TOKEN not in got["out"]
    assert not got["curl_left"], "the token file is gone once the fill is over"


# --- the honest path -------------------------------------------------------------------------------------------------


def test_the_chunks_from_a_true_neighbour_are_written_and_read_back(tmp_path, cc, cut):
    """שכן אמיתי (`chunkcast serve` עם --allow, --until-s ו---read-mibs) מוסר את 0-4: כולן נכתבות למקומן, נקראות
    בחזרה מהמגירה, הרשימה מתרוקנת, rc=0 ו-result הוא `ok`."""
    img, disk = cut
    port = C.free_port()
    (tmp_path / "ndisk").write_bytes(disk.read_bytes())
    (tmp_path / "ntok").write_text(TOKEN + "\n")
    (tmp_path / "ntok").chmod(0o600)
    srv = subprocess.Popen([str(cc / "chunkcast"), "serve", "--listen", f"127.0.0.1:{port}", "--allow", "127.0.0.1",
                            "--token-file", str(tmp_path / "ntok"), *C.image_args(img), "--device", str(tmp_path / "ndisk"),
                            "--level", "3", "--until-s", "120", "--read-mibs", "100"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                           env=C.ENV, text=True)
    try:
        assert srv.stdout.readline().strip() == "serving"
        got = needy(tmp_path, cc, cut, port)
    finally:
        srv.kill()
        srv.wait()
    assert got["rc"] == "rc=0", got["out"] + got["log"]
    assert got["left"] == [] and got["target"] == img.expected()
    assert got["result"] == f"{ID} ok", got["result"]
    assert "--interface 127.0.0.1" not in got["argv"]            # curl's, not chunkcast's: below
    assert "5 chunks written from 127.0.0.1 and re-read whole; 0 left for HTTP" in got["log"], got["log"]


# --- the cloner that serves --------------------------------------------------------------------------------------------


def holder(tmp: Path, replies: list[dict], *, state="writing", cap: str | None = None) -> dict:
    """The cloner that holds: drawer sdb, partition 3's arguments, a `chunkcast` that records its argv and serves
    nothing, and `peer_serve_take` on each reply in turn. Then what neighborfill.sh's ceiling is."""
    run, bin_ = tmp / "run", tmp / "bin"
    for d in (run / "chunk", bin_, run / "targets" / "sdb"):
        d.mkdir(parents=True, exist_ok=True)
    (run / "targets" / "sdb" / "state").write_text(state + "\n")
    (run / "chunk" / "p3.args").write_text("--index /x/p3.idx --index-xxh64 0011223344556677 --raw\n")
    if cap is not None:
        (run / "neighborfill.cap").write_text(cap + "\n")
    (bin_ / "chunkcast").write_text("#!/bin/sh\n"
                                    'printf "%s\\n" "$*" >> "$RUN_DIR/cc.argv"\n'
                                    'for a in "$@"; do case "$a" in */pserve/*.token) cp "$a" "$RUN_DIR/token.seen"; stat -c %a "$a" > "$RUN_DIR/token.mode" ;; esac; done\n'
                                    "echo serving; exec sleep 30\n", newline="\n")
    (bin_ / "chunkcast").chmod(0o755)
    calls = ""
    for i, reply in enumerate(replies):
        (tmp / f"reply{i}.json").write_text(json.dumps(reply))
        calls += f"peer_serve_take {posix(tmp / f'reply{i}.json')!r}; echo \"take=$?\"; "
    script = (f"export RUN_DIR={posix(run)!r} DEVROOT={posix(tmp / 'dev')!r} CHUNK_DIR={posix(run / 'chunk')!r} NF_LEVEL=3 "
              f"PATH={posix(bin_)!r}:\"$PATH\"; "
              + agent_sources("common", "waits", "restore", "smart", "neighborfill", "peerfill")
              + calls + 'echo "cap=$(_nf_cap)"; for p in "$RUN_DIR"/pserve/*.pid; do [ -f "$p" ] && kill "$(cat "$p")"; done')
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True, encoding="utf-8", timeout=60,
                          stdin=subprocess.DEVNULL)
    argv = (run / "cc.argv").read_text().splitlines() if (run / "cc.argv").exists() else []
    return {"out": proc.stdout + proc.stderr, "argv": argv,
            "log": (run / "agent.log").read_text(encoding="utf-8") if (run / "agent.log").exists() else "",
            "cap": [l for l in proc.stdout.splitlines() if l.startswith("cap=")],
            "token": (run / "token.seen").read_text().strip() if (run / "token.seen").exists() else None,
            "mode": (run / "token.mode").read_text().strip() if (run / "token.mode").exists() else None}


def _serve(**over) -> dict:
    return {"peer_serve": [{"id": ID, "dev": "sdb", "part": 3, "listen": "10.55.0.22", "port": 7717,
                            "allow": "10.55.0.21", "token": TOKEN, "rate_mibs": 40, "until_s": 300, **over}]}


def _flags(line: str) -> dict:
    words = line.split()
    return {w: words[i + 1] if i + 1 < len(words) else None for i, w in enumerate(words) if w.startswith("--")}


def test_the_neighbour_serves_only_on_the_named_card_to_the_one_asker_with_an_end_and_a_ceiling(tmp_path):
    """תנאים 2–4: `serve` מאזין לכתובת שהשרת נקב בה (לא 0.0.0.0), `--allow` של המשכפל החסר, `--until-s`, ו-`--read-mibs`
    שיורד למה שנשאר מתקרת הקריאות של המשכפל (כאן המוח אמר 30 ב-neighborfill.cap, וביקשו 40). האסימון בקובץ 0600 ולא
    ב-argv; ההוראה פעם אחת לכל id, גם כשהשרת חוזר עליה; ומה שהשכן קורא יורד מתקרת neighborfill.sh."""
    got = holder(tmp_path, [_serve(), _serve()], cap="30")
    serves = [l for l in got["argv"] if l.startswith("serve ")]
    assert len(serves) == 1, got["argv"]
    f = _flags(serves[0])
    assert f["--listen"] == "10.55.0.22:7717" and f["--allow"] == "10.55.0.21", f
    assert f["--until-s"] == "300" and f["--read-mibs"] == "30", f
    assert f["--index-xxh64"] == "0011223344556677" and "--raw" in f, "the partition's image arguments"
    assert TOKEN not in serves[0] and got["token"] == TOKEN and got["mode"] == "600", got
    assert got["cap"] == ["cap=0"], got["cap"]                     # 30 asked of the brain's 30, all of it taken by the serve


@pytest.mark.parametrize("bad,why", [({"listen": "0.0.0.0"}, "0.0.0.0 is every card"),
                                     ({"allow": "any"}, "may ask is not an IPv4"),
                                     ({"token": "x" * 31}, "token is not 32 hex"),
                                     ({"port": 22}, "port is not a port"),
                                     ({"dev": "../sda"}, "not a device name")])
def test_an_instruction_to_serve_that_is_not_the_servers_shape_starts_nothing(tmp_path, bad, why):
    got = holder(tmp_path, [_serve(**bad)])
    assert [l for l in got["argv"] if l.startswith("serve ")] == [], got["argv"]
    assert why in got["log"], got["log"]


def test_a_drawer_that_is_not_writing_or_done_is_not_served(tmp_path):
    got = holder(tmp_path, [_serve()], state="failed")
    assert [l for l in got["argv"] if l.startswith("serve ")] == []
    assert "its state is 'failed'" in got["log"], got["log"]


# --- chunkcast serve --until-s --read-mibs ---------------------------------------------------------------------------


def _serve_proc(cc: Path, tmp: Path, img: C.Image, disk: Path, extra: list[str]) -> subprocess.Popen:
    (tmp / "tok").write_text(TOKEN + "\n")
    return subprocess.Popen([str(cc / "chunkcast"), "serve", "--token-file", str(tmp / "tok"), *C.image_args(img),
                             "--device", str(disk), "--level", "3", *extra],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, env=C.ENV, text=True)


@pytest.mark.parametrize("extra", [["--listen", "127.0.0.1:{p}", "--until-s", "5"],
                                   ["--listen", "127.0.0.1:{p}", "--read-mibs", "5", "--allow", "127.0.0.1"],
                                   ["--listen", "127.0.0.1:{p}", "--until-s", "5", "--read-mibs", "5"],
                                   ["--listen", "0.0.0.0:{p}", "--until-s", "5", "--read-mibs", "5", "--allow", "127.0.0.1"]])
def test_serve_for_a_neighbour_has_an_end_a_ceiling_one_asker_and_one_card_or_it_does_not_start(tmp_path, cc, pcl, extra):
    port = C.free_port()
    srv = _serve_proc(cc, tmp_path, pcl[0], pcl[1], [x.format(p=port) for x in extra])
    out, err = srv.communicate(timeout=30)
    assert srv.returncode == 2 and "serving" not in out, (out, err)
    assert "--until-s and --read-mibs go together" in err, err


def test_serve_ends_by_itself_at_until_s(tmp_path, cc, pcl):
    port = C.free_port()
    began = time.monotonic()
    srv = _serve_proc(cc, tmp_path, pcl[0], pcl[1], ["--listen", f"127.0.0.1:{port}", "--allow", "127.0.0.1",
                                                    "--until-s", "2", "--read-mibs", "100"])
    out, err = srv.communicate(timeout=30)
    assert srv.returncode == 0 and out.strip() == "serving", (out, err)
    assert 1.5 < time.monotonic() - began < 10
    assert "--until-s reached" in err, err


def test_serve_keeps_its_own_read_ceiling(tmp_path, cc, pcl):
    """תנאי 4: התקרה נאכפת אצל המגיש. 5 חתיכות (20MiB) ב-10MiB/s = לפחות 1.6 ש' (הראשונה מיד); ב-100MiB/s -- עשיריות."""
    img, disk = pcl
    port = C.free_port()
    srv = _serve_proc(cc, tmp_path, img, disk, ["--listen", f"127.0.0.1:{port}", "--allow", "127.0.0.1",
                                                "--until-s", "60", "--read-mibs", "10"])
    try:
        assert srv.stdout.readline().strip() == "serving"
        last = img.offsets[4] + img.zlens[4] - 1
        began = time.monotonic()
        first = img.offsets[0]          # whole frames of the index: in a partclone file the head frame comes before chunk 0
        got = subprocess.run(["curl", "-sS", "--fail", "-H", f"{HDR}: {TOKEN}", "-r", f"{first}-{last}", "-o", os.devnull,
                              "-w", "%{http_code} %{size_download}", f"http://127.0.0.1:{port}/"],
                             capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        took = time.monotonic() - began
    finally:
        srv.kill()
        srv.wait()
    assert got.stdout == f"206 {last + 1 - first}", got.stdout + got.stderr
    assert took >= 1.5, took
