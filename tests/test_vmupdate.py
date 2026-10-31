"""‏#1334 — מנוע עדכון האימג' ב-VM, עם זיופים: תנאים מוקדמים, מחזור חיים,
שמירה אטומית כגרסה חדשה, ביטול והתאוששות אחרי אתחול.

הדיסק עצמו (`vmdisk.build_disk`/`capture_disk`) מזויף כאן — הוא נבדק מול
התקן loop אמיתי ו-QEMU ב-`test_vmupdate_integration.py`, ומול פונקציות
הסוכן ב-`test_vmdisk_agent_parity.py`. מה שנבדק כאן הוא ההחלטות: מי מסורב
ולמה (במספרים), מה נכנס לספרייה ומתי, ומה נשאר על הדיסק.
"""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

from conftest import MANIFEST_256, write_image
from server.vm import vmdisk, vmupdate
from server.db import connect, set_setting
from server.library.images import ImageLibrary

DISK_GUID = "0E239172-A515-D8B4-92A1-AD790E239172"
SOURCE = {**copy.deepcopy(MANIFEST_256), "disk_guid": DISK_GUID,
          "boot_ca": ["Microsoft Windows Production PCA 2011"]}
OWNED = {**copy.deepcopy(SOURCE), "id": "img_0bb001", "name": "Owned",
         "machine_mac": "aa:bb:cc:00:00:01", "version": 3}
NEW_BYTES = b"new partition bytes after windows update"


#: ה-fd שה-macvtap המזויף "פותח" — מספר ש-qemu_argv מקבל (≥3), לא fd אמיתי.
TAP_FD = 17
#: ‏#1468: ה-lease שהרשת "נותנת" בטסטים שמבקשים אותו.
LEASE = {"address": "10.20.30.40", "server": "10.20.30.1", "requests": 1, "ignored": 0,
         "error": None}


class FakeMachine:
    """‏`lease=None` (ברירת המחדל): ה-DHCP "עוד לא ענה" עד שה-VM כבה — מחזור
    החיים נבדק בלי שהראיה ל-lease משנה אותו. ‏dict בלי address — אין lease, מיד.
    ‏dict עם address, או רשימה של כאלה (חידושים) — כל אחד נמסר ל-`on_ack`, וההאזנה
    נמשכת עד שה-VM כבה, כמו ב-`vmnet.watch_lease`."""

    def __init__(self, *, kvm=None, mem=32768, cpus=8, free=10**13, missing=(),
                 firewall=None, user=True, nics=("eth0", "eth1", "eth2"), stacked=None,
                 macvtap=None, lease=None, platform="vmware"):
        self._kvm, self._mem, self._cpus, self._free = kvm, mem, cpus, free
        self.missing = set(missing)
        self._firewall = firewall
        self._user = user
        self.nics = set(nics)
        self._stacked = stacked or {}
        self._macvtap = macvtap
        self._lease = lease
        self._platform = platform
        self.firewalls: list[tuple] = []
        self.taps: list[tuple] = []
        self.closed: list[int] = []
        self.downs = 0
        self.firewall_offs = 0

    def guestfs(self):
        return "python3-guestfs" not in self.missing

    def vm_user(self):
        return vmdisk.VmUser(999, 989) if self._user else None

    def firewall(self, uid, mac):
        self.firewalls.append((uid, mac))
        return self._firewall

    def firewall_off(self):
        self.firewall_offs += 1
        return None

    def nic_exists(self, name):
        return name in self.nics

    def stacked(self, name):
        return set(self._stacked.get(name, ())) if self._stacked.get(name) != "unreadable" else None

    def macvtap(self, lower, mac):
        self.taps.append((lower, mac))
        return (None, self._macvtap) if self._macvtap else (TAP_FD, None)

    def close_tap(self, fd):
        self.closed.append(fd)

    def macvtap_down(self):
        self.downs += 1
        return None

    def watch_lease(self, mac, timeout, stop, on_ack):
        leases = self._lease if isinstance(self._lease, list) else [self._lease]
        if self._lease is not None and not leases[0].get("address"):
            return dict(leases[0])
        last = {"address": None, "server": None, "requests": 0, "ignored": 0, "error": None}
        for lease in leases if self._lease is not None else []:
            on_ack(lease["address"], lease["server"])
            last = dict(lease)
        while not stop():
            time.sleep(0.01)
        return last

    def platform(self):
        return self._platform

    def kvm(self):
        return self._kvm

    def mem_available_mib(self):
        return self._mem

    def cpu_count(self):
        return self._cpus

    def free_bytes(self, path):
        return self._free

    def which(self, name):
        return None if name in self.missing else f"/usr/bin/{name}"

    def exists(self, path):
        return Path(path).name not in self.missing


