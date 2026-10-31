"""ImageSrv — הקצה שמגיש ל-GRUB את התפריט ואת קבצי האתחול.

זה הקצה הדק בלבד. כל ההיגיון יושב ב-grub_menu.py, וה-lookup עצמו שייך
לשרת — הוא מוזרק פנימה כפונקציה. ככה המחולל לא נוגע ב-DB ולא ממציא ממשק.

למה ASGI גולמי ולא FastAPI: ה-HTTP של GRUB מזהה את Content-Length רק
באותיות גדולות. Starlette ממקטין כל כותרת, ואז GRUB לא יודע מתי הקובץ
נגמר — הוא מחכה ~30 שניות, מוותר, והמכונה עולה מהדיסק במקום מהסוכן.
uvicorn עצמו משמר את האותיות כפי שנשלחו, ולכן הנתיבים של GRUB עוקפים
את Starlette לגמרי. נמצא במעבדת ה-VM (issue #12) — GRUB אמיתי, שרת
אמיתי, ותפריט שאושר ב-ACK אבל מעולם לא בוצע.

חיבור בצד השרת:

    app.mount("/boot", create_boot_asgi(
        resolve=my_lookup,                       # ראו חתימת Resolver למטה
        config=GrubConfig(server_base="http://10.44.0.10:8080"),
        boot_dir="/srv/imagesrv/boot",           # vmlinuz + initrd.img
    ))
"""

import ipaddress
import logging
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import parse_qs

from . import trace
from .grub_menu import GrubConfig, normalize_mac, render, render_local_only

log = logging.getLogger("imagesrv.boot")

#: כותרות שחייבות ללוות כל תשובה — באותיות גדולות, בשביל GRUB.
#: בלי no-store, proxy ברשת המכללה יכול להגיש למחשב את ההחלטה של מחשב
#: אחר — או את זו של אתמול.
#:
#: **אין כאן `Connection: close`, בכוונה (#1423).** ‏GRUB יודע איפה הקובץ
#: נגמר לפי Content-Length, וסוגר את החיבור בעצמו כשהוא סוגר את הקובץ.
#: עם `close` השרת סגר ראשון ונשאר ב-TIME_WAIT לשישים שניות. ‏GRUB יוצא
#: מאותם פורטי מקור בכל אתחול (21550 ואילך) ואינו עונה ב-RST, ולכן אתחול
#: חוזר מהיר נתקע על כל פורט כזה כ-15.6 שניות של SYN חוזרים. החצי השני
#: של התיקון — RST כש-GRUB סוגר, במקום LAST-ACK — ב-`server/net/grub_close.py`.
BASE_HEADERS = [
    (b"Cache-Control", b"no-store, no-cache, must-revalidate, max-age=0"),
    (b"Pragma", b"no-cache"),
]

MEDIA_TYPE = "text/plain; charset=us-ascii"
FILE_CHUNK = 1 << 20

#: שם ה-initramfs הגרפי בתיקיית האתחול, לצד `initrd.img` הטקסטואלי (#32).
#: הבנייה מייצרת קובץ עם גרסה בשם (`initrd.img.gui-v0.15.11` במעבדה);
#: המפעיל מעתיק או מקשר את הגרסה הפעילה לשם הקבוע הזה, בדיוק כמו
#: `initrd.img` עצמו.
GUI_INITRD_NAME = "initrd.img.gui"


class Resolver(Protocol):
    """מה שהשרת מספק: MAC ו-IP מקור פנימה, תשובת שרת (ממשק 3) החוצה.

    כאן גם מקומה של שכבת האימות השנייה מסעיף 6 באפיון: המחולל לא מכיר
    טופולוגיית רשת, ולכן כל החלטה שתלויה ברשת שממנה הבקשה הגיעה שייכת
    ל-resolver. **מה שמיושם היום צר מזה, ובכוונה** (‏#536): בקשה שלא
    התקבלה על וילן ההפצה נענית כרגיל — תחנה מחוץ לווילן מושכת תפריט
    לגיטימי (תרחיש 3, ‏#39) — אבל **אינה נספרת** כאתחול ב-`bootguard`.
    ‏known=false לפי רשת המקור אינו מיושם: ה-MAC שבשאילתה אינו זהות,
    והתשובה לכך היא לא לספור אותו — לא לשקר על מה שידוע.
    """

    def __call__(self, mac: str, client_ip: str | None) -> dict: ...


def build_config_text(
    raw_mac: object,
    client_ip: str | None,
    resolve: Resolver,
    config: GrubConfig,
) -> str:
    """הליבה, בלי שכבת HTTP — נוח לבדיקות וגם לשרת אחר.

    לעולם לא זורקת. כל כישלון מחזיר קובץ שעולה מהדיסק המקומי.
    """
    mac = normalize_mac(raw_mac)
    if mac is None:
        log.warning("boot request with unusable mac %r from %s", raw_mac, client_ip)
        return render_local_only("missing or malformed mac")

    try:
        answer = resolve(mac, client_ip)
    except Exception:
        log.exception("resolver failed for %s from %s", mac, client_ip)
        return render_local_only("server lookup failed")

    text = render(answer, config)
    log.info("served boot config for %s from %s", mac, client_ip)
    return text


