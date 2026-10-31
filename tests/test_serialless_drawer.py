"""‏#105 — כונן בלי serial הוא מצב שלישי: "לא ניתן לזהות את הכונן".

סבב החדר סופר מגירות לפי `serial` (‏`room.py:_tally`). כונן שהסוכן לא
הצליח לקרוא לו serial — מתאם USB-SATA, בקר RAID/HBA, ‏`hdparm` חסר —
מגיע ב-hello עם `"serial": null`. לפני התיקון `drawer_list` החזיר לו
‏`fresh: false`, **אותו ערך כמו כונן שנכתב**, ומסך מחשב הבנייה הציג
אותו תחת WRITTEN. כונן ריק שנשלף כ"כתוב" הוא הנזק עצמו.

שלושת המצבים שנבדקים כאן, ואסור לקפל אחד לשני (עיקרון 5):

- ‏`fresh: true`  — יש serial, והוא עוד לא נכתב בסבב הזה.
- ‏`fresh: false` — יש serial, והוא ב-`written_serials`: ראיה חיובית.
- ‏`fresh: null`  — אין serial. הסבב אינו יכול לדעת אם נכתב.

והשער (`fresh_serials`) **ממשיך** להשאיר אותו בחוץ, בכוונה: כונן שנספר
כ"טרי" בלי זהות היה מצרף את המכונה לכל גל וכותב אותו שוב ושוב, כי
‏`_tally` לעולם לא יכול לסמן אותו ככתוב. מה שהשתנה הוא שהוא כבר אינו
נעלם — הוא מוצג בשמו, ובחריץ שלו.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from server.rounds import room
from conftest import room_slots
from test_server_room import (  # noqa: F401 — ה-fixture עצמו
    CLONER1, cloner_hello, report, room as room_view, room_server,
)


def _disk(dev: str, port: int | None, serial: str | None) -> dict:
    return {"dev": dev, "port": port, "serial": serial,
            "model": "USB-SATA Bridge", "size_bytes": 256060514304,
            "removable": False, "scheme": "gpt", "has_data": False}


class _Conn:
    """DB מזויף מינימלי — `_disks` קורא שורה אחת של `disks_json`."""

    def __init__(self, disks: list[dict]):
        self._disks = disks

    def execute(self, *_a, **_k):
        disks = self._disks

        class _R:
            def fetchone(self):
                return {"disks_json": json.dumps(disks)}
        return _R()


# --- drawer_list: שלושה מצבים ------------------------------------------------


def test_a_drawer_with_no_serial_is_not_reported_as_written():
    """הליבה: `fresh: false` הוא "נכתב". כונן בלי serial לא נכתב-לפי-ראיה."""
    got = room.drawer_list(_Conn([_disk("sda", 1, None)]), "m", set())[0]
    assert got["fresh"] is None
    assert got["serial"] is None
    assert got["port"] == 1


def test_an_empty_serial_string_is_the_same_as_no_serial():
    """‏`""` מהסוכן אינו זהות. בלי זה מחרוזת ריקה אחת ב-`written` הייתה
    הופכת כל כונן חסר-serial ל"כתוב"."""
    got = room.drawer_list(_Conn([_disk("sda", 1, "")]), "m", {""})[0]
    assert got["fresh"] is None


def test_the_two_identified_states_are_unchanged():
    """שומר מפני תיקון-יתר: יש serial → ‏True/False כמו קודם."""
    got = room.drawer_list(
        _Conn([_disk("sda", 1, "S1"), _disk("sdb", 2, "S2")]), "m", {"S2"})
    assert [d["fresh"] for d in got] == [True, False]


# --- fresh_serials: השער נשאר סגור, ולא נופל על dev -------------------------


def test_fresh_serials_leaves_out_a_drawer_with_no_serial_and_invents_nothing():
    """אין "fallback" ל-`dev` (‏sda מתחלף בין אתחולים) ואין serial מומצא."""
    got = room.fresh_serials(
        _Conn([_disk("sda", 1, None), _disk("sdb", 2, "S2")]), "m", set())
    assert got == ["S2"]


def test_fresh_serials_with_only_unidentifiable_drawers_is_empty():
    assert room.fresh_serials(_Conn([_disk("sda", 1, None)]), "m", set()) == []


# --- מקצה לקצה: המסך אומר את זה, והמכונה אינה מצטרפת בשקט -----------------


def _open(deploy, **extra) -> dict:
    response = deploy.post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 2, **extra})
    return response


def _open_on_s1(room_server):
    """‏#1410: הסבב נפתח על בחירה מפורשת — חריץ 1 של shich-1, שזוהה (S1)
    כשנבחר. ה-hello שאחריו הוא מה שהטסט בודק."""
    return _open(room_server["deploy"],
                 target_slots=room_slots(room_server["ctx"].conn, {CLONER1: ["S1"]}))


def test_a_cloner_with_only_an_unidentifiable_drawer_is_shown_not_hidden(room_server):
    """המצב מה-Issue: מגירות מחוברות, "0 טריות", ואף מילה למה. עכשיו
    ‏`drawer_list` נושא `fresh: null` בחריץ שלו — המסך יודע להגיד."""
    deploy, anon = room_server["deploy"], room_server["anon"]
    assert _open_on_s1(room_server).status_code == 200
    answer = cloner_hello(anon, CLONER1, [None])     # אותו חריץ, וה-serial לא נקרא
    assert answer["session"] is None          # לא מצטרף — לא נכתב שוב ושוב

    machine = room_view(deploy)["machines"][0]
    assert machine["fresh_drawers"] == 0
    [drawer] = machine["drawer_list"]
    assert drawer["fresh"] is None
    assert drawer["port"] == 1


def test_a_mixed_machine_joins_for_its_identified_drawer_only(room_server):
    deploy, anon = room_server["deploy"], room_server["anon"]
    assert _open_on_s1(room_server).status_code == 200
    answer = cloner_hello(anon, CLONER1, ["S1", None])
    assert answer["session"] is not None
    drawers = {d["dev"]: d for d in room_view(deploy)["machines"][0]["drawer_list"]}
    assert drawers["sda"]["fresh"] is True
    assert drawers["sdb"]["fresh"] is None


def test_a_written_drawer_with_no_serial_is_journaled_as_unidentified(room_server):
    """‏`_tally` אינו סופר אותו (עיקרון 4: ערך מומצא ב-`written_serials`
    היה מדלג על כונן ריק בגל הבא) — וגם אינו אומר "המגירה הוחלפה", כי
    היא לא הוחלפה. היא פשוט לא ניתנת לזיהוי."""
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    opened = _open_on_s1(room_server).json()
    wave = cloner_hello(anon, CLONER1, ["S1", None])["session"]["id"]
    assert deploy.post("/api/console/room/start").status_code == 200
    report(anon, wave, CLONER1, {"sda": "done", "sdb": "done"})

    round_row = ctx.conn.execute(
        "SELECT * FROM room_rounds WHERE id = ?", (opened["id"],)).fetchone()
    total, written, skipped, _cancelled = room._tally(ctx.conn, round_row, ctx.store.members(wave))
    assert (total, written, skipped) == (1, {"S1"}, set())   # #1760: + the red-skipped

    events = [r["event"] for r in ctx.conn.execute(
        "SELECT event FROM journal WHERE event LIKE 'room_drawer_%'")]
    assert events == ["room_drawer_unidentified"]


def test_selecting_an_unidentifiable_slot_says_so_instead_of_not_connected(room_server):
    """הכונן **מחובר**. "אינה מחוברת" שולח את הטכנאי לבדוק כבל תקין."""
    deploy, anon = room_server["deploy"], room_server["anon"]
    cloner_hello(anon, CLONER1, ["S1", None], joining=False)
    response = _open(deploy, target_slots=[{"mac": CLONER1, "ports": [2]}])
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "לא ניתן לזהות את הכונן בחריץ 2" in detail
    assert "אינה מחוברת" not in detail
