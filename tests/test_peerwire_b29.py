"""B29 (#3064): החיבור של ההשלמה ממשכפל שכן לזרימה -- `peerwire.sh`, ומה שקורא לו (progress.sh, drawerback.sh).

עד כאן `peer_serve_take`, `peerfill_run` ו-`peerfill_result_json` נטענו ואף אחד לא קרא להן. כאן שני "משכפלים" אמיתיים
במלואם: **A** (המחזיק) מקבל תשובת שרת עם `peer_serve` ומריץ `chunkcast serve` אמיתי; **B** (החסר) מקבל תשובה עם `peer_fill`,
שומר אותה, ובדרך ל-HTTP של המגירה שחזרה (`peerfill_try`) מושך מ-A, כותב דרך `chunkcast apply` של ה-`$CHUNK_IMAGE_ARGS`
(BLAKE3 כלול), וקורא בחזרה. מה שלא הגיע שלם נשאר בקובץ התשובה של `_chunk_fetch` -- והוא ילך ל-HTTP.

המקרים: A ישר; A שולח חתיכה מזויפת (XXH64 שונה, או **אותו** XXH64 ותוכן אחר -- רק BLAKE3 עוצר); צל (השרת אינו אומר דבר ולכן
שום דבר לא קורה ואין אף בקשה לשכן); הוראה שחוזרת בכל תשובה (פעם אחת); הוראה שפגה; הוראה של מחיצה אחרת; אין BLAKE3 (`none`);
והחוזה מול השרת האמיתי ב-`enforce` (התשובות שהסוכן קורא הן אלה שהשרת כותב).

**בקרה שלילית:** `agent/lib/restore/peerwire.sh` נמחק, ו-`peerfill.sh`, `progress.sh`, `drawerback.sh` ו-`imagesrv-agent` חוזרים
לבסיס (הטסטים, ו-NEEDS שלהם, נשארים): `peerwire` אינו נטען (ValueError ב-`agent_libs`: הסוכן אינו טוען אותו) -- כל טסט שמריץ
את הסוכן נופל, והטסטים הסטטיים נופלים על מה שהקבצים אינם אומרים.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

import chunkcast_rig as C
import test_chunk_b3_3332 as B3
from agentlib import agent_sources
from native import requires_native
from test_agent import BASH, posix
# the fixtures and the neighbour of test_peerfill_b29.py: the same cut images, the same hostile neighbour
from test_peerfill_b29 import FAKE_BLOCKDEV, ID, TOKEN, Neighbour, _wrapper, cc, chunkcut, cut, pcl  # noqa: F401

pytestmark = requires_native("gcc", "zstd", "curl", "jq", ("bash", BASH), posix=True,
                             why="chunkcast needs gcc and libzstd; the frames go over curl")

LIBS = ("common", "waits", "restore", "smart", "neighborfill", "peerfill", "peerwire")
SERIAL = "SER-sda"


def _args(chunk: Path, img: C.Image, cc_dir: Path, *, b3: bool = True) -> str:
    """A cloner's CHUNK_IMAGE_ARGS for partition 3, as chunkrecv.sh and chunkb3.sh leave them: the files in its chunk dir."""
    chunk.mkdir(parents=True, exist_ok=True)
    idx = chunk / "p3.idx"
    idx.write_bytes(img.idx.read_bytes())
    args = f"--index {posix(idx)} --index-xxh64 {img.index_xxh} " + ("--raw" if img.kind == "raw" else f"--head {posix(img.head_z)}")
    if b3:
        listed, digest = B3.b3_list(cc_dir, img, chunk)
        args += f" --b3-file {posix(listed)} --b3-file-hash {digest}"
    return args


def _instruction(n: int, port: int, **over) -> dict:
    return {"id": ID, "dev": "sda", "part": 3, "ip": "127.0.0.1", "port": port, "me": "127.0.0.1", "token": TOKEN,
            "ranges": [[0, n - 1]], "rate_mibs": 100, "until_s": 120, **over}


def _serve_order(port: int, **over) -> dict:
    return {"id": ID, "dev": "sdb", "part": 3, "listen": "127.0.0.1", "port": port, "allow": "127.0.0.1", "token": TOKEN,
            "rate_mibs": 100, "until_s": 120, **over}


