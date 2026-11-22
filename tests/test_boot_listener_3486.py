"""#3486 -- `/boot` של GRUB והקושחה במאזין משלו (`--boot-port`).

הכרעת נדב 15/11: אין בעיה לפתוח בחומת האש פורטים ספציפיים, ולכן השירות היחיד
שפתוח למחשב לא רשום אינו חולק עוד מאזין עם ה-API של הסוכן. ואין שרתי ייצור ולכן אין
תצורה ישנה לשמור עליה: ההפרדה תמיד דלוקה, `/boot` מוגש רק על פורט ה-boot (7711 כברירת
מחדל, `--boot-port` משנה), ו-7712 משרת רק רשומים (חוץ מ-hello).

שלוש חוליות, וכל אחת נבדקת מהצד שלה:

1. **מה ש-GRUB מקבל** (`boot/grub_menu.py`, `boot/http.py`): התפריט, הקרנל, ה-initrd,
   הפירורים וה-bootstrap פונים לפורט ה-boot; `imagesrv.server=` בשורת הקרנל נשאר
   פורט הסוכן (עיקרון 2: אין שדה חדש בשורת הקרנל).
2. **מי מגיש** (`create_boot_app`, `create_agent_app`, `deploy_gate`): המאזין החדש
   מגיש `/boot/*` ושום דבר אחר; על פורט הסוכן `/boot` הוא 404, ולמחשב לא רשום 403.
3. **מה סביב** (`deploy_net`, `nftables-rules.sh`, `main`, `/ports`): ה-`grub.cfg`
   על ה-TFTP, כלל חומת האש, הדגלים והמאזין ב-`main`, ושורת הפורטים.

בקרה שלילית: ראו גוף ה-PR -- כל קובצי הייצור שהשינוי נגע בהם הוחזרו, והטסטים כאן נכשלו.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from boot.grub_menu import GrubConfig, render_bootstrap
from boot.http import config_for_scope
from conftest import hello_body
from server.agent import deploy_gate, identity, registry
from server.app import create_agent_app, create_runtime
from server.core import ports as product_ports
from server.net import deploy_net, ports
from server.system import health
from test_boot_http import fetch_menu
from test_machine_identity import write_leases
from test_nftables_rules import generate, needs_bash

VLAN = "10.44.12.10"                  # כתובת ההפצה של השרת (`--server-url`)
AGENT = f"{VLAN}:7712"
BOOT_PORT = 7711
SPLIT = GrubConfig(server_base=f"http://{AGENT}", boot_port=BOOT_PORT)
REG, REG_IP = "b4:2e:99:07:1a:c4", "10.44.12.21"
UNREG, UNREG_IP = "b4:2e:99:07:1a:c5", "10.44.12.22"
CLONERS = "grp_CLONERS"


def _cmdline(text: str) -> str:
    """שורת הקרנל של ערך הסוכן: מה שאחרי `/boot/vmlinuz `."""
    line = next(row for row in text.splitlines() if " linux (http," in row)
    return line.split("/boot/vmlinuz ", 1)[1]


# --- 1. מה ש-GRUB מקבל ----------------------------------------------------------------


def test_without_boot_port_the_grub_address_is_the_agent_address():
    assert GrubConfig(server_base=f"http://{AGENT}").grub_host == AGENT
    assert GrubConfig(server_base=f"http://{AGENT}", boot_port=None).grub_host == AGENT


def test_boot_port_moves_grub_to_the_boot_port_and_leaves_the_kernel_line_on_the_agent_port():
    text = fetch_menu(config=SPLIT, server=(VLAN, BOOT_PORT))
    assert f"linux (http,{VLAN}:{BOOT_PORT})/boot/vmlinuz" in text
    assert f"initrd (http,{VLAN}:{BOOT_PORT})/boot/initrd.img" in text
    assert f'cat "(http,{VLAN}:{BOOT_PORT})/boot/step?mac=' in text
    cmdline = _cmdline(text)
    assert f"imagesrv.server=https://{AGENT}" in cmdline
    assert f":{BOOT_PORT}" not in cmdline
    # שום מקום אחר בתפריט אינו נושא את פורט ה-boot: רק מה ש-GRUB עצמו מוריד.
    assert f":{BOOT_PORT}" not in text.replace(f"(http,{VLAN}:{BOOT_PORT})", "")





def test_the_address_comes_from_the_connection_and_the_ports_from_the_config():
    """תחנה שהגיעה דרך כרטיס אחר (#39): הכתובת מהחיבור, שני הפורטים מהתצורה."""
    scoped = config_for_scope({"server": ("10.10.10.8", BOOT_PORT)}, SPLIT)
    assert scoped.server_base == "http://10.10.10.8:7712"
    assert scoped.boot_port == BOOT_PORT and scoped.grub_host == f"10.10.10.8:{BOOT_PORT}"
    text = fetch_menu(config=SPLIT, server=("10.10.10.8", BOOT_PORT))
    assert f"linux (http,10.10.10.8:{BOOT_PORT})/boot/vmlinuz" in text
    assert "imagesrv.server=https://10.10.10.8:7712" in _cmdline(text)
    assert VLAN not in text


def test_without_boot_port_the_scope_still_decides_the_port_as_before():
    scoped = config_for_scope({"server": ("10.10.10.8", 8123)},
                              GrubConfig(server_base=f"http://{AGENT}"))
    assert scoped.server_base == "http://10.10.10.8:8123" and scoped.boot_port is None


@pytest.mark.parametrize("port", [0, 65536, -1, True])
def test_a_boot_port_that_is_not_a_port_is_refused(port):
    with pytest.raises(ValueError, match="boot_port"):
        GrubConfig(server_base=f"http://{AGENT}", boot_port=port)


def test_the_bootstrap_on_the_tftp_points_at_the_boot_port():
    text = render_bootstrap(SPLIT)
    assert f"set imagesrv_fallback={VLAN}:{BOOT_PORT}" in text.splitlines()
    assert f"set imagesrv_server=${{net_default_server}}:{BOOT_PORT}" in text
    assert ":7712" not in text
    plain = render_bootstrap(GrubConfig(server_base=f"http://{AGENT}"))
    assert f"set imagesrv_fallback={AGENT}" in plain.splitlines()
    assert f"set imagesrv_server=${{net_default_server}}:7712" in plain


# --- 2. מי מגיש -----------------------------------------------------------------------


def _runtime(tmp_path: Path, images_root: Path, *, boot_port=BOOT_PORT):
    from fakesend import Recorder                          # noqa: PLC0415

    boot_dir = tmp_path / "boot"
    boot_dir.mkdir(exist_ok=True)
    (boot_dir / "vmlinuz").write_bytes(b"K" * 100)
    leases = write_leases(tmp_path / "dnsmasq.leases", {REG: REG_IP, UNREG: UNREG_IP})
    rt = create_runtime(tmp_path / "data", images_root, f"http://{AGENT}",
                        sender_runner=Recorder(block=True), wol_send=lambda _p: None,
                        identity_hooks={"leases": identity.LeaseFile(leases)},
                        known_macs_hooks={"apply": lambda text: None,
                                          "firewall": lambda macs: None},
                        boot_dir=boot_dir, boot_port=boot_port)
    registry.add_machine(rt.conn, REG, "cloner-1", CLONERS, "test")
    return rt


def create_boot_app(rt):
    """מייבאים בקריאה ולא בראש הקובץ: בקרה שלילית שמפילה את הייבוא הייתה מפילה גם את
    הטסטים שאינם צריכים אותו, ו"כולם נפלו" אינו ראיה לכלום."""
    from server.app import create_boot_app as real        # noqa: PLC0415
    return real(rt)


def _client(app, ip: str, port: int) -> TestClient:
    return TestClient(app, base_url=f"http://{VLAN}:{port}", client=(ip, 40000),
                      raise_server_exceptions=False)


@pytest.fixture()
def rt(tmp_path: Path, images_root: Path):
    runtime = _runtime(tmp_path, images_root)
    yield runtime
    runtime.ctx.chunk.stop()


def test_the_boot_listener_serves_boot_files_to_a_machine_nobody_registered(rt):
    c = _client(create_boot_app(rt), UNREG_IP, BOOT_PORT)
    assert c.get("/boot/vmlinuz").content == b"K" * 100
    menu = c.get(f"/boot/menu?mac={UNREG}")
    assert menu.status_code == 200 and deploy_gate.EVENT not in menu.text, menu.text


def test_the_boot_listener_menu_for_a_cloner_points_grub_at_the_boot_port(rt):
    text = _client(create_boot_app(rt), REG_IP, BOOT_PORT).get(f"/boot/menu?mac={REG}").text
    assert f"linux (http,{VLAN}:{BOOT_PORT})/boot/vmlinuz" in text, text
    assert f"imagesrv.server=https://{AGENT}" in _cmdline(text)


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/v1/agent/hello"),
    ("GET", "/api/v1/agent/sessions/active"),
    ("GET", "/api/v1/images/img_1/manifest"),
    ("POST", "/api/console/login"),
    ("GET", "/console/"),
    ("GET", "/"),
])
def test_the_boot_listener_serves_nothing_but_boot(rt, method, path):
    r = _client(create_boot_app(rt), REG_IP, BOOT_PORT).request(method, path, json={})
    assert r.status_code == 404, (path, r.status_code, r.text)





