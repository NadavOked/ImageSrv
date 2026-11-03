"""Live console viewer sessions keyed by the signed console cookie.

‏#1853: כמה צופים לכל עוגייה — צופה ה-VM (#1394) ומוניטור לכל מכונה (מקומי
או דרך שרת משנה) מאותה לשונית/דפדפן."""

from __future__ import annotations

import asyncio
import threading
import weakref

_Item = tuple[str, asyncio.AbstractEventLoop, asyncio.Task]

_lock = threading.Lock()
_tasks: dict[str, list[_Item]] = {}
# ‏#1394: המשימות שבוטלו *כאן*. ביטול של ה-endpoint קורה גם בפירוק החיבור
# (#725), והצופה צריך ראיה חיובית שהביטול הוא ביטול התחברות — לא ניחוש.
_revoked: weakref.WeakSet[asyncio.Task] = weakref.WeakSet()


def register(token: str, username: str, task: asyncio.Task) -> None:
    with _lock:
        _tasks.setdefault(token, []).append(
            (username.casefold(), asyncio.get_running_loop(), task))


def forget(token: str, task: asyncio.Task) -> None:
    with _lock:
        rest = [item for item in _tasks.get(token, []) if item[2] is not task]
        if rest:
            _tasks[token] = rest
        else:
            _tasks.pop(token, None)


def was_revoked(task: asyncio.Task) -> bool:
    with _lock:
        return task in _revoked


def _cancel(items: list[_Item]) -> None:
    for _username, loop, task in items:
        loop.call_soon_threadsafe(task.cancel)


def revoke(token: str | None) -> bool:
    if not token:
        return False
    with _lock:
        items = _tasks.pop(token, [])
        for item in items:
            _revoked.add(item[2])
    _cancel(items)
    return bool(items)


def revoke_user(username: str) -> int:
    name = username.casefold()
    found: list[_Item] = []
    with _lock:
        for token in list(_tasks):
            mine = [item for item in _tasks[token] if item[0] == name]
            if not mine:
                continue
            rest = [item for item in _tasks[token] if item[0] != name]
            if rest:
                _tasks[token] = rest
            else:
                del _tasks[token]
            found.extend(mine)
        for item in found:
            _revoked.add(item[2])
    _cancel(found)
    return len(found)