def _env(run: Path, dev: Path, bin_: Path, cc_dir: Path, args: str, chunk_dir: Path | None = None) -> str:
    chunk = chunk_dir or run / "chunk"
    return (f"export RUN_DIR={posix(run)!r} DEVROOT={posix(dev)!r} CHUNK_DIR={posix(chunk)!r} "
            f"CHUNK_IDX={posix(run / 'chunk' / 'p3.idx')!r} CHUNK_IMAGE_ARGS={args!r} NF_LEVEL=3 NF_PORT={C.free_port()} "
            f"NF_BATCH=1 CHUNKCAST_ALLOW_FILE=1 PATH={posix(bin_)!r}:{posix(cc_dir)!r}:\"$PATH\"; "
            + agent_sources(*LIBS) + 'disk_serial() { echo "SER-$1"; }; ')


class Holder:
    """Cloner A: drawer sdb holds partition 3. `peer_reply_take` on the server's reply starts its frame server (the real
    `chunkcast serve`, or a stand-in that only says it serves, when a hostile neighbour answers on the port instead).
    The shell stays alive: a frame server dies with the shell that started it."""

    def __init__(self, tmp: Path, cc_dir: Path, img: C.Image, disk: Path, replies: list[dict], *, real: bool = True,
                 state: str = "writing"):
        run, bin_, dev = tmp / "a", tmp / "a-bin", tmp / "a-dev"
        for d in (run / "targets" / "sdb", bin_, dev):
            d.mkdir(parents=True, exist_ok=True)
        (run / "targets" / "sdb" / "state").write_text(state + "\n")
        (dev / "sdb3").write_bytes(disk.read_bytes())
        args = _args(run / "chunk", img, cc_dir)
        (run / "chunk" / "p3.args").write_text(args + "\n")
        if real:
            _wrapper(bin_, cc_dir)
        else:
            (bin_ / "chunkcast").write_text('#!/bin/sh\necho "$*" >> "$RUN_DIR/cc.argv"\necho serving\nexec sleep 120\n', newline="\n")
            (bin_ / "chunkcast").chmod(0o755)
        takes = ""
        for i, reply in enumerate(replies):
            (tmp / f"a-reply{i}.json").write_text(json.dumps(reply))
            takes += f"peer_reply_take {posix(tmp / f'a-reply{i}.json')!r}; "
        self.run = run
        script = (_env(run, dev, bin_, cc_dir, args) + takes + f"echo \"take=$?\" > {posix(tmp / 'a.ready')!r}; exec sleep 120")
        # #3908: A's words are kept, not thrown away: a B that is reset by A (the public gate, 3.12, partclone) said only
        # "curl: (56) Recv failure", and A's own reason was nowhere. A file, not a PIPE: nobody reads it while A runs.
        self.out = tmp / "a.out"
        with self.out.open("wb") as sink:
            self.proc = subprocess.Popen([BASH, "-c", script], stdout=sink, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        end = time.monotonic() + 30
        while not (tmp / "a.ready").exists() and time.monotonic() < end:
            assert self.proc.poll() is None, "A's shell ended"
            time.sleep(0.05)
        assert (tmp / "a.ready").exists(), "peer_reply_take did not return"

    def argv(self) -> list[str]:
        f = self.run / "cc.argv"
        return f.read_text().splitlines() if f.exists() else []

    def words(self) -> str:
        """What A said: its shell's output and its agent log."""
        log = self.run / "agent.log"
        return "\n--- A ---\n" + self.out.read_text(encoding="utf-8", errors="replace") + (
            log.read_text(encoding="utf-8", errors="replace") if log.exists() else "(A wrote no agent.log)")

    def close(self):
        self.proc.kill()
        self.proc.wait()


def lacking(tmp: Path, cc_dir: Path, cut_, replies: list[dict], *, b3: bool = True, answer=None, part_ok: bool = True,
            age_s: int = 0, tries: int = 1) -> dict:
    """Cloner B: drawer sda came back and lacks partition 3. Each reply is `peer_reply_take`n (as the reporter does every two
    seconds), then `peerfill_try` runs `tries` times on the answer `_chunk_fetch` would read."""
    img, _disk = cut_
    run, dev, bin_ = tmp / "run", tmp / "dev", tmp / "bin"
    for d in (run / "targets" / "sda", dev, bin_):
        d.mkdir(parents=True, exist_ok=True)
    (run / "targets" / "sda" / "state").write_text("writing\n")
    target = img.blank(dev / "sda3")
    (bin_ / "blockdev").write_text(FAKE_BLOCKDEV, newline="\n")
    (bin_ / "blockdev").chmod(0o755)
    _wrapper(bin_, cc_dir)
    # the drawer's completion runs in a directory of its own (drawerback.sh): not the round's, where the BLAKE3 list is
    back = run / "chunk" / "back.sda"
    back.mkdir(parents=True, exist_ok=True)
    args = _args(run / "chunk", img, cc_dir, b3=b3)
    answer_file = back / "back.answer.json"
    answer_file.write_text(json.dumps({"ranges": answer if answer is not None else [[0, img.n - 1]]}))
    calls = ""
    for i, reply in enumerate(replies):
        (tmp / f"b-reply{i}.json").write_text(json.dumps(reply))
        calls += f"peer_reply_take {posix(tmp / f'b-reply{i}.json')!r}; echo \"take=$?\"; "
    if age_s:
        calls += f'for f in "$RUN_DIR"/pfill/*.at; do [ -f "$f" ] && echo $(( $(date +%s) - {age_s} )) > "$f"; done; '
    for _ in range(tries):
        calls += f'peerfill_try sda 3 {posix(answer_file)!r}; echo "try=$?"; '
    script = (_env(run, dev, bin_, cc_dir, args, back) + f"CHUNK_STATE={posix(back / 'p3.state')!r}; " + calls
              + 'echo "json=$(peerfill_result_json sda)"')
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True, encoding="utf-8", timeout=240,
                          stdin=subprocess.DEVNULL, env={**os.environ})
    lines = proc.stdout.splitlines()
    state = back / "p3.state"
    return {"tries": [ln for ln in lines if ln.startswith("try=")], "out": proc.stdout + proc.stderr,
            "log": (run / "agent.log").read_text(encoding="utf-8") if (run / "agent.log").exists() else "",
            "answer": json.loads(answer_file.read_text()), "target": target.read_bytes(),
            "state": state.read_text() if state.exists() else "",
            "result": (run / "targets" / "sda" / "pf_result").read_text().strip() if (run / "targets" / "sda" / "pf_result").exists() else "",
            "json": next((ln[5:] for ln in lines if ln.startswith("json=")), ""),
            "pfill": sorted(p.name for p in (run / "pfill").glob("*")) if (run / "pfill").exists() else [],
            "argv": (run / "cc.argv").read_text() if (run / "cc.argv").exists() else ""}