class FakeProc:
    def __init__(self, argv):
        self.args = argv
        self.returncode = None
        self._done = threading.Event()

    def wait(self, timeout=None):
        if not self._done.wait(30 if timeout is None else timeout):
            if timeout is not None:
                raise subprocess.TimeoutExpired(self.args, timeout)
            raise AssertionError("FakeProc never finished")
        return self.returncode

    def kill(self):
        self.finish(-9)

    def finish(self, rc=0):
        self.returncode = rc
        self._done.set()


class FakeQemu:
    """‏popen + QMP. ‏`system_powerdown` = ה-guest כבה (QEMU יוצא 0)."""

    def __init__(self, qmp_fails=False):
        self.procs: list[FakeProc] = []
        self.qmp_calls: list[str] = []
        self.kwargs: list[dict] = []
        self.qmp_fails = qmp_fails

    def popen(self, argv, **kw):
        proc = FakeProc(argv)
        self.procs.append(proc)
        self.kwargs.append(kw)
        return proc

    def qmp(self, _path, command):
        self.qmp_calls.append(command)
        if self.qmp_fails:
            raise vmupdate.QmpError("no QMP")
        if command in ("system_powerdown", "quit") and self.procs:
            self.procs[-1].finish(0)
        return {"return": {}}


def fake_build(host, manifest, image_dir, workdir, progress):
    (workdir / "disk.raw").write_bytes(b"\0" * 4096)
    (workdir / "source-manifest.json").write_text(
        json.dumps(vmdisk.public_manifest(manifest)), encoding="utf-8")
    progress(10)
    return vmdisk.BuildResult(1 << 30, [])


def fake_capture(sha=None):
    def capture(host, workdir, out, progress, on_partition=None, *, user):
        out.mkdir(parents=True, exist_ok=True)
        (out / "p1.esp.pcl.zst").write_bytes(NEW_BYTES)
        (out / "p3.windows.pcl.zst").write_bytes(NEW_BYTES)
        good = hashlib.sha256(NEW_BYTES).hexdigest()
        parts = [
            {"index": 1, "type_guid": "C12A7328-F81F-11D2-BA4B-00A0C93EC93B", "role": "esp",
             "fs": "vfat", "start_sector": 2048, "size_bytes": 104857600, "used_bytes": 1,
             "file": "p1.esp.pcl.zst", "sha256": good, "expandable": False,
             "unique_guid": "6C2E9F71-1A3B-4F0A-9C1D-2E7B85A0C3D4", "attrs": "0" * 16},
            {"index": 3, "type_guid": "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7",
             "role": "windows", "fs": "ntfs", "start_sector": 1085440,
             "size_bytes": 40 << 30, "used_bytes": 1, "file": "p3.windows.pcl.zst",
             "sha256": sha or good, "expandable": True,
             "unique_guid": "6C2E9F71-1A3B-4F0A-9C1D-2E7B85A0C3D5", "attrs": "0" * 16},
        ]
        return {"family": 256, "os": "windows", "boot_ca": [], "boot_ca_error": None,
                "source_disk_bytes": 256060514304, "scheme": "gpt", "sector_size": 512,
                "disk_guid": DISK_GUID, "partitions": parts,
                "total_compressed_bytes": 2 * len(NEW_BYTES), "compression": "zstd-3"}
    return capture


@pytest.fixture()
def lab(tmp_path, monkeypatch):
    root = tmp_path / "images"
    write_image(root, SOURCE)
    write_image(root, OWNED)
    ovmf = tmp_path / "ovmf"
    ovmf.mkdir()
    for name in ("OVMF_CODE_4M.fd", "OVMF_VARS_4M.fd", "OVMF_CODE_4M.secboot.fd",
                 "OVMF_VARS_4M.ms.fd"):
        (ovmf / name).write_bytes(name.encode())
    monkeypatch.setattr(vmdisk, "build_disk", fake_build)
    monkeypatch.setattr(vmdisk, "capture_disk", fake_capture())
    conn = connect(tmp_path / "imagesrv.db")
    set_setting(conn, vmupdate.NIC_KEY, "eth0")   # ‏#1468: "כרטיס לעדכוני VM"
    library = ImageLibrary(root)
    qemu = FakeQemu()

    def make(machine=None, **kw):
        kw.setdefault("deploy_interface", lambda: "eth1")
        kw.setdefault("maintenance_every", None)
        return vmupdate.VmUpdate(conn, library, machine=machine or FakeMachine(),
                                 ovmf_dir=ovmf, popen=qemu.popen, qmp_call=qemu.qmp, **kw)

    yield {"conn": conn, "library": library, "qemu": qemu, "make": make, "root": root,
           "monkeypatch": monkeypatch}
    for proc in qemu.procs:            # תהליכון ה-_watch לא נשאר תלוי אחרי הטסט
        if proc.returncode is None:
            proc.finish(0)


