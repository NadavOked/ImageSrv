"""#3502: האינדקס ורשימת ה-BLAKE3 נגזרים בקליטה -- לפני שהאימג' נכנס לספרייה -- ומה שמחכה נראה למפעיל.

ברזל 15/11: אימג' שנקלט לפני #3357 (`img_3fa17f`, 18.5GB דחוס) לא היה לו `.b3`, והגל הראשון מול כיתה המתין 486 שניות
לגזירה שלו. `server/library/capture.py` (`_index_at_ingest`), `ImageLibrary.record_index_staged`,
`server/agent/readbackhash.py` (`lacks_list`, `failed`, `run_forever`) ו-`server/library/indexwait.py` (שורת הבריאות, ההדפסה
בשדרוג).

מה נבדק:

- קליטה של אימג' אמיתי בפורמט 2: ה-`.idx` וה-`.b3` ו-`chunk_b3` כבר שם כשהאימג' נכנס (הרישום נקרא כשתיקיית היעד עוד לא
  קיימת), ושום מעבר ברקע לא רץ (`backfill_readback` כבוי בבדיקות).
- אינדקס שאינו מה שהקליטה חתמה עליו: דבר אינו נכתב, גם לא למחיצה התקינה, והאימג' נכנס כמו עד היום (הרקע קובע `index_error`).
- אחרי שדרוג: המעבר ברקע מתחיל **מעצמו** בעלייה (בלי `kick`) וגוזר מה שחסר.
- שורת הבריאות: אין כלי -- אדום; הגזירה נכשלה -- אדום עם הסיבה; ממתין -- צהוב עם כמה, ובערך כמה זמן; סבב פתוח על אימג' כזה
  נאמר בשמו; הכול גמור -- אין שורה.
- ההדפסה של `python3 -m server.library.indexwait`.

**בקרה שלילית:** `server/library/capture.py`, `server/library/images.py`, `server/agent/readbackhash.py`,
`server/system/health.py` ו-`tools/server-upgrade.sh` חוזרים לבסיס, ו-`server/library/indexwait.py` נמחק.
"""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

import chunk_image as CI
import chunkcast_rig as CR
from native import requires_native
from server.agent import readbackhash as RB
from server.library import chunkformat as F
from server.library.images import ImageLibrary
from server.db import connect
from test_capture import do_capture, make_task, manifest_for, setup_build_machine, sha, wait_verified
from test_chunk_format_2533 import DC, built, _image

try:
    from server.library import indexwait
except ImportError:      # the negative control: without the fix the tests run and fail on behaviour, not on an import
    indexwait = SimpleNamespace(health_checks=lambda ctx: [], main=lambda argv: -1)

#: likewise: `failed` is part of the fix
failed = getattr(RB, "failed", lambda: None)

pytestmark = requires_native("gcc", "zstd", posix=True, why="the BLAKE3 tool is chunkcast, built with gcc and libzstd")

IMAGE = "img_2533aa"
DATA_BYTES = 1025 * 4096


@pytest.fixture(scope="module")
def tool(tmp_path_factory) -> str:
    return str(CR.build_binaries(tmp_path_factory.mktemp("ingest-bin")) / "chunkcast")


@pytest.fixture()
def with_tool(tool, monkeypatch):
    monkeypatch.setattr(RB, "_B3_TOOL", [tool])
    return tool


@pytest.fixture(autouse=True)
def fixtures_derive(monkeypatch):
    """The fixture's frames are stored frames, read here with the fixture's own decoder (`DC`), as every other test
    of the derivation does; a pass or a capture asks `chunkformat.derive_index` and gets that."""
    real = F.derive_index
    monkeypatch.setattr(F, "derive_index",
                        lambda path, raw, limit, stop=None, **kw: real(path, raw, None, stop, **DC, **kw))
    monkeypatch.setattr(RB, "_FAILED", {}, raising=False)


def _on_disk(root, image_id: str) -> dict:
    return json.loads((root / image_id / "manifest.json").read_text(encoding="utf-8"))


def _capture(server, *, p3_signed: str | None = None) -> str:
    """A capture of two real image_format 2 files (the same stream twice, one per partition); `p3_signed` replaces
    the index the capture signed for p3. Returns the image id, once the verification ended `done`."""
    blob, index = built()
    manifest = manifest_for()
    for part in manifest["partitions"]:
        part["sha256"], part["used_bytes"] = sha(blob), DATA_BYTES
    manifest = CI.signed(manifest, {1: index, 3: index})
    manifest["total_compressed_bytes"] = 2 * len(blob)
    if p3_signed is not None:
        manifest["partitions"][1]["index_xxh64"] = p3_signed
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    response = do_capture(server, created["id"], manifest=manifest,
                          files={"p1.esp.pcl.zst": blob, "p3.windows.pcl.zst": blob})
    assert response.status_code == 202, response.text
    assert wait_verified(server, created["id"]) == "done"
    return created["image_id"]


