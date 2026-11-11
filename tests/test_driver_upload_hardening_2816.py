"""#2816: upload reserve, worker writes, and expanded archive sizes."""

import copy
import gzip
import hashlib
import inspect
import io
import tarfile
import threading

import pytest
from fastapi import Request

from server.console import drivers as console_drivers, library as console_library
from server.library import archive
from server.library.drivers import DriverLibrary
from server.storage import disk_reserve
from server.system import inflight

from conftest import MANIFEST_256
from test_drivers import _upload, build_tar, manifest_for

pytestmark = pytest.mark.system_reserve


def _empty_drivers(server):
    library = server["ctx"].drivers
    assert library.scan() == {}
    assert list(library.root.iterdir()) == []
    assert "העלאת דרייבר" not in inflight.running()


def _ample_space(monkeypatch):
    monkeypatch.setattr(console_library, "_library_free",
                        lambda root: disk_reserve.SYSTEM_RESERVE_BYTES + 1024 ** 3)


def test_driver_upload_disk_full_stream_cleans_up(server, monkeypatch):
    monkeypatch.setattr(console_library, "_library_free", lambda root: 1)
    monkeypatch.setattr(disk_reserve, "STREAM_CHECK_EVERY", 1)
    data = build_tar(manifest_for())
    response = _upload(server["admin"], iter([data]))
    assert response.status_code == 507, response.text
    _empty_drivers(server)


def test_driver_upload_content_length_refused_before_tempfile(server, monkeypatch):
    monkeypatch.setattr(console_library, "_library_free", lambda root: 1)
    created = []
    original = console_drivers.tempfile.NamedTemporaryFile

    def record_tempfile(*args, **kwargs):
        created.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(console_drivers.tempfile, "NamedTemporaryFile", record_tempfile)
    response = _upload(server["admin"], build_tar(manifest_for()))
    assert created == [], "Content-Length must be checked before creating a temp file"
    assert response.status_code == 507, response.text
    _empty_drivers(server)


def test_driver_upload_ample_space_succeeds(server, monkeypatch):
    _ample_space(monkeypatch)
    response = _upload(server["admin"], build_tar(manifest_for()))
    assert response.status_code == 200, response.text
    assert response.json() == {"name": "intel-nic", "files": 3}
    assert [p.name for p in server["ctx"].drivers.root.iterdir()] == ["intel-nic"]


def test_driver_upload_writes_off_event_loop(server, monkeypatch):
    _ample_space(monkeypatch)
    loop_threads, write_threads = [], []
    original_stream = Request.stream
    original_write = console_library._write_chunk

    async def stream(request):
        loop_threads.append(threading.get_ident())
        async for chunk in original_stream(request):
            yield chunk

    def write(handle, guard, chunk):
        write_threads.append(threading.get_ident())
        return original_write(handle, guard, chunk)

    monkeypatch.setattr(Request, "stream", stream)
    # On the old route this hook is unused: fail on behavior, not a missing name.
    monkeypatch.setattr(console_drivers, "_write_chunk", write, raising=False)
    response = _upload(server["admin"], build_tar(manifest_for()))
    assert response.status_code == 200, response.text
    assert loop_threads and write_threads, "the upload must use the worker write helper"
    assert set(write_threads).isdisjoint(loop_threads)


def test_driver_upload_holds_inflight_until_finished(server, monkeypatch):
    _ample_space(monkeypatch)
    held = []
    original_write = console_library._write_chunk

    def write(handle, guard, chunk):
        held.append(inflight.running())
        return original_write(handle, guard, chunk)

    monkeypatch.setattr(console_drivers, "_write_chunk", write, raising=False)
    response = _upload(server["admin"], build_tar(manifest_for()))
    assert response.status_code == 200, response.text
    assert held and all("העלאת דרייבר" in active for active in held)
    assert "העלאת דרייבר" not in inflight.running()


