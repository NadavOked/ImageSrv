"""#2956 -- דיסק שנפסל כאיטי בבדיקת הכתיבה שלפני הזרם נרשם ביומן, בעברית, בשמו.

11/11 בעבודה: 96MB/s מול 209 -- הפסילה הייתה רק ב-`targets_json`. הסוכן מכריע ומדווח
`targets[].write_preflight.state = "slow"` (כשל שהוא כשל הדיסק: `failed`, נרשם ב-`disk_failures`);
השרת מעתיק ליומן פעם אחת במעבר, והתרגום קורא את השורה בעברית.
"""

from __future__ import annotations

import json

import pytest

from server import db
from server.agent import reports
from server.console import journal_he
from server.library.images import ImageLibrary

MAC = "78:ac:c0:9b:29:56"
MODEL = "WDC WD5000AAKX-001CA0"
RELATIVE = "איטי — 96 MB/s, פחות מחצי מ-209 MB/s של שאר המגירות מאותו סוג (מכני)"
FLOOR = "איטי — 40 MB/s, מתחת לסף 50 MB/s"


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


#: #2945: מספר המגירה שהמסך מציג הוא מיפוי המכונה (`drawer_slots`), לא ה-port. כאן port 3 = מגירה 1.
SLOTS = ["sata:3", "sata:1", "sata:2"]


def _session(conn, sid="ses_slow", slots=SLOTS):
    if slots is not None:
        conn.execute("INSERT INTO machines (mac, suffix, group_id, added_at, drawer_slots) VALUES (?, '07', 'grp_CLONERS', 'x', ?)",
                     (MAC, json.dumps(slots)))
    conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds,"
        " state, opened_by, created_at, last_join_at, started_at)"
        " VALUES (?, 'grp_CLONERS', 'img_x', 'p', 1, 300, 'running', 't', ?, 0, ?)",
        (sid, db.now_iso(), db.now_iso()))
    conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at)"
                 " VALUES (?, ?, 'writing', ?)", (sid, MAC, db.now_iso()))
    conn.commit()
    db.net_seen(conn, MAC, "10.0.0.7", disks_json=json.dumps([
        {"dev": "sda", "serial": "SER-A", "port": 1, "model": "Samsung SSD 870"},
        {"dev": "sdb", "serial": "SER-B", "port": 2, "model": "Samsung SSD 870"},
        {"dev": "sdc", "serial": "WD-WCC2E0123", "port": 3, "model": MODEL}]))
    return sid


def _t(dev, state, pre=None):
    t = {"dev": dev, "state": state, "bytes_written": 0, "bytes_total": 1}
    if pre is not None:
        t["write_preflight"] = pre
    return t


def _ok(mbps):
    return {"state": "ok", "reason": "", "mbps": mbps}


def _slow_target(mbps=96, reason=RELATIVE):
    return _t("sdc", "failed", {"state": "slow", "reason": reason, "mbps": mbps}) | {"error": reason}


def _wave(slow=None):
    return [_t("sda", "writing", _ok(209)), _t("sdb", "writing", _ok(211)), slow or _slow_target()]


def _report(conn, sid, targets, state="writing"):
    assert reports.ingest(conn, {"session_id": sid, "mac": MAC, "state": state, "targets": targets})["ok"]


def _rows(conn):
    return conn.execute("SELECT * FROM journal WHERE event = 'disk_write_slow'").fetchall()


def _one(conn):
    rows = _rows(conn)
    assert len(rows) == 1, f"expected one disk_write_slow line, got {len(rows)}: {[r['detail'] for r in rows]}"
    return rows[0]


def _say(conn, tmp_path, row):
    lib = tmp_path / "images"
    lib.mkdir(exist_ok=True)
    return journal_he.JournalTranslator(conn, ImageLibrary(lib)).translate(row["event"], row["detail"])


def test_a_disk_failed_as_slow_is_journaled_with_everything_the_operator_needs(conn, tmp_path):
    sid = _session(conn)
    _report(conn, sid, _wave())
    row = _one(conn)
    assert row["machine"] == MAC and row["round_id"] == sid
    label, said = _say(conn, tmp_path, row)
    assert "איטי" in label
    for must in ("מגירה 1", MODEL, "WD-WCC2E0123", "נמדד 96 MB/s", "פחות מחצי מ-209 MB/s",
                 "שאר המגירות שנמדדו בגל: 209, 211 MB/s", "נפסלה", "ולא נכתבה"):
        assert must in said, (must, said)


def test_the_threshold_case_says_the_floor(conn, tmp_path):
    sid = _session(conn)
    _report(conn, sid, _wave(_slow_target(40, FLOOR)))
    row = _one(conn)
    said = _say(conn, tmp_path, row)[1]
    assert "נמדד 40 MB/s, מתחת לסף 50 MB/s" in said, said


