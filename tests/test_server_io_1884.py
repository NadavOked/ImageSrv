"""‏#1884 — דיסק הספרייה בשרת ולחץ ה-I/O בזמן גל וקליטה, שהלמידה לא תאשים את הרשת.

הראיה (20/10): ‏psi full 48% על ה-NVMe המשותף, השידור ב-15–50MB/s, המשכפל 95% idle. גל כזה מסומן
‏`server_disk`: ‏#1842 לא לומד ממנו קצב, ו-#1846 לא מתריע "אבדן קפץ". ‏PSI שלא נקרא — "לא נבדק", לא "תקין".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server import db
from server.rounds import drive_rates
from server.learn import learn_netloss, server_io

MAC = "78:ac:c0:9b:18:84"
SENT = 19_500_000_000
BLOCKS = -(-SENT // drive_rates.UDPCAST_BLOCK)
_seq = iter(range(10 ** 6))


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _proc(root: Path, read_sectors: int, write_sectors: int, io_ms: int, some_us, full_us) -> Path:
    proc = root / "proc"
    (proc / "pressure").mkdir(parents=True, exist_ok=True)
    (proc / "diskstats").write_text(
        f"   8       0 sda 1 0 2 3 4 0 5 6 0 7 8\n"
        f" 259       1 nvme0n1p2 100 0 {read_sectors} 9 50 0 {write_sectors} 9 0 {io_ms} 99\n",
        encoding="ascii")
    if some_us is None:
        (proc / "pressure" / "io").unlink(missing_ok=True)
    else:
        (proc / "pressure" / "io").write_text(
            f"some avg10=0.00 avg60=0.00 avg300=0.00 total={some_us}\n"
            f"full avg10=0.00 avg60=0.00 avg300=0.00 total={full_us}\n", encoding="ascii")
    return proc


@pytest.fixture()
def device(monkeypatch):
    monkeypatch.setattr(server_io, "_device_of", lambda root, proc: "nvme0n1p2")


def _snap(at, dev="nvme0n1p2", r=0, w=0, io=0, some=0, full=0):
    return {"at": at, "device": dev, "read_sectors": r, "write_sectors": w, "io_ms": io,
            "psi_some_us": some, "psi_full_us": full}


# --- המדידה ------------------------------------------------------------------


def test_the_measurement_is_rates_and_shares_of_the_stream_time():
    """‏400ש' זרם: ‏20GB נקראו (50MB/s), השרת כולו חיכה 192ש' (48%) — "server_disk"."""
    got = server_io.measure(_snap(1000), _snap(1500, r=20 * 10 ** 9 // 512, w=10 ** 9 // 512,
                                                io=250_000, some=300 * 10 ** 6, full=192 * 10 ** 6),
                            seconds=400)
    assert got == {"device": "nvme0n1p2", "seconds": 400, "read_mbps": 50.0, "write_mbps": 2.5,
                   "util": 0.5, "psi_some": 0.75, "psi_full": 0.48, "basis": "server_disk"}


def test_a_quiet_server_is_ok_and_an_unread_pressure_is_unchecked_not_ok():
    calm = server_io.measure(_snap(0), _snap(100, full=2 * 10 ** 6), seconds=100)
    assert calm["psi_full"] == 0.02 and calm["basis"] == "ok"
    unread = server_io.measure(_snap(0, some=None, full=None), _snap(100, some=None, full=None))
    assert unread["psi_full"] is None and unread["basis"] is None
    assert unread["read_mbps"] == 0.0                      # הדיסק נקרא; רק ה-PSI לא


def test_a_device_that_changed_or_was_not_found_is_null_not_zero():
    moved = server_io.measure(_snap(0), _snap(100, dev="sda", r=10 ** 9))
    assert moved["read_mbps"] is None and moved["util"] is None and moved["device"] is None
    nas = server_io.measure(_snap(0, dev=None, r=None), _snap(100, dev=None, r=None))
    assert nas["read_mbps"] is None and nas["write_mbps"] is None


def test_the_proc_files_are_parsed(tmp_path):
    proc = _proc(tmp_path, 4000, 800, 1234, 77, 55)
    assert server_io._diskstats(proc, "nvme0n1p2") == {"read_sectors": 4000, "write_sectors": 800,
                                                       "io_ms": 1234}
    assert server_io._psi(proc) == {"psi_some_us": 77, "psi_full_us": 55}
    assert server_io._diskstats(proc, "sdz")["read_sectors"] is None
    assert server_io._psi(tmp_path / "nope") == {"psi_some_us": None, "psi_full_us": None}


# --- גל: התחלה וסוף, ומה שהלמידה עושה איתו --------------------------------------


def _wave(conn, loss: float, rate="400m") -> str:
    sid = f"ses_io{next(_seq):05d}"
    roster = [{"mac": MAC, "serial": f"{sid}-{k}", "model": "M"} for k in range(2)]
    conn.execute("INSERT INTO wave_rates (session_id, decision, at) VALUES (?,?,?)",
                 (sid, json.dumps({"max_bitrate": rate, "roster": roster,
                                   "link": {"interface": "eth1"}}), db.now_iso()))
    conn.commit()
    drive_rates.record_stream(conn, sid, {"sent_bytes": SENT, "loss_blocks": round(BLOCKS * loss),
                                          "wall_s": 400, "ended": "send_done",
                                          "dropped_clients": 0, "timeouts": 10})
    return sid


def _bound(conn, tmp_path, sid, full_us) -> dict:
    server_io.begin(conn, "wave", sid, tmp_path, proc=_proc(tmp_path, 0, 0, 0, 0, 0))
    return server_io.end(conn, "wave", sid, tmp_path, seconds=400,
                         proc=_proc(tmp_path, 40 * 10 ** 9 // 512, 0, 100_000, full_us, full_us))


def test_a_wave_the_server_slowed_is_marked_and_journaled(conn, tmp_path, device):
    sid = _wave(conn, 0.004)
    got = _bound(conn, tmp_path, sid, 192 * 10 ** 6)
    assert got["basis"] == "server_disk" and got["read_mbps"] == 100.0
    assert server_io.bound(conn, sid) is True
    line = conn.execute("SELECT detail FROM journal WHERE event = 'server_disk_slow'").fetchone()
    assert line["detail"].startswith(f"wave {sid} read=100.0MB/s") and "psi_full=48%" in line["detail"]
    other = _wave(conn, 0.004)
    _bound(conn, tmp_path, other, 10 ** 6)
    assert server_io.bound(conn, other) is False
    assert server_io.bound(conn, "ses_never_measured") is False      # לא נמדד ≠ מוגבל


def test_netloss_does_not_blame_the_network_for_a_server_bound_wave(conn, tmp_path, device):
    for loss in (0.004, 0.005, 0.006):
        _bound(conn, tmp_path, _wave(conn, loss), 10 ** 6)
    slow = _wave(conn, 0.03)                                  # פי 6 מהבסיס
    _bound(conn, tmp_path, slow, 192 * 10 ** 6)
    assert learn_netloss.evaluate(conn, slow)["state"] == "server_disk"
    assert conn.execute("SELECT 1 FROM journal WHERE event = 'net_loss_jump'").fetchone() is None
    # ‏ולא נכנס לבסיס של הגל הבא
    nxt = _wave(conn, 0.005)
    _bound(conn, tmp_path, nxt, 10 ** 6)
    got = learn_netloss.assess(conn, nxt)
    assert got["state"] == "ok" and got["waves"] == 3      # שלושת הרגועים, בלי האיטי
    [line] = [c for c in learn_netloss.health_checks(conn)]
    assert line["state"] == "ok"


def test_the_rate_learning_skips_a_server_bound_wave(conn, tmp_path, device):
    """‏#1842: הגל הקודם של המכונה שממנו הקצב נגזר אינו הגל שהשרת האט."""
    calm = _wave(conn, 0.0)
    _bound(conn, tmp_path, calm, 10 ** 6)
    slow = _wave(conn, 0.0, rate="300m")
    drives = [{"mac": MAC, "serial": "n1"}, {"mac": MAC, "serial": "n2"}]
    _, _, before = drive_rates._from_previous(conn, drives, 600_000_000, None)
    assert before["session_id"] == slow                       # לא נמדד — נלמד כרגיל
    _bound(conn, tmp_path, slow, 192 * 10 ** 6)
    _, _, after = drive_rates._from_previous(conn, drives, 600_000_000, None)
    assert after["session_id"] == calm