#: מה שהשרת מספק לרישום צעד: ‏MAC קנוני ושם צעד מהרשימה, החוצה "נרשם".
Recorder = Callable[[str, str], bool]


def record_step(record: Recorder | None, raw_mac: object, raw_step: object,
                client_ip: str | None, *, raw_up: object = None) -> bool:
    """רושם פירור אחד. לעולם לא זורקת — הקורא מחזיר ל-GRUB 200 בכל מקרה.

    כל דחייה נכתבת ליומן השרת, ובכוונה: הבקשה עצמה היא הדבר היחיד
    שהמכונה חסרת-הראש שולחת, ואם היא נופלת כאן בשקט — השביל **אינו
    קיים** ואיש לא יידע. ‏MAC פגום וצעד שאינו מהרשימה הם שני מצבים
    שונים, ולכן שתי שורות שונות ביומן.
    """
    mac = normalize_mac(raw_mac)
    if mac is None:
        log.warning("boot step %r from %s carried an unusable mac %r",
                    raw_step, client_ip, raw_mac)
        return False
    if not trace.is_step(raw_step):
        log.warning("boot step %r from %s (%s) is not in the declared list %s",
                    raw_step, mac, client_ip, trace.STEPS)
        return False
    if record is None:
        log.warning("boot step %r from %s arrived with no recorder wired in",
                    raw_step, mac)
        return False
    try:
        recorded = bool(record(mac, raw_step))
    except Exception:  # noqa: BLE001 — אבחון שמפיל בקשת אתחול הוא נזק
        log.exception("could not record boot step %s for %s", raw_step, mac)
        return False
    if recorded and raw_up is not None:
        try:
            marks = trace.parse_marks(raw_up)
            if marks is None:
                log.warning("boot marks %r from %s (%s) are malformed -- not logged",
                            raw_up, mac, client_ip)
            else:
                log.info("boot marks %s %s: %s", raw_step, mac,
                         " ".join(f"{key}={value:.2f}"
                                  for key, value in marks.items()))
        except Exception:  # noqa: BLE001 — diagnostics never alter the step result
            log.exception("could not log boot marks for %s", mac)
    return recorded


def gui_initrd_path(boot_dir: str | Path | None) -> str | None:
    """הנתיב המוגש של ה-initramfs הגרפי — ‏**רק אם הקובץ באמת שם** (#32).

    ‏`grub_menu` טהור ואינו נוגע בדיסק, ולכן הבדיקה יושבת כאן, במודול
    שממילא מגיש קבצים מהתיקייה הזאת. מה שחוזר הוא ראיה חיובית:
    ‏`is_file()` ענה True. כל דבר אחר — אין תיקיית אתחול, הקובץ חסר, אין
    הרשאה, נתיב פגום — מחזיר None, וכל התפקידים נופלים ל-initramfs
    הטקסטואלי.

    זה מה שמונע את הכשל הגרוע ביותר של #32: התפריט מפנה את GRUB לקובץ
    שאינו קיים, השרת מחזיר 404, והמכונה לא עולה בכלל. מכונה בלי גואי
    עובדת; מכונה שלא עולה לא (עיקרון 1).
    """
    if boot_dir is None:
        return None
    try:
        if not (Path(boot_dir) / GUI_INITRD_NAME).is_file():
            return None
    except Exception:  # noqa: BLE001 — נתיב פגום או הרשאה חסרה = אין גואי
        log.warning("could not check for %s under %r", GUI_INITRD_NAME, boot_dir)
        return None
    return f"/boot/{GUI_INITRD_NAME}"


def config_for_scope(scope: dict, config: GrubConfig) -> GrubConfig:
    """אותה תצורה, אבל עם הכתובת שאליה הלקוח באמת התחבר (issue #39).

    תחנה מחוץ לווילן ההפצה (תרחיש 3) מגיעה דרך ‎$net_default_server, ואם
    התפריט שהיא מקבלת מפנה את הקרנל לכתובת ההפצה הקבועה — היא לא תגיע
    אליה, ותיפול ל-chain_local. המקור כאן הוא ‎scope["server"], שאותו
    uvicorn ממלא מה-sockname של החיבור שהתקבל. מכוון: *לא* כותרת Host,
    שהיא קלט של הלקוח ולכן תשתול כתובת שרירותית בשורת הקרנל.

    לעולם לא זורקת: כל ערך שאינו זוג IPv4+פורט — חסר, IPv6, שם מארח,
    פורט ריק — חוזר לתצורה הקבועה, כמו לפני #39.
    """
    try:
        host, port = scope.get("server")
        if not isinstance(host, str) or isinstance(port, bool):
            return config
        if not isinstance(port, int) or not 0 < port < 65536:
            return config
        if ipaddress.ip_address(host).version != 4:
            return config
        return GrubConfig(
            server_base=f"http://{host}:{port}",
            kernel_path=config.kernel_path,
            initrd_path=config.initrd_path,
            gui_initrd_path=config.gui_initrd_path,
            extra_cmdline=config.extra_cmdline,
        )
    except Exception:  # noqa: BLE001 — כאן זו בדיוק הכוונה
        return config


