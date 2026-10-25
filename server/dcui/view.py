"""The graphical server screen (#1203): Runtime -> one frame of records.

Python stays the privileged backend and the only state machine: this module
turns the same :class:`app.Runtime` the text console draws into the Hebrew
records that ``imagectl-station-gui --screen dcui`` paints (protocol:
docs/interfaces.md section 31, "the graphical screen").  It reads nothing from
the host except :func:`collect_extras` -- every other fact is the
:class:`data.Snapshot` the text console already shows.

A record is one line, ``type<TAB>field<TAB>...``; a frame ends with an empty
line.  Fields whose name ends in ``_m`` are Pango markup: every dynamic value
inside them goes through :func:`esc`.  The typed password never enters a
frame -- only its length does.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import actions
from .data import FIRST_BOOT_PENDING, NicInfo, ServiceState, Snapshot

#: A certificate this close to its end is "פגה בקרוב" (orange).  Not a project
#: constant elsewhere: the console certificate is self-signed for ten years,
#: so this mostly matters to a hand-installed one.  Decided here (#1203).
CERT_WARN_DAYS = 30
#: The hero lists at most this many problems; the title carries the full count.
MAX_PROBLEMS_SHOWN = 3
#: The primary pings every paired secondary every 150 seconds (#1564,
#: ``storage_heartbeat``); ten minutes tolerates three missed beats.
PARENT_CONTACT_FRESH_SECONDS = 10 * 60

SERVICE_LABELS = {
    "imagectl-server": "שרת ImageSrv והקונסולה",
    "dnsmasq": "DHCP ו־TFTP לרשת ההפצה",
    "nftables": "חומת האש של השרת",
}

MENU_TEXT = (   # app.MENU_ITEMS, in the same order (#1203: no wizard item -- #1190)
    ("net", "רשת ניהול", "הכרטיס והכתובת של הקונסולה"),
    ("rnet", "הפעלה מחדש של רשת הניהול", "החלה מחדש של ההגדרה השמורה"),
    ("svc", "הפעלה מחדש של השירותים", "שרת ImageSrv ו־dnsmasq"),
    ("cert", "תעודת אבטחה", "טביעת אצבע, שמות ותוקף"),
    ("net", "הכתובת שהראשי רואה", "לשרת משנה — מאחורי NAT, הכתובת החיצונית"),
    ("disk", "הוספת כונן", "כונן ריק בשרת הופך למיקום אחסון"),   # #1988
)

RO_NOTE = "· המסך לקריאה בלבד · רענון אחרון {refreshed} · כל 5 שניות"


def clean(value: object) -> str:
    """One record field: no separators, no line breaks."""
    return re.sub(r"[\t\r\n]+", " ", str(value))


def esc(value: object) -> str:
    """A dynamic value inside a markup field."""
    return html.escape(clean(value), quote=False)


FSI, PDI = "\u2068", "\u2069"


def iso(value: object) -> str:
    """A Latin value inside a Hebrew sentence, isolated (the mockup's <bdi>):
    without it "office365 — 12 מתוך 14" and "10 Gb/s" come out reordered."""
    return f"{FSI}{clean(value)}{PDI}"


def mono_m(value: object) -> str:
    """The mockup's <bdi class="m"> inside a markup field."""
    return f'{FSI}<span font_family="IBM Plex Mono">{esc(value)}</span>{PDI}'


def kc_m(cap: str) -> str:
    """A key name inside running markup text (the mockup's inline <kc>)."""
    return f'<span font_family="IBM Plex Mono" weight="600">{esc(cap)}</span>'


@dataclass(frozen=True)
class Extras:
    """Facts the graphical screen shows that the Snapshot does not carry."""
    uptime_seconds: int | None = None
    secondaries: tuple[str, ...] = ()
    has_parent: bool = False
    primary_url: str = ""
    advertised_url: str = ""          # 16/10: the address the primary sees
    parent_last_contact: str | None = None


def collect_extras(conn, uptime_path: Path = Path("/proc/uptime")) -> Extras:
    """Read-only, and each part independently: one unreadable source leaves
    its field empty ("not shown"), it does not hide the others."""
    uptime = None
    try:
        uptime = int(float(uptime_path.read_text(encoding="ascii").split()[0]))
    except (OSError, ValueError, IndexError):
        uptime = None
    if conn is None:
        return Extras(uptime_seconds=uptime)
    from server import storage_nodes
    try:
        rows = conn.execute(
            "SELECT label FROM storage_nodes WHERE disabled_at IS NULL ORDER BY label"
        ).fetchall()
        secondaries = tuple(str(row["label"]) for row in rows)
    except Exception:  # noqa: BLE001 -- an old schema: nothing to list
        secondaries = ()
    try:
        has_parent = storage_nodes.has_parent(conn)
        primary = storage_nodes.primary_url(conn)
    except Exception:  # noqa: BLE001
        has_parent, primary = False, ""
    try:
        # its own try: an unreadable contact is "unknown" (never green), and
        # must not turn a paired secondary into "not paired yet" (#1564)
        parent_contact = storage_nodes.parent_last_contact(conn)
    except Exception:  # noqa: BLE001
        parent_contact = None
    try:
        advertised = storage_nodes.advertised_url(conn)
    except Exception:  # noqa: BLE001
        advertised = ""
    return Extras(uptime, secondaries, has_parent, primary, advertised, parent_contact)


# ---- translation of the backend's ASCII messages ------------------------------

_EXACT = {
    "Session expired after 90 seconds without a key.":
        "פג הזמן אחרי 90 שניות בלי מקש — חזרה למסך הראשי.",
    "Authentication is temporarily locked.": "הכניסה נעולה זמנית.",
    "Authentication locked for 30 seconds after 3 failures.":
        "אחרי 3 ניסיונות שגויים הכניסה נעולה ל־30 שניות.",
    "Wrong password.": "סיסמה שגויה.",
    "Admin password reset cancelled; nothing was changed.":
        "איפוס הסיסמה בוטל — לא שונה דבר.",
    "Admin password reset. Log in to the web console with the new password.":
        "הסיסמה אופסה. נכנסים לקונסולה עם הסיסמה החדשה.",
    "Every admin web session was signed out.": "כל החיבורים של admin לקונסולה נותקו.",
    "Two-factor enrollment was removed.": "האימות הדו־שלבי של admin הוסר.",
    "The server DB has no 'admin' account; nothing changed.":
        "אין בשרת משתמש admin — לא שונה דבר.",
    "The new password could not be read back from the DB.":
        "לא הצלחנו לקרוא בחזרה את הסיסמה החדשה מהשרת — לא ידוע אם נשמרה.",
    "The two passwords differ. Enter the new password again.":
        "שתי הסיסמאות שונות. מקלידים את הסיסמה החדשה שוב.",
    actions.PASSWORD_POLICY: "הסיסמה אינה עומדת בכללים: 8–128 תווים, אותיות, ספרות ותו מיוחד.",
    "Services restarted.": "השירותים הופעלו מחדש ונקראו בחזרה כפעילים.",
    "Service restart failed.": "הפעלת השירותים מחדש נכשלה.",
    "Network form is invalid.": "יש שדות לא תקינים בטופס.",
    "Network configuration conflicts with existing settings.":
        "ההגדרה מתנגשת בהגדרות רשת קיימות.",
    "A previous network change still awaits confirmation.":
        "שינוי רשת קודם עדיין ממתין לאישור.",
    "The automatic network rollback timer is not active.":
        "שעון ההחזרה האוטומטית של הרשת לא פעיל — לא משנים רשת בלי רשת ביטחון.",
    "Could not arm automatic network rollback.": "לא הצלחנו להפעיל את רשת הביטחון של הרשת.",
    "Network change was not verified.": "השינוי ברשת לא אומת.",
    "Apply failed; automatic rollback remains armed.":
        "ההחלה נכשלה; ההחזרה האוטומטית עדיין דרוכה.",
    "Live read-back did not match the requested configuration.":
        "המצב החי שנקרא בחזרה אינו מה שביקשנו.",
    "Network change applied; confirm within 60 seconds.":
        "השינוי הוחל. מאשרים תוך 60 שניות.",
    "Management network restarted; the address read back live.":
        "רשת הניהול הופעלה מחדש, והכתובת נקראה בחזרה מהמצב החי.",
    "Management network restart was not verified.": "ההפעלה מחדש של רשת הניהול לא אומתה.",
    "ifdown/ifup failed.": "‏ifdown/ifup נכשל.",
    "Live read-back did not match the saved configuration.":
        "המצב החי שנקרא בחזרה אינו ההגדרה השמורה.",
    "The management interface has no saved configuration.":
        "אין הגדרה שמורה לכרטיס הניהול.",
    "The saved management network configuration is invalid.":
        "ההגדרה השמורה של רשת הניהול אינה תקינה.",
    "No network change is waiting for confirmation.": "אין שינוי רשת שממתין לאישור.",
    "The confirmation window closed; rollback will run.":
        "חלון האישור נסגר — ההגדרה הקודמת תחזור.",
    "The rollback marker could not be cleared.": "לא הצלחנו לנקות את סמן ההחזרה.",
    "The management interface selection was not verified.":
        "בחירת כרטיס הניהול לא נקראה בחזרה.",
    "Network change confirmed.": "השינוי ברשת אושר.",
    "No network change is waiting for rollback.": "אין שינוי רשת להחזיר.",
    "Network rollback requested.": "ההגדרה הקודמת הוחזרה.",
    "Network rollback could not be started.": "לא הצלחנו להחזיר את ההגדרה הקודמת.",
    "Rollback remains armed; the previous configuration will return.":
        "ההחזרה דרוכה — ההגדרה הקודמת תחזור בסוף הזמן.",
    "Rollback timer is not active; recovery was not verified.":
        "שעון ההחזרה לא פעיל — לא ידוע אם ההגדרה הקודמת תחזור.",
    "The pending marker is gone; the timed rollback has completed.":
        "זמן האישור עבר וההגדרה הקודמת הוחזרה.",
    "Static address form is invalid.": "יש שדות לא תקינים בטופס.",
    "Select an existing network interface.": "בוחרים כרטיס רשת קיים.",
    "Select DHCP or Static.": "בוחרים DHCP או כתובת קבועה.",
    "Invalid IPv4 address.": "כתובת IPv4 לא תקינה.",
    "Invalid prefix or subnet mask.": "תחילית או מסכת רשת לא תקינה.",
    "Invalid gateway IPv4 address.": "כתובת השער לא תקינה.",
    "Invalid DNS IPv4 address; separate entries with commas.":
        "כתובת DNS לא תקינה; מפרידים כמה כתובות בפסיק.",
    "Gateway is required.": "חובה למלא שער.",
    "System reboot requested.": "האתחול התבקש.",
    "System poweroff requested.": "הכיבוי התבקש.",
    "System reboot failed.": "האתחול נכשל.",
    "System poweroff failed.": "הכיבוי נכשל.",
    "No result.": "אין תוצאה.",
    "systemctl returned no detail": "systemctl לא החזיר פירוט.",
    "ip returned no detail": "הפקודה ip לא החזירה פירוט.",
    "Restart/shutdown cancelled; nothing was done.": "בוטל — לא בוצע דבר.",
    # F4 on a secondary (16/10)
    "The server database is not available.": "מסד הנתונים של השרת אינו זמין.",
    "Pairing codes are shown only on a secondary server.": "קוד צימוד מוצג רק בשרת משנה.",
    "This server is already paired; disconnect it first.":
        "השרת כבר מצומד — מנתקים אותו קודם (F4).",
    "The pairing window could not be opened.": "לא הצלחנו לפתוח קוד צימוד.",
    "Pairing code opened.": "קוד הצימוד נפתח.",
    "Pairing code cancelled; it can no longer be used.":
        "קוד הצימוד בוטל — אי אפשר להשתמש בו עוד.",
    "The pairing code could not be cancelled; it stays valid until it expires.":
        "לא הצלחנו לבטל את קוד הצימוד — הוא תקף עד שיפוג.",
    "Paired with the primary server.": "השרת מצומד לשרת הראשי.",
    "The code's state could not be read from the DB.":
        "לא הצלחנו לבדוק את מצב הקוד מול מסד הנתונים.",
    "The disconnect could not be written.": "הניתוק לא נכתב — לא ידוע אם השרת עדיין מצומד.",
    "The primary server is still recorded; the disconnect was not confirmed.":
        "השרת הראשי עדיין רשום — הניתוק לא אומת.",
    "Disconnected from the primary server.": "השרת נותק מהשרת הראשי.",
    "Disconnect cancelled; nothing was changed.": "הניתוק בוטל — לא שונה דבר.",
    "Invalid address: use host:port, for example 203.0.113.40:8443.":
        "כתובת לא תקינה: כתובת ופורט, למשל \u2068203.0.113.40:8443\u2069.",
    "The address could not be saved.": "לא הצלחנו לשמור את הכתובת.",
    "The address was not read back as saved.": "הכתובת שנקראה בחזרה אינה זו שנשמרה.",
    "Address saved.": "הכתובת נשמרה.",
    "Address cleared.": "הכתובת נמחקה — היא לא מוגדרת עוד.",
}

_PATTERNS = (
    (r"Authentication locked for (\d+) more seconds\.", "הכניסה נעולה עוד {0} שניות."),
    (r"Admin password reset is locked for (\d+) more seconds\.",
     "איפוס הסיסמה נעול עוד {0} שניות."),
    (r"Admin password reset locked for (\d+) seconds after (\d+) failures\.",
     "אחרי {1} ניסיונות שגויים איפוס הסיסמה נעול ל־{0} שניות."),
    (r"Temporary address applied to ([A-Za-z0-9._-]+)\.", "הכתובת הזמנית הוחלה על {0}."),
    (r"Temporary network command failed: (.+)\.", "פקודת הרשת הזמנית נכשלה: {0}."),
    (r"Conflict (\d+)", "התנגשות {0}"),
    (r"([A-Za-z0-9@._-]+): restart failed", "{0}: ההפעלה מחדש נכשלה"),
    (r"([A-Za-z0-9@._-]+) did not read back active", "{0} לא חזר למצב פעיל"),
    # ‏#1988: "הוסף כונן" (actions.py)
    (r"Disk (\S+) added as storage location (.+)\.", "הכונן {0} נוסף כמיקום האחסון {1}."),
    (r"Mounted at (\S+) \(UUID in /etc/fstab, nofail\)\.", "מחובר קבוע ב-{0} (‏UUID ב-/etc/fstab, ‏nofail)."),
    (r"Write/read-back check passed\.", "קובץ בדיקה נכתב ונקרא בחזרה."),
)


def he(message: str) -> str:
    """The Hebrew for a backend message; an unknown one is shown as it is
    (in English) rather than dropped."""
    if message in _EXACT:
        return _EXACT[message]
    for pattern, text in _PATTERNS:
        match = re.fullmatch(pattern, message)
        if match:
            return text.format(*match.groups())
    return message


# ---- facts -> rows -------------------------------------------------------------

@dataclass(frozen=True)
class Row:
    label: str
    value: str
    sub: str = ""
    pill_kind: str = ""
    pill: str = ""
    tone: str = ""
    label_style: str = ""
    value_style: str = ""

    def record(self, kind: str = "row") -> tuple[str, ...]:
        return (kind, self.label, self.label_style, self.value, self.value_style,
                self.sub, self.pill_kind, self.pill, self.tone)


@dataclass(frozen=True)
class Problem:
    sev: str
    title: str
    what: str
    todo_m: str
    key: tuple[str, str] = ("", "")
    consequence_m: str = ""


def role_label(snapshot: Snapshot) -> str:
    if snapshot.pre_install:
        return "התקנה"
    return "שרת משנה" if snapshot.storage_role == "secondary" else "שרת ראשי"


def deploy_needed(snapshot: Snapshot, extras: Extras) -> bool:
    """Owner 12/10: an unpaired secondary does not need a deploy network yet;
    a primary (and a paired secondary) does."""
    return not (snapshot.storage_role == "secondary" and not extras.has_parent)


def speed_text(raw: str) -> str:
    try:
        mbit = int(raw)
    except (TypeError, ValueError):
        return ""
    if mbit <= 0:
        return ""
    if mbit >= 1000:
        gbit = mbit / 1000
        return f"{gbit:g} Gb/s"
    return f"{mbit} Mb/s"


def _link_text(link: str) -> str:
    if link == "up":
        return "חיבור תקין"
    if link in ("down", "no carrier"):
        return "אין חיבור"
    return "מצב הקישור לא ידוע"


def nic_row(label: str, nic: NicInfo) -> Row:
    mode = {"dhcp": "DHCP", "static": "קבוע"}.get(nic.mode, "")
    speed = speed_text(nic.speed)
    sub = " · ".join(part for part in (mode, _link_text(nic.link), iso(speed) if speed else "") if part)
    value = f"{nic.name} · {nic.address or 'אין כתובת'}"
    if not nic.address_checked:
        return Row(label, value, sub, "warn", "לא נבדק", "", "", "mono")
    if nic.link in ("down", "no carrier"):
        return Row(label, value, sub, "bad", "מנותק", "bad", "", "mono")
    if not nic.address:
        return Row(label, value, sub, "bad", "אין כתובת", "bad", "", "mono")
    if nic.link != "up":
        return Row(label, value, sub, "warn", "קישור לא ידוע", "", "", "mono")
    return Row(label, value, sub, "ok", "מחובר", "", "", "mono")


def deploy_row(snapshot: Snapshot, extras: Extras) -> Row:
    if snapshot.deploy is not None:
        return nic_row("רשת הפצה", snapshot.deploy)
    if deploy_needed(snapshot, extras):
        return Row("רשת הפצה", "עדיין לא הוגדרה", "מגדירים מהקונסולה: רשת ← הפצה",
                   "bad", "דורש טיפול", "bad")
    return Row("רשת הפצה", "עדיין לא הוגדרה", "מגדירים מהקונסולה: רשת ← הפצה",
               "off", "לא הוגדרה")


def service_row(name: str, state: ServiceState | None, snapshot: Snapshot,
                extras: Extras) -> Row:
    label = SERVICE_LABELS.get(name, name)
    active = state.active if state else "unknown"
    if active == "active":
        return Row(name, label, "", "ok", "פועל", "", "mono")
    if active in ("", "unknown"):
        return Row(name, label, "לא הצלחנו לבדוק את מצב השירות", "warn", "לא נבדק",
                   "warn", "mono")
    if name == "dnsmasq" and snapshot.deploy is None:
        if not deploy_needed(snapshot, extras):
            return Row(name, label, "לא פועל: עדיין אין רשת הפצה", "off", "לא נדרש עדיין",
                       "", "mono")
        return Row(name, label, "לא פועל: עדיין אין רשת הפצה", "bad", "דורש טיפול",
                   "bad", "mono")
    if active == "failed":
        return Row(name, label, "נכשל (systemd: failed)", "bad", "נכשל", "bad", "mono")
    if active in ("activating", "reloading"):
        return Row(name, label, f"systemd: {active}", "warn", "עולה", "warn", "mono")
    return Row(name, label, f"לא פועל (systemd: {active})", "bad", "לא פועל", "bad", "mono")


def services_pill(rows: list[Row]) -> tuple[str, str]:
    bad = [row for row in rows if row.pill_kind == "bad"]
    if bad:
        if all(row.pill == "נכשל" for row in bad):
            return "bad", f"{len(bad)} נכשל"
        return "bad", f"{len(bad)} דורש טיפול"
    if any(row.pill_kind == "warn" for row in rows):
        return "warn", "לא הכול נבדק"
    running = sum(row.pill_kind == "ok" for row in rows)
    if running == len(rows):
        return "ok", f"{running} מתוך {len(rows)}"
    return "ok", f"{running} פועלים"


def parse_not_after(value: str) -> datetime | None:
    """The OpenSSL notAfter text ("Sep 19 12:00:00 2036 GMT")."""
    try:
        return datetime.strptime(value.strip(), "%b %d %H:%M:%S %Y %Z")
    except (ValueError, AttributeError):
        return None


def cert_status(snapshot: Snapshot, today: datetime) -> tuple[str, str, str, str, int | None]:
    """(pill kind, pill, sub, tone, days left)."""
    if snapshot.fingerprint == FIRST_BOOT_PENDING:   # #2148: none yet, not a wrong one
        return "warn", "ממתינה", "האתחול הראשון עדיין רץ — הטביעה תוצג בסופו", "warn", None
    if not snapshot.fingerprint or snapshot.fingerprint.startswith("unavailable"):
        return "bad", "לא נקראה", "לא הצלחנו לקרוא את התעודה", "bad", None
    end = parse_not_after(snapshot.certificate_not_after)
    if end is None:
        return "warn", "תוקף לא ידוע", "לא הצלחנו לקרוא את תאריך התפוגה", "warn", None
    days = (end.date() - today.date()).days
    when = end.strftime("%d/%m/%Y")
    if days < 0:
        return "bad", "פגה", f"פגה ב־{when}", "bad", days
    if days <= CERT_WARN_DAYS:
        return "warn", "פגה בקרוב", f"פגה ב־{when} — בעוד {days} ימים", "warn", days
    return "ok", "בתוקף", "להשוות למה שהדפדפן מציג בכניסה הראשונה", "", days


_SVC_TODO = "<b>מה לעשות:</b> הגדרות ← הפעלה מחדש של השירותים."


def problems(snapshot: Snapshot, extras: Extras, today: datetime) -> list[Problem]:
    """Every problem the snapshot can prove, worst first.  Nothing is
    guessed: a cause the snapshot does not carry is not named."""
    found: list[Problem] = []
    services = snapshot.services
    server = services.get("imagectl-server")
    if server is None or server.active != "active":
        state = server.active if server else "unknown"
        if state in ("", "unknown"):
            found.append(Problem(
                "warn", "לא הצלחנו לבדוק את שרת ImageSrv (imagectl-server)",
                "לא ידוע אם הקונסולה ומחשבי השיכפול מקבלים שירות.",
                "<b>מה לעשות:</b> רענון (F5); אם זה חוזר — הגדרות ← הפעלה מחדש של השירותים.",
                ("F2", "הגדרות")))
        else:
            found.append(Problem(
                "bad", "שרת ImageSrv (imagectl-server) לא פועל",
                f"{iso('systemd: ' + state)}. הקונסולה לא נפתחת, ומחשבים לא מקבלים משימות.",
                _SVC_TODO, ("F2", "הגדרות"),
                "הקונסולה <b>לא תיפתח</b> עד שהבעיה הראשונה תתוקן."))
    dns = services.get("dnsmasq")
    dns_state = dns.active if dns else "unknown"
    if snapshot.deploy is not None and dns_state != "active":
        if dns_state in ("", "unknown"):
            found.append(Problem(
                "warn", "לא הצלחנו לבדוק את שירות ה־DHCP של רשת ההפצה (dnsmasq)",
                "לא ידוע אם מחשבים ברשת ההפצה יקבלו כתובת.",
                "<b>מה לעשות:</b> רענון (F5); אם זה חוזר — הגדרות ← הפעלה מחדש של השירותים.",
                ("F2", "הגדרות")))
        else:
            verb = "נכשל" if dns_state == "failed" else "לא פועל"
            found.append(Problem(
                "bad", f"שירות ה־DHCP של רשת ההפצה (dnsmasq) {verb}",
                "מחשבים ברשת ההפצה לא יקבלו כתובת ויעלו מהדיסק המקומי.",
                _SVC_TODO + " אם זה חוזר — לבדוק בקונסולה מי עוד מחלק כתובות.",
                ("F2", "הגדרות"),
                "שיכפול <b>לא יעבוד</b> עד שהבעיה הראשונה תתוקן."))
    if snapshot.deploy is None and deploy_needed(snapshot, extras):
        found.append(Problem(
            "bad", "רשת ההפצה לא הוגדרה",
            "בלי רשת הפצה מחשבים לא יקבלו כתובת, ושיכפול לא יעבוד.",
            "<b>מה לעשות:</b> בקונסולה: רשת ← הפצה.", ("", ""),
            "שיכפול <b>לא יעבוד</b> עד שהבעיה הראשונה תתוקן."))
    mgmt = snapshot.management
    if snapshot.address_lookup_failed:
        found.append(Problem(
            "warn", "לא הצלחנו לקרוא את הכתובות של כרטיסי הרשת",
            "הכתובת שמוצגת לקונסולה אינה בהכרח הכתובת האמיתית.",
            "<b>מה לעשות:</b> רענון (F5); אם זה חוזר — הגדרות ← הפעלה מחדש של רשת הניהול.",
            ("F2", "הגדרות")))
    elif mgmt.link in ("down", "no carrier"):
        found.append(Problem(
            "bad", f"כבל רשת הניהול ({iso(mgmt.name)}) מנותק",
            "הקונסולה לא נגישה מהרשת.",
            "<b>מה לעשות:</b> לבדוק את הכבל; או הגדרות ← רשת ניהול ולבחור כרטיס אחר.",
            ("F2", "הגדרות"), "הקונסולה <b>לא נגישה</b> עד שהבעיה הראשונה תתוקן."))
    elif snapshot.no_address:
        found.append(Problem(
            "bad", f"לרשת הניהול ({iso(mgmt.name)}) אין כתובת",
            "הקונסולה לא נגישה מהרשת.",
            "<b>מה לעשות:</b> הגדרות ← רשת ניהול: כתובת קבועה, או לבדוק את שרת ה־DHCP.",
            ("F2", "הגדרות"), "הקונסולה <b>לא נגישה</b> עד שהבעיה הראשונה תתוקן."))
    deploy = snapshot.deploy
    if deploy is not None and deploy.link in ("down", "no carrier"):
        found.append(Problem(
            "bad", f"כבל רשת ההפצה ({iso(deploy.name)}) מנותק",
            "מחשבים ברשת ההפצה לא יגיעו לשרת.",
            "<b>מה לעשות:</b> לבדוק את הכבל ואת המתג של רשת ההפצה.", ("", ""),
            "שיכפול <b>לא יעבוד</b> עד שהבעיה הראשונה תתוקן."))
    elif deploy is not None and deploy.address_checked and not deploy.address:
        found.append(Problem(
            "bad", f"לכרטיס רשת ההפצה ({iso(deploy.name)}) אין כתובת",
            "מחשבים ברשת ההפצה לא יקבלו שירות.",
            "<b>מה לעשות:</b> בקונסולה: רשת ← הפצה.", ("", ""),
            "שיכפול <b>לא יעבוד</b> עד שהבעיה הראשונה תתוקן."))
    kind, _pill, sub, _tone, days = cert_status(snapshot, today)
    if kind == "bad" and days is None:
        found.append(Problem(
            "bad", "לא הצלחנו לקרוא את תעודת האבטחה",
            "טביעת האצבע לא מוצגת, ואי אפשר להשוות אותה לדפדפן.",
            "<b>מה לעשות:</b> הגדרות ← תעודת אבטחה; אם היא חסרה — להפעיל מחדש את השירותים.",
            ("F2", "הגדרות")))
    elif kind == "bad":
        found.append(Problem(
            "bad", "תעודת האבטחה פגה", f"{sub}. הדפדפן חוסם את הכניסה לקונסולה.",
            "<b>מה לעשות:</b> לחדש את התעודה.", ("", "")))
    elif kind == "warn" and days is not None:
        end = parse_not_after(snapshot.certificate_not_after)
        when = end.strftime("%d/%m/%Y") if end else ""
        found.append(Problem(
            "warn", f"תעודת האבטחה פגה בעוד {days} ימים",
            f"ב־{when} הדפדפן יחסום את הכניסה לקונסולה.",
            "<b>מה לעשות:</b> לחדש את התעודה מהקונסולה (הגדרות ← תעודה).", ("", "")))
    if snapshot.rollback_interface:
        found.append(Problem(
            "warn", f"שינוי ברשת הניהול ({iso(snapshot.rollback_interface)}) ממתין לאישור",
            f"אם לא יאושר, ההגדרה הקודמת תחזור בעוד {snapshot.rollback_seconds} שניות.",
            "<b>מה לעשות:</b> הגדרות ← רשת ניהול, ולאשר או להחזיר.", ("F2", "הגדרות")))
    order = {"bad": 0, "warn": 1}
    return sorted(found, key=lambda item: order[item.sev])


def uptime_text(seconds: int) -> str:
    days, rest = divmod(seconds, 86400)
    if days >= 2:
        return f"{days} ימים"
    if days == 1:
        return "יום אחד"
    hours = rest // 3600
    if hours >= 1:
        return f"{hours} שעות" if hours > 1 else "שעה"
    return f"{max(1, rest // 60)} דקות"


def _local_hhmm(stamp: str) -> str:
    """‏#1933: חותמת ה-DB (UTC) → HH:MM בשעון המכונה, כמו שאר המסך — לא חיתוך
    המחרוזת, שהציג UTC. חותמת בלי היסט היא UTC; מה שאינו חותמת → "" (לא ניחוש)."""
    try:
        when = datetime.fromisoformat(stamp.strip())
    except (AttributeError, TypeError, ValueError):
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone().strftime("%H:%M")


def _round_row(snapshot: Snapshot) -> Row:
    item = snapshot.deploy_round
    if item is None:
        return Row("סבב הפצה", "אין סבב פעיל", "", "off", "ממתין")
    sub = f"התחיל {started}" if (started := _local_hhmm(item.started)) else ""
    if item.state == "running":
        return Row("סבב הפצה", f"{iso(item.label)} · {item.writing} מתוך {item.targets} מחשבים כותבים",
                   sub, "info", "רץ")
    return Row("סבב הפצה", f"{iso(item.label)} · ממתין להצטרפות ({item.targets} מחשבים)",
               sub, "info", "פתוח")


def _parent_contact(extras: Extras, today: datetime) -> tuple[str, str]:
    """Return fresh/stale/unknown and the operator-facing contact text."""
    raw = extras.parent_last_contact
    if not raw:
        return "unknown", "מצומד · מגע לא ידוע"
    try:
        when = datetime.fromisoformat(raw)
        if when.tzinfo is not None:
            when = when.astimezone().replace(tzinfo=None)
        now = today.astimezone().replace(tzinfo=None) if today.tzinfo else today
        age = (now - when).total_seconds()
    except (TypeError, ValueError, OverflowError):
        return "unknown", "מצומד · מגע לא ידוע"
    if age < 0:
        return "unknown", "מצומד · מגע לא ידוע"
    if age <= PARENT_CONTACT_FRESH_SECONDS:
        return "fresh", f"מחובר · מגע לפני {int(age // 60)} דק'"
    return "stale", f"מצומד · אין מגע מאז {when:%H:%M}"


def _peer_row(snapshot: Snapshot, extras: Extras, today: datetime) -> Row:
    if snapshot.storage_role == "secondary":
        if not extras.has_parent:
            return Row("שרת ראשי", "עדיין לא התחבר", "F4 מציג את קוד הצימוד להקלדה בראשי",
                       "info", "ממתין")
        host = _host_of(extras.primary_url) or "לא ידוע"
        state, text = _parent_contact(extras, today)
        return Row("שרת ראשי", host, "", "ok" if state == "fresh" else "warn",
                   text, "" if state == "fresh" else "warn", "", "mono")
    names = extras.secondaries
    if not names:
        return Row("שרתי משנה", "אין", "מוסיפים מהקונסולה: שרתים ← הוספת שרת משנה",
                   "off", "אין")
    count = f"{len(names)} רשום" if len(names) == 1 else f"{len(names)} רשומים"
    return Row("שרתי משנה", count, " · ".join(iso(name) for name in names), "ok", "רשום")


def advertised_row(url: str) -> Row:
    """16/10: the address the primary types; unset is a yellow warning, not a guess."""
    if url:
        return Row("הכתובת שהראשי רואה", url, "מוצגת במסך הקוד (F4)", value_style="mono")
    return Row("הכתובת שהראשי רואה", "לא הוגדרה", "הגדרות (F2) ← הכתובת שהראשי רואה",
               "warn", "לא הוגדרה", "warn")


def system_updates_row(day: str) -> Row:
    """#1411: Debian updates arrive only inside signed releases -- their date says
    how fresh the server's packages are.  Not known is said, not hidden.  One
    line, last in the lower tile: the services tile is clipped once the hero
    lists problems (rendered 17/10 at 1024x768)."""
    if day:
        return Row("עדכוני מערכת", f"מתאריך {iso(day)}")
    return Row("עדכוני מערכת", "תאריך לא ידוע", "", "warn", "לא נבדק")


def _host_of(url: str) -> str:
    match = re.match(r"^[a-z]+://([^/:]+)", url or "")
    return match.group(1) if match else ""


def fingerprint_lines(value: str) -> tuple[str, str]:
    parts = value.split(":")
    if len(parts) >= 32:
        return ":".join(parts[:16]), ":".join(parts[16:32])
    return value, ""


# ---- frame builder -------------------------------------------------------------

class Frame:
    def __init__(self, screen: str):
        self.lines: list[tuple[str, ...]] = [("frame", screen)]

    def add(self, *fields: object) -> None:
        self.lines.append(tuple(clean(field) for field in fields))

    def text(self) -> str:
        return "".join("\t".join(line) + "\n" for line in self.lines) + "\n"


def _header(frame: Frame, snapshot: Snapshot, clock: str, admin_seconds: int | None) -> None:
    admin = (f"מנהל · יציאה אוטומטית בעוד {admin_seconds} שנ׳"
             if admin_seconds is not None else "")
    url = snapshot.console_url if not snapshot.no_address else ""
    shown_version = snapshot.version + (" · לא מאומת" if not snapshot.version_verified else "")
    frame.add("hdr", role_label(snapshot), snapshot.hostname, url, shown_version,
              clock, admin)


MAIN_KEYS = (("F2", "הגדרות", 1), ("F9", "איפוס סיסמה", 0),
             ("F12", "אתחול / כיבוי", 1), ("F5", "רענון", 0))


def main_keys(snapshot: Snapshot, extras: Extras) -> tuple:
    """F4 only on a secondary (16/10): pair when unpaired, disconnect when paired."""
    if snapshot.storage_role != "secondary":
        return MAIN_KEYS
    f4 = ("F4", "ניתוק מהשרת הראשי" if extras.has_parent else "צימוד לשרת ראשי", 0)
    return (f4,) + MAIN_KEYS


def _main_state(snapshot: Snapshot, extras: Extras, found: list[Problem], today: datetime) -> tuple[str, str]:
    """(dot, word) for the status line."""
    if found:
        return "bad", f"דורש טיפול · {len(found)}"
    if snapshot.storage_role == "secondary":
        if not extras.has_parent:
            return "info", "ממתין לחיבור מהראשי"
        state, text = _parent_contact(extras, today)
        return ("ok", text) if state == "fresh" else ("warn", text)
    if snapshot.deploy_round and snapshot.deploy_round.state == "running":
        return "warn", "סבב הפצה רץ"
    return "ok", "תקין"


def _hero(frame: Frame, snapshot: Snapshot, extras: Extras, found: list[Problem], today: datetime) -> None:
    uptime = (("פועל ברציפות", uptime_text(extras.uptime_seconds))
              if extras.uptime_seconds is not None else ("", ""))
    if found:
        many = "בעיה אחת" if len(found) == 1 else f"{len(found)} בעיות"
        first = found[0].consequence_m or "הבעיות מסודרות לפי חומרה — מתחילים מהראשונה."
        frame.add("hero", "bad", f"השרת דורש טיפול · {many}", first, "", "")
        for index, item in enumerate(found[:MAX_PROBLEMS_SHOWN], start=1):
            frame.add("prob", item.sev, index, item.title, item.what, item.todo_m, *item.key)
        return
    if snapshot.storage_role == "secondary" and not extras.has_parent:
        frame.add("hero", "wait", "ממתין לחיבור מהשרת הראשי",
                  "השרת מוכן לצימוד. <b>F4</b> מציג קוד חד־פעמי, את טביעת הזהות ואת הכתובת "
                  "שמקלידים בקונסולה של השרת הראשי: <b>שרתי משנה ← רישום שרת משנה</b>.", "", "")
        return
    if snapshot.storage_role == "secondary":
        host = _host_of(extras.primary_url)
        state, contact = _parent_contact(extras, today)
        title = f"{contact} · {iso(host)}" if host else contact
        detail = ("האימג׳ים מסונכרנים מהראשי. ההפצה כאן היא רק למחשבים ברשת של השרת הזה."
                  if state == "fresh" else
                  "השרת מצומד לראשי, אך אין ראיה למגע טרי. ההפצה המקומית ממשיכה בנפרד.")
        frame.add("hero", "ok" if state == "fresh" else "warn", title,
                  detail,
                  *uptime)
        return
    if snapshot.deploy_round and snapshot.deploy_round.state == "running":
        sub = "כל השירותים פועלים. סבב הפצה רץ עכשיו — אפשר לעקוב אחריו בקונסולה."
    else:
        sub = "כל השירותים פועלים, שתי הרשתות מחוברות והתעודה בתוקף. אין צורך לעשות דבר."
    frame.add("hero", "ok", "השרת תקין", sub, *uptime)


def _tiles(frame: Frame, snapshot: Snapshot, extras: Extras, today: datetime) -> None:
    rows = [service_row(name, snapshot.services.get(name), snapshot, extras)
            for name in ("imagectl-server", "dnsmasq", "nftables")]
    frame.add("tile", "שירותים", *services_pill(rows))
    for row in rows:
        frame.add(*row.record())
    frame.add("tile", "רשת", "", "")
    frame.add(*nic_row("רשת ניהול", snapshot.management).record())
    frame.add(*deploy_row(snapshot, extras).record())
    frame.add("tile", "שרתים והפצה", "", "")
    frame.add(*_peer_row(snapshot, extras, today).record())
    if snapshot.storage_role == "secondary" and not extras.has_parent:
        frame.add(*advertised_row(extras.advertised_url).record())
    frame.add(*_round_row(snapshot).record())
    frame.add(*system_updates_row(snapshot.system_updates).record())
    frame.add("tile", "גישה לקונסולה", "", "")
    url = snapshot.console_url if not snapshot.no_address else "אין כתובת"
    frame.add(*Row("כתובת", url, value_style="strong-mono").record())
    kind, pill, sub, tone, _days = cert_status(snapshot, today)
    fp1, fp2 = fingerprint_lines(snapshot.fingerprint)
    frame.add("fprow", "טביעת אצבע", "(SHA-256)", fp1, fp2, sub, kind, pill, tone)


def _preinstall(frame: Frame, snapshot: Snapshot) -> None:
    if snapshot.console_url:
        frame.add("hero", "wait", "ההתקנה עדיין לא הסתיימה",
                  f"ממשיכים את ההתקנה מדפדפן: <b>{mono_m(snapshot.console_url)}</b>", "", "")
    else:
        frame.add("hero", "bad", "ההתקנה עדיין לא הסתיימה — ואין כתובת",
                  "אף כרטיס רשת לא קיבל כתובת מ־DHCP. <b>F2</b> מגדיר כתובת זמנית.", "", "")
    frame.add("tile", "כרטיסי רשת", "", "")
    for nic in snapshot.interfaces[:6]:
        up = nic.link == "up"
        frame.add(*Row(nic.name, nic.address or "אין כתובת", iso(nic.mac),
                       "ok" if up and nic.address else ("bad" if not up else "warn"),
                       "מחובר" if up else "אין חיבור", "", "mono", "mono").record())
    frame.add("tile", "המשך ההתקנה", "", "")
    frame.add(*Row("כתובת", snapshot.console_url or "אין כתובת",
                   "אשף ההתקנה נפתח בדפדפן בכתובת הזו", value_style="strong-mono").record())


def _main_body(frame: Frame, snapshot: Snapshot, extras: Extras, today: datetime) -> None:
    if snapshot.pre_install:
        _preinstall(frame, snapshot)
        return
    _hero(frame, snapshot, extras, problems(snapshot, extras, today), today)
    _tiles(frame, snapshot, extras, today)


def main_state(snapshot: Snapshot, extras: Extras, today: datetime) -> tuple[str, str]:
    """(dot, word) of the status line -- the server's state on every screen."""
    if snapshot.pre_install:
        return ("info", "התקנה בתהליך") if snapshot.console_url else ("bad", "אין כתובת")
    return _main_state(snapshot, extras, problems(snapshot, extras, today), today)


def _keys(frame: Frame, keys) -> None:
    for caps, label, lock in keys:
        frame.add("key", caps, label, lock)


def _password_hint(frame: Frame, caps: bool) -> None:
    frame.add("d.hint", "EN", "<b>סיסמה באנגלית בלבד</b> — המקלדת נעולה על אנגלית",
              f"Caps Lock: {'מופעל' if caps else 'כבוי'}")


def _auth_dialog(frame: Frame, runtime, now_wall: float) -> None:
    target = ("כדי לפתוח את הגדרות השרת (F2)." if runtime.auth_target == "menu"
              else "כדי לאתחל או לכבות את השרת (F12).")
    frame.add("dlg", "plain", 560, "סיסמת מנהל", target)
    frame.add("d.field", "משתמש", "admin", "ro", 0, 0)
    frame.add("d.field", "סיסמה", len(runtime.password), "password", 1, 1)
    _password_hint(frame, runtime.caps)
    lock = runtime.auth.seconds_left(now_wall)
    if lock:
        frame.add("d.error", f"נעול אחרי {actions.LOCKOUT_ATTEMPTS} ניסיונות שגויים. "
                             f"אפשר לנסות שוב בעוד {lock} שניות.")
    elif runtime.auth.failures:
        frame.add("d.error", f"סיסמה שגויה — ניסיון {runtime.auth.failures} מתוך "
                             f"{actions.LOCKOUT_ATTEMPTS}.")
    frame.add("d.note", "הסיסמה נבדקת על השרת עצמו — עובד גם כשהשירות לא רץ.")
    frame.add("d.note", f"אחרי {actions.LOCKOUT_ATTEMPTS} ניסיונות שגויים — נעילה ל־"
                        f"{actions.LOCKOUT_SECONDS} שניות. שכחת? {kc_m('F9')} במסך הראשי.")
    frame.add("d.btn", "כניסה", "Enter", "pri", 1)
    frame.add("d.btn", "ביטול", "Esc", "plain", 0)


def _reset_dialog(frame: Frame, runtime) -> None:
    stage = runtime.reset_stage
    frame.add("dlg", "plain", 560, "איפוס סיסמת המנהל",
              "בלי הסיסמה הנוכחית — הנוכחות ליד השרת היא ההרשאה.")
    if stage == "done":
        result = runtime.result or actions.ActionResult(False, "No result.")
        frame.add("d.alert" if not result.ok else "d.p", esc(he(result.message)))
        for detail in result.details[:6]:
            frame.add("d.bullet", esc(he(detail)))
        frame.add("d.btn", "חזרה למסך הראשי", "Enter", "pri", 1)
        return
    frame.add("d.bullet", "הסיסמה החדשה תחליף את זו של המשתמש <b>admin</b> בקונסולה.")
    frame.add("d.bullet", "כל החיבורים של admin לקונסולה ינותקו, והאימות הדו־שלבי שלו יוסר.")
    frame.add("d.bullet", "הפעולה נרשמת ביומן.")
    if stage == "confirm":
        frame.add("d.btn", "בחירת סיסמה חדשה", "Enter", "pri", 1)
        frame.add("d.btn", "ביטול", "Esc", "plain", 0)
        return
    if stage == "password":
        frame.add("d.field", "סיסמה חדשה", len(runtime.reset_typed), "password", 1, 1)
    else:
        frame.add("d.field", "סיסמה חדשה", 8, "password-done", 0, 0)
        frame.add("d.field", "שוב, לאימות", len(runtime.reset_typed), "password", 1, 1)
    _password_hint(frame, runtime.caps)
    if runtime.message:
        frame.add("d.error", he(runtime.message))
    frame.add("d.note", "8–128 תווים, עם אותיות, ספרות ותו מיוחד: "
                        + esc('! @ # $ % ^ & * ( ) , . ? " : { } | < >'))
    frame.add("d.note", f"ניסיונות שגויים: {runtime.reset_guard.failures} מתוך "
                        f"{actions.LOCKOUT_ATTEMPTS} (אחריהם נעילה ל־{actions.LOCKOUT_SECONDS} שניות).")
    frame.add("d.btn", "המשך" if stage == "password" else "איפוס", "Enter", "pri", 1)
    frame.add("d.btn", "ביטול", "Esc", "plain", 0)


def _power_dialog(frame: Frame, snapshot: Snapshot, extras: Extras, runtime) -> None:
    restart = runtime.power_choice == "restart"
    lead = (f"השרת {iso(snapshot.hostname)} יפסיק לעבוד עד שיעלה מחדש." if restart
            else f"השרת {iso(snapshot.hostname)} יפסיק לעבוד עד שידליקו אותו.")
    frame.add("dlg", "danger", 640, "לאתחל את השרת?" if restart else "לכבות את השרת?", lead)
    frame.add("d.opt", "אתחול", "השרת עולה מחדש לבד, תוך כמה דקות", int(restart), 0)
    frame.add("d.opt", "כיבוי", "השרת לא יעלה לבד — צריך להדליק אותו ידנית", int(not restart), 0)
    item = snapshot.deploy_round
    if item and item.state == "running":
        frame.add("d.alert", f"<b>סבב הפצה רץ עכשיו:</b> {mono_m(item.label)} — {item.writing} מתוך "
                             f"{item.targets} מחשבים כותבים. הם ייכשלו, וצריך יהיה להריץ את הסבב מחדש.")
    elif item:
        frame.add("d.alert", f"<b>סבב הפצה פתוח:</b> {mono_m(item.label)} — מחשבים שכבר הצטרפו "
                             "יאבדו את הסבב.")
    frame.add("d.bullet", "הקונסולה לא תהיה זמינה עד שהשרת יעלה.")
    frame.add("d.bullet", "מחשבים שיופעלו בינתיים יעלו מהדיסק המקומי שלהם.")
    if snapshot.storage_role != "secondary" and extras.secondaries:
        names = " · ".join(mono_m(name) for name in extras.secondaries)
        word = "שרת המשנה" if len(extras.secondaries) == 1 else "שרתי המשנה"
        frame.add("d.bullet", f"{word} <b>{names}</b> ימשיך לעבוד, בלי סנכרון עד שהראשי יחזור.")
    if snapshot.rollback_interface:
        frame.add("d.bullet", f"שינוי ברשת הניהול ({mono_m(snapshot.rollback_interface)}) עדיין "
                              f"ממתין לאישור — עוד {snapshot.rollback_seconds} שניות.")
    act = runtime.power_focus == "act"
    frame.add("d.btn", "ביטול", "Esc", "plain", int(not act))
    frame.add("d.btn", "אתחל עכשיו" if restart else "כבה עכשיו", "", "danger", int(act))


def _net_fields(frame: Frame, runtime, names: tuple[tuple[str, str, int], ...],
                unused: bool = False) -> None:
    for label, key, index in names:
        focused = runtime.form_field == index
        frame.add("d.field", label, runtime.form.get(key, ""),
                  "text" if focused or not unused else "unused", int(focused), int(focused))


def _network_dialog(frame: Frame, runtime) -> None:
    frame.add("dlg", "plain", 640, "רשת ניהול",
              "הכרטיס שהקונסולה מאזינה עליו (HTTPS 8081), ואיך הוא מקבל כתובת.")
    selected = runtime.form.get("interface", "")
    for nic in runtime.interfaces[:4]:
        link = "חיבור תקין" if nic.link == "up" else "אין חיבור"
        current = "הכרטיס של הקונסולה עכשיו" if nic.name == runtime.snapshot.management.name else ""
        sub = " · ".join(part if part in (link, current) else iso(part)
                         for part in (current, nic.model, nic.mac, link, speed_text(nic.speed)) if part)
        frame.add("d.radio", nic.name, sub, int(nic.name == selected),
                  int(runtime.form_field == 0))
    static = runtime.form.get("mode") == "static"
    on_mode = int(runtime.form_field == 1)
    frame.add("d.opt", "DHCP", "כתובת אוטומטית משרת ה־DHCP", int(not static), on_mode)
    frame.add("d.opt", "כתובת קבועה", "כתובת, מסכה, שער ו־DNS", int(static), on_mode)
    # The fields stay on screen in DHCP too, as on the text screen (Tab still
    # reaches them there); unused, they are drawn read-only until focused.
    _net_fields(frame, runtime, (("כתובת IPv4", "address", 2), ("תחילית / מסכה", "netmask", 3),
                                 ("שער", "gateway", 4), ("DNS (מופרדים בפסיק)", "dns", 5)),
                unused=not static)
    if runtime.message:
        frame.add("d.error", he(runtime.message))
        for detail in (runtime.result.details if runtime.result else ())[:4]:
            frame.add("d.error", he(detail))
    frame.add("d.note", "<b>רשת ביטחון:</b> אחרי Enter ההגדרה נכנסת לתוקף ל־60 שניות, "
                        "וחוזרת לבד אם לא אישרתם.")
    frame.add("d.btn", "החלה (60 שניות)", "Enter", "pri", 0)
    frame.add("d.btn", "ביטול", "Esc", "plain", 0)


def _network_confirm_dialog(frame: Frame, runtime, seconds_left: int) -> None:
    frame.add("dlg", "plain", 560, "לאשר את השינוי ברשת הניהול?",
              f"אם לא תאשרו, ההגדרה הקודמת תחזור לבד בעוד {seconds_left} שניות.")
    frame.add("d.p", "בודקים שהקונסולה נפתחת בכתובת החדשה, ואז מאשרים.")
    if runtime.message:
        frame.add("d.error", he(runtime.message))
    frame.add("d.btn", "אישור", "C", "pri", 0)
    frame.add("d.btn", "החזרה עכשיו", "R", "plain", 0)
    frame.add("d.btn", "השארת השעון", "Esc", "plain", 0)


def _preinstall_network_dialog(frame: Frame, runtime) -> None:
    frame.add("dlg", "plain", 640, "כתובת זמנית",
              "רק עד שההתקנה מסתיימת — אשף ההתקנה קובע את הכתובת הקבועה.")
    selected = runtime.form.get("interface", "")
    for nic in runtime.interfaces[:4]:
        frame.add("d.radio", nic.name, f"{iso(nic.mac)} · {_link_text(nic.link)}",
                  int(nic.name == selected),
                  int(runtime.form_field == 0))
    _net_fields(frame, runtime, (("כתובת IPv4 / תחילית", "address", 1), ("שער", "gateway", 2)))
    if runtime.message:
        frame.add("d.error", he(runtime.message))
        for detail in (runtime.result.details if runtime.result else ())[:4]:
            frame.add("d.error", he(detail))
    frame.add("d.btn", "החלה", "Enter", "pri", 0)
    frame.add("d.btn", "ביטול", "Esc", "plain", 0)


def _tls_dialog(frame: Frame, snapshot: Snapshot, today: datetime) -> None:
    frame.add("dlg", "plain", 640, "תעודת אבטחה", "כפי שנקראה מהתעודה עצמה.")
    fp1, fp2 = fingerprint_lines(snapshot.fingerprint)
    frame.add("d.kv", "טביעת אצבע (SHA-256)", fp1, 1)
    if fp2:
        frame.add("d.kv", "", fp2, 1)
    for index, name in enumerate(snapshot.certificate_sans[:5]):
        frame.add("d.kv", "שמות בתעודה" if index == 0 else "", name, 1)
    end = parse_not_after(snapshot.certificate_not_after)
    _kind, _pill, sub, _tone, _days = cert_status(snapshot, today)
    frame.add("d.kv", "בתוקף עד", end.strftime("%d/%m/%Y") if end else
              snapshot.certificate_not_after, 1)
    frame.add("d.p", esc(sub))
    frame.add("d.btn", "חזרה להגדרות", "Enter", "pri", 1)


def _disk_sub(disk: dict) -> str:
    size = disk.get("size_bytes")
    bits = [f"{size / 1e9:.0f} GB" if isinstance(size, int) else "",
            disk.get("model") or "", f"S/N {disk['serial']}" if disk.get("serial") else "",
            disk.get("bus") or ""]
    return " · ".join(iso(bit) for bit in bits if bit)


def _disk_dialog(frame: Frame, runtime) -> None:
    """#1988: only empty disks are selectable; the rest are listed with why."""
    frame.add("dlg", "plain", 680, "הוספת כונן",
              "רק כוננים ריקים: בלי טבלת מחיצות, בלי חתימה, וראש הכונן וסופו אפסים.")
    listing = runtime.disks
    if listing is None:
        failure = runtime.result
        frame.add("d.error", (failure.he if failure and failure.he else he(runtime.message))
                  or "רשימת הכוננים לא נקראה")
        frame.add("d.note", "\"לא נקרא\" אינו \"אין כונן ריק\". ‏F5 קורא שוב.")
        frame.add("d.btn", "חזרה", "Esc", "plain", 1)
        return
    items = listing.get("items") or []
    if not items:
        frame.add("d.alert", "<b>אין בשרת כונן ריק.</b> כונן שיש עליו מידע אינו מוצע.", "warn")
    for index, disk in enumerate(items[:6]):
        frame.add("d.radio", disk.get("device", "?"), _disk_sub(disk),
                  int(index == runtime.disk_index), 1)
    for disk in (listing.get("hidden") or [])[:3]:
        frame.add("d.bullet", f"{mono_m(disk.get('device', '?'))} — לא מוצע: "
                              f"{esc(disk.get('reason') or 'בלי סיבה')}")
    if runtime.message:
        frame.add("d.error", he(runtime.message))
    frame.add("d.note", "שום דבר לא משתנה עד האישור במסך הבא.")
    frame.add("d.btn", "המשך", "Enter", "pri", int(bool(items)))
    frame.add("d.btn", "ביטול", "Esc", "plain", 0)


def _disk_confirm_dialog(frame: Frame, runtime, disk: dict) -> None:
    """The red button is the confirmation (#1270) -- nothing is typed; the
    focus starts on Cancel, as on the power dialog."""
    frame.add("dlg", "danger", 640, "כל המידע בכונן יימחק",
              f"{iso(disk.get('device', '?'))} · {_disk_sub(disk)}")
    frame.add("d.bullet", "טבלת GPT ומחיצה אחת על כל הכונן, ומערכת קבצים ext4.")
    frame.add("d.bullet", "חיבור קבוע לפי UUID ב־fstab, עם nofail.")
    frame.add("d.bullet", "קובץ בדיקה נכתב ונקרא בחזרה; כשל באמצע מוצג, ולא נשאר מיקום חלקי.")
    act = runtime.disk_focus == "act"
    frame.add("d.btn", "ביטול", "Esc", "plain", int(not act))
    frame.add("d.btn", "מחק את הכונן והוסף", "", "danger", int(act))


def _result_dialog(frame: Frame, runtime) -> None:
    result = runtime.result or actions.ActionResult(False, runtime.message or "No result.")
    frame.add("dlg", "plain", 560, "הפעולה הושלמה" if result.ok else "הפעולה לא הצליחה",
              result.he or he(result.message))
    for detail in result.details[:8]:
        frame.add("d.kv", "", he(detail), 1)
    frame.add("d.btn", "חזרה להגדרות", "Enter", "pri", 1)


def spki_groups(spki: str) -> list[str]:
    clean = (spki or "").upper()
    return [clean[i:i + 4] for i in range(0, len(clean), 4)]


def countdown(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def local_time(iso_value: str) -> str:
    try:
        when = datetime.fromisoformat(iso_value)
    except (TypeError, ValueError):
        return iso_value or "לא ידוע"
    return when.astimezone().strftime("%d/%m/%Y %H:%M:%S") if when.tzinfo else \
        when.strftime("%d/%m/%Y %H:%M:%S")


def _pair_dialog(frame: Frame, runtime, wall: float) -> None:
    """F4 on a secondary (16/10, mockup a2-a4): the code, its time, the
    identity the primary compares and the address it types."""
    stage, code = runtime.pair_stage, runtime.pair
    if stage == "paired":
        paired = runtime.paired or {}
        frame.add("dlg", "plain", 560, "✓ השרת מצומד",
                  "הצימוד הושלם. הקוד בוטל ואי אפשר להשתמש בו שוב.")
        frame.add("d.kv", "שרת ראשי", paired.get("primary_url") or "לא ידוע", 1)
        frame.add("d.kv", "מזהה ראשי", paired.get("parent_id") or "לא ידוע", 1)
        frame.add("d.kv", "זמן", local_time(paired.get("enrolled_at", "")), 1)
        frame.add("d.btn", "סגירה", "Enter", "pri", 1)
        return
    if stage in ("expired", "closed") or code is None:
        if stage == "closed":
            frame.add("dlg", "plain", 560, "הקוד נסגר לפני הזמן",
                      "לא בוצע צימוד. הקוד אינו תקף עוד — הראשי יקבל שגיאה אם יקליד אותו.")
            frame.add("d.alert", "הסיבה: הקוד הוקלד שגוי 5 פעמים בראשי, או שנפתח קוד אחר.")
        else:
            frame.add("dlg", "plain", 560, "הקוד פג תוקף",
                      "לא בוצע צימוד. הקוד אינו תקף עוד — הראשי יקבל שגיאה אם יקליד אותו.")
        frame.add("d.btn", "קוד חדש", "Enter", "pri", 1)
        frame.add("d.btn", "חזרה", "Esc", "plain", 0)
        return
    left = code.expires - wall
    frame.add("dlg", "plain", 680, "קוד צימוד חד־פעמי",
              "מקלידים את הקוד בשרת הראשי. תקף לשימוש אחד, ומוצג רק על המסך הזה.")
    frame.add("d.code", code.code, "זמן שנותר", countdown(left), "warn" if left < 60 else "")
    groups = spki_groups(code.spki)
    if groups:
        frame.add("d.kv", "זהות השרת", " ".join(groups[:4]), 1)
        frame.add("d.kv", "מזהה", code.node_id, 1)
        frame.add("d.kv", "SPKI SHA-256 מלא", " ".join(groups[:8]), 1)
        frame.add("d.kv", "", " ".join(groups[8:16]), 1)
    else:
        frame.add("d.alert", "<b>לשרת אין עדיין זהות בין־שרתית</b> — הראשי לא יוכל להתחבר. "
                             "הגדרות (F2) ← הפעלה מחדש של השירותים.")
    if code.advertised:
        frame.add("d.kv", "הכתובת שהראשי מקליד", code.advertised, 1)
    else:
        frame.add("d.alert", "<b>הכתובת שהראשי רואה לא הוגדרה.</b> "
                             "הגדרות (F2) ← הכתובת שהראשי רואה.", "warn")
    frame.add("d.bullet", "<b>בשרת הראשי:</b> שרתי משנה ← רישום שרת משנה")
    frame.add("d.bullet", "<b>מקלידים את הכתובת</b> ומוודאים שהטביעה זהה לזו שכאן")
    frame.add("d.bullet", "<b>מקלידים את הקוד</b> — 8 תווים, שימוש חד־פעמי")
    if runtime.pair_error:
        frame.add("d.error", he(runtime.pair_error))
    frame.add("d.note", "סגירת המסך (Esc) מבטלת את הקוד. אחרי 5 הקלדות שגויות בראשי הוא נסגר.")
    frame.add("d.btn", "סגירה", "Esc", "plain", 0)
    frame.add("d.btn", "קוד חדש", "F5", "plain", 0)


def _unpair_dialog(frame: Frame, extras: Extras) -> None:
    """Mockup a6: the red button is the confirmation -- nothing is typed."""
    host = _host_of(extras.primary_url)
    frame.add("dlg", "danger", 560, "לנתק את השרת מהשרת הראשי?",
              f"הראשי {iso(host)} יפסיק לראות את השרת הזה." if host else
              "הראשי יפסיק לראות את השרת הזה.")
    frame.add("d.bullet", "צימוד מחדש דורש קוד חדש (F4).")
    frame.add("d.bullet", "האימג׳ים באחסון לא נמחקים.")
    frame.add("d.note", "אין צורך להקליד דבר — הכפתור האדום הוא האישור.")
    frame.add("d.btn", "ביטול", "Esc", "plain", 0)
    frame.add("d.btn", "נתק מהשרת הראשי", "Enter", "danger", 1)


def _advertised_dialog(frame: Frame, runtime) -> None:
    frame.add("dlg", "plain", 640, "הכתובת שהראשי רואה",
              "הכתובת שבה השרת הראשי מגיע לשרת הזה (פורט 8443). מאחורי NAT — הכתובת החיצונית.")
    frame.add("d.field", "כתובת ופורט", runtime.advert, "text", 1, 1)
    if runtime.message:
        frame.add("d.error", he(runtime.message))
    frame.add("d.note", f"לדוגמה {mono_m('203.0.113.40:8443')} — ‏https:// נוסף לבד. "
                        "שדה ריק מוחק את ההגדרה.")
    frame.add("d.btn", "שמירה", "Enter", "pri", 0)
    frame.add("d.btn", "ביטול", "Esc", "plain", 0)


def _settings_body(frame: Frame, runtime, today: datetime) -> None:
    snapshot = runtime.snapshot
    frame.add("ttl", "הגדרות השרת", "רק מה שצריך לעשות מול המכונה עצמה. כל השאר — בקונסולה.")
    for index, (icon, title, sub) in enumerate(MENU_TEXT):
        frame.add("item", icon, title, sub, int(index == runtime.menu_index))
    frame.add("na", "<b>לא זמין כאן</b> (בקונסולה): מחיקת אימג׳ים · הסרת שרתי משנה · "
                    "רשת ההפצה וטבלת המחשבים")
    mgmt = snapshot.management
    selected = runtime.menu_index
    if selected == 0:
        frame.add("pane", "רשת ניהול", f"כרטיס הרשת שהקונסולה מאזינה עליו ({mono_m('HTTPS 8081')}). "
                                       "כתובת מ־DHCP או כתובת קבועה, שער ו־DNS.")
        frame.add(*nic_row("עכשיו", mgmt).record("prow"))
        frame.add("pnote", "<b>רשת ביטחון:</b> שינוי נכנס לתוקף ל־60 שניות, וחוזר לבד אם "
                           "לא אישרתם — כך לא ננעלים מחוץ לשרת.")
        frame.add("pnote", "רשת ההפצה לא מוגדרת כאן — בקונסולה: רשת ← הפצה.")
    elif selected == 1:
        frame.add("pane", "הפעלה מחדש של רשת הניהול",
                  f"מוריד ומעלה את {mono_m(mgmt.name)} עם ההגדרה השמורה, בלי לשנות דבר. "
                  "ההצלחה נקבעת לפי הכתובת שנקראת בחזרה מהמצב החי.")
        frame.add(*nic_row("עכשיו", mgmt).record("prow"))
        frame.add("pnote", "אין כאן שינוי ולכן אין מה לאשר. לשינוי כתובת — "
                           "הגדרת רשת הניהול, עם רשת הביטחון.")
    elif selected == 2:
        frame.add("pane", "הפעלה מחדש של השירותים",
                  f"מפעיל מחדש את {mono_m('imagectl-server')}, ואת {mono_m('dnsmasq')} רק כשהוא מופעל — ואז קורא "
                  "בחזרה את המצב של כל שירות.")
        extras = getattr(runtime, "extras", None) or Extras()
        for name in ("imagectl-server", "dnsmasq"):
            frame.add(*service_row(name, snapshot.services.get(name), snapshot,
                                   extras).record("prow"))
        frame.add("pnote", "מחשבים שבאמצע שיכפול עלולים להיכשל בזמן ההפעלה מחדש.")
    elif selected == 5:
        frame.add("pane", "הוספת כונן",
                  "כונן ריק שהותקן בשרת הופך למיקום אחסון: השרת מחלק אותו, מכין ext4 "
                  "ומחבר אותו קבוע לפי UUID. מוצעים רק כוננים ריקים לפי ראיה.")
        frame.add("pnote", "<b>כל המידע בכונן יימחק.</b> האישור הוא הכפתור האדום — בלי הקלדה.")
        frame.add("pnote", "המיקום החדש מופיע בקונסולה: תשתית ← אחסון.")
    elif selected == 4:
        frame.add("pane", "הכתובת שהראשי רואה",
                  "הכתובת שבה השרת הראשי מגיע לשרת המשנה הזה (פורט 8443). מוצגת במסך "
                  "קוד הצימוד (F4) — זו הכתובת שמקלידים בראשי.")
        extras = getattr(runtime, "extras", None) or Extras()
        frame.add(*advertised_row(extras.advertised_url).record("prow"))
        frame.add("pnote", "מאחורי NAT: הכתובת החיצונית של האתר הזה, לא הכתובת המקומית.")
    else:
        frame.add("pane", "תעודת אבטחה",
                  "טביעת האצבע, השמות שבתעודה ותאריך התפוגה — כפי שנקראו מהתעודה עצמה.")
        fp1, fp2 = fingerprint_lines(snapshot.fingerprint)
        kind, pill, sub, tone, _days = cert_status(snapshot, today)
        frame.add("fprow", "טביעת אצבע", "(SHA-256)", fp1, fp2, sub, kind, pill, tone)
        frame.add("pnote", "משווים את טביעת האצבע למה שהדפדפן מציג בכניסה הראשונה לקונסולה.")


def _chosen(runtime) -> dict | None:
    items = (getattr(runtime, "disks", None) or {}).get("items") or []
    index = getattr(runtime, "disk_index", 0)
    return items[index] if 0 <= index < len(items) else None


SETTINGS_SCREENS = {"menu", "network", "network_confirm", "tls", "result", "advertised",
                    "disk", "disk_confirm"}


def render(runtime, now: float, wall: float, clock: str, today: datetime,
           rollback_left: int = 0) -> str:
    """One frame for the current runtime state.

    ``now`` is the monotonic clock (auto-logout), ``wall`` the epoch (lockouts),
    ``clock`` the header's HH:MM, ``today`` the date certificates are judged on,
    ``rollback_left`` the seconds left on a pending network change."""
    snapshot: Snapshot = runtime.snapshot
    extras: Extras = getattr(runtime, "extras", None) or Extras()
    screen = runtime.screen
    settings = screen in SETTINGS_SCREENS
    frame = Frame("settings" if settings else "main")
    admin = screen in SETTINGS_SCREENS or screen == "power"
    _header(frame, snapshot, clock, runtime.seconds(now) if admin else None)
    if settings:
        _settings_body(frame, runtime, today)
    else:
        _main_body(frame, snapshot, extras, today)
    dot, word = main_state(snapshot, extras, today)
    rest = RO_NOTE.format(refreshed=snapshot.refreshed)
    if screen == "main":
        if snapshot.pre_install:
            _keys(frame, (("F2", "כתובת זמנית", 0), ("F5", "רענון", 0)))
        else:
            _keys(frame, main_keys(snapshot, extras))
        lock = runtime.auth.seconds_left(wall)
        message = runtime.message or (f"Authentication locked for {lock} more seconds."
                                      if lock else "")
        if message:
            frame.add("notice", "info", he(message))
    elif screen == "auth":
        rest = "· הקלדת סיסמה"
        _auth_dialog(frame, runtime, wall)
        frame.add("lang", "שדה באנגלית בלבד")
        _keys(frame, (("Enter", "כניסה", 0), ("Esc", "ביטול", 0)))
    elif screen == "reset":
        rest = "· איפוס סיסמת המנהל"
        _reset_dialog(frame, runtime)
        if runtime.reset_stage in ("password", "repeat"):
            frame.add("lang", "שדה באנגלית בלבד")
        _keys(frame, (("Enter", "המשך", 0), ("Esc", "ביטול", 0)))
    elif screen == "power":
        rest = "· מנהל"
        _power_dialog(frame, snapshot, extras, runtime)
        _keys(frame, (("→ ←", "אתחול / כיבוי", 0), ("Tab", "מעבר בין הכפתורים", 0),
                      ("Enter", "ביצוע הכפתור המסומן", 0), ("Esc", "ביטול", 0)))
    elif screen == "preinstall_network":
        rest = "· כתובת זמנית"
        _preinstall_network_dialog(frame, runtime)
        if runtime.form_field:
            frame.add("lang", "שדה באנגלית בלבד")
        _keys(frame, (("Tab", "שדה הבא", 0), ("Space", "החלפת כרטיס", 0),
                      ("Enter", "החלה", 0), ("Esc", "ביטול", 0)))
    elif screen == "menu":
        rest = RO_NOTE.format(refreshed=snapshot.refreshed)
        _keys(frame, (("↑ ↓", "בחירה", 0), ("Enter", "פתיחה", 0), ("Esc", "חזרה למסך הראשי", 0)))
    elif screen == "network":
        rest = "· רשת ניהול"
        _network_dialog(frame, runtime)
        if runtime.form_field >= 2:
            frame.add("lang", "שדה באנגלית בלבד")
        _keys(frame, (("Tab", "שדה הבא", 0), ("Space", "החלפה", 0),
                      ("Enter", "החלה", 0), ("Esc", "ביטול", 0)))
    elif screen == "network_confirm":
        rest = "· ממתין לאישור"
        _network_confirm_dialog(frame, runtime, rollback_left)
        _keys(frame, (("C", "אישור", 0), ("R", "החזרה עכשיו", 0), ("Esc", "השארת השעון", 0)))
    elif screen == "pair":
        stage = runtime.pair_stage
        rest = {"code": "· קוד צימוד פעיל", "paired": "· מצומד"}.get(stage, "· הקוד אינו תקף")
        _pair_dialog(frame, runtime, wall)
        if stage == "code":
            _keys(frame, (("F5", "קוד חדש", 0), ("Esc", "סגירה וביטול הקוד", 0)))
        elif stage == "paired":
            _keys(frame, (("Enter", "סגירה", 0),))
        else:
            _keys(frame, (("Enter", "קוד חדש", 0), ("Esc", "חזרה", 0)))
    elif screen == "unpair":
        rest = "· אישור ניתוק"
        _unpair_dialog(frame, extras)
        _keys(frame, (("Enter", "נתק", 0), ("Esc", "ביטול", 0)))
    elif screen == "advertised":
        rest = "· הכתובת שהראשי רואה"
        _advertised_dialog(frame, runtime)
        frame.add("lang", "שדה באנגלית בלבד")
        _keys(frame, (("Enter", "שמירה", 0), ("Esc", "ביטול", 0)))
    elif screen == "disk":
        rest = "· הוספת כונן"
        _disk_dialog(frame, runtime)
        _keys(frame, (("↑ ↓", "בחירת כונן", 0), ("Enter", "המשך", 0), ("F5", "קריאה מחדש", 0),
                      ("Esc", "חזרה", 0)))
    elif screen == "disk_confirm" and _chosen(runtime):
        rest = "· אישור מחיקת כונן"
        _disk_confirm_dialog(frame, runtime, _chosen(runtime))
        _keys(frame, (("Tab", "מעבר בין הכפתורים", 0), ("Enter", "ביצוע הכפתור המסומן", 0),
                      ("Esc", "ביטול", 0)))
    elif screen == "tls":
        rest = "· תעודת אבטחה"
        _tls_dialog(frame, snapshot, today)
        _keys(frame, (("Enter", "חזרה", 0), ("Esc", "חזרה", 0)))
    else:
        rest = "· תוצאת פעולה"
        _result_dialog(frame, runtime)
        _keys(frame, (("Enter", "חזרה להגדרות", 0),))
    frame.add("sb", dot, word, rest)
    return frame.text()
