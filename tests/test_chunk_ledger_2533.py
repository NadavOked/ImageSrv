"""#2533 / #2528 stage 2 -- the write ledger at chunk level, on the server.

Nadav, 12/11: the ledger is kept on the server only; the target disk carries the
#1798 mark and no list. A row a (disk by its serial, partition): the closed ranges
of chunk numbers the agent reported DURABLE -- written before an fsync that came
back 0 (the agent's half is in test_chunkcut_2533.py).

What these tests hold: the ledger only grows within one write (a late, older
report erases nothing), a different write on the same disk starts it again -- in
any engine, with or without `durable` -- a disk is its serial AND its size and
twins have no ledger, a ledger that breaks never fails the report, a
report that is not consistent is refused whole and by name, and nothing is
recorded for a disk the server cannot name by its serial or a partition whose
index is not verified -- there is nothing to check the ranges against.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import chunk_image as C
from server import db
from server.rounds import chunkledger as L
from test_fanout import xxh64_reference
from test_server_room import (CLONER1, cloner_hello, room_body, room_server)      # noqa: F401 -- a fixture

CHUNKS = 100
SIZE = 256060514304          # the size every drawer of the room fixture reports
MANIFEST = {"partitions": [{"index": 3, "index_xxh64": "a" * 16, "readback_chunks": CHUNKS},
                           {"index": 1, "index_xxh64": "b" * 16, "readback_chunks": 2},
                           {"index": 4, "index_xxh64": "c" * 16}]}                 # 4: not verified yet


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def rec(conn, durable, *, round_id="ses_1", serial="S1", size_bytes=SIZE, image_id="img_aaaaaa",
        manifest=MANIFEST, mac="aa:bb:cc:00:00:21"):
    L.record(conn, round_id=round_id, mac=mac, serial=serial, size_bytes=size_bytes, image_id=image_id,
             manifest=manifest, durable=durable)


def ranges(conn, serial="S1", part=3, size_bytes=SIZE):
    return L.of_disk(conn, serial, size_bytes).get(part, {}).get("ranges")


# --- ranges ----------------------------------------------------------------------------


def test_union_merges_overlapping_and_adjacent_ranges():
    assert L.union([(0, 4), (10, 12)], [(5, 6), (11, 20), (30, 30)]) == [(0, 6), (10, 20), (30, 30)]
    assert L.union([], [(3, 3)]) == [(3, 3)] and L.union([(0, 9)], [(2, 4)]) == [(0, 9)]


def test_missing_is_what_the_disk_still_needs():
    assert L.missing([(0, 41), (44, 50)], 100) == [(42, 43), (51, 99)]
    assert L.missing([], 3) == [(0, 2)] and L.missing([(0, 2)], 3) == [] and L.missing([], 0) == []


@pytest.mark.parametrize("value, why", [
    ("0-5", "not a list"),
    ([[0, 5], [3, 9]], "is not after"),                 # overlapping
    ([[0, 5], [6, 9]], "is not after"),                 # adjacent: one range, the agent merges
    ([[7, 9], [0, 5]], "is not after"),                 # not sorted
    ([[5, 3]], "outside the partition"),
    ([[0, CHUNKS]], "outside the partition"),           # the last chunk is CHUNKS - 1
    ([[-1, 3]], "outside the partition"),
    ([[0, 5, 9]], "not \\[first, last\\]"),
    ([[0, "5"]], "not \\[first, last\\]"),
    ([[0.0, 5]], "not \\[first, last\\]"),
    ([[True, 5]], "not \\[first, last\\]"),
])
def test_ranges_that_are_not_what_the_contract_says_are_refused(value, why):
    with pytest.raises(L.LedgerProblem, match=why):
        L.well_formed(value, CHUNKS)


def test_well_formed_ranges_pass_as_they_are():
    assert L.well_formed([[0, 41], [44, 50], [CHUNKS - 1, CHUNKS - 1]], CHUNKS) == [(0, 41), (44, 50), (99, 99)]
    assert L.well_formed([], CHUNKS) == []


def test_a_scattered_hole_map_of_a_large_image_is_accepted():
    """A disk that fell behind and skipped chunks one at a time reports a range per
    run: 20,000 chunks (an 80GB partition) with every fourth one missing are 5,000
    ranges, and the stage that completes such a disk needs every one of them. The
    only ceiling is what a well-formed list can hold at all."""
    chunks = 20_000
    value = [[k, k + 2] for k in range(0, chunks, 4)]
    assert len(value) == 5000
    assert L.well_formed(value, chunks) == [(k, k + 2) for k in range(0, chunks, 4)]
    for n in (100, 101):                                 # every other chunk: nothing denser is well-formed
        fullest = [[k, k] for k in range(0, n, 2)]
        assert len(fullest) == (n + 1) // 2 == len(L.well_formed(fullest, n))


@pytest.mark.parametrize("chunks", [100, 101, 0])
def test_more_ranges_than_the_partition_can_hold_are_refused_by_their_count(chunks):
    """Ranges with a gap between them: at most every other chunk. A longer list
    cannot be well-formed, and it is refused by name before one item is read."""
    too_many = [[0, 0]] * ((chunks + 1) // 2 + 1)
    with pytest.raises(L.LedgerProblem, match="more than the %d ranges" % ((chunks + 1) // 2)):
        L.well_formed(too_many, chunks)


def test_a_scattered_hole_map_is_recorded_and_read_back(conn):
    big = {"partitions": [{"index": 3, "index_xxh64": "a" * 16, "readback_chunks": 20_000}]}
    rec(conn, [{"part": 3, "ranges": [[k, k + 2] for k in range(0, 20_000, 4)]}], manifest=big)
    row = L.of_disk(conn, "S1", SIZE)[3]
    assert len(row["ranges"]) == 5000 and row["ranges"][-1] == (19_996, 19_998)
    assert row["missing"] == [(k, k) for k in range(3, 20_000, 4)]


# --- the ledger ------------------------------------------------------------------------


def test_reports_are_cumulative_and_the_ledger_only_grows(conn):
    """A report that was lost changes nothing -- the next one holds everything. And an
    OLDER report that arrives late erases nothing: the ledger may lag, never shrink."""
    rec(conn, [{"part": 3, "ranges": [[0, 41]]}])
    rec(conn, [{"part": 3, "ranges": [[0, 60], [70, 72]]}])
    assert ranges(conn) == [(0, 60), (70, 72)]
    rec(conn, [{"part": 3, "ranges": [[0, 41]]}])                       # the older one, late
    assert ranges(conn) == [(0, 60), (70, 72)]
    row = L.of_disk(conn, "S1", SIZE)[3]
    assert row["missing"] == [(61, 69), (73, 99)] and row["chunks"] == CHUNKS
    assert (row["round_id"], row["image_id"], row["index_xxh64"]) == ("ses_1", "img_aaaaaa", "a" * 16)


def test_each_partition_and_each_disk_has_its_own_row(conn):
    rec(conn, [{"part": 3, "ranges": [[0, 9]]}, {"part": 1, "ranges": [[0, 1]]}])
    rec(conn, [{"part": 3, "ranges": [[5, 5]]}], serial="S2")
    assert ranges(conn) == [(0, 9)] and ranges(conn, part=1) == [(0, 1)]
    assert ranges(conn, "S2") == [(5, 5)] and L.of_disk(conn, "S9", SIZE) == {}


def test_another_write_on_the_same_disk_starts_the_ledger_again(conn):
    """A new wave writes the disk from its start: what the ledger held is not evidence
    for what is on the disk now. Carrying it over is stage 4's explicit `adopt`."""
    rec(conn, [{"part": 3, "ranges": [[0, 60]]}, {"part": 1, "ranges": [[0, 1]]}])
    rec(conn, [{"part": 3, "ranges": [[0, 2]]}], round_id="ses_2")
    assert ranges(conn) == [(0, 2)] and ranges(conn, part=1) is None    # the whole disk, not one partition
    rec(conn, [{"part": 3, "ranges": [[0, 5]]}], round_id="ses_2", image_id="img_bbbbbb")
    assert ranges(conn) == [(0, 5)]
    assert L.of_disk(conn, "S1", SIZE)[3]["image_id"] == "img_bbbbbb"


