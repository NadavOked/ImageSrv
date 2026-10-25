"""‏#705: השרת אוכף שירותים לכל כרטיס רשת — ההחלטות, הנעילה העצמית והחזרה.

כאן הלוגיקה (`server/nic_services.py`) עם hooks מזויפים: כרטיסים, שעון,
מזהה אתחול, טיימר ושלושת שלבי האכיפה. הראיה שהמאזין באמת נסגר/נפתח על
סוקט — ב-`test_nic_listeners.py`; ה-API וה-RBAC — ב-`test_nic_services_api.py`.
"""

from __future__ import annotations

import json

import pytest

from server import dhcp, nic_services
from server.db import connect, get_setting
from server.nic_services import Baseline, NicServices, ServiceError

NICS = [
    {"name": "ens33", "state": "up", "mac": "00:50:56:a1:07:11", "addresses": ["10.10.10.8/24"]},
    {"name": "ens34", "state": "up", "mac": "00:50:56:a1:07:12", "addresses": ["10.44.0.1/24"]},
    {"name": "ens35", "state": "up", "mac": "00:50:56:a1:07:13", "addresses": ["10.20.0.8/24"]},
]
TODAY = Baseline(console="ens33", deploy="ens34", interserver="ens33")


class Clock:
    def __init__(self) -> None:
        self.t = 1_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture()
def rig(tmp_path):
    conn = connect(tmp_path / "imagectl.db")
    clock, scheduled, calls = Clock(), [], []
    boot = {"id": "boot-1"}

    def step(label):
        def run(nic):
            calls.append((label, nic.current_plan()))
            return rig_state["fail"].get(label)
        return run
    rig_state = {"fail": {}}
    svc = NicServices(conn, Baseline(**TODAY.__dict__), {
        "interfaces": lambda: [dict(n) for n in NICS], "now": clock,
        "boot_id": lambda: boot["id"], "schedule": lambda s, fn: scheduled.append((s, fn)),
        "listeners": step("listeners"), "dnsmasq": step("dnsmasq"),
        "timesync": step("timesync"), "firewall": step("firewall")})
    return {"svc": svc, "conn": conn, "clock": clock, "scheduled": scheduled,
            "calls": calls, "fail": rig_state["fail"], "boot": boot}


def events(conn, prefix="nic_services"):
    return [r["event"] for r in conn.execute(
        "SELECT event FROM journal WHERE event LIKE ? ORDER BY id", (prefix + "%",))]


# --- ברירות המחדל = היום -------------------------------------------------------


def test_defaults_are_todays_binds():
    assert nic_services.defaults("ens33", TODAY) == ["console", "monitor", "interserver"]
    assert nic_services.defaults("ens34", TODAY) == ["multicast", "netboot"]
    assert nic_services.defaults("ens35", TODAY) == []
    # בלי כרטיס שרתים ידוע (קונסולה על loopback) — אף כרטיס אינו מקבל קונסולה
    assert nic_services.defaults("ens33", Baseline(deploy="ens34")) == []


def test_an_upgraded_server_changes_nothing_until_the_operator_saves(rig):
    svc = rig["svc"]
    assert not svc.edited()
    assert svc.netboot() is None and svc.firewall_services() is None
    assert svc.refused("monitor", "10.10.10.8") is None
    assert svc.multicast_refused("ens34") is None and svc.multicast_refused("ens33") is None
    view = svc.view("10.10.10.8")
    rows = {r["name"]: r for r in view["nics"]}
    assert view["edited"] is False and view["you"]["interface"] == "ens33"
    assert rows["ens33"]["services"] == ["console", "monitor", "interserver"]
    assert rows["ens35"]["configured"] is False and rows["ens35"]["source"] == "default"
    assert rig["calls"] == []


def test_plan_maps_each_listener_to_the_addresses_of_its_nics():
    eff = {"ens33": ["monitor", "interserver"], "ens34": ["multicast", "netboot"],
           "ens35": ["console", "monitor"]}
    p = nic_services.plan(eff, {"ens33": "10.10.10.8", "ens34": "10.44.0.1", "ens35": "10.20.0.8"})
    assert p["hosts"]["http_console"] == ["10.20.0.8"]
    assert p["hosts"]["http_boot"] == ["10.44.0.1"] and p["hosts"]["interserver"] == ["10.10.10.8"]
    assert p["nics"]["monitor"] == ["ens33", "ens35"] and p["nics"]["multicast"] == ["ens34"]
    assert p["nics"]["interserver"] == ["ens33"]
    # כרטיס בלי כתובת אינו נקשר — ולא נקשר "לכל הכרטיסים" במקומו
    assert nic_services.plan({"ens35": ["console"]}, {"ens35": None})["hosts"]["http_console"] == []


