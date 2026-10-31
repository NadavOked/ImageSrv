"""‏#2040 — מחשב בנייה עם שני כרטיסים מחוברים: רשת ההפצה ורשת הכיתות.

הכרעת נדב (23/10): שני הכרטיסים מחוברים, בכל סדר, וה-BIOS עולה מהדיסק.
1. ‏Windows שעלה מהדיסק לא מקבל כתובת ברשת ההפצה — ה-dnsmasq של כרטיס ההפצה
   עונה רק לאתחול רשת (‏`PXEClient`) ולסוכן (‏vendor class `ImageSrv-agent`).
2. כשעולים מהרשת, הסוכן עובד רק על הכרטיס שה-DHCP של השרת שלנו ענה עליו
   (‏server-id == ה-host של `imagesrv.server`) ומוריד את השני לכל הריצה.
3. לא לפי סדר אלפביתי — זה היה הבאג: הראשון שקיבל חכירה כלשהי נבחר.

צד הסוכן: הלולאה האמיתית של `agent/init` עם `udhcpc`/`ip` מזויפים
(‏`test_init_network.udhcpc_prelude`). צד השרת: הקובץ ש-`dhcp.render`
מייצר, דרך המסלול שכותב אותו (‏`console_dhcp.render_dnsmasq`) ודרך מודל של
כללי ההתאמה של dnsmasq — אין dnsmasq אמיתי ב-Testrunner.
"""

import re
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from server.net import dhcp
from test_agent import AGENT, BASH, posix
from test_init_network import FOREIGN_SID, SERVER_IP, _make_net, _run

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "tools" / "build_initramfs.sh"
#: מה שה-udhcpc של הסוכן שולח (‏`-V`) — כמחרוזת ולא דרך הקבוע, כדי שהטסטים
#: ההתנהגותיים ייפלו בבקרה השלילית על התשובה עצמה ולא על AttributeError.
AGENT_VC = "ImageSrv-agent"

bash = requires_native(("bash", BASH))


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _ip_log(log: Path) -> list[str]:
    return _lines(Path(f"{log}.ip"))


# --- הסוכן: איזה כרטיס -----------------------------------------------------------


@bash
def test_our_server_on_the_second_nic_is_chosen_and_the_first_goes_down(tmp_path):
    """שני כרטיסים, לשניהם חכירה. של השרת שלנו על השני באלפבית — נבחר השני,
    והראשון (רשת הכיתות) יורד. הקוד הישן בחר את eth0: הוא קיבל חכירה ראשון."""
    net = tmp_path / "net"
    _make_net(net, {"lo": 1, "eth0": 1, "eth1": 1})
    proc, _, log = _run(net, "eth0 eth1", foreign_ifs="eth0")
    assert proc.returncode == 0, proc.stdout
    assert "IFACE=eth1" in proc.stdout, proc.stdout
    assert _lines(log) == ["udhcpc eth0 probe", "udhcpc eth1 probe", "udhcpc eth1 lease"]
    ip = _ip_log(log)
    assert "ip link set eth0 down" in ip, ip
    assert not [line for line in ip if line.endswith(" down") and line != "ip link set eth0 down"], ip
    assert f"eth0 has a lease from '{FOREIGN_SID}'" in proc.stdout, proc.stdout
    assert "down for this run: eth0" in proc.stdout, proc.stdout


@bash
def test_bonding_masters_is_not_a_nic_to_take_down(tmp_path):
    """‏`/sys/class/net/bonding_masters` הוא קובץ, לא כרטיס (מודול bonding טעון).
    נמדד ב-netns על ה-Testrunner ‏(23/10): הלולאה ניסתה להוריד אותו והדפיסה
    WARNING שקרי — "would not go down"."""
    net = tmp_path / "net"
    _make_net(net, {"lo": 1, "eth0": 1, "eth1": 1})
    (net / "bonding_masters").write_text("\n", encoding="utf-8", newline="\n")
    proc, _, log = _run(net, "eth0 eth1", foreign_ifs="eth0")
    assert proc.returncode == 0, proc.stdout
    assert "IFACE=eth1" in proc.stdout, proc.stdout
    assert "down for this run: eth0 (#2040)" in proc.stdout, proc.stdout
    assert "bonding_masters" not in proc.stdout, proc.stdout
    assert "ip link set bonding_masters down" not in _ip_log(log), _ip_log(log)


@bash
def test_our_server_on_the_first_nic_leaves_the_second_unprobed_and_down(tmp_path):
    net = tmp_path / "net"
    _make_net(net, {"lo": 1, "eth0": 1, "eth1": 1})
    proc, _, log = _run(net, "eth0 eth1", foreign_ifs="eth1")
    assert proc.returncode == 0, proc.stdout
    assert "IFACE=eth0" in proc.stdout, proc.stdout
    assert _lines(log) == ["udhcpc eth0 probe", "udhcpc eth0 lease"]
    assert "ip link set eth1 down" in _ip_log(log)


