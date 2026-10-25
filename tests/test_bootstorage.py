"""‏#1331 — דרייבר האחסון ה-in-box של הדיסק שנכתב הופך ל-boot-start
(`agent/lib/bootstorage.sh`, ‏interfaces.md §17 צעד 5א).

אותה קופסה של `test_postdeploy.py` (‏sh אמיתי, כלים מזויפים), עם hive
‏`SYSTEM` מזויף: עץ קבצים, ערך לקובץ. מה שנבדק הוא **מה נשאר ב-SYSTEM**
ו**מה דווח** ב-`drivers.boot`:

* ‏`Start` ו-`StartOverride\\0` נכתבים כ-0 **בסט הבקרה של `Select\\Current`**
  — לא ב-ControlSet001 הקבוע — ונקראים חזרה;
* השירות נבחר לפי **הדיסק** (‏nvme → ‏stornvme, ‏sd → ‏storahci), לא לפי
  המכונה — כי מגירה של משכפל הולכת למכונה אחרת (#1269);
* כל כשל — קריאה שנשברה, כתיבה שלא נקראה חזרה, שירות שאינו באימג' — הוא
  `failed` בשמו, ה-staging נשאר `staged` והשחזור `done` (עיקרון 5);
* השלב רץ **רק** אחרי שהחבילות על הדיסק: כל כשל מוקדם לא נוגע ב-SYSTEM.
"""

from __future__ import annotations

import pytest

from native import requires_native
from test_agent import AGENT, BASH
from test_postdeploy import FILES, SVC, SYSTEM_RAID, Box, answer_for

pytestmark = requires_native(("bash", BASH))


def sys_writes(box: Box) -> list[str]:
    return [c for c in box.calls() if c.startswith("-d ")]


def test_a_raid_image_on_a_sata_disk_gets_storahci_boot_start_in_the_current_set(tmp_path):
    box = Box(tmp_path)
    _, result = box.stage("sda")
    assert result["state"] == "staged"
    assert result["boot"] == {"state": "enabled", "service": "storahci"}
    assert box.sysval(f"{SVC}/storahci/Start") == "0"
    assert box.sysval(f"{SVC}/storahci/StartOverride/0") == "0", "StartOverride גובר על Start"
    assert box.sysval("ControlSet001/Services/storahci/Start") == "3", "Select\\Current=2"
    assert box.sysval(f"{SVC}/stornvme/Start") == "3", "רק השירות של הדיסק שנכתב"
    assert len(sys_writes(box)) == 2, box.calls()
    assert box.state() == "staging"   # #1935: not terminal -- done is the caller's, after the mark


def test_an_nvme_disk_gets_stornvme(tmp_path):
    box = Box(tmp_path)
    _, result = box.stage("nvme0n1")
    assert result["boot"] == {"state": "enabled", "service": "stornvme"}
    assert box.sysval(f"{SVC}/stornvme/Start") == "0"
    assert box.sysval(f"{SVC}/storahci/Start") == "3"
    assert box.sysval(f"{SVC}/storahci/StartOverride/0") == "3"


def test_a_driver_that_is_already_boot_start_is_not_written(tmp_path):
    system = {**SYSTEM_RAID, f"{SVC}/storahci/Start": "0", f"{SVC}/storahci/StartOverride/0": "0"}
    box = Box(tmp_path, system=system)
    _, result = box.stage("sda")
    assert result["boot"] == {"state": "already", "service": "storahci"}
    assert sys_writes(box) == []


def test_start_zero_with_an_override_of_three_is_not_already(tmp_path):
    """‏Start=0 לבד אינו ראיה: ‏StartOverride\\0=3 גובר עליו."""
    box = Box(tmp_path, system={**SYSTEM_RAID, f"{SVC}/storahci/Start": "0"})
    _, result = box.stage("sda")
    assert result["boot"] == {"state": "enabled", "service": "storahci"}
    assert box.sysval(f"{SVC}/storahci/StartOverride/0") == "0"
    writes = sys_writes(box)
    assert len(writes) == 1 and "StartOverride" in writes[0], writes


def test_no_override_key_needs_only_start(tmp_path):
    system = {k: v for k, v in SYSTEM_RAID.items() if "StartOverride" not in k}
    box = Box(tmp_path, system=system)
    _, result = box.stage("sda")
    assert result["boot"] == {"state": "enabled", "service": "storahci"}
    assert len(sys_writes(box)) == 1


