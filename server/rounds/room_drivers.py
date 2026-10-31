"""‏#1269 — דרייברים לפי דגם מחשב, לכל דיסק יעד בסבב החדר.

המגירות של מחשב שיכפול הולכות למכונות אחרות, ולכן המלאי של המשכפל אינו
אומר דבר עליהן (‏interfaces.md סעיף 17) — אין כאן "אוטומטי". המפעיל בוחר
**דגם** מתוך מה שיש בספרייה (‏`drivers.models`), לכל `(mac, port)` שנבחר
כיעד. הבחירה נשמרת ברמת ה-round (‏`room_rounds.driver_models_json`), כמו
`target_slots`: היא חלה על כל גלי הסבב.

```json
[{"mac": "aa:bb:cc:00:00:21", "port": 2, "vendor": "LENOVO", "model": "ThinkCentre M70q Gen 3"}]
```

חסר / ``null`` / ``[]`` = בלי דרייברים — הסבב בדיוק כמו לפני ה-Issue.
"""

from __future__ import annotations

import json
import sqlite3

from server.library import drivers
from server.agent import registry


def validate(raw, selected: list[dict] | None, packages: list[dict]) -> list[dict] | None:
    """הבחירה בצורתה הקנונית, ``None`` כשאין בחירה, או ``ValueError`` בשמו.

    כל פריט: ‏MAC קנוני, חריץ שנמצא ב-`target_slots` (‏`selected` — כבר
    מאומת), ודגם **שיש לו חבילה בספרייה עכשיו**. השם שנשמר הוא זה של
    הספרייה, לא מה שהוקלד. אין "כל הדיסקים": בלי בחירת דיסקים אין למה
    לקשור דגם — ונכשלים בקול במקום לנחש.
    """
    if raw is None or raw == []:
        return None
    if not isinstance(raw, list):
        raise ValueError("בחירת הדרייברים אינה תקינה")
    if selected is None:
        raise ValueError("דרייברים לפי דיסק דורשים בחירת דיסקי יעד")
    targets = {(item["mac"], port) for item in selected for port in item["ports"]}
    known = {drivers.model_key(m["vendor"], m["model"]): m for m in drivers.models(packages)}
    chosen: dict[tuple[str, int], dict] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("בחירת הדרייברים אינה תקינה")
        mac = registry.normalize_mac(str(item.get("mac", "")))
        port = item.get("port")
        if mac is None or isinstance(port, bool) or not isinstance(port, int):
            raise ValueError("בחירת הדרייברים אינה תקינה")
        if (mac, port) in chosen:
            raise ValueError(f"דיסק {port} ב־{mac} נבחר פעמיים לדרייברים")
        if (mac, port) not in targets:
            raise ValueError(f"דרייברים לדיסק {port} ב־{mac}, שאינו בין דיסקי היעד")
        model = known.get(drivers.model_key(item.get("vendor"), item.get("model")))
        if model is None:
            raise ValueError(
                f"אין בשרת חבילת דרייברים לדגם {item.get('vendor')} {item.get('model')}")
        chosen[(mac, port)] = {"mac": mac, "port": port,
                               "vendor": model["vendor"], "model": model["model"]}
    return [chosen[key] for key in sorted(chosen)]


def _loaded(round_row: sqlite3.Row | None) -> list[dict] | None:
    if round_row is None or round_row["driver_models_json"] is None:
        return None
    return json.loads(round_row["driver_models_json"])


def view(round_row: sqlite3.Row) -> list[dict] | None:
    return _loaded(round_row)


def ports_for(round_row: sqlite3.Row | None, mac: str) -> list[int] | None:
    """החריצים של המכונה הזו שנבחר להם דגם. ‏``None`` = הסבב בלי דרייברים."""
    chosen = _loaded(round_row)
    if chosen is None:
        return None
    return sorted(item["port"] for item in chosen if item["mac"] == mac)


def choice(round_row: sqlite3.Row, mac: str, port: int) -> dict | None:
    """הדגם שנבחר לחריץ הזה, או ``None`` — "לא נבחר", לא "אין התאמה"."""
    for item in _loaded(round_row) or []:
        if item["mac"] == mac and item["port"] == port:
            return {"vendor": item["vendor"], "model": item["model"]}
    return None
