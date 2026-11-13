"""#1840: `GET /api/console/images` מעביר את מחיצות ה-BitLocker (`fs: "BitLocker"`).

הקונסולה מציגה לפיהן תג כתום ואת המשפט "מוצפן ב-BitLocker: יועתק כמו שהוא,
בלי כיווץ; במחשב אחר יידרש מפתח שחזור" — ברשימה, במגירה ובבחירת האימג'
לסבב (`tests/bitlocker_tag_1840.test.cjs`). `[]` = אין מחיצה מוצפנת.
"""
from __future__ import annotations

import copy

import pytest

pytest.importorskip("fastapi")

from conftest import MANIFEST_256, write_image


def test_the_image_list_names_the_bitlocker_partitions(server):
    m = copy.deepcopy(MANIFEST_256)
    m.update(id="img_184002", name="Encrypted C")
    m["partitions"][1].update(role="data", fs="BitLocker", used_bytes=None, expandable=False)
    write_image(server["ctx"].library.root, m)
    rows = {i["id"]: i for i in server["admin"].get("/api/console/images").json()}
    assert rows["img_184002"]["bitlocker"] == [3]
    assert rows[MANIFEST_256["id"]]["bitlocker"] == []
