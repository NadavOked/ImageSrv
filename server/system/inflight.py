"""עבודה שחיה רק בתוך תהליך השרת ואין לה שורה ב-DB (#1891).

העלאת אימג' מהקונסולה וקבלת אימג' מהראשי (במשני) הן זרם בתוך בקשה אחת.
restart של השרת באמצע חותך אותן, ולכן `auto_reboot.busy_reason` שואל כאן.
המונה בזיכרון במכוון: הוא מת עם התהליך, ולכן אין "יתום" שיחסום אחרי
אתחול. `server-upgrade.sh` רץ בתהליך נפרד ואינו רואה אותו — "החל עדכון"
בודק אותו לפני שהוא משגר את הסקריפט, כמו עדכון VM.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

_lock = threading.Lock()
_active: dict[str, int] = {}


@contextmanager
def holding(what: str) -> Iterator[None]:
    """``what`` — מה רץ, כפי שיופיע בסיבת הסירוב."""
    with _lock:
        _active[what] = _active.get(what, 0) + 1
    try:
        yield
    finally:
        with _lock:
            _active[what] -= 1
            if not _active[what]:
                del _active[what]


def running() -> list[str]:
    """מה רץ עכשיו (ממוין), או רשימה ריקה."""
    with _lock:
        return sorted(_active)
