"""#1407 -- the second belt: a partition file is read only from inside its image folder.

``test_server_core.py`` pins the first belt (``ImageLibrary._validate`` drops a
manifest whose ``file`` is not one plain name). The Issue's fix has a second one,
``guarded_image_path``, which every reader goes through (the sender via
``ImageLibrary.file_path``, vmdisk, vmupdate, readback, library scrub, interserver).
It must hold on its own -- a reader that some day takes a name from somewhere other
than a validated manifest is exactly the case it exists for.

Negative control (mutation, both fix sites at once): ``safe_partition_filename``
accepting any non-empty string, and ``guarded_image_path`` reduced to
``Path(dir) / name`` if it is a file -- the shape of the code before 94552eb6.
"""

from __future__ import annotations

import json
import os

import pytest

from server.library.images import ImageLibrary, guarded_image_path

SECRET = b"root:x:0:0\n"


@pytest.fixture
def layout(tmp_path):
    folder = tmp_path / "lib" / "img_7f3a91"
    folder.mkdir(parents=True)
    (folder / "p1.esp.pcl.zst").write_bytes(b"data")
    outside = tmp_path / "shadow"
    outside.write_bytes(SECRET)
    return folder, outside


@pytest.mark.parametrize("name", ["../../shadow", "sub/../../../shadow"])
def test_a_name_that_leaves_the_folder_is_never_resolved(layout, name):
    folder, _outside = layout
    (folder / "sub").mkdir()
    assert guarded_image_path(folder, name) is None


def test_an_absolute_name_is_never_resolved(layout):
    folder, outside = layout
    assert guarded_image_path(folder, str(outside)) is None


@pytest.mark.skipif(os.name != "posix", reason="symlinks need privilege on Windows; the lab runs it")
def test_a_symlink_in_the_folder_pointing_out_is_never_resolved(layout):
    folder, outside = layout
    (folder / "p2.windows.pcl.zst").symlink_to(outside)
    assert guarded_image_path(folder, "p2.windows.pcl.zst") is None


def test_a_plain_file_in_the_folder_is_served(layout):
    """Guard (passes in both states): the belt does not refuse the normal case."""
    folder, _outside = layout
    assert guarded_image_path(folder, "p1.esp.pcl.zst") == (folder / "p1.esp.pcl.zst").resolve()


def test_the_library_serves_nothing_from_a_traversal_manifest(tmp_path):
    """End to end through the reader the sender uses: a manifest on a storage
    location that names ``../../shadow`` serves no path at all."""
    from test_server_core import MANIFEST_256, write_image

    manifest = json.loads(json.dumps(MANIFEST_256))
    manifest["partitions"][0]["file"] = "../../shadow"
    root = tmp_path / "lib"
    write_image(root, manifest)              # its partition bytes land at tmp_path/shadow
    assert (tmp_path / "shadow").is_file()   # the target exists: a refusal is not "no such file"
    library = ImageLibrary(root)
    assert library.get(manifest["id"]) is None
    assert library.file_path(manifest["id"], "../../shadow") is None
