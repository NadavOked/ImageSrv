"""פירוט קליטה חי לקונסולה (#1505): טבלת המחיצות מהסוכן, וקצב / זמן שנותר שהשרת מודד.

הסוכן מדווח עובדות שהוא קורא מהדיסק — לכל מחיצה: אינדקס, תפקיד, מערכת קבצים,
גודל, בשימוש (או `null` = לא נמדד) ומצב. **מה שדורש זמן נמדד כאן**, כי כאן יש
היסטוריה: ‏`bytes_done` של מחיצה נגזר מ-`source_progress` (בלוקים שנקראו מתוך
סך הבלוקים, בציר הלא-דחוס), והקצב — מדגימות ב**חלון** ולא ממוצע מההתחלה.

כלל אחד חוזר (עיקרון 5): "לא נמדד" אינו אפס. קצב ו-ETA הם `None` עד שיש חלון
של לפחות `MIN_SPAN_S` שניות עם התקדמות, וחוזרים ל-`None` כשהסוכן שתק יותר מ-
`STALE_S` (קצב של דיווח ישן הוא לא קצב). ‏ETA דורש גם שכל המחיצות נמדדו —
סכום חלקי אינו מכנה.

הדגימות בזיכרון בלבד (כמו `login_guard`): אתחול שרת מאפס את החלון, והקצב חוזר
להימדד תוך `MIN_SPAN_S`. הטבלה עצמה נשמרת ב-`tasks.targets_json` ולכן שורדת.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import deque

from server.core.progress_view import capture_progress

STATES = ("waiting", "shrinking", "reading", "done")
WINDOW_S = 30.0       # אורך החלון שבו נמדד הקצב
MIN_SPAN_S = 6.0      # פחות מזה — לא נמדד
STALE_S = 15.0        # שתיקה ארוכה מזה — אין קצב
MAX_TASKS = 64        # תקרת זיכרון לדגימות
_WORD = re.compile(r"^[A-Za-z0-9_.+-]{1,32}$")
_COMPRESSION = re.compile(r"^zstd-\d{1,2}$")
_MAX_INT = 9007199254740991

_lock = threading.Lock()
_samples: dict[str, deque] = {}     # task_id -> deque[(t, compressed, read|None)]
_first: dict[str, float] = {}       # task_id -> זמן הדיווח הראשון


def _count(value):
    return value if type(value) is int and 0 <= value <= _MAX_INT else None


def clean_partitions(raw):
    """הרשימה כפי שהסוכן שלח, או `None` אם היא פגומה בכל אופן שהוא.

    רשימה פגומה נזרקת בשלמותה ולא מתוקנת: מחיצה אחת שהושמטה היא סכום "בשימוש"
    שגוי וזמן שנותר שנראה אמין.
    """
    if not isinstance(raw, list) or not 0 < len(raw) <= 128:
        return None
    out, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            return None
        index = item.get("index")
        role, fs, state = item.get("role"), item.get("fs"), item.get("state")
        if (type(index) is not int or not 0 < index <= 128 or index in seen
                or state not in STATES
                or not all(isinstance(v, str) and _WORD.match(v) for v in (role, fs))):
            return None
        used, size = item.get("used_bytes"), item.get("size_bytes")
        if (used is not None and _count(used) is None) or (size is not None and _count(size) is None):
            return None
        seen.add(index)
        out.append({"index": index, "role": role, "fs": fs, "size_bytes": size,
                    "used_bytes": used, "state": state})
    return out


def sanitize(targets):
    """מנקה `partitions` / `compression` של יעדי הקליטה לפני השמירה."""
    for target in targets:
        if not isinstance(target, dict):
            continue
        clean = clean_partitions(target.get("partitions")) if "partitions" in target else None
        if clean is None:
            target.pop("partitions", None)
        else:
            target["partitions"] = clean
        if "compression" in target and not (
                isinstance(target["compression"], str) and _COMPRESSION.match(target["compression"])):
            del target["compression"]


def _table(targets):
    """(מחיצות עם done_bytes, read_bytes, total_used) מהיעד היחיד של הקליטה."""
    if not isinstance(targets, list) or len(targets) != 1 or not isinstance(targets[0], dict):
        return None
    parts = clean_partitions(targets[0].get("partitions"))
    if parts is None:
        return None
    source = capture_progress({"targets_json": json.dumps(targets)})["source_progress"]
    for part in parts:
        used = part["used_bytes"]
        if part["state"] == "done":
            part["done_bytes"] = used
        elif part["state"] == "reading" and used is not None and source and source["partition"] == part["index"]:
            part["done_bytes"] = used * source["blocks_read"] // source["blocks_total"]
        else:
            part["done_bytes"] = 0 if part["state"] in ("waiting", "shrinking") else None
    known = all(p["done_bytes"] is not None and p["used_bytes"] is not None for p in parts)
    total = sum(p["used_bytes"] for p in parts) if known else None
    read = sum(p["done_bytes"] for p in parts) if known else None
    return parts, read, total, targets[0].get("compression")


def record(task_id, targets, compressed, now=None):
    """דגימה מדיווח שהתקבל. נקרא מ-`reports._ingest_task` אחרי השמירה."""
    table = _table(targets)
    now = time.time() if now is None else now
    with _lock:
        if task_id not in _samples and len(_samples) >= MAX_TASKS:
            oldest = min(_first, key=_first.get)
            _samples.pop(oldest, None)
            _first.pop(oldest, None)
        _first.setdefault(task_id, now)
        window = _samples.setdefault(task_id, deque())
        window.append((now, compressed, table[1] if table else None))
        while window and now - window[0][0] > WINDOW_S:
            window.popleft()


def forget(task_id):
    with _lock:
        _samples.pop(task_id, None)
        _first.pop(task_id, None)


def _rate(window, column, now):
    """בייטים/שנייה בין הדגימה הישנה לחדשה בחלון; `None` = לא נמדד."""
    window = [s for s in window if s[column] is not None]
    if len(window) < 2 or now - window[-1][0] > STALE_S:
        return None
    first, last = window[0], window[-1]
    span = last[0] - first[0]
    if span < MIN_SPAN_S:
        return None
    delta = last[column] - first[column]
    return delta / span if delta > 0 else None


def view(row, now=None):
    """`capture_detail` של שורת משימה ב-`/api/console/tasks`; `None` = הסוכן לא דיווח טבלה."""
    try:
        targets = json.loads(row["targets_json"] or "[]")
    except (TypeError, ValueError, KeyError, IndexError):
        return None
    table = _table(targets)
    if table is None:
        return None
    parts, read, total, compression = table
    now = time.time() if now is None else now
    running = row["state"] == "running"
    with _lock:
        window = list(_samples.get(row["id"], ()))
        first = _first.get(row["id"])
    rate = _rate(window, 1, now) if running else None
    read_rate = _rate(window, 2, now) if running else None
    eta = round((total - read) / read_rate) if read_rate and total is not None and read is not None else None
    elapsed = round(window[-1][0] - first) if window and first is not None else None
    return {"partitions": parts, "compression": compression, "read_bytes": read,
            "total_used_bytes": total, "rate_bps": None if rate is None else round(rate),
            "eta_s": eta, "elapsed_s": elapsed}