def test_a_partition_whose_index_changed_does_not_keep_the_old_chunks(conn):
    rec(conn, [{"part": 3, "ranges": [[0, 60]]}])
    other = {"partitions": [{"index": 3, "index_xxh64": "f" * 16, "readback_chunks": CHUNKS}]}
    rec(conn, [{"part": 3, "ranges": [[0, 2]]}], manifest=other)
    assert ranges(conn) == [(0, 2)] and L.of_disk(conn, "S1", SIZE)[3]["index_xxh64"] == "f" * 16


def test_adopt_carries_the_ledger_into_the_round_that_continues_the_write(conn):
    rec(conn, [{"part": 3, "ranges": [[0, 60]]}])
    assert L.adopt(conn, "S1", SIZE, "ses_2", image_id="img_aaaaaa", manifest=MANIFEST) == 1   # #3117: the same image
    rec(conn, [{"part": 3, "ranges": [[61, 70]]}], round_id="ses_2")
    assert ranges(conn) == [(0, 70)]


@pytest.mark.parametrize("durable, kwargs, why", [
    ([{"part": 3, "ranges": [[0, 5]]}], {"serial": ""}, "serial and size are not known"),
    ([{"part": 3, "ranges": [[0, 5]]}], {"serial": None}, "serial and size are not known"),
    ([{"part": 3, "ranges": [[0, 5]]}], {"size_bytes": 0}, "serial and size are not known"),
    ([{"part": 3, "ranges": [[0, 5]]}], {"size_bytes": None}, "serial and size are not known"),
    ([{"part": 9, "ranges": [[0, 5]]}], {}, "partition 9 is not in the image's manifest"),
    ([{"part": "3", "ranges": [[0, 5]]}], {}, "is not in the image's manifest"),
    ([{"part": 4, "ranges": [[0, 5]]}], {}, "no verified chunk index yet"),
    ([{"part": 3, "ranges": [[0, 5]]}, {"part": 3, "ranges": [[9, 9]]}], {}, "appears twice"),
    ([{"part": 1, "ranges": [[0, 1]]}, {"part": 3, "ranges": [[0, 500]]}], {}, "outside the partition's 100"),
    ({"part": 3}, {}, "not a list of partitions"),
    (["x"], {}, "not an object"),
])
def test_a_report_that_is_not_consistent_is_refused_whole(conn, durable, kwargs, why):
    """All or nothing: one bad partition and the good one beside it is not recorded
    either -- a ledger that was half believed is not a ledger."""
    rec(conn, [{"part": 3, "ranges": [[0, 7]]}])
    with pytest.raises(L.LedgerProblem, match=why):
        rec(conn, durable, **kwargs)
    assert ranges(conn) == [(0, 7)] and ranges(conn, part=1) is None


