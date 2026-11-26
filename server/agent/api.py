"""נקודות הקצה שהסוכן צורך — ממשקים 2, 3, 4 ו-7.

הכללים כאן זהים לצד השני של הרשת: לא זורקים חריגות החוצה, כל כשל
מוחזר כתשובה שסופה דיסק מקומי או שגיאה מסודרת בתבנית המוסכמת.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.concurrency import run_in_threadpool

from boot.grub_menu import normalize_mac as lenient_mac

from server.agent import loops as agent_loops, identity, inventory, probe, registry, reports
from server.agent.reportorder import STALE, STALE_REPLY, ReportOrder   # #3331 (I14)
from server.console import login_guard, users
from server.rounds import pulls
from server.rounds.chunksend import CHUNK, RosterReadError
from server.rounds import ledger   # #2528
from server.rounds import nic_live   # #3277
from server.brain import resume as brain_resume   # #2528 שלב 4 (K11)
from server.net import foreign_vlan
from server.disks import disk_events, drawer_slots, shrink_records
from server.console.auth import COOKIE_NAME, PURPOSE_KIOSK, read_session
from server.db import agent_seen, disk_full, journal, note_disk_full, SqlConnection
from server.rounds import chunkledger, direct, room_console
from server.rounds.durableseq import DurableSeq
from server.rounds import prodline, room_state   # #3181
from server.brain import agentsenses, capability as brain_capability, fill as brain_fill   # #3064 (חושי הסוכן, שער AHCI); #3181 (K5); #4090
from server.brain import joinwatch as brain_join   # #3064: המצטרף (B1, B2, B4, B17-B46), צל
from server.brain import peer as brain_peer   # B29: השלמה ממשכפל שכן, בצל
from server.brain import completing as brain_completing   # #3064 (B16, B34, B30)
from server.brain import slowdisk as brain_slowdisk   # #3268 (B62): דיסק איטי לפני הסבב -- היסטוריה + הבדיקה עכשיו
from server.brain import linkverdict as brain_link   # #3663 (A10, G2): בדיקת כבל חיה ובאנר הקישור האיטי -- המוח מחליט
from server.brain import nicbus
from server.agent import gui_failure, local_clone_report, readbackhash
from server.learn import wol as learn_wol
from server.agent import logs as agent_logs   # #1882
from server.agent import held# #2031
from server.agent import timing as agent_timing   # #2056
from server.disks import disk_sets   # #1857
from server.disks import writefloor, hdd_policy   # #2338; X16: the HDD policy switch
from server.agent import firmware_image# #2080
from server.agent import hello_eta   # #1843
from server.agent import unknown_screen   # #1802
from server.agent import body as agent_body   # #2410/#2416
from server.agent.readbackhash import mode as readback_mode
from server.agent.hello import (build_answer, hello_payload_oversized, journal_unknown, login_required,
                                off_deploy_vlan, well_formed_monitor_auth,
                                well_formed_monitor_secret)
from server.library.images import ImageLibrary, restore_refusal
from server.rounds.sessions import SessionError, SessionStore
from server.core.tasks import TOKEN_HEADER, discard_failed_staging

if TYPE_CHECKING:           # the objects `server.app` attaches to the context (imported here only for their types)
    from server.brain.route import Tracker
    from server.rounds.chunksend import ChunkEngine
    from server.rounds.directchunk import DirectChunks
    from server.rounds.indexprep import IndexPrep

log = logging.getLogger("imagesrv.api")

#: #906: אורך השאלה שנשמרת מ-hello (`prompt`). שורת מסך אחת של הסוכן
#: היא ~60 תווים; מעבר לזה נקצץ, לא נדחה.
PROMPT_MAX_CHARS = 200

#: #2533: כמה זמן נשמר הנתיב של קובץ מחיצה שהוגש (`image_file`), במקום סריקת הספרייה לכל בקשת Range.
FILE_PATH_TTL_S = 10.0

#: #402: הערכים של `disk_probe` בממשק 2. `unchecked` ≠ `no_disks`
#: (עיקרון 5): "לא הצלחנו לספור פורטים" אינו "אפס פורטים". #2334: `no_driver`
#: = בקר אחסון בלי מנהל התקן.
DISK_PROBE_VALUES = frozenset({"drives", "no_disks", "no_ports", "unchecked", "no_driver"})

#: #2489 (7): כמה פריטים מ-`all_macs` של hello נבדקים. הסוכן שולח את כל ממשקי
#: הרשת (`sysinfo.sh`), ומחשב מכללה מחזיק אחד עד ארבעה; מה שמעבר לזה נשמט.
HELLO_MAX_ALL_MACS = 16


@dataclass
class ServerContext:
    conn: SqlConnection
    library: ImageLibrary
    store: SessionStore
    #: #2533: מנוע החתיכות (`chunksend.ChunkEngine`), המנוע היחיד מ-#3173. None = בדיקות שבונות ctx
    #: ביד, ואז אין שידור.
    chunk: ChunkEngine | None = None
    #: #3161: הפצה ישירה במנוע החתיכות (`directchunk.DirectChunks`) -- מה שמחשב הבנייה העלה, וההזמנה
    #: הפתוחה אליו. None = בדיקות שבונות ctx ביד.
    direct_chunk: DirectChunks | None = None
    #: #2533: המספר הרץ של דיווחי `durable` בדלתא, לכל (סבב, מכונה, ערוץ). בזיכרון בלבד, במכוון:
    #: שרת שאותחל אינו יודע מה קיבל, ולכן מבקש הכול מחדש (`durable_full`).
    durable_seq: DurableSeq = field(default_factory=DurableSeq)
    #: #3331 (I14): `(boot_id, seq)` האחרון שהתקבל לכל מכונה -- דיווח התקדמות ישן ממנו נזרק. בזיכרון בלבד, כמו `durable_seq`.
    report_order: ReportOrder = field(default_factory=ReportOrder)
    #: #3181 (K5): כמה חסר למגירה שחזרה ומאיפה להשלים -- המצב של המוח (תקציב לכל משכפל), בזיכרון בלבד.
    fill: brain_fill.Brain = field(default_factory=brain_fill.Brain)
    #: #3064: המוח של המצטרף (`joinwatch`) -- במצב צל בלבד, בזיכרון; אינו מוסיף דבר לתשובה לסוכן.
    join: brain_join.Watch = field(default_factory=brain_join.Watch)
    #: B29: מאיפה להשלים -- מגירה שכנה, משכפל שכן או השרת. במצב צל: נרשם ב-`brain_decisions`, והביצוע נשאר HTTP.
    peer: brain_peer.Brain = field(default_factory=brain_peer.Brain)
    #: #3064 (B16, B34, B30): מגירה שמשלימה נרשמת כהחזקה, ומגירה חשודה מאומתת ולא נמחקת. בזיכרון בלבד.
    completing: brain_completing.Completing = field(default_factory=brain_completing.Completing)
    drivers: object | None = None     # DriverLibrary (#720); None בבדיקות ישנות
    #: #855: מקור חכירות ה-DHCP (`identity.LeaseFile`) — הצד שנוגע בקובץ,
    #: מוזרק כמו `dhcp_hooks`. `server.main` מתקין אותו תמיד; None =
    #: הרצת בדיקות בלי מקור, ואז שומר הזהות אינו מותקן (וה-/health אומר).
    leases: object | None = None
    #: #1128: תיקיית הנתונים — לגיבוי ההגדרות מהקונסולה; None בבדיקות ישנות.
    data_dir: Path | None = None
    #: #705: השירותים לכל כרטיס (`nic_services.NicServices`) — `create_runtime`
    #: מתקין תמיד; None = בדיקות שבונות ctx ביד, ואז אין שער (ההתנהגות של היום).
    nic_services: object | None = None
    #: #1949: (אירוע, פירוט) → [מתי נרשם, כמה נספרו מאז] — ריסון היומן
    #: של סירובי הזהות (`_identity_repeats`). לכל אפליקציה, לא גלובלי.
    #: #2487: גם `("unknown_mac", mac)` — שורת ה-hello של מכונה לא רשומה.
    identity_journaled: dict = field(default_factory=dict)
    #: #2498: אירוע → [תחילת החלון, כמה מפתחות שונים נרשמו, כמה נספרו בלבד]
    #: (`_spend_journal_budget`).
    journal_budget: dict = field(default_factory=dict)
    #: #1973: מנטר ה-UPS (`ups.UpsMonitor`) — `server.main` בלבד; None = אין מנטר.
    ups: object | None = None
    #: #2684: האם שעון השרת מסונכרן (`hello_eta.ClockTrust`) -- אותה ראיה שה-hello נושא
    #: למשכפל (`clock`), וממנה גם ה-`clock` של ה-forecasts שהקונסולה מקבלת. None (בדיקות
    #: שבונות ctx ביד, שרת בלי הוק) = "לא הוכח": הקונסולה לא תצייר שעה.
    clock_trust: object | None = None
    #: #3064 (V6, שלב 2): מונה הבייטים שהשרת הגיש ב-HTTP של קובצי האימג' (`brain.served.Served`) -- ראיית חיים
    #: שאינה תלויה בסוכן. None (בדיקות שבונות ctx ביד) = לא סופרים, ושום דבר אחר לא משתנה.
    served: object | None = None
    #: #3064 (K7, שלב 3): תקציב הרשת של ההגשה (`brain.budget.Budget`). ב-`enforce` כל גוף של קובץ ממתין לתורו (הגינות
    #: לפי משכפל, והשידור קודם); ב-`shadow` ו-`off` אינו משהה דבר. None (בדיקות שבונות ctx ביד) = אין תקציב.
    budget: object | None = None
    #: #3231: הכנת האינדקס של מנוע החתיכות (`indexprep.IndexPrep`) -- השער של משיכה, כמו של גל (#3137).
    #: None (בדיקות שבונות ctx ביד) = אין שער, ומשיכה נפתחת כמו לפני.
    index_prep: IndexPrep | None = None
    #: #3064 (K5): HTTP מול multicast בהשלמה (`brain.route.Tracker`) -- `ended` של `chunk-pass` סוגר את ההורדה (R9). צל בלבד
    route: Tracker | None = None
    #: #3125 (V2, V6-V10, R8): החושים של השרת בזמן גל (`learn.server_senses.Senses`) -- חלון כל `WINDOW_S` שניות
    #: (`_server_senses_clock`). None (בדיקות שבונות ctx ביד) = אין חלונות; ההתחלה והסוף של הגל לא משתנים.
    senses: object | None = None


class _CountedFile(FileResponse):
    """`FileResponse` שסופר לכתובת הפונה כל גוף שנשלח (#3064, V6), ומחכה לתורו בתקציב הרשת כשהוא נאכף (K7). הספירה
    בשכבת ה-ASGI `send`, לכן היא כוללת גם תשובות Range (206) של השלמה ב-HTTP, ולא רק קובץ שלם. `Served.note` ו-
    `Budget.acquire` לעולם אינם מפילים הורדה."""

    def __init__(self, path, *, served, ip: str, budget=None, **kw):
        super().__init__(path, **kw)
        self._served, self._ip, self._budget = served, ip, budget

    async def __call__(self, scope, receive, send):
        async def counting(message):
            is_body = message.get("type") == "http.response.body"
            if is_body and self._budget is not None:
                await self._budget.acquire(self._ip, len(message.get("body") or b""))   # לא חוסם את הלולאה: האחרות ממשיכות
            await send(message)             # אחרי ה-send: בייט שה-transport לא קיבל (הפונה נעלם) אינו ראיה
            if is_body:
                self._served.note(self._ip, len(message.get("body") or b""))
        # #3064 (B10, KT7): כמה הורדות בדרך -- סופר בלבד, לעולם אינו ממתין (`Budget.enter`/`leave`)
        enter, leave = getattr(self._budget, "enter", None), getattr(self._budget, "leave", None)
        if enter is not None and leave is not None:
            enter(self._ip)
        self._served.opened(self._ip)                   # #3125 (V6): כמה הורדות פתוחות במקביל, לכל כתובת
        try:
            await super().__call__(scope, receive, counting)
        finally:
            self._served.closed(self._ip)
            if enter is not None and leave is not None:
                leave(self._ip)


def _error(status: int, message: str, code: str) -> JSONResponse:
    """תבנית השגיאות מהמוסכמות הרוחביות."""
    return JSONResponse(
        {"ok": False, "error": message, "code": code}, status_code=status
    )


def _pull_index_gate(ctx: ServerContext, mac: str, image_id: str) -> JSONResponse | None:
    """#3231: משיכה במנוע החתיכות אינה נפתחת לפני שהאינדקס של האימג' מוכן. הסוכן מוריד אותו
    (`GET …/index/<n>`) **אחרי** שטבלת המחיצות נמחקה, ואימג' שנקלט זה עתה לא היה לו כזה: 404 על דיסק
    ריק. None = מוכן (או שאין שער). 503 `index_preparing` = עוד נגזר (`curl --retry` של
    הסוכן שואל שוב); 409 `index_failed` = הגזירה נכשלה, עם הסיבה. בשניהם הדיסק לא נגע."""
    prep = ctx.index_prep
    if prep is None:
        return None
    state = prep.pull_gate(image_id)
    if state is None:
        return None
    if state["state"] == "preparing":
        return JSONResponse({"ok": False, "error": state["he"], "code": "index_preparing",
                             "percent": state["percent"]}, status_code=503, headers={"Retry-After": "2"})
    pulls.journal_refusal(ctx.conn, mac, state["error"])
    return _error(409, state["error"], "index_failed")


#: #3633: hello שנדחה 400 `bad_json` — שורת יומן אחת (מרוסנת) למקור ולסיבה, בלי הגוף.
HELLO_BAD_JSON = "hello_bad_json"


def _note_bad_hello(ctx: ServerContext, request: Request, reason: str,
                    length: int | None, pos: int | None = None) -> None:
    """#3633: 18/11 משכפל 2 קיבל 400 על כל hello במשך דקות, ויומן השרת אמר רק
    `POST /agent/hello 400` — בלי סיבה, בלי מקור, בלי אות לקונסולה (#3632 נתגלה
    בתפיסת tcpdump). הגוף לא נקרא כדי לדעת מי שלח, ולכן המפתח הוא כתובת המקור
    והסיבה; הגוף עצמו **לא** נרשם (ייתכנו בו סודות). ריסון כמו #1949/#2498:
    שורה אחת בחלון, החוזרות נספרות. הכתיבה ל-DB רצה ב-`run_in_threadpool`."""
    ip = request.client.host if request.client else "?"
    repeats = _journal_repeats(ctx, (HELLO_BAD_JSON, f"{ip}: {reason}"), budgeted=True, source=ip)
    if repeats is None:
        return
    facts = ["length unknown" if length is None else f"{length} bytes"]
    if pos is not None:
        facts.append(f"error at char {pos}")
    suffix = f" [+{repeats[0]} identical requests in the last {repeats[1]}s not journaled]" if repeats[0] else ""
    detail = f"{ip}: {reason} ({', '.join(facts)}){suffix}"
    journal(ctx.conn, HELLO_BAD_JSON, detail)
    log.warning("%s: %s", HELLO_BAD_JSON, detail)


async def _read_body(request: Request, on_bad_json: Callable[[Request, agent_body.BodyError], None] | None = None
                     ) -> tuple[object, JSONResponse | None]:
    """#2410/#2416: הגוף עם תקרת בייטים ונרמול (`body.read_json`). `(גוף, None)`,
    או `(None, תשובה)` — 413/400 בתבנית הרוחבית, **לפני** שער הזהות: גוף שלא
    נקרא אינו מגיע לשום בדיקה. #3633: ``on_bad_json`` (ה-hello) נקרא על 400 `bad_json`
    — תמיד ב-`run_in_threadpool`, כי הוא כותב ליומן (#2750); התשובה עצמה אינה משתנה."""
    try:
        return await agent_body.read_json(request), None
    except agent_body.BodyError as exc:
        if on_bad_json is not None and exc.code == "bad_json":
            await run_in_threadpool(on_bad_json, request, exc)
        return None, _error(exc.status, exc.message, exc.code)


def _identity_refusal(ctx: ServerContext, verdict: identity.Verdict,
                      where: str, source: str | None = None) -> JSONResponse | None:
    """`None` = ממשיכים. סירוב = 403 בשם (`identity_refused` /
    `identity_unverifiable`) ושורת יומן שנוקבת בנתיב — **לפני** כל רישום:
    `net_seen`, ספירת הלולאות, ההצטרפות לסבב, רשומת הכיווץ ואירוע הדיסק
    לא מתרחשים, כי הפונה אינו המכונה (#585). ``source`` — כתובת המקור של
    החיבור, לתקרה לפי מקור (#2489)."""
    if verdict.ok:
        return None
    repeats = _identity_repeats(ctx, verdict, source)
    if repeats is not None:
        suffix = (f" [+{repeats[0]} identical refusals in the last {repeats[1]}s not journaled]"
                  if repeats[0] else "")
        journal(ctx.conn, verdict.event, f"{verdict.detail} ({where}){suffix}")
        log.warning("%s: %s (%s)%s", verdict.event, verdict.detail, where, suffix)
    return _error(403, verdict.message, verdict.event)


#: #1949: סירוב זהות זהה (אותו אירוע, אותו MAC/כתובת ואותה סיבה) נרשם
#: ביומן פעם אחת בחלון כזה; החוזרים בתוכו נספרים ונכתבים כמונה בשורה
#: הבאה. R2 (22/10): 346 שורות `identity_unverifiable` ב-10 דקות — שתיים
#: כל 2 שניות — הסתירו את `client_lost` שבאמצען. הסירוב עצמו (403) אינו
#: משתנה: רק הרישום מרוסן.
IDENTITY_JOURNAL_WINDOW_S = 300
#: מעל כמה מפתחות מנקים את אלה שהחלון שלהם עבר — MAC מזויף שונה בכל
#: בקשה אינו מגדל את הזיכרון בלי גבול.
_IDENTITY_KEYS_PRUNE_AT = 256
_identity_lock = threading.Lock()
_clock = time.monotonic
#: #2498: הריסון לפי מפתח (#1949) אינו עוצר MAC אחר בכל בקשה — כל אחד הוא מפתח
#: חדש. לכן לכל אירוע מרוסן נרשמים אחד-אחד לכל היותר כל כך הרבה מפתחות **שונים**
#: בחלון; מעבר לזה הם נספרים, ושורת `JOURNAL_FLOOD` אחת אומרת שזה התחיל ואחת בחלון
#: הבא אומרת כמה (עיקרון 5). 32 — כמו `rogue_dhcp.MAX_SOURCES` (#2442): כיתה שלמה.
JOURNAL_KEYS_PER_WINDOW = 32
#: #2489 (6): תקרה שנייה, לכל כתובת מקור בתוך האירוע — פונה אחד שמחליף MAC בכל
#: בקשה אינו מכלה לבדו את `JOURNAL_KEYS_PER_WINDOW`, וסירוב של מכונה אחרת עדיין
#: נרשם בשמו. מכונה אמיתית מייצרת מפתח אחד עד שלושה (MAC, כתובת, סיבה).
JOURNAL_KEYS_PER_SOURCE = 8
JOURNAL_FLOOD = "journal_flood"


def _identity_repeats(ctx: ServerContext, verdict: identity.Verdict,
                      source: str | None = None) -> tuple[int, int] | None:
    """None = סירוב שכבר נרשם בחלון הנוכחי — נספר ולא נכתב. אחרת
    (כמה נספרו מאז השורה הקודמת, לפני כמה שניות היא נכתבה) — (0, 0) לראשון.
    #2498: סירוב הוא תמיד פונה שלא הוכח — נספר גם בתקרת המפתחות השונים,
    ו-#2489 גם בתקרה של כתובת המקור שלו."""
    return _journal_repeats(ctx, (verdict.event, verdict.detail), budgeted=True,
                            source=source)


def _journal_repeats(ctx: ServerContext, key: tuple[str, str],
                     budgeted: bool = False, source: str | None = None
                     ) -> tuple[int, int] | None:
    """#1949 לכל שורה חוזרת לפי מפתח; #2487: גם `unknown_mac` של hello, לפי MAC.
    #2498: ``budgeted`` — המפתח נספר גם ב-`JOURNAL_KEYS_PER_WINDOW` של האירוע
    (`key[0]`); מעבר לה None, כמו חוזר. #2489: ``source`` — וגם ב-
    `JOURNAL_KEYS_PER_SOURCE` של הכתובת."""
    now = _clock()
    flood: list[str] = []
    with _identity_lock:
        seen = ctx.identity_journaled.get(key)
        if seen is not None and now - seen[0] < IDENTITY_JOURNAL_WINDOW_S:
            seen[1] += 1
            return None
        admitted, flood = (_spend_journal_budget(ctx, key[0], now, source) if budgeted
                           else (True, []))
        # גם מפתח שלא נרשם נזכר: החוזרים שלו בחלון נספרים כחוזרים, ולא שוב בתקרה.
        ctx.identity_journaled[key] = [now, 0]
        if len(ctx.identity_journaled) > _IDENTITY_KEYS_PRUNE_AT:
            for old in [k for k, (at, _n) in ctx.identity_journaled.items()
                        if now - at >= IDENTITY_JOURNAL_WINDOW_S]:
                del ctx.identity_journaled[old]
    for line in flood:
        journal(ctx.conn, JOURNAL_FLOOD, line)
    if not admitted:
        return None
    return (seen[1], int(now - seen[0])) if seen is not None else (0, 0)


def _spend_journal_budget(ctx: ServerContext, event: str, now: float,
                          source: str | None = None) -> tuple[bool, list[str]]:
    """#2498: (נרשם אחד-אחד?, שורות `JOURNAL_FLOOD` לכתוב). הקורא מחזיק
    את `_identity_lock`. `ctx.journal_budget[event]` = [תחילת החלון, כמה נרשמו,
    כמה נספרו בלבד]. השורה הראשונה בחלון חדש נושאת את המונה של הקודם.
    #2489: `ctx.journal_budget[(event, source)]` = [תחילת החלון, כמה מפתחות]
    של כתובת אחת; מה שמעבר ל-`JOURNAL_KEYS_PER_SOURCE` נספר במונה של האירוע."""
    budget = ctx.journal_budget.get(event)
    lines: list[str] = []
    if budget is None or now - budget[0] >= IDENTITY_JOURNAL_WINDOW_S:
        if budget is not None and budget[2]:
            lines.append(f"{event}: +{budget[2]} more different not journaled one by one"
                         f" in the last {int(now - budget[0])}s")
        budget = ctx.journal_budget[event] = [now, 0, 0]
    if source is not None:
        mine = ctx.journal_budget.get((event, source))
        if mine is None or now - mine[0] >= IDENTITY_JOURNAL_WINDOW_S:
            mine = ctx.journal_budget[(event, source)] = [now, 0]
            if len(ctx.journal_budget) > _IDENTITY_KEYS_PRUNE_AT:
                for old in [k for k, v in ctx.journal_budget.items() if isinstance(k, tuple)
                            and now - v[0] >= IDENTITY_JOURNAL_WINDOW_S]:
                    del ctx.journal_budget[old]
        mine[1] += 1
        if mine[1] > JOURNAL_KEYS_PER_SOURCE:
            budget[2] += 1
            if mine[1] == JOURNAL_KEYS_PER_SOURCE + 1:
                lines.append(f"{event}: over {JOURNAL_KEYS_PER_SOURCE} different from {source} in"
                             f" {IDENTITY_JOURNAL_WINDOW_S}s -- the rest from it are counted,"
                             " not journaled one by one")
            return False, lines
    # budget[1] ממשיך לעלות מעבר לתקרה: השורה "over" נכתבת פעם אחת, גם כשהמונה
    # budget[2] כבר התחיל מסירובים של כתובת שעברה את התקרה שלה.
    budget[1] += 1
    if budget[1] <= JOURNAL_KEYS_PER_WINDOW:
        return True, lines
    budget[2] += 1
    if budget[1] == JOURNAL_KEYS_PER_WINDOW + 1:
        lines.append(f"{event}: over {JOURNAL_KEYS_PER_WINDOW} different in"
                     f" {IDENTITY_JOURNAL_WINDOW_S}s -- the rest are counted, not journaled one by one")
    return False, lines


def identity_gate(ctx: ServerContext, mac: str, request: Request,
                  where: str) -> JSONResponse | None:
    """#855/#997: MAC מוצהר + כתובת המקור תואמת את חכירת ה-DHCP של אותו MAC.

    שומר **אחד** לכל נתיב שמזהה מכונה לפי MAC מהגוף — hello, progress,
    login, pulls, disk-event, shrink-*, וגם `station.py` (sessions)
    ו-`direct.py` (המניפסט החי, לפי ה-MAC של המשימה). `ctx.leases is None`
    = השומר אינו מותקן (הרצת בדיקות בלי מקור; `server.main` מתקין תמיד).
    """
    if ctx.leases is None:
        return None
    client_ip = request.client.host if request.client else None
    return _identity_refusal(
        ctx, identity.verify(ctx.conn, ctx.leases, mac, client_ip), where, client_ip)


def _nic_link_journal(conn, mac: str, change: tuple | None) -> None:
    """#3277: שורה ביומן כשקישור כרטיס ההפצה ירד או חזר (`nic_live.note`). לעולם אינו זורק אל ה-hello."""
    if change is None:
        return
    kind, mbps, top = change
    try:
        journal(conn, "nic_link_low" if kind == "low" else "nic_link_back",
                f"{mac} {mbps}Mb/s" + (f" (max {top}Mb/s)" if top else ""), machine=mac)
    except Exception:    # noqa: BLE001 -- the journal line is advice, not the hello
        log.exception("nic link change of %s was not journaled", mac)


def boot_identity_ok(ctx: ServerContext, mac: str, client_ip: str | None) -> bool:
    """#1629: the boot menu's `?mac=` is not identity. Before that MAC's GUI
    tasks are cancelled on boot (#1576), the same check as `identity_gate`,
    with the same journal line and the same #1949 repeat throttle; the
    request has no response to refuse, so only the cancellation is skipped.
    `ctx.leases is None` = no identity hook installed, as in `identity_gate`."""
    if ctx.leases is None:
        return True
    verdict = identity.verify(ctx.conn, ctx.leases, mac, client_ip)
    return _identity_refusal(ctx, verdict, "boot-cancel", client_ip) is None


def boot_journal_unknown(ctx: ServerContext, mac: str, client_ip: str | None,
                         lease_matched: bool) -> None:
    """#2498: שורת `unknown_mac` של `GET /boot/menu` — מרוסנת כמו זו של ה-hello
    (#2487, אותו מפתח): אחת ל-MAC בחלון, ואחריה מונה. ``lease_matched`` = הבקשה
    על וילן ההפצה ועברה את בדיקת החכירה; רק כשהשומר באמת מותקן ודלוק זו ראיה,
    ואז המכונה אינה נספרת בתקרת המפתחות השונים — מחשב לא רשום אמיתי נרשם גם
    בזמן הצפה. כל השאר (מחוץ לוילן, שומר כבוי) — נספר בה: MAC אקראי בכל בקשה
    אינו מגדל את היומן. התשובה עצמה אינה תלויה בזה (עיקרון 1)."""
    proven = lease_matched and ctx.leases is not None and identity.enabled(ctx.conn)
    repeats = _journal_repeats(ctx, ("unknown_mac", mac), budgeted=not proven)
    if repeats is not None:
        journal_unknown(ctx.conn, mac, client_ip,
                        f" [+{repeats[0]} identical requests in the last {repeats[1]}s not journaled]"
                        if repeats[0] else "")


def gui_session(ctx: ServerContext, request: Request):
    """11/10: the console session the operator signed in with on the build
    machine's GUI, carried as the cookie (`PULL_JAR`). A live, plain session
    of an existing account -- not one mid password change or MFA enrollment;
    a cookie that does not read back as a session is none (None)."""
    session = read_session(ctx.conn, request.cookies.get(COOKIE_NAME))
    if session is not None and session.purpose not in ("", PURPOSE_KIOSK):
        return None
    return session


def _registered(ctx: ServerContext):
    """#3225: "ה-MAC הזה רשום בטבלת המכונות" -- כרטיס הרישום, לא כרטיס אתחול אחר."""
    return lambda mac: registry.lookup(ctx.conn, mac) is not None


def source_gate(ctx: ServerContext, request: Request, where: str,
                image_id: str | None) -> JSONResponse | None:
    """#997: זהות בלי MAC מוצהר — `GET /images/…/manifest|files|readback`. הסוכן
    מושך אותם ב-`http_get` בלי MAC ובלי כותרת (`agent/lib/core/common.sh`), ולכן
    הכיוון הפוך: כתובת המקור → החכירה שיושבת עליה → MAC רשום ב-`machines`.
    אותו מתג, אותם קודים.

    #2657: "רשומה" אינה מספיקה. המכונה צריכה להיות חברה בסבב או במשיכה
    פתוחים של **האימג' הזה** (`SessionStore.has_open_membership`), אחרת
    403 `image_fetch_refused`. חריג אחד, למניפסט בלבד: השחזור מהגואי מושך
    אותו לפני שהמשיכה נפתחת (`single_restore.sh`), ונושא את כניסת המפעיל
    לקונסולה (`gui_session`) — אותה הרשאה ש-`/agent/pulls` מקבל.

    #2489: ``image_id=None`` — נתיב שאינו של אימג' (`GET /agent/sessions/active`):
    מכונה רשומה על הכתובת מספיקה, בלי בדיקת החברות."""
    if ctx.leases is None:
        return None
    client_ip = request.client.host if request.client else None
    signed_in = where == "manifest" and gui_session(ctx, request) is not None
    verdict = identity.verify_source(
        ctx.conn, ctx.leases, client_ip,
        lambda mac: identity.holder_registered(ctx.conn, mac, client_ip, _registered(ctx)),   # #3225
        None if signed_in or image_id is None
        else (lambda mac: ctx.store.has_open_membership(mac, image_id)))
    return _identity_refusal(ctx, verdict, where, client_ip)


def agent_credentials(ctx: ServerContext, request: Request, username: str,
                      password: str, failed_detail: str, machine: str | None = None
                      ) -> tuple[str | None, JSONResponse | None]:
    """#1282: שער הסיסמה **האחד** של נתיבי הסוכן — `/agent/login`,
    `/agent/pulls` ופתיחת סבב מהתחנה (`station.py`).

    `(role, None)` = נכנס; `(None, תשובה)` = סירוב להחזיר כמו שהוא. עד
    כאן רק `/agent/login` עבר דרך `login_guard` ובדק `must_change_password`;
    שני האחרים קראו ל-`users.verify` ישירות — ניחוש סיסמאות בלי השהיה
    ובלי נעילה, ו-admin/admin של התקנה טרייה נכנס לפני שהוחלף.
    `failed_detail` הוא שורת היומן של `agent_login_failed`, ו-``machine`` —
    המכונה שממנה ניסו (#1296), כשהיא ידועה."""
    ip = request.client.host if request.client else "?"
    # #2443 (Fable): תקרת החשבון של פורט הסוכן — נפרדת מזו של הקונסולה.
    try:
        login_guard.check(ctx.conn, username, ip, login_guard.AGENT)
    except login_guard.LoginBlocked as exc:
        return None, _error(exc.status, exc.message,
                            "login_locked" if exc.status == 403 else "login_delayed")
    role = users.verify(ctx.conn, username, password)
    if role is None:
        login_guard.record_failure(ctx.conn, username, ip, login_guard.AGENT)
        journal(ctx.conn, "agent_login_failed", failed_detail, machine=machine)
        return None, _error(401, "wrong username or password", "bad_login")
    login_guard.clear(ctx.conn, username, ip)
    if users.flags(ctx.conn, username)["must_change_password"]:
        return None, _error(403, "החלף סיסמה בקונסולה קודם", "password_change_required")
    return role, None


def create_agent_router(ctx: ServerContext,
                        server_base: str | None = None,
                        data_dir: Path | None = None,
                        clock_status=None) -> APIRouter:
    """server_base — כתובת וילן ההפצה (מ---server-url). hello שהתקבל על
    כתובת מקומית אחרת דורש כניסה תמיד (#42); בלעדיה אין עם מה להשוות,
    וההתנהגות היא הישנה.

    `clock_status` (#1843): `() -> {"synchronized", ...}` — מצב שעון השרת (health
    `timesync_status`). בלעדיו אין שעה למשכפל: ראיה שלא הוזרקה אינה "מסונכרן".
    """
    router = APIRouter(prefix="/api/v1")
    clock_trust = (ctx.clock_trust if ctx.clock_trust is not None
                   else hello_eta.ClockTrust(clock_status))   # #2684: אותה ראיה לקונסולה ולמשכפל

    @router.post("/agent/hello")
    async def agent_hello(request: Request) -> JSONResponse:
        """#1892: מחיצת ה-DB מלאה ו-hello שצריך לכתוב (סבב, בקשת כיבוי) —
        503 מוצהר עם סיבה, ולא 500. הסוכן חוזר לדיסק המקומי כמו בכל כשל
        (עיקרון 1); מכונה לא רשומה עונה known:false כרגיל (`build_answer`)."""
        try:
            return await _agent_hello(request)
        except sqlite3.OperationalError as exc:
            if not disk_full(exc):
                raise
            note_disk_full("hello")
            return _error(503, "the server's database partition is full", "db_full")

    def _note_bad_hello_body(request: Request, exc: agent_body.BodyError) -> None:
        """#3633: ה-`ctx` נשאר כאן, מחוץ ל-route האסינכרוני (#2750)."""
        _note_bad_hello(ctx, request, exc.message, exc.length, exc.pos)

    async def _agent_hello(request: Request) -> JSONResponse:
        body, refused = await _read_body(request, _note_bad_hello_body)
        if refused is not None:
            return refused
        if not isinstance(body, dict):
            declared = request.headers.get("content-length", "")   # #3633: הגוף כבר נפרס; האורך — מה שהוצהר
            await run_in_threadpool(_note_bad_hello, ctx, request, "body is not an object",
                                    int(declared) if declared.isdigit() else None)
            return _error(400, "body is not an object", "bad_json")

        return await run_in_threadpool(_agent_hello_body, request, body)

    def _extra_ok(mac: str, extra: str, client_ip: str | None) -> bool:
        """#2439: כרטיס רשום מ-`all_macs` שאינו נמצא עכשיו במקום אחר
        (`identity.verify_extra`). כרטיס שאינו רשום אינו מזהה ממילא — לא נבדק
        ואינו מרעיש את היומן. `ctx.leases is None` — השומר אינו מותקן, כמו
        ב-`identity_gate`."""
        if ctx.leases is None or registry.lookup(ctx.conn, extra) is None:
            return True
        verdict = identity.verify_extra(ctx.conn, ctx.leases, mac, extra, client_ip)
        return _identity_refusal(ctx, verdict, "hello all_macs", client_ip) is None

    def _learn_boot_card(mac: str, all_macs: list[str], client_ip: str | None) -> None:
        """#3225: מכונה שרשומה לפי כרטיס אחר (#524) -- כרטיס האתחול שלה (`mac`, חכור לכתובת
        הפונה: `identity_gate` עבר) מקבל ראיה בשער ההפצה וב-`source_gate`. רק כרטיס רשום
        שעבר `verify_extra` נשאר ב-`all_macs` כאן, ו-`all_macs` לבדו אינו נרשם. בלי שומר
        זהות (`ctx.leases is None` או מתג כבוי) אין ראיה לכתובת, ולכן אין מה לרשום."""
        if ctx.leases is None or not identity.enabled(ctx.conn) or registry.lookup(ctx.conn, mac) is not None:
            return
        machine = registry.lookup(ctx.conn, mac, all_macs)
        if machine is not None and identity.learn_alias(ctx.conn, mac, machine["mac"], client_ip):
            journal(ctx.conn, "nic_alias_learned", f"{mac} is a card of {machine['mac']} (from {client_ip})",
                    machine=machine["mac"])

    def _agent_hello_body(request: Request, body: dict) -> JSONResponse:
        """All registry/session/sqlite work runs on a worker with its own connection."""

        mac = lenient_mac(body.get("mac"))
        if mac is None:
            return _error(400, "missing or malformed mac", "bad_mac")
        # #1127: המלאי/ה-probe נשמרים כ-JSON בלי תקרה; הסוכן
        # שלנו לעולם לא מתקרב לזו, ומי שכן — נאמר לו, לא נשמר.
        oversized = hello_payload_oversized(body)
        if oversized:
            return _error(413, f"hello field {oversized} exceeds the size or depth cap", "payload_too_large")
        refused = identity_gate(ctx, mac, request, "hello")
        if refused is not None:
            return refused

        # #524: זיהוי לפי כל כרטיס שהמכונה דיווחה, לא רק כרטיס האתחול.
        # ערך פגום מתעלמים ממנו — כמו שדה לא ידוע, לא כמו MAC ראשי חסר.
        # #2489 (7): רק `HELLO_MAX_ALL_MACS` הפריטים הראשונים, ודה-דופליקציה ב-set —
        # בלי תקרה, גוף של 1MiB נשא כ-52 אלף כתובות: סריקה ריבועית ושאילתה לכל אחת.
        all_macs: list[str] = []
        raw_all = body.get("all_macs")
        if isinstance(raw_all, list):
            seen_macs: set[str] = set()
            for item in raw_all[:HELLO_MAX_ALL_MACS]:
                extra = lenient_mac(item)
                if extra is not None and extra not in seen_macs:
                    seen_macs.add(extra)
                    all_macs.append(extra)

        client_ip = request.client.host if request.client else None
        # #2439: `all_macs` מזהה רק מה שלא נמצא עכשיו במקום אחר — אחרת הוא עקף
        # את השער שלמעלה, שבודק את `mac` בלבד. כרטיס שנדחה נשמט מהרשימה (ונרשם
        # ביומן); ה-hello עצמו נענה, ומה שנשאר מזהה כמו תמיד.
        all_macs = [extra for extra in all_macs if extra == mac or _extra_ok(mac, extra, client_ip)]
        _learn_boot_card(mac, all_macs, client_ip)
        disks = body.get("disks") if isinstance(body.get("disks"), list) else None
        optical = drawer_slots.clean_optical(body.get("optical")) if "optical" in body else None
        off_vlan = off_deploy_vlan(request.scope, server_base)
        # hello של דופק (`joining: false`) אומר "אני חי" בלי לבקש להצטרף.
        # מכונה שנעצרה על שגיאה חייבת להישאר נראית בקונסולה, אבל אסור
        # שתיספר כמצטרפת לגל שהיא לא תבצע (#64). סוכן ישן אינו שולח את
        # השדה — והיעדרו נשאר "מצטרף", כמו תמיד.
        joining = body.get("joining")
        if not isinstance(joining, bool):
            joining = True
        # #839: סוד המוניטור של האתחול הזה — 32 ספרות hex קנוניות, כמו
        # שמונה-עשר הבייטים של MAC. ערך פגום נזנח כמו שדה לא ידוע; סוכן
        # ישן אינו שולח אותו כלל, והשורה שומרת את הסוד הקודם (COALESCE).
        monitor_secret = body.get("monitor_secret")
        if not well_formed_monitor_secret(monitor_secret):
            monitor_secret = None
        # #1077: נרשם רק כשיש סוד תקין באותו hello — אחרת COALESCE היה
        # משאיר hmac על סוכן שחזר אחורה.
        monitor_auth = body.get("monitor_auth")
        if monitor_secret is None or not well_formed_monitor_auth(monitor_auth):
            monitor_auth = None
        # #2844: האם האתחול הזה הפעיל את שירות המוניטור. רק bool הוא ראיה;
        # סוכן ישן אינו שולח, וחסר נשאר "לא ידוע" (None).
        monitor_on = body.get("monitor_on")
        if not isinstance(monitor_on, bool):
            monitor_on = None
        # #720 (schema 2): המלאי החומרתי — DMI, PCI, TPM — למיפוי דרייברים.
        # פגום נזנח כמו שדה לא ידוע; סוכן ישן אינו שולח אותו, והגרסה
        # השמורה (אם יש) נשארת.
        hw_inventory = inventory.well_formed(body.get("inventory"))
        # #1049 שלב ב' + #1048: בדיקת המכונה (probe) ו-netprobe (כבל/LLDP).
        # netprobe מגיע ב-hello כשדה אח (מקביל ל-probe) ונשמר בתוך אותה
        # שורת machine_probe. חסר/פגום → None, הגרסה הקודמת נשארת.
        raw_probe = body.get("probe")
        # #3064: `netwatch` (האזנה פסיבית לרשת ההפצה: IGMP, מולטיקאסט זר) הוא אח באותה צורה
        for sibling in ("netprobe", "netwatch"):
            if sibling in body and (isinstance(raw_probe, dict) or raw_probe is None):
                raw_probe = {**(raw_probe or {}), sibling: body.get(sibling)}
        hw_probe = probe.well_formed(raw_probe)
        # #906: המכונה ממתינה לאדם (שאלת SMART/אדום, מסך FAILED) ואומרת
        # על מה. רק `waiting_for: "operator"` עם `prompt` מחרוזת לא-ריקה
        # נשמר (קצוץ ל-PROMPT_MAX_CHARS); כל צורה אחרת — וגם היעדר השדה,
        # שהוא "כבר לא ממתינה" — מנקה. net_seen כותב אותו תמיד, בלי COALESCE.
        prompt = body.get("prompt")
        if (body.get("waiting_for") != "operator" or not isinstance(prompt, str)
                or not prompt.strip()):
            prompt = None
        else:
            prompt = prompt.strip()[:PROMPT_MAX_CHARS]
        # #402: מה הסוכן מצא כשספר אפס דיסקים (ממשק 2). רק ארבעת הערכים
        # המוכרים נשמרים; כל צורה אחרת — וגם היעדר (סוכן ישן) — היא None,
        # והשורה שומרת את הערך הקודם (COALESCE), כמו disks_json.
        disk_probe = body.get("disk_probe")
        if disk_probe not in DISK_PROBE_VALUES:
            disk_probe = None
        # #1367 (נדב 14/10): שיכפול מקומי כותב במכונה עכשיו. רק "copying" נשמר;
        # כל צורה אחרת, וגם היעדר, היא "לא" — ומנקה (net_seen כותב תמיד).
        local_clone = "copying" if body.get("local_clone") == "copying" else None
        # #500: הקושחה (uefi/bios) ומצב ה-Secure Boot כפי שהסוכן קרא מה-EFI
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
        # #2533: מה שהאימג' של הסוכן יודע לקבל (ממשק 2, `engines`) -- לפני `build_answer`: ה-hello הזה
        # עצמו יכול להבשיל את הגל, והמנוע בודק את חבריו ברגע שהוא יוצא.
        if ctx.chunk is not None:
            ctx.chunk.note_agent(mac, body.get("engines"))
        # #2528: מזהה האתחול של ה-hello הזה, לפני שהסבב קורא אותו (`interrupt.on_rejoin`).
        ledger.note_boot(ctx.conn, mac, body.get("boot_id"))
        _nic_link_journal(ctx.conn, mac, nic_live.note(mac, body.get("nic_link"), body.get("boot_id"),
                                                       nic_bus=body.get("nic_bus")))
        # #4090: מהירות הקישור ששונה מזו שהיכולת הנלמדת נשמרה מולה מאפסת אותה כאן, לפני ה-precheck ו-ETA של הגל הבא; אינו זורק
        brain_capability.reset_on_hello(ctx.conn, mac, nic_live.live(mac))
        answer = build_answer(
            ctx.conn, ctx.library, ctx.store, mac,
            disks=disks, client_ip=client_ip, joining=joining,
            off_vlan=off_vlan,
            all_macs=all_macs, monitor_secret=monitor_secret,
            monitor_auth=monitor_auth, monitor_on=monitor_on,
            hw_inventory=hw_inventory, hw_probe=hw_probe,
            prompt=prompt, disk_probe=disk_probe,
            firmware=firmware, secure_boot=secure_boot,
            agent_version=agent_version, local_clone=local_clone, optical=optical,
            optical_reported=True,
            # #2487: שורת `unknown_mac` נכתבת למטה, מרוסנת לפי MAC.
            record_journal=False,
        )
        # #2533: גל שמנוע החתיכות משדר — `session.engine` ו-`session.chunk` (ממשק 3).
        if ctx.chunk is not None and isinstance(answer.get("session"), dict):
            answer["session"].update(ctx.chunk.session_fields(answer["session"]["id"]))
        # #3161: מחשב בנייה שהוא המקור של גל חתיכות -- `task.direct.engine` ו-`task.direct.chunk` (ממשק 3).
        if (ctx.direct_chunk is not None and isinstance(answer.get("task"), dict)
                and isinstance(answer["task"].get("direct"), dict)):
            answer["task"]["direct"].update(ctx.direct_chunk.task_fields(answer["task"], answer["task"]["direct"]))
        # #980: בקשת כיבוי מהקונסולה נוסעת באותו hello, באותו קצב — בלי
        # polling חדש. רק ב-hello האמיתי (לא בתפריט האתחול), ורק למכונה
        # רשומה; השדה חסר כשאין בקשה, וסוכן ישן מתעלם ממנו.
        if answer["known"]:
            # #2056/#2059: מה שהסוכן אומר שהוא מחיל (זמני המתנה/כיבוי) -- נרשם, ומשם "הוחל ב-N מתוך M".
            agent_timing.note_applied(ctx.conn, mac, body.get("applied_timing"))
            # #2080: תמונת הקושחה (אתחול, בקר, BIOS, דיסק לכל חיבור) -- שורה חדשה רק כשהשתנתה
            try:
                firmware_image.record(ctx.conn, mac, body)
            except sqlite3.OperationalError as exc:
                if not disk_full(exc):
                    raise
                note_disk_full(f"hello firmware_image {mac}")
            agentsenses.note_hello(ctx.conn, mac, body)   # #3064 B23: "בלי AHCI אין פס" -- נרשם בצל, אינו חוסם; אינו זורק
            # #2080 (מוקאפ 06 סעיף 2): חיבור שזז מאז ההדלקה הקודמת -- אזהרה במסך ההמתנה של המשכפל. אחרי `record`, כדי שתשקף את ה-hello הזה
            if answer.get("role") == "cloner":
                fw_warning = firmware_image.cloner_warning(ctx.conn, mac)
                if fw_warning is not None:
                    answer["fw_warning"] = fw_warning
                # #3268 (B62): בדיקת הכתיבה של המסך הממתין + מה שהשרת זוכר על ה-serial = החלטה אחת, שהמסך מציג. אין החלטה = השדה חסר
                write_decisions = brain_slowdisk.decide(ctx.conn, mac, body.get("write_check"), disks)
                if write_decisions:
                    answer["write_decisions"] = write_decisions
                # #3663 (A10, G2): בדיקת הכבל החיה והבאנר "קישור איטי" -- המוח מחליט מעובדות ה-hello, הסוכן והגואי מבצעים ומציגים. אין החלטה = השדה חסר
                link_verdict = brain_link.decide(ctx.conn, mac, body.get("nic_link"), answer) or {}
                link_verdict.update(nicbus.decide(ctx.conn, mac, body.get("nic_bus"), body.get("nic_link")))
                if link_verdict:
                    answer["link_verdict"] = link_verdict
            # #1857: מלאי הדיסקים (דגם/גודל/מקום) של משכפל ושל מחשב בנייה -- לרשימת "כל הדיסקים" ולשיוך לסטים
            if answer.get("role") in ("cloner", "build"):
                try:
                    disk_sets.note_hello(ctx.conn, mac, disks)
                except sqlite3.OperationalError as exc:   # מחיצה מלאה אינה מפילה hello (#1892)
                    if not disk_full(exc):
                        raise
                    note_disk_full(f"hello disk_inventory {mac}")
            power_action, power_confirmed = room_console.take_power(ctx.conn, mac)
            # #2031: hello מצטרף = המכונה חזרה לתור; כיבוי שנמסר = היא יוצאת מההחזקה
            if joining or power_action is not None:
                held.clear(ctx.conn, mac)
            if power_action is not None:
                answer["power_action"] = power_action
                # #1367: רק בקשה שאושרה מעל אזהרת השיכפול המקומי מכבה מכונה שכותבת
                if power_confirmed:
                    answer["power_confirmed"] = True
        else:
            # #1802 (נדב 04/11): המסך "המחשב הזה לא רשום" נשאר. כמה שניות עד עלייה
            # אוטומטית מהדיסק (0 = כבוי) — הסוכן מחיל אותה ב-UEFI בלבד, לעולם לא ב-BIOS.
            answer[unknown_screen.SETTING] = unknown_screen.autoboot_seconds(ctx.conn)
            # #2487: מכונה לא רשומה נענית ונרשמת (כך היא נרשמת מהקונסולה), אבל
            # hello חוזר אינו כותב שורת יומן בכל פעם: אחת לחלון, ואחריה מונה —
            # כמו סירובי הזהות (#1949). מכונה אמיתית שולחת hello אחד לאתחול.
            # #2498: אותו מפתח כמו תפריט האתחול — בקשה ו-hello של אותו אתחול הם שורה אחת.
            repeats = _journal_repeats(ctx, ("unknown_mac", mac))
            if repeats is not None:
                journal_unknown(ctx.conn, mac, client_ip,
                                f" [+{repeats[0]} identical requests in the last {repeats[1]}s not journaled]"
                                if repeats[0] else "")
        log.info("hello from %s (%s): known=%s off_vlan=%s",
                 # codeql[py/log-injection] false positive: MAC מנורמל, client_ip מה-socket, והשאר bool (#291)
                 mac, client_ip, answer["known"], off_vlan)
        # hello מוכיח שהסוכן רץ. אם השרת שלח את המכונה הזאת לדיסק
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
        # #1847: hello = המכונה ערה — סוגר הערת WoL פתוחה (אם יש).
        learn_wol.answered(ctx.conn, mac)
        # #1847: פעם אחת לכל אתחול, לא בכל hello (היה מציף את היומן כל 5 דק').
        if (body.get("wol_armed") is False and answer["known"]
                and learn_wol.arm_failure_is_new(ctx.conn, mac)):
            journal(ctx.conn, "wol_arm_failed", mac, machine=mac)
        # #1447: the agent has no NTP and the RTC may drift or have a dead
        # battery (cloner 2 ran 13 min ahead on metal 16/10). The server's
        # clock, read last so it is as fresh as the reply; hello.sh sets
        # the machine's clock from it. Only in the real hello, not the menu.
        answer["now"] = int(time.time())
        hello_eta.add(ctx.conn, ctx.library, answer, clock_trust)   # #1843: מתי הגל צפוי להסתיים
        return JSONResponse(answer)

    @router.post("/agent/login")
    async def agent_login(request: Request) -> JSONResponse:
        """כניסה ממסך השחזור בתחנה (סעיף 13.2: סיסמה → תחנה בודדת).

        אותם משתמשים של הקונסולה — אין סיסמה מקומית שיכולה לדלוף ממכונה
        שתלמידים שולטים בה (סעיף 15). ההצלחה אינה מחזירה טוקן: הסוכן
        ממשיך באותה שיחה, וכל פעולה ממילא עוברת דרך השרת.
        """
        body, refused = await _read_body(request)
        if refused is not None:
            return refused
        if not isinstance(body, dict):
            return _error(400, "body is not an object", "bad_json")
        # #2750: כמו #2748 — הגוף נקרא על הלולאה, עבודת ה-sqlite בתהליכון מהמאגר.
        return await run_in_threadpool(_agent_login_body, request, body)

    def _agent_login_body(request: Request, body: dict) -> JSONResponse:
        username = body.get("username", "")
        mac = lenient_mac(body.get("mac"))
        if mac is None:
            return _error(400, "missing or malformed mac", "bad_mac")
        # #2446: שם/סיסמה שאינם מחרוזת — 400, לפני `login_guard` ו-`users.verify`.
        if not isinstance(username, str) or not isinstance(body.get("password", ""), str):
            return _error(400, "username and password must be strings", "bad_login")
        # #997: הזהות לפני הסיסמה — זר אינו מנסה סיסמאות בשם מכונה, ואינו
        # מייצר `agent_login_failed` על מכונה שאינה שלו.
        refused = identity_gate(ctx, mac, request, "login")
        if refused is not None:
            return refused
        role, refused = agent_credentials(ctx, request, username, body.get("password", ""),
                                          f"{username} at {mac}", machine=mac)
        if refused is not None:
            return refused
        journal(ctx.conn, "agent_login", f"{username} at {mac}", machine=mac)
        return JSONResponse({"ok": True, "role": role})

    @router.post("/agent/pulls")
    async def open_pull(request: Request) -> JSONResponse:
        """פתיחת משיכת יוניקאסט — התחנה מודיעה לשרת שהיא מתחילה למשוך.

        ההרשמה כאן היא כדי שהעבודה תיראה — במבט-העל, ביומן ובדיווחי
        ההתקדמות — ומ-#2657 היא גם הרשות: `/api/v1/images` מגיש קבצים רק
        לחבר בסבב או במשיכה פתוחים של האימג' (`source_gate`). מה שכן נאכף כאן הוא בדיוק מה ש-hello מכריז עליו:
        כניסה, לפי ההגדרה ולפי הווילן (#42) — הצהרה שהסוכן מציית לה
        אינה אכיפה.

        הזרם עצמו אינו תופס את חריץ השידור: כמה משיכות במקביל, וגם
        בזמן סבב כיתה (#60).
        """
        body, refused = await _read_body(request)
        if refused is not None:
            return refused
        if not isinstance(body, dict):
            return _error(400, "body is not an object", "bad_json")
        return await run_in_threadpool(_open_pull_body, request, body)   # #2750

    def _open_pull_body(request: Request, body: dict) -> JSONResponse:
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
        # #381: אימג' שנקלט ממחשב כיתה שייך למכונה שנקלט ממנה. הסירוב
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
        signed_in = gui_session(ctx, request)
        if signed_in is not None:
            username = signed_in.username
        elif login_required(ctx.conn, has_open,
                            off_deploy_vlan(request.scope, server_base)):
            _role, refused = agent_credentials(ctx, request, username,
                                               body.get("password", ""),
                                               f"{username} at {mac} pull", machine=mac)
            if refused is not None:
                return refused
        else:
            # #2489 (3): בלי כניסה ובלי סיסמה השם בגוף לא הוכח — `opened_by` ריק, ולא
            # "admin" שמוצג במבט-העל כמנהל (`session_view.role_of`).
            username = ""

        # #3231: after the login above (nobody unauthorised starts a derivation), before the pull exists
        not_ready = _pull_index_gate(ctx, mac, image_id)
        if not_ready is not None:
            return not_ready
        try:
            session_id = pulls.open_pull(
                ctx.conn, ctx.store, mac, machine["group_id"], image_id, username,
            )
        except SessionError as exc:
            pulls.journal_refusal(ctx.conn, mac, str(exc))
            return _error(409, exc.message, "pull_conflict")
        # codeql[py/log-injection] false positive: MAC מנורמל, image_id קיים בספרייה, session_id נוצר בשרת (#291)
        log.info("unicast pull %s for %s (%s)", session_id, mac, image_id)
        return JSONResponse({"id": session_id, "kind": "unicast",
                             "image_id": image_id, "engine": CHUNK})

    @router.post("/agent/progress")
    async def agent_progress(request: Request) -> JSONResponse:
        body, refused = await _read_body(request)
        if refused is not None:
            return refused
        if not isinstance(body, dict):
            body = {}
        # #2748: הגוף נקרא כאן, על הלולאה; כל עבודת ה-sqlite רצה בתהליכון מהמאגר
        # (כמו hello). על הלולאה, DB נעול עצר את כל השרת עד `busy_timeout`.
        return await run_in_threadpool(_agent_progress_body, request, body)

    def _agent_progress_body(request: Request, body: dict) -> JSONResponse:
        # #855: הזהות נבדקת לפני הדיווח, בסבב ובמשימה כאחד — ורק כשיש MAC
        # לבדוק; MAC שאינו נקרא נשאר `bad_mac` של `reports.ingest`, כי
        # "לא הצלחנו לקרוא את המזהה" קודם ל"המזהה אינו תואם" (עיקרון 5).
        mac = lenient_mac(body.get("mac"))
        if mac is not None:
            refused = identity_gate(ctx, mac, request, "progress")
            if refused is not None:
                return refused
            # #3331 (I14): דיווח שהתעכב ברשת ומגיע אחרי חדש ממנו נרשם ונזרק -- לפני `ingest`, כך שהוא לא מחזיר
            # מצב ויומן אחורה ולא מרענן את `updated_at` (K1 קורא אותו כראיית חיים). הבסיס מתעדכן רק אחרי `ok`.
            if ctx.report_order.check(mac, body) == STALE:
                return JSONResponse(STALE_REPLY)
        if body.get("state") == "held" and "session_id" not in body and "task_id" not in body:
            # #2031: משכפל שמחזיק את התוצאות עד "כבה" (#2024) — בלי סבב ובלי משימה.
            # ראיית חיים (progress_seen), לא hello: אינו מצטרף לגל ואינו "נראה" ל-#1721.
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
        # #1839: דיווח שהתקבל (זהות + חבר בסבב פתוח / אסימון משימה) הוא ראיה
        # שהמכונה חיה — בזמן כתיבה זה הקשר היחיד שלה, ובלעדיו "לא מחובר" אחרי 90ש'.
        if result.get("ok") and mac is not None:
            ctx.report_order.note(mac, body)         # #3331: הדיווח התקבל, והוא הבסיס לבא
            agent_seen(ctx.conn, mac)
            problems = chunkledger.note_report(ctx, mac, body)   # #2533: the durable chunks; a refused field is logged, the report stands
            # #2533 (13/11): `durable` הוא דלתא ממוספרת. היומן הוא איחוד, ולכן מה שהגיע נרשם גם בפער;
            # התשובה אומרת לסוכן אם זה אושר (`durable_seq`) או שעליו לשלוח הכול מחדש (`durable_full`).
            # **המספר מתקדם רק על דיווח שהיומן רשם** (התפר ששלב 2 מצא; הכרעות המתאם 13/11). עם דלתא,
            # טווחים שאושרו בלי שנרשמו לא יישלחו שוב, והדיסק ייראה לשלב ההשלמה חסר יותר ממה שהוא.
            # לכן כשהיומן לא נכתב (`NOT_WRITTEN`: `database is locked`, כל חריגה), או שסורב בסיבה
            # שתחלוף (`RETRY_LATER`: האינדקס עוד לא אומת, האימג' או הדיסק עוד לא ידועים) — התשובה אינה
            # אומרת דבר על היומן. הסוכן אינו מסמן דבר כמאושר, ובדיווח הבא שולח שוב את כל מה שמאז
            # האישור האחרון. לא `durable_full`: השרת לא איבד את מקומו, ואין סיבה לשלוח הכול כל מחזור.
            # סירוב קבוע בשמו (תאומים, רשימה לא תקינה) כן מקדם: שליחה חוזרת לא תשנה אותו.
            placed = ctx.durable_seq.admit(body.get("session_id"), mac, "progress", body)
            if not (placed and (chunkledger.NOT_WRITTEN in problems or chunkledger.RETRY_LATER in problems)):
                result.update(ctx.durable_seq.answer(body.get("session_id"), mac, "progress", placed))
        if result.get("ok") and mac is not None and body.get("session_id"):
            result.update(_round_extras(mac, body))      # #3181: פס ייצור והמוח של ההשלמה
        if result.get("ok") and body.get("task_id") and body.get("state") == "failed":
            discard_failed_staging(ctx.conn, ctx.library.root, body["task_id"])   # #1915
        return JSONResponse(result, status_code=200 if (result.get("ok") or result.get("code") == "not_open") else 400)

    def _round_extras(mac: str, body: dict) -> dict:
        """#3181: דיווח של חבר בגל הנוכחי של סבב החדר. ``production_line`` -- רק בפס ייצור (`prodline.reply`);
        ``fill`` -- תוכנית ההשלמה של המוח (K5), רק במצב ``enforce``. בצל המוח מחליט ורושם, והסוכן לא שומע.
        לעולם אינו מכשיל את הדיווח: כל חריגה נרשמת ביומן השרת והתשובה בלי התוספות."""
        try:
            round_row = room_state.active_round(ctx.conn)
            if round_row is None or round_row["wave_session_id"] != body.get("session_id"):
                return {}
            extras: dict = {}
            line = prodline.note_report(ctx.conn, round_row, mac, body.get("targets"), body.get("line_wait"))
            if line is not None:
                extras["production_line"] = line
            from server.brain import veto          # noqa: PLC0415 -- כמו `budget.production`
            pl = prodline.line_of(ctx.conn, round_row["id"])
            drawers = pl["drawers"] if pl is not None else sum(
                len(item.get("ports") or []) for item in json.loads(round_row["target_slots_json"] or "[]"))
            plans = ctx.fill.observe(ctx.conn, round_row, mac, body, mode=veto.mode(ctx.conn), round_drawers=drawers,
                                     stream_bps=_stream_bps())
            if plans:
                extras["fill"] = plans
            extras.update(ctx.completing.observe(ctx.conn, round_row, mac, body, mode=veto.mode(ctx.conn)))   # #3064: `verify`, enforce + switch only
            ctx.join.observe(ctx.conn, round_row, mac, body, mode=veto.mode(ctx.conn), engine=_engine_status(),
                             stream_Bps=_stream_bps(), budget_bps=ctx.fill.budget(round_row["wave_session_id"], mac),
                             floor_mbps=_write_floor())
            extras.update(ctx.peer.observe(ctx.conn, round_row, mac, body, fill_brain=ctx.fill, budget=ctx.budget))
            return extras
        except Exception:                           # noqa: BLE001
            log.exception("prodline/fill: the extras for %s were not computed", mac)
            return {}

    def _engine_status() -> dict | None:
        """#3064 (המצטרף): המחיצה, המעבר והמצב של גל החתיכות הפעיל. ``None`` = לא ידוע."""
        try:
            return ctx.chunk.status() if ctx.chunk is not None else None
        except Exception:                           # noqa: BLE001
            return None

    def _write_floor() -> int | None:
        """#3064 (B41): המהירות המינימלית לכונן שהמנוע שופט לפיה (`repair.slow_reason`). ``None`` = לא ידוע."""
        try:
            floor = ctx.chunk.write_floor() if ctx.chunk is not None else None
            return floor if isinstance(floor, int) and floor > 0 else None
        except Exception:                           # noqa: BLE001
            return None

    def _stream_bps() -> int | None:
        """הקצב שהמנוע ביקש מהזרם, בבתים לשנייה -- לבדיקת השפיות של K5 בלבד. ``None`` = לא ידוע."""
        try:
            asked = ctx.chunk.sending_bps() if ctx.chunk is not None else None
            return asked // 8 if isinstance(asked, int) and asked > 0 else None
        except Exception:                           # noqa: BLE001
            return None

    #: (image_id, filename) -> (עד מתי, הנתיב). של האפליקציה הזו בלבד, ורק תשובות חיוביות.
    _file_paths: dict[tuple[str, str], tuple[float, Path]] = {}

    def _in_round(session_id: object, mac: str) -> bool:
        """#2487: ה-MAC חבר בסבב הזה — hello צירף אותו, ולכן הוא מכונה רשומה."""
        return isinstance(session_id, str) and ctx.conn.execute(
            "SELECT 1 FROM session_members WHERE session_id = ? AND mac = ?",
            (session_id, mac)).fetchone() is not None

    @router.post("/agent/chunk-pass")
    async def agent_chunk_pass(request: Request) -> JSONResponse:
        """#2533: הודעת הבקרה של מנוע החתיכות — "המקלט מאזין למעבר N" או "המעבר
        נגמר, וזה מה שעמיד על כל דיסק". התשובה היא מה עושים עכשיו (`chunksend.report`).
        אותו שער זהות כמו progress, ורק חבר בסבב שבגוף."""
        body, refused = await _read_body(request)
        if refused is not None:
            return refused
        if not isinstance(body, dict):
            return _error(400, "body is not an object", "bad_json")
        return await run_in_threadpool(_agent_chunk_pass_body, request, body)

    def _agent_chunk_pass_body(request: Request, body: dict) -> JSONResponse:
        mac = lenient_mac(body.get("mac"))
        if mac is None:
            return _error(400, "missing or malformed mac", "bad_mac")
        refused = identity_gate(ctx, mac, request, "chunk-pass")
        if refused is not None:
            return refused
        if not _in_round(body.get("session_id"), mac):
            return _error(403, "this mac is not a member of that session", "not_member")
        if ctx.chunk is None:
            return JSONResponse({"ok": True, "action": "closed"})
        # #2533 (13/11): `durable` הוא דלתא ממוספרת (`durableseq.py`). דלתא שהשרת אינו יכול למקם אינה
        # מגיעה למנוע כלל: אין הכרעה על יומן שאולי חסר בו משהו. הסוכן שולח הכול מחדש, ואז שואל שוב.
        placed = ctx.durable_seq.admit(body["session_id"], mac, "pass", body)
        try:
            result = ({"ok": True, "action": "wait"} if placed is False
                      else ctx.chunk.report(body["session_id"], mac, body))
        except RosterReadError:
            return JSONResponse({"ok": True, "action": "closed"})
        if result.get("ok"):
            result.update(ctx.durable_seq.answer(body["session_id"], mac, "pass", placed))
            agent_seen(ctx.conn, mac)             # כמו progress (#1839): דיווח שהתקבל הוא ראיית חיים
            if ctx.route is not None and placed is not False:
                ctx.route.on_pass(body["session_id"], mac, body)     # #3064 (K5, R9): לעולם אינו זורק
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)

    @router.post("/agent/resume")
    async def agent_resume(request: Request) -> JSONResponse:
        """#2528 שלב 4: משכפל שחזר אחרי כיבוי, בתחילת הגל של "כתוב מחדש" -- "אלה הדיסקים שעליהם סימן הכתיבה
        שלא הושלמה, וכמה זמן תיקח הקריאה החוזרת שלהם". התשובה: לכל דיסק, להמשיך או להתחיל מחדש (`brain_resume`).
        אותו שער זהות כמו chunk-pass, ורק חבר בסבב שבגוף."""
        body, refused = await _read_body(request)
        if refused is not None:
            return refused
        if not isinstance(body, dict):
            return _error(400, "body is not an object", "bad_json")
        return await run_in_threadpool(_agent_resume_body, request, body)

    def _agent_resume_body(request: Request, body: dict) -> JSONResponse:
        mac = lenient_mac(body.get("mac"))
        if mac is None:
            return _error(400, "missing or malformed mac", "bad_mac")
        refused = identity_gate(ctx, mac, request, "resume")
        if refused is not None:
            return refused
        if not _in_round(body.get("session_id"), mac):
            return _error(403, "this mac is not a member of that session", "not_member")
        targets = body.get("targets")
        if not isinstance(targets, list) or len(targets) > 64 or not all(isinstance(t, dict) for t in targets):
            return _error(400, "targets must be a list of objects", "bad_targets")
        row = ctx.conn.execute("SELECT image_id FROM sessions WHERE id = ?", (body["session_id"],)).fetchone()
        manifest = ctx.library.get(row["image_id"]) if row is not None else None
        if manifest is None:
            return _error(409, "the round's image is not in the library", "no_image")
        answers = brain_resume.answer(ctx.conn, session_id=body["session_id"], image_id=row["image_id"],
                                      manifest=manifest, targets=targets)
        agent_seen(ctx.conn, mac)
        return JSONResponse({"ok": True, "targets": answers})

    @router.post("/agent/disk-event")
    async def agent_disk_event(request: Request) -> JSONResponse:
        """בריאות SMART וההכרעה על דיסק יעד (#652), לצפייה ולריבוט-החלפה.

        best-effort מצד הסוכן: כשל כאן אינו מפיל שחזור. השרת שומר את
        התמונה החיה ואינו מכריע ממנה — ההכרעה נעשתה בסוכן, ליד המכונה.
        """
        body, refused = await _read_body(request)
        if refused is not None:
            return refused
        if not isinstance(body, dict):
            body = {}
        return await run_in_threadpool(_agent_disk_event_body, request, body)   # #2750

    def _agent_disk_event_body(request: Request, body: dict) -> JSONResponse:
        # #997: כמו progress — הזהות נבדקת כשיש MAC לבדוק; MAC שאינו נקרא
        # נשאר `bad_mac`/`bad_event` של `disk_events.ingest`.
        mac = lenient_mac(body.get("mac"))
        if mac is not None:
            refused = identity_gate(ctx, mac, request, "disk-event")
            if refused is not None:
                return refused
            # #2487: כמו `open_pull` — MAC לא רשום אינו כותב. חבר בסבב שבגוף הוא
            # רשום: hello צירף אותו לפי `all_macs` (#524), גם מכרטיס שאינו הרשום.
            if registry.lookup(ctx.conn, mac) is None and not _in_round(body.get("session_id"), mac):
                return _error(403, "this mac is not registered", "unknown_mac")
        result = disk_events.ingest(ctx.conn, body)
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)

    @router.post("/agent/local-clone")
    async def agent_local_clone(request: Request) -> JSONResponse:
        """#1322 D: a cloning machine's report of a local clone -- one
        journal line, labelled as the machine's own. Best effort from the
        agent's side; the copy never passed through the server."""
        body, refused = await _read_body(request)
        if refused is not None:
            return refused
        if not isinstance(body, dict):
            return _error(400, "body is not an object", "bad_json")
        return await run_in_threadpool(_agent_local_clone_body, request, body)   # #2750

    def _agent_local_clone_body(request: Request, body: dict) -> JSONResponse:
        mac = lenient_mac(body.get("mac"))
        if mac is None:
            return _error(400, "missing or malformed mac", "bad_mac")
        refused = identity_gate(ctx, mac, request, "local-clone")
        if refused is not None:
            return refused
        if registry.lookup(ctx.conn, mac) is None:      # #2487: כמו `open_pull`
            return _error(403, "this mac is not registered", "unknown_mac")
        try:
            line = local_clone_report.journal_line(body)
        except local_clone_report.BadReport as exc:
            return _error(400, exc.message, "bad_report")
        journal(ctx.conn, "local_clone_note" if local_clone_report.has_note(body) else "local_clone", f"{mac}: {line}",
                machine=mac)   # #2049
        return JSONResponse({"ok": True})

    @router.post("/agent/firmware-accept")
    async def agent_firmware_accept(request: Request) -> JSONResponse:
        """#2080 (הכרעת נדב 25/10): "זה מכוון" במסך ההמתנה של המשכפל -- התמונה הנוכחית נשמרת כהגדרה
        הנכונה, ביומן "אושר במשכפל <שם>". רק משכפל רשום, ורק על עצמו; מי שאינו משכפל -- 403."""
        body, refused = await _read_body(request)
        if refused is not None:
            return refused
        if not isinstance(body, dict):
            return _error(400, "body is not an object", "bad_json")
        return await run_in_threadpool(_agent_firmware_accept_body, request, body)   # #2750

    def _agent_firmware_accept_body(request: Request, body: dict) -> JSONResponse:
        mac = lenient_mac(body.get("mac"))
        if mac is None:
            return _error(400, "missing or malformed mac", "bad_mac")
        refused = identity_gate(ctx, mac, request, "firmware-accept")
        if refused is not None:
            return refused
        machine = registry.lookup(ctx.conn, mac)
        if machine is None:      # #2487: כמו `open_pull`
            return _error(403, "this mac is not registered", "unknown_mac")
        if machine["role"] != "cloner":
            return _error(403, "only a cloner approves its own firmware here", "not_a_cloner")
        if not firmware_image.accept(ctx.conn, mac, "", at_cloner=machine["suffix"]):
            return _error(409, "this machine has not reported its firmware yet", "no_image")
        return JSONResponse({"ok": True})

    @router.post("/agent/log")
    async def agent_log(request: Request) -> JSONResponse:
        """#1882: הקטע האחרון של לוג הסוכן, gzip, פעם אחת בסוף סבב. mac/session_id/reason
        ב-query (הגוף הוא הלוג עצמו). best effort מצד הסוכן — כשל כאן אינו משנה דבר שם."""
        mac = lenient_mac(request.query_params.get("mac"))
        if mac is None:
            return _error(400, "missing or malformed mac", "bad_mac")
        # #2750: הזהות והקבלה (sqlite) בתהליכון מהמאגר; רק קריאת הזרם על הלולאה.
        refused = await run_in_threadpool(identity_gate, ctx, mac, request, "agent-log")
        if refused is not None:
            return refused
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > agent_logs.MAX_BODY:
            return _error(413, "log too large", "too_large")
        # Content-Length אינו חובה (chunked): הגוף נקרא כזרם עם מונה, ובקשה שעוברת את
        # התקרה נעצרת ב-413 ברגע שעברה — לא נקראת כולה לזיכרון (סקירה, #1882).
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > agent_logs.MAX_BODY:
                return _error(413, "log too large", "too_large")
        result = await run_in_threadpool(_agent_log_receive, request, mac, bytes(body))
        status =200 if result.get("ok") else 403 if result.get("code") == "unknown_machine" else 400
        return JSONResponse(result, status_code=status)

    def _agent_log_receive(request: Request, mac: str, body: bytes) -> dict:
        return agent_logs.receive(
            ctx.conn, mac, request.query_params.get("session_id"),
            request.query_params.get("reason"), body,
            known=registry.lookup(ctx.conn, mac) is not None,
            cut_header=request.headers.get("x-imagesrv-log-cut"))   # #3031: did the agent cut the log

    @router.post("/agent/shrink-open")
    async def agent_shrink_open(request: Request) -> JSONResponse:
        """#926: הפריסה המקורית של מחיצת המקור, **לפני** `ntfsresize -s`.

        ההפך מ-disk-event: **לא** best-effort. הסוכן מכווץ רק אחרי 2xx
        עם `ok` — בלי רשומה בשרת אין כתיבה למקור (עיקרון 5).
        """
        body, refused = await _read_body(request)
        if refused is not None:
            return refused
        if not isinstance(body, dict):
            body = {}
        return await run_in_threadpool(_agent_shrink_open_body, request, body)   # #2750

    def _agent_shrink_open_body(request: Request, body: dict) -> JSONResponse:
        mac = lenient_mac(body.get("mac"))
        if mac is not None:                       # #997; None → bad_mac של open_record
            refused = identity_gate(ctx, mac, request, "shrink-open")
            if refused is not None:
                return refused
            if registry.lookup(ctx.conn, mac) is None:      # #2487: כמו `open_pull`
                return _error(403, "this mac is not registered", "unknown_mac")
        try:
            # #2492: רק סידורי שה-MAC הזה דיווח ל-`dev` הזה (`serial_not_reported`, 403).
            row = shrink_records.open_record(ctx.conn, body, require_reported=True)
        except shrink_records.BadRecord as exc:
            return _error(403 if exc.code == "serial_not_reported" else 400, exc.message, exc.code)
        except shrink_records.AlreadyOpen as exc:
            return JSONResponse({"ok": False, "error": "this disk already has an open shrink record",
                                 "code": "already_open", "id": exc.record_id,
                                 "opened_at": exc.opened_at}, status_code=409)
        return JSONResponse({"ok": True, "id": row["id"]})

    def _shrink_identity(body: dict, request: Request, where: str
                         ) -> tuple[str | None, int | None, JSONResponse | None]:
        """`(mac, id, None)` — הפונה הוא המכונה שפתחה את הרשומה הפתוחה; אחרת
        `(None, None, תשובה)`.

        #997: הסידורי הוא הזהות של הרשומה, אבל ה-MAC — שהסוכן שולח תמיד
        (`shrinkmem.sh`) — הוא הזהות של **הפונה**. בלעדיו אין מה להשוות לחכירה.
        #2413: ושער הזהות לבדו אומר רק "הפונה הוא מי שהוא אומר" — תחנה אחרת עם
        חכירה תקפה סגרה (או כתבה הערה על) רשומה של דיסק שאינו שלה, לפי הסידורי,
        והתזכורת להחזיר אותו לגודלו נעלמה. רשומה נסגרת בידי המכונה שפתחה אותה;
        דיסק שעבר מכונה — "נקה" בקונסולה (`clear`), בגלוי ובשם המשתמש."""
        mac = lenient_mac(body.get("mac"))
        if mac is None:
            return None, None, _error(400, "missing or malformed mac", "bad_mac")
        refused = identity_gate(ctx, mac, request, where)
        if refused is not None:
            return None, None, refused
        record_id = body.get("id") if isinstance(body.get("id"), int) else None
        owner = shrink_records.open_owner(ctx.conn, body.get("serial"), record_id, mac)   # #2492
        if owner is None:
            return None, None, _error(404, "no open shrink record for this serial", "not_open")
        if owner != mac:
            journal(ctx.conn, "shrink_refused",
                    f"{where}: {mac} is not {owner}, which opened the record of"
                    f" serial={body.get('serial')}", machine=mac)
            return None, None, _error(403, "this shrink record was opened by another machine",
                                      "not_owner")
        return mac, record_id, None

    @router.post("/agent/shrink-close")
    async def agent_shrink_close(request: Request) -> JSONResponse:
        """#926: המקור הוחזר לגודלו. סגירה של מה שאינו פתוח היא 404."""
        body, refused = await _read_body(request)
        if refused is not None:
            return refused
        if not isinstance(body, dict):
            return _error(400, "body is not an object", "bad_json")
        return await run_in_threadpool(_agent_shrink_close_body, request, body)   # #2750

    def _agent_shrink_close_body(request: Request, body: dict) -> JSONResponse:
        mac, record_id, refused = _shrink_identity(body, request, "shrink-close")
        if refused is not None:
            return refused
        if not shrink_records.close_record(ctx.conn, body.get("serial"), record_id, mac=mac):
            return _error(404, "no open shrink record for this serial", "not_open")
        return JSONResponse({"ok": True})

    @router.post("/agent/shrink-note")
    async def agent_shrink_note(request: Request) -> JSONResponse:
        """#926 (סקירת Fable): למה הרשומה עדיין פתוחה — הטבלה הוחזרה אך
        מערכת הקבצים לא נמתחה. best-effort מצד הסוכן; 404 על מה שאינו פתוח."""
        body, refused = await _read_body(request)
        if refused is not None:
            return refused
        if not isinstance(body, dict):
            return _error(400, "body is not an object", "bad_json")
        return await run_in_threadpool(_agent_shrink_note_body, request, body)   # #2750

    def _agent_shrink_note_body(request: Request, body: dict) -> JSONResponse:
        mac, record_id, refused = _shrink_identity(body, request, "shrink-note")
        if refused is not None:
            return refused
        note = body.get("note")
        if not isinstance(note, str) or not note.strip():
            return _error(400, "note must be a non-empty string", "bad_note")
        if not shrink_records.note_record(ctx.conn, body.get("serial"), record_id, note, mac=mac):
            return _error(404, "no open shrink record for this serial", "not_open")
        return JSONResponse({"ok": True})

    @router.get("/images/{image_id}/manifest")
    def image_manifest(image_id: str, request: Request):
        refused = source_gate(ctx, request, "manifest", image_id)      # #997
        if refused is not None:
            return refused
        manifest = ctx.library.get(image_id)
        live = manifest is None
        if live:
            # #715: מזהה חי (`live_…`) — המניפסט שמחשב הבנייה דיווח. אותו
            # נתיב בדיוק, כדי שהמקבלים לא ידעו מי משדר. לפני שהגיע: 404.
            manifest = direct.live_manifest(ctx.conn, image_id)
        if manifest is None:
            return _error(404, "unknown image", "no_image")
        public = {k: v for k, v in manifest.items() if not k.startswith("_")}
        # #1805/#1864 (נדב 20/10): האימות אחרי הכתיבה — "sample" (ברירת המחדל) או
        # "full" (`readback_full` בהגדרות). נקבע כשהסוכן מושך את המניפסט: לכל הסבב.
        public["readback_mode"] = readback_mode(ctx.conn)
        if live and public["readback_mode"] == "sample":
            # #2218 (הכרעה 03/11): להפצה ישירה אין אימג' בספרייה, ולכן אין רשימה
            # למדגם (`…/readback/<n>` מחזיר 404 — ברזל 30/10, כל יעד יצא כתום).
            # עוברים לבדיקה מלאה מול ה-`readback_xxh64` שמעבר 1 שם במניפסט החי.
            public["readback_mode"] = "full"
            journal(ctx.conn, "readback_mode_switched",
                    f"{image_id} sample -> full: a direct send has no list on the server;"
                    f" every chunk is compared with the live manifest's readback_xxh64"
                    f" ({request.client.host if request.client else '?'})")
        # #2338 (נדב 03/11): סף המהירות של בדיקת הכתיבה לפני הזרם (writecheck.sh),
        # ההגדרה `writecheck_min_mbps`. נקבע כשהסוכן מושך את המניפסט: לכל הסבב.
        public[writefloor.SETTING] = writefloor.mbps(ctx.conn)
        public.update(hdd_policy.manifest(ctx.conn))
        return JSONResponse(public)

    @router.get("/images/{image_id}/files/{filename}")
    def image_file(image_id: str, filename: str, request: Request):
        refused = source_gate(ctx, request, "files", image_id)         # #997
        if refused is not None:
            return refused
        if ctx.direct_chunk is not None and direct.is_live_id(image_id):
            # #3161: לאימג' חי אין קובץ בשרת -- רק פריים הראש שמחשב הבנייה העלה, בטווח מדויק
            return ctx.direct_chunk.file_response(image_id, filename, request.headers.get("range"))
        # רשימה לבנה: מוגש רק קובץ שהמניפסט מכריז עליו בשמו המדויק.
        # #2533: `file_path` סורק את הספרייה כולה (`scan`), והשלמה של חוסר מפוזר היא אלפי בקשות Range
        # לאותו קובץ. התשובה נשמרת לכמה שניות — רק תשובה חיובית; שער הזהות למעלה נשאל בכל בקשה.
        now = time.monotonic()
        kept = _file_paths.get((image_id, filename))
        if kept is None or kept[0] < now:
            path = ctx.library.file_path(image_id, filename)
            if path is None:
                _file_paths.pop((image_id, filename), None)
                return _error(404, "file not in this image's manifest", "no_file")
            if len(_file_paths) > 256:
                _file_paths.clear()
            kept = _file_paths[(image_id, filename)] = (now + FILE_PATH_TTL_S, path)
        if not Path(kept[1]).is_file():             # נמחק מאז: לא 500, ולא מהמטמון
            _file_paths.pop((image_id, filename), None)
            return _error(404, "file not in this image's manifest", "no_file")
        if ctx.served is not None and request.client:
            return _CountedFile(kept[1], served=ctx.served, ip=request.client.host, budget=ctx.budget,
                                media_type="application/octet-stream")
        return FileResponse(kept[1], media_type="application/octet-stream")

    @router.get("/images/{image_id}/readback/{index}")
    def image_readback(image_id: str, index: int, request: Request):
        """#1805/#1864: רשימת ה-XXH64 של מחיצה — שורה לחתיכה של 4MiB של זרם הנתונים
        (`server/agent/readbackhash.py`). הסוכן בודק אותה בעצמו מול `readback_xxh64`
        שבמניפסט; אין רשימה (האינדקס טרם נגזר, הפצה ישירה) — 404, והסוכן אומר "לא אומת"."""
        refused = source_gate(ctx, request, "readback", image_id)
        if refused is not None:
            return refused
        manifest = ctx.library.get(image_id)
        if manifest is None or not manifest.get("_dir") or not manifest.get("_available", True):
            return _error(404, "unknown image", "no_image")
        part = next((p for p in manifest.get("partitions") or []
                     if isinstance(p, dict) and p.get("index") == index), None)
        # #2533: the list is the third column of the partition's index -- there is no
        # `.readback` file beside an image_format 2 file.
        text = readbackhash.list_text(manifest["_dir"], part) if part is not None else None
        if text is None:
            return _error(404, "no read-back list for this partition", "no_list")
        return PlainTextResponse(text)

    @router.get("/images/{image_id}/index/{index}")
    def image_index(image_id: str, index: int, request: Request):
        """#2533: the chunk index of a partition (`<file>.idx`) -- a header line, then
        offset, frame length and XXH64 for every 4MiB chunk of the data stream. The
        reader checks it against the manifest's `index_xxh64` itself. No verified
        index (not derived yet, a direct send) -- 404, never a guessed one."""
        refused = source_gate(ctx, request, "index", image_id)
        if refused is not None:
            return refused
        if ctx.direct_chunk is not None and direct.is_live_id(image_id):
            return ctx.direct_chunk.index_response(image_id, index)    # #3161: the index the source uploaded
        manifest = ctx.library.get(image_id)
        if manifest is None or not manifest.get("_dir") or not manifest.get("_available", True):
            return _error(404, "unknown image", "no_image")
        part = next((p for p in manifest.get("partitions") or []
                     if isinstance(p, dict) and p.get("index") == index), None)
        found = (readbackhash.part_index(manifest["_dir"], part)
                 if part is not None and readbackhash.has_list(manifest["_dir"], part) else None)
        if found is None:
            return _error(404, "no chunk index for this partition", "no_index")
        return PlainTextResponse(found.render())

    @router.get("/images/{image_id}/b3/{index}")
    def image_b3(image_id: str, index: int, request: Request):
        """#3332: the BLAKE3 list of a partition (`<file>.b3`) -- 32 bytes for every chunk of its index.
        The reader checks it against the manifest's `chunk_b3` itself. None (not derived yet) -- 404, and
        the agent writes nothing of the partition (chunkb3.sh). A live image's is the one its source uploaded."""
        refused = source_gate(ctx, request, "b3", image_id)
        if refused is not None:
            return refused
        if ctx.direct_chunk is not None and direct.is_live_id(image_id):
            return ctx.direct_chunk.b3_response(image_id, index)       # the list the source uploaded
        manifest = ctx.library.get(image_id)
        if manifest is None or not manifest.get("_dir") or not manifest.get("_available", True):
            return _error(404, "unknown image", "no_image")
        part = next((p for p in manifest.get("partitions") or []
                     if isinstance(p, dict) and p.get("index") == index), None)
        data = readbackhash.b3_bytes(manifest["_dir"], part) if part is not None else None
        if data is None:
            return _error(404, "no BLAKE3 list for this partition", "no_b3")
        return Response(content=data, media_type="application/octet-stream")

    return router
