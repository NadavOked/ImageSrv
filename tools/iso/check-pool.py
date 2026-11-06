#!/usr/bin/env python3
"""tools/iso/check-pool.py -- the merged ISO repository is version-consistent.

The ISO's apt repository is the netinst's pool with ours merged into it, and
the index keeps both versions of a package (`dpkg-scanpackages -m`). Two
readers then pick one version per package: debootstrap takes the last stanza
of a name (dpkg-scanpackages lists versions in ascending order, so the
newest), and apt takes the newest as its candidate. When our pool lifts a
package and leaves behind one that pins it exactly, the pair cannot be
installed together:

    apt-utils 3.0.3 (netinst only)  Depends: apt (= 3.0.3)
    apt       3.0.4 (our pool, trixie-security of 04/11)

debootstrap installed both, and the engine's `apt-get install` stopped at 45%
(`apt-utils : Depends: apt (= 3.0.3) but 3.0.4 is to be installed`, VM test
06/11). This script fails the build instead.

For the newest version of every package, every versioned Depends/Pre-Depends
alternative whose target is a real package in the index must be satisfied by
that target's newest version (or by a versioned Provides). A group with no
satisfied alternative is a skew. A target that is not in the index at all is
not judged here -- a missing package is build-iso's packages.txt check, and
the netinst carries stanzas whose dependencies live on other CDs.

  python3 -I tools/iso/check-pool.py <Packages[.gz]>...

0 = consistent (the last line says how many packages and relations were
checked). 1 = skew, one line per broken relation. 2 = not checked: an index
that cannot be read, or no packages at all -- "could not check" is not
"consistent" (principle 5).
"""

import gzip
import re
import sys

# --- Debian version ordering (Debian Policy 5.6.12) ---------------------------


def _order(c):
    if c == "~":
        return -1
    if c.isalpha():
        return ord(c)
    return ord(c) + 256


def _cmp_part(a, b):
    while a or b:
        na = re.match(r"\D*", a).group()
        nb = re.match(r"\D*", b).group()
        for i in range(max(len(na), len(nb))):
            ca = _order(na[i]) if i < len(na) else 0
            cb = _order(nb[i]) if i < len(nb) else 0
            if ca != cb:
                return -1 if ca < cb else 1
        a, b = a[len(na):], b[len(nb):]
        da = re.match(r"\d*", a).group()
        db = re.match(r"\d*", b).group()
        ia, ib = int(da or 0), int(db or 0)
        if ia != ib:
            return -1 if ia < ib else 1
        a, b = a[len(da):], b[len(db):]
    return 0


def _split(v):
    epoch, _, rest = v.partition(":") if ":" in v else ("0", "", v)
    upstream, _, revision = rest.rpartition("-") if "-" in rest else (rest, "", "0")
    return int(epoch), upstream, revision


def vcmp(a, b):
    ea, ua, ra = _split(a)
    eb, ub, rb = _split(b)
    if ea != eb:
        return -1 if ea < eb else 1
    return _cmp_part(ua, ub) or _cmp_part(ra, rb)


def satisfies(have, op, want):
    c = vcmp(have, want)
    return {"=": c == 0, ">=": c >= 0, "<=": c <= 0, ">>": c > 0, "<<": c < 0,
            ">": c >= 0, "<": c <= 0}[op]


# --- index ---------------------------------------------------------------------

REL = re.compile(r"^\s*([^\s(:]+)(?::\S+)?\s*(?:\(\s*(<<|<=|>=|>>|=|<|>)\s*([^)\s]+)\s*\))?\s*$")


def parse_rel(text):
    """'a (>= 1) | b, c' -> [[('a', '>=', '1'), ('b', None, None)], [('c', None, None)]]"""
    groups = []
    for group in text.split(","):
        alts = []
        for alt in group.split("|"):
            alt = re.sub(r"\[.*?\]|<.*?>", "", alt)
            m = REL.match(alt)
            if not m:
                raise ValueError(f"unparsable relation: {alt.strip()!r}")
            alts.append((m.group(1), m.group(2), m.group(3)))
        groups.append(alts)
    return groups


def read_stanzas(path):
    raw = open(path, "rb").read()
    if path.endswith(".gz"):
        raw = gzip.decompress(raw)
    text = raw.decode("utf-8")
    for block in re.split(r"\n\s*\n", text):
        fields, key = {}, None
        for line in block.splitlines():
            if line[:1] in (" ", "\t") and key:
                fields[key] += " " + line.strip()
            elif ":" in line:
                key, _, val = line.partition(":")
                key = key.strip()
                fields[key] = val.strip()
        if "Package" in fields and "Version" in fields:
            yield fields


def newest(paths):
    best = {}
    for path in paths:
        for st in read_stanzas(path):
            cur = best.get(st["Package"])
            if cur is None or vcmp(st["Version"], cur["Version"]) > 0:
                best[st["Package"]] = st
    return best


def check(paths):
    """(problems, packages, relations checked)."""
    cand = newest(paths)
    provides = {}
    for st in cand.values():
        for group in parse_rel(st.get("Provides", "")) if st.get("Provides") else []:
            for name, _op, ver in group:
                provides.setdefault(name, []).append(ver)
    problems, checked = [], 0
    for name in sorted(cand):
        st = cand[name]
        for field in ("Pre-Depends", "Depends"):
            if not st.get(field):
                continue
            for group in parse_rel(st[field]):
                judged = [a for a in group if a[1] and a[0] in cand]
                if not judged:
                    continue
                checked += 1
                ok = False
                for dep, op, ver in group:
                    if op is None:
                        ok = ok or dep in cand or dep in provides
                    elif dep in cand and satisfies(cand[dep]["Version"], op, ver):
                        ok = True
                    elif any(pv and satisfies(pv, op, ver) for pv in provides.get(dep, [])):
                        ok = True
                if not ok:
                    have = ", ".join(f"{d} {cand[d]['Version']}" for d, _o, _v in judged)
                    want = " | ".join(f"{d} ({o} {v})" if o else d for d, o, v in group)
                    problems.append(f"{name} {st['Version']}: {field}: {want} -- the index has {have}")
    return problems, len(cand), checked


def main(argv):
    if len(argv) < 2:
        print("usage: check-pool.py <Packages[.gz]>...", file=sys.stderr)
        return 2
    try:
        problems, n, checked = check(argv[1:])
    except (OSError, ValueError, EOFError, UnicodeDecodeError) as e:
        print(f"check-pool: not checked: {e}", file=sys.stderr)
        return 2
    if n == 0:
        print("check-pool: not checked: no packages in " + " ".join(argv[1:]), file=sys.stderr)
        return 2
    for p in problems:
        print(f"check-pool: skew: {p}")
    if problems:
        print(f"check-pool: {len(problems)} versioned relations cannot be met by the newest versions "
              f"in the index -- debootstrap and apt would install a broken set. Add the newer "
              f"version of the depending package to the pool (make-pool.sh).", file=sys.stderr)
        return 1
    print(f"check-pool: consistent: {n} packages, {checked} versioned relations")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
