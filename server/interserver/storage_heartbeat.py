"""הדופק של הראשי למשניים המצומדים — #1564.

המשני רושם ``parent_credentials.last_contact_at`` רק כשהראשי פונה אליו
ועובר את כל השומר (``interserver_api.authenticate_parent``). בלי פנייה
יזומה, משני תקין ושקט היה מוצג כתום ב-DCUI אחרי 10 דקות שבהן פשוט לא
קרה כלום. הדופק הוא אותו ``GET /ping`` המאומת של "בדוק חיבור" (#1017),
כל ``HEARTBEAT_SECONDS``, לכל משני רשום ופעיל.

- **היעדים הם רק שורות ``storage_nodes``** (``base_url`` + ה-SPKI המוצמד),
  שנכתבות ב-enrollment של מנהל בלבד. שום דבר בתשובת המשני אינו קובע לאן
  הדופק הבא נשלח.
- **תקרה קצרה** (``TIMEOUT_SECONDS``) ל-handshake ולבקשה: משני שלא עונה
  מעכב את הדופק של השאר שניות, לא דקות. הדופק רץ במשימה משלו לצד שעון
  החדר (``app._storage_heartbeat``), ולכן גם כך אינו מעכב את ``room.sweep``.
- כל תוצאה נרשמת על השורה כמו בכל קריאה בין-שרתית (``record_contact``):
  תשובה תקינה = ``last_seen_at``; כשל = ``last_error``/``last_error_at``.
"""

from __future__ import annotations

import logging

from server.interserver import interserver_auth, storage_nodes

log = logging.getLogger("imagesrv.storage_heartbeat")

#: הכרעת נדב 17/10: 150 שניות. הסף לירוק ב-DCUI של המשני הוא 10 דקות
#: (``dcui.view.PARENT_CONTACT_FRESH_SECONDS``) — שלושה דופקים שהוחמצו
#: ברצף עדיין אינם הופכים אותו לכתום.
HEARTBEAT_SECONDS = 150.0
#: תקרה ל-handshake ולבקשה של דופק אחד.
TIMEOUT_SECONDS = 5.0


def beat(conn, data_dir, *, timeout: float = TIMEOUT_SECONDS) -> dict:
    """דופק אחד לכל משני רשום ופעיל. מחזיר ``{row_id: None | error}``.

    רק שרת ``standalone`` מחזיק משניים; על משני זה לא עושה דבר. כשל של
    משני אחד נרשם על השורה שלו ואינו עוצר את השאר."""
    if storage_nodes.role(conn) != storage_nodes.ROLE_STANDALONE:
        return {}
    nodes = conn.execute(
        "SELECT id, label, base_url, node_id, pinned_spki, credential_ref,"
        " disabled_at FROM storage_nodes WHERE disabled_at IS NULL ORDER BY id"
    ).fetchall()
    results: dict = {}
    for node in nodes:
        answer, error = None, None
        try:
            client, token = storage_nodes.node_client(conn, data_dir, node,
                                                      timeout=timeout)
            with client:
                answer = client.ping(token)
        except Exception as exc:                             # noqa: BLE001
            error = interserver_auth.redact_secrets(str(exc)) or type(exc).__name__
        else:
            if not isinstance(answer, dict) or answer.get("ok") is not True:
                error = "תשובה לא צפויה משרת המשנה"
        storage_nodes.record_contact(conn, node["id"], error=error)
        if error is None:
            storage_nodes.record_version(conn, node["id"], answer)
        else:
            log.warning("דופק ל-%s נכשל: %s", node["label"], error)
        results[node["id"]] = error
    return results
