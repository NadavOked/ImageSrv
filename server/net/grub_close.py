"""מאזין הסוכן (8080) עונה ב-RST כש-GRUB סוגר חיבור של ‎/boot (#1423).

‏GRUB יוצא מאותם פורטי מקור בכל אתחול (21550 ואילך), עם ISN קטן ובלי
timestamps, ואינו עונה ב-RST. כל socket שנשאר אצל השרת על אחד מה-4-tuples
האלה תוקע את האתחול הבא על אותו פורט: 40 SYN ב-15.6 שניות.

שני מקורות, ושניהם נמדדו על מחשב שיכפול 2 (17/10):

1. **TIME_WAIT** — כשהשרת סגר ראשון (`Connection: close`). זה נפתר בכך
   ש-‎/boot כבר אינו שולח `close` (`boot/http.py`), ו-GRUB סוגר ראשון.
2. **LAST-ACK** — החיבור האחרון (צעד `pre-boot`): ‏GRUB שולח FIN עם חלון
   אפס ועובר לאתחל את לינוקס. הוא לעולם לא מאשר את ה-FIN של השרת, והשרת
   נשאר ב-LAST-ACK עשרות שניות. ‏2 מתוך 5 אתחולים חוזרים התנגשו כך.

הפתרון כאן: כש-GRUB סוגר את הצד שלו בחיבור שהבקשה האחרונה בו הייתה
‎/boot והתשובה כבר נשלחה במלואה, השרת סוגר עם SO_LINGER=0, כלומר RST.
אין LAST-ACK ואין TIME_WAIT, ואין מה לאבד: הלקוח סגר אחרי שקרא את כל
הגוף, ומאגר השליחה ריק. חיבורים אחרים (‏API הסוכן) נסגרים כרגיל.
"""

import logging
import socket
import struct

from uvicorn.protocols.http.h11_impl import H11Protocol

log = logging.getLogger("imagesrv.boot")

BOOT_PREFIX = "/boot/"
LINGER_RESET = struct.pack("ii", 1, 0)


def is_finished_boot_cycle(cycle) -> bool:
    """הבקשה האחרונה בחיבור הייתה ‎/boot, והתשובה עליה הושלמה."""
    if cycle is None or not getattr(cycle, "response_complete", False):
        return False
    path = (getattr(cycle, "scope", None) or {}).get("path", "")
    return path.startswith(BOOT_PREFIX)


class ResetBootOnPeerClose(H11Protocol):
    """‏H11Protocol של uvicorn, עם RST במקום FIN כש-GRUB סוגר ראשון."""

    def eof_received(self) -> None:
        if is_finished_boot_cycle(self.cycle):
            sock = self.transport.get_extra_info("socket")
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, LINGER_RESET)
            except (AttributeError, OSError):
                # נשאר FIN רגיל — כמו לפני #1423, לא כישלון של הבקשה.
                log.warning("could not set SO_LINGER on a closing /boot connection",
                            exc_info=True)
        # ‏None: ‏asyncio סוגר את ה-transport. עם linger 0 זו סגירה ב-RST.
        return None


__all__ = ["ResetBootOnPeerClose", "is_finished_boot_cycle"]
