"""‏#2410 — תקרת הגוף חלה על **כל** נתיב כתיבה בפורט הסוכן (‎:8080).

הקומיט הראשון של #2410 חסם את 9 נתיבי `server/agent/api.py`. אבל באפליקציית
הסוכן יושבים גם הקיוסק של מחשב הבנייה (‏`/api/console/{login,sessions,room…}` —
אותם route-ים של הקונסולה), ‏`station.py`, ‏`cloner_round.py` והמניפסטים של
הקליטה וההפצה הישירה — וכל אחד מהם קרא את הגוף כולו לזיכרון. ‏`body.BodyCap`
יושב לפני הניתוב, והטסט כאן עובר על **כל** נתיב שרשום ב-`create_agent_app`:
נתיב חדש שיתווסף בלי תקרה נופל כאן בשמו, בלי שמישהו יזכור להוסיף לו טסט.

החריג היחיד הוא העלאת נתוני האימג' (‏`PUT /api/v1/capture/{task_id}/files/{filename}`,
‏`body.STREAM_ROUTES`) — והטסט בודק במפורש שקליטה אמיתית, עם קובץ גדול
מהתקרה ועם המניפסט שלה, עוברת.

ובאותו ענף, ‏#2411: פירור אתחול (‏`/boot/step?mac=`) נרשם רק כשה-MAC תואם
את החכירה — כמו `/boot/menu`.

בקרה שלילית: בלי `BodyCap` ב-`create_agent_app`, ועם `station.py`,
‏`cloner_round.py` ו-`capture.py` של a000bec7 — כל נתיבי הכתיבה שאינם ב-`api.py`
מחזירים כאן משהו אחר מ-413; ובלי `_record_verified` ב-`app.py` הזר רושם פירור.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from server.agent import body as agent_body
from server.app import create_agent_app, create_console_app, create_runtime
from server.console import users
from test_capture import PART_A, do_capture, make_task, manifest_for, setup_build_machine, sha
from test_machine_identity import MAC1, identity_server  # noqa: F401

OVER_CAP = agent_body.MAX_BYTES + 1
#: החוזה, כתוב כאן ולא נקרא מ-`body.STREAM_ROUTES`: נתיב העלאה חדש הוא הכרעה
#: שמישהו כותב בשני המקומות, לא חריג שנפתח בשקט.
STREAM = {("PUT", "/api/v1/capture/x1/files/x1")}
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _walk(routes, prefix: str = ""):
    """‏(מתודות, נתיב) של כל route — גם ב-FastAPI שמחזיק ראוטרים מוכללים בעצלות
    (‏`_IncludedRouter`, ‏0.141) וגם בשטוח (‏0.115.11, גרסת הייצור)."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route.methods, prefix + route.path
        elif hasattr(route, "original_router"):
            yield from _walk(route.original_router.routes,
                             prefix + route.include_context.prefix)


@pytest.fixture()
def agent_env(tmp_path: Path, images_root: Path):
    from test_sender import Recorder                       # noqa: PLC0415

    rt = create_runtime(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                        sender_runner=Recorder(block=True), wol_send=lambda _p: None)
    console_app, agent_app = create_console_app(rt), create_agent_app(rt)
    users.create(rt.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True,
                 check_policy=False)
    admin = TestClient(console_app)
    assert admin.post("/api/console/login",
                      json={"username": "noc", "password": "admin-pass-123"}).status_code == 200
    yield {"rt": rt, "app": agent_app, "ctx": rt.ctx, "admin": admin,
           "anon": TestClient(agent_app, raise_server_exceptions=False)}
    rt.ctx.sender.stop()


def _write_routes(app) -> list[tuple[str, str]]:
    found = []
    for methods, path in _walk(app.routes):
        for method in sorted(set(methods) & WRITE_METHODS):
            found.append((method, re.sub(r"\{[^}]+\}", "x1", path)))
    return found


def test_the_walk_sees_the_routes_the_issue_named(agent_env):
    """בלי זה, טסט שעובר על רשימה ריקה היה ירוק (עיקרון 5)."""
    paths = {path for _method, path in _write_routes(agent_env["app"])}
    for expected in ("/api/v1/agent/hello", "/api/v1/agent/sessions",
                     "/api/v1/agent/cloner-round", "/api/v1/capture/x1/preflight",
                     "/api/v1/capture/x1/manifest", "/api/v1/direct/x1/manifest",
                     "/api/console/login", "/api/console/room", "/api/console/tasks/capture"):
        assert expected in paths, expected
    assert len(paths) >= 30, sorted(paths)


