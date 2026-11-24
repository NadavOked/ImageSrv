"""#3469 (K16, #3064): חיזוי זמן גל מההיסטוריה שהשרת כבר מודד, ורישומו מול מה שקרה. **בצל בלבד.**

הלמידה של השרת מציגה זמנים על המסך בלבד: `learn/eta.py` (LR17), התעוררות מ-WoL (LR14), זמני אתחול (LR15), שלבי שחזור
(LR18) וזמן "מוכן" של דיסק (S56). המוח לא קרא אף אחד מהם: החיזוי היחיד שלו הוא K3 (קישור איטי, `linkwarn.py`).

**מה נרשם** (`brain_decisions`, כלל ``K16``, פעולה ``TIME_FORECAST``, ישות ``round`` = מזהה הגל):

- בתחילת הגל: ``PREDICT`` עם ``predicted`` בשניות (`learn_eta.wave`: חציון ה-goodput והשלבים הקבועים של גלים קודמים עם אותו
  מספר מגירות, והמכונה האיטית קובעת), או ``UNPREDICTED`` עם הסיבה. בראיות, לכל מכונה בגל: זמן התעוררות טיפוסי
  (`learn_wol.stats`), זמן אתחול (סכום חציוני השלבים של `boot_step_times`), "מוכן אחרי" (S56, חציון הגלים, המגירה האיטית),
  ושלבי השחזור שנמדדו (`restore_stages`). **מה שלא נמדד כתוב ``unmeasured``**, לא 0 ולא תקין (עיקרון 5).
- בסוף הגל: השורה נסגרת עם ``actual`` (משך הגל מתחילתו), ובתוצאה: חזוי מול בפועל, כמה שניות הגל עמד על שאלה למפעיל
  (`learn_waits.wave_hold`, "לא נמדד" כשאין שחרור ביומן), והחיזוי של מנוע החתיכות לאותו גל (``K3/WAVE_ETA``, #3353) אם נרשם:
  שני החיזויים מול אותו "בפועל".

**מה לא נעשה:** ``matched`` נשאר NULL: הגדרת ה"התאמה" לכל כלל היא של נדב (#3321), ו-`forecast.TOLERANCE` אין בו K16. כשיוגדר
שם, `forecast.judge` יחשב אותה לבד. חיזוי "מתי הסבב יוכל להתחיל" (התעוררות + אתחול של מכונות שטרם הצטרפו) לא נבנה: המספרים
נקראים כאן (`machine_times`), אבל אין "בפועל" לסגור מולם בלי וו ב-`net/wol.py`.

המתג הוא ``brain_veto_mode``: ``off`` = לא מחושב ולא נרשם. **לעולם אינו זורק** אל הגל.
"""

from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime, timezone

from boot.trace import STEPS

from server.brain import agentsenses, forecast, linkwarn, memory
from server.learn import boot as learn_boot, eta as learn_eta, waits as learn_waits, wol as learn_wol
from server.system import steadyclock

log = logging.getLogger("imagesrv.brain")

RULE, ACTION = "K16", "TIME_FORECAST"
PREDICT, UNPREDICTED = "PREDICT", "UNPREDICTED"
OFF = "off"
SHADOW_HE = "צל: נרשם בלבד, שום קצב או סבב לא השתנה"
PEER_RULE, PEER_ACTION = "K3", "WAVE_ETA"
#: שלבי האתחול שנמדדים לכל מכונה: כל מעבר שיש לו קודם, חוץ מ-`entry` (ההמתנה לאדם בתפריט, `learn_boot.SKIPPED`).
BOOT_STEPS = tuple(s for s in STEPS[1:] if s not in learn_boot.SKIPPED)
WHY_HE = {"no_drives": "אין כוננים בגל", "no_image_size": "גודל האימג' לא ידוע",
          "no_history": "אין גל קודם עם אותו מספר מגירות לכל מכונה בגל", "error": "החישוב נכשל"}


def _mode(conn) -> str:
    from server.brain import veto                # כמו ב-`linkwarn`: המתג מגיע מהווטו
    return veto.mode(conn)


def _show(value: object) -> str:
    if value is None:
        return "unmeasured"
    if isinstance(value, dict):
        return "/".join(f"{k}:{v:g}" for k, v in value.items()) or "unmeasured"
    return f"{value:g}" if isinstance(value, float) else str(value)


