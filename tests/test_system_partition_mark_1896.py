"""‏#1896 — הכרעת נדב (22/10): סימון מחיצת המערכת (C:) באימג' ישן.

אימג' שנקלט לפני #1875 ובו שתי מחיצות windows בגודל שווה: אין סימון
`"system": true` מהקליטה, ואי אפשר להבחין ביניהן — min-free "לא נבדק" בכל
דיסק, והאימג' לא מוצע לשום מחשב. עכשיו:

* **סימון בשלושה מקומות:** הקונסולה (`POST /api/console/images/<id>/system-partition`),
  מסך מחשב הבנייה (אותו נתיב, בכניסה שלו) ומסך המשכפל
  (`POST /api/v1/agent/cloner-round/system-partition`, בשער של פתיחת סבב מהמסך).
* **נשמר באימג'** — `"system": true` ו-`"system_source": "operator"` ב-manifest.json,
  **בכתיבה אטומית** (`write_atomic`, ‏#1908). הסוכן קורא אותו מהמניפסט שהשרת מגיש.
* **עד שמסמנים — "לא נבדק"** עם המחיצות לבחירה: ב-`/images` (`system_unmarked`,
  ‏`system_candidates`) ובמצב הסוכן (`unchecked_images`).
* ראיה מהקליטה אינה נדרסת; המשכפל מסמן רק אימג' שעוד לא סומן.
"""

from __future__ import annotations

import copy
import json
import shutil

import pytest

pytest.importorskip("fastapi")

from conftest import MANIFEST_256, write_image
from native import requires_native
from server import cloner_round, images as images_module, minfree
from test_agent import AGENT, BASH, _stub, posix, sh
from test_cloner_round_server import (URL, cloner, nonce, now)  # noqa: F401
from test_machine_identity import MAC1, identity_server  # noqa: F401

IMG = "img_189600"
WIN = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
SIZE = 120 * (1 << 30)


def twins() -> dict:
    """ESP ושתי מחיצות windows בגודל שווה, בלי סימון מהקליטה."""
    m = copy.deepcopy(MANIFEST_256)
    m.update(id=IMG, name="Twins Old", folder="ישן", min_target_bytes=None)
    esp = m["partitions"][0]
    m["partitions"] = [
        esp,
        {"index": 2, "type_guid": WIN, "role": "windows", "fs": "ntfs", "start_sector": 206848,
         "size_bytes": SIZE, "used_bytes": 40 * (1 << 30), "file": "p2.win.pcl.zst",
         "sha256": "bb" * 32, "expandable": False},
        {"index": 3, "type_guid": WIN, "role": "windows", "fs": "ntfs",
         "start_sector": 206848 + SIZE // 512, "size_bytes": SIZE, "used_bytes": 1 << 30,
         "file": "p3.win.pcl.zst", "sha256": "cc" * 32, "expandable": True},
    ]
    from server.images import required_bytes
    m["min_target_bytes"] = required_bytes(m)
    return m


def _image(srv, manifest: dict | None = None) -> None:
    write_image(srv["ctx"].library.root, manifest or twins())


def _on_disk(srv) -> dict:
    return json.loads((srv["ctx"].library.root / IMG / "manifest.json").read_text(encoding="utf-8"))


def _listed(srv) -> dict:
    return next(i for i in srv["admin"].get("/api/console/images").json() if i["id"] == IMG)


def _mark(srv, index, who="admin"):
    return srv[who].post(f"/api/console/images/{IMG}/system-partition", json={"index": index})


# --- "לא נבדק" עד שמסמנים ------------------------------------------------------


def test_an_unmarked_twin_image_is_listed_unchecked_with_both_to_choose(server):
    _image(server)
    row = _listed(server)
    assert row["system_unmarked"] is True
    assert [c["index"] for c in row["system_candidates"]] == [2, 3]
    assert row["min_free_unchecked"] and "C:" in row["min_free_unchecked"]
    assert row["min_target_bytes"] is None


def test_marking_writes_the_manifest_and_the_image_is_deployable(server):
    _image(server)
    r = _mark(server, 2)
    assert r.status_code == 200, r.text
    parts = {p["index"]: p for p in _on_disk(server)["partitions"]}
    assert parts[2].get("system") is True and parts[2].get("system_source") == "operator"
    assert "system" not in parts[3] and "system" not in parts[1]
    row = _listed(server)
    assert row["system_unmarked"] is False and row["min_free_unchecked"] is None
    assert row["min_target_bytes"] is not None
    assert minfree.system_partition(_on_disk(server))["index"] == 2
    detail = server["ctx"].conn.execute(
        "SELECT detail FROM journal WHERE event = 'image_system_partition'").fetchone()
    assert detail and f"{IMG} system=2" in detail["detail"]


def test_the_console_can_change_its_own_mark(server):
    _image(server)
    assert _mark(server, 2).status_code == 200
    assert _mark(server, 3).status_code == 200
    parts = {p["index"]: p for p in _on_disk(server)["partitions"]}
    assert parts[3].get("system") is True and "system" not in parts[2]


@pytest.mark.parametrize("index", [1, 4, "2", None, True])
def test_only_one_of_the_twins_can_be_marked(server, index):
    _image(server)
    before = _on_disk(server)
    r = _mark(server, index)
    assert r.status_code == 400, r.text
    assert _on_disk(server) == before