def test_forget_names_what_it_forgets(conn):
    rec(conn, [{"part": 3, "ranges": [[0, 7]]}])
    rec(conn, [{"part": 3, "ranges": [[0, 7]]}], serial="S2")
    assert L.forget(conn) == 0 and ranges(conn) == [(0, 7)]             # neither named: nothing, not everything
    assert L.forget(conn, serial="S1") == 0 and ranges(conn) == [(0, 7)]         # a serial is not a disk without its size
    assert L.forget(conn, serial="S1", size_bytes=SIZE) == 1 and L.of_disk(conn, "S1", SIZE) == {}
    assert ranges(conn, "S2") == [(0, 7)]
    assert L.forget(conn, round_id="ses_1") == 1 and L.of_disk(conn, "S2", SIZE) == {}


# --- a disk is its serial AND its size; two that answer to both are twins ------------------


def test_two_disks_with_one_serial_and_other_sizes_are_two_disks(conn):
    """The review of 12/11 (2-א): the key was the serial alone, and two disks with the
    same serial -- a cheap USB bridge, a cloned drive -- shared one row."""
    rec(conn, [{"part": 3, "ranges": [[0, 9]]}])
    rec(conn, [{"part": 3, "ranges": [[50, 59]]}], size_bytes=500107862016, mac="aa:bb:cc:00:00:22")
    assert ranges(conn) == [(0, 9)] and ranges(conn, size_bytes=500107862016) == [(50, 59)]


