"""#1354 — the minimum free space is a console setting (`min_free_gb`), not a
constant: default 20, a whole number 0-500, admin only, journalled, and it
reaches the agent in every hello answer. The console shows "fits a disk from
X GB" including it, and the cloning room refuses an image before a wave opens
with the reason in words.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from conftest import hello_body, setup_classroom, write_image
from server.library.images import required_bytes
from test_min_free_1354 import GIB, win11
from test_room_image_fit import (CLONER1, CLONER2, DISK_256, DISK_500, fit_server,  # noqa: F401
                                 hello_with_disks, open_round)


def journal_details(server, event: str) -> list[tuple[str, str]]:
    return [(r["user"], r["detail"]) for r in server["ctx"].conn.execute(
        "SELECT user, detail FROM journal WHERE event = ? ORDER BY id", (event,))]


def test_the_setting_shows_the_default_20_until_it_is_set(server):
    assert server["admin"].get("/api/console/settings").json()["min_free_gb"] == "20"


def test_admin_sets_it_and_the_change_is_journalled(server):
    r = server["admin"].post("/api/console/settings", json={"min_free_gb": 40})
    assert r.status_code == 200, r.text
    assert server["admin"].get("/api/console/settings").json()["min_free_gb"] == "40"
    assert ("noc", "min_free_gb=40") in journal_details(server, "setting_change")


@pytest.mark.parametrize("bad", [-1, 501, "abc", "020", 1.5, True, "", None, "4 0"])
def test_a_value_outside_a_whole_0_to_500_is_refused_and_nothing_is_written(server, bad):
    r = server["admin"].post("/api/console/settings",
                             json={"session_wait_seconds": "77", "min_free_gb": bad})
    assert r.status_code == 400
    assert "0–500" in r.json()["detail"]
    settings = server["admin"].get("/api/console/settings").json()
    assert settings["min_free_gb"] == "20"
    assert settings["session_wait_seconds"] != "77", "a key of the same request was written"
    assert journal_details(server, "setting_change") == []


def test_zero_and_500_are_accepted(server):
    for value in (0, "500"):
        assert server["admin"].post("/api/console/settings",
                                    json={"min_free_gb": value}).status_code == 200


def test_the_deploy_user_cannot_change_it(server):
    assert server["deploy"].post("/api/console/settings",
                                 json={"min_free_gb": 5}).status_code == 403
    assert server["admin"].get("/api/console/settings").json()["min_free_gb"] == "20"


def hello_value(server, mac: str):
    r = server["anon"].post("/api/v1/agent/hello", json=hello_body(mac))
    assert r.status_code == 200, r.text
    return r.json().get("min_free_gb", "MISSING")


def test_every_hello_carries_the_value_the_agent_enforces(server):
    ids = setup_classroom(server)
    assert hello_value(server, ids["mac1"]) == 20
    server["admin"].post("/api/console/settings", json={"min_free_gb": 40})
    assert hello_value(server, ids["mac1"]) == 40


def test_a_corrupt_stored_value_reaches_the_agent_as_the_default_not_zero(server, caplog):
    ids = setup_classroom(server)
    from server.db import set_setting
    set_setting(server["ctx"].conn, "min_free_gb", "lots")
    with caplog.at_level("WARNING"):
        assert hello_value(server, ids["mac1"]) == 20
    assert "min_free_gb" in caplog.text


# --- the console's numbers ---------------------------------------------------


def library_image(manifest: dict, **fields) -> dict:
    """`win11()` as the library accepts it (#1407): a whole `source_disk_bytes`
    (the captured disk, here exactly the layout), a valid `img_` + 6-hex id, and
    the MSR with `fs: ""` as the lab's captures carry it (2cc68464)."""
    parts = [{**p, "fs": ""} if p["role"] == "msr" else p for p in manifest["partitions"]]
    return {**manifest, "partitions": parts,
            "source_disk_bytes": manifest["min_target_bytes"], **fields}


TIGHT = library_image(win11(), id="img_7e1600", name="tight", family=256)


def test_fits_a_disk_from_includes_the_minimum(server, images_root):
    write_image(images_root, TIGHT)
    row = next(i for i in server["admin"].get("/api/console/images").json() if i["id"] == "img_7e1600")
    assert row["layout_bytes"] == required_bytes(TIGHT)
    assert row["min_target_bytes"] == required_bytes(TIGHT) + 12 * GIB
    assert row["min_free_gb"] == 20
    server["admin"].post("/api/console/settings", json={"min_free_gb": 0})
    row = next(i for i in server["admin"].get("/api/console/images").json() if i["id"] == "img_7e1600")
    assert row["min_target_bytes"] == required_bytes(TIGHT)


def test_an_unmeasured_c_has_no_smallest_disk(server, images_root):
    write_image(images_root, library_image(win11(used=None), id="img_00ee00", name="n", family=256))
    row = next(i for i in server["admin"].get("/api/console/images").json() if i["id"] == "img_00ee00")
    assert row["min_target_bytes"] is None
    assert row["layout_bytes"] is not None


# --- the cloning room: refused before a wave opens ---------------------------


def test_the_room_refuses_an_image_that_fits_by_size_but_leaves_too_little(fit_server, tmp_path):
    """C: 225 GiB with 220 GiB used fits a 256 GB drawer by layout (~242.6 GB),
    and leaves 5 GiB + the ~12.6 GiB the drawer adds: short of 20. Refused
    before a wave opens, with the reason named; 0 in the console lets it by."""
    from server.library import minfree   # lazy: the negative control removes the module
    image = library_image(win11(size=225 * GIB, used=220 * GIB), id="img_7e1602", name="tight2")
    need = required_bytes(image)
    assert need < DISK_256 < minfree.min_disk_bytes(image, 20)
    lib = fit_server["admin"].app.state.ctx.library
    write_image(lib.root, image)
    hello_with_disks(fit_server["anon"], CLONER1, [DISK_500, DISK_500])
    hello_with_disks(fit_server["anon"], CLONER2, [DISK_500, DISK_256])
    r = open_round(fit_server["admin"], "img_7e1602")
    assert r.status_code == 409
    want = -(-minfree.min_disk_bytes(image, 20) // 10**9)
    assert r.json()["detail"] == (f"דיסק 2 במחשב 2 הוא 256GB, האימג' צריך {want}GB"
                                  " (כולל 20GB פנויים במחיצת המערכת)")
    fit_server["admin"].post("/api/console/settings", json={"min_free_gb": 0})
    assert open_round(fit_server["admin"], "img_7e1602").status_code == 200
