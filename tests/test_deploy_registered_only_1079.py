"""הכרעת נדב 14/11 (#1079 F6, #2485): ברשת ההפצה השרת משרת רק מחשבים רשומים.

> "את רשת ההפצה אני מגן עליה בווילן נפרד, והשרת צריך למנוע ממחשבים לא רשומים
> לעבור דרך השרת."

שלוש שכבות, וכל אחת נבדקת כאן מהצד שלה:

1. **HTTP** (`server/agent/deploy_gate.py`): בקשה על כתובת ההפצה, מכתובת שהמחשב
   החכור עליה אינו רשום — 403 `deploy_unregistered` לפני כל שירות. פתוחים לו רק
   `/boot/…` ו-`hello` (הדרך לדיסק המקומי או למסך "לא רשום", #1802). שורת יומן
   אחת לכתובת בחלון, ותקרת הצפה. וילן הניהול והקונסולה — לא נוגעים.
2. **nftables** (`deploy/nftables-rules.sh`, `server/net/known_fw.py`): המשוב של
   udpcast נכנס רק מ-MAC רשום (`known_macs`), והקבוצה מתעדכנת עם כל שינוי ברשימה.
3. **`/agent/state`**: מחשב לא רשום אינו מקבל ממנו אף אימג' — גם מחוץ לשער (וילן אחר,
   או המתג `identity_check` כבוי). המקלט של מנוע החתיכות (`chunkcast --from`) — בענף
   המעבדה בלבד (#3214), כי המנוע אינו ב-main.

בקרה שלילית: ראו גוף ה-PR — כל קובץ ייצור שהשינוי נגע בו הוחזר, והטסטים כאן נכשלו.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from conftest import hello_body
from server.agent import deploy_gate, identity, registry
from server.app import create_agent_app, create_console_app, create_runtime
from server.console import users
from server.db import set_setting, update_one, writing
from server.net import known_fw
from test_machine_identity import write_leases
from test_nftables_rules import generate, needs_bash

VLAN = "10.44.12.10"                  # כתובת ההפצה של השרת (`--server-url`)
MGMT = "10.10.10.5"                   # כתובת אחרת של אותו שרת: וילן הניהול
REG, REG_IP = "b4:2e:99:07:1a:c4", "10.44.12.21"
UNREG, UNREG_IP = "b4:2e:99:07:1a:c5", "10.44.12.22"
NOLEASE_IP = "10.44.12.23"
CLONERS = "grp_CLONERS"


@pytest.fixture()
def env(tmp_path: Path, images_root: Path):
    from test_sender import Recorder                       # noqa: PLC0415

    leases = write_leases(tmp_path / "dnsmasq.leases", {REG: REG_IP, UNREG: UNREG_IP})
    fw: list[list[str]] = []
    rt = create_runtime(tmp_path / "data", images_root, f"http://{VLAN}:8080",
                        sender_runner=Recorder(block=True), wol_send=lambda _p: None,
                        identity_hooks={"leases": identity.LeaseFile(leases)},
                        known_macs_hooks={"apply": lambda text: None,
                                          "firewall": lambda macs: fw.append(list(macs))})
    registry.add_machine(rt.conn, REG, "cloner-1", CLONERS, "test")
    agent_app = create_agent_app(rt)

    def client(ip: str, local: str = VLAN) -> TestClient:
        # `base_url` = הכתובת המקומית שהחיבור התקבל עליה (scope["server"]); `client` = הפונה.
        return TestClient(agent_app, base_url=f"http://{local}:8080", client=(ip, 40000),
                          raise_server_exceptions=False)

    yield {"rt": rt, "conn": rt.conn, "client": client, "fw": fw, "leases": leases, "tmp": tmp_path}
    rt.ctx.sender.stop()


def _journal(conn, event: str) -> list[str]:
    return [r["detail"] for r in conn.execute("SELECT detail FROM journal WHERE event = ? ORDER BY id", (event,))]


#: שירותי שיכפול ומידע שאין למחשב לא רשום מה לעשות בהם — מכל ראוטר באפליקציית הסוכן.
CLOSED = [
    ("GET", "/api/v1/agent/sessions/active", None),
    ("GET", "/api/v1/agent/state?mac=" + UNREG, None),
    ("GET", "/api/v1/images/img_1/manifest", None),
    ("GET", "/api/v1/images/img_1/files/p1.pcl.zst", None),
    ("GET", "/api/v1/images/img_1/index/3", None),
    ("POST", "/api/v1/agent/progress", {"mac": UNREG, "session_id": "s1", "state": "writing"}),
    ("POST", "/api/v1/agent/chunk-pass", {"mac": UNREG, "session_id": "s1", "part": 3, "pass": 1,
                                          "state": "listening"}),
    ("POST", "/api/v1/agent/pulls", {"mac": UNREG, "image_id": "img_1"}),
    ("POST", "/api/v1/agent/login", {"username": "noc", "password": "x"}),
    ("POST", "/api/console/login", {"username": "noc", "password": "x"}),   # הקיוסק של מחשב הבנייה
    ("GET", "/api/v1/health", None),
]


@pytest.mark.parametrize("method,path,body", CLOSED, ids=[p for _m, p, _b in CLOSED])
def test_an_unregistered_machine_on_the_deploy_network_reaches_no_cloning_service(env, method, path, body):
    r = env["client"](UNREG_IP).request(method, path, json=body)
    assert r.status_code == 403, (path, r.status_code, r.text)
    assert r.json()["code"] == deploy_gate.EVENT


def test_an_unregistered_machine_still_gets_its_way_to_the_local_disk_and_the_unregistered_screen(env):
    """עיקרון 1 ו-#1802: תפריט האתחול ו-hello פתוחים, והתשובה היא "לא רשום"."""
    c = env["client"](UNREG_IP)
    hello = c.post("/api/v1/agent/hello", json=hello_body(UNREG))
    assert hello.status_code == 200, hello.text
    assert hello.json()["known"] is False and hello.json().get("session") is None
    menu = c.get(f"/boot/menu?mac={UNREG}")
    assert menu.status_code == 200 and deploy_gate.EVENT not in menu.text, menu.text