def test_twins_on_two_machines_have_no_ledger_and_do_not_merge(conn):
    """Same serial, same size, two machines, one round: neither report may be taken
    for the other disk. The ledger of that identity is dropped, and stays dropped
    for the round -- "twins = no continuation" (design 2.3)."""
    rec(conn, [{"part": 3, "ranges": [[0, 9]]}])
    with pytest.raises(L.LedgerProblem, match="twins"):
        rec(conn, [{"part": 3, "ranges": [[50, 59]]}], mac="aa:bb:cc:00:00:22")
    assert L.of_disk(conn, "S1", SIZE) == {}                              # not [(0, 9), (50, 59)]
    for mac in ("aa:bb:cc:00:00:21", "aa:bb:cc:00:00:22"):                # and neither comes back in this round
        with pytest.raises(L.LedgerProblem, match="twins"):
            rec(conn, [{"part": 3, "ranges": [[0, 20]]}], mac=mac)
    assert L.of_disk(conn, "S1", SIZE) == {} and L.adopt(
        conn, "S1", SIZE, "ses_2", image_id="img_aaaaaa", manifest=MANIFEST) == 0
    rec(conn, [{"part": 3, "ranges": [[0, 3]]}], round_id="ses_3")        # another round: one disk writes, a ledger again
    assert ranges(conn) == [(0, 3)]


def test_forget_others_drops_only_the_ledger_of_another_round(conn):
    rec(conn, [{"part": 3, "ranges": [[0, 9]]}])
    rec(conn, [{"part": 3, "ranges": [[0, 9]]}], serial="S2")
    assert L.forget_others(conn, [("S1", SIZE), ("S9", SIZE)], "ses_1") == 0   # this round's own: kept
    assert L.forget_others(conn, [("S1", SIZE)], "ses_2") == 1
    assert L.of_disk(conn, "S1", SIZE) == {} and ranges(conn, "S2") == [(0, 9)]


# --- through the progress report ---------------------------------------------------------


def _verified(ctx, chunks: int = CHUNKS) -> None:
    """img_7f3a91's p3 with a verified index of `chunks` chunks, as readbackhash records it."""
    index = C.index_of([f"{i:016x}" for i in range(chunks)], chunks * 4194304)
    path = Path(ctx.library.root) / "img_7f3a91" / "manifest.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(C.signed(raw, {3: index.render()}), ensure_ascii=False), encoding="utf-8")
    C.place(path.parent, "p3.win.pcl.zst", index)
    listing = index.readback_list()
    assert ctx.library.record_index("img_7f3a91", {3: {
        "index": index.render(), "list": listing, "list_xxh64": xxh64_reference(listing.encode("ascii")),
        "bytes": index.data_bytes}}) == [3]


def _wave(room_server) -> str:
    opened = room_server["deploy"].post("/api/console/room", json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}))
    assert opened.status_code == 200, opened.text
    return cloner_hello(room_server["anon"], CLONER1, ["S1", "S2"])["session"]["id"]


def _progress(room_server, wave: str, targets: list[dict]):
    return room_server["anon"].post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": CLONER1, "state": "writing",
        "targets": [{"bytes_written": 10, "bytes_total": 100, "state": "writing", **t} for t in targets]})


