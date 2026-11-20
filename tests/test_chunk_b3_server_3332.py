"""#3332: השרת גוזר לכל מחיצה את רשימת ה-BLAKE3 שלה, באותה פריסה שבה הוא גוזר את האינדקס.

`server/agent/readbackhash.py` (`B3Lister`, `b3_tool`, `has_b3`), `server/library/chunkformat.py` (`derive_index`),
`ImageLibrary.record_index`, `GET /api/v1/images/<id>/b3/<n>` והשער של הגל (`server/rounds/indexprep.py`). הכלי הוא
ה-`chunkcast` האמיתי (`b3list`/`b3sum`), שנבנה כאן כמו שהשרת בונה אותו.

מה נבדק: הרשימה שנכתבת ליד הקובץ היא BLAKE3 של כל 4MiB של זרם הנתונים, ו-`chunk_b3` הוא ה-BLAKE3 שלה; אימג' שנגזר
לפני שהייתה רשימה מקבל אותה בגזירה חוזרת -- ורק כשהכלי בשרת; כלי שנכשל משאיר את האינדקס (גל ממתין לו, #3137) ולא
משאיר רשימה, ואינו נגזר שוב בכל מעבר. **הרשימה חובה** (נדב 15/11): גל של אימג' בלי רשימה ממתין לגזירה שלה, וכשהיא
אינה יכולה להיווצר (אין כלי, הכלי נכשל) -- הגל אינו יוצא, וכתובה סיבה בשמה.

**בקרה שלילית:** `server/agent/readbackhash.py`, `server/library/chunkformat.py`, `server/library/images.py`,
`server/agent/api.py` ו-`server/rounds/indexprep.py` חוזרים לבסיס (ואיתם `agent/chunkcast.c` ומה שבונה אותו). אין `<file>.b3`,
אין `chunk_b3`, `/b3/` הוא 404, ו-`_B3_TOOL` אינו קיים -- כל טסט כאן נופל.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import chunk_image as CI
import chunkcast_rig as CR
from conftest import MANIFEST_256, write_image
from native import requires_native
from server.agent import readbackhash as RB
from server.library.images import ImageLibrary
from server.rounds import indexprep
from test_chunk_format_2533 import STREAM, _derive, _image, _on_disk, _served, built

pytestmark = requires_native("gcc", "zstd", posix=True, why="the BLAKE3 tool is chunkcast, built with gcc and libzstd")


@pytest.fixture(scope="module")
def tool(tmp_path_factory) -> str:
    return str(CR.build_binaries(tmp_path_factory.mktemp("b3srv-bin")) / "chunkcast")


@pytest.fixture()
def with_tool(tool, monkeypatch):
    monkeypatch.setattr(RB, "_B3_TOOL", [tool])
    return tool


def _run(tool: str, mode: str, data: bytes) -> bytes:
    done = subprocess.run([tool, mode], input=data, capture_output=True)
    assert done.returncode == 0, done.stderr
    return done.stdout


def _want(tool: str) -> tuple[bytes, str]:
    """The list of STREAM's data stream -- its used blocks, the head left out -- and its BLAKE3."""
    _head, data = CI.split(STREAM, False)
    assert len(data) == 1025 * 4096
    listed = _run(tool, "b3list", data)
    return listed, _run(tool, "b3sum", listed).decode().strip()


def test_the_list_beside_the_file_is_the_blake3_of_every_chunk_and_the_manifest_names_it(tmp_path, with_tool):
    lib = _image(tmp_path)
    results = RB.fill(lib, digest=_derive)
    assert [r["problems"] for r in results] == [[]], results
    listed, digest = _want(with_tool)
    assert (tmp_path / "img_2533aa" / "p3.win.pcl.zst.b3").read_bytes() == listed and len(listed) == 2 * 32
    win = _on_disk(tmp_path)["partitions"][1]
    assert win["chunk_b3"] == digest and win["readback_chunks"] == 2
    assert RB.has_b3(lib.get("img_2533aa")["_dir"], lib.get("img_2533aa")["partitions"][1])
    assert RB.fill(lib, digest=_derive) == []                          # whole: not read again


def test_an_image_derived_before_the_list_gets_it_once_the_tool_is_there(tmp_path, tool, monkeypatch):
    monkeypatch.setattr(RB, "_B3_TOOL", [None])                        # a server whose chunkcast has no b3list
    lib = _image(tmp_path)
    assert [r["problems"] for r in RB.fill(lib, digest=_derive)] == [[]]
    assert "chunk_b3" not in _on_disk(tmp_path)["partitions"][1]
    assert not (tmp_path / "img_2533aa" / "p3.win.pcl.zst.b3").exists()
    assert not RB.needs_list(lib.get("img_2533aa"))                    # no tool: nothing to derive again for it
    monkeypatch.setattr(RB, "_B3_TOOL", [tool])
    assert RB.needs_list(lib.get("img_2533aa"))                        # the tool: the image is derived again
    assert [r["problems"] for r in RB.fill(lib, digest=_derive)] == [[]]
    listed, digest = _want(tool)
    assert _on_disk(tmp_path)["partitions"][1]["chunk_b3"] == digest
    assert (tmp_path / "img_2533aa" / "p3.win.pcl.zst.b3").read_bytes() == listed