# --- שמירה רגילה ---------------------------------------------------------------


def test_a_save_on_another_nic_applies_all_three_steps_and_journals(rig):
    svc = rig["svc"]
    out = svc.set("ens35", ["console", "monitor"], user_id="noc", local_address="10.10.10.8")
    assert out["ok"] and out["services"] == ["console", "monitor"]
    assert [c[0] for c in rig["calls"]] == ["listeners", "dnsmasq", "timesync", "firewall"]
    plan = rig["calls"][0][1]
    assert plan["hosts"]["http_console"] == ["10.10.10.8", "10.20.0.8"]
    assert svc.edited() and events(rig["conn"]) == ["nic_services_set"]
    assert svc.pending() is None


def test_ntp_is_default_off_and_can_be_enabled_per_nic(rig):
    svc = rig["svc"]
    assert all("ntp" not in row["services"] for row in svc.view()["nics"])
    out = svc.set("ens34", ["multicast", "netboot", "ntp"], local_address="10.10.10.8")
    assert out["ok"]
    plans = {name: plan for name, plan in rig["calls"]}
    assert plans["timesync"]["nics"]["ntp"] == ["ens34"]
    assert plans["firewall"]["nics"]["ntp"] == ["ens34"]


def test_services_are_validated(rig):
    with pytest.raises(ServiceError) as exc:
        rig["svc"].set("ens35", ["console", "ssh"])
    assert exc.value.status == 400
    with pytest.raises(ServiceError) as exc:
        rig["svc"].set("eth9", ["console"])
    assert exc.value.status == 404
    with pytest.raises(ServiceError) as exc:
        rig["svc"].set("ens35\nevil", ["console"])
    assert exc.value.status == 400
    assert not rig["svc"].edited()


def test_failed_apply_restores_the_previous_state_and_says_so(rig):
    svc = rig["svc"]
    rig["fail"]["listeners"] = "ens35: כתובת חדשה לא עלתה (address in use)"
    with pytest.raises(ServiceError) as exc:
        svc.set("ens35", ["console"], user_id="noc", local_address="10.10.10.8")
    assert exc.value.status == 502 and exc.value.code == "apply_failed"
    assert "address in use" in exc.value.message
    # ההגדרה הקודמת חזרה (ens35 שלא נשמר מעולם — חוזר לברירת המחדל) והוחלה שוב
    assert get_setting(rig["conn"], "nicsvc:ens35") is None and not svc.edited()
    assert [c[0] for c in rig["calls"]].count("listeners") == 2
    assert events(rig["conn"]) == ["nic_services_set", "nic_services_apply_failed"]


def test_a_step_that_is_not_wired_is_an_error_not_a_silent_success(rig):
    svc = rig["svc"]
    svc.hooks["firewall"] = None
    with pytest.raises(ServiceError) as exc:
        svc.set("ens35", ["console"], local_address="10.10.10.8")
    assert "חומת אש" in exc.value.message


# --- נעילה עצמית ---------------------------------------------------------------


def test_the_last_console_nic_cannot_turn_the_console_off(rig):
    svc = rig["svc"]
    with pytest.raises(ServiceError) as exc:
        svc.set("ens33", ["monitor", "interserver"], local_address="10.20.0.99")
    assert exc.value.status == 409 and exc.value.code == "last_console"
    assert "הפעל קונסולה בכרטיס אחר קודם" in exc.value.message
    assert not svc.edited() and rig["calls"] == []


def test_console_off_on_the_nic_you_are_on_needs_a_handoff(rig):
    svc = rig["svc"]
    svc.set("ens35", ["console", "monitor"], local_address="10.10.10.8")
    rig["calls"].clear()
    with pytest.raises(ServiceError) as exc:
        svc.set("ens33", ["monitor", "interserver"], local_address="10.10.10.8",
                console_url=lambda a: f"https://{a}:8081")
    assert exc.value.code == "needs_handoff"
    assert exc.value.extra["go"] == ["https://10.20.0.8:8081"]
    assert rig["calls"] == [] and json.loads(get_setting(rig["conn"], "nicsvc:ens33") or "null") is None


