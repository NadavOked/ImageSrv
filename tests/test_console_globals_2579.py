"""#2579 (C1): no top-level name may be declared twice across the console files.

All console*.js files share one global scope, so a second `const`/`let` of the
same name is a SyntaxError and a second `function` silently replaces the first.
`brainLtr` did exactly that and broke CI. The check reads the files in
index.html load order (`tests/console_src.py`) and uses `tools/console_globals.py`.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

from console_src import STATIC, console_files

# Loaded by file, not by putting all of tools/ on sys.path: tools/ holds private
# modules, and a public test may not reach them at collection (#646).
_spec = importlib.util.spec_from_file_location(
    "console_globals", Path(__file__).resolve().parent.parent / "tools" / "console_globals.py")
cg = importlib.util.module_from_spec(_spec)
sys.modules["console_globals"] = cg
_spec.loader.exec_module(cg)

# Found by this test on main (2026-11-27), production code deliberately untouched
# here: console.js:1239 (folder filter) is shadowed by console.js:3879 (adds the
# text filter), which wins at load time. Remove the entry when the first is
# deleted; the test fails on a stale entry so the list cannot rot.
KNOWN_DUPLICATES = {"imagesVisible"}


def assert_no_duplicates(files, known=frozenset()):
    dups = {k: v for k, v in cg.find_duplicates(files).items() if k not in known}
    stale = sorted(k for k in known if k not in cg.find_duplicates(files))
    assert not dups, "top-level names declared more than once: " + "; ".join(
        f"{n} @ " + ", ".join(f"{f}:{ln}" for f, ln, _ in locs) for n, locs in sorted(dups.items()))
    assert not stale, f"KNOWN_DUPLICATES entries no longer duplicated, remove them: {stale}"


def test_console_files_have_no_duplicate_top_level_names():
    files = {n: (STATIC / n).read_text(encoding="utf-8") for n in console_files()}
    assert_no_duplicates(files, KNOWN_DUPLICATES)


FIXTURE_OK = {
    "a.js": "/* ---------- one ---------- */\nfunction alpha() { return beta; }\nconst beta = 1;\n"
            "/* ---------- two ---------- */\nlet gamma = alpha(), delta = 2;\n",
    "b.js": "function eps() { const alpha = 1; return alpha; }\n",  # nested name is not global
}
FIXTURE_DUP = {
    "a.js": FIXTURE_OK["a.js"],
    "b.js": "function eps() {}\nfunction alpha() {}\n",  # alpha again, another file
}


def test_fixture_with_duplicate_makes_the_check_fail():
    assert_no_duplicates(FIXTURE_OK)  # the clean fixture passes (guard against rejecting all)
    with pytest.raises(AssertionError, match="alpha @ a.js:2, b.js:2"):
        assert_no_duplicates(FIXTURE_DUP)


def test_stale_known_entry_fails():
    with pytest.raises(AssertionError, match="no longer duplicated"):
        assert_no_duplicates(FIXTURE_OK, known={"alpha"})


def test_fixture_declarations_and_cross_region_users():
    info = cg.build({**FIXTURE_OK, "c.js": "alpha(); delta;"}, main="a.js")
    got = {d["name"]: (d["kind"], d["region"], d["users"]) for d in info["decls"]}
    assert got["alpha"] == ("function", 1, ["R2", "b.js", "c.js"])  # b.js: a local of the same name counts (upper bound)
    assert got["beta"] == ("const", 1, [])  # only alpha (same region) uses it
    assert got["gamma"] == ("let", 2, [])
    assert got["delta"] == ("let", 2, ["c.js"])  # second declarator of a `let a, b`
    assert [t for t, _ in info["regions"]][1:] == ["one", "two"]


def test_tokenizer_ignores_braces_in_strings_regex_templates_comments():
    src = ('const s = "}{"; // function fake() {}\n'
           "const r = /[}{]/g; const t = `a${ {x: 1}.x }}`;\n"
           "/* function nope() {} */\nfunction real() { return '}'; }\n"
           "function after() {}\n")
    names = [n for _, n, _ in cg.top_level_decls(src)]
    assert names == ["s", "r", "t", "real", "after"]


def test_render_lists_duplicates_regions_and_cross_region_use():
    info = cg.build({**FIXTURE_DUP, "c.js": "alpha(); delta;"}, main="a.js", dup_files=["a.js", "b.js"])
    md = cg.render(info)
    assert "- `alpha`: a.js:2 (function), b.js:2 (function)" in md
    assert "| R1 | 1 | one | 2 | 1 |" in md          # alpha is used outside R1, beta is not
    assert "- R2 משתמש ב: R1 (1)" in md                # gamma = alpha() in region two
    assert "| function | `alpha` | 2 | R2, b.js, c.js |" in md
    assert "| const | `beta` | 3 | פנימי |" in md
    assert "## R0: " in md and "אין הצהרות ברמה העליונה." in md   # the region before the first header


def test_render_says_none_when_there_is_no_duplicate():
    assert "אין.\n" in cg.render(cg.build(FIXTURE_OK, main="a.js", dup_files=["a.js", "b.js"]))


def test_main_check_reports_the_known_duplicate_and_exits_1(capsys):
    # The real console has exactly KNOWN_DUPLICATES (#4232); --check must name it and fail.
    assert cg.main(["--check"]) == 1
    out = capsys.readouterr().out
    assert [ln.split(":")[0] for ln in out.splitlines()] == [f"duplicate {n}" for n in sorted(KNOWN_DUPLICATES)]


def test_main_writes_the_map(tmp_path, capsys):
    out = tmp_path / "map.md"
    assert cg.main(["--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# מפת הגלובלים של הקונסולה")
    assert "`imagesVisible`" in text
    assert f"{out}: " in capsys.readouterr().out


def test_escaped_backtick_does_not_end_the_template():
    src = "const t = `x\\`y${'}'}`;\nfunction g() {}\n"
    assert [n for _, n, _ in cg.top_level_decls(src)] == ["t", "g"]
