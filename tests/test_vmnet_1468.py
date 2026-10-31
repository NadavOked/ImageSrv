"""‏#1468 — ל-VM לעדכון זהות רשת משלו: macvtap על "כרטיס לעדכוני VM" עם MAC
יציב שהשרת ייצר, ו-DHCPACK כראיה לכתובת (הכרעת נדב 16/10).

מה נבדק כאן:
* הבחירה: כרטיס ההפצה (וכל מה שמעליו/מתחתיו — bond/VLAN) מסורב בשם; כרטיס
  הניהול **מתקבל** (רשת הניהול יכולה להיות גם רשת הכיתות); לא נבחר = סירוב.
* ה-MAC: unicast, מנוהל מקומית, נוצר פעם אחת ונשאר אותו אחרי "אתחול".
* QEMU: ‏`-netdev tap,fd=` עם ה-MAC — בלי slirp, ‏hostfwd/guestfwd/smb.
* אין lease → סירוב בשם עם הרמז לפלטפורמה; יש lease → הכתובת במסך ובחומת האש.
* הכלים עצמם (`vmnet`): פענוח DHCP, ההאזנה, ‏macvtap שלא נקרא בחזרה, ‏sysfs.
"""

from __future__ import annotations

import json
import socket
import struct
import subprocess
import time
from pathlib import Path

import pytest

from server.vm import vmnet, vmupdate
from server.db import get_setting, set_setting
from test_vmupdate import LEASE, TAP_FD, FakeMachine, events, lab, refusal, wait_for  # noqa: F401

MAC = "0a:1b:2c:3d:4e:5f"
OTHER = "0a:1b:2c:3d:4e:60"


# --- הבחירה --------------------------------------------------------------------------


def test_the_deploy_card_is_refused_by_name(lab):
    set_setting(lab["conn"], vmupdate.NIC_KEY, "eth1")
    engine = lab["make"]()                       # deploy_interface = eth1
    exc = refusal(engine)
    assert (exc.status, exc.reason) == (409, "vm_nic_is_deploy")
    assert "eth1" in exc.message and "כרטיס ההפצה" in exc.message
    assert lab["qemu"].procs == [] and engine.machine.taps == []
    with pytest.raises(vmupdate.Refusal) as caught:
        engine.set_network("eth1", "noc")
    assert caught.value.reason == "vm_nic_is_deploy"


def test_a_member_of_the_deploy_bond_is_refused(lab):
    engine = lab["make"](FakeMachine(stacked={"eth2": {"bond0", "eth1"}}),
                         deploy_interface=lambda: "bond0")
    with pytest.raises(vmupdate.Refusal) as caught:
        engine.set_network("eth2", "noc")
    assert caught.value.reason == "vm_nic_is_deploy" and "bond0" in caught.value.message


def test_a_card_whose_stack_was_not_read_is_refused_not_assumed_clean(lab):
    engine = lab["make"](FakeMachine(stacked={"eth2": "unreadable"}))
    with pytest.raises(vmupdate.Refusal) as caught:
        engine.set_network("eth2", "noc")
    assert caught.value.reason == "vm_nic_unchecked"


def test_the_management_card_is_accepted(lab):
    """תוספת נדב 16/10: רשת הניהול יכולה להיות גם רשת הכיתות — ה-VM על אותו
    כרטיס שהשרת משתמש בו. ההפרדה מארח↔אורח היא של macvtap, לא של הבחירה."""
    engine = lab["make"](deploy_interface=lambda: "eth1")
    view = engine.set_network("eth0", "noc")         # eth0 = כרטיס הניהול
    assert view["interface"] == "eth0" and view["problem"] is None
    engine.start("img_7f3a91", "noc")
    wait_for(engine, "running")
    assert engine.machine.taps == [("eth0", view["mac"])]
    assert "vmupdate_network_set" in events(lab["conn"])