def test_the_health_line_names_the_slow_library_disk(conn, tmp_path, device):
    assert server_io.health_checks(conn) == []
    _bound(conn, tmp_path, _wave(conn, 0.0), 192 * 10 ** 6)
    [line] = server_io.health_checks(conn)
    assert line["state"] == "warn" and "דיסק הספרייה איטי — 100 MB/s בגל האחרון" in line["detail"]
    assert "48%" in line["detail"]
    sid = _wave(conn, 0.0)
    server_io.begin(conn, "wave", sid, tmp_path, proc=_proc(tmp_path, 0, 0, 0, None, None))
    server_io.end(conn, "wave", sid, tmp_path, proc=_proc(tmp_path, 0, 0, 0, None, None))
    [line] = server_io.health_checks(conn)
    assert line["state"] == "off" and line["detail"].startswith("לא נבדק")


def test_an_end_without_a_beginning_is_nothing_and_failures_never_raise(conn, tmp_path, monkeypatch):
    assert server_io.end(conn, "wave", "ses_x", tmp_path, proc=tmp_path) is None
    monkeypatch.setattr(server_io, "snapshot", lambda *a, **k: 1 / 0)
    server_io.begin(conn, "wave", "ses_y", tmp_path)              # אינו זורק
    assert conn.execute("SELECT COUNT(*) FROM server_io").fetchone()[0] == 0


# --- החיבורים ----------------------------------------------------------------


def test_the_wave_and_the_capture_are_wired():
    root = Path(__file__).resolve().parent.parent / "server"
    app = (root / "app.py").read_text(encoding="utf-8")
    assert 'server_io.begin(conn, "wave", session["id"]' in app
    end_at = app.index('server_io.end(conn, "wave", session_id')
    assert app.index("drive_rates.record_stream(conn, session_id, outcome)") < end_at \
        < app.index("learn_netloss.evaluate(conn, session_id)")
    cap = (root / "library" / "capture.py").read_text(encoding="utf-8")
    assert 'server_io.begin(ctx.conn, "capture", task_id' in cap
    assert 'server_io.end(ctx.conn, "capture", task_id' in cap
    doc = (root.parent / "docs" / "interfaces.md").read_text(encoding="utf-8")
    assert "server_io" in doc and "server_disk" in doc
