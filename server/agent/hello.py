"""בניית תשובת השרת — ממשק 3. הלב של צד השרת.

אותה פונקציה משרתת את שני הצרכנים:
- POST /api/v1/agent/hello — עם joining=True (hello הוא גם ההצטרפות)
- ה-resolver של תפריט ה-GRUB — עם joining=False (תפריט לא מצרף לסבב)

כל נתיב שלא מסתיים בהוראה מפורשת מסתיים ב-task:null + session:null,
שפירושם אצל הסוכן ואצל המחולל אותו דבר: דיסק מקומי.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import sqlite3
from collections.abc import Callable
from urllib.parse import urlsplit

from server.agent import agent_timing, bootguard, inventory, probe, registry, unknown_screen
from server.core import capabilities
from server.rounds import direct, room, room_drivers
from server.library import minfree
from server.system import timezone
from server.disks import (disk_sets, crcwarn, disk_failures, drawer_slots, shrink_records,
                          slot_names, smart_history)
from server.core.agent_version import server_version
from server.agent.boot_cancel import cancel_gui_tasks_on_boot
from server.db import disk_full, get_setting, journal, net_seen, note_disk_full
from server.agent import readbackhash
from server.disks import model_slots   # #2001
from server.library.images import ImageLibrary
from server.net.ports import server_name
from server.rounds.sessions import SessionStore
from server.core.tasks import active_task

log = logging.getLogger("imagesrv.hello")
_BOOT_SKIP_LOGGED: set[str] = set()

#: ‏#839: סוד המוניטור — 16 בייטים אקראיים שהסוכן הגריל באתחול, כ-32
#: ספרות hex קטנות. אורך קבוע כי תשובת ה-VNC Authentication היא 16 בייטים
#: בדיוק; קנוני (lowercase) כדי שהשוואה בשרת תהיה השוואת מחרוזות פשוטה.
MONITOR_SECRET_RE = re.compile(r"^[0-9a-f]{32}$")


def well_formed_monitor_secret(value: object) -> bool:
    return isinstance(value, str) and MONITOR_SECRET_RE.match(value) is not None


def well_formed_monitor_auth(value: object) -> bool:
    """‏#1077: הערך היחיד שמתקבל הוא ``hmac``. כל דבר אחר — כולל חסר —
    הוא סוכן ישן, והפרוקסי יסרב."""
    return value == "hmac"


def off_deploy_vlan(scope: dict | None, server_base: str | None) -> bool:
    """האם הבקשה התקבלה על כתובת מקומית שאינה כתובת וילן ההפצה (issue #42).

    המקור הוא ‎scope["server"] — ה-sockname של החיבור, שאותו uvicorn ממלא
    (אותו מקור כמו ב-#39). מכוון: *לא* כותרת Host, שהיא קלט של הלקוח
    ולכן תאפשר לתחנה להכריז על עצמה כ"בתוך הווילן".

    לעולם לא זורקת, ובכל ספק עונה False — כלומר "כמו היום". השוואה
    נעשית רק בין שתי כתובות IP: scope חסר, שם מארח (‏TestClient ממלא
    ("testserver", 80)), או ‎server_base בלי כתובת מספרית — כולם ספק.
    ערך ה-server חייב להיות זוג, כמו ב-#39; הפורט עצמו לא נשקל, שכן
    אותה מכונה על אותה כתובת היא אותו וילן.

    ‏#498: ‏``server_base`` עם שם מארח היה ספק **קבוע** — כל בקשה, תמיד
    "בפנים", וחובת הכניסה מחוץ לווילן (#42) מתה בשקט. לכן ``server.main``
    מסרב לעלות עם ‎--server-url שאינו כתובת מספרית; בשרת רץ ה-``False``
    של ספק נשאר רק למקרים החד-פעמיים (scope חסר, ‏TestClient).
    """
    try:
        host, _port = scope.get("server")
        configured = urlsplit(server_base).hostname
        return ipaddress.ip_address(host) != ipaddress.ip_address(configured)
    except Exception:  # noqa: BLE001 — כאן זו בדיוק הכוונה
        return False


def on_deploy_vlan(scope: dict | None, server_base: str | None) -> bool:
    """‏#1802: הבקשה התקבלה **על כתובת ההפצה** — המראה של `off_deploy_vlan`,
    עם הספק לכיוון ההפוך. שם ספק נספר "בפנים" (שומר שאינו סופר אינו
    שומר); כאן "בפנים" פותח את שרשרת האתחול למכונה לא רשומה, ולכן רק
    ראיה חיובית — שתי כתובות IP שוות — עונה True (עיקרון 1). ‏TestClient,
    ‏scope חסר ושרת שרשת ההפצה שלו לא הוגדרה (‏loopback) — כולם False.
    """
    try:
        host, _port = scope.get("server")
        configured = urlsplit(server_base).hostname
        return ipaddress.ip_address(host) == ipaddress.ip_address(configured)
    except Exception:  # noqa: BLE001 — ספק = כמו היום
        return False


#: ‏#976: הכותרת שבה הבדיקה העצמית של השרת (‏`health.default_hooks`) מסמנת
#: את בקשות `/boot/*` של עצמה. שם הכותרת כפי ש-ASGI מוסר אותו: bytes,
#: אותיות קטנות. ‏GRUB אינו שולח כותרות מותאמות, וגם אם ישלח — ראה
#: `self_probe`: מבחוץ הכותרת אינה שווה דבר.
PROBE_HEADER = b"x-imagesrv-probe"


#: ‏#1127: תקרת גודל ועומק לשדות ה-hello שנשמרים כ-JSON (מלאי, probe).
#: ‏64KB/שדה ועומק 8 — הסוכן שלנו שולח קילובייטים בודדים ועומק 3.
HELLO_FIELD_MAX_BYTES = 64 * 1024
HELLO_FIELD_MAX_DEPTH = 8
HELLO_CAPPED_FIELDS = ("inventory", "probe", "netprobe", "optical")


def _json_depth(value: object, depth: int = 1) -> int:
    if isinstance(value, dict):
        return max([depth] + [_json_depth(v, depth + 1) for v in value.values()])
    if isinstance(value, list):
        return max([depth] + [_json_depth(v, depth + 1) for v in value])
    return depth


def hello_payload_oversized(body: dict) -> str | None:
    """שם השדה הראשון שחורג מהתקרה (גודל JSON או עומק), או None."""
    for name in HELLO_CAPPED_FIELDS:
        value = body.get(name)
        if value is None:
            continue
        if _json_depth(value) > HELLO_FIELD_MAX_DEPTH:
            return name
        if len(json.dumps(value, ensure_ascii=False)) > HELLO_FIELD_MAX_BYTES:
            return name
    return None


def self_probe(scope: dict | None) -> bool:
    """האם בקשת `/boot/*` הזו היא הבדיקה העצמית של השרת (‏#976).

    שני תנאים, ושניהם חובה: הכותרת `X-ImageSrv-Probe: 1` נמצאת, **והפונה
    הוא השרת עצמו** — ‏`scope["client"]` הוא loopback, או שווה לכתובת
    המקומית שעליה החיבור התקבל (‏`scope["server"]`). השני נחוץ כי
    בייצור הבדיקה פונה ל-`http://<כתובת ההפצה>:8080`, וחיבור מקומי
    לכתובת כזו יוצא **ממנה** ולא מ-`127.0.0.1`. מכונה ברשת אינה יכולה
    להשלים לחיצת יד TCP מכתובת השרת, ולכן הכותרת אינה דלת אחורית:
    מבחוץ היא מתעלמת, והבקשה נרשמת כמו כל GRUB.

    לעולם לא זורקת, ובכל ספק עונה False — כלומר "כמו היום": הבקשה
    נרשמת. הכיוון הבטוח כאן הוא לרשום, כי בדיקה עצמית שנרשמה בטעות
    היא לכלוך על המסך, ואילו GRUB שלא נרשם הוא מכונה שנעלמה.
    """
    try:
        if dict(scope.get("headers") or ()).get(PROBE_HEADER) != b"1":
            return False
        client_addr = scope.get("client")           # ‏#1127: בלי לקוח אין ראיה
        if not client_addr:
            return False
        client = ipaddress.ip_address(client_addr[0])
        return client.is_loopback or client == ipaddress.ip_address(
            scope.get("server")[0])
    except Exception:  # noqa: BLE001 — כאן זו בדיוק הכוונה
        return False


def server_source(scope: dict | None) -> bool:
    """האם מקור בקשת האתחול הוא השרת עצמו.

    curl מקומי אל כתובת הממשק יוצא מאותה כתובת; אל localhost הוא
    יוצא מ-loopback. בשני המקרים זה אינו אתחול של תחנה, גם בלי
    כותרת ה-probe של מסך הבריאות.
    """
    try:
        client = ipaddress.ip_address(scope.get("client")[0])
        local = ipaddress.ip_address(scope.get("server")[0])
        return client.is_loopback or client == local
    except Exception:  # noqa: BLE001 — ספק נשאר בהתנהגות הישנה
        return False


def valid_machine_mac(mac: str) -> bool:
    """זהות MAC שיכולה לשייך לתחנה: לא אפסים/שידור/מולטיקאסט."""
    try:
        raw = bytes.fromhex(mac.replace(":", ""))
        return len(raw) == 6 and any(raw) and raw != b"\xff" * 6 and not raw[0] & 1
    except (TypeError, ValueError):
        return False


def _debug_boot_skip_once(kind: str, mac: str, client_ip: str | None) -> None:
    if kind in _BOOT_SKIP_LOGGED:
        return
    _BOOT_SKIP_LOGGED.add(kind)
    log.debug("boot menu for %s from %s not recorded: %s", mac, client_ip, kind)


def login_required(conn: sqlite3.Connection, has_open_session: bool,
                   off_vlan: bool = False) -> bool:
    """הכלל של אשף השחזור, במקום אחד.

    ‏hello מכריז עליו ב-`ui.require_login`, והשרת אוכף אותו שוב כשמשיכת
    יוניקאסט נפתחת — הצהרה שהסוכן מציית לה אינה אכיפה.
    """
    if off_vlan:
        # מחוץ לווילן ההפצה אין "הגישה הפיזית היא השמירה": כניסה תמיד,
        # גם בסבב פתוח וגם כשההגדרה כבויה (#42).
        return True
    if has_open_session:
        # בסבב פתוח אין סיסמה — זה מה שחוסך 29 הקלדות.
        return False
    return get_setting(conn, "recovery_require_login") == "true"


def _ui(conn: sqlite3.Connection, has_open_session: bool,
        off_vlan: bool = False, role: str | None = None) -> dict:
    ui = {"language": "he",
          "require_login": login_required(conn, has_open_session, off_vlan)}
    if role in ("cloner", "build"):   # #1967/#2067: the cloner and the build machine beep; the rest as it was
        ui["cloner_beep"] = get_setting(conn, "cloner_beep") != "false"
    return ui


def class_deploy_enabled(conn: sqlite3.Connection) -> bool:
    """‏#880: המתג של v1 "מהדורת שיכפול". ‏hello למחשב הבנייה מכריז
    עליו (הכרטיס "הפצה לכיתות" מוצג רק כשהוא דלוק), ו-`station.py` אוכף
    אותו שוב בפתיחת הסבב — תפריט שמציע מה שהשרת יסרב לו הוא תפריט
    שמשקר. כל ערך שאינו "true" — כולל הגדרה חסרה — הוא כבוי."""
    return get_setting(conn, "class_deploy_enabled") == "true"


def _unknown(conn: sqlite3.Connection, mac: str, client_ip: str | None,
             off_vlan: bool = False, record_journal: bool = True) -> dict:
    if record_journal:
        journal(conn, "unknown_mac", f"{mac} from {client_ip or '?'}")
    return {
        "schema": 1,
        "known": False,
        "name": None,
        "server_name": server_name(conn),
        "server_version": server_version(),
        "tz": timezone.posix_tz(conn),
        "role": "unknown",
        "group": None,
        "task": None,
        "session": None,
        "allowed_images": [],
        "ui": _ui(conn, has_open_session=False, off_vlan=off_vlan),
        # #1081: v1 hides classrooms. Missing = off (principle 1). v2 turns this on.
        "classrooms": capabilities.classrooms(),
        # v1 ships without the toolbox (Nadav, 19/09). Missing = off. v1.1 turns this on.
        "tools": capabilities.tools(),
    }


def _record_seen(conn, mac, ip, disks, monitor_secret, monitor_auth, prompt,
                 disk_probe, firmware, secure_boot, agent_version, local_clone,
                 optical, optical_reported, hw_inventory, hw_probe) -> None:
    net_seen(conn, mac, ip,
             disks_json=json.dumps(disks) if disks is not None else None,
             monitor_secret=monitor_secret, monitor_auth=monitor_auth,
             prompt=prompt, disk_probe=disk_probe,
             firmware=firmware, secure_boot=secure_boot, agent_version=agent_version,
             local_clone=local_clone,
             optical_json=json.dumps(optical) if optical is not None else None,
             update_optical=optical_reported)
    # ‏#720: המלאי החומרתי (schema 2) נשמר מגורסת — שורה חדשה רק כשהשתנה.
    # ‏None = הסוכן לא שלח (schema 1) או שלח פגום: הגרסה הקודמת נשארת.
    if hw_inventory is not None:
        inventory.record(conn, mac, hw_inventory)
    # ‏#1049 שלב ב' + #1048: בדיקת המכונה (כולל netprobe בתוך אותו JSON)
    # — None = לא נשלח/פגום, הגרסה הקודמת נשארת; רק תוכן יציב שהשתנה
    # פותח גרסה.
    if hw_probe is not None:
        probe.record(conn, mac, hw_probe)


def build_answer(
    conn: sqlite3.Connection,
    library: ImageLibrary,
    store: SessionStore,
    mac: str,
    *,
    disks: list[dict] | None = None,
    client_ip: str | None = None,
    joining: bool = False,
    reported_ip: str | None = None,
    off_vlan: bool = False,
    record_seen: bool = True,
    all_macs: list[str] | None = None,
    monitor_secret: str | None = None,
    monitor_auth: str | None = None,
    hw_inventory: dict | None = None,
    hw_probe: dict | None = None,
    multicast: dict | None = None,
    prompt: str | None = None,
    record_journal: bool = True,
    disk_probe: str | None = None,
    firmware: str | None = None,
    secure_boot: int | None = None,
    agent_version: str | None = None,
    local_clone: str | None = None,
    optical: list | None = None,
    optical_reported: bool = False,
    direct_finished: tuple[bool, list[str] | None] = (False, None),
) -> dict:
    # כל מגע של המכונה נרשם ברשימת ההתקנים, גם של מכונה שאינה רשומה
    # בטבלה — ככה מתגלה MAC לא מוכר, וזה חלק מעיקרון 1. ‏hello הוא POST
    # שבו המכונה מדווחת על עצמה, ולכן הוא תמיד רושם (ברירת המחדל).
    #
    # ‏#585: ‏`record_seen=False` בא ממסלול **התפריט** מחוץ לווילן ההפצה.
    # ‏`GET /boot/menu?mac=<MAC>` הוא בקשה בלי גוף ובלי עוגייה שכל פונה
    # שולח; מחוץ לווילן ההפצה אין ראיה שהפונה הוא המכונה, ולכן אסור שהוא
    # יקבע את ה-`ip`/`last_seen` שהשרת "מכיר" עבור אותו MAC — אחרת מכונה
    # כבויה נראית חיה, וכל בינוי זהות עתידי שיסתמך על הכתובת הזו מעגלי.
    # הרישום **אינו מופסק** — הוא רק מותנה בדיוק כמו ספירת האתחול (#536).
    if record_seen:
        try:
            _record_seen(conn, mac, reported_ip or client_ip, disks, monitor_secret,
                         monitor_auth, prompt, disk_probe, firmware, secure_boot,
                         agent_version, local_clone, optical, optical_reported,
                         hw_inventory, hw_probe)
        except sqlite3.OperationalError as exc:
            # ‏#1892: מחיצה מלאה אינה מפילה תשובה שאינה צריכה כתיבה — מכונה לא
            # רשומה מקבלת known:false (עיקרון 1). הכשל נרשם ואדום בבריאות.
            if not disk_full(exc):
                raise
            note_disk_full(f"hello net_seen {mac}")

    machine = registry.lookup(conn, mac, all_macs)
    if machine is None:
        return _unknown(conn, mac, client_ip, off_vlan, record_journal)
    # ‏#2001: "NVMe n" שנוסף ידנית בחלון מקבל את כתובת ה-PCI כשהכונן נראה.
    if (record_seen and disks and machine["role"] in ("cloner", "build")
            and model_slots.complete_ordinals(conn, mac, disks)):
        machine = registry.lookup(conn, mac, all_macs)

    # ‏#880: רק מחשב הבנייה מקבל את השדה — הוא היחיד עם התפריט. סוכן ישן
    # מתעלם משדה שאינו מכיר; סוכן חדש שלא קיבל אותו מתנהג ככבוי.
    extra = ({"class_deploy_enabled": class_deploy_enabled(conn)}
             if machine["role"] == "build" else {})
    answer: dict = {
        **extra,
        # #1081: edition flag. v1 = False (hardcoded). v2 turns this on.
        # Missing = off. Not an operator setting.
        "classrooms": capabilities.classrooms(),
        # v1 ships without the toolbox (Nadav, 19/09): the GUI's "כלים" button
        # follows this. Missing = off. v1.1 turns this on.
        "tools": capabilities.tools(),
        "schema": 1,
        "known": True,
        "name": machine["suffix"],
        "server_name": server_name(conn),
        "server_version": server_version(),
        "tz": timezone.posix_tz(conn),
        "role": machine["role"],
        "group": {
            "id": machine["group_id"],
            "label": machine["label"],
            "suffix": machine["suffix"],
        },
        "task": active_task(conn, mac),
        "session": None,
        # ‏#1863: לפי הגודל בלבד, כולל המינימום הפנוי (#1354) — כמו הקונסולה.
        "allowed_images": library.allowed_for_disks(disks, minfree.min_free_gb(conn)),
        "ui": _ui(conn, has_open_session=False, off_vlan=off_vlan,
                  role=machine["role"]),
        # ‏#874: הזיכרון בשרת במקום סימון על הדיסק — הרשומות הפתוחות שתואמות
        # לסידוריים שהמכונה שלחה או לחריץ באותה מכונה. הסוכן צובע מהן אדום
        # לפני הסבב; סוכן ישן מתעלם משדה שאינו מכיר (schema נשאר 1).
        "disk_failures": disk_failures.open_for(conn, mac, disks),
        # ‏#1859: מה שהשרת למד מאז הסבב הקודם של כל סידורי (183/188 שעלו, מאפיין קריטי
        # שעלה, חום בכתיבה) — [{"serial", "reason"}]. הסוכן הופך את המילה לשאלה כתומה.
        "smart_learned": smart_history.learned_for(conn, disks),
        # ‏#926: דיסק שכווץ בקליטה ולא הוחזר לגודלו — לפי הסידורי שהמכונה
        # שלחה. מחשב הבנייה מציע להחזיר (‏shrinkmem.sh); סוכן ישן מתעלם.
        "shrink_open": shrink_records.open_for(conn, disks),
        # ‏#704: השמות שהמפעיל נתן לחיבורים של המכונה הזו (‏{"sata:1": "דיסק 1"}).
        # מסך מחשב השיכפול מצייר אותם במקום "דיסק N"; ‏{} = אין שמות.
        "slot_names": slot_names.for_mac(conn, mac),
        # ‏#1366: סף ה-CRC המוחלט (הגדרה בקונסולה) — המסך הממתין של מחשב
        # השיכפול צובע ממנו כתום לפני שיש סבב. ‏0 = כבוי.
        "crc_warn_threshold": crcwarn.threshold(conn),
        # ‏#1354: המינימום הפנוי במחיצת המערכת אחרי שחזור (הגדרה בקונסולה).
        # בכל תשובה ולא רק בסבב: השיכפול המקומי (בלי שרת) משתמש באחרון שהגיע.
        "min_free_gb": minfree.min_free_gb(conn),
        # ‏#1864 (נדב 20/10): האימות אחרי הכתיבה — "sample"/"full". בכל תשובה, כמו
        # min_free_gb: השיכפול המקומי (בלי מניפסט מהשרת) קורא את האחרון שהגיע.
        "readback_mode": readbackhash.mode(conn),
        # ‏#1857 (מוקאפ 04): שם הסט והמדבקה של דיסקים ששלח המשכפל ({serial: {set, label, text}}); דיסק בלי סט חסר.
        "disk_labels": disk_sets.labels_for(conn, disks),
        # ‏#2056/#2059: זמני ההמתנה והכיבוי האוטומטי (הגדרות בקונסולה). בכל תשובה לרשומה,
        # ולכל תפקיד. הסוכן מחזיר את מה שהחיל ב-`applied_timing` (ראו interfaces.md).
        **agent_timing.hello_fields(conn),
    }
    if machine["role"] == "cloner" and machine["drawer_count"] is not None:
        answer["drawer_count"] = machine["drawer_count"]
    if machine["role"] in ("cloner", "build"):
        model_map = model_slots.for_mac(conn, mac)   # #2001: מכונה ← דגם ← ברירת מחדל
        answer["drawer_slots"] = drawer_slots.effective(
            machine["drawer_count"], machine["drawer_slots"],
            model_map["slots"] if model_map else None)

    # משימה גוברת על סבב: היא מופנית למכונה הזו, לא לקבוצה.
    if answer["task"] is not None:
        # ‏#715: מחשב הבנייה שהוא המקור מקבל גם את מצב הגל ופרמטרי
        # השידור (‏`task.direct`, ממשק 3). ‏`multicast` הוא של `SenderEngine`
        # (‏api.py); בלעדיו — ברירות המחדל של המנוע.
        if answer["task"]["type"] == direct.DIRECT_SEND:
            answer["task"]["direct"] = direct.task_block(
                conn, store, answer["task"], multicast, *direct_finished)   # #1952
        return answer

    if machine["role"] == "cloner":
        # מחשבי השיכפול דוגמים כל הזמן — ה-hello שלהם הוא גם הדופק
        # שמקדם את סבב החדר (סיום גל, פתיחת הגל הבא). ‏`pulse` ולא
        # `tick`: קידום הסבב לא מפיל את ה-hello, בדיוק כמו
        # `agent_loops.note` שיושב באותו מסלול — והכישלון נרשם ביומן
        # ולא נבלע (#177).
        room.pulse(conn, store, mac)
        # ‏#1954: דיסק שנבחר כשהמשכפל היה כבוי — אותו דיסק נכתב, אחר או חסר מדולג בגלוי.
        room.wake_check(conn, mac, disks)
        if not room.has_fresh_drawers(conn, mac):
            # המגירות של המכונה כבר נכתבו בסבב הזה ולא הוחלפו —
            # היא לא מצטרפת לגל, וממשיכה להמתין (wait_poll).
            return answer

    # ‏#1554: גל החדר הוא session על grp_CLONERS; משכפל מכל קבוצת משכפלים מצטרף אליו.
    session = store.active_for_group(
        room.CLONERS_GROUP if machine["role"] == "cloner" else machine["group_id"])
    if session is None:
        return answer

    if not store.in_roster(session, mac):
        # סבב עם בחירת מחשבים, והמכונה לא ברשימה: היא לא הוזמנה —
        # דיסק מקומי, כאילו אין סבב.
        return answer

    session = store.maybe_start(session)

    if store.member_done(session["id"], mac):
        # שחזר וסיים. הסבב לא מוצע שוב — אחרת לולאת שחזור אחרי כל אתחול.
        return answer

    if session["state"] == "open" and joining:
        if not store.record_hello(session, mac):
            # הגל עשוי היה לצאת אחרי הקריאה מ-`active_for_group`. מכונה
            # שאיחרה עולה מקומית, ואינה מצטרפת לגל שכבר עזב.
            return answer
        if machine["role"] == "cloner":
            # ‏#1703: הדופק שלמעלה רץ לפני ההצטרפות, ולא ספר את המגירות של
            # המכונה הזו. ה-hello שמשלים את הגל מוציא אותו כאן, באותה
            # תשובה — אחרת התשובה `open`, והסוכן לומד שהגל יצא רק מה-hello
            # הבא (22–23 ש' על ברזל במשכפל שהוער ב-WoL). אותו תנאי בדיוק
            # (`room.tick`): גל שעוד חסרות לו מגירות נשאר פתוח.
            room.pulse(conn, store, mac)
        session = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session["id"],)
        ).fetchone()
        if session is None or session["state"] not in ("open", "running"):
            return answer

    if session["state"] == "running" and not store.is_member(session["id"], mac):
        # מאחרים נכנסים לסבב הבא (סעיף 13.3) — עכשיו: דיסק מקומי.
        return answer

    if (session["state"] == "running" and joining and session["prefix"] == "ROOM"
            and room.rejoined_mid_stream(conn, session, mac)):
        # ‏#1728: משכפל שאותחל באמצע הזרם אינו חוזר לאמצע אותו זרם — הוא
        # ממתין לגל הבא (`room._revived` פותח אותו גם כשהגל לא כתב דבר).
        return answer

    answer["session"] = {
        "id": session["id"],
        "state": session["state"],
        "image_id": session["image_id"],
        "prefix": session["prefix"],
        "expected_clients": session["expected_clients"],
        "joined": store.joined_count(session["id"]),
        "starts_in_seconds": store.starts_in_seconds(session)
        if session["state"] == "open"
        else 0,
        # ‏#59: "auto" כשהסבב לא נפתח דרך הבחירה (pulls.py, station.py
        # שלא עודכנו) — סוכן ישן שלא מכיר את השדה מתעלם ממנו וממשיך
        # בבחירה האוטומטית בדיוק כמו קודם (עיקרון 1).
        "expand_partition": session["expand_partition"] or "auto",
        # ‏#1826: ה-portbase שהשולח של **השרת הזה** משדר בו — אותו ערך שמחשב
        # בנייה מקבל ב-`task.direct.multicast`. בלעדיו הסוכן הניח 9000, ו-
        # ‏`--sender-portbase` של השרת לא הגיע לאף מקלט.
        "portbase": (multicast or direct.default_multicast())["portbase"],
    }
    # ‏#695: לסבב חדר, המכונה מקבלת את **הפורטים שנבחרו לכתיבה עבורה בלבד**.
    # ‏null שמור בכוונה לסבב ישן (התנהגות "כל הדיסקים"); קבוצה ריקה = לא נבחרה.
    if machine["role"] == "cloner" and session["prefix"] == "ROOM":
        round_row = room.active_round(conn)
        # ‏#1790: בגל חוזר — בלי חריץ שהדיסק שבו כבר נכתב בסבב (לפי המלאי של ה-hello הזה).
        answer["session"]["target_ports"] = room.target_ports(round_row, mac, disks)
        # ‏#1269: החריצים שלה שנבחר להם דגם דרייברים. ‏null = הסבב בלי
        # דרייברים, והסוכן אינו שואל דבר — ההתנהגות מלפני ה-Issue.
        answer["session"]["driver_ports"] = room_drivers.ports_for(round_row, mac)
    answer["ui"] = _ui(conn, has_open_session=session["state"] == "open",
                       off_vlan=off_vlan, role=machine["role"])
    return answer


def make_resolver(conn: sqlite3.Connection, library: ImageLibrary,
                  store: SessionStore, server_base: str | None = None,
                  *, identity_ok: Callable[[str, str | None], bool] | None = None):
    """ה-Resolver ש-boot/http.py מצפה לו: (mac, client_ip) → ממשק 3.

    בלי הצטרפות ובלי דיסקים — תפריט אתחול רק שואל, לא מחייב.

    ‏`scope` הוא של הבקשה הנוכחית, והוא כאן בשביל שתי שאלות בלבד: על
    **איזו** מכתובות השרת היא התקבלה (‏#536), והאם היא הבדיקה העצמית של
    השרת (‏#976, ‏`self_probe`). מי שמעביר אותו הוא `server/app.py`,
    שקושר את ה-resolver מחדש בכל בקשה.
    """

    def resolve(mac: str, client_ip: str | None,
                scope: dict | None = None) -> dict:
        off_vlan = off_deploy_vlan(scope, server_base)
        probe = self_probe(scope)
        local_request = server_source(scope)
        invalid_mac = not valid_machine_mac(mac)
        do_not_record = off_vlan or probe or local_request or invalid_mac
        if not do_not_record:
            # ‏#1576: אתחול שנספר (אותו תנאי כמו net_seen והספירה של #75)
            # מבטל משימת גואי שלא התחילה -- **לפני** בניית התשובה, כדי שהתפריט
            # והסוכן שעולה לא יראו אותה.
            # #1629: a query MAC is not identity -- the cancellation waits for
            # the agent routes' lease check (`api.boot_identity_ok`, which
            # journals a refusal). None = no identity hook, as in identity_gate.
            if identity_ok is None or identity_ok(mac, client_ip):
                cancel_gui_tasks_on_boot(conn, store, mac)
        answer = build_answer(
            conn, library, store, mac, client_ip=client_ip, joining=False,
            # ‏#585: בקשת תפריט מחוץ לווילן ההפצה אינה כותבת `net_seen` —
            # אותה הכרעה בדיוק כמו הספירה למטה, ומאותו טעם (‏MAC בשאילתה
            # אינו זהות). על וילן ההפצה התפריט כן רושם, כמו היום.
            # ‏#976: וגם לא הבדיקה העצמית של השרת — היא אינה המכונה.
            record_seen=not do_not_record,
            record_journal=not (probe or local_request or invalid_mac),
        )
        if local_request or invalid_mac:
            reason = "server source" if local_request else "invalid MAC"
            _debug_boot_skip_once(reason, mac, client_ip)
            return answer
        if probe:
            # ‏#976: הבדיקה העצמית מקבלת את התפריט האמיתי — זו הראיה
            # החיובית שהיא באה בשבילו — אבל אינה אתחול: לא `net_seen`,
            # ולא ספירה בתקציב הלולאה (‏#75). ארבע כניסות לקונסולה במהלך
            # סבב פתוח שלחו את המכונה הרשומה הראשונה לדיסק המקומי.
            log.info("boot menu for %s is the server's own probe — served, "
                     "not recorded (#976)", mac)
            return answer
        if off_vlan:
            # ‏#536: ‏MAC מהשאילתה אינו זהות. ‏`ATTEMPT_LIMIT` הוא 3,
            # ולכן ארבע בקשות `GET /boot/menu?mac=<תחנה>` — בלי גוף,
            # בלי עוגייה, בלי להיות המכונה — גמרו את תקציב האתחולים של
            # תחנת כיתה חיה, והשומר שנועד להציל אותה מלולאה הוא זה
            # שהוציא אותה מהסבב. מה שכן ניתן לאמת הוא על איזו כתובת
            # מקומית הבקשה התקבלה, וזו בדיוק העמדה של #42: מחוץ לווילן
            # ההפצה השרת אינו מגיש את שרשרת האתחול, ולכן בקשה כזו אינה
            # ראיה שהוא שלח את המכונה הזו לסוכן.
            #
            # התשובה עצמה אינה משתנה — התראה ולא שער (‏#137), והתחנה
            # שמושכת תפריט מרשת אחרת (תרחיש 3, ‏#39) ממשיכה לקבל את
            # הסבב שלה. רק **הספירה** אינה מתרחשת.
            #
            # ‏`off_deploy_vlan` עונה False בכל ספק, ולכן ספק נספר כמו
            # היום. זה **אינו** "לא הצלחנו לבדוק ולכן בסדר": כאן הכיוון
            # הבטוח הוא לספור — שומר שאינו סופר אינו שומר (‏#75).
            log.warning("boot menu for %s (from %s) did not arrive on the "
                        "deployment vlan — served, not counted", mac, client_ip)
            return answer
        if answer.get("known") is not True:
            # ‏#1802: מכונה לא רשומה בווילן ההפצה — המסך פעם אחת בחלון, ואז
            # הדיסק (`unknown_screen`). ‏`bootguard` אינו רלוונטי: אין לה
            # סבב ואין לה משימה, ולכן אין לה הקשר לספור בו.
            if on_deploy_vlan(scope, server_base):
                return unknown_screen.offer(conn, mac, answer)
            return answer
        # רק כאן, ולא ב-hello: בקשת התפריט היא האתחול, והיא גם הרגע
        # היחיד שבו אפשר להבטיח לאן ילך האתחול הבא (‏#75).
        return bootguard.guard(conn, mac, answer)

    return resolve
