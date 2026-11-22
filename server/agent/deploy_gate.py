"""רק מחשבים רשומים מגיעים לשירותי השיכפול ברשת ההפצה (הכרעת נדב 14/11, #1079/#2485).

"את רשת ההפצה אני מגן עליה בווילן נפרד, והשרת צריך למנוע ממחשבים לא רשומים
לעבור דרך השרת." השער יושב **לפני** כל נתיב של אפליקציית הסוכן (:7712), גם לפני
קריאת הגוף: בקשה שהגיעה על כתובת ההפצה, מכתובת שהמחשב החכור עליה אינו רשום,
מקבלת 403 בשם (`deploy_unregistered`) ולא מגיעה לשום שירות — הורדת אימג', דיווח,
קליטה, פתיחת סבב, כניסה לקיוסק של מחשב הבנייה, רשימת הסבבים והכיתות.

**מה כן פתוח למחשב לא רשום, ולמה** (עיקרון 1 ו-#1802):
(`/boot/…`, שרשרת האתחול, אינו מוגש כאן עוד -- #3486: הוא במאזין משלו, `create_boot_app`,
והוא שם הפתוח היחיד לכולם. התפריט שלו נקבע בשרת (`boot/grub_menu.py`): דיסק מקומי,
או המסך "המחשב הזה לא רשום" ברשת ההפצה בלבד, נדב 04/11.)
- `POST /api/v1/agent/hello` — התשובה `known: false` היא המסך, והשורה ביומן היא
  איך המפעיל רואה את ה-MAC ורושם אותו. אין בתשובה הזו סבב, משימה או אימג'.

**איך מזהים:** כמו `identity.verify_source` (#997) — כתובת המקור → החכירה של ה-DHCP
שלנו על הכתובת → ה-MAC שלה רשום בטבלת המכונות. נקרא בכל בקשה (בלי מטמון): מכונה
שנרשמה עכשיו עוברת בבקשה הבאה, בלי אתחול של השרת.

**מכונה שרשומה לפי כרטיס אחר (#524, #3225):** החכירה היא של כרטיס האתחול, שאינו רשום.
הוא עובר רק אם ה-hello האחרון **מאותה כתובת** (עד `identity.ALIAS_SECONDS`) צירף אותו
למכונה שעדיין רשומה, אחרי `identity_gate` ו-`verify_extra` (`identity.holder_registered`).
`all_macs` בלי hello כזה, מכתובת אחרת או אחרי שהמכונה נמחקה -- אינו ראיה.

**מה לא נבדק כאן, בכוונה:**
- בקשה על כתובת שאינה כתובת ההפצה (`on_deploy_vlan` = False) — וילן הניהול
  והקונסולה מוגנים בחומת האש של נדב ובהזדהות (נדב 14/11, הבהרה). השער אינו נוגע בהם.
- המתג `identity_check` כבוי (DHCP חיצוני, בגלוי מהקונסולה ובמסך הבריאות) — אין
  חכירה שאפשר לגזור ממנה MAC, ולכן גם כאן אין מה לבדוק. כמו `identity_gate`.
- `ctx.leases is None` — השומר אינו מותקן (בדיקות שאינן מזריקות חכירות).

**היומן:** שורה אחת לכל כתובת בחלון, ואחריה מונה; מעל 32 כתובות שונות בחלון —
שורת הצפה אחת (`_identity_refusal`, #1949/#2498). מחשב לא רשום ששולח בקשה בכל
שנייה אינו מציף את היומן.
"""

from __future__ import annotations

from starlette.concurrency import run_in_threadpool

from server.agent import identity, registry
from server.agent.hello import on_deploy_vlan

#: האירוע ביומן ובקוד השגיאה.
EVENT = "deploy_unregistered"
MESSAGE = "this machine is not registered -- the deploy network serves registered machines only"
#: מה שמחשב לא רשום מקבל (ראו למעלה). כל נתיב אחר — סגור.
OPEN_ROUTES = frozenset({("POST", "/api/v1/agent/hello")})


def is_open(method: str, path: str) -> bool:
    return (method, path) in OPEN_ROUTES


class RegisteredOnly:
    """ASGI middleware. `ctx` = ה-`ServerContext` של האפליקציה, `server_base` = כתובת ההפצה."""

    def __init__(self, app, ctx, server_base: str | None):
        self.app = app
        self.ctx = ctx
        self.server_base = server_base

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or self.ctx.leases is None
                or is_open(scope.get("method", ""), scope.get("path", ""))
                or not on_deploy_vlan(scope, self.server_base)):
            await self.app(scope, receive, send)
            return
        refused = await run_in_threadpool(self._check, scope)
        if refused is None:
            await self.app(scope, receive, send)
            return
        await refused(scope, receive, send)

    def _check(self, scope):
        """None = רשום (או שאין מה לבדוק). אחרת תשובת 403, אחרי שורת יומן מרוסנת."""
        from server.agent.api import _identity_refusal   # noqa: PLC0415 — api מייבא את hello, לא את השער

        ctx = self.ctx
        client = scope.get("client")
        client_ip = client[0] if client else None
        def registered(mac: str) -> bool:
            return registry.lookup(ctx.conn, mac) is not None

        # #3225: כרטיס אתחול שאינו כרטיס הרישום (#524) -- לפי hello שעבר את verify_extra, מאותה כתובת.
        verdict = identity.verify_source(
            ctx.conn, ctx.leases, client_ip,
            lambda mac: identity.holder_registered(ctx.conn, mac, client_ip, registered))
        if not verdict.ok and verdict.event == identity.REFUSED:
            # "המחשב החכור על הכתובת אינו רשום" — האירוע של ההכרעה, בשמו, ולא "התחזות".
            verdict = identity.Verdict(ok=False, event=EVENT, detail=verdict.detail, message=MESSAGE)
        # הנתיב הוא קלט של הפונה: תו בקרה (%0a) לא יפצל את שורת היומן.
        path = "".join(c if c.isprintable() else "?" for c in str(scope.get("path", "?"))[:120])
        return _identity_refusal(ctx, verdict, f"deploy {scope.get('method', '?')} {path}")
