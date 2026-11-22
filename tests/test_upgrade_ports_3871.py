"""#3871: שרת ששודרג מגרסה שקדמה לפורטי 77xx — מה שנשמר ביום ההתקנה לא שולח את התחנות לפורט הקודם.

פורט שמשתנה בצד אחד בלבד מנתק את כל התחנות. בשדרוג יש שלושה מקומות שנושאים את הפורט של יום ההתקנה,
ואף אחד מהם אינו קוד: כתובת ההפצה (ביחידה או ב-`deploy:url`), ה-bootstrap של GRUB על ה-TFTP, וחומת האש
(זו נבדקת ב-`test_upgrade_rewrites_firewall_3224.py`). כאן:

1. `deploy_net.with_agent_port` ו-`server.main._deploy_state`: הכתובת של הריצה נושאת את הפורט שהסוכן מאזין
   בו, ומה שנשמר אינו נכתב מחדש (שדרוג שנכשל מחזיר את התג הקודם, שקורא את אותה כתובת).
2. `deploy_net.refresh_grub_cfg`: ה-bootstrap הולך אחרי פורט הסוכן של העלייה.
3. `deploy_net.upgrade_hold`: שדרוג שסקריפט של לפני 77xx מריץ (כפתור העדכון מריץ את הסקריפט של התג הישן,
   #2350) עומד לחזור לתג הקודם; עד אז השרת אינו כותב את חומת האש ואת ה-bootstrap בפורטים החדשים.

הפורט "של פעם" כאן הוא 5555: מספר שרירותי, לא הפורט שהוחלף (ההחלפה אינה מכירה אותו בשמו).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import server.main
from boot.grub_menu import GrubConfig, render_bootstrap
from server.db import connect, get_setting
from server.net import deploy_net

from test_upgrade_rewrites_firewall_3224 import _journal, _runtime

REPO = Path(__file__).resolve().parent.parent
AGENT = 7712
BEFORE = "http://10.55.0.1:5555"
NOW = "http://10.55.0.1:7712"


# -- 1. the address the stations are told ------------------------------------------------------------------------

@pytest.mark.parametrize("url, expected", [
    (BEFORE, NOW),                                              # another explicit port: replaced
    (NOW, NOW),                                                 # the agent's port: untouched
    ("http://10.55.0.1", "http://10.55.0.1"),                   # no explicit port: left as written
    ("http://[fd00::1]:5555/x", "http://[fd00::1]:7712/x"),     # IPv6 keeps its brackets, the path stays
    ("http://10.55.0.1:port", "http://10.55.0.1:port"),         # not parsed: returned as it is, never guessed
    ("", ""),
])
def test_the_deploy_address_carries_the_port_the_agent_listens_on(url, expected) -> None:
    assert deploy_net.with_agent_port(url, AGENT) == expected


def test_a_unit_that_names_another_port_is_served_on_the_agents_and_said(capsys) -> None:
    """`IMAGESRV_URL` of install day (the "cli" source): the run's address is on `--port`, and the start says so."""
    args = server.main.build_parser().parse_args(["--server-url", BEFORE])
    state = server.main._deploy_state(args)
    assert (state.source, state.url) == ("cli", NOW)
    said = capsys.readouterr().out
    assert BEFORE in said and NOW in said


def test_a_unit_on_the_agents_port_says_nothing(capsys) -> None:
    args = server.main.build_parser().parse_args(["--server-url", NOW])
    assert server.main._deploy_state(args).url == NOW
    assert capsys.readouterr().out == ""


def test_an_explicit_port_is_the_agents_port(capsys) -> None:
    """`--port` is where the agent listens; a URL that already names it is not touched."""
    args = server.main.build_parser().parse_args(["--server-url", BEFORE, "--port", "5555"])
    assert server.main._deploy_state(args).url == BEFORE
    assert capsys.readouterr().out == ""


