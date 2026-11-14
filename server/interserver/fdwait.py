"""#3180: המתנה ל-socket בלי תקרת ה-1024 של ``select.select``.

``select.select`` בנוי על ``fd_set`` בגודל קבוע (FD_SETSIZE, 1024 בלינוקס),
וזורק ``ValueError: filedescriptor out of range in select()`` על כל socket
שמספר ה-fd שלו מעל התקרה. תהליך עם הרבה קבצים או חיבורים פתוחים — שרת
עמוס, או worker של pytest תחת כיסוי — מגיע לשם, והחיבור הבין-שרתי נופל
על שגיאה שאין לה קשר למשני. ``selectors`` (epoll/poll בלינוקס) אינו מוגבל.
"""

import selectors


def wait_io(rlist, wlist, timeout: float) -> tuple[list, list]:
    """תחליף ל-``select.select(rlist, wlist, [], timeout)``: מחזיר
    ``(readable, writable)`` — רשימות ריקות כשפג הזמן. אותו אובייקט יכול
    להופיע בשתי הרשימות."""
    events: dict = {}
    for obj in rlist:
        events[obj] = events.get(obj, 0) | selectors.EVENT_READ
    for obj in wlist:
        events[obj] = events.get(obj, 0) | selectors.EVENT_WRITE
    readable, writable = [], []
    with selectors.DefaultSelector() as sel:
        for obj, mask in events.items():
            sel.register(obj, mask)
        for key, ready in sel.select(timeout):
            if ready & selectors.EVENT_READ:
                readable.append(key.fileobj)
            if ready & selectors.EVENT_WRITE:
                writable.append(key.fileobj)
    return readable, writable
