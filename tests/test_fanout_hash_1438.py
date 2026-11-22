"""#1438 -- the room round and the capture hash and count inside fanout.

`pv | tee | sha256sum` became `fanout -S <sha file> -P <counter>`. Two
things must not move with it:

* **What is hashed.** The manifest's sha256 is the sha256 of the compressed
  stream exactly as uploaded -- byte-identical to what `sha256sum` produced
  before. The test hashes what the fake curl actually received.
* **What the readers parse.** `fanout -S` writes 64 lowercase hex digits and
  a newline; `sha256sum` wrote "<hex>  -". Both readers (`capture.sh` for the
  manifest, `drawers.sh` for the round verdict) take `awk '{print $1}'`, and
  these tests pin the files they read to the format the binary writes.

And one new state that did not exist before: `-S` is written only at a clean
EOF, so "no sha file" is now possible where sha256sum always printed
something. A capture without a hash must be refused, never shipped with
`"sha256":""` for the server to reject an hour later.
"""

from __future__ import annotations

import hashlib
import json

from native import requires_native
from test_agent import BASH
from test_capture_refusals import ONE_PARTITION, capture_run, refusal_reason
from test_stream_honesty import GOOD_FANOUT

pytestmark = requires_native(("bash", BASH))

RAW = b"raw-partclone-bytes-of-p1"
#: zstd that changes the bytes, so "the compressed stream" and "the raw
#: stream" are different hashes and the test can tell which one was taken.
FRAMING_ZSTD = "#!/bin/sh\nprintf 'ZSTD:'\ncat\n"
COMPRESSED = b"ZSTD:" + RAW

#: curl that keeps what it was asked to upload -- the bytes the server gets.
CURL_KEEP = (
    "#!/bin/sh\n"
    "case \"$*\" in */preflight*) echo '{\"status\":\"ok\",\"reason\":\"space checked\"}'; exit 0 ;; esac\n"
    'f=""\n'
    'while [ $# -gt 0 ]; do [ "$1" = "-T" ] && { f="$2"; shift; }; shift; done\n'
    '[ -n "$f" ] && cat "$f" > "$UPLOADED"\n'
    "exit 0\n"
)


def _capture(tmp_path, fanout: str | None = None):
    stubs = {
        "sgdisk": ONE_PARTITION,
        "curl": CURL_KEEP,
        "zstd": FRAMING_ZSTD,
        "partclone.dd": "#!/bin/sh\nprintf '%s' '" + RAW.decode() + "'\n",
    }
    if fanout is not None:
        stubs["fanout"] = fanout
    uploaded = tmp_path / "uploaded.bin"
    box, run, out = capture_run(tmp_path, stubs=stubs,
                                env={"UPLOADED": uploaded.as_posix()})
    return box, run, out, uploaded


def test_the_manifest_sha256_is_the_hash_of_the_uploaded_compressed_bytes(tmp_path):
    box, run, out, uploaded = _capture(tmp_path)
    assert out.strip().endswith("rc=0"), \
        out + (box / "capture.out").read_text(encoding="utf-8", errors="replace")
    assert uploaded.read_bytes() == COMPRESSED
    manifest = json.loads((run / "new-manifest.json").read_text(encoding="utf-8"))
    part = manifest["partitions"][0]
    assert part["sha256"] == hashlib.sha256(COMPRESSED).hexdigest(), part
    assert part["sha256"] != hashlib.sha256(RAW).hexdigest()
    # -P is the counter total_compressed_bytes is summed from (pv -n format).
    assert manifest["total_compressed_bytes"] == len(COMPRESSED), manifest
    # The file the manifest reader parsed: exactly what fanout -S writes.
    assert (run / "sha.1").read_bytes() == \
        hashlib.sha256(COMPRESSED).hexdigest().encode() + b"\n"


#: fanout that delivered every byte and exited 0 -- but wrote no -S.
FANOUT_NO_HASH = GOOD_FANOUT.replace('fanout_eof "$tmp"\n', "", 1)
assert FANOUT_NO_HASH != GOOD_FANOUT, "the fake still hashes"


def test_a_capture_without_a_hash_is_refused_not_shipped_with_an_empty_sha(tmp_path):
    box, run, out, _uploaded = _capture(tmp_path, fanout=FANOUT_NO_HASH)
    reason = refusal_reason(box, run, out)
    assert "sha256 לא חושב" in reason, reason
    assert not (run / "new-manifest.json").exists()
