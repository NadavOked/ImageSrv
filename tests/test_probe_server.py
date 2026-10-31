"""‏#1049 שלב ב' — השרת שומר את ה-`probe` מה-hello, מציג אותו, ומחשב שערים.

צד הסוכן (שלב א') נבדק ב-`test_probe.py`. כאן: הצורה הקנונית, ההיסטוריה
המגורסת (שדה נדיף אינו גרסה — אבל נשמר), הזנחת probe פגום,
‏`GET /api/console/machines`, ‏RBAC (deploy רואה — זה מידע, לא פעולה),
וכל שער בשלושה מצבים: נמדד-רע / נמדד-טוב / לא-נבדק.
"""

from __future__ import annotations

import copy
import json

import pytest

pytest.importorskip("fastapi")

from conftest import setup_classroom  # noqa: E402
from server.agent import probe  # noqa: E402

MAC = "b4:2e:99:07:1a:c4"

GOOD = {
    "power": {"on_battery": False, "supply": "AC"},
    "rtc": {"hwclock_epoch": 1758124800, "system_epoch": 1758124812, "skew_seconds": -12},
    "cpu": {"model": "Intel Core i5-8500", "cores": 6, "microcode": "0xf4",
            "vulnerabilities": {"meltdown": "Mitigation: PTI"}},
    "memory": {"total_bytes": 8589934592, "dimms": None, "ecc": None},
    "thermal": [{"type": "x86_pkg_temp", "temp_c": 42}],
    "nic": [{"name": "eth0", "speed_mbps": 1000, "duplex": "full",
             "stats": {"rx_crc_errors": 0, "rx_dropped": 0, "tx_errors": 0, "collisions": 0},
             "ip_conflict": {"checked": True, "duplicate": False}}],
    "pci_without_driver": [],
    "kernel": {"lockdown": "[none] integrity confidentiality", "taint": 0},
    "pstore": {"crashed": False, "files": [], "excerpt": None},
    "oem_key": "XXXXX-XXXXX-XXXXX-XXXXX-XXXXX",
    "disks": [{"name": "sda", "nvme_smart": None, "smart_errors": {"count": 0}}],
    "encryption": [],
    "probe_seconds": 0,
    "netprobe": None,
    "ntfs": None,    # #980 §6
}


def _with(**fields) -> dict:
    out = copy.deepcopy(GOOD)
    out.update(fields)
    return out


# --- הצורה ------------------------------------------------------------------


def test_a_well_formed_probe_keeps_all_three_states_as_they_are():
    p = probe.well_formed(_with(power=None, rtc={"error": "since_epoch unreadable"}))
    assert p["power"] is None                             # לא נבדק
    assert p["rtc"] == {"error": "since_epoch unreadable"}   # נכשל
    assert p["cpu"]["cores"] == 6                         # נמדד
    assert set(p) == set(probe.KNOWN_KEYS)


def test_unknown_keys_are_dropped_and_missing_keys_are_null():
    p = probe.well_formed({"power": {"on_battery": True}, "bogus": 1})
    assert "bogus" not in p
    assert p["power"] == {"on_battery": True}
    assert p["rtc"] is None and p["probe_seconds"] is None


def test_strings_and_lists_are_capped():
    p = probe.well_formed(_with(oem_key="k" * 500, pci_without_driver=["x"] * 100,
                                pstore={"crashed": True, "files": [], "excerpt": "e" * 400}))
    assert len(p["oem_key"]) == probe.STR_LIMIT
    assert len(p["pci_without_driver"]) == probe.LIST_LIMIT
    assert len(p["pstore"]["excerpt"]) == probe.STR_LIMIT


@pytest.mark.parametrize("bad", [None, [], "x", 5, True], ids=str)
def test_a_probe_that_is_not_an_object_is_none(bad):
    assert probe.well_formed(bad) is None


# --- היסטוריה מגורסת --------------------------------------------------------


def test_the_same_probe_twice_is_one_row(server):
    conn = server["ctx"].conn
    assert probe.latest(conn, MAC) is None
    assert probe.record(conn, MAC, probe.well_formed(GOOD)) is True
    assert probe.record(conn, MAC, probe.well_formed(GOOD)) is False
    assert len(probe.history(conn, MAC)) == 1


def test_a_stable_change_opens_a_new_version(server):
    conn = server["ctx"].conn
    probe.record(conn, MAC, probe.well_formed(GOOD))
    changed = _with(cpu={**GOOD["cpu"], "microcode": "0xf6"})
    assert probe.record(conn, MAC, probe.well_formed(changed)) is True
    hist = probe.history(conn, MAC)
    assert [h["probe"]["cpu"]["microcode"] for h in hist] == ["0xf6", "0xf4"]
    assert probe.latest(conn, MAC)["probe"]["cpu"]["microcode"] == "0xf6"
    assert set(probe.latest_all(conn)) == {MAC}