def _boot_s(conn, mac: str) -> tuple[float | None, list[str]]:
    """סכום חציוני השלבים. שלב בלי `MIN_SAMPLES` מדידות = "לא נמדד", והסכום כולו "לא נמדד" (לא סכום חלקי שנראה שלם)."""
    total, missing = 0.0, []
    for step in BOOT_STEPS:
        rows = conn.execute("SELECT seconds FROM boot_step_times WHERE mac = ? AND step = ? ORDER BY rowid DESC LIMIT ?",
                            (mac, step, learn_boot.WINDOW)).fetchall()
        if len(rows) < learn_boot.MIN_SAMPLES:
            missing.append(step)
        else:
            total += statistics.median(r["seconds"] for r in rows)
    return (None if missing else round(total, 1)), missing


def _ready_s(conn, mac: str) -> float | None:
    """S56: בכל גל המגירה האיטית (הגל מחכה לה), וחציון הגלים. פחות מ-`MIN_SAMPLES` גלים = "לא נמדד"."""
    worst = []
    for row in conn.execute("SELECT targets_json FROM session_members WHERE mac = ? AND targets_json IS NOT NULL"
                            " ORDER BY updated_at DESC LIMIT ?", (mac, learn_eta.SCAN)):
        try:
            targets = json.loads(row["targets_json"])
        except (TypeError, ValueError):
            continue
        got = [(agentsenses.well_formed_target(t.get("senses")) or {}).get("ready_s")
               for t in targets if isinstance(t, dict)] if isinstance(targets, list) else []
        ready = [v for v in got if v is not None]
        if ready:
            worst.append(max(ready))
        if len(worst) >= learn_boot.WINDOW:
            break
    return float(statistics.median(worst)) if len(worst) >= learn_boot.MIN_SAMPLES else None


def machine_times(conn, mac: str, now: float) -> dict:
    """הזמנים שהשרת למד על מכונה: `wake_s`, `boot_s`, `ready_s`. ``None`` = לא נמדד. כל אחד נכשל לבדו."""
    out: dict = {"wake_s": None, "boot_s": None, "ready_s": None, "boot_missing": []}
    try:
        out["wake_s"] = learn_wol.stats(conn, mac, now)["typical_s"]
    except Exception:                            # noqa: BLE001 -- חוש שלא נקרא הוא "לא נמדד"
        log.exception("brain: %s of %s was not read", "wake_s", mac)
    try:
        out["boot_s"], out["boot_missing"] = _boot_s(conn, mac)
    except Exception:                            # noqa: BLE001
        log.exception("brain: %s of %s was not read", "boot_s", mac)
    try:
        out["ready_s"] = _ready_s(conn, mac)
    except Exception:                            # noqa: BLE001
        log.exception("brain: %s of %s was not read", "ready_s", mac)
    return out


def _evidence(conn, f: dict, drives: list, image_bytes: object, now: float) -> list[str]:
    macs = sorted({d["mac"] for d in drives if isinstance(d, dict) and d.get("mac")})
    by_mac = {m["mac"]: m for m in f.get("machines") or []}
    out = [f"eta_s={_show(f.get('seconds'))}", f"eta_why={f.get('why') or 'history'}",
           f"image_bytes={_show(image_bytes)}", f"machines={len(macs)}"]
    for mac in macs:
        t, m = machine_times(conn, mac, now), by_mac.get(mac) or {}
        # שלבים חסרים נקראים רק כשחלק מהשלבים נמדד: כשאף אחד לא נמדד, `boot_s=unmeasured` אומר הכול
        part = 0 < len(t["boot_missing"]) < len(BOOT_STEPS)
        miss = f" boot_missing={','.join(t['boot_missing'])}" if part else ""
        out.append(f"{mac}: wake_s={_show(t['wake_s'])} boot_s={_show(t['boot_s'])}{miss} ready_s={_show(t['ready_s'])}"
                   f" wave_s={_show(m.get('seconds'))} goodput_bps={_show(m.get('goodput_bps'))}"
                   f" fixed_s={_show(m.get('fixed_s'))} stages_s={_show(m.get('stages_s'))}")
    return out


