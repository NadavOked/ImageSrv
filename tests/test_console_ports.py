"""מסך "פורטים" בקונסולה (#822) — הרשימה מגיעה מהשרת, לא defs קבוע ב-JS.

אותו דפוס hooks מוזרק כמו test_server_health.py (health_server), כך
שאף בדיקה כאן לא נוגעת ב-ss/systemctl אמיתיים.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

try:
    from fastapi.testclient import TestClient
except ImportError:                                   # pragma: no cover
    TestClient = None

SS_DNSMASQ = """State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process
UNCONN 0      0            0.0.0.0:67        0.0.0.0:*     users:(("dnsmasq",pid=612,fd=4))
UNCONN 0      0            0.0.0.0:69        0.0.0.0:*     users:(("dnsmasq",pid=612,fd=6))
"""


@pytest.fixture()
def ports_server(tmp_path: Path, images_root: Path, clock):
    """כמו health_server, אבל בלי דרישת קבצי אתחול — מסך הפורטים אינו
    בודק boot_files/shim, ואין להם בכלל hooks כאן."""
    if TestClient is None:
        pytest.skip("fastapi is required")
    from server import monitor, users
    from server.app import create_app
    from server.db import set_setting

    tftp = tmp_path / "tftp"
    tftp.mkdir(parents=True)

    fake = {"ss": SS_DNSMASQ, "http": 200,
            "interfaces": [{"name": "eth0", "state": "up", "mac": "aa", "addresses": []}],
            "udp_sender_pids": [],
            # ‏#996: טבלת ה-TCP "לא נקראה" כאן — כמו בתחנה בלי ss. בלי
            # ההזרקה הבדיקה הייתה מריצה ss אמיתי (ובמעבדה — רואה מאזינים
            # אמיתיים). המתג עצמו נבדק ב-test_port_toggles.py.
            "ss_tcp": "",
            "nft_ruleset": (
                "table inet imagectl {\n"
                "\tchain input {\n"
                "\t\tiifname \"lo\" accept\n"
                "\t}\n"
                "}\n")}
    hooks = {
        "ss": lambda: fake["ss"],
        "ss_tcp": lambda: fake["ss_tcp"],
        "nft_ruleset": lambda: fake["nft_ruleset"],
        "unit_active": lambda name: "active",
        "http_get": lambda url: fake["http"],
        "http_size": lambda url: (200, 31_000_000),
        "interfaces": lambda: fake["interfaces"],
        "tftp_root": lambda: tftp,
        "udp_sender_pids": lambda: fake["udp_sender_pids"],
    }
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     now_fn=clock, health_hooks=hooks)
    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    users.create(app.state.ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    admin, deploy = TestClient(app), TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    deploy.post("/api/console/login",
                json={"username": "labtech", "password": "deploy-pass-1"})
    anon = TestClient(app)
    return {"admin": admin, "deploy": deploy, "anon": anon, "fake": fake,
           "ctx": app.state.ctx, "monitor": monitor, "set_setting": set_setting}


def by_id(rows):
    return {r["id"]: r for r in rows}


def test_ports_endpoint_is_console_only(ports_server):
    """‏#1073: הדף הוא קונסולה — deploy מקבל 403 `deploy_no_console` (לא
    admin_only: הנתיב עצמו נשאר current_user, והסירוב הוא של הקונסולה)."""
    resp = ports_server["deploy"].get("/api/console/ports")
    assert resp.status_code == 403 and "מחשב הבנייה" in resp.json()["detail"]
    resp = ports_server["admin"].get("/api/console/ports")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_ports_endpoint_requires_login(ports_server):
    assert ports_server["anon"].get("/api/console/ports").status_code == 401


def test_the_list_includes_5900_and_no_8082_and_no_ssh(ports_server):
    """זה בדיוק מה שהעניין #822 מדווח: 5900 חסר ברשימה הקבועה הישנה.
    ‏#1247: 8082 הוסר ואינו ברשימה. ‏#1266: SSH יצא מהמוצר — אין שורת 22,
    לא לתחנות ולא לשרת × כרטיס."""
    rows = by_id(ports_server["admin"].get("/api/console/ports").json())
    assert rows["monitor"]["port"] == "5900"
    assert "kiosk" not in rows and not any(r["port"] == "8082" for r in rows.values())
    assert not any(r["port"] == "22" or r["id"].startswith("ssh") for r in rows.values()), rows.keys()
    # וגם הקיימים מקודם
    assert rows["tftp"]["port"] == "69"
    assert rows["http_boot"]["port"] == "8080"
    assert rows["multicast"]["port"] == "9000–9001"  # פורט-הפצה-מכוון
    assert rows["ntp"]["port"] == "123" and rows["ntp"]["proto"] == "udp"
    assert rows["ntp"]["name"] == "שרת זמן (NTP)"
    assert rows["ntp"]["off_means"] == "השרת לא מספק שעה"
    assert rows["ntp"]["enabled"] is False and rows["ntp"]["listening"] is False
    assert rows["ntp"]["network"] == "per_nic"


def test_monitor_switch_off_shows_off_not_green(ports_server):
    """המתג כבוי כברירת מחדל — השורה חייבת לומר את זה, לא "לא אומת"
    ולא ירוק."""
    rows = by_id(ports_server["admin"].get("/api/console/ports").json())
    assert rows["monitor"]["state"] == "off"
    assert "כבוי" in rows["monitor"]["detail"]


def test_monitor_switch_on_shows_a_warning_not_green(ports_server):
    """הדלקה היא סיכון מוצהר (RFB על כל תחנה) — warn ולא ok, באותו
    היגיון כמו כל דלת שנפתחת לכל התחנות."""
    ports_server["set_setting"](ports_server["ctx"].conn,
                                ports_server["monitor"].STATION_KEY,
                                ports_server["monitor"].flag_json(True))
    rows = by_id(ports_server["admin"].get("/api/console/ports").json())
    assert rows["monitor"]["state"] == "warn"
    assert "דלוק" in rows["monitor"]["detail"]


def test_unverified_ports_say_so_and_are_not_painted_green(ports_server):
    """8081: טבלת הסוקטים של TCP לא נקראה — "לא נקרא" הוא `unknown`
    (אדום — דלת שאי אפשר לראות), ולעולם לא ok (עיקרון 5). ‏#996
    החליף את "אין hook" הישן בקריאה אמיתית מ-`ss -ltnp`; ‏4011 נקרא
    מטבלת ה-UDP, שכאן קיימת ואין בה מאזין — ובלי proxy דלוק זה off."""
    rows = by_id(ports_server["admin"].get("/api/console/ports").json())
    for pid in ("http_console",):
        assert rows[pid]["state"] == "unknown"
        assert rows[pid]["listening"] is None
        assert rows[pid]["state"] != "ok"


def test_pxe_proxy_4011_is_not_a_v1_row(ports_server):
    """‏#1303 (נדב 12/10): ‏PXE proxy שייך לכיתות (v1.2) — ‏`PUT /net/interfaces`
    כבר מסרב לו ב-409 כשהכיתות כבויות. שורת 4011 בדף הפורטים הייתה שארית
    מוצגת של יכולת שאין ב-v1, ולכן היא לא נשלחת."""
    rows = by_id(ports_server["admin"].get("/api/console/ports").json())
    assert "pxe_proxy" not in rows
    assert all(r["port"] != "4011" for r in rows.values())


def test_pxe_proxy_4011_row_is_back_with_classrooms(ports_server, classrooms_on):
    """v1.2: עם כיתות השורה חוזרת — נקראת מטבלת ה-UDP, ובלי proxy דלוק זה off, לא ok."""
    rows = by_id(ports_server["admin"].get("/api/console/ports").json())
    assert rows["pxe_proxy"]["state"] == "off"
    assert rows["pxe_proxy"]["listening"] is False


def test_a_stranger_on_tftp_is_red(ports_server):
    ports_server["fake"]["ss"] = (
        "State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
        'UNCONN 0      0            0.0.0.0:69        0.0.0.0:*     '
        'users:(("evil",pid=1,fd=1))\n')
    rows = by_id(ports_server["admin"].get("/api/console/ports").json())
    assert rows["tftp"]["state"] == "warn"
    assert "evil" in rows["tftp"]["detail"]


def test_every_row_carries_a_firewall_note(ports_server):
    """"הסבר קצר לכל שורה" — בקשת נדב, לא רק state/label."""
    rows = ports_server["admin"].get("/api/console/ports").json()
    assert rows
    for row in rows:
        assert row["note"], f"{row['id']} is missing a note"


def test_ports_list_includes_firewall_row(ports_server):
    rows = by_id(ports_server["admin"].get("/api/console/ports").json())
    fw = rows["firewall"]
    assert fw["name"] == "חומת אש"
    assert fw["toggle"] == "none"
    assert fw["state"] == "ok"
    assert "פעילה" in fw["detail"]
    assert fw["enabled"] is None


def test_firewall_row_three_states(ports_server):
    rows = by_id(ports_server["admin"].get("/api/console/ports").json())
    assert rows["firewall"]["state"] == "ok"

    ports_server["fake"]["nft_ruleset"] = ""
    rows = by_id(ports_server["admin"].get("/api/console/ports").json())
    assert rows["firewall"]["state"] == "off"
    assert rows["firewall"]["detail"] == "לא נטענה"

    ports_server["fake"]["nft_ruleset"] = None
    rows = by_id(ports_server["admin"].get("/api/console/ports").json())
    assert rows["firewall"]["state"] == "unknown"
    assert rows["firewall"]["detail"] == "לא הצלחנו לבדוק"


# --- #1267: שיוך לרשת + חומת אש בשרת לכל פורט שבין רשתות -----------------------

NFT_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "nftables"
DEPLOY_IDS = ("dhcp", "tftp", "http_boot", "multicast", "monitor")   # ‏#1303: ‏pxe_proxy — רק עם כיתות (v1.2)


def _nft(name: str) -> str:
    return (NFT_FIXTURES / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_every_port_row_names_its_network(ports_server):
    """#1267: הקונסולה מפצלת לפי רשת — והשיוך בא מהשרת, לא מרשימה ב-JS.
    ‏`firewall` (מצב ה-nft כולו) אינו פורט ואינו שייך לרשת."""
    rows = by_id(ports_server["admin"].get("/api/console/ports").json())
    for pid in DEPLOY_IDS:
        assert rows[pid]["network"] == "deploy", pid
    assert rows["http_console"]["network"] == "mgmt"
    assert rows["interserver"]["network"] == "interserver"
    assert rows["firewall"]["network"] is None
    assert {r["network"] for r in rows.values()} <= {"deploy", "mgmt", "interserver", "per_nic", None}


def test_cross_network_ports_read_the_server_firewall_per_port(ports_server):
    """ראשי (primary.conf): 8081 פתוח על וילן השרתים, וגם 8443 — ‏#1310,
    "אתה שם?" של מתקין משני. הראיה היא הכלל עצמו, לא היעדר שגיאה."""
    ports_server["fake"]["nft_ruleset"] = _nft("primary.conf")
    rows = by_id(ports_server["admin"].get("/api/console/ports").json())
    assert rows["http_console"]["firewall"] == "open"
    assert "tcp dport 8081 accept" in rows["http_console"]["firewall_detail"]
    assert rows["interserver"]["firewall"] == "open"
    assert "tcp dport 8443 accept" in rows["interserver"]["firewall_detail"]

    # משני: 8443 נפתח רק מכתובת הראשי — והכלל (עם הכתובת) הוא ההסבר.
    ports_server["fake"]["nft_ruleset"] = _nft("secondary.conf")
    rows = by_id(ports_server["admin"].get("/api/console/ports").json())
    assert rows["interserver"]["firewall"] == "open"
    assert "ip saddr" in rows["interserver"]["firewall_detail"]


def test_deploy_ports_carry_no_firewall_verdict(ports_server):
    """ברשת ההפצה אין חומת אש של המכללה — השרת מנהל אותה (נדב 10/10).
    לכן אין שם עמודה, והשרת אינו ממציא ערך."""
    ports_server["fake"]["nft_ruleset"] = _nft("primary.conf")
    rows = by_id(ports_server["admin"].get("/api/console/ports").json())
    for pid in DEPLOY_IDS:
        assert rows[pid]["firewall"] is None, pid


def test_firewall_that_could_not_be_read_is_not_checked_never_open(ports_server):
    """עיקרון 5: nft שלא נקרא, טבלה שלא נטענה, או טבלה בלי policy ידוע —
    ‏`None` ("לא נבדק"), לעולם לא "open"."""
    for ruleset, phrase in ((None, "לא נבדק"), ("", "לא נטענה"),
                            ("table inet imagectl {\n\tchain input {\n\t}\n}\n", "לא ידוע")):
        ports_server["fake"]["nft_ruleset"] = ruleset
        rows = by_id(ports_server["admin"].get("/api/console/ports").json())
        for pid in ("http_console", "interserver"):
            assert rows[pid]["firewall"] is None, (pid, ruleset)
            assert phrase in rows[pid]["firewall_detail"], (pid, ruleset)


def test_port_firewall_ignores_comments_drops_and_longer_port_numbers():
    from server.ports import port_firewall
    body = ("table inet imagectl {\n\tchain input {\n"
            "\t\ttype filter hook input priority filter; policy drop;\n"
            "\t\t# tcp dport 8081 accept — הערה, לא כלל\n"
            "\t\tiifname \"x\" tcp dport 80810 accept\n"
            "\t\tiifname \"x\" tcp dport 8443 drop\n"
            "\t\tiifname \"x\" udp dport 8081 accept\n"
            "\t}\n}\n")
    assert port_firewall(body, "tcp", "8081")[0] == "blocked"
    assert port_firewall(body, "tcp", "8443")[0] == "blocked"
    assert port_firewall(body, "udp", "8081")[0] == "open"
    accept_all = body.replace("policy drop", "policy accept")
    assert port_firewall(accept_all, "tcp", "8081")[0] == "open"
    # טווח/קבוצה שלא פוענחו אינם ראיה ל"חסום" — לא נבדק
    ranged = body.replace("80810", "8000-8100")
    assert port_firewall(ranged, "tcp", "8081") == (
        None, "יש בטבלת imagectl כלל טווח/קבוצה שלא פוענח — לא נבדק")
    # ‏כללי udp של ההפצה אינם נוגעים לשאלה על tcp (‏#1310: בראשי 8443 פתוח)
    assert port_firewall(_nft("primary.conf"), "tcp", "8443")[0] == "open"
    assert port_firewall(_nft("primary.conf"), "tcp", "4444")[0] == "blocked"