def test_only_volatile_fields_changing_is_one_row_but_the_sample_is_refreshed(server, monkeypatch):
    """‏probe_seconds, מספרי rtc, temp_c ומוני הרשת משתנים בכל דגימה: הם
    אינם גרסה — אבל השער (סטיית שעון, CRC) נמדד מהדגימה **האחרונה**."""
    conn = server["ctx"].conn
    probe.record(conn, MAC, probe.well_formed(GOOD))
    first = probe.latest(conn, MAC)
    monkeypatch.setattr(probe, "now_iso", lambda: "2030-01-01T00:00:00+00:00")
    later = _with(probe_seconds=2,
                  rtc={"hwclock_epoch": 1758125000, "system_epoch": 1758125900, "skew_seconds": -900},
                  thermal=[{"type": "x86_pkg_temp", "temp_c": 71}])
    later["nic"][0]["stats"]["rx_crc_errors"] = 7
    assert probe.record(conn, MAC, probe.well_formed(later)) is False
    assert len(probe.history(conn, MAC)) == 1
    now = probe.latest(conn, MAC)
    assert now["probe"]["rtc"]["skew_seconds"] == -900
    assert now["probe"]["thermal"][0]["temp_c"] == 71
    assert now["probe"]["nic"][0]["stats"]["rx_crc_errors"] == 7
    assert now["probe"]["probe_seconds"] == 2
    assert now["seen_at"] == first["seen_at"]        # הגרסה נראתה לראשונה — לא זז
    assert now["sampled_at"] != first["sampled_at"]  # הדגימה — זזה


# --- hello → /machines -------------------------------------------------------


def _hello(server, **fields):
    return server["anon"].post("/api/v1/agent/hello", json={
        "schema": 2, "mac": MAC, "all_macs": [MAC], "ip": "10.44.12.187",
        "disks": [], **fields})


def _machine(server, client="admin") -> dict:
    return next(m for m in server[client].get("/api/console/machines").json()
                if m["mac"] == MAC)


def test_hello_stores_the_probe_and_machines_returns_it(server):
    setup_classroom(server)
    assert _hello(server, probe=GOOD).status_code == 200
    m = _machine(server)
    assert m["probe"] == GOOD
    assert m["probe_seen_at"]
    assert m["probe_verdicts"] == []


def test_a_malformed_probe_does_not_erase_the_previous_sample(server):
    setup_classroom(server)
    assert _hello(server, probe=GOOD).status_code == 200
    assert _hello(server, probe="broken").status_code == 200
    assert _hello(server, probe=[1, 2]).status_code == 200
    assert _hello(server).status_code == 200                    # סוכן ישן — בלי השדה
    assert _machine(server)["probe"] == GOOD


def test_a_machine_that_never_reported_shows_null_not_empty(server):
    setup_classroom(server)
    m = _machine(server)
    assert m["probe"] is None and m["probe_seen_at"] is None
    assert m["probe_verdicts"] == []


def test_deploy_has_no_console_so_the_probe_is_admin_only_on_the_web(server):
    """‏#1073 (18/09): למשתמש הפצה אין קונסולה — הכניסה עצמה מסורבת (403), ולכן
    ה-probe והחיוויים נראים בוובי רק ל-admin. (עד 18/09 הטסט הזה הוכיח ההפך.)"""
    setup_classroom(server)
    _hello(server, probe=_with(power={"on_battery": True, "supply": "BAT0"}))
    m = _machine(server)
    assert m["probe"]["power"]["on_battery"] is True
    assert [v["key"] for v in m["probe_verdicts"]] == ["battery"]
    r = server["deploy"].get("/api/console/machines")
    assert r.status_code == 403, r.text   # console_only (#1073): גם עם עוגייה — אין קונסולה


def test_the_verdicts_follow_the_latest_sample_not_the_first(server):
    """שעון שסטה אחרי הדגימה הראשונה: אותה גרסה, שער חדש."""
    setup_classroom(server)
    _hello(server, probe=GOOD)
    assert _machine(server)["probe_verdicts"] == []
    _hello(server, probe=_with(rtc={"hwclock_epoch": 1, "system_epoch": 100000, "skew_seconds": -99999}))
    m = _machine(server)
    assert [v["key"] for v in m["probe_verdicts"]] == ["rtc"]
    assert len(probe.history(server["ctx"].conn, MAC)) == 1


# --- השערים: שלושה מצבים לכל אחד --------------------------------------------


def _keys(p: dict) -> list[str]:
    return [v["key"] for v in probe.verdicts(p)]


def _levels(p: dict) -> dict[str, str]:
    return {v["key"]: v["level"] for v in probe.verdicts(p)}