def test_the_agent_port_serves_no_boot_and_closes_everything_to_an_unregistered_machine(rt):
    """#3486: /boot אינו על פורט הסוכן. מחשב לא רשום מקבל שם 403 לכל נתיב, כולל /boot;
    רשומה עוברת את השער ופוגשת 404; ורק hello נשאר פתוח (#1802)."""
    app = create_agent_app(rt)
    unregistered = _client(app, UNREG_IP, 7712).get(f"/boot/menu?mac={UNREG}")
    assert unregistered.status_code == 403
    assert unregistered.json()["code"] == deploy_gate.EVENT
    assert _client(app, REG_IP, 7712).get(f"/boot/menu?mac={REG}").status_code == 404
    assert _client(app, REG_IP, 7712).get("/boot/vmlinuz").status_code == 404
    hello = _client(app, UNREG_IP, 7712).post("/api/v1/agent/hello", json=hello_body(UNREG))
    assert hello.status_code == 200 and hello.json()["known"] is False
    # והמאזין הנפרד מגיש לכולם
    assert _client(create_boot_app(rt), UNREG_IP, BOOT_PORT).get(
        f"/boot/menu?mac={UNREG}").status_code == 200


def test_the_combined_test_app_still_serves_boot_and_points_grub_at_the_agent_port(tmp_path, images_root):
    """`create_app` (בדיקות בלבד): אפליקציה אחת, בלי מאזין boot, ולכן `boot_port` None."""
    from server.app import create_app                      # noqa: PLC0415

    app = create_app(tmp_path / "data", images_root, f"http://{AGENT}")
    try:
        r = TestClient(app, base_url=f"http://{AGENT}").get(f"/boot/menu?mac={REG}")
        assert r.status_code == 200, r.text
        assert f":{BOOT_PORT}" not in r.text
    finally:
        app.state.ctx.chunk.stop()


