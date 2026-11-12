// The console's JavaScript files, in the order index.html loads them (#2594).
// Tests that slice console code by function name read it through here instead of
// opening console.js directly, so moving code between the console's own files
// (console.js, later console-core.js) does not move it out from under them.
// The list comes from index.html, never from a hard-coded one.
'use strict';
const fs = require('node:fs');
const path = require('node:path');

const STATIC = path.resolve(__dirname, '../server/static');

// File names (relative to server/static) of every console*.js, in load order.
function consoleFiles() {
  const html = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
  const names = [...html.matchAll(/<script\s+src="(console[^"/?]*\.js)(?:\?[^"]*)?"/g)].map((m) => m[1]);
  if (!names.includes('console.js')) throw new Error('index.html no longer loads console.js');
  return names;
}

// The text of all console files, concatenated in load order.
function consoleJs() {
  return consoleFiles().map((n) => fs.readFileSync(path.join(STATIC, n), 'utf8')).join('\n');
}

module.exports = {consoleFiles, consoleJs};