def test_no_probe_means_no_verdicts():
    assert probe.verdicts(None) == []
    assert probe.verdicts("x") == []


def test_a_clean_measured_probe_has_no_verdicts():
    assert probe.verdicts(GOOD) == []


@pytest.mark.parametrize("value, expect", [
    ({"on_battery": True, "supply": "BAT0"}, ["battery"]),   # נמדד — רע
    ({"on_battery": False, "supply": "AC"}, []),              # נמדד — טוב
    (None, []),                                               # לא נבדק ≠ תקין, אבל גם לא שער
    ({"error": "no readable supply"}, []),                    # נכשל — לא שער, מוצג בכרטיס
], ids=["bad", "good", "null", "error"])
def test_battery_gate(value, expect):
    p = _with(power=value)
    assert _keys(p) == expect
    if expect:
        v = probe.verdicts(p)[0]
        assert v["level"] == "err" and "סוללה" in v["text_he"]


@pytest.mark.parametrize("value, expect", [
    ({"hwclock_epoch": 0, "system_epoch": 0, "skew_seconds": 301}, ["rtc"]),
    ({"hwclock_epoch": 0, "system_epoch": 0, "skew_seconds": -7200}, ["rtc"]),   # לשני הכיוונים
    ({"hwclock_epoch": 0, "system_epoch": 0, "skew_seconds": 300}, []),         # בדיוק על הסף — לא
    ({"hwclock_epoch": 0, "system_epoch": 0, "skew_seconds": -12}, []),
    (None, []),
    ({"error": "garbage"}, []),
], ids=["over", "over-negative", "at-threshold", "good", "null", "error"])
def test_cmos_gate(value, expect):
    p = _with(rtc=value)
    assert _keys(p) == expect
    if expect:
        v = probe.verdicts(p)[0]
        assert v["level"] == "warn" and "BIOS" in v["text_he"]


def test_cmos_text_names_the_skew_in_human_units():
    assert "2 שעות" in probe.verdicts(_with(rtc={"skew_seconds": -7200}))[0]["text_he"]
    assert "3 ימים" in probe.verdicts(_with(rtc={"skew_seconds": 3 * 86400}))[0]["text_he"]
    assert "6 דק'" in probe.verdicts(_with(rtc={"skew_seconds": 400}))[0]["text_he"]


def _nvme(smart) -> dict:
    return _with(disks=[{"name": "nvme0n1", "nvme_smart": smart, "smart_errors": None}])


@pytest.mark.parametrize("smart, expect", [
    ({"critical_warning": 1, "percentage_used": 5, "media_errors": 0, "unsafe_shutdowns": 0}, {"nvme:nvme0n1": "err"}),
    ({"critical_warning": 0, "percentage_used": 5, "media_errors": 3, "unsafe_shutdowns": 0}, {"nvme:nvme0n1": "err"}),
    ({"critical_warning": 0, "percentage_used": 90, "media_errors": 0, "unsafe_shutdowns": 0}, {"nvme:nvme0n1": "warn"}),
    ({"critical_warning": 0, "percentage_used": 89, "media_errors": 0, "unsafe_shutdowns": 0}, {}),
    (None, {}),                                                # SATA, או nvme לא ארוז
    ({"error": "not json"}, {}),
], ids=["critical", "media", "worn", "good", "null", "error"])
def test_nvme_gate(smart, expect):
    assert _levels(_nvme(smart)) == expect


def test_nvme_err_wins_over_worn_for_the_same_disk():
    smart = {"critical_warning": 0, "percentage_used": 95, "media_errors": 2, "unsafe_shutdowns": 0}
    assert _levels(_nvme(smart)) == {"nvme:nvme0n1": "err"}
    assert "2 שגיאות מדיה" in probe.verdicts(_nvme(smart))[0]["text_he"]


@pytest.mark.parametrize("value, expect", [
    ({"crashed": True, "files": ["dmesg-ramoops-0"], "excerpt": "panic"}, ["pstore"]),
    ({"crashed": False, "files": [], "excerpt": None}, []),
    (None, []),
    ({"error": "unreadable"}, []),
], ids=["crashed", "clean", "null", "error"])
def test_pstore_gate(value, expect):
    p = _with(pstore=value)
    assert _keys(p) == expect
    if expect:
        assert probe.verdicts(p)[0]["level"] == "warn"


def _nic(**fields) -> dict:
    nic = copy.deepcopy(GOOD["nic"][0])
    nic.update(fields)
    return _with(nic=[nic])


@pytest.mark.parametrize("conflict, expect", [
    ({"checked": True, "duplicate": True}, {"ip_conflict:eth0": "err"}),
    ({"checked": True, "duplicate": False}, {}),
    (None, {}),                                   # arping לא ארוז
    ({"error": "arping rc 4"}, {}),
], ids=["dup", "clean", "null", "error"])
def test_ip_conflict_gate(conflict, expect):
    assert _levels(_nic(ip_conflict=conflict)) == expect


