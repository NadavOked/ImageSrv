"""‏#1371 — `deploy/apt-repo-consistent.py`: המאגר המקומי של שרת מה-ISO מפרסם
רק חבילות שהקובץ שלהן נמצא בו, ו-Release תואם את האינדקס אחרי השינוי.

הבדיקה הכללית (`assert_consistent`) היא מה ש-apt עצמו דורש: כל `Filename`
קיים, וכל גיבוב ב-Release תואם את הקובץ (ואת התוכן הפרוס של ה-.gz).
"""

from __future__ import annotations

import gzip
import hashlib
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "deploy" / "apt-repo-consistent.py"

STANZAS = {
    "qemu-system-x86": "pool/main/imagesrv/qemu-system-x86_1_amd64.deb",
    "ovmf": "pool/main/imagesrv/ovmf_1_all.deb",
    "openssh-server": "pool/main/o/openssh/openssh-server_10.0p1-7+deb13u4_amd64.deb",
    "dbus-user-session": "pool/main/d/dbus/dbus-user-session_1.16.2-2_amd64.deb",
}
ON_DISK = ("qemu-system-x86", "ovmf")


def _release(suite: Path) -> None:
    """כמו write_index של make-pool.sh: ‏MD5Sum+SHA256, ולכל .gz גם השם הפרוס."""
    files = sorted(p.relative_to(suite).as_posix() for p in suite.rglob("*") if p.is_file())
    lines = ["Origin: Debian", "Suite: stable", "Codename: trixie"]
    for title, algo in (("MD5Sum", "md5"), ("SHA256", "sha256")):
        lines.append(f"{title}:")
        for name in files:
            data = (suite / name).read_bytes()
            lines.append(f" {hashlib.new(algo, data).hexdigest()} {len(data):16d} {name}")
            if name.endswith(".gz"):
                plain = gzip.decompress(data)
                lines.append(f" {hashlib.new(algo, plain).hexdigest()} {len(plain):16d} {name[:-3]}")
    (suite / "Release").write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_repo(root: Path, on_disk=ON_DISK) -> Path:
    for name in on_disk:
        path = root / STANZAS[name]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
    suite = root / "dists" / "trixie"
    index = suite / "main" / "binary-amd64" / "Packages.gz"
    index.parent.mkdir(parents=True)
    body = "\n\n".join(f"Package: {p}\nVersion: 1\nFilename: {f}\nSize: 3" for p, f in STANZAS.items())
    index.write_bytes(gzip.compress((body + "\n").encode(), mtime=0))
    (suite / "main" / "binary-amd64" / "Release").write_text("Component: main\n", encoding="utf-8")
    _release(suite)
    return root


def run(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), str(repo)], capture_output=True, text=True,
                          encoding="utf-8", stdin=subprocess.DEVNULL, timeout=60,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def listed(repo: Path) -> dict[str, str]:
    text = gzip.decompress((repo / "dists/trixie/main/binary-amd64/Packages.gz").read_bytes()).decode()
    out = {}
    for stanza in text.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in stanza.strip().splitlines() if ": " in line)
        if fields:
            out[fields["Package"]] = fields["Filename"]
    return out


def assert_consistent(repo: Path) -> None:
    suite = repo / "dists" / "trixie"
    for filename in listed(repo).values():
        assert (repo / filename).is_file(), f"Packages lists {filename}, which is not in the repo"
    algo = None
    for line in (suite / "Release").read_text(encoding="utf-8").splitlines():
        if not line.startswith(" "):
            algo = {"MD5Sum:": "md5", "SHA256:": "sha256"}.get(line)
            continue
        digest, size, name = line.split()
        path = suite / name
        data = path.read_bytes() if path.exists() else gzip.decompress((suite / (name + ".gz")).read_bytes())
        assert (hashlib.new(algo, data).hexdigest(), int(size)) == (digest, len(data)), name


def test_packages_whose_file_is_missing_are_no_longer_published(tmp_path):
    repo = make_repo(tmp_path / "repo")
    proc = run(repo)
    assert proc.returncode == 0, proc.stdout
    assert set(listed(repo)) == set(ON_DISK)
    assert_consistent(repo)
    assert "2 רשומות בלי קובץ הוסרו" in proc.stdout
    assert proc.stdout.splitlines()[-1].startswith("apt-repo: עקבי — 2 חבילות")


def test_a_consistent_repo_is_left_byte_for_byte(tmp_path):
    repo = make_repo(tmp_path / "repo", on_disk=tuple(STANZAS))
    before = {p: p.read_bytes() for p in (repo / "dists").rglob("*") if p.is_file()}
    proc = run(repo)
    assert proc.returncode == 0, proc.stdout
    assert {p: p.read_bytes() for p in (repo / "dists").rglob("*") if p.is_file()} == before
    assert "הוסרו" not in proc.stdout


def test_a_second_run_finds_nothing_to_drop(tmp_path):
    repo = make_repo(tmp_path / "repo")
    assert run(repo).returncode == 0
    again = run(repo)
    assert again.returncode == 0 and "הוסרו" not in again.stdout
    assert_consistent(repo)


def test_a_repo_that_cannot_be_read_is_not_called_consistent(tmp_path):
    proc = run(tmp_path / "nothing-here")
    assert proc.returncode == 1 and "לא נבדק" in proc.stdout
    repo = make_repo(tmp_path / "repo")
    (repo / "dists/trixie/main/binary-amd64/Packages.gz").write_bytes(b"not gzip")
    proc = run(repo)
    assert proc.returncode == 1 and "לא נבדק" in proc.stdout
