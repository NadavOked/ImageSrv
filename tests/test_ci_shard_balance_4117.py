"""#4117: the public `tests` shards are balanced by measured seconds, not by a hash.

`tools/git/ci_shard_weights.json` holds the seconds of every test file from one
full public run; `ci_shard.py` deals the files out longest-first (LPT) onto the
least loaded shard. What must hold, and what these tests pin:

  * the committed weights split into shards whose load is within 1.2x
    (max/min), counting the serial phase that sits on shard 1;
  * a file is never split -- all its tests land in the same shard;
  * a file the weights do not know falls back to the old sha256 rule (a new
    test file must not break the run), and a weights file that cannot be read
    is an error, not a quiet return to the hash (principle 5);
  * `weigh` builds the table from the gate artifacts, and refuses a junit
    entry it cannot tie to a collected file;
  * the placement does not depend on the order the weights are read in.

The shard count (4) and `lab_gate`'s whole-tree check are tested in
test_ci_shard_2952.py and are not touched here.
"""
from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# The module under test is the one public file of tools/git/ (ci_shard.py is in [public]); the directory is
# not, so the path is taken from the file, as test_ci_shard_2952.py does.
SHARD = ROOT / "tools" / "git" / "ci_shard.py"
sys.path.insert(0, str(SHARD.parent))
import ci_shard  # noqa: E402

WEIGHTS = ROOT / "tools" / "git" / "ci_shard_weights.json"
DOC = json.loads(WEIGHTS.read_text(encoding="utf-8"))


def _loads(n: int) -> list[float]:
    """Worker-seconds per shard as `shard_of` places the committed weights."""
    load = [0.0] * n
    for name, secs in DOC["files"].items():
        load[ci_shard.shard_of(f"{name}::test_x", False, n) - 1] += secs
    load[0] += DOC["serial_s"] * DOC["workers"]
    return load


@pytest.mark.parametrize("n", [4, 6])
def test_the_committed_weights_split_within_1_2_of_each_other(n):
    load = _loads(n)
    assert max(load) / min(load) <= 1.2, [round(x) for x in load]


def test_the_weights_are_a_real_table_not_a_stub():
    assert len(DOC["files"]) > 500
    assert DOC["serial_s"] > 0 and DOC["workers"] >= 1
    assert all(w >= 0 for w in DOC["files"].values())
    assert DOC["source"], "a number without its run and environment is a quote, not evidence (principle 5b)"


def test_a_file_is_never_split_between_shards():
    names = sorted(DOC["files"])[:300]
    for name in names:
        placed = {ci_shard.shard_of(f"{name}::{case}", False, 4)
                  for case in ("test_a", "TestC::test_b[1-::1]", "test_c[שלום]")}
        assert len(placed) == 1, (name, placed)


def test_a_serial_test_stays_on_shard_1_whatever_its_files_weight():
    heaviest = max(DOC["files"], key=DOC["files"].get)
    assert ci_shard.shard_of(f"{heaviest}::test_s", True, 4) == 1


def test_a_file_without_a_weight_falls_back_to_the_old_hash():
    for i in range(300):
        nodeid = f"tests/test_brand_new_{i}.py::test_x[{i}]"
        digest = hashlib.sha256(nodeid.encode("utf-8")).digest()
        assert ci_shard.shard_of(nodeid, False, 4) == 1 + int.from_bytes(digest[:8], "big") % 4


def test_the_placement_does_not_depend_on_the_order_of_the_weights():
    items = list(DOC["files"].items())
    first = ci_shard.plan(dict(items), DOC["serial_s"], DOC["workers"], 4)
    random.Random(7).shuffle(items)
    assert ci_shard.plan(dict(items), DOC["serial_s"], DOC["workers"], 4) == first
    assert first == {k: ci_shard.shard_of(f"{k}::t", False, 4) for k in first}


def test_equal_weights_tie_by_name_then_by_lower_shard():
    got = ci_shard.plan({"b.py": 5, "a.py": 5, "c.py": 5, "d.py": 5, "e.py": 5}, 0, 4, 4)
    assert got == {"a.py": 1, "b.py": 2, "c.py": 3, "d.py": 4, "e.py": 1}


