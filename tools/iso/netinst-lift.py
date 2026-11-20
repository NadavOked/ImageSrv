#!/usr/bin/env python3
"""tools/iso/netinst-lift.py -- the netinst's own packages that trixie-security has replaced (#3217).

The ISO's pool is the netinst's pool with ours merged into it. Ours holds the
closure of packages.txt plus debootstrap's base -- and nothing else. A package
the netinst carries that is in neither stays at the netinst's version, even
when trixie-security has a fix for it. The SBOM (`grype`, v0.94.3) found one:

    apt 3.0.4, apt-utils 3.0.4, libapt-pkg7.0 3.0.4   -- in our pool (the base)
    apt-transport-https 3.0.3                         -- netinst only: CVE-2026-109518

`apt-transport-https` is a transitional package (Section oldlibs, Priority
optional, `Depends: apt (>= 1.5~alpha4)`): nothing requests it and nothing pins
it exactly, so neither the closure nor `check-pool.py` ever reached it.

  python3 -I tools/iso/netinst-lift.py names <netinst Packages[.gz]>...
      the name of every package the netinst carries, one per line

  apt-cache policy <those names> | python3 -I tools/iso/netinst-lift.py lift <netinst Packages[.gz]>...
      `name=version` for every one whose candidate comes from a *-security suite and is newer
      than the newest version the netinst carries -- the ones make-pool.sh downloads

0 = answered (a possibly empty list). 2 = not checked: an index that cannot be read, no
packages in it, or an `apt-cache policy` that answered nothing usable -- "could not check"
is not "nothing to lift" (principle 5). A package apt does not know at all (dropped from
the archive since the netinst was cut) has no candidate: it is said on stderr, not lifted.
"""

import importlib.util
import os
import re
import sys

_spec = importlib.util.spec_from_file_location(
    "check_pool", os.path.join(os.path.dirname(os.path.abspath(__file__)), "check-pool.py"))
check_pool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_pool)

HEADER = re.compile(r"^(\S+):$")
CANDIDATE = re.compile(r"^\s+Candidate:\s+(\S+)\s*$")
VERSION = re.compile(r"^\s+(?:\*\*\*\s+)?(\S+)\s+-?\d+\s*$")
SOURCE = re.compile(r"^\s+-?\d+\s+\S+\s+([^\s/]+)/\S+\s+\S+\s+Packages\s*$")


class NotChecked(Exception):
    pass


def parse_policy(text):
    """{name: (candidate or None, {version: {suite, ...}})} from `apt-cache policy` output."""
    out, name, version = {}, None, None
    for line in text.splitlines():
        m = HEADER.match(line)
        if m:
            name, version = m.group(1), None
            out[name] = [None, {}]
            continue
        if name is None:
            continue
        m = CANDIDATE.match(line)
        if m:
            out[name][0] = None if m.group(1) == "(none)" else m.group(1)
            continue
        m = SOURCE.match(line)
        if m and version is not None:
            out[name][1][version].add(m.group(1))
            continue
        m = VERSION.match(line)
        if m:
            version = m.group(1)
            out[name][1].setdefault(version, set())
    return {n: (c, v) for n, (c, v) in out.items()}


def lift(index, policy_text):
    """([name=version], [names apt does not know]) -- see the module docstring."""
    policy = parse_policy(policy_text)
    if not policy:
        raise NotChecked("apt-cache policy answered no package")
    lifts, unknown = [], []
    for name in sorted(index):
        entry = policy.get(name)
        if entry is None or entry[0] is None:
            unknown.append(name)
            continue
        candidate, versions = entry
        if candidate not in versions:
            raise NotChecked(f"{name}: candidate {candidate} is not in its own version table")
        if (any(s.endswith("-security") for s in versions[candidate])
                and check_pool.vcmp(candidate, index[name]["Version"]) > 0):
            lifts.append(f"{name}={candidate}")
    if len(unknown) == len(index):
        raise NotChecked(f"apt knows none of the {len(index)} packages of the netinst")
    return lifts, unknown


def main(argv):
    if len(argv) < 3 or argv[1] not in ("names", "lift"):
        print(__doc__.split("\n\n")[0], file=sys.stderr)
        print("usage: netinst-lift.py names|lift <Packages[.gz]>...", file=sys.stderr)
        return 2
    try:
        index = check_pool.newest(argv[2:])
        if not index:
            raise NotChecked("no packages in " + " ".join(argv[2:]))
        if argv[1] == "names":
            print("\n".join(sorted(index)))
            return 0
        lifts, unknown = lift(index, sys.stdin.read())
    except NotChecked as e:
        print(f"netinst-lift: not checked: {e}", file=sys.stderr)
        return 2
    except (OSError, ValueError, EOFError, UnicodeDecodeError) as e:
        print(f"netinst-lift: not checked: {e}", file=sys.stderr)
        return 2
    if unknown:
        print(f"netinst-lift: {len(unknown)} netinst packages have no candidate in apt "
              f"(dropped from the archive?): {' '.join(unknown[:20])}{' ...' if len(unknown) > 20 else ''}",
              file=sys.stderr)
    print("\n".join(lifts))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
