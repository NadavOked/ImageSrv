"""Exercise the operator-facing journal, alert drawer and confirmation copy."""

import re
import shutil
import subprocess
from pathlib import Path

from console_src import console_js
from native import requires_native

REPO = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@requires_native(("node", NODE))
def test_console_keeps_system_diagnostics_under_technical_details():
    source = console_js()
    names = (
        "esc", "isRawTechnical", "isSystemDiagnostic", "technicalDetails", "journalDiagnostic",
        "logRowHtml", "openLogDetail", "topNotifications", "poweroffRoom",
    )
    pattern = r"^function (" + "|".join(names) + r")\([^\n]*\n.*?^}"
    blocks = [match[0] for match in re.finditer(pattern, source, re.M | re.S)]
    escape_map = re.search(r"^const ESC_MAP = .*", source, re.M)[0]
    probe = r"""
const assert = require("node:assert/strict");
const ltrRuns = esc;
const journalCls = () => "err";
const isToday = () => true, fmtDate = () => "", fmtHour = () => "";
const healthStatusClass = () => "warn", healthStatusLabel = () => "אזהרה";
const LOG_SEV_HE = {}, LOG_SEV_SHORT = {};
const UI = {pill: () => "", status: () => "", kv: (pairs) => pairs.map((p) => p[1]).join("")};
let drawer;
const openDrawer = (_title, body) => { drawer = body; };
const MACHINES = [];
const GROUPS = [{id: "grp_CLONERS", label: "חדר המשכפלים"}], isAdmin = () => true;
const impactList = (items) => items.map((item) => item[1]).join(" ");
let dialog;
const sheet = (options) => { dialog = options; };
const HEALTH = [
  {state: "warn", label: "גיבוי", detail: "unavailable <disk>"},
  {state: "warn", label: "שעון", detail: "השעון עדיין לא מסונכרן"},
];
const raw = "לא הצלחנו לפתוח את מסד הנתונים: [Errno 13] Permission denied: '<db>'";
const LOG = {rows: [{label: "שגיאת שרת", severity: "error", event: "server_error", text: raw}]};
"""
    assertions = r"""
const row = logRowHtml(LOG.rows[0], 0).cells[0];
assert.match(row, /שגיאת מערכת/);
assert.doesNotMatch(row, /Errno|Permission denied/);
assert.match(logRowHtml({text: "נכתב לספרייה images", event: "capture_done"}, 0).cells[0], /נכתב לספרייה images/);
openLogDetail(0);
assert.match(drawer, /<details[^>]*><summary>פרטים טכניים<\/summary><code dir="ltr">.*Permission denied: &#39;&lt;db&gt;&#39;<\/code><\/details>/);
topNotifications();
assert.match(drawer, /<details[^>]*><summary>פרטים טכניים<\/summary><code dir="ltr">unavailable &lt;disk&gt;<\/code><\/details>/);
assert.match(drawer, /השעון עדיין לא מסונכרן/);
poweroffRoom("grp_CLONERS");
assert.match(dialog.note, /כשידווח שוב לשרת/);
assert.doesNotMatch(dialog.note, /hello/);
assert.equal(dialog.danger, true);
"""
    result = subprocess.run([NODE, "-"], cwd=REPO, input="\n".join([escape_map, *blocks, probe, assertions]),
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr


def test_a_persisted_round_event_keeps_its_id_and_localizes_its_counts():
    from server.console.journal_he_detail import he_detail

    assert he_detail("room_wave", "round_1 wave=2 written=3/8") == "round_1 גל 2 נכתבו 3 מתוך 8"
