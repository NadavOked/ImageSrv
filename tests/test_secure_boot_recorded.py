"""#500: הסוכן מודד את מצב ה-Secure Boot ושולח אותו — והשרת זרק אותו.

שלוש מכונות ברזל מדדו ושלחו ב-07/09 ואף ערך לא נשמר. הראיה כאן היא
**קריאה בחזרה** (עיקרון 5): מה ש-hello דיווח חוזר מהקונסולה — במכונות
הרשומות ובאובייקט הרשת — ו"לא דווח" (סוכן ישן) נשאר null, לא false."""

import pytest

pytest.importorskip("fastapi")

from conftest import hello_body, setup_classroom


def _machines(server):
    return {m["mac"]: m for m in server["admin"].get("/api/console/machines").json()}


def _net(server):
    return {d["mac"]: d for d in server["admin"].get("/api/console/net").json()}


def test_firmware_and_secure_boot_are_read_back_from_the_console(server):
    ids = setup_classroom(server)
    body = hello_body(ids["mac1"])
    body["firmware"] = "uefi"
    body["secure_boot"] = True
    assert server["anon"].post("/api/v1/agent/hello", json=body).status_code == 200
    row = _machines(server)[ids["mac1"]]
    assert row["firmware"] == "uefi" and row["secure_boot"] == 1
    net = _net(server)[ids["mac1"]]
    assert net["firmware"] == "uefi" and net["secure_boot"] is True


def test_an_old_agent_without_the_fields_leaves_null_not_false(server):
    ids = setup_classroom(server)
    old = {k: v for k, v in hello_body(ids["mac1"]).items() if k not in ("firmware", "secure_boot")}
    assert server["anon"].post("/api/v1/agent/hello", json=old).status_code == 200
    net = _net(server)[ids["mac1"]]
    assert net["firmware"] is None and net["secure_boot"] is None


def test_a_malformed_value_is_not_stored_and_a_later_hello_keeps_the_last_good_one(server):
    ids = setup_classroom(server)
    good = hello_body(ids["mac1"]); good["firmware"] = "uefi"; good["secure_boot"] = False
    assert server["anon"].post("/api/v1/agent/hello", json=good).status_code == 200
    bad = hello_body(ids["mac1"]); bad["firmware"] = "coreboot"; bad["secure_boot"] = "yes"
    assert server["anon"].post("/api/v1/agent/hello", json=bad).status_code == 200
    net = _net(server)[ids["mac1"]]
    assert net["firmware"] == "uefi" and net["secure_boot"] is False
