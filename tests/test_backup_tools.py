"""‏#1128 (R41): כלי הגיבוי — `tools/verify-images.py`, ‏`tools/backup-server.sh`
ו-`tools/restore-drill.sh`.

‏verify-images רץ בכל מקום (Python בלבד). שני הסקריפטים הם bash עם rsync
ו-sha256sum — רצים על לינוקס (מעבדת ה-VM / ‏CI הציבורי), ומדולגים בשמם
בווינדוס. תרגיל השחזור מרים `server.main` אמיתי על עותק — זו הראיה
שהגיבוי משוחזר, לא רק שנכתב."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from native import requires_native
from test_installer_1131 import find_bash

REPO = Path(__file__).resolve().parent.parent
VERIFY = REPO / "tools" / "verify-images.py"
BACKUP_SH = REPO / "tools" / "backup-server.sh"
DRILL_SH = REPO / "tools" / "restore-drill.sh"
BASH = find_bash()


def _image(root: Path, name: str, parts: dict[str, bytes], *, unverified: bool = False) -> Path:
    d = root / name
    d.mkdir(parents=True)
    manifest = {"id": name, "name": name, "family": "windows", "min_target_bytes": 1,
                "partitions": []}
    for i, (fname, payload) in enumerate(parts.items(), start=1):
        (d / fname).write_bytes(payload)
        manifest["partitions"].append({"index": i, "file": fname,
                                       "sha256": hashlib.sha256(payload).hexdigest()})
    manifest["partitions"].append({"index": 9, "file": None, "sha256": None, "role": "swap"})
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if unverified:
        (d / "manifest.unverified").write_text("", encoding="utf-8")
    return d


def _verify(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(VERIFY), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, timeout=60)


def test_verify_images_passes_on_matching_hashes_and_skips_in_progress(tmp_path: Path):
    _image(tmp_path, "win-a", {"p1.img": b"a" * 100, "p2.img": b"b" * 50})
    _image(tmp_path, ".capture-42", {"p1.img": b"half"})       # קליטה באמצע — מדולג בשם
    r = _verify(str(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "verified 1 images, 2 partitions, 0 problems" in r.stdout
    assert "SKIP (in progress)" in r.stdout


def test_verify_images_names_a_corrupt_partition(tmp_path: Path):
    d = _image(tmp_path, "win-a", {"p1.img": b"a" * 100})
    (d / "p1.img").write_bytes(b"a" * 99 + b"X")
    r = _verify(str(tmp_path))
    assert r.returncode == 1
    assert "BAD SHA256" in r.stderr and "p1.img" in r.stderr


def test_verify_images_names_a_missing_partition_and_unverified_import(tmp_path: Path):
    d = _image(tmp_path, "win-a", {"p1.img": b"a"})
    (d / "p1.img").unlink()
    _image(tmp_path, "win-b", {"p1.img": b"b"}, unverified=True)
    r = _verify(str(tmp_path))
    assert r.returncode == 1
    assert "MISSING" in r.stderr and "UNVERIFIED" in r.stderr


def test_verify_images_zero_images_is_not_a_pass(tmp_path: Path):
    """‏עיקרון 5: "לא היה מה לבדוק" ≠ "נבדק ותקין" — אלא בבקשה מפורשת."""
    r = _verify(str(tmp_path))
    assert r.returncode == 3 and "NO IMAGES" in r.stderr
    assert _verify("--allow-empty", str(tmp_path)).returncode == 0


# --- הסקריפטים: לינוקס בלבד -------------------------------------------------

posix_tools = requires_native(("bash", BASH), "sha256sum", "curl", posix=True,
                              why="backup-server.sh/restore-drill.sh הם bash+coreutils")


def _seed_server(tmp_path: Path) -> tuple[Path, Path]:
    """‏data_dir עם DB אמיתי של השרת (WAL, משתמש) ותיקיית אימג'ים עם אימג' אחד."""
    from server.agent import registry
    from server.console import users
    from server.db import connect
    data = tmp_path / "data"
    images = tmp_path / "images"
    data.mkdir()
    conn = connect(data / "imagesrv.db")
    users.create(conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    conn.execute("INSERT INTO groups (id, label, role, sort) VALUES ('cl', 'cloners', 'cloner', 0)")
    conn.commit()
    registry.add_machine(conn, "aa:bb:cc:dd:ee:01", "cloner-1", "cl", "test")
    conn.close()
    (data / "branding").mkdir()
    (data / "branding" / "logo.png").write_bytes(b"\x89PNG")
    (data / "storage-test").mkdir()
    (data / "storage-test" / "probe").write_bytes(b"x")
    _image(images, "win-a", {"p1.img": b"a" * 4096})
    _image(images, ".capture-7", {"p1.img": b"half"})
    # שורש מערכת מזויף (--system-root): הסקריפט לא קורא את /etc של המכונה
    # שמריצה את הטסט — ב-CI יש שם קובץ iSCSI שרק root קורא.
    root = tmp_path / "sysroot"
    (root / "etc" / "dnsmasq.d").mkdir(parents=True)
    (root / "etc" / "dnsmasq.d" / "imagesrv-dhcp.conf").write_text("dhcp-range=...\n")
    (root / "etc" / "dnsmasq.d" / "other.conf").write_text("not ours\n")
    (root / "etc" / "nftables.conf").write_text("table inet imagesrv {}\n")
    (root / "etc" / "fstab").write_text("UUID=1 / ext4 defaults 0 1\n//nas/img /srv/imagesrv/images cifs imagesrv 0 0\n")
    return data, images


def _sh(script: Path, *args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, str(script), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
                          timeout=timeout, cwd=str(REPO),
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})


@posix_tools
def test_backup_server_writes_a_consistent_db_data_dir_and_verified_images(tmp_path: Path):
    data, images = _seed_server(tmp_path)
    dest = tmp_path / "dest"
    r = _sh(BACKUP_SH, "--dest", str(dest), "--data-dir", str(data), "--images", str(images),
            "--app-dir", str(REPO), "--system-root", str(tmp_path / "sysroot"))
    assert r.returncode == 0, r.stdout + r.stderr
    dbs = sorted((dest / "db").glob("imagesrv-*.db"))
    assert len(dbs) == 1
    copy = sqlite3.connect(f"file:{dbs[0]}?mode=ro", uri=True)
    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert copy.execute("SELECT count(*) FROM machines").fetchone()[0] == 1
    assert copy.execute("SELECT count(*) FROM users WHERE username='noc'").fetchone()[0] == 1
    assert not (dest / "data" / "branding").exists()             # #1302: שארית, לא הגדרה
    assert not (dest / "data" / "imagesrv.db").exists()          # רק דרך db/
    assert not (dest / "data" / "storage-test").exists()
    assert (dest / "images" / "win-a" / "manifest.json").exists()
    assert not (dest / "images" / ".capture-7").exists()
    sysdirs = list((dest / "system").iterdir())
    assert len(sysdirs) == 1
    sysd = sysdirs[0]
    assert (sysd / "etc" / "dnsmasq.d" / "imagesrv-dhcp.conf").exists()
    assert not (sysd / "etc" / "dnsmasq.d" / "other.conf").exists()     # לא שלנו
    assert (sysd / "etc" / "nftables.conf").exists()
    assert (sysd / "fstab.imagesrv").read_text() == "//nas/img /srv/imagesrv/images cifs imagesrv 0 0\n"
    sums = list(dest.glob("SHA256SUMS-*"))
    assert len(sums) == 1 and dbs[0].name in sums[0].read_text(encoding="utf-8")
    assert oct(dbs[0].stat().st_mode & 0o777) == "0o600"        # umask 077 — סודות


@posix_tools
def test_backup_server_fails_when_a_backed_up_image_is_corrupt(tmp_path: Path):
    """‏rsync שסיים אינו "גיבוי תקין": אימג' שהמניפסט שלו שקרי מפיל את הריצה."""
    data, images = _seed_server(tmp_path)
    (images / "win-a" / "p1.img").write_bytes(b"corrupt")
    r = _sh(BACKUP_SH, "--dest", str(tmp_path / "dest"), "--data-dir", str(data),
            "--images", str(images), "--app-dir", str(REPO), "--system-root", str(tmp_path / "sysroot"))
    assert r.returncode != 0
    assert "BAD SHA256" in r.stderr


@posix_tools
def test_restore_drill_brings_up_a_server_on_the_copy(tmp_path: Path):
    data, images = _seed_server(tmp_path)
    port = 18480 + (os.getpid() % 200) * 3
    r = _sh(DRILL_SH, "--data-dir", str(data), "--images", str(images), "--app-dir", str(REPO),
            "--system-root", str(tmp_path / "sysroot"), "--port", str(port), "--wait", "90", timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS" in r.stdout and '"ok":true' in r.stdout
    assert "1 = 1" in r.stdout                               # מספר האימג'ים שווה
    assert "1 מכונות" in r.stdout


def test_the_docs_describe_backup_and_restore():
    text = (REPO / "docs" / "server-install.md").read_text(encoding="utf-8")
    for needle in ("backup-server.sh", "verify-images.py", "restore-drill.sh",
                   "הורד גיבוי הגדרות", "RPO"):
        assert needle in text, needle
