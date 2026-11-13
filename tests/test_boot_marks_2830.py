"""#2830 — סמני `up=` של ה-initramfs נשמרים ונראים בקונסולה, ולא רק ביומן uvicorn.

החוזה: אתחול תקין = שורה אחת ב-`boot_marks`; ערך פגום או פירור שנדחה = שום שורה (ולא שורה של אפסים);
נקודה שלא הושגה = NULL; אותו `agent-net` פעמיים באותו אתחול = שורה אחת.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from boot.http import record_step
from server import db
from server.agent import boottrace
from server.learn import boot_marks

from console_src import console_js
from native import requires_native
from test_machine_identity import MAC1, identity_server  # noqa: F401

NODE = shutil.which("node")

MAC = "78:ac:c0:9b:11:c2"
UP = "init:0.80,modules:2.10,nic:3.00,link:5.50,dhcp:18.40"


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _arrive(conn, raw_up, mac=MAC, step="agent-net"):
    """מה ש-`boot/http.py` עושה לבקשת פירור — עם הרושמים האמיתיים של השרת."""
    return record_step(lambda m, s: boottrace.record(conn, m, s), mac, step, "10.44.0.59",
                       raw_up=raw_up, marks_record=lambda m, marks: boot_marks.record(conn, m, marks))


def _rows(conn, mac=MAC):
    return conn.execute("SELECT * FROM boot_marks WHERE mac = ?", (mac,)).fetchall()


def test_one_boot_is_one_row_with_the_marks_as_measured(conn):
    assert _arrive(conn, UP) is True
    [row] = _rows(conn)
    assert (row["init_s"], row["modules_s"], row["nic_s"], row["link_s"], row["dhcp_s"]) \
        == (0.8, 2.1, 3.0, 5.5, 18.4)
    assert row["gui_s"] is None                  # לא הושג — NULL, לא 0


def test_history_gives_network_time_and_the_time_of_each_stage(conn):
    _arrive(conn, UP)
    [got] = boot_marks.history(conn, MAC)
    assert got["net_s"] == 18.4
    assert got["took"] == {"init": 0.8, "modules": 1.3, "nic": 0.9, "link": 2.5, "dhcp": 12.9}
    assert "gui" not in got["marks"]


def test_a_missing_mark_is_measured_from_the_one_before_it(conn):
    _arrive(conn, "init:1.00,link:5.00,dhcp:9.00")      # בלי modules ובלי nic
    [got] = boot_marks.history(conn, MAC)
    assert got["took"] == {"init": 1.0, "link": 4.0, "dhcp": 4.0}
    assert "nic" not in got["marks"]


def test_a_clock_that_went_backwards_is_unknown_not_negative(conn):
    _arrive(conn, "init:5.00,modules:2.00,dhcp:9.00")
    [got] = boot_marks.history(conn, MAC)
    assert got["took"] == {"init": 5.0, "modules": None, "dhcp": 7.0}


@pytest.mark.parametrize("raw", ["init:abc", "bogus:1.0", "init:1.0,init:2.0", "init:1;rm", "", "init:"])
def test_a_malformed_value_is_refused_and_never_stored_as_zero(conn, raw):
    assert _arrive(conn, raw) is True                   # הצעד עצמו נרשם
    assert conn.execute("SELECT step FROM boot_steps WHERE mac = ?", (MAC,)).fetchone()["step"] == "agent-net"
    assert _rows(conn) == []
    assert boot_marks.history(conn, MAC) == []


def test_a_step_that_was_not_recorded_leaves_no_marks(conn):
    assert record_step(lambda m, s: False, MAC, "agent-net", "10.44.0.59", raw_up=UP,
                       marks_record=lambda m, marks: boot_marks.record(conn, m, marks)) is False
    assert _rows(conn) == []


def test_a_step_without_marks_stores_nothing(conn):
    _arrive(conn, None)
    assert _rows(conn) == []


def test_the_same_agent_net_twice_is_one_row_and_a_new_boot_is_a_second(conn):
    _arrive(conn, UP)
    _arrive(conn, UP)                                   # שידור כפול של אותו אתחול
    assert len(_rows(conn)) == 1
    _arrive(conn, "init:0.90,modules:2.20,dhcp:19.00")
    assert [r["net_s"] for r in boot_marks.history(conn, MAC)] == [19.0, 18.4]


def test_rows_per_machine_are_capped_and_machines_do_not_share(conn, monkeypatch):
    monkeypatch.setattr(boot_marks, "KEEP", 3)
    for second in range(6):
        _arrive(conn, f"init:1.00,dhcp:{10 + second}.00")
    _arrive(conn, "init:1.00,dhcp:99.00", mac="78:ac:c0:9b:11:c3")
    assert [r["net_s"] for r in boot_marks.history(conn, MAC)] == [15.0, 14.0, 13.0]
    assert [r["net_s"] for r in boot_marks.history(conn, "78:ac:c0:9b:11:c3")] == [99.0]


def test_a_storage_failure_does_not_cost_the_boot_step(conn, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("db is down")
    monkeypatch.setattr(boot_marks, "learn_boot", type("B", (), {"version": staticmethod(broken)}))
    assert _arrive(conn, UP) is True
    assert boot_marks.record(conn, MAC, {"init": 1.0}) is False      # ולא חריגה לקורא
    assert _rows(conn) == []


def test_only_the_declared_marks_are_accepted(conn):
    assert boot_marks.record(conn, MAC, {}) is False
    assert boot_marks.record(conn, MAC, {"bogus": 1.0}) is False
    assert boot_marks.record(conn, MAC, {"init": 1.0, "bogus": 2.0}) is False
    assert _rows(conn) == []


def test_housekeeping_purges_old_rows(conn):
    from datetime import datetime, timedelta, timezone
    from server.system import housekeeping
    _arrive(conn, UP)
    conn.execute("UPDATE boot_marks SET at = ?", ((datetime.now(timezone.utc) - timedelta(days=200)).isoformat(),))
    conn.commit()
    assert housekeeping.purge_tables(conn)["deleted"].get("boot_marks") == 1


# --- מקצה לקצה: HTTP אמיתי → DB → הקונסולה -----------------------------------


def test_the_console_shows_the_boot_the_station_reported(identity_server):
    s = identity_server
    assert s["station"].get(f"/boot/step?mac={MAC1}&s=agent-net&up={UP}").status_code == 200
    got = s["admin"].get(f"/api/console/machines/{MAC1}/data").json()["boot_marks"]
    assert len(got) == 1 and got[0]["net_s"] == 18.4 and got[0]["marks"]["modules"] == 2.1


def test_a_stranger_cannot_plant_marks_for_a_station(identity_server):
    """אותה בדיקת זהות כמו של הפירור עצמו (#2411): פירור שנדחה אינו משאיר סמנים."""
    s = identity_server
    assert s["stranger"].get(f"/boot/step?mac={MAC1}&s=agent-net&up={UP}").status_code == 200
    assert s["admin"].get(f"/api/console/machines/{MAC1}/data").json()["boot_marks"] == []


def test_the_console_page_and_docs_know_the_new_data():
    root = Path(__file__).resolve().parent.parent
    js = (root / "server" / "static" / "console.js").read_text(encoding="utf-8")
    assert "bootMarksHtml(d.boot_marks)" in js
    doc = (root / "docs" / "architecture" / "interfaces.md").read_text(encoding="utf-8")
    assert "boot_marks" in doc and "#2830" in doc


@requires_native(("node", NODE))
def test_the_drawer_renders_network_time_stages_and_a_missing_network(tmp_path):
    js = console_js()
    pick = lambda pattern: re.search(pattern, js, re.M | re.S).group(0)      # noqa: E731
    probe = tmp_path / "probe.cjs"
    probe.write_text("""
const esc = (x) => String(x == null ? "" : x), fmtWhen = (x) => "T:" + x;
""" + pick(r"^const BOOT_MARK_HE = .*?;$") + "\n" + pick(r"^function bootMarksHtml\(.*?^\}") + """
console.log(JSON.stringify({
  none: bootMarksHtml(null), empty: bootMarksHtml([]),
  full: bootMarksHtml([{ at: "a", net_s: 18.4, marks: { init: 0.8, modules: 2.1, dhcp: 18.4 },
                         took: { init: 0.8, modules: 1.3, dhcp: 16.3 } }]),
  nonet: bootMarksHtml([{ at: "b", net_s: null, marks: { init: 0.8 }, took: { init: 0.8 } }]),
}));
""", encoding="utf-8", newline="\n")
    got = subprocess.run([NODE, str(probe)], capture_output=True, text=True, encoding="utf-8",
                         stdin=subprocess.DEVNULL, timeout=60)
    assert got.returncode == 0, got.stderr
    out = json.loads(got.stdout)
    assert "לא נקרא" in out["none"] and "לא נשמר אתחול" in out["empty"]
    assert "רשת עלתה אחרי" in out["full"] and "18.4" in out["full"] and "(+1.3)" in out["full"]
    assert "(+16.3)" in out["full"] and "nic" not in out["full"]        # נקודה שלא הושגה לא מוצגת
    assert "רשת לא נמדדה" in out["nonet"] and "(+" not in out["nonet"]
