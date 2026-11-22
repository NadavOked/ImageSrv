"""#2099: הריצה המלאה רצה במקביל (pytest-xdist) — ופסק ההיגיינה לא הולך לאיבוד.

תחת xdist המנהל (controller) קובע את קוד היציאה ומתעלם מ-`session.exitstatus`
של worker. תהליך שידור יתום (#79) או ניסיון להפעיל שולח אמיתי שה-worker מצא
היו נבלעים — ריצה אדומה שיוצאת 0 (עיקרון 5). כאן נבדק שהממצא עובר מה-worker
אל המנהל, ושהמנהל מפיל עליו את הריצה.
"""

from __future__ import annotations

from types import SimpleNamespace

import conftest
import hygiene
import native


def _session(**config) -> SimpleNamespace:
    return SimpleNamespace(config=SimpleNamespace(**config), exitstatus=0)


def test_a_worker_hands_its_hygiene_findings_to_the_controller(monkeypatch):
    monkeypatch.setitem(conftest._run, "basetemp", None)
    out: dict = {}
    session = _session(workeroutput=out)

    # לא monkeypatch: הוא מתפרק אחרי `_no_real_sender`, שהיה רואה את הרשומה
    # המזויפת ומכשיל את הטסט הזה כאילו הגיע לשולח אמיתי.
    hygiene.blocked_spawns.append(["chunkcast", "--probe-2099"])
    try:
        conftest.pytest_sessionfinish(session, 0)
    finally:
        hygiene.blocked_spawns.pop()

    handed = out[conftest.WORKER_PROBLEMS_KEY]
    assert any("chunkcast --probe-2099" in line for line in handed), handed


def test_the_controller_fails_the_run_on_a_finding_a_worker_handed_over(monkeypatch):
    monkeypatch.setitem(conftest._run, "basetemp", None)
    monkeypatch.setattr(hygiene, "blocked_spawns", [])
    monkeypatch.setattr(conftest, "_skips", native.SkipAudit())
    monkeypatch.setattr(conftest, "_worker_problems", [])
    monkeypatch.setattr(conftest, "_win_failed", [])
    node = SimpleNamespace(workeroutput={
        conftest.WORKER_PROBLEMS_KEY: ["1 תהליכי שידור שרדו את הריצה (#79)"],
    })

    conftest.pytest_testnodedown(node, None)
    session = _session()                      # בלי workeroutput: זה המנהל
    conftest.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 1


def test_each_worker_writes_its_own_sender_logs(monkeypatch, tmp_path):
    """The partition's log is derived from `chunklog.LOG_DIR` and the file's name only -- two workers with the
    same manifest wrote the same file (one test read the evidence of another's pass). Each worker's basetemp is
    its own (`popen-gwN`), and that is where its logs go."""
    from server.rounds import chunklog
    part = "/x/images/img_7f3a91/p1.esp.pcl.zst"
    monkeypatch.setattr(chunklog, "LOG_DIR", chunklog.LOG_DIR)        # put back after the test
    monkeypatch.setattr(hygiene, "_run_tmp", hygiene._run_tmp)        # and the root a stand-in sender must sit under
    seen = []
    for worker in ("popen-gw0", "popen-gw1"):
        assert hygiene.sender_logs_under(tmp_path / worker) == tmp_path / worker
        seen.append(chunklog.log_path(part))
    assert seen[0] != seen[1], seen
    assert [p.parent for p in seen] == [tmp_path / "popen-gw0", tmp_path / "popen-gw1"], seen


def test_the_sender_logs_are_under_the_run_s_basetemp(tmp_path_factory):
    """#2180: `$TMPDIR/imagesrv-*.log` outlived every run (the lab gate's
    TMPDIR summary). The run's own basetemp holds them -- worker or serial."""
    from server.rounds import chunklog
    base = tmp_path_factory.getbasetemp()
    assert chunklog.LOG_DIR == base, chunklog.LOG_DIR
    assert chunklog.log_path("/x/images/img_7f3a91/p1.esp.pcl.zst").parent == base


def test_a_controller_with_nothing_handed_over_stays_green(monkeypatch):
    """הצד השני: בלי ממצא — אין אדום מומצא."""
    monkeypatch.setitem(conftest._run, "basetemp", None)
    monkeypatch.setattr(hygiene, "blocked_spawns", [])
    monkeypatch.setattr(conftest, "_skips", native.SkipAudit())
    monkeypatch.setattr(conftest, "_worker_problems", [])
    monkeypatch.setattr(conftest, "_win_failed", [])

    conftest.pytest_testnodedown(SimpleNamespace(workeroutput={}), None)
    session = _session()
    conftest.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 0
