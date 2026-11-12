"""#2952: the public `tests` run split across four runners -- `tools/git/ci_shard.py`.

What the split must never do is lose a test or run one twice and still be
green (principle 5). So the checks here are on the rule itself (every id in
exactly one shard, whatever the order), on the plugin under a REAL pytest --
the shards' junits together are exactly the plain `--collect-only` -- and on
the merge, which must be red, by name, when a shard is missing, unfinished,
from another commit, or ran a test another shard ran too.

The workflows are checked for the one thing the YAML cannot check itself: the
number of shards is written in three places, and they agree.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tools" / "git" / "lab_gate.py"
SHARD = ROOT / "tools" / "git" / "ci_shard.py"
WORKFLOWS = ROOT / ".github" / "workflows"
sys.path.insert(0, str(SHARD.parent))
import ci_shard  # noqa: E402

try:
    import xdist  # noqa: F401
    HAVE_XDIST = True
except ImportError:  # pragma: no cover
    HAVE_XDIST = False

N = 4
SHA = "0123456789abcdef0123456789abcdef01234567"

#: a tree with the shapes that broke id handling before: IPv6 params (#2121),
#: a class, unicode, and `serial` tests that must all land in shard 1.
TESTS = '''import pytest


@pytest.mark.parametrize("addr", ["::1", "[::]:8080", "fe80::1%eth0", "a::b[c]"])
def test_listen(addr):
    pass


@pytest.mark.parametrize("n", range(40))
def test_many(n):
    pass


class TestBox:
    @pytest.mark.parametrize("x", ["1.2", "p/q", "שלום"])
    def test_inner(self, x):
        pass


@pytest.mark.serial
@pytest.mark.parametrize("n", range(5))
def test_load_sensitive(n):
    pass
'''
CONFTEST = '''def pytest_configure(config):
    config.addinivalue_line("markers", "serial: load sensitive (#2118)")
'''


# --- the rule ------------------------------------------------------------------

def _ids(count: int) -> list[str]:
    return [f"tests/test_{i % 97}.py::test_case[{i}-::1-שלום]" for i in range(count)]


def test_every_id_lands_in_exactly_one_shard_and_none_is_lost():
    ids = _ids(15_000)
    shards = {k: [i for i in ids if ci_shard.shard_of(i, False, N) == k] for k in range(1, N + 1)}
    assert sorted(i for part in shards.values() for i in part) == sorted(ids)
    assert sum(len(part) for part in shards.values()) == len(ids)


def test_the_shard_depends_on_the_id_alone_not_on_order():
    ids = _ids(2_000)
    first = {i: ci_shard.shard_of(i, False, N) for i in ids}
    again = {i: ci_shard.shard_of(i, False, N) for i in reversed(ids)}
    assert first == again


def test_the_split_is_balanced_by_count():
    """Not by duration -- but 15,000 ids over 4 shards by sha256 is within a few %."""
    ids = _ids(15_000)
    counts = [sum(ci_shard.shard_of(i, False, N) == k for i in ids) for k in range(1, N + 1)]
    assert all(0.9 * 15_000 / N < c < 1.1 * 15_000 / N for c in counts), counts


def test_a_serial_test_always_goes_to_shard_1():
    assert {ci_shard.shard_of(i, True, N) for i in _ids(500)} == {1}


@pytest.mark.parametrize("spec", ["", "4", "0/4", "5/4", "a/b", "1/2/3", "-1/4"])
def test_a_bad_shard_spec_is_refused(spec):
    with pytest.raises(ValueError):
        ci_shard.parse_spec(spec)


# --- the plugin under a real pytest, and the merge ------------------------------

def _env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("IMAGESRV_", "PYTEST_", "LAB_"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(ROOT)          # `-p tools.git.ci_shard`, as from the repo root
    return env


def _py(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], cwd=cwd, env=_env(),
                          stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180)


def _ids_of(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line]


def _shards(tmp_path: Path, parallel: tuple[str, ...] = ()) -> tuple[Path, Path]:
    """Run the tree as the workflow does: per shard a collect with the plugin, the
    parallel phase and the serial phase, each into its own artifact directory;
    the last shard also collects the whole tree without the plugin."""
    proj = tmp_path / "proj"
    (proj / "tests").mkdir(parents=True)
    (proj / "tests" / "conftest.py").write_text(CONFTEST, encoding="utf-8")
    (proj / "tests" / "test_s.py").write_text(TESTS, encoding="utf-8")
    root = tmp_path / "shards"
    args = ("-p", "no:cacheprovider")
    for k in range(1, N + 1):
        d = root / f"gate-py3.x-shard-{k}"
        d.mkdir(parents=True)
        shard = ("-p", "tools.git.ci_shard", "--ci-shard", f"{k}/{N}")
        c = _py(proj, str(GATE), "collect", "--out", str(d / "collected.txt"), "--", *args, *shard)
        assert c.returncode == 0, c.stdout + c.stderr
        rcs = []
        for phase, mark, extra in (("parallel", "not serial", parallel), ("serial", "serial", ())):
            run = _py(proj, "-m", "pytest", "tests", "-q", *args, "-m", mark, *extra, *shard,
                      f"--junitxml={d / f'run-{phase}.xml'}")
            assert run.returncode in (0, 5), run.stdout + run.stderr
            (d / f"problems-{phase}.json").write_text("[]", encoding="utf-8")
            rcs.append(str(run.returncode))
        (d / "rc").write_text(" ".join(rcs) + "\n", encoding="utf-8")
        (d / "finished").write_text(SHA + "\n", encoding="utf-8")
    whole = root / f"gate-py3.x-shard-{N}" / ci_shard.WHOLE
    c = _py(proj, str(GATE), "collect", "--out", str(whole), "--", *args)
    assert c.returncode == 0, c.stdout + c.stderr
    allowed = tmp_path / "allowed.json"
    allowed.write_text(json.dumps({"entries": []}), encoding="utf-8")
    return root, allowed


def _merge(root: Path, allowed: Path, capsys) -> tuple[int, str]:
    rc = ci_shard.main(["merge", "--dir", str(root), "--name", "gate-py3.x-shard",
                        "--shards", str(N), "--sha", SHA, "--allowed", str(allowed)])
    return rc, capsys.readouterr().out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> tuple[Path, Path]:
    return _shards(tmp_path_factory.mktemp("ci2952"))


@pytest.fixture
def evidence(run, tmp_path) -> tuple[Path, Path]:
    """A private copy of the four shards' artifacts, for a test to break."""
    root, allowed = run
    shutil.copytree(root, tmp_path / "shards")
    return tmp_path / "shards", allowed


