"""#1884 — דיסק הספרייה בשרת בזמן גל וקליטה, שהלמידה לא תאשים את הרשת.

הראיה (20/10): ה-NVMe המשותף עמוס, השידור ב-15–50MB/s, המשכפל 95% idle. גל כזה מסומן `server_disk`: #1842 לא
לומד ממנו קצב, ו-#1846 לא מתריע "אבדן קפץ". #3365: ההחלטה לפי `diskstats` של ההתקן של הספרייה בלבד (util ו-await),
לא לפי PSI של כל המערכת (ב-LabRoot דיסק השורש הדליק אותו). התקן שלא נמדד — "לא נבדק", לא "תקין".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server import db
from server.rounds import drive_rates
from server.learn import server_io

MAC = "78:ac:c0:9b:18:84"
_seq = iter(range(10 ** 6))


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _proc(root: Path, read_sectors: int, write_sectors: int, io_ms: int, some_us, full_us,
          ios: int = 100, ios_ms: int = 18) -> Path:
    """`ios` בקשות קריאה שהסתיימו, ו-`ios_ms` מ"ש שכולן ביחד לקחו (שדות 4 ו-7 של שורת ה-diskstats)."""
    proc = root / "proc"
    (proc / "pressure").mkdir(parents=True, exist_ok=True)
    (proc / "diskstats").write_text(
        f"   8       0 sda 1 0 2 3 4 0 5 6 0 7 8\n"
        f" 259       1 nvme0n1p2 {ios} 0 {read_sectors} {ios_ms} 50 0 {write_sectors} 9 0 {io_ms} 99\n",
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


def _snap(at, dev="nvme0n1p2", r=0, w=0, io=0, some=0, full=0, rios=0, wios=0, ios_ms=0):
    return {"at": at, "device": dev, "read_sectors": r, "write_sectors": w, "io_ms": io,
            "read_ios": rios, "write_ios": wios, "ios_ms": ios_ms,
            "psi_some_us": some, "psi_full_us": full}


# --- המדידה ------------------------------------------------------------------


def test_the_measurement_is_rates_and_shares_of_the_stream_time():
    """400ש' זרם: 20GB נקראו (50MB/s), ההתקן עסוק 50% מהזמן ובקשה לוקחת 20ms — "server_disk"."""
    got = server_io.measure(_snap(1000), _snap(1500, r=20 * 10 ** 9 // 512, w=10 ** 9 // 512,
                                                io=250_000, some=300 * 10 ** 6, full=192 * 10 ** 6,
                                                rios=30_000, wios=10_000, ios_ms=800_000),
                            seconds=400)
    assert got == {"device": "nvme0n1p2", "seconds": 400, "read_mbps": 50.0, "write_mbps": 2.5,
                   "util": 0.5, "await_ms": 20.0, "psi_some": 0.75, "psi_full": 0.48, "basis": "server_disk"}


def test_a_quiet_library_disk_is_ok_and_an_unread_disk_is_unchecked_not_ok():
    calm = server_io.measure(_snap(0), _snap(100, io=1_000, rios=1000, ios_ms=1_000), seconds=100)
    assert calm["util"] == 0.01 and calm["await_ms"] == 1.0 and calm["basis"] == "ok"
    unread = server_io.measure(_snap(0, io=None), _snap(100, io=None, rios=1000, ios_ms=1_000))
    assert unread["util"] is None and unread["basis"] is None
    assert unread["read_mbps"] == 0.0                      # הסקטורים נקראו; ה-io_ticks לא
    no_requests = server_io.measure(_snap(0), _snap(100), seconds=100)     # הדיסק נקרא, ואיש לא פנה אליו
    assert no_requests["await_ms"] is None and no_requests["basis"] == "ok"


def test_the_system_wide_pressure_does_not_decide_but_the_library_disk_does():
    """#3365: PSI 90% על דיסק אחר (שורש, journald) והספרייה רגועה = "ok"; הספרייה איטית בלי PSI בכלל = "server_disk".
    LabRoot 15/11: PSI some 9-38% ודיסק הספרייה ב-util של 0.3% לפני p3."""
    loaded_elsewhere = server_io.measure(_snap(0), _snap(100, io=300, rios=5000, ios_ms=2500,
                                                         some=90 * 10 ** 6, full=90 * 10 ** 6), seconds=100)
    assert loaded_elsewhere["psi_full"] == 0.9 and loaded_elsewhere["basis"] == "ok"
    slow_disk = server_io.measure(_snap(0, some=None, full=None), _snap(100, io=40_000, rios=3500, ios_ms=40_000,
                                                                       some=None, full=None), seconds=100)
    assert slow_disk["psi_full"] is None and slow_disk["await_ms"] == 11.4
    assert slow_disk["util"] == 0.4 and slow_disk["basis"] == "server_disk"


def test_each_of_the_two_thresholds_alone_is_not_slow():
    """util גבוה עם בקשות מהירות (דיסק מהיר שעסוק), ו-await גבוה כשכמעט לא עסוק (בקשה בודדת איטית)."""
    busy_fast = server_io.measure(_snap(0), _snap(100, io=90_000, rios=500_000, ios_ms=100_000), seconds=100)
    assert busy_fast["util"] == 0.9 and busy_fast["await_ms"] == 0.2 and busy_fast["basis"] == "ok"
    idle_slow = server_io.measure(_snap(0), _snap(100, io=1_000, rios=5, ios_ms=100), seconds=100)
    assert idle_slow["util"] == 0.01 and idle_slow["await_ms"] == 20.0 and idle_slow["basis"] == "ok"


def test_a_device_that_changed_or_was_not_found_is_null_not_zero():
    moved = server_io.measure(_snap(0), _snap(100, dev="sda", r=10 ** 9))
    assert moved["read_mbps"] is None and moved["util"] is None and moved["device"] is None
    nas = server_io.measure(_snap(0, dev=None, r=None), _snap(100, dev=None, r=None))
    assert nas["read_mbps"] is None and nas["write_mbps"] is None
    # #3365: ספרייה שאין לה התקן אינה "תקינה", גם כש-PSI של המערכת נקרא ונמוך: "לא נמדד"
    assert nas["basis"] is None and nas["await_ms"] is None
    assert moved["basis"] is None


def test_the_proc_files_are_parsed(tmp_path):
    proc = _proc(tmp_path, 4000, 800, 1234, 77, 55)
    assert server_io._diskstats(proc, "nvme0n1p2") == {"read_sectors": 4000, "write_sectors": 800,
                                                       "io_ms": 1234, "read_ios": 100, "write_ios": 50,
                                                       "ios_ms": 18 + 9}
    assert server_io._psi(proc) == {"psi_some_us": 77, "psi_full_us": 55}
    assert server_io._diskstats(proc, "sdz")["read_sectors"] is None
    assert server_io._diskstats(proc, "sdz")["ios_ms"] is None
    assert server_io._psi(tmp_path / "nope") == {"psi_some_us": None, "psi_full_us": None}


# --- גל: התחלה וסוף, ומה שהלמידה עושה איתו --------------------------------------


def _wave(conn, rate="400m") -> str:
    """גל של מנוע החתיכות (#3173: היחיד), כפי ש-`decide` שומר אותו (`engine: chunk`). האבדן ברשת של גל כזה מול
    דיסק הספרייה נבדק ב-`test_chunk_netloss_3164` (`server_disk`)."""
    sid = f"ses_io{next(_seq):05d}"
    roster = [{"mac": MAC, "serial": f"{sid}-{k}", "model": "M"} for k in range(2)]
    conn.execute("INSERT INTO wave_rates (session_id, decision, at) VALUES (?,?,?)",
                 (sid, json.dumps({"max_bitrate": rate, "roster": roster, "engine": "chunk",
                                   "link": {"interface": "eth1"}}), db.now_iso()))
    conn.commit()
    return sid


def _bound(conn, tmp_path, sid, slow: bool) -> dict:
    """גל של 400ש': 40GB נקראו. `slow` = ההתקן עסוק ובקשה לוקחת 20ms (מול 1ms). ה-PSI גבוה בשני המקרים, בכוונה."""
    server_io.begin(conn, "wave", sid, tmp_path, proc=_proc(tmp_path, 0, 0, 0, 0, 0, ios=0, ios_ms=0))
    return server_io.end(conn, "wave", sid, tmp_path, seconds=400,
                         proc=_proc(tmp_path, 40 * 10 ** 9 // 512, 0, 100_000, 300 * 10 ** 6, 300 * 10 ** 6,
                                    ios=1000, ios_ms=20_000 if slow else 1_000))


def test_a_wave_the_server_slowed_is_marked_and_journaled(conn, tmp_path, device):
    sid = _wave(conn)
    got = _bound(conn, tmp_path, sid, True)
    assert got["basis"] == "server_disk" and got["read_mbps"] == 100.0
    assert server_io.bound(conn, sid) is True
    line = conn.execute("SELECT detail FROM journal WHERE event = 'server_disk_slow'").fetchone()
    assert line["detail"].startswith(f"wave {sid} read=100.0MB/s") and "util=100% await=20.0ms" in line["detail"]
    other = _wave(conn)
    _bound(conn, tmp_path, other, False)
    assert server_io.bound(conn, other) is False
    assert server_io.bound(conn, "ses_never_measured") is False      # לא נמדד ≠ מוגבל


def test_the_rate_learning_skips_a_server_bound_wave(conn, tmp_path, device):
    """#1842: הגל הקודם של המכונה שממנו הקצב נגזר אינו הגל שהשרת האט."""
    calm = _wave(conn)
    _bound(conn, tmp_path, calm, False)
    slow = _wave(conn, rate="300m")
    drives = [{"mac": MAC, "serial": "n1"}, {"mac": MAC, "serial": "n2"}]
    _, _, before = drive_rates._from_previous(conn, drives, 600_000_000, None)
    assert before["session_id"] == slow                       # לא נמדד — נלמד כרגיל
    _bound(conn, tmp_path, slow, True)
    _, _, after = drive_rates._from_previous(conn, drives, 600_000_000, None)
    assert after["session_id"] == calm


def test_the_health_line_names_the_slow_library_disk(conn, tmp_path, device, monkeypatch):
    assert server_io.health_checks(conn) == []
    _bound(conn, tmp_path, _wave(conn), True)
    [line] = server_io.health_checks(conn)
    assert line["state"] == "warn" and "דיסק הספרייה איטי — 100 MB/s בגל האחרון" in line["detail"]
    assert "עסוק 100% מהזמן, 20.0ms לבקשה" in line["detail"]
    sid = _wave(conn)                                    # ההתקן של הספרייה לא זוהה (למשל NFS): "לא נבדק"
    monkeypatch.setattr(server_io, "_device_of", lambda root, proc: None)
    server_io.begin(conn, "wave", sid, tmp_path, proc=_proc(tmp_path, 0, 0, 0, 0, 0))
    got = server_io.end(conn, "wave", sid, tmp_path, proc=_proc(tmp_path, 0, 0, 0, 0, 0))
    assert got["basis"] is None and server_io.bound(conn, sid) is False
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
    # #3173: the chunk engine's end of a wave -- the library disk first, then the rate learning that reads it
    end_at = app.index('server_io.end(conn, "wave", session_id, library.root, sender=outcome')
    assert end_at < app.index("chunk_rate.record(conn, session_id")
    cap = (root / "library" / "capture.py").read_text(encoding="utf-8")
    assert 'server_io.begin(ctx.conn, "capture", task_id' in cap
    assert 'server_io.end(ctx.conn, "capture", task_id' in cap
    doc = (root.parent / "docs" / "architecture" / "interfaces.md").read_text(encoding="utf-8")
    assert "server_io" in doc and "server_disk" in doc