def test_an_image_without_the_service_is_a_failure_not_a_skip(tmp_path):
    """‏Windows 7 אין לו stornvme — הדיסק הזה לא יעלה, וזה ממצא."""
    system = {k: v for k, v in SYSTEM_RAID.items() if "stornvme" not in k}
    box = Box(tmp_path, system=system)
    _, result = box.stage("nvme0n1")
    assert result["state"] == "staged", "החבילות הונחו — התוצאה שלהן נשארת"
    assert result["boot"]["state"] == "failed" and "באימג' אין את השירות stornvme" in result["boot"]["error"]
    assert sys_writes(box) == []
    assert box.state() == "staging"   # #1935: not terminal -- done is the caller's, after the mark


def test_a_read_that_breaks_is_not_a_missing_service(tmp_path):
    """‏rc 3 = "לא נמצא"; כל קוד אחר = הקריאה עצמה נשברה (עיקרון 5)."""
    box = Box(tmp_path, env="FAKE_SYS_GET_RC=5")
    _, result = box.stage("sda")
    assert result["boot"]["state"] == "failed"
    assert "no storahci" not in result["boot"]["error"]
    assert sys_writes(box) == []


def test_a_write_that_does_not_read_back_is_a_failure(tmp_path):
    box = Box(tmp_path, env="FAKE_SYS_NOOP=1")
    _, result = box.stage("sda")
    assert result["boot"]["state"] == "failed" and "לא אומת" in result["boot"]["error"]
    assert box.sysval(f"{SVC}/storahci/Start") == "3"


def test_a_write_that_fails_is_a_failure_and_the_disk_is_released(tmp_path):
    box = Box(tmp_path, env="FAKE_SYS_RC=4",
              stubs={"umount": 'echo umount "$@" >> "$FAKE_SERVER/umounts"; exit 0'})
    _, result = box.stage("sda")
    assert result["state"] == "staged"
    assert result["boot"]["state"] == "failed" and "rc=4" in result["boot"]["error"]
    assert (box.server / "umounts").read_text().count("umount") == 1


def test_an_unreadable_select_current_writes_nothing(tmp_path):
    system = {k: v for k, v in SYSTEM_RAID.items() if not k.startswith("Select")}
    box = Box(tmp_path, system=system)
    _, result = box.stage("sda")
    assert result["boot"]["state"] == "failed" and "Select" in result["boot"]["error"]
    assert sys_writes(box) == []


def test_a_missing_system_hive_is_a_failure(tmp_path):
    box = Box(tmp_path, system=None)
    _, result = box.stage("sda")
    assert result["state"] == "staged"
    assert result["boot"] == {"state": "failed", "service": "storahci",
                              "error": "כוורת SYSTEM לא נמצאה"}


@pytest.mark.parametrize("disk", ["vda", "mmcblk0"])
def test_a_disk_without_a_mapping_is_skipped_by_name(tmp_path, disk):
    box = Box(tmp_path)
    _, result = box.stage(disk)
    assert result["boot"] == {"state": "skipped", "reason": "unknown_bus"}
    assert not [c for c in box.calls() if "SYSTEM" in c]


@pytest.mark.parametrize("case", ["sha", "no_match", "linux"])
def test_an_earlier_exit_leaves_system_alone_and_reports_no_boot(tmp_path, case):
    """השלב רץ רק כשהחבילות כבר על הדיסק: מסלול שעוצר לפני כן לא נוגע ב-SYSTEM."""
    if case == "sha":
        box = Box(tmp_path, answer=answer_for(FILES, tamper="intel-nic/x64/e1d68x64.sys"))
    elif case == "no_match":
        box = Box(tmp_path, packages={}, answer=answer_for({}))
    else:
        from test_postdeploy import LINUX_PLAN   # noqa: PLC0415
        box = Box(tmp_path, plan=LINUX_PLAN)
    _, result = box.stage("sda")
    assert "boot" not in result
    assert not [c for c in box.calls() if "SYSTEM" in c]
    assert box.sysval(f"{SVC}/storahci/Start") == "3"


def test_the_agent_loads_bootstorage_before_postdeploy():
    main = (AGENT / "imagectl-agent").read_text(encoding="utf-8")
    assert main.index('"$LIB_DIR/bootstorage.sh"') < main.index('"$LIB_DIR/postdeploy.sh"')
    post = (AGENT / "lib" / "postdeploy.sh").read_text(encoding="utf-8")
    assert post.index("_device_path_add \"$_hive\" ||") < post.index('boot_storage_enable "$1"')