def test_the_gate_leaves_only_hello_open():
    assert deploy_gate.is_open("POST", "/api/v1/agent/hello")
    assert not deploy_gate.is_open("GET", "/boot/menu")
    assert not deploy_gate.is_open("GET", "/boot")
    assert not deploy_gate.is_open("GET", "/api/v1/agent/state")
    assert not hasattr(deploy_gate, "OPEN_MOUNT")


# --- 3. מה סביב -----------------------------------------------------------------------


def test_refresh_writes_the_port_this_server_listens_on_now_and_goes_back(tmp_path):
    cfg = tmp_path / "grub" / "grub.cfg"
    assert deploy_net.write_grub_cfg("http://10.44.9.10:7712", tmp_path) is None

    assert deploy_net.refresh_grub_cfg(tmp_path, "", agent_port=BOOT_PORT) is None
    text = cfg.read_text(encoding="ascii")
    assert f"set imagesrv_fallback=10.44.9.10:{BOOT_PORT}" in text.splitlines()
    assert f"${{net_default_server}}:{BOOT_PORT}" in text

    assert deploy_net.refresh_grub_cfg(tmp_path, "", agent_port=7712) is None   # --boot-port הוסר
    assert "set imagesrv_fallback=10.44.9.10:7712" in cfg.read_text(encoding="ascii").splitlines()

    assert deploy_net.refresh_grub_cfg(tmp_path, "", agent_port=BOOT_PORT) is None
    assert deploy_net.refresh_grub_cfg(tmp_path, "") is None              # בלי port: הקובץ כמו שהוא
    assert f"set imagesrv_fallback=10.44.9.10:{BOOT_PORT}" in cfg.read_text(
        encoding="ascii").splitlines()


