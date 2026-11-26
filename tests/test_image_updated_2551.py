"""#2551 -- the images table says when an image was last UPDATED (nadav 06/11, mockup m2551-v2 22/11).

"Updated" is the newest version of the image's lineage, not the capture date of the row's own
version, and it says whether that version came from a VM update. No "stale" mark, no threshold.
"""

from __future__ import annotations

from conftest import MANIFEST_256, write_image

from server.library.images import ImageLibrary


def _library(root, *, v2_origin="vm"):
    write_image(root, {**MANIFEST_256, "id": "img_a10001", "name": "Office",
                       "created": "2026-03-03T08:00:00Z"})
    v2 = {**MANIFEST_256, "id": "img_a10002", "name": "Office", "version": 2,
          "lineage": "img_a10001", "previous_id": "img_a10001", "created": "2026-11-10T09:30:00Z"}
    if v2_origin:
        v2["origin"] = v2_origin
    write_image(root, v2)
    write_image(root, {**MANIFEST_256, "id": "img_b20001", "name": "Plain",
                       "created": "2026-10-20T07:00:00Z"})
    return ImageLibrary(root)


def test_updated_is_the_newest_version_of_the_lineage_and_says_vm(tmp_path):
    shown = {i["id"]: i for i in _library(tmp_path).public_list()}
    for iid in ("img_a10001", "img_a10002"):          # the old version's row too: same lineage
        assert (shown[iid]["updated"], shown[iid]["updated_origin"]) == ("2026-11-10T09:30:00Z", "vm")
    assert shown["img_a10001"]["created"] == "2026-03-03T08:00:00Z"   # capture date stays as it was


def test_a_captured_image_has_its_own_date_and_is_not_marked_vm(tmp_path):
    shown = {i["id"]: i for i in _library(tmp_path).public_list()}
    assert (shown["img_b20001"]["updated"], shown["img_b20001"]["updated_origin"]) == \
        ("2026-10-20T07:00:00Z", "capture")


def test_a_newest_version_that_is_not_from_a_vm_is_not_marked_vm(tmp_path):
    shown = {i["id"]: i for i in _library(tmp_path, v2_origin=None).public_list()}
    assert shown["img_a10001"]["updated_origin"] == "capture"
