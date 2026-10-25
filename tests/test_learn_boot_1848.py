"""‏#1848 — זמן אתחול לכל מכונה ולכל שלב: רגרסיה, ולפני/אחרי עדכון.

הראיה ב-Issue: ‏10.7ש' של ACPI ‏`_PSL` ב-HP G6 נמצאו ביד. כאן: אותו שלב שהתארך
פי 2 מהבסיס של המכונה (ולפחות 3ש') נרשם ביומן ובמסך הבריאות, בשם השלב.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from server import boottrace, db, learn_boot

MAC = "78:ac:c0:9b:11:c2"
OTHER = "aa:bb:cc:00:00:32"


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    c = db.connect(tmp_path / "imagectl.db")
    c.execute("INSERT INTO machines (mac, suffix, group_id, added_at) VALUES (?, '02',"
              " 'grp_CLONERS', 'x')", (MAC,))
    c.commit()
    monkeypatch.setattr(learn_boot, "version", lambda: "v1.0.0")
    return c


class Clock:
    def __init__(self, t: float = 1_760_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture()
def clock(monkeypatch):
    c = Clock()
    # ‏`boottrace` משתמש רק ב-`time.time()`; ‏raising=False — כדי שהבקרה השלילית (קוד בלי
    # המדידה, בלי `import time`) תיכשל על ההתנהגות ולא על ה-fixture.
    monkeypatch.setattr(boottrace, "time", SimpleNamespace(time=c), raising=False)
    return c


def _boot(conn, clock, mac=MAC, kernel_s=2.0, steps=None):
    """אתחול שלם, בשעון מזויף: כל מעבר 0.5ש', ‏`pre-initrd` (טעינת הקרנל) — `kernel_s`."""
    for step in steps or ("menu", "entry", "http-ok", "pre-linux", "pre-initrd", "pre-boot",
                          "agent-net", "agent-start", "agent-hello"):
        clock.t += kernel_s if step == "pre-initrd" else 0.5
        assert boottrace.record(conn, mac, step)


def _times(conn, step):
    return [r["seconds"] for r in conn.execute(
        "SELECT seconds FROM boot_step_times WHERE step = ? ORDER BY rowid", (step,))]


def test_each_adjacent_transition_is_measured_in_server_time(conn, clock):
    _boot(conn, clock, kernel_s=2.25)
    got = {r["step"]: r["seconds"] for r in conn.execute("SELECT * FROM boot_step_times")}
    assert got["pre-initrd"] == pytest.approx(2.25)          # שבר שנייה — לא 0/1/2
    assert got["http-ok"] == pytest.approx(0.5)
    assert "menu" not in got                                  # אין לפניו
    assert "entry" not in got                                 # כולל המתנה לאדם בתפריט
    assert {r["version"] for r in conn.execute("SELECT version FROM boot_step_times")} == {"v1.0.0"}


def test_a_skipped_step_is_not_measured(conn, clock):
    _boot(conn, clock, steps=("menu", "entry", "http-ok", "pre-initrd"))   # pre-linux אבד
    assert _times(conn, "pre-initrd") == []


def test_the_local_branch_is_not_measured(conn, clock):
    _boot(conn, clock)
    clock.t += 1
    boottrace.record(conn, MAC, "local-entry")     # idx 9 אחרי agent-hello (8) — לא מעבר
    assert conn.execute("SELECT COUNT(*) FROM boot_step_times WHERE step = 'local-entry'"
                        ).fetchone()[0] == 0


def test_a_new_boot_does_not_measure_across_boots(conn, clock):
    _boot(conn, clock)
    clock.t += 3600
    boottrace.record(conn, MAC, "menu")
    clock.t += 0.5
    boottrace.record(conn, MAC, "entry")
    clock.t += 0.5
    boottrace.record(conn, MAC, "http-ok")
    assert _times(conn, "http-ok") == [pytest.approx(0.5), pytest.approx(0.5)]


