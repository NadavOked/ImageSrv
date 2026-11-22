"""#3031 -- the agent log sent to the server: no progress frames, and a truthful `truncated`.

On 12/11 six logs of cloner 2 were 84-99% partclone progress frames (ESC[A + CR redraws),
the start of the wave was cut off by the agent's own tail, and `agent_logs.truncated` was 0
in all of them: the server saw only its own cut (the tail was already 64KB when it got it).

Two sides:
  * the agent (`agent_log_prepare` / `agent_log_compact`, datareport.sh): the frames are
    removed before the tail, the LAST frame stays once, and `X-Imagesrv-Log-Cut` says whether
    the agent cut -- 1 also when the count, or the removal itself, failed;
  * the server (`logs.receive`, `POST /api/v1/agent/log`): `truncated` = its own cut OR the
    agent's, and a missing or unreadable header is "not known", counted as a cut.

Real here: the real datareport.sh with the real tr/awk/tail/gzip; only `curl` is a stub that
keeps its arguments and the body. The server side is the real receive / endpoint.

Negative control: datareport.sh, logs.py and api.py back to the base (5fa4ccc78) -- see the PR.
"""

from __future__ import annotations

import gzip
import re
from pathlib import Path

import pytest

from agentlib import flat_lib
from native import requires_native
from server import db
from server.agent import logs as agent_logs, registry
from test_agent import BASH, posix, sh

LIB = flat_lib()
MAC = "aa:bb:cc:dd:30:31"
ESC = "\x1b"
# #3125 raised the agent's ceilings (400 lines / 64KB -> 2000 / 256KB): read from datareport.sh, not copied.
_DR = (Path(__file__).resolve().parent.parent / "agent" / "lib" / "core" / "datareport.sh").read_text(encoding="utf-8")
LINES = int(re.search(r'AGENT_LOG_LINES="\$\{AGENT_LOG_LINES:-(\d+)\}"', _DR).group(1))
CAP = int(re.search(r'AGENT_LOG_BYTES="\$\{AGENT_LOG_BYTES:-(\d+)\}"', _DR).group(1))


def frames(n: int, start: int = 0) -> str:
    """What partclone draws: two lines per update, CR before, ESC[A (up two lines) after."""
    out = []
    for i in range(start, start + n):
        pct = 100.0 * i / max(n + start, 1)
        out.append(f"\rElapsed: 00:{i // 60:02d}:{i % 60:02d}, Remaining: 00:10:00, Completed: {pct:6.2f}%, 3.2GB/min,"
                   f"\rcurrent block: {i * 4096:10d}, total block: {99999999:10d}, Complete: {pct:6.2f}%{ESC}[A{ESC}[A")
    return "".join(out)


def info(i: int) -> str:
    return f"[10:{i // 60:02d}:{i % 60:02d}] sda: step {i} of the wave\n"