def test_the_same_verdict_reported_every_two_seconds_is_one_line(conn):
    sid = _session(conn)
    for _ in range(4):
        _report(conn, sid, _wave())
    assert len(_rows(conn)) == 1
    _report(conn, sid, _wave(), state="done")      # גם הדיווח הסופי
    assert len(_rows(conn)) == 1


def test_only_the_slow_disk_is_named_and_others_that_passed_are_not_journaled(conn):
    sid = _session(conn)
    _report(conn, sid, _wave())
    row = _one(conn)
    assert " sdc " in row["detail"] and " sda " not in row["detail"] and "SER-A" not in row["detail"]


@pytest.mark.parametrize("pre", [
    {"state": "ok", "reason": "", "mbps": 96},                          # מהיר מספיק
    {"state": "failed", "reason": "dd: error writing", "mbps": None},   # כשל כתיבה, לא איטי
    {"state": "unchecked", "reason": "לא נבדק: ...", "mbps": None},
    "slow", None, ["slow"], {"mbps": 96}, {"state": ["slow"]},          # פגום -- לא נקרא, לא נרשם, לא נופל
])
def test_anything_but_slow_is_not_journaled(conn, pre):
    sid = _session(conn)
    _report(conn, sid, [_t("sdc", "failed", pre)])
    assert _rows(conn) == []


def test_an_old_agent_without_write_preflight_is_not_journaled_and_does_not_crash(conn):
    sid = _session(conn)
    _report(conn, sid, [_t("sdc", "writing")])
    assert _rows(conn) == []


def test_another_slow_disk_in_the_wave_is_not_listed_among_the_others(conn):
    sid = _session(conn)
    other_slow = _t("sdb", "failed", {"state": "slow", "reason": FLOOR, "mbps": 40})
    _report(conn, sid, [_t("sda", "writing", _ok(209)), other_slow, _slow_target()])
    rows = {r["detail"].split()[1]: r["detail"] for r in _rows(conn)}
    assert set(rows) == {"sdb", "sdc"}
    assert "others=209 " in rows["sdc"] and "others=209 " in rows["sdb"], rows


def test_a_wave_with_no_other_measured_disk_says_so(conn, tmp_path):
    sid = _session(conn)
    _report(conn, sid, [_slow_target(40, FLOOR)])
    said = _say(conn, tmp_path, _one(conn))[1]
    assert "אף מגירה אחרת לא נמדדה בגל" in said, said


def test_the_drawer_number_is_the_screens_not_the_ata_port(conn, tmp_path):
    """#2945: port 3 ב-`SLOTS` הוא מגירה 1 -- המספר שהמפעיל רואה על מסך המשכפל. "מגירה 3" כאן היא הבאג."""
    sid = _session(conn)
    _report(conn, sid, _wave())
    said = _say(conn, tmp_path, _one(conn))[1]
    assert "מגירה 1" in said and "מגירה 3" not in said, said
    assert "port=3 drawer=1 " in _one(conn)["detail"]      # הגולמי נושא את שניהם


def test_the_default_mapping_is_drawer_k_equals_sata_k(conn, tmp_path):
    sid = _session(conn, slots=None)
    conn.execute("INSERT INTO machines (mac, suffix, group_id, added_at) VALUES (?, '07', 'grp_CLONERS', 'x')", (MAC,))
    conn.execute("UPDATE machines SET drawer_count = 4 WHERE mac = ?", (MAC,))
    conn.commit()
    _report(conn, sid, _wave())
    assert "מגירה 3" in _say(conn, tmp_path, _one(conn))[1]


@pytest.mark.parametrize("slots", [None, ["sata:1", "sata:2"]])    # מכונה לא רשומה / החריץ אינו בין המגירות
def test_no_drawer_mapping_says_disk_without_a_number(conn, tmp_path, slots):
    sid = _session(conn, slots=slots)
    _report(conn, sid, _wave())
    label, said = _say(conn, tmp_path, _one(conn))
    assert "מגירה" not in said.split(" — ")[0], said            # הכותרת (מי נפסל) בלי מספר מומצא
    assert " · דיסק נפסל כאיטי" in said and MODEL in said and "WD-WCC2E0123" in said, said
    assert "drawer=? " in _one(conn)["detail"]


def test_a_disk_the_hello_never_described_says_what_it_does_not_know(conn, tmp_path):
    sid = _session(conn)
    conn.execute("UPDATE net_devices SET disks_json = '[]' WHERE mac = ?", (MAC,))
    conn.commit()
    _report(conn, sid, [_slow_target()])
    said = _say(conn, tmp_path, _one(conn))[1]
    assert "דיסק נפסל" in said and "דגם לא דווח" in said and "סיריאל לא דווח" in said and "מגירה" not in said.split(" — ")[0], said


def test_the_event_is_red_and_has_a_hebrew_label():
    assert "disk_write_slow" in journal_he._SEVERITY_ERR
    assert journal_he.EVENTS_HE.get("disk_write_slow", "disk_write_slow") != "disk_write_slow"
