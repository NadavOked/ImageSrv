// #3666: what the SERVER sends as `rule` for a disk -- so a console test can write a fixture the way the console receives it.
//
// The console decides nothing about a disk's colour any more: the brain (server/brain/diskcolor.py, K3/DISK_COLOR) classifies and
// sends `drawer_list[].rule` (/room) and `disks[].rule` (/machines) ready, and the console shows them. This file is TEST CODE only:
// it builds that object from the same table (agent/lib/data/disk-rule.tsv), the way server/disks/disk_rule.py does, so that a
// fixture can say "a disk with SMART ok" and have the rule the server would have sent. Nothing under server/static reads it,
// and tests/test_disk_color_brain_3666.py holds the real thing (the server) to the table's golden verdicts.
'use strict';
const fs = require('node:fs');
const path = require('node:path');

const TABLE = path.resolve(__dirname, '../agent/lib/data/disk-rule.tsv');
const ROWS = fs.readFileSync(TABLE, 'utf8').split('\n').filter((l) => l && !l.startsWith('#')).map((l) => {
  const [kind, word, fact, color, short_he, he] = l.split('\t');
  return { kind, word, fact, color, short_he: short_he === '-' ? null : short_he, he: he === '-' ? null : he };
});
const COLOR_HE = { red: 'אדום', yellow: 'צהוב', orange: 'כתום', green: 'ירוק', grey: 'לא נבדק' };
const VERDICT = { red: 'DISK_RED', yellow: 'DISK_YELLOW', orange: 'DISK_ORANGE', green: 'DISK_GREEN', grey: 'DISK_NOT_CHECKED' };

const holds = (fact, facts) => fact.split('|').some((alt) => {
  const [name, ...value] = alt.split('=');
  return alt === '*' || (value.length ? facts[name] === value.join('=') : facts[name] === true);
});

function factsOf(disk, { fail = null, write_slow = false } = {}) {
  const ro = !!(fail && fail.cause === 'readonly'), cable = !!(fail && fail.cause === 'cable');   // #3008: a cable cause is the connection's (disk_rule.failures_of)
  return { smart: ro ? 'fail' : disk.smart || 'unchecked', failed_last: !!fail && !ro && !cable, cable_failed: cable, write_incomplete: disk.write_mark === 'incomplete',
    sata_issue: Array.isArray(disk.sata_issues) && disk.sata_issues.some((w) => w === 'link_reset' || w === 'drawer_gone'),
    write_slow: write_slow === true || disk.write_slow === true };
}

function ruleOf(disk, opts = {}) {
  const facts = factsOf(disk, opts);
  const on = (kind) => ROWS.find((r) => r.kind === kind && holds(r.fact, facts));
  const red = on('red'), yellow = on('yellow'), color = red ? 'red' : yellow ? 'yellow' : on('smart').color;
  const word = (r) => (r ? r.word : null), he = (r) => (r ? r.he : null), short = (r) => (r ? r.short_he : null);
  return { color, source: !yellow, target: !red, reason: word(red) || word(yellow), source_reason: word(yellow), target_reason: word(red),
    reason_he: he(red) || he(yellow), source_reason_he: he(yellow), target_reason_he: he(red),
    reason_short_he: short(red) || short(yellow), source_reason_short_he: short(yellow), target_reason_short_he: short(red),
    reasons: ROWS.filter((r) => (r.kind === 'red' || r.kind === 'yellow') && holds(r.fact, facts)).map((r) => ({ word: r.word, he: r.he, short_he: r.short_he })),
    by: 'K3/DISK_COLOR', verdict: VERDICT[color], color_he: COLOR_HE[color] };
}

// A disk object the way the server sends it: with its `rule` (an explicit one in the fixture wins).
const ruled = (disk, opts = {}) => (disk && typeof disk === 'object' && !disk.rule ? { ...disk, rule: ruleOf(disk, opts) } : disk);

// Every disk of a /machines or /room fixture (machines[].disks[], machines[].drawer_list[], round.machines[]...) gets its rule,
// the open failure record joined by serial or by port like server/disks/disk_failures.open_for. Mutates and returns `value`.
function decorate(value, failures = [], mac = null) {
  const failFor = (d, m) => (failures || []).find((f) => (f.serial && f.serial === d.serial) || (f.mac === m && f.port != null && f.port === d.port)) || null;
  const walk = (v, m) => {
    if (Array.isArray(v)) { v.forEach((x) => walk(x, m)); return; }
    if (!v || typeof v !== 'object') return;
    const here = typeof v.mac === 'string' ? v.mac : m;
    for (const key of ['disks', 'drawer_list']) {
      if (Array.isArray(v[key])) v[key] = v[key].map((d) => (d && typeof d === 'object' && !d.rule ? ruled(d, { fail: failFor(d, here) }) : d));
    }
    for (const k of Object.keys(v)) if (k !== 'rule') walk(v[k], here);
  };
  walk(value, mac);
  return value;
}

module.exports = { ruleOf, ruled, decorate, factsOf };
