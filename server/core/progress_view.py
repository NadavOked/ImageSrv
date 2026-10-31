"""Normalize legacy totals and expose capture's separate source-block axis."""

import json


def _field(row, name, default=None):
    """‏sqlite3.Row זורק IndexError על עמודה חסרה, לא מחזיר None.

    שורה שאין בה העמודה אינה "אין התקדמות" — היא שאילתה אחרת. קיפול
    השניים היה מפיל כל hello שאינו נושא targets_json (#446/#435).
    """
    try:
        return row[name]
    except (IndexError, KeyError, TypeError):
        return default


def capture_progress(row):
    source = None
    targets = json.loads(_field(row, "targets_json") or "[]")
    if len(targets) == 1:
        candidate = targets[0].get("source_progress")
        if isinstance(candidate, dict):
            part, read, total = (candidate.get(k) for k in
                                 ("partition", "blocks_read", "blocks_total"))
            if (all(type(n) is int for n in (part, read, total))
                    and 0 < part <= 128 and 0 <= read <= total
                    and 0 < total <= 9007199254740991):
                source = {"partition": part, "blocks_read": read, "blocks_total": total}
                # ‏#1254: "מחיצה K מתוך M" — רק כששניהם שלמים ועקביים. סתירה
                # מורידה את K/M בלבד; האחוז עצמו נשאר נכון.
                seq, count = (candidate.get(k) for k in ("partition_seq", "partitions_total"))
                if all(type(n) is int for n in (seq, count)) and 0 < seq <= count <= 128:
                    source.update(partition_seq=seq, partitions_total=count)
    return {"bytes_written": _field(row, "bytes_written", 0),
            "bytes_total": _field(row, "bytes_total") or None,
            "source_progress": source}
