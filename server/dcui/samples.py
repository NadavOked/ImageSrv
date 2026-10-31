"""The approved mockup's states as real frames (#1203).

C:\\ImageCtl-Lab\\design-approved\\dcui (Nadav, 12/10) drew seven frames from
made-up data.  Here the same data goes through the real path -- Snapshot ->
Runtime -> :func:`view.render` -- so a render of these frames is the
mockup's comparison, and the tests render every screen the server can show.
``python3 -m server.dcui --gui-frames DIR`` writes them, one file per frame.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path

from . import actions, pairing, view
from .app import Runtime
from .data import DeployRound, NicInfo, ServiceState, Snapshot

FP = ("6F:8E:63:D5:A4:D9:77:7F:EC:D7:42:E0:A0:F4:78:F0:"
      "04:62:C5:32:0E:6F:6C:D8:DD:40:C0:CA:6F:5F:F0:94")
TODAY = datetime(2026, 10, 12, 20, 17, 22)
CLOCK = "20:17"
NOW = 1000.0
UP = {name: ServiceState("active", "enabled") for name in
      ("imagesrv-server", "dnsmasq", "nftables")}

PRIMARY = Snapshot(
    hostname="TA", version="v0.71.0", console_url="https://10.20.10.5:8081",
    no_address=False, fingerprint=FP, certificate_sans=("10.20.10.5", "TA"),
    certificate_not_after="Oct  9 12:00:00 2036 GMT",
    management=NicInfo("ens33", "10.20.10.5/24", "dhcp", "up", "10000",
                       "00:50:56:aa:10:05", "VMware VMXNET3"),
    interfaces=(NicInfo("ens33", "10.20.10.5/24", "dhcp", "up", "10000",
                        "00:50:56:aa:10:05", "VMware VMXNET3"),
                NicInfo("ens34", "10.44.0.1/16", "static", "up", "1000",
                        "00:50:56:aa:44:01", "VMware VMXNET3")),
    deploy=NicInfo("ens34", "10.44.0.1/16", "static", "up", "1000",
                   "00:50:56:aa:44:01", "VMware VMXNET3"),
    services=dict(UP), storage_role="standalone", secondary_count=1,
    refreshed="20:17:22", system_updates="2026-10-15",
)
PRIMARY_EXTRAS = view.Extras(uptime_seconds=12 * 86400 + 3600, secondaries=("HAIFA",))

SECONDARY = replace(
    PRIMARY, hostname="HAIFA", console_url="https://10.20.10.8:8081",
    management=NicInfo("ens33", "10.20.10.8/24", "dhcp", "up", "10000",
                       "00:50:56:aa:10:08", "VMware VMXNET3"),
    interfaces=(NicInfo("ens33", "10.20.10.8/24", "dhcp", "up", "10000",
                        "00:50:56:aa:10:08", "VMware VMXNET3"),),
    deploy=None, storage_role="secondary", secondary_count=0,
    services=dict(UP) | {"dnsmasq": ServiceState("inactive", "disabled")},
)


#: 16/10 (mockup #1525, a2): the code screen's data.
SPKI = "a1b2c3d4e5f607189c3e5d27b04f8a61e2d7c93b5a0f14e8d6b27c39a5e01f84"
CODE = pairing.Code("K7M4-X9QD", "5e0c41f2a9d7b380", NOW + 582,
                    "sn_" + SPKI[:16], SPKI, "https://203.0.113.40:8443")


def _runtime(snapshot: Snapshot, extras: view.Extras, **state) -> Runtime:
    runtime = Runtime(Path("/nonexistent"), None, snapshot, last_key_at=NOW - 6)
    runtime.extras = extras
    runtime.interfaces = list(snapshot.interfaces or (snapshot.management,))
    for key, value in state.items():
        setattr(runtime, key, value)
    return runtime


def runtimes() -> dict[str, Runtime]:
    """Frame id -> the Runtime that shows it.  The first seven are the
    mockup's; the rest are the screens it did not draw."""
    problem = replace(
        PRIMARY, services=dict(UP) | {"dnsmasq": ServiceState("failed", "enabled")},
        certificate_not_after="Oct 18 12:00:00 2026 GMT")
    running = replace(PRIMARY, deploy_round=DeployRound(
        "ses_1", "office365", "running", 12, 14, "2026-10-12T20:02:00+03:00"))
    connected = replace(SECONDARY, services=dict(UP), deploy=NicInfo(
        "ens34", "10.45.0.1/16", "static", "up", "1000", "00:50:56:aa:45:01", ""))
    paired = view.Extras(uptime_seconds=3 * 86400, has_parent=True,
                         primary_url="https://10.20.10.5:8443")
    waiting_nat = view.Extras(uptime_seconds=1500, advertised_url="https://203.0.113.40:8443")
    form = {"interface": "ens33", "mode": "static", "address": "10.20.10.9",
            "netmask": "24", "gateway": "10.20.10.1", "dns": "10.20.10.2"}
    preinstall = replace(
        PRIMARY, pre_install=True, console_url="https://10.20.10.37:8081",
        fingerprint="not created",
        interfaces=(NicInfo("ens33", "10.20.10.37/24", "dhcp", "up", "10000",
                            "00:50:56:aa:10:37"),
                    NicInfo("ens34", "", "dhcp", "no carrier", "unknown",
                            "00:50:56:aa:44:37")))
    restarted = actions.ActionResult(True, "Services restarted.",
                                     ("imagesrv-server: active", "dnsmasq: active"))
    return {
        "1-primary-ok": _runtime(PRIMARY, PRIMARY_EXTRAS),
        "2-primary-problem": _runtime(problem, PRIMARY_EXTRAS),
        "3-secondary-waiting": _runtime(SECONDARY, view.Extras(uptime_seconds=1500)),
        "3b-secondary-connected": _runtime(connected, paired),
        "4-admin-password": _runtime(PRIMARY, PRIMARY_EXTRAS, screen="auth",
                                     password="Secr3t!x"),
        "5-customize": _runtime(PRIMARY, PRIMARY_EXTRAS, screen="menu"),
        "6-power": _runtime(running, PRIMARY_EXTRAS, screen="power"),
        "6b-power-focus-act": _runtime(running, PRIMARY_EXTRAS, screen="power",
                                       power_focus="act"),
        "7-auth-locked": _runtime(PRIMARY, PRIMARY_EXTRAS, screen="auth",
                                  auth=actions.AuthGuard(3, NOW + 22), caps=True),
        "8-reset-confirm": _runtime(PRIMARY, PRIMARY_EXTRAS, screen="reset"),
        "8b-reset-repeat": _runtime(PRIMARY, PRIMARY_EXTRAS, screen="reset",
                                    reset_stage="repeat", reset_typed="abc",
                                    reset_guard=actions.AuthGuard(1, 0.0)),
        "9-network": _runtime(PRIMARY, PRIMARY_EXTRAS, screen="network", form=form,
                              form_field=2),
        "9b-network-confirm": _runtime(PRIMARY, PRIMARY_EXTRAS, screen="network_confirm",
                                       form=form),
        "10-tls": _runtime(PRIMARY, PRIMARY_EXTRAS, screen="tls", menu_index=3),
        "11-result": _runtime(PRIMARY, PRIMARY_EXTRAS, screen="result", menu_index=2,
                              result=restarted),
        "12-preinstall": _runtime(preinstall, view.Extras()),
        "12b-preinstall-network": _runtime(
            preinstall, view.Extras(), screen="preinstall_network",
            form={"interface": "ens33", "address": "10.20.10.37/24", "gateway": ""},
            form_field=2),
        # 16/10: F4 on a secondary (mockup #1525, variant A: a1-a6)
        "13-pair-code": _runtime(SECONDARY, waiting_nat, screen="pair", pair=CODE),
        "13b-pair-code-no-address": _runtime(
            SECONDARY, view.Extras(uptime_seconds=1500), screen="pair",
            pair=replace(CODE, advertised="", expires=NOW + 41)),
        "13c-pair-expired": _runtime(SECONDARY, waiting_nat, screen="pair", pair=CODE,
                                     pair_stage="expired"),
        "13d-paired": _runtime(SECONDARY, paired, screen="pair", pair=CODE, pair_stage="paired",
                               paired={"parent_id": "sn_5e91c07a2b4d6f13",
                                       "enrolled_at": "2026-10-12T17:19:09+00:00",
                                       "primary_url": "https://10.20.10.5:8443"}),
        "14-unpair": _runtime(connected, paired, screen="unpair"),
        "15-advertised": _runtime(SECONDARY, waiting_nat, screen="advertised", menu_index=4,
                                  advert="203.0.113.40:8443"),
    }


def frames() -> dict[str, str]:
    return {name: view.render(runtime, NOW, NOW, CLOCK, TODAY, rollback_left=47)
            for name, runtime in runtimes().items()}


def write(directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in frames().items():
        path = directory / f"{name}.frame"
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
    return written