def test_a_list_that_did_not_come_out_leaves_the_index_and_no_list_and_is_not_made_every_pass(tmp_path, monkeypatch):
    broken = tmp_path / "chunkcast"
    broken.write_text("#!/bin/sh\ncat > /dev/null\nexit 1\n", newline="\n")
    broken.chmod(0o755)
    monkeypatch.setattr(RB, "_B3_TOOL", [str(broken)])
    lib = _image(tmp_path / "lib")
    tried: dict = {}
    results = RB.fill(lib, digest=_derive, tried=tried)
    assert [r["problems"] for r in results] == [[]] and results[0]["written"][3]["b3_note"], results
    win = _on_disk(tmp_path / "lib")["partitions"][1]
    assert win["readback_chunks"] == 2 and "chunk_b3" not in win       # the index stands; a wave waits for it
    assert not (tmp_path / "lib" / "img_2533aa" / "p3.win.pcl.zst.b3").exists()
    assert "img_2533aa" in tried and RB.fill(lib, digest=_derive, tried=tried) == []


def test_the_list_is_served_only_once_it_is_there(server, images_root, with_tool):
    anon, ctx = server["anon"], server["ctx"]
    _served(server, images_root)
    assert anon.get("/api/v1/images/img_7f3a91/b3/3").status_code == 404           # not derived yet
    assert RB.fill(ctx.library, digest=_derive)[0]["problems"] == []
    got = anon.get("/api/v1/images/img_7f3a91/b3/3")
    listed, digest = _want(with_tool)
    assert got.status_code == 200 and got.content == listed
    assert ctx.library.get("img_7f3a91")["partitions"][1]["chunk_b3"] == digest
    assert anon.get("/api/v1/images/img_7f3a91/b3/1").json()["code"] == "no_b3"      # p1 has no index
    (images_root / "img_7f3a91" / "p3.win.pcl.zst.b3").write_bytes(listed[:-1])     # not the index's length
    assert anon.get("/api/v1/images/img_7f3a91/b3/3").status_code == 404


def _gate(lib, image_id: str = "img_2533aa", wave: str = "ses_b3"):
    """The wave's gate, asked until it answers something other than "preparing" -- as the room's heartbeat asks it."""
    prep = indexprep.IndexPrep(lib)
    for _ in range(600):
        state = prep.gate(wave, image_id)
        if state is None or state["state"] != "preparing":
            return state
        indexprep.time.sleep(0.1)
    raise AssertionError(f"still preparing: {state}")


def _image_with_p3_only(root: Path, image_id: str = "img_2533aa") -> ImageLibrary:
    """`_image` without the ESP: the gate asks every streamed partition for its index, and `_image`'s p1 is a file
    the capture never signed -- it could never have an index, and a wave of it never goes (as before #3332)."""
    file_blob, index = built()
    only = {**MANIFEST_256, "id": image_id, "partitions": [p for p in MANIFEST_256["partitions"] if p["index"] == 3]}
    write_image(root, CI.signed(only, {3: index}))
    (root / image_id / "p3.win.pcl.zst").write_bytes(file_blob)
    return ImageLibrary(root)


def test_a_wave_waits_for_the_list_and_goes_once_it_is_there(tmp_path, with_tool, monkeypatch):
    monkeypatch.setattr(RB, "_B3_TOOL", [None])
    lib = _image_with_p3_only(tmp_path)
    assert [r["problems"] for r in RB.fill(lib, digest=_derive)] == [[]]       # derived before there was a list
    assert indexprep.missing(lib, "img_2533aa", lib.get("img_2533aa")), "a partition without a list is not ready"
    monkeypatch.setattr(RB, "_B3_TOOL", [with_tool])
    assert _gate(lib) is None, "the wave goes once the list is there"
    listed, digest = _want(with_tool)
    assert _on_disk(tmp_path)["partitions"][0]["chunk_b3"] == digest        # the only partition: p3
    assert (tmp_path / "img_2533aa" / "p3.win.pcl.zst.b3").read_bytes() == listed


def test_no_tool_to_make_the_list_and_the_wave_does_not_go_by_name(tmp_path, monkeypatch):
    monkeypatch.setattr(RB, "_B3_TOOL", [None])
    lib = _image(tmp_path)
    state = _gate(lib)
    assert state is not None and state["state"] == "failed", state
    assert "אין בשרת chunkcast שמייצר רשימות BLAKE3" in state["error"] and indexprep.WAVE_TAIL in state["error"], state


def test_a_list_that_did_not_come_out_and_the_wave_does_not_go_by_name(tmp_path, monkeypatch):
    broken = tmp_path / "chunkcast"
    broken.write_text("#!/bin/sh\ncat > /dev/null\nexit 1\n", newline="\n")
    broken.chmod(0o755)
    monkeypatch.setattr(RB, "_B3_TOOL", [str(broken)])
    lib = _image(tmp_path / "lib")
    state = _gate(lib)
    assert state is not None and state["state"] == "failed", state
    assert "p3: רשימת ה-BLAKE3 לא נוצרה — chunkcast b3list exited 1" in state["error"], state
