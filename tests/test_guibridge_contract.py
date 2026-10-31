"""#1538: tests/guibridge-contract.sh runs as part of pytest.

The script is the GUI bridge's contract (agent/lib/gui/guibridge.sh): HTTP and
cookie refusal, gui_auth's output, the record parser, direct-open,
restore-start (#1073), room-open and class-pick. Nothing ran it, so when
#1499 made gui_auth print the theme after the role, its auth check failed
under `set -e` and the six cases after it stopped running -- silently, for
weeks.

Here it is run, and judged by positive evidence: exit 0 **and** every PASS
line, by name, in order. An exit 0 that printed fewer cases is a failure.

It needs only `sh`: every jq the script reaches is a shell function stub,
except gui_saved_theme, whose "auto" fallback is the expected answer with
jq and without it. A missing `sh` is a declared skip (requires_native) --
and a failure where IMAGESRV_REQUIRE_NATIVE is set.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from native import requires_native

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tests" / "guibridge-contract.sh"
SH = shutil.which("sh")

EXPECTED = [
    "PASS HTTP/cookie rejection, including transport failure with HTTP 200",
    "PASS auth stdout and unresolved-role refusal",
    "PASS record boundaries, duplicate/partial rejection, literal shell text",
    "PASS direct-open body and malformed-slots refusal",
    "PASS restore-start hands off only on the registered machine name (#1073)",
    "PASS room-open posts image and count as arguments",
    "PASS class-pick is refused, no hand-off",
]


@requires_native(("sh", SH), why="tests/guibridge-contract.sh is POSIX sh")
def test_guibridge_contract_runs_every_case(tmp_path):
    out = tmp_path / "contract.out"
    with open(out, "wb") as sink:
        done = subprocess.run([SH, SCRIPT.as_posix()], cwd=ROOT, stdout=sink,
                              stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                              timeout=120, check=False)
    text = out.read_text(encoding="utf-8", errors="replace")
    passed = [line.rstrip("\r") for line in text.splitlines() if line.startswith("PASS")]
    assert done.returncode == 0 and passed == EXPECTED, (
        f"rc={done.returncode}, {len(passed)}/{len(EXPECTED)} cases passed\n{text}")
