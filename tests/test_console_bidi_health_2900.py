"""#2900 (the console side): a path or a number+unit inside a Hebrew sentence the SERVER composed is isolated.

The Overview's "דורש טיפול" and the Health page print the `detail` of a /health row as it came: "עוד לא רץ גיבוי אל
/var/lib/imagesrv/backups — ..." (backup_job.health_check) put the leading "/" and the "-" at the wrong edge on screen. The
sentence stays RTL; only the path and the "24 MB" are isolated (`ltrRuns`, built on the `ltr()` that was already there).

Real here: the console's own `esc`, `ltr`, `LTR_RUN` and `ltrRuns` (sliced from the real files and run in node), and the two
places that print a health row's detail, read from the source.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from console_src import console_js

REPO = Path(__file__).resolve().parent.parent

NODE = r"""
const {consoleJs} = require('./tests/console_src.cjs');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const js = consoleJs();
const cut = (src, from, to, what) => {
  const i = src.indexOf(from), j = src.indexOf(to, i);
  assert.ok(i >= 0 && j > i, what + ' is not in the console source');
  return src.slice(i, j + to.length);
};
const ctx = vm.createContext({});
vm.runInContext(cut(js, 'const ESC_MAP', 'return (text == null ? "" : String(text)).replace(/[&<>"\'`]/g, (c) => ESC_MAP[c]);\n}', 'esc'), ctx);
vm.runInContext(cut(js, 'function ltr(text)', 'return out + esc(s.slice(last));\n}', 'ltr/ltrRuns'), ctx);
const run = (text) => vm.runInContext('ltrRuns', ctx)(text);
const B = (t) => '<bdi dir="ltr">' + t + '</bdi>';

// the Issue's case: the backup folder in "דורש טיפול" -- the path alone is isolated, the sentence is not
assert.equal(run('עוד לא רץ גיבוי אל /var/lib/imagesrv/backups — הגיבוי הראשון רץ תוך דקה'),
  'עוד לא רץ גיבוי אל ' + B('/var/lib/imagesrv/backups') + ' — הגיבוי הראשון רץ תוך דקה');
// "(path, file)" and a path that ends the sentence: the dot and the bracket stay outside
assert.equal(run('ישן — הגיבוי האחרון הצליח (db.sqlite, /mnt/nas/backups).'),
  'ישן — הגיבוי האחרון הצליח (db.sqlite, ' + B('/mnt/nas/backups') + ').');
assert.equal(run('התיקייה /mnt/nas/backups. נסו שוב'), 'התיקייה ' + B('/mnt/nas/backups') + '. נסו שוב');
// a number with its unit, one run (the space between them is inside)
assert.equal(run('המקום השמור (24 MB) קטן מגיבוי אחד'), 'המקום השמור (' + B('24 MB') + ') קטן מגיבוי אחד');
assert.equal(run('כתב 38.4 MB/s, מתחת לסף 50 MB/s'), 'כתב ' + B('38.4 MB/s') + ', מתחת לסף ' + B('50 MB/s'));
// what is not a path or an amount stays as it is
assert.equal(run('1/2 מהדיסקים, a/b, 3 דיסקים'), '1/2 מהדיסקים, a/b, 3 דיסקים');
// escaping is not lost: the server's text is data
assert.equal(run('<b>x</b> /tmp/<i>'), '&lt;b&gt;x&lt;/b&gt; ' + B('/tmp/') + '&lt;i&gt;');
assert.equal(run(''), '');
"""


def test_a_path_and_an_amount_in_a_server_sentence_are_isolated_alone():
    result = subprocess.run(["node", "-"], cwd=REPO, capture_output=True, input=NODE.encode())
    assert result.returncode == 0, result.stderr.decode()


def test_both_places_that_print_a_health_detail_use_it():
    """The Overview's attention item and the Health page's row: the detail goes through `ltrRuns`, not a bare `esc`."""
    js = console_js()
    assert '" — " + ltrRuns(c.detail)' in js, "the Overview attention item prints the detail bare"
    assert "<small>${ltrRuns(c.detail || \"\")}</small>" in js, "the Health page row prints the detail bare"
    assert " — \" + esc(c.detail)" not in js and "<small>${esc(c.detail || \"\")}</small>" not in js