def test_the_server_start_writes_the_boot_port_into_the_grub_cfg(tmp_path, images_root):
    from server.app import create_app                      # noqa: PLC0415

    tftp = tmp_path / "tftp"
    deploy_net.write_grub_cfg(f"http://{AGENT}", tftp)
    deploy = deploy_net.DeployContext(
        state=deploy_net.DeployState("cli", "eth1", f"http://{AGENT}"), agent_port=7712,
        tftp_root=tftp, repo_dir=tmp_path, servers_interface="eth0", boot_port=BOOT_PORT)
    app = create_app(tmp_path / "data", images_root, f"http://{AGENT}", deploy=deploy,
                     grub_password_file=tmp_path / "grub-password")
    try:
        text = (tftp / "grub" / "grub.cfg").read_text(encoding="ascii")
        assert f"set imagesrv_fallback={VLAN}:{BOOT_PORT}" in text.splitlines()
    finally:
        app.state.ctx.chunk.stop()


def test_grub_port_is_the_boot_port_when_there_is_one(tmp_path):
    def ctx(**kw):
        return deploy_net.DeployContext(
            state=deploy_net.DeployState("none", None, None), agent_port=7712,
            tftp_root=tmp_path, repo_dir=tmp_path, **kw)
    assert ctx().grub_port == 7712
    assert ctx(boot_port=BOOT_PORT).grub_port == BOOT_PORT


@pytest.mark.parametrize("boot_port,grub_url,fw_kwargs", [
    (None, "http://10.44.9.10:7712", {}),
    (BOOT_PORT, f"http://10.44.9.10:{BOOT_PORT}", {"boot_port": BOOT_PORT}),
])
def test_completing_the_deploy_network_hands_grub_and_the_firewall_the_boot_port(
        tmp_path, boot_port, grub_url, fw_kwargs):
    from server.db import connect                          # noqa: PLC0415

    conn = connect(tmp_path / "x.db")
    seen: dict = {}

    def firewall(repo, **kw):
        seen["fw"] = kw
        return None

    hooks = {"interfaces": lambda: [{"name": "eth1"}],
             "write_grub_cfg": lambda url, root: seen.setdefault("grub", url) and None,
             "enable_dnsmasq": lambda: None, "firewall": firewall,
             "restart_server": lambda: None}
    ctx = deploy_net.DeployContext(
        state=deploy_net.DeployState("none", None, None), agent_port=7712,
        tftp_root=tmp_path / "tftp", repo_dir=tmp_path, servers_interface="eth0",
        hooks=hooks, boot_port=boot_port)
    result = deploy_net.complete(ctx, conn, interface="eth1", server_ip="10.44.9.10")
    assert result["ok"] is True, result
    assert seen["grub"] == grub_url
    assert result["url"] == "https://10.44.9.10:7712"          # מה שנרשם: כתובת הסוכן
    assert seen["fw"].get("boot_port") == fw_kwargs.get("boot_port")
    assert ("boot_port" in seen["fw"]) is bool(fw_kwargs)


def test_the_nic_services_firewall_step_carries_the_boot_port(tmp_path, images_root):
    from server.app import create_app                      # noqa: PLC0415

    seen: list[dict] = []
    deploy = deploy_net.DeployContext(
        state=deploy_net.DeployState("cli", "eth1", f"http://{AGENT}"), agent_port=7712,
        tftp_root=tmp_path / "tftp", repo_dir=tmp_path, servers_interface="eth0",
        boot_port=BOOT_PORT,
        hooks={"firewall": lambda repo, **kw: seen.append(kw)})
    app = create_app(tmp_path / "data", images_root, f"http://{AGENT}", deploy=deploy,
                     health_hooks={"interfaces": lambda: [{"name": "eth0"}, {"name": "eth1"}]})
    try:
        nic = app.state.ctx.nic_services
        assert nic.hooks["firewall"](nic) is None
        assert seen and seen[-1]["boot_port"] == BOOT_PORT
    finally:
        app.state.ctx.chunk.stop()


