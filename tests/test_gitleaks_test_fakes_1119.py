"""The `generic-api-key` allowlist in `.gitleaks.toml` covers five fake strings of the chunk-engine tests, and nothing near them.

The publish gate of 19/11 stopped on 17 `generic-api-key` findings in 12 files under tests/:
`0123456789abcdef`, `0123456789abcdef0123456789abcdef`, `5ec7e7c0ffee0123456789abcdef0123`, `0123456789ABCDEF` and
`a1b2c3d4e5f60718293a4b5c6d7e8f90`.
The public CI runs the same gitleaks with the same file, so it would have gone red after the release.

The entries are fully anchored (`^...$`): a real secret that merely CONTAINS or EXTENDS a fake must still be caught.

Two layers, because gitleaks is not installed on every machine (the lab testrunner has it):
  * config tests -- always run, read the TOML and apply the regexes with `re`;
  * a behaviour test -- runs the real gitleaks 8.30.1 when it is found (IMAGESRV_GITLEAKS, or the testrunner's
    /work/tools). Without the binary it is skipped, and says so by name.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FAKES = ("0123456789abcdef", "0123456789abcdef0123456789abcdef", "5ec7e7c0ffee0123456789abcdef0123",
         "0123456789ABCDEF", "a1b2c3d4e5f60718293a4b5c6d7e8f90")
#: close, not identical: each of these must still be reported
NEAR_MISSES = (
    "0123456789abcdef1", "00123456789abcdef", "0123456789abcde",
    "0123456789abcdef0123456789abcdef1", "5ec7e7c0ffee0123456789abcdef01234", "5ec7e7c0ffee0123456789abcdef012",
    "0123456789ABCDEF1", "a1b2c3d4e5f60718293a4b5c6d7e8f901", "a1b2c3d4e5f60718293a4b5c6d7e8f9",
)
GITLEAKS_VERSION = "8.30.1"


def _allowlist() -> list[str]:
    rules = tomllib.loads((ROOT / ".gitleaks.toml").read_text(encoding="utf-8"))["rules"]
    rule = next(r for r in rules if r["id"] == "generic-api-key")
    return rule["allowlist"]["regexes"]


def _allowed(value: str) -> bool:
    return any(re.search(rx, value) for rx in _allowlist())


@pytest.mark.parametrize("fake", FAKES)
def test_each_fake_is_allowed_by_an_entry_that_matches_it_exactly(fake):
    entries = [rx for rx in _allowlist() if re.fullmatch(rx, fake) or rx == f"^{fake}$"]
    assert f"^{fake}$" in entries, f"no fully anchored entry for {fake}"
    assert _allowed(fake)


def test_the_new_entries_are_anchored_at_both_ends():
    for fake in FAKES:
        assert f"^{fake}$" in _allowlist(), fake
    unanchored = [rx for rx in _allowlist() if any(f in rx for f in FAKES) and not (rx.startswith("^") and rx.endswith("$"))]
    assert not unanchored, unanchored


@pytest.mark.parametrize("value", NEAR_MISSES)
def test_a_similar_string_that_is_not_on_the_list_is_not_allowed(value):
    assert not _allowed(value), f"{value} slipped through the allowlist"


def _gitleaks() -> str | None:
    for cand in (os.environ.get("IMAGESRV_GITLEAKS", ""), "/work/tools/gitleaks-8.30.1/gitleaks"):
        if cand and not cand.endswith(".tar.gz") and Path(cand).is_file():
            return cand
    return None


def _pin(workflow: str) -> tuple[str, str, str]:
    text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    version = re.search(r'GITLEAKS_VERSION:\s*"([^"]+)"', text)
    sha = re.search(r'GITLEAKS_SHA256:\s*"([0-9a-f]{64})"', text)
    assert version and sha, f"{workflow}: no GITLEAKS_VERSION / GITLEAKS_SHA256 pin"
    return version.group(1), sha.group(1), text


@pytest.mark.parametrize("workflow", ["tests.yml", "nightly.yml"])
def test_the_suite_workflows_install_the_same_pinned_gitleaks_as_the_secrets_scan(workflow):
    """#3943: the behaviour test below is a failure, not a skip, under IMAGESRV_REQUIRE_NATIVE=1 -- the public runner has
    to have the binary, by the pin the secrets scan runs, and the test has to be told where it is."""
    version, sha, text = _pin(workflow)
    assert (version, sha) == _pin("gitleaks.yml")[:2], f"{workflow} pins another gitleaks than gitleaks.yml"
    assert version == GITLEAKS_VERSION, f"{workflow} pins {version}, the test checks for {GITLEAKS_VERSION}"
    assert 'echo "IMAGESRV_GITLEAKS=' in text and "sha256sum -c -" in text, f"{workflow}: the pin is not verified and exported"


def test_the_real_gitleaks_allows_the_fakes_and_still_reports_the_near_misses(tmp_path):
    binary = _gitleaks()
    if binary is None:
        pytest.skip("gitleaks 8.30.1 not found (IMAGESRV_GITLEAKS or /work/tools/gitleaks-8.30.1/gitleaks): the config tests above still ran")
    version = subprocess.run([binary, "version"], capture_output=True, text=True, stdin=subprocess.DEVNULL, check=True).stdout.strip().lstrip("v")
    assert version == GITLEAKS_VERSION, f"gitleaks {version}, the public CI runs {GITLEAKS_VERSION}"
    tree = tmp_path / "tree"
    (tree / "tests").mkdir(parents=True)
    (tree / ".gitleaks.toml").write_text((ROOT / ".gitleaks.toml").read_text(encoding="utf-8"), encoding="utf-8")
    for i, fake in enumerate(FAKES):
        (tree / "tests" / f"fake_{i}.py").write_text(f'KEY = "{fake}"\n', encoding="utf-8")
    for i, near in enumerate(NEAR_MISSES):
        (tree / "tests" / f"near_{i}.py").write_text(f'KEY = "{near}"\n', encoding="utf-8")
    report = tmp_path / "report.json"
    proc = subprocess.run(
        [binary, "dir", str(tree), "--config", str(tree / ".gitleaks.toml"), "--no-banner", "--redact",
         "--report-format", "json", "--report-path", str(report)],
        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    # 0 = clean, 1 = findings; anything else is the scan itself breaking (principle 5)
    assert proc.returncode == 1, f"expected findings (rc 1), got rc {proc.returncode}: {proc.stderr[-400:]}"
    hit = {Path(f["File"]).name for f in json.loads(report.read_text(encoding="utf-8"))}
    fakes_hit = sorted(n for n in hit if n.startswith("fake_"))
    missed = sorted(f"near_{i}.py" for i in range(len(NEAR_MISSES)) if f"near_{i}.py" not in hit)
    assert not fakes_hit, f"the allowlisted fakes are still reported: {fakes_hit}"
    assert not missed, f"a near miss is NOT reported (gitleaks may not flag it at all, or the allowlist is too wide): {missed}"
