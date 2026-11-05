// #2422: esc() in the installer wizard (server/wizard/static/wizard.js) was textContent -> innerHTML.
// A browser serialises a text node escaping only & < > -- not quotes -- and wizard.js uses esc() inside
// value="..." (the NIC list) and href="..." (the console link), where a " closes the attribute. This is
// #1129 again, in the file that did not get that fix. Runs the shipped function, with a document whose
// span serialises text the way a browser does, so the old version fails on behaviour, not on a missing DOM.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const js = fs.readFileSync(path.resolve(__dirname, '../server/wizard/static/wizard.js'), 'utf8');

function loadEsc() {
  const start = js.indexOf('function esc(');
  assert.ok(start >= 0, 'esc exists in wizard.js');
  const end = js.indexOf('\n}', start) + 2;
  const map = js.match(/^const ESC_MAP = .*;$/m);
  const browserSpan = () => {
    let text = '';
    return {set textContent(v) { text = v; },
      get innerHTML() { return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }};
  };
  const c = vm.createContext({document: {createElement: browserSpan}});
  vm.runInContext((map ? map[0] + '\n' : '') + js.slice(start, end), c);
  return c.esc;
}

test('#2422: esc() encodes both quotes, so a value cannot leave value="..." or href="..."', () => {
  const esc = loadEsc();
  const evil = 'x" onfocus="alert(1)" autofocus="';
  const html = `<input type="radio" name="interface" value="${esc(evil)}">`;
  assert.doesNotMatch(html, /" onfocus="/);
  assert.equal(esc(evil), 'x&quot; onfocus=&quot;alert(1)&quot; autofocus=&quot;');
  assert.equal(esc("it's"), 'it&#39;s');
  assert.equal(esc('<b>&</b>'), '&lt;b&gt;&amp;&lt;/b&gt;');
  assert.equal(esc('eth0'), 'eth0');
  assert.equal(esc(42), '42');
});

// The second half of #2422: net.js wrote /health into #health-list -- an element no page has -- with
// c.state unescaped in class="". Dead code with a live sink; it is removed, not patched.
test('#2422: the dead #health-list sink is gone from net.js', () => {
  const net = fs.readFileSync(path.resolve(__dirname, '../server/static/net.js'), 'utf8');
  assert.doesNotMatch(net, /#health-list|#health-refresh|loadHealthPanel/);
  const index = fs.readFileSync(path.resolve(__dirname, '../server/static/index.html'), 'utf8');
  assert.doesNotMatch(index, /health-list/, 'nothing on the page it could have written to');
});