def test_regenerate_firewall_passes_the_boot_port_to_the_generator(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(deploy_net, "_run", lambda args, **kw: calls.append(args) or (False, "", "stop"))
    conf = tmp_path / "nftables.conf"
    conf.write_text("# ImageSrv\n", encoding="utf-8")
    for boot_port in (BOOT_PORT, None):
        deploy_net.regenerate_firewall(tmp_path, deploy_if="eth1", servers_if="eth0",
                                       boot_port=boot_port, conf=conf)
    assert calls[0][-2:] == ["--boot-port", str(BOOT_PORT)]
    assert "--boot-port" not in calls[1]


def _rules(out: str) -> list[str]:
    return [line.strip() for line in out.splitlines() if line.strip().startswith("iifname")]


def test_the_firewall_always_opens_the_boot_port_on_the_deploy_card():
    base = ("--deploy-if", "eth-deploy", "--servers-if", "eth-srv")
    default = _rules(generate(*base))
    assert f'iifname "eth-deploy" tcp dport {BOOT_PORT} accept' in default
    assert 'iifname "eth-deploy" tcp dport 7712 accept' in default
    assert not any(f"dport {BOOT_PORT}" in r and '"eth-srv"' in r for r in default)
    moved = _rules(generate(*base, "--boot-port", "9083"))
    assert 'iifname "eth-deploy" tcp dport 9083 accept' in moved
    assert not any(f"dport {BOOT_PORT}" in r for r in moved)


def test_the_per_nic_firewall_opens_the_boot_port_with_netboot_only():
    args = ("--per-nic", "--service", "ens34:netboot", "--service", "ens35:console")
    got = _rules(generate(*args))
    assert f'iifname "ens34" tcp dport {BOOT_PORT} accept' in got
    assert not any('"ens35"' in r and str(BOOT_PORT) in r for r in got)
    assert 'iifname "ens34" tcp dport 9083 accept' in _rules(generate(*args, "--boot-port", "9083"))
    closed = generate("--per-nic", "--service", "ens35:console")
    assert str(BOOT_PORT) not in closed


@needs_bash
@pytest.mark.parametrize("value", ["80x2", "-1", "8 0"])
def test_the_firewall_generator_refuses_a_boot_port_that_is_not_a_number(value):
    import subprocess                                      # noqa: PLC0415
    from test_nftables_rules import BASH, GEN              # noqa: PLC0415

    proc = subprocess.run([BASH, str(GEN), "--servers-if", "eth0", "--boot-port", value],
                          capture_output=True, text=True, encoding="utf-8", check=False,
                          stdin=subprocess.DEVNULL)
    assert proc.returncode == 2 and "--boot-port must be a number" in proc.stderr


def test_the_boot_port_defaults_to_the_constant_and_the_validation_names_the_clash(capsys):
    import server.main as server_main                      # noqa: PLC0415

    assert product_ports.BOOT == BOOT_PORT
    parser = server_main.build_parser()
    args = parser.parse_args(["--server-url", f"http://{AGENT}"])
    assert args.boot_port == BOOT_PORT
    assert server_main._boot_listener_port(parser, args) == BOOT_PORT
    args = parser.parse_args(["--server-url", f"http://{AGENT}", "--boot-port", "9083"])
    assert server_main._boot_listener_port(parser, args) == 9083

    for argv, word in ((["--boot-port", "443"], "--console-port"),
                       (["--boot-port", "7712"], "--port"),
                       (["--boot-port", "7700"], "--interserver-port"),
                       (["--boot-port", "70000"], "1-65535")):
        bad = parser.parse_args(["--server-url", f"http://{AGENT}", *argv])
        with pytest.raises(SystemExit):
            server_main._boot_listener_port(parser, bad)
        # אחרי "error:" בלבד -- שורת ה-usage שלפניה מונה כל דגל, ואין בה ראיה
        assert word in capsys.readouterr().err.split("error:", 1)[1]


def test_the_flag_that_turned_the_split_off_is_gone(capsys):
    import server.main as server_main                      # noqa: PLC0415

    with pytest.raises(SystemExit):
        server_main.build_parser().parse_args(["--no-boot-on-agent-port"])
    assert "--no-boot-on-agent-port" in capsys.readouterr().err


def _run_main(tmp_path: Path, monkeypatch, *extra_argv: str):
    """כמו `test_console_bind.run_main`, בתוספת מה ש-`main` מעביר ל-runtime ולאפליקציית ה-boot."""
    import uvicorn

    import server.app
    import server.main

    configs: dict[str, list[dict]] = {}
    runtime_kwargs: dict = {}

    def fake_runtime(*_a, **kw):
        runtime_kwargs.update(kw)
        return "rt"

    def fake_config(app, **kwargs):
        configs.setdefault(app, []).append(kwargs)
        return object()

    monkeypatch.setattr(server.app, "create_runtime", fake_runtime)
    monkeypatch.setattr(server.app, "create_agent_app", lambda rt: "agent")
    monkeypatch.setattr(server.app, "create_console_app", lambda rt: "console")
    monkeypatch.setattr(server.app, "create_boot_app", lambda rt: "boot")
    monkeypatch.setattr(uvicorn, "Config", fake_config)
    monkeypatch.setattr(uvicorn, "Server", lambda config: object())
    monkeypatch.setattr(server.main, "serve_all", lambda servers: None)
    monkeypatch.setattr(server.main, "_primary_alive", lambda *a, **k: None)
    monkeypatch.setattr(asyncio, "run", lambda coro=None: coro.close() if coro is not None else None)
    monkeypatch.setattr(server.main, "_interface_for", lambda url: None)
    monkeypatch.setattr("sys.argv", [
        "server.main", "--server-url", f"http://{AGENT}",
        "--data-dir", str(tmp_path / "data"), "--images", str(tmp_path / "img"), *extra_argv])
    server.main.main()
    return configs, runtime_kwargs


def test_main_always_binds_a_boot_listener_on_the_agent_address(tmp_path, monkeypatch):
    configs, runtime_kwargs = _run_main(tmp_path, monkeypatch)
    assert [(c["host"], c["port"]) for c in configs["boot"]] == [(VLAN, BOOT_PORT)]
    assert [(c["host"], c["port"]) for c in configs["agent"]] == [(VLAN, 7712)]
    # אותה הגדרת סגירה של חיבור ה-GRUB (#1423) כמו בפורט הסוכן.
    assert configs["boot"][0]["http"] is configs["agent"][0]["http"]
    assert configs["agent"][0]["ssl_certfile"] and configs["agent"][0]["ssl_keyfile"]
    assert "ssl_certfile" not in configs["boot"][0]
    assert runtime_kwargs["boot_port"] == BOOT_PORT
    assert runtime_kwargs["deploy"].boot_port == BOOT_PORT


def test_main_binds_the_boot_listener_on_the_port_it_was_given(tmp_path, monkeypatch):
    configs, runtime_kwargs = _run_main(tmp_path, monkeypatch, "--boot-port", "9083")
    assert [c["port"] for c in configs["boot"]] == [9083]
    assert runtime_kwargs["boot_port"] == 9083 and runtime_kwargs["deploy"].boot_port == 9083





# --- /ports ושורת הבריאות -------------------------------------------------------------


def test_boot_listener_names_the_port_grub_uses():
    class Listeners:
        def __init__(self, ids):
            self._ids = ids

        def ids(self):
            return self._ids

        def spec(self, port_id):
            return {"port": BOOT_PORT}

    base = f"http://{AGENT}"
    assert health.boot_listener({}, base) == ("http_boot", base)
    assert health.boot_listener({"port_listeners": Listeners(["http_boot"])}, base) == (
        "http_boot", base)
    assert health.boot_listener({"port_listeners": Listeners(["http_boot", "http_grub"])},
                                base) == ("http_grub", f"http://{VLAN}:{BOOT_PORT}")


SS_TCP = ("State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
          f"LISTEN 0      4096       {VLAN}:7712        0.0.0.0:*\n"
          f"LISTEN 0      4096       {VLAN}:{BOOT_PORT}        0.0.0.0:*\n")


def _ports_app(tmp_path: Path, images_root: Path, *, grub: bool):
    from server.app import create_app                      # noqa: PLC0415
    from server.console import users                       # noqa: PLC0415

    listeners = ports.Listeners()
    listeners.add("http_boot", [], port=7712, hosts=[VLAN])
    if grub:
        listeners.add("http_grub", [], port=BOOT_PORT, hosts=[VLAN])
    probed: list[str] = []
    sized: list[str] = []
    tftp = tmp_path / "tftp"
    tftp.mkdir(parents=True)
    hooks = {"ss": lambda: "", "ss_tcp": lambda: SS_TCP, "nft_ruleset": lambda: "",
             "unit_active": lambda name: "active",
             "http_get": lambda url: probed.append(url) or 200,
             "http_size": lambda url: sized.append(url) or (200, 31_000_000),
             "interfaces": lambda: [{"name": "eth0", "state": "up", "mac": "aa", "addresses": []}],
             "tftp_root": lambda: tftp,
             "port_listeners": listeners}
    app = create_app(tmp_path / "data", images_root, f"http://{AGENT}", health_hooks=hooks)
    app.state.sized = sized
    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test",
                 is_builtin=True, check_policy=False)
    admin = TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    return app, admin, probed


