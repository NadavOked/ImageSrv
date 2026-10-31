"""‏#36 — השומר של מצב proxy: כפתור אחד שיכול להקפיא את dnsmasq.

מצב proxy נשען על תכונה שבורה ב-dnsmasq המותקן: בקשת PXE לפורט 4011
מקפיאה את התהליך (‏100% מעבד, מפסיק לענות לכל הסוקטים). הבידוד לאינסטנס
נפרד (‏imagesrv-proxy) כבר שם, והוא מציל את ה-DHCP של וילן ההפצה — אבל
הוא לא הופך את ההדלקה לפעולה שאפשר לעשות בהיסח הדעת.

הבדיקות כאן על ההגנה עצמה, לא על התכונה:

1. ההחלטה נשענת על **ראיה חיובית** — גרסה שנקראה ונמצאת ברשימת
   הגרסאות שנבדקו במעבדה. "לא הצלחנו לקרוא את הגרסה" נופל לצד החוסם
   (עיקרון 5), בדיוק כמו `ProbeResult` ב-#53.
2. גם ה-API חוסם, לא רק המסך — מסך מנוטרל שה-endpoint שמאחוריו פתוח
   אינו הגנה.
3. ההסבר אומר את האמת ונוקב בגרסה שנקראה, כדי שמפעיל לא ינסה לעקוף
   הודעת "לא זמין".
4. כשגרסה תיבדק ותתווסף לרשימה — החסימה נפתחת מעצמה.

אף בדיקה כאן לא מריצה dnsmasq ולא נוגעת בשירות: הגרסה מוזרקת כ-hook,
בדיוק כמו `apply`/`probe`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

#: הכרעת נדב 11/10: ‏proxy מסורב בשרת כשהכיתות כבויות (v1). השומר של #36
#: נבדק כאן על v1.2 — ולכן היכולת דלוקה (`classrooms_on`).
pytestmark = pytest.mark.usefixtures("classrooms_on")

from server.net import dhcp, dhcp_host
from server.console.journal_he import EVENTS_HE

try:
    from fastapi.testclient import TestClient
except ImportError:                                   # pragma: no cover
    TestClient = None


#: הפלט האמיתי של `dnsmasq --version` על מכונת המעבדה (Debian trixie).
REAL_291 = (
    "Dnsmasq version 2.91  Copyright (c) 2000-2025 Simon Kelley\n"
    "Compile time options: IPv6 GNU-getopt DBus no-UBus i18n IDN2 DHCP DHCPv6\n"
)


# --- ההחלטה הטהורה ----------------------------------------------------------


def test_the_installed_version_is_read_out_of_the_real_banner():
    support = dhcp.proxy_support(REAL_291)
    assert support.read is True and support.version == "2.91"
    assert support.broken is True
    assert support.verified is False


@pytest.mark.parametrize("raw", [None, "", "dnsmasq: command not found", "   "])
def test_a_version_we_could_not_read_is_not_a_version_that_is_fine(raw):
    """עיקרון 5: "לא הצלחנו לבדוק" אינו "בדקנו והכל תקין". בלי גרסה
    שנקראה אין ראיה חיובית, ולכן `verified` נשאר False."""
    support = dhcp.proxy_support(raw)
    assert support.read is False and support.version == ""
    assert support.verified is False
    # ו"לא נקרא" אינו "שבור ידוע" — אלה שני מצבים שונים, ושניהם חוסמים.
    assert support.broken is False


def test_a_version_nobody_tested_is_neither_broken_nor_approved():
    """‏2.92 יצאה בלי אזכור תיקון ב-CHANGELOG. היעדר אזכור אינו ראיה
    לתיקון — הגרסה נשארת "לא נבדקה", והיא חוסמת."""
    support = dhcp.proxy_support("Dnsmasq version 2.92  Copyright (c) 2000-2025")
    assert support.read is True and support.version == "2.92"
    assert support.broken is False
    assert support.verified is False


def test_proxy_support_is_never_falsey():
    """אותו לקח של #53: אובייקט falsey הופך `if support:` מקרי לאישור.
    ההחלטה נקראת מהשדות, לא מהאמת-ערך של האובייקט."""
    assert dhcp.proxy_support(None)
    assert dhcp.proxy_support(REAL_291)
    assert dhcp.ProxySupport(False)


def test_the_guard_opens_by_itself_when_a_version_passes_the_lab(monkeypatch):
    """הרשימה היא רשימת ראיות: ברגע שגרסה נבדקה מול תחנת UEFI ועבדה,
    היא נכנסת ל-PROXY_VERIFIED — וההגנה נפתחת בלי לגעת בשום מקום אחר."""
    assert dhcp_host.PROXY_VERIFIED == (), (
        "הרשימה ריקה בכוונה: אף גרסה עוד לא נבדקה מול חומרה"
    )
    monkeypatch.setattr(dhcp_host, "PROXY_VERIFIED", ("2.93",))
    assert dhcp.proxy_support("Dnsmasq version 2.93").verified is True
    # וגרסה אחרת עדיין חסומה — הרשימה מדויקת, לא "מכאן והלאה".
    assert dhcp.proxy_support(REAL_291).verified is False


def test_the_explanation_names_the_real_failure_and_the_real_version():
    """מפעיל שרואה "לא זמין" מנסה לעקוף. ההסבר חייב לומר מה קורה
    בפועל, ועל איזו גרסה הוא מבוסס."""
    broken = dhcp.proxy_support(REAL_291).reason()
    assert "2.91" in broken and "4011" in broken
    assert "imagesrv-proxy" in broken          # למה זה לא מפיל את ההפצה
    assert "confirm_proxy_broken" in broken    # ואיך בכל זאת מדליקים לבדיקה

    unknown = dhcp.proxy_support(None).reason()
    assert "לא ניתן לקרוא את גרסת dnsmasq" in unknown

    untested = dhcp.proxy_support("Dnsmasq version 2.92").reason()
    assert "2.92" in untested and "לא נבדק" in untested


def test_reading_the_version_never_touches_the_service(monkeypatch):
    """`dnsmasq_version` מריץ `--version` בלבד. כשלון הרצה מחזיר None,
    שהוא "לא יודעים" — לא חריגה שמפילה את הקונסולה."""
    calls = []

    class Fake:
        returncode = 0
        stdout = REAL_291

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Fake()

    monkeypatch.setattr(dhcp_host.subprocess, "run", fake_run)
    assert dhcp_host.dnsmasq_version() == REAL_291
    assert calls == [["dnsmasq", "--version"]]

    def boom(cmd, **kwargs):
        raise OSError("no such file")

    monkeypatch.setattr(dhcp_host.subprocess, "run", boom)
    assert dhcp_host.dnsmasq_version() is None


def test_a_nonzero_exit_is_not_a_version(monkeypatch):
    """קוד יציאה שאינו 0 = לא קראנו גרסה, גם אם משהו נפלט ל-stdout."""

    class Failed:
        returncode = 2
        stdout = "Dnsmasq version 2.91"

    monkeypatch.setattr(dhcp_host.subprocess, "run", lambda cmd, **kw: Failed())
    assert dhcp_host.dnsmasq_version() is None


# --- דרך ה-API --------------------------------------------------------------


@pytest.fixture()
def guarded(tmp_path: Path, images_root: Path, clock):
    """שרת עם גרסת dnsmasq מוזרקת. אין subprocess, אין systemctl."""
    if TestClient is None:
        pytest.skip("fastapi is required")
    from server.console import users
    from server.app import create_app

    fake = {
        "proxy_applied": [],
        "dnsmasq_version": REAL_291,
    }
    hooks = {
        "interfaces": lambda: [
            {"name": "eth0", "state": "up", "mac": "aa:aa:aa:aa:aa:00",
             "addresses": ["10.44.9.10/24"]},
            {"name": "eth1", "state": "up", "mac": "aa:aa:aa:aa:aa:01",
             "addresses": ["10.44.1.10/24"]},
        ],
        "probe": lambda name: dhcp.ProbeResult(True, ()),
        "apply": lambda text: None,
        "apply_proxy": lambda text, active: (
            fake["proxy_applied"].append((text, active)), None)[1],
        "dnsmasq_version": lambda: fake["dnsmasq_version"],
    }
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     now_fn=clock, dhcp_hooks=hooks)
    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    admin = TestClient(app)
    admin.post("/api/console/login",
               json={"username": "noc", "password": "admin-pass-123"})
    return {"admin": admin, "fake": fake, "ctx": app.state.ctx, "app": app}


ON = {"proxy": True, "server_ip": "10.44.1.10", "confirm": "eth1"}


def test_the_api_refuses_proxy_on_a_version_that_was_never_verified(guarded):
    """‏#36 סעיף 3: הכפתור לבדו אינו הגנה. גם קריאה ישירה ל-API — עם
    ההקלדה של שם הכרטיס ובלי המסך — נעצרת."""
    admin, fake = guarded["admin"], guarded["fake"]
    response = admin.put("/api/console/net/interfaces/eth1", json=ON)
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "2.91" in detail and "4011" in detail
    # לא הוחל כלום: לא נכתב קובץ proxy ולא הופעלה יחידה.
    assert fake["proxy_applied"] == []
    # וההגדרה לא נשמרה — הכרטיס נשאר כבוי.
    rows = {r["name"]: r for r in admin.get("/api/console/net/interfaces").json()}
    assert rows["eth1"]["proxy"] is False


def test_the_api_refuses_when_it_could_not_read_the_version_at_all(guarded):
    """עיקרון 5 בשכבת ה-HTTP: dnsmasq שלא ענה אינו dnsmasq תקין."""
    admin, fake = guarded["admin"], guarded["fake"]
    fake["dnsmasq_version"] = None
    response = admin.put("/api/console/net/interfaces/eth1", json=ON)
    assert response.status_code == 409
    assert "לא ניתן לקרוא את גרסת dnsmasq" in response.json()["detail"]
    assert fake["proxy_applied"] == []


def test_switching_full_dhcp_to_proxy_is_gated_too(guarded):
    """מעבר מ-DHCP מלא ל-proxy אינו "הדלקה" (‏turning_on הוא False),
    ובלי בדיקה נפרדת הוא היה עוקף את השומר לגמרי."""
    admin = guarded["admin"]
    good = dict(enabled=True, range_start="10.44.1.50", range_end="10.44.1.200",
                netmask="255.255.255.0", lease="12h", server_ip="10.44.1.10")
    assert admin.put("/api/console/net/interfaces/eth1",
                     json={**good, "confirm": "eth1"}).status_code == 200
    assert admin.put("/api/console/net/interfaces/eth1",
                     json=ON).status_code == 409


def test_an_explicit_acknowledgement_lets_the_lab_turn_it_on(guarded):
    """ההגנה היא אישור, לא קיר: את הגרסה הבאה חייבים להיות מסוגלים
    לבדוק. האישור נפרד מהקלדת שם הכרטיס — שתי פעולות, לא אחת."""
    admin, fake = guarded["admin"], guarded["fake"]
    response = admin.put("/api/console/net/interfaces/eth1",
                         json={**ON, "confirm_proxy_broken": True})
    assert response.status_code == 200, response.text
    text, active = fake["proxy_applied"][-1]
    assert active is True and "interface=eth1" in text

    # האישור לבדו אינו מספיק — שם הכרטיס עדיין נדרש.
    admin.put("/api/console/net/interfaces/eth1", json={"proxy": False})
    refused = admin.put("/api/console/net/interfaces/eth1",
                        json={"proxy": True, "server_ip": "10.44.1.10",
                              "confirm_proxy_broken": True})
    assert refused.status_code == 409 and "שם הממשק" in refused.json()["detail"]


def test_a_verified_version_needs_no_acknowledgement(guarded, monkeypatch):
    """כשהגרסה המותקנת נבדקה במעבדה, ההגנה נעלמת מעצמה — אין דגל
    שצריך לזכור להסיר ואין "שבור לנצח" מקודד."""
    monkeypatch.setattr(dhcp_host, "PROXY_VERIFIED", ("2.93",))
    guarded["fake"]["dnsmasq_version"] = "Dnsmasq version 2.93"
    assert guarded["admin"].put("/api/console/net/interfaces/eth1",
                                json=ON).status_code == 200


def test_turning_proxy_on_is_written_to_the_hebrew_journal(guarded):
    """מי הדליק, על איזה כרטיס, ועל איזו גרסת dnsmasq — כששואלים למה
    ה-PXE קפא, זו השורה שעונה."""
    admin = guarded["admin"]
    assert admin.put("/api/console/net/interfaces/eth1",
                     json={**ON, "confirm_proxy_broken": True}).status_code == 200
    rows = admin.get("/api/console/journal").json()
    events = {r["event"]: r for r in rows}
    assert "dhcp_proxy_risk" in events, [r["event"] for r in rows]
    entry = events["dhcp_proxy_risk"]
    assert entry["label"] == EVENTS_HE["dhcp_proxy_risk"]
    assert "2.91" in entry["text"] and "eth1" in entry["text"]
    # ולצדה השורה הרגילה של שינוי המצב.
    assert "dhcp_set" in events and "proxy" in events["dhcp_set"]["text"]


def test_an_unreadable_version_is_named_as_such_in_the_journal(guarded):
    guarded["fake"]["dnsmasq_version"] = None
    admin = guarded["admin"]
    assert admin.put("/api/console/net/interfaces/eth1",
                     json={**ON, "confirm_proxy_broken": True}).status_code == 200
    entry = next(r for r in admin.get("/api/console/journal").json()
                 if r["event"] == "dhcp_proxy_risk")
    assert "הגרסה לא נקראה" in entry["text"]


def test_the_console_reads_the_same_verdict_the_api_enforces(guarded):
    """המסך לא מנסח אזהרה משלו: הוא מציג את `reason` של השרת. אחרת
    השניים מתפצלים, והמסך ממשיך להבטיח משהו שה-API כבר לא עושה."""
    admin = guarded["admin"]
    support = admin.get("/api/console/net/proxy-support").json()
    assert support["version"] == "2.91" and support["broken"] is True
    assert support["verified"] is False and support["read"] is True
    refused = admin.put("/api/console/net/interfaces/eth1", json=ON)
    assert refused.json()["detail"] == support["reason"]


def test_proxy_support_is_admin_only(guarded, tmp_path):
    """מצב ה-proxy הוא מידע על תשתית — משתמש deploy לא רואה אותו."""
    from server.console import users
    ctx = guarded["ctx"]
    users.create(ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    deploy = TestClient(guarded["app"])
    deploy.post("/api/console/login",
                json={"username": "labtech", "password": "deploy-pass-1"})
    assert deploy.get("/api/console/net/proxy-support").status_code == 403
    assert deploy.put("/api/console/net/interfaces/eth1",
                      json={**ON, "confirm_proxy_broken": True}).status_code == 403
