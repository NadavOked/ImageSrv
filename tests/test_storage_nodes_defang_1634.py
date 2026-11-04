"""‏#1634 סעיף 4: ‏`GET /storage-nodes/{id}/images` ו-`/machines` הם פרוקסי לתשובת
המשני, והחזירו אותה גולמית — בניגוד ל-relay של הקונסולה (#1179), שמעביר כל
מחרוזת דרך `peer_safe.defang`. המשני הוא שרת אחר באתר אחר, ואינו מקור אמין
לדפדפן של הראשי.

הלקוח הבין-שרתי מוחלף בזיוף שמחזיר תשובה עוינת — כך הטסט אינו תלוי ב-mTLS
(‏pyOpenSSL), והמסלול שנבדק הוא ה-endpoint עצמו: מה שיוצא לדפדפן.
"""

from __future__ import annotations

from server import db
from server.interserver import peer_safe, storage_nodes

EVIL = '<img src=x onerror=alert(1)>"\'`&'
HTML = set("<>\"'`&")


class _Client:
    def __init__(self, answers):
        self.answers = answers

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_json(self, path, token):
        return self.answers[path]


def _node(server, monkeypatch, answers) -> str:
    conn = server["ctx"].conn
    conn.execute("INSERT INTO storage_nodes (id, label, base_url, credential_ref, enrolled_at)"
                 " VALUES ('n1634', 'חיפה', 'https://10.30.0.8:8443/api/interserver/v1', 'c', ?)",
                 (db.now_iso(),))
    conn.commit()
    monkeypatch.setattr(storage_nodes, "node_client", lambda *a, **k: (_Client(answers), "t"))
    return "n1634"


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in _strings(k) + _strings(v)]
    return []


def test_the_images_proxy_carries_no_html_syntax_from_the_secondary(server, monkeypatch):
    nid = _node(server, monkeypatch, {"/images": [
        {"id": "img_1", "name": EVIL, "size_bytes": 5, "created_at": EVIL, EVIL: [EVIL, 1, None]}]})
    body = server["admin"].get(f"/api/console/storage-nodes/{nid}/images").json()
    assert body["connected"] is True, body
    images = body["images"]
    assert _strings(images) and not any(HTML & set(s) for s in _strings(images)), images
    assert images[0]["name"] == peer_safe.defang(EVIL), "readable, in the look-alike characters"
    assert images[0]["id"] == "img_1" and images[0]["size_bytes"] == 5, "ids and numbers unchanged"


def test_the_machines_proxy_carries_no_html_syntax_from_the_secondary(server, monkeypatch):
    nid = _node(server, monkeypatch, {"/machines": {
        "node_id": EVIL, "machines": [{"mac": "aa:bb:cc:dd:ee:ff", "name": EVIL, "group": {"label": EVIL}}]}})
    body = server["admin"].get(f"/api/console/storage-nodes/{nid}/machines").json()
    assert body["connected"] is True, body
    out = {"machines": body["machines"], "node_id": body["node_id"]}
    assert _strings(out) and not any(HTML & set(s) for s in _strings(out)), out
    assert body["machines"][0]["mac"] == "aa:bb:cc:dd:ee:ff"
    assert body["machines"][0]["name"] == peer_safe.defang(EVIL)
