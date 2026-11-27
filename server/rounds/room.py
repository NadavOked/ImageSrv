"""סבב חדר השיכפולים — הפצה מצטברת על פני גלים (אפיון סעיף 29).

הסבב חי בשרת ומצהיר יעד סך-כוננים. כל גל הוא session רגיל על קבוצת
השיכפול — אותו מנוע שידור, אותם דיווחי התקדמות. מה שמייחד את החדר:

- המוכנות נמדדת בכוננים (מגירות), לא במחשבים. גל יוצא כשהמגירות
  הטריות מכסות את היתרה, או בלחיצת "התחל עכשיו". בלי טיימר —
  בחדר עומד אדם ליד המכונות.
- מגירה מזוהה ב-serial של הכונן: כונן שכבר נכתב בסבב לא נספר שוב,
  ומכונה שהמגירות שלה לא הוחלפו לא מצטרפת לגל הבא.
- כשגל מסתיים והיעד לא הושג, הגל הבא נפתח אוטומטית — "כיבוי,
  החלפת מגירות, הדלקה — סבב שני מתחיל אוטומטית".

הערה: אין כאן `from .api import ServerContext` — זה היה סוגר מעגל
(api → hello → room). ctx מגיע כפרמטר ומשמש כפי שהוא.
"""

from __future__ import annotations

import json
import logging
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import overload

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from server.console import auth
from server.core import unreadable
from server.rounds import drawer_cancel, drive_rates, prodline, room_drivers, room_names, room_precheck, round_cloners
from server.rounds import room_stream   # #3173/#3978: the stream rate and the network card, from the chunk engine
from server.library import drivers, minfree
from server.agent import registry, reports
from server.disks import disk_events, disk_failures, disk_rule, disk_sets, drawer_slots, slot_names
from server.storage import locations as storage_locations
from server.disks import model_slots   # #2001
from server.db import _settle, _write_lock, journal, now_iso, SqlConnection, update_one, writing
from server.system import clockstep
from server.net import pxe_log
from server.agent import readbackhash
from server.learn import eta as learn_eta, models as learn_models, ports as learn_ports, tuning as learn_tuning
from server.learn import netloss as learn_netloss     # #3164
from server.storage import history as storage_history
from server.agent import held# #2031
from server.agent import timing as agent_timing   # #2056
from server.brain import gates, linkwarn, roundend, timeforecast   # #3277, #3276 (K3 link, K10), #3469 (K16); gates: #3064 שערים לפני פתיחה, בצל
from server.brain import faultwho, line as brain_line, slotfill   # B24/B38 (פס ייצור), B33, B50 -- בצל
from server.brain import cpucap, services as brain_services   # K12 (מעבד), K13 (שירותים) לפני פתיחת סבב או גל -- בצל
from server.brain import opengate   # #3662 (B70): הפסק האחד לפני פתיחת סבב; בצל, ו-`brain_round_gate=enforce` כבוי
from server.brain import silence   # #3657: K-LOST, K-STALL -- המוח מחליט, והשרת מבצע
from server.brain import diskcolor   # #3666 (K3/DISK_COLOR): המוח מסווג דיסק -- צבע, מקור/יעד וסיבה; הטבלה האחת ב-disk-rule.tsv
from server.brain import scheduled_cast
from server.library.images import required_bytes, restore_refusal
from server.library.imagefit import validate_expand_choice
from server.rounds.sessions import SessionError, SessionStore, SessionSuperseded
#: #2594: חישובי המצב הטהורים של הסבב יושבים ב-`room_state` ומיובאים לכאן בחזרה,
#: כך ש-`room.<שם>` ממשיך לעבוד לכל קורא קיים (גם בטסטים).
from server.rounds.room_state import (  # noqa: F401 -- re-export (#2594)
    _cancelled, _closed, _disks, _expected, _fresh_disks, _no_disk_count, _port, active_rounds, round_of_mac, round_of_wave,
    _red, _remaining, _roster, _selected_ports, _skipped, _unconfirmed, _written,
    active_round, fresh_serials, has_fresh_drawers, ready_drives, red_drives,
    shown_disks, target_disks, target_ports, visible_round, wave_drives,
)
from server.core import machine_names
from server.core.tasks import request_origin
from server.brain import precheck as brain_precheck   # #3662 (B72): אזהרות לפני התחל -- המוח מכריע, בצל

#: #1732: מה שהמפעיל קורא על דיסק אדום — אותו משפט כמו בסוכן (smart.sh). #2848: מהכלל האחד.
SMART_RED_HE = disk_rule.REASON_HE["smart_fail"]

log = logging.getLogger("imagesrv.room")

CLONERS_GROUP = registry.CLONERS_GROUP
#: #1554: חברי החדר — כל קבוצה בתפקיד cloner (ראו registry.ROOM_GROUPS).
_IN_ROOM = f"group_id IN {registry.ROOM_GROUPS}"
#: #3898 (שלב 1): האם מותר סבב שני בזמן שסבב אחר פעיל. **כבוי, במכוון, עד שלב 3**: חריץ השידור (`one_active_broadcast`) והמנועים
#: (`ctx.sender`, `ctx.chunk`) עדיין יחידים, וגל שני שיוצא בזמן שהראשון משדר היה נכשל בשמו ("המנוע עדיין משדר").
#: כבוי = ההתנהגות כמו לפני #3898, בית-בבית. הבלעדיות של המשכפל (`round_cloners`) נאכפת בשני המצבים.
PARALLEL_ROUNDS = False
#: מי רשאי להפעיל את חדר השיכפולים. רשימת-היתר מפורשת, ולא "מחובר".
#:
#: אותה הכרעה כמו ``station.ROUND_OPENER_ROLES`` (#94), ובאותו נימוק:
#: היום זה כל התפקידים שקיימים ולכן זה לא משנה התנהגות, אבל הקוד שלף
#: את ``admin_only`` ומעולם לא בדק דבר — ותפקיד שלישי (צופה, מבקר,
#: חשבון ניטור) היה מקבל ביום היוולדו את ``/wake`` ו-``/start``, שהם
#: הפעולה שדורסת כל מגירה מחוברת בחדר. "לא ידענו מה התפקיד" הוא סירוב.
#:
#: ``GET`` נשאר פתוח לכל מחובר — קריאת מצב אינה הרסנית.
ROOM_OPERATOR_ROLES = ("admin", "deploy")
#: מכונה שדיברה עם השרת בטווח הזה נחשבת ערה — הסוכן דוגם כל ~2 שניות.
AWAKE_SECONDS = 30
#: #450, #3657: חבר גל שלא דיווח **התקדמות** (לא hello) מוכרז אבוד. הסף (`lost_wait_seconds`, הגדרה, ברירת מחדל 180) וההשוואה
#: הם החלטה של המוח (`brain.silence.judge_lost`, K-LOST); `_mark_lost` אוסף חותמות ומבצע.
#: #1721, #3657: WoL חוזר לגל פתוח (כל כמה זמן, כמה פעמים, ומתי הסבב נכשל בשם המחשבים שלא חזרו) הוא החלטה של המוח --
#: `brain.silence.judge_rewake`, כלל K-REWAKE. `_rewake` אוסף עובדות ומבצע: שולח, מכשיל או מכייל.

#: 17/10: חלון קצב שלא התחדש כך וכך שניות — הדיווחים פסקו, והקצב שבו ישן.
#: שלושה חלונות של 10 ש' (`reports.RATE_WINDOW_S`), עם דיווח כל ~2 ש'.
RATE_STALE_S = 30

#: #3638 (J3b, 18/11): כמה שניות מהבייט הראשון של מגירה עד שה-`eta_s` שלה נמדד. הקצב שממנו נגזר ה-ETA הוא הממוצע
#: מאז הבייט הראשון, והמנוע מאיץ בהתחלה, ולכן בדקה הראשונה הוא נמוך מהקצב שיגיע והחיזוי גבוה מדי. בגל אחד (J3b) אחרי
#: 60 ש' השגיאה עוד הגיעה ל-+35%..+86% (לפי מיקום הבייט הראשון בתוך חלון הדגימה), ואחרי 90 ש' ל-+14%..+46% ולא יותר;
#: הנתונים והטווחים ב-`tests/test_room_eta_3638.py`. עד אז `eta_s` הוא `None`, והקונסולה נופלת להערכה לפי היסטוריה.
ETA_MIN_SAMPLE_S = 90


def fail_wave(conn: SqlConnection, session_id: str, reason: str,
              *, commit: bool = True) -> bool:
    """הופך כשל שולח לסופי בחדר; רק מפעיל יכול לפתוח אותו מחדש.

    `commit=False` — הקורא כבר בתוך טרנזאקציה (`sessions.fail` מריץ אותה
    כ-`related` באותו מעבר אטומי); אחרת הכתיבה עוברת ב-`update_one`, שגם
    משחרר נעילה כשאף שורה לא תאמה (#54), ומסתיימת ב-`writing` (#525).
    """
    sql = ("UPDATE room_rounds SET state = 'failed', failed_reason = ?, closed_at = ?"
           " WHERE wave_session_id = ? AND state = 'active'")
    args = (reason, now_iso(), session_id)
    if not commit:
        return conn.execute(sql, args).rowcount == 1
    with writing(conn):
        return update_one(conn, sql, args)


def wake_check(conn: SqlConnection, mac: str, disks) -> None:
    """#1954 (נדב 22/10): משכפל שנבחרו לו דיסקים כשהיה כבוי עלה — ה-hello שלו
    (`disks`, המלאי של עכשיו) מכריע פעם אחת לכל חריץ שנקשר ל-serial: אותו דיסק —
    הקשירה יורדת והחריץ נכתב כרגיל (גם בגלים הבאים, עם מגירות שהוחלפו); דיסק אחר
    או חריץ ריק — **החריץ יוצא מהבחירה של הסבב ולא נכתב**, היעד יורד באחד,
    `room_drawer_wake_mismatch` ביומן, והקונסולה אומרת אותו (`wake_skipped`).
    אסור לכתוב לדיסק שלא נבחר. hello בלי מלאי, או חריץ שנראה ריק כשבמלאי יש
    דיסק בלי חריץ (סוכן ישן, כונן לא ממופה) — אין הכרעה: החריץ נשאר קשור ואינו
    נכתב (`_unconfirmed`)."""
    round_row = active_round(conn)
    if (round_row is None or round_row["target_slots_json"] is None
            or not isinstance(disks, list)):
        return
    raw = round_row["target_slots_json"]
    slots = json.loads(raw)
    item = next((x for x in slots if x.get("mac") == mac and isinstance(x.get("expect"), dict)), None)
    if item is None:
        return
    disks = [d for d in disks if isinstance(d, dict)]
    found = {d["port"]: d.get("serial") for d in disks
             if isinstance(d.get("port"), int) and not isinstance(d.get("port"), bool)}
    # דיסק בלי חריץ (סוכן ישן, כונן לא ממופה): חריץ שנראה ריק אינו ראיה שהוא ריק — לא מכריעים עליו.
    unported = len(found) < len(disks)
    expect = item["expect"]
    keep = {p: s for p, s in expect.items() if found.get(int(p)) is None and unported}
    gone = [(int(p), s, found.get(int(p))) for p, s in sorted(expect.items(), key=lambda kv: int(kv[0]))
            if p not in keep and found.get(int(p)) != s]
    if keep == expect:
        return                                   # nothing decided: no slot of it is known
    if keep:
        item["expect"] = keep
    else:
        item.pop("expect")
    if gone:
        item["ports"] = [p for p in item["ports"] if p not in {g[0] for g in gone}]
        item["wake_skipped"] = [{"port": p, "serial": s, "found": f} for p, s, f in gone]
    # התביעה: רק מי שראה את אותה בחירה מעדכן (שני hello במקביל — אחד מכריע).
    # #54: הכתיבה בתוך `_write_lock` + `writing` — כתיבה שנכשלה אינה משאירה נעילה יתומה.
    with _write_lock, writing(conn):
        claimed = update_one(
            conn,
            "UPDATE room_rounds SET target_slots_json = ?, target_drives = MAX(0, target_drives - ?)"
            " WHERE id = ? AND state = 'active' AND target_slots_json = ?",
            (json.dumps(slots), len(gone), round_row["id"], raw),
        )
    if not claimed:
        return
    row = conn.execute("SELECT suffix FROM machines WHERE mac = ?", (mac,)).fetchone()
    for port, serial, now in gone:
        journal(conn, "room_drawer_wake_mismatch",
                f'{round_row["id"]} {_room_label(row["suffix"]) if row else mac} port={port}'
                f" chosen={serial} found={now or 'missing'}", machine=mac, round_id=round_row["id"])


def drawer_list(conn: SqlConnection, mac: str, written: set[str],
                member: sqlite3.Row | None = None,
                selected_ports: set[int] | None = None,
                started_at: str | None = None,
                skipped: set[str] | None = None,
                cancelled: set[str] | None = None) -> list[dict]:
    """המגירות של המכונה כפי שהטכנאי רואה אותן: חריץ, התקן ומצב (#27).

    `port` הוא ה-ataN שהסוכן דיווח בממשק 2 — החריץ הפיזי, לא סדר הגילוי
    של הקרנל. סוכן ישן, VM או בקר לא-ATA לא מדווחים אותו, והוא יוצא `null`:
    הקונסולה נופלת חזרה לתצוגה לפי שם ההתקן, בלי להיכשל (עיקרון 1).

    `started_at` (#410) — מתי הגל יצא לדרך; ממנו נגזרים `rate_bps` ו-`eta_s`
    לכל מגירה. בלעדיו שניהם `None` — "לא נמדד", לא 0.

    `skipped` (#1760) — ה-`skipped_serials` של הסבב: מגירה שדולגה כאדומה סגורה,
    כמו בחדר עצמו (`_closed`). `fresh: false` עם `skipped_red: true` — "דולגה, לא
    נכתבה"; `fresh: false` בלעדיו נשאר "נכתבה".

    `cancelled` (#1936) — ה-`cancelled_serials` של הסבב: מגירה שמפעיל ביטל סגורה
    באותו אופן — `fresh: false` עם `cancelled_round: true` = "בוטלה, לא נכתבה".
    """
    skipped = skipped or set()
    cancelled = cancelled or set()
    targets = {}
    cancels: dict[str, dict] = {}
    if member is not None:
        targets = {t.get("dev"): t
                   for t in json.loads(member["targets_json"] or "[]")
                   if isinstance(t, dict)}
        if "session_id" in member.keys():               # #1386 (a bare member: no wave to ask)
            cancels = drawer_cancel.by_dev(conn, member["session_id"], mac)
    drawers = []
    disks = _disks(conn, mac)
    failures = disk_rule.machine_failures(conn, mac, disks)   # #2848: "נכשל בשיכפול הקודם" -- צהוב
    set_labels = disk_sets.labels_for(conn, disks)   # #1857: שאילתה אחת לכל המגירות
    round_smart = disk_events.last_by_serial(   # #2706: מה שהסבב האחרון קרא -- שאילתה אחת
        conn, [d["serial"] for d in disks if isinstance(d, dict) and isinstance(d.get("serial"), str) and d["serial"]])
    for disk in disks:
        if not isinstance(disk, dict):
            continue
        port = disk.get("port")
        target = targets.get(disk.get("dev")) or {}
        drawers.append({
            "dev": disk.get("dev"),
            "port": port if isinstance(port, int) and not isinstance(port, bool)
            else None,
            # #1495: כתובת ה-PCI של בקר NVMe (hello, #1485) — בלעדיה `drawer` של
            # כונן NVMe היה תמיד None, גם כשהוא ממופה כדיסק N.
            "pci": disk.get("pci") if isinstance(disk.get("pci"), str) else None,
            # #553: הסריאל והדגם הם מה שמזהה כונן **ביד**. `port` אומר
            # לאיזו מגירה ללכת; הסריאל אומר איזה כונן זה כשמחזיקים
            # אותו — וכשאין `port` (NVMe, VM, בקר לא-ATA) הוא כל מה
            # שנשאר. שורת כשל בלי אחד מהם שולחת אדם לחפש.
            "serial": disk.get("serial") or None,
            "model": disk.get("model") or None,
            # #1857 (מוקאפ 04): שם הסט והמדבקה ("קוריאים · xx-7") לפי הסידורי. None = בלי סט.
            "set_text": disk_sets.text_of(set_labels, disk.get("serial")),
            # #105: שלושה מצבים, לא שניים. `None` = הכונן לא דיווח serial,
            # ולכן הסבב (שסופר לפי serial) אינו יכול לדעת אם הוא נכתב —
            # "לא ניתן לזהות את הכונן", לא `False` ("נכתבה") ולא `True`.
            "fresh": (disk["serial"] not in written and disk["serial"] not in skipped
                      and disk["serial"] not in cancelled)
            if disk.get("serial") else None,
            "skipped_red": bool(disk.get("serial")) and disk["serial"] in skipped,   # #1760
            "cancelled_round": bool(disk.get("serial")) and disk["serial"] in cancelled,   # #1936
            # #695: האם הפורט הזה נבחר כיעד בסבב (לצביעת הגריד/בחירה).
            "selected": (selected_ports is not None
                         and isinstance(port, int) and not isinstance(port, bool)
                         and port in selected_ports),
            "state": target.get("state"),
            # #3209: נשלפה והסבב מחכה לה (`state` נשאר `failed`) -- גואי מחשב הבנייה בהפצה ישירה מציג "נשלף" באפור.
            "pulled": _pulled(target, member),
            "error": target.get("error"),
            # #2987: המשפט למפעיל שהסוכן עשה מ-`error` (agent/lib/core/opsay.sh) -- הכרטיס אומר אותו;
            # `error` נשאר האבחון המלא (הריחוף, היומן, ata_cause). None = סוכן ישן: הקונסולה מתרגמת את `error`.
            "error_short": _text(target.get("error_short")),
            # #3167: הערה על מגירה שהושלמה (שם שנכתב לאימג' שלא כובה נקי) -- לא `error`: ירוק, שורה אפורה.
            "note": _text(target.get("note")) if target.get("state") == "done" else None,
            # #2988: מגירה שעומדת באמצע הכתיבה -- `waiting_disk` / `waiting_net`, כמה שניות, ותקרת הסוכן.
            # None = זזה, או סוכן ישן. תצוגה בלבד: ההכרעה נשארת אצל התקרות שהיו.
            **_stall(target),
            # #3064 (R5, מוקאפ production-line-mockup-1114): מגירה שחזרה ועוד משלימה -- "מצטרף ומשלים", חסרים N. None = לא משלימה.
            "rejoin": _rejoin(target),
            # #1386: "בטל דיסק זה" — מי ביקש ומתי (`text_he`), ו-`done` = הסוכן דיווח
            # `cancelled`. None = לא בוטל. בקשה בלי `done` על מגירה שכותבת = "מבטל…".
            "cancel": cancels.get(disk.get("dev")),
            # #1269: תוצאת הנחת הדרייברים על המגירה הזו (מנורמלת ב-ingest).
            # None = השלב לא רץ עליה — לא `no_match`.
            "drivers": target.get("drivers"),
            # #1883: כמה זמן ארך כל שלב (`[{stage, seconds}]`, `restore_stages.clean`). None = לא נמדד.
            "stages": target.get("stages"),
            # #1805: הקריאה החוזרת מהדיסק אחרי הכתיבה — `verified` / `sampled` (#1864:
            # "נבדק במדגם", לא "אומת") / `unverified`
            # (לאימג' אין גיבוב: "לא אומת", לעולם לא "אומת"). None = לא דווח
            # (סוכן ישן, או שהשלב עוד לא רץ). כשל של הקריאה הוא `failed` + `error`.
            "readback": target.get("readback")
            if target.get("readback") in ("verified", "sampled", "unverified") else None,
            "readback_reason": target.get("readback_reason")
            if isinstance(target.get("readback_reason"), str) else None,
            # #1907 (נדב 22/10): `unverified` מסיבה משלו — רשימת הבדיקה לא הורדה מהשרת
            # אחרי כל הניסיונות. None = כל סיבה אחרת (או סוכן ישן).
            "readback_why": "list_not_fetched"
            if target.get("readback") == "unverified"
            and target.get("readback_why") == "list_not_fetched" else None,
            # #1366: MB/s של בדיקת הכתיבה לפני הזרם (מדוח הסוכן). None = לא נמדד —
            # לא 0 (עיקרון 5ב). דיסק איטי כבר נכשל בשמו (`error`): זה המספר עצמו.
            "write_mbps": _write_mbps(target),
            # #1845: מול הדגם — המדידה של הגל הזה, ובלעדיה האחרונה של הדיסק, מול חציון
            # הדיסקים האחרים מאותו דגם. `slow: null` = אין מדידה או אין בסיס — רק הסף המוחלט.
            "model_speed": learn_models.judge(conn, disk.get("model") or None,
                                              disk.get("serial") or None, _write_mbps(target)),
            # #552: המפעיל ראה "0 of 5 written" במשך 75 דקות. המונה
            # הזה סופר מגירות ש**סיימו**, ולכן הוא 0 גם כשהכול רץ מצוין
            # וגם כשהכול תקוע — ואי-אפשר להבחין. הבייטים הם ההבחנה.
            "bytes_written": _int(target.get("bytes_written"), "bytes_written"),
            "bytes_total": _int(target.get("bytes_total"), "bytes_total"),
            "stalled_s": _stalled_s(target.get("moved_at")),
            # #410: קצב ו-ETA — מהבייטים שדווחו, לא מהערכה. None = לא נמדד.
            **_pace(target, started_at),
            # #872: בכמה עלה מונה ה-CRC (199) בסבב הזה, מדוח הסוכן. None =
            # לא נמדד — לא 0 (עיקרון 5). >0 מוצג "CRC +N · לבדוק כבל", ואינו
            # צובע את הדיסק.
            "crc_delta": target["crc_delta"]
            if isinstance(target.get("crc_delta"), int)
            and not isinstance(target.get("crc_delta"), bool) else None,
            # #652: בריאות SMART כפי שדווחה ב-hello (ממטמון הסוכן), כדי
            # שהמפעיל יראה "מגירה N: SMART אזהרה" **לפני** שהוא משגר סבב.
            # `unchecked` = לא-נבדק, לא כשל (עיקרון 5).
            "smart": disk.get("smart") or "unchecked",
            # #2706: הפסק של הסבב האחרון שקרא את הסידורי (`disk_events`) -- לתצוגה. `smart` שלמעלה
            # נשאר של ה-hello: השער האדום (`open`) וה-precheck קוראים אותו, ופסק מאתחול קודם אינו ראיה על עכשיו.
            "smart_round": round_smart.get(disk.get("serial")) if isinstance(disk.get("serial"), str) else None,
            # #1366: למה `fail` (health_failed / pending_sectors / uncorrectable) —
            # "ה-BIOS יעצור ב-F1". None = סוכן ישן או דיסק ירוק.
            "smart_reason": disk.get("smart_reason") or None,
            # #1798: סימן "כתיבה לא הושלמה" שעל הדיסק עצמו (hello, writemark.sh) —
            # `incomplete` / `clean` / `unchecked`. None = סוכן ישן — לא "תקין".
            "write_mark": disk.get("write_mark")
            if disk.get("write_mark") in ("incomplete", "clean", "unchecked") else None,
            # #2848 (נדב 11/11): בעיית SATA מה-hello (`link_reset` / `drawer_gone`, hwissues.sh) — אדום,
            # לא יעד, כן מקור. None = סוכן ישן או לא נקרא — לא "תקין"; [] = נקרא, אין.
            "sata_issues": _sata_issues(disk.get("sata_issues")),
            # #710: הגודל שהסוכן דיווח (sysinfo.sh), כדי שהגריד יראה קיבולת
            # אמיתית לכל דיסק ולא 0. חסר => 0, לא נחסם (עיקרון 5).
            "size_bytes": _int(disk.get("size_bytes"), "size_bytes"),
            # #432: קישור ה-SATA (מהירות/מקסימום/מצב) ועליית מונה ה-CRC מאז
            # הסבב הקודם של הדיסק. None/`unknown` = לא נקרא — לא "תקין".
            **room_precheck.drawer_fields(conn, disk, mac),
            # #2848 (נדב 11/11): "איטי" אדום **רק בסבב שבו נמדד** — בדיקת הכתיבה של היעד הזה
            # בגל הזה (`targets[].write_preflight`). מדידה מסבב קודם (`last_write_check`) אינה
            # צובעת: הסבב הבא מודד מחדש, ודיסק שאינו איטי עוד חוזר לצבע של ה-SMART שלו.
            "write_slow": _write_slow(target),
        })
        # #2848: הכלל האחד — הצבע, מקור/יעד והסיבה, כמו שהשרת אוכף (`_validate_target_slots`).
        # #3666: המוח מסווג (K3/DISK_COLOR) ונרשם; הקונסולה והגואי מציגים את מה שהוא אמר.
        drawers[-1]["rule"] = diskcolor.decide(conn, drawers[-1], failures, write_slow=drawers[-1]["write_slow"],
                                               point="drawer_list", mac=mac)
    return drawers