def test_driver_upload_actual_size_checked_before_import(server, monkeypatch):
    data = build_tar(manifest_for())
    free = iter([disk_reserve.SYSTEM_RESERVE_BYTES + 2 * len(data),
                 disk_reserve.SYSTEM_RESERVE_BYTES + len(data) - 1])
    monkeypatch.setattr(console_library, "_library_free", lambda root: next(free))
    imported = []
    original_import = DriverLibrary.import_tar

    def record_import(library, *args, **kwargs):
        imported.append(args)
        return original_import(library, *args, **kwargs)

    monkeypatch.setattr(DriverLibrary, "import_tar", record_import)
    response = _upload(server["admin"], data)
    assert response.status_code == 507, response.text
    assert imported == []
    _empty_drivers(server)


@pytest.fixture(params=["image", "driver"])
def expanded_tar(request):
    payload = bytes(20 * 1024 * 1024)
    if request.param == "driver":
        files = {"large.sys": payload}
        manifest = manifest_for(files=files)
        folder = manifest["name"]
    else:
        manifest = copy.deepcopy(MANIFEST_256)
        manifest["id"] = "img_2816ab"
        files = {}
        for part in manifest["partitions"]:
            data = payload if not files else b"partition"
            files[part["file"]] = data
            part["sha256"] = hashlib.sha256(data).hexdigest()
        manifest["total_compressed_bytes"] = sum(map(len, files.values()))
        folder = manifest["id"]
    data = gzip.compress(build_tar(manifest, files=files, folder=folder))
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
        total = sum(member.size for member in tar.getmembers())
    assert total >= 20 * 1024 * 1024
    assert len(data) < 100 * 1024
    return request.param, data, total


@pytest.mark.parametrize("refuse", [False, True], ids=["allow", "refuse"])
def test_import_checks_expanded_size_before_extract(tmp_path, expanded_tar, refuse):
    kind, data, total = expanded_tar
    source = tmp_path / "package.tar.gz"
    source.write_bytes(data)
    root = tmp_path / "library"
    root.mkdir()
    importer = archive.import_tar if kind == "image" else DriverLibrary(root).import_tar
    args = (source, root, set()) if kind == "image" else (source,)
    observed = []

    class Refused(Exception):
        pass

    def before_extract(size):
        staging = list(root.glob(".import-*"))
        assert len(staging) == 1
        assert list(staging[0].rglob("*")) == [], "callback must precede extraction"
        observed.append(size)
        if refuse:
            raise Refused("expanded size refused")

    # Exercise old importers too, so missing support fails an assertion, not TypeError.
    kwargs = {"before_extract": before_extract} if (
        "before_extract" in inspect.signature(importer).parameters) else {}
    rejected = False
    try:
        importer(*args, **kwargs)
    except Refused:
        rejected = True
    assert observed == [total], "importer must check the total before extracting"
    assert rejected == refuse
    if refuse:
        assert list(root.iterdir()) == []
    else:
        assert len(list(root.glob("*/manifest.json"))) == 1
        assert not list(root.glob(".import-*"))


def test_upload_refuses_expanded_archive(server, monkeypatch, expanded_tar):
    kind, data, total = expanded_tar
    free = disk_reserve.SYSTEM_RESERVE_BYTES + 5 * 1024 * 1024
    assert disk_reserve.refusal(free, 2 * len(data), "upload") is None
    assert disk_reserve.refusal(free, len(data), "archive") is None
    assert disk_reserve.refusal(free, total, "expanded") is not None
    monkeypatch.setattr(console_library, "_library_free", lambda root: free)
    extracted = []
    original_extract = tarfile.TarFile.extractall

    def extract(tar, *args, **kwargs):
        extracted.append(args)
        return original_extract(tar, *args, **kwargs)

    monkeypatch.setattr(tarfile.TarFile, "extractall", extract)
    library = server["ctx"].library if kind == "image" else server["ctx"].drivers
    before = set(library.root.iterdir()) if library.root.exists() else set()
    response = server["admin"].post(
        f"/api/console/{'images' if kind == 'image' else 'drivers'}/upload", content=data,
        headers={"Content-Type": "application/x-tar"})
    assert response.status_code == 507, response.text
    assert extracted == []
    assert set(library.root.iterdir()) == before
    if kind == "driver":
        _empty_drivers(server)
