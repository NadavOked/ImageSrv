#!/usr/bin/env python3
"""tools/license_manifest.py -- the list of packages an image ships, and the gate on the non-free ones (#3077).

build_initramfs.sh knows, for every packed file, which Debian package owns it
(it already copies that package's copyright file). It hands this script one
line per package, tab separated:

    name  version  section  copyright-file

and this script

  * writes the list the image ships beside its licences: name, version,
    section, and the license ids found in the copyright file (the "License:"
    fields -- read, not guessed; a firmware package, whose copyright file
    holds a hundred of them, points at the shipped copyright file instead); and
  * stops the build (exit 1) when a package of a non-free section is not in the
    declared list (tools/nonfree-declared.txt), or has an empty section: "could
    not tell" is not "free" (principle 5). A declared package is one whose
    notice is in THIRD_PARTY_NOTICES.md (tests/test_license_manifest_3077.py).

  python3 -I tools/license_manifest.py --declared FILE --out FILE < packages.tsv

0 = written and every non-free package is declared. 1 = an undeclared or
unclassified package (named on stderr, nothing written). 2 = bad input.
"""

import argparse
import re
import sys

LICENSE_FIELD = re.compile(r"^License:[ \t]*(.+?)[ \t]*$", re.M)
NONFREE = re.compile(r"^non-free")


def declared(path):
    names = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                names.add(line)
    return names


def license_ids(copyright_path):
    with open(copyright_path, encoding="utf-8", errors="replace") as f:
        found = LICENSE_FIELD.findall(f.read())
    ids = []
    for value in found:
        for tok in re.split(r"\s+(?:and|or)\s+|,", value):
            tok = tok.strip()
            if tok and tok not in ids:
                ids.append(tok)
    return ids


MAX_IDS = 8   # a firmware package's copyright file carries one License: per vendor (hundreds)


def license_cell(name, ids):
    if not ids:
        return "unknown"
    if len(ids) > MAX_IDS:
        return f"{len(ids)} license ids, see usr/share/doc/licenses/{name}.copyright"
    return ", ".join(ids)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--declared", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    try:
        ok = declared(args.declared)
    except OSError as e:
        print(f"license_manifest: cannot read {args.declared}: {e}", file=sys.stderr)
        return 2
    rows, bad = [], []
    for n, line in enumerate(sys.stdin.read().splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 4 or not parts[0] or not parts[1]:
            print(f"license_manifest: line {n} is not name<TAB>version<TAB>section<TAB>copyright: {line!r}", file=sys.stderr)
            return 2
        name, version, section, copyright_path = parts
        try:
            ids = license_ids(copyright_path)
        except OSError as e:
            print(f"license_manifest: cannot read {copyright_path}: {e}", file=sys.stderr)
            return 2
        if not section:
            bad.append(f"{name} {version}: no section -- cannot tell if it is free")
        elif NONFREE.match(section) and name not in ok:
            bad.append(f"{name} {version} ({section}) is in the image but not in {args.declared}"
                       " -- add its notice to THIRD_PARTY_NOTICES.md and declare it, or take it out")
        rows.append((name, version, section, license_cell(name, ids)))
    if not rows:
        print("license_manifest: no packages at all -- the list was not produced", file=sys.stderr)
        return 2
    if bad:
        for b in bad:
            print("license_manifest: " + b, file=sys.stderr)
        return 1
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write("# package\tversion\tsection\tlicenses (License: fields of the copyright file)\n")
        for r in sorted(rows):
            f.write("\t".join(r) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
