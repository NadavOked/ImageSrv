"""‏#553 — כונן שנכשל מדווח בשורה שאפשר לפעול לפיה.

**נמדד על ברזל, 08/09/2026:** סבב על 5 מגירות נפל, ‏**המפעיל לא ראה
שום דבר.** המסך הראה `0 of 5 written` ותו לא, ואיזה כונן נפל היה
קיים בשרת ולא הוצג בשום מקום.

## ⚠️ והמלכודת שהופכת את זה למסוכן

**שם ההתקן בלינוקס אינו סדר יציאות ה-SATA.** נמדד על HP2 באותו יום:

```
sda → port 1     sdb → port 3     sdc → port 2
```

שורה שאומרת "‏sdb נכשל" ומתורגמת ל"מגירה 2" **שולחת את הטכנאי
למגירה הלא נכונה** — והוא מוציא כונן תקין ומשאיר את הפגום בפנים.
**זה גרוע מלא לדווח בכלל.**

`port` הוא ה-`ataN` שהסוכן מדווח — החריץ הפיזי. הקרנל ממספר מ-1,
ולכן `ata1` הוא החריץ הראשון בבקר: **המגירה העליונה** (#27).
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from server.rounds import room


def _disk(dev: str, port: int | None, serial: str | None,
          model: str | None = "SAMSUNG MZ7LN256") -> dict:
    return {"dev": dev, "port": port, "serial": serial, "model": model,
            "size_bytes": 256060514304, "removable": False,
            "scheme": "gpt", "has_data": True}


class _Member(dict):
    """שורת `session_members` מזויפת — רק `targets_json` נקרא כאן."""


def _drawers(disks: list[dict], targets: list[dict], written=frozenset()):
    """קורא ל-`drawer_list` האמיתי עם DB מזויף מינימלי.

    ‏**הפונקציה האמיתית ולא עותק שלה:** טסט שמשכפל את הלוגיקה עובר
    גם כשהמימוש נסחף.
    """
    class _Conn:
        def execute(self, *_a, **_k):
            class _R:
                def fetchone(self_inner):
                    return {"disks_json": json.dumps(disks)}
            return _R()

    member = _Member(targets_json=json.dumps(targets))
    return room.drawer_list(_Conn(), "aa:bb:cc:dd:ee:ff", set(written), member)


# --- המזהים שהשורה נשענת עליהם -------------------------------------------


def test_the_drawer_carries_serial_and_model():
    """בלי אלה, שורת הכשל אומרת "משהו נפל" ושולחת אדם לחפש."""
    got = _drawers([_disk("sda", 1, "naa.111")], [])[0]
    assert got["serial"] == "naa.111"
    assert got["model"] == "SAMSUNG MZ7LN256"
    assert got["port"] == 1


def test_the_physical_port_is_not_the_device_order():
    """**המלכודת עצמה**, בדיוק כפי שנמדדה על HP2.

    ‏`sdb` היא מגירה **3**. טסט שמניח `sda→1, sdb→2, sdc→3` היה
    עובר על נתונים מומצאים ונכשל על המכונה.
    """
    disks = [_disk("sda", 1, "naa.1"), _disk("sdb", 3, "naa.2"),
             _disk("sdc", 2, "naa.3")]
    by_dev = {d["dev"]: d["port"] for d in _drawers(disks, [])}
    assert by_dev == {"sda": 1, "sdb": 3, "sdc": 2}


def test_a_disk_without_a_port_reports_none_and_not_a_guess():
    """‏NVMe, ‏VM ובקר לא-ATA אינם מדווחים חריץ.

    **‏`None` ולא `0` ולא ניחוש** — מספר מגירה שגוי גרוע מהיעדר מספר,
    וזה בדיוק מה שההערה ב-`port_from_path` אומרת.
    """
    got = _drawers([_disk("nvme0n1", None, "nvme-1")], [])[0]
    assert got["port"] is None


@pytest.mark.parametrize("bad", [True, False, "1", 1.0])
def test_a_port_that_is_not_an_int_is_refused(bad):
    """‏`True` הוא `int` בפייתון. בלי הסינון, "מגירה True" על המסך."""
    assert _drawers([_disk("sda", bad, "naa.1")], [])[0]["port"] is None


# --- מה שהשורה מציגה ------------------------------------------------------


def test_a_failed_drawer_carries_its_state_and_error():
    """המצב והשגיאה מגיעים מ-`targets_json` של החבר."""
    got = _drawers(
        [_disk("sda", 1, "naa.1")],
        [{"dev": "sda", "state": "failed",
          "error": "fanout: buffer overrun (drive too slow)"}])[0]
    assert got["state"] == "failed"
    assert "buffer overrun" in got["error"]


def test_a_drawer_that_finished_is_not_marked_failed():
    """רדיוס הפגיעה — מגירה שהצליחה אינה נכנסת לרשימת הכשלים."""
    got = _drawers([_disk("sdb", 3, "naa.2")],
                   [{"dev": "sdb", "state": "done", "error": None}])[0]
    assert got["state"] == "done"
    assert got["error"] is None


def test_a_disk_the_round_never_touched_has_no_state():
    """כונן שלא היה יעד אינו "לא נכשל" — הוא לא נבדק.

    ‏`None` ולא `"ok"`: השניים נראים זהים על המסך ואינם זהים.
    """
    got = _drawers([_disk("sdc", 2, "naa.3")], [])[0]
    assert got["state"] is None
    assert got["error"] is None
