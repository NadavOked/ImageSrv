"""#3487: שתי תקלות בחומת האש -- 123/udp נסגר אחרי התקנה חוזרת, וכלל udpcast אינו עוקב אחרי `--sender-portbase`.

בשתיהן מה שהשרת עושה (chrony מגיש זמן, `udp-sender` תופס פורט) והכלל ש-nftables פותח נכתבים בנפרד:

1. **NTP.** המחולל כותב `udp dport 123` רק ב-`--per-nic`. המתקין, ברוץ שוב, כותב את ברירת המחדל בלי לקרוא
   את `nicsvc:*` -- וה-policy drop זורק את השאלות ל-chrony שממשיך להגיש. עכשיו: `--ntp-if` במחולל,
   `nic_services.ntp_interfaces` קורא את הכרטיסים מה-DB, והמתקין מעביר אותם.
2. **השידור.** `--sender-portbase P` קובע את פורטי השידור; `regenerate_firewall` (הנתיב של הקונסולה) לא העביר
   `--mcast-ports`, והכלל חזר לברירת המחדל של הייצור. עכשיו ה-portbase האמיתי מגיע למחולל -- ורק כשהוא
   שונה מברירת המחדל (פורט-הפצה-מכוון: 9000), כדי ששרת ייצור יקרא בדיוק כמו קודם.
   #3173: udpcast (P, P+1) הוסר, והטווח הוא של מנוע החתיכות בלבד: P+2 (נתונים) ו-P+3 (משוב הקצב).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import server.app
from server.core import ports as product_ports
from server.db import connect, set_setting
from server.net import deploy_net, nic_services
from server.rounds import chunksend
from server.rounds.chunkctx import DEFAULT_PORTBASE

from test_installer_1131 import BASH as INSTALLER_BASH, bindir, fake_tool, installer_function, run_bash  # noqa: F401
from test_nftables_per_nic import SHELLS, rules, run
from test_nftables_rules import _nft_check, needs_bash

REPO = Path(__file__).resolve().parent.parent
PRIMARY = ("--deploy-if", "eth-deploy", "--servers-if", "eth-srv")


# --- 1. NTP: המחולל ------------------------------------------------------------------------


@needs_bash
@pytest.mark.parametrize("shell", SHELLS)
def test_ntp_if_opens_123_on_that_nic_only(shell) -> None:
    got = rules(run(shell, *PRIMARY, "--ntp-if", "ens34", "--ntp-if", "ens35.7").stdout)
    assert 'iifname "ens34" udp dport 123 accept' in got
    assert 'iifname "ens35.7" udp dport 123 accept' in got
    assert [r for r in got if "dport 123" in r and "ens34" not in r and "ens35.7" not in r] == []


@needs_bash
@pytest.mark.parametrize("shell", SHELLS)
def test_without_ntp_if_the_default_ruleset_has_no_123(shell) -> None:
    """מה שאסור לשבור: בלי הדגל הפלט זהה לקודם (השרת שלא נערך אינו מגיש זמן)."""
    assert "dport 123" not in run(shell, *PRIMARY).stdout


@needs_bash
@pytest.mark.parametrize("shell", SHELLS)
def test_ntp_if_next_to_per_nic_is_refused_not_dropped(shell) -> None:
    proc = run(shell, "--per-nic", "--ntp-if", "ens34", "--service", "ens34:netboot")
    assert proc.returncode == 2 and proc.stdout == ""
    assert "--service IF:ntp" in proc.stderr


@needs_bash
@pytest.mark.parametrize("name", ['eth"1', "eth1\n", "eth1 eth2", "ens;34"])
def test_ntp_if_rejects_unsafe_interface_names_before_output(name) -> None:
    proc = run(SHELLS[0], *PRIMARY, "--ntp-if", name)
    assert proc.returncode != 0 and proc.stdout == ""
    assert "not safe" in proc.stderr, proc.stderr    # נדחה בשל השם, לא בשל "unknown option"


@needs_bash
def test_ntp_if_with_no_name_is_refused() -> None:
    proc = run(SHELLS[0], *PRIMARY, "--ntp-if", "")
    assert proc.returncode != 0 and proc.stdout == ""


@needs_bash
@pytest.mark.skipif(shutil.which("nft") is None, reason="nft is not installed")
def test_the_ntp_rule_is_accepted_by_nft(tmp_path) -> None:
    candidate = tmp_path / "ntp.conf"
    candidate.write_text(run(SHELLS[0], *PRIMARY, "--ntp-if", "eth-deploy").stdout, encoding="utf-8")
    proc = _nft_check(candidate)
    assert proc.returncode == 0, proc.stderr


# --- 1. NTP: מה שנשמר ב-DB, והמתקין ----------------------------------------------------------


def _save(conn, nic: str, services) -> None:
    set_setting(conn, nic_services.SETTING_PREFIX + nic, json.dumps({"services": services}))


def test_ntp_interfaces_reads_the_saved_nics_with_ntp(tmp_path) -> None:
    conn = connect(tmp_path / "imagesrv.db")
    _save(conn, "ens34", ["multicast", "netboot", "ntp"])
    _save(conn, "ens35", ["console"])
    _save(conn, "ens33", ["ntp"])
    assert nic_services.ntp_interfaces(conn) == ["ens33", "ens34"]


def test_ntp_interfaces_of_a_server_nobody_edited_is_empty(tmp_path) -> None:
    assert nic_services.ntp_interfaces(connect(tmp_path / "imagesrv.db")) == []


def test_an_unreadable_record_is_an_error_not_no_nics(tmp_path) -> None:
    """עיקרון 5: "לא הצלחנו לקרוא" אינו "אף כרטיס אינו מגיש זמן"."""
    conn = connect(tmp_path / "imagesrv.db")
    set_setting(conn, nic_services.SETTING_PREFIX + "ens34", "{not json")
    with pytest.raises(ValueError, match="ens34"):
        nic_services.ntp_interfaces(conn)


def _installer_script(data_dir: Path) -> str:
    return (f'APP_DIR="{REPO.as_posix()}"; DATA_DIR="{data_dir.as_posix()}"\n'
            + installer_function("console_ntp_interfaces_from_db"))


@pytest.mark.skipif(INSTALLER_BASH is None or shutil.which("python3") is None,
                    reason="the installer helper runs bash + python3")
def test_the_installer_helper_prints_the_ntp_nics(tmp_path, bindir) -> None:
    conn = connect(tmp_path / "imagesrv.db")
    _save(conn, "ens34", ["netboot", "ntp"])
    _save(conn, "ens35", ["console"])
    conn.commit()
    conn.close()
    proc = run_bash(_installer_script(tmp_path) + "console_ntp_interfaces_from_db", tmp_path, bindir)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == ["ens34"]


@pytest.mark.skipif(INSTALLER_BASH is None or shutil.which("python3") is None,
                    reason="the installer helper runs bash + python3")
def test_the_installer_helper_fails_on_an_unreadable_record(tmp_path, bindir) -> None:
    conn = connect(tmp_path / "imagesrv.db")
    set_setting(conn, nic_services.SETTING_PREFIX + "ens34", "{not json")
    conn.commit()
    conn.close()
    proc = run_bash(_installer_script(tmp_path) + "console_ntp_interfaces_from_db", tmp_path, bindir)
    assert proc.returncode != 0, "a read that failed must not look like 'no NICs'"


def test_install_firewall_hands_the_ntp_nics_to_the_generator() -> None:
    """הצנרת: הפונקציה נקראת, והתוצאה נכנסת ל-`NFT_ARGS` כ-`--ntp-if`, ולא נבלעת (`|| die`)."""
    body = installer_function("install_firewall")
    assert re.search(r'NTP_FOR_NFT="\$\(console_ntp_interfaces_from_db\)" \|\| die', body)
    assert 'NFT_ARGS+=(--ntp-if "$NTP_NIC")' in body


# --- 2. udpcast: הנתיב שהקונסולה מפעילה ----------------------------------------------------


def _fake_run(calls):
    def run_(cmd, **_kw):
        calls.append(list(cmd))
        return (True, "table inet imagesrv { }\n", "") if cmd[0] == "sh" else (True, "", "")
    return run_


def _generator_args(tmp_path, monkeypatch, **kw) -> list[str]:
    conf = tmp_path / "nftables.conf"
    conf.write_text("# ImageSrv old\n", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(deploy_net, "_run", _fake_run(calls))
    assert deploy_net.regenerate_firewall(REPO, deploy_if="eth1", servers_if="eth0", conf=conf, **kw) is None
    return next(c for c in calls if c[0] == "sh")


def test_regenerate_firewall_derives_the_range_from_the_sender_portbase(tmp_path, monkeypatch) -> None:
    args = _generator_args(tmp_path, monkeypatch, sender_portbase=30199)
    assert args[args.index("--mcast-ports") + 1] == "30201-30202"


def test_the_derived_range_covers_the_chunk_engines_feedback_port_3512(tmp_path, monkeypatch) -> None:
    """(P+2)..(P+3): the chunk engine sends on P+2, and the receivers' feedback datagram goes to P+3 (#3512).
    The range of P-(P+1) closed that port the moment the console rewrote the firewall. #3173: P and P+1 were
    udpcast's, and they stay closed."""
    args = _generator_args(tmp_path, monkeypatch, sender_portbase=30199)
    low, high = (int(n) for n in args[args.index("--mcast-ports") + 1].split("-"))
    assert low == 30199 + chunksend.PORT_OFFSET and high == low + 1


