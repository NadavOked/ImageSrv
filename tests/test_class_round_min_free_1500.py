"""#1500 -- סבב כיתה נפתח עם אימג' שכלל המקום הפנוי (#1354) אינו ניתן לבדיקה עליו.

#1494 גרם לחדר השיכפולים לסרב לאימג' כזה (`used_bytes` של מחיצת המערכת
`null`), כי הסוכן מסרב לו בכל מחשב. סבב כיתה -- מהקונסולה
(`POST /api/console/sessions`) ומהתחנה (`POST /api/v1/agent/sessions`) --
לא עבר באותה בדיקה: הוא נפתח, העיר את הכיתה ב-WoL, ונכשל בכל מחשב באותה
הודעה. כאן שני המסלולים מסרבים (409) באותה מחרוזת של הסוכן, **לפני** שמשהו
נפתח או מוער; כלל כבוי (`min_free_gb = 0`) ואימג' שנמדד אינם נחסמים.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from conftest import setup_classroom, write_image
from test_old_image_used_bytes_1494 import UNCHECKED, old_image
from test_station import open_body

from server.db import set_setting
from server.library import minfree

OLD, MEASURED = "img_5790e4", "img_7f3a91"


@pytest.fixture()
def station(tmp_path, images_root, clock):
    from fastapi.testclient import TestClient

    from server.app import create_app
    from server.console import users

    write_image(images_root, old_image(OLD))          # C: לא נמדד -- הכלל אינו ניתן לבדיקה
    woken: list[bytes] = []
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     now_fn=clock, wol_send=woken.append)
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test",
                 is_builtin=True, check_policy=False)
    users.create(ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test",
                 check_policy=False)
    admin, deploy = TestClient(app), TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    deploy.post("/api/console/login", json={"username": "labtech", "password": "deploy-pass-1"})
    return {"ctx": ctx, "admin": admin, "deploy": deploy, "anon": TestClient(app),
            "woken": woken, "ids": setup_classroom({"admin": admin})}


def open_from_console(station, image_id):
    r = station["deploy"].post("/api/console/sessions", json={
        "group_id": station["ids"]["group"], "image_id": image_id})
    return r.status_code, (r.json().get("detail") if r.status_code != 200 else None)


def open_from_station(station, image_id):
    r = station["anon"].post("/api/v1/agent/sessions", json=open_body(
        station["ids"]["mac1"], image_id=image_id))
    return r.status_code, (r.json().get("error") if r.status_code != 200 else None), \
        (r.json().get("code") if r.status_code != 200 else None)


def sessions(station) -> int:
    return station["ctx"].conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]


def test_the_console_refuses_a_class_round_it_could_not_check(station):
    status, detail = open_from_console(station, OLD)
    assert (status, detail) == (409, UNCHECKED)
    assert sessions(station) == 0
    assert station["woken"] == []                      # אף מחשב לא הוער לסבב שנכשל


def test_the_station_refuses_a_class_round_it_could_not_check(station):
    status, message, code = open_from_station(station, OLD)
    assert (status, message, code) == (409, UNCHECKED, "min_free_unchecked")
    assert sessions(station) == 0
    assert station["woken"] == []


@pytest.mark.parametrize("opener", [open_from_console, open_from_station],
                         ids=["console", "station"])
def test_the_rule_switched_off_does_not_block(station, opener):
    set_setting(station["ctx"].conn, minfree.SETTING, "0")
    assert opener(station, OLD)[0] == 200
    assert sessions(station) == 1


@pytest.mark.parametrize("opener", [open_from_console, open_from_station],
                         ids=["console", "station"])
def test_a_measured_image_still_opens(station, opener):
    assert opener(station, MEASURED)[0] == 200
    assert sessions(station) == 1