def test_unknown_path_counts_as_your_own_nic(rig):
    """עיקרון 5: "לא ידוע דרך איזה כרטיס" אינו "לא דרך הכרטיס הזה"."""
    svc = rig["svc"]
    svc.set("ens35", ["console"], local_address="127.0.0.1")
    with pytest.raises(ServiceError) as exc:
        svc.set("ens33", ["interserver"], local_address="testserver")
    assert exc.value.code == "needs_handoff"


def test_console_off_elsewhere_is_applied_at_once(rig):
    svc = rig["svc"]
    svc.set("ens35", ["console"], local_address="10.20.0.8")
    out = svc.set("ens33", ["monitor", "interserver"], local_address="10.20.0.8")
    assert out["ok"] and "pending" not in out and svc.pending() is None


def test_interserver_off_has_no_countdown(rig):
    """המתאם: כיבוי בין-שרתים אינו נועל את המפעיל — בלי 60 שנ'."""
    out = rig["svc"].set("ens33", ["console", "monitor"], local_address="10.10.10.8")
    assert out["ok"] and rig["svc"].pending() is None and rig["scheduled"] == []


# --- מסירה, אישור וחזרה ------------------------------------------------------


def handoff(rig):
    svc = rig["svc"]
    svc.set("ens35", ["console", "monitor"], local_address="10.10.10.8")
    rig["calls"].clear()
    out = svc.set("ens33", ["monitor", "interserver"], user_id="noc",
                  local_address="10.10.10.8", handoff=True)
    return svc, out


def test_handoff_answers_first_and_applies_after(rig):
    svc, out = handoff(rig)
    assert out["pending"]["seconds_left"] == 60 and out["pending"]["interface"] == "ens33"
    # מה שהקונסולה מציגה: מה הכרטיס נתן לפני — גם כשהרשומה הגולמית הייתה "ברירת מחדל"
    assert out["pending"]["previous"] == ["console", "monitor", "interserver"]
    assert out["pending"]["applied"] == ["monitor", "interserver"]
    assert rig["calls"] == [], "nothing is applied before the answer leaves"
    delays = [s for s, _fn in rig["scheduled"]]
    assert delays[0] == nic_services.APPLY_DELAY and delays[1] > 60
    rig["scheduled"][0][1]()                      # ההחלה הדחויה
    assert rig["calls"][0][1]["hosts"]["http_console"] == ["10.20.0.8"]
    assert svc.pending() is not None
    assert events(rig["conn"])[-2:] == ["nic_services_set", "nic_services_armed"]


def test_not_confirmed_in_time_reverts_by_itself(rig):
    svc, _out = handoff(rig)
    rig["scheduled"][0][1]()
    rig["calls"].clear()
    rig["clock"].t += 59
    assert svc.tick() is None and svc.pending() is not None
    rig["clock"].t += 2
    assert svc.tick() == "expired"
    assert get_setting(rig["conn"], "nicsvc:ens33") is None         # חזר לברירת המחדל
    assert rig["calls"][0][1]["hosts"]["http_console"] == ["10.10.10.8", "10.20.0.8"]
    assert svc.view()["last"]["event"] == "reverted"
    assert events(rig["conn"])[-1] == "nic_services_reverted"


def test_confirm_keeps_the_change(rig):
    svc, _out = handoff(rig)
    rig["clock"].t += 30
    assert svc.confirm("noc")["ok"]
    assert svc.pending() is None
    rig["clock"].t += 60
    assert svc.tick() is None
    assert json.loads(get_setting(rig["conn"], "nicsvc:ens33"))["services"] == ["monitor", "interserver"]
    assert svc.view()["last"]["event"] == "kept"


def test_confirm_after_the_window_is_refused(rig):
    svc, _out = handoff(rig)
    rig["clock"].t += 61
    with pytest.raises(ServiceError) as exc:
        svc.confirm("noc")
    assert exc.value.code == "expired"


def test_revert_now_and_a_second_change_while_pending(rig):
    svc, _out = handoff(rig)
    with pytest.raises(ServiceError) as exc:
        svc.set("ens34", ["multicast"], local_address="10.20.0.8")
    assert exc.value.code == "pending"
    assert svc.revert_now("noc")["ok"]
    assert svc.pending() is None and get_setting(rig["conn"], "nicsvc:ens33") is None


def test_failed_deferred_apply_reverts_and_is_told(rig):
    svc, _out = handoff(rig)
    rig["fail"]["firewall"] = "nft -c: syntax error"
    rig["scheduled"][0][1]()
    assert svc.pending() is None
    last = svc.view()["last"]
    assert last["event"] == "reverted" and last["reason"] == "apply_failed"
    assert any("nft -c" in e for e in last["errors"])


