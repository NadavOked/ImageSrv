"""‏#959 — `tools/drivers/build-package.py`: מ-driver pack של היצרן לחבילה
שהשרת מקבל. הראיה שהפורמט זהה אינה השוואת מבנה — היא **העלאה** של מה
שהכלי בנה ל-`POST /api/console/drivers/upload` והתאמה ל-hello של מכונה
עם אותו דגם / אותו בקר.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.library.drivers import validate_manifest             # noqa: E402

from test_drivers import _hello, _upload                  # noqa: E402
from test_inventory import GOOD as INVENTORY, MAC         # noqa: E402

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "drivers" / "build-package.py"
CAB = REPO / "tests" / "fixtures" / "driver-pack.cab"

NET_INF = (
    '[Version]\nSignature="$Windows NT$"\nClass=Net\n'
    'ClassGUID={4d36e972-e325-11ce-bfc1-08002be10318}\n'
    '[Intel.NTamd64]\n'
    '%E15BC% = E15BC.ndi, PCI\\VEN_8086&DEV_15BC&SUBSYS_86F01043&REV_10\n'
    '%E15BC% = E15BC.ndi, PCI\\VEN_8086&DEV_15BC\n'          # כפול, בלי SUBSYS
    '%E15BD% = E15BD.ndi, PCI\\VEN_8086&DEV_15BD\n'
)
AHCI_INF = (
    '[Version]\nSignature="$Windows NT$"\nClass = HDC\n'
    '[Intel.NTamd64]\n%AHCI% = AHCI.inst, PCI\\VEN_8086&DEV_A352&CC_0106\n'
)
GPU_INF = (
    '[Version]\nSignature="$Windows NT$"\nClass=Display\n'
    '[Intel]\n%GPU% = GPU.inst, PCI\\VEN_8086&DEV_3E92\n'     # לא רשת/אחסון — לא במלאי
)


def fake_pack(root: Path) -> Path:
    pack = root / "sp150000"
    (pack / "net" / "x64").mkdir(parents=True)
    (pack / "ahci").mkdir()
    (pack / "gpu").mkdir()
    # ‏INF של Intel מגיע ב-UTF-16 עם BOM; השאר ASCII. שניהם חייבים להיקרא.
    (pack / "net" / "e1d68x64.inf").write_bytes(b"\xff\xfe" + NET_INF.encode("utf-16-le"))
    (pack / "net" / "x64" / "e1d68x64.sys").write_bytes(b"MZ nic")
    (pack / "ahci" / "iaStorAC.inf").write_text(AHCI_INF, encoding="utf-8")
    (pack / "ahci" / "iaStorAC.sys").write_bytes(b"MZ ahci")
    (pack / "gpu" / "igdlh64.inf").write_text(GPU_INF, encoding="utf-8")
    (pack / "readme.txt").write_text("HP EliteDesk 800 G6 driver pack\n", encoding="utf-8")
    return pack


def run_tool(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), *args], cwd=cwd, capture_output=True,
        text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
    )


def read_tar(path: Path) -> tuple[dict, dict[str, bytes]]:
    with tarfile.open(path, "r:*") as tar:
        members = tar.getmembers()
        assert all(m.isfile() for m in members), "השרת דוחה פריט שאינו קובץ"
        blobs = {m.name: tar.extractfile(m).read() for m in members}
    (folder,) = {n.split("/")[0] for n in blobs}
    manifest = json.loads(blobs[f"{folder}/manifest.json"])
    return manifest, blobs


# --- מתיקייה -----------------------------------------------------------------


def test_a_directory_becomes_a_manifest_with_model_and_pci_any_rules(tmp_path):
    pack = fake_pack(tmp_path)
    out = tmp_path / "pkg.tar"
    proc = run_tool(str(pack), "--vendor", "HP", "--model", "HP EliteDesk 800 G6",
                    "--version", "2.1", "--out", str(out), cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    manifest, blobs = read_tar(out)
    assert validate_manifest(manifest) is None
    assert manifest["name"] == "hp-hp-elitedesk-800-g6"
    assert manifest["match"] == [
        {"vendor": "HP", "model": "HP EliteDesk 800 G6"},
        # 2 INF של רשת/אחסון → 3 מזהים: כפילות ו-SUBSYS נזרקו, ה-GPU לא נכנס.
        {"pci_any": ["8086:15bc", "8086:15bd", "8086:a352"]},
    ]
    assert [f["path"] for f in manifest["files"]] == [
        "ahci/iaStorAC.inf", "ahci/iaStorAC.sys", "gpu/igdlh64.inf",
        "net/e1d68x64.inf", "net/x64/e1d68x64.sys", "readme.txt"]
    sha = {f["path"]: f["sha256"] for f in manifest["files"]}
    assert sha["net/x64/e1d68x64.sys"] == hashlib.sha256(b"MZ nic").hexdigest()
    assert blobs["hp-hp-elitedesk-800-g6/net/x64/e1d68x64.sys"] == b"MZ nic"
    assert "driver pack 2.1" in manifest["description"] and "3 INF" in manifest["description"]
    # הסיכום: דגם, INF, מזהים, גודל, sha256 — כל אחד בשמו, עם המספר.
    assert "HP EliteDesk 800 G6" in proc.stdout
    assert "INF:        3 (רשת/אחסון: 2; דולגו: display=1)" in proc.stdout
    assert "מזהי PCI:   3" in proc.stdout and "קבצים:      6" in proc.stdout
    assert f"{out.stat().st_size:,} bytes" in proc.stdout
    assert hashlib.sha256(out.read_bytes()).hexdigest() in proc.stdout


def test_the_built_package_is_accepted_by_the_server_and_matches_a_hello(server, tmp_path):
    """הראיה שהפורמט זהה: ה-tar שנבנה נכנס, ומכונה עם אותו דגם **או** אותו
    בקר מופיעה ב-`matches`, גם כשהמזהה נכתב בלי class."""
    from conftest import setup_classroom   # noqa: PLC0415
    pack = fake_pack(tmp_path)
    out = tmp_path / "pkg.tar"
    assert run_tool(str(pack), "--vendor", "LENOVO", "--model", "ThinkCentre M720q",
                    "--name", "m720q", "--out", str(out), cwd=tmp_path).returncode == 0
    admin = server["admin"]
    r = _upload(admin, out.read_bytes())
    assert r.status_code == 200, r.text
    assert r.json() == {"name": "m720q", "files": 6}
    setup_classroom(server)
    assert _hello(server).status_code == 200                       # LENOVO / M720q / 8086:15bc:020000
    (pkg,) = admin.get("/api/console/drivers").json()
    assert pkg["matches"] == [MAC]
    # אותה מכונה כ-Dell, בלי הדגם — עדיין תואמת, דרך הבקר בלבד (בלי class במניפסט).
    _hello(server, {**INVENTORY, "dmi": {**INVENTORY["dmi"], "sys_vendor": "Dell Inc."}})
    answer = server["anon"].get(f"/api/v1/agent/drivers?mac={MAC}").json()
    assert [(p["name"], p["by"]) for p in answer["packages"]] == [("m720q", "pci")]
    # ובלי הבקר ובלי הדגם — לא.
    _hello(server, {**INVENTORY, "pci": ["10de:1234:030000"],
                    "dmi": {**INVENTORY["dmi"], "sys_vendor": "Dell Inc."}})
    assert server["anon"].get(f"/api/v1/agent/drivers?mac={MAC}").json()["packages"] == []


def test_without_vendor_the_model_rule_is_not_written_and_the_summary_says_so(tmp_path):
    pack = fake_pack(tmp_path)
    proc = run_tool(str(pack), "--model", "M720q", "--out", str(tmp_path / "p.tar"), cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    manifest, _ = read_tar(tmp_path / "p.tar")
    assert manifest["match"] == [{"pci_any": ["8086:15bc", "8086:15bd", "8086:a352"]}]
    assert "אין כלל דגם" in proc.stdout


@pytest.mark.parametrize("prepare, args, fragment", [
    (lambda p: shutil.rmtree(p / "net") or shutil.rmtree(p / "ahci"), ["--model", "X"], "אין על מה להתאים"),
    (lambda p: (p / "manifest.json").write_text("{}"), ["--vendor", "HP", "--model", "X"], "manifest.json"),
    (lambda p: (p / "עברית.txt").write_text("x"), ["--vendor", "HP", "--model", "X"], "לא ASCII"),
    (lambda p: None, ["--model", "עברית"], "--name"),
    (lambda p: None, ["--vendor", "HP", "--model", "X", "--name", "../x"], "--name"),
], ids=["nothing-to-match", "manifest-present", "non-ascii-path", "hebrew-model", "bad-name"])
def test_refusals_are_by_name_and_leave_no_tar(tmp_path, prepare, args, fragment):
    pack = fake_pack(tmp_path)
    prepare(pack)
    out = tmp_path / "p.tar"
    proc = run_tool(str(pack), *args, "--out", str(out), cwd=tmp_path)
    assert proc.returncode == 1 and fragment in proc.stderr, (proc.stdout, proc.stderr)
    assert not out.exists()


def test_a_source_that_is_neither_a_directory_nor_a_cab_is_refused(tmp_path):
    exe = tmp_path / "sp150000.exe"
    exe.write_bytes(b"MZ")
    proc = run_tool(str(exe), "--vendor", "HP", "--model", "X", cwd=tmp_path)
    assert proc.returncode == 1 and ".cab" in proc.stderr
    assert not list(tmp_path.glob("*.tar"))


# --- מ-CAB ------------------------------------------------------------------


def _cab_extractor_present() -> bool:
    import importlib.util  # noqa: PLC0415
    spec = importlib.util.spec_from_file_location("build_package", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._extractor() is not None


def test_the_cab_fixture_is_the_expected_one():
    """המשקל נגד `skip`: הקובץ קיים ולא הוחלף בשקט. ‏makecab, ‏17/09."""
    assert CAB.is_file() and CAB.stat().st_size == 371
    assert hashlib.sha256(CAB.read_bytes()).hexdigest() == (
        "a4ccabfb97100b423fff7f94d5c145e99f29cfa997c2715080cfbdef32d69071")


@pytest.mark.skipif(not _cab_extractor_present(),
                    reason="expand.exe / cabextract / 7z are required to extract a CAB")
def test_a_cab_is_extracted_and_its_subdirectories_are_kept(tmp_path):
    out = tmp_path / "from-cab.tar"
    proc = run_tool(str(CAB), "--vendor", "LENOVO", "--model", "ThinkCentre M720q",
                    "--out", str(out), cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    manifest, blobs = read_tar(out)
    assert [f["path"] for f in manifest["files"]] == ["e1d68x64.inf", "x64/e1d68x64.sys"]
    assert manifest["match"][1] == {"pci_any": ["8086:15bc", "8086:15bd"]}
    assert blobs["lenovo-thinkcentre-m720q/x64/e1d68x64.sys"] == b"MZ fake nic driver"
    assert "INF:        1 (רשת/אחסון: 1; דולגו: 0)" in proc.stdout


def test_a_broken_cab_is_refused_not_packaged_empty(tmp_path):
    if not _cab_extractor_present():
        pytest.skip("expand.exe / cabextract / 7z are required to extract a CAB")
    bad = tmp_path / "broken.cab"
    bad.write_bytes(b"MSCF" + b"\0" * 40)
    proc = run_tool(str(bad), "--vendor", "HP", "--model", "X", "--out", str(tmp_path / "p.tar"),
                    cwd=tmp_path)
    assert proc.returncode == 1 and "CAB" in proc.stderr, (proc.stdout, proc.stderr)
    assert not (tmp_path / "p.tar").exists()


def test_no_cab_extractor_is_a_refusal_by_name(tmp_path, monkeypatch):
    """בלי expand/cabextract/7z הכלי מסרב ואומר מה חסר — לא "פורש" אפס קבצים."""
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("SystemRoot", str(tmp_path))         # expand.exe נמצא לפי SystemRoot
    proc = run_tool(str(CAB), "--vendor", "HP", "--model", "X", "--out", str(tmp_path / "p.tar"),
                    cwd=tmp_path)
    assert proc.returncode == 1 and "cabextract" in proc.stderr and "expand" in proc.stderr
    assert not (tmp_path / "p.tar").exists()
