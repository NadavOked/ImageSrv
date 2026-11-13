"""התראות במייל מהשרת — #1751 (נדב 18/10: "כמו ה-Notification Center של QNAP").

השרת שולח מייל כשיש עדכון, כשהוא עלה מחדש (כולל "הייתי למטה מ-X עד Y"),
אחרי הפעלה מחדש אוטומטית, ובתקלות: סבב שנכשל, דיסק אדום, מקום פנוי נמוך,
ושרת ראשי/משני שלא עונה. **איך** שולחים ו**למי** — ``email_config`` (חשבונות
שליחה וכללים); OAuth — ``email_oauth``; הטקסט — ``email_text``. כאן: התור,
השליחה, מקורות האירועים ותהליכון הרקע.

**מקור האירועים הוא היומן (`journal`) ולא קריאות חדשות בכל אתר.** התהליכון
קורא את היומן מהשורה האחרונה שראה (סמן ב-``settings``) וממפה אירועים לסוגים
(``JOURNAL_KINDS``). שלושה אירועים נכתבים ליומן מכאן ועוברים באותו מסלול:
``server_started`` (מהדופק שנשמר לדיסק), ``update_available`` (מבדיקת העדכונים
האחרונה) ו-``peer_unreachable``/``peer_back`` (מחותמות המגע הבין-שרתי).
``low_space`` (מדגימת המקום הפנוי במחיצת הנתונים, ``Watcher._check_space``) — פעם
אחת לכל ירידה מתחת לרזרבה. ``smart_red`` נכתב ב-``disk_events`` (דיסק ש-SMART שלו
אדום, פעם אחת לכל דיסק שהאדים) ועובר כאן כ"דיסק אדום", כמו ``disk_failure``.

כך **שום endpoint ושום פעולה אינם נחסמים**: מי שיוצר אירוע רק כותב שורת יומן.
השליחה — SMTP עם תקרת זמן קשיחה — רצה רק בתהליכון הרקע (``Watcher``).

**איחוד והגבלת קצב, לכל (כלל, סוג, שפה):** לכל צירוף כזה לכל היותר מייל
ממתין אחד (אינדקס חלקי ב-DB), ואירועים נוספים מצטרפים אליו. מייל יוצא לא לפני
``GATHER_SECONDS`` מהאירוע הראשון שבו, ולא לפני ``RATE_WINDOW_SECONDS`` מהמייל
הקודם באותו צירוף.

**הצלחה = 250 על DATA (עיקרון 5).** כל כשל נרשם ביומן עם הקוד, והמייל נשאר
בתור עם ניסיונות חוזרים ו-backoff עד ``MAX_ATTEMPTS``, ואז ויתור גלוי.
STARTTLS שהשרת אינו מציע — כשל, לא שליחה גלויה.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import smtplib
import socket
import ssl
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.policy import SMTP as SMTP_POLICY
from email.utils import formatdate, make_msgid
from pathlib import Path

from server import db
from server.interserver import auth as interserver_auth
from server.interserver import storage_nodes
from server.net import ports
from server.storage import disk_reserve
from server.system import email_config, email_oauth, email_text
from server.system import timezone as tzsetting
from server.db import get_setting, journal, set_setting
from server.system.email_text import t

log = logging.getLogger("imagesrv.email_alerts")

CURSOR_KEY = "email_alerts_journal_cursor"
NOTIFIED_UPDATE_KEY = "email_alerts_update_notified"
PEER_STATE_KEY = "email_alerts_peer_state"
LOW_SPACE_KEY = "email_alerts_low_space"
HEARTBEAT_FILE = "alive.heartbeat"

#: אירוע ביומן → (סוג התראה, תנאי על ה-detail או ``None``). הטקסט בשורת המייל
#: הוא ``item.<אירוע>`` בקטלוג. ``auto_reboot`` נרשם גם על כל דחייה ("נדחה — …"
#: כל 10 דקות); רק ביצוע, ויתור ותקלה הם התראה. התנאי קורא את **היומן**
#: (שנכתב ב-``auto_reboot.py``), לא טקסט של מייל.
JOURNAL_KINDS: dict[str, tuple[str, object]] = {
    "update_available": ("update_available", None),
    "update_apply_done": ("update_installed", None),
    "update_apply_failed": ("update_failed", None),
    "server_started": ("server_started", None),
    "auto_reboot": ("auto_reboot", lambda d: d.startswith(("השרת אותחל", "דולג", "תקלה"))),
    "room_failed": ("round_failed", None),
    "disk_failure": ("disk_red", None),
    "smart_red": ("disk_red", None),
    "low_space": ("low_space", None),
    "peer_unreachable": ("peer", None),
    "peer_back": ("peer", None),
}

#: תקרת זמן **לכל השליחה** — חיבור, TLS, אימות, נמענים ו-DATA יחד. כל צעד
#: מקבל רק את מה שנשאר מהתקרה (``_Deadline``), ולא timeout משלו.
TIMEOUT_SECONDS = 20.0
GATHER_SECONDS = 60
RATE_WINDOW_SECONDS = 15 * 60
MAX_ATTEMPTS = 8
BACKOFF_BASE_SECONDS = 60
BACKOFF_CAP_SECONDS = 3600
MAX_ITEMS = 50
KEEP_FINISHED = 200
TICK_SECONDS = 15.0
HEARTBEAT_SECONDS = 60
#: הדופק של הראשי למשניים הוא 150 שניות (``storage_heartbeat``) — ארבעה
#: דופקים שהוחמצו לפני שאומרים "לא עונה".
PEER_SILENT_SECONDS = 600
#: ``low_space`` נדרך שוב רק כשהמקום חזר לרזרבה **ועוד** זה. בלי השוליים, מחיצה
#: שעומדת על הקו — ``StreamGuard`` של הקליטה עוצר בדיוק ברזרבה, journald מוסיף
#: ומנקה — הייתה "חוצה" אותו בכל דגימה, וכל חציה הייתה מייל.
LOW_SPACE_CLEAR_MARGIN = 1024 ** 3
JOURNAL_BATCH = 500


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(ts) -> datetime | None:
    try:
        when = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else None


@contextmanager
def _tx(conn):
    """כתיבה דרך המסלול של db: ``_settle`` → ``_write_lock`` → ``writing`` (#54, #457).
    לעולם לא קוראים ל-``journal`` בתוך הבלוק — הנעילה אינה רה-אנטרנטית."""
    db._settle(conn)
    with db._write_lock, db.writing(conn):
        yield conn


# --- זמן מקומי לשורות המייל ---------------------------------------------------

def local_text(conn, when: datetime | None) -> str:
    """"18/10/2026 14:32" באזור הזמן של השרת (#1447). אזור שלא נטען — UTC, ונאמר."""
    lang = email_text.language(conn)
    if when is None:
        return t(lang, "time_unknown")
    try:
        from zoneinfo import ZoneInfo  # noqa: PLC0415
        zone, suffix = ZoneInfo(tzsetting.zone_name(conn)), ""
    except Exception:                                          # noqa: BLE001
        zone, suffix = timezone.utc, t(lang, "utc_suffix")
    return when.astimezone(zone).strftime("%d/%m/%Y %H:%M") + suffix


def _duration_text(lang: str, seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return t(lang, "dur.lt_minute")
    if minutes < 120:
        return t(lang, "dur.minutes", minutes=minutes)
    hours, rest = divmod(minutes, 60)
    return (t(lang, "dur.hours_minutes", hours=hours, minutes=rest) if rest
            else t(lang, "dur.hours", hours=hours))


# --- השליחה -------------------------------------------------------------------

@dataclass
class SendResult:
    ok: bool
    code: int | None
    message: str
    refused: dict = field(default_factory=dict)

    def summary(self) -> str:
        head = f"קוד {self.code}" if self.code is not None else "בלי קוד SMTP"
        return f"{head}: {self.message}"


class _Deadline:
    def __init__(self, seconds: float):
        self.seconds = seconds
        self.end = time.monotonic() + seconds

    def left(self) -> float:
        rest = self.end - time.monotonic()
        if rest <= 0:
            raise TimeoutError(f"תם הזמן ({self.seconds:g} שניות)")
        return rest

    def arm(self, smtp) -> None:
        if smtp.sock is not None:
            smtp.sock.settimeout(self.left())


def _text(resp) -> str:
    return resp.decode("utf-8", "replace") if isinstance(resp, bytes) else str(resp)


def _failure_text(exc: BaseException, host, port) -> str:
    """כשל בצד שלנו (רשת, TLS, זמן) — ניסוח קבוע בעברית; ``str(exc)`` הגולמי
    (errno, שם מחלקה, פרטי OpenSSL) נכתב ליומן השרת ולא לתשובה (CodeQL
    py/stack-trace-exposure, PR ציבורי #54). תשובת **שרת הדואר** עצמה אינה עוברת
    כאן — היא שימושית למפעיל ועוברת מנוקה (``email_config.public_text``)."""
    log.warning("email alerts: send via %s:%s failed: %s: %s", host, port, type(exc).__name__, exc)
    where = f"{host}:{port}"
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        text = f"שרת הדואר {where} אינו מציע STARTTLS — לא נשלח בטקסט גלוי"
    elif isinstance(exc, ssl.SSLCertVerificationError):
        text = f"תעודת ה-TLS של שרת הדואר {where} לא אומתה"
    elif isinstance(exc, ssl.SSLError):
        text = f"TLS נכשל מול שרת הדואר {where}"
    elif isinstance(exc, TimeoutError):
        text = f"תם הזמן מול שרת הדואר {where}"
    elif isinstance(exc, socket.gaierror):
        text = f"שם שרת הדואר {host} לא נמצא (DNS)"
    elif isinstance(exc, ConnectionRefusedError):
        text = f"שרת הדואר {where} סירב לחיבור"
    elif isinstance(exc, smtplib.SMTPServerDisconnected):
        text = f"שרת הדואר {where} ניתק את החיבור"
    elif isinstance(exc, OSError):
        text = f"שגיאת רשת מול שרת הדואר {where}"
    else:
        text = f"השליחה דרך {where} נכשלה"
    return text + " — הפרטים ביומן השרת"


def send(acc: dict, recipients: list[str], msg: EmailMessage, *, password: str | None = None,
         oauth_token: str | None = None, timeout: float = TIMEOUT_SECONDS) -> SendResult:
    """שולח מייל אחד מהחשבון ``acc``. ``ok`` רק כש-DATA נענה ב-250 — ראיה חיובית
    (עיקרון 5). ``refused``: נמענים שנדחו כשאחרים התקבלו (המייל יצא אליהם).
    אימות: ``password`` — ``AUTH`` רגיל; ``oauth`` — ``AUTH XOAUTH2`` עם האסימון.
    חשבון עם אימות בלי סוד — לא מתחבר בכלל."""
    auth = acc.get("auth", "none")
    if auth == "password" and not password:
        return SendResult(False, None, "אין סיסמה לחשבון — לא התחברנו לשרת הדואר")
    if auth == "oauth" and not oauth_token:
        return SendResult(False, None, "אין אסימון OAuth לחשבון — לא התחברנו לשרת הדואר")
    deadline = _Deadline(timeout)
    # תשובות שרת הדואר עוברות למפעיל (בדיקה, היסטוריה) — מנוקות, מקוצרות, ובלי
    # הסוד אם השרת הדהד אותו.
    hidden = [password, oauth_token]
    if auth == "oauth":
        hidden.append(email_oauth.xoauth2(acc["username"] or acc["from_addr"], oauth_token))

    def reply(resp) -> str:
        return email_config.public_text(_text(resp), hidden)

    context = ssl.create_default_context()
    host, port, security = acc["host"], int(acc["port"]), acc["security"]
    smtp = None
    # שם ל-EHLO במפורש: בלעדיו smtplib קורא ל-``socket.getfqdn()`` — חיפוש DNS
    # שאינו תחת ה-timeout של הסוקט, כלומר מחוץ לתקרה.
    helo = socket.gethostname() or "imagesrv"
    try:
        if security == "tls":
            smtp = smtplib.SMTP_SSL(host, port, local_hostname=helo,
                                    timeout=deadline.left(), context=context)
        else:
            smtp = smtplib.SMTP(host, port, local_hostname=helo, timeout=deadline.left())
        deadline.arm(smtp)
        smtp.ehlo_or_helo_if_needed()
        if security == "starttls":
            # שרת שאינו מציע STARTTLS — כישלון, לא שליחה גלויה (SMTPNotSupportedError).
            deadline.arm(smtp)
            code, resp = smtp.starttls(context=context)
            if code != 220:
                return SendResult(False, code, reply(resp))
            deadline.arm(smtp)
            smtp.ehlo()
        if auth == "password":
            deadline.arm(smtp)
            smtp.login(acc["username"], password)
        elif auth == "oauth":
            deadline.arm(smtp)
            blob = email_oauth.xoauth2(acc["username"] or acc["from_addr"], oauth_token)
            code, resp = smtp.auth("XOAUTH2", lambda challenge=None: blob, initial_response_ok=True)
            if code != 235:
                return SendResult(False, code, reply(resp))
        deadline.arm(smtp)
        code, resp = smtp.mail(acc["from_addr"])
        if code != 250:
            return SendResult(False, code, reply(resp))
        refused: dict = {}
        accepted = []
        last = (None, "")
        for rcpt in recipients:
            deadline.arm(smtp)
            code, resp = smtp.rcpt(rcpt)
            if code in (250, 251):
                accepted.append(rcpt)
            else:
                refused[rcpt] = f"{code} {reply(resp)}"
                last = (code, reply(resp))
        if not accepted:
            return SendResult(False, last[0], "כל הנמענים נדחו: " + "; ".join(
                f"{k}: {v}" for k, v in refused.items()), refused)
        deadline.arm(smtp)
        code, resp = smtp.data(msg.as_bytes(policy=SMTP_POLICY))
        if code != 250:
            return SendResult(False, code, reply(resp), refused)
        return SendResult(True, 250, reply(resp), refused)
    except smtplib.SMTPResponseException as exc:
        return SendResult(False, exc.smtp_code, reply(exc.smtp_error))
    except (OSError, smtplib.SMTPException) as exc:            # כולל ssl.SSLError ו-TimeoutError
        return SendResult(False, None, _failure_text(exc, host, port))
    finally:
        if smtp is not None:
            try:
                if smtp.sock is not None:
                    smtp.sock.settimeout(2.0)
                    smtp.quit()
            except Exception:                                  # noqa: BLE001
                smtp.close()


def send_from(acc: dict, data_dir, recipients: list[str], msg: EmailMessage, *, sender=None,
              http=None) -> SendResult:
    """שליחה מחשבון עם הסוד שלו: הסיסמה מקובץ ה-0600, או אסימון גישה טרי."""
    sender = sender or send
    try:
        password = token = None
        if acc.get("auth") == "password":
            password = email_config.read_secret(data_dir, acc["id"], "password")
        elif acc.get("auth") == "oauth":
            token = email_oauth.access_token(acc, data_dir, **({"http": http} if http else {}))
        return sender(acc, recipients, msg, password=password, oauth_token=token)
    except email_oauth.OAuthError as exc:
        log.warning("email alerts: account %s: OAuth: %s", acc.get("id"), exc)
        return SendResult(False, None, exc.public)
    except interserver_auth.CredentialFileError as exc:
        return SendResult(False, None, email_config.secret_error_text(exc))
    except Exception as exc:                                   # noqa: BLE001
        log.warning("email alerts: account %s: send failed: %s: %s", acc.get("id"),
                    type(exc).__name__, exc)
        return SendResult(False, None, "השליחה נכשלה בשרת — הפרטים ביומן השרת")


def kind_label(lang: str, kind: str) -> str:
    return t(lang, f"kind.{kind}")


def build_message(conn, lang: str, from_addr: str, recipients: list[str], kind: str,
                  items: list[str], dropped: int = 0) -> EmailMessage:
    """המייל בשפת הנמען (``email_text``): חלק ``text/plain`` וחלק HTML עם ``dir``/
    ``lang`` ו-``text-align`` — בעברית מימין לשמאל, באנגלית משמאל לימין."""
    from html import escape  # noqa: PLC0415
    direction = email_text.direction(lang)
    align = email_text.ALIGN[direction]
    server = ports.server_name(conn)
    label = t(lang, f"kind.{kind}")
    count = len(items) + dropped
    msg = EmailMessage()
    msg["Subject"] = (t(lang, "subject_count", server=server, label=label, count=count)
                      if count > 1 else t(lang, "subject", server=server, label=label))
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=(from_addr.split("@")[-1] or None))
    heading = t(lang, "heading", server=server, label=label)
    bullets = list(items) + ([t(lang, "dropped", count=dropped)] if dropped else [])
    footer = t(lang, "footer", server=server)
    plain = [heading, ""] + [f"• {line}" for line in bullets] + ["", footer]
    msg.set_content("\n".join(plain), charset="utf-8", cte="base64")
    html = (f'<!DOCTYPE html><html lang="{lang}" dir="{direction}"><head><meta charset="utf-8">'
            "</head><body>"
            f'<div dir="{direction}" lang="{lang}" style="direction:{direction};'
            f'text-align:{align};font-family:Arial,sans-serif">'
            f"<p><b>{escape(heading)}</b></p><ul>"
            + "".join(f"<li>{escape(line)}</li>" for line in bullets)
            + f'</ul><p style="color:#666">{escape(footer)}</p></div></body></html>')
    msg.add_alternative(html, subtype="html", charset="utf-8", cte="base64")
    # אחרי set_content: הוא מוחק כל כותרת content-*.
    msg["Content-Language"] = lang
    return msg


def probe_message(conn, acc: dict, to: str, lang: str, when: datetime | None = None) -> EmailMessage:
    """מייל הבדיקה — אותו בונה בדיוק, כדי שהבדיקה תבדוק את מה שיישלח."""
    return build_message(conn, lang, acc["from_addr"], [to], "test",
                         [t(lang, "item_line_plain", when=local_text(conn, when or utcnow()),
                            text=t(lang, "item.test"))])


# --- התור ---------------------------------------------------------------------
#
# שורה = מייל אחד ל-(כלל, סוג, שפה). הנמענים נקבעים בזמן השליחה מהכלל — אלה
# שהשפה שלהם היא שפת השורה — כך שנמען שנוסף או הוסר תופס גם מייל שממתין.

def _merge_items(first: list, first_dropped: int, second: list, second_dropped: int):
    items = list(first) + list(second)
    dropped = first_dropped + second_dropped + max(0, len(items) - MAX_ITEMS)
    return items[:MAX_ITEMS], dropped


def enqueue(conn, kind: str, make_item, *, now: datetime | None = None,
            doc: dict | None = None) -> int:
    """מצרף אירוע לכל כלל שהוא עובר (קטגוריה + חומרה), בכל שפה של נמעני הכלל.
    ``make_item(lang)`` מחזיר את שורת האירוע בשפה. מחזיר כמה שורות תור נגעו —
    0 = אין כלל שמעוניין, וזה לא כשל."""
    if doc is None:
        doc, _ = email_config.load(conn)
    now = now or utcnow()
    touched = 0
    for rule in email_config.matching_rules(doc, kind):
        for lang in sorted({r["lang"] for r in rule["recipients"]}):
            _enqueue_one(conn, rule["id"], kind, lang, make_item(lang), now)
            touched += 1
    return touched


def _enqueue_one(conn, rule_id: str, kind: str, lang: str, item: str, now: datetime) -> None:
    with _tx(conn):
        row = conn.execute("SELECT id, items_json, dropped FROM email_queue WHERE rule_id = ?"
                           " AND kind = ? AND lang = ? AND state = 'pending'",
                           (rule_id, kind, lang)).fetchone()
        if row is not None:
            items, dropped = _merge_items(json.loads(row["items_json"]), row["dropped"], [item], 0)
            conn.execute("UPDATE email_queue SET items_json = ?, dropped = ? WHERE id = ?",
                         (json.dumps(items, ensure_ascii=False), dropped, row["id"]))
            return
        last = conn.execute("SELECT MAX(sent_at) AS at FROM email_queue WHERE rule_id = ?"
                            " AND kind = ? AND lang = ? AND state = 'sent'",
                            (rule_id, kind, lang)).fetchone()["at"]
        due = now + timedelta(seconds=GATHER_SECONDS)
        last_at = _parse(last)
        if last_at is not None:
            due = max(due, last_at + timedelta(seconds=RATE_WINDOW_SECONDS))
        conn.execute("INSERT INTO email_queue (rule_id, kind, lang, items_json, created_at, next_at)"
                     " VALUES (?, ?, ?, ?, ?, ?)",
                     (rule_id, kind, lang, json.dumps([item], ensure_ascii=False),
                      _iso(now), _iso(due)))


def _back_to_pending(conn, row, *, attempts: int, next_at: str,
                     code: int | None, error: str | None) -> None:
    """מחזיר שורה שנלקחה לשליחה לתור. אם בינתיים נפתחה שורה ממתינה לאותו מפתח
    (האינדקס מרשה אחת), השורות מתאחדות — לא נזרק אירוע ולא נכשלת הכתיבה."""
    other = conn.execute("SELECT id, items_json, dropped, attempts FROM email_queue"
                         " WHERE rule_id = ? AND kind = ? AND lang = ? AND state = 'pending'",
                         (row["rule_id"], row["kind"], row["lang"])).fetchone()
    if other is None:
        conn.execute("UPDATE email_queue SET state = 'pending', attempts = ?, next_at = ?,"
                     " last_code = ?, last_error = ? WHERE id = ?",
                     (attempts, next_at, code, error, row["id"]))
        return
    items, dropped = _merge_items(json.loads(row["items_json"]), row["dropped"],
                                  json.loads(other["items_json"]), other["dropped"])
    conn.execute("UPDATE email_queue SET items_json = ?, dropped = ?, attempts = ?,"
                 " next_at = MAX(next_at, ?), last_code = ?, last_error = ? WHERE id = ?",
                 (json.dumps(items, ensure_ascii=False), dropped, max(attempts, other["attempts"]),
                  next_at, code, error, other["id"]))
    conn.execute("DELETE FROM email_queue WHERE id = ?", (row["id"],))


def recover_interrupted(conn) -> int:
    """שורות שנשארו ``sending`` (השרת נעצר באמצע שליחה) חוזרות לתור. ייתכן
    שהמייל כבר יצא — עדיף כפול מאשר אבוד, וזה נאמר ביומן."""
    rows = conn.execute("SELECT * FROM email_queue WHERE state = 'sending'").fetchall()
    if not rows:
        return 0
    with _tx(conn):
        for row in rows:
            _back_to_pending(conn, row, attempts=row["attempts"], next_at=row["next_at"],
                             code=row["last_code"], error="השליחה נקטעה — ייתכן שנשלח")
    journal(conn, "email_alert_requeued", f"{len(rows)} מיילים שנקטעו באמצע שליחה חזרו לתור")
    return len(rows)


def backoff_seconds(attempts: int) -> int:
    return min(BACKOFF_BASE_SECONDS * 2 ** max(0, attempts - 1), BACKOFF_CAP_SECONDS)


def _finish(conn, row, state: str, error: str) -> None:
    with _tx(conn):
        conn.execute("UPDATE email_queue SET state = ?, last_error = ? WHERE id = ?",
                     (state, error, row["id"]))


def deliver_due(conn, data_dir, *, now: datetime | None = None, sender=send,
                http=None) -> list[SendResult]:
    """שולח את מה שהגיע זמנו. כל תוצאה נכתבת לתור **וליומן** — גם הצלחה.
    חשבון חסום (בלי סיסמה / לא מחובר) — השורה ממתינה ואינה צורכת ניסיונות."""
    doc, error = email_config.load(conn)
    if error:
        return []
    now = now or utcnow()
    rows = conn.execute("SELECT * FROM email_queue WHERE state = 'pending' AND next_at <= ?"
                        " ORDER BY id", (_iso(now),)).fetchall()
    results = []
    for row in rows:
        rule = email_config.rule(doc, row["rule_id"])
        label = kind_label(row["lang"], row["kind"])
        if rule is None or not rule.get("enabled"):
            _finish(conn, row, "failed", "הכלל נמחק או כובה — לא נשלח")
            continue
        acc = email_config.account(doc, rule["account_id"])
        if acc is None:
            _finish(conn, row, "failed", "חשבון השליחה של הכלל נמחק — לא נשלח")
            continue
        if email_config.blocked_reason(acc, data_dir):
            continue
        recipients = [r["address"] for r in rule["recipients"] if r["lang"] == row["lang"]]
        if not recipients:
            _finish(conn, row, "failed", "אין עוד נמענים בשפה הזו בכלל — לא נשלח")
            continue
        with _tx(conn):
            claimed = conn.execute("UPDATE email_queue SET state = 'sending'"
                                   " WHERE id = ? AND state = 'pending'", (row["id"],)).rowcount == 1
        if not claimed:
            continue
        try:
            msg = build_message(conn, row["lang"], acc["from_addr"], recipients, row["kind"],
                                json.loads(row["items_json"]), row["dropped"])
            result = send_from(acc, data_dir, recipients, msg, sender=sender, http=http)
        except Exception as exc:                               # noqa: BLE001
            # ההיסטוריה מוצגת בקונסולה: ניסוח קבוע, והפרטים ליומן השרת.
            log.warning("email alerts: queue row %s: build failed: %s: %s", row["id"],
                        type(exc).__name__, exc)
            result = SendResult(False, None, "בניית המייל נכשלה — הפרטים ביומן השרת")
        results.append(result)
        attempts = row["attempts"] + 1
        where = f"{label} · {rule.get('name') or rule['id']}"
        if result.ok:
            with _tx(conn):
                conn.execute("UPDATE email_queue SET state = 'sent', sent_at = ?, attempts = ?,"
                             " last_code = 250, last_error = NULL WHERE id = ?",
                             (_iso(now), attempts, row["id"]))
            journal(conn, "email_alert_sent",
                    f"{where} → {len(recipients) - len(result.refused)} נמענים, קוד 250")
            if result.refused:
                journal(conn, "email_alert_refused", f"{where}: נדחו — " + "; ".join(
                    f"{k}: {v}" for k, v in result.refused.items()))
        elif attempts >= MAX_ATTEMPTS:
            with _tx(conn):
                conn.execute("UPDATE email_queue SET state = 'failed', attempts = ?, last_code = ?,"
                             " last_error = ? WHERE id = ?",
                             (attempts, result.code, result.message, row["id"]))
            journal(conn, "email_alert_gave_up",
                    f"{where}: לא נשלח אחרי {attempts} ניסיונות — {result.summary()}")
        else:
            due = _iso(now + timedelta(seconds=backoff_seconds(attempts)))
            with _tx(conn):
                _back_to_pending(conn, row, attempts=attempts, next_at=due,
                                 code=result.code, error=result.message)
            journal(conn, "email_alert_failed",
                    f"{where}: ניסיון {attempts}/{MAX_ATTEMPTS} נכשל — {result.summary()}")
    _prune(conn)
    return results


def _prune(conn) -> None:
    with _tx(conn):
        conn.execute("DELETE FROM email_queue WHERE state IN ('sent', 'failed') AND id NOT IN"
                     " (SELECT id FROM email_queue WHERE state IN ('sent', 'failed')"
                     "  ORDER BY id DESC LIMIT ?)", (KEEP_FINISHED,))


#: אירועי היומן שמרכיבים את היסטוריית ההתראות (לצד שורות התור).
HISTORY_EVENTS = ("email_alert_sent", "email_alert_refused", "email_alert_failed",
                  "email_alert_gave_up", "email_alert_requeued", "email_test",
                  "email_alerts_password_cleared", "email_alerts_settings",
                  "email_oauth_connected", "email_oauth_failed")


def history(conn, limit: int = 50) -> dict:
    """מה נשלח, מה נכשל ולמה (קוד SMTP), מה ממתין — עם זמן וכלל. מהתור ומהיומן,
    בלי טבלה חדשה. החדשות קודם."""
    doc, _ = email_config.load(conn)
    rows = conn.execute("SELECT * FROM email_queue ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    queue = []
    for r in rows:
        rule = email_config.rule(doc, r["rule_id"]) or {}
        acc = email_config.account(doc, rule.get("account_id", "")) or {}
        queue.append({
            "id": r["id"], "kind": r["kind"], "label": kind_label(r["lang"], r["kind"]),
            "severity": email_config.KIND_SEVERITY.get(r["kind"], "err"), "lang": r["lang"],
            "rule_id": r["rule_id"], "rule_name": rule.get("name"),
            "account_id": acc.get("id"), "account_name": acc.get("name"),
            "recipients": [x["address"] for x in rule.get("recipients", []) if x["lang"] == r["lang"]],
            "state": r["state"], "attempts": r["attempts"], "max_attempts": MAX_ATTEMPTS,
            "code": r["last_code"], "error": r["last_error"],
            "created_at": r["created_at"], "sent_at": r["sent_at"], "next_at": r["next_at"],
            "count": len(json.loads(r["items_json"])) + r["dropped"],
            "items": json.loads(r["items_json"])[:5]})
    marks = ",".join("?" * len(HISTORY_EVENTS))
    log_rows = conn.execute(f"SELECT ts, user, event, detail FROM journal WHERE event IN ({marks})"
                            " ORDER BY id DESC LIMIT ?", (*HISTORY_EVENTS, limit)).fetchall()
    return {"queue": queue,
            "journal": [{"ts": r["ts"], "user": r["user"], "event": r["event"],
                         "detail": r["detail"]} for r in log_rows]}


def queue_summary(conn) -> dict:
    pending = conn.execute("SELECT COUNT(*) AS n FROM email_queue"
                           " WHERE state IN ('pending', 'sending')").fetchone()["n"]
    sent = conn.execute("SELECT MAX(sent_at) AS at FROM email_queue WHERE state = 'sent'").fetchone()
    fail = conn.execute("SELECT kind, rule_id, attempts, last_code, last_error, state FROM email_queue"
                        " WHERE last_error IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    return {"pending": pending, "last_sent_at": sent["at"],
            "last_failure": None if fail is None else {
                "kind": fail["kind"], "rule_id": fail["rule_id"], "attempts": fail["attempts"],
                "max_attempts": MAX_ATTEMPTS, "code": fail["last_code"],
                "error": fail["last_error"], "state": fail["state"]}}


# --- מקורות האירועים ----------------------------------------------------------

def write_heartbeat(data_dir, now: datetime) -> None:
    path = Path(data_dir) / HEARTBEAT_FILE
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(_iso(now), encoding="ascii")
    os.replace(tmp, path)


def read_heartbeat(data_dir) -> tuple[datetime | None, str | None]:
    """(הדופק האחרון, שגיאה). אין קובץ = (None, None); קובץ שלא נקרא או
    לא פוענח = (None, הסיבה) — "לא נקרא" אינו "לא היה" (עיקרון 5)."""
    path = Path(data_dir) / HEARTBEAT_FILE
    try:
        raw = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    when = _parse(raw)
    return (when, None) if when is not None else (None, f"תוכן לא מפוענח: {raw[:40]!r}")


def startup_detail(conn, prev: datetime | None, error: str | None, now: datetime) -> str:
    lang = email_text.language(conn)
    up = t(lang, "start.up", now=local_text(conn, now))
    if error:
        return t(lang, "start.unreadable", up=up, error=error)
    if prev is None:
        return t(lang, "start.none", up=up)
    if prev > now:
        return t(lang, "start.future", up=up, prev=local_text(conn, prev))
    return t(lang, "start.down", up=up,
             duration=_duration_text(lang, (now - prev).total_seconds()),
             prev=local_text(conn, prev), now=local_text(conn, now))


def _peer_status(conn, now: datetime) -> dict[str, tuple[bool, str]]:
    """{מפתח: (לא עונה?, תיאור)} — רק עם ראיה: בראשי, כשל אחרון מאוחר מהתשובה
    האחרונה ושקט של ``PEER_SILENT_SECONDS``; במשני, שהראשי לא פנה כל הזמן הזה.
    "לא נמדד" (חותמות ריקות) אינו "לא עונה"."""
    silent = now - timedelta(seconds=PEER_SILENT_SECONDS)
    lang = email_text.language(conn)
    out: dict[str, tuple[bool, str]] = {}
    if storage_nodes.role(conn) == storage_nodes.ROLE_SECONDARY:
        row = conn.execute("SELECT last_contact_at FROM parent_credentials"
                           " WHERE singleton = 1").fetchone()
        seen = _parse(row["last_contact_at"]) if row is not None else None
        if seen is not None:
            down = seen < silent
            out["primary"] = (down, t(lang, "peer.primary_down", seen=local_text(conn, seen))
                              if down else t(lang, "peer.primary_back"))
        return out
    rows = conn.execute("SELECT id, label, base_url, enrolled_at, last_seen_at, last_error,"
                        " last_error_at FROM storage_nodes WHERE disabled_at IS NULL").fetchall()
    for row in rows:
        seen, failed = _parse(row["last_seen_at"]), _parse(row["last_error_at"])
        since = seen or _parse(row["enrolled_at"])
        down = (failed is not None and (seen is None or failed > seen)
                and since is not None and since < silent)
        name = f"{row['label']} ({row['base_url']})"
        out[row["id"]] = (down, t(
            lang, "peer.node_down", name=name,
            seen=local_text(conn, seen) if seen else t(lang, "peer.never"),
            failed=local_text(conn, failed), error=row["last_error"] or "?")
            if down else t(lang, "peer.node_back", name=name))
    return out


def data_free_bytes(data_dir) -> int | None:
    """המקום הפנוי במחיצת הנתונים (ה-DB) — אותה מדידה כמו שורת ``db_space`` בבריאות.
    ``None`` = לא נמדד (עיקרון 5), לא אפס."""
    if data_dir is None:
        return None
    try:
        return shutil.disk_usage(data_dir).free
    except OSError:
        return None


class Watcher:
    """תהליכון הרקע: דופק לדיסק, קריאת היומן, בדיקות מצב ושליחה.

    כל צעד בתוך ``try`` משלו: צעד שנכשל נרשם (פעם אחת לכל הודעה שונה) ואינו
    עוצר את השאר — ובמיוחד לא את הדופק."""

    def __init__(self, conn, data_dir, *, now=utcnow, sender=send,
                 tick: float = TICK_SECONDS, http=None):
        self.conn, self.data_dir, self.now, self.sender, self.tick = conn, data_dir, now, sender, tick
        self.http = http
        #: נקודת ההזרקה של הבדיקות — בלי מדידה של הדיסק של מכונת הבדיקה.
        self.free_bytes = lambda: data_free_bytes(data_dir)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False
        self._last_beat: datetime | None = None
        self._last_error: str | None = None

    # מחזור-חיים ---------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="email-alerts", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(self.tick)

    # צעד אחד ------------------------------------------------------------------
    def run_once(self) -> None:
        now = self.now()
        if not self._started:
            self._step("startup", lambda: self._startup(now))
            self._started = True
        self._step("heartbeat", lambda: self._heartbeat(now))
        self._step("update", self._check_update)
        self._step("peers", lambda: self._check_peers(now))
        self._step("space", self._check_space)
        self._step("journal", self._tail_journal)
        self._step("deliver", lambda: deliver_due(self.conn, self.data_dir, now=now,
                                                  sender=self.sender, http=self.http))

    def _step(self, name: str, fn) -> None:
        try:
            fn()
        except Exception as exc:                               # noqa: BLE001
            log.exception("email alerts: %s failed", name)
            text = f"{name}: {type(exc).__name__}: {exc}"
            if text != self._last_error:
                self._last_error = text
                try:
                    journal(self.conn, "email_alerts_step_failed", text)
                except Exception:                              # noqa: BLE001
                    log.exception("email alerts: journal failed too")

    def _startup(self, now: datetime) -> None:
        if get_setting(self.conn, CURSOR_KEY) is None:
            # הפעלה ראשונה: מתחילים מכאן — לא שולחים את כל ההיסטוריה.
            top = self.conn.execute("SELECT COALESCE(MAX(id), 0) AS n FROM journal").fetchone()["n"]
            set_setting(self.conn, CURSOR_KEY, str(top))
        recover_interrupted(self.conn)
        prev, error = read_heartbeat(self.data_dir)
        journal(self.conn, "server_started", startup_detail(self.conn, prev, error, now))

    def _heartbeat(self, now: datetime) -> None:
        if self._last_beat is None or (now - self._last_beat).total_seconds() >= HEARTBEAT_SECONDS:
            write_heartbeat(self.data_dir, now)
            self._last_beat = now

    def _check_update(self) -> None:
        from server import update  # noqa: PLC0415
        last = update.get_last_check(self.conn)
        if not last or last.get("available") is not True or not last.get("latest"):
            return
        latest = str(last["latest"])
        if get_setting(self.conn, NOTIFIED_UPDATE_KEY) == latest:
            return
        # היומן קודם: כפול עדיף על אבוד.
        journal(self.conn, "update_available", f"{last.get('current') or '?'} → {latest}")
        set_setting(self.conn, NOTIFIED_UPDATE_KEY, latest)

    def _check_peers(self, now: datetime) -> None:
        try:
            state = json.loads(get_setting(self.conn, PEER_STATE_KEY) or "{}")
        except ValueError:
            state = {}
        current = _peer_status(self.conn, now)
        new_state = {}
        for key, (down, text) in current.items():
            was_down = state.get(key) == "down"
            if down and not was_down:
                journal(self.conn, "peer_unreachable", text)
            elif was_down and not down:
                journal(self.conn, "peer_back", text)
            if down:
                new_state[key] = "down"
        if new_state != state:
            set_setting(self.conn, PEER_STATE_KEY, json.dumps(new_state))

    def _check_space(self) -> None:
        """``low_space`` פעם אחת לכל ירידה מתחת לרזרבה (``disk_reserve`` — הסף של שורת
        הבריאות ``db_space``). המצב נשמר ב-``settings``, כך שהפעלה מחדש אינה שולחת שוב.
        "לא נמדד" אינו נמוך ואינו חזרה: המצב נשאר כמו שהיה."""
        free = self.free_bytes()
        if free is None:
            return
        reserve = disk_reserve.reserve()
        was_low = get_setting(self.conn, LOW_SPACE_KEY) == "low"
        if free < reserve and not was_low:
            # היומן קודם: כפול עדיף על אבוד.
            journal(self.conn, "low_space",
                    f"{self.data_dir}: נותרו {disk_reserve.gb(free)} ({free} בייט),"
                    f" פחות מרזרבת המערכת ({disk_reserve.gb(reserve)})")
            set_setting(self.conn, LOW_SPACE_KEY, "low")
        elif was_low and free >= reserve + LOW_SPACE_CLEAR_MARGIN:
            set_setting(self.conn, LOW_SPACE_KEY, "")

    def _tail_journal(self) -> None:
        cursor = int(get_setting(self.conn, CURSOR_KEY) or 0)
        rows = self.conn.execute("SELECT id, ts, event, detail FROM journal WHERE id > ?"
                                 " ORDER BY id LIMIT ?", (cursor, JOURNAL_BATCH)).fetchall()
        if not rows:
            return
        doc, _ = email_config.load(self.conn)
        for row in rows:
            mapping = JOURNAL_KINDS.get(row["event"])
            if mapping is None:
                continue
            kind, accept = mapping
            detail = row["detail"] or ""
            if accept is not None and not accept(detail):
                continue
            when = local_text(self.conn, _parse(row["ts"]))

            def make_item(lang, event=row["event"], when=when, detail=detail):
                return t(lang, "item_line", when=when, text=t(lang, f"item.{event}"), detail=detail)
            enqueue(self.conn, kind, make_item, now=self.now(), doc=doc)
        set_setting(self.conn, CURSOR_KEY, str(rows[-1]["id"]))
