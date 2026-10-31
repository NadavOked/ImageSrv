"""‏#953: אימג' שאינו נכנס למגירה הקטנה ביותר בחדר — מסורב בשרת, לא רק במסך.

נדב, 17/09: "שמתי כונן 256, אימג' שלא נכנס ל-256 לא אמור להופיע
באפשרויות." הרצפה היא המגירה הקטנה ביותר שדווחה ב-hello האחרון (או
בין המגירות שנבחרו, ‏#695), והסיבה נוקבת במגירה ובמספרים. בלי דיווח —
אין סירוב (בדיקה 2.7 במכונה היא הקו האחרון), והמסך מקבל `disk_floor:
null` כדי להגיד זאת. הספרייה חושפת `min_target_bytes` (הנגזר)
ו-`used_bytes` (סכום, או `null` כשמחיצה לא נמדדה).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import Clock, hello_body, write_image, MANIFEST_256

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None

CLONER1 = "aa:bb:cc:00:00:31"
CLONER2 = "aa:bb:cc:00:00:32"
GB = 10 ** 9
DISK_256 = 256_060_514_304          # 256.06 GB — כונן "256" אמיתי (17/09)
DISK_500 = 500_107_862_016          # 500.1 GB


def _manifest(image_id: str, name: str, windows_bytes: int) -> dict:
    """אותה גיאומטריה כמו MANIFEST_256, עם מחיצת Windows בגודל אחר —
    ‏`required_bytes` נגזר ממנה (‏#82), לא מהשדה המוצהר."""
    esp, win = MANIFEST_256["partitions"]
    return {
        **MANIFEST_256, "id": image_id, "name": name,
        "source_disk_bytes": 320 * GB, "min_target_bytes": 320 * GB,
        "partitions": [esp, {**win, "size_bytes": windows_bytes}],
    }


# סוף הפריסה = 1085440×512 + size + 1MiB → 229.56GB ו-299.56GB, כלומר
# "צריך 230GB" ו"צריך 300GB" אחרי עיגול כלפי מעלה.
IMAGE_230 = _manifest("img_f17230", "Fits 256", 229 * GB)
IMAGE_300 = _manifest("img_f17300", "Needs 300", 299 * GB)


@pytest.fixture()
def fit_server(tmp_path: Path):
    if TestClient is None:
        pytest.skip("fastapi is required")
    from test_sender import Recorder                       # noqa: PLC0415

    from server.console import users
    from server.app import create_app

    images = tmp_path / "images"
    write_image(images, IMAGE_230)
    write_image(images, IMAGE_300)
    app = create_app(
        tmp_path / "data", images, "http://10.44.12.10:8080",
        now_fn=Clock(), sender_runner=Recorder(), wol_send=lambda _b: None,
    )
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    admin = TestClient(app)
    admin.post("/api/console/login",
               json={"username": "noc", "password": "admin-pass-123"})
    for mac, name in ((CLONER1, "1"), (CLONER2, "2")):
        assert admin.post("/api/console/machines", json={
            "mac": mac, "name": name, "group_id": "grp_CLONERS",
        }).status_code == 200
    yield {"admin": admin, "anon": TestClient(app)}
    ctx.sender.stop()


def hello_with_disks(client, mac: str, sizes: list[int]) -> None:
    body = hello_body(mac)
    body["disks"] = [
        {"dev": f"sd{chr(ord('a') + i)}", "size_bytes": size, "port": i + 1,
         "model": "Drawer SSD", "serial": f"{mac[-2:]}-{i}", "removable": False,
         "scheme": "gpt", "has_data": False}
        for i, size in enumerate(sizes)
    ]
    assert client.post("/api/v1/agent/hello", json=body).status_code == 200


def reported_slots(client) -> list[dict]:
    """‏#1410: כל מגירה מזוהה שמסך החדר מציג — "בחר הכול" של המפעיל."""
    slots = []
    for m in client.get("/api/console/room").json()["machines"]:
        ports = sorted(d["port"] for d in m["drawer_list"] if d["serial"] and d["port"])
        if ports:
            slots.append({"mac": m["mac"], "ports": ports})
    return slots


def open_round(client, image_id: str, **extra):
    """פתיחת סבב; בלי `target_slots` מפורש — כל המגירות שדווחו."""
    extra.setdefault("target_slots", reported_slots(client))
    return client.post("/api/console/room",
                       json={"image_id": image_id, "target_drives": 4, **extra})


def test_an_image_bigger_than_the_smallest_drawer_is_refused_with_the_reason(fit_server):
    admin, anon = fit_server["admin"], fit_server["anon"]
    hello_with_disks(anon, CLONER1, [DISK_500, DISK_500])
    hello_with_disks(anon, CLONER2, [DISK_500, DISK_256])   # דיסק 2 במחשב 2

    r = open_round(admin, "img_f17300")

    assert r.status_code == 409
    assert r.json()["detail"] == "דיסק 2 במחשב 2 הוא 256GB, האימג' צריך 300GB"
    assert admin.get("/api/console/room").json()["round"] is None   # לא נפתח


def test_an_image_that_fits_the_smallest_drawer_opens(fit_server):
    admin, anon = fit_server["admin"], fit_server["anon"]
    hello_with_disks(anon, CLONER1, [DISK_500, DISK_500])
    hello_with_disks(anon, CLONER2, [DISK_500, DISK_256])

    assert open_round(admin, "img_f17230").status_code == 200


def test_without_any_disk_report_there_is_no_floor_and_nothing_to_select(fit_server):
    """מכונות כבויות שטרם דיווחו — אין רצפה: המסך מקבל ‏`disk_floor: null`
    ומציג "גודל הדיסקים לא ידוע". מאז #1410 גם אין מה לבחור, ולכן הסבב
    נדחה על הבחירה (‏400) — לא על הגודל (‏409)."""
    admin = fit_server["admin"]
    assert admin.get("/api/console/room").json()["disk_floor"] is None
    r = open_round(admin, "img_f17300")
    assert r.status_code == 400 and r.json()["detail"] == "יש לבחור לפחות דיסק יעד אחד"


def test_a_drawer_without_a_size_is_not_a_floor(fit_server):
    """סוכן ישן בלי `size_bytes` (או 0) — "לא נמדד", לא "0 בייט"."""
    admin, anon = fit_server["admin"], fit_server["anon"]
    hello_with_disks(anon, CLONER1, [DISK_500, 0])
    assert admin.get("/api/console/room").json()["disk_floor"]["size_bytes"] == DISK_500
    assert open_round(admin, "img_f17300").status_code == 200


def test_the_status_view_names_the_floor_the_screen_compares_against(fit_server):
    admin, anon = fit_server["admin"], fit_server["anon"]
    hello_with_disks(anon, CLONER1, [DISK_500, DISK_500])
    hello_with_disks(anon, CLONER2, [DISK_500, DISK_256])

    floor = admin.get("/api/console/room").json()["disk_floor"]

    assert floor == {"mac": CLONER2, "name": "2", "port": 2, "dev": "sdb",
                     "size_bytes": DISK_256}


def test_selected_slots_that_exclude_the_small_drawer_let_the_image_through(fit_server):
    """‏#695: כשנבחרו מגירות, רק הן הרצפה — הקטנה שלא נבחרה אינה חוסמת."""
    admin, anon = fit_server["admin"], fit_server["anon"]
    hello_with_disks(anon, CLONER1, [DISK_500, DISK_500])
    hello_with_disks(anon, CLONER2, [DISK_500, DISK_256])

    refused = open_round(admin, "img_f17300",
                         target_slots=[{"mac": CLONER2, "ports": [1, 2]}])
    assert refused.status_code == 409
    assert refused.json()["detail"].startswith("דיסק 2 במחשב 2 הוא 256GB")

    assert open_round(admin, "img_f17300",
                      target_slots=[{"mac": CLONER2, "ports": [1]}]).status_code == 200


def test_the_library_list_exposes_the_derived_requirement_and_used_bytes(tmp_path):
    from server.library.images import ImageLibrary, required_bytes

    write_image(tmp_path, IMAGE_300)
    unmeasured = {
        **IMAGE_230, "id": "img_dead00", "name": "Hibernated",
        "partitions": [IMAGE_230["partitions"][0],
                       {**IMAGE_230["partitions"][1], "used_bytes": None}],
    }
    write_image(tmp_path, unmeasured)

    by_id = {img["id"]: img for img in ImageLibrary(tmp_path).public_list()}

    fit300 = by_id["img_f17300"]
    assert fit300["min_target_bytes"] == required_bytes(IMAGE_300)
    assert 299 * GB < fit300["min_target_bytes"] <= 300 * GB    # הנגזר, לא 320GB המוצהר
    assert fit300["used_bytes"] == 31457280 + 84509376512
    # מחיצה שלא נמדדה ("would not mount", #84) — "לא ידוע", לא 0 ולא סכום חלקי.
    assert by_id["img_dead00"]["used_bytes"] is None


@pytest.mark.parametrize("floor_bytes", [None, DISK_500])
def test_an_image_that_fits_no_disk_size_is_refused_not_waved_through(floor_bytes):
    """‏#1863: דרישה שאינה ניתנת לקביעה (`min_disk_bytes` = None) — כאן מחיצת
    מערכת שאינה הניתנת להרחבה, עם 5GB פנויים מול מינימום 20 — **אינה "נכנס"**.
    עד #1863 ‏`fit_refusal` החזיר כאן None, הסבב נפתח, והסוכן סירב בכל מגירה;
    והרשימה של המכונה (`allowed_for_disks`) כבר הסתירה אותו — שתי הכרעות."""
    from server.library.minfree import min_disk_bytes
    from server.rounds.room import fit_refusal

    esp, win = IMAGE_230["partitions"]
    # exactly one marked partition is the one the restore grows (expandable_candidate) -- not C:
    stuck = {**IMAGE_230, "partitions": [
        {**esp, "expandable": True},
        {**win, "expandable": False, "used_bytes": win["size_bytes"] - 5 * GB}]}
    assert min_disk_bytes(stuck, 20) is None
    floor = None if floor_bytes is None else {
        "mac": CLONER1, "name": "1", "port": 1, "dev": "sda", "size_bytes": floor_bytes}
    reason = fit_refusal(stuck, floor, 20)
    assert reason is not None and reason.startswith("לא נכנס — יישארו רק 4.7 GB פנויים ב-C:"), reason
    assert fit_refusal(stuck, floor, 0) is None     # the rule off: the layout fits 500GB
