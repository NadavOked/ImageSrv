"""#1861 -- every code word about a disk or a machine reaches the operator in Hebrew.

Nadav, 20/10: "the errors the machine finds before (like F1) -- are they all in
Hebrew?". Mostly -- but each family of words is *made* in one place and
*translated* in three (the agent's sh, the native GUI's C, the console's JS), and
every translator falls back to the word itself. A word added where it is made and
missed in one translator reached the screen as ``cable``, ``health_failed``,
``skipped`` or ``client_partial``, and no test fell: ``test_hebrew_screen_strings``
looks for *sentences* of three words, not one code word, and
``test_journal_events_he`` saw only ``journal(conn, "literal")`` on one line.

So every list here is **read from the producer's code**, never typed by hand: a
new reason in smart.sh, a new cause in ata_cause.py, a new target state, a new
``die_local`` line or a new journal event that is not said in Hebrew everywhere
it is shown fails this file by name. The console's rendering is checked in
``reasons_he_1861.test.cjs``.
"""

from __future__ import annotations

import ast
import re
import shlex
import subprocess
from pathlib import Path

import pytest

from server import journal_he, reports
from server.ata_cause import CAUSES
from test_smart import BASH, HEALTH_FAIL, HEALTH_OK, PRELUDE, full, preflight

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "agent" / "lib"
ROUNDS = REPO / "native-gui" / "src" / "screens_rounds.c"
CONSOLE = REPO / "server" / "static" / "console.js"
HEBREW = re.compile("[א-ת]")

needs_bash = pytest.mark.skipif(BASH is None, reason="bash נדרש לסוכן ה-POSIX")


def sh(script: str) -> subprocess.CompletedProcess:
    """test_smart.sh, but read as UTF-8: the Hebrew is the thing under test (the
    locale codec of a Windows station would garble it into a false failure)."""
    return subprocess.run([BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script], capture_output=True,
                          text=True, encoding="utf-8", cwd=str(REPO), stdin=subprocess.DEVNULL, timeout=120)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --- the producers, read from their code ------------------------------------------


def smart_words() -> set[tuple[str, str]]:
    """Every (verdict, reason) smart.sh can store: smart_classify's echoes, every
    _smart_store with literal words, and the `set --` that turns ok into ro_unread."""
    text = _read(LIB / "smart.sh")
    pairs = set(re.findall(r'echo "(ok|fail|warn|unchecked) (\w+)"', text))
    pairs |= set(re.findall(r'_smart_store "\$1" (ok|fail|warn|unchecked) (\w+)', text))
    pairs |= set(re.findall(r'set -- (ok|fail|warn|unchecked) (\w+)', text))
    return pairs


def reason_words() -> list[str]:
    """What can sit in a `reason` the operator is shown: smart.sh's words and the
    server's cause of a failed clone (failed_last carries it, smartgate.sh), and the
    orange words of #1859 (smart.sh SMART_WARN_REASONS)."""
    return sorted({r for _, r in smart_words() if r != "ok"} | set(CAUSES) | set(warn_words()))


def warn_words() -> list[str]:
    """#1859: SMART_WARN_REASONS, the orange words smartattrs.sh and the server's learning say."""
    m = re.search(r'^SMART_WARN_REASONS="([^"]*)"', _read(LIB / "smart.sh"), re.M)
    return m.group(1).split() if m else []


def target_states() -> list[str]:
    """Every state word the agent writes for a target (progress.sh target_set /
    fail_written_target, and target_init's waiting)."""
    found = {"waiting"}   # target_init (progress.sh)
    for path in LIB.glob("*.sh"):
        for m in re.finditer(r'\btarget_set "?\$[\w{}]+"? "?([a-z_]+)"?', _read(path)):
            found.add(m.group(1))
    found.add("failed")   # fail_written_target
    return sorted(found)


def test_the_scans_see_every_family_they_were_written_for():
    reasons = set(reason_words())
    assert {"health_failed", "uncorrectable", "pending_sectors", "no_health", "readonly",
            "no_smartctl", "timeout", "open_failed", "no_smart", "ro_unread"} <= reasons, reasons
    assert set(CAUSES) <= reasons
    # #2042 (Nadav 23/10): no "replacing" -- it came only from "replace" on an orange disk's question
    assert {"skipped", "writing", "verifying", "cancelled", "done"} <= set(target_states())


# --- the agent (sh) -------------------------------------------------------------------


@needs_bash
@pytest.mark.parametrize("reason", reason_words())
def test_the_agent_says_every_reason_in_hebrew(reason):
    out = sh(PRELUDE + f"smart_reason_he {reason}")
    assert out.returncode == 0, out.stderr
    assert HEBREW.search(out.stdout) and reason not in out.stdout, f"{reason!r} -> {out.stdout!r}"
    assert out.stdout != "סיבה לא מוכרת", f"{reason!r} has no words of its own in smart_reason_he"