def test_the_ports_page_gets_a_row_for_the_boot_listener_and_probes_the_menu_on_it(
        tmp_path, images_root):
    app, admin, probed = _ports_app(tmp_path, images_root, grub=True)
    try:
        rows = {r["id"]: r for r in admin.get("/api/console/ports").json()}
        grub = rows["http_grub"]
        assert grub["port"] == str(BOOT_PORT) and grub["state"] == "ok", grub
        assert grub["network"] == "deploy" and grub["toggle"] == "confirm"
        assert grub["toggle_url"] == "/api/console/ports/http_grub"
        assert grub["warning_he"]
        assert f"{VLAN}:{BOOT_PORT}" in grub["detail"]
        # שורת הסוכן אינה טוענת עוד ל-/boot, ואינה בודקת תפריט
        assert rows["http_boot"]["desc"] == "API הסוכן"
        assert probed and all(f":{BOOT_PORT}/boot/menu" in url for url in probed), probed
    finally:
        app.state.ctx.chunk.stop()


def test_health_pulls_the_boot_files_from_the_boot_listener(tmp_path, images_root):
    app, admin, _probed = _ports_app(tmp_path, images_root, grub=True)
    try:
        assert admin.get("/api/console/health").status_code == 200
        sized = app.state.sized
        assert any(u == f"http://{VLAN}:{BOOT_PORT}/boot/vmlinuz" for u in sized), sized
        assert not any(u.startswith(f"http://{AGENT}/boot") for u in sized), sized
    finally:
        app.state.ctx.chunk.stop()


