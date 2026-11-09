"""#1920 -- the console side of the "דיסק-און-קי" page: the tree node, the icon (option A, Nadav 07/11),
the one summary window, and the states the mockup draws.

The page is run for real under node (``usbstick.js`` with the few console.js helpers stubbed): what the
operator would read in each state is asserted on the HTML the page builds, not on source text.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "server" / "static"
NODE = shutil.which("node")
#: option A of the approved mockup (docs/design/mockups-1107b/1920-usb.html): a USB stick, connector and body.
ICON_A = '<rect x="7" y="10" width="10" height="11" rx="2"/><path d="M9.5 10V3.5h5V10M11 6.5h.01M13 6.5h.01"/>'


def _read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


# --- the tree, the icon, the includes -----------------------------------------------------------------


def test_the_node_is_in_the_infrastructure_group_admin_only_with_icon_a():
    html = _read("index.html")
    infra = html[html.index('id="infraTA"'):html.index('onclick="toggleInventoryGroup(this,\'adminTA\')"')]
    node = re.search(r'<div class="inventory-node"[^>]*data-page="usbstick"[^>]*>.*?</div>', infra, re.S)
    assert node, "no usbstick node under תשתית"
    assert "data-admin" in node.group(0) and "דיסק-און-קי" in node.group(0)
    assert ICON_A in node.group(0)
    assert infra.index('data-page="drivers"') < infra.index('data-page="usbstick"')     # as the mockup's tree


def test_the_icon_is_one_and_the_same_in_the_tree_the_page_header_and_the_device_card():
    js = _read("console.js")
    assert f"\"t-usb\": '{ICON_A}'" in js
    page = _read("usbstick.js")
    assert page.count('uiIcon("t-usb")') == 1 and 'icon: "t-usb"' in page      # device card + page header
    others = [m for m in re.findall(r'"t-[a-z]+": \'(.*?)\'', js) if m != ICON_A]
    assert ICON_A not in others, "another tree icon is the same shape as the stick"


def test_the_script_loads_after_console_js_with_the_same_version_as_every_include():
    html = _read("index.html")
    assert html.index('src="console.js?v=') < html.index('src="usbstick.js?v=')
    assert set(re.findall(r'\?v=(\d+\.\d+)"', html)) == {"12.96"}   # round/1107c: one value for every include


def test_the_page_is_registered_and_the_settings_download_row_moved_out():
    js = _read("console.js")
    assert re.search(r'usbstick: \{[^}]*render: \(\) => usbStickPage\(\)[^}]*load: \(\) => loadUsbStick\(\)', js)
    assert 'type: "usbclone"' not in js and "usbCloneRowHtml" not in js
    assert "function usbCloneSheet()" in js and "async function loadUsbClone()" in js     # the download itself stays


# --- the page, run under node --------------------------------------------------------------------------

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const esc = (x) => String(x == null ? "" : x).replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const calls = [];
const ctx = {
  esc, ltr: (t) => `<bdi>${esc(t)}</bdi>`, fmtDate: (t) => String(t).slice(0, 10), fmtClock: (t) => String(t).slice(11, 16),
  fmtBytes: (n) => (n / 1e9).toFixed(1) + " GB", uiIcon: (n) => `<svg data-icon="${n}"></svg>`,
  pagePlaceholder: () => "LOADING", isAdmin: () => true, refreshPage() {}, usbCloneSheet() {}, current: "usbstick",
  renderCurrent() {}, loadUsbClone: async () => {}, USB_CLONE: { available: true },
  toast: (m) => calls.push(["toast", m]), post: async (p, b) => { calls.push(["post", p, b]); return POST_RESULT; },
  api: async (p) => { calls.push(["api", p]); return API_RESULT; },
  sheet: (o) => { calls.push(["sheet", o]); SHEET = o; },
  UI: {
    objHeader: (o) => `<header name="${esc(o.name)}" icon="${esc(o.icon)}">${o.actions}</header>`,
    card: (o) => `<card title="${esc(o.title)}"><acts>${o.acts || ""}</acts><small>${esc(o.small || "")}</small>${o.body}</card>`,
    pill: (c, t) => `<pill ${c}>${esc(t)}</pill>`, status: (c, t) => `<st ${c}>${esc(t)}</st>`,
    kv: (rows) => rows.map(([k, v]) => `<kv>${esc(k)}=${v}</kv>`).join(""),
    note: (c, h) => `<note ${c}>${h}</note>`,
    datagrid: (o) => `<table cols="${o.columns.join("|")}">${o.rows.map((r) => "<tr>" + r.join("|") + "</tr>").join("")}</table>`,
  },
};
let API_RESULT = null, POST_RESULT = null, SHEET = null;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8") + "\n;this.__fns = {usbStickPage, usbStickSync, usbsPlanHtml, usbsPartialText};", ctx);
const state = JSON.parse(process.argv[3]);
(async () => {
  const out = {};
  vm.runInContext("USB_STICK = " + JSON.stringify(state.stick) + "; usbStickError = " + JSON.stringify(state.error || "") + ";", ctx);
  out.page = ctx.__fns.usbStickPage();
  if (state.plan) out.plan = ctx.__fns.usbsPlanHtml(state.plan);
  if (state.sync) {
    API_RESULT = state.stick; POST_RESULT = state.post || {state: "ok"};
    await ctx.__fns.usbStickSync();
    out.sheetOpened = !!SHEET;
    if (SHEET) { out.sheet = {title: SHEET.title, submit: SHEET.submitLabel, note: SHEET.note}; await SHEET.onSubmit({}); }
  }
  out.calls = calls.map((c) => c[0] === "sheet" ? ["sheet"] : c);
  console.log(JSON.stringify(out));
})();
"""

