"""לשונית ההגדרות: תעודת ה-TLS של הקונסולה ושם המארח (‏#153).

שני הכרטיסים נוגעים במכונה — קבצי התעודה, ‏``hostnamectl``, ‏``systemctl
restart`` — ולכן, כמו ה-DHCP, כל מגע עובר דרך hooks שבדיקות מזריקות:

- ``hostname()`` — השם שהמכונה מדווחת עכשיו (הקריאה החוזרת).
- ``set_hostname(name)`` — ‏``None`` בהצלחה, אחרת הודעת שגיאה.
- ``restart_server()`` — מתזמן ``systemctl restart`` **אחרי** שהתשובה
  יצאה (``deploy_net.restart_server_later``).

**הרגע הנכון לאתחול:** תעודה חדשה נטענת רק כש-uvicorn עולה מחדש, ושם
המארח נקרא פעם אחת בעליית ``main``. לכן שניהם מסתיימים באתחול — אבל
לעולם לא באמצע סבב פתוח/רץ (409, אותה שאילתה של כפתור העדכון), ותמיד
אחרי שהשינוי עצמו נבדק.

**עיקרון 5:** תעודה נטענת ב-``ssl`` לפני שהיא מחליפה את הקיימת; שם מארח
"השתנה" רק כשהוא נקרא בחזרה מהמכונה; ואתחול שלא תוזמן הוא כשל שאומר
מה כן קרה ("נכתב, לא בשימוש"), לא הצלחה.
"""

from __future__ import annotations

import socket
import subprocess
from typing import Callable

from fastapi import APIRouter, Body, Depends, HTTPException

from . import auth, console_tls, deploy_net, hostname_rule
from .api import ServerContext
from .db import journal
from .update import restart_blocker

Hooks = dict[str, Callable]


def hostname_refusal(name: str) -> str | None:
    """למה ``name`` (כבר באותיות קטנות) אינו שם שרת, או ``None``.
    ‏#1997 (נדב, 22/10): שם השרת הוא שם מחשב — אותו כלל כמו מחשב בנייה ושרת
    משנה (`hostname_rule`): אותיות אנגליות, ספרות ומקף, עד 15, בלי נקודות.
    עד כאן RFC 1123 (עד 253, עם נקודות) — ושם כזה לא נכנס לשורת שרת המשנה
    ולא ל-NetBIOS."""
    return hostname_rule.refusal(name, "שם השרת")


