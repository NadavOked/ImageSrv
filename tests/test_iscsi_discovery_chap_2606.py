"""#2606: גילוי iSCSI אינו מקבל CHAP ואינו מעביר אותו — הכרעה מתועדת, לא תמיכה.

`iscsi_discover(portal, chap)` קיבל את פרטי ה-CHAP ולא השתמש בהם: `/scan` ו-`/test`
העבירו את הסוד עד הפונקציה, והוא נעצר שם. ה-CHAP של המיקום הוא `node.session.auth.*`
(`iscsi_login`), ולא `discovery.sendtargets.auth.*`; תמיכה בגילוי מאומת דורשת
`-m discoverydb`, שלא נמדד יחד עם `-o nonpersistent` (#1983) מול יעד. לכן: שום פרט
אימות אינו נשלח לגילוי, והאשף אומר שה-CHAP חל על ההתחברות בלבד (storage_page.test.cjs).

ההתנהגות מול יעד שעובד היום לא השתנתה: אותו argv בדיוק ל-iscsiadm (כאן), ואותו login עם
CHAP (`iscsi_login` לא נגע; test_iscsi_discovery_1983.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import test_storage_locations as tsl
from server.storage import locations as sl
from server.storage.locations import OK, HookResult
from test_iscsi_discovery_1983 import SECRET, iscsiadm   # noqa: F401 -- `iscsiadm` is a fixture
from test_storage_locations import IQN, PORTAL, env      # noqa: F401 -- `env` is a fixture

USER = "imagesrv-chap"
SCAN = "/api/console/storage-locations/scan"


@pytest.fixture
def discover_calls(monkeypatch):
    """מה שהגיע ל-hook של הגילוי, בכל קריאה: ה-args וה-kwargs כפי שהם."""
    calls: list[tuple[tuple, dict]] = []

    def record(self, *args, **kwargs):
        calls.append((args, kwargs))
        return HookResult(OK, payload={"items": list(self.iscsi_items), "rejected": 0})

    monkeypatch.setattr(tsl.FakeHooks, "iscsi_discover", record)
    return calls


@pytest.mark.parametrize("creds", [
    {"chap_user": USER, "chap_secret": SECRET},
    {"user": USER, "secret": SECRET},
], ids=["chap_user", "user"])
def test_scan_does_not_hand_chap_to_the_discovery(discover_calls, env, creds):
    body = env["admin"].post(SCAN, json={"type": "iscsi", "portal": PORTAL, "creds": creds}).json()
    assert body["ok"] is True and body["items"] == [{"portal": PORTAL, "iqn": IQN}]
    assert discover_calls == [((PORTAL,), {})], "the discovery gets the portal, and nothing else"


def test_test_connection_does_not_hand_chap_to_the_discovery(discover_calls):
    hooks = {"iscsi_discover": tsl.FakeHooks().iscsi_discover}
    outcome = sl.test_connection("iscsi", {"portal": PORTAL, "iqn": IQN, "chap_user": USER,
                                           "chap_secret": SECRET},
                                 hooks, tmp_target=Path("unused"), server_base="")
    assert outcome["ok"] is True, outcome
    assert discover_calls == [((PORTAL,), {})]


def test_the_discovery_argv_is_the_same_as_before_and_carries_no_chap(iscsiadm):
    """שומר מפני תיקון-יתר: אותה פקודה בדיוק כמו אחרי #1983 — בלי discoverydb, בלי auth."""
    iscsiadm.discovered = f"{PORTAL},1 {IQN}\n"
    outcome = sl.test_connection("iscsi", {"portal": PORTAL, "iqn": IQN, "chap_user": USER,
                                           "chap_secret": SECRET},
                                 {"iscsi_discover": sl.iscsi_discover},
                                 tmp_target=Path("unused"), server_base="")
    assert outcome["ok"] is True, outcome
    assert iscsiadm.calls == [["iscsiadm", "-m", "discovery", "-t", "st", "-p", PORTAL,
                               "-o", "nonpersistent"]]

