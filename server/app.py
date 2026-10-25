"""הרכבת האפליקציה — כאן כל החלקים מתחברים.

זה הקובץ שסוגר את הלולאה: אותו resolver שעונה ל-hello מוזרק גם ל-endpoint
של תפריט ה-GRUB (boot/http.py). אין שאילתה שנייה ואין ממשק שני.

‏#731 (tracer 1 של #703): המצב המשותף — ‏DB, ספרייה, שולח, משימות-רקע
והאתחול החד-פעמי — יושב ב-`ServerRuntime` **אחד** ש-`create_runtime`
בונה פעם אחת. שתי האפליקציות (`create_agent_app` לסוכן, `create_console_app`
לקונסולה) נבנות מאותו runtime, ולכן אין שני DB, אין שני sweep ואין שני
תהליכי-רקע. ‏`create_app` נשאר כמעטפת דקה שמרכיבה את שתיהן על אפליקציה
אחת — התנהגות זהה למה שהיה, בשביל הבדיקות וה-e2e.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Connection

from fastapi import Depends, FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from boot.grub_menu import GrubConfig
from boot.http import create_boot_asgi, gui_initrd_path

from . import auth, auto_reboot, boottrace, deploy_net, dhcp, drive_rates, pulls, registry
from . import learn_netloss, learn_wol   # #1846, #1847
from . import server_io   # #1884
from . import pxe_log   # #1887
from . import timezone, timesync   # #1447, #1773
from .api import ServerContext, create_agent_router
from .console_api import create_console_router
from .console_data import create_console_data_router   # #1882-#1889
from .console_hostcfg import create_hostcfg_router
from .console_timezone import create_timezone_router
from .console_timesync import create_timesync_router
from .console_ups import create_ups_router
from .console_tls import ConsoleTLS
from .console_storage import create_storage_router
from .console_backup_target import create_backup_target_router
from .console_storage_locations import create_storage_locations_router
from . import storage_locations
from . import storage_heartbeat
from . import housekeeping   # #1737
from .db import journal
from . import wol
from . import direct
from . import smart_ask
from .sender import SenderEngine
from .station import create_station_router
from .console_library import create_library_router
from .console_drivers import create_agent_drivers_router, create_drivers_router   # #720
from .console_tools import create_tools_router   # #649 שלב 1
from .console_usb import create_usb_router   # #1914: the clone stick download
from .drivers import DriverLibrary
from .capture import (create_agent_capture_router, create_console_capture_router,
                      start_verification)
from .console_net import create_net_router
from .nic_services import create_nic_services_router
from . import console_dhcp
from .console_dhcp import create_dhcp_router
from .console_netcfg import create_netcfg_router, drain_crumbs
from .db import connect
from .health import create_health_router
from .health import default_hooks as health_default_hooks
from .update import (PUBLIC_UPDATE_URL, create_update_router, current_version, display_version,
                     default_hooks as default_update_hooks, read_version,
                     VersionRead)
from .hello import make_resolver, off_deploy_vlan, self_probe
from .images import ImageLibrary
from .kiosk import create_kiosk_router, mark_build_gui
from .cloner_round import create_cloner_round_router
from . import room
from . import usedbackfill
from . import readbackhash
from .room import CLONERS_GROUP, create_room_router
from .room_console import create_room_console_router
from .wake_api import create_wake_router
from .sessions import SessionStore
from .users import ensure_admin
from . import storage_nodes
from . import monitor
from .monitor import create_monitor_router
from .vmhost import VmHost
from .vmupdate import VmUpdate
from .vmupdate_api import create_vmupdate_router, create_vmupdate_viewer_router
from . import image_versions
from .reconcile import reconcile_on_start
from .work_areas import sweep as sweep_work_areas

log = logging.getLogger("imagectl.app")

STATIC_DIR = Path(__file__).parent / "static"

#: כל כמה שניות שעון-הרקע מריץ את הדופק של חדר השיכפולים (#456). זהו רק
#: רשת-הביטחון לחדר **שקט לגמרי**: כשמכונות מדברות, ה-hello שלהן מקדם את
#: ה-tick בקצב ~2ש' ממילא. הסף להכרזת אובדן הוא `room.LOST_SECONDS` (180),
#: ולכן דגימה כל 5ש' מכריזה אובד תוך ~5ש' אחרי שהסף חלף — מספיק דק.
ROOM_CLOCK_SECONDS = 5.0


def _off_vlan_decline(mac: str, step: str) -> bool:
    """רושם שאינו רושם: פירור אתחול (#400) שהתקבל מחוץ לווילן ההפצה (#584).

    ‏`GET /boot/step?mac=&s=` לוקח את ה-MAC מהשאילתה, וזה אינו זהות;
    מחוץ לווילן ההפצה אין ראיה שהפונה הוא המכונה. מחזיר False —
    **ראיה חיובית** שהפירור לא נרשם, לא היעדר חריגה — ורושם ביומן מי
    ניסה ומה. ‏`record_step` מחזיר ל-GRUB 200 בכל מקרה (שקט כלפי מסך
    האתחול, רועש ביומן), ולכן האבחון נשאר אמין בלי להפיל אתחול."""
    log.warning("boot step %s for %s not recorded — off deployment vlan (#584)",
                step, mac)
    return False


def _probe_decline(mac: str, step: str) -> bool:
    """רושם שאינו רושם: הפירור `menu` של הבדיקה העצמית של השרת (‏#976).

    אותה צורה כמו `_off_vlan_decline`, ומאותו טעם: הפונה אינו המכונה.
    בלי זה כל כניסה לקונסולה איפסה את שביל הפירורים של מכונה רשומה
    ל-"תפריט האתחול נמסר (1/9)" — גם כשהיא הייתה ב-`agent-hello` רגע
    קודם. ‏info ולא warning: זה המצב הרגיל, פעם בכל מסך בריאות."""
    log.info("boot step %s for %s not recorded — server's own probe (#976)",
             step, mac)
    return False


class ConsoleNoStaleCache:
    """קבצי הקונסולה מתעדכנים עם השרת. בלי revalidation, עדכון גרסה
    משאיר אצל המשתמשים JS ישן מול API חדש — באגים בלתי ניתנים לשחזור.

    middleware גולמי ולא @app.middleware: העטיפה של Starlette בונה כל
    תשובה מחדש וממקטינה את שמות הכותרות — כולל של ‎/boot, שם GRUB חייב
    "Content-Length" באותיות גדולות (ראו boot/http.py). כאן נוגעים אך
    ורק בתשובות הקונסולה; כל השאר עובר כמות שהוא."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/console"):
            await self.inner(scope, receive, send)
            return

        async def send_no_cache(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", [])
                           if k.lower() != b"cache-control"]
                headers.append((b"Cache-Control", b"no-cache"))
                message = {**message, "headers": headers}
            await send(message)

        await self.inner(scope, receive, send_no_cache)


@dataclass
class ServerRuntime:
    """המצב המשותף לשתי האפליקציות — בעל יחיד (#731/#703).

    ‏`create_runtime` בונה אותו **פעם אחת** ומריץ בו את האתחול החד-פעמי
    (‏`sweep_work_areas`, ‏`drain_crumbs`, ‏`ensure_admin`); ‏`create_agent_app`
    ו-`create_console_app` מקבלים אותו כמות שהוא. שני runtime = שני DB,
    שני sweep, שני תהליכי-רקע — וזה בדיוק מה שהפיצול הזה מונע."""

    ctx: ServerContext
    conn: Connection
    library: ImageLibrary
    store: SessionStore
    server_base: str
    data_dir: Path
    netcfg_dir: Path
    wol_send: object | None = None
    boot_dir: str | Path | None = None
    extra_cmdline: tuple[str, ...] = ()
    dhcp_hooks: dict | None = None
    health_hooks: dict | None = None
    storage_hooks: dict | None = None
    storage_check_interval: float = storage_locations.CHECK_INTERVAL
    netcfg_hooks: dict | None = None
    known_macs_hooks: dict | None = None
    #: #748: תיקיית עץ השרת (ל-``git describe``/``fetch``/``checkout``)
    #: וה-hooks של בדיקת/הרצת העדכון. ברירת המחדל בפועל נקבעת ב-main.py.
    repo_dir: str | Path | None = None
    update_hooks: dict | None = None
    #: הקצב של שעון-הרקע של החדר (#456). ברירת המחדל היא הייצור; בדיקות
    #: מעבירות ערך קצר כדי שהלולאה תדגום מהר בלי להמתין 5ש'.
    room_clock_interval: float = ROOM_CLOCK_SECONDS
    #: ‏#1564: קצב הדופק למשניים המצומדים. בדיקות מעבירות ערך קצר.
    heartbeat_interval: float = storage_heartbeat.HEARTBEAT_SECONDS
    # ‏#703 (tracer 5): זהות ה-TLS של הקונסולה, כשהיא מוגשת ב-HTTPS. ``None``
    # = ‏``--console-tls off`` (loopback) או בדיקות — ואז העוגייה בלי
    # ``Secure`` ו-``/me`` מדווח ``tls: null``. ‏main מעביר ערך אמיתי.
    console_tls: ConsoleTLS | None = None
    # ‏#1088: רשת ההפצה — מקורה (יחידה/קונסולה/לא הוגדרה) ומה שהקונסולה
    # צריכה כדי להשלים את ההתקנה בהדלקת DHCP. ``None`` = בדיקות/קוד ישן:
    # ההדלקה מתנהגת כמו קודם, בלי השלמה.
    deploy: "deploy_net.DeployContext | None" = None
    #: ‏#153: hooks של לשונית התעודה/שם המארח (``console_hostcfg``) —
    #: ‏``None`` = ברירות המחדל האמיתיות; בדיקות מזריקות, כמו ``dhcp_hooks``.
    host_hooks: dict | None = None
    #: ‏#1334: עדכון אימג' ב-VM — מנוע אחד לתהליך (VM אחד בכל פעם).
    vmupdate: "VmUpdate | None" = None
    #: ‏#1372: המארח של עדכון ה-VM — אזהרה בסטטוס העדכון ובבריאות, וההכנה
    #: בעלייה. ‏None = לא הודלק (בדיקות); ‏main מדליק.
    vm_host: "VmHost | None" = None
    auto_reboot_hook: object = auto_reboot.default_reboot
    auto_reboot_interval: float = auto_reboot.CHECK_SECONDS
    #: ‏#1737: הסיבוב היומי (‏`housekeeping`) — מתי הראשון ובאיזה קצב. בדיקות מקצרות.
    housekeeping_first: float = housekeeping.FIRST_RUN_S
    housekeeping_every: float = housekeeping.EVERY_S


async def _room_clock(rt: ServerRuntime, interval: float) -> None:
    """לולאת הרקע שמריצה את הדופק של חדר השיכפולים בקצב קבוע — #456.

    ‏**גל שאיש אינו צופה בו חייב להתקדם.** בלי הלולאה הזו `tick` רץ רק
    מ-hello של משכפל או מ-GET `/overview`; חדר שכל מכונותיו נעלמו אינו
    מייצר אף אחד מהם, ו-`_mark_lost` לעולם אינו מפקיע את החברים השקטים
    (#456, והזנב של #659). היא המקור ה**בלתי-תלוי-בצופה** ל-tick.

    ‏`tick` עושה I/O סינכרוני מול sqlite; מריצים אותו ב-worker thread כדי
    לא לחסום את לולאת האירועים המשותפת לשלושת השרתים. ‏`db.Database` נותן
    חיבור לכל תהליכון (WAL) — אותו מודל בדיוק כמו מסלול ה-hello של uvicorn,
    שגם הוא רץ בתהליכון מהמאגר. ‏`room.sweep` לעולם אינו זורק, ולכן דגימה
    שנכשלה נרשמת ביומן וממשיכה — הלולאה אינה מתה על תקלה חולפת.

    #1066: באותו תהליכון, כל `storage_check_interval` (ברירת מחדל 60ש')
    נבדקים מיקומי האחסון המחוברים/לא-נגישים. disconnected לא נבדק.

    ‏#1737: ובאותו תהליכון, פעם ביום (‏`housekeeping.Schedule`), התקרה לכל מה
    שאינו אימג' — טבלאות שגדלות ואזורי עבודה נטושים. ‏`housekeeping.run` לעולם
    אינו זורק.
    """
    pxe_elapsed = 0.0   # ‏#1887
    storage_every = rt.storage_check_interval or storage_locations.CHECK_INTERVAL
    # ‏#2123: מיקום שסומן "לא נבדק" בעלייה נבדק כבר בפעימה הראשונה, לא אחרי דקה.
    elapsed = storage_every if storage_locations.has_unchecked(rt.conn) else 0.0
    chores = housekeeping.Schedule(rt.housekeeping_first, rt.housekeeping_every)
    while True:
        await asyncio.sleep(interval)
        await asyncio.to_thread(room.sweep, rt.conn, rt.store)
        # ‏#1573: משיכה שנכשלה או שתקה נסגרת גם כשאיש אינו צופה.
        await asyncio.to_thread(pulls.background_sweep, rt.store)
        # ‏#1847: שליחה חוזרת מוקדמת להערה שעברה פי 2 מהזמן הטיפוסי, והתראה על רצף.
        await asyncio.to_thread(learn_wol.sweep, rt.conn, rt.wol_send)
        # ‏#1887: מה ש-dnsmasq ראה (קריאת journal בלבד) — כל `pxe_log.EVERY_S`. לעולם אינו זורק.
        pxe_elapsed += interval
        if pxe_elapsed >= pxe_log.EVERY_S:
            pxe_elapsed = 0.0
            await asyncio.to_thread(pxe_log.sweep, rt.conn)
        elapsed += interval
        if elapsed >= storage_every:
            elapsed = 0.0
            hooks = rt.storage_hooks or storage_locations.default_hooks()
            await asyncio.to_thread(
                storage_locations.poll, rt.conn, rt.library, hooks)
        if chores.due(interval):
            await asyncio.to_thread(housekeeping.run, rt.conn,
                                    lambda: _library_roots(rt))


def _library_roots(rt: ServerRuntime) -> list[Path]:
    """‏#1737: השורשים שהסחיפה היומית עוברת עליהם — המקומי, ומיקום אחסון רק
    כשהוא מחובר עכשיו (על מיקום שנפל אין מה לקרוא, וזה לא "ריק")."""
    roots = [Path(rt.library.root)]
    for loc in storage_locations.library_roots(rt.conn, rt.library.root):
        if loc.get("available"):
            roots.append(Path(loc["path"]))
    return roots


async def _storage_heartbeat(rt: ServerRuntime, interval: float) -> None:
    """‏#1564: הדופק למשניים המצומדים, כל ``interval`` שניות.

    משימה **נפרדת** משעון החדר, באותו `lifespan`: משני שלא עונה מעכב רק
    את הדופק (עד ``storage_heartbeat.TIMEOUT_SECONDS`` לכל משני), לעולם
    לא את `room.sweep`. כשל של משני נרשם על השורה שלו בתוך ``beat``; חריגה
    שמגיעה לכאן (DB) נרשמת ביומן והלולאה ממשיכה לדופק הבא."""
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(storage_heartbeat.beat, rt.conn, rt.data_dir)
        except Exception:                                    # noqa: BLE001
            log.exception("הדופק לשרתי המשנה נכשל — ממשיכים לדופק הבא")


def _install_room_clock(app: FastAPI, rt: ServerRuntime) -> None:
    """מתקין את שעון-הרקע של החדר על מחזור-החיים (`lifespan`) של האפליקציה.

    מותקן על **אפליקציה אחת בלבד בכל תהליך** — הקונסולה בייצור, והמעטפת
    המשולבת בבדיקות/e2e — אחרת שלוש האפליקציות שחולקות runtime היו מריצות
    שלושה שעונים על אותו DB. ‏`lifespan` (ולא תשתית תזמון חדשה) הוא ההכרעה
    ב-#456: המשימה עולה ב-startup ומבוטלת ב-shutdown, בלי מודול scheduler.
    """
    interval = rt.room_clock_interval

    # הקונסולה מוגשת ב-uvicorn.Server אחד **לכל כתובת** (loopback + כרטיס
    # השרתים), וכל אחד מריץ את ה-lifespan: שני שעונים, שני מתזמני ריסטרט
    # ושתי סגירות שולח. כולם על לולאה אחת (ports.py) — ולכן מונה: הראשון
    # שנכנס מפעיל, האחרון שיוצא עוצר. מאזין בודד שנסגר (#996) אינו עוצר.
    live = {"n": 0, "tasks": ()}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        live["n"] += 1
        if live["n"] == 1:
            if rt.vmupdate is not None:
                # ‏#1649: שמירת VM שנקטעה באתחול משאירה עותק dm ושני loop
                await asyncio.to_thread(rt.vmupdate.clear_leftovers)
            task = asyncio.create_task(_room_clock(rt, interval))
            reboot_task = asyncio.create_task(
                auto_reboot.run(rt, rt.auto_reboot_hook, rt.auto_reboot_interval))
            rt.ctx.auto_reboot_task = reboot_task
            heartbeat_task = asyncio.create_task(
                _storage_heartbeat(rt, rt.heartbeat_interval))
            live["tasks"] = (task, reboot_task, heartbeat_task)
        try:
            yield
        finally:
            live["n"] -= 1
            if live["n"] == 0:
                for running in live["tasks"]:
                    running.cancel()
                for running in live["tasks"]:
                    with suppress(asyncio.CancelledError):
                        await running
                live["tasks"] = ()
                # ‏#1426: השולח יושב על ctx; ל-ServerRuntime אין `sender`
                await asyncio.to_thread(rt.ctx.sender.close)

    app.router.lifespan_context = lifespan


def _backfill_used_bytes(conn: Connection, library: ImageLibrary) -> None:
    """‏#1494: שורת יומן לכל אימג' שנוסתה בו השלמה — מה נכתב, ומה לא ולמה."""
    def report(result: dict) -> None:
        written = ", ".join(f"p{i}={b}" for i, b in sorted(result["written"].items()))
        if written:
            journal(conn, "image_used_bytes_derived",
                    f"{result['image_id']}: used_bytes מכותרת partclone — {written}")
        if result["problems"]:
            journal(conn, "image_used_bytes_underived",
                    f"{result['image_id']}: לא הושלם — {'; '.join(result['problems'])}")
    try:
        usedbackfill.backfill(library, report=report)
    except Exception:                       # תהליכון רקע: חריגה אינה נבלעת בשקט
        log.exception("used_bytes backfill failed")
        journal(conn, "image_used_bytes_underived", "ההשלמה נכשלה — ראו יומן השרת")


def _readback_reporter(conn: Connection):
    """‏#1805: שורת יומן לכל אימג' שנוסה — מה גובב לקריאה חוזרת, ומה לא ולמה."""
    def report(result: dict) -> None:
        written = ", ".join(f"p{i}" for i in sorted(result["written"]))
        if written:
            journal(conn, "image_readback_derived",
                    f"{result['image_id']}: גיבוב לקריאה חוזרת — {written}")
        if result["problems"]:
            journal(conn, "image_readback_underived",
                    f"{result['image_id']}: לא גובב — {'; '.join(result['problems'])}")
    return report


def create_runtime(
    data_dir: str | Path,
    images_root: str | Path,
    server_base: str,
    *,
    now_fn=None,
    sender_runner=None,
    sender_portbase: int | None = None,
    # ‏e2e_simulation בלבד: שולח מדומה (ראו sender.Sender.simulate).
    sender_simulate: bool = False,
    auto_reboot_hook=None,
    auto_reboot_interval: float = auto_reboot.CHECK_SECONDS,
    # שתי תקרות ההמתנה של udpcast. ‏None = ברירת המחדל של `sender.py`
    # (כמו `sender_portbase`): הערך בייצור אינו זז, ומי שצריך תקרה קצרה —
    # מעבדה, טסט, ‏e2e — מעביר אותה כאן במקום לחכות דקות (#341).
    sender_max_wait: int | None = None,
    sender_start_timeout: float | None = None,
    wol_send=None,
    interface: str | None = None,
    dhcp_hooks: dict | None = None,
    health_hooks: dict | None = None,
    storage_hooks: dict | None = None,
    storage_check_interval: float = storage_locations.CHECK_INTERVAL,
    netcfg_hooks: dict | None = None,
    known_macs_hooks: dict | None = None,
    # ‏#855: ‏`{"leases": identity.LeaseFile(...)}` — מקור חכירות ה-DHCP
    # לשומר הזהות של hello/progress. ‏`None` = השומר אינו מותקן (בדיקות
    # יחידה, כמו `dhcp_hooks`); ‏`server.main` מעביר תמיד, ו-e2e מעביר
    # קובץ משלו. אין ברירת מחדל אמיתית כאן בכוונה: קובץ dnsmasq של המכונה
    # שהטסטים רצים עליה אינו ראיה על מכונות מדומות.
    identity_hooks: dict | None = None,
    # ‏#748: ברירת המחדל None משאירה את ``repo_dir`` כתיקיית העבודה של
    # ``server/update.py`` בזמן הריצה (main.py הוא היחיד שמעביר ערך אמיתי).
    repo_dir: str | Path | None = None,
    update_hooks: dict | None = None,
    netcfg_state_dir: str | Path | None = None,
    boot_dir: str | Path | None = None,
    extra_cmdline: tuple[str, ...] = (),
    # ‏Storage Nodes (#655/#723): תפקיד ההתקנה. ‏None = לא הועבר (בדיקות,
    # וקוד ישן) ואז ההגדרה נשארת כפי שהיא — התפקיד נקרא ממילא fail-closed
    # (‏storage_nodes.role). ‏main מעביר תמיד את הערך מהדגל (ברירת מחדל
    # standalone), וזו הכתיבה בהפעלה.
    storage_role: str | None = None,
    primary_url: str | None = None,
    # ‏#456: הקצב של שעון-הרקע של החדר. ברירת המחדל היא הייצור; בדיקות
    # מעבירות ערך קצר כדי שהלולאה תדגום מהר.
    room_clock_interval: float = ROOM_CLOCK_SECONDS,
    # ‏#1564: ראו ``ServerRuntime.heartbeat_interval``.
    heartbeat_interval: float = storage_heartbeat.HEARTBEAT_SECONDS,
    # ‏#1737: ראו ``ServerRuntime.housekeeping_first``.
    housekeeping_first: float = housekeeping.FIRST_RUN_S,
    housekeeping_every: float = housekeeping.EVERY_S,
    # ‏#703 (tracer 5): ``None`` = הקונסולה בלי TLS (loopback/בדיקות).
    console_tls: ConsoleTLS | None = None,
    # ‏#1088: ראו ``ServerRuntime.deploy``.
    deploy: "deploy_net.DeployContext | None" = None,
    # ‏#153: ראו ``ServerRuntime.host_hooks``.
    host_hooks: dict | None = None,
    # ‏#1013: בעלייה, לכתוב את קובץ ה-dnsmasq הראשי מה-DB אם הוא שונה ממה
    # שעל הדיסק (‏`console_dhcp.sync_main_conf`). ‏False כברירת מחדל מאותו
    # טעם כמו ``known_macs_hooks``: רק ``main`` מדליק; בדיקות לא נוגעות
    # ב-dnsmasq של המכונה שהן רצות עליה.
    sync_dnsmasq: bool = False,
    # ‏#705: שלבי האכיפה של השירותים לכל כרטיס (‏`nic_services.NicServices`)
    # ועוד hooks שלו — בדיקות מזריקות; ‏`main` מוסיף את המאזינים אחרי שהם קיימים.
    nic_hooks: dict | None = None,
    # ‏#1334: מנוע עדכון ה-VM. ‏None = המנוע האמיתי מול המארח; בדיקות
    # מזריקות מנוע עם זיופים (כמו ``dhcp_hooks``).
    vmupdate_engine: "VmUpdate | None" = None,
    # ‏#1372: ראו ``ServerRuntime.vm_host``. ‏False כברירת מחדל מאותו טעם כמו
    # ``sync_dnsmasq``: רק ``main`` שואל את המארח שהוא רץ עליו.
    vm_host: bool = False,
    # הכרעת נדב 15/10: ‏`drive_rates.read_link` — מהירות כרטיס ההפצה, הגג
    # והפתיחה של הקצב לפיה. ‏None כברירת מחדל מאותו טעם כמו ``sync_dnsmasq``:
    # רק ``main`` קורא את ‎/sys של המכונה; בלעדיו — 600m/900m של היום.
    link_speed=None,
    # ‏#1494: השלמת `used_bytes` לאימג'ים ישנים מכותרת partclone, ברקע, פעם
    # אחת בעלייה. ‏False כברירת מחדל מאותו טעם כמו ``sync_dnsmasq``: רק
    # ``main`` מדליק; בבדיקות תהליכון רקע שמריץ zstd על ספריית tmp היה כותב
    # ליומן בזמן לא צפוי.
    backfill_used_bytes: bool = False,
    # ‏#1805: הגיבוב לקריאה החוזרת (readbackhash.py) — תהליכון רקע שעובר על
    # הספרייה בעלייה, כשאימג' נכנס (`kick`), ופעם בעשר דקות. ‏False כמו למעלה:
    # רק ``main`` מדליק.
    backfill_readback: bool = False,
    # ‏#1447: אזור הזמן של המערכת מיושר להגדרה בעלייה (``timedatectl``).
    # ‏False כברירת מחדל מאותו טעם כמו ``sync_dnsmasq``: רק ``main`` מדליק —
    # בבדיקות ברירת המחדל הייתה משנה את אזור הזמן של מכונת הבדיקות.
    apply_timezone: bool = False,
    apply_timesync: bool = False,
) -> ServerRuntime:
    """בונה את המצב המשותף פעם אחת ומריץ את האתחול החד-פעמי.

    כל מה שהיה בראש `create_app` עד בניית ה-ctx יושב כאן עכשיו: פתיחת
    ה-DB, ה-sweep, ה-drain, יצירת המנהל. שתי האפליקציות נבנות ממנו."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    conn = connect(data_dir / "imagectl.db")
    auto_reboot.consume_marker(conn, data_dir)

    # ‏#655/#723: תצורת ה-Storage Node נכתבת בהפעלה כשהיא הועברה (main).
    # ‏None = לא הועבר — משאירים את ההגדרה הקיימת; התפקיד נקרא ממילא
    # fail-closed. הוולידציה (משני מחייב כתובת-אב) נעשתה כבר ב-main.
    if storage_role is not None:
        storage_nodes.persist_config(conn, storage_role, primary_url)

    library = ImageLibrary(images_root)
    library.bind_invalid_reporter(
        lambda path, reason: journal(
            conn, "image_manifest_rejected",
            f"המניפסט {path} הוצא מהספרייה: {reason}", ""))
    storage_locations.ensure_local(conn, library.root)
    storage_locations.mark_unchecked_after_start(conn)   # ‏#2123
    library.bind_locations(
        lambda: storage_locations.library_roots(conn, library.root))
    # ‏#1334: ברירת המחדל לכל שושלת גרסאות — מה שמחשב בנייה מקבל לסבב חדש.
    library.bind_defaults(lambda: image_versions.pointers(conn))

    # ‏#88: מה שקליטה שהשרת מת באמצעה השאירה בשורש הספרייה נסחף כאן —
    # אבל רק מה שטבלת המשימות מוכיחה שהוא יתום. ההנמקה המלאה, כולל למה
    # אוטומטי ולא דיווח-והמפעיל-ימחק, בראש work_areas.py. עולה מיד אחרי
    # פתיחת ה-DB: זה מקור הראיה, ואין מה לסחוף בלעדיו.
    sweep_work_areas(conn, library.root)

    # ‏#1494: אימג' שנקלט לפני #1368 נושא `used_bytes: null`, וכלל המקום הפנוי
    # (‏#1354) מסרב לו בכל מגירה. ברקע: קריאת כותרת למחיצה מהירה, אבל מיקום
    # רשת שנפל לא יעכב את עליית השרת; כל קריאה תחומה בטיימר משלה.
    if apply_timezone:
        timezone.apply_on_start(conn, {**timezone.default_hooks(), **(host_hooks or {})})
    if apply_timesync:
        timesync.apply_on_start(conn, {**timesync.default_hooks(), **(host_hooks or {})})

    if backfill_used_bytes:
        threading.Thread(target=_backfill_used_bytes, args=(conn, library),
                         name="used-bytes-backfill", daemon=True).start()

    # ‏#56: זרוע ההחזרה (`imagectl-netrollback`) רצה כשהשרת **לא** רץ —
    # זו כל מטרתה — ולכן אין לה חיבור ל-DB. היא משאירה פירור על הדיסק,
    # והוא הופך לשורת יומן כאן, בהפעלה שאחריה, עם זמן ההחזרה האמיתי.
    # מיד אחרי פתיחת ה-DB ולפני שהקונסולה יכולה לענות: מי שפותח את
    # היומן אחרי אתחול חייב לראות שם ששינוי הרשת שלו לא נתפס.
    #
    # ברירת המחדל של התיקייה נגזרת מ-`--data-dir`, וביחידה זה בדיוק
    # `netcfg_host.STATE_DIR` — שני הצדדים חייבים להסכים, אחרת הסמן
    # נכתב במקום אחד ונקרא באחר, וההגנה פשוט לא קיימת.
    netcfg_dir = Path(netcfg_state_dir) if netcfg_state_dir else data_dir / "netcfg"
    netcfg_dir.mkdir(parents=True, exist_ok=True)

    # ‏WoL יוצא על **וילן ההפצה בלבד**. בלי הכפייה הזו השידור הולך לפי
    # טבלת הניתוב — ובשרת דו-כרטיסי ברירת המחדל היא הרשת הרגילה של
    # המכללה. אז מחשבי השיכפול לא מתעוררים (הם בוילן ההפצה), והמכללה
    # מקבלת ברודקאסט שאין לה מה לעשות איתו. נמדד במעבדה (בדיקה 7.2,
    # 2026-08-29): ‏12 חבילות על eth1 ואפס על eth0.
    #
    # ‏#44 הוסיף את היכולת לכפות ממשק ואת הטסטים סביבה — אבל הטסטים
    # מזריקים שולח, ואיש לא בנה אחד למסלול האמיתי: `wol_send` נשאר
    # `None` וכל שידור נפל לברירת המחדל חסרת-הממשק. יכולת שקיימת ואינה
    # מחוברת נראית בדיוק כמו יכולת שעובדת (עיקרון 5).
    if wol_send is None and interface:
        wol_send = wol.broadcast_sender(interface)

    # מנוע השידור מחובר למעברי המצב של הסבב: running מתחיל לשדר,
    # סגירה עוצרת. ניהול הסבב עצמו לא יודע מה זה udpcast.
    #
    # תהליכון השידור כותב ליומן, ופעם היה צריך לשם כך חיבור משלו:
    # sqlite לא סובל שני תהליכונים שמנהלים טרנזאקציות על אותו חיבור.
    # היום ההפרדה הזאת נמצאת ב-db.Database, שנותן חיבור לכל תהליכון —
    # לא רק לתהליכון השידור אלא גם לתהליכוני המאגר של uvicorn, שמריצים
    # כל endpoint סינכרוני וכל dependency סינכרוני של הקונסולה.
    # ‏#201: `sender_portbase` הוא הפורט שהשידור **באמת** יתפוס. הוא נשאר
    # `None` בייצור, ואז נופלים על `DEFAULT_PORTBASE` (9000) — ברירת המחדל
    # לא זזה. מי שמעביר ערך הוא מי שאסור לו להתנגש בהפצה אמיתית: ה-e2e,
    # שמריץ שרת אמיתי בתת-תהליך ולכן אין לאן להזריק לו שולח מזויף. הגנה
    # שאינה תלויה בכך שמישהו יזכור לנקות — פורט שאינו יכול להתנגש (#156).
    def on_sender_event(event: str, detail: str) -> None:
        journal(conn, event, detail)
        status = sender.status()
        session_id = status.get("session_id") if status else None
        if session_id and event in ("send_done", "send_failed", "send_stopped"):
            # קצב אוטומטי (11/10): מה ש-udp-sender ספר בגל שהסתיים — הגל הבא
            # עולה רק על אפס שידורים חוזרים שנקראו כאן. לעולם אינו זורק.
            # ‏#1758: ואיך השולח סיים — גל שנכשל עם שידורים חוזרים הוא ההגנה.
            outcome = {**sender.wave_outcome(session_id), "ended": event}
            drive_rates.record_stream(conn, session_id, outcome)
            # ‏#1884: הדיסק של הספרייה בגל — לפני #1846, שלא יאשים את הרשת בגל שהשרת האט.
            server_io.end(conn, "wave", session_id, library.root, seconds=outcome.get("wall_s"))
            # ‏#1846: האבדן של הגל מול הבסיס של כרטיס ההפצה — קפיצה נרשמת ביומן.
            # לעולם אינו זורק.
            learn_netloss.evaluate(conn, session_id)
        if event != "send_failed":
            return
        if session_id:
            store.fail(
                session_id, detail,
                related=lambda: room.fail_wave(
                    conn, session_id, detail, commit=False,
                ),
            )

    sender = SenderEngine(
        library,
        interface=interface,
        on_event=on_sender_event,
        **({"runner": sender_runner} if sender_runner else {}),
        **({"portbase": sender_portbase} if sender_portbase is not None else {}),
        **({"simulate": True} if sender_simulate else {}),
        **({"max_wait": sender_max_wait} if sender_max_wait is not None else {}),
        **({"start_timeout": sender_start_timeout}
           if sender_start_timeout is not None else {}),
        link_speed=link_speed,
        # ‏#447 + קצב אוטומטי (11/10): הקצב לגל — מהגל הקודם של כל מחשב שיכפול
        # (#1471) והכוננים שבו, לעולם לא מעל `max_bitrate` (בלעדיו — 900m) ולא מעל
        # 90% מהקישור של כרטיס ההפצה (15/10).
        # ‏#1884: באותו רגע — צילום הדיסק של הספרייה ולחץ ה-I/O של השרת (לעולם אינו זורק).
        wave_bitrate=lambda session: (
            server_io.begin(conn, "wave", session["id"], library.root),
            drive_rates.decide(
                conn, session["id"], lambda: room.wave_drives(conn, session["id"]),
                sender.max_bitrate, link=sender.link))[1],
        # ‏#1430: `store` נבנה למטה (הוא צריך את `sender.stop`); הקריאה
        # היא מתהליכון השולח, הרבה אחרי שהפונקציה הזו סיימה.
        live_receivers=lambda session_id: store.live_count(session_id),
        # ‏#1950: חבר `lost` שעדיין מקבל את הזרם נספר במחיצה הבאה.
        lost_receivers=lambda session_id: store.lost_ips(session_id),
        # ברזל 17/10: שאלת SMART פתוחה בגל עוצרת את שעון ה-180 של השולח.
        # מכונה שחרגה מהתקרה (‏ASK_HOLD_MAX_SECONDS) משתחררת מהגל לבדה.
        pending_asks=lambda session_id: smart_ask.waiting_in_wave(conn, session_id),
    )
    def wake_class(group_id: str, opener_mac: str | None,
                   roster: list[str] | None = None) -> None:
        # סבב שנפתח — מכל מקור — מעיר את מחשבי הכיתה, חוץ מזה שפתח.
        # סבב עם בחירת מחשבים מעיר רק את הנבחרים.
        woken = wol.wake_group(conn, group_id, exclude_mac=opener_mac,
                               only=set(roster) if roster else None,
                               **({"send": wol_send} if wol_send else {}))
        if woken:
            journal(conn, "wol_sent", f"{group_id} count={woken}")

    def on_running(session: dict) -> None:
        # ‏#715: גל שהמקור שלו הוא מחשב בנייה — המנוע של השרת **אינו**
        # משדר; מחשב הבנייה רואה `running` ב-hello הבא ומשדר בעצמו.
        # ביומן, כדי שגל שלא יצא ייראה כמה שהוא ולא כ"המנוע לא התחיל".
        if direct.is_direct_wave(conn, session["id"]):
            journal(conn, "send_delegated", f'{session["id"]} {session["image_id"]}')
            # קצב אוטומטי: גם הזרם ממחשב הבנייה עובר ברשת — אותה פתיחה, אותו
            # גג וירידה לכונן איטי; ‏`task.direct.multicast.max_bitrate` מכאן.
            drive_rates.decide(conn, session["id"],
                               lambda: room.wave_drives(conn, session["id"]),
                               sender.max_bitrate, direct=True)
            return
        sender.start(session)

    store = SessionStore(
        conn,
        on_running=on_running,
        on_closed=lambda session_id: sender.stop(session_id),
        on_opened=wake_class,
        **({"now_fn": now_fn} if now_fn else {}),
    )
    # ‏#411: מה שנשאר `running` מהריצה הקודמת — משימת קליטה, סבב שידור —
    # מסומן כישלון **בשם** לפני שהקונסולה עונה (הכרעת נדב 06/09, אפשרות
    # 1). ‏`sender.status()` הוא הראיה החיובית היחידה שסבב עדיין משודר;
    # מנוע שזה עתה נבנה אינו משדר דבר. אחרי ה-sweep (שמשאיר `running`
    # במכוון) ולפני שמישהו יכול לקרוא את הסבב "פעיל" ב-hello.
    reconciled = reconcile_on_start(conn, library.root, sender.status())
    # ‏#1470: קליטה שהאימות שלה נקטע באתחול — האימות רץ מחדש מההתחלה, ברקע;
    # האימג' נשאר מחוץ לספרייה עד שהוא עובר.
    for task_id in reconciled["tasks_reverify"]:
        start_verification(conn, library.root, task_id)
    # ‏#720: ספריית חבילות הדרייברים — תיקייה ליד ה-DB, לא בספריית האימג'ים.
    ctx = ServerContext(conn=conn, library=library, store=store, sender=sender,
                        drivers=DriverLibrary(data_dir / "drivers"),
                        leases=(identity_hooks or {}).get("leases"),
                        data_dir=data_dir)
    drain_crumbs(ctx, netcfg_dir)
    # ‏#705: השירותים לכל כרטיס — **לפני** ה-render של dnsmasq בעלייה, כי הוא
    # קורא אותם; ‏`startup` מחזיר שינוי שפג בזמן שהשרת לא רץ.
    ctx.nic_services = _nic_services(ctx, interface, deploy, dhcp_hooks, sync_dnsmasq,
                                     nic_hooks, netcfg_hooks, health_hooks,
                                     apply_timesync=apply_timesync)
    ctx.nic_services.startup()
    sender.gate = ctx.nic_services.multicast_refused

    # בהתקנה טרייה: admin/admin עם החלפה כפויה (#1085). אם כבר יש מנהל — לא נוגעים.
    password = ensure_admin(conn)
    if password:
        print("\n  כניסה ראשונה: admin / admin — הקונסולה תדרוש החלפת סיסמה\n",
              flush=True)

    # ‏#141: KNOWN_MACS_CONF הוא נגזרת של טבלת המכונות, לא מקור — קובץ
    # שנמחק או לא נכתב מעולם משאיר **כל** מכונה בלי dhcp-boot (עיקרון 5).
    # נכתב מחדש כאן, בעליית השרת, ולא רק כשמישהו עורך מכונה מהקונסולה
    # (console_api._sync_known_macs, אותם hooks בדיוק). ‏`known_macs_hooks`
    # הוא `None` כברירת מחדל בכוונה: ‏main.py הוא היחיד שמדליק אותו
    # במפורש — בלעדיו זו הרצת בדיקות, ואסור לה לגעת בדיסק/ב-dnsmasq.
    if known_macs_hooks is not None:
        error = known_macs_hooks["apply"](dhcp.render_known_macs(registry.all_macs(conn)))
        if error:
            journal(conn, "known_macs_apply_failed", error)
    if sync_dnsmasq:
        console_dhcp.sync_main_conf(
            ctx, {**console_dhcp.default_hooks(), **(dhcp_hooks or {})}, deploy)

    engine = vmupdate_engine or VmUpdate(
        conn, library,
        # ‏ה-VM לעולם אינו יוצא לאינטרנט דרך כרטיס ההפצה (docs/vm-update.md).
        deploy_interface=lambda: deploy.state.interface if deploy else None)
    host = (VmHost(engine.host_problem, repo_dir or Path(__file__).resolve().parents[1])
            if vm_host else None)
    if host is not None:
        health_hooks = {**(health_hooks or {}), "vm_host": host.health}
    if backfill_readback:
        # ‏#1864 (ברזל 20/10): אחרי שהשולח קיים — הגיבוב מפנה לו את הדרך (`server_busy`),
        # ואימג' שנפתח לו סבב קודם לאימג' גדול שאיש לא פורס (`last_use`, ‏`kick`).
        threading.Thread(target=readbackhash.run_forever, args=(library,),
                         kwargs={"report": _readback_reporter(conn),
                                 "busy": readbackhash.server_busy(conn, sender),
                                 "rank": readbackhash.last_use(conn)},
                         name="readback-digest", daemon=True).start()
    return ServerRuntime(
        ctx=ctx, conn=conn, library=library, store=store,
        server_base=server_base, data_dir=data_dir, netcfg_dir=netcfg_dir,
        wol_send=wol_send, boot_dir=boot_dir, extra_cmdline=extra_cmdline,
        dhcp_hooks=dhcp_hooks, health_hooks=health_hooks,
        storage_hooks=storage_hooks,
        storage_check_interval=storage_check_interval,
        netcfg_hooks=netcfg_hooks,
        known_macs_hooks=known_macs_hooks,
        repo_dir=repo_dir, update_hooks=update_hooks,
        room_clock_interval=room_clock_interval,
        heartbeat_interval=heartbeat_interval,
        housekeeping_first=housekeeping_first, housekeeping_every=housekeeping_every,
        console_tls=console_tls, deploy=deploy, host_hooks=host_hooks,
        vmupdate=engine, vm_host=host,
        auto_reboot_hook=auto_reboot_hook or auto_reboot.default_reboot,
        auto_reboot_interval=auto_reboot_interval,
    )


def _nic_services(ctx, interface, deploy, dhcp_hooks, sync_dnsmasq, nic_hooks,
                  netcfg_hooks=None, health_hooks=None, apply_timesync=False):
    """‏#705: ‏`NicServices` עם הבסיס של היום (כרטיס הניהול מה-`DeployContext`,
    כרטיס ההפצה של השולח) ושני שלבי אכיפה שחיים כאן — dnsmasq וחומת האש.
    ‏dnsmasq מחובר רק כשיש hooks מפורשים או ‏`sync_dnsmasq` (‏main): ברירות
    המחדל האמיתיות כותבות ל-/etc ומפעילות שירות, ובדיקה אסור לה."""
    from . import nic_services, timesync

    hooks = dict(nic_hooks or {})
    # ‏#113: אותה רשימת כרטיסים שהוזרקה לשאר הלשוניות — בדיקה לא קוראת את המכונה
    injected = next((h["interfaces"] for h in (dhcp_hooks, netcfg_hooks, health_hooks)
                     if h and "interfaces" in h), None)
    if "interfaces" not in hooks and injected is not None:
        hooks["interfaces"] = injected
    if "dnsmasq" not in hooks and (dhcp_hooks is not None or sync_dnsmasq):
        merged = {**console_dhcp.default_hooks(), **(dhcp_hooks or {})}

        def dnsmasq_step(_nic):
            # רשת הפצה שטרם הוגדרה (#1088): dnsmasq כבוי — אין מה לשער.
            if deploy is None or not deploy.state.configured:
                return None
            return console_dhcp.apply_dnsmasq(ctx, merged, deploy, "nic services", "")
        hooks["dnsmasq"] = dnsmasq_step
    if "firewall" not in hooks and deploy is not None:
        def firewall_step(nic):
            regenerate = (deploy.hooks or {}).get("firewall", deploy_net.regenerate_firewall)
            per = nic.firewall_services()
            if per is None:      # חזרנו ל"היום" — הכללים לפי שני התפקידים
                return regenerate(deploy.repo_dir, deploy_if=deploy.state.interface,
                                  servers_if=deploy.servers_interface,
                                  primary_ip=deploy.primary_ip)
            return regenerate(deploy.repo_dir, deploy_if=None, servers_if=None,
                              primary_ip=deploy.primary_ip, services=per)
        hooks["firewall"] = firewall_step
    if "timesync" not in hooks and apply_timesync:
        chrony_hooks = timesync.default_hooks()

        def timesync_step(nic):
            interfaces, effective = nic.state()
            present = {row["name"]: row for row in interfaces}
            networks = []
            for name, services in effective.items():
                if "ntp" not in services:
                    continue
                for cidr in present.get(name, {}).get("addresses") or []:
                    try:
                        networks.append(str(__import__("ipaddress").ip_interface(cidr).network))
                    except ValueError:
                        continue
            timesync.apply_server(networks, chrony_hooks)
            return None
        hooks["timesync"] = timesync_step
    base = nic_services.Baseline(
        console=deploy.servers_interface if deploy is not None else None,
        deploy=interface or (deploy.state.interface if deploy is not None else None))
    service = nic_services.NicServices(ctx.conn, base, hooks)
    if apply_timesync and hooks.get("timesync"):
        # startup default is explicit port 0. A server without chrony (or its
        # chrony.conf) still comes up: the failure is a named journal line,
        # not a server that never answers (enforce() catches the same way).
        try:
            hooks["timesync"](service)
        except Exception as exc:                              # noqa: BLE001
            from .db import journal
            journal(ctx.conn, "timesync_apply_failed",
                    f"שרת NTP לפי כרטיס: {exc or type(exc).__name__}")
    return service


#: ‏#1266: הדגל שאינו מוגש לתחנות מ-PXE, גם כשהועבר ב-`--extra-cmdline`.
#: הסוכן כבר אינו מכיר אותו (#1303); ההסרה נשארת בשביל initramfs ישן.
STRIPPED_CMDLINE = "imagectl.debug"


def _boot_asgi(rt: ServerRuntime):
    """אפליקציית ה-ASGI הגולמית של ‎/boot, בנויה מ-runtime משותף.

    כל מה ש-GRUB נוגע בו — התפריט, הקרנל וה-initramfs — מוגש מכאן:
    ה-HTTP של GRUB מזהה Content-Length רק באותיות גדולות, ו-Starlette
    ממקטין כותרות. ההסבר המלא ב-boot/http.py (issue #12). ‏extra_cmdline:
    תוספות מפעיל לשורת הקרנל דרך הגדרה — לא בעריכת קוד על השרת (#18).

    התצורה נבנית **בכל בקשה**, כי `imagectl.monitor` אינו תוספת מפעיל
    אלא מתג בקונסולה (#690), שצריך לתפוס בלי להפעיל מחדש.
    ‏`imagectl.debug` (מעטפת הניפוי המקומית של הסוכן) **מוסר** מתוספות
    המפעיל תמיד: עד #1266 רק מתג ה-SSH בקונסולה הכניס אותו, והמתג יצא
    מהמוצר — אז הדגל אינו מוגש מ-PXE לעולם (`STRIPPED_CMDLINE`)."""
    conn = rt.conn
    resolve = make_resolver(conn, rt.library, rt.store, rt.server_base)

    async def boot_asgi(scope, receive, send):
        # ‏#584: שביל הפירורים (#400) נרשם רק אם הבקשה התקבלה על וילן
        # ההפצה — אותה הכרעה בדיוק כמו הספירה של #536, ומאותו טעם:
        # ה-MAC שבשאילתה אינו זהות, ומחוץ לווילן אין ראיה שהפונה הוא
        # המכונה. פירור זר היה הופך "לא ידוע" ל"בדקנו, הגיע" (עיקרון 5)
        # ומאפס את `first_at` של שביל אמיתי. ההכרעה המודעת (#584): פירור
        # מחוץ לווילן **אינו נרשם**; המחיר המוצהר הוא שתחנת תרחיש-3 (#39)
        # שמאתחלת מחוץ לווילן מאבדת את שביל האבחון שלה. ‏boot/ אינו מכיר
        # טופולוגיית רשת, ולכן ההחלטה יושבת כאן, בדיוק כמו ה-resolver.
        off_vlan = off_deploy_vlan(scope, rt.server_base)
        # ‏#976: הבדיקה העצמית של השרת מסומנת, ומכובדת רק כשהפונה הוא
        # השרת עצמו (‏`self_probe`). היא מקבלת את התפריט האמיתי ואינה
        # רושמת דבר — לא `net_seen`, לא פירור ולא ספירת אתחול.
        probe = self_probe(scope)
        await create_boot_asgi(
            # ‏#536: הסקופ של **הבקשה הזו** נכנס ל-resolver, שרק ממנו
            # אפשר לדעת על איזו מכתובות השרת היא התקבלה. הספירה של
            # ‏`bootguard` תלויה בזה, ולכן ה-resolver נקשר כאן ולא פעם
            # אחת למעלה. ‏`boot/` אינו מכיר טופולוגיית רשת ואינו צריך.
            resolve=lambda mac, ip: resolve(mac, ip, scope),
            config=GrubConfig(
                server_base=rt.server_base,
                # ‏#32: נבדק בכל בקשה, ומאותה סיבה שהתצורה כולה נבנית
                # בכל בקשה — ‏initramfs גרפי שנוסף או הוסר מתיקיית האתחול
                # תופס מיד, בלי הפעלה מחדש של השרת. הבדיקה זולה (stat
                # אחד), ובקשת אתחול היא ממילא אירוע נדיר לכל מכונה.
                gui_initrd_path=gui_initrd_path(rt.boot_dir),
                # #690: the monitor flag has one source of truth, the console
                # switch; #1266: imagectl.debug is never served from PXE.
                extra_cmdline=monitor.station_cmdline(
                    tuple(p for p in rt.extra_cmdline
                          if not p.startswith(STRIPPED_CMDLINE)),
                    monitor.stations_enabled(conn)),
            ),
            boot_dir=rt.boot_dir,
            # ‏#400: שביל הפירורים. מוזרק כמו ה-resolver — ‏`boot/` אינו
            # מכיר DB, והשרת אינו מכיר את תחביר ה-GRUB. ‏#584: מחוץ לווילן
            # ההפצה הרושם דוחה את הפירור בגלוי במקום לכתוב אותו.
            record=(_probe_decline if probe
                    else _off_vlan_decline if off_vlan
                    else lambda mac, step: boottrace.record(conn, mac, step)),
        )(scope, receive, send)

    return boot_asgi


def _add_agent_routes(app: FastAPI, rt: ServerRuntime, *, gui_channel: bool = True) -> None:
    """הצד שהסוכן (initramfs) והקושחה רואים: ‎/api/v1 (‏hello/login/pulls/
    progress/disk-event/images), ‏capture הסוכן, מסך התחנה, ומאזין ה-‎/boot.

    ‏#824: `create_station_router` (‏`/api/v1/agent/{state,groups,sessions}`)
    הוא גם ה-API שהסוכן על המכונה צורך — ‏guistate.sh/guibridge.sh/
    guiparent.sh/classround.sh קוראים אותו דרך `$SERVER`, שהוא פורט הסוכן.
    הנתיבים האלה מחזירים את מה ש-hello מחזיר ממילא למכונה (ראה ה-docstring
    של ‏`station_state`), ואין ביניהם נתיב ניהול — הפעולה היחידה, פתיחת סבב,
    דורשת שם וסיסמה. ולכן הם חיים כאן ולא על הקונסולה (#703: הגבול הוא
    ה-socket). ‏#1247: מסך התחנה בדפדפן והמאזין ‎:8082 הוסרו; הגואי הנייטיב
    הוא הצרכן היחיד."""
    app.include_router(create_agent_router(rt.ctx, rt.server_base, rt.data_dir))
    app.include_router(create_agent_drivers_router(rt.ctx))   # #720
    app.include_router(create_cloner_round_router(rt.ctx))   # 16/10: a cloner opens a round on its own drawers
    app.include_router(create_agent_capture_router(rt.ctx))
    app.include_router(direct.create_direct_router(rt.ctx))   # #715
    # ‏17/09 (מדוד על השרת החי): הכניסה במסך מחשב הבנייה קיבלה 404 —
    # ‏buildmenu.sh/roomflow.sh קוראים `$SERVER/api/console/{login,folders,
    # images,tasks/capture,room…}` על פורט הסוכן. אותה משפחה כמו #824:
    # allowlist מחשב הבנייה (בלי שום נתיב ניהול); `create_station_router` כלול בו.
    # ‏#1576: מה שנכנס כאן הוא ערוץ הגואי של מחשב הבנייה -- השרת מסמן, לא הגוף.
    app.include_router(create_kiosk_router(rt.ctx, room_wake=_kiosk_room_wake(rt),
                                           version=_server_version(rt)),
                       dependencies=[Depends(mark_build_gui)] if gui_channel else [])
    app.mount("/boot", _boot_asgi(rt))


def _server_version(rt: ServerRuntime):
    """‏#1252: תג ה-git של השרת לכותרת הגואי (‏`/agent/state`) — אותו מקור
    כמו `/me.version` (‏#915) ואותה ברירת מחדל לתיקיית העץ (‏#748)."""
    repo_dir = rt.repo_dir or Path(__file__).resolve().parents[1]
    hooks = {**default_update_hooks(), **(rt.update_hooks or {})}
    return lambda: current_version(hooks, repo_dir)


def _kiosk_room_wake(rt: ServerRuntime):
    """שליחת ה-WoL של חדר השיכפולים — אותה פונקציה בקונסולה ובסוכן.
    ‏`only` (נדב 17/10) — רק מחשבי השיכפול שנבחרו, כל אחד לפי ה-MAC שלו (הם כבר
    אומתו כמחשבי שיכפול רשומים, בכל קבוצת שיכפול); ‏None = כל החדר."""
    conn = rt.conn
    kw = {"send": rt.wol_send} if rt.wol_send else {}

    def wake(only=None):
        if only is None:
            return wol.wake_group(conn, CLONERS_GROUP, **kw)
        each = [wol.wake_machine(conn, mac, **kw) for mac in sorted(only)]
        return wol.WakeResult(sum(int(r) for r in each), [m for r in each for m in r.failed],
                              [x for r in each for x in r.reasons])
    return wake


def _add_console_routes(app: FastAPI, rt: ServerRuntime) -> None:
    """הצד של הקונסולה/הניהול: נתיבי הניהול שהקונסולה צורכת (כולל room,
    שהוא גם מסך ניהול). ה-API `/api/v1/agent/*` חי על הסוכן (#824)."""
    ctx = rt.ctx
    health_hooks = dict(rt.health_hooks or {})
    # ‏#748: תיקיית העץ נופלת לתיקיית הקוד עצמה כשאין ``--repo-dir``
    # מפורש (בדיקות/הרצה ישירה) — ראו ``main.py``.
    repo_dir = rt.repo_dir or Path(__file__).resolve().parents[1]
    # ‏#915/#1297: הגרסה שמוצגת בקונסולה נקראת מאותו מקור כמו כפתור
    # "עדכן". ההכרעה נשמרת לפי HEAD, ולכן בקשות /me אינן מריצות שוב את
    # המאמת; checkout מחליף את מפתח המטמון.
    update_hooks = {**default_update_hooks(), **(rt.update_hooks or {})}
    # ‏#1297: warm the per-HEAD cache at application startup.  Requests reuse
    # it; a checkout changes HEAD and therefore computes a fresh value.
    started_with = display_version(update_hooks, repo_dir)
    # ‏#1546: ‏`/health/live` היא הראיה של server-upgrade.sh ש"החדש רץ" — ולכן
    # היא הגרסה שהתהליך **טען**, לא מה שעל הדיסק עכשיו (שרת חיפה, 17/10: העץ
    # על v0.75.0, תהליך של v0.74.2, ו-live אמר v0.75.0). הקריאה בעלייה היא
    # זו שכבר חיממה את המטמון למעלה — לא קריאה שנייה ל-`describe`: גרסה
    # ש"לא זמינה" נשמרת במטמון הזה פעם אחת, ו-`/agent/state` (#1252) חולק
    # אותו. קריאה שנכשלה בעלייה אינה ננעלת — `read_version` רץ שוב בבקשה
    # עד שהיא מצליחה (וכך גם הקטגוריה של `version_error`), ואז היא ננעלת.
    live_version = [VersionRead(started_with.version, None, None)
                    if started_with.version else None]

    def running_version():
        if live_version[0] is None:
            read = read_version(update_hooks, repo_dir)
            if read.version is None:
                return read
            live_version[0] = read
        return live_version[0]
    # ‏#1073: למשתמש הפצה אין קונסולה — `deploy` מקבל 403 על **כל** נתיב
    # ניהול, גם עם עוגייה שפורט הסוכן הנפיק (אותו סוד). ה-dependency נוסף
    # כאן, בהרכבה, ולא בתוך הראוטרים: ‏`kiosk.py` בונה מהם עותקים משלו
    # לפורט הסוכן, ושם deploy מותר — מחשב הבנייה נכנס משם.
    # ‏HTTP בלבד (`Request`): ה-WebSocket של המוניטור סוגר deploy ב-4403
    # **אחרי** accept (#904) כדי שהסיבה תגיע לדפדפן, ולכן נשאר מחוץ לזה.
    no_deploy = [Depends(auth.console_only(ctx.conn))]

    def include(router) -> None:
        app.include_router(router, dependencies=no_deploy)

    # ‏#968: ‏uptime מאותו מנגנון הזרקה של מסך הבריאות (health_hooks);
    # ‏deploy_ip מ-server_base — null כשרשת ההפצה טרם הוגדרה (#1088), כי
    # אז server_base הוא 127.0.0.1 ולא כתובת שתחנה רואה.
    uptime_hook = {**health_default_hooks(), **(rt.health_hooks or {})}["uptime"]
    include(create_console_router(
        ctx, rt.known_macs_hooks,
        version=lambda: display_version(update_hooks, repo_dir),
        tls=rt.console_tls,
        uptime=uptime_hook,
        deploy_ip=lambda: deploy_net.deploy_ip(
            rt.server_base, rt.deploy.state if rt.deploy else None)))
    include(create_storage_router(ctx, rt.data_dir))   # #727/#740
    include(create_storage_locations_router(
        ctx, rt.storage_hooks, rt.data_dir, rt.server_base))   # #1066
    include(create_backup_target_router(ctx, rt.storage_hooks, rt.data_dir))   # #1956
    include(create_library_router(ctx))
    include(create_console_data_router(ctx))   # #1882-#1889: מה שנאסף על מכונה
    include(create_drivers_router(ctx))   # #720
    include(create_tools_router(ctx, rt.data_dir))   # #649: ארגז הכלים — קטלוג + בחירה
    include(create_usb_router(ctx, rt.data_dir))   # #1914: הדיסק-און-קי לשיכפול — הורדה למנהל
    include(create_net_router(ctx))
    # ‏#1013: מתג ה-TFTP בדף הפורטים מחיל את אותם קובצי dnsmasq, דרך אותם
    # hooks, כמו לשונית ה-DHCP — מקום אחד שכותב את הקובץ.
    dhcp_hooks = {**console_dhcp.default_hooks(), **(rt.dhcp_hooks or {})}
    dhcp_router = create_dhcp_router(ctx, dhcp_hooks, deploy=rt.deploy)
    include(dhcp_router)
    include(create_netcfg_router(ctx, rt.netcfg_dir, rt.netcfg_hooks,
                                 dhcp_steps=dhcp_router.dhcp_steps))   # #1263
    include(create_nic_services_router(ctx))   # #705
    include(create_health_router(
        ctx, rt.server_base, health_hooks,
        dnsmasq_apply=lambda what, user_id: console_dhcp.apply_dnsmasq(
            ctx, dhcp_hooks, rt.deploy, what, user_id),
        version=running_version))   # #1159, #1546
    include(create_update_router(
        ctx, repo_dir, rt.server_base, rt.update_hooks,
        public_url=PUBLIC_UPDATE_URL,
        vm_warning=rt.vm_host.warning if rt.vm_host else None,   # #1372
        busy_source=rt))   # #1891
    include(create_hostcfg_router(ctx, rt.data_dir, rt.console_tls, rt.host_hooks,
                                  busy_source=rt))   # #153, #1891
    include(create_timezone_router(ctx, rt.host_hooks))   # #1447
    include(create_timesync_router(ctx, rt.host_hooks))   # #1773
    include(create_ups_router(ctx, (rt.host_hooks or {}).get("ups")))   # #1973
    # ‏#690: admin RFB proxy + settings — ה-HTTP שלו admin_only ממילא.
    app.include_router(create_monitor_router(ctx))
    # ‏#1334: ה-HTTP דרך include (console_only: deploy מקבל את הסירוב של #1073),
    # וה-WebSocket — כמו המוניטור — מחוץ לו, וסוגר בעצמו כל מי שאינו admin.
    if rt.vmupdate is not None:
        include(create_vmupdate_router(ctx, rt.vmupdate))
        app.include_router(create_vmupdate_viewer_router(ctx, rt.vmupdate))
    include(image_versions.create_versions_router(ctx))   # #1334: גרסאות + ברירת מחדל
    include(create_console_capture_router(ctx))
    include(create_room_router(ctx, wake=_kiosk_room_wake(rt)))
    # ‏#984: מכונה בודדת / grp_BUILD — אותו שולח מוזרק כמו בחדר.
    include(create_wake_router(ctx, rt.wol_send))
    # ‏#980: כיבוי החדר + היסטוריית סבבים — קונסולה בלבד, לא בסוכן.
    include(create_room_console_router(ctx))
    app.mount("/console", StaticFiles(directory=STATIC_DIR, html=True), name="console")
    app.add_middleware(ConsoleNoStaleCache)

    @app.get("/")
    def root() -> RedirectResponse:
        return RedirectResponse("/console/")


def create_agent_app(rt: ServerRuntime) -> FastAPI:
    """האפליקציה שהסוכן והקושחה מדברים איתה (‎:8080 בייצור)."""
    app = FastAPI(title="ImageSrv agent", docs_url=None, redoc_url=None)
    setattr(app.state, auth.KIOSK_APP_FLAG, True)   # #1340
    app.state.ctx = rt.ctx
    app.state.runtime = rt
    app.state.data_dir = rt.data_dir
    _add_agent_routes(app, rt)
    return app


def create_console_app(rt: ServerRuntime) -> FastAPI:
    """אפליקציית הקונסולה/הניהול (‎:8081 בייצור)."""
    app = FastAPI(title="ImageSrv console", docs_url=None, redoc_url=None)
    setattr(app.state, auth.CONSOLE_APP_FLAG, True)   # #1340
    app.state.ctx = rt.ctx
    app.state.runtime = rt
    app.state.data_dir = rt.data_dir
    _add_console_routes(app, rt)
    # ‏#456: שעון-הרקע של החדר יושב על הקונסולה — היא האפליקציה שרצה תמיד
    # בייצור, והחדר הוא ממילא מסך ניהול. אפליקציה **אחת** בכל תהליך.
    _install_room_clock(app, rt)
    return app


def create_alive_app(rt: ServerRuntime, fingerprint: str) -> FastAPI:
    """‏#1310: המאזין "אתה שם?" של הראשי על 8443 — אפליקציה **נפרדת**, בלי
    אף ראוטר של הקונסולה או הסוכן. השם הוא השם שהקונסולה מציגה בראש העץ,
    והגרסה מאותו מקור כמו `/me` (‏`_server_version`)."""
    from . import ports, primary_alive
    return primary_alive.create_alive_app(
        name=lambda: ports.server_name(rt.conn), version=_server_version(rt),
        fingerprint=fingerprint)


def create_app(
    data_dir: str | Path,
    images_root: str | Path,
    server_base: str,
    **kwargs,
) -> FastAPI:
    """מעטפת דקה: runtime אחד + שני צדי הראוטרים על אפליקציה אחת.

    זו ההתנהגות הישנה בדיוק (הכול על פורט אחד), והיא נשמרת בשביל בדיקות
    היחידה וה-TestClient שמדברים עם שני הצדדים דרך לקוח אחד. הייצור
    (`main.py`) מריץ את `create_agent_app`/`create_console_app` על שני
    פורטים; הפיצול עצמו נבדק ב-`tests/test_app_split.py`.

    הפרמטרים הם של `create_runtime` (ראה שם); מועברים דרך `**kwargs`
    כדי שמקור אמת אחד יגדיר אותם."""
    rt = create_runtime(data_dir, images_root, server_base, **kwargs)
    app = FastAPI(title="ImageSrv", docs_url=None, redoc_url=None)
    app.state.ctx = rt.ctx
    app.state.runtime = rt
    app.state.data_dir = rt.data_dir
    # allowlist הסוכן לפני הקונסולה: שניהם מגדירים POST /api/console/login,
    # והראשון מנצח. בבדיקות המשולבות (#1073) deploy נכנס דרך הסוכן (allowlist)
    # ומסורב על נתיבי ניהול (`console_only`). MFA והחלפה כפויה (#1085)
    # נבדקים על `create_console_app`, לא כאן — בייצור הם על שני פורטים.
    # ‏#1576: בלי סימון ערוץ הגואי -- כאן נתיבי הקיוסק מאפילים על אותם נתיבי
    # קונסולה, וכל קליטה "מהקונסולה" בבדיקות הייתה מסומנת build_gui. הסימון
    # נבדק על הפיצול האמיתי (`create_agent_app` + `create_console_app`).
    _add_agent_routes(app, rt, gui_channel=False)
    _add_console_routes(app, rt)
    # ‏#824: נתיבי התחנה (`create_station_router`) מגיעים מ-`_add_agent_routes`
    # — לא נוספים כאן בנפרד. תוספת מפורשת למעטפת בלבד היא בדיוק מה שהסתיר
    # את ה-404 של הסוכן בייצור אחרי #738.
    # ‏#456: המעטפת המשולבת (בדיקות/e2e) מריצה גם את שעון-הרקע של החדר,
    # כדי שההתנהגות תהיה זהה לייצור. אפליקציה אחת → שעון אחד.
    _install_room_clock(app, rt)
    return app