def test_a_reboot_during_the_window_reverts_at_startup_before_any_bind(rig):
    svc, _out = handoff(rig)
    rig["calls"].clear()
    rig["boot"]["id"] = "boot-2"
    assert svc.startup() == "boot"
    assert svc.reverted_at_startup and rig["calls"] == []   # הקשירה שאחרי תשקף את ה-DB
    assert get_setting(rig["conn"], "nicsvc:ens33") is None


def test_a_restart_inside_the_window_keeps_the_timer(rig):
    svc, _out = handoff(rig)
    rig["scheduled"].clear()
    rig["clock"].t += 20
    assert svc.startup() is None
    assert rig["scheduled"] and 40 <= rig["scheduled"][0][0] <= 41


def test_a_corrupt_marker_is_reported_and_reverted(rig):
    svc = rig["svc"]
    from server.db import set_setting
    set_setting(rig["conn"], nic_services.PENDING_KEY, "{not json")
    assert svc.view()["pending"]["corrupt"] is True
    assert svc.tick() == "expired" and svc.pending() is None


# --- שערי זמן-ריצה -------------------------------------------------------------


def test_monitor_gate_follows_the_nic_of_the_connection(rig):
    svc = rig["svc"]
    svc.set("ens35", ["console"], local_address="10.10.10.8")
    assert svc.refused("monitor", "10.10.10.8") is None
    assert "כבוי בכרטיס ens35" in svc.refused("monitor", "10.20.0.8")
    assert svc.refused("monitor", "127.0.0.1") is None
    assert "לא ניתן לקבוע" in svc.refused("monitor", "192.0.2.7")


def test_multicast_gate_refuses_a_nic_with_multicast_off(rig):
    svc = rig["svc"]
    svc.set("ens34", ["netboot"], local_address="10.10.10.8")
    assert "שידור כבוי בכרטיס ens34" in svc.multicast_refused("ens34")


def test_sender_fails_by_name_when_multicast_is_off(tmp_path):
    from server.sender import SenderEngine
    events_seen = []
    engine = SenderEngine(library=None, interface="ens34", simulate=True,
                          on_event=lambda e, d: events_seen.append((e, d)))
    engine.gate = lambda iface: f"שידור כבוי בכרטיס {iface}"
    engine.start({"id": "ses_1", "image_id": "img_1"})
    engine._thread.join(5)
    assert engine.status()["state"] == "failed"
    assert ("send_failed", "שידור כבוי בכרטיס ens34") in events_seen


# --- dnsmasq וחומת אש ----------------------------------------------------------


def test_dnsmasq_shuts_netboot_off_by_name_even_against_the_installer_file():
    cfg = dhcp.InterfaceConfig(name="ens34", enabled=True, range_start="10.44.0.100",
                               range_end="10.44.0.200", server_ip="10.44.0.1")
    legacy = dhcp.render([cfg], tftp_root="/srv/tftp")
    assert legacy == dhcp.render([cfg], tftp_root="/srv/tftp", netboot=None, nics=["ens33"])
    gated = dhcp.render([cfg], tftp_root="/srv/tftp", netboot=["ens35"],
                        nics=["ens33", "ens34", "ens35"])
    assert "except-interface=ens34" in gated and "except-interface=ens33" in gated
    assert "dhcp-range=set:if-ens34" not in gated, "a NIC with netboot off hands out nothing"
    assert "interface=ens35" in gated
    none = dhcp.render([cfg], tftp_root="/srv/tftp", netboot=[], nics=["ens34"])
    assert "interface=lo" in none, "never a file with no interface= (dnsmasq would bind all)"


def test_firewall_services_and_deploy_completion_carry_the_per_nic_choice(rig, tmp_path):
    from server import deploy_net
    svc = rig["svc"]
    svc.set("ens35", ["console"], local_address="10.10.10.8")
    per = dict(svc.firewall_services())
    assert per["ens35"] == ["console"] and per["ens34"] == ["multicast", "netboot"]
    seen = {}
    ctx = deploy_net.DeployContext(
        state=deploy_net.DeployState("none", None, None), agent_port=8080,
        tftp_root=tmp_path, repo_dir=tmp_path, servers_interface="ens33",
        services=svc.firewall_services,
        hooks={"write_grub_cfg": lambda *a: None, "enable_dnsmasq": lambda: None,
               "firewall": lambda *a, **k: seen.update(k),
               "restart_server": lambda: None})
    svc.base.deploy = None            # רשת ההפצה טרם הוגדרה — ens34 נעשה הפצה עכשיו
    assert deploy_net.complete(ctx, rig["conn"], interface="ens34", server_ip="10.44.0.1")["ok"]
    per = dict(seen["services"])
    assert per["ens34"] == ["multicast", "netboot"], "the NIC being completed gets today's deploy"
    assert per["ens33"] == ["console", "monitor", "interserver"] and per["ens35"] == ["console"]


