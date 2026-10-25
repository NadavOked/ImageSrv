#!/usr/bin/env python3
"""security-recheck.py — #1975: חולשות ידועות בתלויות של תג נתון.

שימוש (על מכונת Debian 13 עם apt — ה-testrunner; לא בווינדוס):

  python3 -I tools/security-recheck.py --repo . --tag v1.0.0 \\
      [--tracker FILE] [--baseline PREV.tsv] > run.tsv

מה הוא בודק, ומאיפה:

* **חבילות Debian** — הרשימה היא `tools/iso/packages.txt` **בתג** (האיחוד
  של המתקין, ה-initramfs, הגואי, ארגז הכלים וה-ISO; ‏test_iso_build.py
  שומר שהיא שלמה). לכל קבוצה (`# --- [installer]` וכו') ‏apt-cache מחשב
  את סגור התלויות (Depends/PreDepends) על **המכונה הזו**, וכל חבילה
  ממופה לחבילת המקור שלה ולגרסה ש-apt היה מתקין היום (trixie +
  trixie-security). מול security-tracker.debian.org מודפס כל CVE שלא
  נסגר ב-trixie, או שנסגר בגרסה חדשה ממה ש-apt מציע. CVE בדרגת
  ‏`unimportant` נספר ולא מודפס.
* **הצמדות פייתון** — `server/requirements.txt` בתג מול גרסת חבילת
  ה-python3-* של Debian (#113): הצמדה שאינה הגרסה שנפרסת מסומנת.
* **הקונסולה** — `<script src>`/`<link href>`/`import` לכתובת חיצונית
  ב-`server/static` בתג, וה-JS שהועתק ל-`server/static/vendor` (noVNC)
  נבדק כחבילת Debian שה-README שלו נוקב בה.
* **Dependabot** — רק עם `--dependabot` ו-`gh` מחובר. בלעדיו מודפס
  "not checked" — לא "אין התראות". ל-testrunner אין GitHub, ולכן בתחנת
  הפיתוח מריצים בנפרד `--dependabot-only` (בלי apt ובלי תג).

הפלט: TSV ל-stdout (שורת כותרת ראשונה). עם `--baseline` (פלט של ריצה
קודמת) שורה שלא הייתה שם מסומנת NEW. מה **לא** נבדק: האם הקוד הפגיע
נגיש — זו הכרעה, והיא כתובה ב-SECURITY.md וב-#1975.

קודי יציאה: 0 — נבדק (גם כשיש ממצאים); 2 — לא נבדק (אין apt-cache, התג
לא נמצא, ה-tracker לא נקרא, רשימות apt ישנות מ-`--max-age-days`). "לא
הצלחנו לבדוק" אינו "אין חולשות" (עיקרון 5).
"""

from __future__ import annotations

import argparse
import email.utils
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

TRACKER_URL = "https://security-tracker.debian.org/tracker/data/json"
RELEASE = "trixie"
# Debian wraps some sources in a signed template; the tracker files CVEs
# under the unsigned source.
SIGNED = {"linux-signed-amd64": "linux", "grub-efi-amd64-signed": "grub2",
          "shim-signed": "shim"}
# PyPI name -> Debian binary, where it is not python3-<lowercase name>.
PYPI_DEB = {"pyopenssl": "python3-openssl"}
HEADER = re.compile(r"^#\s*---\s*\[([\w-]+)\]")
INLINE = re.compile(r"#\s*\[([\w-]+)\]")
EXTERNAL_JS = re.compile(
    r"""<script[^>]*\bsrc\s*=\s*["']?(?:https?:)?//|<link[^>]*\bhref\s*=\s*["']?(?:https?:)?//"""
    r"""|\bimport\b[^;\n]*["'](?:https?:)?//""", re.I)


class Unchecked(Exception):
    """The check itself could not run -- exit 2, never an empty table."""


# --- Debian version order (deb-version(7)) ------------------------------------

def _order(c: str) -> int:
    if c == "~":
        return -1
    if c.isdigit():
        return 0
    if c.isalpha():
        return ord(c)
    return ord(c) + 256


