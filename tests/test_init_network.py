"""agent/init before DHCP: short polls instead of fixed sleeps (#1451).

The real block from agent/init (after the efivarfs mount up to the address
read) runs against a fake /sys/class/net, with ip/udhcpc/fail stubbed. The
old code paid `sleep 2` plus `sleep 1` per interface per pass; the new one
polls in 0.1 s ticks under the same limits (2 s for a NIC to appear, 1 s of
carrier polling per pass, the 90 s DHCP deadline).
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, posix

pytestmark = requires_native(("bash", BASH))


def _network_block(net: Path, uptime: Path, short_deadline: bool = False) -> str:
    lines = (AGENT / "init").read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if "efivarfs would not mount" in line) + 2
    end = next(i for i, line in enumerate(lines) if line.startswith("IP=$(ip -4 addr show"))
    block = "\n".join(lines[start:end]).replace("/sys/class/net", posix(net))
    block = block.replace("/proc/uptime", posix(uptime))
    block = block.replace("/run/imagesrv", posix(net.parent / "run"))   # #2040: the probe file
    if short_deadline:
        block = block.replace("+ 90 ))", "+ 2 ))")
    tick = next((line for line in lines if line.startswith("poll_tick()")), "")
    mark = next((line for line in lines if line.startswith("boot_mark()")), "")
    mark = mark.replace("/proc/uptime", posix(uptime))
    return tick + '\n_up=""\n' + mark + "\n" + block


#: ‏#2040: ה-host של imagesrv.server, וה-server-id של DHCP זר (רשת הכיתות).
SERVER_IP = "10.44.9.10"
FOREIGN_SID = "192.168.50.1"


def udhcpc_prelude(net: Path, log: Path, ok_ifs: str, foreign_ifs: str = "") -> str:
    """‏udhcpc ו-ip מזויפים ללולאה האמיתית של init. כרטיס ב-`ok_ifs` מקבל
    חכירה; ה-probe (‏`-q`) רושם server-id כמו ה-hook האמיתי (‏udhcpc.probe) —
    כרטיס ב-`foreign_ifs` מקבל אותה משרת זר, והשאר מהשרת שלנו. הלוג:
    ‏`udhcpc <כרטיס> probe|lease`; ה-argv המלא ב-`<log>.args`; ‏`ip link` ב-`<log>.ip`."""
    (net.parent / "run").mkdir(exist_ok=True)
    return f"""
LIB_DIR=/nonexistent-lib
IMAGESRV_SERVER=http://{SERVER_IP}:8080
NET={posix(net)!r}
LOG={posix(log)!r}
RUN={posix(net.parent / "run")!r}
OK_IFS={ok_ifs!r}
FOREIGN_IFS={foreign_ifs!r}
ip() {{ case "$1" in link) echo "ip $*" >> "$LOG.ip" ;; esac; }}
udhcpc() {{
    echo "$*" >> "$LOG.args"
    case " $* " in *" -q "*) _role=probe ;; *) _role=lease ;; esac
    echo "udhcpc $2 $_role" >> "$LOG"
    case " $OK_IFS " in *" $2 "*) ;; *) return 1 ;; esac
    case " $FOREIGN_IFS " in *" $2 "*) _sid={FOREIGN_SID} ;; *) _sid={SERVER_IP} ;; esac
    [ "$_role" = probe ] && printf 'serverid=%s\\n' "$_sid" > "$RUN/dhcp-probe.$2"
    return 0
}}
"""


def _make_net(net: Path, carriers: dict[str, int]) -> None:
    for name, carrier in carriers.items():
        interface = net / name
        interface.mkdir(parents=True)
        (interface / "carrier").write_text(f"{carrier}\n", encoding="utf-8", newline="\n")


def _run(net: Path, ok_ifs: str, extra: str = "", short_deadline: bool = False,
         foreign_ifs: str = ""):
    log = net.parent / "dhcp.log"
    uptime = net.parent / "uptime"
    uptime.write_text("12.34 50.00\n", encoding="utf-8", newline="\n")
    prelude = udhcpc_prelude(net, log, ok_ifs, foreign_ifs) + f"""