def test_the_shards_together_are_exactly_the_whole_collect(run):
    root, _ = run
    whole = _ids_of(root / f"gate-py3.x-shard-{N}" / ci_shard.WHOLE)
    parts = [_ids_of(root / f"gate-py3.x-shard-{k}" / "collected.txt") for k in range(1, N + 1)]
    assert len(whole) == 4 + 40 + 3 + 5
    assert sorted(i for part in parts for i in part) == sorted(whole)
    assert all(parts), "a shard got nothing -- the fixture tree is too small to show the split"
    serial = [i for i in whole if "test_load_sensitive" in i]
    assert set(serial) <= set(parts[0])


def test_the_merge_is_green_on_four_complete_shards(evidence, capsys):
    rc, out = _merge(*evidence, capsys)
    assert rc == ci_shard.GREEN, out
    assert "gate: 52 collected, 52 in junit" in out


def test_a_missing_shard_cannot_be_verified_by_name(evidence, capsys):
    root, allowed = evidence
    shutil.rmtree(root / "gate-py3.x-shard-3")
    rc, out = _merge(root, allowed, capsys)
    assert rc == ci_shard.CANNOT, out
    assert "shard 3/4: no evidence (gate-py3.x-shard-3)" in out


def test_a_shard_without_its_junit_cannot_be_verified(evidence, capsys):
    root, allowed = evidence
    (root / "gate-py3.x-shard-2" / "run-parallel.xml").unlink()
    rc, out = _merge(root, allowed, capsys)
    assert rc == ci_shard.CANNOT, out
    assert "shard 2/4: evidence incomplete, missing run-parallel.xml" in out


