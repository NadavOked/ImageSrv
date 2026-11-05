"""‏#2440: השרת אינו עולה ומגיש בלי חומת האש שהותקנה לו, ו-"אין חומת אש" אינו "הצליח".

1. ‏`imagesrv-server.service` הצהיר רק `After=network-online.target` — בלי שום קשר
   ל-`nftables.service` (נמדד במעבדה: ‏`ORDERING ON nftables: NONE`). ‏nftables
   שלא עלה באתחול השאיר את 8080/8081/8443 בלי סינון, והשרת עלה כרגיל.
2. ‏`regenerate_firewall` החזיר `None` — "הצליח" — כשלא הותקנה חומת אש, וההגדרה
   מהקונסולה דיווחה OK בלי שנכתב כלל אחד.

מה **אסור** לשבור: שרת שהותקן ב-`--no-firewall` (מצב שהמתקין תומך בו). הוא עולה,
וההשלמה ושינוי השירותים ממשיכים — בגלוי (אזהרה בתשובה ושורת יומן).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

import server.app
import server.main
from server.net import deploy_net, ports

REPO = Path(__file__).resolve().parent.parent
UNIT = REPO / "deploy" / "imagesrv-server.service"


def _run_main(tmp_path: Path, monkeypatch) -> list:
    """‏main עם כל מה שאחרי השער מנוטרל. מחזיר את הקריאות ל-create_runtime."""
    uvicorn = pytest.importorskip("uvicorn")
    built = []
    monkeypatch.setattr(server.app, "create_runtime", lambda *a, **k: built.append(a) or "rt")
    monkeypatch.setattr(server.app, "create_agent_app", lambda rt: "agent")
    monkeypatch.setattr(server.app, "create_console_app", lambda rt: "console")
    monkeypatch.setattr(uvicorn, "Config", lambda app, **kw: object())
    monkeypatch.setattr(uvicorn, "Server", lambda config: object())
    monkeypatch.setattr(server.main, "serve_all", lambda servers: None)
    monkeypatch.setattr(server.main, "_primary_alive", lambda *a, **k: None)
    monkeypatch.setattr(asyncio, "run", lambda coro=None: None)
    monkeypatch.setattr(server.main, "_interface_for", lambda url: None)
    monkeypatch.setattr("sys.argv", [
        "server.main", "--server-url", "http://10.44.12.10:8080",
        "--data-dir", str(tmp_path / "data"), "--images", str(tmp_path / "img")])
    server.main.main()
    return built


def test_an_installed_firewall_that_is_not_loaded_stops_the_server(tmp_path, monkeypatch, capsys):
    """המתקין כתב חומת אש, והטבלה `inet imagesrv` אינה טעונה (nftables לא עלה) —
    השרת אינו עולה, ואומר למה. קודם: עלה ומאזין בלי סינון."""
    monkeypatch.setattr(deploy_net, "firewall_installed", lambda conf=None: True)
    monkeypatch.setattr(ports, "read_nft_ruleset", lambda: "table inet filter {\n}\n")
    with pytest.raises(SystemExit) as stopped:
        _run_main(tmp_path, monkeypatch)
    assert stopped.value.code == 1
    assert "inet imagesrv" in capsys.readouterr().err


def test_a_server_installed_without_a_firewall_still_starts_and_says_so(
        tmp_path, monkeypatch, capsys):
    """הצד החיובי — `--no-firewall` נתמך: עולה, ומדפיס אזהרה."""
    monkeypatch.setattr(deploy_net, "firewall_installed", lambda conf=None: False)
    built = _run_main(tmp_path, monkeypatch)
    assert built, "השרת לא עלה בשרת שהותקן בלי חומת אש"


@pytest.mark.parametrize("installed,ruleset,blocked", [
    (True, "table inet imagesrv {\n}\n", False),        # טעונה
    (True, "table inet filter {\n}\n", True),           # הותקנה, לא טעונה
    (True, None, True),                                 # nft לא נקרא — לא "טעונה"
    (False, None, False),                               # ‏--no-firewall
    (None, "table inet imagesrv {\n}\n", True),         # הקובץ לא נקרא
])
def test_the_gate_has_one_answer_per_state(monkeypatch, installed, ruleset, blocked):
    monkeypatch.setattr(deploy_net, "firewall_installed", lambda conf=None: installed)
    assert bool(deploy_net.firewall_gate(read_ruleset=lambda: ruleset)) is blocked


def test_regenerating_without_an_installed_firewall_is_not_a_success(tmp_path):
    error = deploy_net.regenerate_firewall(REPO, deploy_if="eth1", servers_if="eth0",
                                           conf=tmp_path / "nftables.conf")
    assert error, "regenerate_firewall דיווח הצלחה בלי שנכתב כלל"
    assert "חומת אש" in error
    assert not (tmp_path / "nftables.conf").exists()


def test_completing_the_install_without_a_firewall_continues_with_a_warning(tmp_path):
    """‏--no-firewall: ההשלמה אינה נכשלת בגלל חומת אש שאינה קיימת, אבל התשובה אומרת זאת."""
    from server.db import connect
    conn = connect(tmp_path / "x.db")
    hooks = {
        "write_grub_cfg": lambda url, root: None,
        "enable_dnsmasq": lambda: None,
        "firewall": lambda repo, **kw: deploy_net.regenerate_firewall(
            repo, **kw, conf=tmp_path / "absent.conf"),
        "restart_server": lambda: None,
    }
    ctx = deploy_net.DeployContext(
        state=deploy_net.DeployState("none", None, None), agent_port=8080,
        tftp_root=tmp_path / "tftp", repo_dir=REPO, servers_interface="eth0", hooks=hooks)
    result = deploy_net.complete(ctx, conn, interface="eth1", server_ip="10.44.9.10")
    assert result["ok"] is True and result["recorded"] is True, result
    assert any("חומת אש" in w for w in result.get("warnings", [])), result


def test_nic_services_on_a_server_without_a_firewall_journal_it(tmp_path, images_root):
    """שינוי שירותים לכל כרטיס אינו מוחזר בגלל חומת אש שאינה קיימת — אבל נרשם."""
    deploy = deploy_net.DeployContext(
        state=deploy_net.DeployState("cli", "eth1", "http://10.44.9.10:8080"),
        agent_port=8080, tftp_root=tmp_path / "tftp", repo_dir=REPO,
        servers_interface="eth0",
        hooks={"firewall": lambda repo, **kw: deploy_net.regenerate_firewall(
            repo, **kw, conf=tmp_path / "absent.conf")})
    app = server.app.create_app(tmp_path / "data", images_root, "http://10.44.9.10:8080",
                                deploy=deploy)
    ctx = app.state.ctx
    assert ctx.nic_services.hooks["firewall"](ctx.nic_services) is None
    events = [r["event"] for r in ctx.conn.execute("SELECT event FROM journal")]
    assert "firewall_not_installed" in events


def test_the_unit_orders_after_nftables_without_a_hard_dependency():
    """סדר: ‏After=nftables.service, כדי שהשער ימצא את הטבלה טעונה באתחול.
    בלי Requires/Requisite — אלה היו מפילים (או מדליקים nftables) בשרת
    שהותקן ב---no-firewall; האכיפה היא השער ב-main."""
    lines = [l for l in UNIT.read_text(encoding="utf-8").splitlines()
             if not l.lstrip().startswith("#")]
    after = " ".join(l.split("=", 1)[1] for l in lines if l.startswith("After="))
    assert "nftables.service" in after.split()
    assert not any(re.match(r"(Requires|Requisite|BindsTo)=.*nftables", l) for l in lines)