def test_the_durable_chunks_of_a_report_reach_the_ledger_by_the_disks_serial(room_server):
    ctx = room_server["ctx"]
    _verified(ctx)
    wave = _wave(room_server)
    answer = _progress(room_server, wave, [
        {"dev": "sda", "durable": [{"part": 3, "ranges": [[0, 41], [44, 50]]}]},
        {"dev": "sdb"}])                                                     # no field: no information
    assert answer.status_code == 200 and answer.json()["ok"] is True, answer.text
    row = L.of_disk(ctx.conn, "S1", SIZE)[3]
    assert row["ranges"] == [(0, 41), (44, 50)] and row["missing"] == [(42, 43), (51, 99)]
    assert (row["round_id"], row["image_id"], row["mac"]) == (wave, "img_7f3a91", CLONER1)
    assert L.of_disk(ctx.conn, "S2", SIZE) == {}                                   # not "none are durable": nothing known
    _progress(room_server, wave, [{"dev": "sda", "durable": [{"part": 3, "ranges": [[0, 60]]}]}])
    assert L.of_disk(ctx.conn, "S1", SIZE)[3]["ranges"] == [(0, 60)]


def test_a_refused_ledger_does_not_fail_the_report(room_server):
    """The progress itself stands -- the write goes on. What is lost is only what could
    have been continued from, and the server's log says why."""
    ctx = room_server["ctx"]
    _verified(ctx)
    wave = _wave(room_server)
    answer = _progress(room_server, wave, [{"dev": "sda", "durable": [{"part": 3, "ranges": [[0, 5000]]}]}])
    assert answer.status_code == 200 and answer.json()["ok"] is True, answer.text
    assert L.of_disk(ctx.conn, "S1", SIZE) == {}
    assert L.note_report(ctx, CLONER1, {"session_id": wave, "targets": [
        {"dev": "sda", "durable": [{"part": 3, "ranges": [[0, 5000]]}]}]})[0].startswith("'sda': range [0, 5000]")


def test_no_ledger_for_an_image_whose_index_is_not_verified(room_server):
    ctx = room_server["ctx"]                                                 # the fixture image: no index at all
    wave = _wave(room_server)
    assert _progress(room_server, wave, [{"dev": "sda", "durable": [{"part": 3, "ranges": [[0, 5]]}]}]).json()["ok"]
    assert L.of_disk(ctx.conn, "S1", SIZE) == {}


def test_no_ledger_for_a_disk_the_server_cannot_name(room_server):
    ctx = room_server["ctx"]
    _verified(ctx)
    wave = _wave(room_server)
    problems = L.note_report(ctx, CLONER1, {"session_id": wave, "targets": [
        {"dev": "sdz", "durable": [{"part": 3, "ranges": [[0, 5]]}]}]})
    assert len(problems) == 2 and "serial and size are not known" in problems[0]
    assert problems[1] == L.RETRY_LATER                # a later hello may name the disk
    assert ctx.conn.execute("SELECT COUNT(*) FROM chunk_ledger").fetchone()[0] == 0


def test_a_refusal_that_the_server_may_lift_says_retry_later(room_server, monkeypatch):
    """The reports are a numbered delta (stage 3; the coordinator, 13/11): on
    `NOT_WRITTEN` or `RETRY_LATER` the number does not move on and the agent sends the
    ranges again. Three refusals hang on what the server knows NOW, not on the report:
    the index is not verified yet, the round's image is not known, the last hello did
    not name the disk. The reason by name stays in the list, beside the mark -- and
    once the server knows, the very same report is recorded."""
    ctx = room_server["ctx"]
    wave = _wave(room_server)                                            # the fixture image: no index yet
    report = {"session_id": wave, "targets": [{"dev": "sda", "durable": [{"part": 3, "ranges": [[0, 5]]}]}]}
    problems = L.note_report(ctx, CLONER1, report)
    assert len(problems) == 2 and "no verified chunk index yet" in problems[0] and problems[1] == L.RETRY_LATER
    assert L.of_disk(ctx.conn, "S1", SIZE) == {}
    _verified(ctx)
    L._SEEN.clear()                                                      # the manifest is kept MANIFEST_TTL_S
    assert L.note_report(ctx, CLONER1, report) == []
    assert L.of_disk(ctx.conn, "S1", SIZE)[3]["ranges"] == [(0, 5)]

    from server.rounds import direct
    L._SEEN.clear()
    with monkeypatch.context() as m:
        m.setattr(ctx.library, "get", lambda image_id: None)
        m.setattr(direct, "live_manifest", lambda conn, image_id: None)
        problems = L.note_report(ctx, CLONER1, report)
    assert len(problems) == 2 and "image is not known" in problems[0] and problems[1] == L.RETRY_LATER
    L._SEEN.clear()

    two = {"session_id": wave, "targets": [{"dev": "sdz", "durable": [{"part": 3, "ranges": [[0, 5]]}]},
                                           {"dev": "sdy", "durable": [{"part": 3, "ranges": [[0, 5]]}]}]}
    assert L.note_report(ctx, CLONER1, two).count(L.RETRY_LATER) == 1    # a mark of the report, once