def test_no_card_chosen_is_a_named_refusal(lab):
    set_setting(lab["conn"], vmupdate.NIC_KEY, "")
    exc = refusal(lab["make"]())
    assert (exc.status, exc.reason) == (409, "vm_nic_unset")


def test_an_absent_card_is_refused(lab):
    with pytest.raises(vmupdate.Refusal) as caught:
        lab["make"]().set_network("eth9", "noc")
    assert (caught.value.status, caught.value.reason) == (404, "vm_nic_missing")


@pytest.mark.parametrize("name", ["lo", vmnet.TAP_NAME, 'eth0" drop', "", 7])
def test_names_that_cannot_be_a_vm_card(lab, name):
    with pytest.raises(vmupdate.Refusal) as caught:
        lab["make"]().set_network(name, "noc")
    assert caught.value.reason == "vm_nic_invalid"


# --- ה-MAC ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [b"\xff" * 6, b"\x00" * 6, b"\x01\x02\x03\x04\x05\x06"])
def test_a_new_mac_is_unicast_and_locally_administered(raw):
    mac = vmnet.new_mac(lambda n: raw[:n])
    first = int(mac[:2], 16)
    assert first & 0x01 == 0 and first & 0x02 == 0x02
    assert vmnet.valid_mac(mac) and mac[3:] == ":".join(f"{b:02x}" for b in raw[1:])


def test_the_mac_is_created_once_and_survives_a_restart(lab):
    first = lab["make"]().ensure_mac()
    assert vmnet.valid_mac(first)
    again = lab["make"]()                            # שרת שעלה מחדש: מנוע חדש, אותו DB
    assert again.ensure_mac() == first == get_setting(lab["conn"], vmupdate.MAC_KEY)
    assert again.network_view()["mac"] == first
    assert events(lab["conn"]).count("vmupdate_mac_created") == 1


@pytest.mark.parametrize("mac", ["51:54:00:13:34:01", "00:11:22:33:44:55",
                                 "0A:1B:2C:3D:4E:5F", "0a1b2c3d4e5f", None])
def test_valid_mac_rejects_anything_that_is_not_ours(mac):
    assert vmnet.valid_mac(mac) is False


# --- QEMU ------------------------------------------------------------------------------


def test_qemu_gets_the_macvtap_fd_and_the_mac_and_nothing_else(tmp_path):
    argv = vmupdate.qemu_argv(workdir=tmp_path, memory_mib=1024, cpus=1, accel="kvm",
                              secure_boot=False, tap_fd=TAP_FD, mac=MAC)
    nets = [argv[i + 1] for i, a in enumerate(argv) if a == "-netdev"]
    assert nets == [f"tap,id=net0,fd={TAP_FD}"]
    assert f"e1000e,netdev=net0,mac={MAC},romfile=" in argv
    joined = " ".join(argv)
    for forbidden in ("user,", "hostfwd", "guestfwd", "smb", "bridge", "ifname=", "script=",
                      "vhost", "tcp:"):
        assert forbidden not in joined, forbidden


@pytest.mark.parametrize("fd,mac", [(None, MAC), (1, MAC), (TAP_FD, "01:00:00:00:00:01")])
def test_qemu_argv_refuses_a_bad_fd_or_mac(tmp_path, fd, mac):
    with pytest.raises(ValueError):
        vmupdate.qemu_argv(workdir=tmp_path, memory_mib=1024, cpus=1, accel="kvm",
                           secure_boot=False, tap_fd=fd, mac=mac)


# --- ה-lease ------------------------------------------------------------------------------


@pytest.mark.parametrize("virt,needle", [
    ("vmware", "Forged transmits"), ("microsoft", "MAC address spoofing"),
    ("none", "ברזל"), (None, "Hyper-V")])