def test_the_ports_page_has_no_boot_row_without_the_listener(tmp_path, images_root):
    app, admin, probed = _ports_app(tmp_path, images_root, grub=False)
    try:
        rows = {r["id"]: r for r in admin.get("/api/console/ports").json()}
        assert "http_grub" not in rows
        assert "אתחול" in rows["http_boot"]["desc"]
        assert probed and all(url.startswith(f"http://{AGENT}/boot/menu") for url in probed)
    finally:
        app.state.ctx.chunk.stop()


def test_a_77xx_firewall_without_the_boot_port_is_stale_and_the_upgrade_checks_it(tmp_path):
    """שרת 77xx ששודרג לגרסה הזו: חומת האש שלו נושאת את סימן 77xx אבל לא את פורט ה-/boot, ובכל עלייה
    השרת שולח את GRUB לפורט ה-boot. בלי כתיבה מחדש GRUB היה מוצא אותו סגור, ולכן קובץ בלי `BOOT_MARK`
    ישן, ו-`server-upgrade.sh` מחזיר לתג הקודם כשהסימן חסר אחרי השדרוג."""
    text = (Path(__file__).parent / "fixtures" / "nftables" / "primary.conf").read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    old = "".join(line for line in lines if not line.startswith(deploy_net.BOOT_MARK))
    assert len(old.splitlines()) == len(lines) - 1 and deploy_net.PORTS_MARK in old
    conf = tmp_path / "nftables.conf"
    conf.write_text(text, encoding="utf-8")
    assert deploy_net.stale_firewall(conf) is None
    conf.write_text(old, encoding="utf-8")
    reason = deploy_net.stale_firewall(conf)
    assert reason and str(BOOT_PORT) in reason
    script = (Path(__file__).resolve().parents[1] / "tools" / "server-upgrade.sh").read_text(encoding="utf-8")
    assert f"grep -q '^{deploy_net.BOOT_MARK}'" in script
