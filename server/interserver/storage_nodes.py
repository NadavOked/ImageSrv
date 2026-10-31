"""Storage Nodes — תפקיד השרת המותקן, ורישום המשניים (#655, tracer 1.1 / #723).

הפרוסה הקטנה ביותר של הפיצ'ר: **תפקיד** מותקן שנקרא fail-closed,
**ספירת** המשניים הפעילים, ומהם **דגל** ה-capability שממנו הקונסולה
חושפת את לשונית "העברה בין סניפים". אין כאן העברות, enrollment או ניהול
מרחוק — אלה השלבים הבאים בשרשרת #655.

לשרת יש תפקיד התקנה יחיד:

- ``standalone`` — עובד עצמאית, ורשאי להחזיק משניים ישירים. משהחזיק
  אחד, הקונסולה מכנה אותו "ראשי", אבל אין תפקיד ``primary`` נפרד.
- ``secondary`` — שרת ImageSrv מקומי מלא, עם הורה אחד, שאינו יכול
  להחזיק ילדים.

התפקיד נקרא **fail-closed** (עיקרון 1 + עיקרון 5): ערך **חסר** (``None``)
הוא ברירת המחדל הלגיטימית של שרת טרי — ``main`` כותב ערך מפורש בהפעלה —
ולכן הוא ``standalone``. אבל ערך ש**נכתב** ואינו אחד מהתפקידים המוכרים
(פגום, זר, או ``"primary"``) נכשל-סגור ל-``secondary`` — המצב שאינו
מנהל משניים — ולא ``standalone`` המורשה-יותר (‏#746). שרת שההגדרה שלו
פגומה אינו "מתקדם" להרשאות ניהול.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import uuid
from pathlib import Path

from server.core import hostname_rule
from server.interserver import interserver_auth
from server.core.atomicfile import write_atomic
from server.db import (_write_lock, get_setting, journal, now_iso, set_setting, set_settings,
                       update_one, writing)

log = logging.getLogger("imagesrv.storage_nodes")

ROLE_STANDALONE = "standalone"
ROLE_SECONDARY = "secondary"
ROLES = (ROLE_STANDALONE, ROLE_SECONDARY)

#: מפתחות ההגדרה בטבלת ``settings`` (db.py). אותו מסלול get_setting/
#: set_setting של כל שאר ההגדרות — לא טבלה נפרדת.
ROLE_KEY = "storage:role"
PRIMARY_URL_KEY = "storage:primary_url"
#: זהות השרת ברשת הבין-שרתית. #740 מעביר אותה בהמשך ל-``storage_identity``
#: (עם SPKI/תעודה שנגזרים משכבת ה-TLS); עד אז מזהה יציב בהגדרות מספיק
#: להצגה בחלון ה-pairing המקומי.
NODE_ID_KEY = "storage:node_id"
PARENT_ID_KEY = "storage:parent_id"


def role(conn) -> str:
    """התפקיד המותקן, נקרא fail-closed.

    ‏#746: ערך **חסר** (``None``) הוא ברירת המחדל הלגיטימית של שרת טרי —
    ‏``main`` כותב ערך מפורש בהפעלה, וזהו מצב "לא הוגדר עדיין", לא ערך
    פגום — ולכן הוא מוכרע כ-``standalone``. אבל ערך ש**נכתב** ואינו אחד
    מהתפקידים המוכרים (פגום, זר, או ``"primary"``) **נכשל-סגור**
    ל-``secondary`` — המצב שאינו יכול לנהל משניים — ולא ל-``standalone``
    המורשה-יותר. אחרת ``storage:role`` פגום היה "מתקדם" להרשאות ניהול
    במקום להיסגר (עיקרון 1: מצב לא-ברור מסתיים בברירת המחדל הבטוחה).
    """
    raw = get_setting(conn, ROLE_KEY)
    if raw in ROLES:
        return raw
    if raw is None:
        return ROLE_STANDALONE
    log.warning(
        "storage:role שנקרא אינו תפקיד מוכר (%r) — נכשל-סגור ל-%s",
        raw, ROLE_SECONDARY)
    return ROLE_SECONDARY


def primary_url(conn) -> str:
    """כתובת השרת הראשי — רלוונטית רק למשני; ריקה אחרת."""
    return get_setting(conn, PRIMARY_URL_KEY) or ""


def enabled_node_count(conn) -> int:
    """כמה משניים רשומים **ופעילים** (``disabled_at IS NULL``)."""
    return conn.execute(
        "SELECT COUNT(*) AS n FROM storage_nodes WHERE disabled_at IS NULL"
    ).fetchone()["n"]


def can_interbranch_transfer(conn, user_role: str) -> bool:
    """הדגל שממנו הקונסולה חושפת את לשונית ההעברה בין הסניפים.

    שלושה תנאים, וכולם חייבים להתקיים: המשתמש הוא ``admin``, השרת
    הוא ``standalone`` (משני אינו דוחף מעלה — היררכיה חד-כיוונית),
    ויש לפחות משני פעיל אחד להעביר אליו. משתמש ``deploy``, שרת משני,
    ושרת בלי משניים — כולם מקבלים ``False``. האכיפה בשרת היא הסמכותית;
    הסתרת הלשונית היא רק נראות.
    """
    return (
        user_role == "admin"
        and role(conn) == ROLE_STANDALONE
        and enabled_node_count(conn) > 0
    )


def can_enroll_secondary(conn, user_role: str) -> bool:
    """הדגל שממנו הראשי חושף את פעולת "הוסף משני" (#740).

    בניגוד ל-``interbranch_transfer``, זה **אינו** דורש משני קיים — זו
    בדיוק הפעולה שיוצרת את הראשון. admin **וגם** standalone."""
    return user_role == "admin" and role(conn) == ROLE_STANDALONE


def can_open_local_pairing(conn, user_role: str) -> bool:
    """הדגל שממנו המשני חושף את פאנל ה-pairing המקומי (#740). admin+secondary."""
    return user_role == "admin" and role(conn) == ROLE_SECONDARY


class StorageConfigError(ValueError):
    """תצורת אחסון שאי אפשר להתקין — משני בלי כתובת שרת ראשי."""


def normalize_config(role_value: str, url: str | None) -> tuple[str, str]:
    """מאמת ומחזיר ``(role, primary_url)`` לכתיבה.

    משני מחייב כתובת שרת ראשי לא-ריקה, ותצורה סותרת **נכשלת בקול**
    ולא מתגלגלת ל-``standalone`` בשקט בזמן ההתקנה (עיקרון 5). כתובת
    שרת ראשי על ``standalone`` נזרקת — אין לה משמעות בלי הורה.
    """
    if role_value not in ROLES:
        raise StorageConfigError(f"תפקיד אחסון לא מוכר: {role_value!r}")
    url = (url or "").strip()
    if role_value == ROLE_SECONDARY and not url:
        raise StorageConfigError(
            "שרת משנה מחייב כתובת שרת ראשי (--primary-url)")
    return role_value, (url if role_value == ROLE_SECONDARY else "")


def persist_config(conn, role_value: str, url: str | None) -> None:
    """כותב את תצורת האחסון להגדרות בהפעלה (נקרא מ-``main``/מהמתקין).

    מאמת קודם דרך :func:`normalize_config` — קריאה עם תצורה סותרת
    זורקת :class:`StorageConfigError` ואינה כותבת דבר.

    ‏#732: התפקיד והכתובת נכתבים ב**טרנזאקציה אחת** (``set_settings``).
    שתי כתיבות נפרדות היו יכולות להשאיר תפקיד חדש עם כתובת ישנה אם
    השנייה נכשלת (דיסק מלא, נעילה) — מצב לא-עקבי שנקרא כתצורה תקינה.
    """
    role_value, url = normalize_config(role_value, url)
    set_settings(conn, {ROLE_KEY: role_value, PRIMARY_URL_KEY: url})


# --- ניהול המרשם: קבוצות והמשניים הרשומים (#727, tracer #2) ----------------
#
# כאן **אין** enrollment: שרת משני נוצר בהוספה עם כתובת ואישורי מנהל
# (שלב #3, כמו הוספת מארח ל-vCenter). הפרוסה הזו מנהלת רשומות **קיימות**
# — קבוצות שמארגנות אותן, שינוי שם/שיוך/השבתה/מחיקה של משני — ואוכפת
# שהכול קורה רק על שרת ראשי. ההיררכיה חד-כיוונית: משני אינו מנהל ילדים,
# ולכן כל פונקציה מְשַׁנָּה בודקת את התפקיד בעצמה (עיקרון 5), ולא סומכת
# על שכבת ה-route לבדה.


class NodeManagementForbidden(RuntimeError):
    """ניהול משניים מותר רק על שרת ראשי (``standalone``).

    לא נתפס בשכבת ה-service: משני שמנסה לנהל ילדים הוא הפרה של
    ההיררכיה החד-כיוונית, לא מצב תקין שמתגלגל בשקט. שכבת ה-route
    מתרגמת אותו ל-409, אבל האכיפה עצמה יושבת כאן — קריאה ישירה
    לפונקציה על משני נכשלת בדיוק כמו דרך ה-API.
    """


class NodeManagementUnauthorized(RuntimeError):
    """ניהול משניים מותר ל-``admin`` בלבד (#732).

    ה-route חוסם ``deploy`` ב-403 דרך ``admin_only``, אבל האכיפה
    יושבת גם כאן: קריאה ישירה לשכבת ה-service בשם משתמש שאינו מנהל
    נכשלת בדיוק כמו דרך ה-API. עד #732 שכבת ה-service בדקה רק את
    התפקיד המותקן (standalone) והסתמכה על ה-route לבדו לזהות מנהל —
    בדיוק החצי שעיקרון 5 (route+service) דורש לסגור.
    """


def assert_can_manage_nodes(conn, user: tuple[str, str]) -> None:
    """השומר של **כל** פעולת ניהול משניים, כולל ה-listings (#732).

    שני תנאים, שניהם נאכפים בשכבת ה-service ולא רק ב-route: המשתמש
    הוא ``admin`` (אחרת :class:`NodeManagementUnauthorized`, ‏403),
    והשרת הוא ``standalone`` (אחרת :class:`NodeManagementForbidden`,
    ‏409). האכיפה הכפולה היא עיקרון 5: route+service, לא route לבדו.
    """
    if user[1] != "admin":
        raise NodeManagementUnauthorized(
            "ניהול שרתי משנה מותר למנהל בלבד")
    if role(conn) != ROLE_STANDALONE:
        raise NodeManagementForbidden(
            "ניהול שרתי משנה אפשרי רק בשרת ראשי (standalone)")


# --- קבוצות אחסון ----------------------------------------------------------

def list_groups(conn, user: tuple[str, str]) -> list[dict]:
    """הקבוצות עם מספר המשניים בכל אחת, לפי סדר התצוגה.

    ‏#732: גם קריאה אוכפת admin+standalone בשכבת ה-service — לא רק
    ה-route. רשימת המשניים היא מידע ניהולי בין-סניפי, ואין סיבה
    שתיחשף למי שאסור לו לנהל אותם.
    """
    assert_can_manage_nodes(conn, user)
    rows = conn.execute(
        "SELECT g.id, g.label, g.sort, COUNT(n.id) AS nodes"
        " FROM storage_node_groups g"
        " LEFT JOIN storage_nodes n ON n.group_id = g.id"
        " GROUP BY g.id ORDER BY g.sort, g.id"
    ).fetchall()
    return [dict(r) for r in rows]


def create_group(conn, label: str, user: tuple[str, str]) -> str:
    """יוצר קבוצה בסוף הרשימה ומחזיר את המזהה שנוצר.

    המזהה הוא UUID יציב (כמו הערת הסכימה) — הקבוצה מזוהה בשמה במסך,
    והמזהה נשאר פנימי ולכן אינו צריך להיגזר משם שהמפעיל הקליד.
    """
    assert_can_manage_nodes(conn, user)
    label = (label or "").strip()
    if not label:
        raise ValueError("שם הקבוצה ריק")
    gid = uuid.uuid4().hex
    last = conn.execute(
        "SELECT MAX(sort) AS n FROM storage_node_groups"
    ).fetchone()["n"]
    with _write_lock, writing(conn):
        conn.execute(
            "INSERT INTO storage_node_groups (id, label, sort) VALUES (?, ?, ?)",
            (gid, label, (last or 0) + 1),
        )
    journal(conn, "storage_group_create", f"{gid} label={label}", user[0])
    return gid


def rename_group(conn, group_id: str, label: str, user: tuple[str, str]) -> None:
    assert_can_manage_nodes(conn, user)
    label = (label or "").strip()
    if not label:
        raise ValueError("שם הקבוצה ריק")
    with _write_lock, writing(conn):
        if not update_one(
            conn, "UPDATE storage_node_groups SET label = ? WHERE id = ?",
            (label, group_id),
        ):
            raise ValueError("קבוצה לא קיימת")
    journal(conn, "storage_group_edit", f"{group_id} label={label}", user[0])


def reorder_groups(conn, ids: list[str], user: tuple[str, str]) -> None:
    """סדר התצוגה. הרשימה חייבת להיות **בדיוק** קבוצת המזהים הקיימת,
    בלי כפילויות (#732).

    רשימה חלקית, ריקה או עם מזהה כפול משאירה חלק מהקבוצות עם ``sort``
    שלא נכתב, והמפעיל רואה סדר שאינו מה שביקש בלי לדעת שזה מה שקרה —
    "הצלחה" שאינה הצלחה (עיקרון 5). מזהה כפול גם כותב ``sort`` פעמיים
    ומשאיר אינדקס מדולג. לכן נדרשת התאמה מדויקת: אותם מזהים, כל אחד פעם.
    """
    assert_can_manage_nodes(conn, user)
    known = {r["id"] for r in conn.execute("SELECT id FROM storage_node_groups")}
    if len(ids) != len(set(ids)):
        raise ValueError("רשימת הסדר מכילה מזהה כפול")
    if set(ids) != known:
        raise ValueError("רשימת הסדר חייבת לכלול בדיוק את כל הקבוצות הקיימות")
    with _write_lock, writing(conn):
        for index, gid in enumerate(ids):
            conn.execute(
                "UPDATE storage_node_groups SET sort = ? WHERE id = ?",
                (index, gid),
            )
    journal(conn, "storage_group_reorder", ", ".join(ids), user[0])


def delete_group(conn, group_id: str, user: tuple[str, str]) -> None:
    """מוחק קבוצה. המשניים שבה אינם נמחקים — ``group_id`` שלהם הופך
    ל-NULL דרך ה-``ON DELETE SET NULL`` שבסכימה (foreign_keys=ON)."""
    assert_can_manage_nodes(conn, user)
    with _write_lock, writing(conn):
        if not update_one(
            conn, "DELETE FROM storage_node_groups WHERE id = ?", (group_id,)
        ):
            raise ValueError("קבוצה לא קיימת")
    journal(conn, "storage_group_delete", group_id, user[0])


# --- משניים רשומים (רשומות קיימות בלבד — היצירה היא enrollment, #3) ---------

def list_nodes(conn, user: tuple[str, str]) -> list[dict]:
    """כל המשניים הרשומים עם שם הקבוצה ומצב ההשבתה.

    ‏#732: אוכף admin+standalone בשכבת ה-service כמו כל פעולת ניהול.
    ‏#954 גל 7: ``node_id`` (המזהה הנגזר מ-SPKI, ``sn_<hex>``) נוסף
    לתשובה — קיים בעמודה מאז ה-enrollment (#883) אך לא נחשף; טבלת
    "סניפים" מציגה אותו ליד השם.
    """
    assert_can_manage_nodes(conn, user)
    # ‏#1017: ``last_seen_at``/``last_error``/``last_error_at`` — זמן התגובה
    # האחרון כפי שהשרת רשם (``record_contact``), כדי שהטבלה תציג "ענתה
    # לאחרונה HH:MM" משעון השרת גם לפני שהדף בדק בעצמו.
    rows = conn.execute(
        "SELECT n.id, n.label, n.base_url, n.group_id, n.tls_fingerprint,"
        " n.node_id, n.enrolled_at, n.disabled_at, g.label AS group_label,"
        " n.last_seen_at, n.last_error, n.last_error_at,"
        " n.server_version, n.server_version_at"
        " FROM storage_nodes n"
        " LEFT JOIN storage_node_groups g ON g.id = n.group_id"
        " ORDER BY n.label, n.id"
    ).fetchall()
    # ‏#1997: שם שנרשם לפני הכלל ואינו שם מחשב ("חיפה") — מוצג כמו שהוא, וכאן
    # הסיבה, ל"דורש טיפול" בסקירה. ‏null = חוקי. אין מיגרציה שמשנה שם.
    return [{**dict(r), "label_problem": hostname_rule.refusal(r["label"], "שם שרת המשנה")}
            for r in rows]


def check_label(label: str | None) -> str:
    """‏#1997 (נדב, 22/10): שם של שרת משנה הוא שם מחשב — `hostname_rule`.
    מחזיר את השם אחרי ``strip``, או ``ValueError`` בעברית שנוקבת בכלל.
    נקרא ברישום (גם ב-route, **לפני** ה-pairing) ובעריכה."""
    label = (label or "").strip()
    if not label:
        raise ValueError("שם שרת המשנה ריק")
    problem = hostname_rule.refusal(label, "שם שרת המשנה")
    if problem:
        raise ValueError(problem)
    return label


#: סמן ל"השדה לא נכח בבקשה" — נבדל מ-``None`` המפורש (ניתוק קבוצה).
_UNSET = object()


def edit_node(conn, node_id: str, user: tuple[str, str], *,
              label=_UNSET, group_id=_UNSET) -> None:
    """עריכת משני — שם ו/או שיוך לקבוצה — ב**טרנזאקציה אחת** (#732).

    עד #732 ה-route קרא ל-``relabel_node`` ואז ל-``set_node_group``
    כשתי כתיבות נפרדות: השם נכתב ו-commit, ואם השיוך נכשל אחריו (קבוצה
    לא קיימת → 400) השם כבר שונה — הצלחה חלקית שהמפעיל אינו יודע עליה.
    כאן הכול-או-כלום: מאמתים תחילה, ואז כותבים את שני השינויים באותו
    בלוק ``writing``; כישלון של אחד מגלגל את שניהם (``rollback``).

    ``group_id`` שהוא ``None`` מפורש = ניתוק; היעדרו (``_UNSET``) = לא
    נגעו בשיוך. אותו הבדל ל-``label``.
    """
    assert_can_manage_nodes(conn, user)
    if label is not _UNSET:
        # ‏#1997: שם קיים שאינו חוקי ("חיפה") שנשלח שוב כמו שהוא — עם שינוי
        # הקבוצה בלבד — אינו נחסם; שם **חדש** חייב לעמוד בכלל.
        current = conn.execute(
            "SELECT label FROM storage_nodes WHERE id = ?", (node_id,)).fetchone()
        stripped = (label or "").strip()
        unchanged = current is not None and stripped == current["label"]
        label = stripped if unchanged else check_label(label)
    if group_id is not _UNSET:
        group_id = group_id or None
        # קבוצה שאינה קיימת נדחית בקול — אחרת ה-FK היה זורק IntegrityError סתום.
        if group_id is not None and conn.execute(
            "SELECT 1 FROM storage_node_groups WHERE id = ?", (group_id,)
        ).fetchone() is None:
            raise ValueError("קבוצה לא קיימת")
    if label is _UNSET and group_id is _UNSET:
        return                       # אין מה לשנות
    with _write_lock, writing(conn):
        # ``update_one`` היה עושה ``rollback`` על אי-התאמה ומקטע את
        # הטרנזאקציה המשותפת; כאן ``execute`` גולמי + ``raise``, וה-
        # ``writing`` מגלגל את שני השינויים יחד.
        if label is not _UNSET:
            if conn.execute(
                "UPDATE storage_nodes SET label = ? WHERE id = ?",
                (label, node_id),
            ).rowcount != 1:
                raise ValueError("שרת משנה לא קיים")
        if group_id is not _UNSET:
            if conn.execute(
                "UPDATE storage_nodes SET group_id = ? WHERE id = ?",
                (group_id, node_id),
            ).rowcount != 1:
                raise ValueError("שרת משנה לא קיים")
    detail = [f"label={label}"] if label is not _UNSET else []
    if group_id is not _UNSET:
        detail.append(f"group={group_id or '—'}")
    journal(conn, "storage_node_edit", f"{node_id} " + " ".join(detail), user[0])


def set_node_disabled(conn, node_id: str, disabled: bool,
                      user: tuple[str, str]) -> None:
    """השבתה/הפעלה מחדש. חותמת ולא דגל: משני מושבת אינו נמחק, ו-NULL
    הוא "פעיל" (כמו ``users.disabled_at``). ``enabled_node_count``
    סופר NULL בלבד, ולכן השבתה מסתירה מיד את לשונית ההעברה."""
    assert_can_manage_nodes(conn, user)
    stamp = now_iso() if disabled else None
    with _write_lock, writing(conn):
        if not update_one(
            conn, "UPDATE storage_nodes SET disabled_at = ? WHERE id = ?",
            (stamp, node_id),
        ):
            raise ValueError("שרת משנה לא קיים")
    journal(conn, "storage_node_disable" if disabled else "storage_node_enable",
            node_id, user[0])


def delete_node(conn, node_id: str, user: tuple[str, str]) -> None:
    assert_can_manage_nodes(conn, user)
    with _write_lock, writing(conn):
        if not update_one(
            conn, "DELETE FROM storage_nodes WHERE id = ?", (node_id,)
        ):
            raise ValueError("שרת משנה לא קיים")
    journal(conn, "storage_node_delete", node_id, user[0])


# --- הצד המקומי של המשני: חלון pairing ו-break-glass (#740, tracer 2.1) ------
#
# כאן אין TLS ואין תקשורת בין-שרתית: אלה פעולות שמנהל מפעיל **מקומית**
# על המשני עצמו — פותח חלון pairing ורואה קוד חד-פעמי, או מנתק אב קיים
# (break-glass). האכיפה חוזרת בשכבת ה-service (עיקרון 5): גם קריאה ישירה
# לפונקציה בתפקיד/מצב שגוי נכשלת, לא רק דרך ה-route.


class NotSecondaryError(RuntimeError):
    """פעולת pairing מקומית מותרת רק על שרת ``secondary``.

    ראשי/עצמאי אינו נכנס למסלול המשני — הוא אינו מקבל אב. ה-route
    מתרגם ל-409; האכיפה עצמה יושבת כאן."""


class ParentAlreadyEnrolledError(RuntimeError):
    """כבר יש אב רשום — revoke-first. אב חדש אינו דורס אב קיים בשקט.

    ה-route מתרגם ל-409 עם "נתקו את האב הקיים תחילה"; החלפת אב שקטה
    היא בדיוק מה שהדגם הנעול אוסר (פינוי מקומי בלבד, לא מרחוק)."""


def node_id(conn) -> str:
    """מזהה השרת ברשת הבין-שרתית, יציב לאורך זמן. נוצר עצלן פעם אחת."""
    existing = get_setting(conn, NODE_ID_KEY)
    if existing:
        return existing
    generated = uuid.uuid4().hex
    set_setting(conn, NODE_ID_KEY, generated)
    return generated


def has_parent(conn) -> bool:
    """האם למשני כבר יש אב רשום (רשומת ``parent_credentials``)."""
    return conn.execute(
        "SELECT 1 FROM parent_credentials WHERE singleton = 1"
    ).fetchone() is not None


def parent_last_contact(conn) -> str | None:
    """Last positively authenticated contact from the paired parent."""
    row = conn.execute(
        "SELECT last_contact_at FROM parent_credentials WHERE singleton = 1"
    ).fetchone()
    return row["last_contact_at"] if row is not None else None


def assert_local_pairing_allowed(conn, user: tuple[str, str]) -> None:
    """השומר של פעולות ה-pairing המקומיות: admin **וגם** secondary.

    ‏deploy נחסם ב-``NodeManagementUnauthorized`` (403 ב-route), וראשי/
    עצמאי ב-``NotSecondaryError`` (409). האכיפה כפולה — route+service."""
    if user[1] != "admin":
        raise NodeManagementUnauthorized("פעולת pairing מותרת למנהל בלבד")
    if role(conn) != ROLE_SECONDARY:
        raise NotSecondaryError(
            "חלון pairing נפתח רק על שרת משנה (secondary)")


def open_local_pairing(conn, user: tuple[str, str]) -> dict:
    """פותח חלון pairing מקומי ומחזיר את הקוד **פעם אחת בלבד**.

    דוחה אם כבר קיים אב (revoke-first). הקוד הגלוי חוזר רק כאן; נשמר
    ממנו רק הגיבוב המלוח (:mod:`interserver_auth`).
    """
    assert_local_pairing_allowed(conn, user)
    return _open_pairing(conn, user[0], interserver_auth.WINDOW_TTL_SECONDS)


def _open_pairing(conn, actor: str, ttl_seconds: int) -> dict:
    if has_parent(conn):
        raise ParentAlreadyEnrolledError(
            "כבר קיים אב רשום — נתקו אותו תחילה (break-glass)")
    code = interserver_auth.open_pairing_window(conn, ttl_seconds=ttl_seconds)
    journal(conn, "storage_pairing_open", node_id(conn), actor)
    status = interserver_auth.pairing_window_status(conn)
    return {
        "code": code,
        "node_id": node_id(conn),
        "protocol_version": interserver_auth.PROTOCOL_VERSION,
        "expires_at": status.get("expires_at"),
        "attempts_remaining": status.get("attempts_remaining"),
    }


def local_pairing_status(conn, user: tuple[str, str]) -> dict:
    """מצב החלון — **בלי הקוד** — plus זהות ההצגה. admin+secondary."""
    assert_local_pairing_allowed(conn, user)
    status = interserver_auth.pairing_window_status(conn)
    status["node_id"] = node_id(conn)
    status["protocol_version"] = interserver_auth.PROTOCOL_VERSION
    status["has_parent"] = has_parent(conn)
    return status


def unbind_parent(conn, user: tuple[str, str]) -> None:
    """פינוי מקומי (break-glass): מוחק את האב הרשום וסוגר חלון פתוח.

    זו הדרך היחידה להחליף אב — פינוי מקומי על המשני, לא דריסה מרחוק.
    מוחק את רשומת האישור, מנקה את מזהה האב, וסוגר כל חלון pairing פתוח.
    """
    assert_local_pairing_allowed(conn, user)
    _unbind(conn, user[0])


def _unbind(conn, actor: str) -> None:
    with _write_lock, writing(conn):
        conn.execute("DELETE FROM parent_credentials WHERE singleton = 1")
        conn.execute("DELETE FROM pairing_windows WHERE singleton = 1")
    set_setting(conn, PARENT_ID_KEY, "")
    journal(conn, "storage_parent_unbind", node_id(conn), actor)


# --- אותן פעולות ממסך השרת הפיזי (DCUI, ‏F4) — הכרעת נדב 16/10 -------------
#
# וריאנט א' של המוקאפ (#1525): **בלי סיסמה** — הנוכחות ליד המקלדת של השרת
# היא ההרשאה, כמו איפוס הסיסמה ב-F9. אין כאן route ואין כתובת רשת: הפונקציות
# נקראות רק מתהליך ה-DCUI, root על tty1, ישירות מול ה-DB — אותו ערוץ של
# כל פעולה אחרת שלו (server/dcui/actions.py). שומר התפקיד נשאר: ראשי/עצמאי
# נכשל ב-``NotSecondaryError`` כמו ה-API (409). הסיכון שהוצג לנדב ואושר: הקוד
# לבדו אינו מספיק — חד-פעמי, ‏10 דקות, 5 ניסיונות, ותקף רק כשמנהל מחובר
# בקונסולת הראשי מקליד אותו (ואחרי שהשווה את הטביעה).

PHYSICAL_ACTOR = "dcui"


def _assert_secondary(conn) -> None:
    if role(conn) != ROLE_SECONDARY:
        raise NotSecondaryError(
            "חלון pairing נפתח רק על שרת משנה (secondary)")


def open_physical_pairing(conn) -> dict:
    """F4: פותח חלון של 10 דקות ומחזיר את הקוד **פעם אחת**. משני בלבד,
    ובלי אב רשום (revoke-first) — בדיוק כמו ``open_local_pairing``."""
    _assert_secondary(conn)
    opened = _open_pairing(conn, PHYSICAL_ACTOR,
                           interserver_auth.PHYSICAL_WINDOW_TTL_SECONDS)
    # מזהה של **החלון הזה**: חלון שנפתח אחריו (מה-API המקומי, גם באותה
    # שנייה) מחליף אותו, והמסך צריך לדעת שהקוד שהוא מציג כבר אינו תקף.
    opened["window_id"] = pairing_window_id(conn)
    return opened


def pairing_window_id(conn) -> str:
    """מזהה לא-סודי של החלון הפתוח — גיבוב של המלח האקראי שלו (16 בתים
    חדשים לכל פתיחה); ``""`` כשאין חלון. הקוד והגיבוב שלו אינם יוצאים."""
    row = conn.execute(
        "SELECT code_salt FROM pairing_windows WHERE singleton = 1").fetchone()
    return hashlib.sha256(bytes(row["code_salt"])).hexdigest()[:16] if row else ""


def close_physical_pairing(conn, window_id: str) -> bool:
    """המסך עם הקוד נסגר — הקוד מבוטל. סוגר רק את **החלון שלו**: חלון
    שנפתח אחריו (מה-API) אינו נמחק. ``True`` כשנסגר."""
    _assert_secondary(conn)
    with _write_lock, writing(conn):
        closed = bool(window_id) and pairing_window_id(conn) == window_id and conn.execute(
            "DELETE FROM pairing_windows WHERE singleton = 1").rowcount == 1
    if closed:
        journal(conn, "storage_pairing_closed", node_id(conn), PHYSICAL_ACTOR)
    return closed


def unbind_parent_physical(conn) -> None:
    """F4 על משני מצומד: ניתוק מקומי — אותו פינוי של ``unbind_parent``."""
    _assert_secondary(conn)
    _unbind(conn, PHYSICAL_ACTOR)


def parent_info(conn) -> dict | None:
    """האב הרשום (מזהה וזמן הרישום) — ``None`` כשאין."""
    row = conn.execute(
        "SELECT parent_id, enrolled_at FROM parent_credentials WHERE singleton = 1"
    ).fetchone()
    return dict(row) if row is not None else None


def identity_display(conn) -> tuple[str, str] | None:
    """‏(node_id, SPKI) של זהות ה-TLS — מה שהראשי קורא ב-preview — או
    ``None`` כשעדיין לא נוצרה (השרת לא עלה עם המאזין הבין-שרתי)."""
    row = _identity_row(conn)
    return (row["node_id"], row["server_spki"]) if row is not None else None


#: הכתובת שבה **הראשי** מגיע למשני (מאחורי NAT — החיצונית). המשני מציג
#: אותה במסך הקוד; הראשי מקליד אותה. ריק = לא הוגדרה (אזהרה, לא ניחוש).
ADVERTISED_URL_KEY = "storage:advertised_url"


def normalize_advertised_url(raw: str) -> str:
    """``host:port`` או ``https://host:port`` → ``https://host:port``;
    ריק → ריק (מחיקה). כל השאר נבדק ב-``parse_interserver_url`` (https,
    פורט מפורש, בלי משתמש/query) ונכשל בקול."""
    text = (raw or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    host, port, path = interserver_auth.parse_interserver_url(text)
    if path not in ("", "/"):
        raise interserver_auth.InterserverURLError("בלי נתיב — רק כתובת ופורט")
    shown = f"[{host}]" if ":" in host else host
    return f"https://{shown}:{port}"


def advertised_url(conn) -> str:
    return get_setting(conn, ADVERTISED_URL_KEY) or ""


def set_advertised_url(conn, raw: str) -> str:
    """שומר את הכתובת המנורמלת ומחזיר אותה **כפי שנקראה בחזרה**."""
    value = normalize_advertised_url(raw)
    set_setting(conn, ADVERTISED_URL_KEY, value)
    return advertised_url(conn)


# --- זהות ה-TLS של השרת עצמו (#740, tracer 2.1) ----------------------------
#
# לכל שרת ImageSrv זהות בין-שרתית אחת: תעודה חתומה-עצמית + מפתח, שממנה
# נגזר ה-node_id (SPKI). היא משמשת גם כ**שרת** (מאזין ה-enrollment של
# המשני) וגם כ**לקוח** (הראשי מציג אותה כתעודת-לקוח כשהוא נרשם למשני).

INTERSERVER_DIRNAME = "interserver"


class IdentityError(RuntimeError):
    """אין זהות TLS בין-שרתית, ואי אפשר ליצור אותה בלי data_dir."""


def _is_ip(host: str) -> bool:
    import ipaddress
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _write_0600(path: Path, data: bytes) -> None:
    """כותב חומר רגיש (מפתח פרטי) ב-0600, בכתיבה אטומית.

    ‏#1828: עד כאן התיעוד אמר "אטומית" והקוד היה ``O_TRUNC`` על הקובץ עצמו —
    כתיבה שנקטעה השאירה מפתח חתוך. עכשיו זה ``write_atomic`` (‏#1908)."""
    write_atomic(path, data, mode=0o600)


def _identity_row(conn):
    return conn.execute(
        "SELECT node_id, server_spki, server_cert_ref, server_key_ref"
        " FROM storage_identity WHERE singleton = 1"
    ).fetchone()


def _identity_with_pem(row: dict) -> dict:
    """מוסיף cert_pem (לא-סוד) ו-key_pem (best-effort) לשורת הזהות."""
    out = dict(row)
    try:
        out["cert_pem"] = Path(row["server_cert_ref"]).read_bytes()
    except OSError:
        out["cert_pem"] = None
    try:
        out["key_pem"] = Path(row["server_key_ref"]).read_bytes()
    except OSError:
        out["key_pem"] = None
    return out


def ensure_identity(conn, data_dir, *, host_sans=()) -> dict:
    """מחזיר את זהות ה-TLS, ויוצר אותה פעם אחת אם אין. ‏node_id נגזר מ-SPKI."""
    row = _identity_row(conn)
    if row is not None:
        return _identity_with_pem(dict(row))
    from cryptography.x509.oid import ExtendedKeyUsageOID

    directory = Path(data_dir) / INTERSERVER_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    cert_pem, key_pem = interserver_auth.generate_self_signed(
        "imagesrv-node",
        ip_sans=[h for h in host_sans if _is_ip(h)],
        dns_sans=[h for h in host_sans if not _is_ip(h)],
        eku=(ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH),
    )
    spki = interserver_auth.spki_sha256(cert_pem)
    nid = interserver_auth.node_id_from_spki(spki)
    cert_path = directory / "server.crt"
    key_path = directory / "server.key"
    write_atomic(cert_path, cert_pem)
    _write_0600(key_path, key_pem)
    with _write_lock, writing(conn):
        conn.execute(
            "INSERT INTO storage_identity (singleton, node_id, server_spki,"
            " server_cert_ref, server_key_ref) VALUES (1, ?, ?, ?, ?)",
            (nid, spki, str(cert_path), str(key_path)),
        )
    return {"node_id": nid, "server_spki": spki, "server_cert_ref": str(cert_path),
            "server_key_ref": str(key_path), "cert_pem": cert_pem, "key_pem": key_pem}


def identity(conn, *, data_dir=None) -> dict:
    """זהות ה-TLS של השרת. יוצר אם ``data_dir`` ניתן ואין עדיין; אחרת חריגה."""
    row = _identity_row(conn)
    if row is not None:
        return _identity_with_pem(dict(row))
    if data_dir is not None:
        return ensure_identity(conn, data_dir)
    raise IdentityError(
        "אין זהות TLS בין-שרתית — הפעל את השרת עם --interserver-* ליצירתה")


# --- לקוח יוצא למשני רשום (הצד של הראשי) -----------------------------------

def node_row(conn, node_id: str):
    """רשומת משני לפי מזהה-שורה, עם חומר האמון — או ``None``."""
    return conn.execute(
        "SELECT id, label, base_url, node_id, pinned_spki, credential_ref,"
        " disabled_at FROM storage_nodes WHERE id = ?", (node_id,)).fetchone()


# --- #1017: מתי המשני ענה לאחרונה ------------------------------------------
#
# כל קריאה בין-שרתית — ‏/check, ‏/machines, ‏/images, ‏push ו-pull — רושמת
# את תוצאתה על השורה: תשובה = ``last_seen_at`` עכשיו וניקוי הכשל; כשל =
# ‏``last_error``/``last_error_at`` עכשיו, ו-``last_seen_at`` נשאר מה שהיה.
# כך "לא ענה — 08:12" הוא זמן השרת, לא שעון הדפדפן שקרא את הדף.

CONTACT_FIELDS = ("last_seen_at", "last_error", "last_error_at")


def record_contact(conn, node_id: str, *, error: str | None) -> None:
    """‏``error=None`` = המשני ענה; אחרת הכשל (כבר בלי סודות)."""
    now = now_iso()
    with _write_lock, writing(conn):
        if error is None:
            conn.execute(
                "UPDATE storage_nodes SET last_seen_at = ?, last_error = NULL,"
                " last_error_at = NULL WHERE id = ?", (now, node_id))
        else:
            conn.execute(
                "UPDATE storage_nodes SET last_error = ?, last_error_at = ?"
                " WHERE id = ?", (error, now, node_id))


def record_version(conn, node_id: str, answer) -> None:
    """‏#1300: גרסת המשני מתשובה מוצלחת שלו (`/ping`, `/machines`). רק כשהשדה
    **נכח** בתשובה — משני ישן בלי השדה אינו מוחק גרסה שנקראה, ואינו נרשם
    כ"נבדק". ערך שאינו מחרוזת = "לא ידועה" (NULL עם זמן), לא ניחוש."""
    if not isinstance(answer, dict) or "server_version" not in answer:
        return
    value = answer["server_version"]
    value = value[:64] if isinstance(value, str) and value else None
    with _write_lock, writing(conn):
        conn.execute(
            "UPDATE storage_nodes SET server_version = ?, server_version_at = ?"
            " WHERE id = ?", (value, now_iso(), node_id))


def contact_fields(conn, node_id: str) -> dict:
    """השלושה כפי שרשומים עכשיו — לתשובות הפרוקסי, כדי שהדף יציג את זמן
    השרת בלי קריאה נוספת ל-`GET /storage-nodes`. משני שנמחק → כולם ``None``."""
    row = conn.execute(
        "SELECT last_seen_at, last_error, last_error_at FROM storage_nodes"
        " WHERE id = ?", (node_id,)).fetchone()
    if row is None:
        return {k: None for k in CONTACT_FIELDS}
    return {k: row[k] for k in CONTACT_FIELDS}


def node_client(conn, data_dir, node, *, timeout: float | None = None) -> tuple:
    """‏``(PinnedMTLSClient לא-פתוח, token)`` למשני רשום: הטוקן מקובץ ה-0600,
    תעודת-הלקוח מזהות השרת, וה-SPKI המוצמד מהרשומה. הקורא פותח ב-``with``.
    ‏``timeout`` (‏#1564, הדופק) = תקרה קצרה גם ל-handshake וגם לבקשה;
    ‏``None`` = ברירות המחדל של הלקוח."""
    from server.interserver import storage_client
    token = interserver_auth.load_credential(node["credential_ref"])
    ident = identity(conn, data_dir=data_dir)
    host, port, _ = interserver_auth.parse_interserver_url(node["base_url"])
    client = storage_client.PinnedMTLSClient(
        host, port, expected_secondary_spki=node["pinned_spki"],
        cert_pem=ident["cert_pem"], key_pem=ident["key_pem"],
        **({} if timeout is None else {"timeout": timeout, "request_timeout": timeout}))
    return client, token


# --- רישום אב על המשני, ורישום משני על הראשי -------------------------------

def record_parent(conn, *, parent_id: str, token_hash: bytes, bound_cert_ref: str,
                  pinned_parent_spki: str, protocol_version: str) -> None:
    """רושם את אישור-האב היחיד על המשני, בטרנזאקציה אחת עם אכיפת אב-יחיד.

    ה-check-then-insert יושב תחת אותה נעילת-כתיבה, וה-PK ה-singleton הוא
    השובר השני ברמת ה-DB (עיקרון 5: לא סומכים על הבדיקה לבדה תחת מקביליות)."""
    with _write_lock, writing(conn):
        if conn.execute(
            "SELECT 1 FROM parent_credentials WHERE singleton = 1"
        ).fetchone() is not None:
            raise ParentAlreadyEnrolledError("כבר קיים אב רשום")
        try:
            conn.execute(
                "INSERT INTO parent_credentials (singleton, parent_id, token_hash,"
                " bound_cert_ref, pinned_parent_spki, protocol_version, enrolled_at)"
                " VALUES (1, ?, ?, ?, ?, ?, ?)",
                (parent_id, token_hash, bound_cert_ref, pinned_parent_spki,
                 protocol_version, now_iso()),
            )
        except sqlite3.IntegrityError as exc:
            raise ParentAlreadyEnrolledError("כבר קיים אב רשום") from exc


def rebind_parent_cert(conn, *, bound_cert_ref: str) -> None:
    """‏#1122: האב חידש תעודה על **אותו** מפתח — מעדכנים את הכריכה לתעודה
    החדשה. ה-SPKI המוצמד והטוקן אינם משתנים; זו לא רשומת-אב חדשה.
    ‏0 שורות שעודכנו = אין אב רשום = כשל בקול, לא "כנראה בסדר" (עיקרון 5)."""
    with _write_lock, writing(conn):
        cur = conn.execute(
            "UPDATE parent_credentials SET bound_cert_ref = ? WHERE singleton = 1",
            (bound_cert_ref,))
        if cur.rowcount != 1:
            raise ValueError("אין אב רשום לעדכון הכריכה")


def record_parent_contact(conn) -> None:
    """Record positive contact only after the shared parent guard succeeded."""
    with _write_lock, writing(conn):
        cur = conn.execute(
            "UPDATE parent_credentials SET last_contact_at = ? WHERE singleton = 1",
            (now_iso(),))
        if cur.rowcount != 1:
            raise ValueError("אין אב רשום לעדכון מגע")


#: ‏#883: צורת מזהה המשני — ``sn_`` + 16 הקסה (``node_id_from_spki``). זה
#: גם שם קובץ הטוקן ב-``secondaries/``, ולכן הצורה נאכפת כאן ולא רק
#: בנקודת הגזירה.
_NODE_ID_RE = re.compile(r"^sn_[0-9a-f]{16}$")


def valid_node_id(value: object) -> bool:
    return isinstance(value, str) and _NODE_ID_RE.fullmatch(value) is not None


def enroll_node(conn, user: tuple[str, str], *, label: str, base_url: str,
                node_id: str, pinned_spki: str, client_cert_ref: str,
                credential_ref: str, protocol_version: str,
                group_id: str | None = None) -> str:
    """רושם משני חדש על הראשי אחרי שה-pairing הצליח והטוקן נכתב לקובץ.

    ‏admin+standalone נאכף (כמו כל ניהול). ה-SPKI המוצמד וההפניה
    לתעודת-הלקוח נשמרים כחומר האמון; הטוקן עצמו **אינו** ב-DB — רק
    ``credential_ref`` לקובץ ה-0600 שכבר נכתב. ‏#883: ``node_id`` חייב
    להיות בצורת ``sn_<16 hex>`` — הוא נגזר מ-SPKI, לא מהצהרה."""
    assert_can_manage_nodes(conn, user)
    label = check_label(label)
    if not valid_node_id(node_id):
        raise ValueError(f"מזהה משני לא תקין: {node_id!r}")
    if group_id and conn.execute(
        "SELECT 1 FROM storage_node_groups WHERE id = ?", (group_id,)
    ).fetchone() is None:
        raise ValueError("קבוצה לא קיימת")
    rid = uuid.uuid4().hex
    with _write_lock, writing(conn):
        conn.execute(
            "INSERT INTO storage_nodes (id, label, base_url, group_id,"
            " tls_fingerprint, node_id, protocol_version, pinned_spki,"
            " client_cert_ref, credential_ref, enrolled_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, label, base_url, group_id, None, node_id, protocol_version,
             pinned_spki, client_cert_ref, credential_ref, now_iso()),
        )
    journal(conn, "storage_node_enroll", f"{rid} node_id={node_id}", user[0])
    return rid