def test_the_address_the_console_recorded_is_served_on_the_agents_port_and_not_rewritten(tmp_path, capsys) -> None:
    """The "console" source: `deploy:url` keeps what was recorded -- a failed upgrade rolls back to the previous
    tag, which reads the same setting and listens on its own port."""
    data = tmp_path / "data"
    data.mkdir()
    conn = connect(data / "imagesrv.db")
    try:
        deploy_net.record(conn, "eth1", BEFORE)
    finally:
        conn.close()
    args = server.main.build_parser().parse_args(["--server-url", "", "--data-dir", str(data)])
    state = server.main._deploy_state(args)
    assert (state.source, state.interface, state.url) == ("console", "eth1", NOW)
    assert BEFORE in capsys.readouterr().out
    conn = connect(data / "imagesrv.db")
    try:
        assert get_setting(conn, deploy_net.SETTING_URL) == BEFORE
    finally:
        conn.close()


# -- 2. the GRUB bootstrap on the TFTP root ----------------------------------------------------------------------

def _bootstrap(tftp: Path) -> str:
    return (tftp / "grub" / "grub.cfg").read_text(encoding="ascii")


def test_the_bootstrap_follows_the_agents_port(tmp_path) -> None:
    assert deploy_net.write_grub_cfg(BEFORE, tmp_path) is None
    assert ":5555" in _bootstrap(tmp_path)
    assert deploy_net.refresh_grub_cfg(tmp_path, "", agent_port=AGENT) is None
    text = _bootstrap(tmp_path)
    assert text == render_bootstrap(GrubConfig(server_base=NOW))
    assert "set imagesrv_fallback=10.55.0.1:7712" in text.splitlines() and ":5555" not in text


def test_without_a_port_the_bootstrap_keeps_its_own(tmp_path) -> None:
    assert deploy_net.write_grub_cfg(BEFORE, tmp_path) is None
    assert deploy_net.refresh_grub_cfg(tmp_path, "") is None
    assert _bootstrap(tmp_path) == render_bootstrap(GrubConfig(server_base=BEFORE))


# -- 3. an upgrade that a script of before 77xx is running -------------------------------------------------------

#: the marker's name, written here as tools/server-upgrade.sh writes it (`UPGRADE_STATE`), not read from the code under test
MARKER = "server-upgrade.state"


def _marker(data: Path, doc) -> Path:
    data.mkdir(parents=True, exist_ok=True)
    path = data / MARKER
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
    return path


OLD_SCRIPT = {"repo": "/opt/imagesrv", "previous": "v0.96.0", "tag": "v0.97.0", "stage": "restart"}
#: what the 77xx script adds to it (the key and the value are compared with the code in a test below)
NEW_SCRIPT = {**OLD_SCRIPT, "ports": "77xx"}


def test_no_marker_is_no_upgrade_running(tmp_path) -> None:
    assert deploy_net.upgrade_hold(tmp_path) is None


def test_the_marker_of_the_77xx_script_holds_nothing(tmp_path) -> None:
    _marker(tmp_path, NEW_SCRIPT)
    assert deploy_net.upgrade_hold(tmp_path) is None


def test_the_marker_of_an_older_script_holds_and_says_why(tmp_path) -> None:
    _marker(tmp_path, OLD_SCRIPT)
    reason = deploy_net.upgrade_hold(tmp_path)
    assert reason and "77xx" in reason


@pytest.mark.parametrize("kind", ["garbage", "directory"])
def test_a_marker_that_cannot_be_read_is_not_no_upgrade(tmp_path, kind) -> None:
    """Principle 5: "could not read" is a reason of its own, and nothing is rewritten on a guess."""
    if kind == "garbage":
        _marker(tmp_path, "{not json")
    else:
        (tmp_path / MARKER).mkdir()
    reason = deploy_net.upgrade_hold(tmp_path)
    assert reason and "לא נקרא" in reason