def test_every_write_route_on_the_agent_port_is_capped(agent_env):
    client, refused, streams = agent_env["anon"], {}, []
    payload = b'{"pad": "' + b"x" * OVER_CAP + b'"}'
    for method, path in _write_routes(agent_env["app"]):
        if (method, path) in STREAM:
            streams.append((method, path))
            continue
        response = client.request(method, path, content=payload,
                                  headers={"Content-Type": "application/json"})
        if response.status_code != 413 or response.json().get("code") != "payload_too_large":
            refused[f"{method} {path}"] = (response.status_code, response.text[:120])
    assert not refused, refused
    assert set(streams) == STREAM


def test_a_chunked_body_over_the_cap_is_refused_too(agent_env):
    """בלי `Content-Length` — המונה בזמן הקריאה הוא השער (‏`/api/console/room`
    קורא `request.json()` בעצמו, ולכן רק `BodyCap` עומד בדרכו)."""
    raw = b'{"pad": "' + b"x" * OVER_CAP + b'"}'

    def chunks():
        for i in range(0, len(raw), 64 * 1024):
            yield raw[i:i + 64 * 1024]

    response = agent_env["anon"].post("/api/console/room", content=chunks(),
                                      headers={"Content-Type": "application/json"})
    assert "content-length" not in {k.lower() for k in response.request.headers}
    assert response.status_code == 413, response.text


def test_a_body_under_the_cap_still_reaches_the_route(agent_env):
    """בקרה: גוף של ~900KB עובר את השער — ה-hello נענה (מכונה לא רשומה)."""
    from conftest import hello_body                       # noqa: PLC0415

    body = {**hello_body("aa:bb:cc:11:22:33"), "pad": "x" * (900 * 1024)}
    response = agent_env["anon"].post("/api/v1/agent/hello", json=body)
    assert response.status_code == 200 and response.json()["known"] is False


def test_a_real_capture_with_a_file_over_the_cap_lands_in_the_library(agent_env, images_root):
    """הנתיב הבינארי אינו חסום: מחיצה של 3MiB עולה, המניפסט (JSON, מתחת לתקרה)
    מתקבל, והאימג' נכנס לספרייה אחרי אימות ה-sha256."""
    big = b"\x5a" * (3 * agent_body.MAX_BYTES)
    mac = setup_build_machine(agent_env)
    created = make_task(agent_env, mac).json()
    manifest = manifest_for()
    manifest["partitions"][1]["sha256"] = sha(big)
    manifest["total_compressed_bytes"] = len(PART_A) + len(big)
    response = do_capture(agent_env, created["id"], manifest=manifest,
                          files={"p1.esp.pcl.zst": PART_A, "p3.windows.pcl.zst": big})
    assert response.status_code == 202, response.text
    row = agent_env["ctx"].conn.execute("SELECT state FROM tasks WHERE id = ?",
                                        (created["id"],)).fetchone()
    assert row["state"] == "done"
    assert (images_root / created["image_id"] / "p3.windows.pcl.zst").stat().st_size == len(big)
    raw = json.loads((images_root / created["image_id"] / "manifest.json").read_text(encoding="utf-8"))
    assert raw["partitions"][1]["sha256"] == sha(big)


# --- #2411: ‏/boot/step ------------------------------------------------------


def _step(server, client: str, mac: str) -> str | None:
    assert server[client].get(f"/boot/step?mac={mac}&s=menu").status_code == 200
    row = server["ctx"].conn.execute("SELECT step FROM boot_steps WHERE mac = ?",
                                     (mac,)).fetchone()
    return row["step"] if row else None


def test_a_stranger_cannot_write_a_stations_boot_trail(identity_server):
    assert _step(identity_server, "stranger", MAC1) is None


def test_the_station_still_writes_its_own_boot_trail(identity_server):
    """בקרה: ה-MAC מהכתובת שבחכירה שלו — הפירור נרשם כמו היום."""
    assert _step(identity_server, "station", MAC1) == "menu"
