"""משיכת יוניקאסט — תחנה בודדת שמושכת אימג' ב-HTTP (‏issue #60).

זה המסלול של אשף השחזור (זרימה 13.2): אדם ניגש למחשב אחד, בוחר אימג',
מקליד ERASE, והמכונה מושכת את קבצי המחיצות מ-`/api/v1/images/...`. אין
כאן `udp-sender` ואין כתובת מולטיקאסט — ולכן אין כאן גם את החריץ היחיד
שהמולטיקאסט תופס: כמה משיכות רצות יחד, וגם בזמן שסבב כיתה משדר.

למה בכל זאת session ולא "כלום": משיכה היא **עבודה אמיתית על השרת** —
רוחב פס וקריאה מהדיסק. עד היום היא לא הייתה רשומה בשום מקום, ומפעיל
שהסתכל בקונסולה ראה שרת פנוי בזמן ששתי תחנות מושכות ממנו. כ-session
היא מקבלת בחינם את מה שכל עבודה אחרת מקבלת: דיווחי התקדמות (ממשק 4),
שורת יומן, ותצוגה במבט-העל.

המשיכה נפתחת כבר במצב `running` — אין למי לחכות, התחנה מושכת בעצמה.
היא נסגרת כשהיא מסתיימת בהצלחה (`closed`), ביד של מפעיל מהקונסולה, או
כ-`failed` עם סיבה — כשהמכונה דיווחה כישלון, או כששתקה מעבר לתקרה
(‏#1573). "נכשל", "הסתיים" ו"רץ" הם שלושה מצבים שונים: עד #1573 משיכה
שנכשלה נשארה `running` — הקונסולה הציגה אותה "מושכת", ושרת סירב לכל
משיכה חדשה של אותה תחנה ב"כבר מושכת".
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

from server.system import clockstep
from server.db import journal, now_iso
from server.agent.reports import FINAL
from server.rounds.sessions import PULL_PREFIX, TAKEN, UNICAST, SessionError, SessionStore

log = logging.getLogger(__name__)

#: מעל כמה שניות בלי **שום דיווח** מהמכונה משיכה נסגרת כ-`failed` (#1573).
#: הסוכן מדווח כל ~2ש' לכל אורך המשיכה (‏`progress_loop`, גם בהנחת
#: הדרייברים), ודיווח בודד יכול להימשך ~40ש' (‏`curl --max-time 10
#: --retry 3`). החדר מפקיע אחרי 180ש' (‏`room.LOST_SECONDS`, אותו קצב);
#: כאן השוליים רחבים יותר בכוונה, כי משיכות אינן נסגרות בעליית שרת
#: (‏`reconcile` נוגע בשידור בלבד) — עדכון או אתחול של השרת בן כמה דקות
#: עוצר את הדיווחים של משיכה בריאה, ואסור שיפיל אותה. נגזר, לא נמדד.
SILENT_SECONDS = 600


def _roster_macs(row: sqlite3.Row) -> list[str]:
    raw = row["roster_json"]
    return json.loads(raw) if raw else []


def active_for(store: SessionStore, mac: str) -> sqlite3.Row | None:
    """המשיכה הפעילה של המכונה הזו, אם יש.

    שתי משיכות של אותה מכונה היו כותבות לאותו דיסק בו-זמנית — וזו
    התנגשות אמיתית, בשונה משתי תחנות שונות. נחסמת במפורש, עם הסבר.

    הזיהוי הוא לפי ה-roster ולא לפי `session_members`: שורת החבר נכתבת
    **אחרי** ה-INSERT של הסבב, ולכן בדיקה שנשענת עליה מפספסת בדיוק
    משיכה שנפתחה ברגע זה (#104). ה-roster נכתב באותו INSERT עצמו.
    שורה ישנה בלי roster (התקנה מלפני העמודה) נבדקת עדיין לפי החבר —
    "אין ראיה ב-roster" אינו "אין משיכה".
    """
    for row in store.active_pulls():
        macs = _roster_macs(row)
        if mac in macs or (not macs and store.is_member(row["id"], mac)):
            return row
    return None


def _is_the_same_request(store: SessionStore, row: sqlite3.Row,
                         image_id: str) -> bool:
    """האם המשיכה הפעילה היא אותה בקשה בדיוק, שאיש עוד לא דיווח עליה.

    הסוכן שולח עם `curl --retry 3 --max-time 10`, ופקיעת `--max-time`
    היא שגיאה חולפת לעניין `--retry`: שרת שקיבל בקשה, פתח משיכה וענה
    ב-11 שניות מקבל אותה **שוב**. אותה תחנה + אותו אימג' + אפס דיווחים
    = זו אותה משיכה, ולכן מחזירים לה את אותו `id` במקום לפתוח שנייה או
    לחסום אותה מפני עצמה.

    משיכה שכבר דיווחה התקדמות אינה "אותה בקשה" אלא עבודה קיימת, והחסימה
    עליה נשארת. משיכה שהסתיימה או נכשלה כבר אינה כאן: ‏`open_pull` מריץ
    את `sweep` לפני הבדיקה, וזה סוגר אותה (‏#1573).

    ‏`all` על רשימה ריקה הוא בכוונה: אין חבר = התהליכון שפתח עדיין לא
    הספיק לכתוב אותו, ואין דיווח שאפשר לאבד.
    """
    if row["image_id"] != image_id:
        return False
    return all(
        not member["done"] and member["state"] == "waiting"
        and not member["bytes_written"]
        for member in store.members(row["id"])
    )


def _resume_or_refuse(conn: sqlite3.Connection, store: SessionStore,
                      row: sqlite3.Row, mac: str, image_id: str) -> str:
    if not _is_the_same_request(store, row, image_id):
        raise SessionError(TAKEN[UNICAST])
    journal(conn, "pull_retry", f"{mac} — {row['id']}")
    return row["id"]


def open_pull(conn: sqlite3.Connection, store: SessionStore, mac: str,
              group_id: str, image_id: str, opened_by: str) -> str:
    """פותחת משיכה ורושמת את המכונה כחברה בה מיד.

    ההרשמה כאן ולא ב-hello: אין hello שיצרף אותה — המשיכה כבר רצה, וגם
    הדיווח הראשון שלה חייב למצוא חבר קיים (`reports.ingest`).

    שני מסלולים מגיעים למשיכה קיימת: הבדיקה המקדימה, ו-`store.open`
    שנדחה על האינדקס הייחודי מפני שתהליכון אחר כתב בין השניים (#104).
    שניהם נגמרים באותו מקום — אותו `id` אם זו אותה בקשה, ואחרת סירוב.

    ‏`sweep` קודם (#1573): משיכה קודמת שהסתיימה, נכשלה או שתקה אינה
    "כבר מושכת". בלעדיו היא נסגרה רק כשמישהו פתח את הקונסולה.
    """
    sweep(store)
    existing = active_for(store, mac)
    if existing is not None:
        return _resume_or_refuse(conn, store, existing, mac, image_id)
    try:
        session_id = store.open(
            group_id, image_id, PULL_PREFIX, expected_clients=1,
            opened_by=opened_by, roster=[mac], kind=UNICAST,
        )
    except SessionError:
        existing = active_for(store, mac)
        if existing is None:
            raise           # לא המרוץ — שגיאת ולידציה אמיתית
        return _resume_or_refuse(conn, store, existing, mac, image_id)
    conn.execute(
        "INSERT INTO session_members (session_id, mac, state, updated_at)"
        " VALUES (?, ?, 'waiting', ?)",
        (session_id, mac, now_iso()),
    )
    conn.commit()
    return session_id


def _parse(stamp: str | None) -> datetime | None:
    """חותמת ISO **עם אזור זמן**, או `None`. חותמת בלי אזור (שורה ישנה או
    ידנית) אינה נקראת: ‏naive מול aware זורק `TypeError`, והוא היה מפיל את
    `/overview` ואת `open_pull` עצמם (‏`now_iso` כותב תמיד עם אזור)."""
    try:
        parsed = datetime.fromisoformat(stamp) if stamp else None
    except (TypeError, ValueError):
        return None
    return parsed if parsed is not None and parsed.tzinfo is not None else None


def _failure(row: sqlite3.Row, members: list[sqlite3.Row],
             now: datetime) -> str | None:
    """למה המשיכה הזו נכשלה — או `None` אם אין ראיה שנכשלה.

    שני מקורות בלבד: המכונה **אמרה** שנכשלה (כל החברים במצב סופי ולא
    כולם `done`), או שהמכונה **שתקה** מעל `SILENT_SECONDS` מאז הדיווח
    האחרון שלה (‏`updated_at` זז על כל דיווח שאינו סופי — כאן הוא בדיוק
    הראיה, בניגוד לקיפאון בייטים, ‏`reports.stamp_movement`). חבר שעוד
    לא נכתב: נמדד מפתיחת המשיכה. חותמת שאינה נקראת אינה "שתיקה" — לא
    נמדד, ולא מכריזים (אותה הכרעה כמו `room._mark_lost`).
    """
    if members and all(m["state"] in FINAL for m in members):
        errors = "; ".join(m["error"] for m in members if m["error"])
        return errors or "המכונה דיווחה " + ", ".join(m["state"] for m in members)
    stamps = [_parse(m["updated_at"]) for m in members] or [_parse(row["created_at"])]
    if None in stamps:
        return None
    last = max(stamps)
    if (now - last).total_seconds() <= SILENT_SECONDS:
        return None
    return f"אין דיווח מהמכונה מאז {last.astimezone().strftime('%H:%M')}"


def sweep(store: SessionStore) -> None:
    """סוגרת משיכות שהסתיימו — בהצלחה (`closed`) או בכישלון (`failed`).

    הצלחה רק על ראיה חיובית: כל החברים `done` (ממשק 4). כישלון אינו
    נעלם: הסבב עובר ל-`failed` עם הסיבה ב-`failed_reason`, ושורת
    `pull_failed` ביומן נושאת אותה (עיקרון 4/5) — לצד `client_failed`
    שהדיווח עצמו כבר רשם. ‏`store.fail` מותנה במצב, ולכן שני תהליכונים
    (הקונסולה, השעון, ‏`open_pull`) רושמים שורה אחת בלבד.
    """
    now = datetime.now(timezone.utc)
    clockstep.guard(store.conn, store, now)       # #1947: before `_failure` reads a stamp
    for row in store.active_pulls():
        members = store.members(row["id"])
        if members and all(m["done"] for m in members):
            store.close(row["id"], "", event="pull_done")
            continue
        reason = _failure(row, members, now)
        if reason is not None and store.fail(row["id"], reason):
            journal(store.conn, "pull_failed", f"{row['id']} — {reason}")


def background_sweep(store: SessionStore) -> bool:
    """‏`sweep` משעון-הרקע (`app._room_clock`) — משיכה שאיש אינו צופה בה
    חוסמת אחרת גם את האתחול הלילי ואת עדכון השרת. כמו `room.sweep`:
    לעולם אינה זורקת, אבל כשל נרשם ביומן ולא נבלע."""
    try:
        sweep(store)
        return True
    except Exception as exc:      # noqa: BLE001 — לא מפיל את לולאת הרקע
        log.exception("pull background sweep failed")
        try:
            journal(store.conn, "pull_sweep_failed",
                    " ".join(f"{type(exc).__name__}: {exc}".split())[:200])
        except Exception:         # noqa: BLE001 — גם היומן לא מפיל את הלולאה
            log.exception("pull sweep failure was not journaled")
        return False


def journal_refusal(conn: sqlite3.Connection, mac: str, reason: str) -> None:
    """סירוב לפתוח משיכה נרשם. תחנה שאינה מצליחה למשוך היא תקלה שמישהו
    יחפש בקונסולה, ולא אירוע שקורה בשקט."""
    journal(conn, "pull_refused", f"{mac} — {reason}")
