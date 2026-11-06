"""#2585: preserve BIOS boot.img without copying the target's protective MBR.

Regular files stand in for disks. Table writing is stubbed; dd, base64, cmp
and (for restore) jq are real. No root, loop devices or grub-install needed.
"""

import base64
import json
import shutil

import pytest

from agentlib import agent_sources
from native import requires_native
from test_agent import BASH, posix, sh
from test_capture_refusals import (
    CURL_SINK, GPT_DISK, ONE_PARTITION, capture_run,
)

pytestmark = requires_native(("bash", BASH))
needs_jq = requires_native(("jq", shutil.which("jq")))
FIELD = "gpt_boot_code_base64"
# Includes NUL, high bytes and a non-zero final byte: shell text is not binary.
CODE = bytes(range(256)) + bytes(range(184))
NEW_TABLE = bytes(440) + bytes(range(72))


@pytest.mark.parametrize("code", [CODE, bytes(440)])
def test_capture_records_only_nonzero_boot_code(tmp_path, code):
    source = code + GPT_DISK[440:]
    box, run, out = capture_run(
        tmp_path, image=source,
        stubs={"sgdisk": ONE_PARTITION, "curl": CURL_SINK})
    assert out.strip().endswith("rc=0"), out + (box / "capture.out").read_text()
    manifest = json.loads((run / "new-manifest.json").read_text())
    if any(code):
        assert manifest[FIELD] == base64.b64encode(code).decode()
    else:
        assert FIELD not in manifest
    assert manifest["scheme"] == "gpt" and manifest["partitions"]
    assert (box / "dev" / "sda").read_bytes()[:len(source)] == source   # the box pads the disk
    assert not list(run.glob("gpt-boot.*"))


def _box(tmp_path, payload=CODE):
    run, dev = tmp_path / "run", tmp_path / "dev"
    run.mkdir()
    dev.mkdir()
    target = dev / "sdb"
    target.write_bytes(b"X" * 4096)
    manifest = tmp_path / "manifest.json"
    obj = {"scheme": "gpt", "disk_guid": None}
    if payload is not None:
        obj[FIELD] = base64.b64encode(payload).decode()
    manifest.write_text(json.dumps(obj))
    table = tmp_path / "new-table"
    table.write_bytes(NEW_TABLE)
    plan = tmp_path / "plan"
    plan.write_text(
        "1|0FC63DAF-8483-4772-8E79-3D69D8477DE4|linux|ext4|2048|1048576"
        "|p1.img.zst|" + "a" * 64 + "|false|||\n", newline="\n")
    prelude = (
        f'RUN_DIR={posix(run)!r}; DEVROOT={posix(dev)!r}; '
        'LOG_FILE="$RUN_DIR/log"; '
        + agent_sources("gptboot", "expand", "lccopy")
        + f'MANIFEST={posix(manifest)!r}; TABLE={posix(table)!r}; PLAN={posix(plan)!r}; '
        + r'''
log() { printf '%s\n' "$*" >> "$LOG_FILE"; }
json_get() { jq -r "$2" "$1"; }
plan_preflight() { cp "$PLAN" "$RUN_DIR/gate.plan"; }
target_write_started() { :; }
writemark_args() { :; }
reread_table() { :; }
verify_table() { :; }
sgdisk() {
    printf '%s\n' "$*" >> "$RUN_DIR/table.calls"
    case "$1" in --zap-all) return 0 ;; esac
    command dd if="$TABLE" of="$DEVROOT/sdb" bs=512 count=1 conv=notrunc
}
target_set() { :; }
disk_fits() { :; }
expand_last() { :; }
fail_written_target() { printf '%s\n' "$2" > "$RUN_DIR/$1.failed"; }
'''
    )
    return run, target, manifest, prelude


@needs_jq
def test_restore_writes_440_bytes_after_table_and_keeps_the_rest(tmp_path):
    run, target, manifest, prelude = _box(tmp_path)
    before_manifest = manifest.read_bytes()
    out = sh(prelude + 'apply_gpt sdb "$MANIFEST"; echo "rc=$?"')
    assert out.strip() == "rc=0", out
    assert (run / "table.calls").read_text().splitlines()[0].startswith("--zap-all")
    assert target.read_bytes() == CODE + NEW_TABLE[440:] + b"X" * (4096 - 512)
    assert manifest.read_bytes() == before_manifest
    assert not list(run.glob("gpt-boot.*"))


