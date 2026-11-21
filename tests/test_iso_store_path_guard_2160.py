"""#2160: מזהה ISO אינו יכול להוציא את הגישה מספריית ה-ISO -- לא ב-`..`, ולא בקישור סימלי."""
from __future__ import annotations

import os

import pytest

from server.library import isos

IID = "iso_a1b2c3"


def _store(tmp_path):
    store = isos.IsoStore(tmp_path / "library")
    temp = tmp_path / "upload.tmp"
    temp.write_bytes(b"x" * 10)
    meta = store.admit(temp, name="good", folder="", filename="a.iso", size=10, sha256="0" * 64, user="t")
    return store, meta["id"]


def _link(link, target):
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"no symlink support here: {exc}")


def test_a_valid_id_resolves_and_deletes(tmp_path):
    store, iso_id = _store(tmp_path)
    assert store.get(iso_id)["present"] is True
    assert store.delete(iso_id)["id"] == iso_id
    assert store.get(iso_id) is None


@pytest.mark.parametrize("bad", ["../library", f"../{IID}", f"{IID}/..", f"{IID}/../{IID}", "..", "", "/etc",
                                 f"{IID}\\..", None, 7])
def test_traversal_ids_are_refused(tmp_path, bad):
    store, _ = _store(tmp_path)
    assert store.get(bad) is None
    with pytest.raises(isos.IsoError) as err:
        store.delete(bad)
    assert err.value.status == 404


def test_a_symlinked_folder_pointing_outside_is_refused_and_survives_delete(tmp_path):
    store, _ = _store(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / isos.META).write_text(
        f'{{"id": "{IID}", "name": "evil", "size": 1, "sha256": "x"}}', encoding="utf-8")
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    _link(store.dir / IID, outside)
    assert store.get(IID) is None
    with pytest.raises(isos.IsoError):
        store.delete(IID)
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_a_symlinked_folder_pointing_at_another_iso_is_refused(tmp_path):
    store, real = _store(tmp_path)
    _link(store.dir / IID, store.dir / real)
    assert store.get(IID) is None
    with pytest.raises(isos.IsoError):
        store.delete(IID)
    assert store.get(real)["present"] is True
