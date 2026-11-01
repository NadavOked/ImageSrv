"""מבנה ההתראות במייל (#1751) — כמו ה-Notification Center של QNAP, בשלושה חלקים.

1. **חשבונות שליחה** (``accounts``): איך שולחים. ספק מרשימה (``PROVIDERS``)
   ממלא מארח, פורט והצפנה; המפעיל רואה אותם רק ב"SMTP מותאם". אימות:
   ``none`` (relay / Direct Send), ``password`` או ``oauth`` (device code,
   ‏``email_oauth``).
2. **כללים** (``rules``): על מה ולמי. כל כלל: קטגוריות, רמת חומרה מינימלית,
   נמענים — **ושפה לכל נמען** — וחשבון שולח. **כלל ברירת המחדל נוצר לבד**
   עם הנמען הראשון (``add_recipient``): שגיאות ואזהרות מכל הקטגוריות, בשפת
   הממשק. המסלול הרגיל לא בונה כלל (נדב: "אני לא זוכר שהגדרתי הרבה ב-QNAP").
3. **היסטוריה** — בתור (``email_alerts.history``).

**סודות** — סיסמה ואסימון OAuth — בקבצי ``0600`` תחת ``<data_dir>/email-accounts``
(‏``interserver_auth.write_credential_0600``), כמו שאר הסודות של השרת. לא ב-DB,
לא ביומן, לא בשום GET. **שינוי יעד בלי סוד חדש מוחק את הסודות** (סקירת
אבטחה): מי שמחליף ספק/מארח/פורט/הצפנה/אימות/משתמש/אפליקציה בלי להזין
סיסמה באותה בקשה לא יקבל את הסיסמה או את האסימון בשליחה הבאה.
"""

from __future__ import annotations

import json
import re
import secrets
from pathlib import Path

from server.interserver import auth as interserver_auth
from server.system import email_text
from server.db import get_setting, set_setting

SETTING = "email_alerts"

#: סוגי ההתראה, בסדר שהמסך מציג.
KINDS: tuple[str, ...] = ("update_available", "update_installed", "update_failed",
                          "server_started", "auto_reboot", "round_failed", "disk_red",
                          "low_space", "peer")
SEVERITIES = ("info", "warn", "err")
KIND_SEVERITY: dict[str, str] = {
    "update_available": "info", "update_installed": "info", "update_failed": "err",
    "server_started": "warn", "auto_reboot": "info", "round_failed": "err",
    "disk_red": "err", "low_space": "warn", "peer": "err",
}
#: קטגוריות (נדב 18/10): שיכפול, דיסקים, עדכונים, מערכת, שרתי משנה.
CATEGORIES: dict[str, tuple[str, ...]] = {
    "cloning": ("round_failed",),
    "disks": ("disk_red",),
    "updates": ("update_available", "update_installed", "update_failed"),
    "system": ("server_started", "auto_reboot", "low_space"),
    "peers": ("peer",),
}
KIND_CATEGORY = {kind: cat for cat, kinds in CATEGORIES.items() for kind in kinds}

SECURITY = ("starttls", "tls", "none")
AUTH = ("none", "password", "oauth")
#: ספקים: מה הבחירה ממלאת. ‏``host=None`` = נגזר (Direct Send) או נשאר למפעיל
#: (relay). ‏``auth`` = האימותים המותרים, הראשון הוא ברירת המחדל. ‏``oauth`` =
#: ספק ה-OAuth (``email_oauth.OAUTH``).
PROVIDERS: dict[str, dict] = {
    "office365": {"host": "smtp.office365.com", "port": 587, "security": "starttls",
                  "auth": ("oauth", "password"), "oauth": "microsoft"},
    "o365_direct": {"host": None, "port": 25, "security": "starttls", "auth": ("none",)},
    "relay": {"host": None, "port": 25, "security": "none", "auth": ("none",)},
    "gmail": {"host": "smtp.gmail.com", "port": 587, "security": "starttls",
              "auth": ("oauth", "password"), "oauth": "google"},
    "custom": {"host": None, "port": 587, "security": "starttls", "auth": ("none", "password")},
}
#: מה שמשנה **לאן** הסודות נשלחים (ראו בראש הקובץ).
RETARGET = ("provider", "host", "port", "security", "auth", "username",
            "oauth_client_id", "oauth_tenant")
