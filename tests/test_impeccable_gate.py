"""#2064: שער Impeccable על `server/static`, עם מחגר.

הטסט הראשון הוא השער עצמו: הגלאי האמיתי, בגרסה הנעולה, מול הרשימה
המוכרת. השאר בודקים את מנגנון השער בגלאי מזויף — כדי שהמחגר, בדיקת
ה-sha ומצבי הכישלון יירוצו גם בסביבה שאין בה את הבינארי.

בינארי חסר: דילוג עם סיבה בשמה, ובמקום שהכלים אמורים להיות
(`IMAGESRV_REQUIRE_NATIVE=1`, ה-CI) — כישלון. לא "עבר" (עיקרון 5).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import impeccable_gate as gate
import native


def _fmt(entries: list[dict]) -> str:
    return "\n".join(f"    {e['file']}:{e['line']}  {e['rule']}  {e['snippet']}" for e in entries)


def test_console_has_no_new_impeccable_findings_and_no_stale_ones():
    binary = gate.resolve_binary()
    if binary is None:
        why = f"אין בינארי בסביבה הזו — {gate.ENV_BIN} ריק ו-{gate.LAB_BIN} אינו קיים"
        if native.native_required():
            pytest.fail(f"impeccable (#2064): {native.ENV_FLAG}=1 ו{why}", pytrace=False)
        pytest.skip(f"impeccable (#2064): {why}")
    gate.verify(binary)                       # לפני כל הרצה, לא פעם בהתקנה
    known = gate.load_baseline()
    assert (known["engine"], known["sha256"]) == (gate.ENGINE, gate.SHA256), (
        "הרשימה המוכרת נכתבה מול engine אחר — הצמדה חדשה מחייבת רשימה חדשה")
    current = gate.normalize(gate.run_detect([str(binary)]))
    new, stale = gate.compare(current, known["findings"])
    problems = []
    if new:
        problems.append(f"{len(new)} ממצאי Impeccable חדשים — לתקן את העיצוב, "
                        f"לא להוסיף לרשימה:\n{_fmt(new)}")
    if stale:
        problems.append(f"{len(stale)} ממצאים ברשימה המוכרת כבר אינם — למחוק אותם "
                        f"מ-{gate.BASELINE.name} (המחגר):\n{_fmt(stale)}")
    assert not problems, "\n".join(problems)


# --- המנגנון, בגלאי מזויף --------------------------------------------------

def _finding(file: str, rule: str = "side-tab", snippet: str = "border-inline-start: 4px solid",
             line: int = 1) -> dict:
    return {"antipattern": rule, "name": rule, "description": "", "severity": "warning",
            "category": "slop", "file": file, "line": line, "snippet": snippet}


@pytest.fixture
def fake(tmp_path):
    """גלאי מזויף: כותב stdout ויוצא בקוד שנבחר. מחזיר את ה-cmd ואת הארגומנטים שקיבל."""
    def make(stdout: str, rc: int) -> list[str]:
        script = tmp_path / "fake_detect.py"
        script.write_text(
            "import sys, pathlib\n"
            f"pathlib.Path({str(tmp_path / 'argv.json')!r}).write_text("
            "__import__('json').dumps(sys.argv[1:]))\n"
            f"sys.stdout.write({stdout!r})\nsys.exit({rc})\n",
            encoding="utf-8", newline="\n")
        return [sys.executable, str(script)]
    return make


def test_detect_runs_only_the_local_detector_with_config_and_inline_ignores_off(fake, tmp_path):
    gate.run_detect(fake("[]", 0), target=tmp_path)
    argv = json.loads((tmp_path / "argv.json").read_text(encoding="utf-8"))
    assert argv == ["detect", "--json", "--no-config", str(tmp_path)]


def test_findings_are_parsed_on_exit_2(fake, tmp_path):
    found = gate.run_detect(fake(json.dumps([_finding("a.css")]), 2), target=tmp_path)
    assert [f["antipattern"] for f in found] == ["side-tab"]


@pytest.mark.parametrize("stdout, rc", [
    ("[]", 1),                    # יעד שלא נסרק — גם כשהפלט נראה נקי
    (json.dumps([_finding("a.css")]), 1),
    ("not json", 0),
    ('{"a": 1}', 0),
    ("[]", 2),                    # "יש ממצאים" ורשימה ריקה
    ("[]", 3),
])
def test_a_detector_that_did_not_finish_a_scan_is_an_error_not_a_clean_result(fake, tmp_path, stdout, rc):
    with pytest.raises(gate.GateError):
        gate.run_detect(fake(stdout, rc), target=tmp_path)


def test_a_wrong_sha_is_an_error(tmp_path):
    binary = tmp_path / "imp"
    binary.write_bytes(b"not the pinned engine")
    with pytest.raises(gate.GateError, match="sha256"):
        gate.verify(binary)
    gate.verify(binary, expected=gate.sha256_of(binary))


def test_a_declared_binary_that_is_not_there_is_an_error_not_a_skip(tmp_path):
    with pytest.raises(gate.GateError):
        gate.resolve_binary({gate.ENV_BIN: str(tmp_path / "missing")}, lab=tmp_path / "x")


def test_no_binary_anywhere_resolves_to_none_and_the_lab_path_is_used(tmp_path):
    assert gate.resolve_binary({}, lab=tmp_path / "missing") is None
    lab = tmp_path / "imp"
    lab.write_bytes(b"x")
    assert gate.resolve_binary({}, lab=lab) == lab


def test_normalize_makes_paths_repo_relative_and_drops_the_root_from_snippets(tmp_path):
    raw = [_finding(str(tmp_path / "server" / "static" / "console.css"),
                    snippet=f"  linked from {tmp_path}/server/static/index.html  ", line=7.0)]
    assert gate.normalize(raw, root=tmp_path) == [{
        "file": "server/static/console.css", "rule": "side-tab",
        "snippet": "linked from /server/static/index.html", "line": 7}]


def _entry(file="server/static/console.css", rule="side-tab", snippet="s", line=1) -> dict:
    return {"file": file, "rule": rule, "snippet": snippet, "line": line}


def test_ratchet_a_new_finding_fails():
    new, stale = gate.compare([_entry(), _entry(rule="marquee")], [_entry()])
    assert new == [_entry(rule="marquee")] and stale == []


def test_ratchet_a_fixed_finding_that_is_still_listed_fails():
    new, stale = gate.compare([], [_entry()])
    assert new == [] and stale == [_entry()]


def test_ratchet_a_moved_line_is_the_same_finding():
    assert gate.compare([_entry(line=40)], [_entry(line=7)]) == ([], [])


def test_ratchet_an_identical_extra_finding_is_new():
    """הבקרה השלילית של ה-Issue מוסיפה `border-inline-start: 4px solid` — זהה
    לממצא שכבר קיים ב-console.css. ספירה, לא קבוצה, היא מה שתופס אותו."""
    new, stale = gate.compare([_entry(line=1), _entry(line=99)], [_entry(line=1)])
    assert len(new) == 1 and stale == []


def test_the_known_list_is_for_the_pinned_engine():
    known = gate.load_baseline()
    assert (known["engine"], known["sha256"]) == (gate.ENGINE, gate.SHA256)
    for e in known["findings"]:
        assert set(e) == {"file", "rule", "snippet", "line"}, e
        assert e["file"].startswith("server/static/"), e


def test_fetch_downloads_verifies_and_reuses_the_cache(tmp_path):
    src = tmp_path / "release-asset"
    src.write_bytes(b"engine bytes")
    sha = gate.sha256_of(src)
    dest = tmp_path / "cache" / "imp"
    url = src.as_uri()
    assert gate.fetch(dest, url=url, expected=sha) == "downloaded"
    assert dest.read_bytes() == b"engine bytes"
    assert gate.fetch(dest, url=url, expected=sha) == "cache"


def test_fetch_never_leaves_a_mismatching_file_in_place(tmp_path):
    src = tmp_path / "release-asset"
    src.write_bytes(b"tampered")
    dest = tmp_path / "cache" / "imp"
    dest.parent.mkdir()
    dest.write_bytes(b"stale cache")          # sha שגוי: לא "cache"
    with pytest.raises(gate.GateError):
        gate.fetch(dest, url=src.as_uri(), expected="0" * 64)
    assert dest.read_bytes() == b"stale cache"
    assert not list(dest.parent.glob("*.part"))


@pytest.mark.parametrize("workflow", ["tests.yml", "nightly.yml"])
def test_the_ci_fetches_the_pinned_engine_before_the_suite(workflow):
    """בלי השלב הזה `IMAGESRV_REQUIRE_NATIVE=1` מכשיל את השער ב-CI — ומוטב
    שהטסט הזה יאמר למה, ולא ריצה ציבורית אדומה."""
    text = (gate.REPO / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    fetch_at = text.find("tests/impeccable_gate.py fetch")
    assert fetch_at != -1, f"{workflow}: אין שלב fetch"
    assert fetch_at < text.find("python -m pytest tests"), f"{workflow}: fetch אחרי pytest"
    assert gate.ENV_BIN in text
    assert "impeccable-${{ steps.impeccable.outputs.sha256 }}" in text