def test_listening_is_measured_from_the_sockets_not_the_setting(rig):
    svc = rig["svc"]
    svc.hooks["bound"] = lambda: {"http_console": ["10.10.10.8", "127.0.0.1"], "http_boot": []}
    rows = {r["name"]: r for r in svc.view()["nics"]}
    assert rows["ens33"]["listening"]["console"] is True
    assert rows["ens33"]["listening"]["monitor"] is True, "the monitor rides the console socket"
    assert rows["ens34"]["listening"]["netboot"] is False, "on in the setting, not bound = said"
    assert rows["ens34"]["listening"]["multicast"] is None, "no listener to measure"
    assert rows["ens33"]["listening"]["interserver"] is None, "no interserver listener here"
    svc.hooks["bound"] = lambda: (_ for _ in ()).throw(OSError("boom"))
    rows = {r["name"]: r for r in svc.view()["nics"]}
    assert rows["ens33"]["listening"]["console"] is None, "could not measure ≠ not listening"


# --- main(): הקשירה בעלייה --------------------------------------------------


def _run_main(tmp_path, monkeypatch, nic):
    """כמו `test_console_bind.run_main`, עם runtime שנושא `nic_services` אמיתי."""
    import asyncio
    from types import SimpleNamespace

    import uvicorn

    import server.app
    import server.main
    configs: dict[str, list[str]] = {}

    def fake_config(app, **kwargs):
        configs.setdefault(app, []).append(kwargs["host"])
        return object()
    rt = SimpleNamespace(ctx=SimpleNamespace(nic_services=nic), conn=nic.conn)
    monkeypatch.setattr(server.app, "create_runtime", lambda *a, **k: rt)
    monkeypatch.setattr(server.app, "create_agent_app", lambda rt: "agent")
    monkeypatch.setattr(server.app, "create_console_app", lambda rt: "console")
    monkeypatch.setattr(uvicorn, "Config", fake_config)
    monkeypatch.setattr(uvicorn, "Server", lambda config: object())
    monkeypatch.setattr(server.main, "serve_all", lambda listeners: None)
    monkeypatch.setattr(server.main, "_primary_alive", lambda *a, **k: None)
    monkeypatch.setattr(server.main, "_owner_nic", lambda address: {
        "10.10.10.8": "ens33", "10.44.0.1": "ens34"}.get(address))
    monkeypatch.setattr(asyncio, "run", lambda coro=None: None)
    monkeypatch.setattr(server.main, "_interface_for", lambda url: None)
    monkeypatch.setattr("sys.argv", [
        "server.main", "--server-url", "http://10.44.0.1:8080", "--host", "10.44.0.1",
        "--console-host", "10.10.10.8",
        "--data-dir", str(tmp_path / "data"), "--images", str(tmp_path / "img")])
    server.main.main()
    return configs


def test_main_binds_as_today_until_a_nic_is_saved(rig, tmp_path, monkeypatch):
    nic = rig["svc"]
    nic.base = Baseline()                 # main גוזר אותו בעצמו מהדגלים
    configs = _run_main(tmp_path, monkeypatch, nic)
    assert configs["console"] == ["10.10.10.8", "127.0.0.1"]
    assert configs["agent"] == ["10.44.0.1"]
    assert nic.base.console == "ens33", "the baseline is derived from the flags"


def test_main_binds_per_nic_once_edited(rig, tmp_path, monkeypatch):
    from server.db import set_setting
    nic = rig["svc"]
    nic.base = Baseline()
    set_setting(rig["conn"], "nicsvc:ens35", json.dumps({"services": ["console", "netboot"]}))
    configs = _run_main(tmp_path, monkeypatch, nic)
    assert configs["console"] == ["10.10.10.8", "10.20.0.8", "127.0.0.1"]
    assert configs["agent"][-2:] == ["10.44.0.1", "10.20.0.8"], configs["agent"]
    set_setting(rig["conn"], "nicsvc:ens33", json.dumps({"services": ["monitor"]}))
    configs = _run_main(tmp_path, monkeypatch, nic)
    assert "10.10.10.8" not in configs["console"], "console off on ens33 = not bound there"
