"""14/11: the release's SBOM, grype scan and SBOM attestation (.github/workflows/release-sbom.yml,
tools/iso/grype_report.py). The workflow runs only in the public repo, by hand from the
release chain; this reads it as text and runs the report on grype JSON written here.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "release-sbom.yml"
REPORT = ROOT / "tools" / "iso" / "grype_report.py"
PUBLIC = "github.repository == 'NadavOked/ImageSrv'"
PINNED = re.compile(r"^\s*(?:- )?uses: ([\w.-]+/[\w./-]+)@([0-9a-f]{40}) # v?\d+\.\d+\.\d+\s*$")


def _text():
    return WORKFLOW.read_text(encoding="utf-8")


def _grype(tmp_path: Path, matches: list[dict], db: dict | None = None) -> Path:
    p = tmp_path / "g.json"
    p.write_text(json.dumps({"matches": matches, "descriptor": {"db": {"built": "2026-11-14"} if db is None else db}}))
    return p


def _match(sev: str, vid: str, name: str, fixed: list[str]) -> dict:
    fix = {"versions": fixed, "state": "fixed" if fixed else "not-fixed"}
    return {"vulnerability": {"id": vid, "severity": sev, "fix": fix}, "artifact": {"name": name, "version": "1.0"}}


def _report(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(REPORT), *map(str, args)], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=60)


def test_manual_only_public_only_and_least_privilege():
    text = _text()
    on = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert "workflow_dispatch:" in on and "push:" not in on and "pull_request" not in on
    assert re.search(rf"^    if: {re.escape(PUBLIC)}$", text, re.MULTILINE)
    top = re.search(r"^permissions:[ \t]*\n((?: +[^\n]*\n)*)", text, re.MULTILINE)
    assert top and top.group(1).strip() == "contents: read"
    job = re.search(r"^    permissions:\n((?:      [^\n]*\n)+)", text, re.MULTILINE)
    granted = {line.split(":")[0].strip() for line in job.group(1).splitlines()}
    assert granted == {"contents", "id-token", "attestations"}, granted
    uses = [line for line in text.splitlines() if re.match(r"^\s*(?:- )?uses:", line)]
    assert uses and all(PINNED.match(line) for line in uses), uses


def test_the_tools_are_pinned_by_version_and_sha256_and_the_tag_is_checked():
    text = _text()
    assert re.search(r'^  SYFT_VERSION: "1\.55\.0"$', text, re.MULTILINE)
    assert re.search(r'^  SYFT_SHA256: "[0-9a-f]{64}"$', text, re.MULTILINE)
    assert re.search(r'^  GRYPE_SHA256: "[0-9a-f]{64}"$', text, re.MULTILINE)
    assert 'sha256sum -c -' in text
    assert r'^v[0-9]+\.[0-9]+\.[0-9]+$' in text
    sbom_sh = (ROOT / "tools" / "iso" / "sbom.sh").read_text(encoding="utf-8")
    assert re.search(r'^SYFT_VERSION="1\.55\.0"', sbom_sh, re.MULTILINE), "sbom.sh checks the same syft"


def test_the_grype_canary_runs_before_the_iso_is_scanned():
    names = re.findall(r"^      - name: ([^\n]+)$", _text(), re.MULTILINE)
    assert names.index("grype can still find a vulnerability (canary)") < names.index(
        "grype on the ISO's SBOM (report)")
    assert "--min-matches 1" in _text()
    assert "--distro debian:13" in _text() and '--sbom "$s.cdx.json"' in _text()


def test_it_attests_the_sbom_and_not_build_provenance():
    text = _text()
    assert "uses: actions/attest@" in text and "sbom-path:" in text
    assert "attest-build-provenance" not in text, "the ISO is not built here; provenance would name the wrong builder"


def test_report_lists_critical_and_high_that_have_a_fix_once_each(tmp_path):
    g = _grype(tmp_path, [_match("Critical", "CVE-1", "openssl", ["3.0.1"]),
                          _match("Critical", "CVE-1", "openssl", ["3.0.1"]),
                          _match("High", "CVE-2", "zlib", []), _match("Low", "CVE-3", "bash", ["9"])])
    run = _report(g)
    assert run.returncode == 0, run.stdout
    assert "4 matches, 3 findings" in run.stdout
    assert "Critical 1 · High 1 · Low 1" in run.stdout
    assert run.stdout.count("| Critical | CVE-1 | openssl | 1.0 | 3.0.1 |") == 1
    assert "CVE-2" not in run.stdout and "CVE-3" not in run.stdout


def test_a_version_next_to_its_fixed_version_in_the_sbom_is_superseded(tmp_path):
    """v0.94.3: libbrotli1 1.1.0-2+b7 in pool/main/b/ next to 1.1.0-2+deb13u1 in pool/main/imagesrv/."""
    old = _match("Critical", "CVE-9", "libbrotli1", ["1.1.0-2+deb13u1"])
    old["artifact"]["version"] = "1.1.0-2+b7"
    only = _match("High", "CVE-8", "apt-transport-https", ["3.0.4"])
    g = _grype(tmp_path, [old, only])
    cdx = tmp_path / "s.cdx.json"
    cdx.write_text(json.dumps({"components": [{"name": "libbrotli1", "version": "1.1.0-2+b7"},
                                              {"name": "libbrotli1", "version": "1.1.0-2+deb13u1"},
                                              {"name": "apt-transport-https", "version": "1.0"}]}))
    run = _report(g, "--sbom", cdx)
    assert "1 superseded" in run.stdout
    assert "libbrotli1" not in run.stdout and "| High | CVE-8 | apt-transport-https |" in run.stdout


def test_report_cannot_verify_without_a_database_a_json_or_the_canary_matches(tmp_path):
    assert _report(_grype(tmp_path, [], db={})).returncode == 2
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    assert _report(bad).returncode == 2
    assert _report(_grype(tmp_path, []), "--min-matches", "1").returncode == 2