def test_no_lease_is_a_named_refusal_with_the_platform_hint(lab, virt, needle):
    machine = FakeMachine(lease={"address": None, "server": None, "requests": 3, "error": None},
                          platform=virt)
    engine = lab["make"](machine, lease_timeout=1)
    engine.start("img_7f3a91", "noc")
    status = wait_for(engine, "failed")
    assert status["error"]["reason"] == "no_lease"
    message = status["error"]["message"]
    assert "ביקש כתובת" in message and "3 בקשות" in message and needle in message
    assert status["network"]["state"] == "no_lease" and status["disk_ready"] is True
    assert "quit" in lab["qemu"].qmp_calls
    assert "vmupdate_no_lease" in events(lab["conn"])
    assert machine.downs >= 1 and machine.firewall_offs >= 1     # ה-macvtap והטבלה ירדו


def test_no_request_at_all_says_so_differently(lab):
    machine = FakeMachine(lease={"address": None, "server": None, "requests": 0, "error": None})
    engine = lab["make"](machine, lease_timeout=1)
    engine.start("img_7f3a91", "noc")
    message = wait_for(engine, "failed")["error"]["message"]
    assert "לא ביקש כתובת" in message and "כתובת קבועה" in message


def test_a_listener_that_could_not_listen_is_not_a_quiet_network(lab):
    machine = FakeMachine(lease={"address": None, "server": None, "requests": 0,
                                 "error": "Operation not permitted"})
    engine = lab["make"](machine, lease_timeout=1)
    engine.start("img_7f3a91", "noc")
    message = wait_for(engine, "failed")["error"]["message"]
    assert "לא ניתן לבדוק" in message and "Operation not permitted" in message


def wait_address(engine, address, timeout=10.0):
    deadline = time.monotonic() + timeout
    while (engine.status()["network"] or {}).get("address") != address:
        assert time.monotonic() < deadline, engine.status()["network"]
        time.sleep(0.02)
    return engine.status()["network"]


def test_a_lease_is_shown_and_the_host_filter_never_depends_on_it(lab):
    """סקירת אבטחה 16/10: הכתובת מה-DHCP **מוצגת** בלבד. חומת האש במארח היא לפי
    MAC — אין `ip saddr` שתוקף בוילן יכול לבחור בעזרת ACK מזויף."""
    machine = FakeMachine(lease=LEASE)
    engine = lab["make"](machine)
    engine.start("img_7f3a91", "noc")
    wait_for(engine, "running")
    mac = engine.ensure_mac()
    net = wait_address(engine, "10.20.30.40")
    assert (net["state"], net["server"], net["mac"], net["interface"]) == (
        "leased", "10.20.30.1", mac, "eth0")
    engine.maintenance(guard=True)
    assert machine.firewalls and set(machine.firewalls) == {(999, mac)}   # רק MAC, תמיד
    record = json.loads(get_setting(lab["conn"], vmupdate.LEASE_KEY))
    assert record["address"] == "10.20.30.40" and record["mac"] == mac
    assert engine.network_view()["lease"]["address"] == "10.20.30.40"
    assert "vmupdate_lease" in events(lab["conn"])


def test_a_renewal_with_a_new_address_updates_what_is_shown(lab):
    second = {**LEASE, "address": "10.20.30.77"}
    engine = lab["make"](FakeMachine(lease=[LEASE, LEASE, second]))
    engine.start("img_7f3a91", "noc")
    wait_for(engine, "running")
    assert wait_address(engine, "10.20.30.77")["state"] == "leased"
    assert json.loads(get_setting(lab["conn"], vmupdate.LEASE_KEY))["address"] == "10.20.30.77"
    assert events(lab["conn"]).count("vmupdate_lease") == 2      # אותה כתובת פעמיים = שורה אחת


def test_a_firewall_reload_after_teardown_cannot_bring_the_table_back(lab):
    """סקירת אבטחה 16/10: טעינה ופירוק אינם חופפים. אחרי ש-`_net_down` התחיל,
    סבב התחזוקה אינו טוען את הטבלה מחדש (ואינו מכבה VM שכבר כבה)."""
    machine = FakeMachine()
    engine = lab["make"](machine)
    engine.start("img_7f3a91", "noc")
    wait_for(engine, "running")
    loads = len(machine.firewalls)
    engine._net_down()                       # הפירוק התחיל (QEMU יצא)
    engine.maintenance(guard=True)           # הסבב שרץ במקביל
    assert len(machine.firewalls) == loads and machine.firewall_offs == 1
    assert engine.status()["state"] == "running"


