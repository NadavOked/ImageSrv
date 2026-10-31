"""מי פונה לשרת מרשת שאינה וילן ההפצה (‏#137).

ההכרעה של נדב: מכונה שקוראת מרשת אחרת נצבעת **אדום** בבריאות, בלי
ירוק-או-לא, עד שהיא מפסיקה לפנות. גם המקרה הלגיטימי — הוא עומד ליד
מחשב ומושך אימג' ביד — מוצג אדום, כי הוא אירוע שראוי לתשומת לב;
והמקרה שהמשימה נכתבה בשבילו הוא מחשב בנייה או מחשב שיכפול שחובר
לשקע הלא נכון, וייראה תקין ולא יעבוד.

**זו התראה ולא שער.** הפונקציות כאן אינן נוגעות בתשובה שנשלחת למכונה
ואינן זורקות לעולם: מכונה לא מוכרת ממשיכה לקבל ‏`known:false` ואתחול
מהדיסק בדיוק כמו קודם (עיקרון 1).

**וזו אינה לולאה.** ‏`agent_loops` ממשיך להוציא את המקרה הזה מפורשות,
ובצדק: מחוץ לווילן ההפצה השרת אינו מגיש את שרשרת האתחול ואין לו ראיה
שהמכונה עברה בתפריט שלו (‏#42). שתי השורות נפרדות במסך ובקוד — הנימוק
שם נכון, והמסקנה שונה.

**תצוגה חיה, לא ארכיון.** שורה יורדת אחרי חלון שתיקה, והמונה יורד
איתה. הארכיון הוא היומן.
"""

from __future__ import annotations

import ipaddress
import logging
import sqlite3
from datetime import datetime, timedelta

from server.agent.agent_loops import SILENCE_SECONDS
from server.db import _write_lock, journal, now_iso, writing

log = logging.getLogger("imagesrv.foreign_vlan")

__all__ = ["SILENCE_SECONDS", "current", "note", "vlan_checks", "where"]


def _cutoff(now: str) -> str:
    """החותמת שמתחתיה שתיקה מורידה את השורה.

    ההשוואה על מחרוזות ISO ולא על מספרים, מפני ש-`now_iso` כותב תמיד
    UTC ברוחב קבוע — ולכן סדר לקסיקוגרפי הוא סדר כרונולוגי.
    """
    return (datetime.fromisoformat(now)
            - timedelta(seconds=SILENCE_SECONDS)).isoformat(timespec="seconds")


def where(scope: dict | None) -> str | None:
    """הכתובת המקומית שעליה הבקשה התקבלה — כלומר מאיזו רשת היא הגיעה.

    אותו מקור בדיוק שממנו נגזר ‎`off_deploy_vlan`: ‏`scope["server"]`,
    ה-sockname של החיבור. מכוון: *לא* כותרת Host, שהיא קלט של הלקוח
    ולכן תאפשר לתחנה להכריז על עצמה כ"בתוך הווילן".

    ‏None פירושו שאי אפשר לומר מאיזו רשת — ואז אין מה להציג.
    """
    try:
        host, _port = scope.get("server")
        return str(ipaddress.ip_address(host))
    except Exception:  # noqa: BLE001 — כאן זו בדיוק הכוונה
        return None


def note(conn: sqlite3.Connection, mac: str, scope: dict | None,
         *, off_vlan: bool, now: str | None = None) -> int | None:
    """רושם פנייה מרשת זרה ומחזיר את מספרה בחלון הנוכחי.

    ‏None = אין מה לרשום, או שלא ניתן לאשר שנרשם. ניטור בלבד.
    """
    try:
        if not off_vlan:
            return None
        address = where(scope)
        if address is None:
            # ‏`off_deploy_vlan` מחזיר True רק כששתי הכתובות נקראו, ולכן
            # לא אמורים להגיע לכאן. אם בכל זאת — אין מה להציג, ובפרט
            # אסור להמציא "רשת לא ידועה" ולהראות אותה כעובדה (עיקרון 5).
            # codeql[py/log-injection] false positive: ה-MAC עבר normalize_mac (hex ונקודתיים בלבד) (#291)
            log.error("off-vlan hello from %s but its network is unreadable", mac)
            return None
        return _count(conn, mac, address, now or now_iso())
    except Exception:  # noqa: BLE001 — ניטור לא מפיל hello
        # codeql[py/log-injection] false positive: ה-MAC עבר normalize_mac (hex ונקודתיים בלבד) (#291)
        log.exception("off-vlan contact from %s was not recorded", mac)
        return None