PLAN = {
    "device_id": "usb-0123", "digest": "abc",
    "to_server": {"new": 12, "already": 3, "rejected": [{"name": "clone-20261105-112000.json", "reason": "שדה serial אסור בסכמה"}],
                  "ignored": 0, "beyond_cap": 0, "total": 16},
    "to_stick": {"models": 8, "smart_version": "aaaaaaaaaaaa", "smart_on_stick": "bbbbbbbbbbbb", "smart_changed": True,
                 "files": ["rates.json"], "nothing_to_write": False},
    "nothing_to_do": False}
ROW = {"record_id": "r1", "recorded_at": "2026-11-06T18:44:00Z", "model": "HP EliteDesk 8000 SFF", "controller": "ide", "driver": "ata_piix",
       "bytes": 85_000_000_000, "copy_mbps": 55, "verify_mbps": 61, "result": "ok", "in_server": False, "imported_at": None}
CONNECTED = {"state": "connected", "reason": "", "notes": [], "read_error": None, "last_sync": None, "plan": PLAN, "rows": [ROW],
             "device": {"device_id": "usb-0123", "label": "IMGSRV_DATA", "fstype": "vfat", "size_bytes": 15_000_000_000}}


def run(tmp_path: Path, **state) -> dict:
    harness = tmp_path / "harness.cjs"
    harness.write_text(HARNESS, encoding="utf-8", newline="\n")
    out = subprocess.run([NODE, str(harness), str(STATIC / "usbstick.js"), json.dumps(state)], capture_output=True, text=True,
                         encoding="utf-8", stdin=subprocess.DEVNULL, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@requires_native(("node", NODE))
def test_a_connected_stick_shows_the_device_the_log_and_an_enabled_sync(tmp_path):
    page = run(tmp_path, stick=CONNECTED)["page"]
    assert 'name="דיסק-און-קי" icon="t-usb"' in page
    assert re.search(r'<button class="btn primary"[^>]*onclick="usbStickSync\(\)">סנכרן</button>', page)
    assert "הורדת קובץ ה-USB…" in page and "IMGSRV_DATA" in page and "usb-0123" in page
    assert "<b>12</b> רשומות חדשות, 3 שכבר בשרת" in page
    assert "HP EliteDesk 8000 SFF" in page and "IDE · ata_piix" in page and "חדש" in page
    assert "מחובר · לקריאה בלבד" in page
    assert "1 רשומה בהתקן אינה תקינה" in page and "שדה serial אסור בסכמה" in page
    assert "svg data-icon=\"t-usb\"" in page


@requires_native(("node", NODE))
def test_no_stick_disables_sync_and_says_what_to_do_including_the_vm_cases(tmp_path):
    none = {**CONNECTED, "state": "none", "device": None, "plan": None, "rows": []}
    page = run(tmp_path, stick=none)["page"]
    assert re.search(r'<button class="btn" type="button" disabled[^>]*>סנכרן</button>', page)
    assert "לא זוהה דיסק-און-קי עם התווית IMGSRV_DATA" in page and "בדיקה מחדש" in page
    assert "Hyper-V" in page and "ESXi" in page and "לא יזוהה" in page


@requires_native(("node", NODE))
def test_an_unsafe_ambiguous_or_unread_device_is_explained_not_called_not_connected(tmp_path):
    for st, why in (("unsafe", "אינו על אפיק USB"), ("ambiguous", "מחוברים 2"), ("unread", "לא הצלחתי לבדוק")):
        page = run(tmp_path, stick={**CONNECTED, "state": st, "device": None, "plan": None, "rows": [], "reason": why})["page"]
        assert why in page and "לא זוהה דיסק-און-קי" not in page and "disabled" in page


@requires_native(("node", NODE))
def test_a_request_that_failed_is_not_shown_as_no_stick(tmp_path):
    page = run(tmp_path, stick=None, error="שגיאה 503")["page"]
    assert "שגיאה 503" in page and "לא הצלחתי לקרוא את מצב הדיסק-און-קי" in page and "לא זוהה דיסק-און-קי" not in page


@requires_native(("node", NODE))
def test_text_from_the_stick_or_the_server_is_escaped(tmp_path):
    evil = "<img src=x onerror=alert(1)>"
    stick = json.loads(json.dumps(CONNECTED))
    stick["rows"][0]["model"] = evil
    stick["plan"]["to_server"]["rejected"][0]["reason"] = evil
    stick["last_sync"] = {"state": "ok", "at": "2026-11-07T08:31:00Z", "by": evil,
                          "to_server": {"imported": 1, "already": 0, "rejected": []},
                          "to_stick": {"error": evil}}
    got = run(tmp_path, stick=stick, plan=stick["plan"])
    assert "<img" not in got["page"] and "<img" not in got["plan"]


@requires_native(("node", NODE))
def test_the_summary_says_what_goes_each_way_and_what_is_not_touched(tmp_path):
    html = run(tmp_path, stick=CONNECTED, plan=PLAN)["plan"]
    for text in ("<b>12</b> רשומות חדשות ייקלטו", "3 כבר בשרת", "תידחה", "מה שהשרת מדד בעצמו לא נדרס",
                 "קצבים ל-<b>8</b> דגמי מחשב", "bbbbbbbbbbbb", "server-sync", "יומן שטח שלא נקלט עדיין בשרת לא נדרס",
                 "מידע על דיסקים (serial, SMART לפי דיסק) לא עובר בשום כיוון"):
        assert text in html, text


@requires_native(("node", NODE))
def test_sync_reads_the_stick_again_opens_one_window_and_posts_only_the_digest_it_showed(tmp_path):
    got = run(tmp_path, stick=CONNECTED, sync=True)
    assert got["sheetOpened"] and got["sheet"]["title"] == "סנכרון עם הדיסק-און-קי" and got["sheet"]["submit"] == "סנכרן"
    kinds = [c[0] for c in got["calls"]]
    assert kinds[:2] == ["api", "sheet"], "reads fresh, then asks once"
    assert kinds.count("sheet") == 1 and kinds.count("post") == 1, "one window, one approval"
    post = next(c for c in got["calls"] if c[0] == "post")
    assert post[1] == "/usb-stick/sync" and post[2] == {"digest": "abc", "confirm": True}


@requires_native(("node", NODE))
def test_nothing_to_sync_says_so_and_opens_no_window_and_posts_nothing(tmp_path):
    idle = json.loads(json.dumps(CONNECTED))
    idle["plan"]["nothing_to_do"] = True
    got = run(tmp_path, stick=idle, sync=True)
    assert not got["sheetOpened"] and not any(c[0] == "post" for c in got["calls"])
    assert any(c[0] == "toast" and "אין מה לסנכרן" in c[1] for c in got["calls"])


@requires_native(("node", NODE))
def test_the_last_sync_card_names_a_partial_result_and_a_failed_direction(tmp_path):
    base = {"state": "partial", "at": "2026-11-07T08:31:00Z", "by": "nadav",
            "to_server": {"imported": 11, "already": 3, "rejected": [{"name": "clone-x.json", "reason": "שדה serial אסור בסכמה"}]},
            "to_stick": {"skipped": False, "models": 8, "smart_version": "aaaaaaaaaaaa", "verified": True}}
    page = run(tmp_path, stick={**CONNECTED, "last_sync": base})["page"]
    assert "הושלם עם דחייה אחת" in page and "11</b> רשומות נקלטו" in page and "מנהל: nadav" in page
    failed = {**base, "to_stick": {"error": "הכתיבה להתקן נכשלה: boom"}}
    page = run(tmp_path, stick={**CONNECTED, "last_sync": failed})["page"]
    assert "הושלם חלקית: כיוון אחד נכשל" in page and "הכתיבה להתקן נכשלה: boom" in page


@requires_native(("node", NODE))
def test_the_log_has_a_link_column_between_the_controller_and_the_size_2694(tmp_path):
    # the approved mockup (1920-usb.html): ... | בקר | קישור | גודל | ...; the value is the stick's report field, read as it came
    def cells(link):
        stick = json.loads(json.dumps(CONNECTED))
        stick["rows"][0]["link_gbps"] = link
        page = run(tmp_path, stick=stick)["page"]
        cols = re.search(r'<table cols="([^"]*)"', page).group(1).split("|")
        row = re.search(r"<tr>(.*?)</tr>", page, re.S).group(1).split("|")
        return cols, row

    cols, row = cells(3.0)
    assert cols[2:5] == ["בקר", "קישור", "גודל"], cols
    assert row[3] == "<bdi>3 Gb/s</bdi>", row                       # JSON 3.0 reaches the page as the number 3
    assert cells(6.0)[1][3] == "<bdi>6 Gb/s</bdi>" and cells(1.5)[1][3] == "<bdi>1.5 Gb/s</bdi>"
    # null (the stick could not read a link) and a report from before the field are "not read", not a number and not 0
    assert cells(None)[1][3] == '<span class="muted">לא נקרא</span>'
    stick = json.loads(json.dumps(CONNECTED))
    stick["rows"][0].pop("link_gbps", None)
    assert re.search(r"<tr>(.*?)</tr>", run(tmp_path, stick=stick)["page"], re.S).group(1).split("|")[3]         == '<span class="muted">לא נקרא</span>'