def test_a_registered_machine_passes(env):
    c = env["client"](REG_IP)
    assert c.get("/api/v1/agent/sessions/active").status_code == 200
    assert c.post("/api/v1/agent/hello", json=hello_body(REG)).json()["known"] is True


def test_a_machine_registered_now_passes_at_once_and_one_removed_is_closed_at_once(env):
    """בלי אתחול של השרת: השער קורא את טבלת המכונות בכל בקשה."""
    c = env["client"](UNREG_IP)
    assert c.get("/api/v1/agent/sessions/active").status_code == 403
    registry.add_machine(env["conn"], UNREG, "cloner-2", CLONERS, "test")
    assert c.get("/api/v1/agent/sessions/active").status_code == 200
    with writing(env["conn"]):
        assert update_one(env["conn"], "DELETE FROM machines WHERE mac = ?", (UNREG,))
    assert c.get("/api/v1/agent/sessions/active").status_code == 403


def test_an_address_with_no_lease_is_refused_as_unverifiable_not_waved_through(env):
    r = env["client"](NOLEASE_IP).get("/api/v1/agent/sessions/active")
    assert r.status_code == 403 and r.json()["code"] == "identity_unverifiable"


def test_a_leases_file_that_cannot_be_read_closes_the_gate(env):
    """עיקרון 5: "לא הצלחנו לבדוק" אינו "רשום"."""
    env["leases"].unlink()
    r = env["client"](REG_IP).get("/api/v1/agent/sessions/active")
    assert r.status_code == 403 and r.json()["code"] == "identity_unverifiable"


def test_the_identity_switch_off_is_the_operators_open_choice_as_for_every_identity_check(env):
    """DHCP חיצוני: אין חכירה שאפשר לגזור ממנה MAC — כמו `identity_gate`, בגלוי במסך הבריאות."""
    set_setting(env["conn"], "identity_check", "false")
    assert env["client"](UNREG_IP).get("/api/v1/agent/sessions/active").status_code == 200


