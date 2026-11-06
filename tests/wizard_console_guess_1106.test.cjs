// CodeQL #293/#330 (js/xss-through-dom): the console link in the installer wizard was built from the
// hostname/address typed on the form, with no check of what was typed. It is now a link only for a host
// name or an IPv4 address; anything else falls back to the page's own origin. Runs the shipped functions.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const js = fs.readFileSync(path.resolve(__dirname, '../server/wizard/static/wizard.js'), 'utf8');

function slice(name) {
  const start = js.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} exists in wizard.js`);
  return js.slice(start, js.indexOf('\n}', start) + 2);
}

function loadGuess(form) {
  const c = vm.createContext({
    location: {origin: 'https://10.44.0.1:8090'},
    lastProgress: {},
    config: () => form,
  });
  const helper = js.includes('function isValidHost(') ? slice('isValidHost') + '\n' : '';
  vm.runInContext(helper + slice('consoleGuess'), c);
  return c.consoleGuess;
}

test('#293/#330: a typed host name or address becomes the https link', () => {
  assert.equal(loadGuess({mode: 'dhcp', hostname: 'imagesrv-01', address: ''})(),
               'https://imagesrv-01:8081');
  assert.equal(loadGuess({mode: 'static', hostname: 'x', address: '10.44.0.5'})(),
               'https://10.44.0.5:8081');
});

test('#293/#330: anything else typed on the form is not put into the link', () => {
  for (const typed of ['evil.example/"><img src=x onerror=alert(1)>', 'a b', 'x@evil', '']) {
    assert.equal(loadGuess({mode: 'dhcp', hostname: typed, address: ''})(), 'https://10.44.0.1:8090',
                 typed);
  }
});
