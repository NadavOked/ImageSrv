"""#2060 -- the notifications drawer said "הכול תקין" when /health was never read.

``topNotifications`` looped over ``Array.isArray(HEALTH) ? HEALTH : []``: a failed
/health (error, timeout, server not answering) left ``HEALTH = null``, the loop ran
on ``[]``, and with no failed machine the drawer showed "אין התראות" and a green
"הכול תקין" pill -- "not checked" folded into "checked, all fine" (principle 5).

The fix: an unread /health is a row of its own ("בדיקת הבריאות לא נקראה: <why>")
and a warning pill. The guard side: a /health that *was* read and is empty still
says "הכול תקין" -- the fix must not turn every drawer yellow.

Runs the real function from console.js under node, with the drawer captured.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


def _drawer(tmp_path: Path, health_js: str, error: str = "", machines_js: str = "[]") -> dict:
    js = (REPO / "server" / "static" / "console.js").read_text(encoding="utf-8")
    fn = re.search(r"^function topNotifications\(\) \{.*?^\}", js, re.M | re.S)
    assert fn, "topNotifications is gone from console.js"
    probe = tmp_path / "probe.cjs"
    probe.write_text("""
const esc = (x) => String(x);
const machineIsFailed = (m) => m.state === "failed", machineName = (m) => m.name || m.mac;
const healthStatusClass = (s) => (s === "bad" ? "err" : "warn"), healthStatusLabel = (s) => (s === "bad" ? "תקלה" : "אזהרה");
const UI = { pill: (cls, text) => JSON.stringify({ cls, text }) };
let OUT = null;
const openDrawer = (title, body, opts) => { OUT = { title, body, sub: opts.sub, pill: JSON.parse(opts.meta) }; };
""" + f"let MACHINES = {machines_js}, HEALTH = {health_js}, healthError = {json.dumps(error)};\n"
        + fn.group(0) + "\ntopNotifications();\nconsole.log(JSON.stringify(OUT));\n",
        encoding="utf-8", newline="\n")
    out = subprocess.run([NODE, str(probe)], capture_output=True, text=True, encoding="utf-8",
                         stdin=subprocess.DEVNULL, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@requires_native(("node", NODE))
def test_an_unread_health_is_not_all_fine(tmp_path):
    got = _drawer(tmp_path, "null", error="שגיאה 503")
    assert "בדיקת הבריאות לא נקראה: שגיאה 503" in got["body"], got
    assert "אין התראות" not in got["body"], got
    assert got["pill"]["cls"] == "warn" and got["pill"]["text"] != "הכול תקין", got


@requires_native(("node", NODE))
def test_health_not_loaded_yet_still_says_not_checked(tmp_path):
    got = _drawer(tmp_path, "null", error="")
    assert "בדיקת הבריאות לא נקראה" in got["body"], got
    assert got["pill"]["cls"] == "warn", got


@requires_native(("node", NODE))
def test_unread_health_next_to_a_failed_machine_lists_both(tmp_path):
    got = _drawer(tmp_path, "null", error="timeout",
                  machines_js='[{"mac": "aa", "name": "PC1", "state": "failed"}]')
    assert "PC1" in got["body"] and "בדיקת הבריאות לא נקראה: timeout" in got["body"], got
    assert got["pill"]["cls"] == "warn", got


@requires_native(("node", NODE))
def test_a_read_empty_health_is_still_all_fine(tmp_path):
    got = _drawer(tmp_path, "[]")
    assert "אין התראות" in got["body"], got
    assert got["pill"] == {"cls": "ok", "text": "הכול תקין"}, got


@requires_native(("node", NODE))
def test_a_read_health_warning_is_listed(tmp_path):
    got = _drawer(tmp_path, '[{"id": "disk", "label": "מקום פנוי", "state": "warn", "detail": "9%"}, '
                            '{"id": "dns", "label": "DNS", "state": "ok"}]')
    assert "מקום פנוי" in got["body"] and "DNS" not in got["body"], got
    assert "לא נקראה" not in got["body"], got
    assert got["pill"] == {"cls": "warn", "text": "פתוחות"}, got
