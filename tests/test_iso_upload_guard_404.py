"""#404: העלאת ISO מהקונסולה עוברת את שומר 8081 -- ורק היא.

הפער שזה סוגר: ``iso.js`` שולח את ה-ISO כ-``application/octet-stream`` ושומר הקונסולה
(``fetch_guard``, #1633) התיר רק json ו-x-tar, כך שכל העלאה מהדפדפן קיבלה 415 -- ו-
``tests/test_iso_tab_404.py`` לא ראה זאת כי הוא קורא ל-``server["admin"]``, המעטפת
המשולבת, שבה השומר אינו. כאן ההעלאה עוברת ב-``create_console_app`` (8081, כמו בייצור),
והכותרת והנתיב נקראים **מקובץ ה-JS עצמו**, לא נכתבים מחדש בטסט -- כך שהלקוח והשרת לא
יכולים להיסחף זה מזה בלי שטסט יפול.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from conftest import complete_console_login
from server.app import create_console_app
from server.console import fetch_guard
from server.library import isos

ISO_JS = Path(__file__).resolve().parent.parent / "server" / "static" / "iso.js"
OCTET = "application/octet-stream"


def _console(server):
    console = TestClient(create_console_app(server["app"].state.runtime))
    complete_console_login(console, server["ctx"].conn, "noc", "admin-pass-123")
    return console


def _iso(size: int = 100_000) -> bytes:
    body = bytearray(b"\xa5" * size)
    body[:isos.PVD_AT] = b"\0" * isos.PVD_AT
    body[isos.PVD_AT:isos.PVD_AT + 7] = b"\x01CD001\x01"
    return bytes(body)


def _client_upload() -> tuple[str, str]:
    """``(נתיב, Content-Type)`` כפי ש-``iso.js`` שולח אותם -- נקרא מהקוד, לא מהטסט."""
    text = ISO_JS.read_text(encoding="utf-8")
    opened = re.findall(r'request\.open\("POST",\s*"([^"?]+)\??"', text)
    types = re.findall(r'request\.setRequestHeader\("Content-Type",\s*"([^"]+)"\)', text)
    assert len(opened) == 1 and len(types) == 1, (opened, types)
    return opened[0], types[0]


def _upload(console, raw, *, path=None, ctype=OCTET, params=None, headers=None):
    sent = {"Content-Type": ctype, **(headers or {})}
    query = {"name": "Win", "filename": "Win.iso", **(params or {})}
    return console.post(path or "/api/console/isos/upload", params=query, content=raw, headers=sent)


def _saved(server) -> list[str]:
    root = Path(server["ctx"].library.root) / isos.DIRNAME
    return sorted(p.name for p in root.rglob("*") if p.is_file()) if root.exists() else []


def test_the_client_and_the_guard_agree_on_the_upload_content_type(server):
    """הסכמה בין הלקוח לשרת: מה ש-``iso.js`` שולח, בנתיב שהוא שולח אליו, עובר את השומר."""
    path, ctype = _client_upload()
    assert path == "/api/console/isos/upload"
    scope = {"type": "http", "method": "POST", "path": path,
             "headers": [(b"content-type", ctype.encode())]}
    assert fetch_guard.refusal(scope) is None, (path, ctype)
    r = _upload(_console(server), _iso(), path=path, ctype=ctype)
    assert r.status_code == 200, r.text


def test_a_valid_iso_with_octet_stream_is_accepted_on_the_console_port(server):
    raw = _iso(300_000)
    r = _upload(_console(server), raw)
    assert r.status_code == 200, r.text
    assert r.json()["sha256"] == hashlib.sha256(raw).hexdigest()
    stored = Path(server["ctx"].library.root) / isos.DIRNAME / r.json()["id"] / isos.FILE
    assert stored.read_bytes() == raw, "הקובץ בדיסק זהה בית-בבית למה שהועלה"


def test_octet_stream_with_a_charset_parameter_is_the_same_type(server):
    r = _upload(_console(server), _iso(), ctype="application/octet-stream; charset=binary")
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("method, path", [
    ("POST", "/api/console/users"),
    ("POST", "/api/console/images/upload"),
    ("POST", "/api/console/drivers/upload"),
    ("POST", "/api/console/isos/upload/"),
    ("POST", "/api/console/isos/upload2"),
    ("POST", "/api/console/iso-folders"),
    ("PUT", "/api/console/isos/upload"),
    ("DELETE", "/api/console/isos/upload"),
], ids=lambda v: v.replace("/api/console/", ""))
def test_octet_stream_to_any_other_path_is_still_415(server, method, path):
    """החריג הוא לשיטה+נתיב אחד בלבד -- לא לכל הקונסולה, לא לתחילית ולא ללוכסן סוגר."""
    r = _console(server).request(method, path, content=b"{}", headers={"Content-Type": OCTET})
    assert (r.status_code, r.json().get("code")) == (415, "body_type"), (method, path, r.text)


def test_other_simple_types_are_still_refused_on_the_iso_path(server):
    """החריג הוא ל-octet-stream בלבד: סוגי ה"פשוטים" של דף זר נדחים גם בנתיב ה-ISO."""
    console = _console(server)
    for ctype in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x"):
        r = _upload(console, _iso(), ctype=ctype)
        assert r.status_code == 415, (ctype, r.text)
    assert _saved(server) == []


def test_a_foreign_page_still_cannot_upload_an_iso(server):
    """Fetch Metadata חל על הנתיב החדש כמו על כל נתיב: דף זר נדחה 403 ושום דבר לא נשמר."""
    r = _upload(_console(server), _iso(), headers={"Sec-Fetch-Site": "cross-site"})
    assert (r.status_code, r.json().get("code")) == (403, "cross_site"), r.text
    assert _saved(server) == []


def test_octet_stream_body_without_a_content_type_is_still_415(server):
    r = _console(server).post("/api/console/isos/upload", params={"filename": "a.iso"}, content=_iso())
    assert r.status_code == 415, r.text


def test_a_file_that_is_not_an_iso_is_still_refused_through_the_guard(server):
    r = _upload(_console(server), b"PK\x03\x04" + b"\0" * 400_000)
    assert r.status_code == 400, r.text
    assert _saved(server) == []


def test_a_wrong_sha256_is_still_refused_through_the_guard(server):
    r = _upload(_console(server), _iso(), headers={"X-Expected-Sha256": "0" * 64})
    assert r.status_code == 400, r.text
    assert _saved(server) == []