def test_the_management_side_is_not_touched(env, images_root):
    """הבהרת נדב 14/11: בוילן הניהול ההגנה היא חומת האש וההזדהות, לא סינון לפי מחשב.
    בקשה שהגיעה על כתובת אחרת של השרת אינה נבדקת, והקונסולה (8081) אינה עוברת בשער."""
    # main: `/sessions/active` is behind `source_gate` (#2489) everywhere, so the probe is `/state`:
    # its own identity check passes (the lease is this address), and only the gate would refuse it.
    assert env["client"](UNREG_IP, local=MGMT).get(f"/api/v1/agent/state?mac={UNREG}").status_code == 200
    rt = env["rt"]
    users.create(rt.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    console = TestClient(create_console_app(rt), base_url=f"http://{VLAN}:8081", client=(UNREG_IP, 40001))
    assert console.post("/api/console/login",
                        json={"username": "noc", "password": "admin-pass-123"}).status_code == 200


def test_one_journal_line_per_address_in_a_window_and_a_flood_is_counted_not_written(env, tmp_path):
    c = env["client"](UNREG_IP)
    for _ in range(50):
        assert c.get("/api/v1/agent/sessions/active").status_code == 403
    lines = _journal(env["conn"], deploy_gate.EVENT)
    assert len(lines) == 1 and UNREG in lines[0] and UNREG_IP in lines[0], lines
    # ארבעים כתובות שונות, כל אחת חכורה למחשב לא רשום: 32 נרשמות (עם הראשונה), השאר נספרות
    pairs = {REG: REG_IP, UNREG: UNREG_IP,
             **{f"02:00:00:00:01:{i:02x}": f"10.44.13.{i + 1}" for i in range(40)}}
    write_leases(env["leases"], pairs)
    for i in range(40):
        env["client"](f"10.44.13.{i + 1}").get("/api/v1/agent/sessions/active")
    assert len(_journal(env["conn"], deploy_gate.EVENT)) == 32
    assert any(deploy_gate.EVENT in d for d in _journal(env["conn"], "journal_flood"))


def test_a_control_character_in_the_path_does_not_split_the_journal_line(env):
    env["client"](UNREG_IP).get("/api/v1/agent/x%0afake")
    (line,) = _journal(env["conn"], deploy_gate.EVENT)
    assert "\n" not in line


def test_every_new_event_has_a_hebrew_label():
    from server.console import journal_he
    for event in (deploy_gate.EVENT, "known_macs_firewall_failed"):
        assert event in journal_he.EVENTS_HE, event


# --- חומת האש: known_macs -------------------------------------------------------------


def test_the_firewall_list_is_loaded_at_start_and_on_every_registration(env):
    assert env["fw"][0] == []                          # עליית השרת (create_runtime): הטבלה עוד ריקה
    rt = env["rt"]
    users.create(rt.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    admin = TestClient(create_console_app(rt))
    assert admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"}).status_code == 200
    r = admin.post("/api/console/machines", json={"mac": UNREG, "name": "cloner-2", "group_id": CLONERS})
    assert r.status_code == 200, r.text
    assert env["fw"][-1] == sorted([REG, UNREG])
    assert r.json()["network"] == {"applied": True, "error": None}


def test_a_firewall_list_that_did_not_load_is_said_in_the_answer_and_the_journal(tmp_path, images_root):
    from test_sender import Recorder                       # noqa: PLC0415

    rt = create_runtime(tmp_path / "data", images_root, f"http://{VLAN}:8080",
                        sender_runner=Recorder(block=True), wol_send=lambda _p: None,
                        known_macs_hooks={"apply": lambda text: None, "firewall": lambda macs: "nft said no"})
    try:
        assert _journal(rt.conn, "known_macs_firewall_failed") == ["nft said no"]
        users.create(rt.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
        admin = TestClient(create_console_app(rt))
        admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
        r = admin.post("/api/console/machines", json={"mac": UNREG, "name": "cloner-2", "group_id": CLONERS})
        assert r.status_code == 200 and r.json()["network"] == {"applied": False, "error": "nft said no"}
    finally:
        rt.ctx.sender.stop()


def test_the_list_file_empties_the_set_and_names_every_registered_mac():
    text = known_fw.render([REG, UNREG])
    assert "flush set inet imagesrv known_macs\n" in text
    assert f"add element inet imagesrv known_macs {{ {REG}, {UNREG} }}\n" in text
    empty = known_fw.render([])
    assert "flush set inet imagesrv known_macs" in empty and "add element" not in empty


@pytest.mark.parametrize("bad", ["B4:2E:99:07:1A:C4", "b4:2e:99:07:1a:c4\nflush ruleset", "x"])
def test_a_mac_that_is_not_canonical_never_reaches_nft(tmp_path, bad):
    calls = []
    error = known_fw.apply([bad], tmp_path / "k.nft", run=lambda *a, **k: calls.append(a))
    assert error and calls == [] and not (tmp_path / "k.nft").exists()


def test_apply_writes_then_loads_and_a_refusal_is_said_by_name(tmp_path):
    seen = []

    def ok(cmd, **_kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    assert known_fw.apply([REG], tmp_path / "nft.d" / "k.nft", run=ok) is None
    assert seen == [["nft", "-f", str(tmp_path / "nft.d" / "k.nft")]]
    assert REG in (tmp_path / "nft.d" / "k.nft").read_text(encoding="utf-8")

    def no(cmd, **_kw):
        return subprocess.CompletedProcess(cmd, 1, "", "Error: No such file or directory")

    assert "nft rc=1" in known_fw.apply([REG], tmp_path / "k.nft", run=no)

    def missing(cmd, **_kw):
        raise FileNotFoundError("nft")

    assert "nft לא רץ" in known_fw.apply([REG], tmp_path / "k.nft", run=missing)


# --- המחולל של חומת האש ------------------------------------------------------------------



def _rules(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


@needs_bash
@pytest.mark.parametrize("args,nic", [
    (("--deploy-if", "eth-deploy", "--servers-if", "eth-srv", "--mcast-ports", "2232-2233"), "eth-deploy"),
    (("--per-nic", "--mcast-ports", "2232-2233", "--service", "ens34:multicast,netboot",
      "--service", "ens35:console"), "ens34"),
])
def test_udpcast_feedback_enters_only_from_a_registered_mac_and_the_rest_is_logged_and_dropped(args, nic):
    got = _rules(generate(*args))
    port = f'iifname "{nic}" udp dport 2232-2233'
    mine = [r for r in got if r.startswith(port)]
    assert mine == [f"{port} ether saddr @known_macs accept",
                    f'{port} limit rate 5/minute log prefix "imagesrv-unregistered " level info',
                    f"{port} drop"], mine
    text = generate(*args)
    assert "set known_macs {" in text and "type ether_addr" in text
    # ה-include אחרי סגירת הטבלה: כל טעינה של הקובץ ממלאת את הקבוצה מחדש
    assert text.rstrip().endswith('include "/etc/imagesrv/nft.d/*.nft"')
    assert known_fw.INCLUDE_GLOB in text and Path(known_fw.CONF).parent.as_posix() + "/*.nft" == known_fw.INCLUDE_GLOB


@needs_bash
def test_dhcp_tftp_the_agent_port_and_the_console_are_as_they_were():
    """מחשב לא רשום עדיין מקבל את הדרך לדיסק / למסך "לא רשום" (#1802), והקונסולה בוילן
    הניהול פתוחה כמו קודם (הבהרת נדב 14/11)."""
    got = _rules(generate("--deploy-if", "eth-deploy", "--servers-if", "eth-srv", "--mcast-ports", "2232-2233"))
    for rule in ('iifname "eth-deploy" udp dport 67 accept', 'iifname "eth-deploy" udp dport 69 accept',
                 'iifname "eth-deploy" tcp dport 8080 accept', 'iifname "eth-srv" tcp dport 8081 accept'):
        assert rule in got, rule


# --- /agent/state: גם בלי השער ---------------------------------------------------------------


def test_the_state_of_an_unregistered_machine_names_no_image_even_where_the_gate_does_not_stand(env, images_root):
    """בקשה על כתובת אחרת של השרת (השער אינו עומד שם): המחשב הלא רשום מקבל `known: false`,
    בלי `allowed_images` ובלי `unchecked_images`. מחשב רשום עם אותם דיסקים — מקבל אותם."""
    # `images_root` (conftest) already holds MANIFEST_256, which fits the disk of `hello_body`.
    for mac, ip in ((UNREG, UNREG_IP), (REG, REG_IP)):
        c = env["client"](ip, local=VLAN)
        assert c.post("/api/v1/agent/hello", json=hello_body(mac)).status_code == 200
    stranger = env["client"](UNREG_IP, local=MGMT).get(f"/api/v1/agent/state?mac={UNREG}").json()
    assert stranger["known"] is False and stranger["allowed_images"] == [] and stranger["unchecked_images"] == []
    own = env["client"](REG_IP, local=MGMT).get(f"/api/v1/agent/state?mac={REG}").json()
    assert own["known"] is True and own["allowed_images"], own