MAX_RECIPIENTS = 20
MAX_RULES = 20
MAX_ACCOUNTS = 5

_ADDR = re.compile(r"^[A-Za-z0-9.!#$%&*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
_HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
_TOKEN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class ConfigError(ValueError):
    """הגדרה שנדחתה — ההודעה בעברית, למפעיל."""


# --- טעינה ושמירה -----------------------------------------------------------------

def empty() -> dict:
    return {"accounts": [], "rules": []}


def load(conn) -> tuple[dict, str | None]:
    """(מסמך, שגיאה). מסמך שלא נקרא = ריק, **והשגיאה נאמרת** (עיקרון 5)."""
    raw = get_setting(conn, SETTING)
    if not raw:
        return empty(), None
    try:
        doc = json.loads(raw)
        if not isinstance(doc, dict) or not isinstance(doc.get("accounts", []), list) \
                or not isinstance(doc.get("rules", []), list):
            raise ValueError("מבנה לא צפוי")
    except ValueError as exc:
        return empty(), f"הגדרות ההתראות שמורות פגומות ({exc}) — שום מייל לא יוצא עד שמירה מחדש"
    return {"accounts": doc.get("accounts", []), "rules": doc.get("rules", [])}, None


def save(conn, doc: dict) -> None:
    set_setting(conn, SETTING, json.dumps(doc, ensure_ascii=False))


def account(doc: dict, account_id: str) -> dict | None:
    return next((a for a in doc["accounts"] if a["id"] == account_id), None)


def rule(doc: dict, rule_id: str) -> dict | None:
    return next((r for r in doc["rules"] if r["id"] == rule_id), None)


def default_account(doc: dict) -> dict | None:
    return doc["accounts"][0] if doc["accounts"] else None


# --- סודות: קבצי 0600 ------------------------------------------------------------

def _secret_dir(data_dir) -> Path:
    return Path(data_dir) / "email-accounts"


def secret_path(data_dir, account_id: str, what: str) -> Path:
    """``what`` הוא ``password`` או ``oauth``; המזהה נבדק — הוא נכנס לשם קובץ."""
    if not _TOKEN.match(account_id) or what not in ("password", "oauth"):
        raise ValueError(f"מזהה חשבון לא תקין: {account_id!r}")
    return _secret_dir(data_dir) / f"{account_id}.{what}"


def write_secret(data_dir, account_id: str, what: str, value: str) -> None:
    _secret_dir(data_dir).mkdir(parents=True, exist_ok=True)
    interserver_auth.write_credential_0600(secret_path(data_dir, account_id, what), value)


def read_secret(data_dir, account_id: str, what: str) -> str | None:
    """``None`` = אין. קובץ לא בטוח זורק ``CredentialFileError`` — לא "אין"."""
    path = secret_path(data_dir, account_id, what)
    if not path.exists():
        return None
    return interserver_auth.load_credential(path)


def clear_secrets(data_dir, account_id: str) -> None:
    for what in ("password", "oauth"):
        secret_path(data_dir, account_id, what).unlink(missing_ok=True)


def read_oauth(data_dir, account_id: str) -> dict:
    raw = read_secret(data_dir, account_id, "oauth")
    if not raw:
        return {}
    doc = json.loads(raw)
    return doc if isinstance(doc, dict) else {}


def blocked_reason(acc: dict, data_dir) -> str | None:
    """למה אין לשלוח מהחשבון הזה עכשיו, או ``None``. נבדק לפני כל ניסיון —
    חשבון חסום **אינו** צורך ניסיונות בתור."""
    try:
        if acc.get("auth") == "password" and not read_secret(data_dir, acc["id"], "password"):
            return ("אין סיסמה שמורה — היא נמחקה כשהספק, המארח, הפורט, ההצפנה או המשתמש שונו "
                    "בלי סיסמה, או שעוד לא הוזנה. הזינו סיסמה כדי לחדש את השליחה")
        if acc.get("auth") == "oauth" and not read_oauth(data_dir, acc["id"]).get("refresh_token"):
            return "החשבון אינו מחובר — לחצו \"התחבר\" ואשרו בדפדפן"
    except Exception as exc:                                   # noqa: BLE001
        return f"קובץ הסוד של החשבון לא נקרא: {exc}"
    return None


def account_public(acc: dict, data_dir) -> dict:
    """החשבון כפי שהקונסולה רואה אותו — **בלי** סיסמה ובלי אסימון."""
    out = dict(acc)
    error = None
    try:
        password_set = bool(data_dir) and read_secret(data_dir, acc["id"], "password") is not None
        oauth = read_oauth(data_dir, acc["id"]) if data_dir else {}
    except Exception as exc:                                   # noqa: BLE001
        password_set, oauth, error = False, {}, f"קובץ הסוד לא נקרא: {exc}"
    out.update({"password_set": password_set, "oauth_connected": bool(oauth.get("refresh_token")),
                "oauth_client_secret_set": bool(oauth.get("client_secret")),
                "secret_error": error, "blocked": blocked_reason(acc, data_dir) if data_dir else None})
    return out


# --- אימות ---------------------------------------------------------------------

def _clean_str(value, what: str, limit: int = 256) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{what}: נדרש טקסט")
    value = value.strip()
    if len(value) > limit or _CONTROL.search(value):
        raise ConfigError(f"{what}: ארוך מדי או מכיל תווי בקרה")
    return value


def clean_addr(value, what: str) -> str:
    value = _clean_str(value, what, 254)
    if not _ADDR.match(value):
        raise ConfigError(f"{what}: כתובת מייל לא תקינה: {value!r}")
    return value


def direct_send_host(from_addr: str) -> str:
    """‏Office 365 Direct Send: ה-MX של הדומיין — ``college.ac.il`` →
    ``college-ac-il.mail.protection.outlook.com``. ריק כשאין עדיין כתובת שולח."""
    domain = from_addr.rsplit("@", 1)[1].lower() if "@" in from_addr else ""
    return f"{domain.replace('.', '-')}.mail.protection.outlook.com" if domain else ""


_GUID = re.compile(r"^[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}$")
_DOMAIN = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
TENANT_ALIASES = ("organizations", "common", "consumers")


def valid_tenant(tenant: str) -> bool:
    """‏tenant של Microsoft נכנס לנתיב ה-URL של ספק הזהות — רק GUID, דומיין, או
    אחד הכינויים (סקירת אבטחה: SSRF דרך ``oauth_tenant``)."""
    return isinstance(tenant, str) and (tenant in TENANT_ALIASES or bool(_GUID.match(tenant))
                                        or bool(_DOMAIN.match(tenant)))


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def merge_account(current: dict | None, body: dict, secrets_present: bool
                  ) -> tuple[dict, str | None, str | None, bool]:
    """מאמת חשבון (חדש כש-``current`` הוא ``None``). מחזיר (חשבון, סיסמה חדשה,
    ‏client secret חדש, למחוק סודות?). זורק ``ConfigError`` לפני שנכתב דבר."""
    if not isinstance(body, dict):
        raise ConfigError("גוף הבקשה חייב להיות אובייקט")
    acc = json.loads(json.dumps(current)) if current else {
        "id": new_id("acc"), "name": "", "provider": "custom", "host": "", "port": 587,
        "security": "starttls", "auth": "none", "username": "", "from_addr": "",
        "oauth_client_id": "", "oauth_tenant": "organizations"}
    if "provider" in body:
        if body["provider"] not in PROVIDERS:
            raise ConfigError(f"ספק לא מוכר: {body['provider']!r}")
        preset = PROVIDERS[body["provider"]]
        acc["provider"] = body["provider"]
        acc["port"], acc["security"] = preset["port"], preset["security"]
        if preset["host"]:
            acc["host"] = preset["host"]
        elif body["provider"] != (current or {}).get("provider"):
            acc["host"] = ""
        acc["auth"] = preset["auth"][0]
        if acc["auth"] == "none":
            acc["username"] = ""
    if "name" in body:
        acc["name"] = _clean_str(body["name"], "שם", 80)
    if "host" in body:
        host = _clean_str(body["host"], "מארח", 253)
        if host and not _HOST.match(host):
            raise ConfigError(f"מארח לא תקין: {host!r}")
        acc["host"] = host
    if "port" in body:
        port = body["port"]
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ConfigError("פורט: מספר שלם 1–65535")
        acc["port"] = port
    if "security" in body:
        if body["security"] not in SECURITY:
            raise ConfigError(f"הצפנה: אחת מ-{', '.join(SECURITY)}")
        acc["security"] = body["security"]
    if "auth" in body:
        if body["auth"] not in AUTH:
            raise ConfigError(f"אימות: אחד מ-{', '.join(AUTH)}")
        acc["auth"] = body["auth"]
    for key, what in (("username", "שם משתמש"), ("oauth_client_id", "מזהה אפליקציה (client id)")):
        if key in body:
            acc[key] = _clean_str(body[key], what, 256)
    if "oauth_tenant" in body:
        tenant = _clean_str(body["oauth_tenant"], "tenant", 253) or "organizations"
        if not valid_tenant(tenant):
            raise ConfigError("tenant: מזהה (GUID), דומיין, או organizations / common / consumers")
        acc["oauth_tenant"] = tenant
    if "from_addr" in body:
        acc["from_addr"] = clean_addr(body["from_addr"], "כתובת השולח") if body["from_addr"] else ""
    if acc["provider"] == "o365_direct" and "host" not in body and \
            ("provider" in body or "from_addr" in body) and acc["from_addr"]:
        acc["host"] = direct_send_host(acc["from_addr"])
    password = body.get("password") or None
    if password is not None and (not isinstance(password, str) or len(password) > 1024
                                 or _CONTROL.search(password)):
        raise ConfigError("סיסמה: עד 1024 תווים, בלי תווי בקרה")
    client_secret = body.get("oauth_client_secret") or None
    if client_secret is not None and (not isinstance(client_secret, str) or len(client_secret) > 512
                                      or _CONTROL.search(client_secret)):
        raise ConfigError("client secret: עד 512 תווים, בלי תווי בקרה")
    allowed = PROVIDERS[acc["provider"]]["auth"]
    if acc["auth"] not in allowed:
        raise ConfigError(f"הספק שנבחר אינו תומך באימות {acc['auth']!r} (מותר: {', '.join(allowed)})")
    if acc["auth"] == "none" and acc["username"]:
        raise ConfigError("שליחה בלי אימות (Direct Send / relay) — בלי שם משתמש")
    if acc["auth"] != "none" and acc["security"] == "none":
        raise ConfigError("בלי הצפנה אין אימות: הסיסמה או האסימון היו עוברים ברשת גלויים")
    # סוד נחשב רק אם הוא תואם לאימות הסופי: סיסמה רק ל-``password``, ‏client secret
    # רק ל-``oauth``. אחרת סיסמה הייתה "מכשירה" שינוי יעד של חשבון OAuth — והאסימון
    # הישן היה ממשיך לפעול מול היעד החדש (סקירת אבטחה, #1751).
    if password is not None and acc["auth"] != "password":
        raise ConfigError("סיסמה נשמרת רק בחשבון עם אימות בסיסמה")
    if client_secret is not None and acc["auth"] != "oauth":
        raise ConfigError("client secret נשמר רק בחשבון עם כניסת חשבון (OAuth)")
    retargeted = current is not None and any(acc.get(k) != current.get(k) for k in RETARGET)
    # שינוי יעד מוחק **כל** סוד שמור — גם כשהגיעה סיסמה חדשה (היא נכתבת אחרי המחיקה),
    # וב-OAuth תמיד: האסימון הישן אינו עובר ליעד חדש, וצריך להתחבר מחדש.
    clear = bool(body.get("clear_secrets") is True or (retargeted and secrets_present))
    return acc, password, client_secret, clear


def _clean_recipients(value) -> list[dict]:
    if not isinstance(value, list) or len(value) > MAX_RECIPIENTS:
        raise ConfigError(f"נמענים: רשימה של עד {MAX_RECIPIENTS}")
    out: list[dict] = []
    for item in value:
        if isinstance(item, str):
            item = {"address": item}
        if not isinstance(item, dict):
            raise ConfigError("נמען: {address, lang}")
        addr = clean_addr(item.get("address"), "נמען")
        lang = item.get("lang") or email_text.DEFAULT_LANGUAGE
        if lang not in email_text.LANGUAGES:
            raise ConfigError(f"שפה: אחת מ-{', '.join(email_text.LANGUAGES)}")
        if addr.lower() not in (r["address"].lower() for r in out):
            out.append({"address": addr, "lang": lang})
    return out


def merge_rule(current: dict | None, body: dict, doc: dict) -> dict:
    if not isinstance(body, dict):
        raise ConfigError("גוף הבקשה חייב להיות אובייקט")
    r = json.loads(json.dumps(current)) if current else {
        "id": new_id("rule"), "name": "", "enabled": True,
        "account_id": (default_account(doc) or {}).get("id", ""),
        "categories": list(CATEGORIES), "min_severity": "warn", "recipients": []}
    if "name" in body:
        r["name"] = _clean_str(body["name"], "שם הכלל", 80)
    if "enabled" in body:
        if not isinstance(body["enabled"], bool):
            raise ConfigError("enabled: נדרש true/false")
        r["enabled"] = body["enabled"]
    if "account_id" in body:
        if account(doc, body["account_id"]) is None:
            raise ConfigError(f"חשבון שליחה לא קיים: {body['account_id']!r}")
        r["account_id"] = body["account_id"]
    if "categories" in body:
        cats = body["categories"]
        if not isinstance(cats, list) or not cats or any(c not in CATEGORIES for c in cats):
            raise ConfigError(f"קטגוריות: לפחות אחת מ-{', '.join(CATEGORIES)}")
        r["categories"] = [c for c in CATEGORIES if c in cats]
    if "min_severity" in body:
        if body["min_severity"] not in SEVERITIES:
            raise ConfigError(f"רמת חומרה: אחת מ-{', '.join(SEVERITIES)}")
        r["min_severity"] = body["min_severity"]
    if "recipients" in body:
        r["recipients"] = _clean_recipients(body["recipients"])
    if account(doc, r["account_id"]) is None:
        raise ConfigError("לכלל חייב להיות חשבון שליחה — הגדירו קודם איך שולחים")
    return r


def add_recipient(doc: dict, address: str, lang: str) -> tuple[dict, bool]:
    """המסלול הקצר: נמען לכלל הראשון. אין כללים — **כלל ברירת המחדל נוצר לבד**:
    שגיאות ואזהרות מכל הקטגוריות, בשפה שנמסרה (שפת הממשק). מחזיר (כלל, נוצר?)."""
    if not doc["rules"]:
        created = merge_rule(None, {"name": "ברירת מחדל",
                                    "recipients": [{"address": address, "lang": lang}]}, doc)
        doc["rules"].append(created)
        return created, True
    first = doc["rules"][0]
    updated = merge_rule(first, {"recipients": first["recipients"] + [{"address": address,
                                                                      "lang": lang}]}, doc)
    doc["rules"][0] = updated
    return updated, False


def severity_passes(kind: str, minimum: str) -> bool:
    rank = {level: i for i, level in enumerate(SEVERITIES)}
    return rank.get(KIND_SEVERITY.get(kind, "err"), 2) >= rank.get(minimum, 0)


def matching_rules(doc: dict, kind: str) -> list[dict]:
    """הכללים הדלוקים שהאירוע ``kind`` עובר: הקטגוריה שלו בכלל, והחומרה שלו ברמה."""
    cat = KIND_CATEGORY.get(kind)
    return [r for r in doc["rules"] if r.get("enabled") and cat in r.get("categories", ())
            and severity_passes(kind, r.get("min_severity", "warn"))
            and account(doc, r.get("account_id", "")) is not None]


def warnings(doc: dict) -> list[str]:
    """אזהרות שאינן חוסמות. ‏Direct Send מעביר רק לנמענים בדומיין של השולח."""
    out = []
    for r in doc["rules"]:
        acc = account(doc, r.get("account_id", ""))
        if not acc or acc.get("provider") != "o365_direct" or "@" not in acc.get("from_addr", ""):
            continue
        domain = acc["from_addr"].rsplit("@", 1)[1].lower()
        outside = [x["address"] for x in r["recipients"]
                   if x["address"].rsplit("@", 1)[-1].lower() != domain]
        if outside:
            out.append(f"Direct Send מעביר רק לנמענים בדומיין {domain} — לא יגיע אל: "
                       + ", ".join(outside))
    return out