def test_a_refusal_that_no_retry_can_lift_does_not_say_retry_later(room_server):
    """Sending these again changes nothing, so the number moves on -- and a report that
    asked for a retry every two seconds would never end."""
    ctx = room_server["ctx"]
    _verified(ctx)
    wave = _wave(room_server)
    for durable in ([{"part": 3, "ranges": [[0, 500]]}],                  # past the partition's end
                    [{"part": 3, "ranges": [[4, 2]]}],
                    [{"part": 9, "ranges": [[0, 5]]}],                    # not in the manifest
                    [{"part": 3, "ranges": [[0, 5]]}, {"part": 3, "ranges": [[9, 9]]}],
                    {"part": 3}):
        problems = L.note_report(ctx, CLONER1, {"session_id": wave, "targets": [{"dev": "sda", "durable": durable}]})
        assert len(problems) == 1 and L.RETRY_LATER not in problems and L.NOT_WRITTEN not in problems, durable
    mixed = L.note_report(ctx, CLONER1, {"session_id": wave, "targets": [
        {"dev": "sda", "durable": [{"part": 3, "ranges": [[0, 500]]}]},
        {"dev": "sdz", "durable": [{"part": 3, "ranges": [[0, 5]]}]}]})
    assert len(mixed) == 3 and mixed[2] == L.RETRY_LATER                  # one of the two may be lifted
    for dev in (None, 7, ["sda"]):                                        # a target that names no device
        problems = L.note_report(ctx, CLONER1, {"session_id": wave, "targets": [
            {"dev": dev, "durable": [{"part": 3, "ranges": [[0, 5]]}]}]})
        assert len(problems) == 1 and "names no device" in problems[0], dev


def _hello_says(ctx, change) -> None:
    """Rewrites what CLONER1's last hello said about its disks -- `change(disks)`."""
    disks = json.loads(ctx.conn.execute("SELECT disks_json FROM net_devices WHERE mac = ?",
                                        (CLONER1,)).fetchone()["disks_json"])
    out = change(disks)
    with db.writing(ctx.conn):
        ctx.conn.execute("UPDATE net_devices SET disks_json = ? WHERE mac = ?",
                         (None if out is None else json.dumps(out), CLONER1))