def test_a_macvtap_that_was_not_created_keeps_the_vm_off_with_the_hint(lab):
    engine = lab["make"](FakeMachine(macvtap="RTNETLINK answers: Operation not supported",
                                     platform="microsoft"))
    engine.start("img_7f3a91", "noc")
    status = wait_for(engine, "failed")
    assert status["error"]["reason"] == "macvtap"
    assert "Operation not supported" in status["error"]["message"]
    assert "MAC address spoofing" in status["error"]["message"]
    assert lab["qemu"].procs == [] and status["disk_ready"] is True


def test_the_network_comes_down_when_the_vm_stops(lab):
    machine = FakeMachine()
    engine = lab["make"](machine)
    engine.start("img_7f3a91", "noc")
    wait_for(engine, "running")
    engine.shutdown("noc")
    wait_for(engine, "stopped")
    assert machine.downs == 1 and machine.firewall_offs == 1


# --- vmnet: פענוח DHCP ------------------------------------------------------------------


def dhcp_frame(*, op, chaddr, kind, yiaddr="0.0.0.0", server=None, ports=None, xid=None,
               src=None):
    """מסגרת DHCP. ‏`src` — MAC המקור של ה-Ethernet (ברירת מחדל: ה-chaddr בבקשה,
    שרת DHCP בתשובה)."""
    ports = ports or ((68, 67) if op == 1 else (67, 68))
    src = src or (chaddr if op == 1 else "02:00:00:00:00:fe")
    opts = bytes([53, 1, kind]) + (bytes([54, 4]) + socket.inet_aton(server) if server else b"")
    boot = (bytes([op, 1, 6, 0]) + (xid or b"\x12\x34\x56\x78") + b"\0" * 8
            + socket.inet_aton(yiaddr) + b"\0" * 8
            + bytes.fromhex(chaddr.replace(":", "")) + b"\0" * 10 + b"\0" * 192
            + b"\x63\x82\x53\x63" + opts + b"\xff")
    udp = struct.pack("!HHHH", *ports, 8 + len(boot), 0) + boot
    ip = bytes([0x45, 0, 0, 0, 0, 0, 0, 0, 64, 17, 0, 0]) + b"\0" * 8
    return b"\xff" * 6 + bytes.fromhex(src.replace(":", "")) + b"\x08\x00" + ip + udp


XID = b"\x12\x34\x56\x78"


def test_dhcp_from_reads_a_request_and_an_ack_for_our_mac_only():
    assert vmnet.dhcp_from(dhcp_frame(op=1, chaddr=MAC, kind=1), MAC) == ("request", None, None, XID)
    assert vmnet.dhcp_from(dhcp_frame(op=1, chaddr=MAC, kind=3), MAC) == ("request", None, None, XID)
    ack = dhcp_frame(op=2, chaddr=MAC, kind=5, yiaddr="10.20.30.40", server="10.20.30.1")
    assert vmnet.dhcp_from(ack, MAC) == ("ack", "10.20.30.40", "10.20.30.1", XID)
    assert vmnet.dhcp_from(ack, OTHER) is None                            # לא האורח שלנו
    assert vmnet.dhcp_from(dhcp_frame(op=2, chaddr=MAC, kind=2, yiaddr="10.20.30.40"),
                           MAC) is None                                   # OFFER אינו lease
    assert vmnet.dhcp_from(dhcp_frame(op=2, chaddr=MAC, kind=6), MAC) is None   # NAK
    assert vmnet.dhcp_from(b"\0" * 20, MAC) is None