# --- השרת: ‏drivers.boot נשמר, פגום נזנח לבדו, כשל נרשם ביומן --------------

pytest.importorskip("fastapi")

from server import reports   # noqa: E402
from test_room_drivers import M70Q, _wave   # noqa: E402,F401
from test_server_room import CLONER1, cloner_hello, room, room_server   # noqa: E402,F401


def test_a_well_formed_boot_rides_along():
    value = {"state": "staged", "packages": ["p"],
             "boot": {"state": "failed", "service": "stornvme", "error": "x", "junk": 1}}
    assert reports.well_formed_drivers(value) == {
        "state": "staged", "packages": ["p"],
        "boot": {"state": "failed", "service": "stornvme", "error": "x"}}


@pytest.mark.parametrize("bad", ["enabled", {"state": "installed"}, {"service": "storahci"}, []])
def test_a_malformed_boot_is_dropped_and_the_staging_is_kept(bad):
    assert reports.well_formed_drivers({"state": "staged", "packages": ["p"], "boot": bad}) == {
        "state": "staged", "packages": ["p"]}


def test_a_failed_boot_storage_reaches_the_overview_and_the_journal(server):
    from test_server_api import hello, open_session   # noqa: PLC0415
    ids = open_session(server)
    hello(server, ids["mac1"])
    body = {"session_id": ids["session"], "mac": ids["mac1"], "state": "writing",
            "targets": [{"dev": "sda", "bytes_written": 1, "bytes_total": 2, "state": "writing"}]}
    assert server["anon"].post("/api/v1/agent/progress", json=body).status_code == 200
    boot = {"state": "failed", "service": "stornvme", "error": "the image has no stornvme service"}
    final = {**body, "state": "done", "drivers": {"state": "staged", "packages": ["p"], "boot": boot}}
    assert server["anon"].post("/api/v1/agent/progress", json=final).status_code == 200
    member = next(m for m in server["admin"].get("/api/console/overview").json()["session"]["members"]
                  if m["mac"] == ids["mac1"])
    assert member["drivers"]["boot"] == boot
    rows = [e for e in server["admin"].get("/api/console/journal").json()
            if e["event"] == "boot_storage_failed"]
    assert len(rows) == 1 and "stornvme" in rows[0]["text"]


def test_an_enabled_boot_storage_is_not_journaled(server):
    from test_server_api import hello, open_session   # noqa: PLC0415
    ids = open_session(server)
    hello(server, ids["mac1"])
    final = {"session_id": ids["session"], "mac": ids["mac1"], "state": "done",
             "targets": [{"dev": "sda", "bytes_written": 2, "bytes_total": 2, "state": "done"}],
             "drivers": {"state": "staged", "packages": ["p"],
                         "boot": {"state": "enabled", "service": "storahci"}}}
    assert server["anon"].post("/api/v1/agent/progress", json=final).status_code == 200
    events = [e["event"] for e in server["admin"].get("/api/console/journal").json()]
    assert "boot_storage_failed" not in events and "drivers_staged" in events


def test_a_drawers_failed_boot_storage_is_journaled_once_with_its_dev(room_server):
    _, wave = _wave(room_server, [{"mac": CLONER1, "port": 2, **M70Q}])
    anon = room_server["anon"]
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    boot = {"state": "failed", "service": "storahci", "error": "כוורת SYSTEM לא נמצאה"}
    body = {"session_id": wave, "mac": CLONER1, "state": "done", "targets": [
        {"dev": "sda", "bytes_written": 9, "bytes_total": 9, "state": "done"},
        {"dev": "sdb", "bytes_written": 9, "bytes_total": 9, "state": "done",
         "drivers": {"state": "staged", "packages": ["m70q-nic"], "boot": boot}},
    ]}
    assert anon.post("/api/v1/agent/progress", json=body).json() == {"ok": True}
    assert anon.post("/api/v1/agent/progress", json=body).json() == {"ok": True}
    machine = next(m for m in room(room_server["deploy"])["machines"] if m["mac"] == CLONER1)
    by_dev = {d["dev"]: d["drivers"] for d in machine["drawer_list"]}
    assert by_dev["sdb"]["boot"] == boot
    rows = [e for e in room_server["admin"].get("/api/console/journal").json()
            if e["event"] == "boot_storage_failed"]
    assert len(rows) == 1 and "sdb" in rows[0]["text"] and "כוורת SYSTEM" in rows[0]["text"]