def _count(conn: sqlite3.Connection, mac: str, address: str,
           ts: str) -> int | None:
    """ה-UPSERT, כולל איפוס החלון — בכתיבה אחת, בלי מרוץ קריאה-כתיבה.

    שתיקה ארוכה מ-`SILENCE_SECONDS` מתחילה ספירה חדשה באותה שורה.
    הכתובת נדרסת תמיד: מה שמוצג הוא מהיכן היא פונה **עכשיו**.

    ‏`_write_lock` ו-`writing` הם אותם שני מנגנונים שתוקנו ב-#272 על
    ‏`net_seen` ובמסלול ה-hello ב-#356, וזה **אותו מסלול hello בדיוק**
    (#518). התור מונע הרעבה בין תהליכוני uvicorn, ו-`writing` מבטיח
    שכתיבה שנכשלה לא תשאיר ``BEGIN`` פתוח על החיבור — ה-`except` שב-`note`
    בולע כדי שניטור לא יפיל hello, וחיבור מורעל היה הופך את הבליעה הזו
    למה ש**מפיל** את ה-hello הבא. הנעילה עוטפת את הכתיבה **בלבד**:
    קריאת האימות ורישום היומן שאחריה יושבים מחוצה לה, כי `journal` נוטל
    את אותה נעילה בעצמו והיא ``Lock`` ולא ``RLock``.
    """
    cutoff = _cutoff(ts)
    with _write_lock, writing(conn):
        conn.execute(
            "INSERT INTO off_vlan_contacts (mac, address, hits, first_at, last_at)"
            " VALUES (?, ?, 1, ?, ?)"
            " ON CONFLICT (mac) DO UPDATE SET"
            "   address  = excluded.address,"
            "   hits     = CASE WHEN last_at >= ? THEN hits + 1 ELSE 1 END,"
            "   first_at = CASE WHEN last_at >= ? THEN first_at ELSE excluded.first_at END,"
            "   last_at  = excluded.last_at",
            (mac, address, ts, ts, cutoff, cutoff),
        )
    # ראיה חיובית: הערך נקרא בחזרה. שורה שאינה שם, או שהחותמת בה אינה
    # זו שנכתבה, פירושה שהרישום לא קרה — ולא שהוא יצא אחד (עיקרון 5).
    row = conn.execute(
        "SELECT hits, last_at FROM off_vlan_contacts WHERE mac = ?", (mac,)
    ).fetchone()
    if row is None or row["last_at"] != ts:
        # codeql[py/log-injection] false positive: ה-MAC עבר normalize_mac (hex ונקודתיים בלבד) (#291)
        log.error("off-vlan contact from %s was not recorded", mac)
        journal(conn, "off_vlan_unverified", mac)
        return None
    if row["hits"] == 1:
        # פעם אחת לחלון, ולא לכל hello. היומן הוא הארכיון: הוא מה
        # שמאפשר לשאול "המחשב הזה עשה את זה גם שלשום" הרבה אחרי
        # שהשורה ירדה מהמסך.
        journal(conn, "off_vlan_contact", f"{mac} from {address}")
    return int(row["hits"])


