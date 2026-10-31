"""‏#2099: הריצה המלאה רצה במקביל (pytest-xdist) — ופסק ההיגיינה לא הולך לאיבוד.

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
    hygiene.blocked_spawns.append(["udp-sender", "--probe-2099"])
    try:
        conftest.pytest_sessionfinish(session, 0)
    finally:
        hygiene.blocked_spawns.pop()

    handed = out[conftest.WORKER_PROBLEMS_KEY]
    assert any("udp-sender --probe-2099" in line for line in handed), handed


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


def test_each_worker_writes_its_own_sender_logs(monkeypatch):
    """לוג המחיצה נגזר מ-`SENDER_LOG` ומשם הקובץ בלבד — שני workers עם אותו
    מניפסט כתבו לאותו קובץ (שם ראה טסט אחד את `Starting transfer` של אחר)."""
    from server.rounds import sender
    shared = sender.SENDER_LOG.with_name("imagesrv-sender.log")
    part = "/x/images/img_7f3a91/p1.esp.pcl.zst"
    seen = []
    for worker in ("gw0", "gw1"):
        monkeypatch.setattr(sender, "SENDER_LOG", shared)
        hygiene.isolate_sender_log(worker)
        seen.append(sender.partition_log(part))
    assert seen[0] != seen[1], seen
    assert all(p.parent == shared.parent for p in seen), seen


def test_a_serial_run_keeps_the_sender_log_where_it_was(monkeypatch):
    from server.rounds import sender
    monkeypatch.setattr(sender, "SENDER_LOG", sender.SENDER_LOG)
    before = sender.SENDER_LOG
    assert hygiene.isolate_sender_log(None) is None
    assert sender.SENDER_LOG == before


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
