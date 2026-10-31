"""שם הכרטיס הוא השדה שנכתב לקובץ dnsmasq כטקסט חופשי (‏#102).

‏`validate` בדקה כל שדה כתובת ולא בדקה את השם — והשם הוא היחיד שנכתב
בלי מרכאות ובלי בריחה. ‏uvicorn מפענח `%0A` בנתיב לשורה חדשה, ולכן
‏`PUT /interfaces/<שם>` עם `allow_missing` היה **הוראה שלמה** לקובץ
שדמון שרץ כ-root קורא: `dhcp-range` שני על ממשק שאיש לא הגדיר. רווח
או `#` בשם אינם הזרקה אלא התרחיש הריאלי — dnsmasq מסרב לעלות, ווילן
ההפצה כולו נשאר בלי DHCP ובלי PXE.

הבדיקות כאן הן על **שני** המסלולים שכותבים שם, כי רק אחד מהם בדק:
הוספה ידנית (`POST /interfaces`) והגדרה (`PUT /interfaces/{name}`),
ובתוך ההגדרה גם מסלול ה-proxy — שהיציאה המוקדמת `if not cfg.enabled`
פטרה מהוולידציה כולה.
"""

from __future__ import annotations

from urllib.parse import quote

import pytest

pytest.importorskip("fastapi")

from server.net import dhcp
from server.net.dhcp import InterfaceConfig, render, render_proxy, validate

from test_server_dhcp import (              # noqa: F401 — הפיקסטורה מיובאת בשמה
    GOOD, dhcp_server,
)

#: מה שאסור להיכנס. השורה הראשונה היא ההזרקה מהאישיו — `dhcp-range`
#: לא דורש `/`, ולכן הוא עובר בשלמותו דרך נתיב ה-URL.
INJECTION = "eth0\ndhcp-range=10.0.0.1,10.0.0.200,255.255.255.0,12h"

HOSTILE = [
    INJECTION,
    "eth0\ndhcp-script=/x",
    "eth 0",                                # רווח — dnsmasq לא עולה
    "eth0#x",                               # תגובה — שאר השורה נבלעת
    "eth0\ttab",
    "eth0\r",
    "",
    "-eth0",                                # לא מתחיל באות/ספרה
    "כרטיס",                                # לא אנגלית
    "e" * 16,                               # מעבר ל-IFNAMSIZ
    "eth0/../x",
]

LEGAL = ["eth0", "eth1.700", "br-lan", "enp3s0f1", "eth0:1", "wlan_0", "e" * 15]


# --- הכלל עצמו --------------------------------------------------------------


@pytest.mark.parametrize("name", HOSTILE)
def test_a_hostile_name_is_refused_in_hebrew(name):
    with pytest.raises(ValueError) as err:
        dhcp.validate_name(name)
    assert "שם כרטיס" in str(err.value)


@pytest.mark.parametrize("name", LEGAL)
def test_a_real_interface_name_still_passes(name):
    dhcp.validate_name(name)


def test_the_refusal_shows_the_name_that_was_refused():
    """תו בלתי-נראה חייב להיראות בהודעה — אחרת המפעיל רואה "שם פסול"
    על מה שנראה לו כמו `eth0` תקין."""
    with pytest.raises(ValueError) as err:
        dhcp.validate_name("eth0 ")
    assert repr("eth0 ") in str(err.value)


def test_the_name_is_checked_before_the_disabled_shortcut():
    """‏`if not cfg.enabled: return` דילג על הבדיקה כולה — וממשק
    ב-proxy הוא בדיוק `proxy and not enabled`."""
    for cfg in (InterfaceConfig(INJECTION),
                InterfaceConfig(INJECTION, proxy=True),
                InterfaceConfig(INJECTION, **GOOD)):
        with pytest.raises(ValueError) as err:
            validate(cfg)
        assert "שם כרטיס" in str(err.value)


def test_a_proxy_interface_no_longer_skips_validation():
    """כתובת השרת נכתבת ל-`dhcp-range=set:...,<כתובת>,proxy` — גם היא
    עברה קודם בלי בדיקה, כי ממשק proxy אינו enabled."""
    with pytest.raises(ValueError) as err:
        validate(InterfaceConfig("eth1.700", proxy=True, server_ip="10.44.9.300"))
    assert "כתובת השרת" in str(err.value)
    validate(InterfaceConfig("eth1.700", proxy=True, server_ip="10.44.101.10"))


# --- השער האחרון לפני הקובץ --------------------------------------------------


def test_the_renderers_refuse_a_bad_row_instead_of_writing_it():
    """רשומה שנשמרה לפני התיקון לא הופכת לקובץ שבור בשקט.

    מי שנכשל הוא מי שכותב את השם: הקובץ הראשי כותב גם `except-interface`
    לממשק proxy ולכן רואה את שניהם, וקובץ ה-proxy אינו רואה ממשק
    שמחלק כתובות — ואין טעם שייכשל עליו.
    """
    full = [InterfaceConfig(INJECTION, **GOOD)]
    proxied = [InterfaceConfig(INJECTION, proxy=True)]
    for configs in (full, proxied):
        with pytest.raises(ValueError):
            render(configs)
    with pytest.raises(ValueError):
        render_proxy(proxied)
    assert render_proxy(full)                  # לא שלו — ולא נופל עליו


# --- שני המסלולים, דרך ה-API -------------------------------------------------


def path_of(name: str) -> str:
    """מה שהדפדפן או `curl` באמת שולחים — ‏`%0A` לשורה חדשה, ‏`%23`
    ל-`#`. בלי הקידוד `#` בכלל לא היה מגיע לשרת (הוא fragment), והטסט
    היה מדווח על משהו אחר לגמרי."""
    return "/api/console/net/interfaces/" + quote(name, safe="")


