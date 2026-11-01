"""‏OAuth לשליחת מייל — device code (RFC 8628) ו-XOAUTH2 (#1751).

למה device code: השרת אינו צריך כתובת חזרה (redirect) שהדפדפן של המפעיל מגיע
אליה. השרת מבקש קוד, הקונסולה מציגה קוד וכתובת, המפעיל מאשר בדפדפן **שלו**
(במחשב שלו, עם האינטרנט שלו), והשרת מקבל refresh token ושומר אותו ב-0600.
בשליחה: refresh → access token (בזיכרון בלבד) → ‏``AUTH XOAUTH2``.

**מה צריך להיות פתוח מהשרת** (docs/email-alerts.md): HTTPS יוצא לספק הזהות
(Microsoft: ‏``login.microsoftonline.com``; ‏Google: ‏``oauth2.googleapis.com``) —
בבקשת הקוד, בהמתנה לאישור, ובכל חידוש אסימון — ו-SMTP יוצא לשרת הדואר. בלי
יציאה לאינטרנט מרשת הניהול, ‏"התחבר" נכשל **בצעד הראשון** (בקשת הקוד), בגלוי.

**‏client id:** לכל ספק צריך מזהה של אפליקציה רשומה. אין כאן מזהה "ציבורי"
של מוצר אחר — שימוש בזהות של אפליקציה אחרת הוא התחזות. המכללה רושמת
אפליקציה (Azure: public client, ‏SMTP.Send) ומזינה את המזהה. הפירוט במסמך.

כל קריאת רשת עוברת דרך ``http`` מוזרק (``http_post``) — הבדיקות לא יוצאות לרשת.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from server.system import email_config

log = logging.getLogger("imagesrv.email_alerts")

#: נקודות הקצה. ‏``{tenant}`` — של המכללה, או ``organizations``. ‏``host`` — המארח
#: היחיד שמותר לפנות אליו בשם הספק; כל URL אחר נדחה לפני הבקשה (סקירת אבטחה: SSRF).
OAUTH: dict[str, dict] = {
    "microsoft": {
        "host": "login.microsoftonline.com",
        "device": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/devicecode",
        "token": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        "scope": "https://outlook.office.com/SMTP.Send offline_access",
    },
    # ‏Google מגביל את ה-scopes של device code; ‏``https://mail.google.com/`` (הנדרש ל-SMTP)
    # עשוי להידחות ב-``invalid_scope``. המנגנון כאן אחיד, והתשובה של Google מוצגת כמו שהיא.
    "google": {
        "host": "oauth2.googleapis.com",
        "device": "https://oauth2.googleapis.com/device/code",
        "token": "https://oauth2.googleapis.com/token",
        "scope": "https://mail.google.com/",
    },
}
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
TIMEOUT_SECONDS = 15.0


class OAuthError(RuntimeError):
    """כשל OAuth — ההודעה בעברית ועם תשובת הספק.

    ‏``public`` — מה שהקונסולה מציגה: הניסוח שלנו ותשובת הספק, מנוקים ומקוצרים
    (``email_config.public_text``). הקונסולה קוראת אותו ולא את ``str(exc)``
    (CodeQL ‏py/stack-trace-exposure, ‏PR ציבורי #54)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.public = email_config.public_text(message)


def http_post(url: str, fields: dict, timeout: float = TIMEOUT_SECONDS) -> tuple[int, dict]:
    """‏POST של טופס; מחזיר (קוד, JSON). ‏4xx של ספק OAuth הוא תשובה (``error``),
    לא חריגה. אין חיבור / תם הזמן — ``OAuthError`` בשמו."""
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:          # noqa: S310 — https קבוע
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {"error": f"HTTP {exc.code}"}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        host = urllib.parse.urlsplit(url).hostname
        # ליומן רק סוג החריגה ו-errno: הטקסט שלה עלול לשאת את גוף הבקשה
        # (client_secret, ‏refresh_token). למפעיל — ניסוח קבוע.
        errno = getattr(getattr(exc, "reason", exc), "errno", None)
        # בלי שם השרת: הוא נגזר מטבלת הספקים (מפתח "token"), ו-CodeQL קורא
        # אותו כסוד (PR ציבורי #56). המפעיל רואה אותו בהודעה למטה.
        log.warning("email oauth: POST to the identity provider failed: %s (errno %s)",
                    type(exc).__name__, errno)
        raise OAuthError(f"אין חיבור ל-{host} — לשרת צריכה להיות יציאה ב-HTTPS "
                         "לספק הזהות כדי להתחבר ולחדש את האסימון (הפרטים ביומן השרת)") from None


def _endpoints(acc: dict) -> dict:
    provider = email_config.PROVIDERS.get(acc.get("provider"), {}).get("oauth")
    if provider not in OAUTH:
        raise OAuthError("הספק של החשבון הזה אינו תומך ב-OAuth")
    if not acc.get("oauth_client_id"):
        raise OAuthError("חסר מזהה אפליקציה (client id) — המכללה צריכה לרשום אפליקציה; "
                         "ההוראות ב-docs/email-alerts.md")
    tenant = acc.get("oauth_tenant") or "organizations"
    if not email_config.valid_tenant(tenant):
        raise OAuthError(f"tenant לא תקין: {tenant!r}")
    ep = OAUTH[provider]
    quoted = urllib.parse.quote(tenant, safe="")
    out = {"provider": provider, "device": ep["device"].format(tenant=quoted),
           "token": ep["token"].format(tenant=quoted), "scope": ep["scope"]}
    for key in ("device", "token"):
        parts = urllib.parse.urlsplit(out[key])
        if parts.scheme != "https" or parts.hostname != ep["host"]:
            raise OAuthError(f"כתובת ספק הזהות אינה המארח הצפוי ({ep['host']}): {out[key]}")
    return out


def _explain(body: dict) -> str:
    """תשובת הספק (``error``, ‏``error_description``) — שימושית למפעיל, ולכן עוברת,
    אבל מנוקה ומקוצרת."""
    text = " — ".join(str(x) for x in (body.get("error"), body.get("error_description")) if x)
    return email_config.public_text(text) or "תשובה לא צפויה"


def fingerprint(acc: dict) -> tuple:
    """הטביעה של **היעד** של החשבון — שדות ``RETARGET``. התחברות שהתחילה מול יעד
    אחד אינה נשמרת לחשבון שהיעד שלו שונה בינתיים."""
    return tuple(acc.get(key) for key in email_config.RETARGET)


class DeviceFlows:
    """בקשות התחברות פתוחות, בזיכרון בלבד (``device_code`` לעולם אינו יוצא מהשרת).

    סקירת אבטחה (#1751): התחברות שהתחילה לפני שינוי יעד, מחיקה או ניתוק לא
    שומרת אסימון. שני שומרים: ``cancel`` נקרא מכל מקום שמוחק סודות; ו-``poll``
    משווה את טביעת היעד לזו שנשמרה ב-``start``. השמירה עצמה נעשית תחת הנעילה,
    ורק אם הבקשה עדיין פתוחה **אחרי** תשובת הספק — ``cancel`` שקרה בזמן הבקשה
    לספק גובר."""

    def __init__(self, http=http_post, clock=time.monotonic):
        self.http, self.clock = http, clock
        self._open: dict[str, dict] = {}
        #: דור לכל חשבון; ``cancel`` מגדיל. ‏``start`` שהדור השתנה בזמן הבקשה לספק
        #: אינו פותח התחברות (cancel שהגיע באמצע ``start`` גובר).
        self._gen: dict[str, int] = {}
        self._lock = threading.Lock()

    def is_open(self, account_id: str) -> bool:
        with self._lock:
            return account_id in self._open

    def start(self, acc: dict, data_dir) -> dict:
        ep = _endpoints(acc)
        with self._lock:
            generation = self._gen.get(acc["id"], 0)
        status, body = self.http(ep["device"], {"client_id": acc["oauth_client_id"],
                                                "scope": ep["scope"]})
        if status != 200 or not body.get("device_code") or not body.get("user_code"):
            raise OAuthError(f"בקשת קוד ההתחברות נדחתה (HTTP {status}): {_explain(body)}")
        interval = int(body.get("interval") or 5)
        expires = int(body.get("expires_in") or 900)
        with self._lock:
            if self._gen.get(acc["id"], 0) != generation:
                raise OAuthError("ההתחברות בוטלה — החשבון שונה, נמחק או נותק בזמן שהיא התחילה")
            self._open[acc["id"]] = {"device_code": body["device_code"], "interval": interval,
                                     "until": self.clock() + expires, "fp": fingerprint(acc)}
        return {"user_code": body["user_code"],
                "verification_uri": body.get("verification_uri") or body.get("verification_url"),
                "expires_in": expires, "interval": interval, "message": body.get("message")}

    def cancel(self, account_id: str) -> bool:
        """סוגר התחברות פתוחה של החשבון. ‏``True`` = הייתה כזו."""
        with self._lock:
            self._gen[account_id] = self._gen.get(account_id, 0) + 1
            return self._open.pop(account_id, None) is not None

    def poll(self, acc: dict, data_dir) -> dict:
        """שאלה אחת לספק. ‏``connected`` רק כשהגיע refresh token ונכתב ל-0600."""
        with self._lock:
            pending = self._open.get(acc["id"])
        if pending is None:
            return {"state": "idle", "message": "אין בקשת התחברות פתוחה"}
        if self.clock() > pending["until"]:
            with self._lock:
                self._open.pop(acc["id"], None)
            return {"state": "expired", "message": "הקוד פג — לחצו \"התחבר\" שוב"}
        if fingerprint(acc) != pending["fp"]:
            self.cancel(acc["id"])
            return {"state": "cancelled", "message": "החשבון שונה מאז שההתחברות התחילה — לחצו \"התחבר\" שוב"}
        ep = _endpoints(acc)
        fields = {"client_id": acc["oauth_client_id"], "grant_type": DEVICE_GRANT,
                  "device_code": pending["device_code"]}
        stored = email_config.read_oauth(data_dir, acc["id"])
        if stored.get("client_secret"):
            fields["client_secret"] = stored["client_secret"]
        status, body = self.http(ep["token"], fields)
        error = body.get("error")
        if error in ("authorization_pending", "slow_down"):
            return {"state": "pending", "message": "ממתין לאישור בדפדפן",
                    "interval": pending["interval"] + (5 if error == "slow_down" else 0)}
        with self._lock:
            if self._open.get(acc["id"]) is not pending:
                # בוטלה בזמן הבקשה לספק (שינוי יעד / מחיקה / ניתוק) — לא שומרים דבר.
                return {"state": "cancelled", "message": "ההתחברות בוטלה — לחצו \"התחבר\" שוב"}
            self._open.pop(acc["id"], None)
            if status != 200 or not body.get("refresh_token"):
                return {"state": "error", "message": f"ההתחברות נכשלה (HTTP {status}): {_explain(body)}"}
            save_token(data_dir, acc["id"], body["refresh_token"], stored.get("client_secret"))
            TOKENS.put(acc["id"], body.get("access_token"), body.get("expires_in"))
        return {"state": "connected", "message": "החשבון מחובר"}


def save_token(data_dir, account_id: str, refresh_token: str | None, client_secret: str | None) -> None:
    doc = {k: v for k, v in (("refresh_token", refresh_token), ("client_secret", client_secret)) if v}
    email_config.write_secret(data_dir, account_id, "oauth", json.dumps(doc))


class _TokenCache:
    """‏access tokens בזיכרון בלבד, עד דקה לפני שפגו."""

    def __init__(self):
        self._tokens: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def put(self, account_id: str, token: str | None, expires_in) -> None:
        if token:
            with self._lock:
                self._tokens[account_id] = (token, time.monotonic() + int(expires_in or 3600) - 60)

    def get(self, account_id: str) -> str | None:
        with self._lock:
            token, until = self._tokens.get(account_id, (None, 0.0))
        return token if token and time.monotonic() < until else None

    def drop(self, account_id: str) -> None:
        with self._lock:
            self._tokens.pop(account_id, None)


TOKENS = _TokenCache()


def access_token(acc: dict, data_dir, http=http_post) -> str:
    """אסימון גישה לשליחה: מהזיכרון, או חידוש מה-refresh token (וסיבוב שלו כשהספק
    מחזיר חדש). כל כשל — ``OAuthError`` גלוי, והשליחה נכשלת עם הסיבה."""
    cached = TOKENS.get(acc["id"])
    if cached:
        return cached
    stored = email_config.read_oauth(data_dir, acc["id"])
    if not stored.get("refresh_token"):
        raise OAuthError("החשבון אינו מחובר — לחצו \"התחבר\"")
    ep = _endpoints(acc)
    fields = {"client_id": acc["oauth_client_id"], "grant_type": "refresh_token",
              "refresh_token": stored["refresh_token"], "scope": ep["scope"]}
    if stored.get("client_secret"):
        fields["client_secret"] = stored["client_secret"]
    status, body = http(ep["token"], fields)
    if status != 200 or not body.get("access_token"):
        raise OAuthError(f"חידוש האסימון נכשל (HTTP {status}): {_explain(body)} — ייתכן שצריך להתחבר מחדש")
    if body.get("refresh_token") and body["refresh_token"] != stored["refresh_token"]:
        save_token(data_dir, acc["id"], body["refresh_token"], stored.get("client_secret"))
    TOKENS.put(acc["id"], body["access_token"], body.get("expires_in"))
    return body["access_token"]


def xoauth2(user: str, token: str) -> str:
    """המחרוזת של ``AUTH XOAUTH2`` (לפני base64, ש-smtplib מוסיף)."""
    return f"user={user}\x01auth=Bearer {token}\x01\x01"