@pytest.mark.parametrize("bad", ["127.0.0.1", "169.254.1.1", "224.0.0.1", "255.255.255.255",
                                 "0.0.0.0", "240.0.0.1"])
def test_an_ack_with_an_address_that_cannot_be_a_lease_is_not_one(bad):
    assert vmnet.dhcp_from(dhcp_frame(op=2, chaddr=MAC, kind=5, yiaddr=bad), MAC) is None


OUT, IN = 4, 0     # sll_pkttype: PACKET_OUTGOING / PACKET_HOST


class FakeSock:
    """‏(מסגרת, כיוון) — ‏OUT = האורח שלח, ‏IN = נמסר לו מהחוט."""

    def __init__(self, frames, stop_when_empty=None):
        self.frames = list(frames)
        self.closed = False
        self.stop_when_empty = stop_when_empty

    def settimeout(self, _t):
        pass

    def recvfrom(self, _n):
        if not self.frames:
            if self.stop_when_empty is not None:
                self.stop_when_empty.append(True)
            raise socket.timeout()
        frame, kind = self.frames.pop(0)
        return frame, ("imagesrv-vm0", 0x0800, kind, 1, b"")

    def close(self):
        self.closed = True


def watch(frames, timeout=60):
    """עד שהמסגרות נגמרו (ואז "ה-VM כבה"); מחזיר (התוצאה, הקריאות ל-on_ack)."""
    done, acks = [], []
    sock = FakeSock(frames, stop_when_empty=done)
    got = vmnet.watch_lease("imagesrv-vm0", MAC, timeout, lambda: bool(done),
                            lambda a, s: acks.append((a, s)), open_socket=lambda _i: sock)
    assert sock.closed
    return got, acks


REQ = (dhcp_frame(op=1, chaddr=MAC, kind=1), OUT)
ACK = (dhcp_frame(op=2, chaddr=MAC, kind=5, yiaddr="10.0.9.9", server="10.0.9.1"), IN)


def test_watch_lease_returns_the_ack_and_counts_requests():
    got, acks = watch([REQ, (dhcp_frame(op=1, chaddr=OTHER, kind=1), OUT), ACK])
    assert got == {"address": "10.0.9.9", "server": "10.0.9.1", "requests": 1, "ignored": 0,
                   "error": None}
    assert acks == [("10.0.9.9", "10.0.9.1")]


@pytest.mark.parametrize("forged", [
    (dhcp_frame(op=2, chaddr=MAC, kind=5, yiaddr="10.0.0.1", xid=b"\x99" * 4), IN),  # xid לא שלנו
    (dhcp_frame(op=2, chaddr=MAC, kind=5, yiaddr="10.0.0.1"), OUT),        # האורח "ענה לעצמו"
    (dhcp_frame(op=2, chaddr=MAC, kind=5, yiaddr="10.0.0.1", src=MAC), IN),  # מה-MAC של ה-VM
])
def test_a_forged_or_self_made_ack_is_not_a_lease(forged):
    got, acks = watch([REQ, forged])
    assert got["address"] is None and acks == [] and got["ignored"] == 1


def test_an_ack_before_any_request_from_the_guest_is_not_a_lease():
    got, acks = watch([ACK])
    assert got["address"] is None and acks == [] and got["ignored"] == 1


def test_watch_lease_keeps_listening_and_reports_a_renewal():
    renewed = (dhcp_frame(op=2, chaddr=MAC, kind=5, yiaddr="10.0.9.50", server="10.0.9.1",
                          xid=b"\x55" * 4), IN)
    again = (dhcp_frame(op=1, chaddr=MAC, kind=3, xid=b"\x55" * 4), OUT)
    got, acks = watch([REQ, ACK, again, renewed])
    assert acks == [("10.0.9.9", "10.0.9.1"), ("10.0.9.50", "10.0.9.1")]
    assert got["address"] == "10.0.9.50" and got["requests"] == 2


