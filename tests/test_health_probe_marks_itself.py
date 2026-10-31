"""הבדיקה העצמית של השרת אינה נרשמת כמכונה (‏#976).

‏`/api/console/health` (וגם `/ports`) מושכים בחזרה את
‏`GET /boot/menu?mac=<MAC רשום>` ו-`?mac=00:00:00:00:00:00` — זו הראיה
החיובית שהשרת מגיש תפריט בכתובת ההפצה ומה כתוב בשורת הקרנל. עד 17/09
השרת לא הבחין בין הבדיקה של עצמו לבין GRUB של מכונה: כל כניסה לקונסולה
דרסה את ‏IP/נראה-לאחרונה/שביל-הפירורים של מכונה אמיתית ל-`127.0.0.1 ·
1/9`, יצרה התקן "לא רשום" `00:00:00:00:00:00` — **וגם ספרה אתחול**
בתקציב לולאת האתחול (‏#75) של המכונה שנבדקה.

התיקון: הבדיקה מסמנת את עצמה בכותרת `X-ImageSrv-Probe: 1`, והשרת מכבד
את הסימון **רק** כשהפונה הוא השרת עצמו — ‏loopback, או הכתובת המקומית
שעליה החיבור התקבל (חיבור של השרת לכתובת ההפצה של עצמו יוצא ממנה,
לא מ-`127.0.0.1`). ‏GRUB אמיתי לעולם אינו מגיע משם, ומבחוץ הכותרת
אינה שווה דבר — ולכן היא אינה דרך לאתחל בלי להירשם.

הבדיקות כאן מדברות עם השרת דרך HTTP בדיוק כמו הבדיקה העצמית: ה-hooks
של הבריאות מוזרקים, אבל במקום ערכים קבועים הם קוראים את **אותה
אפליקציה** בחזרה, מכתובת לקוח נשלטת.
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from conftest import hello_body, setup_classroom            # noqa: E402

from server.agent.bootguard import ATTEMPT_LIMIT                   # noqa: E402

try:
    from fastapi.testclient import TestClient
except ImportError:                                          # pragma: no cover
    TestClient = None

BASE = "http://10.44.12.10:8080"
ZERO_MAC = "00:00:00:00:00:00"
STATION_IP = "10.44.12.59"
PROBE = {"X-ImageSrv-Probe": "1"}


@pytest.fixture()
def probing_server(tmp_path: Path, images_root: Path, clock):
    """שרת מלא שהבדיקה העצמית שלו באמת מושכת את `/boot/menu` — דרך
    ‏TestClient שמגיע מ-loopback, בדיוק כמו `urllib` על השרת."""
    if TestClient is None:
        pytest.skip("fastapi is required")
    from test_sender import Recorder                           # noqa: PLC0415

    from server.console import users                                   # noqa: PLC0415
    from server.app import create_app                          # noqa: PLC0415

    tftp = tmp_path / "tftp"
    (tftp / "grub").mkdir(parents=True)
    for name in ("bootx64.efi", "grubx64.efi", "grub/grub.cfg"):
        (tftp / name).write_bytes(b"x")

    holder: dict = {}

    def http_get(url: str) -> int | None:
        return holder["self"].get(url, headers=PROBE).status_code

    hooks = {
        "ss": lambda: "",
        "ss_tcp": lambda: "",      # ‏#996: /ports קורא גם את טבלת ה-TCP
        "unit_active": lambda name: "active",
        "http_get": http_get,
        "http_size": lambda url: (200, 31_000_000),
        "interfaces": lambda: [],
        "tftp_root": lambda: tftp,
        "udp_sender_pids": lambda: [],
    }
    app = create_app(tmp_path / "data", images_root, BASE,
                     now_fn=clock, health_hooks=hooks,
                     sender_runner=Recorder(block=True))
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    users.create(ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    admin, deploy = TestClient(app), TestClient(app)
    assert admin.post("/api/console/login", json={
        "username": "noc", "password": "admin-pass-123"}).status_code == 200
    assert deploy.post("/api/console/login", json={
        "username": "labtech", "password": "deploy-pass-1"}).status_code == 200
    # הבדיקה העצמית: השרת פונה לעצמו. בשרת פיתוח זה `127.0.0.1`.
    holder["self"] = TestClient(app, client=("127.0.0.1", 40000))
    bundle = {
        "app": app, "ctx": ctx, "admin": admin, "deploy": deploy,
        # GRUB של תחנה אמיתית — מכתובת בווילן ההפצה, בלי שום כותרת.
        "station": TestClient(app, client=(STATION_IP, 40001)),
    }
    yield bundle
    ctx.sender.stop()


def devices(server) -> dict:
    return {d["mac"]: d for d in server["admin"].get("/api/console/net").json()}


def station_boots_and_reports(server, mac: str) -> dict:
    """מכונה אמיתית: תפריט, שביל פירורים עד 9/9, ואז hello — ומחזיר את
    השורה שלה ב-`/net` כפי שהמפעיל רואה אותה."""
    station = server["station"]
    assert station.get(f"{BASE}/boot/menu?mac={mac}").status_code == 200
    assert station.get(f"{BASE}/boot/step?mac={mac}&s=agent-hello").status_code == 200
    body = hello_body(mac)
    body["ip"] = STATION_IP
    assert station.post(f"{BASE}/api/v1/agent/hello", json=body).status_code == 200
    row = devices(server)[mac]
    assert row["ip"] == STATION_IP and row["boot"]["index"] == 9
    return row


def evidence(row: dict) -> tuple:
    # ‏`seconds`/`stalled` בפירורי האתחול נגזרים משעון הקיר בזמן הקריאה —
    # שנייה שמתחלפת בין "לפני" ל"אחרי" אינה "השרת כתב" (נפל פעם אחת בשער
    # המעבדה, 17/09). הראיה היא הצעד, האינדקס והחותמת — לא הגיל.
    def strip(step):
        return {k: v for k, v in step.items() if k not in ("seconds", "stalled")}
    boot = row["boot"]
    if isinstance(boot, list):
        boot = [strip(step) for step in boot]
    elif isinstance(boot, dict):     # צעד יחיד — אותו גיל, אותה סיבה (נפל שוב 19/09)
        boot = strip(boot)
    return row["ip"], row["last_seen"], boot


# --- הבאג עצמו, דרך /api/console/health ------------------------------------


def test_health_does_not_invent_the_all_zero_device(probing_server):
    setup_classroom(probing_server)
    rows = probing_server["admin"].get("/api/console/health").json()
    assert {r["id"]: r["state"] for r in rows}["server"] == "ok"   # הבדיקה רצה
    assert ZERO_MAC not in devices(probing_server)


def test_health_does_not_journal_the_all_zero_probe(probing_server):
    setup_classroom(probing_server)
    rows = probing_server["admin"].get("/api/console/health").json()
    assert {r["id"]: r["state"] for r in rows}["server"] == "ok"
    events = [row["event"] for row in
              probing_server["admin"].get("/api/console/journal").json()]
    assert "unknown_mac" not in events


def test_an_all_zero_mac_is_never_recorded_or_journaled(probing_server):
    setup_classroom(probing_server)
    assert probing_server["station"].get(
        f"{BASE}/boot/menu?mac={ZERO_MAC}").status_code == 200
    events = [row["event"] for row in
              probing_server["admin"].get("/api/console/journal").json()]
    assert "unknown_mac" not in events
    assert ZERO_MAC not in devices(probing_server)


def test_a_menu_fetch_from_the_servers_own_interface_ip_is_not_a_machine(probing_server):
    local = TestClient(probing_server["app"], client=("10.44.12.10", 40002))
    mac = "02:11:22:33:44:55"
    assert local.get(f"{BASE}/boot/menu?mac={mac}").status_code == 200
    assert mac not in devices(probing_server)


def test_a_real_unicast_mac_is_still_recorded(probing_server):
    mac = "02:11:22:33:44:55"
    assert probing_server["station"].get(f"{BASE}/boot/menu?mac={mac}").status_code == 200
    assert devices(probing_server)[mac]["ip"] == STATION_IP


def test_startup_removes_the_existing_all_zero_device(tmp_path):
    from server.db import connect, net_seen
    path = tmp_path / "imagesrv.db"
    conn = connect(path)
    net_seen(conn, ZERO_MAC, "10.44.0.1")
    conn.close()
    conn = connect(path)
    try:
        assert conn.execute("SELECT 1 FROM net_devices WHERE mac = ?", (ZERO_MAC,)).fetchone() is None
    finally:
        conn.close()


def test_health_leaves_the_probed_machine_evidence_alone(probing_server):
    """בדיקת הבריאות מושכת תפריט אתחול (עד #1266 — גם עבור המכונה הרשומה
    הראשונה, בשביל חיווי ה-SSH) — וה-IP, "נראה לאחרונה" ושביל הפירורים
    של המכונה נשארים מה שהיא עצמה דיווחה."""
    ids = setup_classroom(probing_server)
    before = evidence(station_boots_and_reports(probing_server, ids["mac1"]))

    rows = {r["id"]: r for r in
            probing_server["admin"].get("/api/console/health").json()}
    assert rows["server"]["state"] == "ok"   # התפריט באמת נמשך ונקרא

    assert evidence(devices(probing_server)[ids["mac1"]]) == before


def test_the_ports_screen_does_not_mark_either(probing_server):
    """אותה בדיקה עצמית רצה גם מ-`/ports`."""
    ids = setup_classroom(probing_server)
    before = evidence(station_boots_and_reports(probing_server, ids["mac1"]))
    admin = probing_server["admin"]
    assert admin.get("/api/console/ports").status_code == 200
    seen = devices(probing_server)
    assert ZERO_MAC not in seen
    assert evidence(seen[ids["mac1"]]) == before


def test_health_probes_do_not_spend_the_boot_budget(probing_server):
    """‏#75 סופר בקשות תפריט לסבב פתוח. ארבע כניסות לקונסולה במהלך סבב
    היו שולחות את המכונה הרשומה הראשונה לדיסק המקומי — בלי שאתחלה."""
    ids = setup_classroom(probing_server)
    response = probing_server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 2})
    assert response.status_code == 200
    for _ in range(ATTEMPT_LIMIT + 1):
        assert probing_server["admin"].get("/api/console/health").status_code == 200
    text = probing_server["station"].get(f"{BASE}/boot/menu?mac={ids['mac1']}").text
    assert "set default=imagesrv" in text
    assert "boot-loop-guard" not in text


# --- הסימון אינו דלת אחורית ------------------------------------------------


def test_a_marked_request_from_outside_is_recorded_like_any_boot(probing_server):
    """הכותרת מכובדת רק מהשרת עצמו. מכונה בווילן ששולחת אותה נרשמת
    בדיוק כמו GRUB — אחרת זו דרך לאתחל בלי להשאיר עקבות."""
    ids = setup_classroom(probing_server)
    outsider = TestClient(probing_server["app"], client=("10.44.12.77", 40002))
    assert outsider.get(f"{BASE}/boot/menu?mac={ids['mac2']}",
                        headers=PROBE).status_code == 200
    row = devices(probing_server)[ids["mac2"]]
    assert row["ip"] == "10.44.12.77"
    assert row["boot"]["step"] == "menu"


def test_the_servers_own_address_counts_as_itself(probing_server):
    """בייצור השרת פונה ל-`http://10.44.12.10:8080` — וחיבור מקומי
    לכתובת הזו יוצא **ממנה**, לא מ-`127.0.0.1`."""
    ids = setup_classroom(probing_server)
    before = evidence(station_boots_and_reports(probing_server, ids["mac1"]))
    itself = TestClient(probing_server["app"], client=("10.44.12.10", 40003))
    assert itself.get(f"{BASE}/boot/menu?mac={ids['mac1']}",
                      headers=PROBE).status_code == 200
    assert evidence(devices(probing_server)[ids["mac1"]]) == before


def test_loopback_without_the_mark_is_not_a_boot(probing_server):
    """‏#1178 הפך את הכלל הישן ("בקשה מ-loopback בלי הכותרת נרשמת"): משיכת
    תפריט מהשרת עצמו — curl מקומי, verify-boot-payload — אינה אתחול של
    תחנה, עם כותרת או בלעדיה, והיא אינה יוצרת מכונה ואינה רושמת צעד."""
    ids = setup_classroom(probing_server)
    bare = TestClient(probing_server["app"], client=("127.0.0.1", 40004))
    assert bare.get(f"{BASE}/boot/menu?mac={ids['mac2']}").status_code == 200
    assert ids["mac2"] not in devices(probing_server)


# --- ה-hooks האמיתיים שולחים את הסימון --------------------------------------


@pytest.mark.parametrize("hook", ["http_get", "http_size"])
def test_the_default_probes_carry_the_mark(monkeypatch, hook):
    """‏`default_hooks()` הם מה שרץ בייצור — ואם הם לא שולחים את
    הכותרת, כל מה שלמעלה מגן על שום דבר."""
    from server.system import health                                  # noqa: PLC0415

    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["request"] = request
        raise OSError("not a real server")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    health.default_hooks()[hook](BASE + "/boot/menu?mac=" + ZERO_MAC)
    request = captured["request"]
    assert isinstance(request, urllib.request.Request)
    assert request.get_header("X-imagesrv-probe") == "1"