def _cmp_part(a: str, b: str) -> int:
    while a or b:
        na = re.match(r"^[^\d]*", a).group(0)
        nb = re.match(r"^[^\d]*", b).group(0)
        for i in range(max(len(na), len(nb))):
            oa = _order(na[i]) if i < len(na) else 0
            ob = _order(nb[i]) if i < len(nb) else 0
            if oa != ob:
                return -1 if oa < ob else 1
        a, b = a[len(na):], b[len(nb):]
        da = re.match(r"^\d*", a).group(0)
        db = re.match(r"^\d*", b).group(0)
        if int(da or 0) != int(db or 0):
            return -1 if int(da or 0) < int(db or 0) else 1
        a, b = a[len(da):], b[len(db):]
    return 0


def _split(v: str) -> tuple[int, str, str]:
    epoch, _, rest = v.rpartition(":") if ":" in v else ("0", "", v)
    up, _, rev = rest.rpartition("-") if "-" in rest else (rest, "", "0")
    return int(epoch or 0), up, rev


def version_cmp(a: str, b: str) -> int:
    ea, ua, ra = _split(a)
    eb, ub, rb = _split(b)
    if ea != eb:
        return -1 if ea < eb else 1
    return _cmp_part(ua, ub) or _cmp_part(ra, rb)


# --- inputs -------------------------------------------------------------------