def _write_slow(target: dict) -> bool:
    pre = target.get("write_preflight")
    return isinstance(pre, dict) and pre.get("state") in ("slow", "failed")


SATA_ISSUES = ("link_reset", "drawer_gone")


def _sata_issues(value) -> list[str] | None:
    """#2848: רק המילים המוכרות; מה שאינו רשימה = לא נקרא (None), לא "אין"."""
    return [w for w in value if w in SATA_ISSUES] if isinstance(value, list) else None


def _write_mbps(target: dict) -> int | None:
    pre = target.get("write_preflight")
    mbps = pre.get("mbps") if isinstance(pre, dict) else None
    return mbps if isinstance(mbps, int) and not isinstance(mbps, bool) and mbps > 0 else None


_BAD_INT_SEEN: set[tuple[str, str]] = set()


def _int(value, field: str = "") -> int:
    """A reported number. Missing (None) is 0 as before; a value that is there but is not a
    number is still 0 for the screen, but it is no longer silent (#3129, A-DB-08): one
    warning per field and value, so a broken agent report shows in the server log."""
    try:
        return int(value)
    except (TypeError, ValueError):
        if value is not None and len(_BAD_INT_SEEN) < 256 and (field, repr(value)) not in _BAD_INT_SEEN:
            _BAD_INT_SEEN.add((field, repr(value)))
            log.warning("report field %s is not a number, counted as 0: %r", field or "?", value)
        return 0


def _pace(target: dict, started_at: str | None) -> dict:
    """#410: `rate_bps` ו-`eta_s` של מגירה, מהבייטים שדווחו.

    17/10 (ברזל): הקצב הוא **הקצב העדכני** — `recent_bps`, הבייטים בחלון
    האחרון של 10 שניות לפחות (`reports._stamp_window`). עד כאן הוא היה ממוצע
    מאז שהגל יצא, והמכנה כלל את ההמתנה לפני הבייט הראשון (38 ש' ב-
    `ses_fbd8f9ca`, דקות כששאלת SMART פתוחה) ובין המחיצות — מספר שאינו
    "עכשיו". ETA = מה שנשאר חלקי הקצב, רק כשיש סך ורק כשהמגירה כותבת.

    ⚠️ `None` ≠ `0` (עיקרון 5): גל שאינו רץ, חלון שטרם נסגר, חלון שלא זז
    (העצירה נראית ב-`stalled_s`), או חלון שהתיישן — הדיווחים פסקו יותר מ-
    `RATE_STALE_S` — כולם "לא נמדד", ומסך שמצייר אותם כאפס אומר "עצר".
    """
    none = {"rate_bps": None, "eta_s": None}
    if target.get("state") != "writing" or _iso_or_none(started_at) is None:
        return none
    rate, age = target.get("recent_bps"), _stalled_s(target.get("rate_at"))
    if (not isinstance(rate, int) or isinstance(rate, bool) or rate <= 0
            or age is None or age > RATE_STALE_S):
        return none
    written = _int(target.get("bytes_written"), "bytes_written")
    total = _int(target.get("bytes_total"), "bytes_total")
    avg = _average_bps(target, written)
    eta = int((total - written) / avg) if total > written and avg else None
    return {"rate_bps": int(rate), "eta_s": eta}


def _average_bps(target: dict, written: int) -> float | None:
    """#3638: הקצב שממנו נגזר `eta_s` -- הבייטים שנכתבו חלקי הזמן מ-`first_moved_at` (#447, חותמת שרת: הבייט
    הראשון) עד עכשיו, ורק אחרי `ETA_MIN_SAMPLE_S`. `rate_bps` עצמו נשאר קצב החלון (10 ש'): הוא מה שהמגירה עושה
    עכשיו, וזה מה שהמסך מציג כקצב. ב-J3b `eta_s` שנגזר מחלון כזה קפץ בין 88 ל-184 ש' לאותו גל, כי קצב חלון של
    מגירה בריאה נע בין 25 ל-90MB/s וה-`max` של הגל בוחר תמיד את הנמוך.

    ⚠️ `None` = "לא נמדד", לא 0 (עיקרון 5): אין `first_moved_at`, חותמת פגומה או בלי אזור זמן, שעון שלא התקדם, או
    פחות מ-`ETA_MIN_SAMPLE_S` -- בלי ראיה אין ETA, ולא ETA מהחלון שנראה כמו מדידה."""
    first = _iso_or_none(target.get("first_moved_at"))
    if first is None or first.tzinfo is None or written <= 0:
        return None
    seconds = (_utcnow() - first).total_seconds()
    return written / seconds if seconds >= ETA_MIN_SAMPLE_S else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _whole(value: object) -> int | None:
    """מספר שלם אי-שלילי מהדוח; כל דבר אחר (bool, שלילי, מחרוזת) -- None, לא 0."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _stall(target: dict) -> dict:
    """#2988: `stall` / `stall_s` / `stall_limit_s` מדוח הסוכן (agent/lib/restore/stall.sh).

    רק על מגירה שכותבת, ורק מילה מוכרת עם מספר שניות -- אחרת שלושתם None ("זזה"
    או "לא דווח"), לעולם לא "ממתין" בלי ראיה. `stall_limit_s` None = הסוכן לא מסר תקרה."""
    kind, secs = target.get("stall"), _whole(target.get("stall_s"))
    if target.get("state") != "writing" or kind not in ("waiting_disk", "waiting_net") or secs is None:
        return {"stall": None, "stall_s": None, "stall_limit_s": None}
    return {"stall": kind, "stall_s": secs, "stall_limit_s": _whole(target.get("stall_limit_s"))}


def _rejoin(target: dict) -> dict | None:
    """#3064 (R5): מגירה שחזרה ועוד משלימה -- `rejoin` מדוח הסוכן (interfaces.md סעיף 4), לשורה בקונסולה: כמה חסר ובאיזו מחיצה.

    רק על מגירה שכותבת, ורק כש-`missing` שלם אי-שלילי (אחרת `None`: "לא נאמר", לעולם לא 0). `chunks`/`part` שאינם שלמים
    -- `None` בנפרד; ו-`step` רק מילה מוכרת (`neighbor` / `http`). הקונסולה אינה מנחשת אחוז בלי `chunks`."""
    raw = target.get("rejoin")
    if target.get("state") != "writing" or not isinstance(raw, dict):
        return None
    missing = _whole(raw.get("missing"))
    if missing is None:
        return None
    step = raw.get("step")
    return {"missing": missing, "chunks": _whole(raw.get("chunks")), "part": _whole(raw.get("part")),
            "step": step if step in ("neighbor", "http") else None}


def _pulled(target: dict, member: sqlite3.Row | None) -> bool:
    """#3209: מגירה שנשלפה והסבב עוד מחכה לה -- `pulled` מדוח הסוכן (`drawer_back_json`, #3288).

    רק על ראיה: היעד `failed` **והסוכן אמר `pulled` ב-`true` מפורש**, **והמכונה עצמה עוד `writing`**.
    אחרי הכתיבה (`verifying` ואילך, או מכונה שסיימה) אף אחד לא מחכה למגירה -- היא נכשלה בסיבה שלה, ואדומה.
    הערך האמיתי הוא `False`, לא "לא ידוע": `state` נשאר `failed` כמו שהיה (השרת, ההחלטות והקונסולה קוראים אותו)."""
    return (target.get("pulled") is True and target.get("state") == "failed"
            and member is not None and member["state"] == "writing")


def _stalled_s(moved_at: str | None) -> int | None:
    """כמה שניות עברו מאז שהבייטים של המגירה הזאת גדלו — `None` כשלא ידוע.

    ⚠️ **`None` ו-`0` הם שני מצבים שונים ואסור לקפל אותם.** `0` הוא
    "נמדד, והמגירה זזה עכשיו"; `None` הוא "אין חותמת" — מגירה שטרם
    דיווחה, או שרת שהתחיל לפני שהשדה הזה קיים. מסך שמצייר `None`
    כאפס מציג "הכול זז" על מגירה שלא נמדדה כלל (עיקרון 5).
    """
    if not moved_at:
        return None
    try:
        moved = datetime.fromisoformat(moved_at)
    except (TypeError, ValueError):
        return None
    elapsed = (datetime.now(timezone.utc) - moved).total_seconds()
    # A future persisted timestamp is a clock step, not evidence of recent
    # progress.  Make it immediately due for the stall sweep instead of
    # freezing the drawer until wall time catches up.
    return silence.STALL_S if elapsed < 0 else int(elapsed)


def precheck_now(conn: SqlConnection, store: SessionStore,
                 round_row: sqlite3.Row) -> list[dict]:
    """#432: אותן אזהרות ש-`status_view` מציג, על חברי הגל הפתוח — ברגע
    שהוא יוצא לדרך, ביד או לבד."""
    written = _closed(round_row)          # #1760
    names = slot_names.all_by_mac(conn)
    machines = []
    for member in store.members(round_row["wave_session_id"]):
        mac = member["mac"]
        row = conn.execute("SELECT suffix FROM machines WHERE mac = ?", (mac,)).fetchone()
        drawers = drawer_list(conn, mac, written, None, _selected_ports(round_row, mac))
        for d in drawers:
            d["slot_name"] = slot_names.resolve(names.get(mac, {}), d["port"], d["serial"],
                                                d.get("pci"))
        machines.append({"mac": mac, "name": row["suffix"] if row else None,
                         "display_name": _room_label(row["suffix"]) if row else None, "joined": True,
                         "drawer_list": drawers, "slot_names": names.get(mac, {})})
    linkwarn.attach(conn, machines, _image_bytes(store, round_row))     # #3277
    return room_precheck.warnings(machines, round_row["target_slots_json"] is not None)


def _image_bytes(store: SessionStore, round_row: sqlite3.Row) -> int | None:
    """#3277: גודל האימג' לסבב (`linkwarn.image_bytes`), דרך `store.image_bytes` שהאפליקציה מחברת. None = לא ידוע."""
    return linkwarn.image_bytes(round_row, getattr(store, "image_bytes", None))


def _wave_started(conn: SqlConnection, store: SessionStore, round_row: sqlite3.Row) -> None:
    """#3277 (K3, צל): הגל יצא -- המוח רושם כל משכפל שהקישור שלו איטי, עם ETA חזוי. לעולם אינו זורק."""
    try:
        macs = [m["mac"] for m in store.members(round_row["wave_session_id"])]
        linkwarn.started(conn, round_row["wave_session_id"], macs,
                         {mac: _machine_name(conn, mac) for mac in macs}, _image_bytes(store, round_row))
    except Exception:                            # noqa: BLE001 -- הגל יוצא גם כשהמוח נכשל
        log.exception("wave %s: the brain's link decision failed", round_row["wave_session_id"])
    try:
        sid = round_row["wave_session_id"]           # #3469 (K16, צל): חיזוי הזמן מההיסטוריה של השרת
        drives = (drive_rates.decision(conn, sid) or {}).get("roster") or wave_drives(conn, sid)
        timeforecast.started(conn, sid, drives, _image_bytes(store, round_row))
    except Exception:                            # noqa: BLE001
        log.exception("wave %s: the brain's time forecast failed", round_row["wave_session_id"])


def journal_precheck(conn: SqlConnection, round_row: sqlite3.Row,
                     warned: list[dict], user: str) -> None:
    """#432: "סבב שהתחיל בכל זאת חייב לרשום שהאזהרה הייתה שם". שורה אחת
    לגל, רק כשהייתה אזהרה; `user` ריק = הגל יצא לבד (המגירות כיסו את היתרה)."""
    if not warned:
        return
    items = []
    for w in warned:
        item = f"{w['mac']}:{w['port'] if w['port'] is not None else w['serial']}"
        if w.get("nic_mbps"):
            item += f" nic={w['nic_mbps']}"   # #3277: the cloner's distribution card, Mb/s
        if w["crc_rise"]:
            item += f" crc+{w['crc_rise']}"
        if w.get("port_drop") and w.get("port_cap_gbps") and w.get("link_gbps"):
            item += f" drop={w['port_cap_gbps']:g}>{w['link_gbps']:g}"   # #3591 ב'1: a port that saw more
        if w.get("smart_learned"):
            item += f" smart={w['smart_learned']}"   # #2707: #1859's word -- the journal says its kind too
        if w.get("port_suspect"):
            item += " learned=port"      # #1844: the same connection with different disks
        if w.get("disk_suspect"):
            item += " learned=disk"      # #1844: the same disk in different connections
        if w.get("model_slow"):
            item += " learned=slow_model"   # #1845: slower than the usual for its model
        items.append(item)
    # שורה אחת, בלי תקרת `_one_line`: תשע מגירות לא ייחתכו באמצע (סידורי מה-hello
    # הוא קלט חיצוני — רק שבירות השורה מוחלפות).
    detail = f"{round_row['id']} wave={round_row['wave_number']} " + ", ".join(items)
    journal(conn, "room_precheck_warned", " ".join(detail.splitlines()), user,
            round_id=round_row["id"])
    brain_precheck.record(conn, round_row["id"], warned, manual=bool(user))   # #3662 (S28, B72): המוח מתעד שהאזהרה לא חסמה; אינו זורק


#: #953: "256GB" על מדבקת כונן הוא 256×10⁹ — הקונסולה מדברת באותן יחידות.
GB = 10 ** 9


def _room_label(suffix: object) -> str:
    """#3123: שם מחשב השיכפול כפי שהמפעיל קורא אותו — `משכפל-N`, או השם הישן כמו שהוא.
    טקסטים למפעיל (סירוב, סיבת כשל, יומן) משתמשים בזה; מזהה (`mac`, `confirm_name`) נשאר ה-suffix."""
    return machine_names.label("cloner", suffix)


def disk_floor(conn: SqlConnection, selected: list[dict] | None = None
               ) -> dict | None:
    """המגירה **הקטנה ביותר** שמחשבי השיכפול דיווחו ב-hello האחרון — הרצפה
    שאימג' חייב להיכנס אליה כדי שסבב ייכתב על כל המגירות (#953).

    `selected` (#695) מצמצם לפורטים שנבחרו: מגירה שלא תיכתב אינה רצפה.
    בלי בחירה — כל המגירות של כל מחשבי החדר, כפי שהסבב אכן יכתוב אליהן.

    `None` = **אין דיווח** (מכונות שטרם דיברו, או סוכן ישן בלי
    `size_bytes`) — לא "הכול נכנס". הקורא מציג הערה, ובדיקה 2.7 במכונה
    היא הקו האחרון (עיקרון 5). גודל 0/חסר הוא "לא נמדד" ומדולג, לא רצפה.
    """
    chosen = ({item["mac"]: set(item["ports"]) for item in selected}
              if selected is not None else None)
    floor = None
    for row in conn.execute(
        f"SELECT mac, suffix FROM machines WHERE {_IN_ROOM} ORDER BY suffix",
    ):
        if chosen is not None and row["mac"] not in chosen:
            continue
        for disk in _disks(conn, row["mac"]):
            if not isinstance(disk, dict):
                continue
            port = disk.get("port")
            port = port if isinstance(port, int) and not isinstance(port, bool) else None
            if chosen is not None and port not in chosen[row["mac"]]:
                continue
            size = _int(disk.get("size_bytes"), "size_bytes")
            if size <= 0:
                continue
            if floor is None or size < floor["size_bytes"]:
                # #3123: `name` כאן הוא מה שהמפעיל קורא (משפט הסירוב והקונסולה), לא מזהה — המזהה הוא `mac`.
                floor = {"mac": row["mac"], "name": _room_label(row["suffix"]), "port": port,
                         "dev": disk.get("dev"), "size_bytes": size}
    return floor


class FitRefusal(SessionError):
    """`open_round` סירב כי האימג' אינו נכנס (`fit_refusal`). SessionError
    כמו קודם — הקונסולה עונה 409 כרגיל; מסך המשכפל מבחין בו (קוד `fit`, #1863)."""


