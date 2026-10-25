"""#672 -- target Secure Boot db preflight, before any restore write."""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
BOOTCADB = REPO / "agent" / "lib" / "bootcadb.sh"
DB_GUID = "d719b2cb-3d3a-4596-a3bc-dad00e67656f"
X509_GUID = bytes.fromhex("a159c0a5e494a74a87b5ab155c2bf072")


def find_bash() -> str | None:
    if os.name == "nt":
        for candidate in (r"C:\Program Files\Git\usr\bin\bash.exe",
                          r"C:\Program Files\Git\bin\bash.exe"):
            if Path(candidate).exists():
                return candidate
    return shutil.which("bash")


BASH = find_bash()
pytestmark = requires_native(("bash", BASH))


def posix(path: Path) -> str:
    text = str(path).replace("\\", "/")
    return "/" + text[0].lower() + text[2:] if len(text) > 1 and text[1] == ":" else text


def efi_signature_list(subject: str) -> bytes:
    """Build an EFI_SIGNATURE_LIST with one synthetic X.509 signature."""
    cert = b"SUBJECT=" + subject.encode() + b"\0"
    signature = b"\0" * 16 + cert                 # SignatureOwner + data
    header = X509_GUID + struct.pack("<III", 28 + len(signature), 0, len(signature))
    return b"\x07\0\0\0" + header + signature   # efivar attributes + ESL


def secure_boot(enabled: int) -> bytes:
    return b"\x07\0\0\0" + bytes([enabled])


def fixture_env(tmp_path: Path, *, sb: int | None, db: bytes | None) -> tuple[dict, Path, Path]:
    """efivars, RUN_DIR, manifest and openssl/jq stubs; returns (env, run_dir, manifest)."""
    efivars = tmp_path / "efivars"
    if sb is not None:
        efivars.mkdir()
        (efivars / "SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c").write_bytes(secure_boot(sb))
    if db is not None:
        efivars.mkdir(exist_ok=True)
        (efivars / f"db-{DB_GUID}").write_bytes(db)
    run_dir = tmp_path / "run"
    (run_dir / "targets" / "sda").mkdir(parents=True)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"boot_ca": ["Microsoft UEFI CA 2023"]}), encoding="utf-8")
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "openssl").write_text(
        "#!/bin/sh\n"
        "f=''\nwhile [ $# -gt 0 ]; do [ \"$1\" = -in ] && f=$2; shift; done\n"
        "if grep -q 'SUBJECT=Microsoft UEFI CA 2023' \"$f\"; then\n"
        "  echo 'subject=CN=Microsoft UEFI CA 2023,O=Microsoft Corporation'\n"
        "elif grep -q 'SUBJECT=Other CA' \"$f\"; then\n"
        "  echo 'subject=CN=Other CA,O=Fixture'\n"
        "else exit 1; fi\n",
        encoding="utf-8", newline="\n")
    (tools / "openssl").chmod(0o755)
    (tools / "jq").write_text(
        "#!/bin/sh\necho 'Microsoft UEFI CA 2023'\n",
        encoding="utf-8", newline="\n")
    (tools / "jq").chmod(0o755)
    env = os.environ.copy()
    env.update(EFIVARS_ROOT=posix(efivars), RUN_DIR=posix(run_dir),
               PATH=posix(tools) + ":" + env["PATH"])
    return env, run_dir, manifest