def git_show(repo: str, tag: str, path: str) -> str:
    r = subprocess.run(["git", "-C", repo, "show", f"{tag}:{path}"],
                       capture_output=True, text=True, encoding="utf-8",
                       stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise Unchecked(f"git show {tag}:{path}: {r.stderr.strip()}")
    return r.stdout


def package_groups(text: str) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    group = "ungrouped"
    for line in text.splitlines():
        m = HEADER.match(line)
        if m:
            group = m.group(1)
            continue
        name = line.split("#", 1)[0].strip()
        if not name:
            continue
        tag = INLINE.search(line)
        groups.setdefault(tag.group(1) if tag else group, []).append(name)
    if not groups:
        raise Unchecked("tools/iso/packages.txt lists no packages")
    return groups


def apt(*args: str) -> str:
    r = subprocess.run(["apt-cache", *args], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise Unchecked(f"apt-cache {args[0]}: {r.stderr.strip()[:300]}")
    return r.stdout


def closure(pkgs: list[str]) -> set[str]:
    """What `apt-get install PKGS` installs on an empty machine.

    The resolver itself, simulated against an empty dpkg status, with
    --no-install-recommends like every install of the server and the
    initramfs (#1987 -- python3-python-multipart came only as a Recommends
    and is no longer there). It picks one alternative per OR-group --
    `apt-cache depends --recurse` follows every alternative and lists cups and
    keepassxc for a DHCP server.
    """
    with tempfile.NamedTemporaryFile(prefix="sec-recheck-status-") as status:
        r = subprocess.run(["apt-get", "-s", "-q", "-o", f"Dir::State::status={status.name}",
                            "-o", "Debug::NoLocking=1", "install", "--no-install-recommends", *pkgs],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise Unchecked(f"apt-get -s install: {r.stderr.strip()[:300]}")
    names = set(re.findall(r"^Inst (\S+) ", r.stdout, re.M))
    missing = {p for p in pkgs if p not in names}
    if missing:
        raise Unchecked(f"apt would not install: {' '.join(sorted(missing))}")
    return names


def sources(binaries: set[str]) -> dict[str, tuple[str, str] | None]:
    """binary -> (source, source version), as apt would install it today."""
    res: dict[str, tuple[str, str] | None] = {}
    for stanza in apt("show", "--no-all-versions", *sorted(binaries)).split("\n\n"):
        f = dict(re.findall(r"^([\w-]+): (.*)$", stanza, re.M))
        if "Package" not in f:
            continue
        src, ver = f.get("Source", f["Package"]), f["Version"]
        m = re.match(r"^(\S+) \((\S+)\)$", src)
        if m:
            src, ver = m.groups()
        if src in SIGNED:
            # The wrapper's own version means nothing to the tracker; the
            # signed binary names the unsigned build it carries.
            m = re.search(rf"(?:^|, ){re.escape(SIGNED[src])} \(= (\S+)\)", f.get("Built-Using", ""))
            if not m:
                res[f["Package"]] = None        # a metapackage: its dependency carries it
                continue
            src, ver = SIGNED[src], m.group(1)
        res[f["Package"]] = (src, ver)
    missing = binaries - res.keys()
    if missing:
        raise Unchecked(f"apt-cache show gave no version for: {' '.join(sorted(missing))}")
    return res


def apt_lists_age_days() -> float:
    newest = 0.0
    lists = "/var/lib/apt/lists"
    for name in os.listdir(lists):
        if name.endswith("InRelease") and "trixie-security" in name:
            with open(os.path.join(lists, name), encoding="utf-8", errors="replace") as fh:
                m = re.search(r"^Date: (.*)$", fh.read(4096), re.M)
            if m:
                newest = max(newest, email.utils.parsedate_to_datetime(m.group(1)).timestamp())
    if not newest:
        raise Unchecked("no trixie-security InRelease in /var/lib/apt/lists -- apt does not see security updates")
    return (time.time() - newest) / 86400


def load_tracker(path: str | None) -> dict:
    try:
        if path:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        with urllib.request.urlopen(TRACKER_URL, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise Unchecked(f"security tracker: {exc}") from exc


# --- the checks -----------------------------------------------------------------

def debian_rows(tracker: dict, used: dict[str, tuple[str, set[str]]]) -> tuple[list, int]:
    rows, unimportant = [], 0
    for src, (ver, groups) in sorted(used.items()):
        for cve, entry in sorted(tracker.get(src, {}).items()):
            rel = entry.get("releases", {}).get(RELEASE)
            if not rel:
                continue
            fixed = rel.get("fixed_version", "")
            status = rel.get("status", "?")
            if status == "resolved" and fixed and version_cmp(ver, fixed) >= 0:
                continue
            if status == "resolved" and not fixed:
                continue
            if rel.get("urgency") == "unimportant":
                unimportant += 1
                continue
            note = "no-dsa" if rel.get("nodsa") else ""
            if status == "resolved":
                status = f"fixed in {fixed}, apt offers {ver}"
            rows.append(["debian", ",".join(sorted(groups)), src, ver, cve, status,
                         rel.get("urgency", ""), note,
                         f"https://security-tracker.debian.org/tracker/{cve}"])
    return rows, unimportant


def pin_rows(req: str, debver: dict[str, tuple[str, str]]) -> list:
    rows = []
    for name, pin in re.findall(r"^([A-Za-z0-9_.-]+)==(\S+)", req, re.M):
        deb = PYPI_DEB.get(name.lower(), f"python3-{name.lower()}")
        ver = debver[deb][1] if deb in debver else apt_version(deb)
        upstream = _split(ver)[1].split("+")[0] if ver else ""
        if upstream != pin:
            rows.append(["pin", "server", name, pin, "-",
                         f"pin {pin} != Debian {deb} {ver or 'missing'}", "", "", "#113"])
    return rows


def apt_version(binary: str) -> str:
    m = re.search(r"^Version: (\S+)", apt("show", "--no-all-versions", binary), re.M)
    return m.group(1) if m else ""


def vendor_sources(repo: str, tag: str) -> dict[str, str]:
    """Debian source -> version for JS vendored under server/static/vendor.

    Each vendored tree names the Debian package it was copied from in its
    README (`Debian package `novnc` 1:1.6.0-2`); that is what the tracker
    knows. A vendored tree without that line cannot be checked -- exit 2.
    """
    r = subprocess.run(["git", "-C", repo, "ls-tree", "-r", "--name-only", tag,
                        "server/static/vendor"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise Unchecked(f"git ls-tree {tag} server/static/vendor: {r.stderr.strip()}")
    found: dict[str, str] = {}
    for top in sorted({"/".join(p.split("/")[:4]) for p in r.stdout.split()}):
        m = re.search(r"Debian package `([\w.+-]+)` (\S+)", git_show(repo, tag, f"{top}/README.md"))
        if not m:
            raise Unchecked(f"{top}/README.md does not name the Debian package it came from")
        found[m.group(1)] = m.group(2)
    return found


def console_rows(repo: str, tag: str) -> list:
    r = subprocess.run(["git", "-C", repo, "ls-tree", "-r", "--name-only", tag, "server/static"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode != 0 or not r.stdout.strip():
        raise Unchecked(f"git ls-tree {tag} server/static: {r.stderr.strip() or 'empty'}")
    rows = []
    for path in r.stdout.split():
        if not path.endswith((".html", ".js", ".css", ".mjs")):
            continue
        for n, line in enumerate(git_show(repo, tag, path).splitlines(), 1):
            if EXTERNAL_JS.search(line):
                rows.append(["console", "console", path, str(n), "-",
                             "external script/style", "", "", line.strip()[:120]])
    return rows


def dependabot_rows(enabled: bool, repo_slug: str) -> list:
    if not enabled:
        return [["dependabot", "-", "-", "-", "-", "not checked (--dependabot not given)", "", "", ""]]
    if not shutil.which("gh"):
        return [["dependabot", "-", "-", "-", "-", "not checked (gh not installed here)", "", "", ""]]
    r = subprocess.run(["gh", "api", f"repos/{repo_slug}/dependabot/alerts", "--paginate",
                        "--jq", '.[] | select(.state=="open") | [.number, .security_advisory.severity,'
                        ' .dependency.package.name, .dependency.manifest_path,'
                        ' (.security_advisory.cve_id // .security_advisory.ghsa_id)] | @tsv'],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise Unchecked(f"gh api dependabot: {r.stderr.strip()[:300]}")
    return [["dependabot", f"alert #{n}", pkg, manifest, cve, "open", sev, "", ""]
            for n, sev, pkg, manifest, cve in (ln.split("\t") for ln in r.stdout.splitlines())]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--tag")
    ap.add_argument("--tracker", help="local copy of the tracker JSON (default: download)")
    ap.add_argument("--baseline", help="a previous run's TSV; rows not in it are marked NEW")
    ap.add_argument("--max-age-days", type=float, default=3.0)
    ap.add_argument("--dependabot", action="store_true")
    ap.add_argument("--dependabot-only", action="store_true",
                    help="only the Dependabot rows -- for the workstation, where gh is and apt is not")
    ap.add_argument("--github-repo", default="NadavOked/ImageCtl-archive")
    a = ap.parse_args(argv)
    if not a.dependabot_only and not a.tag:
        ap.error("--tag is required (or --dependabot-only)")
    try:
        if a.dependabot_only:
            if not shutil.which("gh"):
                raise Unchecked("gh not found -- Dependabot needs the workstation")
            rows = dependabot_rows(True, a.github_repo)
            print("	".join(["kind", "groups", "component", "version", "cve", "status",
                             "urgency", "note", "source"]))
            for row in rows:
                print("	".join(row))
            return 0
        if not (shutil.which("apt-cache") and shutil.which("apt-get")):
            raise Unchecked("apt-cache/apt-get not found -- run on a Debian 13 host")
        age = apt_lists_age_days()
        if age > a.max_age_days:
            raise Unchecked(f"apt lists are {age:.1f} days old -- run apt-get update first")
        groups = package_groups(git_show(a.repo, a.tag, "tools/iso/packages.txt"))
        used: dict[str, tuple[str, set[str]]] = {}
        debver: dict[str, tuple[str, str]] = {}
        for group, pkgs in groups.items():
            for binary, sv in sources(closure(pkgs)).items():
                if sv is None:
                    continue
                src, ver = debver[binary] = sv
                old = used.get(src)
                # Several binaries of one source can differ (binNMU, a stale
                # one): the oldest decides.
                if old is None or version_cmp(ver, old[0]) < 0:
                    used[src] = (ver, old[1] if old else set())
                used[src][1].add(group)
        for src, ver in vendor_sources(a.repo, a.tag).items():
            used.setdefault(src, (ver, set()))[1].add("console-vendor")
        tracker = load_tracker(a.tracker)
        if "openssl" not in tracker:
            raise Unchecked("tracker JSON has no 'openssl' entry -- not the tracker format")
        rows, unimportant = debian_rows(tracker, used)
        rows += pin_rows(git_show(a.repo, a.tag, "server/requirements.txt"), debver)
        rows += console_rows(a.repo, a.tag)
        rows += dependabot_rows(a.dependabot, a.github_repo)
        seen: set[tuple[str, str]] = set()
        if a.baseline:
            with open(a.baseline, encoding="utf-8") as fh:
                seen = {(c[2], c[4]) for c in (ln.rstrip("\n").split("\t") for ln in fh) if len(c) > 4}
    except Unchecked as exc:
        print(f"unchecked: {exc}", file=sys.stderr)
        return 2
    print("\t".join(["kind", "groups", "component", "version", "cve", "status",
                     "urgency", "note", "source", "new"]))
    for row in rows:
        new = "NEW" if a.baseline and (row[2], row[4]) not in seen else ""
        print("\t".join(row + [new]))
    print(f"# tag={a.tag} sources={len(used)} apt-lists-age={age:.1f}d "
          f"rows={len(rows)} unimportant-not-shown={unimportant}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
