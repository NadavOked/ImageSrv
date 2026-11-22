"""#3802 (follow-up to #3548): a scan that keeps `( ... ) &` from coming back without `bg_detach_fds`.

The mechanism (tests/test_gap_subshell_fd_3524.py, tests/test_bg_fds_3548.py): a background subshell started from inside a `$( )` capture, after a
redirected call (`fn >&2`), inherits the capture's pipe on fd 10+, and `$( )` waits for the job to end. The one fix is `bg_detach_fds` (bgfds.sh), the
first thing in the job. #3548 put it in six places; this test makes the other background subshells of `agent/` a known list.

What it checks, by running a small shell-text scanner (not a grep for one string):
- every `( ... ) &` under `agent/` starts with `bg_detach_fds`, or sits in BASELINE below, keyed by file and function with the number of sites;
- BASELINE is exact: a site that gained `bg_detach_fds` (or went away) must leave the list, so the list only shrinks;
- the scanner itself: marked / unmarked / redirected jobs, a `case` pattern inside the job, a `$( )` inside it, a brace group (not a subshell).

BASELINE is NOT a verdict that those sites are safe. Their entry says only that on 20/11 nobody had shown that the site is reached from inside a
`$( )` (the static chain is unreliable here: calls through variables, `>&2` parking and sourced files) -- #3802 item 2 stays open for them.
`bg_detach_fds` is added to an entry's site only with proof that it is captured (#3802: "never add it by guess").
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1] / "agent"

#: Written as `fn name -> number of unmarked sites`, per file (relative to agent/). Exact.
BASELINE = {
    "init": {"platform_module_expected": 1},
    "lib/capture/capture.sh": {"capture_disk": 2},
    "lib/capture/directchunk.sh": {"direct_chunk_pass": 2},
    "lib/capture/directserve.sh": {"direct_chunk_targeted": 1},
    "lib/core/waits.sh": {"unblock_fifo": 1},
    "lib/gui/failscreen.sh": {"fail_show": 1},
    "lib/hw/netwatch.sh": {"netwatch_idle_start": 2},
    "lib/localclone/lccopy.sh": {"_lc_target_pipeline": 1, "lc_stream": 2},
    "lib/localclone/lctune.sh": {"lc_exp_direct": 1},
    "lib/localclone/localclone.sh": {"lc_run": 1},
    "lib/restore/drawerback.sh": {"_db_start": 1},
    "lib/restore/drawerfill.sh": {"_fl_live": 1},
    "lib/restore/drawers.sh": {"run_restore_drawers": 1},
    "lib/restore/idlecheck.sh": {"_ic_check": 1, "idle_wc_scan": 1, "idle_wc_start": 1},
    "lib/restore/idlelong.sh": {"_il_seg": 1},
    "lib/restore/prodline.sh": {"_pl_start": 1},
    "lib/restore/restore.sh": {"restore_partition": 1},
    "lib/restore/resume.sh": {"_rs_read_mbps": 1, "resume_seed": 2},
    "lib/restore/writecheck.sh": {"write_check": 1},
    "lib/smart/smartselftest.sh": {"smart_selftest_idle_start": 1},
}

REDIRECT = r"(?:\s+\d*[<>]{1,2}&?\s*\S+)*"
AFTER_CLOSE = re.compile(rf"^{REDIRECT}\s*&(?!&)\s*(?:#.*)?$")
FUNC_DEF = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{")
CASE_PATTERN = re.compile(r"^\(?[^\s()]+(?:\|[^\s()]+)*$")


def background_subshells(text: str) -> list[tuple[int, bool]]:
    """(line of the opening paren, starts with bg_detach_fds) for every `( ... ) [redirects] &`.

    A forward scan with a stack of open parens; single and double quotes and comments are skipped, `$( )` and `$(( ))` are
    pushed apart from a subshell's `(`, and a `)` that closes a `case` pattern (one token, no spaces before it on its
    line) does not pop."""
    found: list[tuple[int, bool]] = []
    stack: list[tuple[str, int]] = []            # (kind, offset): kind in "(", "$("
    n = len(text)
    i = 0
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "'":
            j = text.find("'", i + 1)
            i = n if j < 0 else j + 1
            continue
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if c == "#" and (i == 0 or text[i - 1] in " \t\n;|&("):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "$" and text.startswith("(", i + 1):
            stack.append(("$(", i))
            i += 2
            continue
        if c == "(":
            stack.append(("(", i))
        elif c == ")" and stack:
            ls = text.rfind("\n", 0, i) + 1
            le = text.find("\n", i)
            rest = text[i + 1:len(text) if le < 0 else le]
            prefix = re.split(r";;|\bin\b", text[ls:i])[-1].strip()
            if (CASE_PATTERN.match(prefix) and stack[-1][0] == "(" and stack[-1][1] < ls
                    and not AFTER_CLOSE.match(rest)):
                i += 1                           # a case pattern's `)`: it opens nothing
                continue
            kind, start = stack.pop()
            if kind == "(":
                if AFTER_CLOSE.match(rest):
                    body = text[start + 1:i].lstrip()
                    found.append((text.count("\n", 0, start) + 1, body.startswith("bg_detach_fds")))
        i += 1
    return found


def enclosing_function(lines: list[str], lineno: int) -> str:
    for k in range(lineno - 1, -1, -1):
        m = FUNC_DEF.match(lines[k])
        if m:
            return m.group(1)
    return ""


def agent_sources() -> list[Path]:
    files = sorted(AGENT.rglob("*.sh")) + [AGENT / "imagesrv-agent", AGENT / "init"]
    assert len(files) > 100, f"found {len(files)} agent shell files: the scan is looking in the wrong place"
    return files


def unmarked_sites() -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for path in agent_sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.split("\n")
        for lineno, marked in background_subshells(text):
            if not marked:
                fn = enclosing_function(lines, lineno)
                slot = out.setdefault(path.relative_to(AGENT).as_posix(), {})
                slot[fn] = slot.get(fn, 0) + 1
    return out


def test_every_background_subshell_detaches_or_is_listed():
    seen = unmarked_sites()
    new = {f: {fn: c - BASELINE.get(f, {}).get(fn, 0) for fn, c in fns.items() if c > BASELINE.get(f, {}).get(fn, 0)}
           for f, fns in seen.items()}
    new = {f: fns for f, fns in new.items() if fns}
    assert not new, (
        "a `( ... ) &` without bg_detach_fds as its first command (#3548, #3524): started inside `$( )` it holds the capture open until the job ends. "
        "Start the job with `bg_detach_fds`, or -- only with proof it is never reached from a capture -- list it in BASELINE:\n"
        + "\n".join(f"  agent/{f}: {fn} (+{c})" for f, fns in sorted(new.items()) for fn, c in fns.items()))


def test_the_baseline_only_shrinks():
    seen = unmarked_sites()
    stale = {f: {fn: c - seen.get(f, {}).get(fn, 0) for fn, c in fns.items() if c > seen.get(f, {}).get(fn, 0)}
             for f, fns in BASELINE.items()}
    stale = {f: fns for f, fns in stale.items() if fns}
    assert not stale, (
        "BASELINE lists sites that are marked now (or gone): remove them, so the list stays the list of what is still unproven:\n"
        + "\n".join(f"  agent/{f}: {fn} (-{c})" for f, fns in sorted(stale.items()) for fn, c in fns.items()))


@pytest.mark.parametrize("text,want", [
    ("f() {\n    ( bg_detach_fds; work ) &\n}\n", [(2, True)]),
    ("f() {\n    (\n        bg_detach_fds\n        work\n    ) &\n}\n", [(2, True)]),
    ("f() {\n    ( work ) &\n}\n", [(2, False)]),
    ("f() {\n    ( work ) > /dev/null 2>&1 < /dev/null &   # note\n}\n", [(2, False)]),
    ("f() {\n    ( bg_detach_fds; work ) >> \"$L\" 2>&1 &\n}\n", [(2, True)]),
    ("f() {\n    ( work ) \n}\n", []),                                                    # not backgrounded
    ("f() {\n    ( work ) && other\n}\n", []),                                           # && is not &
    ("f() {\n    { work; } &\n}\n", []),                                                # a brace group is not a subshell
    ("f() {\n    x=$(g) &\n    ( work ) &\n}\n", [(3, False)]),                         # $( ) closes without being a site
    ("f() {\n    (\n        case \"$a\" in\n            x|y) work ;;\n            *) : ;;\n        esac\n    ) &\n}\n", [(2, False)]),
    ("f() {\n    ( echo \"a ) b\" # c ) d\n      work ) &\n}\n", [(2, False)]),        # parens in quotes and comments
])
def test_the_scanner(text, want):
    assert background_subshells(text) == want