class RoundRefusal(ValueError):
    """#3891: סירוב ולידציה של `open_round`, בטקסט שלנו שנכתב למפעיל (מגירה אדומה, מלאי שהשתנה, חריץ בלי serial).
    ValueError כמו קודם -- הקונסולה עונה 400 כרגיל; מסך המשכפל מציג את `message`. ValueError אחר שעולה מכאן
    הוא טקסט של פייתון, ולא עובר ללקוח (CodeQL py/stack-trace-exposure)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class RoundGateRefusal(SessionError):
    """#3662 (B70): `open_round` סירב כי המוח פסק ``BLOCK`` (`opengate.gate`, רק ב-``brain_round_gate=enforce``). SessionError כמו
    `FitRefusal` -- הקונסולה עונה 409; הטקסט הוא של הקוד הקיים בשער שהכריע."""


def fit_refusal(manifest: dict, floor: dict | None, min_free_gb: int = 0) -> str | None:
    """הסיבה שהאימג' אינו נכנס לרצפה — או `None` כשהוא נכנס, או כשאין
    רצפה ידועה. **אותה מחרוזת בדיוק** שמסך החדר מציג על האימג' המושבת
    (`imageFitReason` ב-`console.js`), כדי שהמפעיל יראה סיבה אחת
    משני הצדדים: "דיסק 2 במחשב 2 הוא 256GB, האימג' צריך 300GB".

    #1354: "צריך" כולל את המינימום הפנוי במחיצת המערכת (`min_disk_bytes`),
    וכשזה מה שהוסיף — המשפט אומר זאת.

    #1494: `used_bytes` של מחיצת המערכת שלא נמדד **הוא** סירוב, בלי קשר
    לרצפה: הסוכן מסרב לו בכל מגירה ("לא נבדק"), וסבב שנפתח עליו נכשל כולו
    (ממצא ברזל 16/10). עד #1494 זה החזיר `None` כאן, והסבב נפתח.
    """
    blocked = minfree.unchecked(manifest, min_free_gb)
    if blocked is not None:
        return blocked
    need = minfree.min_disk_bytes(manifest, min_free_gb)
    if need is None:
        # #1863: דרישה שאינה ניתנת לקביעה אינה "נכנס" (עיקרון 5) — אותה הכרעה
        # שמסתירה אותו מרשימת המכונה (`allowed_for_disks`). `None` כאן אחרי
        # `unchecked`: פריסה חסרה, או מחיצת מערכת שלא תגדל לאף גודל דיסק.
        layout = required_bytes(manifest)
        return ((minfree.refusal(manifest, layout, min_free_gb) if layout is not None else None)
                or "לא ניתן לקבוע כמה מקום האימג' צריך, ולכן הוא אינו נכתב")
    if floor is None or need <= floor["size_bytes"]:
        return None
    slot = (f"דיסק {floor['port']}" if floor["port"] is not None
            else f"דיסק {floor['dev'] or '?'}")
    layout = minfree.min_disk_bytes(manifest, 0)   # #1897: כולל D:/E: בגודלן במקור — לא "הפנויים" הוסיפו אותן
    extra = (f" (כולל {min_free_gb}GB פנויים במחיצת המערכת)"
             if layout is not None and need > layout else "")
    return (f"{slot} במחשב {floor['name']} הוא {round(floor['size_bytes'] / GB)}GB, "
            f"האימג' צריך {-(-need // GB)}GB{extra}")


# --- מחזור החיים -------------------------------------------------------------


def _refuse_if_changed_since_shown(mac: str, now: list[dict] | None, displayed: object) -> None:
    """#3092 (owner 15/11, option a): the console sends, per chosen slot, the serial and size it DREW
    (`displayed`); the server refuses when the stored inventory of this moment says otherwise -- a drawer
    swapped and re-reported (hello) between the console's last refresh and the POST was never seen by the
    operator, and #2458's `shown` would have recorded it as "what the console showed". No `displayed` = a path
    with no browser (the cloner's own screen, a direct deploy): nothing was drawn, `shown` stays the inventory
    of this moment as before. A `displayed` that is not the contract's shape is refused, not ignored."""
    if displayed is None:
        return
    bad = RoundRefusal("בחירת דיסקי היעד אינה תקינה")
    if not isinstance(displayed, list) or not all(isinstance(x, dict) for x in displayed):
        raise bad
    drawn: dict[int, dict] = {}
    for x in displayed:
        port = x.get("port")
        if isinstance(port, bool) or not isinstance(port, int) or port in drawn:
            raise bad
        drawn[port] = x
    if now is None or set(drawn) != {d["port"] for d in now}:
        raise bad
    for d in now:
        was = drawn[d["port"]]
        size = was.get("size_bytes")
        if (was.get("serial"), None if isinstance(size, bool) else size) != (d["serial"], d["size_bytes"]):
            raise RoundRefusal(f"אי אפשר לפתוח סבב: המלאי של {mac} השתנה מאז שהוצג (חריץ {d['port']}) — בחרו שוב")


def _validate_target_slots(conn: SqlConnection, raw) -> list[dict]:
    """#695: מאמת את בחירת דיסקי היעד ומחזיר צורה קנונית. דוחה בגלוי כל
    קלט פגום — MAC זר, פורט מחוץ ל-drawer_count, פורט כפול, או פורט שאינו
    מחובר כרגע — כדי שלעולם לא ייכתב דיסק לא-נכון (עיקרון 4/5/7). אין
    fallback ל"כל הדיסקים": בחירה ריקה נדחית."""
    if not isinstance(raw, list) or not raw:
        raise RoundRefusal("יש לבחור לפחות דיסק יעד אחד")
    selected = []
    seen_macs = set()
    for item in raw:
        if not isinstance(item, dict):
            raise RoundRefusal("בחירת דיסקי היעד אינה תקינה")
        mac = registry.normalize_mac(item.get("mac", ""))
        ports = item.get("ports")
        displayed = item.get("displayed")        # #3092: what the console drew in each chosen slot (optional)
        if mac is None or mac in seen_macs or not isinstance(ports, list):
            raise RoundRefusal("בחירת דיסקי היעד אינה תקינה")
        row = conn.execute(
            f"SELECT drawer_count, drawer_slots FROM machines WHERE mac = ? AND {_IN_ROOM}",
            (mac,),
        ).fetchone()
        if row is None:
            raise RoundRefusal("נבחר מחשב שאינו שייך לחדר השכפול")
        model_map = model_slots.for_mac(conn, mac)   # #2001
        allowed_ports = drawer_slots.sata_ports(drawer_slots.effective(
            row["drawer_count"], row["drawer_slots"], model_map["slots"] if model_map else None))
        if (not ports or len(set(ports)) != len(ports) or any(
                isinstance(p, bool) or not isinstance(p, int)
                or p not in allowed_ports for p in ports)):
            raise RoundRefusal(f"בחירת המגירות של {mac} אינה תקינה")
        disks = [d for d in _disks(conn, mac) if isinstance(d, dict)]
        live = {d.get("port") for d in disks if d.get("serial")}
        # #105: כונן מחובר בלי serial אינו "אינה מחוברת" — זה שולח את
        # הטכנאי לבדוק כבל תקין. הוא נדחה, ובשם הנכון.
        unidentified = sorted(
            {d.get("port") for d in disks if not d.get("serial")}
            & set(ports) - live)
        if unidentified:
            raise RoundRefusal(
                f"אי אפשר לפתוח סבב: לא ניתן לזהות את הכונן בחריץ "
                f"{unidentified[0]} ב־{mac} (הכונן לא דיווח serial)")
        if not set(ports) <= live:
            raise RoundRefusal(
                f"אי אפשר לפתוח סבב: אחת המגירות שנבחרו ב־{mac} אינה מחוברת")
        # #1732 (נדב 18/10): דיסק אדום לעולם אינו יעד — בכל מסלול שעובר כאן (סבב חדר, סבב
        # ממסך משכפל, הפצה ישירה), גם כשהמסך לא סינן אותו. #2848 (נדב 11/11): "אדום" הוא
        # הכלל האחד (`disk_rule`) — SMART נכשל, קישור SATA איטי, בעיית SATA — באותו טקסט
        # שהמסכים כותבים. "איטי בסבב הזה" אינו כאן: סבב שעוד לא נפתח לא מדד דבר.
        failures = disk_rule.machine_failures(conn, mac, disks)
        judged = {d.get("port"): diskcolor.decide(conn, d, failures, point="open_round", mac=mac) for d in disks
                  if d.get("serial") and d.get("port") in ports}   # #3666: המוח מסווג, וההרשאה (`target`) היא שלו
        red = sorted(p for p, v in judged.items() if not v["target"])
        if red:
            port, why = red[0], judged[red[0]]
            raise RoundRefusal(
                f"אי אפשר לפתוח סבב: דיסק {port} ב־{mac} — {why['target_reason_he']}")
        item = {"mac": mac, "ports": sorted(ports)}
        # #2458: מה שהקונסולה מציגה עכשיו בכל חריץ שנבחר — המלאי השמור שהיא מציירת ממנו
        # (`_disks`). בגל הראשון הסוכן משווה למה **שהוצג**, ולא למלאי של ה-hello שאחרי
        # הבחירה (`shown_disks`, `session.target_disks`): מגירה שהוחלפה בינתיים נכשלת בשמה.
        item["shown"] = target_disks(item["ports"], disks)
        _refuse_if_changed_since_shown(mac, item["shown"], displayed)   # #3092
        # #1954 (נדב 22/10): משכפל כבוי — הבחירה היא לפי הדיווח האחרון שלו, ולכן היא
        # נקשרת ל-serial של כל דיסק שנבחר. כשהוא עולה (`wake_check`) — אותו דיסק נכתב,
        # דיסק אחר או חריץ ריק מדולג בגלוי. משכפל ער: כמו קודם, לפי פורט.
        seen = conn.execute("SELECT last_seen FROM net_devices WHERE mac = ?", (mac,)).fetchone()
        if not _is_awake(seen["last_seen"] if seen else None):
            item["expect"] = {str(d["port"]): d["serial"] for d in disks
                              if d.get("serial") and d.get("port") in ports}
        selected.append(item)
        seen_macs.add(mac)
    return sorted(selected, key=lambda item: item["mac"])


def library_packages(ctx) -> list[dict]:
    """החבילות שבספרייה עכשיו (#1269). `ctx.drivers` חסר בבדיקות ישנות."""
    return list(ctx.drivers.scan().values()) if ctx.drivers is not None else []


#: #3898: פתיחת סבב אחת בכל פעם. הבדיקה "המשכפל תפוס?" והכתיבה של הסבב החדש הן שני צעדים, ושני מפעילים שפותחים יחד על אותו
#: משכפל היו עוברים את שתי הבדיקות; בתוך הנעילה, הבדיקה של השני רואה את הסבב של הראשון. פתיחה נדירה, ולכן נעילה גסה.
_OPEN_LOCK = threading.Lock()


def open_round(ctx, image_id: str, target_drives: int, user: str,
               target_slots=None, expand_partition=None, driver_models=None,
               hostnames=None) -> dict:
    with _OPEN_LOCK:
        return _open_round(ctx, image_id, target_drives, user, target_slots, expand_partition, driver_models, hostnames)


def _open_round(ctx, image_id: str, target_drives: int, user: str,
                target_slots=None, expand_partition=None, driver_models=None,
                hostnames=None) -> dict:
    manifest = ctx.library.get(image_id)
    if manifest is None:
        raise RoundRefusal("אימג' לא קיים בספרייה")
    unavailable = storage_locations.unavailable_message(manifest)
    if unavailable:
        raise SessionError(unavailable)
    # #381: אימג' הקשור למכונה אחת אינו נשפך על מגירות. אין כאן רשימת
    # יעדים בכלל — הכוננים יותקנו במכונות שאיש עוד אינו יודע מי הן —
    # ולכן `restore_refusal` מסרב אותו, וזה הכיוון הנכון (עיקרון 5).
    refusal = restore_refusal(manifest, None)
    if refusal is not None:
        raise RoundRefusal(refusal)
    if target_drives < 1:
        raise RoundRefusal("יעד הכוננים חייב להיות חיובי")
    # #59: בחירת ההרחבה חלה על **כל** גלי הסבב, ולכן היא נשמרת ברמת
    # ה-round ולא ב-session של גל בודד — כל גל חדש קורא אותה מחדש.
    expand_choice = validate_expand_choice(manifest, expand_partition)
    machines = ctx.conn.execute(
        f"SELECT COUNT(*) AS n FROM machines WHERE {_IN_ROOM}"
    ).fetchone()["n"]
    if machines == 0:
        raise RoundRefusal("אין מחשבי שיכפול רשומים — רשמו אותם בקונסולה קודם")
    if active_rounds(ctx.conn) and not PARALLEL_ROUNDS:   # #3898: עד שלב 3 -- כמו תמיד
        raise SessionError("כבר יש סבב חדר פעיל")

    # #1410: בחירת דיסקי היעד מפורשת וחובה. מאמתים אותה לפני שנוגעים
    # בשום דבר; אין fallback לכתיבה לכל המגירות.
    selected = _validate_target_slots(ctx.conn, target_slots)
    # #3898: משכפל בסבב אחד בלבד -- סירוב בשם, לפני שנפתח גל. אטומי מול פותח אחר: כל הפתיחה תחת `_OPEN_LOCK`.
    taken = round_cloners.busy(ctx.conn, {item["mac"] for item in selected})
    if taken:
        mac = sorted(taken)[0]
        raise SessionError(f"{round_cloners.label(ctx.conn, mac)} כבר בסבב אחר ({round_label(ctx, taken[mac])})")
    selected_count = sum(len(item["ports"]) for item in selected)
    if target_drives < selected_count:
        raise RoundRefusal(
            "מספר היעדים בסבב קטן ממספר הדיסקים שנבחרו לגל הראשון")
    # #1269: דגם דרייברים לכל דיסק יעד — אופציונלי; מאומת לפני כל כתיבה.
    chosen_drivers = room_drivers.validate(driver_models, selected, library_packages(ctx))
    # #3181: יותר דיסקים ממגירות = פס ייצור, לבד, בלי מתג. בפס ייצור שם לכל דיסק שביקשו (`queue`).
    drawer_total = prodline.drawers(ctx.conn, selected)
    line = prodline.is_line(target_drives, drawer_total)
    # #2786: שם מחשב לכל דיסק יעד — אופציונלי; מאומת לפני כל כתיבה, כמו הדרייברים.
    chosen_names = prodline.validate_names(hostnames, selected, target_drives, manifest, line)
    # #953: אימג' שאינו נכנס למגירה הקטנה ביותר שדווחה אינו נפתח — לא רק
    # מושבת במסך. הסירוב נוקב במגירה ובמספרים; 409 כמו כל התנגשות עם מצב
    # החדר. בלי דיווח (`None`) אין סירוב — בדיקה 2.7 במכונה תסרב לה לבדה.
    refusal = fit_refusal(manifest, disk_floor(ctx.conn, selected),
                          minfree.min_free_gb(ctx.conn))   # #1354
    if refusal is not None:
        raise FitRefusal(refusal)

    # K12/K13 (צל): כוח העיבוד והשירותים לפני שהסבב נפתח. נרשם בלבד; רק `brain_cpu_gate` דלוק מחזיק, ורק על WAIT.
    brain_services.gate(ctx.conn, point="open_round")
    cpu_gate = cpucap.gate(ctx.conn, point="open_round", live=len(ctx.store.active_pulls()))
    if cpu_gate.hold:
        raise SessionError(cpu_gate.he)
    # #3662 (B70): הפסק האחד של המוח לפני שחריץ הסבב נתפס. ב-shadow (ברירת המחדל) לא נקרא דבר כאן והשערים רצים ברקע אחרי הפתיחה (`gates.start`);
    # רק `brain_round_gate=enforce` (כבוי) קורא אותם עכשיו, ורק `BLOCK` חוסם. הסירובים שלמעלה (אדום, לא נכנס) הם אינווריאנטים ורצים לפניו.
    opened = opengate.gate(ctx.conn, [item["mac"] for item in selected])
    if opened.block:
        raise RoundGateRefusal(opened.he)
    # פתיחת הגל תופסת את חריץ הסבב היחיד במערכת ומעירה את החדר (WoL).
    wave_id = ctx.store.open(
        CLONERS_GROUP, image_id, prefix="ROOM",
        expected_clients=len(selected), opened_by=user, expand_partition=expand_choice,
        roster=[item["mac"] for item in selected],   # נדב 17/10: מעירים רק את מי שנבחר
    )
    round_id = "room_" + secrets.token_hex(4)
    ctx.conn.execute(
        "INSERT INTO room_rounds (id, image_id, target_drives, target_slots_json,"
        " expand_partition, driver_models_json, hostnames_json, state, wave_session_id,"
        " opened_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)",
        (round_id, image_id, target_drives,
         json.dumps(selected, separators=(",", ":")),
         expand_choice,
         json.dumps(chosen_drivers, ensure_ascii=False) if chosen_drivers else None,
         json.dumps(chosen_names, ensure_ascii=False) if chosen_names else None,
         wave_id, user, now_iso()),
    )
    if line:
        prodline.open_line(ctx.conn, round_id, selected, target_drives, drawer_total, chosen_names)
    ctx.conn.commit()
    readbackhash.kick(image_id)   # #1864: digest the round's image first, while its machines boot
    storage_history.sample_local(ctx.conn, getattr(ctx, "library", None), "round")   # #1886
    journal(ctx.conn, "room_open",
            f"{round_id} {image_id} target={target_drives}"
            # #59: אותו כלל כמו session_open — "auto" אינו נרשם, כדי
            # שסבב שלא נגע בהרחבה לא ייראה כמו החלטה שמישהו קיבל.
            + (f" expand={expand_choice}" if expand_choice != "auto" else "")
            + (f" drivers={len(chosen_drivers)}" if chosen_drivers else "")
            + (f" names={chosen_names['names'][0]['name']}..{chosen_names['names'][-1]['name']}"
               if chosen_names else "")
            + (f" line=drawers:{drawer_total}" if line else ""),
            user, round_id=round_id)
    gates.start(ctx.conn, round_id, [item["mac"] for item in selected])   # #3064 (K3, צל): עשרת השערים ברקע, אחרי שהסבב נשמר; אינו זורק ואינו מחכה
    return {"id": round_id, "wave_session_id": wave_id}


def round_label(ctx, round_row: sqlite3.Row) -> str:
    """שם הסבב כפי שהמפעיל רואה אותו — שם האימג' שהוא משדר.

    לסבב אין שם משלו: יש `id` אקראי שאינו מופיע על שום מסך, ומספר גל
    שהוא ספרה. מה שהמפעיל **קורא** בכותרת הוא שם האימג', וזה מה שהוא
    מקליד כדי לעצור (עיקרון 7). הנפילה חזרה ל-`image_id` היא אותה
    נפילה בדיוק כמו ב-`status_view` — **מקום אחד**, אחרת סבב שהאימג'
    שלו נמחק באמצע היה בלתי-ניתן לעצירה.
    """
    # #715: לסבב מדיסק חי אין אימג' בספרייה — השם הוא `<מכונה>:<דיסק>`,
    # וזה מה שהמפעיל מקליד כדי לעצור אותו.
    from server.rounds import direct                 # noqa: PLC0415 — direct מייבא את room
    if direct.is_build_disk(round_row):
        return direct.source_label(ctx.conn, round_row)
    manifest = ctx.library.get(round_row["image_id"])
    return manifest["name"] if manifest else round_row["image_id"]


def close_round(ctx, user: str, confirm_name: str) -> None:
    round_row = visible_round(ctx.conn)
    if round_row is None:
        raise SessionError("אין סבב חדר פעיל")
    # פעולה הרסנית מאחורי הקלדת שם — אותו דפוס כמו מחיקת אימג'
    # (`console_library.delete_image`), ובאותה שורה באפיון. לפני #533
    # POST ריק עם עוגייה תקפה הרג משדר חי, וההקלדה נאכפה במסך בלבד.
    if confirm_name != round_label(ctx, round_row):
        raise ValueError("השם שהוקלד אינו זהה לשם האימג' שהסבב משדר")
    original_state = round_row["state"]
    # #3707: שלוש הכתיבות של הסגירה — התביעה, `closed` והשחזור — עוברות ב-`_write_lock`
    # כמו כל כותב חם. בלעדיו הן התחרו על נעילת הכתיבה עם תהליכון השערים (`open_round`,
    # #3064) וקיבלו `database is locked`; ובשחזור זה השאיר את הסבב ב-`closing` לתמיד.
    _settle(ctx.conn)
    with _write_lock, writing(ctx.conn):
        claimed = update_one(
            ctx.conn,
            "UPDATE room_rounds SET state = 'closing'"
            " WHERE id = ? AND state IN ('active', 'failed')",
            (round_row["id"],),
        )
    if not claimed:
        raise SessionError("סבב החדר כבר נסגר או נמצא בתהליך סגירה")
    # #2427: הגל נקרא **אחרי** ה-CAS. `_finish_wave` בתהליכון אחר יכול היה להחליף את
    # הגל בין הקריאה למעלה ל-CAS; עם השורה הישנה הגל החדש נשאר `open` ותפס את
    # חריץ המולטיקאסט לתמיד. מ-`closing` והלאה ה-UPDATE שלו (`state = 'active'`)
    # כבר אינו יכול להחליף אותו, ומה שנקרא כאן הוא הגל האחרון.
    round_row = ctx.conn.execute(
        "SELECT * FROM room_rounds WHERE id = ?", (round_row["id"],)).fetchone()
    try:
        wave = ctx.conn.execute(
            "SELECT id, state FROM sessions WHERE id = ?",
            (round_row["wave_session_id"],),
        ).fetchone()
        if wave is not None and wave["state"] in ("open", "running"):
            ctx.store.close(wave["id"], user)
        # store.close קורא on_closed → עצירת המנוע רק כשהגל עוד open/running.
        # גל שכבר סגור (או חסר) היה מחזיר ok והמשדר נשאר חי (#439).
        # מזהה הגל מונע מסגירה מאוחרת של כשל ישן לעצור שידור חדש שכבר
        # נפתח בחריץ שהתפנה (#773).
        pid = ctx.chunk.stop(round_row["wave_session_id"]) if ctx.chunk is not None else None
        if pid is not None:
            who = f"PID {pid}" if pid > 0 else "לא הצלחנו לוודא שהוא מת"
            raise SessionError(
                f"chunkcast send עדיין רץ ({who}) אחרי ניסיון העצירה — הסבב לא נסגר"
            )
        _settle(ctx.conn)
        with _write_lock, writing(ctx.conn):
            ctx.conn.execute(
                "UPDATE room_rounds SET state = 'closed', closed_at = ?"
                " WHERE id = ? AND state = 'closing'",
                (now_iso(), round_row["id"]),
            )
    except Exception:
        _settle(ctx.conn)
        with _write_lock, writing(ctx.conn):
            ctx.conn.execute(
                "UPDATE room_rounds SET state = ? WHERE id = ? AND state = 'closing'",
                (original_state, round_row["id"]),
            )
        raise
    journal(ctx.conn, "room_close",
            f'{round_row["id"]} written={round_row["written_drives"]}'
            f'/{round_row["target_drives"]}', user, round_id=round_row["id"])
    # #715: המקור מפסיק עם הסבב — המשימה נסגרת, ומחשב הבנייה רואה זאת
    # ב-hello הבא ואינו משדר.
    from server.rounds import direct                 # noqa: PLC0415
    direct.cancel_source_task(ctx.conn, round_row, user)


def _iso_or_none(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def _mark_lost(conn: SqlConnection, store: SessionStore, wave: sqlite3.Row,
               members: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """מסמן `lost` חבר גל שלא דיווח התקדמות מעל סף האובדן (#450).

    **#3657: ההחלטה (הסף, ההשוואה, גרייס הגל, חותמת בעתיד) היא של המוח** --
    `brain.silence.judge_lost`, כלל K-LOST, נרשמת ב-`brain_decisions` עם הראיות.
    כאן רק נאספים החברים והחותמות, ומתבצעת התוצאה: ה-UPDATE, מחיקת היומן
    (מאחורי הווטו) ושורת היומן. אין החלטה מהמוח = לא מסמנים (עיקרון 5).

    הגרייס נמדד מ-`max(updated_at, started_at)`: גל שרק התחיל לרוץ נותן
    לכל חבר את הסף כולו להשמיע קול ראשון לפני שהוא מוכרז אבוד — אחרת
    חבר שהצטרף בשלב הפתיחה (`updated_at` בן דקות, כי בפתיחה אין
    progress) היה מופקע ברגע שהגל מתחיל. שני מקורות `None` (אין חותמת)
    אינם "אבוד" אלא "לא נמדד", והכיוון הבטוח הוא לא להכריז (עיקרון 5).

    ה-UPDATE מותנה במצב המדויק שנקרא: דיווח שהגיע בין הקריאה לכתיבה כבר
    שינה אותו, והתביעה הזו נותנת לו לנצח (`update_one` גם משחרר את
    הנעילה כשלא תאם — #54). מחזיר את החברים המעודכנים כשמשהו השתנה.

    #1947: חותמת **בעתיד** אינה "אבוד". היא קפיצת שעון שהגלאי של התהליך
    (`clockstep`) לא ראה — למשל קפיצה לפני הדגימה הראשונה אחרי עלייה. עד
    #1947 היא סומנה אבודה מיד (#1771 רצה רק שלא תקפיא את הגל), וזה בדיוק
    מה שהפיל גל שלם כשהשעון זז אחורה. עכשיו היא מכוילת ל"עכשיו": המכונה
    מקבלת את הסף כולו מעכשיו — חסום, לא קפוא, ולא אובדן מיידי.
    """
    now = _utcnow()
    lost_limit = agent_timing.lost_seconds(conn)   # #2056: הגדרה בקונסולה; המוח קורא אותה כקלט, ולא מחזיק עותק
    # #3657 (K-LOST, S22): מי אבד ומי חותמתו בעתיד -- החלטה של המוח. אין החלטה (המוח נכשל) = לא מסמנים ולא מכיילים דבר
    calls = silence.decide_lost(conn, members, wave_id=wave["id"], started_at=wave["started_at"], now=now,
                                lost_s=lost_limit, jump_s=clockstep.JUMP_SECONDS, final=reports.FINAL)
    if calls is None:
        return members
    acted = [c for c in calls if c.verdict in (silence.LOST, silence.FUTURE)]
    rows = silence.record(conn, acted, round_id=wave["id"])
    verdict = {c.target: c.verdict for c in acted}
    lost = [m for m in members if verdict.get(m["mac"]) == silence.LOST]
    future = [m for m in members if verdict.get(m["mac"]) == silence.FUTURE]
    if future:
        _rebase_future(conn, wave, future, now)
        for m in future:
            silence.settle(conn, rows.get(m["mac"]), "חותמת הגל וחבריה כוילו ל'עכשיו' (קפיצת שעון), לא סומן אבוד")
    if not lost:
        return store.members(wave["id"]) if future else members
    marked = [
        m for m in lost
        if update_one(
            conn,
            "UPDATE session_members SET state = ?, updated_at = ?"
            " WHERE session_id = ? AND mac = ? AND state = ?",
            (reports.LOST, now_iso(), wave["id"], m["mac"], m["state"]),
        )
    ]
    marked_macs = {m["mac"] for m in marked}
    for m in lost:
        silence.settle(conn, rows.get(m["mac"]),
                       "השרת סימן 'אבד'" if m["mac"] in marked_macs
                       else "דיווח הגיע בין ההחלטה לסימון, ולכן לא סומן אבוד")
    if not marked:
        return members
    # `update_one` כבר עשה commit לכל שינוי מצב, ו-`journal` נועל וכותב
    # בעצמו (#379) — ולכן אין כאן `conn.commit()` חשוף (#450, #54).
    from server.rounds import ledger                     # noqa: PLC0415
    from server.brain import veto                        # noqa: PLC0415
    for m in marked:
        # #3064 (K2, D21): הווטו נשאל לפני המחיקה. ב-shadow (ברירת המחדל) הוא רושם בלבד, והמחיקה מתבצעת כמו היום
        if veto.check(conn, action="EXPIRE_LEDGER", rule="D21", round_id=wave["id"], targets=[m["mac"]],
                      reason=f"no progress for {lost_limit}s", now=now.timestamp()).proceed:
            ledger.expire(conn, wave["id"], m["mac"])   # #2528: לא חזרה בזמן — אין המשך, והמידע נמחק
        open_ = _unreported(m)   # #2841: what it was doing when it went quiet
        journal(conn, "client_lost", f'{m["mac"]} in {wave["id"]} last={m["state"]}'
                + (" unreported=" + ",".join(f"{d}:{s}" for d, s in open_) if open_ else ""),
                machine=m["mac"], round_id=wave["id"])
    return store.members(wave["id"])


#: #2841: מצבי יעד שבהם לא נמסרה תוצאה — המגירה עוד נכתבת או נבדקת.
_OPEN_TARGET_STATES = ("waiting", "writing", "verifying", "naming", "staging")
_TARGET_HE = {"waiting": "ממתינה", "writing": "בכתיבה", "verifying": "באימות",
              "naming": "במתן שם", "staging": "בהנחת דרייברים", "unread": "לא נקרא"}


def _unreported(member: sqlite3.Row) -> list[tuple[str, str]]:
    """#2841: יעדי החבר שהדיווח האחרון שלו השאיר פתוחים — ``[(dev, state)]``.

    targets_json שלא נקרא אינו "אין יעדים פתוחים" (עיקרון 5): הוא מוחזר כ-
    ``[("?", "unread")]``, כך שהשורה ביומן אומרת שלא ידוע מה נשאר פתוח."""
    try:
        targets = json.loads(member["targets_json"] or "[]")
    except (TypeError, ValueError):
        return [("?", "unread")]
    if not isinstance(targets, list):
        return [("?", "unread")]
    return [(str(t.get("dev", "?")), str(t.get("state")))
            for t in targets
            if isinstance(t, dict) and t.get("state") in _OPEN_TARGET_STATES]


def _lost_before_result(conn: SqlConnection, members: list[sqlite3.Row],
                        total: int, target: int) -> str | None:
    """#2841: אף מגירה לא נכתבה, ומשכפל הפסיק לדווח באמצע בלי תוצאה — הסיבה
    בשמה, או ``None`` כשזה לא המקרה (עבודה, 11/11: 0/3 שנסגר כ-`room_done`
    רגיל, והמגירות נשארו "באימות" בלי שאיש אמר שהתוצאה לא הגיעה)."""
    if total:
        return None
    lost = [m for m in members if m["state"] == reports.LOST]
    if not lost:
        return None
    parts = []
    for m in lost:
        row = conn.execute("SELECT suffix FROM machines WHERE mac = ?", (m["mac"],)).fetchone()
        name = _room_label(row["suffix"]) if row and row["suffix"] else m["mac"]
        open_ = _unreported(m)
        what = (" (" + ", ".join(f"{d} {_TARGET_HE.get(s, s)}" for d, s in open_) + ")"
                if open_ else "")
        parts.append(f"{name}{what}")
    return (f"המשכפל {', '.join(parts)} הפסיק לדווח לפני שדיווח תוצאה"
            f" — {total}/{target} נכתבו")


def _failed_before_write(conn: SqlConnection, members: list[sqlite3.Row],
                         total: int, target: int, cancelled) -> str | None:
    """#2907: אף מגירה לא נכתבה, ומשכפל דיווח (או נרשם) `failed` — הסיבה בשמה,
    או ``None``. הסדר של #2841: משכפל שאבד קודם (`_lost_before_result`).

    מדיסק חי אין גל שני, ולכן גל שנסגר כך **הוא** הסבב. בלי סיבה הוא נסגר
    `closed` 0/N ונעלם מהמסך כאילו הסתיים — כשכל המגירות נכשלו, או כשהשרת עלה
    מחדש באמצע (`reconcile` מכשיל את החברים בשם `RESTARTED_SESSION`).
    מגירה שמפעיל ביטל אינה כשל: `cancelled` לא ריק → ``None``."""
    if total or cancelled:
        return None
    failed = [m for m in members if m["state"] == "failed"]
    if not failed:
        return None
    parts = []
    for m in failed:
        row = conn.execute("SELECT suffix FROM machines WHERE mac = ?", (m["mac"],)).fetchone()
        name = _room_label(row["suffix"]) if row and row["suffix"] else m["mac"]
        why = (m["error"] or "").strip()
        parts.append(f"{name} ({why})" if why else name)
    return f"כל המגירות נכשלו: {', '.join(parts)} — {total}/{target} נכתבו"


def _rebase_future(conn: SqlConnection, wave: sqlite3.Row,
                   future: list[sqlite3.Row], now: datetime) -> None:
    """#1947: חותמות הגל שבעתיד (קפיצה שלא נצפתה) נקבעות ל-`now` — מותנה
    בערך שנקרא, כך שדיווח שהגיע בינתיים מנצח; ושורת `clock_jump` ביומן."""
    stamp = now.isoformat(timespec="seconds")
    ahead = now + timedelta(seconds=clockstep.JUMP_SECONDS)
    started = _iso_or_none(wave["started_at"])
    with writing(conn):
        if started is not None and started > ahead:
            conn.execute("UPDATE sessions SET started_at = ? WHERE id = ? AND started_at = ?",
                         (stamp, wave["id"], wave["started_at"]))
        for m in future:
            updated = _iso_or_none(m["updated_at"])
            if updated is not None and updated > now:
                conn.execute(
                    "UPDATE session_members SET updated_at = ?"
                    " WHERE session_id = ? AND mac = ? AND updated_at = ? AND state = ?",
                    (stamp, wave["id"], m["mac"], m["updated_at"], m["state"]))
    journal(conn, "clock_jump", f'? {wave["id"]}', round_id=wave["id"])


def _utcnow() -> datetime:
    """השעון של הסבב: ה-WoL החוזר (#1721), ההפקעה וגלאי הקפיצות (#1947) —
    פונקציה, כדי שהבדיקה תזייף אותו."""
    return datetime.now(timezone.utc)


def _unseen(conn: SqlConnection, store: SessionStore,
            round_row: sqlite3.Row, wave: sqlite3.Row) -> list[str]:
    """#1721: מחשבי השיכפול שהגל הפתוח עוד ממתין להם ושלא נראו מאז שנפתח.

    רק מי ש**נבחר** לסבב (#1580; None = סבב ישן בלי בחירה — לא נוגעים),
    שנשארה לו לפי הדיווח האחרון מגירה נבחרת שלא נכתבה (מי שסיים את כולן
    אינו נדרש לגל הזה), שלא הצטרף לגל, וש-hello שלו אינו מאז הפתיחה.
    משכפל בספירת הכיבוי אינו שולח hello, ולכן "נראה" = עלה מחדש."""
    roster = _roster(round_row)
    opened = _iso_or_none(wave["created_at"])
    if roster is None or opened is None:
        return []
    joined = {m["mac"] for m in store.members(wave["id"])}
    written = _closed(round_row)          # #1760: a red-skipped drawer does not need the cloner
    unseen = []
    for mac in roster:
        if mac in joined or not fresh_serials(conn, mac, written,
                                              _selected_ports(round_row, mac)):
            continue
        if _not_back(conn, mac, opened):
            unseen.append(mac)
    return unseen


#: the resolution of the stamps `_not_back` compares (both are written with `timespec="seconds"`).
_STAMP_RES = timedelta(seconds=1)


def _not_back(conn: SqlConnection, mac: str, opened: datetime) -> bool:
    """המכונה לא נראתה מאז שהגל נפתח (או נראתה ושתקה, או מחזיקה תוצאות)."""
    row = conn.execute("SELECT last_seen FROM net_devices WHERE mac = ?", (mac,)).fetchone()
    seen = _iso_or_none(row["last_seen"]) if row else None
    # #2909: משכפל שמחזיק תוצאות (#2031) עוד שולח hello לא-מצטרף — ה-beat של מסכי
    # השיכפול המקומי — ו-`last_seen` הטרי שלו אינו "חזר לתור": הוא לא יצטרף עד "כבה".
    # #2940: "seen after the opening" is not "still awake": a cloner switched off from the
    # console (its hold gone) kept a `last_seen` after the opening and was never woken.
    # #4043: `last_seen` and the wave's `created_at` are both cut to whole seconds, so a hello and an opening one
    # stamp apart may be 0.1 s apart (hello at S.9, opening at S+1.1): that is not an order, and it made an awake
    # cloner "not back" (a false LINE_RELIGHT, ~1 run in 4). Only a gap of 2 s or more is "before the opening".
    return seen is None or seen + _STAMP_RES < opened or held.is_held(conn, mac) or _hello_stale(seen)


def _hello_stale(seen: datetime) -> bool:
    """#2940: the last hello is older than `AWAKE_SECONDS` (the beat of a cloner that is up is
    ~10 s). Real time, like `_fresh`: `last_seen` is written with it. A stamp in the future
    (clock step, #774) is not called stale here -- the clock-jump rules (#1947) own that case."""
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - seen > timedelta(seconds=AWAKE_SECONDS)


@overload
def _aware(at: datetime) -> datetime: ...


@overload
def _aware(at: None) -> None: ...


def _aware(at: datetime | None) -> datetime | None:
    if at is None:
        return None
    return at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)


def _left_held(conn: SqlConnection, mac: str, opened: datetime, now: datetime) -> datetime | None:
    """מתי ``mac`` עבר ממוחזק לשקט **בגל הזה**, או None.

    כיבוי מהקונסולה (`held.clear`) כותב `held_released` והשעון ב-detail (לא ב-`ts`: הוא שעון הקיר).
    החזקה שהתיישנה בלי clear — חשמל שנמשך — נספרת מ-`held_at + FRESH_SECONDS`. שחרור של גל קודם
    אינו נספר. משתי החותמות שבתוך הגל נלקחת המאוחרת."""
    best: datetime | None = None
    for row in conn.execute(
            "SELECT detail FROM journal WHERE event = 'held_released' AND machine = ?", (mac,)):
        at = _aware(_iso_or_none(row["detail"]))
        if at is not None and opened <= at <= now and (best is None or at > best):
            best = at
    row = conn.execute("SELECT held_at FROM net_devices WHERE mac = ?", (mac,)).fetchone()
    if row is not None and row["held_at"] and not held.is_held(conn, mac, now):
        at = _aware(_iso_or_none(row["held_at"]))
        if at is not None:
            stale = at + timedelta(seconds=held.FRESH_SECONDS)
            if opened <= stale <= now and (best is None or stale > best):
                best = stale
    return best


def _silent_for(conn: SqlConnection, quiet: list[str], opened: datetime | None,
                now: datetime) -> float | None:
    """כמה שניות עברו מאז שהמכונה השקטה האחרונה עזבה את המוחזק, או None.

    None כשאחת מהשקטות לא עזבה מוחזק בגל הזה: היא הייתה כבויה מההתחלה, והשעון נשאר פתיחת הגל.
    הגיל הקטן ביותר הוא השחרור המאוחר ביותר, כדי שמכונה שזה עתה נכבתה לא תירש שתיקה ארוכה של שכנה."""
    if opened is None or not quiet:
        return None
    moments = []
    for mac in quiet:
        at = _left_held(conn, mac, opened, now)
        if at is None:
            return None
        moments.append(at)
    return (now - max(moments)).total_seconds()


def _rewake(conn: SqlConnection, store: SessionStore,
            round_row: sqlite3.Row, wave: sqlite3.Row) -> None:
    """#1721: WoL חוזר — עם תקרה — למי שהגל הפתוח ממתין לו ולא חזר.

    ברזל rc5: גל 2 נפתח והעיר את המשכפל באותה שנייה שבה הוא נכנס לספירת
    הכיבוי; ה-WoL נבלע, המשכפל כבה, והסבב נשאר `active` בלי איש שיעיר אותו.
    הספירה היא שורות `room_rewake` של הגל ביומן — כך היא שורדת אתחול שרת
    ונראית למפעיל. אחרי התקרה והאתחול — הסבב נכשל בגלוי ובשם המכונה
    (עיקרון 5), והחריץ משתחרר.

    **#3657: ההחלטה (מתי שוב, כמה פעמים, מתי נכשל, וגיל שלילי אחרי קפיצת שעון)
    היא של המוח** — `brain.silence.judge_rewake`, כלל K-REWAKE, נרשמת ב-
    `brain_decisions` עם הראיות והתוצאה. כאן נאספות העובדות (מי לא נראה, כמה
    הערות נשלחו, כמה זמן מאז שהגל נפתח, ומתי עבר ממוחזק לשקט) ומתבצעת התוצאה.
    אין החלטה מהמוח = לא שולחים, לא מכשילים ולא מכיילים.

    **#3912:** גל שחיכה למוחזק סופר את ההערות ואת תקרת האתחול מרגע השחרור,
    לא מפתיחת הגל. בלי רגע כזה לכל שקט — השעון נשאר פתיחת הגל."""
    unseen = _unseen(conn, store, round_row, wave)
    if not unseen:
        return
    # #3590: דלוק ומחזיק תוצאות (#2031) הוא ראיה חיובית שהמכונה דולקת ומדווחת — ה-WoL אליה
    # לשווא, והיא נשארת "לא נראתה" רק כדי שהסבב ייכשל בשמה אחרי אותה תקרה.
    waiting = [mac for mac in unseen if held.is_held(conn, mac)]
    quiet = [mac for mac in unseen if mac not in waiting]
    now = _aware(_utcnow())
    opened = _aware(_iso_or_none(wave["created_at"]))
    if opened is None:
        # a `created_at` that is not a date: how long the wave has waited is not known, and a WoL is not sent on a guess
        log.warning("room: wave %s of round %s has no readable created_at (%r): no repeated WoL for it",
                    wave["id"], round_row["id"], wave["created_at"])
        return
    age = (now - opened).total_seconds()
    sent = conn.execute(
        "SELECT COUNT(*) AS n FROM journal WHERE event = 'room_rewake' AND detail LIKE ?",
        (wave["id"] + " %",),
    ).fetchone()["n"]

    def since_last() -> float | None:
        """כמה שניות מאז ההערה האחרונה של הגל (נכתבה אחרי קפיצת השעון, כלומר זמן אמיתי), או None."""
        last = conn.execute(
            "SELECT MAX(ts) AS ts FROM journal WHERE event = 'room_rewake' AND detail LIKE ?",
            (wave["id"] + " %",),
        ).fetchone()["ts"]
        last_at = _iso_or_none(last)
        return (_utcnow() - last_at).total_seconds() if last_at else None

    call = silence.decide_rewake(conn, round_id=round_row["id"], sent=sent, age_s=age, since_last=since_last,
                                 quiet=len(quiet), waiting=len(waiting),
                                 silent_for_s=_silent_for(conn, quiet, opened, now))
    if call is None or call.verdict == silence.WAIT:
        return
    rows = silence.record(conn, [call], round_id=round_row["id"], entity_kind="round")
    names = ", ".join(_machine_name(conn, mac) for mac in unseen)
    if call.verdict == silence.CALIBRATE:
        # #3590: הגל ממתין רק למחזיק, אין הערה שנשלחה, ולכן אין חותמת זמן אמיתית — מכיילים את הגל ל'עכשיו' (כמו #1947)
        with writing(conn):
            conn.execute("UPDATE sessions SET created_at = ? WHERE id = ? AND created_at = ?",
                         (_utcnow().isoformat(timespec="seconds"), wave["id"], wave["created_at"]))
        silence.settle(conn, rows.get(round_row["id"]), "חותמת פתיחת הגל כוילה ל'עכשיו'")
        return
    if call.verdict == silence.SEND:
        store.on_opened(CLONERS_GROUP, None, quiet)
        quiet_names = ", ".join(_machine_name(conn, mac) for mac in quiet)
        journal(conn, "room_rewake",
                f'{wave["id"]} {round_row["id"]} n={call.n}/{call.of} {quiet_names}',
                round_id=round_row["id"])
        silence.settle(conn, rows.get(round_row["id"]), f"נשלח WoL חוזר ל: {quiet_names}")
        return
    if call.verdict != silence.FAIL:
        return
    reason = (f"מחשב השיכפול {names} לא חזר לגל — {call.of + 1} הערות WoL"
              " בלי תשובה. לבדוק חשמל, כבל רשת ו-Wake-on-LAN ב-BIOS")
    # #2031: דלוק ומחזיק תוצאות (#2024) — WoL לא יעיר אותו; הסיבה האמיתית, בשמו
    if waiting:
        reason = (f"מחשב השיכפול {', '.join(_machine_name(conn, mac) for mac in waiting)}"
                  " דלוק ומחכה ללחיצה על 'כבה' — יש בו דיסק עם בעיה מהסבב הקודם,"
                  " ולכן לא הצטרף לגל. לחצו 'כבה' (במחשב או בקונסולה) ופתחו סבב חדש")
    if _fail_round(conn, store, round_row, reason):
        journal(conn, "room_wake_failed",
                f'{round_row["id"]} {wave["id"]} {names}'
                f' written={round_row["written_drives"]}/{round_row["target_drives"]}',
                round_id=round_row["id"])
        from server.rounds import direct         # noqa: PLC0415 — כמו ב-`_resume`
        direct.cancel_source_task(conn, round_row, "")
        silence.settle(conn, rows.get(round_row["id"]), f"הסבב נכשל בשם: {names}")
    else:
        silence.settle(conn, rows.get(round_row["id"]), "הסבב כבר אינו פתוח: לא הוכשל")


def _k10(conn: SqlConnection, store: SessionStore,
         round_row: sqlite3.Row, wave: sqlite3.Row) -> None:
    """#3276 (K10, צל): מה שהמוח רואה על הגל הפתוח -- כל משכפל שנבחר: יש לו מגירה לכתוב, מוחזק, הצטרף, וכמה
    שניות אחרי הדיווח הסופי האחרון שלו נפתח הגל (ה-WoL של הפתיחה). רושם בלבד; לעולם אינו זורק אל הדופק."""
    roster = _roster(round_row)
    opened = _iso_or_none(wave["created_at"])
    if roster is None or opened is None:
        return
    try:
        joined = {m["mac"] for m in store.members(wave["id"])}
        written = _closed(round_row)
        final = sorted(reports.FINAL)
        cloners = []
        for mac in roster:
            last = conn.execute(
                "SELECT updated_at FROM session_members WHERE mac = ? AND session_id != ?"
                f" AND state IN ({','.join('?' * len(final))}) ORDER BY updated_at DESC LIMIT 1",
                (mac, wave["id"], *final)).fetchone()
            done = _iso_or_none(last["updated_at"]) if last else None
            cloners.append(roundend.Cloner(
                mac=mac, name=_machine_name(conn, mac),
                fresh=bool(fresh_serials(conn, mac, written, _selected_ports(round_row, mac))),
                held=held.is_held(conn, mac), joined=mac in joined,
                done_gap_s=(opened - done).total_seconds() if done else None))
        roundend.observe(conn, round_id=round_row["id"], wave_id=wave["id"], cloners=cloners,
                         end_s=agent_timing.seconds(conn, agent_timing.END))
    except Exception:                            # noqa: BLE001 -- הסבב ממשיך גם כשהמוח נכשל
        log.exception("wave %s: K10 failed", wave["id"])


def _line_cloners(conn: SqlConnection, round_row: sqlite3.Row, roster) -> list:
    """המשכפלים של סבב פס ייצור כפי ש-B24 רואה אותם: מספר, מגירות, ומוחזק."""
    cloners = []
    for mac in roster:
        row = conn.execute("SELECT suffix FROM machines WHERE mac = ?", (mac,)).fetchone()
        suffix = str(row["suffix"]) if row else ""
        drawers = max(prodline.machine_drawers(conn, mac), len(_selected_ports(round_row, mac) or ()))
        cloners.append(brain_line.Cloner(mac=mac, name=_machine_name(conn, mac),
                                         number=int(suffix) if suffix.isdigit() else None,
                                         drawers=drawers or None, held=held.is_held(conn, mac)))
    return cloners


def _relight(conn: SqlConnection, store: SessionStore,
             round_row: sqlite3.Row, wave: sqlite3.Row) -> None:
    """#3916: בפס ייצור, משכפל שנכבה ועוד יש לו סבב (ב-`keep` של B24) מודלק ב-WoL. **ההחלטה של המוח**
    (`brain.line.relight`, נרשמת ב-`brain_decisions`): ב-`shadow` רק נרשם, ב-`enforce` נשלח. אין כאן כישלון
    של הסבב: מי שלא חזר גם אחרי ההדלקות נשאר ל-K-LOST ול-`_rewake`. לעולם אינו זורק אל הדופק."""
    try:
        roster, opened = _roster(round_row), _iso_or_none(wave["created_at"])
        if roster is None or opened is None or prodline.line_of(conn, round_row["id"]) is None:
            return
        joined = {m["mac"] for m in store.members(wave["id"])}
        off = {mac for mac in roster if mac not in joined and _not_back(conn, mac, opened)}
        if not off:
            return
        macs = brain_line.relight(conn, round_id=round_row["id"], wave_id=wave["id"], remaining=_remaining(round_row),
                                  cloners=_line_cloners(conn, round_row, roster), off=off)
        if macs:
            store.on_opened(CLONERS_GROUP, None, macs)
            journal(conn, "room_relight", f'{wave["id"]} {round_row["id"]} {", ".join(_machine_name(conn, m) for m in macs)}',
                    round_id=round_row["id"])
    except Exception:                            # noqa: BLE001 -- הסבב ממשיך גם כשהמוח נכשל
        log.exception("wave %s: relight failed", wave["id"])


def _brain_after_wave(conn: SqlConnection, round_row: sqlite3.Row, members: list[sqlite3.Row],
                      closed: int) -> None:
    """B33 ו-B50 (כל גל) ו-B24 (גל של פס ייצור), בצל: רושם בלבד; לעולם אינו זורק אל סיום הגל.
    ``closed`` = מה שהסבב כבר סגר אחרי הגל הזה (נכתב, אדום, בוטל, חריץ ריק)."""
    wave_id = round_row["wave_session_id"]
    slotfill.after_wave(conn, wave_id, round_row["id"])
    faultwho.after_wave(conn, wave_id, round_row["id"], members)   # B50: החריץ, הכבל או הדיסק
    roster = _roster(round_row)
    try:
        if roster is None or prodline.line_of(conn, round_row["id"]) is None:
            return
        brain_line.after_wave(conn, round_id=round_row["id"], wave_id=wave_id,
                              remaining=round_row["target_drives"] - closed, cloners=_line_cloners(conn, round_row, roster),
                              joined={m["mac"] for m in members})
    except Exception:                            # noqa: BLE001 -- הסבב ממשיך גם כשהמוח נכשל
        log.exception("wave %s: B24 failed", wave_id)


def _b38(conn: SqlConnection, round_row: sqlite3.Row) -> None:
    """B38 (צל): המטרה של הסבב, פעם אחת, כשהגל הראשון פתוח. לעולם אינו זורק אל הדופק."""
    try:
        if round_row["target_slots_json"] is None:
            return
        selected = json.loads(round_row["target_slots_json"])
        brain_line.decide_objective(conn, round_id=round_row["id"], target=round_row["target_drives"],
                                    drawers=prodline.drawers(conn, selected) or None, cloners=len(selected))
    except Exception:                            # noqa: BLE001
        log.exception("round %s: B38 failed", round_row["id"])


def _machine_name(conn: SqlConnection, mac: str) -> str:
    row = conn.execute("SELECT suffix FROM machines WHERE mac = ?", (mac,)).fetchone()
    return _room_label(row["suffix"]) if row else mac


def tick(conn: SqlConnection, store: SessionStore, joiner: str | None = None) -> None:
    """Advance on cloner hellos, never on screen reads.

    ``joiner`` is the cloner whose hello drives this pulse (``pulse``); only Scheduled-Cast (#3569) reads it.

    With no hello, an idle room waits. No timer or additional SQLite writer.

    #1947: every pulse first checks the server clock for a step and rebases
    the active rounds' stamps -- before any timer below reads them.
    """
    clockstep.guard(conn, store, _utcnow())
    from server.rounds import direct_settle           # noqa: PLC0415 -- #2985: imports direct, which imports room
    direct_settle.settle(conn, store, _utcnow())      # #2985: a closed direct round closes its source task too
    round_row = active_round(conn)
    if round_row is None:
        roundend.settle(conn, None)                   # #3276: K10's open decisions get what the old code did
        return
    wave = None
    if round_row["wave_session_id"]:
        wave = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (round_row["wave_session_id"],)
        ).fetchone()
    roundend.settle(conn, wave["id"] if wave is not None and wave["state"] == "open" else None)

    if wave is None or wave["state"] == "closed":
        # הסבב פעיל והגל שלו כבר אינו. בלי הענף הזה זה מצב **יציב**:
        # כל דופק עתידי נוסג כאן, החדר נראה פתוח ואף גל אינו נפתח (#217).
        _resume(conn, store, round_row, wave)
    elif wave["state"] == "failed":
        # גיבוי למעבר האטומי של callback המשדר: גם אם ה-session כבר
        # סומן מכיוון אחר, כשל מפורש לעולם אינו נתיב לגל אוטומטי נוסף.
        fail_wave(conn, wave["id"],
                  wave["failed_reason"] or "גל השידור נכשל")
    elif wave["state"] == "open":
        _k10(conn, store, round_row, wave)            # #3276 (K10, צל): רושם בלבד
        _b38(conn, round_row)                         # B38 (צל): המטרה של הסבב, פעם אחת
        # #1760 (ברזל D6c): מגירה שה-hello כבר אומר שהיא אדומה, אצל מי שהצטרף,
        # אינה טרייה — ולכן גם אינה מגירה שהגל ממתין לה. בלי זה גל של "אחת אדומה
        # ואחת תקינה" חיכה ל-2 טריות ולא יצא לעולם (44 דקות, room_3afb859d).
        remaining = _remaining(round_row) - red_drives(conn, store, round_row)
        ready = ready_drives(conn, store, round_row)
        # #715: מדיסק חי הגל ממתין גם למניפסט — מקבל שיוצא לדרך לפניו אין
        # לו מול מה לאמת, ו-`GET …/manifest` שלו היה נענה 404.
        from server.rounds import direct             # noqa: PLC0415
        # #3137: גל של מנוע החתיכות ממתין גם לאינדקס של האימג' — שנגזר עכשיו, בזמן שהמכונות עולות.
        # נשאל בכל דופק של גל פתוח, כדי שההכנה תתחיל מיד ולא רק כשהמגירות מוכנות.
        gate = getattr(store, "start_gate", None)
        held = gate(wave["id"], wave["image_id"]) if gate is not None else None
        if held is not None and held["state"] == "failed":
            if _fail_round(conn, store, round_row, held["error"]):
                journal(conn, "room_failed", f'{round_row["id"]} {wave["id"]} {held["error"]}',
                        round_id=round_row["id"])
            return
        start_now = 0 < ready and remaining <= ready and direct.source_ready(round_row) and held is None
        if start_now and scheduled_cast.hold(conn, store, wave, _utcnow(), joiner):
            start_now = False       # #3569: waiting for the second cloner -- the last `else` (rewake, relight) still runs
        if start_now:
            warned = precheck_now(conn, store, round_row)     # #432
            store.start_auto(wave["id"])
            journal_precheck(conn, round_row, warned, "")
            _wave_started(conn, store, round_row)          # #3277
        elif 0 < ready and remaining <= ready and held is not None:
            pass        # #3137: the drawers are ready and the image is not yet -- the view says how far
        elif ready == 0 and round_row["target_drives"] > 0 and _remaining(round_row) <= 0:
            # #2908: היעד הושג — `wake_check` (#1954) הוריד אותו לכמה שכבר נכתב. זו סגירה
            # מוצלחת ולא "כל המגירות אדומות": אדום מופחת מהיתרה רק למעלה, ב-`remaining`.
            if _close_target_met(conn, store, round_row):
                direct.cancel_source_task(conn, round_row, "")
        elif ready == 0 and remaining <= 0:
            # כל מה שהגל ממתין לו אדום: אין מה לכתוב — נסגר בגלוי, לא `open` לנצח.
            if _fail_round(conn, store, round_row, NOTHING_TO_WRITE_REASON):
                journal(conn, "room_failed",
                        f'{round_row["id"]} {wave["id"]} אין מגירות לכתוב — כל המגירות אדומות'
                        f' written={round_row["written_drives"]}/{round_row["target_drives"]}',
                        round_id=round_row["id"])
                direct.cancel_source_task(conn, round_row, "")
        else:
            _rewake(conn, store, round_row, wave)         # #1721
            _relight(conn, store, round_row, wave)        # #3916
    elif wave["state"] == "running":
        members = store.members(wave["id"])
        # #450: מכונה שנעלמה (כובתה, איבדה רשת, קרסה) לעולם אינה מדווחת
        # מצב סופי, ובלעדי הפקעה הגל ממתין לה לנצח וגל נוסף אינו נפתח.
        # מפקיעים אותה **לפני** בדיקת הסיום, ובודקים מול `reports.FINAL`
        # (הטרמינליים + `lost`).
        members = _mark_lost(conn, store, wave, members)
        from server.brain import tick as brain_tick      # noqa: PLC0415
        brain_tick.run(conn, wave["id"])                  # #3320 (K1, KT1): חיות בצל, לכל היותר פעם ב-10 ש'
        # `partial` הוא סיום לכל דבר (#67): מכונה שכתבה שתי מגירות מתוך
        # שלוש אמרה את דברה, והגל אינו ממתין לה עוד. הספירה שאחריו היא
        # ממילא לפי מגירה — המגירה שנכשלה פשוט לא נספרת ונשארת טרייה.
        if members and all(m["state"] in reports.FINAL for m in members):
            _finish_wave(conn, store, round_row, members)


#: תקרה לאורך פרט ביומן. הודעת חריגה יכולה לגרור traceback שלם, ושורת
#: יומן שאי אפשר לקרוא על המסך אינה מוסיפה למפעיל דבר.
_DETAIL_MAX = 200


def _one_line(value: object) -> str:
    """ערך כפי שמותר לכתוב אותו לשורת יומן או לוג: **שורה אחת**.

    **לא** מפני שהוא מזייף רשומה בטבלת היומן — הוא אינו יכול:
    `journal()` מעביר את `detail` כפרמטר קשור לעמודת TEXT, ושורה חדשה
    בתוכו נשארת שדה אחד. בקרה שלילית שהייתה נשענת על "רשומה שנייה
    בטבלה" **הייתה נכשלת להיכשל**, וזה בדיוק סוג השומר שנראה עובד.

    שתי הסיבות האמיתיות:

    1. `log.exception` הולך ליומן ה-systemd, שהוא **טקסטואלי**. שורה
       חדשה בהודעת חריגה מייצרת שם שורה שנראית עצמאית — כלומר רשומה
       מזויפת ביומן שהמפעיל קורא. זה אותו יומן שכבר נכווינו בו ב-#179.
    2. תקרת אורך: הודעת חריגה יכולה לגרור traceback שלם, ושדה שאי-אפשר
       לקרוא על המסך אינו מוסיף למפעיל דבר.

    `normalize_mac` מחזיר hex קנוני או `None`, ולכן מסלול ה-MAC אינו
    ניתן לניצול ממילא. **הווקטור החי היחיד הוא טקסט החריגה** — והשמירה
    יושבת בנקודת הכתיבה, לא בהנחה על מי שקורא.
    """
    text = str(value).replace("\r", " ").replace("\n", " ")
    return " ".join(text.split())[:_DETAIL_MAX]


def pulse(conn: SqlConnection, store: SessionStore, mac: str, joining: bool = True) -> bool:
    """הדופק של הסבב מתוך מסלול ה-hello — לעולם אינו מפיל אותו.

    אותה מוסכמה כמו `agent_loops.note`: הפונקציה לעולם אינה זורקת
    ולעולם אינה נוגעת בתשובה שנשלחת למכונה, ועיקרון 1 נשאר בדיוק כפי
    שהוא. hello שמחזיר 500 אומר למחשב השיכפול שהשרת מת, וזה שקר —
    השרת חי, רק קידום הסבב נכשל (#177).

    **אבל לא בשקט (עיקרון 5).** גל שלא נפתח הוא בדיוק מה שהמפעיל חייב
    לראות: הסבב נראה תקוע והחדר ממתין לאדם שיבחין. לכן הכישלון נרשם
    ביומן — שם הוא מגיע למסך — וגם ב-`log.exception` עם ה-traceback.
    `except: pass` היה הופך את הבאג לשקוף, וזה גרוע מ-500.

    מוחזר האם הדופק עבר, למי שכן רוצה לדעת.
    """
    who = _one_line(mac)
    try:
        if joining and scheduled_cast.enabled(conn):
            tick(conn, store, mac)       # #3569: at the deadline, the hello that is about to join joins the wave
        else:
            tick(conn, store)
        return True
    except Exception as exc:      # noqa: BLE001 — קידום הסבב לא מפיל hello
        log.exception("room tick from %s failed", who)
        try:
            journal(conn, "room_tick_failed", f"{who} — {_one_line(exc)}")
        except Exception:         # noqa: BLE001 — גם היומן לא מפיל hello
            log.exception("room tick failure from %s was not journaled", who)
        return False


def sweep(conn: SqlConnection, store: SessionStore) -> bool:
    """הדופק של הסבב מהרקע — **גל שאיש אינו צופה בו חייב להתקדם** (#456).

    `tick` מקודם היום משני מקורות בלבד: hello של משכפל (`pulse`) ו-GET
    `/overview`. שניהם תלויים במשהו חיצוני — מכונה ששולחת hello, או אדם
    שמסתכל על מבט-העל. כשכל המכונות נעלמו (כובו, נותקו, קרסו) **אין יותר
    hello**, ומסך החדר מושך `/api/console/room` שהוא read-only (#446,
    #453) — ולכן `_mark_lost` לא נקרא, אף חבר לא מוכרז אבוד, והגל תקוע
    לנצח (#456, וזה גם המקרה שנשאר פתוח ב-#659 כשהמכונה **האחרונה**
    נעלמת). לולאת הרקע (`app._room_clock`) קוראת לכאן בקצב קבוע, ולכן
    ההפקעה אינה תלויה עוד בצופה או ב-hello של שורד.

    אותה מוסכמה כמו `pulse`: לעולם אינה זורקת. הרקע אינו יכול להחזיר 500
    לאיש, אבל כישלון בקידום הסבב עדיין חייב להיראות — לכן `log.exception`
    עם ה-traceback ושורת יומן, ולא `except: pass` שמעלים את הבאג (עיקרון
    5). מוחזר האם הדופק עבר, למי שכן רוצה לדעת (הבדיקה).
    """
    try:
        tick(conn, store)
        return True
    except Exception as exc:      # noqa: BLE001 — קידום הסבב לא מפיל את לולאת הרקע
        log.exception("room background tick failed")
        try:
            journal(conn, "room_tick_failed", f"background — {_one_line(exc)}")
        except Exception:         # noqa: BLE001 — גם היומן לא מפיל את הלולאה
            log.exception("room background tick failure was not journaled")
        return False


def rejoined_mid_stream(conn: SqlConnection, session: sqlite3.Row, mac: str) -> bool:
    """#1728: hello **מצטרף** של חבר בגל `running` שכבר נכנס לזרם — אתחול.

    ברזל rc5 (18/10): `reboot -f` במשכפל באמצע מחיצה 3; הוא חזר תוך 27
    שניות, קיבל את אותו גל רץ, ניסה לכתוב את מחיצה 1 מזרם שכבר במחיצה 3
    (`fanout: empty stream`), ושני דיסקים תקינים נרשמו `disk_failure`.

    הראיה היא מצב החבר: הסוכן מתחיל לשחזר רק כשה-hello אמר `running`, ומאז
    הוא מדווח progress ואינו שולח hello מצטרף עד שהעבודה נגמרה (הדופק של
    הגואי הוא `joining: false`). חבר שעוד `waiting` הוא ה-hello הראשון אחרי
    שהגל יצא — כך הסוכן לומד זאת — והוא מקבל את הגל כמו קודם.

    חבר שכבר אמר את דברו (`failed`/`partial`) פשוט אינו מקבל את הגל שוב.
    חבר שהיה באמצע מסומן `lost` (UPDATE מותנה, כמו `_mark_lost`), והיומן
    אומר `room_rejoin` פעם אחת — זו הראיה ש-`_revived` קורא בסוף הגל.
    מוחזר האם ה-hello הזה **אינו** מקבל את הגל."""
    row = conn.execute(
        "SELECT state, updated_at FROM session_members WHERE session_id = ? AND mac = ?",
        (session["id"], mac),
    ).fetchone()
    if row is None:
        return False
    from server.rounds import interrupt              # noqa: PLC0415 -- interrupt imports room
    # #2528 (סקירה 1-א): חבר `waiting` שקיבל את הגל באתחול **אחר** אותחל בין קבלת הגל לדיווח
    # הראשון שלו — הוא כבר יכול היה להתחיל לכתוב, ואינו מקבל את הגל שוב. אותו אתחול, או
    # בלי יומן — ה-hello הראשון אחרי שהגל יצא, כמו קודם.
    if row["state"] == "waiting" and not interrupt.other_boot(conn, session["id"], mac):
        return False
    if row["state"] in reports.TERMINAL:
        return True
    if row["state"] != reports.LOST:
        # #2528: מזהה אתחול אחר מזה שקיבל את הגל = המכונה כבתה או אותחלה — המפעיל נשאל,
        # והגל הבא אינו נפתח מעצמו (`interrupt.settle`). בלי הראיה הזו — כמו קודם.
        # סקירה 1-ז: בדיקה שלא רצה אינה "לא אותחל" — החבר אינו מסומן `lost` (הגל אינו
        # יכול להסתיים ולפתוח גל נוסף בלי שאלה), אינו מקבל את הגל, וה-hello הבא שואל שוב.
        if not interrupt.checked(conn, session, mac, row):
            return True
        update_one(
            conn,
            "UPDATE session_members SET state = ?, updated_at = ?"
            " WHERE session_id = ? AND mac = ? AND state = ?",
            (reports.LOST, now_iso(), session["id"], mac, row["state"]),
        )
    else:
        # #3064 שלב 5: חבר שהמוח כבר קבע עליו LOST חזר (אותחל) באותו סבב -- תצפית לזיכרון. לעולם אינו זורק
        from server.brain import recall                  # noqa: PLC0415
        recall.returned(conn, mac, session["id"])
    detail = f'{mac} in {session["id"]}'
    if conn.execute(
        "SELECT 1 FROM journal WHERE event = 'room_rejoin' AND detail = ?", (detail,),
    ).fetchone() is None:
        journal(conn, "room_rejoin", detail, machine=mac, round_id=session["id"])
    return True


def _revived(conn: SqlConnection, wave_id: str, members: list[sqlite3.Row]) -> bool:
    """#1728: האם חבר `lost` בגל הזה חזר וחי — אתחול באמצע הזרם (`room_rejoin`)
    ו-hello בחלון `AWAKE_SECONDS`. גל כזה שלא כתב דבר אינו "גל מת" (#1672):
    המשכפל מוכן לגל הבא. חבר שנעלם ולא חזר — אין ראיה, והסבב נכשל כמו קודם."""
    for m in members:
        if m["state"] != reports.LOST or conn.execute(
            "SELECT 1 FROM journal WHERE event = 'room_rejoin' AND detail = ?",
            (f'{m["mac"]} in {wave_id}',),
        ).fetchone() is None:
            continue
        seen = conn.execute(
            "SELECT last_seen FROM net_devices WHERE mac = ?", (m["mac"],)
        ).fetchone()
        if seen is not None and _is_awake(seen["last_seen"]):
            return True
    return False


def _journal_drawer_changed(conn: SqlConnection, round_id: str,
                            member: sqlite3.Row, dev: str | None,
                            recorded: str | None, current: str | None,
                            event: str = "room_drawer_changed") -> None:
    row = conn.execute(
        "SELECT suffix FROM machines WHERE mac = ?", (member["mac"],)
    ).fetchone()
    name = _room_label(row["suffix"]) if row else member["mac"]
    detail = (
        f"{round_id} {name} {dev or '?'} serial="
        f"{recorded or 'missing'} current={current or 'missing'}"
    )
    if conn.execute(
        "SELECT 1 FROM journal WHERE event = ? AND detail = ?",
        (event, detail),
    ).fetchone() is None:
        journal(conn, event, detail, machine=member["mac"], round_id=round_id)


def _held_elsewhere(conn: SqlConnection, round_row: sqlite3.Row,
                    member: sqlite3.Row, serial: str) -> str | None:
    """#2421: מכונה **אחרת** שמחזיקה את הסידורי הזה — או None.

    הסבב סוגר מגירות לפי סידורי בלבד (`written_serials`), בכוונה: מגירה שנכתבה
    ונדדה למשכפל אחר באותו סבב אינה נכתבת שוב. אבל כשמכונה אחרת מחזיקה את אותו
    סידורי — שני כוננים עם סידורי זהה מהמפעל, או תחנה שהכריזה ב-hello שלה על
    הסידורי של מגירה במכונה אחרת — הדיווח של אחת היה סוגר את המגירה של השנייה,
    והיא הייתה מוצגת "נכתב" בלי שנכתב בה בייט (עיקרון 4). "מחזיקה" = במלאי של
    ה-hello האחרון שלה, ואחד משניים: היא דיברה איתנו מאז שהגל התחיל, **או**
    שהסידורי יושב בחריץ שנבחר לה בסבב הזה (גם כשהיא כבויה — אחרת די היה לחכות
    שהמשכפל יכבה). מגירה שעברה ממכונה כבויה שלא נבחרה בה אינה עוצרת דבר; מכונה
    שנבחרה ועוד לא התעוררה מתעדכנת ב-hello הבא שלה, ועד אז הכתיבה לא נספרת."""
    wave = conn.execute("SELECT started_at, created_at FROM sessions WHERE id = ?",
                        (member["session_id"],)).fetchone()
    since = (wave["started_at"] or wave["created_at"]) if wave is not None else None
    chosen = {item["mac"]: set(item.get("ports") or [])
              for item in json.loads(round_row["target_slots_json"] or "[]")
              if isinstance(item, dict) and item.get("mac") != member["mac"]}
    for row in conn.execute(
            "SELECT mac, disks_json, last_seen FROM net_devices WHERE mac != ?"
            " AND instr(disks_json, ?) > 0", (member["mac"], serial)):
        try:
            disks = json.loads(row["disks_json"])
        except (TypeError, ValueError):
            unreadable.said("net_devices.disks_json", row["disks_json"])    # #3129: a corrupt row is said
            continue
        held = [d for d in disks if isinstance(d, dict) and d.get("serial") == serial]             if isinstance(disks, list) else []
        if not held:
            continue
        if since and row["last_seen"] and row["last_seen"] >= since:
            return row["mac"]
        if any(d.get("port") in chosen.get(row["mac"], ()) for d in held):
            return row["mac"]
    return None


def _journal_serial_conflict(conn: SqlConnection, round_id: str, member: sqlite3.Row,
                             dev: str | None, serial: str, other: str) -> None:
    detail = f"{round_id} {member['mac']} {dev or '?'} serial={serial} also on {other}"
    if conn.execute("SELECT 1 FROM journal WHERE event = ? AND detail = ?",
                    ("room_drawer_serial_conflict", detail)).fetchone() is None:
        journal(conn, "room_drawer_serial_conflict", detail, machine=member["mac"],
                round_id=round_id)


def _own_serial(target: dict, serial_of: dict) -> str | None:
    """#2421: הסידורי של יעד `skipped`/`cancelled` — זה שבמלאי של **המכונה
    המדווחת** באותו `dev`. עד כאן נלקח הסידורי שבדיווח, ותחנה סגרה (כ"בוטל" או
    "דולג") מגירה של מכונה אחרת לפי הסידורי שלה. סידורי בדיווח שאינו זה שבמלאי
    — None: לא נסגר כלום, והמגירה נשארת טרייה לגל הבא (`done` נקשר כך כבר ב-
    `reports.stamp_done_serials`)."""
    current = serial_of.get(target.get("dev"))
    reported = target.get("serial")
    if reported and reported != current:
        return None
    return current or None


def _tally(conn: SqlConnection, round_row: sqlite3.Row,
           members: list[sqlite3.Row]) -> tuple[int, set[str], set[str], set[str]]:
    """כמה מגירות נכתבו בסבב עד כה, ואילו — לפי serial; ואילו נסגרו בלי שנכתבו:
    דולגו כאדומות (#1760) או בוטלו בידי מפעיל (#1936).

    **הספירה חוזרת על עצמה בבטחה:** מגירה שכבר בקבוצה אינה נספרת שוב,
    ולכן ספירה חוזרת של אותו גל (במסלול השחזור של `_resume`) מחזירה
    בדיוק את אותו מספר. זה מה שמאפשר לשחזר בלי לדעת אם הספירה הקודמת
    הספיקה להיכתב.
    """
    written = _written(round_row)
    skipped = _skipped(round_row)
    cancelled = _cancelled(round_row)
    new_drives = 0
    for member in members:
        disks = [d for d in _disks(conn, member["mac"]) if isinstance(d, dict)]
        serial_of = {d.get("dev"): d.get("serial") for d in disks}
        for target in json.loads(member["targets_json"] or "[]"):
            if not isinstance(target, dict):
                continue
            state = target.get("state")
            pre = target.get("write_preflight")
            slow = state == "failed" and isinstance(pre, dict) and pre.get("state") == "slow"
            if state == "skipped" or slow:
                # #1760: דולגה כאדומה — סגורה בסבב, לא נכתבה. דילוג כתום (המפעיל
                # בחר "דלג") אינו נסגר כאן: הוא נשאר טרי לגל הבא, כמו קודם.
                serial = _own_serial(target, serial_of)          # #2421
                if serial and serial not in written and (slow or _red_skip(
                        conn, member, target.get("dev"), serial, disks)):
                    other = _held_elsewhere(conn, round_row, member, serial)
                    if other is None:
                        skipped.add(serial)
                    else:
                        _journal_serial_conflict(conn, round_row["id"], member,
                                                 target.get("dev"), serial, other)
                continue
            if target.get("state") == "cancelled":
                # #1936: הסוכן דיווח שהמגירה בוטלה (#1386 — הראיה, `drawer_cancel`).
                # ביטול של מפעיל סופי לסבב: סגורה, לא נכתבה, ואינה פותחת גל נוסף.
                serial = _own_serial(target, serial_of)          # #2421
                if serial and serial not in written:
                    other = _held_elsewhere(conn, round_row, member, serial)
                    if other is None:
                        cancelled.add(serial)
                    else:
                        _journal_serial_conflict(conn, round_row["id"], member,
                                                 target.get("dev"), serial, other)
                continue
            if target.get("state") != "done":
                continue
            dev = target.get("dev")
            recorded = target.get("serial")
            current = serial_of.get(dev)
            # #1126 item 4 binds a completed target to the serial observed at
            # completion when that binding is available.  Rows written by an
            # older server have no such field; absence is not evidence that a
            # drawer was swapped, so retain their historical behaviour and
            # count the serial currently observed in that device.  A present
            # binding remains authoritative and must match exactly.
            # #3139: the drawer that came back under another name (sda -> sdc) is the same disk -- the
            # serial it was completed with is still in the machine's inventory, under the new name
            if recorded and recorded != current and recorded not in serial_of.values():
                _journal_drawer_changed(
                    conn, round_row["id"], member, dev, recorded, current,
                )
                continue
            serial = recorded or current
            if not serial:
                # #105: לא הוחלפה — מעולם לא הייתה לה זהות לספור. לא נספרת
                # (ערך מומצא ב-`written_serials` היה מדלג על כונן ריק).
                _journal_drawer_changed(
                    conn, round_row["id"], member, dev, recorded, current,
                    event="room_drawer_unidentified",
                )
                continue
            if serial not in written:
                other = _held_elsewhere(conn, round_row, member, serial)          # #2421
                if other is not None:
                    _journal_serial_conflict(conn, round_row["id"], member, dev, serial, other)
                    continue
                written.add(serial)
                new_drives += 1
    # #3181: בפס ייצור דיסק שנכתב והוצא באמצע הגל כבר אינו במלאי, ולפעמים גם לא בדיווח האחרון --
    # הוא נספר לפי פס הייצור, שראה אותו מגיע ל-done. פעם אחת לכל serial, כמו כל השאר.
    for serial in prodline.written_serials(conn, round_row["id"]) - written - skipped - cancelled:
        written.add(serial)
        new_drives += 1
    return (round_row["written_drives"] + new_drives, written, skipped - written,
            cancelled - written)


def _reported_no_disk(conn: SqlConnection, round_row: sqlite3.Row,
                      member: sqlite3.Row) -> set[int]:
    """#2201: החריצים שהמשכפל קיבל לגל הזה (`target_ports`) ואין להם יעד בדיווח שלו.

    מאז #2312 הסוכן כותב את המגירות שיש, ועל חריץ שנבחר ואין בו דיסק — רק רושם
    ביומן; יעד (`target_init`) יש לכל מגירה שהוא כתב או דילג עליה. ולכן דיווח **עם**
    יעדים, שכל אחד מהם ממוקם בחריץ, הוא הראיה: חריץ שנשלח ואין עליו יעד — אין בו דיסק.
    **אין הכרעה** (עיקרון 5) כשאין יעדים בכלל (סוכן ישן שעצר על המגירה החסרה, מכונה
    שאבדה לפני הדיווח הראשון), או כשיעד אחד אינו ממוקם — `port` בדיווח, ובלעדיו לפי
    ה-`dev` במלאי של ה-hello: בלי לדעת מה נכתב, אי-אפשר לדעת מה חסר."""
    targets = [t for t in json.loads(member["targets_json"] or "[]") if isinstance(t, dict)]
    if not targets:
        return set()
    disks = [d for d in _disks(conn, member["mac"]) if isinstance(d, dict)]
    port_of = {d.get("dev"): _port(d.get("port")) for d in disks if d.get("dev")}
    covered = set()
    for target in targets:
        port = _port(target.get("port"))
        if port is None:
            port = port_of.get(target.get("dev"))
        if port is None:
            return set()
        covered.add(port)
    sent = target_ports(round_row, member["mac"], disks)
    return set(sent or []) - covered


def _close_no_disk(conn: SqlConnection, round_row: sqlite3.Row,
                   members: list[sqlite3.Row]) -> tuple[str | None, list[tuple[str, int]]]:
    """#2201 (הכרעה 03/11): חריץ שנבחר ואין בו דיסק **נסגר לסבב** כמו אדום (#1760) או
    מבוטל (#1936) — יוצא מ-`ports` (לא נשלח שוב, הגל הבא אינו ממתין לו) ונרשם
    ב-`no_disk` של הפריט, והיעד **אינו** יורד: הוא נספר כנכשל (`written=1/2`), לא
    כ"דולג" כמו `wake_skipped` (#1954). מחזיר את `target_slots_json` אחרי הסגירה ואת
    החריצים שנסגרו עכשיו. חוזר על עצמו בבטחה: חריץ שכבר יצא מ-`ports` אינו נשלח עוד."""
    raw = round_row["target_slots_json"]
    if raw is None:
        return raw, []
    slots = json.loads(raw)
    closed: list[tuple[str, int]] = []
    for member in members:
        item = next((x for x in slots if x.get("mac") == member["mac"]), None)
        gone = _reported_no_disk(conn, round_row, member) & set(item["ports"] if item else [])
        if not gone:
            continue
        item["ports"] = [p for p in item["ports"] if p not in gone]
        item["no_disk"] = sorted(set(item.get("no_disk") or []) | gone)
        closed += [(member["mac"], p) for p in sorted(gone)]
    return (json.dumps(slots) if closed else raw), closed


def _slots_claim(round_row: sqlite3.Row, slots_raw: str | None) -> tuple[str, tuple, str, tuple]:
    """#2201: סגירת החריצים נכתבת באותה תביעה כמו הספירה — ורק על ה-`target_slots_json`
    שנקרא (`wake_check`, #1954, כותב אותו גם). בלי סגירה — העדכון כמו קודם."""
    if slots_raw == round_row["target_slots_json"]:
        return "", (), "", ()
    return (", target_slots_json = ?", (slots_raw,),
            " AND target_slots_json = ?", (round_row["target_slots_json"],))


def _journal_no_disk(conn: SqlConnection, round_row: sqlite3.Row,
                     closed: list[tuple[str, int]]) -> None:
    for mac, port in closed:
        journal(conn, "room_drawer_no_disk",
                f'{round_row["id"]} {_machine_name(conn, mac)} port={port}',
                machine=mac, round_id=round_row["id"])


def _clear_written_failures(conn: SqlConnection, round_row: sqlite3.Row,
                            written: set[str]) -> None:
    """#1792: מה שהגל הזה הוסיף ל-`written_serials` סוגר את רשומת הכשל הכתומה שלו.
    לפני התביעה (סגירת הגל / עדכון הסבב) ולא אחריה: הניקוי חוזר על עצמו בבטחה,
    ואם הוא נכשל — הדופק נרשם `room_tick_failed` והגל לא נסגר, והדופק הבא מנסה
    שוב עם אותה ספירה. אחרי התביעה כשל היה נבלע: הסבב כבר סופר את הדיסק."""
    disk_failures.clear_written(conn, written - _written(round_row), round_row["id"])


def _skipped_note(skipped: set[str], cancelled: set[str], no_disk: int = 0) -> str:
    """#1760: שורת היומן אומרת כמה דולגו כאדומות — "1/2" לבדו נקרא כמו כשל.
    #1936: וכמה בוטלו בידי מפעיל — `written=1/2 cancelled=1`.
    #2201: וכמה חריצים נבחרו ולא היה בהם דיסק — `written=1/2 no_disk=1`."""
    return ((f" skipped_red={len(skipped)}" if skipped else "")
            + (f" cancelled={len(cancelled)}" if cancelled else "")
            + (f" no_disk={no_disk}" if no_disk else ""))


def _red_skip(conn: SqlConnection, member: sqlite3.Row, dev: str | None,
              serial: str, disks: list[dict]) -> bool:
    """#1760: היעד דולג **כאדום** — ראיה חיובית אחת משתיים: ה-disk_event של הגל
    על הדיסק הזה אומר `verdict: fail` עם `decision: skip` (smart_preflight), או
    שה-hello האחרון אומר שהדיסק בעל הסידורי הזה אדום. דילוג בלי אחת מהן (המפעיל
    דילג על כתום) אינו אדום."""
    row = conn.execute(
        "SELECT verdict, decision FROM disk_events WHERE session_id = ? AND mac = ?"
        " AND disk = ?", (member["session_id"], member["mac"], dev),
    ).fetchone() if dev else None
    if row is not None and row["verdict"] == "fail" and row["decision"] == "skip":
        return True
    return any(d.get("serial") == serial and _red(d) for d in disks)


def _resume(conn: SqlConnection, store: SessionStore,
            round_row: sqlite3.Row, wave: sqlite3.Row | None) -> None:
    """הסבב פעיל, והגל שהוא מצביע עליו סגור (או שאין כזה) — #217.

    שלוש דרכים נמדדות להגיע לכאן: סגירת הגל מ-endpoint הסבבים הכללי של
    הקונסולה (בלי שום תקלה — `close_session` אינו יודע שזה גל של חדר),
    וכשל בכתיבת `room_rounds` אחרי ש-`store.close` או `store.open` כבר
    בוצע להם commit — שני מסלולי הנסיגה של `_finish_wave`.

    **התביעה כאן היא איפוס המצביע**, בדיוק כמו שהסגירה המותנית היא
    התביעה ב-#177: `UPDATE` מותנה שרק תהליכון אחד מנצח בו, ומי שהפסיד
    רואה `NULL` וכבר אינו במצב הזה. איפוס לפני הפתיחה ולא אחריה, כדי
    שכשל **בפתיחה** ישאיר מצב שהדופק הבא יודע לתקן — ולא סבב שמצביע על
    גל שאינו קיים.
    """
    total, written = round_row["written_drives"], _written(round_row)
    skipped, cancelled = _skipped(round_row), _cancelled(round_row)
    no_disk = _no_disk_count(round_row)
    members: list[sqlite3.Row] = []
    if wave is not None:
        members = store.members(wave["id"])
        # #447, #3164, #2345, #2028: גם גל שנסגר מבחוץ נספר. #2817: דרך העטיפה — מתעד שזרק לא
        # משאיר את הסבב תקוע על `_resume` שנכשל בכל דופק. `learn_ports` לא נרשם כאן (ראו `_finish_wave`).
        _record_wave_stats(conn, wave["id"], members, ports=False)
        total, written, skipped, cancelled = _tally(conn, round_row, members)
        slots_raw, closed = _close_no_disk(conn, round_row, members)   # #2201
        no_disk += len(closed)
        set_sql, set_args, where_sql, where_args = _slots_claim(round_row, slots_raw)
        _clear_written_failures(conn, round_row, written)          # #1792
        if not update_one(
            conn,
            "UPDATE room_rounds SET written_drives = ?, written_serials = ?,"
            " skipped_serials = ?, cancelled_serials = ?" + set_sql + ", wave_session_id = NULL"
            " WHERE id = ? AND state = 'active' AND wave_session_id = ?" + where_sql,
            (total, json.dumps(sorted(written)), json.dumps(sorted(skipped)),
             json.dumps(sorted(cancelled)), *set_args, round_row["id"], wave["id"], *where_args),
        ):
            return
        conn.commit()
        _journal_no_disk(conn, round_row, closed)

    # #2528: שאלה פתוחה על מכונה שכבתה באמצע — אין גל נוסף ואין סגירה בלי תשובה.
    from server.rounds import interrupt              # noqa: PLC0415 -- interrupt imports room
    if interrupt.settle(conn, store, round_row, total):
        return

    target = round_row["target_drives"]
    # #715: סבב מדיסק חי הוא סבב **יחיד** — הגל שנגמר הוא הסבב, גם כשמגירה
    # נכשלה; אין גל נוסף לפתוח בלי שמחשב הבנייה ישדר שוב, וזו החלטה של אדם.
    from server.rounds import direct                 # noqa: PLC0415
    if (total + len(skipped) + len(cancelled) + no_disk >= target
            or direct.is_build_disk(round_row)):
        # #2907: מדיסק חי, גל שנסגר מתחת לסבב (עלייה מחדש של השרת: חבריו `failed`)
        # בלי שאף מגירה נכתבה — הסבב נכשל בשמו, לא `closed` 0/N בלי סיבה.
        why = ((_lost_before_result(conn, members, total, target)
                or _failed_before_write(conn, members, total, target, cancelled))
               if direct.is_build_disk(round_row) else None)
        if why:
            conn.execute(
                "UPDATE room_rounds SET state = 'failed', failed_reason = ?, closed_at = ?"
                " WHERE id = ? AND state = 'active'",
                (why, now_iso(), round_row["id"]),
            )
            conn.commit()
            journal(conn, "room_failed", f'{round_row["id"]} {why} written={total}/{target}',
                    round_id=round_row["id"])
            direct.cancel_source_task(conn, round_row, "")
            return
        conn.execute(
            "UPDATE room_rounds SET state = 'closed', closed_at = ? WHERE id = ?",
            (now_iso(), round_row["id"]),
        )
        conn.commit()
        journal(conn, "room_done", f'{round_row["id"]} written={total}/{target}'
                + _skipped_note(skipped, cancelled, no_disk), round_id=round_row["id"])
        direct.cancel_source_task(conn, round_row, "")
        return
    if wave is not None:
        # לא בשקט (עיקרון 5): גל שנעלם מתחת לסבב הוא בדיוק מה שהמפעיל
        # צריך לראות, גם כשהחדר מתאושש לבדו. שורה אחת, כי התביעה למעלה
        # מצליחה פעם אחת.
        journal(conn, "room_wave_lost",
                f'{round_row["id"]} {wave["id"]} written={total}/{target}',
                round_id=round_row["id"])
    _attach_wave(conn, store, round_row, total)


def _attach_wave(conn: SqlConnection, store: SessionStore,
                 round_row: sqlite3.Row, total: int) -> None:
    """מצמיד לסבב גל — קיים או חדש.

    **קודם מאמצים.** גל שנפתח ואיש לא הספיק להצביע עליו כבר מחזיק את
    חריץ המולטיקאסט היחיד, ופתיחת גל נוסף הייתה נכשלת ב-`TAKEN` בכל
    דופק מכאן והלאה. הזיהוי הוא קבוצה + קידומת + אימג' — חתימה שסבב
    כיתה אינו נושא.
    """
    existing = store.active_for_group(CLONERS_GROUP)
    opened_here = False
    if (existing is not None and existing["prefix"] == "ROOM"
            and existing["image_id"] == round_row["image_id"]):
        wave_id = existing["id"]
    else:
        machines = conn.execute(
            f"SELECT COUNT(*) AS n FROM machines WHERE {_IN_ROOM}"
        ).fetchone()["n"]
        brain_services.gate(conn, point="attach_wave", round_id=round_row["id"])      # K13 (צל)
        if cpucap.gate(conn, point="attach_wave", round_id=round_row["id"],
                       live=len(store.active_pulls())).hold:      # K12: רק `brain_cpu_gate` דלוק; הדופק הבא ינסה שוב
            return
        try:
            wave_id = store.open(
                CLONERS_GROUP, round_row["image_id"], prefix="ROOM",
                expected_clients=_expected(round_row, machines), opened_by="",
                expand_partition=round_row["expand_partition"],
                roster=_roster(round_row),
            )
        except SessionError:
            # החריץ תפוס בידי סבב אחר. **לא נבלע**: המצב כבר ביומן
            # (`room_wave_lost`) וגם על המסך (`wave_state: closed`),
            # והדופק הבא ינסה שוב. חריגה כאן הייתה מייצרת
            # `room_tick_failed` בכל hello — רעש שמכסה על השורה שכן
            # אומרת משהו.
            return
        opened_here = True
    if not update_one(
        conn,
        "UPDATE room_rounds SET wave_session_id = ?, wave_number = wave_number + 1"
        " WHERE id = ? AND state = 'active' AND wave_session_id IS NULL",
        (wave_id, round_row["id"]),
    ):
        # תהליכון אחר הקדים אותנו. גל שאיש אינו מצביע עליו יחזיק את החריץ
        # לנצח, ולכן סוגרים את מה ש**אנחנו** פתחנו — אבל רק אחרי שנבדק
        # שהסבב אינו מצביע עליו: המנצח יכול היה לאמץ בדיוק את הגל הזה
        # (זה הענף הראשון כאן), וסגירתו הייתה מחזירה את החדר למצב שממנו
        # באנו. שאילתה, ולא הנחה — הראיה החיובית היא שהמצביע אינו שלנו.
        if opened_here and conn.execute(
            "SELECT 1 FROM room_rounds WHERE id = ? AND wave_session_id = ?",
            (round_row["id"], wave_id),
        ).fetchone() is None:
            store.close(wave_id, "")
        return
    conn.commit()
    journal(conn, "room_wave",
            f'{round_row["id"]} wave={round_row["wave_number"] + 1}'
            f' written={total}/{round_row["target_drives"]}', round_id=round_row["id"])


#: #1760 — הסיבה כשכל המגירות שהגל ממתין להן אדומות (SMART נכשל / קריאה בלבד).
NOTHING_TO_WRITE_REASON = ("אין מגירות לכתוב — כל המגירות שנבחרו אדומות (SMART נכשל"
                           " או קריאה בלבד), ועל אדום לא כותבים")

#: נדב 17/10 — הסיבה על שורת הסבב כשגל הסתיים בלי שאף מגירה נכתבה בו.
DEAD_WAVE_REASON = "אף מגירה לא נכתבה בגל — הזרם נעצר, או שכל המחשבים נכשלו או נותקו"


def _fail_dead_wave(conn: SqlConnection, store: SessionStore,
                    round_row: sqlite3.Row, total: int) -> None:
    """גל שכל יעדיו נכשלו או נותקו מסיים את הסבב כ-`failed` (נדב, ברזל 17/10).

    עד כאן נפתח גל נוסף "שממתין למגירות מוחלפות" — אבל מגירה שנכשלה נשארת
    טרייה, ומשכפל שנותק (או נכבה אחרי הכשל, #1560) לא יחזור לבדו: הסבב
    נשאר `active` וחריץ המולטיקאסט תפוס, בלי שאיש יבחין (`room_72308383`).
    גל שכתב **משהו** ממשיך כמו קודם. הסגירה היא התביעה, כמו ב-`room_done`."""
    if not _fail_round(conn, store, round_row, DEAD_WAVE_REASON):
        return
    journal(conn, "room_failed",
            f'{round_row["id"]} {round_row["wave_session_id"]}'
            f' written={total}/{round_row["target_drives"]}', round_id=round_row["id"])


def _fail_round(conn: SqlConnection, store: SessionStore,
                round_row: sqlite3.Row, reason: str) -> bool:
    """סוגר את הגל ומסמן את הסבב `failed` עם `reason`; מחזיר האם הקריאה הזו
    היא שהפילה אותו (הסגירה היא התביעה, #177) — ורק היא כותבת ליומן."""
    if not store.close(round_row["wave_session_id"], ""):
        return False
    with writing(conn):                  # #54: a failed commit leaves no orphan lock
        return update_one(
            conn,
            "UPDATE room_rounds SET state = 'failed', failed_reason = ?, closed_at = ?"
            " WHERE id = ? AND state = 'active' AND wave_session_id = ?",
            (reason, now_iso(), round_row["id"], round_row["wave_session_id"]),
        )


def _close_target_met(conn: SqlConnection, store: SessionStore,
                      round_row: sqlite3.Row) -> bool:
    """#2908: הגל הפתוח ממתין למגירה שהסבב כבר אינו צריך — היעד ירד (`wake_check`, #1954:
    הדיסק בחריץ הוחלף) לכמה שכבר נכתב. הסבב נסגר `room_done`, לא `room_failed`: הסגירה היא
    התביעה (#177), והיומן אומר כמה חריצים דולגו בהעירה (`wake_skipped=N`). מחזיר האם
    הקריאה הזו היא שסגרה."""
    if not store.close(round_row["wave_session_id"], ""):
        return False
    with writing(conn):                  # #54: a failed commit leaves no orphan lock
        closed = update_one(
            conn,
            "UPDATE room_rounds SET state = 'closed', closed_at = ?"
            " WHERE id = ? AND state = 'active' AND wave_session_id = ?",
            (now_iso(), round_row["id"], round_row["wave_session_id"]),
        )
    if closed:
        woken = sum(len(item.get("wake_skipped") or [])
                    for item in json.loads(round_row["target_slots_json"] or "[]"))
        journal(conn, "room_done",
                f'{round_row["id"]} written={round_row["written_drives"]}/{round_row["target_drives"]}'
                + _skipped_note(_skipped(round_row), _cancelled(round_row), _no_disk_count(round_row))
                + (f" wake_skipped={woken}" if woken else ""), round_id=round_row["id"])
    return closed


def _record_wave_stats(conn: SqlConnection, session_id: str,
                       members: list[sqlite3.Row], *, ports: bool = True) -> None:
    """מה שהגל לימד, לפני הספירה. #2817: "לעולם אינו זורק" נאכף **כאן**, לא רק במוסכמה
    של כל מתעד — חריגה באחד מהם הייתה משאירה את הגל פתוח ואת הסבב תקוע. כל מתעד נקרא
    לחוד: כשל באחד נרשם ביומן התהליך, והבאים אחריו והספירה ממשיכים."""
    recorders = (
        # #447: מה שכל מגירה כתבה. חוזר על עצמו בבטחה.
        ("drive_rates", lambda: drive_rates.record_wave(conn, session_id, members)),
        # #3164: אבדן ברשת ההפצה בגל חתיכות -- רק אחרי ש-`drops` ו-`delivered` נרשמו. קפיצה = שורת יומן.
        ("learn_netloss", lambda: learn_netloss.evaluate_chunk(conn, session_id)),
        # #1844: מה שנמדד בכל חיבור SATA — "כנראה כבל" או "הדיסק".
        ("learn_ports", lambda: learn_ports.record_wave(conn, session_id, members)),
        # #1845: ה-MB/s של בדיקת הכתיבה לכל דיסק — הבסיס של "איטי מהרגיל לדגם".
        ("learn_models", lambda: learn_models.record_wave(conn, session_id, members)),
        # #2028: סבב למידה פתוח — הגל נספר, ואחרי השני מותאמות ההגדרות.
        ("learn_tuning", lambda: learn_tuning.record_wave(conn, session_id)),
    )
    for name, record in recorders:
        if name == "learn_ports" and not ports:
            continue
        try:
            record()
        except Exception:                        # noqa: BLE001 — הזיכרון אינו עוצר את סיום הגל
            log.exception("wave %s: %s.record_wave failed; the wave still closes (#2817)",
                          session_id, name)


def _finish_wave(conn: SqlConnection, store: SessionStore,
                 round_row: sqlite3.Row, members: list[sqlite3.Row]) -> None:
    """הגל הסתיים: סופרים לפי serial אילו מגירות נכתבו, וממשיכים."""
    current = conn.execute(
        "SELECT state, wave_session_id FROM room_rounds WHERE id = ?",
        (round_row["id"],),
    ).fetchone()
    if (current is None or current["state"] != "active"
            or current["wave_session_id"] != round_row["wave_session_id"]):
        return
    # #3277 (R3): ה-ETA בפועל מול מה שהמוח חזה כשהגל יצא. לעולם אינו זורק.
    began = conn.execute("SELECT started_at FROM sessions WHERE id = ?",
                         (round_row["wave_session_id"],)).fetchone()
    linkwarn.finished(conn, round_row["wave_session_id"], began["started_at"] if began else None)
    timeforecast.finished(conn, round_row["wave_session_id"], began["started_at"] if began else None)   # #3469
    # #2528: מכונה בגל הזה כבתה או אותחלה, ויש עליה שאלה. הגל נסגר (החריץ מתפנה, השולח
    # נעצר) ואינו מחליט דבר: הדופק הבא סופר אותו ב-`_resume`, ושם ההכרעה של המפעיל.
    from server.rounds import interrupt              # noqa: PLC0415 -- interrupt imports room
    if interrupt.holds(conn, round_row["id"]):
        # סקירה 1-ה: מה שנמדד בכל חיבור SATA נרשם גם בגל שנעצר על שאלה — `_resume` רושם
        # את שאר הלמידה (קצב, דגמים, כיוונון), ואת זו לא. לעולם אינו זורק (#2817: נאכף כאן).
        try:
            learn_ports.record_wave(conn, round_row["wave_session_id"], members)
        except Exception:                        # noqa: BLE001 — הזיכרון אינו עוצר את סיום הגל
            log.exception("wave %s: learn_ports.record_wave failed; the wave still closes (#2817)",
                          round_row["wave_session_id"])
        store.close(round_row["wave_session_id"], "")
        return
    _record_wave_stats(conn, round_row["wave_session_id"], members)
    total, written, skipped, cancelled = _tally(conn, round_row, members)
    slots_raw, closed = _close_no_disk(conn, round_row, members)   # #2201
    no_disk = _no_disk_count(round_row) + len(closed)
    set_sql, set_args, where_sql, where_args = _slots_claim(round_row, slots_raw)
    _clear_written_failures(conn, round_row, written)              # #1792
    _brain_after_wave(conn, round_row, members, total + len(skipped) + len(cancelled) + no_disk)  # B24, B33 (צל)

    # #715: מדיסק חי הגל היחיד הוא הסבב כולו (ראו `_resume`).
    # #1760: מגירה שדולגה כאדומה סוגרת את המקום שלה — 1/2 עם אחת אדומה הוא הסוף.
    # #1936 (FI O2): וכך גם מגירה שמפעיל ביטל — 1/2 עם אחת שבוטלה הוא `room_done`,
    # לא גל 2 שמעיר את המשכפל ולא `room_failed`.
    # #2201 (ברזל 25/10, room_edc35340): וכך גם חריץ שנבחר ולא היה בו דיסק — נכשל
    # בשמו (`no_disk`), ואין גל 2 שממתין לו.
    from server.rounds import direct                 # noqa: PLC0415
    # #2841: מדיסק חי אין גל שני — משכפל שנעלם לפני שדיווח, ושום מגירה לא
    # נכתבה: הסבב נכשל בשמו, לא `room_done written=0/N` שנראה כמו סיום.
    # #2907: וכך גם כשכל המגירות דיווחו `failed` (בלי שאיש אבד).
    lost_why = ((_lost_before_result(conn, members, total, round_row["target_drives"])
                 or _failed_before_write(conn, members, total, round_row["target_drives"],
                                         cancelled))
                if direct.is_build_disk(round_row) else None)
    if lost_why:
        if _fail_round(conn, store, round_row, lost_why):
            journal(conn, "room_failed",
                    f'{round_row["id"]} {round_row["wave_session_id"]} {lost_why}'
                    f' written={total}/{round_row["target_drives"]}', round_id=round_row["id"])
        return
    if (total + len(skipped) + len(cancelled) + no_disk >= round_row["target_drives"]
            or direct.is_build_disk(round_row)):
        # הסגירה היא התביעה: שני תהליכונים שהגיעו לכאן עם אותו גל —
        # רק זה שסגר אותו בפועל כותב את השורה התחתונה של הסבב (#177).
        if not store.close(round_row["wave_session_id"], ""):
            return
        if not update_one(
            conn,
            "UPDATE room_rounds SET written_drives = ?, written_serials = ?,"
            " skipped_serials = ?, cancelled_serials = ?" + set_sql
            + ", state = 'closed', closed_at = ?"
            " WHERE id = ? AND state = 'active' AND wave_session_id = ?" + where_sql,
            (total, json.dumps(sorted(written)), json.dumps(sorted(skipped)),
             json.dumps(sorted(cancelled)), *set_args, now_iso(),
             round_row["id"], round_row["wave_session_id"], *where_args),
        ):
            return
        conn.commit()
        _journal_no_disk(conn, round_row, closed)
        journal(conn, "room_done",
                f'{round_row["id"]} written={total}/{round_row["target_drives"]}'
                + _skipped_note(skipped, cancelled, no_disk), round_id=round_row["id"])
        return

    # #1728: משכפל שאותחל באמצע הזרם וחזר — הגל הבא, לא סבב שנכשל.
    # #1936: ביטול של מפעיל אינו כשל — גל שבו מגירה בוטלה אינו "גל מת".
    if (total == round_row["written_drives"] and skipped == _skipped(round_row)
            and cancelled == _cancelled(round_row)
            and not _revived(conn, round_row["wave_session_id"], members)):
        _fail_dead_wave(conn, store, round_row, total)
        return

    # היעד לא הושג — הגל הבא נפתח מעצמו וממתין למגירות מוחלפות.
    machines = conn.execute(
        f"SELECT COUNT(*) AS n FROM machines WHERE {_IN_ROOM}"
    ).fetchone()["n"]
    current = conn.execute(
        "SELECT state, wave_session_id FROM room_rounds WHERE id = ?",
        (round_row["id"],),
    ).fetchone()
    if (current is None or current["state"] != "active"
            or current["wave_session_id"] != round_row["wave_session_id"]):
        return
    # סגירת הגל הגמור ופתיחת הבא הן טרנזאקציה אחת: בין השתיים החריץ
    # היה פנוי, ושני דופקים בו-זמנית הפכו את השני ל-`TAKEN` — ה-hello
    # שהריץ אותו החזיר 500 (#177), ופותח שלישי (סבב כיתה מהקונסולה)
    # שהיה נכנס לאותו חלון היה משאיר את החדר בלי גל בכלל.
    try:
        wave_id = store.open(
            CLONERS_GROUP, round_row["image_id"], prefix="ROOM",
            expected_clients=_expected(round_row, machines), opened_by="",
            replaces=round_row["wave_session_id"],
            expand_partition=round_row["expand_partition"],
            roster=_roster(round_row),
        )
    except SessionSuperseded:
        # תהליכון אחר סגר את הגל הזה ופתח את הבא. זה אינו כישלון אלא
        # בדיוק מה שהאטומיות נועדה לייצר: פותח אחד, לא שניים.
        return
    if not update_one(
        conn,
        "UPDATE room_rounds SET written_drives = ?, written_serials = ?,"
        " skipped_serials = ?, cancelled_serials = ?" + set_sql + ", wave_session_id = ?,"
        " wave_number = wave_number + 1"
        " WHERE id = ? AND state = 'active' AND wave_session_id = ?" + where_sql,
        (total, json.dumps(sorted(written)), json.dumps(sorted(skipped)),
         json.dumps(sorted(cancelled)), *set_args, wave_id,
         round_row["id"], round_row["wave_session_id"], *where_args),
    ):
        if conn.execute(
            "SELECT 1 FROM room_rounds WHERE id = ? AND wave_session_id = ?",
            (round_row["id"], wave_id),
        ).fetchone() is None:
            store.close(wave_id, "")
        return
    conn.commit()
    _journal_no_disk(conn, round_row, closed)
    journal(conn, "room_wave",
            f'{round_row["id"]} wave={round_row["wave_number"] + 1}'
            f' written={total}/{round_row["target_drives"]}', round_id=round_row["id"])


# --- התצוגה ------------------------------------------------------------------


def _fresh(stamp: str | None) -> bool:
    if not stamp:
        return False
    try:
        seen = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    # #774: חסם תחתון. חותמת בעתיד (סטיית שעון, DB מיובא) נותנת גיל
    # שלילי שמקיים `<= AWAKE_SECONDS` — והמכונה נראתה "ערה" לכל משך
    # הסטייה. חותמת עתידית אינה ראיה שהמכונה חיה עכשיו.
    age = datetime.now(timezone.utc) - seen
    return timedelta(0) <= age <= timedelta(seconds=AWAKE_SECONDS)


def local_copy_now(local_clone: str | None, last_seen: str | None) -> bool:
    """#1367 (נדב 14/10): המכונה באמצע שיכפול מקומי **עכשיו** — ה-hello האחרון
    אמר `local_clone: copying`, והוא טרי (`AWAKE_SECONDS`; ה-beat של הסוכן כל
    10 ש'). מכונה ששתקה אינה "כותבת": אין ראיה לכך — ואין למי לשלוח אזהרה."""
    return local_clone == "copying" and _fresh(last_seen)


def _is_awake(*stamps: str | None) -> bool:
    """ערה אם דיברה איתנו בחלון האחרון — ב-hello **או** בדיווח progress.

    #449: ``last_seen`` מתעדכן מ-hello בלבד, אבל בזמן שחזור הכתיבה
    סינכרונית והסוכן חדל לפעום — לולאת הרקע מדווחת **progress** ולא
    hello. מכונה שכותבת בפועל דיווחה התקדמות לפני שניות, וזו ראיה
    חיובית שהיא חיה; סימונה כ"ישנה" קיפל "עסוקה בכתיבה" ו"נפלה/איבדה
    רשת" לאותו סימן (עיקרון 5). ``updated_at`` של חבר הסבב הוא הראיה
    השנייה — מספיקה אחת מהשתיים בחלון."""
    return any(_fresh(stamp) for stamp in stamps)


def _stream_stalled(machines: list[dict], conn: SqlConnection | None = None,
                    round_id: str | None = None, wave_id: str | None = None) -> bool:
    """האם **כל** המגירות הכותבות עצרו יחד — כלומר הזרם, לא הכונן.

    #3657: ההחלטה (הסף של 180 ש', שתי מגירות לפחות, "לא נמדד" אינו "עצור") היא של המוח --
    `brain.silence.judge_stall`, כלל K-STALL -- ונרשמת ב-`brain_decisions` כשיש `conn` וסבב.
    כאן רק התווית שהמסך מציג.

    #3723: `wave_id` הוא הגל שהסבב משדר. אחרי `send_done` שלו המגירות רק מסיימות לכתוב, ושתיקתן אינה "הזרם נעצר"."""
    return silence.stream_stalled(conn, machines, round_id, wave_id)


def status_view(ctx) -> dict:
    """מה שמסך החדר מציג: המכונות, המגירות, והסבב אם יש."""
    round_row = visible_round(ctx.conn)
    written = _written(round_row) if round_row else set()
    member_of = {}
    wave = None
    if round_row is not None:
        member_of = {
            m["mac"]: m for m in ctx.store.members(round_row["wave_session_id"])
        }
        wave = ctx.conn.execute(
            "SELECT state, started_at FROM sessions WHERE id = ?",
            (round_row["wave_session_id"],),
        ).fetchone()
    started_at = wave["started_at"] if wave and wave["state"] == "running" else None

    machines = []
    all_names = slot_names.all_by_mac(ctx.conn)   # #704
    model_maps = model_slots.by_mac(ctx.conn)      # #2001
    for row in ctx.conn.execute(
        "SELECT m.mac, m.suffix, m.drawer_count, m.drawer_slots,"
        " d.last_seen, d.disk_probe, d.local_clone, d.held_at, d.held_json"
        " FROM machines m"
        " LEFT JOIN net_devices d ON d.mac = m.mac"
        f" WHERE m.{_IN_ROOM} ORDER BY m.suffix"
    ):
        member = member_of.get(row["mac"])
        drawers = drawer_list(ctx.conn, row["mac"], written, member,
                              _selected_ports(round_row, row["mac"]), started_at,
                              _skipped(round_row) if round_row else None,   # #1760
                              _cancelled(round_row) if round_row else None)  # #1936
        # #704: השם של כל מגירה (לפי החריץ; NVMe לפי סידורי) — תווית בלבד,
        # `selected`/`port` לא זזים. None = אין שם, המסך מציג "דיסק N".
        names = all_names.get(row["mac"], {})
        model_map = model_maps.get(row["mac"])
        slots = drawer_slots.effective(row["drawer_count"], row["drawer_slots"],
                                       model_map["slots"] if model_map else None)
        for d in drawers:
            d["slot_name"] = slot_names.resolve(names, d["port"], d["serial"], d.get("pci"))
            d["drawer"] = drawer_slots.drawer_of(slots, d)
        # #710: כל עוד #704 (הגדרת מגירות במסוף) לא נבנה, העמודה NULL —
        # ואז ברירת המחדל היא מספר הדיסקים שהמכונה מדווחת בפועל (הפורט
        # הגבוה ביותר, או הספירה כשאין פורטים), כדי שהגריד לא יסתיר דיסק.
        _ports = [d["port"] for d in drawers if isinstance(d["port"], int)]
        _count = row["drawer_count"] or (max(_ports + [len(drawers)]) if drawers else 1)
        machines.append({
            "mac": row["mac"],
            "name": row["suffix"],
            # #3123: המספר והשם שהמפעיל רואה; `name` נשאר הטכני (אישורים משווים אליו).
            "number": machine_names.number_of("cloner", row["suffix"]),
            "display_name": machine_names.label("cloner", row["suffix"]),
            "drawer_count": _count,   # #695 הוגדר במסוף / #710 ברירת מחדל לפי הדיסקים
            "drawer_slots": slots,
            "awake": _is_awake(row["last_seen"],
                               member["updated_at"] if member else None),
            "drawers": len(drawers),
            # #402: כש-`drawers` הוא 0 — למה: `no_disks` (חברו כונן) /
            # `no_ports` (הבקר מנוטרל בקושחה) / `unchecked` (לא נספר) /
            # null (סוכן ישן). המספר לבדו קיפל את שלושתם.
            "disk_probe": row["disk_probe"],
            "fresh_drawers": sum(1 for d in drawers if d["fresh"]),
            "drawer_list": drawers,
            # #704: כל השמות של המכונה, גם של חריץ ריק (sata:3 בלי דיסק).
            "slot_names": names,
            "joined": member is not None,
            # #1367: שיכפול מקומי כותב עכשיו — "כבה אותו" שואל לפני
            "local_copy": local_copy_now(row["local_clone"], row["last_seen"]),
            # #2031: דלוק ומחכה ל"כבה" (#2024) — {at, problems} או None
            "held": held.view(row["held_at"], row["held_json"]),
            "state": member["state"] if member else None,
            "bytes_written": member["bytes_written"] if member else 0,
            "bytes_total": member["bytes_total"] if member else 0,
            "error": member["error"] if member else None,
        })

    label = ctx.conn.execute("SELECT label FROM groups WHERE id = ?", (CLONERS_GROUP,)).fetchone()
    view = {"round": None, "machines": machines, "group_label": label["label"] if label else "",
            "stream_stalled": _stream_stalled(machines, ctx.conn, round_row["id"] if round_row is not None else None,
                                              round_row["wave_session_id"] if round_row is not None else None),
            # #953: הרצפה שהמסך משווה אליה כל אימג' לפני פתיחת סבב — אותה
            # רצפה בדיוק שהשרת מסרב לפיה, ממקום אחד. null = אין דיווח.
            "disk_floor": disk_floor(ctx.conn),
            # #2786: ריפוד וסיומת המרצה לתבנית השמות — גם למפעיל הפצה.
            "clone_names": room_names.settings_view(ctx.conn)}
    if round_row is not None:
        slots = (json.loads(round_row["target_slots_json"])
                 if round_row["target_slots_json"] else None)
        ready = (ready_drives(ctx.conn, ctx.store, round_row)
                 if round_row["state"] == "active" else 0)
        open_wave = (round_row["state"] == "active" and wave is not None
                     and wave["state"] == "open")
        _gate = getattr(ctx.store, "start_gate", None)      # #3137
        if open_wave:     # #3277: the slow link and its ETA, before "start" -- shown by `room_precheck.warnings`
            linkwarn.attach(ctx.conn, machines, linkwarn.image_bytes(
                round_row, lambda image_id: _manifest_bytes(ctx, image_id)))
        forecast = _wave_forecast(ctx, round_row, wave, started_at)   # #1843; #3638: גם מקור אומדן האימות ב-`_wave_pace`
        view["round"] = {
            "id": round_row["id"],
            # 17/10: הגל הנוכחי — הקונסולה מזהה בו את `/overview.session` של אותו סבב
            # ומציגה שורה אחת, לא שתיים.
            "wave_session_id": round_row["wave_session_id"],
            "state": round_row["state"],
            "failed_reason": round_row["failed_reason"],
            "image_id": round_row["image_id"],
            "image_name": round_label(ctx, round_row),
            "target_drives": round_row["target_drives"],
            "target_slots": slots,
            "expand_partition": round_row["expand_partition"] or "auto",
            # #1269: דגם הדרייברים לכל דיסק יעד; null = הסבב בלי דרייברים.
            "driver_models": room_drivers.view(round_row),
            # #2786: שם המחשב לכל דיסק יעד; null = השם שבאימג' נשאר.
            "hostnames": room_names.view(round_row),
            "written_drives": round_row["written_drives"],
            # #3181: פס ייצור -- המונה, השם הבא, מה אפשר להוציא, והחלטות המוח; null = סבב רגיל.
            "production_line": prodline.view(ctx.conn, round_row),
            # #1760: מגירות שדולגו כאדומות — סגורות, לא נכתבו, ואינן "נותרו".
            "skipped_drives": len(_skipped(round_row)),
            "remaining_drives": _remaining(round_row),
            "wave_number": round_row["wave_number"],
            "wave_state": wave["state"] if wave else "closed",
            "ready_drives": ready,
            # #3137: הגל הפתוח ממתין לאינדקס של האימג' — `{state, percent, he}`; null = אינו ממתין לו.
            "preparing": (_gate(round_row["wave_session_id"], round_row["image_id"], start=False)
                          if open_wave and _gate is not None else None),
            # #432: האזהרות הכתומות לפני "התחל" — רק לגל פתוח; אזהרה, לא חסימה.
            "precheck": (room_precheck.warnings(machines, slots is not None)
                         if open_wave else []),
            # #65: מי חסר בגל הפתוח ולמה — כבה אותו / התחל בכל זאת / בטל.
            "missing": (_with_pxe(ctx.conn, room_precheck.missing(
                machines, _remaining(round_row),
                slots, ready)) if open_wave else []),
            "opened_by": round_row["opened_by"],
            # #2528: מכונה שכבתה או אותחלה באמצע הסבב — השאלה למפעיל; null = אין שאלה.
            "interrupt": _interrupt().view(ctx.conn, round_row),
            # #715: מאיפה הבייטים — ספרייה (השרת משדר) או מחשב בנייה.
            "source": _direct().source_view(ctx.conn, round_row),
            # #410: זמן, קצב והערכת סיום של הגל — null כשאין מה למדוד.
            **_wave_pace(started_at, machines, forecast),
            # #989/#3173/#3978: קצב הזרם וכרטיס "מצב הרשת" (`network`) -- ממנוע החתיכות: הגל שהוא מחזיק עכשיו, ואחרת
            # הגל האחרון של הסבב שנרשם (`room_stream`). null = "לא נמדד" (עיקרון 5), לא 0.
            **room_stream.view(ctx.conn, ctx.chunk, round_row),
            # #447: באיזו תקרה הגל יוצא (או יצא), ולפי איזה כונן.
            "wave_rate": _wave_rate(ctx, round_row, wave),
            # #1843: כמה זמן הגל צפוי לקחת לפי הגלים הקודמים של אותם מחשבי שיכפול
            # עם אותו מספר מגירות. `seconds: null` + `why` = "אין עדיין הערכה".
            "forecast": forecast,
        }
    return view


def _manifest_bytes(ctx, image_id: str) -> object:
    library = getattr(ctx, "library", None)
    manifest = library.get(image_id) if library is not None else None
    return manifest.get("total_compressed_bytes") if isinstance(manifest, dict) else None


def _with_pxe(conn: SqlConnection, missing: list[dict]) -> list[dict]:
    """#1887: למכונה שחסרה כי היא כבויה/לא עונה — מה ש-dnsmasq ראה מאז ההערה האחרונה
    (`{state, he}`). מידע בלבד; `unchecked` = הלוג לא נקרא."""
    for item in missing:
        if item.get("reason") == "off":
            got = pxe_log.verdict(conn, item["mac"], pxe_log.since_last_wake(conn, item["mac"]))
            item["pxe"] = {"state": got["state"], "he": got["he"]}
    return missing


def _wave_forecast(ctx, round_row: sqlite3.Row, wave: sqlite3.Row | None,
                   started_at: str | None) -> dict | None:
    return learn_eta.with_clock(
        wave_forecast(ctx.conn, getattr(ctx, "library", None), round_row, wave, started_at),
        getattr(ctx, "clock_trust", None))   # #2684


def wave_forecast(conn: SqlConnection, library, round_row: sqlite3.Row,
                  wave: sqlite3.Row | None, started_at: str | None) -> dict | None:
    """#1843: `learn_eta.wave` לגל הנוכחי — לגל שרץ לפי הכוננים שיצא אליהם
    (`wave_rates.roster`), לגל פתוח לפי מי שהצטרף עד עכשיו. None = אין גל."""
    session_id = round_row["wave_session_id"]
    if not session_id or wave is None or wave["state"] not in ("open", "running"):
        return None
    stored = drive_rates.decision(conn, session_id)
    drives = ((stored or {}).get("roster") if wave["state"] == "running" else None)         or wave_drives(conn, session_id)
    manifest = library.get(round_row["image_id"]) if library is not None else None
    size = manifest.get("total_compressed_bytes") if isinstance(manifest, dict) else None
    return learn_eta.wave(conn, drives, size, started_at, session_id)


def _wave_rate(ctx, round_row: sqlite3.Row, wave: sqlite3.Row | None) -> dict | None:
    """#447: מה שנבחר לגל כשיצא; לגל `open` — מה שייבחר לפי מי שהצטרף עד
    עכשיו (`projected`). גם כשהמשדר הוא מחשב בנייה (#715, `direct: true`)
    — הקצב שלו נקבע כאן. None: גל רץ/סגור שלא נשמרה לו בחירה — "לא ידוע",
    לא התקרה."""
    session_id = round_row["wave_session_id"]
    if not session_id:
        return None
    direct = _direct().is_build_disk(round_row)
    stored = drive_rates.decision(ctx.conn, session_id)
    if stored is not None:
        # `roster` (הכוננים כשהגל יצא) הוא לזיכרון בשרת, לא למסך.
        return {**{k: v for k, v in stored.items() if k != "roster"},
                "projected": False}
    if wave is None or wave["state"] != "open" or round_row["state"] != "active":
        return None
    ceiling = ctx.chunk.max_bitrate if ctx.chunk is not None else None
    # כרטיס ההפצה (15/10) — רק כשהשרת הוא המשדר; גל ישיר יוצא מהכרטיס של
    # מחשב הבנייה.
    link = ctx.chunk.link() if ctx.chunk is not None and not direct else None
    return {**drive_rates.choose(ctx.conn, wave_drives(ctx.conn, session_id), ceiling,
                                 session_id, direct=direct, link=link),
            "projected": True}


def _verify_s(forecast: dict | None) -> int | None:
    """#3638: כמה שניות לוקח שלב האימות (`readback`) אחרי הכתיבה -- החציון שהשרת מדד בגלים הקודמים של אותן מכונות
    (`learn_eta.wave` -> `machines[].stages_s.readback`, `restore_stages`), והארוך מבין המכונות: הגל נגמר כשהאחרונה נגמרת.

    ⚠️ `None` = "אין אומדן", ולא 0 ולא קבוע מומצא (עיקרון 5ב): בלי היסטוריה, או כשמכונה אחת בלי שלב מדוד, הגל לא נקבע
    בשום מספר, והתוצאה היא `eta_verify_s: None` -- ה-ETA אומר רק את הכתיבה, ושדה אומר שהאימות לא נכלל."""
    machines = (forecast or {}).get("machines")
    if not isinstance(machines, list) or not machines:
        return None
    got = []
    for machine in machines:
        stages = machine.get("stages_s") if isinstance(machine, dict) else None
        seconds = stages.get("readback") if isinstance(stages, dict) else None
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
            return None
        got.append(seconds)
    return int(max(got))


def _wave_pace(started_at: str | None, machines: list[dict], forecast: dict | None = None) -> dict:
    """#410: מה שהמפעיל רואה בראש המסך — כמה זמן הגל רץ, באיזה קצב,
    ומתי הוא צפוי להסתיים.

    `elapsed_s` מ-`started_at` של הגל (`running` בלבד). `rate_bps` הוא
    **האיטית** מבין המגירות הכותבות שנמדדו, ו-`eta_s` הוא **הארוך** —
    הגל נגמר כשהאחרונה נגמרת, וקצב ממוצע-על-הכול היה מסתיר מגירה
    שמפגרת. None כשאין מגירה כותבת שנמדדה (עיקרון 5: לא 0).

    #3598 (J1, 17/11): `eta_s` הוא None גם כשיש מגירה **כותבת, עם יתרה, בלי קצב
    נמדד** (חלון שלא זז: `waiting_disk`, השלמה) -- "לא נמדד" אינו "אינו קובע".
    ה-max של השאר אמר "13 שניות" כשעל מגירה אחת נשארו 15.5GB (עוד 24 דקות).

    #3638 (J3b, 18/11): מגירה שכותבת עם יתרה ובלי `eta_s` -- גם כשהקצב שלה נמדד, כי ה-ETA עוד לא עבר את
    `ETA_MIN_SAMPLE_S` -- הופכת את `eta_s` של הגל ל-None באותה דרך. וה-ETA כולל את שלב האימות (`_verify_s` מההיסטוריה,
    `forecast` של הגל): `eta_s` = כתיבה + אימות, ו-`eta_verify_s` הוא החלק של האימות, או None כשהוא לא נכלל. ב-J3b
    ה-ETA נגמר עם `send_done`, 33 ש' לפני `room_done`.
    """
    since = _iso_or_none(started_at)
    elapsed = (max(0, int((datetime.now(timezone.utc) - since).total_seconds()))
               if since is not None else None)
    all_writing = [d for m in machines for d in m.get("drawer_list", [])
                   if d.get("state") == "writing"]
    writing = [d for d in all_writing if d.get("rate_bps") is not None]
    rate = min(d["rate_bps"] for d in writing) if writing else None
    etas = [d["eta_s"] for d in writing if d.get("eta_s") is not None]
    unmeasured = any(d.get("eta_s") is None
                     and _int(d.get("bytes_total")) > _int(d.get("bytes_written"))
                     for d in all_writing)
    wrote = max(etas) if etas and not unmeasured else None
    verify = _verify_s(forecast) if wrote is not None else None
    return {"started_at": started_at, "elapsed_s": elapsed, "rate_bps": rate,
            "eta_s": wrote + verify if wrote is not None and verify else wrote,
            "eta_verify_s": verify}


def _wake_targets(conn: SqlConnection, macs) -> set[str]:
    """נדב 17/10: "הער" מעיר רק את מה שנבחר. רשימה לא ריקה של מחשבי שיכפול
    רשומים, אחרת 400 — בקשה שלא נקראה אינה "כל החדר" (עיקרון 5): בחדר יש
    מכונה שאסור להעיר מרחוק, ו-`wol_sent grp_CLONERS count=3` העיר גם אותה."""
    if not isinstance(macs, list) or not macs:
        raise HTTPException(400, "לא נבחר מחשב שיכפול להערה")
    chosen = set()
    for raw in macs:
        mac = registry.normalize_mac(raw) if isinstance(raw, str) else None
        # לפי תפקיד הקבוצה ולא לפי `grp_CLONERS` — כך גם אחרי #1555 (חדר שכולל כל
        # קבוצת שיכפול), בכל סדר מיזוג.
        if mac is None or conn.execute(
                "SELECT 1 FROM machines m JOIN groups g ON g.id = m.group_id"
                " WHERE m.mac = ? AND g.role = 'cloner'", (mac,)).fetchone() is None:
            raise HTTPException(400, f"אינו מחשב שיכפול רשום: {str(raw)[:32]}")
        chosen.add(mac)
    return chosen


def _suggest():
    from server.rounds import suggest            # noqa: PLC0415 -- suggest מייבא את room
    return suggest


def _interrupt():
    from server.rounds import interrupt              # noqa: PLC0415 -- interrupt imports room
    return interrupt


def _console():
    """`room_console` מייבא את `room` — ולכן הייבוא כאן מאוחר, כמו `_direct`."""
    from server.rounds import room_console           # noqa: PLC0415
    return room_console


def _direct():
    """`direct` מייבא את `room` (ולידציית היעדים, הסבב הפעיל) — ולכן כאן
    הייבוא מאוחר, באותה צורה כמו `session_view` ב-`sessions.py`."""
    from server.rounds import direct                 # noqa: PLC0415
    return direct


# --- ה-API -------------------------------------------------------------------


def create_room_router(ctx, wake=None, origin: str = "console") -> APIRouter:
    """`wake` מוזרק מ-app.py כדי ששליחת ה-WoL תהיה אותה פונקציה בכל
    המערכת (וניתנת לזיוף בבדיקות). `origin` — `build` כשהראוטר יושב על
    פורט הסוכן (הקיוסק של מחשב הבנייה); נרשם ב"בטל דיסק זה" (#1386)."""
    router = APIRouter(prefix="/api/console/room")
    current_user, _admin_only = auth.dependencies(ctx.conn)

    def room_operator(user=Depends(current_user)) -> tuple[str, str]:
        """מחובר **וגם** בתפקיד שמותר לו להפעיל את החדר."""
        if user[1] not in ROOM_OPERATOR_ROLES:
            journal(ctx.conn, "room_role_denied", f"{user[0]} ({user[1]})")
            raise HTTPException(403, "פעולה למפעיל סבבים בלבד")
        return user

    @router.get("")
    def status(user=Depends(current_user)):
        # Observation is never a room state-machine event (#446).
        return status_view(ctx)

    @router.get("/driver-models")
    def list_driver_models(user=Depends(current_user)):
        """#1269: הדגמים שיש להם חבילת דרייברים בספרייה — הרשימה שממנה
        המפעיל בוחר. רק מכללי הדגם של המניפסטים (`drivers.models`)."""
        return drivers.models(library_packages(ctx))

    @router.get("/suggest")
    def suggest_round(target_drives: int, user=Depends(current_user)):
        """#3897: אילו משכפלים ומגירות השרת מציע ל-N דיסקים. קריאה בלבד -- לא פותחת ולא מעירה דבר."""
        try:
            return _suggest().suggest(ctx, target_drives)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @router.post("")
    async def open_(request: Request, user=Depends(room_operator)):
        body = await request.json()
        source = body.get("source")
        keep = None
        try:
            if body.get("keep_on") is not None:
                if user[1] != "admin":
                    # כמו `/room/poweroff`: כיבוי משכפלים אינו פעולה של מפעיל הפצה (הקיוסק נכנס לכאן).
                    journal(ctx.conn, "room_role_denied", f"{user[0]} ({user[1]}) keep_on")
                    raise HTTPException(403, "כיבוי המשכפלים שלא נבחרו — למנהל בלבד")
                # #3123: "השאר דלוקים" בפתיחת סבב — נבדק **לפני** שהסבב נפתח: רשימה שלא
                # הובנה אינה מכבה כלום ואינה פותחת סבב. ההרצה (הכיבוי) אחרי שהסבב נפתח.
                keep = _console().resolve_keep(ctx.conn, body["keep_on"])
            # #715: `source: {kind: build_disk, mac, disk}` — המקור הוא דיסק
            # במחשב הבנייה, אין `image_id`, והיעדים הם בחירה מפורשת.
            if isinstance(source, dict) and source.get("kind") == _direct().BUILD_DISK:
                if body.get("hostnames") is not None:   # #2786: לא נשמט בשקט
                    raise ValueError("שמות מחשב — רק בסבב מהספרייה, לא בהפצה ישירה")
                opened = _direct().open_direct_round(
                    ctx, source, body.get("target_slots"),
                    body.get("target_drives"), user[0],
                    body.get("driver_models"),     # #1269
                    origin=request_origin(request))   # #1576
            else:
                opened = open_round(
                    ctx, body.get("image_id", ""),
                    int(body.get("target_drives", 0)), user[0],
                    body.get("target_slots"),          # #1410: explicit, non-empty selection is required
                    body.get("expand_partition"),      # #59: בחירת ההרחבה (אופציונלי)
                    body.get("driver_models"),         # #1269: דרייברים לכל דיסק (אופציונלי)
                    body.get("hostnames"),             # #2786: שם מחשב לכל דיסק (אופציונלי)
                )
        except SessionError as exc:       # לפני ValueError — הוא יורש ממנו
            raise HTTPException(409, str(exc))
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc) or "בקשה לא תקינה")
        if keep is None:
            return opened
        # המשכפלים של הסבב עצמו (ה-WoL של הפתיחה מעיר אותם) לא נכבים גם אם לא נבחרו.
        room_console = _console()
        result = room_console.keep_on(
            ctx.conn, keep, user[0], f"השאר דלוקים בפתיחת סבב {opened['id']}",
            protect=room_console.round_protect(ctx.conn))
        return {**opened, "keep_on": result}

    @router.post("/start")
    def start_now(user=Depends(room_operator)):
        round_row = active_round(ctx.conn)
        if round_row is None:
            raise HTTPException(409, "אין סבב חדר פעיל")
        if not _direct().source_ready(round_row):
            raise HTTPException(
                409, "מחשב הבנייה עדיין קורא את הדיסק — המניפסט טרם הגיע")
        warned = precheck_now(ctx.conn, ctx.store, round_row)   # #432
        try:
            ctx.store.start_now(round_row["wave_session_id"], user[0])
        except SessionError as exc:
            raise HTTPException(409, str(exc))
        journal_precheck(ctx.conn, round_row, warned, user[0])
        _wave_started(ctx.conn, ctx.store, round_row)       # #3277
        return {"ok": True, "warned": len(warned)}

    @router.post("/missing/{mac}/poweroff")
    async def poweroff_missing(mac: str, request: Request, user=Depends(room_operator)):
        """#65 "כבה אותו": כיבוי **מכונה אחת** שהגל הפתוח מראה כחסרה — כדי
        להחליף בה מגירות. רק לגל `open` (כיבוי באמצע כתיבה מפיל אותה), רק
        למכונה שב-`missing` ושעונה ב-hello (הכיבוי נמסר בתשובתו, דרך הסוכן
        ו-`arm_wol`, #587). `confirm_name` = שם המחשב, מחלון האישור (#1270).
        על פורט הסוכן כי זו הכרעת נדב (05/09): הפלט והבחירה — במחשב הבנייה."""
        try:
            body = await request.json()
        except Exception:                              # noqa: BLE001
            body = {}
        typed = body.get("confirm_name", "") if isinstance(body, dict) else ""
        canon = registry.normalize_mac(mac)
        if canon is None:
            raise HTTPException(400, "MAC לא תקין")
        view = status_view(ctx)["round"]
        if view is None or view["state"] != "active":
            raise HTTPException(409, "אין סבב חדר פעיל")
        if view["wave_state"] != "open":
            raise HTTPException(409, "הגל כבר יצא לדרך — כיבוי עכשיו מפיל כתיבה חיה")
        entry = next((m for m in view["missing"] if m["mac"] == canon), None)
        if entry is None:
            raise HTTPException(409, "המחשב אינו חסר בגל הזה")
        if not entry["can_poweroff"]:
            raise HTTPException(409, "המחשב אינו מחובר — אין למי לשלוח כיבוי")
        name = entry["name"] or canon
        if typed != name:
            raise HTTPException(400, "השם שנשלח אינו שם המחשב")
        # #1367 (נדב 14/10): אישור מעל אזהרת "באמצע שיכפול מקומי" — מהחלון
        # של הקונסולה. רק `true` ממש; בלעדיו הסוכן זורק בקשה שמגיעה בזמן העתקה.
        confirmed = isinstance(body, dict) and body.get("confirm_local_copy") is True
        from server.rounds import room_console           # noqa: PLC0415 — room_console מייבא את room
        room_console.request_power(ctx.conn, [canon], room_console.POWEROFF, user[0], confirmed=confirmed)
        journal(ctx.conn, "room_missing_poweroff",
                f"{canon} ({name})" + (" local copy confirmed" if confirmed else ""), user[0],
                machine=canon)
        return {"requested": 1, "ttl_s": room_console.POWEROFF_TTL_SECONDS}

    @router.post("/wake")
    async def wake_room(request: Request, user=Depends(room_operator)):
        if wake is None:
            raise HTTPException(503, "WoL אינו מחובר בשרת הזה")
        # נדב 17/10: `macs` — מחשבי השיכפול שנבחרו במסך; רק הם מתעוררים. **בלי גוף
        # בכלל** (הקונסולה) — כל החדר, כמו קודם. גוף שלא נקרא, שאינו אובייקט, או
        # אובייקט בלי `macs` ("mac" בשגיאת כתיב) — 400: בקשה שלא הובנה אינה "כל החדר"
        # (עיקרון 5), ובחדר יש מכונה שאסור להעיר מרחוק (סקירת אבטחה, #1577).
        raw = await request.body()
        only = None
        if raw.strip():
            try:
                body = json.loads(raw)
            except ValueError:
                raise HTTPException(400, "גוף הבקשה אינו JSON — אף מחשב לא הוער") from None
            if not isinstance(body, dict) or "macs" not in body:
                raise HTTPException(400, "לא נבחר מחשב שיכפול להערה (macs)")
            only = _wake_targets(ctx.conn, body["macs"])
        sent = wake() if only is None else wake(only=only)
        journal(ctx.conn, "wol_sent", f"{CLONERS_GROUP} count={sent}"
                + (f" macs={','.join(sorted(only))}" if only is not None else ""), user[0])
        # `sent` ולא `woken`: חבילת WoL היא UDP broadcast בלי ACK —
        # `sendto` שהצליח פירושו שהקרנל קיבל 102 בייט, ותו לא. אין
        # ראיה שהמכונה קמה, והשם `woken` טען טענה שלא נמדדה (#528;
        # המודול עצמו מונה `sent`). הראיה החיובית להתעוררות היא hello
        # שמגיע בחלון — וזו הכרעה נפרדת.
        # הסיבה עולה למסך ולא רק ליומן: "0 מחשבים" בלי הסבר שולח את
        # הטכנאי לחפש WoL ב-BIOS של 12 מכונות, כשהסיבה היא כבל אחד
        # בשרת (#74). getattr — שולח מוזרק בטסטים עשוי להחזיר int רגיל.
        return {"sent": int(sent),
                "failed": len(getattr(sent, "failed", ())),
                "reasons": list(getattr(sent, "reasons", ()))}

    @router.post("/drawer/cancel")
    async def cancel_drawer(request: Request, user=Depends(room_operator)):
        """#1386 "בטל דיסק זה": מגירה אחת יוצאת מגל שכותב; השאר ממשיכות.

        `{mac, dev}` — המגירה כפי שהיא ב-`drawer_list`. רק בגל שיצא לדרך, רק
        למחשב שחבר בו, ורק למגירה שהדיווח האחרון שלה `writing` (המסכים מציגים
        את הכפתור רק אז). הבקשה נמסרת לסוכן בתשובת הדיווח הבא (~2 ש');
        "בוטל" נרשם ביומן כשהסוכן מדווח `cancelled` (`drawer_cancel.confirmed`).
        בלי הקלדת שם (#1270) — חלון האישור הוא האישור."""
        try:
            body = await request.json()
        except Exception:                              # noqa: BLE001
            body = None
        if not isinstance(body, dict):
            raise HTTPException(400, "גוף הבקשה אינו אובייקט JSON")
        mac = registry.normalize_mac(body.get("mac")) if isinstance(body.get("mac"), str) else None
        dev = body.get("dev")
        if mac is None or not isinstance(dev, str) or not dev.isalnum() or len(dev) > 32:
            raise HTTPException(400, "חסר מחשב (mac) או דיסק (dev) תקין")
        round_row = active_round(ctx.conn)
        wave = (ctx.conn.execute("SELECT * FROM sessions WHERE id = ?",
                                 (round_row["wave_session_id"],)).fetchone()
                if round_row is not None and round_row["wave_session_id"] else None)
        if wave is None:
            raise HTTPException(409, "אין סבב חדר פעיל")
        if wave["state"] != "running":
            raise HTTPException(409, "הגל עוד לא יצא לדרך — אין כתיבה לבטל")
        member = ctx.conn.execute(
            "SELECT * FROM session_members WHERE session_id = ? AND mac = ?",
            (wave["id"], mac)).fetchone()
        if member is None:
            raise HTTPException(409, "המחשב אינו בגל הזה")
        target = next((t for t in json.loads(member["targets_json"] or "[]")
                       if isinstance(t, dict) and t.get("dev") == dev), None)
        prior = drawer_cancel.existing(ctx.conn, wave["id"], mac, dev)
        if prior is not None:
            raise HTTPException(409, "הדיסק כבר בוטל — " + drawer_cancel.by_dev(
                ctx.conn, wave["id"], mac)[dev]["text_he"])
        if target is None or target.get("state") != "writing":
            raise HTTPException(409, "הדיסק אינו כותב עכשיו — אין מה לבטל")
        drawer_cancel.request(ctx.conn, wave["id"], mac, dev, user[0], origin)
        named = slot_names.label(ctx.conn, mac, dev=dev) or dev
        journal(ctx.conn, "room_drawer_cancel_requested",
                f"{round_row['id']} {mac} {named} in {wave['id']} ({origin})", user[0],
                machine=mac, round_id=round_row["id"])
        return {"ok": True}

    @router.post("/close")
    async def close_(request: Request, user=Depends(room_operator)):
        # גוף ריק או לא-JSON הוא בדיוק המקרה שההקלדה נועדה לתפוס, ולכן
        # הוא נופל לאישור ריק — 400 עם ההסבר, ולא 500 שנראה כתקלת שרת.
        try:
            body = await request.json()
        except Exception:                              # noqa: BLE001
            body = {}
        typed = body.get("confirm_name", "") if isinstance(body, dict) else ""
        try:
            close_round(ctx, user[0], typed)
        except SessionError as exc:       # לפני ValueError — הוא יורש ממנו
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"ok": True}

    @router.post("/interrupt")
    async def answer_interrupt(request: Request, user=Depends(room_operator)):
        """#2528: התשובה לשאלה על מכונה שכבתה או אותחלה באמצע הסבב — `{"answer":
        "rewrite" | "close"}`. הכרעת נדב 12/11: **במחשב הבנייה** עונה כל מי שמחובר לקיוסק
        (מנהל או משתמש הפצה), ולכן הנתיב בראוטר הזה, שנכנס לקיוסק; **בקונסולה** עונה מנהל
        בלבד — משתמש הפצה אינו נכנס אליה (#1073, `console_only`). רשימת-ההיתר של #152
        נשארת: תפקיד שלישי שייוולד לא יקבל את "כתוב מחדש" ביום היוולדו. תשובה אחת סוגרת
        את השאלה בכל מקום שהיא מוצגת בו; 409 כשאין שאלה פתוחה."""
        try:
            body = await request.json()
        except Exception:                              # noqa: BLE001
            body = {}
        # סקירה 1-ו: הגוף נקרא על הלולאה; עבודת ה-sqlite בתהליכון מהמאגר (כמו #2748).
        return await run_in_threadpool(_answer_interrupt, body if isinstance(body, dict) else {}, user[0])

    def _answer_interrupt(body: dict, who: str) -> dict:
        choice = body.get("answer")
        round_row = active_round(ctx.conn)
        if round_row is None:
            raise HTTPException(409, "אין סבב חדר פעיל")
        try:
            # סקירה 1-ב: התשובה חלה רק על המכונות שהמסך הציג (`machines`), לא על שאלה
            # שנרשמה אחרי שהמפעיל ראה אותו.
            answered = _interrupt().answer(ctx.conn, round_row, choice, who, origin,
                                           body.get("machines"))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if not answered:
            raise HTTPException(409, "אין שאלה פתוחה על המכונות האלה בסבב הזה")
        sweep(ctx.conn, ctx.store)    # הגל כבר נגמר? התשובה מיושמת עכשיו, לא בדופק הבא
        return {"ok": True, "answer": choice, "machines": answered}

    return router
