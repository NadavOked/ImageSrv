"""#2900/#2903/#2921: תצוגת מספרים, ניסוח, תאריך וניגודיות."""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LRI, PDI = "\u2066", "\u2069"


def _t(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def test_native_drawer_reasons_isolate_amounts_at_render_time():
    bay = _t("native-gui/src/lcbay.c")
    assert re.search(r"Text tl = lc_reason_text\(.*g->line,", bay)
    assert "lc_reason_text(cr, 12, 400, why, avail)" in _t("native-gui/src/screens_standby.c")
    assert "lc_reason_text(cr, 13, 400, li[i]," in _t("native-gui/src/screens_lcrun.c")
    pattern = bay.split("g_regex_new(", 1)[1].split(", G_REGEX_OPTIMIZE", 1)[0]
    pattern = "".join(ast.literal_eval(line.strip()) for line in pattern.splitlines() if line.strip())
    replacement = re.search(r'g_regex_replace\(amount, why, -1, 0, ("[^"]+")', bay)
    assert replacement
    replacement = ast.literal_eval(replacement[1]).encode("latin-1").decode()
    assert re.sub(pattern, replacement, "מחיצה 3: שגיאת כתיבה אחרי 91.6 GB\u200f — שגיאת קלט/פלט") == (
        f"מחיצה 3: שגיאת כתיבה אחרי {LRI}91.6 GB{PDI}\u200f — שגיאת קלט/פלט"
    )
    assert re.sub(pattern, replacement, "איטי — 33 MB/s, מתחת לסף 50 MB/s") == (
        f"איטי — {LRI}33 MB/s{PDI}, מתחת לסף {LRI}50 MB/s{PDI}"
    )


def test_console_production_row_isolates_each_number_with_its_unit():
    result = subprocess.run(["node", "-"], cwd=REPO, capture_output=True, input=r"""
const {readFileSync} = require('node:fs');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const consoleJs = readFileSync('server/static/console.js', 'utf8');
const ctx = vm.createContext({esc: String, bayChip: () => ['green', ''], drawerPulled: () => false});
vm.runInContext(consoleJs.slice(consoleJs.indexOf('const DRAWER_ERROR_HE'), consoleJs.indexOf('const DRAWER_STATE_HE')), ctx);
vm.runInContext(readFileSync('server/static/console-prodline.js', 'utf8'), ctx);
const display = (error, error_short) => ctx.plRow({}, {disks: []}, {}, {live: true, disk: {state: 'failed', error, error_short}})[3];
const slow = 'איטי — 33 MB/s, מתחת לסף 50 MB/s';
assert.equal(ctx.drawerErrorText(slow), slow);
assert.equal(display(slow), 'איטי — \u206633 MB/s\u2069, מתחת לסף \u206650 MB/s\u2069');
const peer = 'איטי — 12 MB/s, פחות מחצי מ-80 MB/s של שאר המגירות מאותו סוג (מכני)';
assert.equal(display(peer, peer), 'איטי — \u206612 MB/s\u2069, פחות מחצי מ-\u206680 MB/s\u2069 של שאר המגירות מאותו סוג (מכני)');
const hdd = 'איטי — דיסק מכני (HDD), נמדד 15 MB/s: נתמך כמקור בלבד';
assert.equal(display(hdd, hdd), 'איטי — דיסק מכני (HDD), נמדד \u206615 MB/s\u2069: נתמך כמקור בלבד');
const local = 'מחיצה 3: שגיאת כתיבה אחרי 91.6 GB\u200f — שגיאת קלט/פלט';
assert.equal(display(local), 'מחיצה 3: שגיאת כתיבה אחרי \u206691.6 GB\u2069\u200f — שגיאת קלט/פלט');
assert.equal(display('תקלה בדיסק של 100 GB'), 'תקלה בדיסק של 100 GB');
""".encode())
    assert result.returncode == 0, result.stderr.decode()


def test_standby_line_and_machine_gender_say_it_to_the_operator():
    sb = _t("native-gui/src/screens_standby.c")
    assert "השרת ענה hello" not in sb and "השרת מגיב" in sb and "השרת החזיר שגיאה" in sb
    assert 'a->st.machine_name:"לא ידועה"' in _t("native-gui/src/screens.c")


def test_dcui_system_updates_date_is_day_month_year():
    from server.dcui import view

    assert view.system_updates_row("2026-10-15").record()[3] == f"מתאריך {view.FSI}15/10/2026{PDI}"
    assert view.system_updates_row("").record()[3] == "תאריך לא ידוע"


def test_console_badges_have_dark_foreground_on_bright_fills():
    css = _t("server/static/console.css")
    assert "background:var(--clr-action-soft);color:var(--clr-action-fg)" in css.split(".upd .ubadge{", 1)[1].split("}", 1)[0]
    assert "--clr-bright-fill-on:#10161b;" in _t("server/static/design-tokens.css")
    rules = re.findall(r'([^{}]+)\{color:var\(--clr-bright-fill-on\)\}', css)
    for theme in (':root[data-theme="dark"]', ':root:not([data-theme="light"])'):
        for selector in (".topbar #alertBadge", "#login .rules li.ok i", ".pw-pol li.ok i", ".tag.has-alerts"):
            assert any(f"{theme} {selector}" in rule for rule in rules)
