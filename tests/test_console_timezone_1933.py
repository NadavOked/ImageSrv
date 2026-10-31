"""‏#1933 / #1932: זמנים למפעיל בשעון המקומי של השרת, לא ב-UTC כלשונו.

‏#1933 (נדב 21/10 05:33): הקונסולה כתבה "עודכן היום ב-02:22" כשהשעה בישראל
הייתה 05:22 — ‏`fmtClock` חתך את מחרוזת ה-ISO (UTC). הקונסולה ממירה עכשיו
לאזור של השרת, ולכן ‏`/me` מחזיר אותו (‏`timezone`, ‏#1447). ומסך השרת (DCUI)
חתך את אותה חותמת בשורת "סבב הפצה · התחיל HH:MM".

‏#1932: מהראשי, הגדרות חיפה הראו "אזור זמן — לא נקרא: הנתיב אינו זמין דרך שרת
המשנה", והסקירה "זמן פעילות לא נבדק". ‏`/timezone` (קריאה וכתיבה, כמו
‏`/settings`) עובר בערוץ, וזמן הפעילות מגיע ב-`/overview` — ‏`/me` אינו עובר
בערוץ במכוון (הוא של המשתמש בראשי).
"""

from __future__ import annotations

import os
import time
from dataclasses import replace

import pytest

from server import db
from server.system import timezone

# ‏fixtures של שני השרתים האמיתיים (‏#1179) — ייבוא, לא העתקה.
from test_secondary_console_relay import secondary                  # noqa: F401
from test_storage_transfer import paired                             # noqa: F401


# --- #1933: /me נושא את אזור התצוגה ------------------------------------------

def test_me_carries_the_servers_zone_for_the_console_clock(server):
    me = server["admin"].get("/api/console/me").json()
    # בלי הגדרה: אזור ברירת המחדל של #1447 — זה שהשרת מיישר אליו את המערכת בעלייה. לא UTC.
    assert me["timezone"] == "Asia/Jerusalem"
    db.set_setting(server["ctx"].conn, timezone.SETTING, "Europe/London")
    assert server["admin"].get("/api/console/me").json()["timezone"] == "Europe/London"


# --- #1933: מסך השרת (DCUI) — "התחיל HH:MM" בשעון המכונה ----------------------

@pytest.fixture()
def jerusalem_process_tz():
    """ה-DCUI מציג בשעון המכונה (‏datetime.now() מקומי). כאן המכונה = ירושלים."""
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset אינו קיים בווינדוס — הטסט רץ על מעבדת הלינוקס")
    old = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Jerusalem"
    time.tzset()
    yield
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


def test_dcui_round_start_is_the_machines_clock_not_utc(jerusalem_process_tz):
    from server.dcui import view                                   # noqa: PLC0415
    from server.dcui.data import DeployRound, demo_snapshot        # noqa: PLC0415

    snap = replace(demo_snapshot(), deploy_round=DeployRound(
        "s", "office365", "running", 2, 3, "2026-10-21T02:22:00+00:00"))
    assert view._round_row(snap).sub == "התחיל 05:22"
    winter = replace(snap, deploy_round=replace(snap.deploy_round, started="2026-12-01T10:00:00+00:00"))
    assert view._round_row(winter).sub == "התחיל 12:00"            # שעון חורף: +02:00
    bad = replace(snap, deploy_round=replace(snap.deploy_round, started="לא חותמת"))
    assert view._round_row(bad).sub == ""                          # לא נקרא → בלי שעה, לא ניחוש


# --- #1932: אזור הזמן וזמן הפעילות של שרת המשנה, דרך הראשי -------------------

@pytest.fixture()
def fake_tz(monkeypatch):
    """‏timedatectl ורשימת tzdata מזויפים לשני השרתים (שניהם באותו תהליך) —
    לעולם לא נוגעים במכונה (hostguard, #113)."""
    state = {"zone": "Asia/Jerusalem", "set": []}

    def set_tz(zone):
        state["set"].append(zone)
        state["zone"] = zone

    monkeypatch.setattr(timezone, "default_hooks",
                        lambda: {"timezone": lambda: state["zone"], "set_timezone": set_tz})
    monkeypatch.setattr(timezone, "zones",
                        lambda root=None: ["Asia/Jerusalem", "Europe/London", "UTC"])
    return state


def _relay(server, nid, path, method="GET", body=None):
    return server["admin"].request(method, f"/api/console/storage-nodes/{nid}/console{path}",
                                   json=body)


def test_the_secondarys_timezone_is_read_through_the_primary(fake_tz, server, secondary, paired):
    db.set_setting(secondary["conn"], timezone.SETTING, "Europe/London")   # שונה מהראשי
    resp = _relay(server, paired, "/timezone")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["zone"] == "Europe/London" and "UTC" in body["zones"]


def test_the_secondarys_timezone_is_changed_through_the_primary(fake_tz, server, secondary,
                                                               paired):
    resp = _relay(server, paired, "/timezone", "POST", {"zone": "Europe/London"})
    assert resp.status_code == 200, resp.text
    assert db.get_setting(secondary["conn"], timezone.SETTING) == "Europe/London"
    assert db.get_setting(server["ctx"].conn, timezone.SETTING) is None, "הראשי לא שונה"
    assert fake_tz["set"] == ["Europe/London"]


def test_the_secondarys_uptime_reaches_the_primary_in_overview(fake_tz, server, secondary,
                                                              paired):
    own = secondary["admin"].get("/api/console/overview").json()
    assert own["uptime_seconds"] == 600                            # ה-hook של המשני
    resp = _relay(server, paired, "/overview")
    assert resp.status_code == 200, resp.text
    assert resp.json()["uptime_seconds"] == 600