def _fill(img: C.Image, port: int, **over) -> dict:
    return {"peer_fill": {SERIAL: _instruction(img.n, port, **over)}}


# --- both cloners, the way the server says it ----------------------------------------------------------------------------


def test_a_serves_what_the_server_told_it_and_b_completes_from_it_and_re_reads(tmp_path, cc, cut):
    """הלב: A שומע `peer_serve` ומגיש (`chunkcast serve` אמיתי, עם --allow, אסימון, --until-s ו---read-mibs). B שומע
    `peer_fill`, וכש-`peerfill_try` רץ על התשובה של `_chunk_fetch` -- חמש החתיכות נכתבות למקומן, נקראות בחזרה, נרשמות בקובץ
    המצב של המגירה (ולכן `_dp_have` של `_db_part` יראה אותן), הקובץ מתרוקן ו-HTTP אינו רץ (1), והתוצאה לשרת היא `ok`."""
    img, disk = cut
    port = C.free_port()
    a = Holder(tmp_path, cc, img, disk, [{"peer_serve": [_serve_order(port)]}])
    try:
        assert (a.run / "pserve" / f"{ID}.started").exists() and a.argv()[0].startswith("serve "), a.argv()
        got = lacking(tmp_path, cc, cut, [_fill(img, port)])
    finally:
        a.close()
    assert got["tries"] == ["try=1"], got["out"] + got["log"] + a.words()
    assert got["answer"] == {"ranges": []}, "nothing is left for HTTP"
    assert got["target"] == img.expected(), "every chunk on the drawer, from the neighbour"
    assert f"ok {img.n} 0-{img.n - 1}" in got["state"], got["state"]
    assert got["result"] == f"{ID} ok", got["result"]
    assert json.loads(got["json"][len(',"peer_fill_result":'):]) == {"id": ID, "result": "ok", "why": ""}, got["json"]
    assert f"{img.n} chunks written from 127.0.0.1 and re-read whole; 0 left for HTTP" in got["log"], got["log"]
    assert TOKEN not in got["log"] and TOKEN not in "".join(a.argv()), "the token is in a file, never in argv or the log"
    assert not list((tmp_path / "run" / "pfill").glob("*.json")), "the token is not kept past its use"