def _response_headers(content_type: bytes, length: int) -> list:
    return [(b"Content-Type", content_type),
            (b"Content-Length", str(length).encode())] + BASE_HEADERS


def create_boot_asgi(resolve: Resolver, config: GrubConfig,
                     boot_dir: str | Path | None = None,
                     record: Recorder | None = None):
    """אפליקציית ASGI לנתיבי ‎/boot: התפריט + קבצי הקרנל וה-initramfs.

    ממופה ב-mount על "/boot", ולכן ה-path שמגיע לכאן הוא היחסי:
    "/menu", "/step", "/vmlinuz", "/initrd.img".

    ‏`record` הוא שביל הפירורים של #400 — מוזרק כמו ה-resolver, מאותה
    סיבה: המודול הזה אינו מכיר DB.
    """
    root = Path(boot_dir).resolve() if boot_dir else None

    async def send_all(send, status: int, body: bytes,
                       content_type: bytes = b"text/plain") -> None:
        await send({"type": "http.response.start", "status": status,
                    "headers": _response_headers(content_type, len(body))})
        await send({"type": "http.response.body", "body": body})

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        # Starlette חדש משאיר את הנתיב המלא ושם את התחילית ב-root_path;
        # ישן מקצץ בעצמו. מטפלים בשניהם — ה-mount לא אמור להכתיב לנו גרסה.
        path = scope["path"]
        prefix = scope.get("root_path", "")
        if prefix and path.startswith(prefix):
            path = path[len(prefix):] or "/"
        client_ip = scope.get("client")[0] if scope.get("client") else None

        # ‏#400: פירור. תמיד 200 וגוף של בייט אחד, גם כשלא נרשם דבר —
        # ‏GRUB מדפיס אותו, וקוד שגיאה כאן היה שגיאה על מסך האתחול.
        if path in (trace.STEP_PATH, "/step", "/step/"):
            params = parse_qs(scope.get("query_string", b"").decode())
            record_step(record,
                        (params.get("mac") or [None])[0],
                        (params.get("s") or [None])[0],
                        client_ip,
                        raw_up=(params.get("up") or [None])[0])
            await send_all(send, 200, trace.TINY_BODY, MEDIA_TYPE.encode())
            return

        if path in ("/menu", "/menu/"):
            macs = parse_qs(scope.get("query_string", b"").decode()).get("mac")
            # הכתובת שאליה התחנה באמת התחברה, ולא בהכרח זו שבתצורה (#39).
            body = build_config_text(macs[0] if macs else None, client_ip,
                                     resolve, config_for_scope(scope, config))
            # הצעד הראשון בשביל, והיחיד שאינו תלוי בכך שהמכונה עוד
            # מדברת: אם הוא לבדו רשום — היא נעצרה בדיוק כאן.
            record_step(record, macs[0] if macs else None, "menu", client_ip)
            # תמיד 200. GRUB מטפל גרוע בקודי שגיאה, ומחשב שנתקע במסך
            # אתחול גרוע בהרבה ממחשב שעלה מהדיסק שלו.
            await send_all(send, 200, body.encode("ascii", "replace"),
                           MEDIA_TYPE.encode())
            return

        # קובץ מתוך תיקיית האתחול — שם קובץ בלבד, בלי תתי-תיקיות.
        name = path.lstrip("/")
        target = (root / name) if root and name and "/" not in name else None
        if target is None or not target.is_file() or target.parent != root:
            await send_all(send, 404, b"not found")
            return

        size = target.stat().st_size
        await send({"type": "http.response.start", "status": 200,
                    "headers": _response_headers(b"application/octet-stream", size)})
        with target.open("rb") as handle:
            while True:
                chunk = handle.read(FILE_CHUNK)
                if not chunk:
                    await send({"type": "http.response.body", "body": b""})
                    break
                await send({"type": "http.response.body", "body": chunk,
                            "more_body": True})
        log.info("served boot file %s (%d bytes) to %s", name, size, client_ip)

    return app


__all__ = ["Resolver", "Recorder", "build_config_text", "config_for_scope",
           "create_boot_asgi", "gui_initrd_path", "record_step",
           "GUI_INITRD_NAME", "BASE_HEADERS", "MEDIA_TYPE"]
