"""‏#1334 — קצה-לקצה מול התקני loop אמיתיים ו-QEMU: אימג' זעיר → דיסק → VM
שמריץ מטען ומכבה את עצמו → שמירה כגרסה חדשה → פתיחה חוזרת של הגרסה החדשה
וקריאת מה שה-VM כתב.

**מה זה מוכיח:** שפונקציות הסוכן (‏apply_gpt, ‏expand_last, ‏grow, ‏shrink_plan,
‏_shrink_gpt, ‏_shrink_compact, ‏boot_ca_json…) רצות על השרת מול /dev/loopN
ומחזירות את מה שהן מחזירות על ברזל; שהטבלה חוזרת מהדיסק עם אותם GUIDים;
שהמחיצה נמתחת בפתיחה ומתכווצת בשמירה, וה-recovery שאחריה עוברת לזנב וחוזרת;
שה-sha256 של כל קובץ בגרסה החדשה נכון; ושמה שה-VM כתב לדיסק **נמצא בגרסה
החדשה** — כי פותחים אותה שוב באותו מסלול וקוראים.

**מה זה לא מוכיח:** Windows. האימג' כאן הוא ESP עם `startup.nsh` ש-UEFI
Shell של OVMF מריץ (כותב קובץ ומכבה), ומחיצת NTFS ריקה. ‏Windows אמיתי,
‏Secure Boot, ‏KVM על ברזל, הפעלת רישיון ו-Windows Update — ראו
`docs/vm-update.md` סעיף 7.

**הרצה:** root (‏losetup, ‏mount), ‏Linux, והכלים של השרת. ‏opt-in במפורש —
‏`IMAGESRV_VM_IT=1` — כי ריצת המעבדה הרגילה אינה root, ודילוג שקט על "אין
root" היה ירוק בלי ראיה (#52). ‏TCG (אין /dev/kvm ב-Testrunner — הוא VM):
‏`allow_tcg=True` הוא דגל הבדיקה, ולא נגיש מה-API.

    sudo -n env IMAGESRV_VM_IT=1 TMPDIR=$HOME/x-tmp python3 -m pytest \\
        tests/test_vmupdate_integration.py -q -rs

המכונה היחידה שנוגעים בה: קבצים דלילים ב-TMPDIR שמחוברים ל-loop. כל צומת
שנכתבת אליו מערכת קבצים נבדק קודם שהוא `/dev/loopNpM` של **הקובץ שלנו**.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from server.vm import agent_shell, vmdisk, vmupdate
from server.db import connect, set_setting
from server.library.images import ImageLibrary, required_bytes

TOOLS = ("losetup", "sgdisk", "partclone.fat", "partclone.ntfs", "mkfs.vfat", "mkntfs",
         "ntfsresize", "ntfsfix", "zstd", "jq", "blkid", "mount", "nft", "setpriv",
         "qemu-system-x86_64", "ip", "dmsetup")
SBIN = os.environ.get("PATH", "") + ":/usr/sbin:/sbin"
MISSING = [t for t in TOOLS if shutil.which(t, path=SBIN) is None]
if not Path("/usr/share/OVMF/OVMF_CODE_4M.fd").exists():
    MISSING.append("ovmf")
if not vmupdate.Machine().guestfs():
    MISSING.append("python3-guestfs")
if os.name != "nt" and vmupdate.Machine().vm_user() is None:
    MISSING.append("the imagesrv-vm system user")

pytestmark = [pytest.mark.vm_it, pytest.mark.skipif(
    os.environ.get("IMAGESRV_VM_IT") != "1" or os.name == "nt"
    or getattr(os, "geteuid", lambda: 1)() != 0 or bool(MISSING),
    reason="opt-in (IMAGESRV_VM_IT=1), Linux, root, and the server's tools"
           + (f" — missing: {', '.join(MISSING)}" if MISSING else ""))]

MIB = 1 << 20
GIB = 1 << 30
ENV = {**os.environ, "PATH": SBIN}
PAYLOAD = "vmupdate-ran"
MARKER = "written-before-capture"


def sgdisk_info(text: str) -> dict[str, str]:
    """`sgdisk -i N` → {שדה: המילה הראשונה בערך}."""
    found = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and value.strip():
            found[key.strip()] = value.split()[0]
    return found


def sh(*argv: str, input: bytes | None = None) -> str:
    proc = subprocess.run(list(argv), input=input, capture_output=True, env=ENV,
                          **({} if input is not None else {"stdin": subprocess.DEVNULL}))
    assert proc.returncode == 0, f"{argv}: rc={proc.returncode} {proc.stderr.decode(errors='replace')}"
    return proc.stdout.decode(errors="replace")


def ours(node: str, backing: Path) -> None:
    """הבלם: רק `/dev/loopNpM` שמחובר לקובץ הדליל שלנו."""
    match = re.fullmatch(r"/dev/(loop\d+)p\d+", node)
    assert match, node
    assert str(backing) in sh("losetup", "-j", str(backing)) and \
        f"/dev/{match.group(1)}:" in sh("losetup", "-j", str(backing)), node


def capture_source(work: Path, library: Path) -> dict:
    """אימג' Windows זעיר: ESP ‏(vfat, ‏startup.nsh) + C: ‏(NTFS) + recovery
    ‏(NTFS) אחריה — הפריסה של Windows 11, עם זנב שההרחבה מזיזה."""
    disk = work / "source.raw"
    with disk.open("wb") as fh:
        fh.truncate(4 * GIB)
    sh("sgdisk", "-n1:2048:+64M", "-t1:EF00", "-n2:0:+2560M", "-t2:0700",
       "-n3:0:+64M", "-t3:2700", str(disk))
    loop = sh("losetup", "--find", "--show", "--partscan", str(disk)).strip()
    try:
        for i in (1, 2, 3):
            ours(f"{loop}p{i}", disk)
        sh("mkfs.vfat", "-F", "32", "-n", "ESP", f"{loop}p1")
        sh("mkntfs", "-Q", "-L", "Windows", f"{loop}p2")
        sh("mkntfs", "-Q", "-L", "Recovery", f"{loop}p3")
        mnt = work / "mnt"
        mnt.mkdir()
        sh("mount", f"{loop}p1", str(mnt))
        try:
            # ה-UEFI Shell של OVMF מריץ startup.nsh מ-fs0: — כותב קובץ ASCII
            # (‏>a) ומכבה את המכונה. זה ה"עדכון" של הבדיקה.
            (mnt / "startup.nsh").write_bytes(
                f"echo -off\r\necho {PAYLOAD} >a fs0:\\ran.txt\r\nreset -s\r\n".encode())
        finally:
            sh("umount", str(mnt))
        sh("mount", "-t", "ntfs-3g", f"{loop}p2", str(mnt))
        try:
            (mnt / "marker.txt").write_text(MARKER)
        finally:
            sh("umount", str(mnt))
        disk_guid = re.search(r"Disk identifier \(GUID\): (\S+)", sh("sgdisk", "-p", loop)).group(1)
        folder = library / "img_1334aa"
        folder.mkdir(parents=True)
        parts = []
        for index, role, fs, tool, flags in ((1, "esp", "vfat", "partclone.fat", []),
                                             (2, "windows", "ntfs", "partclone.ntfs", ["-I"]),
                                             (3, "recovery", "ntfs", "partclone.ntfs", ["-I"])):
            info = sgdisk_info(sh("sgdisk", "-i", str(index), loop))
            name = f"p{index}.{role}.pcl.zst"
            raw = subprocess.run([tool, "-c", *flags, "-s", f"{loop}p{index}", "-L",
                                  str(work / f"pcl{index}.log")], capture_output=True, env=ENV,
                                 stdin=subprocess.DEVNULL, check=True).stdout
            packed = subprocess.run(["zstd", "-3", "-c"], input=raw, capture_output=True,
                                    env=ENV, check=True).stdout
            (folder / name).write_bytes(packed)
            parts.append({
                "index": index, "type_guid": info["Partition GUID code"],
                "unique_guid": info["Partition unique GUID"], "attrs": info["Attribute flags"],
                "role": role, "fs": fs, "start_sector": int(info["First sector"]),
                "size_bytes": int(info["Partition size"]) * 512, "used_bytes": 1 << 20,
                "file": name, "sha256": hashlib.sha256(packed).hexdigest(),
                "expandable": role == "windows"})
    finally:
        sh("losetup", "-d", loop)
    manifest = {
        "schema": 1, "id": "img_1334aa", "name": "IT source", "description": "",
        "folder": "IT", "created": "2026-10-13T00:00:00+03:00", "created_by": "test",
        "family": 256, "os": "windows", "boot_ca": [], "boot_ca_error": None,
        "source_disk_bytes": 4 * GIB, "min_target_bytes": 0, "target_floor_bytes": None,
        "scheme": "gpt", "sector_size": 512, "disk_guid": disk_guid, "partitions": parts,
        "total_compressed_bytes": sum((folder / p["file"]).stat().st_size for p in parts),
        "compression": "zstd-3",
    }
    manifest["min_target_bytes"] = required_bytes(manifest)
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def wait_state(engine, want: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = engine.status()
        if status["state"] == want:
            return status
        if status["state"] == "failed":
            log = engine.workdir / "run" / "agent.log"
            raise AssertionError(f"{status['error']}\n"
                                 + (log.read_text(errors="replace")[-3000:] if log.exists() else ""))
        time.sleep(1)
    raise AssertionError(f"state stayed {engine.status()} after {timeout}s (wanted {want})")


def table_of(disk: Path) -> dict[int, dict[str, str]]:
    listing = sh("sgdisk", "-p", str(disk))
    indexes = [int(m) for m in re.findall(r"^\s*(\d+)\s", listing, re.M)]
    return {i: sgdisk_info(sh("sgdisk", "-i", str(i), str(disk))) for i in indexes}


@pytest.fixture()
def library_root():
    """הספרייה במקום שמשתמש ה-VM יכול לעבור בו — כמו /srv/imagesrv/images
    בייצור. ‏TMPDIR של המעבדה יושב תחת /home/test ‏(0700), ושם imagesrv-vm
    אינו מגיע לקובצי ה-ESP שהורדו (נמדד 13/10)."""
    import tempfile
    root = Path(tempfile.mkdtemp(prefix="f1334-lib-", dir="/var/tmp"))
    root.chmod(0o755)
    yield root / "images"
    for line in sh("losetup", "-a").splitlines():
        if str(root) in line:
            subprocess.run(["losetup", "-d", line.split(":", 1)[0]], env=ENV,
                           stdin=subprocess.DEVNULL, check=False)
    shutil.rmtree(root, ignore_errors=True)


def test_open_boot_payload_save_and_reopen(tmp_path, library_root):
    source = capture_source(tmp_path, library_root)
    library = ImageLibrary(library_root)
    assert library.get("img_1334aa") is not None, "the test image itself must be valid"
    conn = connect(tmp_path / "imagesrv.db")
    vmupdate.write_settings(conn, {"memory_mib": 1024, "cpus": 1})
    # ‏#1468: ה-VM על macvtap בכרטיס היציאה של המכונה. ה-UEFI Shell אינו עושה DHCP,
    # ולכן תקרת ה-lease גבוהה מזמן המטען — כאן נבדק החיבור, לא ה-lease.
    nic = sh("ip", "-o", "-4", "route", "get", "1.1.1.1").split(" dev ")[1].split()[0]
    set_setting(conn, vmupdate.NIC_KEY, nic)
    engine = vmupdate.VmUpdate(conn, library, allow_tcg=True, ram_reserve_mib=256,
                               growth_reserve_bytes=64 * MIB, lease_timeout=3600)

    # 1. אימג' → דיסק → VM. ‏boot_ca == [] → בלי Secure Boot (ה-Shell אינו חתום).
    engine.start("img_1334aa", "root")
    running = wait_state(engine, "running", 300)
    assert running["secure_boot"] is False and running["accel"] in ("kvm", "tcg")

    # 2. הטבלה חזרה מהדיסק: GUIDים, ההרחבה, וה-recovery בזנב.
    table = table_of(engine.workdir / "disk.raw")
    assert sorted(table) == [1, 2, 3]
    disk_text = sh("sgdisk", "-p", str(engine.workdir / "disk.raw"))
    assert source["disk_guid"] in disk_text
    for part in source["partitions"]:
        assert table[part["index"]]["Partition unique GUID"] == part["unique_guid"]
    grown = int(table[2]["Partition size"]) * 512
    assert grown > source["partitions"][1]["size_bytes"] + GIB, "C: was not stretched"
    assert int(table[3]["First sector"]) > source["partitions"][2]["start_sector"]

    # 2ב. הבידוד: QEMU רץ כמשתמש ה-VM, והטבלה של vmnet חוסמת אותו מהמארח.
    vm_user = vmupdate.Machine().vm_user()
    uid, gid = vm_user.uid, vm_user.gid
    # ‏`process=imagesrv-vmupdate` משנה את שם התהליך — מחפשים בשורת הפקודה.
    owners = [line.split()[0] for line in sh("ps", "-e", "-o", "uid=,args=").splitlines()
              if "imagesrv-vmupdate,process=" in line]
    assert owners and set(owners) == {str(uid)}, owners
    qemu_line = next(line for line in sh("ps", "-e", "-o", "args=").splitlines()
                     if "imagesrv-vmupdate,process=" in line)
    assert "tap,id=net0,fd=" in qemu_line and "user,id=" not in qemu_line
    mac = engine.ensure_mac()
    link = sh("ip", "-d", "-o", "link", "show", "dev", "imagesrv-vm0")
    assert f"imagesrv-vm0@{nic}" in link and "macvtap mode bridge" in link and mac in link
    table = sh("nft", "list", "table", "inet", "imagesrv_vm")
    assert f"skuid {uid} drop" in table and f"ether saddr {mac} drop" in table
    host_ip = sh("ip", "-o", "-4", "route", "get", "1.1.1.1").split(" src ")[1].split()[0]
    for target in ("127.0.0.1", host_ip, "10.255.255.1"):
        probe = subprocess.run(
            ["setpriv", f"--reuid={uid}", f"--regid={gid}", "--clear-groups",
             "--no-new-privs", "--", "python3", "-c",
             f"import socket; s=socket.socket(); s.settimeout(3); s.connect(('{target}', 22))"],
            capture_output=True, env=ENV, stdin=subprocess.DEVNULL)
        assert probe.returncode != 0, f"the VM user reached {target}:22"

    # 3. המטען רץ ב-UEFI Shell ומכבה — ‏QEMU יוצא 0, המצב stopped.
    stopped = wait_state(engine, "stopped", 900)
    assert stopped["forced"] is False and stopped["runtime_seconds"] >= 0
    # ה-macvtap והטבלה ירדו עם QEMU (#1468)
    gone = subprocess.run(["ip", "-o", "link", "show", "dev", "imagesrv-vm0"], env=ENV,
                          capture_output=True, stdin=subprocess.DEVNULL)
    assert gone.returncode != 0, gone.stdout
    table_gone = subprocess.run(["nft", "list", "table", "inet", "imagesrv_vm"], env=ENV,
                                capture_output=True, stdin=subprocess.DEVNULL)
    assert table_gone.returncode != 0, table_gone.stdout

    # 4. שמירה כגרסה חדשה.
    engine.save("root", "IT v2", "after the payload")
    saved = wait_state(engine, "saved", 900)
    new_id = saved["result"]["image_id"]
    assert not engine.workdir.exists()
    new = library.get(new_id)
    assert new is not None and library.get("img_1334aa") is not None
    assert (new["version"], new["previous_id"], new["folder"]) == (2, "img_1334aa", "IT")
    assert new["disk_guid"] == source["disk_guid"] and new["os"] == "windows"
    assert new["boot_ca"] == [] or new["boot_ca"] is None
    for part in new["partitions"]:
        data = (Path(new["_dir"]) / part["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == part["sha256"], part["file"]
    by_index = {p["index"]: p for p in new["partitions"]}
    assert by_index[2]["expandable"] is True and "shrunk_from_bytes" in by_index[2]
    assert by_index[2]["size_bytes"] < grown
    assert by_index[3]["start_sector"] == (
        by_index[2]["start_sector"] + by_index[2]["size_bytes"] // 512
        + (int(table[3]["First sector"]) - int(table[2]["First sector"])
           - int(table[2]["Partition size"])))
    assert new["min_target_bytes"] == required_bytes(new)

    # 5. פותחים את הגרסה החדשה באותו מסלול וקוראים מה שה-VM כתב.
    again = tmp_path / "reopen"
    again.mkdir()
    built = vmdisk.build_disk(vmdisk.Host(), new, Path(new["_dir"]), again, lambda n: None)
    assert built.warnings == []
    disk = again / "disk.raw"
    loop = sh("losetup", "--find", "--show", "--partscan", "--read-only", str(disk)).strip()
    mnt = tmp_path / "check"
    mnt.mkdir()
    try:
        sh("mount", "-o", "ro", f"{loop}p1", str(mnt))
        try:
            assert (mnt / "ran.txt").read_text(errors="replace").strip() == PAYLOAD
        finally:
            sh("umount", str(mnt))
        sh("mount", "-t", "ntfs-3g", "-o", "ro", f"{loop}p2", str(mnt))
        try:
            assert (mnt / "marker.txt").read_text() == MARKER
        finally:
            sh("umount", str(mnt))
    finally:
        sh("losetup", "-d", loop)


def _sha_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def test_save_shrinks_a_copy_and_never_writes_the_vm_disk(tmp_path, library_root):
    """‏#1491: שמירה שמכווצת את C: — ה-sha256 של `disk.raw` אחריה זהה לזה
    שלפניה, ובכל זאת הגרסה החדשה מכווצת (‏`shrunk_from_bytes`) ונפתחת. ובסוף
    לא נשאר עותק: לא ‏dm, לא loop על הדיסק, ולא קובץ copy-on-write."""
    source = capture_source(tmp_path, library_root)
    work = library_root.parent / "work"
    work.mkdir()
    built = vmdisk.build_disk(vmdisk.Host(), source, library_root / "img_1334aa", work,
                              lambda n: None)
    assert built.warnings == []
    disk = work / "disk.raw"
    grown = int(table_of(disk)[2]["Partition size"]) * 512
    before = _sha_of(disk)

    out = library_root.parent / "out"
    captured = vmdisk.capture_disk(vmdisk.Host(), work, out, lambda n: None,
                                   user=vmupdate.Machine().vm_user())

    assert _sha_of(disk) == before, "the save wrote to the VM's own disk"
    c_drive = next(p for p in captured["partitions"] if p["index"] == 2)
    assert "shrunk_from_bytes" in c_drive and c_drive["size_bytes"] < grown
    assert vmdisk.VIEW_NAME not in sh("dmsetup", "ls")
    assert sh("losetup", "-j", str(disk)).strip() == ""
    assert not (work / "run" / "shrink.cow").exists()

    # מה שנשמר הוא הכיווץ: נפתח באותו מסלול, וה-marker שבמקור שם.
    new = {**source, **captured}
    for part in new["partitions"]:
        if part.get("file"):
            (out / part["file"]).rename(library_root / "img_1334aa" / f"new.{part['file']}")
            part["file"] = f"new.{part['file']}"
    again = tmp_path / "reopen"
    again.mkdir()
    vmdisk.build_disk(vmdisk.Host(), new, library_root / "img_1334aa", again, lambda n: None)
    loop = sh("losetup", "--find", "--show", "--partscan", "--read-only",
              str(again / "disk.raw")).strip()
    mnt = tmp_path / "check"
    mnt.mkdir()
    try:
        sh("mount", "-t", "ntfs-3g", "-o", "ro", f"{loop}p2", str(mnt))
        try:
            assert (mnt / "marker.txt").read_text() == MARKER
        finally:
            sh("umount", str(mnt))
    finally:
        sh("losetup", "-d", loop)


def _leave_a_cut_save_behind(disk: Path, cow: Path) -> None:
    """מה ש-`shrink_view` משאיר כשהשרת אותחל באמצע כיווץ (‏#1649) — אותם
    חלקים, בלי `finally`: loop לקריאה בלבד על הדיסק, loop על ה-COW, והעותק."""
    origin = sh("losetup", "--find", "--show", "--read-only", str(disk)).strip()
    with cow.open("wb") as fh:
        fh.truncate(64 * MIB)
    store = sh("losetup", "--find", "--show", str(cow)).strip()
    sectors = disk.stat().st_size // 512
    sh("dmsetup", "create", vmdisk.VIEW_NAME, "--table",
       f"0 {sectors} snapshot {origin} {store} N {vmdisk.VIEW_CHUNK}")


class NoShrinkHost(vmdisk.Host):
    """אותו מארח אמיתי, ותוכנית הזנב של הסוכן ריקה: אין מה לכווץ, ולכן השמירה
    לא עוברת ב-`shrink_view` (שמנקה לבדו שאריות) — המסלול ש-#1649 שבר."""

    def agent(self, func, *args, **kw):
        if func == "_shrink_tail":
            return agent_shell.Call(0, "", "")
        return super().agent(func, *args, **kw)


@pytest.mark.parametrize("shrinks", [False, True], ids=["nothing-to-shrink", "shrinks-c-drive"])
def test_a_save_after_a_cut_save_clears_the_leftovers_and_succeeds(tmp_path, library_root, shrinks):
    """‏#1649: אחרי אתחול באמצע כיווץ נשארים העותק ושני loop. ה-`detach_all`
    (של ביטול, ושל סוף כל שמירה) נכשל עליהם — והשמירה הבאה מנקה ומצליחה, גם
    כשאין מה לכווץ (שם `shrink_view` לא רץ)."""
    source = capture_source(tmp_path, library_root)
    work = library_root.parent / "work"
    work.mkdir()
    vmdisk.build_disk(vmdisk.Host(), source, library_root / "img_1334aa", work, lambda n: None)
    disk = work / "disk.raw"
    cow = work / "run" / "shrink.cow"
    before = _sha_of(disk)
    _leave_a_cut_save_behind(disk, cow)
    try:
        assert vmdisk.VIEW_NAME in sh("dmsetup", "ls")
        with pytest.raises(vmdisk.DiskError) as stuck:      # הכשל כפי שדווח ב-discard
            vmdisk.detach_all(vmdisk.Host(), disk)
        assert stuck.value.reason == "loop"

        out = library_root.parent / "out"
        captured = vmdisk.capture_disk(vmdisk.Host() if shrinks else NoShrinkHost(), work, out,
                                       lambda n: None, user=vmupdate.Machine().vm_user())
    finally:
        subprocess.run(["dmsetup", "remove", "--retry", vmdisk.VIEW_NAME], env=ENV,
                       capture_output=True, stdin=subprocess.DEVNULL)
        for backing in (cow, disk):
            if not backing.exists():
                continue
            for line in sh("losetup", "-j", str(backing)).splitlines():
                subprocess.run(["losetup", "-d", line.split(":", 1)[0]], env=ENV,
                               capture_output=True, stdin=subprocess.DEVNULL)

    assert any("shrunk_from_bytes" in p for p in captured["partitions"]) == shrinks
    assert _sha_of(disk) == before, "the save wrote to the VM's own disk"
    assert vmdisk.VIEW_NAME not in sh("dmsetup", "ls")
    assert sh("losetup", "-j", str(disk)).strip() == ""
    assert not cow.exists()          # נמחק רק אחרי שה-loop שלו נקרא בחזרה כמנותק