def test_a_takes_the_same_order_once_however_often_the_server_says_it(tmp_path, cc, cut):
    img, disk = cut
    port = C.free_port()
    a = Holder(tmp_path, cc, img, disk, [{"peer_serve": [_serve_order(port)]}] * 2, real=False)
    try:
        assert len(a.argv()) == 1, a.argv()
    finally:
        a.close()
    (tmp_path / "again").mkdir()
    b = Holder(tmp_path / "again", cc, img, disk, [{"peer_serve": [_serve_order(port, id="b" * 16)]}], real=False, state="failed")
    try:
        assert b.argv() == [], "a drawer that failed is not served from"
    finally:
        b.close()


# --- the neighbour is not trusted ------------------------------------------------------------------------------------


def test_a_that_sends_a_forged_chunk_is_refused_and_what_it_did_not_bring_stays_for_http(tmp_path, cc, cut):
    """A מכריז שהוא מגיש (תשובת `peer_serve`), אבל מי שעונה בפורט הוא שכן זדוני ששולח בחתיכה 2 פריים מזויף באורך הנכון.
    0 ו-1 נכתבות; 2 לא נכתבת (לא בית); `2-4` נשארות בקובץ התשובה -- בדיוק מה ש-`_chunk_fetch` יביא מהשרת -- והתוצאה `bad`."""
    img, disk = cut
    hostile = Neighbour(img, tmp_path, forge={2})
    a = Holder(tmp_path, cc, img, disk, [{"peer_serve": [_serve_order(hostile.port)]}], real=False)
    try:
        assert a.argv() and f"--listen 127.0.0.1:{hostile.port}" in a.argv()[0], a.argv()
        got = lacking(tmp_path, cc, cut, [_fill(img, hostile.port)])
    finally:
        a.close()
        hostile.close()
    assert got["tries"] == ["try=0"], got["out"] + got["log"]
    assert got["answer"] == {"ranges": [[2, 4]]}, got["answer"]
    assert got["target"] == img.expected({0, 1}), "not a byte of the forged chunk on the drawer"
    assert got["result"].startswith(f"{ID} bad"), got["result"]
    assert '"result":"bad"' in got["json"], got["json"]
    assert f"ok 2 0-1" in got["state"], got["state"]
    assert all(tok == TOKEN for _ip, tok, _r in hostile.asked) and hostile.asked


def test_a_chunk_with_the_indexs_xxh64_and_other_bytes_is_stopped_by_blake3_on_the_wired_path(tmp_path, cc, pcl):
    """אגיס, 15/11: תוכן אחר עם ה-XXH64 של האינדקס. כאן הנתיב המחובר (BLAKE3 מהארגומנטים של התיקייה של הסבב, כשהמגירה
    רצה בתיקייה משלה): החתיכה לא נכתבת, 2-4 נשארות ל-HTTP, והשכן `bad`."""
    img, _ = pcl
    hostile = Neighbour(img, tmp_path, frames={2: B3.forged_chunk(cc, img, 2)})
    a = Holder(tmp_path, cc, img, pcl[1], [{"peer_serve": [_serve_order(hostile.port)]}], real=False)
    try:
        got = lacking(tmp_path, cc, pcl, [_fill(img, hostile.port)])
    finally:
        a.close()
        hostile.close()
    assert "chunk 2 does not match the index (wrong BLAKE3) -- not written" in got["out"] + got["log"], got["out"] + got["log"]
    assert got["answer"] == {"ranges": [[2, 4]]} and got["target"] == img.expected({0, 1})
    assert got["result"].startswith(f"{ID} bad"), got["result"]


