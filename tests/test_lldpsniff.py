"""imagesrv-lldpsniff — פריים LLDP ל-JSON (#1048).

מקמפל את agent/lldpsniff.c ומריץ `--from-file` בלי סוקט. רץ במעבדה;
מדולג בווינדוס (`requires_native`, gcc + POSIX).
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "agent" / "lldpsniff.c"

needs_cc = requires_native(
    ("cc", shutil.which("gcc") or shutil.which("cc")),
    posix=True, why="lldpsniff נבנה על המעבדה בלבד",
)


def tlv(typ: int, value: bytes) -> bytes:
    n = len(value)
    assert 0 <= typ <= 127 and 0 <= n <= 511
    return struct.pack("!H", (typ << 9) | n) + value


def lldp_frame(*, chassis_mac=bytes.fromhex("aabbccddeeff"),
               port=b"Gi1/0/12", sysname=b"sw-lab-1",
               portdesc=b"lab port", ttl=120) -> bytes:
    payload = (
        tlv(1, bytes([4]) + chassis_mac)
        + tlv(2, bytes([5]) + port)
        + tlv(3, struct.pack("!H", ttl))
        + tlv(4, portdesc)
        + tlv(5, sysname)
        + tlv(0, b"")
    )
    dst = bytes.fromhex("0180c200000e")
    src = bytes.fromhex("001122334455")
    return dst + src + bytes.fromhex("88cc") + payload


@pytest.fixture(scope="module")
def binary(tmp_path_factory):
    out = tmp_path_factory.mktemp("build") / "imagesrv-lldpsniff"
    build = subprocess.run(
        ["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(out), str(SOURCE)],
        capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return out


def run_from_file(binary: Path, path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(binary), "--from-file", str(path)],
        capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)


def test_lldpsniff_c_stays_under_200_lines():
    n = len(SOURCE.read_text(encoding="utf-8").splitlines())
    assert n <= 200, n


def test_builder_compiles_lldpsniff():
    builder = (REPO / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    assert "lldpsniff.c" in builder
    assert 'imagesrv-lldpsniff"' in builder or "imagesrv-lldpsniff'" in builder


@needs_cc
def test_a_recorded_frame_becomes_json(binary, tmp_path):
    path = tmp_path / "frame.bin"
    path.write_bytes(lldp_frame())
    proc = run_from_file(binary, path)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out == {
        "switch": "sw-lab-1",
        "chassis": "aa:bb:cc:dd:ee:ff",
        "port": "Gi1/0/12",
        "port_desc": "lab port",
        "ttl": 120,
    }


@needs_cc
def test_non_ascii_bytes_in_a_switch_name_still_yield_valid_json(binary, tmp_path):
    """שם מתג עם בייט שאינו ASCII (latin-1 `é`, או זבל) — הפלט חייב להישאר JSON
    תקין ב-ASCII, אחרת ה-hello **כולו** נדחה בשרת כ-UTF-8 פגום. הבייט מקודד
    כ-``\u00e9``; מה שהוא "אומר" פחות חשוב ממה שהוא לא שובר."""
    path = tmp_path / "frame.bin"
    path.write_bytes(lldp_frame(sysname=b"sw-caf" + bytes([0xE9, 0x20, 0xFF]), port=b"1\"2"))
    proc = run_from_file(binary, path)
    assert proc.returncode == 0, proc.stderr
    proc.stdout.encode("ascii")                       # אין בייט גולמי ≥0x80
    out = json.loads(proc.stdout)
    assert out["switch"] == "sw-café ÿ" and out["port"] == '1"2'


@needs_cc
def test_truncated_frame_does_not_crash_missing_fields_are_null(binary, tmp_path):
    path = tmp_path / "trunc.bin"
    path.write_bytes(lldp_frame()[:20])
    proc = run_from_file(binary, path)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["switch"] is None
    assert out["chassis"] is None
    assert out["port"] is None
    assert out["port_desc"] is None
    assert out["ttl"] is None


@needs_cc
def test_empty_file_is_unheard_rc_2(binary, tmp_path):
    path = tmp_path / "empty.bin"
    path.write_bytes(b"")
    proc = run_from_file(binary, path)
    assert proc.returncode == 2, proc.stdout
    assert json.loads(proc.stdout) == {"unheard": True, "waited_s": 0}


@needs_cc
def test_missing_file_is_listen_error_rc_3(binary, tmp_path):
    proc = run_from_file(binary, tmp_path / "no-such.bin")
    assert proc.returncode == 3
    out = json.loads(proc.stdout)
    assert "error" in out and out["error"]