@needs_jq
@pytest.mark.parametrize("payload", [None, bytes(440)])
def test_old_or_zero_manifest_does_not_read_or_write_boot_code(tmp_path, payload):
    run, target, manifest, prelude = _box(tmp_path, payload)
    before_manifest = manifest.read_bytes()
    # The table writer above uses command dd; any boot-code dd is forbidden.
    out = sh(prelude + 'dd() { echo unexpected-dd >> "$RUN_DIR/dd.calls"; return 91; }; '
             'apply_gpt sdb "$MANIFEST"; echo "rc=$?"')
    assert out.strip() == "rc=0", out
    assert not (run / "dd.calls").exists()
    assert target.read_bytes() == NEW_TABLE + b"X" * (4096 - 512)
    assert manifest.read_bytes() == before_manifest


@needs_jq
@pytest.mark.parametrize("fault", ["mismatch", "write", "read", "short"])
def test_failed_boot_code_fails_the_named_drive(tmp_path, fault):
    run, target, manifest, prelude = _box(tmp_path)
    inject = {
        "mismatch": 'command dd if=/dev/zero "${@:2}"',
        "read": "return 7",
        "short": 'command dd if=/dev/zero of="${2#of=}" bs=1 count=439',
    }
    # Test harness is bash; production module remains POSIX sh.
    if fault == "write":
        stub = 'dd() { case "$*" in *conv=notrunc,fsync*) return 7 ;; esac; command dd "$@"; }; '
    else:
        stub = ('dd() { case "$1" in "if=$DEVROOT/sdb") '
                + inject[fault] + '; return $? ;; esac; command dd "$@"; }; ')
    out = sh(prelude + stub + 'lc_prepare_target sdb "$MANIFEST"; echo "rc=$?"')
    assert out.strip() == "rc=1", out
    reason = (run / "sdb.failed").read_text()
    assert "sdb" in reason and "קוד האתחול של GPT" in reason, reason
    assert not (run / "sda.failed").exists()
    assert target.read_bytes()[440:] == NEW_TABLE[440:] + b"X" * (4096 - 512)
    assert not list(run.glob("gpt-boot.*"))


@needs_jq
@pytest.mark.parametrize("value", [None, "", "!" * 588, "AAAA", 42,
                                    base64.b64encode(bytes(512)).decode()])
def test_malformed_boot_code_is_a_failure_without_a_boot_write(tmp_path, value):
    run, target, manifest, prelude = _box(tmp_path)
    manifest.write_text(json.dumps({FIELD: value}))
    out = sh(prelude + 'dd() { echo unexpected-dd >> "$RUN_DIR/dd.calls"; return 91; }; '
             'restore_gpt_boot_code sdb "$MANIFEST"; echo "rc=$?"')
    assert out.strip() == "rc=1", out
    assert not (run / "dd.calls").exists()
    assert target.read_bytes() == b"X" * 4096


@pytest.mark.parametrize("size", [0, 439])
def test_capture_short_read_is_not_an_empty_uefi_disk(tmp_path, size):
    run, target, manifest, prelude = _box(tmp_path)
    target.write_bytes(bytes(size))
    out = sh(prelude + 'capture_gpt_boot_code "$DEVROOT/sdb"; echo "rc=$?"')
    assert out.strip() == "rc=1", out
    assert not list(run.glob("gpt-boot.*"))


@needs_jq
def test_unavailable_manifest_check_fails_by_name(tmp_path):
    run, target, manifest, prelude = _box(tmp_path, None)
    out = sh(prelude + 'jq() { return 127; }; '
             'lc_prepare_target sdb "$MANIFEST"; echo "rc=$?"')
    assert out.strip() == "rc=1", out
    assert "sdb: לא הצלחנו לשחזר ולאמת את קוד האתחול של GPT" in (run / "sdb.failed").read_text()


def test_server_accepts_and_preserves_optional_disk_field():
    from server.library.images import ImageLibrary
    from test_capture import manifest_for

    manifest = manifest_for()
    manifest[FIELD] = base64.b64encode(CODE).decode()
    before = json.loads(json.dumps(manifest))
    assert ImageLibrary._validate(manifest, require_library_fields=False) is None
    assert manifest == before