def test_no_blake3_list_means_the_neighbour_is_not_asked_and_the_server_hears_none(tmp_path, cc, pcl):
    """בלי רשימת BLAKE3 בארגומנטים -- אף בקשה לא יוצאת, התשובה כמו שהייתה (הכול ל-HTTP), והתוצאה `none` (לא `bad`)."""
    img, _ = pcl
    peer = Neighbour(img, tmp_path)
    try:
        got = lacking(tmp_path, cc, pcl, [_fill(img, peer.port)], b3=False)
    finally:
        peer.close()
    assert peer.asked == [], peer.asked
    assert got["answer"] == {"ranges": [[0, img.n - 1]]}, got["answer"]
    assert got["result"].startswith(f"{ID} none"), got["result"]


# --- shadow: the server says nothing, and nothing happens ----------------------------------------------------------------


def test_in_shadow_nothing_starts_nothing_is_kept_and_no_neighbour_is_asked(tmp_path, cc, pcl):
    """ברירת המחדל (`brain_peer_mode=shadow`): תשובת השרת אינה נושאת `peer_serve` ולא `peer_fill`. A לא מריץ `chunkcast`,
    B לא שומר דבר, `peerfill_try` לא נוגע בקובץ התשובה, אין בקשה לשום שכן, ואין `peer_fill_result` בדיווח."""
    img, disk = pcl
    peer = Neighbour(img, tmp_path)
    shadow = {"ok": True, "durable_seq": 4, "fill": {"S": {"verdict": "HTTP"}}}
    a = Holder(tmp_path, cc, img, disk, [shadow, shadow], real=False)
    try:
        assert a.argv() == [] and not (a.run / "pserve").exists(), "A started nothing"
        got = lacking(tmp_path, cc, pcl, [shadow, shadow])
    finally:
        a.close()
        peer.close()
    assert got["tries"] == ["try=0"] and got["pfill"] == [] and got["result"] == "" and got["json"] == "", got
    assert got["answer"] == {"ranges": [[0, img.n - 1]]} and peer.asked == []
    assert got["target"] == img.expected(set())


# --- the instruction: once, in time, for its partition ---------------------------------------------------------------------


def test_an_instruction_the_server_repeats_is_tried_once(tmp_path, cc, cut):
    """השרת חוזר על `peer_fill` בכל תשובה, ו-`peerfill_try` נקרא בכל השלמה. הוראה אחת = ניסיון אחד: הקריאה השנייה (והתשובה
    החוזרת) אינן מפעילות שכן שוב -- אפילו כשנשאר חסר."""
    img, _ = cut
    peer = Neighbour(img, tmp_path, forge={2})
    try:
        got = lacking(tmp_path, cc, cut, [_fill(img, peer.port), _fill(img, peer.port)], tries=2)
    finally:
        peer.close()
    assert got["tries"] == ["try=0", "try=0"], got["out"]
    assert got["answer"] == {"ranges": [[2, 4]]}, "the second call left the first one's answer alone"
    asked_once = len(peer.asked)
    assert asked_once >= 1 and got["log"].count("chunks of partition 3 from 127.0.0.1") == 1, got["log"]