@bash
def test_no_nic_with_our_server_is_server_not_found_not_a_guess(tmp_path):
    """שני כרטיסים, לשניהם חכירה, אף אחת מהשרת שלנו — אין ניחוש: ‏`fail` (אתחול
    → ‏GRUB → דיסק מקומי, עיקרון 1), אף מחזיק חכירה לא רץ, ואף כרטיס לא נבדק
    פעמיים (הרשת שלו לא זזה). הקוד הישן לקח את eth0 והמשיך."""
    net = tmp_path / "net"
    _make_net(net, {"lo": 1, "eth0": 1, "eth1": 1})
    proc, extra, log = _run(net, "eth0 eth1", short_deadline=True, foreign_ifs="eth0 eth1")
    assert proc.returncode == 3, proc.stdout
    assert (f"FAIL: no DHCP lease from the ImageSrv server {SERVER_IP} on any interface"
            " (another network on: eth0 eth1)") in proc.stdout, proc.stdout
    assert "IFACE=" not in proc.stdout
    assert _lines(log) == ["udhcpc eth0 probe", "udhcpc eth1 probe"]
    assert not [line for line in _ip_log(log) if line.endswith(" down")]
    assert extra <= 8.0       # עד ה-deadline, בלי לולאה צפופה


@bash
def test_without_imagesrv_server_no_lease_counts_as_ours(tmp_path):
    net = tmp_path / "net"
    _make_net(net, {"lo": 1, "eth0": 1})
    proc, _, log = _run(net, "eth0", extra="IMAGESRV_SERVER=", short_deadline=True)
    assert proc.returncode == 3, proc.stdout
    assert "FAIL: no DHCP lease from the ImageSrv server" in proc.stdout, proc.stdout
    assert "udhcpc eth0 lease" not in _lines(log)


def test_every_udhcpc_in_init_names_the_agent_to_dnsmasq():
    """ה-`-V` של הסוכן וה-vendor class שה-dnsmasq מחכה לו הם אותה מחרוזת —
    אחרת בכרטיס ההפצה הסוכן עצמו לא היה מקבל כתובת."""
    text = (AGENT / "init").read_text(encoding="utf-8").replace("\\\n", " ")
    calls = [line for line in text.splitlines() if re.search(r"^\s*(if )?udhcpc -i", line)]
    assert len(calls) == 2, calls          # הבדיקה (‏-q) ומחזיק החכירה
    for call in calls:
        assert f"-V {AGENT_VC} " in call, call
    assert dhcp.AGENT_VENDOR_CLASS == AGENT_VC
    assert "udhcpc.probe" in calls[0] and " -q " in calls[0], calls[0]
    assert "udhcpc.script" in calls[1] and " -q " not in calls[1], calls[1]


def _probe_hook() -> str:
    text = BUILD.read_text(encoding="utf-8")
    m = re.search(r"cat > \"\$ROOT/etc/imagesrv/udhcpc\.probe\" << 'EOF'\n(.*?)\nEOF\n",
                  text, re.S)
    assert m, "the udhcpc probe hook heredoc moved -- update the extractor"
    return m.group(1)


@bash
@pytest.mark.parametrize("event,written", [("bound", True), ("renew", True),
                                           ("deconfig", False), ("leasefail", False)])
def test_the_probe_hook_records_the_server_and_configures_nothing(tmp_path, event, written):
    run = tmp_path / "run"
    run.mkdir()
    hook = tmp_path / "udhcpc.probe"
    hook.write_text(_probe_hook().replace("/run/imagesrv", posix(run)) + "\n",
                    encoding="utf-8", newline="\n")
    calls = tmp_path / "calls"
    script = (f'ip() {{ echo "ip $*" >> {posix(calls)!r}; }}; '
              f'ifconfig() {{ echo "ifconfig $*" >> {posix(calls)!r}; }}; '
              f'route() {{ echo "route $*" >> {posix(calls)!r}; }}; '
              f'export -f ip ifconfig route; interface=eth1 serverid={SERVER_IP} '
              f'ip=10.44.9.77 router=10.44.9.1 "$BASH" {posix(hook)!r} {event}')
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                          encoding="utf-8", stdin=subprocess.DEVNULL, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = run / "dhcp-probe.eth1"
    if written:
        assert _lines(out) == [f"serverid={SERVER_IP}", "ip=10.44.9.77"]
    else:
        assert not out.exists()
    assert not calls.exists(), _lines(calls)     # בלי כתובת, בלי route, בלי הורדה


# --- השרת: dnsmasq בכרטיס ההפצה -------------------------------------------------

DEPLOY = dhcp.InterfaceConfig("eth1", enabled=True, range_start="10.44.9.50",
                              range_end="10.44.9.200", netmask="255.255.255.0",
                              gateway="10.44.9.1", server_ip=SERVER_IP)