def run_preflight(tmp_path: Path, *, sb: int | None, db: bytes | None) -> tuple[int, dict]:
    env, run_dir, manifest = fixture_env(tmp_path, sb=sb, db=db)
    script = (f"log() {{ :; }}; . {posix(BOOTCADB)!r}; "
              f"boot_ca_preflight {posix(manifest)!r} sda; rc=$?; "
              "printf '%s' \"$rc\"")
    proc = subprocess.run([BASH, "-c", script], env=env, cwd=REPO,
                          stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8")
    target = run_dir / "targets" / "sda"
    result = {"state": (target / "boot_ca_state").read_text(encoding="utf-8"),
              "reason": (target / "boot_ca_reason").read_text(encoding="utf-8")}
    return int(proc.stdout), result


def test_secure_boot_off_is_ok_without_db(tmp_path: Path):
    rc, result = run_preflight(tmp_path, sb=0, db=None)
    assert rc == 0 and result["state"] == "ok"
    assert "כבוי" in result["reason"]


def test_required_ca_present_is_ok(tmp_path: Path):
    rc, result = run_preflight(tmp_path, sb=1, db=efi_signature_list("Microsoft UEFI CA 2023"))
    assert rc == 0 and result["state"] == "ok"


def test_required_ca_absent_blocks(tmp_path: Path):
    rc, result = run_preflight(tmp_path, sb=1, db=efi_signature_list("Other CA"))
    assert rc == 1 and result["state"] == "failed"
    assert result["reason"] == "המחשב לא יאמין לאימג' — Secure Boot יחסום אתחול"


def test_missing_efivars_is_unchecked_not_ok(tmp_path: Path):
    rc, result = run_preflight(tmp_path, sb=None, db=None)
    assert rc == 0 and result["state"] == "unchecked"


def test_corrupt_db_is_unchecked_not_missing_ca(tmp_path: Path):
    rc, result = run_preflight(tmp_path, sb=1, db=b"\x07\0\0\0broken")
    assert rc == 0 and result["state"] == "unchecked"
    assert "לפענח" in result["reason"]


def test_progress_report_carries_explicit_preflight_result(tmp_path: Path):
    run = tmp_path / "report"
    target = run / "targets" / "sda"
    target.mkdir(parents=True)
    for name, value in (("state", "waiting"), ("base", "0"),
                        ("bytes.raw", ""), ("total", "100"),
                        ("boot_ca_state", "unchecked"),
                        ("boot_ca_reason", "db unreadable")):
        (target / name).write_text(value, encoding="utf-8")
    script = (f"export RUN_DIR={posix(run)!r}; "
              f". {posix(REPO / 'agent/lib/common.sh')!r}; "
              f". {posix(REPO / 'agent/lib/progress.sh')!r}; "
              "build_progress session-1 aa:bb:cc:dd:ee:ff")
    proc = subprocess.run([BASH, "-c", script], cwd=REPO, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)["targets"][0]["boot_ca_preflight"]
    assert result == {"state": "unchecked", "reason": "db unreadable"}


def run_restore_gate(tmp_path: Path, *, sb: int | None, db: bytes | None):
    """The real run_restore with the real bootcadb.sh: does the gate sit before apply_gpt?"""
    env, run_dir, manifest = fixture_env(tmp_path, sb=sb, db=db)
    lib = REPO / "agent" / "lib"
    script = (f". {posix(lib / 'common.sh')!r}; . {posix(lib / 'progress.sh')!r}; "
              f". {posix(lib / 'bootcadb.sh')!r}; . {posix(lib / 'restore.sh')!r}; "
              "disk_fits() { return 0; }; "
              "write_check() { return 0; }; write_speed_verdict() { return 0; }; readback_drawers() { :; }; "
              "apply_gpt() { echo APPLY_GPT; return 1; }; fail_written_target() { :; }; "
              f"run_restore multicast sda http://s img {posix(manifest)!r}; "
              'echo "rc=$?"')
    proc = subprocess.run([BASH, "-c", script], env=env, cwd=REPO,
                          stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8")
    target = run_dir / "targets" / "sda"
    return proc, target


def test_restore_refuses_a_missing_ca_before_the_table_is_written(tmp_path: Path):
    proc, target = run_restore_gate(tmp_path, sb=1, db=efi_signature_list("Other CA"))
    assert "rc=1" in proc.stdout and "APPLY_GPT" not in proc.stdout, proc.stdout + proc.stderr
    assert (target / "state").read_text(encoding="utf-8").strip() == "failed"
    assert (target / "error").read_text(encoding="utf-8").strip() ==         "המחשב לא יאמין לאימג' — Secure Boot יחסום אתחול"


def test_restore_without_efivars_is_unchecked_and_reaches_the_table(tmp_path: Path):
    proc, target = run_restore_gate(tmp_path, sb=None, db=None)
    assert "APPLY_GPT" in proc.stdout, proc.stdout + proc.stderr
    assert (target / "boot_ca_state").read_text(encoding="utf-8") == "unchecked"
