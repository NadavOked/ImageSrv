"""Single GUI restore and pull: real HTTP Range, chunkcast apply, and disk files.

Only hardware, GUI and final filesystem readback are seams. The readback seam
compares every target byte; it also injects failure to prove completion is gated.
Run on the VM test queue, not on the development workstation.
"""

import json
import os
import shlex
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import chunkcast_rig as C
from agentlib import agent_sources
from native import requires_native
from test_agent import BASH
from test_pull_after_capture_3231 import _pull, chunk_engine  # noqa: F401 -- chunk_engine is a fixture
from test_server_room import room_server  # noqa: F401 -- a fixture
from test_wave_after_capture_3137 import _fresh_capture, derive, wait_for  # noqa: F401 -- derive is a fixture

pytestmark = requires_native(("bash", BASH), "gcc", "zstd", "curl", "jq", posix=True)


@pytest.fixture(scope="module")
def binaries(tmp_path_factory):
    return C.build_binaries(tmp_path_factory.mktemp("single-bin"))


@pytest.fixture(scope="module", params=["raw", "partclone"])
def image(request, binaries, tmp_path_factory):
    return C.build(tmp_path_factory.mktemp("single-image"), binaries / "fanout",
                   kind=request.param, chunks=2.25, noisy=())


def run_restore(tmp_path, binaries, image, *, gui=False, fault="", fmt=2, engine="chunk", img="img_test"):
    disk = image.blank(tmp_path / "disk3")
    expected = tmp_path / "expected"
    expected.write_bytes(image.expected())
    idx = image.idx.read_bytes()
    if fault == "hash":
        # Valid compressed bytes, deliberately incorrect DATA hash in an otherwise
        # correctly authenticated index: this must reach the XXH64 check itself.
        lines = idx.decode().splitlines()
        fields = lines[2].split()
        fields[2] = "0000000000000000"
        lines[2] = " ".join(fields)
        idx = ("\n".join(lines) + "\n").encode()
    b3 = subprocess.run([str(binaries / "chunkcast"), "b3list"], input=image.data, capture_output=True, check=True).stdout
    b3_hash = subprocess.run([str(binaries / "chunkcast"), "b3sum"], input=b3, capture_output=True, check=True).stdout.decode().strip()
    part = {"index": 3, "index_xxh64": C.xxh64(binaries / "fanout", idx)}
    if fault != "no-b3":   # #3332: the BLAKE3 list is required
        part["chunk_b3"] = b3_hash
    manifest = {"id": "img_test", "image_format": fmt,
                "total_compressed_bytes": image.file.stat().st_size, "partitions": [part]}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    ranges = []
    size = image.file.stat().st_size
    release = threading.Event()   # "cancel": the data range holds here after the first frame

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = json.dumps({"id": "pull_test", "engine": engine}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.endswith("/manifest"):
                body, status = json.dumps(manifest).encode(), 200
            elif "/index/" in self.path:
                body, status = idx, 200
            elif "/b3/" in self.path:
                body, status = b3, 200
            else:
                header = self.headers.get("Range")
                assert header and header.startswith("bytes="), header
                start, end = map(int, header[6:].split("-"))
                ranges.append((start, end))
                body, status = image.file.read_bytes()[start:end + 1], 206
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            cut = status == 206 and start == image.offsets[0] and fault in ("network", "cancel")
            try:
                # the data range: the first frame whole, then the connection drops (network)
                # or stands still until the restore is cancelled (cancel)
                self.wfile.write(body[:image.zlens[0]] if cut else body)
                if cut and fault == "cancel":
                    (tmp_path / "served").touch()
                    release.wait(20)
            except (BrokenPipeError, ConnectionResetError):
                pass  # apply may reject a chunk before curl has read the rest
            if cut:
                self.close_connection = True

        def log_message(self, *_args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    sources = agent_sources("waits", "restore", "chunkb3", "chunkrecv", "pull", "single_restore", "srgui")   # srgui: sr_stop_stream, the GUI's cancel
    # The pre-fix tree has no helper. A negative control must reach the old
    # writer and fail behavior assertions, rather than fail on a missing source.
    helper = C.REPO / "agent/lib/restore/chunksingle.sh"
    if helper.exists():
        sources += f". {shlex.quote(str(helper))}; "
    script = sources + r'''
mkdir -p "$RUN_DIR/targets/sda"
echo writing > "$RUN_DIR/targets/sda/state"
log() { echo "$*" >> "$LOG_FILE"; }
json_get() { jq -r "$2" "$1"; }
http_get() { curl -fsS "$1"; }
disk_fits() { :; }; boot_ca_preflight() { :; }; plan_gate() { :; }
write_check() { :; }; write_speed_verdict() { :; }; discard_before_restore() { :; }
apply_gpt() { :; }; expand_last() { :; }; finish_grow() { echo GROW >> "$TRACE"; }
assert_target() { :; }; node_is_block() { [ -f "$1" ]; }
partition_node() { echo "$RUN_DIR/disk3"; }; disk_serial() { echo SER-sda; }
next_stream_ceiling() { STREAM_START_CEILING=5; }
if [ "$FAULT" = no-ledger ]; then
    chunkcast() { cat > /dev/null; return 0; }
fi
chunk_watch_start() { :; }; chunk_watch_end() { :; }; round_closed() { return 1; }
target_counter() { : > "$2"; }
target_partition_done() { echo PART-DONE >> "$TRACE"; }
target_set() { echo "$2" > "$RUN_DIR/targets/$1/state"; }
fail_written_target() { echo "FAIL $1 $2 [${3:-disk}]" >> "$TRACE"; target_set "$1" failed; }
writeback_why() { :; }; ntfs_boot_target() { :; }
manifest_plan() { echo '3|g|data|unknown|2048|999|p3.zst|sha|false|u|uuid|'; }
restore_partition() { echo LEGACY >> "$TRACE"; cp "$RUN_DIR/expected" "$RUN_DIR/disk3"; }
readback_drawers() {
    echo READBACK >> "$TRACE"
    if [ "$FAULT" = readback ] || ! cmp "$RUN_DIR/disk3" "$RUN_DIR/expected"; then
        fail_written_target "$2" 'readback failed'; return 1
    fi
}
pick_internal_disk() { echo sda; }; smart_red() { return 1; }
job_reset() { :; }; target_init() { :; }; sr_gui_start() { :; }; sr_share_gate() { :; }
session_open_gate() { :; }; progress_loop() { :; }; sr_cancel_watch() { :; }
sr_cancel_requested() { return 1; }; stage_drivers() { :; }
writemark_release_done() { echo RELEASE >> "$TRACE"; }
pull_close() { echo CLOSE >> "$TRACE"; }; sr_gui_done() { echo GUI-DONE >> "$TRACE"; }
single_restore_failed_screen() { echo GUI-FAILED >> "$TRACE"; }; ui_error_hold() { :; }
sr_refused() { echo REFUSED >> "$TRACE"; return 1; }; die_local() { exit 90; }
json_escape() { printf '%s' "$1"; }
if [ "$FAULT" = cancel ]; then   # "ביטול השחזור" once the first frame is out: the GUI's own stopper (srgui.sh)
    # bounded, off the captured pipes, and only once its own frame was served: it stops every curl of "$IMG"
    ( n=0; while [ ! -e "$RUN_DIR/served" ] && [ "$n" -lt 200 ]; do sleep 0.1; n=$((n + 1)); done
      [ ! -e "$RUN_DIR/served" ] || sr_stop_stream "$IMG" ) > /dev/null 2>&1 &
fi
if [ "$GUI" = 1 ]; then
    single_restore_run "$IMG"
else
    pull_open "$SERVER" "$MAC" "$IMG" '' '' || exit 91
    run_restore unicast sda "$SERVER" "$IMG" "$RUN_DIR/manifest.json"
fi
echo "RC=$?" >> "$TRACE"
'''
    env = {**os.environ, "PATH": f"{binaries}:{os.environ['PATH']}",
           "CHUNKCAST_ALLOW_FILE": "1", "RUN_DIR": str(tmp_path),
           "LOG_FILE": str(tmp_path / "log"), "TRACE": str(tmp_path / "trace"),
           "SERVER": f"http://127.0.0.1:{httpd.server_port}", "MAC": "aa:bb:cc:dd:ee:ff",
           "HTTP_TIMEOUT": "5", "HTTP_RETRIES": "0", "WAIT_STREAM_STALL_S": "5",
           "WAIT_DRAWER_S": "5", "GUI": str(int(gui)), "FAULT": fault, "IMG": img}
    try:
        proc = subprocess.run([BASH, "-c", script], env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=40)
        assert proc.returncode == 0, proc.stdout + proc.stderr
    finally:
        release.set()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    return (tmp_path / "trace").read_text(), disk.read_bytes(), ranges


@pytest.mark.parametrize("gui", [False, True], ids=["station-pull", "build-gui"])
def test_complete_restore(tmp_path, binaries, image, gui):
    trace, disk, ranges = run_restore(tmp_path, binaries, image, gui=gui)
    assert "RC=0" in trace and "LEGACY" not in trace, trace
    assert disk == image.expected()
    assert trace.index("PART-DONE") < trace.index("READBACK") < trace.index("GROW")
    if gui:
        assert trace.index("READBACK") < trace.index("RELEASE") < trace.index("GUI-DONE")
    # the partclone map, then ONE range from the first frame to the last -- not a request a chunk
    head = [(0, image.offsets[0] - 1)] if image.kind == "partclone" else []
    assert ranges == head + [(image.offsets[0], image.file.stat().st_size - 1)]
    ledger = tmp_path / "targets/sda/ledger/durable.3"
    assert ledger.read_text().strip() == f"0-{image.n - 1}"
    counter = tmp_path / "targets/sda/feed.bytes"
    assert int(counter.read_text().splitlines()[-1]) == image.file.stat().st_size


@pytest.mark.parametrize("fault,reason", [("hash", "wrong XXH64"), ("network", "ההורדה מהשרת נקטעה")])
def test_partial_failure_is_named_and_never_written_as_success(tmp_path, binaries, image, fault, reason):
    trace, disk, _ = run_restore(tmp_path, binaries, image, gui=True, fault=fault)
    assert "RC=1" in trace and "FAIL sda מחיצה 3 על sda:" in trace, trace
    assert reason in trace and "GUI-FAILED" in trace, trace
    # the stream's fault, not the drive's: no ATA evidence against a healthy disk (#1598)
    assert "[stream]" in trace and "[disk]" not in trace, trace
    assert "READBACK" not in trace and "GUI-DONE" not in trace and "RELEASE" not in trace
    assert disk == image.expected({0})  # corrupt/missing chunk and its successors untouched


def test_gui_cancel_stops_the_chunk_download(tmp_path, binaries, image):
    # "ביטול השחזור" finds the stream by its URL on curl's command line (sr_stop_stream): a download
    # it cannot find runs to the stall ceiling, and the cancel waits that long for nothing.
    img = f"img_cancel_{uuid.uuid4().hex[:12]}"   # sr_stop_stream stops every curl of the image: this test's own only
    trace, disk, _ = run_restore(tmp_path, binaries, image, fault="cancel", img=img)
    assert "restore: stopping the stream (curl" in (tmp_path / "log").read_text()
    assert "RC=1" in trace and "curl rc=143" in trace, trace   # killed by the cancel, not timed out (28)
    assert "READBACK" not in trace and "PART-DONE" not in trace, trace
    # curl's stdout is a pipe: the first frame (412 bytes) may still be in its buffer when the cancel kills it, so
    # chunk 0 is written or not -- but no chunk after it, and nothing beyond what apply verified.
    assert disk in (image.expected(set()), image.expected({0})), (tmp_path / "log").read_text()


def test_readback_failure_prevents_success(tmp_path, binaries, image):
    trace, _, _ = run_restore(tmp_path, binaries, image, gui=True, fault="readback")
    assert "READBACK" in trace and "RC=1" in trace, trace
    assert "GROW" not in trace and "GUI-DONE" not in trace and "RELEASE" not in trace


def test_a_manifest_without_the_blake3_list_writes_nothing(tmp_path, binaries, image):
    """#3332: the list is required -- the partition is not written on XXH64 alone, and the failure says why."""
    trace, disk, ranges = run_restore(tmp_path, binaries, image, gui=True, fault="no-b3")
    assert "RC=1" in trace and "BLAKE3" in trace and "[stream]" in trace, trace
    assert "READBACK" not in trace and "GUI-DONE" not in trace and ranges == [], trace
    assert disk != image.expected()


def test_a_manifest_without_the_blake3_list_writes_nothing(tmp_path, binaries, image):
    """#3332: the list is required -- the partition is not written on XXH64 alone, and the failure says why."""
    trace, disk, ranges = run_restore(tmp_path, binaries, image, gui=True, fault="no-b3")
    assert "RC=1" in trace and "BLAKE3" in trace and "[stream]" in trace, trace
    assert "READBACK" not in trace and "GUI-DONE" not in trace and ranges == [], trace
    assert disk != image.expected()


def test_zero_exit_without_ledger_cannot_complete(tmp_path, binaries, image):
    trace, _, _ = run_restore(tmp_path, binaries, image, gui=True, fault="no-ledger")
    assert "RC=1" in trace and "FAIL sda" in trace, trace
    assert "READBACK" not in trace and "GUI-DONE" not in trace


# #3173: the server never names `udpcast` any more (`engine_of` is always `chunk`): that case went with it.
# What is left is the agent's own choice: a format 1 image, or an answer that names no engine, is the legacy writer.
@pytest.mark.parametrize("fmt,engine", [(1, "chunk"), (2, None)])
def test_legacy_selection_is_unchanged(tmp_path, binaries, image, fmt, engine):
    trace, _, ranges = run_restore(tmp_path, binaries, image, fmt=fmt, engine=engine)
    assert "LEGACY" in trace and "RC=0" in trace and ranges == [], trace


def test_pull_response_names_configured_engine_chunk(chunk_engine, derive):
    """The pull's answer names the chunk engine (#3173: the only one) -- once #3231's gate has the image's index ready."""
    _fresh_capture(chunk_engine["ctx"])
    answers = []
    assert wait_for(lambda: answers.append(_pull(chunk_engine)) or answers[-1].status_code == 200), answers[-1].text
    assert answers[-1].json()["engine"] == "chunk"
