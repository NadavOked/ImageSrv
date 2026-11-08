// #2586: real POSIX shell functions, with mounts replaced by fixture directories.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');

const root = path.resolve(__dirname, '..');
const quote = s => "'" + s.replaceAll("'", "'\\''") + "'";
function shell(script) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'imagesrv-2586-'));
  try {
    const result = spawnSync('sh', ['-s'], {
      cwd: root, encoding: 'utf8',
      input: `PATH=/usr/bin:/bin:$PATH\nexport RUN_DIR=${quote(tmp.replaceAll('\\', '/'))}\n` +
        'LOG_FILE="$RUN_DIR/log"\nexport LOG_FILE\n' +
        '. agent/lib/capture/manifest.sh\n. agent/lib/postdeploy/hostname.sh\n' +
        '. agent/lib/postdeploy/hostname_linux.sh\n. agent/lib/restore/grow.sh\n' +
        '. agent/lib/postdeploy/postdeploy.sh\n' +   // plan_bitlocker (#1840), as imagesrv-agent sources it
        'log() { printf "%s\\n" "$*" >> "$LOG_FILE"; }\n' + script,
    });
    assert.ifError(result.error);
    return result;
  } finally {
    fs.rmSync(tmp, {recursive: true, force: true});
  }
}

for (const guid of ['4F68BCE3-E8CD-4DB1-96E7-FBCAF984B709',
                    '4f68bce3-e8cd-4db1-96e7-fbcaf984b709']) {
  test(`8304 is linux: ${guid}`, () => {
    const r = shell(`_partition_role ${guid}\n`);
    assert.equal(r.status, 0, r.stderr);
    assert.equal(r.stdout.trim(), 'linux');
  });
}

test('8302 and ext4 0700 data partitions stay data', () => {
  const r = shell(`
_partition_role 933AC7E1-2EB4-4F13-B844-0E14E2AEF915
role=$(_partition_role EBD0A0A2-B9E5-4433-87C0-68B6B72699C7)
[ "$role" = windows ] || exit 8
_probe_mount() { echo unexpected-mount; return 1; }
_system_role 2 /dev/fake ext4
`);
  assert.equal(r.status, 0, r.stderr);
  assert.equal(r.stdout.trim(), 'data\ndata');
});

for (const evidence of [false, true]) {
  test(`linux system marking requires /etc evidence: ${evidence}`, () => {
    const r = shell(`
: > "$RUN_DIR/system.1"
_probe_mount() {
    [ "$3" = ro,noload ] || return 1
    ${evidence ? 'mkdir -p "$4/etc" && : > "$4/etc/os-release"' : 'return 0'}
}
umount() { printf 'unmounted\\n'; }
_linux_system 1 /dev/fake ext4 || exit 9
_system_json linux 1
`);
    assert.equal(r.status, 0, r.stderr);
    assert.equal(r.stdout.trim(), evidence ? 'unmounted\n,"system":true' : 'unmounted');
  });
}

for (const scenario of ['missing', 'no_etc', 'mount_failed']) {
  test(`hostname distinguishes ${scenario}`, () => {
    const role = scenario === 'missing' ? 'data' : 'linux';
    const r = shell(`
manifest_plan() { echo '1|guid|${role}|ext4|2048|4096|p1|sha|false'; }
_linux_marked() { return 0; }
partition_node() { echo /dev/fake; }
mount() { echo mount >> "$RUN_DIR/calls"; return ${scenario === 'mount_failed' ? 1 : 0}; }
umount() { echo umount >> "$RUN_DIR/calls"; }
write_hostname sda /dev/null LAB1-05
rc=$?
echo "rc=$rc" >&2
if [ -f "$RUN_DIR/calls" ]; then cat "$RUN_DIR/calls" >&2; fi
exit "$rc"
`);
    assert.equal(r.status, 1, r.stderr);
    const result = JSON.parse(r.stdout);
    assert.equal(result.ok, false);
    assert.equal(result.code, scenario === 'missing' ? 'no_linux_partition' : scenario);
    if (scenario === 'missing') {
      assert.equal(result.error, 'אין מחיצת לינוקס במניפסט');
      assert.equal(r.stderr.trim(), 'rc=1');
    } else if (scenario === 'no_etc') {
      assert.equal(r.stderr.trim(), 'rc=1\nmount\numount');
    } else {
      assert.equal(r.stderr.trim(), 'rc=1\nmount');
    }
  });
}

test('linux without /etc has no identity to reset', () => {
  const r = shell(`
manifest_plan() { echo '1|guid|linux|ext4|2048|4096|p1|sha|false'; }
_linux_marked() { return 0; }
partition_node() { echo /dev/fake; }
mount() { echo mount; }
umount() { echo umount >&2; }
_linux_id_files() { echo unexpected-write; return 1; }
linux_identity_reset sda /dev/null
rc=$?
cat "$LOG_FILE" >&2
exit "$rc"
`);
  assert.equal(r.status, 0, r.stderr);
  assert.equal(r.stdout, '');
  assert.match(r.stderr, /umount/);
  assert.match(r.stderr, /no Linux partition holds \/etc -- no identity to reset/);
});
