"""#3064 (K13): שירותי השרת שהסבב צריך -- המוח יודע, מסביר וממליץ. **הוא לא מדליק ולא מכבה.** (נדב 15/11)

**מה נבדק** (`read`): dnsmasq ו-imagesrv כיחידות systemd (`systemctl is-active`), ו-TFTP (69/udp), DHCP (67/udp),
HTTP (8080/tcp), NTP (123/udp) ו-syslog (514/udp) לפי **פורט מאזין** בטבלת הסוקטים (`ss`) -- אותם hooks שמסך הבריאות
והפורטים קוראים (`health.default_hooks`). שלושה מצבים, לא שניים (עיקרון 5): ``active`` (ראיה חיובית), ``inactive``
(ראיה חיובית שאינו), ו-``unchecked`` -- הבדיקה עצמה לא הצליחה. **בדיקה שנכשלה אינה "פעיל" ואינה "כבוי".**

**`systemctl is-active` הוא משפחת הפקודות ש-`1` בה תשובה** (CLAUDE.md): קוד היציאה נקרא לתוך משתנה ומטופל בשם --
`0` + ``active`` = פעיל; `3` + ``inactive``/``failed`` = כבוי; כל השאר (`4` = אין יחידה כזו, ``activating``, פלט שאינו מוכר,
timeout, אין `systemctl`) = לא נבדק.

**מה הסבב צריך** (`needed`): dnsmasq, TFTP ו-HTTP תמיד; DHCP רק כשהשרת מוגדר לשרת אותו (`dhcp_on`) -- ברשת עם DHCP של
המכללה הוא כבוי בכוונה. NTP, syslog ו-imagesrv מדווחים ואינם תנאי.

**ההכרעה** (`verdict`) לפני פתיחת סבב או גל, נרשמת בצל ב-``brain_decisions`` (K13) עם הסיבה בעברית ("TFTP כבוי, הסבב
ייכשל"): ``BLOCKED`` (שירות נדרש כבוי), ``UNDECIDED`` (נדרש ולא נבדק), ``READY``. **לעולם אינה חוסמת.**

**ההמלצה והכפתור:** שורה אחת במסך הבריאות (`health_check`), בלי מסך חדש. כפתור "להדליק" רק לשירות מהרשימה הסגורה
`STARTABLE` -- TFTP ו-HTTP, באותה דרך שדף הפורטים מדליק אותם (`ports.set_enabled`, ו-dnsmasq מרונדר מחדש). **DHCP ו-dnsmasq
לעולם אינם מקבלים כפתור:** DHCP נשאר רק במסלול הקיים, עם כל שכבות האישור. הפעולה: `POST /api/console/brain/services/{id}/start`,
**TFTP נחסם כשיש סבב, גל או משיכה חיים** (הדלקתו מפעילה מחדש את dnsmasq, ו-DHCP של הוילן איתו): 409 עם הסבר, והכפתור מושבת עם "יש סבב פעיל"
(`busy_reason`; מה שלא ניתן לבדוק נחשב עסוק, לא פנוי). מנהל בלבד, ורק על שירות שנמדד **כבוי** (לא על "לא נבדק"), ועם אימות חוזר מהסוקט אחרי ההפעלה. **לעולם אינו זורק** אל הסבב.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException

from server.brain import memory
from server.db import journal
from server.net import ports
from server.system.health_rows import check

log = logging.getLogger("imagesrv.brain")

RULE, ACTION = "K13", "SERVICES_FOR_ROUND"
READY, BLOCKED, UNDECIDED, NOT_RUN = "READY", "BLOCKED", "UNDECIDED", "OFF"
ACTIVE, INACTIVE, UNCHECKED = "active", "inactive", "unchecked"
#: id -> (שם בעברית, פרוטוקול, פורט ברירת מחדל). ה-id של TFTP/DHCP/HTTP/NTP הוא של שורות `/ports`.
PORTS = {"tftp": ("TFTP", "udp", 69), "dhcp": ("DHCP", "udp", 67), "http_boot": ("HTTP", "tcp", 8080),
         "ntp": ("NTP", "udp", 123), "syslog": ("syslog", "udp", 514)}
UNITS = {"dnsmasq": ("dnsmasq (DHCP ו-TFTP)", "dnsmasq"), "imagesrv": ("imagesrv", "imagesrv-server")}
#: הרשימה הסגורה של מה שמותר להדליק מהקונסולה. DHCP ו-dnsmasq אינם בה, בכוונה.
STARTABLE = ("tftp", "http_boot")
#: מי מהם מפעיל מחדש את dnsmasq (ואיתו את ה-DHCP של הוילן): לא מדליקים אותו כשיש גל או סבב חיים.
RESTARTS_DNSMASQ = ("tftp",)
UNIT_TIMEOUT_S = 5.0
CACHE_S = 10.0
DEDUPE_S = 30.0
SHADOW_HE = "צל: ההחלטה נרשמה בלבד, שום שירות לא הודלק או כובה"


@dataclass(frozen=True)
class Service:
    id: str
    label: str
    state: str                  # active / inactive / unchecked
    why: str                    # הראיה, בעברית
    needed: bool                # הסבב צריך אותו
    startable: bool = False     # כפתור "להדליק": ברשימה הסגורה, וגם נמדד כבוי


# --- הבדיקות ---------------------------------------------------------------------------------------------------


def default_unit(name: str) -> tuple[int, str] | None:
    """``(קוד יציאה, פלט)`` של `systemctl is-active`, או None -- לא הצליח לרוץ (אין systemctl, timeout)."""
    try:
        done = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True,
                              timeout=UNIT_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.returncode, done.stdout.strip()


def unit_state(got: tuple[int, str] | None) -> tuple[str, str]:
    """``(מצב, למה)``. רק קוד היציאה והפלט **יחד** נותנים ראיה חיובית; אחרת -- לא נבדק."""
    if got is None:
        return UNCHECKED, "systemctl לא רץ (אין systemctl או timeout)"
    rc, out = got
    if rc == 0 and out == "active":
        return ACTIVE, "systemctl: active"
    if rc == 3 and out in ("inactive", "failed"):
        return INACTIVE, f"systemctl: {out}"
    return UNCHECKED, f"systemctl החזיר קוד {rc} ופלט {out!r}: לא ראיה לפעיל ולא לכבוי"


def _port_state(table: str | None, port: int) -> tuple[str, str]:
    bound = ports.bind_addresses(table, port) if table else None
    if bound is None:
        return UNCHECKED, "טבלת הסוקטים לא נקראה (ss לא זמין)"
    return (ACTIVE, f"מאזין על {', '.join(bound)}") if bound else (INACTIVE, f"אף אחד לא מאזין על {port}")


def read(hooks: dict) -> list[Service]:
    """כל השירותים ברשימה, בסדר קבוע. ``hooks``: ``ss`` (udp), ``ss_tcp``, ``unit(name)``, ``dhcp_on()`` (True/False/None),
    ``http_port()`` (או None = 8080). הקורא מזריק; בטסטים לעולם לא נוגעים במכונה."""
    try:
        udp, tcp = hooks["ss"](), hooks["ss_tcp"]()
    except Exception:                              # noqa: BLE001 -- ss שנכשל = "לא נקרא"
        log.exception("brain: K13 could not read the socket table")
        udp = tcp = None
    http_port = (hooks.get("http_port") or (lambda: None))() or PORTS["http_boot"][2]
    try:
        dhcp_on = hooks["dhcp_on"]()
    except Exception:                              # noqa: BLE001
        log.exception("brain: K13 could not read whether DHCP is meant to run")
        dhcp_on = None
    out = []
    for sid, (label, unit) in UNITS.items():
        try:
            state, why = unit_state(hooks["unit"](unit))
        except Exception:                          # noqa: BLE001
            log.exception("brain: K13 could not check %s", unit)
            state, why = UNCHECKED, "הבדיקה נכשלה"
        out.append(Service(sid, label, state, why, needed=sid == "dnsmasq"))
    for sid, (label, proto, port) in PORTS.items():
        state, why = _port_state(tcp if proto == "tcp" else udp, http_port if sid == "http_boot" else port)
        needed = sid in ("tftp", "http_boot") or (sid == "dhcp" and dhcp_on is not False)
        if sid == "dhcp" and state == INACTIVE and dhcp_on is None:
            state, why = UNCHECKED, "לא ידוע אם השרת אמור לשרת DHCP (ההגדרה לא נקראה)"
        out.append(Service(sid, label, state, why, needed, startable=sid in STARTABLE and state == INACTIVE))
    return out


# --- ההכרעה והבריאות -------------------------------------------------------------------------------------------


def verdict(services: list[Service]) -> tuple[str, str]:
    """``(פסק, סיבה)``. כבוי שנדרש קודם ללא-נבדק: הוא ראיה, והשני רק חוסר ראיה."""
    down = [s for s in services if s.needed and s.state == INACTIVE]
    blind = [s for s in services if s.needed and s.state == UNCHECKED]
    if down:
        return BLOCKED, "; ".join(f"{s.label} כבוי, הסבב ייכשל" for s in down) + \
            ("; " + "; ".join(f"{s.label} לא נבדק" for s in blind) if blind else "")
    if blind:
        return UNDECIDED, "; ".join(f"{s.label} לא נבדק: {s.why}" for s in blind)
    names = ", ".join(s.label for s in services if s.needed)
    return READY, f"{names} פעילים"


def busy_reason(ctx) -> str | None:
    """למה אסור עכשיו להפעיל מחדש את dnsmasq: סבב חדר פעיל, גל חי (גל שהסתיים ונשאר לסיכום אינו חי), או משיכה חיה.
    None = אין. **כשל בבדיקה = עסוק** ("לא הצלחנו לבדוק" אינו "פנוי")."""
    try:
        from server.rounds import room_state              # noqa: PLC0415
        if room_state.active_round(ctx.conn) is not None:
            return "יש סבב פעיל"
        current = ctx.store.active_broadcast()
        if current is not None and not ctx.store._spent(current):
            return "יש גל פעיל"
        if ctx.store.active_pulls():
            return "יש משיכה פעילה"
        return None
    except Exception:                                  # noqa: BLE001
        log.exception("brain: K13 could not tell whether a round is live")
        return "לא ניתן לבדוק אם יש סבב פעיל"


def health_check(services: list[Service], busy: str | None = None) -> dict:
    """שורת הבריאות: ירוק רק כשכל מה שנדרש נמדד פעיל. ``actions`` = הכפתורים (רשימה סגורה). ``busy`` = `busy_reason`:
    כפתור שמפעיל מחדש את dnsmasq מוצג מושבת (`disabled`) עם ``reason_he``."""
    kind, reason = verdict(services)
    row = check("brain_services", "שירותי השרת שהסבב צריך",
                {READY: "ok", BLOCKED: "warn", UNDECIDED: "unknown"}[kind], reason)
    acts = [{"id": s.id, "label_he": f"להדליק {s.label}", "url": f"/api/console/brain/services/{s.id}/start",
             **({"disabled": True, "reason_he": busy} if busy and s.id in RESTARTS_DNSMASQ else {})}
            for s in services if s.startable]
    if acts:
        row["actions"] = acts
    if any(s.needed and s.state == INACTIVE and not s.startable for s in services):
        row["detail"] += " — שירות שאין לו כפתור (DHCP, dnsmasq) מודלק רק במסלול הקיים שלו"
    return row


_hold = threading.Lock()
_seen: dict[tuple, float] = {}
_cached: list = [None, 0.0]            # [services, מתי נקרא (monotonic)]


def forget() -> None:
    with _hold:
        _seen.clear()
        _cached[:] = [None, 0.0]


def attach(conn, reader: Callable[[], list[Service]]) -> None:
    """מחבר ל-`conn` את הקורא (`read` עם ה-hooks של האפליקציה), כמו `grace.attach`. בלעדיו -- `gate` אינו מחליט."""
    try:
        conn.brain_services = reader
    except AttributeError:
        log.warning("brain: %s cannot carry the services reader; K13 is not run", type(conn).__name__)


def gate(conn, *, point: str, round_id: str | None = None, mono: Callable[[], float] = time.monotonic) -> str | None:
    """ההכרעה לפני פתיחת סבב (``open_round``) או גל (``attach_wave``): נרשמת בצל, ומוחזר הפסק (או None: לא רצה).
    **אינה חוסמת**, והקורא אינו צריך לבדוק אותה. `read` הוא shell-out, ולכן נשמר ``CACHE_S`` שניות."""
    try:
        from server.brain import veto                   # noqa: PLC0415 -- כמו `slotfill`
        reader = getattr(conn, "brain_services", None)
        if reader is None or veto.mode(conn) == veto.OFF:
            return None
        now = mono()
        with _hold:
            services = _cached[0] if _cached[0] is not None and now - _cached[1] < CACHE_S else None
        if services is None:
            services = reader()
            with _hold:
                _cached[:] = [services, now]
        kind, reason = verdict(services)
        key = (point, kind, reason)
        with _hold:
            fresh = key not in _seen or now - _seen[key] >= DEDUPE_S
            if fresh:
                _seen[key] = now
        if fresh:
            memory.record(conn, mode=veto.SHADOW, rule=RULE, action=ACTION, verdict=kind, reason=reason,
                          evidence=[f"{s.id}={s.state}{'' if s.needed else ' (לא נדרש)'}: {s.why}" for s in services],
                          point=point, round_id=round_id, outcome=SHADOW_HE)
            log.info("brain K13 %s %s: %s", point, kind, reason)
        return kind
    except Exception:                                  # noqa: BLE001 -- הסבב אינו נפתח או נחסם בגלל כשל של המוח
        log.exception("brain: K13 at %s failed", point)
        return None


# --- פעולת המפעיל ----------------------------------------------------------------------------------------------

TOGGLE_FAILED = "ההפעלה נכשלה — הפרטים ביומן השרת"


def listener_apply(listeners, port_id: str) -> Callable[[str], str | None] | None:
    """`ports.Listeners.request(port_id, True)` כפעולה שמחזירה טקסט שגיאה או None. None = אין מנהל מאזינים."""
    if listeners is None:
        return None

    def apply(user_id: str) -> str | None:
        del user_id
        try:
            listeners.request(port_id, True)
        except ports.ListenerError as exc:
            return str(exc)
        return None
    return apply


def create_router(ctx, reader: Callable[[], list[Service]],
                  apply: dict[str, Callable[[str], str | None] | None],
                  busy: Callable[[], str | None] | None = None) -> APIRouter:
    """``apply[id](user_id)`` -> שגיאה או None (`console_dhcp.apply_dnsmasq` ל-TFTP, `listener_apply` ל-HTTP). בטסטים מזריקים."""
    from server.console import auth                    # noqa: PLC0415 -- room.py imports this module (`gate`)
    router = APIRouter(prefix="/api/console/brain")
    _current, admin_only = auth.dependencies(ctx.conn)

    @router.post("/services/{service_id}/start")
    def start(service_id: str, user=Depends(admin_only)):
        if service_id not in PORTS and service_id not in UNITS:
            raise HTTPException(404, "אין שירות כזה ברשימה")
        if service_id not in STARTABLE:
            raise HTTPException(409, "לשירות הזה אין כפתור: DHCP ו-dnsmasq מודלקים רק במסלול הקיים שלהם, עם האישורים שלו")
        row = next(s for s in reader() if s.id == service_id)
        if row.state != INACTIVE:
            raise HTTPException(409, f"{row.label}: " + ("כבר פעיל" if row.state == ACTIVE
                                else f"המצב לא נבדק ({row.why}), ולכן לא מדליקים"))
        why = busy() if busy is not None and service_id in RESTARTS_DNSMASQ else None
        if why:
            raise HTTPException(409, f"{row.label}: לא מדליקים עכשיו, ההדלקה מפעילה מחדש את dnsmasq ו-DHCP של הוילן איתו — {why}")
        action = apply.get(service_id)
        if action is None:
            raise HTTPException(409, f"{row.label}: אין חיבור להפעלה בתהליך הזה")
        ports.set_enabled(ctx.conn, service_id, True)
        journal(ctx.conn, "port_toggle", f"{service_id} on", user[0])
        try:
            error = action(user[0])
        except Exception:                              # noqa: BLE001
            log.exception("brain: starting %s failed", service_id)
            error = TOGGLE_FAILED
        now = next(s for s in reader() if s.id == service_id)
        verified = now.state == ACTIVE
        if error is not None or not verified:
            journal(ctx.conn, "port_unverified", f"{service_id} want=on applied={error is None} listening={verified}"
                    f" {error or ''}".strip(), user[0])
        return {"ok": error is None and verified, "verified": verified, "detail": error or "", "service": now.state}
    return router
