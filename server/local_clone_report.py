"""#1322 D: what a cloning machine reports after a local disk-to-disk clone.

The copy itself never passes through the server -- the machine copies one of
its drawers to the others and works with the server down. When the server
answers, the agent (``agent/lib/localclone.sh`` ``lc_report``) sends what it
did, and it becomes one journal line, labelled as the machine's own report.
Best effort from the agent's side: a refused report changes nothing on the
machine's screen. Nothing here is trusted beyond a line of text -- it is not
a round, not a disk failure record, and it moves no state.
#1466 adds a read-only shrink path and optional stage timers to that report.
"""

from __future__ import annotations

from typing import Any

#: one error text from the machine, at most -- the journal is read by people
ERROR_MAX = 200


class BadReport(ValueError):
    """The body is not a local clone report."""


def _drawer(item: Any) -> str:
    if not isinstance(item, dict):
        raise BadReport("a drawer is not an object")
    port = item.get("port")
    dev = item.get("dev")
    if isinstance(port, int) and not isinstance(port, bool) and 0 < port < 100:
        return f"דיסק {port}"
    if isinstance(dev, str) and dev.isalnum() and len(dev) <= 16:
        return dev
    raise BadReport("a drawer has neither a port nor a device name")


def _amount(value: Any) -> str:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return f"{value / 1e9:.1f} GB"
    return "כמות לא נמדדה"


def _stages(body: dict) -> str:
    """Return the measured local-clone stages in journal display order."""
    fields = (
        ("info_s", "מדידה {v}ש'"),
        ("resize_s", "כיווץ {v}ש'"),
        ("relocations_mb", "הועברו {v}MB"),
        ("copy_s", "העתקה {v}ש'"),
        ("verify_s", "אימות {v}ש'"),
        ("restore_s", "סיום {v}ש'"),
    )
    measured = []
    for key, label in fields:
        value = body.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            measured.append(label.format(v=value))
    return ", ".join(measured)


def _note(item: Any) -> str:
    """#2049: a done drawer's ``ntfs_note`` (#2009) as one journal-sized line; "" = none."""
    note = item.get("ntfs_note") if isinstance(item, dict) else None
    return " ".join(note.split())[:ERROR_MAX] if isinstance(note, str) else ""


def has_note(body: dict) -> bool:
    """#2049: a done drawer carries a note -- the journal row is a warning (orange), not info."""
    targets = body.get("targets")
    return isinstance(targets, list) and any(
        isinstance(t, dict) and t.get("state") == "done" and _note(t) for t in targets)


def journal_line(body: dict) -> str:
    """The journal's detail for one report, or BadReport."""
    source = _drawer(body.get("source"))
    targets = body.get("targets")
    if not isinstance(targets, list) or not targets or len(targets) > 8:
        raise BadReport("targets must be a list of 1-8 drawers")
    parts = []
    for item in targets:
        name = _drawer(item)
        state = item.get("state")
        if state == "done":
            note = _note(item)
            parts.append(f"{name} הושלם — {note}" if note else f"{name} הצליח")
        elif state == "failed":
            error = item.get("error") if isinstance(item.get("error"), str) else ""
            error = " ".join(error.split())[:ERROR_MAX]
            parts.append(f"{name} נכשל" + (f" ({error})" if error else ""))
        else:
            raise BadReport(f"unknown target state: {state!r}")
    elapsed = body.get("elapsed_s")
    when = (f"{elapsed // 60}:{elapsed % 60:02d}"
            if isinstance(elapsed, int) and not isinstance(elapsed, bool) and elapsed >= 0 else "זמן לא נמדד")
    line = f"מקור {source} → " + ", ".join(parts) + f" · {_amount(body.get('bytes'))} · {when}"
    if body.get("stopped") is True:
        line += " · נעצר באמצע"
    if body.get("shrunk") is True:
        restore = body.get("source_restore")
        line += " · המקור כווץ זמנית " + ("והוחזר לגודלו" if restore == "ok"
                                          else "ולא הוחזר לגודלו — לבדוק את המקור")
    shrink_path = body.get("shrink_path")
    if shrink_path == "snapshot":
        line += " · כיווץ על עותק בזיכרון — המקור נקרא בלבד ולא נכתב"
        teardown = body.get("snapshot_teardown")
        if isinstance(teardown, str) and teardown not in ("", "ok"):
            error = " ".join(teardown.split())[:ERROR_MAX]
            line += f" · העותק בזיכרון לא שוחרר ({error}) — לאתחל את המחשב לפני שיכפול נוסף"
    elif shrink_path == "refused":
        line += " · יעד קטן סורב: לא היה עותק בזיכרון (device-mapper או מקום) — המקור לא נכתב"
    stages = _stages(body)
    if stages:
        line += " · שלבים: " + stages
    late = body.get("late_s")   # #1913: sent again after the server came back
    if isinstance(late, int) and not isinstance(late, bool) and late >= 0:
        line += f" · דוח מאוחר: השרת לא ענה בסוף השיכפול, והדוח נשלח {late // 60}:{late % 60:02d} אחריו"
    return line + " (דיווח המחשב — השיכפול לא עבר דרך השרת)"
