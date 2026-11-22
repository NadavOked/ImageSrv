"""מפקד: אילו פעולות של הקונסולה נגישות מהראשי על 7700 (#3492, מתוך #1179).

הקונסולה של שרת משני עוברת על 7700 דרך שתי רשימות סגורות, CONSOLE_GETS ו-
CONSOLE_WRITES (server/interserver/api.py), ורצה בזהות primary:<user>/admin.
הביטויים ברשימות הם [^/]+: נתיב חדש בן קטע אחד מתחת ל-/images/,
/storage-locations/ או /users/ ייפתח על 7700 מיד, ובשום טסט לא יופיע אדום.
הטסט הזה עובר על **כל** פעולה של /api/console מתוך OpenAPI ובודק אותה מול
הרשימות — ומשווה את הקבוצה הנגישה לרשימה שכתובה כאן ביד.

PINNED נכתב ביד ולא נגזר מהרשימות: טסט שמחשב את המצופה מאותו מקור תמיד עובר.
מי שהוסיף נתיב ורואה את הטסט נופל — מוסיף אותו לכאן **במודע**, אחרי שהחליט שהוא
צריך להיות נגיש מהראשי בזהות admin (כולל MFA ותפקיד שהמשני אינו בודק; ראו
deploy/firewall-rules.md).

מקור הרשימה: 56 פעולות, מתוך 249 ב-/api/console — נמדד על lab/vm-1114f
@ 9affa37fb, 15/11 (אגיס, #1179).

הערה על מה שהטסט **אינו** מכסה: הבקשה בתוך התהליך רצה עם client=127.0.0.1 ב-scope
(_run_console), ולכן נתיב שמגן על עצמו ב-require_local או בבדיקת loopback
**אינו** נחסם שם. נתיב כזה שמתאים לביטוי ברשימה ייכנס לקבוצה הנגישה כאן, ויידרש
להיכנס ל-PINNED — ואין להסיק מכך שהוא "סגור". הטסט בודק התאמה לרשימות, לא
התנהגות של נתיב.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("fastapi")

from server.interserver import api as interserver_api                   # noqa: E402

PREFIX = "/api/console"
HTTP_METHODS = ("get", "post", "put", "delete", "patch")

#: (שיטה, נתיב כפי שמופיע ב-OpenAPI, אחרי /api/console).
PINNED: frozenset[tuple[str, str]] = frozenset({
    ("GET", "/disk-failures"), ("GET", "/drivers"), ("GET", "/folders"),
    ("GET", "/groups"), ("GET", "/health"), ("GET", "/hostname"), ("GET", "/images"),
    ("GET", "/images/{image_id}"), ("GET", "/journal"), ("GET", "/journal/events"),
    ("GET", "/machines"), ("GET", "/monitor/machines"), ("GET", "/monitor/settings"),
    ("POST", "/monitor/{mac}/agent-power"), ("GET", "/net"), ("GET", "/net/config"),
    ("POST", "/net/config/confirm"), ("PUT", "/net/config/{name}"),
    ("PUT", "/net/config/{name}/deploy"), ("POST", "/net/config/{name}/preview"),
    ("PUT", "/net/config/{name}/second"), ("GET", "/net/deploy"),
    ("GET", "/net/interfaces"), ("POST", "/net/interfaces"),
    ("DELETE", "/net/interfaces/{name}"), ("PUT", "/net/interfaces/{name}"),
    ("PUT", "/net/interfaces/{name}/description"),
    ("GET", "/net/interfaces/{name}/leases"), ("GET", "/overview"),
    ("GET", "/ports"), ("GET", "/room"), ("POST", "/room"), ("POST", "/room/close"),
    ("GET", "/room/driver-models"), ("GET", "/room/history"), ("GET", "/sessions"),
    ("GET", "/settings"), ("POST", "/settings"), ("GET", "/shrink-records"),
    ("GET", "/storage-locations"), ("GET", "/storage-locations/disks"),
    ("GET", "/storage-node-groups"), ("GET", "/storage-nodes"),
    ("GET", "/storage-transfers"), ("GET", "/tasks"), ("GET", "/timezone"),
    ("POST", "/timezone"), ("GET", "/tls"), ("GET", "/update"),
    ("POST", "/update/apply"), ("POST", "/update/check"), ("GET", "/update/status"),
    ("GET", "/users"), ("POST", "/users"), ("DELETE", "/users/{username}"),
    ("PUT", "/users/{username}"),
})


def _console_operations(app) -> set[tuple[str, str]]:
    ops: set[tuple[str, str]] = set()
    for path, item in app.openapi()["paths"].items():
        if not path.startswith(PREFIX):
            continue
        sub = path[len(PREFIX):] or "/"
        for method in item:
            if method in HTTP_METHODS:
                ops.add((method.upper(), sub))
    return ops


def _reachable(ops: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """הפעולות שהשער הבין-שרתי מעביר — אותן שתי פונקציות ש-console_get/
    console_write קוראות. {param} מוחלף בערך שרירותי: הביטויים מקבלים כל קטע."""
    out: set[tuple[str, str]] = set()
    for method, sub in ops:
        sample = re.sub(r"\{[^}]+\}", "x1", sub)
        if method == "GET":
            ok = interserver_api.console_get_type(sample) is not None
        else:
            ok = interserver_api.console_write_allowed(method, sample)
        if ok:
            out.add((method, sub))
    return out


def test_openapi_has_console_operations(server):
    """שומר על המפקד עצמו: אם ה-OpenAPI חזר ריק או בלי הקונסולה, "אף פעולה לא
    נגישה" לא יעבור כהצלחה (עיקרון 5)."""
    ops = _console_operations(server["app"])
    assert len(ops) > 150, f"OpenAPI של הקונסולה מכיל רק {len(ops)} פעולות"
    assert PINNED <= ops, (
        "פעולה ברשימה המוצמדת אינה קיימת יותר ב-OpenAPI — להסיר אותה מ-PINNED: "
        f"{sorted(PINNED - ops)}")


def test_console_surface_on_8443_is_pinned(server):
    reachable = _reachable(_console_operations(server["app"]))
    new = sorted(reachable - PINNED)
    gone = sorted(PINNED - reachable)
    assert not new, (
        "נתיב חדש נגיש מהראשי בזהות admin — להוסיף במודע לרשימה המוצמדת "
        f"(PINNED בטסט הזה, אחרי שהוחלט שהוא צריך להיות פתוח על 7700): {new}")
    assert not gone, (
        "פעולה מהרשימה המוצמדת כבר אינה נגישה מהראשי — אם זה מכוון, להסיר אותה מ-"
        f"PINNED; אם לא, הרשימות ב-api.py נשברו: {gone}")
    assert len(reachable) == len(PINNED) == 56
