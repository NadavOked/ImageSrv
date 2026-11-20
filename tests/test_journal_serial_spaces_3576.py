"""#3576 -- a disk whose serial holds spaces keeps its set tag in the console journal.

The APS-SL3N-512 of cloner 2 reports ``t10.ATA     APS-SL3N-512 ... SICSD40302WL``. The journal's
``_SERIAL`` was ``\S+`` and stopped at the first space (``t10.ATA``), so the set tag was never found --
silently. ``disk_failure`` writes the serial raw; ``disk_write_slow`` has always written it without
spaces, which is not the string the DB (and the set) holds. One normalisation now, ``disk_sets.serial_key``,
used by the writer and by ``text_of``; the DB is untouched.
"""

from __future__ import annotations

import pytest

from server import db
from server.console import journal_he
from server.disks import disk_failures, disk_sets
from server.library.images import ImageLibrary
from test_disk_failures import CABLE_LINES
from test_slow_write_journal_2956 import MAC, _one, _report, _session, _slow_target, _wave

#: 63 characters, the way the APS reports it (spaces inside, none at the ends).
APS = "t10.ATA     APS-SL3N-512" + " " * 27 + "SICSD40302WL"
TAG = "קוריאים · xx-7"


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _assign(conn, *pairs):
    set_id = disk_sets.create(conn, "קוריאים", "xx", 29, True, "t")
    disk_sets.assign(conn, set_id, [{"serial": s, "suffix": n} for s, n in pairs], "t")


def _say(conn, tmp_path, event, detail):
    lib = tmp_path / "images"
    lib.mkdir(exist_ok=True)
    return journal_he.JournalTranslator(conn, ImageLibrary(lib)).translate(event, detail)[1]


def test_the_fixture_serial_is_the_aps_shape():
    assert len(APS) <= 64 and " " in APS and not APS.startswith(" ") and not APS.endswith(" ")


def test_a_disk_failure_of_a_serial_with_spaces_shows_the_set_and_the_whole_serial(conn, tmp_path):
    _assign(conn, (APS, "7"))
    said = _say(conn, tmp_path, "disk_failure", f"{MAC} sdc serial={APS} port=3 cause=disk")
    assert said.endswith(f" · {TAG}"), said
    assert f"serial={APS} port=3" in said, said


def test_the_real_emitter_of_disk_failure_is_read_back_with_its_tag(conn, tmp_path):
    _assign(conn, (APS, "7"))
    conn.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds,"
                 " state, opened_by, created_at, last_join_at) VALUES ('ses_f', 'grp_CLONERS', 'img_x', 'p',"
                 " 1, 300, 'running', 't', ?, 0)", (db.now_iso(),))
    conn.commit()
    row = disk_failures.record(conn, "ses_f", MAC, {"dev": "sdc", "serial": APS, "port": 3,
                                                    "ata_port": 3, "ata_log": CABLE_LINES,
                                                    "state": "failed", "error": "write error"})
    assert row is not None
    detail = conn.execute("SELECT detail FROM journal WHERE event = 'disk_failure'").fetchone()["detail"]
    assert f"serial={APS} " in detail, detail
    assert _say(conn, tmp_path, "disk_failure", detail).endswith(f" · {TAG}")


def test_the_automatic_clearing_line_shows_the_set_too(conn, tmp_path):
    _assign(conn, (APS, "7"))
    said = _say(conn, tmp_path, "disk_failure_cleared",
                f"#4 serial={APS} " + disk_failures.AUTO_CLEARED_HE.format(round_id="ses_x"))
    assert said.endswith(f" · {TAG}"), said
    said = _say(conn, tmp_path, "disk_failure_cleared",
                f"#4 serial={APS} " + disk_failures.RETURNED_CLEARED_HE.format(session_id="ses_x") + " (sdb -> sdc)")
    assert f" · {TAG}" in said, said


def test_a_slow_write_of_a_serial_with_spaces_is_written_as_the_key_and_finds_the_set(conn, tmp_path):
    _assign(conn, (APS, "7"))
    sid = _session(conn)
    _report(conn, sid, _wave(_slow_target() | {"serial": APS}))
    row = _one(conn)
    assert f"serial={disk_sets.serial_key(APS)} port=" in row["detail"], row["detail"]
    assert " " not in row["detail"].split("serial=")[1].split(" port=")[0]
    # the DB's serial and the written one meet in ONE place
    assert disk_sets.text_of(disk_sets.all_labels(conn), disk_sets.serial_key(APS)) == TAG
    assert _say(conn, tmp_path, row["event"], row["detail"]).endswith(f" · {TAG}")


def test_a_serial_without_a_set_gets_no_tag_and_is_not_cut(conn, tmp_path):
    _assign(conn, ("S7", "7"))
    said = _say(conn, tmp_path, "disk_failure", f"{MAC} sdc serial={APS} port=3 cause=disk")
    assert "xx-" not in said and f"serial={APS} port=3" in said, said


def test_two_disks_that_differ_only_in_spaces_never_get_each_others_tag(conn, tmp_path):
    _assign(conn, ("AB C", "1"), ("A BC", "2"))
    labels = disk_sets.all_labels(conn)
    # the exact spelling still finds its own disk
    assert disk_sets.text_of(labels, "AB C") == "קוריאים · xx-1"
    assert disk_sets.text_of(labels, "A BC") == "קוריאים · xx-2"
    # the space-less key belongs to both: no tag at all, not the first one's
    assert disk_sets.text_of(labels, "ABC") is None
    said = _say(conn, tmp_path, "disk_write_slow", f"{MAC} sdc serial=ABC port=3 drawer=1 mbps=9 others=- model=? — x")
    assert "xx-" not in said, said


def test_serial_key_matches_the_db_cut_and_rejects_non_strings():
    long = "t10.ATA " + "x " * 40                                  # > 64 characters
    assert disk_sets.serial_key(long) == disk_sets.serial_key(long.strip()[:64])
    assert disk_sets.serial_key("  A  B ") == "AB"
    for bad in (None, 7, ["A"], "", "   "):
        assert disk_sets.serial_key(bad) is None
