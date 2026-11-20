"""B29 (#3064): החוזה בין השרת לסוכן של ההשלמה ממשכפל שכן -- התשובות הן של `server/brain/peer.py` עצמו, והקוד שקורא אותן הוא
`peerwire.sh` האמיתי (`peer_reply_take`).

ב-`shadow` (ברירת המחדל) התשובות אינן נושאות `peer_serve` ו-`peer_fill`, ושום דבר לא קורה בסוכן. ב-`enforce` A מקבל `peer_serve`
ו-`chunkcast serve` עולה עם כתובת השכן ו-`--allow` של החסר; B מקבל `peer_fill` והסוכן שומר אותו לפי ה-serial שהשרת מפתח בו.

**בקרה שלילית:** `agent/lib/restore/peerwire.sh` נמחק ו-`imagesrv-agent` חוזר לבסיס: `peerwire` אינו נטען (ValueError ב-`agent_libs`)
-- הטסט נופל לפני שהסוכן קורא תשובה.
"""

from __future__ import annotations

import json
import subprocess

import pytest

pytest.importorskip("fastapi")

from agentlib import agent_sources  # noqa: E402
from native import requires_native  # noqa: E402
from test_agent import BASH, posix  # noqa: E402
from test_brain_peer_b29 import _busy_server, _enforce, _peer_report, _rejoin, _setup  # noqa: E402
from test_server_room import room_server  # noqa: E402,F401

pytestmark = requires_native("jq", ("bash", BASH), posix=True, why="the answers are read with jq, as the agent does")

LIBS = ("common", "waits", "restore", "smart", "neighborfill", "peerfill", "peerwire")


def _agent(tmp_path, replies: dict[str, dict]) -> tuple[dict, str]:
    run, bin_ = tmp_path / "r", tmp_path / "bin"
    for dev in ("sda", "sdb"):                       # the server names the neighbour's drawer by the dev of its hello
        (run / "targets" / dev).mkdir(parents=True, exist_ok=True)
        (run / "targets" / dev / "state").write_text("writing\n")
    for d in (run / "chunk", bin_):
        d.mkdir(parents=True, exist_ok=True)
    (run / "chunk" / "p3.args").write_text("--index /x/p3.idx --index-xxh64 0011223344556677 --raw\n")
    (bin_ / "chunkcast").write_text('#!/bin/sh\necho "$*" >> "$RUN_DIR/cc.argv"\necho serving\nexec sleep 30\n', newline="\n")
    (bin_ / "chunkcast").chmod(0o755)
    calls = ""
    for name, reply in replies.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(reply))
        calls += f"peer_reply_take {posix(tmp_path / f'{name}.json')!r}; "
    script = (f"export RUN_DIR={posix(run)!r} DEVROOT={posix(tmp_path / 'dev')!r} CHUNK_DIR={posix(run / 'chunk')!r} NF_LEVEL=3 "
              f"PATH={posix(bin_)!r}:\"$PATH\"; " + agent_sources(*LIBS) + calls
              + 'for p in "$RUN_DIR"/pserve/*.pid; do [ -f "$p" ] && kill "$(cat "$p")"; done')
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True, encoding="utf-8", timeout=60, stdin=subprocess.DEVNULL)
    argv = (run / "cc.argv").read_text().splitlines() if (run / "cc.argv").exists() else []
    return ({"argv": argv, "run": run, "log": (run / "agent.log").read_text(encoding="utf-8") if (run / "agent.log").exists() else ""},
            proc.stdout + proc.stderr)


def test_in_shadow_the_servers_answers_start_and_keep_nothing_in_the_agent(room_server, tmp_path):
    wave = _setup(room_server)
    _busy_server(room_server)
    needy, holder = _rejoin(room_server, wave), _peer_report(room_server, wave)
    assert not {"peer_serve", "peer_fill"} & (set(needy) | set(holder))
    got, out = _agent(tmp_path, {"a": holder, "b": needy})
    assert got["argv"] == [] and not (got["run"] / "pserve").exists() and not (got["run"] / "pfill").exists(), out


def test_the_agent_reads_what_the_real_server_writes_in_enforce(room_server, tmp_path):
    wave = _setup(room_server)
    _busy_server(room_server)
    _enforce(room_server)
    _rejoin(room_server, wave)
    reply_a = _peer_report(room_server, wave)
    reply_b = _rejoin(room_server, wave)
    assert "peer_serve" in reply_a and "peer_fill" in reply_b, (reply_a, reply_b)
    got, out = _agent(tmp_path, {"a": reply_a, "a-again": reply_a, "b": reply_b})
    serves = [ln for ln in got["argv"] if ln.startswith("serve ")]
    assert len(serves) == 1, out + got["log"]                       # the server repeats its order: once is enough
    words = serves[0].split()
    assert words[words.index("--listen") + 1].startswith("10.55.0.22:"), serves
    assert words[words.index("--allow") + 1] == "10.55.0.21", serves
    assert reply_a["peer_serve"][0]["token"] not in serves[0], "the token is in a file, not in argv"
    kept = json.loads((got["run"] / "pfill" / "A1.json").read_text())
    assert kept["id"] == reply_a["peer_serve"][0]["id"] == reply_b["peer_fill"]["A1"]["id"], "one pairing, both sides"
    assert kept["me"] == "10.55.0.21" and kept["ip"] == "10.55.0.22" and kept["ranges"] == [[0, 29]], kept