def test_the_upgrade_script_writes_the_key_the_server_looks_for() -> None:
    """The two sides of the marker: what `tools/server-upgrade.sh` writes is what `upgrade_hold` reads."""
    assert (deploy_net.UPGRADE_MARKER, deploy_net.UPGRADE_PORTS_KEY, deploy_net.UPGRADE_PORTS_77XX) == (MARKER, "ports", "77xx")
    script = (REPO / "tools" / "server-upgrade.sh").read_text(encoding="utf-8")
    assert f'"$DATA_DIR/{MARKER}"' in script or f"$DATA_DIR/{MARKER}" in script
    assert f'"{deploy_net.UPGRADE_PORTS_KEY}": "{deploy_net.UPGRADE_PORTS_77XX}"' in script
    assert f"^{deploy_net.PORTS_MARK}" in script            # step 5a greps the generator's mark, as its own line


def test_a_stale_firewall_is_not_rewritten_while_an_older_script_runs_the_upgrade(tmp_path, images_root) -> None:
    """The old tag is about to come back, and it listens on its own ports: the ruleset stays as it is."""
    _marker(tmp_path / "data", OLD_SCRIPT)
    order: list[str] = []
    rt = _runtime(tmp_path, images_root, stale=lambda: "old conf",
                  fw_step=lambda nic: order.append("rewrite"), loaded=order)
    try:
        assert order == ["loaded"]
        held = _journal(rt.conn, "firewall_rewrite_held")
        assert len(held) == 1 and held[0].startswith("old conf — ") and "77xx" in held[0]
        assert _journal(rt.conn, "firewall_rewritten") == []
    finally:
        rt.ctx.chunk.stop()


def test_a_stale_firewall_is_rewritten_while_the_77xx_script_runs_the_upgrade(tmp_path, images_root) -> None:
    _marker(tmp_path / "data", NEW_SCRIPT)
    order: list[str] = []
    rt = _runtime(tmp_path, images_root, stale=lambda: "old conf",
                  fw_step=lambda nic: order.append("rewrite"), loaded=order)
    try:
        assert order == ["rewrite", "loaded"]
        assert _journal(rt.conn, "firewall_rewritten") == ["old conf"]
        assert _journal(rt.conn, "firewall_rewrite_held") == []
    finally:
        rt.ctx.chunk.stop()


def _start_with_a_bootstrap_of_before(tmp_path, images_root):
    from server.app import create_app                                     # noqa: PLC0415

    tftp = tmp_path / "tftp"
    assert deploy_net.write_grub_cfg(BEFORE, tftp) is None
    deploy = deploy_net.DeployContext(
        state=deploy_net.DeployState("cli", "eth1", NOW), agent_port=AGENT, tftp_root=tftp, repo_dir=REPO,
        servers_interface="eth0")
    app = create_app(tmp_path / "data", images_root, NOW, deploy=deploy,
                     health_hooks={"interfaces": lambda: [{"name": "eth0"}, {"name": "eth1"}]},
                     grub_password_file=tmp_path / "etc" / "grub-password")
    app.state.ctx.chunk.stop()
    return _bootstrap(tftp)


def test_a_start_moves_the_bootstrap_to_the_agents_port(tmp_path, images_root) -> None:
    pytest.importorskip("fastapi")
    text = _start_with_a_bootstrap_of_before(tmp_path, images_root)
    assert "set imagesrv_fallback=10.55.0.1:7712" in text.splitlines() and ":5555" not in text


def test_a_start_inside_an_older_scripts_upgrade_leaves_the_bootstrap_on_its_port(tmp_path, images_root) -> None:
    pytest.importorskip("fastapi")
    _marker(tmp_path / "data", OLD_SCRIPT)
    text = _start_with_a_bootstrap_of_before(tmp_path, images_root)
    assert "set imagesrv_fallback=10.55.0.1:5555" in text.splitlines() and ":7712" not in text