@pytest.mark.parametrize("case", ["empty serial", "no serial", "serial is not text", "size 0", "no size"])
def test_a_disk_the_hello_reported_without_a_serial_or_a_size_has_no_ledger_and_no_retry(room_server, case):
    """The design, 2.3: a disk with no serial has no ledger and nothing to continue
    from (and a room round does not write such a disk at all, #2448). The hello DID
    report it, so no later report can name it: the refusal is final, by name, and the
    agent is not asked to send the same ranges again until the round ends."""
    ctx = room_server["ctx"]
    _verified(ctx)
    wave = _wave(room_server)
    spoil = {"empty serial": lambda d: d.update(serial=""), "no serial": lambda d: d.pop("serial"),
             "serial is not text": lambda d: d.update(serial=7), "size 0": lambda d: d.update(size_bytes=0),
             "no size": lambda d: d.pop("size_bytes")}[case]

    def change(disks):
        spoil(next(d for d in disks if d["dev"] == "sdb"))
        return disks
    _hello_says(ctx, change)
    assert L.nameless_of(ctx.conn, CLONER1) == {"sdb"}
    assert L.disks_of(ctx.conn, CLONER1) == {"sda": ("S1", SIZE)}                  # as before: only the named
    problems = L.note_report(ctx, CLONER1, {"session_id": wave, "targets": [
        {"dev": "sdb", "durable": [{"part": 3, "ranges": [[0, 5]]}]},
        {"dev": "sda", "durable": [{"part": 3, "ranges": [[0, 5]]}]}]})
    assert len(problems) == 1 and problems[0].startswith("'sdb': the hello reported this disk with no serial")
    assert L.of_disk(ctx.conn, "S1", SIZE)[3]["ranges"] == [(0, 5)]                # the disk beside it is recorded
    assert ctx.conn.execute("SELECT COUNT(*) FROM chunk_ledger").fetchone()[0] == 1


@pytest.mark.parametrize("case", ["the disk is not in the hello", "no disks in the hello", "the hello cannot be read"])
def test_a_disk_the_hello_did_not_report_may_be_named_later(room_server, case):
    """No hello, or a hello without this disk: the next hello may name it -- RETRY_LATER."""
    ctx = room_server["ctx"]
    _verified(ctx)
    wave = _wave(room_server)
    _hello_says(ctx, {"the disk is not in the hello": lambda disks: [d for d in disks if d["dev"] != "sdb"],
                      "no disks in the hello": lambda disks: None,
                      "the hello cannot be read": lambda disks: "not a list"}[case])
    assert L.nameless_of(ctx.conn, CLONER1) == set()
    problems = L.note_report(ctx, CLONER1, {"session_id": wave, "targets": [
        {"dev": "sdb", "durable": [{"part": 3, "ranges": [[0, 5]]}]}]})
    assert len(problems) == 2 and "serial and size are not known" in problems[0] and problems[1] == L.RETRY_LATER


def test_a_report_that_was_refused_writes_no_ledger(room_server):
    """Only a report that was accepted is evidence: not a member of the wave = nothing."""
    ctx = room_server["ctx"]
    _verified(ctx)
    wave = _wave(room_server)
    stranger = room_server["anon"].post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": "aa:bb:cc:00:00:99", "state": "writing",
        "targets": [{"dev": "sda", "bytes_written": 1, "bytes_total": 2, "state": "writing",
                     "durable": [{"part": 3, "ranges": [[0, 5]]}]}]})
    assert stranger.json().get("ok") is not True
    assert ctx.conn.execute("SELECT COUNT(*) FROM chunk_ledger").fetchone()[0] == 0


def test_two_drawers_of_one_machine_with_one_identity_have_no_ledger(room_server):
    """Twins in one machine (#2448): the hello shows two disks with the same serial and
    size. Whichever reports, the server cannot tell which disk it was."""
    ctx = room_server["ctx"]
    _verified(ctx)
    opened = room_server["deploy"].post("/api/console/room", json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}))
    assert opened.status_code == 200, opened.text
    wave = cloner_hello(room_server["anon"], CLONER1, ["S1", "S1"])["session"]["id"]     # sda and sdb: one identity
    assert _progress(room_server, wave, [{"dev": "sda", "durable": [{"part": 3, "ranges": [[0, 5]]}]}]).json()["ok"]
    assert ctx.conn.execute("SELECT COUNT(*) FROM chunk_ledger").fetchone()[0] == 0
    problems = L.note_report(ctx, CLONER1, {"session_id": wave, "targets": [
        {"dev": "sdb", "durable": [{"part": 3, "ranges": [[0, 5]]}]}]})
    assert len(problems) == 1 and "twins" in problems[0]             # until the round ends: no retry