def test_watch_lease_times_out_without_an_address():
    ticks = iter(range(0, 1000, 10))
    sock = FakeSock([REQ])
    got = vmnet.watch_lease("imagesrv-vm0", MAC, 30, lambda: False, open_socket=lambda _i: sock,
                            clock=lambda: next(ticks))
    assert got["address"] is None and got["requests"] == 1 and got["error"] is None


def test_watch_lease_that_cannot_listen_says_so():
    def boom(_ifname):
        raise PermissionError(1, "Operation not permitted")
    got = vmnet.watch_lease("imagesrv-vm0", MAC, 30, lambda: False, open_socket=boom)
    assert got["address"] is None and "Operation not permitted" in got["error"]


# --- vmnet: macvtap ו-sysfs ----------------------------------------------------------------


def _ip(shown: str, fail: str | None = None):
    calls = []

    def run(argv, **_kw):
        calls.append(argv)
        if fail and fail in argv:
            return subprocess.CompletedProcess(argv, 2, "", "RTNETLINK answers: File exists")
        if argv[:3] == ["ip", "-d", "-o"]:
            return subprocess.CompletedProcess(argv, 0, shown, "")
        return subprocess.CompletedProcess(argv, 0, "", "")
    return run, calls


def _sysfs(tmp_path, index=42, dev=True):
    sysfs, devdir = tmp_path / "sys", tmp_path / "dev"
    (sysfs / vmnet.TAP_NAME).mkdir(parents=True)
    (sysfs / vmnet.TAP_NAME / "ifindex").write_text(f"{index}\n")
    devdir.mkdir()
    if dev:
        (devdir / f"tap{index}").write_text("")
    return sysfs, devdir


SHOWN = (f"42: {vmnet.TAP_NAME}@eth0: <BROADCAST,MULTICAST,UP> mtu 1500 qdisc noqueue \\"
         f"    link/ether {MAC} brd ff:ff:ff:ff:ff:ff promiscuity 0 macvtap mode bridge")


def test_macvtap_up_creates_bridge_mode_with_our_mac_and_reads_it_back(tmp_path):
    sysfs, devdir = _sysfs(tmp_path)
    run, calls = _ip(SHOWN)
    path, why = vmnet.macvtap_up("eth0", MAC, run=run, sysfs=sysfs, dev=devdir)
    assert (path, why) == (devdir / "tap42", None)
    assert ["ip", "link", "add", "link", "eth0", "name", vmnet.TAP_NAME, "address", MAC,
            "type", "macvtap", "mode", "bridge"] in calls


@pytest.mark.parametrize("shown", [SHOWN.replace("mode bridge", "mode vepa"),
                                   SHOWN.replace(MAC, OTHER), SHOWN.replace("@eth0", "@eth1"), ""])
def test_a_macvtap_that_does_not_read_back_is_a_refusal(tmp_path, shown):
    sysfs, devdir = _sysfs(tmp_path)
    run, _calls = _ip(shown)
    path, why = vmnet.macvtap_up("eth0", MAC, run=run, sysfs=sysfs, dev=devdir)
    assert path is None and "לא ידוע מה נוצר" in why


def test_a_failed_ip_link_add_is_named(tmp_path):
    sysfs, devdir = _sysfs(tmp_path)
    run, _calls = _ip(SHOWN, fail="add")
    path, why = vmnet.macvtap_up("eth0", MAC, run=run, sysfs=sysfs, dev=devdir)
    assert path is None and "File exists" in why


def test_a_tap_device_that_never_appears_is_named(tmp_path):
    sysfs, devdir = _sysfs(tmp_path, dev=False)
    run, _calls = _ip(SHOWN)
    path, why = vmnet.macvtap_up("eth0", MAC, run=run, sysfs=sysfs, dev=devdir, wait_s=0,
                                 sleep=lambda _s: None)
    assert path is None and "udev" in why