def test_regenerate_firewall_without_a_portbase_leaves_the_generators_default(tmp_path, monkeypatch) -> None:
    assert "--mcast-ports" not in _generator_args(tmp_path, monkeypatch)


def _app_with_firewall_recorder(tmp_path, seen: list, **kwargs):
    deploy = deploy_net.DeployContext(
        state=deploy_net.DeployState("cli", "eth1", "http://10.44.9.10:7712"),
        agent_port=7712, tftp_root=tmp_path / "tftp", repo_dir=REPO, servers_interface="eth0",
        hooks={"firewall": lambda repo, **kw: seen.append(kw)})
    return server.app.create_app(
        tmp_path / "data", tmp_path / "images", "http://10.44.9.10:7712", deploy=deploy,
        health_hooks={"interfaces": lambda: [{"name": "eth0"}, {"name": "eth1"}]}, **kwargs)


def test_the_console_path_hands_the_senders_real_portbase_to_the_firewall(tmp_path) -> None:
    seen: list = []
    app = _app_with_firewall_recorder(tmp_path, seen, sender_portbase=30199)
    nic = app.state.ctx.nic_services
    assert nic.hooks["firewall"](nic) is None
    assert seen[-1]["sender_portbase"] == 30199


def test_a_production_server_calls_the_firewall_exactly_as_before(tmp_path) -> None:
    seen: list = []
    app = _app_with_firewall_recorder(tmp_path, seen)
    assert app.state.ctx.chunk.port - chunksend.PORT_OFFSET == DEFAULT_PORTBASE
    nic = app.state.ctx.nic_services
    assert nic.hooks["firewall"](nic) is None
    assert "sender_portbase" not in seen[-1]


@needs_bash
def test_the_derived_range_reaches_the_ruleset(tmp_path, monkeypatch) -> None:
    """הקצה-לקצה של הנתיב: argv של `regenerate_firewall` מוזן למחולל האמיתי, והכלל נושא את הטווח החדש."""
    args = _generator_args(tmp_path, monkeypatch, sender_portbase=30199)
    ports = args[args.index("--mcast-ports") + 1]
    out = run(SHELLS[0], *PRIMARY, "--mcast-ports", ports).stdout
    assert "udp dport 30201-30202 ether saddr @known_macs accept" in out
    assert "dport 30199" not in out and "dport 30200" not in out   # #3173: udpcast's P and P+1 stay closed
    default = f"{product_ports.TRIDENT_DATA}-{product_ports.TRIDENT_CONTROL}"
    assert f"dport {default}" not in out   # ברירת המחדל של המחולל (הייצור) הוחלפה בטווח שנגזר
