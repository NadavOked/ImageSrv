"""#3546 / #3173: קוד היציאה של `tools/e2e_simulation.py` -- 0 רק כשהלולאה נסגרה, ו-1 בכל דרך אחרת, בלי להשאיר שרת או תיקייה.

הסימולציה המלאה צריכה שרת, gcc ו-`chunkcast`; כאן רק ה-`main` שלה, כשה-harness והשלבים מוחלפים בדמה. מה שנבדק הוא החוזה
של הסקריפט, כפי שכתוב בראשו ("יציאה: 0 אם הלולאה נסגרה, 1 אם משהו בדרך נשבר"):

- בניית `chunkcast` שנכשלה, שרת שלא עונה, בדיקה שנכשלה בשלב, ותהליכון מכונה שנפל -- **יציאה 1**, והטקסט אומר מה נשבר.
- פחות מ-`MIN_CHECKS` בדיקות = **יציאה 1** ("ריצה חלקית אינה ריצה ירוקה"), גם כשאף בדיקה לא נכשלה.
- כל שמונת השלבים רצים, **בסדר**, והשלב של מנוע החתיכות (9ג) ביניהם.
- בכל יציאה: המכונות נעצרות, השרת נסגר (ונהרג אם אינו נסגר), ותיקיית העבודה נמחקת.

**בקרה שלילית:** ראו את גוף ה-PR -- מוטציות ב-`main` שמפילות את הטסטים על קוד היציאה או על השלב החסר.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

e2e = importlib.import_module("tools.e2e_simulation")

PHASES = ["setup", "capture", "edges", "class", "room", "direct", "chunk", "journal"]


class Machine:
    def __init__(self, mac, error=None):
        self.mac, self.error, self.stopped = mac, error, False

    def stop(self):
        self.stopped = True


class Server:
    def __init__(self, hangs=False):
        self.hangs, self.terminated, self.killed = hangs, False, False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        if self.hangs:
            raise TimeoutError("did not exit")

    def kill(self):
        self.killed = True


class Rig:
    """הדמה: ה-harness והשלבים. `fail` = שלב שנכשל (SystemExit כמו `check`), `broken` = מכונה שהתהליכון שלה נפל."""

    def __init__(self, tmp_path, monkeypatch, *, passed=None, fail=None, broken=None, chunkcast_error=None,
                 server_up=True, server_hangs=False):
        self.calls, self.server = [], Server(hangs=server_hangs)
        self.workdir = tmp_path / "workdir"
        self.workdir.mkdir()
        self.machines = [Machine("aa:bb:cc:00:00:01", error=broken), Machine("aa:bb:cc:00:00:02")]
        ctx = SimpleNamespace(builder=self.machines[0], cloner=self.machines[1], stations={})

        def build_chunkcast(workdir):
            self.calls.append("build")
            if chunkcast_error:
                raise SystemExit(chunkcast_error)

        harness = SimpleNamespace(
            BASE="http://127.0.0.1:0", passed=e2e.MIN_CHECKS if passed is None else passed,
            build_chunkcast=build_chunkcast,
            start_server=lambda workdir: (self.server, tmp_path / "data", tmp_path / "images"),
            wait_for_server=lambda: server_up, server_log_tail=lambda workdir: "SERVER LOG TAIL")
        monkeypatch.setattr(e2e, "harness", harness)
        monkeypatch.setattr(e2e.tempfile, "mkdtemp", lambda prefix="": str(self.workdir))
        for name in PHASES:
            module = f"phase_{name}"

            def run(*args, _name=name):
                self.calls.append(_name)
                if fail == _name:
                    raise SystemExit(f"FAILED: step in {_name}")
                return ctx if _name == "setup" else None
            monkeypatch.setattr(e2e, module, SimpleNamespace(run=run))


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    return lambda **kwargs: Rig(tmp_path, monkeypatch, **kwargs)


def test_a_full_loop_exits_zero_runs_every_phase_in_order_and_cleans_up(rig, capsys):
    r = rig()
    assert e2e.main() == 0
    assert r.calls == ["build", *PHASES], "שלב שנדלג עליו היה הופך את ה-9ג לריצה ירוקה בלעדיו"
    assert r.server.terminated and not r.server.killed
    assert all(m.stopped for m in r.machines)
    assert not r.workdir.exists(), "תיקיית העבודה נמחקה"
    assert "הלולאה נסגרה" in capsys.readouterr().out


def test_a_run_that_ran_fewer_checks_than_the_floor_is_not_green(rig, capsys):
    r = rig(passed=e2e.MIN_CHECKS - 1)
    assert e2e.main() == 1
    out = capsys.readouterr().out
    assert f"רצו רק {e2e.MIN_CHECKS - 1} בדיקות מתוך {e2e.MIN_CHECKS}" in out and "הלולאה נסגרה" not in out
    assert r.server.terminated and all(m.stopped for m in r.machines) and not r.workdir.exists()


def test_a_chunkcast_that_cannot_be_built_stops_before_any_server_starts(rig, capsys):
    r = rig(chunkcast_error="gcc is missing")
    assert e2e.main() == 1
    assert r.calls == ["build"], "אין שרת, ואין שלב אחד, בלי chunkcast"
    assert "gcc is missing" in capsys.readouterr().out
    assert not r.workdir.exists() and not r.server.terminated


def test_a_server_that_does_not_answer_prints_its_log_and_exits_one(rig, capsys):
    r = rig(server_up=False)
    assert e2e.main() == 1
    assert r.calls == ["build"]
    assert "SERVER LOG TAIL" in capsys.readouterr().out
    assert r.server.terminated and not r.workdir.exists()


def test_a_failed_check_in_the_chunk_phase_stops_the_loop_names_it_and_prints_the_log(rig, capsys):
    r = rig(fail="chunk")
    assert e2e.main() == 1
    assert r.calls == ["build", *PHASES[:PHASES.index("chunk") + 1]], "אחרי שלב שנכשל לא רצים שלבים נוספים"
    out = capsys.readouterr().out
    assert "FAILED: step in chunk" in out and "סוף יומן השרת" in out and "SERVER LOG TAIL" in out
    assert r.server.terminated and all(m.stopped for m in r.machines) and not r.workdir.exists()


def test_a_failure_names_the_machine_thread_that_fell(rig, capsys):
    r = rig(fail="room", broken=RuntimeError("thread died"))
    assert e2e.main() == 1
    out = capsys.readouterr().out
    assert "  תהליכון aa:bb:cc:00:00:01 נפל: thread died" in out and "✗" not in out
    assert all(m.stopped for m in r.machines)


def test_a_machine_thread_that_fell_fails_the_run_even_when_every_check_passed(rig, capsys):
    r = rig(broken=RuntimeError("thread died"))
    assert e2e.main() == 1
    out = capsys.readouterr().out
    assert "✗ תהליכון aa:bb:cc:00:00:01 נפל: thread died" in out and "SERVER LOG TAIL" in out and "הלולאה נסגרה" not in out
    assert r.calls[-1] == "journal", "הנפילה נבדקת אחרי שכל השלבים רצו"
    assert r.server.terminated and all(m.stopped for m in r.machines)


def test_a_server_that_does_not_exit_is_killed(rig):
    r = rig(server_hangs=True)
    assert e2e.main() == 0
    assert r.server.terminated and r.server.killed
    assert not r.workdir.exists()
