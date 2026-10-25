"""tools/security-recheck.py (#1975) — הלוגיקה, בלי apt ובלי רשת.

הכלי מכריע "פגיע / לא פגיע" לפי השוואת גרסאות Debian. השוואה שגויה היא
"נקי" שקרי (עיקרון 5), ולכן היא נבדקת מול דוגמאות מ-deb-version(7) ומול
המקרים שבאמת פגשנו (‏binNMU ‏`+b9`, ‏`~deb13u1`, ‏epoch).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("security_recheck", REPO / "tools" / "security-recheck.py")
sr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sr)


def test_version_order() -> None:
    less = [
        ("1.0~rc1", "1.0"),
        ("1:1.37.0-6", "1:1.37.0-6+b9"),
        ("2.12-9+deb13u2", "2.12-9+deb13u3"),
        ("16.1-2~deb13u1", "16.1-2"),
        ("6.12.107-1", "6.12.113-1"),
        ("9.9", "1:0.1"),
        ("43.0.0-3", "43.0.0-3+deb13u1"),
        ("1.2.3", "1.2.10"),
    ]
    for a, b in less:
        assert sr.version_cmp(a, b) < 0, (a, b)
        assert sr.version_cmp(b, a) > 0, (b, a)
    assert sr.version_cmp("0.0.20-1.1~deb13u1", "0.0.20-1.1~deb13u1") == 0


def test_package_groups_reads_sections_and_inline_tags() -> None:
    text = ("# header\n# --- [installer] x ---\ndnsmasq\ngit   # #1185\n"
            "# --- [toolbox] y ---\nnwipe\ndebootstrap  # [installer] live\n")
    assert sr.package_groups(text) == {"installer": ["dnsmasq", "git", "debootstrap"],
                                       "toolbox": ["nwipe"]}


def test_debian_rows_keeps_only_what_is_still_open_here() -> None:
    def cve(status, fixed=None, urgency="not yet assigned", nodsa=None):
        rel = {"status": status, "urgency": urgency}
        if fixed:
            rel["fixed_version"] = fixed
        if nodsa:
            rel["nodsa"] = nodsa
        return {"releases": {"trixie": rel}}
    tracker = {"busybox": {
        "CVE-OPEN": cve("open", nodsa="Minor issue"),
        "CVE-FIXED-HERE": cve("resolved", "1:1.37.0-6"),
        "CVE-FIXED-LATER": cve("resolved", "1:1.37.0-7"),
        "CVE-NOT-AFFECTED": cve("resolved", "0"),
        "CVE-UNIMPORTANT": cve("open", urgency="unimportant"),
    }, "other": {"CVE-X": cve("open")}}
    rows, unimportant = sr.debian_rows(tracker, {"busybox": ("1:1.37.0-6+b9", {"initramfs"})})
    got = {r[4]: r for r in rows}
    assert set(got) == {"CVE-OPEN", "CVE-FIXED-LATER"}
    assert got["CVE-OPEN"][7] == "no-dsa"
    assert "apt offers 1:1.37.0-6+b9" in got["CVE-FIXED-LATER"][5]
    assert unimportant == 1


def test_no_apt_is_unchecked_not_clean(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sr.shutil, "which", lambda name: None)
    assert sr.main(["--tag", "HEAD", "--repo", str(REPO)]) == 2
    out = capsys.readouterr()
    assert out.out == "" and out.err.startswith("unchecked:")


def test_dependabot_only_without_gh_is_unchecked(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sr.shutil, "which", lambda name: None)
    assert sr.main(["--dependabot-only"]) == 2
    assert capsys.readouterr().err.startswith("unchecked:")


def test_closure_resolves_without_recommends_like_every_install(monkeypatch) -> None:
    """#1987 (lab/int-1022): every server/initramfs install is
    --no-install-recommends, so the closure the re-check scans is that one --
    a Recommends-only package (python3-python-multipart) is not on the server,
    and listing its CVEs would be a finding about a machine we do not ship."""
    seen: list[list[str]] = []

    class Done:
        returncode, stdout, stderr = 0, "Inst dnsmasq (2.90 Debian:13)\n", ""

    def run(argv, **kw):
        seen.append(list(argv))
        return Done()

    monkeypatch.setattr(sr.subprocess, "run", run)
    assert sr.closure(["dnsmasq"]) == {"dnsmasq"}
    (argv,) = seen
    assert argv[:2] == ["apt-get", "-s"] and "install" in argv, argv
    assert "--no-install-recommends" in argv, argv