def _hostnamectl(name: str) -> str | None:
    try:
        done = subprocess.run(["hostnamectl", "set-hostname", name],
                              capture_output=True, text=True, timeout=20,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"hostnamectl לא רץ: {exc}"
    if done.returncode != 0:
        return f"hostnamectl החזיר {done.returncode}: {(done.stderr or done.stdout).strip()}"
    return None


def default_hooks() -> Hooks:
    return {
        "hostname": socket.gethostname,
        "set_hostname": _hostnamectl,
        "restart_server": deploy_net.restart_server_later,
    }


def create_hostcfg_router(ctx: ServerContext, data_dir, running_tls,
                          hooks: Hooks | None = None, busy_source=None) -> APIRouter:
    """‏``running_tls`` — ה-``ConsoleTLS`` שהתהליך הזה עלה איתו (``None`` =
    הקונסולה בלי TLS). ההשוואה מולו היא מה שאומר "ממתין לאתחול"."""
    router = APIRouter(prefix="/api/console")
    _, admin_only = auth.dependencies(ctx.conn)
    hooks = {**default_hooks(), **(hooks or {})}

    def _not_during_round() -> None:
        blocker = restart_blocker(busy_source or ctx)   # ‏#1891
        if blocker:
            raise HTTPException(409, f"{blocker} — השינוי מחייב אתחול של "
                                     "השרת, ולכן ממתין שזה יסתיים")

    @router.get("/tls")
    def tls_status(user=Depends(admin_only)):
        if running_tls is None:
            return {"enabled": False}
        on_disk = console_tls.cert_details(running_tls.cert_path.read_bytes())
        hostname = hooks["hostname"]()
        return {
            "enabled": True,
            "on_disk": on_disk,
            "running_fingerprint": running_tls.fingerprint_sha256,
            "pending_restart": on_disk["fingerprint_sha256"] != running_tls.fingerprint_sha256,
            "hostname": hostname,
            "covers_hostname": hostname in on_disk["sans"],
        }

    @router.post("/tls")
    def tls_upload(body: dict = Body(default={}), user=Depends(admin_only)):
        if running_tls is None:
            raise HTTPException(409, "הקונסולה רצה בלי TLS (loopback) — אין תעודה להחליף")
        cert = body.get("cert_pem") if isinstance(body, dict) else None
        key = body.get("key_pem") if isinstance(body, dict) else None
        if not isinstance(cert, str) or not isinstance(key, str) or not cert or not key:
            raise HTTPException(400, "נדרשים שני קבצים: תעודה (PEM) ומפתח פרטי (PEM)")
        _not_during_round()
        try:
            info = console_tls.install_upload(data_dir, cert.encode(), key.encode())
        except console_tls.UploadRefused as exc:
            journal(ctx.conn, "console_tls_refused", str(exc), user[0])
            raise HTTPException(400, str(exc)) from None
        journal(ctx.conn, "console_tls_uploaded",
                f"{info['subject']} עד {info['not_after'][:10]} "
                f"SHA-256 {info['fingerprint_sha256'][:23]}…", user[0])
        error = hooks["restart_server"]()
        if error:
            journal(ctx.conn, "console_tls_restart_failed", error, user[0])
            raise HTTPException(500, f"התעודה נכתבה לדיסק אך אינה בשימוש — "
                                     f"האתחול לא תוזמן: {error}. היא תיכנס "
                                     f"לתוקף באתחול הבא של השירות")
        return {"ok": True, "restarting": True, "certificate": info}

    @router.post("/tls/reissue")
    def tls_reissue(user=Depends(admin_only)):
        if running_tls is None:
            raise HTTPException(409, "הקונסולה רצה בלי TLS (loopback) — אין תעודה להחליף")
        _not_during_round()
        try:
            info = console_tls.reissue_self_signed(data_dir, hooks["hostname"]())
        except console_tls.UploadRefused as exc:
            journal(ctx.conn, "console_tls_refused", str(exc), user[0])
            raise HTTPException(400, str(exc)) from None
        journal(ctx.conn, "console_tls_reissued",
                f"{info['subject']} עד {info['not_after'][:10]} "
                f"SHA-256 {info['fingerprint_sha256'][:23]}…", user[0])
        error = hooks["restart_server"]()
        if error:
            journal(ctx.conn, "console_tls_restart_failed", error, user[0])
            raise HTTPException(500, f"התעודה נכתבה לדיסק אך אינה בשימוש — "
                                     f"האתחול לא תוזמן: {error}. היא תיכנס "
                                     f"לתוקף באתחול הבא של השירות")
        return {"ok": True, "restarting": True, "certificate": info}

    @router.get("/hostname")
    def hostname_status(user=Depends(admin_only)):
        return {"hostname": hooks["hostname"]()}

    @router.post("/hostname")
    def hostname_change(body: dict = Body(default={}), user=Depends(admin_only)):
        raw = body.get("hostname") if isinstance(body, dict) else None
        name = raw.strip().lower() if isinstance(raw, str) else ""
        refusal = hostname_refusal(name)
        if refusal:
            raise HTTPException(400, refusal)
        old = hooks["hostname"]()
        if name == old:
            raise HTTPException(400, f"זה כבר שם המארח ({old})")
        _not_during_round()
        error = hooks["set_hostname"](name)
        if not error:
            seen = hooks["hostname"]()
            if seen != name:
                error = (f"הפקודה הצליחה אך המכונה עדיין מדווחת {seen!r} — "
                         f"השינוי לא אומת")
        if error:
            journal(ctx.conn, "hostname_change_failed", f"{old} → {name}: {error}", user[0])
            raise HTTPException(500, f"שם המארח לא שונה: {error}")
        journal(ctx.conn, "hostname_changed", f"{old} → {name}", user[0])
        restart_error = hooks["restart_server"]()
        if restart_error:
            journal(ctx.conn, "hostname_restart_failed", restart_error, user[0])
            raise HTTPException(500, f"שם המארח שונה ל-{name}, אך אתחול השרת לא "
                                     f"תוזמן: {restart_error}")
        return {"ok": True, "hostname": name, "restarting": True}

    return router