def test_no_whole_tree_collect_cannot_be_verified(evidence, capsys):
    root, allowed = evidence
    (root / f"gate-py3.x-shard-{N}" / ci_shard.WHOLE).unlink()
    rc, out = _merge(root, allowed, capsys)
    assert rc == ci_shard.CANNOT, out


def test_an_unfinished_shard_is_red_by_name(evidence, capsys):
    root, allowed = evidence
    (root / "gate-py3.x-shard-2" / "finished").unlink()
    rc, out = _merge(root, allowed, capsys)
    assert rc == ci_shard.RED, out
    assert "shard 2/4: did not finish green" in out


def test_evidence_from_another_commit_is_red(evidence, capsys):
    root, allowed = evidence
    (root / "gate-py3.x-shard-1" / "finished").write_text("f" * 40, encoding="utf-8")
    rc, out = _merge(root, allowed, capsys)
    assert rc == ci_shard.RED, out
    assert "shard 1/4: evidence is for" in out


def test_a_test_that_ran_in_two_shards_is_red(evidence, capsys):
    """Shard 2's junit copied over shard 3's: shard 3's tests did not run, and
    shard 2's ran twice -- both by name."""
    root, allowed = evidence
    shutil.copy(root / "gate-py3.x-shard-2" / "run-parallel.xml",
                root / "gate-py3.x-shard-3" / "run-parallel.xml")
    rc, out = _merge(root, allowed, capsys)
    assert rc == ci_shard.RED, out
    assert "ran in more than one phase" in out
    assert "collected ids did not run" in out


@pytest.mark.skipif(not HAVE_XDIST, reason="pytest-xdist (the lab and the public CI) runs the shards")
def test_the_plugin_holds_under_xdist_workers(tmp_path, capsys):
    """The CI runs each shard with `-n auto --dist load`: the workers must
    import the plugin and deselect the same items, or the merge goes red."""
    root, allowed = _shards(tmp_path, parallel=("-n", "2", "--dist", "load"))
    rc, out = _merge(root, allowed, capsys)
    assert rc == ci_shard.GREEN, out


# --- the workflows ---------------------------------------------------------------

@pytest.mark.parametrize("workflow", ["tests.yml", "nightly.yml"])
def test_the_number_of_shards_agrees_in_the_three_places(workflow):
    text = (WORKFLOWS / workflow).read_text(encoding="utf-8")
    matrix = re.findall(r"^\s+shard: \[([\d, ]+)\]$", text, re.MULTILINE)
    assert len(matrix) == 1, f"{workflow}: one shard matrix"
    listed = [int(x) for x in matrix[0].split(",")]
    n = len(listed)
    assert listed == list(range(1, n + 1))
    assert re.findall(r'--ci-shard "\$SHARD/(\d+)"', text) == [str(n)]
    assert re.findall(r"--shards (\d+)", text) == [str(n)]
    assert re.findall(r"matrix\.shard == (\d+)", text) and \
        set(re.findall(r"matrix\.shard == (\d+)", text)) == {str(n)}, \
        "the extra steps go to the last shard, the one that also collects the whole tree"


def test_the_merge_job_keeps_the_names_the_public_gate_reads():
    """The publish gate requires the checks 'pytest (3.12)' and
    'pytest (3.13)'; the job that reports them must be the merge."""
    text = (WORKFLOWS / "tests.yml").read_text(encoding="utf-8")
    job = text.split("\n  pytest:\n", 1)
    assert len(job) == 2, "tests.yml: no `pytest` job"
    head = job[1].split("\n    steps:", 1)[0]
    assert "name: pytest (${{ matrix.python-version }})" in head
    assert "needs: shard" in head
    assert "if: ${{ !cancelled() }}" in head
    assert "tools/git/ci_shard.py merge" in job[1]