def send(tmp_path: Path, log_text: str, *, fake_tools: dict[str, str] | None = None):
    """agent_log_send with a stub curl; `fake_tools` = name -> the script of a tool that replaces the real one."""
    run = tmp_path / "run"
    (run / "targets" / "sda").mkdir(parents=True)
    (run / "targets" / "sda" / "state").write_text("done\n", encoding="utf-8", newline="\n")
    log = run / "agent.log"
    log.write_bytes(log_text.encode("utf-8"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tools = {"curl": (f'for a in "$@"; do echo "$a"; done > {posix(tmp_path)}/curl.args\n'
                      f'for a in "$@"; do case "$a" in @*) cp "${{a#@}}" {posix(tmp_path)}/body.gz ;; esac; done\nexit 0\n')}
    tools.update(fake_tools or {})
    for name, body in tools.items():
        f = bin_dir / name
        f.write_text("#!/bin/sh\n" + body, encoding="utf-8", newline="\n")
        f.chmod(0o755)
    out = sh(f'export PATH="$(cd {posix(bin_dir)!r} && pwd):$PATH" RUN_DIR={posix(run)!r} LOG_FILE={posix(log)!r}'
             f' SERVER=http://10.0.0.1:7712 MAC={MAC}; . {posix(LIB)}/common.sh;'
             f' . {posix(LIB)}/datareport.sh; agent_log_send ses_3031; echo "rc=$?"')
    assert "rc=0" in out, out
    args = (tmp_path / "curl.args").read_text(encoding="utf-8").splitlines() if (tmp_path / "curl.args").exists() else []
    body = gzip.decompress((tmp_path / "body.gz").read_bytes()).decode("utf-8") if (tmp_path / "body.gz").exists() else None
    return body, args, log.read_text(encoding="utf-8", errors="replace")


def cut_header(args: list[str]) -> str:
    [h] = [a for a in args if a.startswith("X-Imagesrv-Log-Cut:")]
    return h.split(":", 1)[1].strip()


pytestmark_agent = requires_native(("bash", BASH))


# --- the agent ---------------------------------------------------------------------------------


@pytestmark_agent
def test_hundreds_of_kb_of_progress_do_not_push_out_the_start_of_the_wave(tmp_path):
    """The Issue's definition of done: a log with hundreds of KB of progress and 100 info lines --
    every info line arrives, in order, from the very first, and the flag says nothing was cut."""
    text = "".join(info(i) + frames(60, i * 60) for i in range(100))
    assert len(text.encode()) > 500 * 1024, len(text)
    body, args, _ = send(tmp_path, text)
    got = [ln for ln in body.splitlines() if ln.startswith("[10:") and "of the wave" in ln]
    assert got == [info(i).strip() for i in range(100)]
    assert len(body.encode()) <= CAP
    assert body.count("Completed:") == 1 and body.count("Complete:") == 1   # one frame, the last
    assert "(last partclone progress frame)" in body.splitlines()[-1]
    assert cut_header(args) == "0"


@pytestmark_agent
def test_an_info_line_glued_behind_a_frame_survives(tmp_path):
    """The Issue's sample: `...Complete: 36.68%[A[12:52:15] sda: 40 kernel lines kept` -- the line
    has no newline of its own, ESC[A stands between."""
    text = (info(1) + frames(3) + "\rcurrent block: 5, total block: 9, Complete:  36.68%" + ESC + "[A"
            + "[12:52:15] sda: 40 kernel lines kept\n" + info(2))
    body, _, _ = send(tmp_path, text)
    assert "[12:52:15] sda: 40 kernel lines kept" in body.splitlines()
    assert not any(ln.startswith("[A") or ESC in ln for ln in body.splitlines())
    assert body.splitlines().index(info(1).strip()) < body.splitlines().index("[12:52:15] sda: 40 kernel lines kept")


@pytestmark_agent
def test_a_bracketed_word_at_the_start_of_a_line_is_not_taken_for_an_escape(tmp_path):
    """Only what follows an ESC is stripped: `[x] ...` of the agent's own is left alone."""
    body, _, _ = send(tmp_path, "[x] a plain line\n" + info(3))
    assert body.splitlines()[0] == "[x] a plain line"


@pytestmark_agent
def test_a_log_past_the_ceiling_after_the_removal_says_the_agent_cut(tmp_path):
    """Negative: LINES + 200 info lines are still more than the LINES the agent sends -- the start IS cut,
    and the header says so (it said nothing before: truncated=0)."""
    body, args, _ = send(tmp_path, "".join(info(i) for i in range(LINES + 200)))
    assert len(body.splitlines()) == LINES and info(LINES + 199).strip() == body.splitlines()[-1]
    assert cut_header(args) == "1"


@pytestmark_agent
def test_a_log_past_the_byte_ceiling_after_the_removal_says_the_agent_cut(tmp_path):
    """Few lines, many bytes: the byte ceiling cuts, the header says it."""
    body, args, _ = send(tmp_path, "".join(f"[10:00:00] {'x' * 900} {i}\n" for i in range(CAP // 900 + 50)))
    assert len(body.encode()) <= CAP
    assert cut_header(args) == "1"


@pytestmark_agent
def test_a_log_that_fits_exactly_is_not_marked_cut(tmp_path):
    body, args, _ = send(tmp_path, "".join(info(i) for i in range(LINES)))
    assert len(body.splitlines()) == LINES and cut_header(args) == "0"


@pytestmark_agent
def test_when_the_removal_itself_fails_the_raw_tail_goes_marked_cut(tmp_path):
    """The check failing is not "nothing to remove": awk dies, the old behaviour (the raw tail)
    is sent -- and the header is 1, and the local log says why."""
    text = info(1) + frames(5) + info(2)
    body, args, local = send(tmp_path, text, fake_tools={"awk": "echo 'awk: cannot run' >&2\nexit 2\n"})
    assert body is not None and info(2).strip() in body
    assert cut_header(args) == "1"
    assert "progress frames NOT removed" in local


@pytestmark_agent
def test_when_the_count_fails_the_header_is_not_zero(tmp_path):
    """wc gives nothing: the agent cannot tell whether it cut -- "not known" is not "whole"."""
    body, args, _ = send(tmp_path, info(1) + info(2), fake_tools={"wc": "exit 1\n"})
    assert info(2).strip() in body
    assert cut_header(args) == "1"


@pytestmark_agent
def test_an_unreadable_log_is_not_delivered_and_the_round_is_unaffected(tmp_path):
    """No log file at all: an empty body is refused by the server (bad_log) -- the agent says so locally and returns 0."""
    run = tmp_path / "run"
    (run / "targets" / "sda").mkdir(parents=True)
    (run / "targets" / "sda" / "state").write_text("done\n", encoding="utf-8", newline="\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text("#!/bin/sh\nexit 22\n", encoding="utf-8", newline="\n")   # the server's refusal of an empty body
    curl.chmod(0o755)
    log = run / "agent.log"
    out = sh(f'export PATH="$(cd {posix(bin_dir)!r} && pwd):$PATH" RUN_DIR={posix(run)!r} LOG_FILE={posix(log)!r}'
             f' SERVER=http://10.0.0.1:7712 MAC={MAC}; . {posix(LIB)}/common.sh;'
             f' . {posix(LIB)}/datareport.sh; agent_log_send ses_3031; echo "rc=$?"; cat {posix(log)}')
    assert "rc=0" in out and "NOT delivered" in out and "progress frames NOT removed" in out
    assert not (run / "agentlog.gz").exists() and not (run / "agentlog.txt").exists()


# --- the server --------------------------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "imagesrv.db")
    registry.add_machine(c, MAC, "cloner-9", "grp_CLONERS", "test")
    return c


def stored(conn, **kw):
    got = agent_logs.receive(conn, MAC, "ses_1", "failed", gzip.compress(kw.pop("text", "[10:00:00] hi\n").encode()),
                             known=True, **kw)
    assert got["ok"], got
    return agent_logs.text_of(conn, got["id"])["truncated"]


def test_the_agent_said_it_did_not_cut_and_the_server_did_not_either(conn):
    assert stored(conn, cut_header="0") is False


def test_the_agent_said_it_cut_the_flag_is_set(conn):
    """The Issue: truncated=0 although the agent cut. The server's text is small -- only the agent knows."""
    assert stored(conn, cut_header="1") is True


@pytest.mark.parametrize("header", [None, "", "yes", "00", " 0", "true"])
def test_a_missing_or_unreadable_header_is_not_known_and_counts_as_cut(conn, header):
    assert stored(conn, cut_header=header) is True


def test_the_servers_own_cut_still_counts_when_the_agent_says_zero(conn):
    big = "".join(f"[10:00:00] line {i:06d} " + "x" * 80 + "\n" for i in range(4000))   # ~390KB > MAX_TEXT (256KB)
    assert stored(conn, text=big, cut_header="0") is True


def test_the_header_reaches_the_flag_through_the_endpoint(server):
    ctx, admin, anon = server["ctx"], server["admin"], server["anon"]
    registry.add_machine(ctx.conn, MAC, "cloner-9", "grp_CLONERS", "test")
    flags = []
    for header in ({"X-Imagesrv-Log-Cut": "0"}, {"X-Imagesrv-Log-Cut": "1"}, {}):
        r = anon.post(f"/api/v1/agent/log?mac={MAC}&session_id=ses_9&reason=round_end",
                      content=gzip.compress(b"[10:00:00] hi\n"), headers={"Content-Type": "application/gzip", **header})
        assert r.status_code == 200, r.text
        flags.append(admin.get(f"/api/console/agent-logs/{r.json()['id']}").json()["truncated"])
    assert flags == [False, True, True]


def test_the_interface_documents_the_header_and_what_truncated_means():
    doc = (Path(__file__).resolve().parent.parent / "docs" / "architecture" / "interfaces.md").read_text(encoding="utf-8")
    assert "X-Imagesrv-Log-Cut" in doc and "agent_log_prepare" in doc