CLASSROOM = dhcp.InterfaceConfig("eth2", enabled=True, range_start="10.44.20.50",
                                 range_end="10.44.20.200", netmask="255.255.255.0",
                                 gateway="10.44.20.1", server_ip="10.44.20.10")

BIOS_PXE = "PXEClient:Arch:00000:UNDI:002001"
UEFI_PXE = "PXEClient:Arch:00007:UNDI:003016"
WINDOWS = "MSFT 5.0"


def _answers(text: str, nic: str, vendor_class: str) -> bool:
    """מודל של כללי dnsmasq שהקובץ נשען עליהם: בלי `dhcp-range` לכרטיס אין
    תשובה; כל בקשה מתויגת בשם הכרטיס שהגיעה עליו (man dnsmasq); ‏
    `dhcp-vendorclass` מתאים **כתת-מחרוזת** של אופציה 60; ‏`dhcp-ignore` מתעלם
    מלקוח כשכל התגים שלו מתקיימים (‏`tag:!x` = התג x אינו).
    ‏**תג `set:` של `dhcp-range` אינו בקבוצה כש-`dhcp-ignore` נבדק** — נמדד על
    dnsmasq 2.91 ב-netns ‏(23/10): ‏`dhcp-ignore=tag:if-s0,tag:!imagesrv-boot`
    לא עצר את `MSFT 5.0` (‏DHCPOFFER, ‏"tags: if-s0, s0"). מודל, לא dnsmasq."""
    lines = [ln.split("#", 1)[0].strip() for ln in text.splitlines()]
    if not any(re.fullmatch(rf"dhcp-range=set:if-{re.escape(nic)},.*", ln) for ln in lines):
        return False
    tags = {nic}
    for ln in lines:
        m = re.fullmatch(r"dhcp-vendorclass=set:([^,]+),(.+)", ln)
        if m and m.group(2) in vendor_class:
            tags.add(m.group(1))
    for ln in lines:
        if ln.startswith("dhcp-ignore="):
            conds = [c.removeprefix("tag:") for c in ln.removeprefix("dhcp-ignore=").split(",")]
            if all((c[1:] not in tags) if c.startswith("!") else (c in tags) for c in conds):
                return False
    return True


def test_the_deploy_nic_answers_netboot_and_the_agent_only():
    text = dhcp.render([DEPLOY, CLASSROOM], tftp_root="/srv/tftp", unregistered_if="eth1")
    for client in (WINDOWS, "", "udhcp 1.37.0", "dhcpcd-10.0.6:Linux"):
        assert not _answers(text, "eth1", client), client
    for client in (BIOS_PXE, UEFI_PXE, AGENT_VC):
        assert _answers(text, "eth1", client), client
    assert "dhcp-vendorclass=set:imagesrv-boot,PXEClient" in text
    assert f"dhcp-vendorclass=set:imagesrv-boot,{AGENT_VC}" in text
    assert "dhcp-ignore=tag:eth1,tag:!imagesrv-boot" in text


def test_another_nic_that_hands_out_addresses_still_answers_windows():
    """וילן אחר שהשרת מחלק בו כתובות (‏eth2) — שם Windows כן צריך כתובת."""
    text = dhcp.render([DEPLOY, CLASSROOM], tftp_root="/srv/tftp", unregistered_if="eth1")
    assert _answers(text, "eth2", WINDOWS)
    assert "dhcp-ignore=tag:eth2" not in text


def test_without_a_named_deploy_nic_nothing_is_gated():
    """אין ראיה איזה כרטיס הוא ההפצה (עיקרון 1) — הקובץ כמו לפני #2040."""
    text = dhcp.render([DEPLOY, CLASSROOM], tftp_root="/srv/tftp")
    assert "dhcp-ignore" not in text and "dhcp-vendorclass" not in text
    assert _answers(text, "eth1", WINDOWS)


@pytest.mark.parametrize("source", ["cli", "console"])
def test_the_file_the_console_writes_gates_the_deploy_nic(tmp_path, images_root, clock, source):
    """המסלול שכותב את הקובץ (‏`console_dhcp.render_dnsmasq`) — כרטיס ההפצה מכל
    מקור. ‏dnsmasq לא מופעל: בלי `dhcp_hooks`, בלי מכונה."""
    pytest.importorskip("fastapi")
    from test_unknown_screen_1802 import _rendered
    text = _rendered(tmp_path, images_root, clock, source, "eth1")
    assert not _answers(text, "eth1", WINDOWS)
    assert _answers(text, "eth1", UEFI_PXE) and _answers(text, "eth1", AGENT_VC)
    assert _answers(text, "eth2", WINDOWS)
    assert "dhcp-ignore=tag:eth1,tag:!imagesrv-boot" in text
