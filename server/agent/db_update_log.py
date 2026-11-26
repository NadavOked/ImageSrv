"""#86: עדכון ה-UEFI db בזמן שחזור — ביומן השרת, עם המצב לפני ואחרי.

הסוכן מכריע ומבצע (`agent/lib/postdeploy/dbupdate.sh`) ומדווח את התוצאה ב-`targets[].db_update`:
``{"state": "applied|refused|failed", "reason", "before": [...], "after": [...]}``. כאן רק מעתיקים
ליומן — **פעם אחת** לכל יעד, במעבר: הדיווח חוזר כל 2 שניות, ושורה נרשמת רק כשהדיווח הקודם של
אותו `dev` לא נשא את אותו מצב. כתיבה למצב האבטחה של מחשב אינה קורית בשקט: גם `refused` (לא נכתב
דבר) נרשם, כי בלעדיו המפעיל רואה רק "Secure Boot יחסום אתחול" ולא למה המחשב לא הוכשר.
"""

from __future__ import annotations

import json
import logging

from server.db import journal, SqlConnection

log = logging.getLogger("imagesrv.db_update_log")

#: המצב -> האירוע ביומן. מצב אחר (סוכן עתידי, קלט פגום) אינו נרשם כאחד מאלה.
EVENTS = {"applied": "db_update_applied", "refused": "db_update_refused", "failed": "db_update_failed"}
_MAX_NAMES = 16
_MAX_TEXT = 200


def _update(target: object) -> dict | None:
    value = target.get("db_update") if isinstance(target, dict) else None
    return value if isinstance(value, dict) and value.get("state") in EVENTS else None


def _names(value: object) -> str:
    """רשימת התעודות כפי שהסוכן קרא אותן. לא רשימה = `?` (לא נקרא), לא "ריק"."""
    if not isinstance(value, list):
        return "?"
    names = [" ".join(v.split())[:_MAX_TEXT] for v in value[:_MAX_NAMES] if isinstance(v, str)]
    return "; ".join(names) or "-"


def _seen(raw: str | None) -> dict[str, str]:
    """dev -> המצב שכבר נרשם בדיווח הקודם. `targets_json` פגום = כלום: עדיף שורה כפולה על שורה
    שלא נרשמה (עיקרון 5)."""
    try:
        targets = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return {}
    return {t["dev"]: u["state"] for t in targets if isinstance(t, dict) and isinstance(t.get("dev"), str)
            and (u := _update(t)) is not None}


def journal_new(conn: SqlConnection, session_id: str, mac: str,
                previous_json: str | None, targets: list) -> None:
    """שורת יומן לכל יעד שבדיווח הזה הגיע למצב חדש של עדכון ה-db. לעולם אינו זורק."""
    try:
        already = _seen(previous_json)
        for t in targets:
            update = _update(t)
            dev = t.get("dev") if isinstance(t, dict) else None
            if update is None or not isinstance(dev, str) or already.get(dev) == update["state"]:
                continue
            reason = " ".join(str(update.get("reason") or "").split())[:_MAX_TEXT]
            journal(conn, EVENTS[update["state"]],
                    f"{mac} {dev} in {session_id} — {reason or '?'}"
                    f" | db before: {_names(update.get('before'))} | db after: {_names(update.get('after'))}",
                    machine=mac, round_id=session_id)
    except Exception:   # noqa: BLE001 — הרישום ליומן אינו עוצר דיווח התקדמות
        log.exception("journaling the db update of %s in %s failed", mac, session_id)
