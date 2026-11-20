#!/usr/bin/env python3
"""The vulnerability report of a release's SBOM: grype's JSON, summarised.

    python3 -I tools/iso/grype_report.py GRYPE.json [--sbom CDX.json] [--min-matches N]

grype matches the packages of the ISO's SBOM (tools/iso/sbom.sh) against its
vulnerability database. This prints the count by severity, and every Critical
and High finding that has a fixed version -- those get an Issue, by hand
(SECURITY.md decides what is reachable). Writes the same to $GITHUB_STEP_SUMMARY.

Two things measured on v0.94.3 (14/11) shape it:
  * A finding is (id, package, version) once: grype matched the same .deb up to
    four times (56,064 matches without --distro, 6,496 with it).
  * The ISO's pool carries Debian's original .deb AND the security update in
    pool/main/imagesrv/ (libbrotli1 1.1.0-2+b7 next to 1.1.0-2+deb13u1); apt
    installs the newer one. With --sbom, a finding on a version is "superseded"
    when the SBOM has another version of the same package that grype did NOT
    match for that id -- grype's own judgement, no version arithmetic here.
    Superseded findings are counted, not listed.

Exit 0: a report. Exit 2: the file is not grype's JSON, or grype did not say
which database it used, or there are fewer than --min-matches matches (the
canary SBOM must have some): "no vulnerability" from a scan that did not run is
not a clean scan (principle 5).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ORDER = ["Critical", "High", "Medium", "Low", "Negligible", "Unknown"]


def findings(doc: dict) -> dict[tuple[str, str, str], dict]:
    """(id, package, version) -> severity, fix state and versions, artifact type; once each."""
    out: dict[tuple[str, str, str], dict] = {}
    for m in doc["matches"]:
        vuln, art = m["vulnerability"], m["artifact"]
        key = (vuln["id"], art["name"], art["version"])
        fix = vuln.get("fix") or {}
        out.setdefault(key, {"severity": vuln.get("severity") or "Unknown", "state": fix.get("state") or "?",
                             "fixed": list(fix.get("versions") or []), "type": art.get("type", "?")})
    return out


def sbom_versions(cdx: dict) -> dict[str, set[str]]:
    versions: dict[str, set[str]] = defaultdict(set)
    for c in cdx.get("components", []):
        if c.get("name") and c.get("version"):
            versions[c["name"]].add(c["version"])
    return versions


def superseded(found: dict, versions: dict[str, set[str]]) -> set[tuple[str, str, str]]:
    return {(vid, name, ver) for (vid, name, ver) in found
            if any(v != ver and (vid, name, v) not in found for v in versions.get(name, ()))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("grype_json", type=Path)
    ap.add_argument("--sbom", type=Path, help="the CycloneDX SBOM grype scanned (marks superseded findings)")
    ap.add_argument("--min-matches", type=int, default=0)
    a = ap.parse_args(argv)
    try:
        doc = json.loads(a.grype_json.read_text(encoding="utf-8"))
        matches = doc["matches"]
        db = doc["descriptor"]["db"]
        found = findings(doc)
        versions = sbom_versions(json.loads(a.sbom.read_text(encoding="utf-8"))) if a.sbom else {}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"grype-report: CANNOT VERIFY -- not grype's JSON or not an SBOM ({exc!r})")
        return 2
    if not db:
        print("grype-report: CANNOT VERIFY -- grype names no vulnerability database")
        return 2
    if len(matches) < a.min_matches:
        print(f"grype-report: CANNOT VERIFY -- {len(matches)} matches, at least {a.min_matches} expected")
        return 2
    gone = superseded(found, versions)
    live = {k: v for k, v in found.items() if k not in gone}
    by_severity = Counter(v["severity"] for v in live.values())
    severe = sorted(((v["severity"], vid, name, ver, ", ".join(v["fixed"]), v["type"])
                     for (vid, name, ver), v in live.items()
                     if v["severity"] in ("Critical", "High") and v["state"] == "fixed"),
                    key=lambda r: (ORDER.index(r[0]), r[2], r[1]))
    built = db.get("built") or db.get("status", {}).get("built") or "?"
    lines = [f"## grype -- {len(matches)} matches, {len(found)} findings, {len(gone)} superseded in the "
             f"SBOM by another version; database built {built}", "",
             " · ".join(f"{s} {by_severity[s]}" for s in ORDER if by_severity[s]) or "no findings", "",
             f"Critical and High with a fixed version: {len(severe)}", ""]
    if severe:
        lines += ["| severity | id | package | version | fixed in | found as |", "|---|---|---|---|---|---|"]
        lines += [f"| {s} | {i} | {n} | {v} | {f} | {t} |" for s, i, n, v, f, t in severe]
    text = "\n".join(lines) + "\n"
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
