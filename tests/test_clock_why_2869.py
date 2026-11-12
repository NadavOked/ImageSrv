"""#2869 -- an unsynchronised clock says WHY when the server can see it: no default route, no DNS.

fd1111 (11/11): "מקור לא ידוע · לא מסונכרן · סטייה לא ידועה (אין מקור זמן נבחר)" for an hour,
and the operator could not tell a missing gateway/DNS from a dead time server. The reason is read
from what the server has (/proc/net/route, resolv.conf), never guessed; a file that cannot be read
says nothing (principle 5).

Negative control: server/system/timesync.py, health.py, dcui/data.py, dcui/view.py from origin/main.
"""
from __future__ import annotations

from dataclasses import replace

from server.dcui import samples, view
from server.dcui.data import CLOCK_UNSYNCED
from server.system import health, timesync

TRACKING_UNSYNC = ("Reference ID    : 00000000 ()\nStratum         : 0\n"
                   "System time     : 0.000019000 seconds slow of NTP time\n"
                   "Leap status     : Not synchronised\n")
TRACKING_SYNC = TRACKING_UNSYNC.replace("Not synchronised", "Normal")
SOURCES_NONE = ("MS Name/IP address         Stratum Poll Reach LastRx Last sample\n"
                "===============================================================\n"
                "^? 10.0.0.9                      0   6     0     -     +0ns[   +0ns] +/-    0ns\n")
SOURCES_OK = ("MS Name/IP address         Stratum Poll Reach LastRx Last sample\n"
              "===============================================================\n"
              "^* 10.44.0.250                   2   6   377    12   +3us[ +5us] +/-  20ms\n")
ROUTE_HEAD = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
ROUTE_LAN = "ens160\t0064A8C0\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n"
ROUTE_DEFAULT = "ens160\t00000000\t0164A8C0\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"


def _status(reason, tracking=TRACKING_UNSYNC, sources=SOURCES_NONE, called=None):
    def hook():
        if called is not None:
            called.append(1)
        return reason
    return timesync.status({"tracking": lambda: tracking, "sources": lambda: sources,
                            "now": lambda: "2026-11-11T20:00:00+00:00", "network_reason": hook})


def test_no_gateway_is_said_on_the_health_row():
    row = health.time_sync_check(_status("no_gateway"))
    assert row["state"] == "warn" and "בשרת אין שער ברירת מחדל — שרתי הזמן לא נגישים" in row["detail"], row


def test_no_dns_is_said_on_the_health_row():
    row = health.time_sync_check(_status("no_dns"))
    assert "בשרת אין DNS — שמות שרתי הזמן לא יתורגמו" in row["detail"], row


def test_an_unknown_reason_changes_nothing():
    row = health.time_sync_check(_status(None))
    assert row["detail"] == "מקור לא ידוע · לא מסונכרן · סטייה לא ידועה (אין מקור זמן נבחר)", row


def test_a_synchronised_clock_never_asks_and_never_blames():
    called: list[int] = []
    status = _status("no_dns", TRACKING_SYNC, SOURCES_OK, called)
    assert called == [] and "network_reason" not in status
    assert "DNS" not in health.time_sync_check(status)["detail"]


def test_the_reason_is_read_from_the_routes_and_resolv_conf(tmp_path):
    route, resolv = tmp_path / "route", tmp_path / "resolv.conf"
    route.write_text(ROUTE_HEAD + ROUTE_LAN)
    resolv.write_text("nameserver 80.179.52.100\n")
    assert timesync._network_reason(str(route), str(resolv)) == "no_gateway"
    route.write_text(ROUTE_HEAD + ROUTE_LAN + ROUTE_DEFAULT)
    resolv.write_text("# no interface declares a DNS server\n")
    assert timesync._network_reason(str(route), str(resolv)) == "no_dns"
    resolv.write_text("# c\nnameserver 80.179.52.100\n")
    assert timesync._network_reason(str(route), str(resolv)) is None


def test_a_file_that_cannot_be_read_blames_nobody(tmp_path):
    assert timesync._network_reason(str(tmp_path / "missing"), str(tmp_path / "missing2")) is None


def test_the_dcui_card_gives_the_reason():
    extras = view.Extras(uptime_seconds=240, secondaries=())
    snap = replace(samples.PRIMARY, clock=CLOCK_UNSYNCED, clock_server="10.44.0.250",
                   clock_why="בשרת אין DNS — שמות שרתי הזמן לא יתורגמו")
    problem = view.clock_problem(snap, extras)
    assert problem is not None and "סיבה: בשרת אין DNS — שמות שרתי הזמן לא יתורגמו." in problem.what
    assert "סיבה:" not in view.clock_problem(replace(snap, clock_why=""), extras).what


def test_the_installer_summary_warns_before_the_install():
    """#2869: the orange box on the summary (pixels are rendered in the lab; this is the contract)."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "native-gui" / "src" / "screens_install_final.c").read_text(encoding="utf-8")
    assert "static int clock_warning(" in src and "clock_warning(s,warn,sizeof warn)" in src
    assert "בחרתם שעון אוטומטי מהאינטרנט" in src   # the text is built as "<which clock>, <the rest>"
    assert ", אבל לכרטיס הניהול אין %s — השעון לא יסונכרן ועדכונים לא יעבדו. הוסיפו שער ו-DNS, או בחרו שרת זמן של הארגון לפי כתובת IP." in src
    assert "בחרתם שרת זמן לפי שם" in src
