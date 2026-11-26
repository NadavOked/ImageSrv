"""#3284 -- the console shows the open copies of the waiting-screen write check and lets an admin release one.

Both sides: an open copy is listed (serial, when, stale or not, which machine) and only an admin sees or
releases it; a release is one explicit request, leaves the other copies alone, frees the serial for a new
copy, and is journaled with the admin's name; nothing is ever released by looking, by time, or by a
second listing; and an unreadable record is "could not read", never "no copies" (principle 5).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.console import write_copies
from server.disks.write_backup import _dir
from test_server_room import CLONER1, room_server  # noqa: F401 -- fixture

URL = "/api/console/write-copies"
STATIC = Path(__file__).resolve().parent.parent / "server" / "static"


def _copy(ctx, serial: str, bid: str, *, stale: int = 0, mac: str = CLONER1, created: int | None = None) -> str:
    folder = _dir(ctx.data_dir, serial)
    folder.mkdir(parents=True, exist_ok=True)
    meta = {"id": bid, "serial": serial, "size": 1, "regions": "0:4096", "sha256": "0" * 64, "mac": mac,
            "created": created if created is not None else int(time.time())}
    if stale:
        meta["stale"] = {"blocks": stale, "at": 1, "mac": mac}
    (folder / f"{bid}.bin").write_bytes(b"x" * 8192)
    (folder / f"{bid}.json").write_text(json.dumps(meta), encoding="utf-8")
    return folder.name


A, B = "a" * 32, "b" * 32


def test_open_copies_are_listed_with_serial_time_state_and_machine(room_server):
    ctx, admin = room_server["ctx"], room_server["admin"]
    key = _copy(ctx, "WD-1", A, stale=3, created=1000)
    _copy(ctx, "S4-2", B, created=2000)
    got = admin.get(URL).json()
    assert got["unreadable"] == []
    by = {c["serial"]: c for c in got["copies"]}
    assert set(by) == {"WD-1", "S4-2"}
    assert by["WD-1"]["stale"] == {"blocks": 3, "at": 1} and by["S4-2"]["stale"] is None
    assert by["WD-1"]["key"] == key and by["WD-1"]["created"] == 1000 and by["WD-1"]["bytes"] == 8192
    assert by["WD-1"]["machine"] and CLONER1 not in by["WD-1"]["machine"]   # a name, not the MAC
    assert [c["serial"] for c in got["copies"]] == ["S4-2", "WD-1"]       # newest first


def test_no_copies_is_an_empty_list_and_an_unreadable_record_is_not(room_server):
    ctx, admin = room_server["ctx"], room_server["admin"]
    assert admin.get(URL).json() == {"copies": [], "unreadable": []}
    key = _copy(ctx, "WD-1", A)
    (_dir(ctx.data_dir, "WD-1") / f"{A}.json").write_text("{not json", encoding="utf-8")
    got = admin.get(URL).json()
    assert got["copies"] == [] and [u["key"] for u in got["unreadable"]] == [key]   # said, not hidden


def test_a_directory_read_failure_is_reported_without_exposing_details(room_server, monkeypatch, caplog):
    message = "permission denied for /internal/write-backups"

    def fail_to_list(_root, _names):
        raise OSError(message)

    monkeypatch.setattr(write_copies, "open_copies", fail_to_list)
    r = room_server["admin"].get(URL)
    assert r.status_code == 500, r.text
    assert message not in r.json()["detail"]
    assert r.json()["detail"] == write_copies.UNREADABLE_HE
    assert message in caplog.text


def test_only_an_admin_sees_or_releases(room_server):
    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    key = _copy(ctx, "WD-1", A)
    for call in (lambda c: c.get(URL), lambda c: c.delete(f"{URL}/{key}/{A}")):
        assert call(deploy).status_code == 403
        assert call(anon).status_code in (401, 403)
    assert (_dir(ctx.data_dir, "WD-1") / f"{A}.json").is_file()   # nothing was touched


def test_release_removes_only_that_copy_frees_the_serial_and_is_journaled_with_the_admin(room_server):
    ctx, admin = room_server["ctx"], room_server["admin"]
    key = _copy(ctx, "WD-1", A, stale=2)
    _copy(ctx, "S4-2", B)
    r = admin.delete(f"{URL}/{key}/{A}")
    assert r.status_code == 200 and r.json() == {"ok": True}, r.text
    folder = _dir(ctx.data_dir, "WD-1")
    assert not (folder / f"{A}.json").exists() and not (folder / f"{A}.bin").exists()
    assert [c["serial"] for c in admin.get(URL).json()["copies"]] == ["S4-2"]   # the other one stayed
    from server.disks.write_backup import open_for   # noqa: PLC0415
    assert open_for(ctx.data_dir, "WD-1") == [] and len(open_for(ctx.data_dir, "S4-2")) == 1   # a new copy for WD-1 is allowed again
    row = ctx.conn.execute("SELECT user, detail, machine FROM journal WHERE event = 'write_backup_admin_released'").fetchone()
    assert row and row["user"] == "noc" and "serial=WD-1" in row["detail"] and f"id={A}" in row["detail"] and "stale=yes" in row["detail"]
    assert row["machine"] == CLONER1
    assert admin.delete(f"{URL}/{key}/{A}").status_code == 404   # a second click is "already released", not a crash


def test_nothing_is_released_by_looking_or_by_age(room_server):
    ctx, admin = room_server["ctx"], room_server["admin"]
    _copy(ctx, "OLD", A, stale=1, created=1)   # 1970
    for _ in range(3):
        assert [c["serial"] for c in admin.get(URL).json()["copies"]] == ["OLD"]
    assert ctx.conn.execute("SELECT COUNT(*) FROM journal WHERE event = 'write_backup_admin_released'").fetchone()[0] == 0


@pytest.mark.parametrize("key,bid", [("zz", A), ("abc", A), ("", A), ("5744", "../x"), ("5744", "short"), ("5744", "A" * 32)])
def test_a_bad_key_or_id_is_refused_before_touching_anything(room_server, key, bid):
    r = room_server["admin"].delete(f"{URL}/{key or '-'}/{bid}")
    assert r.status_code in (400, 404)


def test_the_console_has_the_tab_and_a_red_sheet_without_typed_words():
    js = (STATIC / "disks.js").read_text(encoding="utf-8")
    assert "עותקי בדיקת כתיבה" in js and "function dkCopiesTab" in js and 'api("/write-copies")' in js
    release = js[js.index("function dkCopyRelease"):]
    release = release[:release.index("\n}\n")]
    assert "danger: true" in release and 'submitLabel: "שחרר עותק"' in release
    assert "fields" not in release and "confirm_name" not in release   # no typed confirmation (Nadav 10/10)
    assert not re.search(r"\b(prompt|confirm)\(", js)
