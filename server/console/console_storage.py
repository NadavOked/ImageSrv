"""API הקונסולה לניהול מרשם המשניים (#655, tracer #2 / #727).

ניהול **קבוצות** וניהול **רשומות משניים קיימות** — שינוי שם, שיוך
לקבוצה, השבתה/הפעלה ומחיקה. אין כאן הוספת משני: הוספה היא enrollment
(כתובת + אישורי מנהל + טביעת TLS) והיא שלב נפרד בשרשרת (#3), כמו
הוספת מארח ל-vCenter.

RBAC כפול (עיקרון 5 + ההיררכיה החד-כיוונית של #655):

- ``admin_only`` — deploy מקבל 403, בדיוק כמו בשאר מסכי הניהול.
- ``require_standalone`` — משני מקבל 409: הוא אינו מנהל ילדים, וההיררכיה
  זורמת מלמעלה למטה בלבד. האכיפה חוזרת גם בשכבת ה-service, כדי שקריאה
  ישירה לפונקציה על משני תיכשל בדיוק כמו דרך ה-API.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect

from server.console import auth, monitor
from server.agent import registry
from server.interserver import (interserver_api, interserver_auth, peer_safe, storage_client,
                                storage_nodes, storage_transfer)
from server.agent.api import ServerContext
from server.db import journal, now_iso

log = logging.getLogger("imagesrv.console_storage")


def secondary_unreachable(exc: BaseException, port=None) -> str:
    """הודעה בעברית כשהשרת המשני לא ענה (#1016). הטקסט הגולמי של מערכת
    ההפעלה ([WinError 10061], connection refused) הולך ללוג בלבד; הכישלון
    עצמו נשאר גלוי (עיקרון 5)."""
    log.warning("secondary unreachable: %s: %s", type(exc).__name__, exc)
    return ("שרת המשנה לא עונה בכתובת הזו — בדקו כתובת, פורט "
            f"{port if port else 8443} וחומת אש")


class _NotSent(Exception):
    """‏#1179 שלב 2: הכתיבה לא יצאה מהראשי (אין זהות/טוקן, החיבור לא נפתח)."""


def create_storage_router(ctx: ServerContext, data_dir=None) -> APIRouter:
    router = APIRouter(prefix="/api/console")
    _current_user, admin_only = auth.dependencies(ctx.conn)

    def require_standalone(user=Depends(admin_only)) -> tuple[str, str]:
        """מנהל **וגם** שרת ראשי. deploy נופל ב-403 מ-``admin_only``
        עוד לפני שמגיעים לכאן; משני נופל כאן ב-409."""
        if storage_nodes.role(ctx.conn) != storage_nodes.ROLE_STANDALONE:
            raise HTTPException(
                409, "ניהול שרתי משנה אפשרי רק בשרת ראשי (standalone)")
        return user

    # --- קבוצות אחסון -------------------------------------------------------

    @router.get("/storage-node-groups")
    def list_groups(user=Depends(require_standalone)):
        return storage_nodes.list_groups(ctx.conn, user)

    @router.post("/storage-node-groups")
    async def create_group(request: Request, user=Depends(require_standalone)):
        label = (await request.json()).get("label", "")
        try:
            gid = storage_nodes.create_group(ctx.conn, label, user)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"id": gid}

    @router.post("/storage-node-groups/order")
    async def reorder_groups(request: Request, user=Depends(require_standalone)):
        ids = (await request.json()).get("ids")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise HTTPException(400, "צריך רשימת מזהים")
        try:
            storage_nodes.reorder_groups(ctx.conn, ids, user)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"ok": True}

    @router.put("/storage-node-groups/{gid}")
    async def rename_group(gid: str, request: Request,
                           user=Depends(require_standalone)):
        label = (await request.json()).get("label", "")
        try:
            storage_nodes.rename_group(ctx.conn, gid, label, user)
        except ValueError as exc:
            raise HTTPException(404 if "לא קיימת" in str(exc) else 400, str(exc))
        return {"ok": True}

    @router.delete("/storage-node-groups/{gid}")
    def delete_group(gid: str, user=Depends(require_standalone)):
        try:
            storage_nodes.delete_group(ctx.conn, gid, user)
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        return {"ok": True}

    # --- משניים רשומים ------------------------------------------------------

    @router.get("/storage-nodes")
    def list_nodes(user=Depends(require_standalone)):
        return storage_nodes.list_nodes(ctx.conn, user)

    @router.put("/storage-nodes/{nid}")
    async def edit_node(nid: str, request: Request,
                        user=Depends(require_standalone)):
        """שינוי שם ו/או שיוך לקבוצה, ב**טרנזאקציה אחת** (#732).
        ``group_id`` הנוכח בגוף — כולל ``null`` — משמעו שיוך מפורש
        (``null`` = ניתוק). שדה שאינו בגוף אינו נוגע בערך הקיים."""
        body = await request.json()
        kwargs = {}
        if "label" in body:
            kwargs["label"] = body["label"]
        if "group_id" in body:
            kwargs["group_id"] = body["group_id"]
        try:
            storage_nodes.edit_node(ctx.conn, nid, user, **kwargs)
        except ValueError as exc:
            raise HTTPException(404 if "לא קיים" in str(exc) else 400, str(exc))
        return {"ok": True}

    @router.post("/storage-nodes/{nid}/disabled")
    async def set_disabled(nid: str, request: Request,
                           user=Depends(require_standalone)):
        # ‏#732: פרסור בוליאני קפדני. ``bool("false")`` הוא ``True``,
        # ולכן ``{"disabled": "false"}`` היה **משבית** משני. מקבלים רק
        # בוליאני אמיתי; מחרוזת/מספר/אובייקט נדחים ב-400.
        disabled = (await request.json()).get("disabled")
        if not isinstance(disabled, bool):
            raise HTTPException(400, "השדה disabled חייב להיות בוליאני (true/false)")
        try:
            storage_nodes.set_node_disabled(ctx.conn, nid, disabled, user)
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        return {"ok": True}

    @router.delete("/storage-nodes/{nid}")
    def delete_node(nid: str, user=Depends(require_standalone)):
        try:
            storage_nodes.delete_node(ctx.conn, nid, user)
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        return {"ok": True}

    # --- העברת אימג' ראשי→משני (#655 v1) ------------------------------------
    #
    # ‏admin+standalone. ההעברה היא משימת רקע; כאן רק פתיחה ותצוגה. הכניסה
    # לספריית המשני עוברת אימות sha256 **שם** (import_tar) — הראשי מדווח
    # ``done`` רק אחרי 200 שהמשני החזיר אחרי האימות.

    @router.post("/storage-nodes/{nid}/transfer")
    async def start_transfer(nid: str, request: Request,
                             user=Depends(require_standalone)):
        body = await request.json()
        image_id = str(body.get("image_id") or "")
        location_id = str(body.get("location_id") or "loc_local")
        try:
            tid = storage_transfer.start_transfer(
                ctx, data_dir, nid, image_id, user, location_id=location_id)
        except storage_transfer.TransferError as exc:
            raise HTTPException(exc.status, exc.detail)
        return {"id": tid}

    @router.get("/storage-transfers")
    def all_transfers(user=Depends(require_standalone)):
        return storage_transfer.list_transfers(ctx.conn, user)

    @router.post("/storage-transfers/{tid}/cancel")
    def cancel_transfer(tid: str, user=Depends(require_standalone)):
        try:
            return storage_transfer.cancel_transfer(ctx.conn, tid, user)
        except storage_transfer.TransferError as extra:
            raise HTTPException(extra.status, extra.detail)

    # --- משיכת אימג' משני→ראשי (#1071) ---------------------------------------
    #
    # היוזם תמיד הראשי. המשני רק עונה GET. ‏deploy → 403 (אין קונסולה, #1073);
    # שרת שאינו standalone → 409. אימג' שכבר קיים בראשי → 409, לא דריסה.

    @router.post("/storage-nodes/{nid}/pull")
    async def start_pull(nid: str, request: Request,
                         user=Depends(require_standalone)):
        image_id = str((await request.json()).get("image_id") or "")
        try:
            tid = storage_transfer.start_pull(ctx, data_dir, nid, image_id, user)
        except storage_transfer.TransferError as extra:
            raise HTTPException(extra.status, extra.detail)
        return {"id": tid}

    # ‏#1017: כל פרוקסי רושם את תוצאת הקריאה על השורה (``record_contact``)
    # ומחזיר את שלושת שדות המגע (``last_seen_at``/``last_error``/
    # ‏``last_error_at``) — זמן השרת, לא שעון הדפדפן. "תשובה לא צפויה" היא
    # מגע (המשני ענה) **וגם** כשל (התשובה אינה שמישה) — נרשמת ככשל.

    def _contact(node, error: str | None) -> dict:
        storage_nodes.record_contact(ctx.conn, node["id"], error=error)
        return storage_nodes.contact_fields(ctx.conn, node["id"])

    @router.post("/storage-nodes/{nid}/check")
    def node_check(nid: str, user=Depends(require_standalone)):
        """‏#1017: "בדוק חיבור" — ‏``GET /ping`` המאומת של המשני בלבד, בלי
        לשלוף מכונות או אימג'ים. ‏``connected:false`` עם הסיבה — 200, לא 5xx;
        משני מושבת אינו נשאל (ואינו נרשם כ"לא ענה")."""
        node = storage_nodes.node_row(ctx.conn, nid)
        if node is None:
            raise HTTPException(404, "שרת משנה לא קיים")
        if node["disabled_at"]:
            return {"connected": False, "error": "שרת המשנה מושבת",
                    "checked_at": now_iso(), "node_id": None, "protocol_version": None,
                    **storage_nodes.contact_fields(ctx.conn, node["id"])}
        try:
            client, token = storage_nodes.node_client(ctx.conn, data_dir, node)
            with client:
                answer = client.ping(token)
        except Exception as exc:                             # noqa: BLE001
            error = interserver_auth.redact_secrets(str(exc))
            return {"connected": False, "error": error, "checked_at": now_iso(),
                    "node_id": None, "protocol_version": None,
                    **_contact(node, error)}
        if not isinstance(answer, dict) or answer.get("ok") is not True:
            error = "תשובה לא צפויה משרת המשנה"
            return {"connected": False, "error": error, "checked_at": now_iso(),
                    "node_id": None, "protocol_version": None,
                    **_contact(node, error)}
        storage_nodes.record_version(ctx.conn, node["id"], answer)   # #1300
        return {"connected": True, "error": None, "checked_at": now_iso(),
                "node_id": answer.get("node_id"),
                "protocol_version": answer.get("protocol_version"),
                **_contact(node, None)}

    @router.get("/storage-nodes/{nid}/images")
    def node_images(nid: str, user=Depends(require_standalone)):
        """פרוקסי ל-``GET /images`` של המשני. כשל חיבור = ``connected:false``."""
        node = storage_nodes.node_row(ctx.conn, nid)
        if node is None:
            raise HTTPException(404, "שרת משנה לא קיים")
        if node["disabled_at"]:
            return {"connected": False, "error": "שרת המשנה מושבת", "images": [],
                    **storage_nodes.contact_fields(ctx.conn, node["id"])}
        try:
            client, token = storage_nodes.node_client(ctx.conn, data_dir, node)
            with client:
                answer = client.get_json("/images", token)
        except Exception as extra:                           # noqa: BLE001
            error = interserver_auth.redact_secrets(str(extra))
            return {"connected": False, "error": error, "images": [],
                    **_contact(node, error)}
        if not isinstance(answer, list):
            error = "תשובה לא צפויה משרת המשנה"
            return {"connected": False, "error": error, "images": [],
                    **_contact(node, error)}
        return {"connected": True, "error": None, "images": answer,
                **_contact(node, None)}

    # --- צפייה במשני: המכונות שלו (#655 v1) ----------------------------------
    #
    # הראשי שואל את המשני בערוץ המאומת ומחזיר את התשובה כפי שהיא. כשל
    # חיבור אינו 5xx אלא ``connected:false`` עם הסיבה — כרטיס הסניף מציג
    # "לא מחובר" במקום להיעלם (עיקרון 5: לא-הצלחנו-לשאול ≠ אין מכונות).

    @router.get("/storage-nodes/{nid}/machines")
    def node_machines(nid: str, user=Depends(require_standalone)):
        node = storage_nodes.node_row(ctx.conn, nid)
        if node is None:
            raise HTTPException(404, "שרת משנה לא קיים")
        if node["disabled_at"]:
            return {"connected": False, "error": "שרת המשנה מושבת", "machines": [],
                    **storage_nodes.contact_fields(ctx.conn, node["id"])}
        try:
            client, token = storage_nodes.node_client(ctx.conn, data_dir, node)
            with client:
                answer = client.get_json("/machines", token)
        except Exception as exc:                             # noqa: BLE001
            error = interserver_auth.redact_secrets(str(exc))
            return {"connected": False, "error": error, "machines": [],
                    **_contact(node, error)}
        machines = answer.get("machines")
        if not isinstance(machines, list):
            error = "תשובה לא צפויה משרת המשנה"
            return {"connected": False, "error": error, "machines": [],
                    **_contact(node, error)}
        storage_nodes.record_version(ctx.conn, node["id"], answer)   # #1300
        return {"connected": True, "error": None, "machines": machines,
                "node_id": answer.get("node_id"), **_contact(node, None)}

    # --- הקונסולה של המשני דרך הראשי: GET מרשימה מותרת (#1179) ---------------
    #
    # הדפדפן קורא ל-``…/storage-nodes/{id}/console/<נתיב קונסולה>`` כשנבחר משני
    # (‏``api()`` ב-console.js), והעמוד של הראשי מצייר את מה שחוזר — אותם
    # עמודים ואותן לשוניות. רק GET ורק נתיב מ-``CONSOLE_GETS``; נתיב אחר אינו
    # נשלח למשני. כשל חיבור/דחייה של הערוץ → 502 עם הסיבה (העמוד מציג "לא
    # נקרא"), לא תשובה ריקה. 401/403 של המשני אינם עוברים כפי שהם — 401 היה
    # שולח את הדפדפן לכניסה **לראשי**. כל מחרוזת עוברת ``peer_safe.defang``.

    @router.get("/storage-nodes/{nid}/console/{path:path}")
    def node_console(nid: str, path: str, request: Request,
                     user=Depends(require_standalone)):
        sub = "/" + path
        kind = interserver_api.console_get_type(sub)
        if kind is None:
            raise HTTPException(404, "הנתיב אינו זמין דרך שרת המשנה")
        node = storage_nodes.node_row(ctx.conn, nid)
        if node is None:
            raise HTTPException(404, "שרת משנה לא קיים")
        label = node["label"] or "שרת המשנה"
        if node["disabled_at"]:
            raise HTTPException(409, f"{label} מושבת — לא נשאל")
        query = request.url.query
        # ‏``sub`` כבר עבר את ``console_get_type`` (בלי ``%``/``\``/``..``) — עובר כפי שהוא.
        target = "/console" + sub + (f"?{query}" if query else "")
        try:
            client, token = storage_nodes.node_client(ctx.conn, data_dir, node)
            with client:
                status, answer = client.get_answer(
                    target, token, {"ImageSrv-Acting-User": user[0]})
        except Exception as exc:                             # noqa: BLE001
            error = interserver_auth.redact_secrets(str(exc))
            _contact(node, error)
            raise HTTPException(502, f"{label} לא ענה: {error}")
        if status != 200:
            error = f"הערוץ סירב ({status}): {answer.get('detail') if isinstance(answer, dict) else ''}"
            _contact(node, error)
            raise HTTPException(502, f"{label} לא ענה: {error}")
        if (not isinstance(answer, dict) or not isinstance(answer.get("status"), int)
                or isinstance(answer.get("status"), bool) or "body" not in answer):
            _contact(node, "תשובה לא צפויה משרת המשנה")
            raise HTTPException(502, f"{label}: תשובה לא צפויה מהמשני")
        _contact(node, None)
        inner, body = answer["status"], peer_safe.defang(answer["body"])
        if inner == 200:
            if not isinstance(body, kind):
                raise HTTPException(
                    502, f"{label}: תשובה לא צפויה מהמשני ל-{sub} (לא {kind.__name__})")
            sent = answer.get("headers") if isinstance(answer.get("headers"), dict) else {}
            # הכותרות האלה הן זמן ISO / true / מספר — ערך אחר אינו עובר כלל.
            forward = {k: v for k, v in sent.items()
                       if k in interserver_api.CONSOLE_HEADERS and isinstance(v, str)
                       and re.fullmatch(r"[A-Za-z0-9:.+\-]{1,64}", v)}
            return JSONResponse(body, headers=forward)
        detail = body.get("detail") if isinstance(body, dict) else None
        detail = detail if isinstance(detail, str) else f"שגיאה {inner}"
        if inner in (400, 404, 409, 422):
            raise HTTPException(inner, detail)
        raise HTTPException(502, f"{label} החזיר {inner}: {detail}")

    # --- כתיבה למשני מהראשי, מרשימה מותרת (#1179 שלב 2) ---------------------
    #
    # admin + standalone כמו כל הנתיבים כאן. רק ``(שיטה, נתיב)`` מ-``CONSOLE_WRITES``
    # — אחר 404 בלי לשאול. גוף עד ``MAX_CONSOLE_WRITE_BODY``, JSON. **ערך שעבר
    # ``defang`` אינו חוזר למשני**: גוף שנושא תו כזה → 409, גלוי (ראו ``peer_safe``).
    # כל ניסיון נרשם ביומן הראשי (``peer_write``) עם התוצאה; המשני רושם משלו.
    # כשל: לפני שהבקשה נכתבה במלואה (החיבור לא נפתח, או ECONNRESET בתוך ה-sendall)
    # → 502 "לא נשלחה"; אחרי כתיבה מלאה → 502 "לא ידוע אם בוצעה" —
    # שני מצבים שונים (עיקרון 5), לא "נכשל" אחד.

    @router.api_route("/storage-nodes/{nid}/console/{path:path}",
                      methods=["POST", "PUT", "DELETE"])
    async def node_console_write(nid: str, path: str, request: Request,
                                 user=Depends(require_standalone)):
        import asyncio
        import json
        method, sub = request.method, "/" + path
        if not interserver_api.console_write_allowed(method, sub):
            raise HTTPException(404, "הפעולה אינה זמינה על שרת המשנה מכאן")
        node = storage_nodes.node_row(ctx.conn, nid)
        if node is None:
            raise HTTPException(404, "שרת משנה לא קיים")
        label = node["label"] or "שרת המשנה"
        what = f"{label} ({nid}): {method} {sub}"
        if node["disabled_at"]:
            raise HTTPException(409, f"{label} מושבת — לא נשאל")
        raw = await request.body()
        if len(raw) > interserver_api.MAX_CONSOLE_WRITE_BODY:
            raise HTTPException(413, "גוף הבקשה גדול מהתקרה")
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            raise HTTPException(400, "גוף JSON לא תקין")
        tainted = peer_safe.carries_defanged(body)
        if tainted is not None:
            raise HTTPException(409, (
                "הערך כולל תו שהוחלף בתצוגה משרת המשנה (‹ › ” ’ ˋ ＆) — שמירה מכאן הייתה "
                f"משנה אותו בשקט במשני: \"{tainted[:80]}\". תקנו את הערך בקונסולה של {label}."))
        query = request.url.query
        target = "/console" + sub + (f"?{query}" if query else "")

        def send() -> tuple[int, object]:
            try:
                client, token = storage_nodes.node_client(ctx.conn, data_dir, node)
                slow = interserver_api.CONSOLE_WRITE_TIMEOUTS.get(sub)   # #1776
                if slow is not None:
                    client.request_timeout = slow
                client.open()
            except Exception as exc:                         # noqa: BLE001
                raise _NotSent(interserver_auth.redact_secrets(str(exc)))
            try:
                return client.send_answer(method, target, token, body,
                                          {"ImageSrv-Acting-User": user[0]})
            except storage_client.RequestNotSent as exc:
                raise _NotSent(interserver_auth.redact_secrets(str(exc)))
            finally:
                client.close()

        try:
            status, answer = await asyncio.to_thread(send)
        except _NotSent as exc:
            _contact(node, str(exc))
            journal(ctx.conn, "peer_write", f"{what} → לא נשלחה: {exc}", user[0])
            raise HTTPException(502, f"{label} לא ענה — הפעולה לא נשלחה: {exc}")
        except Exception as exc:                             # noqa: BLE001
            error = interserver_auth.redact_secrets(str(exc))
            _contact(node, error)
            journal(ctx.conn, "peer_write", f"{what} → לא ידוע: {error}", user[0])
            raise HTTPException(502, f"החיבור ל-{label} נפל אחרי השליחה — לא ידוע אם "
                                     f"הפעולה בוצעה. רעננו ובדקו: {error}")
        if status != 200 or not isinstance(answer, dict) or not isinstance(
                answer.get("status"), int) or isinstance(answer.get("status"), bool):
            detail = answer.get("detail") if isinstance(answer, dict) else ""
            error = f"הערוץ סירב ({status}): {detail}" if status != 200 else "תשובה לא צפויה משרת המשנה"
            _contact(node, error)
            journal(ctx.conn, "peer_write", f"{what} → {error}", user[0])
            raise HTTPException(502, f"{label}: {error}")
        _contact(node, None)
        inner, reply = answer["status"], peer_safe.defang(answer.get("body"))
        journal(ctx.conn, "peer_write", f"{what} → {inner}", user[0])
        if inner == 200:
            return JSONResponse(reply)
        detail = reply.get("detail") if isinstance(reply, dict) else None
        detail = detail if isinstance(detail, str) else f"שגיאה {inner}"
        if inner in (400, 404, 409, 422):
            raise HTTPException(inner, detail)
        raise HTTPException(502, f"{label} החזיר {inner}: {detail}")

    # --- מוניטור למכונה של המשני, דרך המשני (#655 v1) ------------------------
    #
    # פרוקסי-של-פרוקסי: הדפדפן ↔ הראשי (WebSocket) ↔ המשני (מנהרה בערוץ
    # המאומת) ↔ המכונה (RFB). הראשי לעולם אינו רואה סוד של מכונה — המשני
    # מזדהה מולה בסוד שלו (#846) ומעביר לראשי זרם שכבר עבר SecurityResult.
    # קודי הסגירה כמו במוניטור המקומי (סעיף 14 ב-interfaces.md); כשל שהגיע
    # מהמשני חוזר כ-4000+הקוד שלו עם ה-detail שלו כסיבה.

    #: ‏#1129: הכותב אל המנהרה של כל מוניטור-דרך-משני פתוח, לפי ``nid/mac``
    #: — הנתיב היחיד של פקודת כוח למכונה של המשני (ראו ``monitor.power``).
    tunnels: dict[str, object] = {}

    @router.post("/storage-nodes/{nid}/monitor/{mac}/power")
    async def remote_power(nid: str, mac: str, body: dict,
                           user=Depends(require_standalone)):
        """‏#1129: כוח למכונה של המשני — אותו חוזה כמו `POST /monitor/{mac}/power`,
        אלא שהשם הקנוני מגיע מרשימת המכונות של **המשני** (הראשי אינו רושם
        אותה), והפריים נכתב למנהרה הפתוחה — המשני מעביר אותו למכונה
        כמו כל בייט אחר אחרי SecurityResult."""
        import asyncio
        node = storage_nodes.node_row(ctx.conn, nid)
        if node is None:
            raise HTTPException(404, "שרת משנה לא קיים")
        canonical = registry.normalize_mac(mac)
        if canonical is None:
            raise HTTPException(404, "מכונה לא מוכרת")
        action = body.get("action")
        if action not in monitor.POWER_ACTIONS:
            raise HTTPException(400, "פעולה לא מוכרת — reboot או poweroff")

        def remote_name() -> str | None:
            client, token = storage_nodes.node_client(ctx.conn, data_dir, node)
            with client:
                answer = client.get_json("/machines", token)
            machines = answer.get("machines") if isinstance(answer, dict) else None
            for row in machines or []:
                if isinstance(row, dict) and row.get("mac") == canonical:
                    return row.get("name")
            return None

        try:
            name = await asyncio.to_thread(remote_name)
        except Exception as exc:                             # noqa: BLE001
            raise HTTPException(502, "שרת המשנה אינו זמין: "
                                + interserver_auth.redact_secrets(str(exc)))
        if name is None:
            raise HTTPException(404, "המכונה אינה רשומה בשרת המשנה")
        if body.get("confirm") != name:
            raise HTTPException(403, monitor.POWER_NAME_STALE)   # #1850: איש אינו מקליד
        writer = tunnels.get(f"{nid}/{canonical}")
        if writer is None:
            raise HTTPException(
                409, "אין מוניטור פתוח למכונה — הפקודה נוסעת דרך חיבור המוניטור")
        writer.write(monitor.power_frame(action))
        await writer.drain()
        journal(ctx.conn, "monitor_power", f"{canonical} {action} node={nid}", user[0])
        return {"ok": True, "action": action}

    @router.websocket("/storage-nodes/{nid}/monitor/{mac}")
    async def remote_monitor(websocket: WebSocket, nid: str, mac: str):
        import asyncio
        if not monitor.origin_allowed(websocket.headers):   # ‏#1129: לפני accept
            await websocket.close(code=monitor.WS_FORBIDDEN,
                                  reason=monitor.ORIGIN_REFUSED)
            return
        await monitor.accept_browser(websocket)             # ‏#904: קוד וסיבה, לא 403
        found = auth.check(ctx.conn, websocket.cookies.get(auth.COOKIE_NAME))
        if found is None:
            await websocket.close(code=monitor.WS_UNAUTHENTICATED, reason="נדרשת התחברות")
            return
        if found[1] != "admin":
            await websocket.close(code=monitor.WS_FORBIDDEN, reason="פעולה למנהל בלבד")
            return
        if storage_nodes.role(ctx.conn) != storage_nodes.ROLE_STANDALONE:
            await websocket.close(code=4409, reason="צפייה בשרת המשנה אפשרית רק משרת ראשי")
            return
        node = storage_nodes.node_row(ctx.conn, nid)
        if node is None:
            await websocket.close(code=4404, reason="שרת משנה לא קיים")
            return
        if node["disabled_at"]:
            await websocket.close(code=4409, reason="שרת המשנה מושבת")
            return
        canonical = registry.normalize_mac(mac)
        if canonical is None:
            await websocket.close(code=4404, reason="מכונה לא מוכרת")
            return
        try:
            token = interserver_auth.load_credential(node["credential_ref"])
            ident = storage_nodes.identity(ctx.conn, data_dir=data_dir)
            host, port, _ = interserver_auth.parse_interserver_url(node["base_url"])
        except Exception as exc:                             # noqa: BLE001
            await websocket.close(code=4500, reason=monitor.close_reason(
                interserver_auth.redact_secrets(str(exc))))
            return
        writer = None
        try:
            try:
                reader, writer = await asyncio.wait_for(storage_client.open_tunnel(
                    host, port, expected_secondary_spki=node["pinned_spki"],
                    cert_path=ident["server_cert_ref"], key_path=ident["server_key_ref"],
                    path=f"/monitor/{canonical}", token=token), timeout=15.0)
            except storage_client.InterserverTunnelRefused as exc:
                await websocket.close(code=4000 + exc.status,
                                      reason=monitor.close_reason(exc.detail))
                return
            except (OSError, asyncio.TimeoutError, storage_client.InterserverClientError) as exc:
                await websocket.close(
                    code=4502,
                    reason=monitor.close_reason(
                        f"השרת המשני אינו זמין: {interserver_auth.redact_secrets(str(exc))}"))
                return
            tunnels[f"{nid}/{canonical}"] = writer
            journal(ctx.conn, "monitor_connected", f"{canonical} node={nid}", found[0])
            outcome = "error"
            try:
                outcome = await monitor.bridge_browser(websocket, reader, writer)
            except (WebSocketDisconnect, asyncio.CancelledError):
                outcome = "browser"
                raise
            finally:
                journal(ctx.conn, "monitor_closed",
                        f"{canonical} {outcome} node={nid}", found[0])
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        finally:
            tunnels.pop(f"{nid}/{canonical}", None)
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:                            # noqa: BLE001
                    pass

    # --- הצד המקומי של המשני: חלון pairing ו-break-glass (#740) --------------
    #
    # פעולות שמנהל מפעיל **מקומית** על המשני. שלושה שומרים, וכולם
    # נאכפים: ``admin_only`` (deploy→403), locality (peer שאינו loopback
    # →403 — נאכף על כתובת ה-peer של ASGI, לא על ``X-Forwarded-For``),
    # ותפקיד secondary (ראשי/עצמאי→409, בשכבת ה-service). היות מנהל
    # מאומת אינו מספיק לבדו — כך דורש הדגל הנעול.

    def require_local(request: Request, user=Depends(admin_only)) -> tuple[str, str]:
        client = request.client
        if not interserver_auth.is_loopback(client.host if client else None):
            raise HTTPException(403, "פעולת pairing מקומית מותרת מ-loopback בלבד")
        return user

    def _pairing_http(exc: Exception) -> HTTPException:
        if isinstance(exc, storage_nodes.NodeManagementUnauthorized):
            return HTTPException(403, str(exc))
        return HTTPException(409, str(exc))   # NotSecondary / ParentAlreadyEnrolled

    @router.post("/storage-pairing-window")
    def open_pairing_window(user=Depends(require_local)):
        """פותח חלון pairing מקומי ומחזיר את הקוד **פעם אחת בלבד**."""
        try:
            return storage_nodes.open_local_pairing(ctx.conn, user)
        except (storage_nodes.NotSecondaryError,
                storage_nodes.ParentAlreadyEnrolledError,
                storage_nodes.NodeManagementUnauthorized) as exc:
            raise _pairing_http(exc)

    @router.get("/storage-pairing-window")
    def pairing_window_status(user=Depends(require_local)):
        """מצב החלון — **בלי הקוד**. refresh/status לעולם אינם מחזירים אותו."""
        try:
            return storage_nodes.local_pairing_status(ctx.conn, user)
        except (storage_nodes.NotSecondaryError,
                storage_nodes.NodeManagementUnauthorized) as exc:
            raise _pairing_http(exc)

    @router.delete("/storage-parent")
    def unbind_parent(user=Depends(require_local)):
        """פינוי מקומי (break-glass): מנתק את האב הרשום וסוגר חלון פתוח."""
        try:
            storage_nodes.unbind_parent(ctx.conn, user)
        except (storage_nodes.NotSecondaryError,
                storage_nodes.NodeManagementUnauthorized) as exc:
            raise _pairing_http(exc)
        return {"ok": True}

    # --- הצד של הראשי: הוספת משני דרך enrollment מוקשח (#740) ----------------
    #
    # ‏admin+standalone (``require_standalone``). המנהל מזין כתובת מפורשת,
    # קוד חד-פעמי, ואת ה-SPKI של המשני כפי שהוא מוצג שם. ``preview`` קורא
    # את ה-SPKI של השרת כדי שהמנהל יַשווה **לפני** שהוא שולח את הקוד; אין
    # כאן שם משתמש/סיסמה של המשני (הדגם הנעול הסיר את זה).

    def _identity():
        if data_dir is None:
            raise HTTPException(500, "data_dir לא הוגדר — אין זהות TLS בין-שרתית")
        return storage_nodes.ensure_identity(ctx.conn, data_dir)

    @router.post("/storage-nodes/enroll/preview")
    async def enroll_preview(request: Request, user=Depends(require_standalone)):
        """קורא את ה-SPKI של המשני שבכתובת, להשוואה ידנית מול תצוגת המשני."""
        body = await request.json()
        url = body.get("url", "")
        # ‏#1997: השם נבדק כבר כאן (שלב 1 בחלון), לא אחרי שהמפעיל הקליד את הקוד.
        if "label" in body:
            try:
                storage_nodes.check_label(body["label"])
            except ValueError as exc:
                raise HTTPException(400, str(exc))
        try:
            host, port, _ = interserver_auth.parse_interserver_url(url)
        except interserver_auth.InterserverURLError as exc:
            raise HTTPException(400, str(exc))
        try:
            spki = storage_client.fetch_server_spki(host, port)
        except Exception as exc:                             # noqa: BLE001
            raise HTTPException(502, secondary_unreachable(exc, port))
        return {"secondary_spki": spki, "node_id": interserver_auth.node_id_from_spki(spki)}

    @router.post("/storage-nodes/enroll")
    async def enroll(request: Request, user=Depends(require_standalone)):
        """מבצע pairing מוקשח מול המשני, כותב את הטוקן לקובץ 0600, ורק אז
        רושם את המשני. הטוקן לעולם אינו חוזר בתשובה ואינו נכתב ל-DB/יומן."""
        body = await request.json()
        url = body.get("url", "")
        version = str(body.get("protocol_version", ""))
        if version != interserver_auth.PROTOCOL_VERSION:
            raise HTTPException(426, f"גרסת פרוטוקול חייבת להיות {interserver_auth.PROTOCOL_VERSION}")
        expected_spki = str(body.get("expected_secondary_spki") or "")
        if not expected_spki:
            raise HTTPException(400, "חסר expected_secondary_spki (הצמד את ה-SPKI של שרת המשנה)")
        # ‏#1997: שם שרת המשנה הוא שם מחשב — נבדק **לפני** ה-pairing. אחרי
        # ה-pairing המשני כבר קשור, ורשומה שנופלת על השם משאירה אותו קשור.
        try:
            storage_nodes.check_label(body.get("label"))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        try:
            _host, enroll_port, _ = interserver_auth.parse_interserver_url(url)
        except interserver_auth.InterserverURLError as exc:
            raise HTTPException(400, str(exc))

        ident = _identity()
        try:
            result = storage_client.pair_secondary(
                url, code=str(body.get("code") or ""),
                expected_secondary_spki=expected_spki,
                cert_pem=ident["cert_pem"], key_pem=ident["key_pem"],
                primary_id=ident["node_id"])
        except storage_client.InterserverIdentityMismatch as exc:
            # ‏#883: ההצהרה סותרת את המפתח — כשל בקול, לפני שהקוד נשלח.
            raise HTTPException(400, interserver_auth.redact_secrets(str(exc)))
        except storage_client.InterserverClientError as exc:
            # הודעת ה-detail עלולה לשאת חומר — מסתירים לפני שהיא עוזבת.
            raise HTTPException(502, interserver_auth.redact_secrets(str(exc)))
        except OSError as exc:
            # החיבור עצמו לא נפתח (סירוב, אין נתיב, זמן קצוב) — #1016.
            raise HTTPException(502, secondary_unreachable(exc, enroll_port))

        # ‏#883: המזהה — וממנו שם קובץ הטוקן — נגזר מה-SPKI שהמפעיל הצמיד
        # וה-handshake אימת, לא מהצהרת ה-JSON של המשני. חגורה שנייה על
        # שם הקובץ: רק ``sn_<hex>`` נכנס ל-``secondaries/``.
        secondary_id = interserver_auth.node_id_from_spki(expected_spki)
        if not storage_nodes.valid_node_id(secondary_id):
            raise HTTPException(400, f"מזהה משני לא תקין: {secondary_id!r}")
        cred_dir = Path(data_dir) / "secondaries"
        cred_dir.mkdir(parents=True, exist_ok=True)
        cred_path = cred_dir / f"{secondary_id}.token"
        # קודם הקובץ העמיד (0600), ורק אחרי שהצליח — רשומת ה-DB.
        interserver_auth.write_credential_0600(cred_path, result["token"])
        try:
            node_row = storage_nodes.enroll_node(
                ctx.conn, user, label=body.get("label", ""), base_url=url,
                node_id=secondary_id, pinned_spki=expected_spki,
                client_cert_ref=interserver_auth.certificate_ref(ident["cert_pem"]),
                credential_ref=str(cred_path),
                protocol_version=interserver_auth.PROTOCOL_VERSION,
                group_id=body.get("group_id"))
        except (ValueError, sqlite3.IntegrityError) as exc:
            # הרשומה נכשלה אבל המשני כבר קשור — לא re-enroll שקט: מנקים את
            # הקובץ היתום ומדווחים שהשחזור הוא ניתוק מקומי על המשני.
            # codeql[py/path-injection] false positive: שם הקובץ עבר valid_node_id (sn_<16 hex>) למעלה (#291)
            cred_path.unlink(missing_ok=True)
            raise HTTPException(
                409, f"רישום המשני נכשל ({exc}). המשני כבר קשור — נתקו אותו "
                     f"מקומית (break-glass) לפני רישום מחדש.")
        return {"ok": True, "id": node_row, "node_id": secondary_id,
                "secondary_spki": expected_spki}

    return router
