"""Regression coverage for non-finite numbers in manifest extension fields."""

import json

import pytest

from conftest import MANIFEST_256, MANIFEST_500
from server.core.tasks import TOKEN_HEADER
from test_capture import make_task, manifest_for, setup_build_machine, task_token
from test_direct_server import _joined_then, _live_manifest
from test_server_room import room_server  # noqa: F401


def with_number(manifest, number):
    # Preserve the wire spelling of the exponent, which float() would lose.
    text = json.dumps({**manifest, "extension": ["NUMBER"]})
    return text.replace('"NUMBER"', number).encode()


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_capture_rejects_nonfinite_manifest(server, number):
    task = make_task(server, setup_build_machine(server)).json()
    task_id = task["id"]
    response = server["anon"].put(
        f"/api/v1/capture/{task_id}/manifest",
        headers={TOKEN_HEADER: task_token(server, task_id)},
        content=with_number(manifest_for(), number))
    assert response.status_code == 400
    assert response.json()["detail"] == "manifest is not valid JSON"
    assert server["ctx"].library.get(task["image_id"]) is None


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_direct_rejects_nonfinite_manifest(room_server, number):
    opened, conn, detail = _joined_then(room_server, raw=with_number(_live_manifest(), number))
    assert detail == "manifest is not valid JSON"
    row = conn.execute("SELECT live_manifest_json FROM room_rounds WHERE source_task_id = ?",
                       (opened["task_id"],)).fetchone()
    assert row["live_manifest_json"] is None


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_library_reports_nonfinite_manifest(server, images_root, number):
    image_id = MANIFEST_256["id"]
    path = images_root / image_id / "manifest.json"
    path.write_bytes(with_number(json.loads(path.read_text(encoding="utf-8")), number))
    library = server["ctx"].library
    reports = []
    library.bind_invalid_reporter(lambda path, reason: reports.append((path, reason)))
    found = library.scan()
    assert image_id not in found
    assert MANIFEST_500["id"] in found
    assert len(reports) == 1 and reports[0][0] == path and "non-finite" in reports[0][1]
    detail = server["admin"].get(f"/api/console/images/{image_id}")
    assert detail.status_code == 404 and detail.json()["detail"]
    served = server["anon"].get(f"/api/v1/images/{image_id}/manifest")
    assert served.status_code == 404 and served.json()["code"] == "no_image"