def test_stacked_follows_bond_and_vlan_links(tmp_path):
    sysfs = tmp_path / "net"
    for nic, links in {"eth1": ["upper_bond0"], "eth2": ["upper_bond0"],
                       "bond0": ["lower_eth1", "lower_eth2", "upper_bond0.30"],
                       "bond0.30": ["lower_bond0"], "eth0": []}.items():
        (sysfs / nic).mkdir(parents=True)
        for link in links:
            (sysfs / nic / link).write_text("")
    assert vmnet.stacked("eth2", sysfs) == {"bond0", "eth1", "bond0.30"}
    assert vmnet.stacked("eth0", sysfs) == set()
    assert vmnet.stacked("eth7", sysfs) is None                     # לא נקרא ≠ אין קשר


def test_detect_virt_and_the_hint_for_an_unknown_platform():
    def run_with(rc, out):
        return lambda argv, **_kw: subprocess.CompletedProcess(argv, rc, out, "")
    assert vmnet.detect_virt(run=run_with(0, "vmware\n")) == "vmware"
    assert vmnet.detect_virt(run=run_with(1, "none\n")) == "none"
    assert vmnet.detect_virt(run=run_with(1, "")) is None
    hint = vmnet.platform_hint("kvm")
    assert "kvm" in hint and "Forged transmits" in hint and "MAC address spoofing" in hint


# --- ה-API ---------------------------------------------------------------------------------

BASE = "/api/console/vmupdate/network"


@pytest.fixture()
def api_vm(server):
    engine = server["app"].state.runtime.vmupdate
    engine.machine = FakeMachine()
    engine.deploy_interface = lambda: "eth1"
    yield engine


def test_network_endpoints_are_admin_only(server, api_vm):
    assert server["anon"].get(BASE).status_code == 401
    assert server["deploy"].get(BASE).status_code == 403
    assert server["anon"].put(BASE, json={"interface": "eth0"}).status_code == 401
    assert server["deploy"].put(BASE, json={"interface": "eth0"}).status_code == 403


def test_get_shows_the_mac_the_hint_and_put_saves_the_choice(server, api_vm):
    admin = server["admin"]
    view = admin.get(BASE).json()
    assert view["ok"] is True and view["interface"] is None and vmnet.valid_mac(view["mac"])
    assert view["deploy_interface"] == "eth1" and "Forged transmits" in view["hint"]
    saved = admin.put(BASE, json={"interface": "eth0"})
    assert saved.status_code == 200 and saved.json()["interface"] == "eth0"
    assert saved.json()["mac"] == view["mac"]                    # אותו MAC — לא נוצר מחדש
    refused = admin.put(BASE, json={"interface": "eth1"})
    assert refused.status_code == 409 and refused.json()["code"] == "vm_nic_is_deploy"
    assert admin.get(BASE).json()["interface"] == "eth0"         # הסירוב לא שינה דבר
    assert admin.put(BASE, json={"nic": "eth0"}).status_code == 400
    cleared = admin.put(BASE, json={"interface": None})
    assert cleared.status_code == 200 and cleared.json()["interface"] is None


def test_get_flags_a_saved_card_that_became_the_deploy_card(server, api_vm):
    server["admin"].put(BASE, json={"interface": "eth0"})
    api_vm.deploy_interface = lambda: "eth0"                     # ההפצה עברה לכרטיס הזה
    problem = server["admin"].get(BASE).json()["problem"]
    assert problem["code"] == "vm_nic_is_deploy"


def test_status_path_still_documented_shape(lab):
    """המפתחות החדשים של הסטטוס (`network`, ‏`vm_mac`) — בשמם, כמו ב-interfaces.md."""
    engine = lab["make"]()
    engine.start("img_7f3a91", "noc")
    status = wait_for(engine, "running")
    assert status["vm_mac"] == engine.ensure_mac()
    assert status["network"]["state"] == "waiting" and status["network"]["seconds_left"] > 0
    assert set(status["network"]) == {"interface", "mac", "state", "address", "server", "at",
                                      "seconds_left"}
    assert Path(engine.workdir).exists()