fail() {{ echo "FAIL: $1"; echo "UP=$_up"; exit 3; }}
{extra}
"""
    script = prelude + _network_block(net, uptime, short_deadline) \
        + '\necho "IFACE=$IFACE"\necho "UP=$_up"\n'
    if short_deadline:
        # #2099: `_deadline=$(( $(date +%s) + 2 ))` counts whole seconds -- started at .9 of
        # a second, the script had ~1.1 s and not 2 (extra 1.16 / 1.24; serial r4 too). Start
        # at the top of a second, so the 2 s are 2 s and the bound in the test holds as written.
        time.sleep(1.0 - time.time() % 1.0)
    started = time.monotonic()
    proc = subprocess.run(
        [BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
        capture_output=True, text=True, encoding="utf-8",
        stdin=subprocess.DEVNULL, timeout=60,
    )
    elapsed = time.monotonic() - started
    baseline_start = time.monotonic()
    subprocess.run(
        [BASH, "-c", 'export PATH="/usr/bin:$PATH"; :'],
        capture_output=True, text=True, encoding="utf-8",
        stdin=subprocess.DEVNULL, timeout=60,
    )
    return proc, elapsed - (time.monotonic() - baseline_start), log


def test_carrier_that_comes_up_quickly_is_used_without_the_old_fixed_waits(tmp_path):
    net = tmp_path / "net"
    _make_net(net, {"lo": 1, "eth0": 0})
    writer = '( command sleep 0.3; echo 1 > "$NET/eth0/carrier" ) >/dev/null 2>&1 &'
    proc, extra, _ = _run(net, "eth0", writer)
    assert proc.returncode == 0
    assert "IFACE=eth0" in proc.stdout
    assert extra < 2.2      # old code: sleep 2 + sleep 1 >= 3.0 s


def test_only_an_interface_with_carrier_gets_dhcp(tmp_path):
    net = tmp_path / "net"
    _make_net(net, {"lo": 1, "eth0": 0, "eth1": 1})
    proc, _, log = _run(net, "eth0 eth1")
    assert proc.returncode == 0
    assert "IFACE=eth1" in proc.stdout
    assert log.read_text(encoding="utf-8").splitlines() == ["udhcpc eth1 probe",
                                                            "udhcpc eth1 lease"]


def test_a_failed_lease_moves_on_to_the_next_interface(tmp_path):
    net = tmp_path / "net"
    _make_net(net, {"lo": 1, "eth0": 1, "eth1": 1})
    proc, _, log = _run(net, "eth1")
    assert proc.returncode == 0
    assert "IFACE=eth1" in proc.stdout
    assert log.read_text(encoding="utf-8").splitlines() == [
        "udhcpc eth0 probe", "udhcpc eth1 probe", "udhcpc eth1 lease"]


def test_no_carrier_anywhere_still_ends_in_fail_at_the_deadline(tmp_path):
    net = tmp_path / "net"
    _make_net(net, {"lo": 1, "eth0": 0})
    proc, extra, _ = _run(net, "", short_deadline=True)
    assert proc.returncode == 3
    assert "FAIL: no DHCP lease on any interface" in proc.stdout
    assert 1.5 <= extra <= 8.0


def test_a_sleep_that_refuses_fractions_still_finds_the_carrier(tmp_path):
    net = tmp_path / "net"
    _make_net(net, {"lo": 1, "eth0": 0})
    extra_script = """
sleep() { case "$1" in *.*) return 1 ;; esac; command sleep "$1"; }
( command sleep 0.3; echo 1 > "$NET/eth0/carrier" ) >/dev/null 2>&1 &
"""
    proc, extra, _ = _run(net, "eth0", extra_script)
    assert proc.returncode == 0
    assert "IFACE=eth0" in proc.stdout
    assert extra < 4.0


def test_the_network_block_marks_nic_link_and_dhcp_in_order(tmp_path):
    net = tmp_path / "net"
    _make_net(net, {"lo": 1, "eth0": 1})
    proc, _, _ = _run(net, "eth0")
    assert proc.returncode == 0
    assert proc.stdout.splitlines()[-1] == "UP=nic:12.34,link:12.34,dhcp:12.34"


def test_no_nic_means_no_nic_mark(tmp_path):
    net = tmp_path / "net"
    _make_net(net, {"lo": 1})
    proc, _, _ = _run(net, "", short_deadline=True)
    assert proc.returncode == 3
    assert "FAIL: no DHCP lease on any interface" in proc.stdout
    # the marks were printed (fail shows them) and carry no nic/link/dhcp
    assert proc.stdout.splitlines()[-1] == "UP="


def test_init_marks_init_and_modules_in_the_whole_file():
    text = (AGENT / "init").read_text(encoding="utf-8")
    assert text.index("boot_mark init") < text.index("mods_load_all")
    assert text.index("mods_load_all") < text.index("boot_mark modules")
