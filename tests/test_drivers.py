"""‏#720 — ספריית חבילות הדרייברים: ייבוא מאומת (sha256 של **כל** קובץ
לפני הכניסה, עיקרון 6), חבילה בלתי-משתנה, התאמה דטרמיניסטית שורה-שורה,
ו-endpoints לקונסולה ולסוכן.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.library import drivers                                        # noqa: E402
from server.library.drivers import (DriverError, DriverLibrary, match,    # noqa: E402
                            safe_relative, validate_manifest)

from test_inventory import GOOD as INVENTORY, MAC                 # noqa: E402

FILES = {"e1d68x64.inf": b"[Version]\nSignature=\"$Windows NT$\"\n",
         "e1d68x64.sys": b"\x4d\x5a fake driver bytes",
         "x64/e1d68x64.cat": b"catalog"}


def manifest_for(name="intel-nic", files=FILES, **extra) -> dict:
    return {
        "schema": 1, "name": name, "description": "Intel I219 NIC",
        "match": [{"pci": ["8086:15bc:020000"]}],
        "files": [{"path": p, "sha256": hashlib.sha256(b).hexdigest()}
                  for p, b in files.items()],
        **extra,
    }


def build_tar(manifest: dict, files: dict = FILES, folder: str | None = None,
              extra_files: dict | None = None, arcname_manifest="manifest.json") -> bytes:
    """tar של חבילה — תיקייה אחת עם manifest.json והקבצים."""
    folder = folder or manifest["name"]
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        def add(name: str, data: bytes) -> None:
            info = tarfile.TarInfo(f"{folder}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        add(arcname_manifest, json.dumps(manifest).encode())
        for path, data in {**files, **(extra_files or {})}.items():
            add(path, data)
    return buf.getvalue()


def write_tar(tmp_path: Path, data: bytes, name="pkg.tar") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


# --- המניפסט -----------------------------------------------------------------


def test_a_good_manifest_passes():
    assert validate_manifest(manifest_for()) is None
    assert validate_manifest(manifest_for(
        match=[{"vendor": "LENOVO", "model": "ThinkCentre M720q"}])) is None


@pytest.mark.parametrize("change, fragment", [
    ({"schema": 2}, "schema"),
    ({"name": "../evil"}, "שם חבילה"),
    ({"name": "עברית"}, "שם חבילה"),
    ({"name": "a" * 65}, "שם חבילה"),
    ({"match": []}, "match"),
    ({"match": [{"pci": ["8086:15bc:020000"], "vendor": "x", "model": "y"}]}, "לא שניהם"),
    ({"match": [{"pci": []}]}, "pci"),
    ({"match": [{"pci": ["8086:15BC:020000"]}]}, "PCI"),
    ({"match": [{"vendor": "LENOVO"}]}, "model"),
    ({"match": [{"model": "M720q"}]}, "vendor"),
    ({"match": [{}]}, "כלל התאמה"),
    ({"files": []}, "files"),
    ({"files": [{"path": "../x.inf", "sha256": "a" * 64}]}, "נתיב"),
    ({"files": [{"path": "/x.inf", "sha256": "a" * 64}]}, "נתיב"),
    ({"files": [{"path": "x.inf", "sha256": "zz"}]}, "sha256"),
    ({"files": [{"path": "x.inf", "sha256": "a" * 64}, {"path": "x.inf", "sha256": "b" * 64}]}, "כפול"),
    ({"description": 5}, "description"),
], ids=lambda v: str(v)[:40])
def test_a_bad_manifest_is_refused_by_name(change, fragment):
    problem = validate_manifest({**manifest_for(), **change})
    assert problem and fragment in problem, problem


@pytest.mark.parametrize("raw, expected", [
    ("e1d.inf", "e1d.inf"),
    ("x64\\e1d.inf", "x64/e1d.inf"),           # tar שנוצר ב-Windows
    ("a/./b", "a/b"), ("../b", None), ("/abs", None), ("", None), (5, None),
    ("שם.inf", None), ("a\tb", None),
])
def test_safe_relative(raw, expected):
    assert safe_relative(raw) == expected



# --- #959: מזהה בלי class, וכלל "אחד מ-" ------------------------------------
# ‏INF של דרייבר נוקב ב-`PCI\VEN_8086&DEV_15BC` בלבד — אין בו קוד class,
# וחבילת דגם מתאימה כשאחד מבקריה נמצא במכונה, לא כשכולם נמצאים.


def test_a_pci_id_may_omit_the_class_and_pci_any_needs_one_hit():
    assert validate_manifest(manifest_for(match=[{"pci": ["8086:15bc"]}])) is None
    assert validate_manifest(manifest_for(
        match=[{"pci_any": ["8086:15bc", "10ec:8168:020000"]}])) is None
    pkgs = [{"name": "any-of", "match": [{"pci_any": ["8086:15bc", "10ec:8168:020000"]}]},
            {"name": "all-of-short", "match": [{"pci": ["8086:15bc", "8086:a352"]}]}]
    assert match(INVENTORY, pkgs) == [{"name": "all-of-short", "by": "pci"},
                                      {"name": "any-of", "by": "pci"}]
    # ‏`8086:15bc` תואם רק בקר שה-vendor:device שלו זהים — לא כל מוצר של Intel.
    assert match({**INVENTORY, "pci": ["8086:15bd:020000"]}, pkgs) == []
    assert match({**INVENTORY, "pci": ["10ec:8168:020000"]}, pkgs) == [
        {"name": "any-of", "by": "pci"}]


@pytest.mark.parametrize("rule, fragment", [
    ({"pci_any": []}, "pci_any"),
    ({"pci_any": ["8086:15BC"]}, "PCI"),
    ({"pci_any": ["8086"]}, "PCI"),
    ({"pci_any": ["8086:15bc"] * 513}, "pci_any"),
    ({"pci_any": ["8086:15bc"], "pci": ["8086:15bc"]}, "לא שניהם"),
    ({"pci_any": ["8086:15bc"], "vendor": "x", "model": "y"}, "לא שניהם"),
], ids=lambda v: str(v)[:40])
def test_a_bad_pci_any_rule_is_refused_by_name(rule, fragment):
    problem = validate_manifest(manifest_for(match=[rule]))
    assert problem and fragment in problem, problem

# --- ההתאמה — טהורה ודטרמיניסטית --------------------------------------------

PKGS = [
    {"name": "nic-by-pci", "match": [{"pci": ["8086:15bc:020000"]}]},
    {"name": "chipset-by-model", "match": [{"vendor": "lenovo", "model": "thinkcentre m720q"}]},
    {"name": "dell-only", "match": [{"vendor": "Dell Inc.", "model": "OptiPlex 7040"}]},
    {"name": "needs-both-ids", "match": [{"pci": ["8086:15bc:020000", "10ec:8168:020000"]}]},
    {"name": "both-rules", "match": [{"vendor": "LENOVO", "model": "10SQS0AK00"},
                                     {"pci": ["8086:a352:010601"]}]},
]


def test_match_is_by_pci_first_then_model_then_name():
    hits = match(INVENTORY, PKGS)
    assert hits == [
        {"name": "both-rules", "by": "pci"},        # תאם גם בדגם — PCI גובר
        {"name": "nic-by-pci", "by": "pci"},
        {"name": "chipset-by-model", "by": "model"},
    ]
    assert match(INVENTORY, list(reversed(PKGS))) == hits, "הסדר אינו תלוי בסדר הקלט"


def test_a_pci_rule_needs_every_id_it_lists():
    assert match(INVENTORY, [PKGS[3]]) == []
    richer = {**INVENTORY, "pci": INVENTORY["pci"] + ["10ec:8168:020000"]}
    assert match(richer, [PKGS[3]]) == [{"name": "needs-both-ids", "by": "pci"}]


def test_a_model_rule_matches_product_name_or_product_version():
    assert match(INVENTORY, [{"name": "n", "match": [{"vendor": "LENOVO", "model": "10SQS0AK00"}]}])
    assert match(INVENTORY, [{"name": "v", "match": [{"vendor": "LENOVO", "model": "ThinkCentre M720q"}]}])
    assert not match(INVENTORY, [{"name": "w", "match": [{"vendor": "Dell Inc.", "model": "10SQS0AK00"}]}])
    assert not match(INVENTORY, [{"name": "b", "match": [{"vendor": "LENOVO", "model": "3132"}]}])


def test_no_inventory_and_no_match_are_empty_lists_not_errors():
    assert match(None, PKGS) == []
    assert match({}, PKGS) == []
    assert match({"dmi": {}, "pci": [], "tpm": None}, PKGS) == []
    assert match(INVENTORY, []) == []


# --- ייבוא: מה נכנס ומה לא --------------------------------------------------


@pytest.fixture()
def library(tmp_path) -> DriverLibrary:
    return DriverLibrary(tmp_path / "drivers")


def test_a_good_package_is_imported_and_immutable(library, tmp_path):
    manifest = library.import_tar(write_tar(tmp_path, build_tar(manifest_for())))
    assert manifest["name"] == "intel-nic"
    assert set(library.scan()) == {"intel-nic"}
    folder = library.root / "intel-nic"
    assert (folder / "manifest.json").is_file()
    assert (folder / "x64/e1d68x64.cat").read_bytes() == b"catalog"
    assert not list(library.root.glob(".import-*")), "אזור הביניים נוקה"
    # בלתי-משתנה: אותו שם שוב נדחה, גם עם תוכן אחר.
    with pytest.raises(DriverError, match="כבר קיימת"):
        library.import_tar(write_tar(tmp_path, build_tar(manifest_for()), "again.tar"))
    assert library.public(library.get("intel-nic"))["files"][0]["path"] == "e1d68x64.inf"


def test_a_file_whose_sha256_differs_is_refused_and_nothing_is_left(library, tmp_path):
    tampered = {**FILES, "e1d68x64.sys": b"not the bytes the manifest signed"}
    with pytest.raises(DriverError, match="sha256"):
        library.import_tar(write_tar(tmp_path, build_tar(manifest_for(), files=tampered)))
    assert library.scan() == {}
    assert not any(library.root.iterdir()), "לא נשאר דבר — לא חבילה ולא אזור ביניים"


def test_a_declared_file_that_is_missing_is_refused(library, tmp_path):
    fewer = {k: v for k, v in FILES.items() if k != "e1d68x64.sys"}
    with pytest.raises(DriverError, match="חסר"):
        library.import_tar(write_tar(tmp_path, build_tar(manifest_for(), files=fewer)))
    assert library.scan() == {}


def test_a_file_the_manifest_does_not_declare_is_refused(library, tmp_path):
    with pytest.raises(DriverError, match="אינו במניפסט"):
        library.import_tar(write_tar(tmp_path, build_tar(
            manifest_for(), extra_files={"surprise.sys": b"x"})))
    assert library.scan() == {}


def test_a_bad_manifest_and_a_missing_manifest_are_refused(library, tmp_path):
    with pytest.raises(DriverError, match="מניפסט לא תקין"):
        library.import_tar(write_tar(tmp_path, build_tar(manifest_for(match=[]))))
    with pytest.raises(DriverError, match="אין manifest.json"):
        library.import_tar(write_tar(tmp_path, build_tar(
            manifest_for(), arcname_manifest="readme.txt"), "b.tar"))
    assert library.scan() == {}


def test_the_folder_name_in_the_tar_does_not_matter_the_manifest_does(library, tmp_path):
    library.import_tar(write_tar(tmp_path, build_tar(manifest_for(), folder="whatever")))
    assert set(library.scan()) == {"intel-nic"}


def test_a_folder_without_a_verified_manifest_is_not_a_package(library):
    stray = library.root / "half"
    stray.mkdir(parents=True)
    (stray / drivers.UNVERIFIED).write_text(json.dumps(manifest_for("half")))
    renamed = library.root / "other"
    renamed.mkdir()
    (renamed / "manifest.json").write_text(json.dumps(manifest_for("not-other")))
    assert library.scan() == {}


def test_file_path_serves_only_declared_files(library, tmp_path):
    library.import_tar(write_tar(tmp_path, build_tar(manifest_for())))
    assert library.file_path("intel-nic", "x64/e1d68x64.cat").is_file()
    assert library.file_path("intel-nic", "x64\\e1d68x64.cat").is_file()
    assert library.file_path("intel-nic", "manifest.json") is None
    assert library.file_path("intel-nic", "../intel-nic/e1d68x64.inf") is None
    assert library.file_path("nope", "e1d68x64.inf") is None


# --- ה-endpoints -------------------------------------------------------------


def _hello(server, inv=INVENTORY):
    return server["anon"].post("/api/v1/agent/hello", json={
        "schema": 2, "mac": MAC, "all_macs": [MAC], "ip": "10.44.12.187",
        "disks": [], "inventory": inv})


def _upload(client, data: bytes):
    return client.post("/api/console/drivers/upload", content=data,
                       headers={"Content-Type": "application/x-tar"})


def test_upload_is_admin_only_and_verifies_before_entry(server):
    admin, deploy = server["admin"], server["deploy"]
    assert _upload(deploy, build_tar(manifest_for())).status_code == 403
    bad = build_tar(manifest_for(), files={**FILES, "e1d68x64.sys": b"tampered"})
    r = _upload(admin, bad)
    assert r.status_code == 400 and "sha256" in r.json()["detail"]
    assert admin.get("/api/console/drivers").json() == []
    r = _upload(admin, build_tar(manifest_for()))
    assert r.status_code == 200 and r.json() == {"name": "intel-nic", "files": 3}
    assert _upload(admin, b"").status_code == 400
    assert _upload(admin, b"not a tar at all").status_code == 400


def test_the_list_counts_registered_machines_that_match(server):
    from conftest import setup_classroom   # noqa: PLC0415
    admin = server["admin"]
    _upload(admin, build_tar(manifest_for()))
    _upload(admin, build_tar(manifest_for("dell-only", match=[
        {"vendor": "Dell Inc.", "model": "OptiPlex 7040"}])))
    setup_classroom(server)
    assert _hello(server).status_code == 200
    # מכונה לא רשומה עם מלאי תואם אינה נספרת.
    server["anon"].post("/api/v1/agent/hello", json={
        "schema": 2, "mac": "aa:bb:cc:dd:ee:ff", "ip": "10.44.12.99",
        "disks": [], "inventory": INVENTORY})
    by_name = {p["name"]: p for p in admin.get("/api/console/drivers").json()}
    assert by_name["intel-nic"]["matches"] == [MAC]
    assert by_name["dell-only"]["matches"] == []
    assert by_name["intel-nic"]["files"][0]["sha256"]
    assert server["deploy"].get("/api/console/drivers").status_code == 403   # #1073: אין קונסולה ל-deploy


def test_delete_is_behind_typing_the_name(server):
    admin = server["admin"]
    _upload(admin, build_tar(manifest_for()))
    r = admin.post("/api/console/drivers/intel-nic/delete", json={"confirm_name": "intel"})
    assert r.status_code == 400
    assert server["deploy"].post("/api/console/drivers/intel-nic/delete",
                                 json={"confirm_name": "intel-nic"}).status_code == 403
    assert admin.post("/api/console/drivers/intel-nic/delete",
                      json={"confirm_name": "intel-nic"}).status_code == 200
    assert admin.get("/api/console/drivers").json() == []
    assert admin.post("/api/console/drivers/intel-nic/delete",
                      json={"confirm_name": "intel-nic"}).status_code == 404
    events = [e for e in admin.get("/api/console/journal").json() if e["event"].startswith("driver_")]
    assert sorted(e["event"] for e in events) == ["driver_delete", "driver_upload"]


def test_the_agent_gets_three_states_and_files_by_exact_name(server):
    admin, anon = server["admin"], server["anon"]
    _upload(admin, build_tar(manifest_for()))
    # 1. מעולם לא דיווחה מלאי — "לא ידוע", לא "אין התאמה".
    assert anon.get(f"/api/v1/agent/drivers?mac={MAC}").json() == {
        "ok": True, "inventory": False, "packages": []}
    # 2. מלאי בלי חבילה תואמת.
    _hello(server, {**INVENTORY, "pci": []})
    assert anon.get(f"/api/v1/agent/drivers?mac={MAC}").json() == {
        "ok": True, "inventory": True, "packages": []}
    # 3. תואם — הקבצים בשמם, עם sha256 וכתובת.
    _hello(server)
    answer = anon.get(f"/api/v1/agent/drivers?mac={MAC.upper()}").json()
    (pkg,) = answer["packages"]
    assert pkg["name"] == "intel-nic" and pkg["by"] == "pci"
    cat = next(f for f in pkg["files"] if f["path"] == "x64/e1d68x64.cat")
    assert cat["sha256"] == hashlib.sha256(b"catalog").hexdigest()
    assert anon.get(cat["url"]).content == b"catalog"
    assert anon.get("/api/v1/agent/drivers/intel-nic/files/manifest.json").status_code == 404
    assert anon.get("/api/v1/agent/drivers/intel-nic/files/../x").status_code in (400, 404)
    assert anon.get("/api/v1/agent/drivers?mac=nonsense").status_code == 400
