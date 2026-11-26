"""#4051 -- `tools/dev/ship.py --mutate-base`: בקרה שלילית ב-PR של טסטים בלבד.

ה-PR לא נוגע בייצור, ולכן אין מה להחזיר לבסיס. המוטציה נעשית בקובץ ייצור **שה-PR לא שינה**, והכלי
מסרב בכל מצב שבו זה היה עוקף את הכלל "מחזירים את כל קובצי הייצור שה-PR נגע בהם" (#439).
משתמשים בארגז של test_dev_ship_3109 (ריפו אמיתי, תור אמיתי ב-sandbox, ssh ו-gh מזויפים).
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from test_dev_ship_3109 import SHIP, Box, _git, _write

DECL = "מוטציה בקובץ שה-PR לא שינה"


class TestOnlyBox(Box):
    """בנוסף ל-fixbr של הארגז: testonly (טסט בלבד) ו-prodbr (טסט + קובץ ייצור חדש)."""

    __test__ = False

    def __init__(self, root):
        super().__init__(root)
        _git(self.repo, "checkout", "-q", "main")
        _git(self.repo, "checkout", "-q", "-b", "testonly")
        _write(self.repo / "tests" / "test_x.py", "def test_ok():\n    assert True\n\n\ndef test_more():\n    assert True\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "tests only")
        _git(self.repo, "checkout", "-q", "-b", "prodbr")
        _write(self.repo / "agent" / "other.sh", "#!/bin/sh\necho other\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "tests plus production")

    def env(self, **extra: str) -> dict:
        # ה-gh המזויף קורא stdin בקידוד ברירת המחדל; בווינדוס זה cp1252 והעברית הופכת לג'יבריש
        return super().env(PYTHONUTF8="1", **extra)

    def so(self,*args: str, branch: str = "testonly") -> subprocess.CompletedProcess:
        return self.ship("--branch", branch, *args)


@pytest.fixture
def tb(tmp_path) -> TestOnlyBox:
    return TestOnlyBox(tmp_path)


def _good(tb, *more, **kw):
    return tb.so("--mutate-base", "agent/foo.sh:BASE_LINE", "--symbol", "BASE_LINE", *more, "tests/test_x.py", **kw)


def test_a_test_only_pr_is_submitted_with_fix_and_neg_and_the_neg_is_behavioural(tb):
    r = _good(tb)
    assert r.returncode == 0, r.stderr + r.stdout
    assert len(tb.pending()) == 2
    fix, neg = tb.work / "t1-fix", tb.work / "t1-neg"
    assert "BASE_LINE=1" in (fix / "agent" / "foo.sh").read_text()
    assert (neg / "agent" / "foo.sh").read_text() == "#!/bin/sh\necho base\n"
    assert (neg / "tests" / "test_x.py").read_text() == (fix / "tests" / "test_x.py").read_text()
    (c,) = tb.comments()
    assert "`BASE_LINE`: fix=1 neg=0" in c["body"]


def test_the_pr_comment_declares_the_mutation_in_a_file_the_pr_did_not_change(tb):
    assert _good(tb).returncode == 0
    (c,) = tb.comments()
    assert f"{DECL}: `agent/foo.sh`" in c["body"]


def test_a_regular_submission_does_not_carry_the_declaration(tb):
    r = tb.ship("--revert", "agent/foo.sh", "--mutate", "agent/new.py:NEW_MARK", "--mutate", "agent/two.py:return 1=>return 2",
                "--symbol", "NEW_GUARD", "tests/test_x.py")
    assert r.returncode == 0, r.stderr
    body = tb.comments()[0]["body"]
    assert "הגשה לתור" in body and DECL not in body   # הראשון: שהגוף נקרא בעברית, אחרת השני ריק מתוכן


def test_a_production_change_in_the_diff_is_refused_with_no_bypass(tb):
    r = tb.so("--mutate-base", "agent/foo.sh:BASE_LINE", "--symbol", "BASE_LINE", "tests/test_x.py", branch="prodbr")
    assert r.returncode == 1 and "agent/other.sh" in r.stderr and "שינוי ייצור" in r.stderr
    tb.untouched()


def test_the_production_change_is_refused_even_when_it_is_covered_by_a_mutate_beside_it(tb):
    r = tb.so("--mutate", "agent/other.sh:echo other", "--mutate-base", "agent/foo.sh:BASE_LINE",
              "--symbol", "BASE_LINE", "tests/test_x.py", branch="prodbr")
    assert r.returncode == 1 and "agent/other.sh" in r.stderr
    tb.untouched()


def test_mixing_with_revert_or_mutate_is_refused_even_without_a_production_change(tb):
    r = tb.so("--revert", "tests/test_x.py", "--mutate-base", "agent/foo.sh:BASE_LINE", "--symbol", "BASE_LINE", "tests/test_x.py")
    assert r.returncode == 1 and "אינו משתלב" in r.stderr
    tb.untouched()


def test_a_file_the_pr_did_change_must_use_the_regular_mutate(tb):
    r = tb.so("--mutate-base", "agent/foo.sh:NEW_GUARD", "--symbol", "NEW_GUARD", "tests/test_x.py", branch="fixbr")
    assert r.returncode == 1 and "agent/foo.sh" in r.stderr
    tb.untouched()


def test_a_test_or_docs_file_is_not_a_production_control(tb):
    r = tb.so("--mutate-base", "docs/n.md:notes", "--symbol", "notes", "tests/test_x.py")
    assert r.returncode == 1 and "אינו קובץ ייצור" in r.stderr
    tb.untouched()


def test_a_file_missing_from_the_branch_is_refused(tb):
    r = tb.so("--mutate-base", "agent/nothere.sh:X", "--symbol", "X", "tests/test_x.py")
    assert r.returncode == 1 and "אינו בענף" in r.stderr
    tb.untouched()


def test_without_a_comment_the_declaration_would_not_be_recorded_so_it_is_refused(tb):
    r = _good(tb, "--no-comment")
    assert r.returncode == 1 and "חייבת להירשם" in r.stderr
    tb.untouched()


def test_the_symbol_must_still_be_gone_from_neg(tb):
    r = tb.so("--mutate-base", "agent/foo.sh:BASE_LINE", "--symbol", "echo base", "tests/test_x.py")
    assert r.returncode == 1 and "neg=1" in r.stderr
    assert tb.pending() == [] and tb.comments() == []


def test_a_mutation_that_matches_nothing_is_refused(tb):
    r = tb.so("--mutate-base", "agent/foo.sh:NO_SUCH_TEXT", "--symbol", "BASE_LINE", "tests/test_x.py")
    assert r.returncode == 1 and "לא שינה" in r.stderr
    assert tb.pending() == [] and tb.comments() == []


def test_the_symbol_is_still_required(tb):
    r = tb.so("--mutate-base", "agent/foo.sh:BASE_LINE", "tests/test_x.py")
    assert r.returncode == 1 and "--symbol" in r.stderr
    tb.untouched()


def test_help_does_not_crash_on_a_non_utf8_console():
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
    p = subprocess.run([sys.executable, str(SHIP), "--help"], env=env, capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    assert b"--mutate-base" in p.stdout