def test_an_instruction_that_ended_before_the_drawer_got_to_it_is_not_used_and_blames_no_one(tmp_path, cc, cut):
    """ההוראה נשמרת עם הזמן שהגיעה. מגירה ש"הגיעה" אליה 200 שניות אחרי שהשכן כבר נסגר (`until_s` 120) לא פונה אליו -- הוא
    כבר לא שם, ו-`bad` היה מוציא שכן תקין מכל השיבוצים. התוצאה `none`, הכול ל-HTTP."""
    img, _ = cut
    peer = Neighbour(img, tmp_path)
    try:
        got = lacking(tmp_path, cc, cut, [_fill(img, peer.port)], age_s=200)
    finally:
        peer.close()
    assert peer.asked == [] and got["tries"] == ["try=0"], got["out"] + got["log"]
    assert got["answer"] == {"ranges": [[0, img.n - 1]]}
    assert got["result"].startswith(f"{ID} none the instruction ended"), got["result"]


def test_an_instruction_with_five_seconds_left_is_not_used_and_blames_no_one(tmp_path, cc, cut):
    """הסף ב-`peerfill_try` הוא 10 שניות, לא "פגה". הוראה שנותרו לה 5 (`until_s` 120, גיל 115): שכן שנסגר בעוד רגע לא יספיק להביא
    חתיכה, ו-`bad` היה מאשים אותו. לא פונים אליו, התוצאה `none`, הכול ל-HTTP. (#3782: מוטציית `-lt 0` עברה את הטסט של 200 ש'.)"""
    img, _ = cut
    peer = Neighbour(img, tmp_path)
    try:
        got = lacking(tmp_path, cc, cut, [_fill(img, peer.port)], age_s=115)
    finally:
        peer.close()
    assert peer.asked == [] and got["tries"] == ["try=0"], got["out"] + got["log"]
    assert got["answer"] == {"ranges": [[0, img.n - 1]]}
    assert got["result"].startswith(f"{ID} none the instruction ended"), got["result"]


def test_an_instruction_with_fifteen_seconds_left_is_still_used(tmp_path, cc, cut):
    """הצד השני של הסף (#3782): הוראה שנותרו לה 15 שניות (גיל 105 מתוך 120) עדיין נוצלת. השכן נשאל, והתוצאה אינה `none`."""
    img, _ = cut
    peer = Neighbour(img, tmp_path)
    try:
        got = lacking(tmp_path, cc, cut, [_fill(img, peer.port)], age_s=105)
    finally:
        peer.close()
    assert peer.asked, got["out"] + got["log"]
    assert not got["result"].startswith(f"{ID} none the instruction ended"), got["result"]


def test_an_instruction_for_another_partition_waits_for_its_own_turn(tmp_path, cc, cut):
    img, _ = cut
    peer = Neighbour(img, tmp_path)
    try:
        got = lacking(tmp_path, cc, cut, [_fill(img, peer.port, part=4)])
    finally:
        peer.close()
    assert peer.asked == [] and got["tries"] == ["try=0"] and got["answer"] == {"ranges": [[0, img.n - 1]]}
    assert any(n.endswith(".json") for n in got["pfill"]), "kept, for the partition it is about"
    assert got["result"] == ""


# --- where it is called from ---------------------------------------------------------------------------------------------