def current(conn: sqlite3.Connection, now: str | None = None) -> list[dict]:
    """מי פונה מרשת זרה **עכשיו** — שורה אחת למחשב, האחרון בראש.

    ירידה מהרשימה אינה "נפתר". אין אירוע "נרפא", יש רק היעדר אירוע:
    מחשב כבוי שותק בדיוק כמו מחשב שהועבר לשקע הנכון, ושניהם ירדו
    מכאן בלי שנדע במה מדובר. מה שנשאר בינתיים הוא היומן.
    """
    moment = now or now_iso()
    at = datetime.fromisoformat(moment)
    return [
        {
            "mac": row["mac"],
            # "כיתה ושם" כשהמכונה רשומה; אחרת MAC, וגם הוא מוצג.
            "name": f'{row["label"]}-{row["suffix"]}' if row["suffix"] else None,
            "address": row["address"],
            "hits": int(row["hits"]),
            "first_at": row["first_at"],
            "last_at": row["last_at"],
            "silent_seconds": max(
                0, int((at - datetime.fromisoformat(row["last_at"])).total_seconds())
            ),
        }
        for row in conn.execute(
            "SELECT v.mac, v.address, v.hits, v.first_at, v.last_at,"
            "       m.suffix, g.label"
            " FROM off_vlan_contacts v"
            " LEFT JOIN machines m ON m.mac = v.mac"
            " LEFT JOIN groups g ON g.id = m.group_id"
            " WHERE v.last_at >= ? ORDER BY v.last_at DESC, v.mac",
            (_cutoff(moment),),
        )
    ]


def vlan_checks(contacts: list[dict] | None, deploy_vlan: str) -> list[dict]:
    """מי מדבר עם השרת מרשת שאינה וילן ההפצה — שורה לכל מחשב (#137).

    **רק אדום.** אין כאן דירוג בין "לגיטימי" ל"תקלה", כי לשרת אין ממה
    להסיק אותו: אותה פנייה בדיוק היא נדב שעומד ליד מחשב ומושך אימג'
    ביד, ומחשב שיכפול שחובר לשקע הלא נכון וייראה תקין עד שיתברר שאינו
    עובד. ההכרעה היא שהאירוע נראה בשני המקרים.

    השורה אומרת **מאיזו רשת** נפתחה הפנייה ומה מצופה — כי בלי זה
    "משהו לא בסדר ברשת" הוא בדיוק המשפט ששולח לחפש במקום הלא נכון.

    ‏None פירושו שהרשימה לא נקראה, וזו שורה **אדומה** ולא ריקה. וגם
    הירוקה נזהרת בלשונה: היא אומרת מה נמדד — לא הגיעה פנייה כזאת
    בעשר הדקות האחרונות — ולא "כל המחשבים בשקע הנכון". מחשב כבוי
    שותק בדיוק כמו מחשב שהועבר, ואין אירוע שאומר "נרפא".

    ‏(#354) עברה הנה מ-`server/system/health.py` — זה המודול שהיא באמת שייכת
    אליו. `check` ו-`_last_seen` נשארים ב-health.py כי הוא כבר מייבא
    את המודול הזה; ייבוא בכיוון ההפוך בראש הקובץ היה יוצר מעגל.
    """
    from server.system.health import check, _last_seen  # noqa: PLC0415 — נמנע ממעגל ייבוא
    label = "מחשבים שפונים מרשת אחרת"
    if contacts is None:
        return [check("off_vlan", label, "bad",
                      "רשימת הפניות מרשת אחרת לא נקראה — אין לדעת אם מחשב "
                      "מדבר עם השרת מחוץ לווילן ההפצה")]
    if not contacts:
        return [check("off_vlan", label, "ok",
                      f"אף מחשב לא פנה לשרת מחוץ לווילן ההפצה ב-"
                      f"{SILENCE_SECONDS // 60} הדקות האחרונות. "
                      "מחשב כבוי שותק גם הוא — ירידה מהרשימה אינה \"תוקן\"")]
    rows = [check("off_vlan", label, "bad",
                  f"{len(contacts)} מחשבים פונים לשרת מרשת שאינה וילן ההפצה — "
                  "בדקו לאיזה שקע הם מחוברים")]
    rows += [
        check(f"off_vlan:{one['mac']}", one["name"] or one["mac"], "bad",
              f"פנתה מ-{one['address']} · וילן ההפצה הוא {deploy_vlan} · "
              f"{one['hits']} פניות · {_last_seen(one['silent_seconds'])}")
        for one in contacts
    ]
    return rows
