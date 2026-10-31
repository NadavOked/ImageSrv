"""‏#1813 — אימג' נכנס לספרייה רק אחרי שהבייטים שלו על הדיסק.

הסדר שנבדק בכל אחד משלושת המסלולים (קליטה, ייבוא tar/העלאה, משיכה ממשני):
‏fsync לכל קובץ מחיצה → ‏fsync לתיקייה → הפרסום (rename/replace) → ‏fsync
לתיקייה שמחזיקה את השם החדש. נפילת חשמל לא תשאיר מניפסט תקין מעל קבצים
באורך 0.

הקריאות נרשמות ביומן אירועים אחד (‏fsync_file/fsync_dir בכל מודול, ו-rename/
‏replace של ‏pathlib) — ומה שנבדק הוא **הסדר**, לא רק שהן קרו.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from conftest import MANIFEST_256, write_image
from server.library import archive, capture
from server.interserver import storage_transfer
from server.library.archive import import_tar, tar_stream


@pytest.fixture()
def events(monkeypatch):
    log: list[tuple[str, str]] = []

    def rec(kind):
        return lambda path: log.append((kind, os.path.normcase(os.path.abspath(path))))

    for module in (archive, capture, storage_transfer):
        monkeypatch.setattr(module, "fsync_file", rec("fsync_file"), raising=False)
        monkeypatch.setattr(module, "fsync_dir", rec("fsync_dir"), raising=False)
    real_rename, real_replace = Path.rename, Path.replace

    def rename(self, target):
        log.append(("publish", os.path.normcase(os.path.abspath(target))))
        return real_rename(self, target)

    def replace(self, target):
        log.append(("publish", os.path.normcase(os.path.abspath(target))))
        return real_replace(self, target)

    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr(Path, "replace", replace)
    return log


def _n(path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _index(log, kind, path) -> int:
    hits = [i for i, e in enumerate(log) if e == (kind, _n(path))]
    assert hits, f"{kind} {path} never happened: {log}"
    return hits[-1]


def _publish_of(log, path) -> int:
    return _index(log, "publish", path)


# --- ייבוא tar (העלאה מהקונסולה, קליטה במשני) ----------------------------------

def test_an_imported_archive_is_on_disk_before_its_manifest_is_named(tmp_path, events):
    source = tmp_path / "source"
    write_image(source, MANIFEST_256)
    tar = tmp_path / "upload.tar"
    tar.write_bytes(b"".join(tar_stream(source / MANIFEST_256["id"], MANIFEST_256["id"])))
    root = tmp_path / "images"
    root.mkdir()
    events.clear()

    import_tar(tar, root, set())

    folder = root / MANIFEST_256["id"]
    publish = _publish_of(events, folder / "manifest.json")
    for part in MANIFEST_256["partitions"]:
        if part.get("file"):
            assert _index(events, "fsync_file", folder / part["file"]) < publish
    assert _index(events, "fsync_dir", folder) > publish        # השם החדש מחזיק
    assert any(e == ("fsync_dir", _n(folder)) for e in events[:publish])
    assert _index(events, "fsync_dir", root) > publish


# --- קליטה ממחשב הבנייה --------------------------------------------------------

def test_a_capture_is_on_disk_before_it_enters_the_library(server, images_root, events):
    from test_capture import do_capture, make_task, setup_build_machine

    from server.core.tasks import staging_dir

    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    staging = staging_dir(images_root, created["id"])
    response = do_capture(server, created["id"])
    assert response.status_code == 202
    target = images_root / created["image_id"]
    assert (target / "manifest.json").is_file()

    publish = _publish_of(events, target)
    for name in ("p1.esp.pcl.zst", "p3.windows.pcl.zst", "manifest.json"):
        assert _index(events, "fsync_file", staging / name) < publish
    assert _index(events, "fsync_dir", staging) < publish
    assert _index(events, "fsync_dir", images_root) > publish


# --- משיכה ממשני ----------------------------------------------------------------

def test_a_pulled_image_is_on_disk_before_it_enters_the_library(server, events, request):
    pytest.importorskip("OpenSSL")
    from server.interserver import interserver_auth
    if not interserver_auth.can_export_keying_material():
        pytest.skip("אין tls-exporter (pyOpenSSL) — המשיכה אינה ניתנת להרצה כאן")
    from test_storage_pull import PULL, PULL_ID, _incoming, _wait

    secondary = request.getfixturevalue("secondary")
    paired = request.getfixturevalue("paired")
    write_image(secondary["root"], PULL)
    resp = server["admin"].post(f"/api/console/storage-nodes/{paired}/pull",
                                json={"image_id": PULL_ID})
    assert resp.status_code == 200, resp.text
    assert _wait(server["admin"], paired, resp.json()["id"])["state"] == "done"

    root = Path(server["ctx"].library.root)
    incoming = _incoming(server) / PULL_ID
    publish = _publish_of(events, root / PULL_ID)
    for part in PULL["partitions"]:
        if part.get("file"):
            assert _index(events, "fsync_file", incoming / part["file"]) < publish
    assert _index(events, "fsync_dir", incoming) < publish
    assert _index(events, "fsync_dir", root) > publish


from test_storage_pull import paired, secondary  # noqa: E402,F401 — fixtures
