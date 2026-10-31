from __future__ import annotations

import hashlib
import json

import server.library.library_scrub as library_scrub
from server.library.images import ImageLibrary
from server.library.library_scrub import scrub_library
from conftest import ESP_GUID, LINUX_GUID, SWAP_GUID


def _write_image(root, image_id="img_abc123"):
    folder = root / image_id
    folder.mkdir()
    contents = {"p1.esp.pcl.zst": b"esp", "p2.root.pcl.zst": b"root"}
    # ‏role/type_guid/fs מהקבוצות הקבועות, ו-min_target_bytes שמחזיק את
    # הפריסה — אחרת `_validate` דוחה את המניפסט מאז #1407 והספרייה ריקה.
    kinds = [("esp", ESP_GUID, "vfat"), ("linux", LINUX_GUID, "ext4")]
    partitions = [
        {"index": index, "type_guid": guid, "role": role, "fs": fs,
         "start_sector": index * 2048,
         "size_bytes": 1024, "file": name,
         "sha256": hashlib.sha256(data).hexdigest()}
        for index, ((name, data), (role, guid, fs))
        in enumerate(zip(contents.items(), kinds), 1)
    ]
    partitions.append({"index": 3, "type_guid": SWAP_GUID, "role": "swap",
                       "fs": "swap", "start_sector": 6144,
                       "size_bytes": 1024, "file": None, "sha256": None})
    manifest = {
        "schema": 1,
        "id": image_id,
        "name": "Test image",
        "family": 256,
        "min_target_bytes": 4 * 1024 * 1024,
        "partitions": partitions,
    }
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for name, data in contents.items():
        (folder / name).write_bytes(data)
    return folder


def _image(result):
    return result["images"][0]


def test_healthy_image_is_intact_and_swap_is_skipped(tmp_path):
    # Catches hashing failures and a mutation that treats swap as a stored file.
    _write_image(tmp_path)
    result = scrub_library(ImageLibrary(tmp_path))
    image = _image(result)
    assert result["intact"] is True
    assert image["state"] == "intact"
    assert [item["state"] for item in image["files"]] == ["ok", "ok"]
    assert [item["index"] for item in image["files"]] == [1, 2]


def test_flipped_byte_is_mismatch_and_image_drift(tmp_path):
    # Negative control: catches a mutation that always reports files/images intact.
    folder = _write_image(tmp_path)
    (folder / "p1.esp.pcl.zst").write_bytes(b"Esp")
    result = scrub_library(ImageLibrary(tmp_path))
    image = _image(result)
    assert result["intact"] is False
    assert image["state"] == "drift"
    assert image["files"][0]["state"] == "mismatch"


def test_absent_manifest_file_is_missing_not_mismatch(tmp_path):
    # Catches a mutation that collapses absence into checksum mismatch.
    folder = _write_image(tmp_path)
    (folder / "p2.root.pcl.zst").unlink()
    image = _image(scrub_library(ImageLibrary(tmp_path)))
    missing = image["files"][1]
    assert image["state"] == "drift"
    assert missing["state"] == "missing"
    assert "actual_sha256" not in missing


def test_hash_io_failure_is_unreadable_not_mismatch(tmp_path, monkeypatch):
    # Catches a mutation that collapses a failed check into mismatch or success.
    _write_image(tmp_path)

    def cannot_read(path):
        raise OSError("simulated read failure")

    monkeypatch.setattr(library_scrub, "_sha256", cannot_read)
    image = _image(scrub_library(ImageLibrary(tmp_path)))
    assert image["state"] == "drift"
    assert [item["state"] for item in image["files"]] == ["unreadable", "unreadable"]


def test_console_scrub_supports_one_or_all_and_is_admin_only(server):
    all_images = server["admin"].post("/api/console/images/scrub")
    assert all_images.status_code == 200
    assert len(all_images.json()["images"]) == 2

    one = server["admin"].post("/api/console/images/img_7f3a91/scrub")
    assert one.status_code == 200
    assert [image["id"] for image in one.json()["images"]] == ["img_7f3a91"]
    assert server["deploy"].post("/api/console/images/scrub").status_code == 403