@needs_bash
@pytest.mark.parametrize("cause", [c for c in CAUSES if c != "readonly"])   # read-only is red: never asked (#1742)
def test_the_note_on_a_disk_that_failed_last_time_carries_no_raw_word(tmp_path, cause):
    """smartgate.sh: "המפעיל דילג (failed_last: cable)" was the line the drawer card,
    the console's "נכשל בסבב — …" and the failure screen all showed. #2042 (Nadav 23/10):
    orange is not asked any more -- the line is the note a done drawer carries."""
    _, run, err = preflight(tmp_path, full(HEALTH_OK), failed={"sda": cause})
    line = (run / "targets/sda/smart_note").read_text(encoding="utf-8")
    assert "failed_last" not in line and not re.search(rf"\b{cause}\b", line), line
    assert "נכשל בשיכפול הקודם" in line, line


@needs_bash
@pytest.mark.parametrize("body,word,he", [
    (full(HEALTH_FAIL), "health_failed", "בדיקת הבריאות נכשלה"),
    # #1921 (Nadav 20/10): 197/198 are orange now -- asked, never refused; their words are said by
    # test_the_agent_says_every_reason_in_hebrew like every other orange word.
])
def test_the_refusal_of_a_red_disk_says_why_in_hebrew(tmp_path, body, word, he):
    _, run, err = preflight(tmp_path, body)
    line = (run / "targets/sda/error").read_text(encoding="utf-8")
    assert word not in line and he in line, (line, err)


@needs_bash
def test_the_failure_screen_never_shows_a_target_state_word(tmp_path):
    """failscreen.sh's drawer rows: a state with no pill of its own was printed raw
    (`off|writing`). Every state the agent writes is given here at once."""
    from test_failure_screen_1569 import run_agent, values
    states = target_states()
    targets = {f"sd{chr(ord('a') + i)}": (s, "") for i, s in enumerate(states)}
    r = run_agent(tmp_path, 'ui_error_hold "no drawer completed" hold_beat', targets=targets)
    pills = [row.rsplit("|", 1)[-1] for row in values(r["recs"], "fail_row")]
    assert len(pills) == len(states), r["recs"]
    for pill in pills:
        assert HEBREW.search(pill) and pill not in states, pills


def die_local_lines() -> list[str]:
    """Every line die_local can be handed: the literals, the dynamic ones with their
    variables filled, and sr_refused's English (srgui.sh hands it on)."""
    lines: set[str] = set()
    for path in [REPO / "agent" / "imagectl-agent", *LIB.glob("*.sh")]:
        text = _read(path)
        for m in re.finditer(r'\b(?:die_local|sr_refused "?[\w$-]+"?) "([^"]*)"', text):
            raw = m.group(1)
            if re.fullmatch(r"\$\w+", raw):
                continue   # srgui.sh: die_local "$2" -- sr_refused's line, collected above
            if "$_sr_why" in raw:   # single_restore.sh: the red disk's two whys
                lines |= {raw.replace("$_sr_disk", "sda").replace("$_sr_why", w)
                          for w in re.findall(r'_sr_why="([^"]+)"', text)}
                continue
            lines.add(re.sub(r"\$\{[^}]*\}|\$\w+", "3", raw))
    return sorted(lines)


def _fatal_he(lines: list[str]) -> list[str]:
    script = f". {LIB.as_posix()}/failscreen.sh; " + "; ".join(
        f"fail_fatal_he {shlex.quote(x)}; echo" for x in lines)
    out = sh(script)
    assert out.returncode == 0, out.stderr
    return out.stdout.splitlines()


@needs_bash
def test_every_die_local_line_has_its_own_hebrew_sentence():
    """die_local's screen (#1569): a line nobody mapped fell to "המחשב נעצר: <English>"."""
    lines = die_local_lines()
    assert {"MAC not registered", "manifest fetch failed", "restore: sda failed SMART", "restore: sda is read-only -- never written (#1732)",
            "room target selection is missing"} <= set(lines), lines
    default, = _fatal_he(["a line nobody translated"])
    for line, he in zip(lines, _fatal_he(lines)):
        assert HEBREW.search(he) and he != default, f"{line!r} -> {he!r}"
        english = {w for w in re.findall(r"[A-Za-z]{4,}", line)} - {"SMART"}
        assert not any(w in he for w in english), f"{line!r} -> {he!r}"


@needs_bash
def test_an_unmapped_fatal_line_is_said_without_its_english():
    """The raw line stays in fail_code (the technical details), and nowhere else."""
    he, = _fatal_he(["a line nobody translated"])
    assert HEBREW.search(he) and "nobody" not in he, he


# --- the native GUI (C) ----------------------------------------------------------------


