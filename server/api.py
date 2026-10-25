"""נקודות הקצה שהסוכן צורך — ממשקים 2, 3, 4 ו-7.

הכללים כאן זהים לצד השני של הרשת: לא זורקים חריגות החוצה, כל כשל
מוחזר כתשובה שסופה דיסק מקומי או שגיאה מסודרת בתבנית המוסכמת.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from sqlite3 import Connection

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from boot.grub_menu import normalize_mac as lenient_mac

from . import (agent_loops, disk_events, drawer_slots, foreign_vlan, identity, inventory,
               login_guard, probe, pulls, registry, reports, shrink_records, users)
from .auth import COOKIE_NAME, PURPOSE_KIOSK, read_session
from .db import agent_seen, disk_full, journal, note_disk_full
from . import direct, gui_failure, learn_wol, local_clone_report, readbackhash, room_console, smart_ask
from . import agent_logs   # #1882
from . import held   # #2031
from .readbackhash import mode as readback_mode
from .hello import (build_answer, hello_payload_oversized, login_required, off_deploy_vlan,
                    well_formed_monitor_auth, well_formed_monitor_secret)
from .images import ImageLibrary, restore_refusal
from .sessions import SessionError, SessionStore
from .tasks import TOKEN_HEADER, discard_failed_staging

log = logging.getLogger("imagectl.api")

#: ‏#906: אורך השאלה שנשמרת מ-hello (`prompt`). שורת מסך אחת של הסוכן
#: היא ~60 תווים; מעבר לזה נקצץ, לא נדחה.
PROMPT_MAX_CHARS = 200

#: ‏#402: הערכים של `disk_probe` בממשק 2. `unchecked` ≠ `no_disks`
#: (עיקרון 5): "לא הצלחנו לספור פורטים" אינו "אפס פורטים".
DISK_PROBE_VALUES = frozenset({"drives", "no_disks", "no_ports", "unchecked"})


@dataclass
class ServerContext:
    conn: Connection
    library: ImageLibrary
    store: SessionStore
    sender: object | None = None      # SenderEngine; None בבדיקות יחידה
    drivers: object | None = None     # DriverLibrary (#720); None בבדיקות ישנות
    #: ‏#855: מקור חכירות ה-DHCP (`identity.LeaseFile`) — הצד שנוגע בקובץ,
    #: מוזרק כמו `dhcp_hooks`. ‏`server.main` מתקין אותו תמיד; ‏None =
    #: הרצת בדיקות בלי מקור, ואז שומר הזהות אינו מותקן (וה-/health אומר).
    leases: object | None = None
    #: ‏#1128: תיקיית הנתונים — לגיבוי ההגדרות מהקונסולה; None בבדיקות ישנות.
    data_dir: Path | None = None
    #: ‏#705: השירותים לכל כרטיס (`nic_services.NicServices`) — ‏`create_runtime`
    #: מתקין תמיד; None = בדיקות שבונות ctx ביד, ואז אין שער (ההתנהגות של היום).
    nic_services: object | None = None
    #: ‏#1949: ‏(אירוע, פירוט) → ‏[מתי נרשם, כמה נספרו מאז] — ריסון היומן
    #: של סירובי הזהות (`_identity_repeats`). לכל אפליקציה, לא גלובלי.
    identity_journaled: dict = field(default_factory=dict)
    #: ‏#1973: מנטר ה-UPS (`ups.UpsMonitor`) — ‏`server.main` בלבד; None = אין מנטר.
    ups: object | None = None


def _error(status: int, message: str, code: str) -> JSONResponse:
    """תבנית השגיאות מהמוסכמות הרוחביות."""
    return JSONResponse(
        {"ok": False, "error": message, "code": code}, status_code=status
    )


def _identity_refusal(ctx: ServerContext, verdict: identity.Verdict,
                      where: str) -> JSONResponse | None:
    """‏`None` = ממשיכים. סירוב = 403 בשם (`identity_refused` /
    `identity_unverifiable`) ושורת יומן שנוקבת בנתיב — **לפני** כל רישום:
    ‏`net_seen`, ספירת הלולאות, ההצטרפות לסבב, רשומת הכיווץ ואירוע הדיסק
    לא מתרחשים, כי הפונה אינו המכונה (‏#585)."""
    if verdict.ok:
        return None
    repeats = _identity_repeats(ctx, verdict)
    if repeats is not None:
        suffix = (f" [+{repeats[0]} identical refusals in the last {repeats[1]}s not journaled]"
                  if repeats[0] else "")
        journal(ctx.conn, verdict.event, f"{verdict.detail} ({where}){suffix}")
        log.warning("%s: %s (%s)%s", verdict.event, verdict.detail, where, suffix)
    return _error(403, verdict.message, verdict.event)


#: ‏#1949: סירוב זהות זהה (אותו אירוע, אותו MAC/כתובת ואותה סיבה) נרשם
#: ביומן פעם אחת בחלון כזה; החוזרים בתוכו נספרים ונכתבים כמונה בשורה
#: הבאה. ‏R2 (22/10): ‏346 שורות `identity_unverifiable` ב-10 דקות — שתיים
#: כל 2 שניות — הסתירו את `client_lost` שבאמצען. הסירוב עצמו (403) אינו
#: משתנה: רק הרישום מרוסן.
IDENTITY_JOURNAL_WINDOW_S = 300
#: מעל כמה מפתחות מנקים את אלה שהחלון שלהם עבר — MAC מזויף שונה בכל
#: בקשה אינו מגדל את הזיכרון בלי גבול.
_IDENTITY_KEYS_PRUNE_AT = 256
_identity_lock = threading.Lock()
_clock = time.monotonic


def _identity_repeats(ctx: ServerContext, verdict: identity.Verdict) -> tuple[int, int] | None:
    """‏None = סירוב שכבר נרשם בחלון הנוכחי — נספר ולא נכתב. אחרת
    ‏(כמה נספרו מאז השורה הקודמת, לפני כמה שניות היא נכתבה) — ‏(0, 0) לראשון."""
    now = _clock()
    key = (verdict.event, verdict.detail)
    with _identity_lock:
        seen = ctx.identity_journaled.get(key)
        if seen is not None and now - seen[0] < IDENTITY_JOURNAL_WINDOW_S:
            seen[1] += 1
            return None
        ctx.identity_journaled[key] = [now, 0]
        if len(ctx.identity_journaled) > _IDENTITY_KEYS_PRUNE_AT:
            for old in [k for k, (at, _n) in ctx.identity_journaled.items()
                        if now - at >= IDENTITY_JOURNAL_WINDOW_S]:
                del ctx.identity_journaled[old]
    return (seen[1], int(now - seen[0])) if seen is not None else (0, 0)


def identity_gate(ctx: ServerContext, mac: str, request: Request,
                  where: str) -> JSONResponse | None:
    """‏#855/#997: ‏MAC מוצהר + כתובת המקור תואמת את חכירת ה-DHCP של אותו MAC.

    שומר **אחד** לכל נתיב שמזהה מכונה לפי MAC מהגוף — ‏hello, ‏progress,
    ‏login, ‏pulls, ‏disk-event, ‏shrink-*, וגם `station.py` (‏sessions)
    ו-`direct.py` (המניפסט החי, לפי ה-MAC של המשימה). ‏`ctx.leases is None`
    = השומר אינו מותקן (הרצת בדיקות בלי מקור; ‏`server.main` מתקין תמיד).
    """
    if ctx.leases is None:
        return None
    client_ip = request.client.host if request.client else None
    return _identity_refusal(
        ctx, identity.verify(ctx.conn, ctx.leases, mac, client_ip), where)


def source_gate(ctx: ServerContext, request: Request, where: str) -> JSONResponse | None:
    """‏#997: זהות בלי MAC מוצהר — `GET /images/…/manifest|files`. הסוכן
    מושך אותם ב-`http_get` בלי MAC ובלי כותרת (‏`agent/lib/common.sh`), ולכן
    הכיוון הפוך: כתובת המקור → החכירה שיושבת עליה → MAC רשום ב-`machines`.
    אותו מתג, אותם קודים. הסוכן אינו משתנה ואין initrd חדש."""
    if ctx.leases is None:
        return None
    client_ip = request.client.host if request.client else None
    verdict = identity.verify_source(
        ctx.conn, ctx.leases, client_ip,
        lambda mac: registry.lookup(ctx.conn, mac) is not None)
    return _identity_refusal(ctx, verdict, where)


def agent_credentials(ctx: ServerContext, request: Request, username: str,
                      password: str, failed_detail: str
                      ) -> tuple[str | None, JSONResponse | None]:
    """‏#1282: שער הסיסמה **האחד** של נתיבי הסוכן — ‏`/agent/login`,
    ‏`/agent/pulls` ופתיחת סבב מהתחנה (`station.py`).

    ‏`(role, None)` = נכנס; ‏`(None, תשובה)` = סירוב להחזיר כמו שהוא. עד
    כאן רק `/agent/login` עבר דרך `login_guard` ובדק `must_change_password`;
    שני האחרים קראו ל-`users.verify` ישירות — ניחוש סיסמאות בלי השהיה
    ובלי נעילה, ו-admin/admin של התקנה טרייה נכנס לפני שהוחלף.
    ‏`failed_detail` הוא שורת היומן של `agent_login_failed`."""
    ip = request.client.host if request.client else "?"
    try:
        login_guard.check(ctx.conn, username, ip)
    except login_guard.LoginBlocked as exc:
        return None, _error(exc.status, exc.message,
                            "login_locked" if exc.status == 403 else "login_delayed")
    role = users.verify(ctx.conn, username, password)
    if role is None:
        login_guard.record_failure(ctx.conn, username, ip)
        journal(ctx.conn, "agent_login_failed", failed_detail)
        return None, _error(401, "wrong username or password", "bad_login")
    login_guard.clear(ctx.conn, username, ip)
    if users.flags(ctx.conn, username)["must_change_password"]:
        return None, _error(403, "החלף סיסמה בקונסולה קודם", "password_change_required")
    return role, None


def create_agent_router(ctx: ServerContext,
                        server_base: str | None = None,
                        data_dir: Path | None = None) -> APIRouter:
    """‏server_base — כתובת וילן ההפצה (מ---server-url). ‏hello שהתקבל על
    כתובת מקומית אחרת דורש כניסה תמיד (#42); בלעדיה אין עם מה להשוות,
    וההתנהגות היא הישנה.
    """
    router = APIRouter(prefix="/api/v1")

    @router.post("/agent/hello")
    async def agent_hello(request: Request) -> JSONResponse:
        """‏#1892: מחיצת ה-DB מלאה ו-hello שצריך לכתוב (סבב, בקשת כיבוי) —
        ‏503 מוצהר עם סיבה, ולא 500. הסוכן חוזר לדיסק המקומי כמו בכל כשל
        (עיקרון 1); מכונה לא רשומה עונה known:false כרגיל (`build_answer`)."""
        try:
            return await _agent_hello(request)
        except sqlite3.OperationalError as exc:
            if not disk_full(exc):
                raise
            note_disk_full("hello")
            return _error(503, "the server's database partition is full", "db_full")

    async def _agent_hello(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "body is not JSON", "bad_json")
        if not isinstance(body, dict):
            return _error(400, "body is not an object", "bad_json")

        return await run_in_threadpool(_agent_hello_body, request, body)

    def _agent_hello_body(request: Request, body: dict) -> JSONResponse:
        """All registry/session/sqlite work runs on a worker with its own connection."""

        mac = lenient_mac(body.get("mac"))
        if mac is None:
            return _error(400, "missing or malformed mac", "bad_mac")
        # ‏#1127: המלאי/ה-probe נשמרים כ-JSON בלי תקרה; הסוכן
        # שלנו לעולם לא מתקרב לזו, ומי שכן — נאמר לו, לא נשמר.
        oversized = hello_payload_oversized(body)
        if oversized:
            return _error(413, f"hello field {oversized} exceeds the size or depth cap", "payload_too_large")
        refused = identity_gate(ctx, mac, request, "hello")
        if refused is not None:
            return refused

        # ‏#524: זיהוי לפי כל כרטיס שהמכונה דיווחה, לא רק כרטיס האתחול.
        # ערך פגום מתעלמים ממנו — כמו שדה לא ידוע, לא כמו MAC ראשי חסר.
        all_macs: list[str] = []
        raw_all = body.get("all_macs")
        if isinstance(raw_all, list):
            for item in raw_all:
                extra = lenient_mac(item)
                if extra is not None and extra not in all_macs:
                    all_macs.append(extra)

        client_ip = request.client.host if request.client else None
        disks = body.get("disks") if isinstance(body.get("disks"), list) else None
        optical = drawer_slots.clean_optical(body.get("optical")) if "optical" in body else None
        reported_ip = body.get("ip") if isinstance(body.get("ip"), str) else None
        off_vlan = off_deploy_vlan(request.scope, server_base)
        # ‏hello של דופק (`joining: false`) אומר "אני חי" בלי לבקש להצטרף.
        # מכונה שנעצרה על שגיאה חייבת להישאר נראית בקונסולה, אבל אסור
        # שתיספר כמצטרפת לגל שהיא לא תבצע (#64). סוכן ישן אינו שולח את
        # השדה — והיעדרו נשאר "מצטרף", כמו תמיד.
        joining = body.get("joining")
        if not isinstance(joining, bool):
            joining = True
        # ‏#839: סוד המוניטור של האתחול הזה — 32 ספרות hex קנוניות, כמו
        # שמונה-עשר הבייטים של MAC. ערך פגום נזנח כמו שדה לא ידוע; סוכן
        # ישן אינו שולח אותו כלל, והשורה שומרת את הסוד הקודם (COALESCE).
        monitor_secret = body.get("monitor_secret")
        if not well_formed_monitor_secret(monitor_secret):
            monitor_secret = None
        # ‏#1077: נרשם רק כשיש סוד תקין באותו hello — אחרת COALESCE היה
        # משאיר hmac על סוכן שחזר אחורה.
        monitor_auth = body.get("monitor_auth")
        if monitor_secret is None or not well_formed_monitor_auth(monitor_auth):
            monitor_auth = None
        # ‏#720 (schema 2): המלאי החומרתי — DMI, PCI, TPM — למיפוי דרייברים.
        # פגום נזנח כמו שדה לא ידוע; סוכן ישן אינו שולח אותו, והגרסה
        # השמורה (אם יש) נשארת.
        hw_inventory = inventory.well_formed(body.get("inventory"))
        # ‏#1049 שלב ב' + #1048: בדיקת המכונה (probe) ו-netprobe (כבל/LLDP).
        # netprobe מגיע ב-hello כשדה אח (מקביל ל-probe) ונשמר בתוך אותה
        # שורת machine_probe. חסר/פגום → None, הגרסה הקודמת נשארת.
        raw_probe = body.get("probe")
        if isinstance(raw_probe, dict) and "netprobe" in body:
            raw_probe = {**raw_probe, "netprobe": body.get("netprobe")}
        elif raw_probe is None and "netprobe" in body:
            raw_probe = {"netprobe": body.get("netprobe")}
        hw_probe = probe.well_formed(raw_probe)
        # ‏#906: המכונה ממתינה לאדם (שאלת SMART/אדום, מסך FAILED) ואומרת
        # על מה. רק `waiting_for: "operator"` עם `prompt` מחרוזת לא-ריקה
        # נשמר (קצוץ ל-PROMPT_MAX_CHARS); כל צורה אחרת — וגם היעדר השדה,
        # שהוא "כבר לא ממתינה" — מנקה. ‏net_seen כותב אותו תמיד, בלי COALESCE.
        prompt = body.get("prompt")
        if (body.get("waiting_for") != "operator" or not isinstance(prompt, str)
                or not prompt.strip()):
            prompt = None
        else:
            prompt = prompt.strip()[:PROMPT_MAX_CHARS]
        # ‏#402: מה הסוכן מצא כשספר אפס דיסקים (ממשק 2). רק ארבעת הערכים
        # המוכרים נשמרים; כל צורה אחרת — וגם היעדר (סוכן ישן) — היא None,
        # והשורה שומרת את הערך הקודם (COALESCE), כמו disks_json.
        disk_probe = body.get("disk_probe")
        if disk_probe not in DISK_PROBE_VALUES:
            disk_probe = None
        # ‏#1367 (נדב 14/10): שיכפול מקומי כותב במכונה עכשיו. רק "copying" נשמר;
        # כל צורה אחרת, וגם היעדר, היא "לא" — ומנקה (net_seen כותב תמיד).
        local_clone = "copying" if body.get("local_clone") == "copying" else None
        # ‏#500: הקושחה (uefi/bios) ומצב ה-Secure Boot כפי שהסוכן קרא מה-EFI
        # var. רק הצורה המדויקת נשמרת; כל דבר אחר = None והשורה שומרת את
        # הערך הקודם (COALESCE) — "לא דווח" אינו "כבוי".
        firmware = body.get("firmware")
        if firmware not in ("uefi", "bios"):
            firmware = None
        secure_boot = body.get("secure_boot")
        secure_boot = int(secure_boot) if isinstance(secure_boot, bool) else None
        agent_version = body.get("agent_version")
        # A missing/invalid version in an old hello is explicitly unknown.
        if (not isinstance(agent_version, str) or not agent_version
                or len(agent_version) > 128
                or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-" for c in agent_version)):
            agent_version = "unknown"
        answer = build_answer(
            ctx.conn, ctx.library, ctx.store, mac,
            disks=disks, client_ip=client_ip, joining=joining,
            reported_ip=reported_ip, off_vlan=off_vlan,
            all_macs=all_macs, monitor_secret=monitor_secret,
            monitor_auth=monitor_auth,
            hw_inventory=hw_inventory, hw_probe=hw_probe,
            prompt=prompt, disk_probe=disk_probe,
            firmware=firmware, secure_boot=secure_boot,
            agent_version=agent_version, local_clone=local_clone, optical=optical,
            optical_reported=True,
            # ‏#1952: מי קיבל את המחיצה הקודמת עד סופה — מהלוג של מחשב הבנייה.
            direct_finished=direct.clean_finished(body),
            # ‏#715: פרמטרי השידור למקור שהוא מחשב בנייה — של המנוע, אם יש.
            multicast=ctx.sender.multicast_params() if ctx.sender is not None else None,
        )
        # ‏#980: בקשת כיבוי מהקונסולה נוסעת באותו hello, באותו קצב — בלי
        # polling חדש. רק ב-hello האמיתי (לא בתפריט האתחול), ורק למכונה
        # רשומה; השדה חסר כשאין בקשה, וסוכן ישן מתעלם ממנו.
        if answer["known"]:
            power_action, power_confirmed = room_console.take_power(ctx.conn, mac)
            # ‏#2031: hello מצטרף = המכונה חזרה לתור; כיבוי שנמסר = היא יוצאת מההחזקה
            if joining or power_action is not None:
                held.clear(ctx.conn, mac)
            if power_action is not None:
                answer["power_action"] = power_action
                # ‏#1367: רק בקשה שאושרה מעל אזהרת השיכפול המקומי מכבה מכונה שכותבת
                if power_confirmed:
                    answer["power_confirmed"] = True
            # ‏#989 ס׳ 2: שאלת ה-SMART — נרשמת, והתשובה המנצחת (קונסולה או
            # המכונה עצמה, הראשונה גוברת) חוזרת באותה תשובה. שדה חסר = אין מה לבצע.
            ask_answer = smart_ask.note_hello(
                ctx.conn, mac, smart_ask.well_formed_ask(body.get("ask")),
                smart_ask.well_formed_claim(body.get("ask_answered")))
            if ask_answer is not None:
                answer["ask_answer"] = ask_answer
        log.info("hello from %s (%s): known=%s off_vlan=%s",
                 # codeql[py/log-injection] false positive: MAC מנורמל, client_ip מה-socket, והשאר bool (#291)
                 mac, client_ip, answer["known"], off_vlan)
        # ‏hello מוכיח שהסוכן רץ. אם השרת שלח את המכונה הזאת לדיסק
        # המקומי ובכל זאת הסוכן ענה — השרשור לדיסק נכשל, וזה מה שמסך
        # הבריאות מראה (#112). ניטור בלבד: לא נוגע בתשובה שנשלחת.
        agent_loops.note(ctx.conn, ctx.store, mac, answer, off_vlan=off_vlan)
        # ובשורה נפרדת משלו: המכונה מדברת איתנו מרשת שאינה וילן ההפצה
        # (#137). לא לולאה — ולכן לא נספר שם — אבל כן אירוע שהמפעיל
        # צריך לראות, בין אם הוא עומד ליד המחשב בכוונה ובין אם המחשב
        # חובר לשקע הלא נכון. התראה, לא שער: התשובה למעלה כבר נבנתה.
        foreign_vlan.note(ctx.conn, mac, request.scope, off_vlan=off_vlan)
        # 11/10: "the GUI did not come up" -- the agent's one hello before it
        # reboots to the local disk. An alert in the journal, never a gate.
        if "gui_failed" in body:
            gui_failure.note(ctx.conn, mac, body.get("gui_failed"), known=bool(answer["known"]))
        # ‏#1847: hello = המכונה ערה — סוגר הערת WoL פתוחה (אם יש).
        learn_wol.answered(ctx.conn, mac)
        # ‏#1847: פעם אחת לכל אתחול, לא בכל hello (היה מציף את היומן כל 5 דק').
        if (body.get("wol_armed") is False and answer["known"]
                and learn_wol.arm_failure_is_new(ctx.conn, mac)):
            journal(ctx.conn, "wol_arm_failed", mac)
        # #1447: the agent has no NTP and the RTC may drift or have a dead
        # battery (cloner 2 ran 13 min ahead on metal 16/10). The server's
        # clock, read last so it is as fresh as the reply; hello.sh sets
        # the machine's clock from it. Only in the real hello, not the menu.
        answer["now"] = int(time.time())
        return JSONResponse(answer)

    @router.post("/agent/login")
    async def agent_login(request: Request) -> JSONResponse:
        """כניסה ממסך השחזור בתחנה (סעיף 13.2: סיסמה → תחנה בודדת).

        אותם משתמשים של הקונסולה — אין סיסמה מקומית שיכולה לדלוף ממכונה
        שתלמידים שולטים בה (סעיף 15). ההצלחה אינה מחזירה טוקן: הסוכן
        ממשיך באותה שיחה, וכל פעולה ממילא עוברת דרך השרת.
        """
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "body is not JSON", "bad_json")
        if not isinstance(body, dict):
            return _error(400, "body is not an object", "bad_json")
        username = body.get("username", "")
        mac = lenient_mac(body.get("mac"))
        if mac is None:
            return _error(400, "missing or malformed mac", "bad_mac")
        # ‏#997: הזהות לפני הסיסמה — זר אינו מנסה סיסמאות בשם מכונה, ואינו
        # מייצר `agent_login_failed` על מכונה שאינה שלו.
        refused = identity_gate(ctx, mac, request, "login")
        if refused is not None:
            return refused
        role, refused = agent_credentials(ctx, request, username, body.get("password", ""),
                                          f"{username} at {mac}")
        if refused is not None:
            return refused
        journal(ctx.conn, "agent_login", f"{username} at {mac}")
        return JSONResponse({"ok": True, "role": role})

    @router.post("/agent/pulls")
    async def open_pull(request: Request) -> JSONResponse:
        """פתיחת משיכת יוניקאסט — התחנה מודיעה לשרת שהיא מתחילה למשוך.

        לא "בקשת רשות למשוך": קבצי האימג' מוגשים ממילא (‏`/api/v1/images`),
        וההרשמה כאן היא כדי שהעבודה תיראה — במבט-העל, ביומן ובדיווחי
        ההתקדמות. מה שכן נאכף כאן הוא בדיוק מה ש-hello מכריז עליו:
        כניסה, לפי ההגדרה ולפי הווילן (#42) — הצהרה שהסוכן מציית לה
        אינה אכיפה.

        הזרם עצמו אינו תופס את חריץ השידור: כמה משיכות במקביל, וגם
        בזמן סבב כיתה (#60).
        """
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "body is not JSON", "bad_json")
        if not isinstance(body, dict):
            return _error(400, "body is not an object", "bad_json")

        mac = lenient_mac(body.get("mac"))
        if mac is None:
            return _error(400, "missing or malformed mac", "bad_mac")
        refused = identity_gate(ctx, mac, request, "pulls")      # #997
        if refused is not None:
            return refused
        machine = registry.lookup(ctx.conn, mac)
        if machine is None:
            # עיקרון 1: מכונה שאיננה מכירים לא מקבלת עבודה, גם לא משלה.
            pulls.journal_refusal(ctx.conn, mac, "MAC לא רשום")
            return _error(403, "this mac is not registered", "unknown_mac")
        image_id = body.get("image_id", "")
        manifest = ctx.library.get(image_id)
        if manifest is None:
            pulls.journal_refusal(ctx.conn, mac, f"אימג' {image_id} לא קיים")
            return _error(404, "unknown image", "no_image")
        # ‏#381: אימג' שנקלט ממחשב כיתה שייך למכונה שנקלט ממנה. הסירוב
        # **גלוי** — הודעה שאומרת של מי הוא ומה ביקשנו — ולא נפילה שקטה
        # לדיסק מקומי (עיקרון 5).
        refusal = restore_refusal(manifest, [mac])
        if refusal is not None:
            pulls.journal_refusal(ctx.conn, mac, refusal)
            return _error(403, refusal, "image_bound_to_another_machine")

        session = ctx.store.active_for_group(machine["group_id"])
        has_open = (session is not None and session["state"] == "open"
                    and ctx.store.in_roster(session, mac))
        username = str(body.get("username") or "").strip()
        # 11/10 (Nadav: "הכול גואי"): the GUI restore carries the console
        # session the operator signed in with on the build machine's GUI.
        # A live, plain session of an existing account is the authorization
        # -- and the pull is opened in that user's name, so the console can
        # follow it -- without a second password. A cookie that does not read
        # back as a session is no session: the password road below decides.
        gui_session = read_session(ctx.conn, request.cookies.get(COOKIE_NAME))
        if gui_session is not None and gui_session.purpose not in ("", PURPOSE_KIOSK):
            gui_session = None     # mid password change / MFA enrollment: not a session for work
        if gui_session is not None:
            username = gui_session.username
        elif login_required(ctx.conn, has_open,
                            off_deploy_vlan(request.scope, server_base)):
            _role, refused = agent_credentials(ctx, request, username,
                                               body.get("password", ""),
                                               f"{username} at {mac} pull")
            if refused is not None:
                return refused

        try:
            session_id = pulls.open_pull(
                ctx.conn, ctx.store, mac, machine["group_id"], image_id, username,
            )
        except SessionError as exc:
            pulls.journal_refusal(ctx.conn, mac, str(exc))
            return _error(409, str(exc), "pull_conflict")
        # codeql[py/log-injection] false positive: MAC מנורמל, image_id קיים בספרייה, session_id נוצר בשרת (#291)
        log.info("unicast pull %s for %s (%s)", session_id, mac, image_id)
        return JSONResponse({"id": session_id, "kind": "unicast",
                             "image_id": image_id})

    @router.post("/agent/progress")
    async def agent_progress(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "body is not JSON", "bad_json")
        if not isinstance(body, dict):
            body = {}
        # ‏#855: הזהות נבדקת לפני הדיווח, בסבב ובמשימה כאחד — ורק כשיש MAC
        # לבדוק; ‏MAC שאינו נקרא נשאר `bad_mac` של `reports.ingest`, כי
        # "לא הצלחנו לקרוא את המזהה" קודם ל"המזהה אינו תואם" (עיקרון 5).
        mac = lenient_mac(body.get("mac"))
        if mac is not None:
            refused = identity_gate(ctx, mac, request, "progress")
            if refused is not None:
                return refused
        if body.get("state") == "held" and "session_id" not in body and "task_id" not in body:
            # ‏#2031: משכפל שמחזיק את התוצאות עד "כבה" (#2024) — בלי סבב ובלי משימה.
            # ראיית חיים (‏progress_seen), לא hello: אינו מצטרף לגל ואינו "נראה" ל-#1721.
            if mac is None:
                return _error(400, "missing or malformed mac", "bad_mac")
            if registry.lookup(ctx.conn, mac) is None:
                return _error(403, "unknown machine", "unknown_machine")
            held.note(ctx.conn, mac, held.well_formed(body.get("problems")))
            agent_seen(ctx.conn, mac)
            reply: dict = {"ok": True}
            power_action, _confirmed = room_console.take_power(ctx.conn, mac)
            if power_action is not None:      # #980: "כבה" מהקונסולה — באותה תשובה
                held.clear(ctx.conn, mac)
                reply["power_action"] = power_action
            return JSONResponse(reply)
        result = reports.ingest(ctx.conn, body,
                                token=request.headers.get(TOKEN_HEADER, ""))
        if result.get("code") == "bad_token":     # #855: כמו ההעלאה — 403
            return JSONResponse(result, status_code=403)
        # ‏#1839: דיווח שהתקבל (זהות + חבר בסבב פתוח / אסימון משימה) הוא ראיה
        # שהמכונה חיה — בזמן כתיבה זה הקשר היחיד שלה, ובלעדיו "לא מחובר" אחרי 90ש'.
        if result.get("ok") and mac is not None:
            agent_seen(ctx.conn, mac)
        if result.get("ok") and body.get("task_id") and body.get("state") == "failed":
            discard_failed_staging(ctx.conn, ctx.library.root, body["task_id"])   # #1915
        return JSONResponse(result, status_code=200 if (result.get("ok") or result.get("code") == "not_open") else 400)

    @router.post("/agent/disk-event")
    async def agent_disk_event(request: Request) -> JSONResponse:
        """בריאות SMART וההכרעה על דיסק יעד (#652), לצפייה ולריבוט-החלפה.

        best-effort מצד הסוכן: כשל כאן אינו מפיל שחזור. השרת שומר את
        התמונה החיה ואינו מכריע ממנה — ההכרעה נעשתה בסוכן, ליד המכונה.
        """
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "body is not JSON", "bad_json")
        if not isinstance(body, dict):
            body = {}
        # ‏#997: כמו progress — הזהות נבדקת כשיש MAC לבדוק; MAC שאינו נקרא
        # נשאר `bad_mac`/`bad_event` של `disk_events.ingest`.
        mac = lenient_mac(body.get("mac"))
        if mac is not None:
            refused = identity_gate(ctx, mac, request, "disk-event")
            if refused is not None:
                return refused
        result = disk_events.ingest(ctx.conn, body)
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)

    @router.post("/agent/local-clone")
    async def agent_local_clone(request: Request) -> JSONResponse:
        """#1322 D: a cloning machine's report of a local clone -- one
        journal line, labelled as the machine's own. Best effort from the
        agent's side; the copy never passed through the server."""
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "body is not JSON", "bad_json")
        if not isinstance(body, dict):
            return _error(400, "body is not an object", "bad_json")
        mac = lenient_mac(body.get("mac"))
        if mac is None:
            return _error(400, "missing or malformed mac", "bad_mac")
        refused = identity_gate(ctx, mac, request, "local-clone")
        if refused is not None:
            return refused
        try:
            line = local_clone_report.journal_line(body)
        except local_clone_report.BadReport as exc:
            return _error(400, str(exc), "bad_report")
        journal(ctx.conn, "local_clone_note" if local_clone_report.has_note(body) else "local_clone", f"{mac}: {line}")   # #2049
        return JSONResponse({"ok": True})

    @router.post("/agent/log")
    async def agent_log(request: Request) -> JSONResponse:
        """‏#1882: הקטע האחרון של לוג הסוכן, gzip, פעם אחת בסוף סבב. ‏mac/‏session_id/‏reason
        ב-query (הגוף הוא הלוג עצמו). best effort מצד הסוכן — כשל כאן אינו משנה דבר שם."""
        mac = lenient_mac(request.query_params.get("mac"))
        if mac is None:
            return _error(400, "missing or malformed mac", "bad_mac")
        refused = identity_gate(ctx, mac, request, "agent-log")
        if refused is not None:
            return refused
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > agent_logs.MAX_BODY:
            return _error(413, "log too large", "too_large")
        # ‏Content-Length אינו חובה (chunked): הגוף נקרא כזרם עם מונה, ובקשה שעוברת את
        # התקרה נעצרת ב-413 ברגע שעברה — לא נקראת כולה לזיכרון (סקירה, #1882).
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > agent_logs.MAX_BODY:
                return _error(413, "log too large", "too_large")
        result = agent_logs.receive(
            ctx.conn, mac, request.query_params.get("session_id"),
            request.query_params.get("reason"), bytes(body),
            known=registry.lookup(ctx.conn, mac) is not None)
        status = 200 if result.get("ok") else 403 if result.get("code") == "unknown_machine" else 400
        return JSONResponse(result, status_code=status)

    @router.post("/agent/shrink-open")
    async def agent_shrink_open(request: Request) -> JSONResponse:
        """‏#926: הפריסה המקורית של מחיצת המקור, **לפני** `ntfsresize -s`.

        ההפך מ-disk-event: **לא** best-effort. הסוכן מכווץ רק אחרי 2xx
        עם `ok` — בלי רשומה בשרת אין כתיבה למקור (עיקרון 5).
        """
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "body is not JSON", "bad_json")
        if not isinstance(body, dict):
            body = {}
        mac = lenient_mac(body.get("mac"))
        if mac is not None:                       # #997; None → bad_mac של open_record
            refused = identity_gate(ctx, mac, request, "shrink-open")
            if refused is not None:
                return refused
        try:
            row = shrink_records.open_record(ctx.conn, body)
        except shrink_records.BadRecord as exc:
            return _error(400, str(exc), exc.code)
        except shrink_records.AlreadyOpen as exc:
            return JSONResponse({"ok": False, "error": "this disk already has an open shrink record",
                                 "code": "already_open", "id": exc.record_id,
                                 "opened_at": exc.opened_at}, status_code=409)
        return JSONResponse({"ok": True, "id": row["id"]})

    def _shrink_identity(body: dict, request: Request, where: str) -> JSONResponse | None:
        # ‏#997: הסידורי הוא הזהות של הרשומה, אבל ה-MAC — שהסוכן שולח תמיד
        # (‏`shrinkmem.sh`) — הוא הזהות של **הפונה**. בלעדיו אין מה להשוות לחכירה.
        mac = lenient_mac(body.get("mac"))
        if mac is None:
            return _error(400, "missing or malformed mac", "bad_mac")
        return identity_gate(ctx, mac, request, where)

    @router.post("/agent/shrink-close")
    async def agent_shrink_close(request: Request) -> JSONResponse:
        """‏#926: המקור הוחזר לגודלו. סגירה של מה שאינו פתוח היא 404."""
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "body is not JSON", "bad_json")
        if not isinstance(body, dict):
            return _error(400, "body is not an object", "bad_json")
        refused = _shrink_identity(body, request, "shrink-close")
        if refused is not None:
            return refused
        record_id = body.get("id") if isinstance(body.get("id"), int) else None
        if not shrink_records.close_record(ctx.conn, body.get("serial"), record_id):
            return _error(404, "no open shrink record for this serial", "not_open")
        return JSONResponse({"ok": True})

    @router.post("/agent/shrink-note")
    async def agent_shrink_note(request: Request) -> JSONResponse:
        """‏#926 (סקירת Fable): למה הרשומה עדיין פתוחה — הטבלה הוחזרה אך
        מערכת הקבצים לא נמתחה. best-effort מצד הסוכן; 404 על מה שאינו פתוח."""
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "body is not JSON", "bad_json")
        if not isinstance(body, dict):
            return _error(400, "body is not an object", "bad_json")
        refused = _shrink_identity(body, request, "shrink-note")
        if refused is not None:
            return refused
        note = body.get("note")
        if not isinstance(note, str) or not note.strip():
            return _error(400, "note must be a non-empty string", "bad_note")
        record_id = body.get("id") if isinstance(body.get("id"), int) else None
        if not shrink_records.note_record(ctx.conn, body.get("serial"), record_id, note):
            return _error(404, "no open shrink record for this serial", "not_open")
        return JSONResponse({"ok": True})

    @router.get("/images/{image_id}/manifest")
    def image_manifest(image_id: str, request: Request):
        refused = source_gate(ctx, request, "manifest")      # #997
        if refused is not None:
            return refused
        manifest = ctx.library.get(image_id)
        if manifest is None:
            # ‏#715: מזהה חי (`live_…`) — המניפסט שמחשב הבנייה דיווח. אותו
            # נתיב בדיוק, כדי שהמקבלים לא ידעו מי משדר. לפני שהגיע: 404.
            manifest = direct.live_manifest(ctx.conn, image_id)
        if manifest is None:
            return _error(404, "unknown image", "no_image")
        public = {k: v for k, v in manifest.items() if not k.startswith("_")}
        # ‏#1805/#1864 (נדב 20/10): האימות אחרי הכתיבה — "sample" (ברירת המחדל) או
        # "full" (`readback_full` בהגדרות). נקבע כשהסוכן מושך את המניפסט: לכל הסבב.
        public["readback_mode"] = readback_mode(ctx.conn)
        return JSONResponse(public)

    @router.get("/images/{image_id}/files/{filename}")
    def image_file(image_id: str, filename: str, request: Request):
        refused = source_gate(ctx, request, "files")         # #997
        if refused is not None:
            return refused
        # רשימה לבנה: מוגש רק קובץ שהמניפסט מכריז עליו בשמו המדויק.
        path = ctx.library.file_path(image_id, filename)
        if path is None:
            return _error(404, "file not in this image's manifest", "no_file")
        return FileResponse(path, media_type="application/octet-stream")

    @router.get("/images/{image_id}/readback/{index}")
    def image_readback(image_id: str, index: int, request: Request):
        """‏#1805/#1864: רשימת ה-XXH64 של מחיצה — שורה לחתיכה של 4MiB של זרם הנתונים
        (‏`server/readbackhash.py`). הסוכן בודק אותה בעצמו מול `readback_xxh64`
        שבמניפסט; אין רשימה (טרם גובב, הפצה ישירה) — 404, והסוכן אומר "לא אומת"."""
        refused = source_gate(ctx, request, "readback")
        if refused is not None:
            return refused
        manifest = ctx.library.get(image_id)
        if manifest is None or not manifest.get("_dir") or not manifest.get("_available", True):
            return _error(404, "unknown image", "no_image")
        part = next((p for p in manifest.get("partitions") or []
                     if isinstance(p, dict) and p.get("index") == index), None)
        if part is None or not readbackhash.has_list(manifest["_dir"], part):
            return _error(404, "no read-back list for this partition", "no_list")
        return FileResponse(readbackhash.list_path(manifest["_dir"], part), media_type="text/plain")

    return router