# --- הקליטה ---------------------------------------------------------------------------------------------


def test_a_capture_enters_the_library_with_its_index_and_blake3_list_already_there(server, images_root, with_tool,
                                                                                    monkeypatch):
    seen = []
    real = getattr(ImageLibrary, "record_index_staged", None)

    def watch(self, folder, values):
        seen.append(set(ImageLibrary(images_root).scan()))   # the library as it is while the index is recorded
        return real(self, folder, values)

    monkeypatch.setattr(ImageLibrary, "record_index_staged", watch, raising=False)
    image_id = _capture(server)
    assert len(seen) == 1 and image_id not in seen[0], seen        # recorded before the image entered
    folder = images_root / image_id
    for name in ("p1.esp.pcl.zst", "p3.windows.pcl.zst"):
        assert (folder / (name + ".idx")).is_file() and (folder / (name + ".b3")).is_file(), name
    raw = _on_disk(images_root, image_id)
    assert all(p["readback_chunks"] == 2 and len(p["chunk_b3"]) == 64 for p in raw["partitions"]), raw
    lib = ImageLibrary(images_root)
    assert not RB.needs_list(lib.get(image_id)), "nothing left for a wave to wait for"
    assert RB.fill(lib) == [], "and nothing for the background pass either"
    events = [r["event"] for r in server["ctx"].conn.execute("SELECT event FROM journal").fetchall()]
    assert "capture_index_derived" in events


def test_an_index_that_is_not_the_one_the_capture_signed_writes_nothing_and_the_image_enters_as_before(
        server, images_root, with_tool):
    image_id = _capture(server, p3_signed="0" * 16)
    folder = images_root / image_id
    assert not list(folder.glob("*.idx")) and not list(folder.glob("*.b3")), sorted(p.name for p in folder.iterdir())
    raw = _on_disk(images_root, image_id)
    assert all("readback_chunks" not in p and "chunk_b3" not in p for p in raw["partitions"]), raw
    events = [r["event"] for r in server["ctx"].conn.execute("SELECT event FROM journal").fetchall()]
    assert "capture_index_derived" not in events


def test_an_ingest_without_the_tool_still_derives_the_index_and_leaves_the_list_to_the_tool(server, images_root,
                                                                                            monkeypatch):
    monkeypatch.setattr(RB, "_B3_TOOL", [None])
    image_id = _capture(server)
    folder = images_root / image_id
    assert (folder / "p3.windows.pcl.zst.idx").is_file() and not list(folder.glob("*.b3"))
    assert "chunk_b3" not in _on_disk(images_root, image_id)["partitions"][1]


# --- אחרי שדרוג: המעבר ברקע ----------------------------------------------------------------------------


def test_the_background_pass_starts_by_itself_on_startup_and_builds_the_list_an_old_image_lacks(tmp_path, with_tool):
    lib = _image(tmp_path)
    assert RB.needs_list(lib.get(IMAGE))
    reported, stop = [], threading.Event()
    got = threading.Event()

    def report(result):
        reported.append(result)
        got.set()

    thread = threading.Thread(target=RB.run_forever, args=(lib,),
                              kwargs={"report": report, "interval": 3600.0, "stop": stop}, daemon=True)
    thread.start()                                           # no kick(): an hour is the interval
    try:
        assert got.wait(120), "the pass did not start on its own"
    finally:
        stop.set()
        RB._KICK.set()
        thread.join(30)
    assert not thread.is_alive()
    assert reported[0]["image_id"] == IMAGE and reported[0]["problems"] == [], reported
    assert RB.has_b3(lib.get(IMAGE)["_dir"], lib.get(IMAGE)["partitions"][1])
    assert not RB.needs_list(lib.get(IMAGE))


def test_a_pass_that_could_not_make_the_list_says_so_by_name_until_it_does(tmp_path, tool, monkeypatch):
    broken = tmp_path / "chunkcast"
    broken.write_text("#!/bin/sh\ncat > /dev/null\nexit 1\n", newline="\n")
    broken.chmod(0o755)
    monkeypatch.setattr(RB, "_B3_TOOL", [str(broken)])
    lib = _image(tmp_path / "lib")
    RB.fill(lib)
    assert IMAGE in (failed() or {}) and "chunkcast b3list exited 1" in failed()[IMAGE], failed()
    monkeypatch.setattr(RB, "_B3_TOOL", [tool])
    RB.fill(lib, tried={})                                   # the files are the same: a new pass, as after an upgrade
    assert failed() == {} and not RB.needs_list(lib.get(IMAGE))