def _c_words(function: str) -> set[str]:
    text = _read(ROUNDS)
    start = re.search(r"\nconst char \*" + function + r"\(const char \*(\w+)\) \{", text)
    assert start, f"{function}() is gone from screens_rounds.c"
    body = text[start.end():text.index("\n}\n", start.end())]
    return set(re.findall(r'!strcmp\(' + start.group(1) + r', "(\w+)"\)', body))


def test_the_native_gui_has_words_for_every_reason():
    missing = set(reason_words()) - _c_words("smart_reason_text")
    assert not missing, f"smart_reason_text passes these through raw: {sorted(missing)}"


def test_the_native_gui_has_words_for_every_verdict():
    verdicts = {v for v, _ in smart_words()} | {"failed_last", "passed", "pending"}
    missing = verdicts - _c_words("smart_he") - {"unchecked"}   # unchecked is the first test, with ""
    assert not missing, f"smart_he passes these through raw: {sorted(missing)}"


# --- the console (JS): the maps; the rendering is in reasons_he_1861.test.cjs -----------


def _js_map(name: str) -> dict[str, str]:
    block = re.search(r"const " + name + r" = \{([^}]*)\}", _read(CONSOLE))
    assert block, f"{name} is gone from console.js"
    return dict(re.findall(r'(\w+):\s*"([^"]+)"', block.group(1)))


def test_the_console_has_words_for_every_reason():
    said = set(_js_map("SMART_REASON_HE")) | set(_js_map("FAILURE_CAUSE_HE")) | set(_js_map("SMART_UNCHECKED_HE"))
    if "SMART_WARN_HE" in _read(CONSOLE):   # #1859
        said |= set(_js_map("SMART_WARN_HE"))
    assert not set(reason_words()) - said, sorted(set(reason_words()) - said)


def test_the_console_has_words_for_every_target_state():
    missing = set(target_states()) - set(_js_map("DRAWER_STATE_HE"))
    assert not missing, f"DRAWER_STATE_HE shows these raw: {sorted(missing)}"


# --- the journal (server) --------------------------------------------------------------


def journal_events() -> dict[str, str]:
    """Every event the server journals, by AST -- the literal scan of
    test_journal_events_he missed a call split over two lines, an f-string, an
    `event=` keyword and a parameter's default (#1801)."""
    found: dict[str, str] = {}
    fstrings: set[str] = set()
    for path in sorted((REPO / "server").rglob("*.py")):
        tree = ast.parse(_read(path))
        where = str(path.relative_to(REPO))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
                if name == "journal" and len(node.args) >= 2:
                    ev = node.args[1]
                    if isinstance(ev, ast.Constant) and isinstance(ev.value, str):
                        found[ev.value] = f"{where}:{node.lineno}"
                    elif isinstance(ev, ast.JoinedStr):
                        fstrings.add(ast.unparse(ev))
                for kw in node.keywords:
                    if kw.arg == "event" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        found[kw.value.value] = f"{where}:{node.lineno}"
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args.args[-len(node.args.defaults):] if node.args.defaults else []
                for arg, default in zip(args, node.args.defaults):
                    if arg.arg == "event" and isinstance(default, ast.Constant) and isinstance(default.value, str)                             and default.value:   # "" is a query filter's default (console_api.py), not an event
                        found[default.value] = f"{where}:{node.lineno}"
    # An f-string event is expanded over the values it can take. A new one fails
    # here until it is listed with its values -- it is not skipped.
    expand = {"f'client_{state}'": [f"client_{s}" for s in reports.TERMINAL]}
    unknown = fstrings - set(expand)
    assert not unknown, f"journal event built by an f-string with no known values: {sorted(unknown)}"
    for f in fstrings:
        for ev in expand[f]:
            found.setdefault(ev, f"f-string {f}")
    return found


def test_the_journal_scan_sees_the_events_the_literal_scan_missed():
    found = journal_events()
    assert {"client_partial", "session_autoclose", "shrink_close", "image_manifest_rejected"} <= set(found)
    assert len(found) > 200, len(found)


def test_every_journal_event_has_a_hebrew_label():
    missing = {ev: where for ev, where in journal_events().items() if ev not in journal_he.EVENTS_HE}
    assert not missing, "journal events with no Hebrew label (the operator reads the key): " + ", ".join(
        f"{ev} ({where})" for ev, where in sorted(missing.items()))


def test_a_partial_client_reads_like_its_siblings():
    """#1801: client_partial's label and detail are said like client_done/client_failed's."""
    tr = journal_he.JournalTranslator.__new__(journal_he.JournalTranslator)
    tr._machines, tr._groups, tr._images, tr._sessions = {}, {}, {}, {}   # no names to resolve here
    detail = "AA:BB:CC:DD:EE:01 in ses_1a2b3c — sdb: הכתיבה נכשלה"   # reports.py:274
    for event in ("client_failed", "client_partial"):
        label, text = tr.translate(event, detail)
        assert HEBREW.search(label) and event not in label, (event, label)
        assert " in " not in text and "ses_" not in text, (event, text)