def wait_for(engine, state, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = engine.status()
        if current["state"] == state:
            return current
        time.sleep(0.05)
    raise AssertionError(f"state stayed {engine.status()} (wanted {state})")


def events(conn):
    return [r[0] for r in conn.execute("SELECT event FROM journal ORDER BY rowid")]


# --- תנאים מוקדמים: סירוב בשם ובמספרים ------------------------------------------


def refusal(engine, image="img_7f3a91"):
    with pytest.raises(vmupdate.Refusal) as caught:
        engine.start(image, "noc")
    return caught.value


def test_no_kvm_is_refused_by_name_and_never_falls_back_to_tcg(lab):
    engine = lab["make"](FakeMachine(kvm="אין /dev/kvm"))
    exc = refusal(engine)
    assert (exc.status, exc.reason) == (503, "no_kvm")
    assert "אין תמיכה בווירטואליזציה" in exc.message
    assert lab["qemu"].procs == [] and not engine.workdir.exists()


def test_tcg_only_behind_the_explicit_test_flag(lab):
    engine = lab["make"](FakeMachine(kvm="אין /dev/kvm"), allow_tcg=True)
    engine.start("img_7f3a91", "noc")
    wait_for(engine, "running")
    argv = lab["qemu"].procs[0].args
    assert "q35,accel=tcg,smm=on" in argv and "max" in argv


def test_memory_refusal_carries_the_numbers(lab):
    exc = refusal(lab["make"](FakeMachine(mem=12000)))
    assert (exc.status, exc.reason) == (503, "memory")
    assert exc.numbers == {"vm_mib": 16384, "reserve_mib": 4096, "available_mib": 12000}
    assert "16.0 GB" in exc.message and "11.7 GB" in exc.message


def test_unreadable_memory_is_a_refusal_not_a_pass(lab):
    exc = refusal(lab["make"](FakeMachine(mem=None)))
    assert exc.reason == "memory"


def test_vm_may_not_take_every_core(lab):
    exc = refusal(lab["make"](FakeMachine(cpus=4)))
    assert exc.reason == "cpus" and exc.numbers == {"vm_cpus": 4, "server_cpus": 4}


@pytest.mark.system_reserve   # #1737: the real 10GiB is one of the numbers (conftest _host_free_space)
def test_disk_space_refusal_names_every_number(lab):
    exc = refusal(lab["make"](FakeMachine(free=50 * 10**9)))
    assert (exc.status, exc.reason) == (507, "disk_space")
    # תפוס 31457280+84509376512, מרווח 30GiB, גרסה חדשה = הדחוס 57982058496 (‏#1390),
    # ורזרבה למערכת 10GiB (‏#1737)
    need = 31457280 + 84509376512 + 30 * (1 << 30) + 57982058496 + 10 * (1 << 30)
    assert exc.numbers == {"need_bytes": need, "free_bytes": 50 * 10**9}
    assert "פנויים 50.0 GB" in exc.message
    assert "רזרבה למערכת 10.7 GB" in exc.message


def test_missing_tools_are_named(lab):
    exc = refusal(lab["make"](FakeMachine(missing={"qemu-system-x86_64",
                                                     "OVMF_VARS_4M.ms.fd"})))
    assert exc.reason == "missing_tool"
    assert "qemu-system-x86_64" in exc.message and "OVMF_VARS_4M.ms.fd" in exc.message


# ‏#1468: הכרטיס של ה-VM (הפצה מסורבת, ניהול מותר, macvtap, ‏lease) —
# ‏`test_vmnet_1468.py`.


def test_unknown_image_is_404(lab):
    exc = refusal(lab["make"](), image="img_ffffff")
    assert (exc.status, exc.reason) == (404, "image")


# --- מחזור החיים ------------------------------------------------------------------


def test_start_builds_boots_and_the_argv_never_bridges(lab):
    engine = lab["make"]()
    engine.start("img_7f3a91", "noc")
    status = wait_for(engine, "running")
    assert status["accel"] == "kvm" and status["secure_boot"] is True
    assert status["resources"]["memory_mib"] == 16384 and status["resources"]["cpus"] == 4
    argv = lab["qemu"].procs[0].args
    joined = " ".join(argv)
    mac = vmupdate.get_setting(lab["conn"], vmupdate.MAC_KEY)
    assert f"-netdev tap,id=net0,fd={TAP_FD}" in joined     # #1468: macvtap, לא slirp
    assert f"e1000e,netdev=net0,mac={mac},romfile=" in joined and "ide-hd" in joined
    assert "user,id=" not in joined and "bridge" not in joined
    assert f"unix:{engine.workdir / 'vnc.sock'},share=force-shared" in argv
    # הבידוד: QEMU רץ כמשתמש ה-VM, ואין שום פתח נכנס או שיתוף
    assert argv[argv.index("-run-with") + 1] == "user=imagesrv-vm"
    for forbidden in ("hostfwd", "guestfwd", "smb=", "bridge", "-vnc :", "tcp:", "ifname=",
                      "script="):
        assert forbidden not in joined, forbidden
    assert "elevateprivileges" not in joined     # נשבר עם run-with (SIGSYS, נמדד)
    # ה-macvtap על הכרטיס שנבחר, והבידוד נטען לפני ההדלקה — בלי כתובת עדיין
    assert engine.machine.taps == [("eth0", mac)]
    assert engine.machine.firewalls == [(999, mac)]
    assert lab["qemu"].kwargs[0]["pass_fds"] == (TAP_FD,)
    assert engine.machine.closed == [TAP_FD]     # ל-QEMU עותק משלו
    assert "OVMF_CODE_4M.secboot.fd" in joined
    assert (engine.workdir / "vars.fd").read_bytes() == b"OVMF_VARS_4M.ms.fd"
    assert events(lab["conn"])[-2:] == ["vmupdate_start", "vmupdate_running"]


def test_whoever_sees_running_also_sees_its_journal_row(lab, monkeypatch):
    """‏#1468: ‏`running` ושורת היומן שלו הם רגע אחד. כשהיומן נכתב אחרי שחרור
    הנעילה, קורא של status() ראה `running` בלי השורה (נכשל בריצה מלאה תחת עומס).
    כאן הכתיבה איטית בכוונה, כדי שהחלון יהיה תמיד פתוח ולא רק תחת עומס."""
    real = vmupdate.journal

    def slow(conn, event, *args, **kwargs):
        if event == "vmupdate_running":
            time.sleep(0.5)
        return real(conn, event, *args, **kwargs)

    monkeypatch.setattr(vmupdate, "journal", slow)
    engine = lab["make"]()
    engine.start("img_7f3a91", "noc")
    wait_for(engine, "running")
    assert events(lab["conn"])[-1] == "vmupdate_running", events(lab["conn"])


def test_a_second_vm_is_refused_while_one_is_open(lab):
    engine = lab["make"]()
    engine.start("img_7f3a91", "noc")
    wait_for(engine, "running")
    exc = refusal(engine, image="img_0bb001")
    assert (exc.status, exc.reason) == (409, "busy")


def test_shutdown_is_acpi_and_the_vm_ends_stopped(lab):
    engine = lab["make"]()
    engine.start("img_7f3a91", "noc")
    wait_for(engine, "running")
    engine.shutdown("noc")
    assert lab["qemu"].qmp_calls == ["system_powerdown"]
    status = wait_for(engine, "stopped")
    assert status["forced"] is False and status["runtime_seconds"] is not None


def test_force_off_falls_back_to_kill_when_qmp_is_dead(lab):
    lab["qemu"].qmp_fails = True
    engine = lab["make"]()
    engine.start("img_7f3a91", "noc")
    wait_for(engine, "running")
    engine.force_off("noc")
    status = wait_for(engine, "stopped")
    assert status["forced"] is True
    assert lab["qemu"].procs[0].returncode == -9
    assert "vmupdate_force_off" in events(lab["conn"])


def test_qemu_that_dies_on_its_own_is_a_named_failure(lab):
    engine = lab["make"]()
    engine.start("img_7f3a91", "noc")
    wait_for(engine, "running")
    (engine.workdir / "qemu.log").write_text("qemu: could not open disk\n")
    lab["qemu"].procs[0].finish(1)
    status = wait_for(engine, "failed")
    assert status["error"]["reason"] == "qemu_exit"
    assert "could not open disk" in status["error"]["message"]
    assert status["disk_ready"] is True     # הדיסק שלם — אפשר להדליק שוב


def test_failed_build_leaves_nothing_bootable(lab):
    def broken(*_a, **_k):
        raise vmdisk.DiskError("sha256", "מחיצה 3: ה-sha256 אינו תואם")
    lab["monkeypatch"].setattr(vmdisk, "build_disk", broken)
    engine = lab["make"]()
    engine.start("img_7f3a91", "noc")
    status = wait_for(engine, "failed")
    assert status["error"] == {"reason": "sha256", "message": "מחיצה 3: ה-sha256 אינו תואם"}
    assert status["disk_ready"] is False and lab["qemu"].procs == []
    with pytest.raises(vmupdate.Refusal) as caught:
        engine.boot("noc")
    assert caught.value.reason == "state"


# --- השמירה ---------------------------------------------------------------------


def run_to_stopped(lab, image="img_7f3a91"):
    engine = lab["make"]()
    engine.start(image, "noc")
    wait_for(engine, "running")
    engine.shutdown("noc")
    wait_for(engine, "stopped")
    return engine


def test_save_is_refused_while_the_vm_runs(lab):
    engine = lab["make"]()
    engine.start("img_7f3a91", "noc")
    wait_for(engine, "running")
    with pytest.raises(vmupdate.Refusal) as caught:
        engine.save("noc", "Office 2024 v2")
    assert (caught.value.status, caught.value.reason) == (409, "state")


def test_save_lands_a_new_version_and_keeps_the_old_one(lab):
    engine = run_to_stopped(lab)
    before = set(lab["library"].scan())
    engine.save("noc", "Office 2024 v2", "after October updates")
    status = wait_for(engine, "saved")
    new_id = status["result"]["image_id"]
    assert set(lab["library"].scan()) == before | {new_id}
    new = lab["library"].get(new_id)
    assert new["name"] == "Office 2024 v2" and new["description"] == "after October updates"
    assert new["version"] == 2 and new["previous_id"] == "img_7f3a91"
    assert new["folder"] == "Office" and new["created_by"] == "noc"
    assert new["target_floor_bytes"] is None and "machine_mac" not in new
    assert lab["library"].get("img_7f3a91")["name"] == "Office 2024 Standard"
    assert not engine.workdir.exists()
    assert events(lab["conn"])[-2:] == ["vmupdate_save", "vmupdate_saved"]


def test_a_bound_image_stays_bound_in_its_new_version(lab):
    engine = run_to_stopped(lab, image="img_0bb001")
    engine.save("noc", "Owned v4")
    new = lab["library"].get(wait_for(engine, "saved")["result"]["image_id"])
    assert new["machine_mac"] == "aa:bb:cc:00:00:01" and new["version"] == 4


def test_a_bad_sha_never_reaches_the_library(lab):
    lab["monkeypatch"].setattr(vmdisk, "capture_disk", fake_capture(sha="cd" * 32))
    engine = run_to_stopped(lab)
    before = set(lab["library"].scan())
    engine.save("noc", "Office 2024 v2")
    status = wait_for(engine, "failed")
    assert status["error"]["reason"] == "verify" and "sha256 mismatch" in status["error"]["message"]
    assert set(lab["library"].scan()) == before
    assert not (engine.workdir / "out").exists()


def test_hibernated_windows_keeps_the_disk_for_another_boot(lab):
    def hibernated(*_a, **_k):
        raise vmdisk.DiskError("hibernated", "הדיסק מהובר — כבה את Windows כיבוי מלא")
    lab["monkeypatch"].setattr(vmdisk, "capture_disk", hibernated)
    engine = run_to_stopped(lab)
    engine.save("noc", "Office 2024 v2")
    status = wait_for(engine, "failed")
    assert status["error"]["reason"] == "hibernated" and status["disk_ready"] is True
    engine.boot("noc")
    assert wait_for(engine, "running")["error"] is None


@pytest.mark.parametrize("reason", ["shrink", "capture", "stream", "unchecked"])
def test_a_failed_save_keeps_the_disk_for_another_boot(lab, reason):
    """‏#1491: השמירה מכווצת עותק ואינה כותבת לדיסק — גם כשל באמצע הכיווץ
    או הזרם משאיר אותו שלם: מדליקים שוב (ה-chkdsk שההודעה מבקשת) ושומרים."""
    def broken(*_a, **_k):
        raise vmdisk.DiskError(reason, "נכשל באמצע")
    lab["monkeypatch"].setattr(vmdisk, "capture_disk", broken)
    engine = run_to_stopped(lab)
    engine.save("noc", "Office 2024 v2")
    status = wait_for(engine, "failed")
    assert status["error"]["reason"] == reason and status["disk_ready"] is True
    engine.boot("noc")
    assert wait_for(engine, "running")["error"] is None


def test_the_new_version_name_is_validated_not_cleaned(lab):
    engine = run_to_stopped(lab)
    with pytest.raises(ValueError):
        engine.save("noc", "trailing space ")
    assert engine.status()["state"] == "stopped"


# --- ביטול והתאוששות --------------------------------------------------------------


def test_discard_is_refused_while_running_and_wipes_when_stopped(lab, monkeypatch):
    monkeypatch.setattr(vmdisk, "detach_all", lambda host, disk: None)
    engine = lab["make"]()
    engine.start("img_7f3a91", "noc")
    wait_for(engine, "running")
    with pytest.raises(vmupdate.Refusal):
        engine.discard("noc")
    engine.shutdown("noc")
    wait_for(engine, "stopped")
    assert engine.discard("noc")["state"] == "idle"
    assert not engine.workdir.exists()
    assert events(lab["conn"])[-1] == "vmupdate_discard"


def test_a_restart_mid_run_is_interrupted_with_the_disk_kept(lab):
    engine = lab["make"]()
    engine.start("img_7f3a91", "noc")
    wait_for(engine, "running")
    again = lab["make"]()                 # שרת שעלה מחדש: אותו אזור עבודה
    status = again.status()
    assert status["state"] == "failed" and status["disk_ready"] is True
    assert status["error"]["reason"] == "interrupted"
    assert refusal(again).reason == "busy"   # עדיין פתוח — מדליקים או מבטלים


def test_a_restart_mid_save_does_not_trust_the_disk(lab):
    engine = run_to_stopped(lab)
    state = json.loads((engine.workdir / "state.json").read_text(encoding="utf-8"))
    state["state"] = "saving"
    (engine.workdir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    status = lab["make"]().status()
    assert status["state"] == "failed" and status["disk_ready"] is False


# --- פונקציות טהורות --------------------------------------------------------------


@pytest.mark.parametrize("boot_ca,expected", [([], False), (None, True),
                                              (["Microsoft UEFI CA 2023"], True)])
def test_secure_boot_is_off_only_on_evidence_of_unsigned_loaders(boot_ca, expected):
    assert vmupdate.secure_boot_for({"boot_ca": boot_ca}) is expected
    assert vmupdate.secure_boot_for({}) is True


def test_qemu_argv_without_secure_boot_uses_the_plain_firmware(tmp_path):
    argv = vmupdate.qemu_argv(workdir=tmp_path, memory_mib=512, cpus=1, accel="kvm",
                              secure_boot=False, tap_fd=TAP_FD, mac="02:00:00:00:00:01",
                              ovmf_dir=Path("/fw"))
    joined = " ".join(argv)
    assert "smm=on" not in joined and str(Path("/fw") / "OVMF_CODE_4M.fd") in joined
    assert "-cpu host" in joined and "-m 512" in joined


@pytest.mark.parametrize("previous,expected", [(None, 2), (1, 2), (3, 4), ("7", 2), (0, 2)])
def test_compose_manifest_increments_the_version(previous, expected):
    source = {**SOURCE}
    if previous is not None:
        source["version"] = previous
    captured = fake_capture()(None, None, Path("/nonexistent-never-written"), lambda n: None) \
        if False else {"partitions": SOURCE["partitions"], "sector_size": 512,
                       "source_disk_bytes": SOURCE["source_disk_bytes"]}
    manifest = vmdisk.compose_manifest(source, captured, new_id="img_aaaaaa", name="n",
                                       description="", user="noc", now="t")
    assert manifest["version"] == expected
    # נגזר מהפריסה (#82/#1171), לא הועתק מהמקור
    assert manifest["min_target_bytes"] == 1085440 * 512 + 254803968000 + 256 * 1024


def test_settings_are_validated_and_bounded(lab):
    conn = lab["conn"]
    assert vmupdate.read_settings(conn) == {"memory_mib": 16384, "cpus": 4}
    assert vmupdate.write_settings(conn, {"memory_mib": 8192, "cpus": 2}) == \
        {"memory_mib": 8192, "cpus": 2}
    for bad in ({"memory_mib": 10}, {"cpus": "4"}, {"disk": 1}):
        with pytest.raises(ValueError):
            vmupdate.write_settings(conn, bad)


# --- בידוד הרשת: בלי חומת אש — בלי VM -------------------------------------------


def test_a_firewall_that_did_not_load_keeps_the_vm_off(lab):
    engine = lab["make"](FakeMachine(firewall="nft -f נכשל"))
    engine.start("img_7f3a91", "noc")
    status = wait_for(engine, "failed")
    assert status["error"]["reason"] == "firewall" and "nft -f נכשל" in status["error"]["message"]
    assert lab["qemu"].procs == [] and status["disk_ready"] is True
    # ה-macvtap שכבר נוצר אינו נשאר פתוח ותלוי (#1468)
    assert engine.machine.closed == [TAP_FD] and engine.machine.downs == 1


def test_without_the_vm_user_nothing_starts(lab):
    exc = refusal(lab["make"](FakeMachine(user=False)))
    assert exc.reason == "vm_user" and "imagesrv-vm" in exc.message


def test_missing_guestfs_is_named(lab):
    exc = refusal(lab["make"](FakeMachine(missing={"python3-guestfs"})))
    assert exc.reason == "missing_tool" and "python3-guestfs" in exc.message


# --- הכרעות נדב: שליטה, 15 דקות, 7 ימים ----------------------------------------------


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture()
def timed(lab):
    clock = Clock()
    engine = lab["make"](now=clock, maintenance_every=None)
    engine.start("img_7f3a91", "noc")
    wait_for(engine, "running")
    return engine, clock


def test_a_second_admin_is_view_only(timed):
    engine, _clock = timed
    engine.status("dana")
    assert engine.status("dana")["you_control"] is False
    for action in (engine.shutdown, engine.force_off, engine.discard):
        with pytest.raises(vmupdate.Refusal) as caught:
            action("dana")
        assert (caught.value.status, caught.value.reason) == (403, "view_only")
        assert "noc" in caught.value.message
    with pytest.raises(vmupdate.Refusal):
        engine.save("dana", "x")


def test_take_control_approved_by_the_controller(lab, timed):
    engine, _clock = timed
    engine.status("noc")                     # השולט נוכח
    asked = engine.request_control("dana")
    assert asked["control_request"]["by"] == "dana" and asked["controller"] == "noc"
    assert engine.status("noc")["control_request"]["seconds_left"] == 60
    engine.answer_control("noc", True)
    status = engine.status("dana")
    assert status["controller"] == "dana" and status["you_control"] is True
    assert status["control_last"] == {"by": "dana", "outcome": "approved"}
    assert "vmupdate_control_granted" in events(lab["conn"])
    engine.shutdown("dana")                  # עכשיו מותר לה


def test_take_control_denied(lab, timed):
    engine, _clock = timed
    engine.status("noc")
    engine.request_control("dana")
    engine.answer_control("noc", False)
    status = engine.status("dana")
    assert status["controller"] == "noc" and status["control_request"] is None
    assert status["control_last"] == {"by": "dana", "outcome": "denied"}
    assert "vmupdate_control_denied" in events(lab["conn"])


def test_an_unanswered_request_is_denied_after_60_seconds(lab, timed):
    engine, clock = timed
    engine.status("noc")
    engine.request_control("dana")
    clock.t += 59
    engine.status("noc")                     # השולט עדיין כאן, לא עונה
    assert engine.status("dana")["control_request"] is not None
    clock.t += 2
    status = engine.status("dana")
    assert status["controller"] == "noc" and status["control_request"] is None
    assert status["control_last"]["outcome"] == "timeout"
    assert "vmupdate_control_timeout" in events(lab["conn"])


def test_a_controller_gone_for_two_minutes_hands_over_automatically(lab, timed):
    engine, clock = timed
    engine.status("noc")
    clock.t += 121                           # השולט לא שאל 2 דקות
    status = engine.request_control("dana")
    assert status["controller"] == "dana" and status["control_last"]["outcome"] == "auto"
    assert "vmupdate_control_auto" in events(lab["conn"])


def test_only_the_controller_answers(timed):
    engine, _clock = timed
    engine.status("noc")
    engine.request_control("dana")
    with pytest.raises(vmupdate.Refusal) as caught:
        engine.answer_control("dana", True)
    assert caught.value.reason == "view_only"


def test_force_is_offered_after_15_minutes_of_graceful_shutdown(lab):
    clock = Clock()
    qemu = lab["qemu"]
    engine = lab["make"](now=clock, maintenance_every=None)
    engine.start("img_7f3a91", "noc")
    wait_for(engine, "running")
    original = qemu.qmp
    qemu.qmp = lambda path, cmd: {"return": {}}     # Windows מתעדכן ולא כבה
    engine._qmp = qemu.qmp
    engine.shutdown("noc")
    clock.t += 14 * 60
    assert engine.status("noc")["force_offered"] is False
    clock.t += 61
    status = engine.status("noc")
    assert status["force_offered"] is True and status["shutdown_waited_seconds"] == 901
    qemu.qmp = original
    engine._qmp = original
    engine.force_off("noc")
    wait_for(engine, "stopped")


def test_force_off_then_boot_again_on_the_same_disk(lab):
    engine = lab["make"]()
    engine.start("img_7f3a91", "noc")
    wait_for(engine, "running")
    (engine.workdir / "disk.raw").write_bytes(b"work in progress")
    engine.force_off("noc")
    assert wait_for(engine, "stopped")["forced"] is True
    engine.boot("noc")
    assert wait_for(engine, "running")["forced"] is False
    assert (engine.workdir / "disk.raw").read_bytes() == b"work in progress"
    assert len(lab["qemu"].procs) == 2


def test_an_undecided_disk_is_deleted_after_7_days_with_a_journal_line(lab, monkeypatch):
    monkeypatch.setattr(vmdisk, "detach_all", lambda host, disk: None)
    clock = Clock()
    engine = lab["make"](now=clock, maintenance_every=None)
    engine.start("img_7f3a91", "noc")
    wait_for(engine, "running")
    engine.shutdown("noc")
    status = wait_for(engine, "stopped")
    assert status["expires_in_seconds"] == 7 * 24 * 3600
    clock.t += 7 * 24 * 3600 - 1
    assert engine.status()["state"] == "stopped" and engine.workdir.exists()
    clock.t += 1
    assert engine.status()["state"] == "idle"
    assert not engine.workdir.exists()
    assert events(lab["conn"])[-1] == "vmupdate_expired"


def test_a_firewall_flushed_while_running_is_reloaded_or_the_vm_goes_off(lab):
    """‏`flush ruleset` של חומת האש הראשית מוחק גם את הטבלה שלנו. סבב
    התחזוקה טוען אותה שוב; אם לא הצליח — ה-VM נכבה בכוח, ונרשם ביומן."""
    machine = FakeMachine()
    engine = lab["make"](machine)
    engine.start("img_7f3a91", "noc")
    wait_for(engine, "running")
    engine.maintenance(guard=True)
    assert len(machine.firewalls) == 2 and engine.status()["state"] == "running"
    machine._firewall = "nft: no such table"
    engine.maintenance(guard=True)
    status = wait_for(engine, "stopped")
    assert status["forced"] is True
    assert "vmupdate_firewall_lost" in events(lab["conn"])
    assert any("בידוד הרשת" in w for w in status["warnings"])


def test_the_viewer_list_tracks_join_leave_and_marks_the_controller(timed):
    """הפס "גם X צופה ב-VM": מי מחובר למסך, מאיפה ומאז, והשולט מסומן."""
    engine, _clock = timed
    assert engine.status("noc")["viewers"] == []
    mine = engine.viewer_join("noc", "10.0.0.5")
    hers = engine.viewer_join("dana", "10.0.0.9")
    viewers = engine.status("dana")["viewers"]
    assert [(v["user"], v["address"], v["controller"]) for v in viewers] == [
        ("noc", "10.0.0.5", True), ("dana", "10.0.0.9", False)]
    assert all(v["since"] for v in viewers)
    engine.viewer_leave(mine)
    assert [v["user"] for v in engine.status("dana")["viewers"]] == ["dana"]
    engine.viewer_leave(hers)
    engine.viewer_leave(hers)            # עזיבה כפולה אינה שוברת
    assert engine.status("dana")["viewers"] == []


def test_the_controller_mark_follows_a_handover(timed):
    engine, _clock = timed
    engine.status("noc")
    engine.viewer_join("noc", "10.0.0.5")
    engine.viewer_join("dana", "10.0.0.9")
    engine.request_control("dana")
    engine.answer_control("noc", True)
    marks = {v["user"]: v["controller"] for v in engine.status("dana")["viewers"]}
    assert marks == {"noc": False, "dana": True}


def test_cancel_while_preparing_stops_the_build_and_leaves_nothing(lab, monkeypatch):
    """"ביטול" בחלון ההכנה (המוקאפ): התהליכון עוצר בחתיכה הבאה, הדיסק החלקי
    נמחק, והמצב חוזר ל-idle — בלי VM ובלי שינוי באימג'."""
    monkeypatch.setattr(vmdisk, "detach_all", lambda host, disk: None)
    gate = threading.Event()
    entered = threading.Event()

    def slow_build(host, manifest, image_dir, workdir, progress):
        (workdir / "disk.raw").write_bytes(b"partial")
        entered.set()
        assert gate.wait(10), "test did not release the build after cancellation"
        progress(1)                     # כאן הביטול נתפס
        raise AssertionError("the build went on after the cancel")

    monkeypatch.setattr(vmdisk, "build_disk", slow_build)
    engine = lab["make"]()
    engine.start("img_7f3a91", "noc")
    try:
        assert entered.wait(10), "build did not reach the cancellation barrier"
        assert engine.discard("noc")["state"] == "preparing"
    finally:
        gate.set()
    wait_for(engine, "idle")
    # idle is published before the journal write. Join the actual preparation
    # worker before checking the journal, and fail if it has not completed.
    engine.join()
    assert not any(t.is_alive() for t in engine._threads)
    assert not engine.workdir.exists() and lab["qemu"].procs == []
    assert events(lab["conn"])[-1] == "vmupdate_discard"


def test_precheck_reports_what_start_would_refuse(lab):
    assert lab["make"](FakeMachine(kvm="אין /dev/kvm")).precheck("img_7f3a91")["code"] == "no_kvm"
    good = lab["make"]().precheck("img_7f3a91")
    assert good["ok"] is True and good["memory_mib"] == 16384 and good["accel"] == "kvm"
    engine = lab["make"]()
    engine.start("img_7f3a91", "noc")
    wait_for(engine, "running")
    busy = engine.precheck("img_0bb001")
    assert busy["code"] == "busy" and busy["details"]["controller"] == "noc"
    assert busy["details"]["image_name"] == "Office 2024 Standard"


def test_viewer_ids_are_opaque_and_you_is_marked(timed):
    engine, _clock = timed
    token = engine.viewer_join("noc", "10.0.0.5")
    engine.viewer_join("dana", "10.0.0.9")
    rows = engine.status("dana")["viewers"]
    assert all(token not in r["id"] and len(r["id"]) == 12 for r in rows)
    assert {r["user"]: r["you"] for r in rows} == {"noc": False, "dana": True}