def started(conn, wave_id: str, drives: list, image_bytes: object, now: float | None = None) -> int | None:
    """הגל יצא: שורה אחת לגל. ``drives`` = ``mac`` לכל מגירה (כמו `learn_eta.wave`). מחזיר את מספר השורה, או None."""
    try:
        mode = _mode(conn)
        if mode == OFF or memory.decisions(conn, round_id=wave_id, rule=RULE, limit=1):
            return None
        now = steadyclock.now() if now is None else now      # #3826: `now` נקרא רק מול `wol_wakes.sent_epoch` (steadyclock, #3768)
        f = learn_eta.wave(conn, drives, image_bytes, None, wave_id)
        seconds = f.get("seconds")
        evidence = _evidence(conn, f, drives, image_bytes, now)
        if seconds is None:
            code = f.get("why")
            why = WHY_HE.get(code, "לא נחזה") if isinstance(code, str) else "לא נחזה"
            verdict, predicted = UNPREDICTED, None
            reason = f"אין חיזוי: {why}" + (f" ({', '.join(f['missing'])})" if f.get("missing") else "")
        else:
            slow = max(f["machines"], key=lambda m: m["seconds"])
            verdict, predicted = PREDICT, float(seconds)
            reason = f"ETA חזוי מהיסטוריה {linkwarn.duration_he(seconds)}, המכונה האיטית {slow['mac']}"
        row = memory.record(conn, mode=mode, rule=RULE, action=ACTION, verdict=verdict, round_id=wave_id,
                            reason=f"{reason}. {SHADOW_HE}", evidence=evidence, entity=("round", wave_id),
                            predicted=predicted, unit="s" if predicted is not None else None)
        log.info("brain %s", json.dumps({"mode": mode, "rule": RULE, "action": ACTION, "verdict": verdict,
                                          "round": wave_id, "reason": reason, "memory": row}, ensure_ascii=False))
        return row
    except Exception:                            # noqa: BLE001 -- הגל יוצא גם כשהמוח נכשל
        log.exception("brain: the time forecast of %s was not recorded", wave_id)
        return None


def _iso(text: str | None) -> datetime | None:
    try:
        at = datetime.fromisoformat(text) if text else None
    except ValueError:
        return None
    return at if at is not None and at.tzinfo is not None else None


def _peer_s(conn, wave_id: str) -> int | None:
    """החיזוי של מנוע החתיכות לאותו גל (``K3/WAVE_ETA``, #3353), מהשורה שהוא רשם. אין שורה = None."""
    for d in memory.decisions(conn, round_id=wave_id, rule=PEER_RULE, limit=64):
        if d["action"] == PEER_ACTION and d["verdict"] == PREDICT:
            for line in d.get("evidence") or []:
                if str(line).startswith("eta_s="):
                    try:
                        return int(float(str(line).split("=", 1)[1]))
                    except ValueError:
                        return None
    return None


def finished(conn, wave_id: str, started_at: str | None, now: datetime | None = None) -> None:
    """סוף הגל: ה-``actual`` מול החזוי, ההמתנה לאדם, וחיזוי המנוע. גל שנכשל או שזמן ההתחלה שלו לא נקרא = "לא נמדד"."""
    try:
        rows = [d for d in memory.decisions(conn, round_id=wave_id, rule=RULE, limit=8) if d["outcome_state"] == "open"]
        if not rows:
            return
        began = _iso(started_at)
        state = conn.execute("SELECT state FROM sessions WHERE id = ?", (wave_id,)).fetchone()
        failed = state is not None and state["state"] == "failed"
        actual = None if began is None or failed else round(((now or datetime.now(timezone.utc)) - began).total_seconds())
        hold, peer = learn_waits.wave_hold(conn, wave_id), _peer_s(conn, wave_id)
        for d in rows:
            want = d.get("predicted")
            guess = int(want) if isinstance(want, (int, float)) and not isinstance(want, bool) and want > 0 else None
            ratio = f" (בפועל/חזוי {actual / guess:.2f})" if actual is not None and guess else ""
            memory.resolve(conn, [d["id"]], (
                f"חזוי מהיסטוריה {linkwarn.duration_he(guess) if guess else 'לא נחזה'}, בפועל "
                f"{linkwarn.duration_he(actual) if actual is not None else 'לא נמדד'}"
                f"{' (הגל נכשל)' if failed else ''}{ratio}; "
                f"המתנה לאדם בגל: {'לא נמדד' if hold is None else str(hold) + ' שניות'}; "
                f"חיזוי המנוע (K3/WAVE_ETA): {'לא נרשם' if peer is None else linkwarn.duration_he(peer)}"),
                actual=actual, matched=forecast.judge(RULE, guess, actual))
    except Exception:                            # noqa: BLE001
        log.exception("brain: the actual time of %s was not recorded", wave_id)
