"""#4210: שורת הקליטה ב-README מתארת את מסלול ה-BitLocker בפועל."""

import json
import re
from pathlib import Path

from native import requires_native
from test_agent import BASH
from test_bitlocker_raw_1840 import _capture

pytestmark = requires_native(("bash", BASH))
README = Path(__file__).resolve().parent.parent / "README.md"


def test_readme_describes_raw_bitlocker_capture(tmp_path, monkeypatch):
    box, run, out = _capture(tmp_path, monkeypatch, "")
    assert out.strip().endswith("rc=0"), out + (box / "capture.out").read_text(
        encoding="utf-8", errors="replace")
    manifest = json.loads((run / "new-manifest.json").read_text(encoding="utf-8"))
    encrypted = next(part for part in manifest["partitions"] if part["index"] == 3)
    assert encrypted["fs"] == "BitLocker", encrypted
    assert encrypted["expandable"] is False, encrypted
    ran = (run / "partclone.ran").read_text(encoding="utf-8")
    assert re.search(r"^partclone\.dd .*-s \S*/sda3 ", ran, re.M), ran

    row = next(line for line in README.read_text(encoding="utf-8").splitlines()
               if line.startswith("| **קליטה** |"))
    assert "מועתק סקטור־אחר־סקטור, בלי פענוח" in row, row
    assert f'`fs: "{encrypted["fs"]}"`' in row, row