def test_the_reporters_loop_hands_every_answer_to_the_neighbour_wiring(tmp_path):
    """`progress_loop` (progress.sh) קורא ל-`peer_reply_take` על `progress.reply` אחרי כל שליחה: תשובה עם `peer_serve` מפעילה
    את `chunkcast serve`, ו-`peer_fill` נשמר. מי שלא טען את `peerwire.sh` ממשיך לדווח כרגיל."""
    run, bin_ = tmp_path / "run", tmp_path / "bin"
    for d in (run / "chunk", bin_, run / "targets" / "sdb"):
        d.mkdir(parents=True, exist_ok=True)
    (run / "targets" / "sdb" / "state").write_text("writing\n")
    (run / "chunk" / "p3.args").write_text("--index /x/p3.idx --index-xxh64 0011223344556677 --raw\n")
    (bin_ / "chunkcast").write_text('#!/bin/sh\necho "$*" >> "$RUN_DIR/cc.argv"\necho serving\nexec sleep 30\n', newline="\n")
    (bin_ / "chunkcast").chmod(0o755)
    reply = {"ok": True, "peer_serve": [_serve_order(48200, listen="10.55.0.22", allow="10.55.0.21")],
             "peer_fill": {"S1": _instruction(5, 48200, ranges=[[0, 4]], me="10.55.0.21", ip="10.55.0.22")}}
    (tmp_path / "reply.json").write_text(json.dumps(reply))
    script = (f"export RUN_DIR={posix(run)!r} DEVROOT={posix(tmp_path / 'dev')!r} CHUNK_DIR={posix(run / 'chunk')!r} NF_LEVEL=3 PROGRESS_INTERVAL_S=0.2 "
              f"PATH={posix(bin_)!r}:\"$PATH\"; " + agent_sources(*LIBS, "sysinfo", "progress", "failmark")
              + f'build_progress() {{ echo "{{}}"; }}; http_post_json() {{ cat {posix(tmp_path / "reply.json")!r}; }}; '
              'ledger_ack() { :; }; cancel_from_reply() { :; }; '
              'progress_loop ses_1 aa:bb:cc:dd:ee:ff http://srv & loop=$!; n=0; '
              'until [ -f "$RUN_DIR/pserve/' + ID + '.pid" ] && [ -f "$RUN_DIR/pfill/S1.id" ] || [ "$n" -ge 200 ]; do sleep 0.1; n=$((n + 1)); done; '
              'kill "$loop"; for p in "$RUN_DIR"/pserve/*.pid; do [ -f "$p" ] && kill "$(cat "$p")"; done')
    subprocess.run([BASH, "-c", script], capture_output=True, text=True, encoding="utf-8", timeout=60, stdin=subprocess.DEVNULL)
    argv = (run / "cc.argv").read_text().splitlines() if (run / "cc.argv").exists() else []
    assert len([ln for ln in argv if ln.startswith("serve ")]) == 1, argv
    assert (run / "pfill" / "S1.id").read_text().strip() == ID


def test_the_report_carries_what_a_neighbour_gave_by_the_drawers_name(tmp_path):
    """`build_progress` נושא `peer_fill_result` של המגירה מהקובץ שבתיקיית ה-target שלה -- לא מהתיקייה שבה ההשלמה רצה."""
    run = tmp_path / "run"
    (run / "targets").mkdir(parents=True)
    script = (f"export RUN_DIR={posix(run)!r}; . {posix(Path(__file__).resolve().parents[1] / 'agent/lib/core/common.sh')}; "
              + agent_sources("sysinfo", "diskinfo", "progress", "failmark", "neighborfill", "peerfill", "waits")
              + 'target_init sda 0; build_progress ses_1 aa:bb:cc:dd:ee:ff; echo; '
                f'echo "{ID} bad the piece from chunk 2 did not arrive whole" > "$RUN_DIR/targets/sda/pf_result"; '
                'build_progress ses_1 aa:bb:cc:dd:ee:ff; echo')
    out = subprocess.run([BASH, "-c", script], capture_output=True, text=True, encoding="utf-8", timeout=60,
                         stdin=subprocess.DEVNULL).stdout.strip().splitlines()
    before, after = (json.loads(ln) for ln in out[-2:])
    assert "peer_fill_result" not in before["targets"][0], "nothing said = no field"
    assert after["targets"][0]["peer_fill_result"] == {"id": ID, "result": "bad", "why": "the piece from chunk 2 did not arrive whole"}


def test_a_drawer_back_asks_the_neighbour_wiring_before_it_fetches_over_http():
    """השער הסטטי של הקריאה ב-`_db_part`: `peerfill_try` לפני `_chunk_fetch`, וברירת המחדל (הפונקציה אינה קיימת, או 0) היא HTTP.
    `_db_part` יושב ב-`drawerfill.sh` מאז #3181 (פס הייצור), ושלב ה-HTTP שלו הוא המקום של הקריאה."""
    src = (Path(__file__).resolve().parents[1] / "agent/lib/restore/drawerfill.sh").read_text(encoding="utf-8")
    assert src.index('peerfill_try "$2" "$3" "$CHUNK_ANSWER"') < src.index('_chunk_fetch "$_db_srv" "$_db_img" "$4" "$3"'), "before the fetch"
    assert 'command -v peerfill_try' in src and '[ "$_dp_skip" = 1 ] || _chunk_fetch' in src, "absent or 0 = fetch, as before"
