"""לשונית ההגדרות "התראות במייל" — ה-API (#1751). **admin בלבד**; ‏deploy
מקבל 403 כמו בכל נתיב ניהול (``auth.console_only`` בהרכבה, ‏``admin_only`` כאן).

המבנה (``email_config``): חשבונות שליחה, כללים, היסטוריה. המסלול הקצר של
המסך: ``POST /accounts`` (ספק מרשימה), ‏``/oauth/start`` + ‏``/oauth/poll``
("התחבר עם Microsoft"), ‏``POST /recipients`` — שיוצר לבד את כלל ברירת המחדל
— ו-``POST /accounts/{id}/test``. כל השאר "מתקדם".

שום תשובה אינה מחזירה סיסמה, אסימון, client secret או device code. בדיקה
ו-OAuth הם סינכרוניים אבל תחומים בזמן (``email_alerts.TIMEOUT_SECONDS``,
‏``email_oauth.TIMEOUT_SECONDS``), ו-FastAPI מריץ ``def`` בתהליכון מהמאגר.
"""

from fastapi import APIRouter, Body, Depends, HTTPException

from server.console import auth
from server.system import email_alerts, email_config, email_oauth, email_text
from server.agent.api import ServerContext
from server.db import journal


def create_email_router(ctx: ServerContext) -> APIRouter:
    router = APIRouter(prefix="/api/console/email-alerts")
    _, admin_only = auth.dependencies(ctx.conn)
    flows = email_oauth.DeviceFlows(http=lambda url, fields: _http()(url, fields))
    ctx.email_oauth_flows = flows     # מה שפתוח — ל-GET (``oauth_pending``) ולבדיקות

    def _http():
        # בדיקות מזריקות ``ctx.email_http``; בייצור — HTTPS אמיתי לספק הזהות.
        return getattr(ctx, "email_http", None) or email_oauth.http_post

    def _data_dir():
        if ctx.data_dir is None:
            raise HTTPException(503, "תיקיית הנתונים אינה ידועה לשרת הזה — אין איפה לשמור סודות")
        return ctx.data_dir

    def _doc() -> dict:
        doc, error = email_config.load(ctx.conn)
        if error:
            raise HTTPException(409, error)
        return doc

    def _account(doc: dict, account_id: str) -> dict:
        acc = email_config.account(doc, account_id)
        if acc is None:
            raise HTTPException(404, "חשבון שליחה לא קיים")
        return acc

    def _view() -> dict:
        doc, error = email_config.load(ctx.conn)
        lang = email_text.language(ctx.conn)
        watcher = getattr(ctx, "email_alerts", None)
        return {
            "accounts": [{**email_config.account_public(a, ctx.data_dir),
                          "oauth_pending": flows.is_open(a["id"])} for a in doc["accounts"]],
            "rules": doc["rules"],
            "kinds": [{"key": k, "label": email_alerts.kind_label(lang, k),
                       "severity": email_config.KIND_SEVERITY[k],
                       "category": email_config.KIND_CATEGORY[k]} for k in email_config.KINDS],
            "categories": [{"key": c, "label": email_text.t(lang, f"cat.{c}"),
                            "kinds": list(kinds)} for c, kinds in email_config.CATEGORIES.items()],
            "severities": list(email_config.SEVERITIES),
            "languages": list(email_text.LANGUAGES),
            "default_language": lang,
            "providers": [{"key": k, "host": p["host"], "port": p["port"], "security": p["security"],
                           "auth": list(p["auth"]), "oauth": p.get("oauth")}
                          for k, p in email_config.PROVIDERS.items()],
            "warnings": email_config.warnings(doc),
            "config_error": error,
            "queue": email_alerts.queue_summary(ctx.conn),
            "running": bool(watcher is not None and watcher.running),
        }

    def _note(what: str, user) -> None:
        journal(ctx.conn, "email_alerts_settings", what, user[0])

    @router.get("")
    def get_settings(user=Depends(admin_only)):
        del user
        return _view()

    # --- חשבונות שליחה ----------------------------------------------------------
    def _save_account(doc, current, body, user) -> dict:
        data_dir = _data_dir()
        try:
            has_secret = current is not None and bool(
                email_config.read_secret(data_dir, current["id"], "password")
                or email_config.read_oauth(data_dir, current["id"]))
        except Exception:                                      # noqa: BLE001
            has_secret = True        # קובץ סוד שלא נקרא — מניחים שיש, כדי שיימחק בשינוי יעד
        try:
            acc, password, client_secret, clear = email_config.merge_account(current, body, has_secret)
        except email_config.ConfigError as exc:
            raise HTTPException(400, str(exc)) from None
        if current is None and len(doc["accounts"]) >= email_config.MAX_ACCOUNTS:
            raise HTTPException(400, f"עד {email_config.MAX_ACCOUNTS} חשבונות שליחה")
        if current is not None and (clear or email_oauth.fingerprint(acc) != email_oauth.fingerprint(current)):
            # **לפני** מחיקת הסודות: התחברות פתוחה לא תשמור אסימון ליעד שהשתנה.
            flows.cancel(acc["id"])
        if clear:
            email_config.clear_secrets(data_dir, acc["id"])
            email_oauth.TOKENS.drop(acc["id"])
            if body.get("clear_secrets") is not True and password is None:
                journal(ctx.conn, "email_alerts_password_cleared",
                        f"{acc['name'] or acc['id']}: הספק/המארח/הפורט/ההצפנה/האימות/המשתמש שונו בלי"
                        " סיסמה — הסודות השמורים נמחקו, והשליחה חסומה עד שיוזנו מחדש", user[0])
        if password is not None:
            email_config.write_secret(data_dir, acc["id"], "password", password)
        if client_secret is not None:
            stored = email_config.read_oauth(data_dir, acc["id"])
            email_oauth.save_token(data_dir, acc["id"], stored.get("refresh_token"), client_secret)
        if current is None:
            doc["accounts"].append(acc)
        else:
            doc["accounts"] = [acc if a["id"] == acc["id"] else a for a in doc["accounts"]]
        email_config.save(ctx.conn, doc)
        _note(f"חשבון {acc['id']} provider={acc['provider']} host={acc['host'] or '-'}:{acc['port']}"
              f" security={acc['security']} auth={acc['auth']}"
              + (" password=changed" if password is not None else "")
              + (" secrets=cleared" if clear else ""), user)
        return acc

    @router.post("/accounts")
    def create_account(body: dict = Body(default={}), user=Depends(admin_only)):
        doc = _doc()
        acc = _save_account(doc, None, body, user)
        return {"account_id": acc["id"], **_view()}

    @router.put("/accounts/{account_id}")
    def update_account(account_id: str, body: dict = Body(default={}), user=Depends(admin_only)):
        doc = _doc()
        _save_account(doc, _account(doc, account_id), body, user)
        return _view()

    @router.delete("/accounts/{account_id}")
    def delete_account(account_id: str, user=Depends(admin_only)):
        doc = _doc()
        _account(doc, account_id)
        used = [r["name"] or r["id"] for r in doc["rules"] if r["account_id"] == account_id]
        if used:
            raise HTTPException(409, "החשבון בשימוש בכללים: " + ", ".join(used))
        flows.cancel(account_id)
        doc["accounts"] = [a for a in doc["accounts"] if a["id"] != account_id]
        email_config.save(ctx.conn, doc)
        email_config.clear_secrets(_data_dir(), account_id)
        email_oauth.TOKENS.drop(account_id)
        _note(f"חשבון {account_id} נמחק", user)
        return _view()

    @router.post("/accounts/{account_id}/test")
    def test_account(account_id: str, body: dict = Body(default={}), user=Depends(admin_only)):
        doc = _doc()
        acc = _account(doc, account_id)
        try:
            to = email_config.clean_addr((body or {}).get("to"), "לאן לשלוח את הבדיקה")
        except email_config.ConfigError as exc:
            raise HTTPException(400, str(exc)) from None
        lang = (body or {}).get("lang") or email_text.language(ctx.conn)
        if lang not in email_text.LANGUAGES:
            raise HTTPException(400, "שפה לא מוכרת")
        if not acc["host"] or not acc["from_addr"]:
            raise HTTPException(400, "חסר בחשבון: " + ", ".join(
                w for w, ok in (("מארח", acc["host"]), ("כתובת שולח", acc["from_addr"])) if not ok))
        blocked = email_config.blocked_reason(acc, _data_dir())
        if blocked:
            raise HTTPException(409, blocked)
        msg = email_alerts.probe_message(ctx.conn, acc, to, lang)
        result = email_alerts.send_from(acc, _data_dir(), [to], msg, http=_http())
        journal(ctx.conn, "email_test", f"{acc['name'] or acc['id']} → {to}: "
                + ("נשלח — " if result.ok else "נכשל — ") + result.summary(), user[0])
        return {"ok": result.ok, "code": result.code, "message": result.message,
                "refused": result.refused}

    # --- OAuth (device code) ------------------------------------------------------
    @router.post("/accounts/{account_id}/oauth/start")
    def oauth_start(account_id: str, user=Depends(admin_only)):
        doc = _doc()
        acc = _account(doc, account_id)
        if acc.get("auth") != "oauth":
            raise HTTPException(409, "החשבון אינו מוגדר לכניסה עם חשבון (OAuth)")
        try:
            return flows.start(acc, _data_dir())
        except email_oauth.OAuthError as exc:
            journal(ctx.conn, "email_oauth_failed", f"{acc['name'] or acc['id']}: {exc}", user[0])
            raise HTTPException(502, str(exc)) from None

    @router.post("/accounts/{account_id}/oauth/poll")
    def oauth_poll(account_id: str, user=Depends(admin_only)):
        doc = _doc()
        acc = _account(doc, account_id)
        try:
            result = flows.poll(acc, _data_dir())
        except email_oauth.OAuthError as exc:
            result = {"state": "error", "message": str(exc)}
        if result["state"] == "connected":
            journal(ctx.conn, "email_oauth_connected", acc["name"] or acc["id"], user[0])
        elif result["state"] == "error":
            journal(ctx.conn, "email_oauth_failed", f"{acc['name'] or acc['id']}: {result['message']}",
                    user[0])
        return result

    @router.delete("/accounts/{account_id}/oauth")
    def oauth_disconnect(account_id: str, user=Depends(admin_only)):
        doc = _doc()
        acc = _account(doc, account_id)
        flows.cancel(acc["id"])
        email_config.secret_path(_data_dir(), acc["id"], "oauth").unlink(missing_ok=True)
        email_oauth.TOKENS.drop(acc["id"])
        _note(f"חשבון {acc['id']}: החיבור (OAuth) נותק", user)
        return _view()

    # --- נמענים: המסלול הקצר -------------------------------------------------------
    @router.post("/recipients")
    def add_recipient(body: dict = Body(default={}), user=Depends(admin_only)):
        doc = _doc()
        if not doc["accounts"]:
            raise HTTPException(409, "קודם בוחרים איך שולחים (חשבון שליחה)")
        lang = (body or {}).get("lang") or email_text.language(ctx.conn)
        try:
            address = email_config.clean_addr((body or {}).get("address"), "נמען")
            rule, created = email_config.add_recipient(doc, address, lang)
        except email_config.ConfigError as exc:
            raise HTTPException(400, str(exc)) from None
        email_config.save(ctx.conn, doc)
        _note(f"נמען {address} ({lang}) → כלל {rule['id']}"
              + (" — כלל ברירת המחדל נוצר" if created else ""), user)
        return {"rule_id": rule["id"], "rule_created": created, **_view()}

    # --- כללים ----------------------------------------------------------------------
    def _save_rule(doc, current, body, user) -> dict:
        try:
            r = email_config.merge_rule(current, body, doc)
        except email_config.ConfigError as exc:
            raise HTTPException(400, str(exc)) from None
        if current is None:
            if len(doc["rules"]) >= email_config.MAX_RULES:
                raise HTTPException(400, f"עד {email_config.MAX_RULES} כללים")
            doc["rules"].append(r)
        else:
            doc["rules"] = [r if x["id"] == r["id"] else x for x in doc["rules"]]
        email_config.save(ctx.conn, doc)
        _note(f"כלל {r['id']} enabled={str(r['enabled']).lower()} min={r['min_severity']}"
              f" categories={','.join(r['categories'])} recipients={len(r['recipients'])}", user)
        return r

    @router.post("/rules")
    def create_rule(body: dict = Body(default={}), user=Depends(admin_only)):
        doc = _doc()
        r = _save_rule(doc, None, body, user)
        return {"rule_id": r["id"], **_view()}

    @router.put("/rules/{rule_id}")
    def update_rule(rule_id: str, body: dict = Body(default={}), user=Depends(admin_only)):
        doc = _doc()
        current = email_config.rule(doc, rule_id)
        if current is None:
            raise HTTPException(404, "כלל לא קיים")
        _save_rule(doc, current, body, user)
        return _view()

    @router.delete("/rules/{rule_id}")
    def delete_rule(rule_id: str, user=Depends(admin_only)):
        doc = _doc()
        if email_config.rule(doc, rule_id) is None:
            raise HTTPException(404, "כלל לא קיים")
        doc["rules"] = [r for r in doc["rules"] if r["id"] != rule_id]
        email_config.save(ctx.conn, doc)
        _note(f"כלל {rule_id} נמחק", user)
        return _view()

    # --- היסטוריה -------------------------------------------------------------------
    @router.get("/history")
    def get_history(user=Depends(admin_only)):
        del user
        return email_alerts.history(ctx.conn)

    return router