def test_the_serial_phase_counts_as_load_on_shard_1():
    got = ci_shard.plan({"a.py": 10, "b.py": 10}, 100, 4, 2)  # 400 worker-seconds already on shard 1
    assert got == {"a.py": 2, "b.py": 2}


@pytest.mark.parametrize("content", ["", "not json", "{}", '{"files": [], "serial_s": 1, "workers": 4}',
                                     '{"files": {"a.py": "x"}, "serial_s": 1, "workers": 4}'])
def test_an_unreadable_weights_file_is_an_error_not_the_hash(tmp_path, content):
    bad = tmp_path / "w.json"
    bad.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="cannot read the shard weights"):
        ci_shard._weights(bad)
    with pytest.raises(ValueError, match="cannot read the shard weights"):
        ci_shard._weights(tmp_path / "missing.json")


# --- weigh -----------------------------------------------------------------------

def _gate(root: Path, name: str, k: int, collected: list[str], parallel: dict, serial: dict) -> None:
    d = root / f"{name}-{k}"
    d.mkdir(parents=True)
    (d / "collected.txt").write_text("\n".join(collected) + "\n", encoding="utf-8")
    for phase, cases in (("parallel", parallel), ("serial", serial)):
        body = "".join(f'<testcase classname="{c}" name="{n}" time="{t}"/>' for (c, n), t in cases.items())
        (d / f"run-{phase}.xml").write_text(
            f'<?xml version="1.0"?><testsuites><testsuite name="pytest">{body}</testsuite></testsuites>',
            encoding="utf-8")


def _two_pythons(root: Path, extra: dict | None = None) -> None:
    ids = ["tests/test_a.py::TestK::test_1", "tests/test_a.py::test_2[::1]", "tests/test_b.py::test_3",
           "tests/test_s.py::test_ser"]
    for name, scale in (("gate-py3.12-shard", 1), ("gate-py3.13-shard", 3)):
        for k in (1, 2):
            _gate(root, name, k, ids if k == 1 else [],
                  {("tests.test_a.TestK", "test_1"): 2 * scale, ("tests.test_a", "test_2[::1]"): 1 * scale,
                   ("tests.test_b", "test_3"): 4 * scale, **(extra or {})} if k == 1 else {},
                  {("tests.test_s", "test_ser"): 10 * scale} if k == 1 else {})


def test_weigh_sums_per_file_and_takes_the_mean_of_the_pythons(tmp_path):
    _two_pythons(tmp_path)
    doc = ci_shard.weigh(tmp_path, ["gate-py3.12-shard", "gate-py3.13-shard"], 2, 4, "run X")
    # 3.12: a=3, b=4, serial=10; 3.13: a=9, b=12, serial=30; mean a=6, b=8, serial=20
    assert doc["files"] == {"tests/test_a.py": 6.0, "tests/test_b.py": 8.0}
    assert doc["serial_s"] == 20.0 and doc["workers"] == 4 and doc["source"] == "run X"


def test_weigh_refuses_a_junit_entry_it_cannot_tie_to_a_collected_file(tmp_path):
    _two_pythons(tmp_path, extra={("tests.test_ghost", "test_boo"): 5})
    with pytest.raises(ValueError, match="no collected file for classname 'tests.test_ghost'"):
        ci_shard.weigh(tmp_path, ["gate-py3.12-shard"], 2, 4, "")


def test_weigh_cli_writes_a_table_the_loader_reads(tmp_path, capsys):
    _two_pythons(tmp_path)
    out = tmp_path / "w.json"
    rc = ci_shard.main(["weigh", "--dir", str(tmp_path), "--name", "gate-py3.12-shard", "--shards", "2",
                        "--workers", "4", "--out", str(out), "--source", "run X"])
    assert rc == ci_shard.GREEN
    assert "weigh: 2 files" in capsys.readouterr().out
    files, serial_s, workers = ci_shard._weights(out)
    assert files == {"tests/test_a.py": 3.0, "tests/test_b.py": 4.0} and (serial_s, workers) == (10.0, 4)