def test_a_capture_mark_is_evidence_and_is_not_overwritten(server):
    m = twins()
    m["partitions"][1]["system"] = True        # #1875: capture found the SYSTEM hive here
    _image(server, m)
    assert _listed(server)["system_candidates"] == []
    assert _mark(server, 3).status_code == 400
    assert "system" not in {p["index"]: p for p in _on_disk(server)["partitions"]}[3]


def test_the_build_machine_marks_first_and_only_the_admin_changes_it(server):
    """מחשב הבנייה: פורט הסוכן, ברשימת ההיתר (‏`KIOSK_CONSOLE_ROUTES`). ‏deploy
    מסמן אימג' שעוד לא סומן — כמו המשכפל; שינוי — admin בקונסולה."""
    from fastapi.testclient import TestClient
    from server.app import create_agent_app
    from test_console_rbac_matrix import _deploy_cookie
    _image(server)
    rt = server["app"].state.runtime
    kiosk = TestClient(create_agent_app(rt))
    url = f"/api/console/images/{IMG}/system-partition"
    assert kiosk.post(url, json={"index": 2}).status_code == 401          # no session, no mark
    kiosk.cookies.update(_deploy_cookie(rt))
    assert kiosk.post(url, json={"index": 3}).status_code == 200, "deploy, first mark"
    r = kiosk.post(url, json={"index": 2})
    assert r.status_code == 400 and "מהקונסולה" in r.json()["detail"], r.text
    assert {p["index"]: p for p in _on_disk(server)["partitions"]}[3].get("system") is True
    assert _mark(server, 2).status_code == 200                               # the admin changes it


def test_the_write_is_atomic(server, monkeypatch):
    """‏#1908: מניפסט חתוך הוא אימג' שבור — הכתיבה דרך `write_atomic`, ונפילה
    באמצע משאירה את הישן שלם."""
    _image(server)
    before = _on_disk(server)
    seen = []

    def boom(path, data, **kw):
        seen.append(str(path))
        raise OSError("disk full")
    monkeypatch.setattr(images_module, "write_atomic", boom)
    with pytest.raises(OSError):
        server["ctx"].library.record_system_partition(IMG, 2)
    assert seen and seen[0].endswith("manifest.json")
    assert _on_disk(server) == before


# --- מסך מחשב הבנייה והמשכפל: מצב הסוכן ----------------------------------------


def test_the_agent_state_lists_the_unchecked_image_until_it_is_marked(identity_server):
    """allowed_images של המכונה מסונן לדיסקים שלה (‏#1863) — כאן מגירות של 256GB, והאימג'
    (‏120+120GiB) נכנס ל-500: לכן מכונה עם דיסק גדול."""
    from conftest import hello_body
    srv = identity_server
    _image(srv)
    cloner(srv)
    body = hello_body(MAC1, disk_bytes=1_000_204_886_016)
    assert srv["station"].post("/api/v1/agent/hello", json=body).status_code == 200
    state = srv["station"].get(f"/api/v1/agent/state?mac={MAC1}").json()
    listed = {i["id"]: i for i in state["unchecked_images"]}
    assert [c["index"] for c in listed[IMG]["candidates"]] == [2, 3]
    assert IMG not in [i["id"] for i in state["allowed_images"]]
    assert _mark(srv, 3).status_code == 200
    state = srv["station"].get(f"/api/v1/agent/state?mac={MAC1}").json()
    assert IMG not in [i["id"] for i in state["unchecked_images"]]
    assert IMG in [i["id"] for i in state["allowed_images"]]


def _cloner_mark(srv, index, with_nonce=True):
    body = {"mac": MAC1, "image_id": IMG, "index": index}
    if with_nonce:
        body["nonce"] = nonce(srv).json()["nonce"]
    return srv["station"].post(URL + "/system-partition", json=body)


def test_the_cloner_marks_an_unmarked_image_from_its_screen(identity_server):
    srv = identity_server
    _image(srv)
    cloner(srv)
    r = _cloner_mark(srv, 2)
    assert r.status_code == 200, r.text
    assert {p["index"]: p for p in _on_disk(srv)["partitions"]}[2].get("system") is True
    # once marked: changing it is the console's
    r = _cloner_mark(srv, 3)
    assert r.status_code == 409 and "מהקונסולה" in r.json()["error"]


def test_the_cloner_route_keeps_the_screen_gate(identity_server):
    srv = identity_server
    _image(srv)
    cloner(srv)
    assert _cloner_mark(srv, 2, with_nonce=False).status_code == 403
    r = srv["stranger"].post(URL + "/system-partition",
                             json={"mac": MAC1, "image_id": IMG, "index": 2, "nonce": "0" * 32})
    assert r.status_code == 403
    assert "system" not in {p["index"]: p for p in _on_disk(srv)["partitions"]}[2]


# --- the agent reads the same mark -------------------------------------------------

needs_shell = requires_native(("bash", BASH), ("jq", shutil.which("jq")))


@needs_shell
def test_the_agent_judges_the_marked_partition(tmp_path):
    from test_system_partition_1875 import fits, system_index
    for sub in ("a", "b", "c"):
        (tmp_path / sub).mkdir()
    m = twins()
    assert system_index(tmp_path / "a", m) == "ambiguous"
    m["partitions"][2].update(system=True, system_source="operator")
    assert system_index(tmp_path / "b", m) == "3"
    rc, err = fits(tmp_path / "c", m, 500107862016)
    assert rc == "rc=0", err
    assert minfree.refusal(m, 500107862016, 20) is None
