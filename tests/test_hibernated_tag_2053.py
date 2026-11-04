"""#2053: התג "מצב שינה" בקונסולה נגזר מ-`src_unhib` הקיים במניפסט (#2213).

‏`GET /api/console/images` מעביר את השדה כמו שהוא, ו-`None` כשהוא חסר —
היעדרו אינו טענה שהמקור לא היה מושהה (עיקרון 5). התצוגה עצמה נבדקת
ב-`tests/hibernated_tag_2053.test.cjs`.
"""
from __future__ import annotations

import copy

import pytest

pytest.importorskip("fastapi")

from conftest import MANIFEST_256, write_image


def _listed(srv, image_id: str) -> dict:
    return next(i for i in srv["admin"].get("/api/console/images").json() if i["id"] == image_id)


@pytest.mark.parametrize("action", ["unhibernated", "unhibernated_logfile"])
def test_the_image_list_carries_src_unhib_as_it_is_in_the_manifest(server, action):
    m = copy.deepcopy(MANIFEST_256)
    m.update(id="img_205301", name="Hibernated Source", src_unhib=action)
    write_image(server["ctx"].library.root, m)
    assert _listed(server, m["id"])["src_unhib"] == action


def test_an_image_without_the_field_lists_null_not_a_claim(server):
    # ה-fixture כבר כתב את MANIFEST_256 — אימג' בלי השדה, כמו כל אימג' מלפני #2213.
    assert "src_unhib" not in MANIFEST_256
    row = _listed(server, MANIFEST_256["id"])
    assert "src_unhib" in row and row["src_unhib"] is None
