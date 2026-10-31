"""Live console viewer sessions keyed by the signed console cookie."""

from __future__ import annotations

import asyncio
import threading
import weakref

_lock = threading.Lock()
_tasks: dict[str, tuple[str, asyncio.AbstractEventLoop, asyncio.Task]] = {}
# ‏#1394: המשימות שבוטלו *כאן*. ביטול של ה-endpoint קורה גם בפירוק החיבור
# (#725), והצופה צריך ראיה חיובית שהביטול הוא ביטול התחברות — לא ניחוש.
_revoked: weakref.WeakSet[asyncio.Task] = weakref.WeakSet()


def register(token: str, username: str, task: asyncio.Task) -> None:
    with _lock:
        _tasks[token] = (username.casefold(), asyncio.get_running_loop(), task)


def forget(token: str, task: asyncio.Task) -> None:
    with _lock:
        if _tasks.get(token, (None, None, None))[2] is task:
            _tasks.pop(token, None)


def was_revoked(task: asyncio.Task) -> bool:
    with _lock:
        return task in _revoked


def revoke(token: str | None) -> bool:
    if not token:
        return False
    with _lock:
        item = _tasks.pop(token, None)
        if item is not None:
            _revoked.add(item[2])
    if item is None:
        return False
    _username, loop, task = item
    loop.call_soon_threadsafe(task.cancel)
    return True


def revoke_user(username: str) -> int:
    with _lock:
        found = [(token, value) for token, value in _tasks.items()
                 if value[0] == username.casefold()]
        for token, value in found:
            _tasks.pop(token, None)
            _revoked.add(value[2])
    for _token, (_username, loop, task) in found:
        loop.call_soon_threadsafe(task.cancel)
    return len(found)
