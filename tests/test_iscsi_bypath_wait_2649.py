"""‏#2649: ‏`iscsi_login` ממתין לקישור ה-by-path ש-udev יוצר אחרי ש-`iscsiadm -l` חזר.

נמדד 07/11 על testrunner: הקישור מופיע 40-100ms אחרי החזרת הקריאה, ו-15 מתוך 15
התחברויות החזירו `ok` עם `device_by_path` ריק. כאן `_run` ו-`_iscsi_by_path` מזויפים.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from server.storage import locations as sl
from server.storage.locations import FAILED, OK, HookResult
from test_iscsi_discovery_1983 import iscsiadm   # noqa: F401
from test_storage_locations import IQN, PORTAL

REAL_WAIT = sl._wait_iscsi_by_path   # ‏הפיקסצ'ר של #1983 מחליף אותה ב-stub
LINK = f"/dev/disk/by-path/ip-{PORTAL}-iscsi-{IQN}-lun-0"


@pytest.fixture
def real_wait(iscsiadm, monkeypatch):
    monkeypatch.setattr(sl, "_wait_iscsi_by_path", REAL_WAIT)
    monkeypatch.setattr(sl, "ISCSI_BYPATH_WAIT_S", 0.3)
    monkeypatch.setattr(sl, "ISCSI_BYPATH_POLL_S", 0.01)


def _appears_after(monkeypatch, polls: int | None):
    """`_iscsi_by_path` מחזיר ריק `polls` פעמים ואז את הקישור; None = לעולם לא."""
    state = {"n": 0}

    def lookup(portal, iqn):
        state["n"] += 1
        return LINK if polls is not None and state["n"] > polls else ""
    monkeypatch.setattr(sl, "_iscsi_by_path", lookup)
    return state


def test_login_waits_for_a_link_that_udev_creates_late(real_wait, monkeypatch):
    state = _appears_after(monkeypatch, polls=5)
    result = sl.iscsi_login(IQN, PORTAL)
    assert result.status == OK
    assert result.payload["device_by_path"] == LINK
    assert state["n"] == 6


def test_login_fails_by_name_when_the_link_never_appears(real_wait, monkeypatch):
    _appears_after(monkeypatch, polls=None)
    result = sl.iscsi_login(IQN, PORTAL)
    assert result.status == FAILED
    assert "לא הופיע" in result.reason
    assert "device_by_path" not in (result.payload or {})


def test_the_wait_is_bounded_and_runs_udev_settle(monkeypatch):
    monkeypatch.setattr(sl, "ISCSI_BYPATH_WAIT_S", 0.2)
    monkeypatch.setattr(sl, "ISCSI_BYPATH_POLL_S", 0.01)
    calls = []
    monkeypatch.setattr(sl, "_run", lambda cmd, timeout=30, input_text=None:
                        calls.append(cmd) or HookResult(FAILED, "no udevadm"))
    state = _appears_after(monkeypatch, polls=None)
    assert sl._wait_iscsi_by_path(PORTAL, IQN) == ""
    assert calls and calls[0][:2] == ["udevadm", "settle"]   # settle כושל לא עוצר
    assert 2 <= state["n"] < 100


def test_an_immediate_link_costs_no_sleep(monkeypatch):
    monkeypatch.setattr(sl, "_run", lambda *a, **k: HookResult(OK))
    monkeypatch.setattr(sl, "_iscsi_by_path", lambda p, i: LINK)
    monkeypatch.setattr(sl.time, "sleep", lambda s: pytest.fail("slept"))
    assert sl._wait_iscsi_by_path(PORTAL, IQN) == LINK