def test_a_regression_is_journaled_with_the_step_name(conn, clock):
    for _ in range(3):
        _boot(conn, clock, kernel_s=2.0)
    _boot(conn, clock, kernel_s=12.7)               # +10.7ש' — ה-_PSL של ה-G6
    got = learn_boot.regression(conn, MAC, "pre-initrd")
    assert got["slow"] is True and got["baseline"] == pytest.approx(2.0) and got["samples"] == 3
    line = conn.execute("SELECT detail FROM journal WHERE event = 'boot_step_slow'").fetchone()
    assert line["detail"] == f"{MAC} pre-initrd 12.7s baseline=2.0s (3 boots)"
    (row,) = [r for r in learn_boot.health_checks(conn) if r["id"].startswith("boot_slow")]
    assert row["id"] == f"boot_slow:{MAC}" and row["state"] == "warn" and row["label"] == "02"
    assert "'הקרנל נטען' ארך 12.7ש' מול בסיס 2.0ש'" in row["detail"]


def test_twice_a_tiny_step_is_noise_not_a_regression(conn, clock):
    """‏0.5ש' → 1.5ש' הוא פי 3, אבל רק שנייה — מתחת ל-MIN_DELTA_S."""
    for _ in range(3):
        _boot(conn, clock, kernel_s=0.5)
    _boot(conn, clock, kernel_s=1.5)
    assert learn_boot.regression(conn, MAC, "pre-initrd")["slow"] is False
    assert conn.execute("SELECT 1 FROM journal WHERE event = 'boot_step_slow'").fetchone() is None


def test_without_a_baseline_it_is_unchecked(conn, clock):
    _boot(conn, clock, kernel_s=2.0)
    _boot(conn, clock, kernel_s=20.0)
    got = learn_boot.regression(conn, MAC, "pre-initrd")
    assert got["slow"] is None and got["baseline"] is None and got["samples"] == 1


def test_the_baseline_is_per_machine(conn, clock):
    for _ in range(4):
        _boot(conn, clock, mac=OTHER, kernel_s=12.0)   # מכונה אחרת, איטית מטבעה
    for _ in range(3):
        _boot(conn, clock, kernel_s=2.0)
    _boot(conn, clock, kernel_s=3.0)
    assert learn_boot.regression(conn, MAC, "pre-initrd")["slow"] is False
    assert learn_boot.regression(conn, OTHER, "pre-initrd")["slow"] is False


def test_before_and_after_an_update(conn, clock, monkeypatch):
    for _ in range(3):
        _boot(conn, clock, kernel_s=2.0)
    monkeypatch.setattr(learn_boot, "version", lambda: "v1.0.1")
    for _ in range(3):
        _boot(conn, clock, kernel_s=12.0)
    got = learn_boot.version_compare(conn)
    assert got["now"] == "v1.0.1" and got["before"] == "v1.0.0"
    kernel = next(s for s in got["steps"] if s["step"] == "pre-initrd")
    assert kernel["slow"] is True and kernel["before_s"] == pytest.approx(2.0)
    assert next(s for s in got["steps"] if s["step"] == "http-ok")["slow"] is False
    (row,) = [r for r in learn_boot.health_checks(conn) if r["id"] == "boot_version"]
    assert row["state"] == "warn" and row["label"] == "זמן אתחול לפני/אחרי v1.0.1"
    assert "'הקרנל נטען' 2.0ש' → 12.0ש' (מול v1.0.0)" in row["detail"]


def test_one_version_has_no_comparison(conn, clock):
    for _ in range(3):
        _boot(conn, clock)
    assert learn_boot.version_compare(conn) is None
    ids = [r["id"] for r in learn_boot.health_checks(conn)]
    assert ids == ["boot_slow"]


def test_no_measurements_no_rows(conn):
    assert learn_boot.health_checks(conn) == []


def test_a_broken_measurement_does_not_lose_the_breadcrumb(conn, clock, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(learn_boot, "measure", broken)
    assert boottrace.record(conn, MAC, "menu")
    assert boottrace.record(conn, MAC, "entry")
    assert conn.execute("SELECT step FROM boot_steps WHERE mac = ?", (MAC,)).fetchone()[0] == "entry"


def test_the_health_screen_carries_it():
    import inspect
    from server import health
    assert "_learn_boot.health_checks(ctx.conn)" in inspect.getsource(health.collect)


def test_the_latest_measurement_is_not_part_of_its_own_baseline(conn, clock):
    """בסיס 2,2,10 → חציון 2; המדידה האחרונה (10) אינה מושכת אותו ל-6."""
    for kernel_s in (2.0, 2.0, 10.0, 10.0):
        _boot(conn, clock, kernel_s=kernel_s)
    got = learn_boot.regression(conn, MAC, "pre-initrd")
    assert got["baseline"] == pytest.approx(2.0) and got["slow"] is True