# --- מה שהמפעיל רואה ------------------------------------------------------------------------------------


def _ctx(lib, tmp_path):
    return SimpleNamespace(library=lib, conn=connect(str(tmp_path / "health.db")))


def test_no_row_when_every_image_has_its_list(tmp_path, with_tool):
    lib = _image(tmp_path / "lib")
    assert [r["state"] for r in indexwait.health_checks(_ctx(lib, tmp_path))] == ["warn"]
    RB.fill(lib)
    assert indexwait.health_checks(_ctx(lib, tmp_path)) == []


def test_waiting_images_are_a_yellow_row_with_how_many_and_roughly_how_long(tmp_path, with_tool):
    lib = _image(tmp_path / "lib")
    (row,) = indexwait.health_checks(_ctx(lib, tmp_path))
    assert row["id"] == "image_index" and row["state"] == "warn", row
    assert "1 אימג'ים עוד בלי רשימת BLAKE3" in row["detail"] and "Office 2024 Standard" in row["detail"], row
    assert "בערך 1 דק'" in row["detail"] and "סבב פתוח" not in row["detail"], row


def test_a_round_open_on_a_waiting_image_is_named_and_is_not_blocked(tmp_path, with_tool):
    lib = _image(tmp_path / "lib")
    ctx = _ctx(lib, tmp_path)
    ctx.conn.execute("INSERT INTO room_rounds (id, image_id, target_drives, state, opened_by, created_at)"
                     " VALUES ('room_3502', ?, 1, 'active', 'noc', '2026-11-15T10:00:00+00:00')", (IMAGE,))
    ctx.conn.commit()
    (row,) = indexwait.health_checks(ctx)
    assert row["state"] == "warn" and "סבב פתוח על Office 2024 Standard" in row["detail"], row
    assert "אינו נחסם" in row["detail"] and "ימתין לרשימה" in row["detail"], row


def test_no_tool_and_images_without_a_list_is_a_red_row(tmp_path, monkeypatch):
    monkeypatch.setattr(RB, "_B3_TOOL", [None])
    lib = _image(tmp_path / "lib")
    (row,) = indexwait.health_checks(_ctx(lib, tmp_path))
    assert row["state"] == "bad" and "אין בשרת chunkcast שמייצר אותה" in row["detail"], row


def test_a_failed_pass_is_a_red_row_with_its_reason(tmp_path, monkeypatch):
    broken = tmp_path / "chunkcast"
    broken.write_text("#!/bin/sh\ncat > /dev/null\nexit 1\n", newline="\n")
    broken.chmod(0o755)
    monkeypatch.setattr(RB, "_B3_TOOL", [str(broken)])
    lib = _image(tmp_path / "lib")
    RB.fill(lib)
    (row,) = indexwait.health_checks(_ctx(lib, tmp_path))
    assert row["state"] == "bad" and "הגזירה נכשלה" in row["detail"] and "chunkcast b3list exited 1" in row["detail"], row


def test_a_library_that_cannot_be_read_is_not_a_quiet_health(tmp_path):
    class Broken:
        def scan(self):
            raise OSError("disk gone")
    (row,) = indexwait.health_checks(SimpleNamespace(library=Broken(), conn=connect(str(tmp_path / "h.db"))))
    assert row["state"] == "unknown" and "disk gone" in row["detail"], row


def test_the_upgrade_print_says_how_many_wait_and_the_exit_code_says_whether_it_could_check(tmp_path, with_tool,
                                                                                           capsys):
    _image(tmp_path / "lib")
    assert indexwait.main([str(tmp_path / "lib")]) == 0
    out = capsys.readouterr().out
    assert "1 אימג'ים עוד בלי אינדקס או רשימת BLAKE3" in out and "Office 2024 Standard" in out, out
    RB.fill(ImageLibrary(tmp_path / "lib"))
    assert indexwait.main([str(tmp_path / "lib")]) == 0
    assert "כל האימג'ים בספרייה כבר עם אינדקס ורשימת BLAKE3" in capsys.readouterr().out


def test_the_upgrade_print_fails_by_name_when_there_is_no_tool(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(RB, "_B3_TOOL", [None])
    _image(tmp_path / "lib")
    assert indexwait.main([str(tmp_path / "lib")]) == 2
    assert "אין בשרת chunkcast שמייצר רשימות BLAKE3" in capsys.readouterr().out