@pytest.mark.parametrize("stats, expect", [
    ({"rx_crc_errors": 3, "rx_dropped": 0, "tx_errors": 0, "collisions": 0}, {"crc:eth0": "warn"}),
    ({"rx_crc_errors": 0, "rx_dropped": 9, "tx_errors": 0, "collisions": 0}, {}),   # dropped אינו שער
    (None, {}),
    ({"error": "no statistics"}, {}),
], ids=["crc", "clean", "null", "error"])
def test_crc_gate(stats, expect):
    assert _levels(_nic(stats=stats)) == expect


def test_verdicts_are_json_and_carry_key_level_text():
    p = _with(power={"on_battery": True, "supply": "BAT0"},
              rtc={"skew_seconds": 9999}, pstore={"crashed": True, "files": [], "excerpt": None})
    out = json.loads(json.dumps(probe.verdicts(p), ensure_ascii=False))
    assert [v["key"] for v in out] == ["battery", "rtc", "pstore"]
    assert all(set(v) == {"key", "level", "text_he"} and v["level"] in ("warn", "err") for v in out)


# --- #1048 netprobe (כבל + LLDP) --------------------------------------------


def test_well_formed_keeps_netprobe_in_all_three_states():
    measured = {"cable": {"status": "ok", "pairs": []},
                "lldp": {"switch": "sw-lab-1", "chassis": "aa:bb:cc:dd:ee:ff",
                         "port": "Gi1/0/12", "port_desc": "lab", "ttl": 120}}
    p = probe.well_formed(_with(netprobe=measured))
    assert p["netprobe"]["lldp"]["switch"] == "sw-lab-1"
    assert probe.well_formed(_with(netprobe=None))["netprobe"] is None
    assert probe.well_formed(_with(netprobe={"error": "no iface"}))["netprobe"] == {"error": "no iface"}


def _np(cable=None, lldp=None) -> dict:
    return _with(netprobe={"cable": cable, "lldp": lldp})


@pytest.mark.parametrize("cable, expect", [
    ({"status": "open", "pairs": [{"pair": "B", "code": "Open", "length_m": 12}]}, ["cable"]),
    ({"status": "short", "pairs": [{"pair": "A", "code": "Short", "length_m": 3}]}, ["cable"]),
    ({"status": "ok", "pairs": [{"pair": "A", "code": "OK", "length_m": None}]}, []),
    ({"skipped": "link up"}, []),
    (None, []),
    ({"error": "ethtool --cable-test rc 1"}, []),
], ids=["open", "short", "ok", "skipped", "null", "error"])
def test_cable_gate_three_states(cable, expect):
    p = _np(cable=cable, lldp={"unheard": True, "waited_s": 35})
    assert _keys(p) == expect
    if expect:
        v = probe.verdicts(p)[0]
        assert v["level"] == "err" and v["key"] == "cable" and "כבל פגום" in v["text_he"]


def test_cable_verdict_names_the_pair_and_length():
    p = _np(cable={"status": "open", "pairs": [{"pair": "B", "code": "Open", "length_m": 12}]})
    assert "זוג B פתוח ב-12 מטר" in probe.verdicts(p)[0]["text_he"]
    p = _np(cable={"status": "short", "pairs": [{"pair": "A", "code": "Short", "length_m": 3}]})
    assert "זוג A קצר ב-3 מטר" in probe.verdicts(p)[0]["text_he"]


def test_lldp_unheard_is_not_a_verdict():
    assert probe.verdicts(_np(lldp={"unheard": True, "waited_s": 35})) == []
    assert probe.verdicts(_np(lldp={"error": "no such interface"})) == []
    assert probe.verdicts(_np(lldp=None)) == []


def test_hello_sibling_netprobe_is_stored_inside_probe(server):
    """netprobe מגיע ב-hello כשדה אח ונשמר בתוך אותה שורת machine_probe."""
    setup_classroom(server)
    np = {"cable": {"skipped": "link up"},
          "lldp": {"switch": "sw-lab-1", "chassis": "aa:bb:cc:dd:ee:ff",
                   "port": "Gi1/0/12", "port_desc": "lab", "ttl": 120}}
    body = {k: v for k, v in GOOD.items() if k != "netprobe"}
    assert _hello(server, probe=body, netprobe=np).status_code == 200
    m = _machine(server)
    assert m["probe"]["netprobe"]["lldp"]["switch"] == "sw-lab-1"
    assert m["probe"]["netprobe"]["cable"]["skipped"] == "link up"
    assert m["probe_verdicts"] == []
