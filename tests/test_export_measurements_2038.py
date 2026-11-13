"""#2038 -- tools/export-measurements.py: numbers only, no identifiers.

The privacy test the issue asks for: a database full of identifiers (serials,
MACs, IPs, host names, the college's name) is exported, and none of them may be in
the output; and the guard that is behind that must refuse, not clean up, when an
identifier does get into a cell.
"""
from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from server import db

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "export-measurements.py"

SERIAL = "WD-WMC4M0123456"
MAC = "aa:bb:cc:dd:ee:01"
IP = "10.44.12.57"
HOST = "LABROOM-PC07"
COLLEGE = "Hamaginim College"
START = datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc)


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def make_db(path: Path, *, model: str = "WDC WD5000AAKX", state: str = "closed") -> Path:
    conn = db.connect(path)
    sid = "ses_secret_id_1"
    conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state,"
        " opened_by, created_at, last_join_at, started_at, closed_at)"
        " VALUES (?, 'grp_CLONERS', 'img_office365_" + COLLEGE.split()[0] + "', ?, 1, 300, ?, 'nadav', ?, 0, ?, ?)",
        (sid, HOST, state, _iso(START), _iso(START), _iso(START + timedelta(minutes=5, seconds=12))))
    conn.execute("INSERT INTO session_members (session_id, mac, state, done, updated_at)"
                 " VALUES (?, ?, 'done', 1, ?)", (sid, MAC, _iso(START)))
    conn.execute("INSERT INTO wave_rates (session_id, decision, at, stream, drops, delivered)"
                 " VALUES (?, '{}', ?, ?, ?, ?)",
                 (sid, _iso(START),
                  json.dumps({"sent_bytes": 19_500_000_000, "loss_blocks": 3, "stream_bps": 70_200_000,
                              "ended": "send_done"}),
                  json.dumps({MAC: {"rcvbuf": 2, "inerrors": 5, "failed_drawers": 0}}),
                  json.dumps({MAC: {"drawers": 2, "done": 2, "written_bytes": 39_000_000_000}})))
    for k, rate in enumerate((60_000_000, 64_000_000)):
        conn.execute("INSERT INTO drive_rates (serial, model, session_id, rate_bps, cap_bps, bytes, seconds, at)"
                     " VALUES (?, ?, ?, ?, 75000000, 19500000000, 300, ?)",
                     (f"{SERIAL}{k}", model, sid, rate, _iso(START)))
    conn.execute("INSERT INTO disk_inventory (serial, model, size_bytes, mac, port, first_seen, last_seen)"
                 " VALUES (?, ?, 500000000000, ?, 1, ?, ?)", (f"{SERIAL}0", model, MAC, _iso(START), _iso(START)))
    conn.commit()
    conn.close()
    return path


def run(db_path: Path, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(TOOL), "--db", str(db_path), "--out", str(out)],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL, encoding="utf-8")


def test_the_export_has_the_numbers_and_none_of_the_identifiers(tmp_path):
    path = make_db(tmp_path / "imagesrv.db")
    out = tmp_path / "m.csv"
    r = run(path, out)
    assert r.returncode == 0, r.stderr
    text = out.read_text(encoding="utf-8")
    for secret in (SERIAL, MAC, MAC.upper(), IP, HOST, COLLEGE, COLLEGE.split()[0], "ses_secret_id_1", "nadav",
                   "grp_CLONERS", "2026-10-25", "09:00"):
        assert secret.lower() not in text.lower(), f"{secret!r} leaked into the export"
    assert not re.search(r"(?i)\b[0-9a-f]{2}(:[0-9a-f]{2}){5}\b", text) and not re.search(r"\b\d+\.\d+\.\d+\.\d+\b", text)
    [row] = list(csv.DictReader(out.open(encoding="utf-8")))
    assert row["wave"] == "1" and row["month"] == "2026-10" and row["machines"] == "1"
    assert (row["drawers"], row["drawers_done"], row["drawers_failed"]) == ("2", "2", "0")
    assert row["duration_s"] == "312" and row["sent_gb"] == "19.5" and row["stream_mb_s"] == "70.2"
    assert row["loss_blocks"] == "3" and row["receive_drops"] == "7" and row["ended"] == "send_done"
    assert (row["drive_mb_s_min"], row["drive_mb_s_median"], row["drive_mb_s_max"]) == ("60.0", "62.0", "64.0")
    assert row["drive_models"] == "WDC WD5000AAKX"


def test_an_identifier_that_got_into_a_cell_stops_the_export_and_writes_nothing(tmp_path):
    """The negative control of the guard: a drive 'model' that carries the serial (some firmwares do)."""
    path = make_db(tmp_path / "imagesrv.db", model=f"WDC WD5000AAKX {SERIAL}0")
    out = tmp_path / "m.csv"
    r = run(path, out)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "contains an identifier this database knows" in r.stderr
    assert not out.exists(), "a file was written although the guard refused"


@pytest.mark.parametrize("model,what", [("switch-10.44.12.57", "MAC or an IP"), ("nic aa:bb:cc:dd:ee:99", "MAC or an IP")])
def test_an_address_in_any_cell_stops_the_export(tmp_path, model, what):
    path = make_db(tmp_path / "imagesrv.db", model=model)
    out = tmp_path / "m.csv"
    r = run(path, out)
    assert r.returncode == 1 and what in r.stderr, r.stderr
    assert not out.exists()


def test_nothing_to_export_is_a_refusal_and_not_an_empty_success(tmp_path):
    path = make_db(tmp_path / "imagesrv.db", state="failed")     # no closed wave
    out = tmp_path / "m.csv"
    r = run(path, out)
    assert r.returncode == 1 and "no closed multicast wave" in r.stderr
    assert not out.exists()


def test_a_database_that_cannot_be_read_is_exit_2(tmp_path):
    bad = tmp_path / "not-a-db.db"
    bad.write_text("this is not sqlite", encoding="utf-8")
    assert run(bad, tmp_path / "m.csv").returncode == 2
    assert run(tmp_path / "missing.db", tmp_path / "m.csv").returncode == 2


def test_the_export_never_writes_to_the_database(tmp_path):
    path = make_db(tmp_path / "imagesrv.db")
    before = path.read_bytes()
    assert run(path, tmp_path / "m.csv").returncode == 0
    assert path.read_bytes() == before