def _nothing_was_written(fake, before: int) -> None:
    assert len(fake["applied"]) == before
    assert not any("10.0.0.200" in text for text in fake["applied"])
    assert not any("10.0.0.200" in text for text, _ in fake["proxy_applied"])


@pytest.mark.parametrize("name", [INJECTION, "eth 0", "eth0#x", "-eth0"])
def test_configure_refuses_a_hostile_name_from_the_url_path(dhcp_server, name):
    """מסלול ההגדרה — השם מגיע מהנתיב, ו-`allow_missing` פותח אותו לכל
    מחרוזת. זו ההגדרה של "גמור" באישיו."""
    admin, fake = dhcp_server["admin"], dhcp_server["fake"]
    before = len(fake["applied"])
    resp = admin.put(path_of(name),
                     json={**GOOD, "confirm": name, "allow_missing": True})
    assert resp.status_code == 400
    assert "שם כרטיס" in resp.json()["detail"]
    _nothing_was_written(fake, before)


def test_the_proxy_path_is_refused_too(dhcp_server):
    """‏proxy הוא המסלול שדילג על הוולידציה — והוא נבדק לפני שאר
    השכבות, כדי ששם פסול לא יחזור כ-409 על גרסת dnsmasq."""
    admin, fake = dhcp_server["admin"], dhcp_server["fake"]
    before = len(fake["proxy_applied"])
    resp = admin.put(path_of(INJECTION),
                     json={"proxy": True, "confirm": INJECTION,
                           "confirm_proxy_broken": True, "allow_missing": True,
                           "server_ip": "10.44.101.10"})
    assert resp.status_code == 400
    assert "שם כרטיס" in resp.json()["detail"]
    assert len(fake["proxy_applied"]) == before


@pytest.mark.parametrize("name", [INJECTION, "eth 0", "eth0#x"])
def test_add_interface_refuses_the_same_names(dhcp_server, name):
    """המסלול שכן בדק — אותה הודעה, מאותו מקום אחד."""
    admin = dhcp_server["admin"]
    resp = admin.post("/api/console/net/interfaces", json={"name": name})
    assert resp.status_code == 400
    assert "שם כרטיס" in resp.json()["detail"]


def test_the_config_file_never_grows_a_line_nobody_asked_for(dhcp_server):
    """הראיה החיובית: אחרי הניסיון העוין, הקובץ שנכתב הוא בדיוק מה
    שהוגדר — כרטיס אחד, טווח אחד."""
    admin, fake = dhcp_server["admin"], dhcp_server["fake"]
    assert admin.put("/api/console/net/interfaces/eth0",
                     json={**GOOD, "confirm": "eth0"}).status_code == 200
    admin.put(path_of(INJECTION),
              json={**GOOD, "confirm": INJECTION, "allow_missing": True})
    text = fake["applied"][-1]
    assert len([ln for ln in text.splitlines() if ln.startswith("dhcp-range=")]) == 1
    assert "10.0.0.200" not in text


# --- המסלול השלישי, שנשאר מאחור (#130) ---------------------------------------


@pytest.mark.parametrize("name", [INJECTION, "eth0 ", "eth0#c", "e" * 40])
def test_describe_refuses_the_same_names(dhcp_server, name):
    """‏#102 אכף את השם בשני מסלולים; ‏`describe` היה השלישי ולא נבדק.

    זו אינה הזרקה ל-dnsmasq — ‏`all_configs()` קוראת רק מפתחות
    ‏`dhcp:` — אלא **כישלון שקט**: ‏`nicdesc:eth0 ` (עם רווח) הוא מפתח
    אחר מ-`nicdesc:eth0`, ולכן המפעיל מקליד תיאור, מקבל ``{"ok": true}``
    ולא רואה אותו לעולם.
    """
    admin = dhcp_server["admin"]
    conn = dhcp_server["ctx"].conn
    before = conn.execute(
        "SELECT COUNT(*) FROM settings WHERE key LIKE 'nicdesc:%'").fetchone()[0]

    r = admin.put(path_of(name) + "/description", json={"description": "וילן הפצה"})

    assert r.status_code == 400, f"{name!r} התקבל"
    assert "שם כרטיס" in r.json()["detail"]
    assert conn.execute(
        "SELECT COUNT(*) FROM settings WHERE key LIKE 'nicdesc:%'"
    ).fetchone()[0] == before, "נכתבה שורה למרות הסירוב"


def test_describe_still_works_for_a_real_name(dhcp_server):
    """הבדיקה אינה חוסמת את השימוש שלשמו הנתיב קיים."""
    admin = dhcp_server["admin"]
    assert admin.put(path_of("eth1.700") + "/description",
                     json={"description": "וילן הפצה"}).status_code == 200
    assert dhcp_server["ctx"].conn.execute(
        "SELECT value FROM settings WHERE key = 'nicdesc:eth1.700'"
    ).fetchone()[0] == "וילן הפצה"


def test_an_empty_name_never_reaches_the_handler(dhcp_server):
    """שם ריק נדחה בניתוב ולא בוולידציה — ``/interfaces//description``
    אינו מתאים לאף מסלול. נבדק במפורש כדי ש-404 לא ייראה כמו פער."""
    admin = dhcp_server["admin"]
    conn = dhcp_server["ctx"].conn
    before = conn.execute(
        "SELECT COUNT(*) FROM settings WHERE key LIKE 'nicdesc:%'").fetchone()[0]

    assert admin.put("/api/console/net/interfaces//description",
                     json={"description": "x"}).status_code == 404
    assert conn.execute(
        "SELECT COUNT(*) FROM settings WHERE key LIKE 'nicdesc:%'"
    ).fetchone()[0] == before
