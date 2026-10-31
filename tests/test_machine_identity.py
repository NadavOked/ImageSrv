"""‏#855 — זהות מכונה: ‏MAC מוצהר **וגם** כתובת מקור שתואמת את חכירת ה-DHCP.

הסוכן ב-initramfs חסר-מצב ואין לו איפה להחזיק סוד; הראיה שיש היא
הרשת: השרת הוא ה-DHCP של וילן ההפצה (‏#702), ולכן לכל MAC שעלה ב-PXE
יש חכירה עם כתובת. hello/progress שמגיעים **מכתובת אחרת** אינם המכונה.

ארבעת הנתיבים של ה-Issue: ‏hello (מסירת `task.token`, חברות והבשלה של
סבב), ‏progress של סבב, ‏progress של קליטה (בנוסף לאסימון — ‏db67dbd).
ועיקרון 5 בשני מקומות: "אין חכירה" ו"אין קובץ" הם `identity_unverifiable`
(סירוב בשם), לא "תקין"; והמתג `identity_check` כבוי הוא מצב מוצהר
שהבריאות אומרת, לא ברירת מחדל שקטה.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

import server.app
import server.main
from conftest import hello_body, setup_classroom
from server.agent import identity
from server.core.tasks import TOKEN_HEADER
from test_capture import make_task, setup_build_machine
from tools.e2e import harness

STATION_IP = "10.44.12.50"
STRANGER_IP = "10.44.12.99"
MAC1 = "b4:2e:99:07:1a:c4"
MAC2 = "b4:2e:99:07:1a:c5"
BUILD = "aa:bb:cc:00:00:10"
IMAGE = "img_7f3a91"


def write_leases(path: Path, pairs: dict[str, str]) -> Path:
    """קובץ חכירות בתבנית dnsmasq: ‏`<פקיעה> <mac> <ip> <שם> <client-id>`."""
    lines = [f"1789000000 {mac} {ip} STN-{i} 01:{mac}"
             for i, (mac, ip) in enumerate(pairs.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _health_hooks(tmp_path: Path) -> dict:
    """הבדיקות המזויפות של מסך הבריאות (‏test_server_health) — כדי ש-/health
    יענה בלי לגעת ב-ss/systemctl של המכונה שהטסט רץ עליה."""
    tftp = tmp_path / "tftp"
    (tftp / "grub").mkdir(parents=True)
    for name in ("bootx64.efi", "grubx64.efi", "grub/grub.cfg"):
        (tftp / name).write_bytes(b"x")
    return {
        "ss": lambda: "", "unit_active": lambda name: "active",
        "http_get": lambda url: 200,
        "http_size": lambda url: (200, 9_000_000),
        "interfaces": lambda: [], "tftp_root": lambda: tftp,
        "udp_sender_pids": lambda: [],
    }


@pytest.fixture()
def identity_server(tmp_path: Path, images_root: Path, clock, monkeypatch):
    """כמו `server`, עם קובץ חכירות מוזרק: ‏MAC1 ו-BUILD חכורים ל-STATION_IP,
    ‏MAC2 בלי חכירה. שני לקוחות אנונימיים — מכתובת החכירה ומכתובת זרה."""
    from server.rounds import sender as sender_module
    from server.console import users
    from test_sender import Recorder

    monkeypatch.setattr(sender_module, "port_holders", lambda port: [])
    leases = write_leases(tmp_path / "dnsmasq.leases",
                          {MAC1: STATION_IP, BUILD: STATION_IP})
    app = server.app.create_app(
        tmp_path / "data", images_root, "http://10.44.12.10:8080",
        now_fn=clock, sender_runner=Recorder(block=True),
        health_hooks=_health_hooks(tmp_path),
        identity_hooks={"leases": identity.LeaseFile(leases)},
    )
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    users.create(ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    admin, deploy = TestClient(app), TestClient(app)
    assert admin.post("/api/console/login",
                      json={"username": "noc", "password": "admin-pass-123"}).status_code == 200
    assert deploy.post("/api/console/login",
                       json={"username": "labtech", "password": "deploy-pass-1"}).status_code == 200
    bundle = {
        "app": app, "ctx": ctx, "admin": admin, "deploy": deploy, "clock": clock,
        "leases": leases,
        # ‏`client=` של TestClient הוא ה-peer שהשרת רואה (`request.client.host`).
        "station": TestClient(app, client=(STATION_IP, 40001)),
        "stranger": TestClient(app, client=(STRANGER_IP, 40002)),
        # ‏`anon` — מה שהעוזרים המשותפים (make_task/do_capture) מצפים לו.
        "anon": TestClient(app, client=(STATION_IP, 40003)),
    }
    yield bundle
    ctx.sender.stop()


def hello(client: TestClient, mac: str):
    return client.post("/api/v1/agent/hello", json=hello_body(mac))


def journal_events(server, event: str) -> list[dict]:
    return server["admin"].get("/api/console/journal", params={"event": event}).json()


def journal_details(server, event: str) -> list[tuple[str, str]]:
    """‏(user, detail) גולמיים — המתרגם של הקונסולה מחליף MAC בשם המחשב,
    וכאן בודקים מה **נכתב**."""
    return [(r["user"], r["detail"]) for r in server["ctx"].conn.execute(
        "SELECT user, detail FROM journal WHERE event = ? ORDER BY id", (event,))]


def net_macs(server) -> set[str]:
    return {row["mac"] for row in server["admin"].get("/api/console/net").json()}


def open_round(server, expected: int) -> dict:
    ids = setup_classroom(server, expected)
    response = server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": IMAGE,
              "prefix": "LAB1", "expected_clients": expected})
    assert response.status_code == 200, response.text
    ids["session"] = response.json()["id"]
    return ids


def session_state(server, session_id: str) -> str:
    return server["ctx"].conn.execute(
        "SELECT state FROM sessions WHERE id = ?", (session_id,)).fetchone()["state"]


# --- הפרסר: קובץ dnsmasq.leases ----------------------------------------------


def test_parse_leases_reads_the_dnsmasq_format_and_normalises_the_mac():
    text = ("1789000000 B4:2E:99:07:1A:C4 10.44.12.50 STN-05 01:b4:2e:99:07:1a:c4\n"
            "1789000001 aa:bb:cc:00:00:10 10.44.12.51 * *\n")
    assert identity.parse_leases(text) == {MAC1: "10.44.12.50", BUILD: "10.44.12.51"}


def test_parse_leases_skips_what_is_not_an_ipv4_lease():
    """‏dnsmasq כותב לאותו קובץ גם שורת `duid` ורשומות IPv6; שורה ריקה,
    שורה קצרה ושורה שאין בה MAC אינן חכירות — ואינן מפילות את הקריאה."""
    text = ("duid 00:01:00:01:2a:3b:4c:5d:aa:bb:cc:00:00:99\n"
            "\n"
            "1789000000 12345678 fd00::5 STN-06 00:01:00:01\n"
            "garbage\n"
            f"1789000000 {MAC1} 10.44.12.50 STN-05 *\n")
    assert identity.parse_leases(text) == {MAC1: "10.44.12.50"}


def test_parse_leases_the_last_line_for_a_mac_wins():
    text = (f"1789000000 {MAC1} 10.44.12.50 STN-05 *\n"
            f"1789000900 {MAC1} 10.44.12.77 STN-05 *\n")
    assert identity.parse_leases(text) == {MAC1: "10.44.12.77"}


def test_lease_file_distinguishes_unreadable_from_no_lease(tmp_path: Path):
    """שלושה מצבים, לא שניים (עיקרון 5): הקובץ נקרא ויש חכירה; נקרא ואין;
    לא נקרא בכלל. ‏`if lookup.ip:` מקרי נופל לצד החוסם בשניים האחרונים."""
    path = write_leases(tmp_path / "leases", {MAC1: STATION_IP})
    leases = identity.LeaseFile(path)
    assert leases.lookup(MAC1) == identity.Lookup(read=True, ip=STATION_IP)
    assert leases.lookup(MAC2) == identity.Lookup(read=True, ip=None)
    path.unlink()
    assert leases.lookup(MAC1) == identity.Lookup(read=False, ip=None)


# --- hello ------------------------------------------------------------------


def test_hello_from_the_leased_address_is_answered_as_before(identity_server):
    ids = setup_classroom(identity_server)
    response = hello(identity_server["station"], ids["mac1"])
    assert response.status_code == 200, response.text
    assert response.json()["known"] is True
    assert ids["mac1"] in net_macs(identity_server)
    assert journal_events(identity_server, "identity_refused") == []


def test_hello_from_another_address_is_refused_and_leaves_no_trace(identity_server):
    """הסירוב הוא 403 בשם, היומן נוקב ב-MAC המוצהר, בכתובת המקור ובכתובת
    שבחכירה — והמכונה המזויפת **אינה** נרשמת: לא ב-`net_seen` (רשימת
    ההתקנים), לא בספירת הלולאות. רישום היה נותן לזייפן לשנות את ה-`ip`
    ו-`last_seen` שהשרת מכיר עבור מכונה אמיתית (‏#585)."""
    ids = setup_classroom(identity_server)
    response = hello(identity_server["stranger"], ids["mac1"])
    assert response.status_code == 403, response.text
    body = response.json()
    assert body["ok"] is False and body["code"] == "identity_refused"
    assert "task" not in body and "session" not in body

    rows = journal_details(identity_server, "identity_refused")
    assert len(rows) == 1
    for needle in (ids["mac1"], STRANGER_IP, STATION_IP):
        assert needle in rows[0][1], rows[0]
    assert ids["mac1"] not in net_macs(identity_server)
    hits = identity_server["ctx"].conn.execute(
        "SELECT COUNT(*) AS n FROM agent_loops").fetchone()["n"]
    assert hits == 0


def test_a_forged_hello_does_not_hand_over_the_task_token(identity_server):
    """פריט 2 של ה-Issue: מכונה זרה ששולחת hello עם ה-MAC של מחשב הבנייה
    הייתה מקבלת אסימון קליטה תקף. עכשיו — 403 בלי גוף של תשובת hello."""
    setup_build_machine(identity_server, BUILD)
    assert make_task(identity_server, BUILD).status_code == 200
    forged = hello(identity_server["stranger"], BUILD)
    assert forged.status_code == 403
    assert "token" not in forged.text
    genuine = hello(identity_server["station"], BUILD)
    assert genuine.status_code == 200
    assert genuine.json()["task"]["token"]


def test_a_forged_hello_neither_joins_nor_ripens_a_round(identity_server):
    """פריט 3: חברות והבשלה של סבב לפי MAC מוצהר. שני hello מזויפים
    (שתי המכונות הצפויות) אינם מצרפים ואינם מניעים `start_auto`; שני
    hello אמיתיים כן."""
    ids = open_round(identity_server, expected=2)
    store = identity_server["ctx"].store
    for mac in (ids["mac1"], ids["mac2"]):
        assert hello(identity_server["stranger"], mac).status_code == 403
    assert store.joined_count(ids["session"]) == 0
    assert session_state(identity_server, ids["session"]) == "open"
    assert journal_events(identity_server, "session_start_auto") == []
    # ‏MAC2 אין לו חכירה בפיקסטורה — מוסיפים אותה: הסבב צריך שתי מכונות אמיתיות.
    write_leases(identity_server["leases"], {MAC1: STATION_IP, MAC2: STATION_IP,
                                             BUILD: STATION_IP})
    for mac in (ids["mac1"], ids["mac2"]):
        assert hello(identity_server["station"], mac).status_code == 200
    assert store.joined_count(ids["session"]) == 2
    # ההבשלה נמדדת ב-hello **שאחרי** המצטרף האחרון (`maybe_start` קודם ל-`record_hello`).
    assert hello(identity_server["station"], ids["mac1"]).status_code == 200
    assert session_state(identity_server, ids["session"]) == "running"


def test_a_mac_without_a_lease_is_unverifiable_not_ok(identity_server):
    """עיקרון 5: "לא מצאנו חכירה" אינו "בדקנו והכתובת תואמת". סירוב בשם
    משלו — לא `identity_refused` (אין כתובת להשוות אליה) ולא 200."""
    ids = setup_classroom(identity_server)
    response = hello(identity_server["station"], ids["mac2"])
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "identity_unverifiable"
    rows = journal_details(identity_server, "identity_unverifiable")
    assert len(rows) == 1 and ids["mac2"] in rows[0][1]
    assert ids["mac2"] not in net_macs(identity_server)


def test_an_unreadable_leases_file_refuses_by_name(identity_server):
    """‏DHCP חיצוני, קובץ שנמחק, הרשאות — "לא הצלחנו לבדוק" ≠ "תקין".
    הסירוב נוקב בקובץ, כדי שהמפעיל ידע מה לתקן (או לכבות את המתג)."""
    ids = setup_classroom(identity_server)
    identity_server["leases"].unlink()
    response = hello(identity_server["station"], ids["mac1"])
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "identity_unverifiable"
    rows = journal_details(identity_server, "identity_unverifiable")
    assert len(rows) == 1
    assert identity_server["leases"].name in rows[0][1]


def test_a_malformed_mac_is_still_bad_mac(identity_server):
    """‏"לא הצלחנו לקרוא את המזהה" קודם לזהות — אין MAC להשוות מולו."""
    body = hello_body("not-a-mac")
    response = identity_server["stranger"].post("/api/v1/agent/hello", json=body)
    assert response.status_code == 400 and response.json()["code"] == "bad_mac"


def test_the_boot_menu_is_not_gated(identity_server):
    """‏GRUB מבקש את התפריט לפני שיש סוכן; מסלול ה-/boot אינו נחסם
    (‏#976 ו-#585 מטפלים בו במקומו, לפי כתובת השרת ולא לפי חכירה)."""
    ids = setup_classroom(identity_server)
    response = identity_server["stranger"].get(f"/boot/menu?mac={ids['mac1']}")
    assert response.status_code == 200, response.text


# --- המתג ------------------------------------------------------------------


def test_the_switch_is_on_by_default_and_health_reports_the_leases(identity_server):
    settings = identity_server["admin"].get("/api/console/settings").json()
    assert settings["identity_check"] in (None, "true")
    rows = {r["id"]: r for r in identity_server["admin"].get("/api/console/health").json()}
    assert rows["identity"]["state"] == "ok", rows["identity"]
    assert "2" in rows["identity"]["detail"]          # שתי חכירות בקובץ


def test_switching_off_admits_everyone_and_is_journalled_and_visible(identity_server):
    """‏DHCP חיצוני: המתג כבוי הוא מצב **מוצהר** — היומן אומר מי כיבה,
    והבריאות אומרת שהזהות אינה נבדקת. hello זר עובר."""
    ids = setup_classroom(identity_server)
    assert identity_server["admin"].post(
        "/api/console/settings", json={"identity_check": False}).status_code == 200
    assert ("noc", "identity_check=false") in journal_details(identity_server, "setting_change")

    assert hello(identity_server["stranger"], ids["mac1"]).status_code == 200
    assert hello(identity_server["station"], ids["mac2"]).status_code == 200
    rows = {r["id"]: r for r in identity_server["admin"].get("/api/console/health").json()}
    assert rows["identity"]["state"] == "warn"
    assert "לא נבדקת" in rows["identity"]["detail"]
    assert "מתג" in rows["identity"]["detail"]

    assert identity_server["admin"].post(
        "/api/console/settings", json={"identity_check": "true"}).status_code == 200
    assert hello(identity_server["stranger"], ids["mac1"]).status_code == 403


def test_the_deploy_user_cannot_switch_it_off(identity_server):
    assert identity_server["deploy"].post(
        "/api/console/settings", json={"identity_check": "false"}).status_code == 403


def test_health_flags_an_unreadable_leases_file_as_bad(identity_server):
    """המתג דלוק והקובץ לא נקרא = כל hello מסורב. זה מצב שהמפעיל חייב
    לראות במסך הבריאות, לא לגלות ממכונות שלא עולות."""
    identity_server["leases"].unlink()
    rows = {r["id"]: r for r in identity_server["admin"].get("/api/console/health").json()}
    assert rows["identity"]["state"] == "bad"
    assert identity_server["leases"].name in rows["identity"]["detail"]


# --- progress ---------------------------------------------------------------


def _round_report(mac: str, session_id: str) -> dict:
    return {"session_id": session_id, "mac": mac, "state": "writing",
            "targets": [{"dev": "sda", "bytes_written": 10, "bytes_total": 100,
                         "state": "writing"}]}


def test_a_round_report_from_another_address_is_refused(identity_server):
    """פריט 4: דיווח סבב לפי `session_id`+MAC מהגוף. מכתובת זרה → 403,
    והחבר אינו משתנה; מכתובת החכירה → כרגיל."""
    ids = open_round(identity_server, expected=1)
    for _ in range(2):      # הצטרפות, ואז ה-hello שמבשיל (‏maybe_start קודם ל-record_hello)
        assert hello(identity_server["station"], ids["mac1"]).status_code == 200
    assert session_state(identity_server, ids["session"]) == "running"

    forged = identity_server["stranger"].post(
        "/api/v1/agent/progress", json=_round_report(ids["mac1"], ids["session"]))
    assert forged.status_code == 403, forged.text
    assert forged.json()["code"] == "identity_refused"
    row = identity_server["ctx"].conn.execute(
        "SELECT state, bytes_written FROM session_members WHERE session_id = ? AND mac = ?",
        (ids["session"], ids["mac1"])).fetchone()
    assert (row["state"], row["bytes_written"]) == ("waiting", 0)

    genuine = identity_server["station"].post(
        "/api/v1/agent/progress", json=_round_report(ids["mac1"], ids["session"]))
    assert genuine.status_code == 200, genuine.text


def test_a_capture_report_needs_both_the_token_and_the_identity(identity_server):
    """האסימון (‏db67dbd) והזהות הם שני שומרים, לא אחד: אסימון נכון
    מכתובת זרה — 403 זהות; כתובת נכונה בלי אסימון — 403 אסימון; שניהם — 200."""
    setup_build_machine(identity_server, BUILD)
    task = make_task(identity_server, BUILD).json()
    token = hello(identity_server["station"], BUILD).json()["task"]["token"]
    report = {"task_id": task["id"], "mac": BUILD, "state": "capturing",
              "targets": [{"dev": "sda", "bytes_written": 5, "bytes_total": 0,
                           "state": "capturing"}]}

    forged = identity_server["stranger"].post(
        "/api/v1/agent/progress", json=report, headers={TOKEN_HEADER: token})
    assert forged.status_code == 403 and forged.json()["code"] == "identity_refused"
    no_token = identity_server["station"].post("/api/v1/agent/progress", json=report)
    assert no_token.status_code == 403 and no_token.json()["code"] == "bad_token"
    genuine = identity_server["station"].post(
        "/api/v1/agent/progress", json=report, headers={TOKEN_HEADER: token})
    assert genuine.status_code == 200, genuine.text
    state = identity_server["ctx"].conn.execute(
        "SELECT state FROM tasks WHERE id = ?", (task["id"],)).fetchone()["state"]
    assert state == "running"


def test_a_report_with_a_malformed_mac_is_still_bad_mac(identity_server):
    response = identity_server["stranger"].post(
        "/api/v1/agent/progress", json=_round_report("zz", "ses_x"))
    assert response.status_code == 400 and response.json()["code"] == "bad_mac"


# --- מתי השומר מותקן --------------------------------------------------------


def test_without_a_lease_source_the_gate_is_not_installed(server):
    """הפיקסטורה הרגילה של החבילה (בלי `identity_hooks`) — כמו `dhcp_hooks`
    ו-`known_macs_hooks`: הצד שנוגע במכונה מוזרק, ובלעדיו אין שומר.
    זה **אינו** מצב ייצור — `server.main` מתקין אותו תמיד (הטסט הבא)."""
    ids = setup_classroom(server)
    assert server["anon"].post("/api/v1/agent/hello",
                               json=hello_body(ids["mac1"])).status_code == 200


def test_main_installs_the_lease_file_by_default(tmp_path: Path, monkeypatch):
    """הראיה הישירה: ‏`server.main` מעביר ל-`create_runtime` מקור חכירות —
    ברירת המחדל היא קובץ dnsmasq של דביאן, ו---dhcp-leases מחליף אותו."""
    import asyncio
    import uvicorn

    args = server.main.build_parser().parse_args(["--server-url", "http://10.44.12.10:8080"])
    assert args.dhcp_leases == identity.DEFAULT_LEASES == "/var/lib/misc/dnsmasq.leases"

    captured: dict = {}

    def fake_create_runtime(*args, **kwargs):
        captured["identity_hooks"] = kwargs.get("identity_hooks")
        return "rt"

    monkeypatch.setattr(server.app, "create_runtime", fake_create_runtime)
    monkeypatch.setattr(server.app, "create_agent_app", lambda rt: "agent")
    monkeypatch.setattr(server.app, "create_console_app", lambda rt: "console")
    monkeypatch.setattr(uvicorn, "Config", lambda app, **kwargs: object())
    monkeypatch.setattr(uvicorn, "Server", lambda config: object())
    monkeypatch.setattr(server.main, "serve_all", lambda servers: None)
    monkeypatch.setattr(asyncio, "run", lambda coro=None: None)
    monkeypatch.setattr(server.main, "_interface_for", lambda url: None)
    monkeypatch.setattr("sys.argv", [
        "server.main", "--server-url", "http://10.44.12.10:8080",
        "--data-dir", str(tmp_path / "data"), "--images", str(tmp_path / "img"),
        "--dhcp-leases", str(tmp_path / "custom.leases"),
    ])
    server.main.main()
    leases = captured["identity_hooks"]["leases"]
    assert isinstance(leases, identity.LeaseFile)
    assert leases.path == tmp_path / "custom.leases"


def test_the_simulation_leases_every_machine_it_runs_to_loopback(tmp_path: Path):
    """‏e2e (‏tools/e2e_simulation.py): שרת אמיתי, ארבע מכונות על loopback.
    השומר **אינו** מכובה שם — ההרנס כותב קובץ חכירות לכל MAC שהוא מפעיל
    (גם ה-MAC "הלא מוכר" של שלב הקצוות — בייצור גם הוא מקבל חכירה) ומעביר
    אותו ב---dhcp-leases."""
    path = harness.write_leases(tmp_path)
    leases = identity.LeaseFile(path)
    for mac in (harness.BUILD_MAC, harness.CLONER_MAC, harness.UNKNOWN_MAC,
                *harness.CLASS_MACS):
        assert leases.lookup(mac) == identity.Lookup(read=True, ip="127.0.0.1"), mac
    assert harness.NO_LEASE_MAC not in identity.parse_leases(path.read_text())