def test_a_rewrite_that_reports_no_durable_chunks_drops_the_old_ledger(room_server):
    """The review of 12/11 (2-ב): a disk written again by the pipe of today (udpcast --
    its reports carry no `durable`) kept the ledger of the write before it, and a later
    "continue" would have skipped chunks that are no longer there. Any accepted report
    about a disk from another round is evidence that it is being written now."""
    ctx = room_server["ctx"]
    _verified(ctx)
    wave = _wave(room_server)
    L.record(ctx.conn, round_id="ses_before", mac=CLONER1, serial="S1", size_bytes=SIZE, image_id="img_7f3a91",
             manifest=ctx.library.get("img_7f3a91"), durable=[{"part": 3, "ranges": [[0, 60]]}])
    L.record(ctx.conn, round_id="ses_before", mac=CLONER1, serial="S9", size_bytes=SIZE, image_id="img_7f3a91",
             manifest=ctx.library.get("img_7f3a91"), durable=[{"part": 3, "ranges": [[0, 60]]}])
    assert _progress(room_server, wave, [{"dev": "sda"}, {"dev": "sdb"}]).json()["ok"]    # no durable at all
    assert L.of_disk(ctx.conn, "S1", SIZE) == {}                       # written again: nothing to continue from
    assert L.of_disk(ctx.conn, "S9", SIZE)[3]["ranges"] == [(0, 60)]   # a disk this report is not about


def test_a_ledger_that_breaks_never_fails_the_report(room_server, monkeypatch):
    """The review of 12/11 (2-ג): the progress was already taken when the ledger is
    written. Anything it throws is logged -- never a 500 on a report that stands."""
    ctx = room_server["ctx"]
    _verified(ctx)
    wave = _wave(room_server)

    def broken(*args, **kwargs):
        raise RuntimeError("the ledger's disk is full")
    monkeypatch.setattr(L, "record", broken)
    answer = _progress(room_server, wave, [{"dev": "sda", "durable": [{"part": 3, "ranges": [[0, 5]]}]}])
    assert answer.status_code == 200 and answer.json()["ok"] is True, answer.text
    assert L.note_report(ctx, CLONER1, {"session_id": wave, "targets": [
        {"dev": "sda", "durable": [{"part": 3, "ranges": [[0, 5]]}]}]}) == [L.NOT_WRITTEN]


def test_a_write_that_failed_is_told_apart_from_a_report_that_was_refused(room_server, monkeypatch):
    """The reports are a numbered delta (stage 3): the server must know whether THIS
    one reached the ledger. `NOT_WRITTEN` in the answer = the write itself broke --
    the same report sent again may well be recorded, so the number must not move on.
    A refusal by name is not that: sending the same ranges again changes nothing."""
    ctx = room_server["ctx"]
    _verified(ctx)
    wave = _wave(room_server)
    good = {"dev": "sda", "durable": [{"part": 3, "ranges": [[0, 5]]}]}
    refused = L.note_report(ctx, CLONER1, {"session_id": wave, "targets": [
        {"dev": "sdz", "durable": [{"part": 3, "ranges": [[0, 5]]}]},             # a disk with no serial
        {"dev": "sda", "durable": [{"part": 3, "ranges": [[0, 500]]}]}]})         # past the partition's end
    assert len(refused) == 3 and L.NOT_WRITTEN not in refused        # two by name, and RETRY_LATER for sdz
    assert L.note_report(ctx, CLONER1, {"session_id": wave, "targets": [good]}) == []
    real = L.record

    def locked_on_the_second(*args, **kwargs):
        if kwargs["serial"] != "S1":
            raise sqlite3.OperationalError("database is locked")
        return real(*args, **kwargs)
    monkeypatch.setattr(L, "record", locked_on_the_second)
    mixed = L.note_report(ctx, CLONER1, {"session_id": wave, "targets": [
        {"dev": "sdz", "durable": [{"part": 3, "ranges": [[0, 5]]}]}, good, {"dev": "sdb", "durable": [
            {"part": 3, "ranges": [[0, 5]]}]}]})
    assert L.NOT_WRITTEN in mixed                    # one disk refused, one written, one broke: not written
