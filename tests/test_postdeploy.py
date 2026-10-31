"""‏#720 — staging של דרייברים ב-NTFS המשוחזר (`agent/lib/postdeploy/postdeploy.sh`).

הפונקציה רצה על ה-sh האמיתי עם זיופים של הכלים שהיא קוראת: ‏curl (השרת),
‏ntfs-3g/umount (המאונט), ‏hivewrite (הרג'יסטרי, עם "hive" שהוא קובץ טקסט
של DevicePath) ו-`json_get` (אין jq בווינדוס — הפילטרים האמיתיים נבדקים
בנפרד תחת `requires jq`). מה שנבדק הוא **מה נשאר על הדיסק** ו**מה דווח**:
הקבצים נמצאים בייט-בייט, ‏DevicePath נכתב כ-REG_EXPAND_SZ (‏`-x`) ושומר
את הערך הקיים, ו-`drivers.json` אומר את האמת — ‏staged / no_match / skipped
/ failed — ובכל מקרה השחזור נשאר `done` (עיקרון 5: "הושלם, דרייברים לא
הונחו" הוא מצב, לא כשל שקט).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_captured_stdout import stub

pytestmark = requires_native(("bash", BASH))

WIN_PLAN = "1|EBD0A0A2-B9E5-4433-87C0-68B6B72699C7|windows|ntfs|4096|1|p1|y|false|u"
LINUX_PLAN = "2|0FC63DAF-8483-4772-8E79-3D69D8477DE4|linux|ext4|4096|1|p2|y|false|u"
STOCK = r"%SystemRoot%\inf"
ENTRY = r"%SystemRoot%\..\ImageSrv\Drivers"

FILES = {"intel-nic": {"e1d68x64.inf": b"[Version]\n", "x64/e1d68x64.sys": b"\x4d\x5a sys"},
         "ahci-fix": {"storahci.inf": b"[Version] ahci\n"}}

#: ‏curl מזויף: ‏`?mac=` → תשובת השרת מהקובץ; ‏`/files/<pkg>/<path>` → הקובץ
#: מתיקיית "השרת". כל URL אחר — 22 (כמו `curl -f` על 404).
CURL = r'''
for a in "$@"; do url="$a"; done
case "$url" in
  *"/api/v1/agent/drivers?mac="*) cat "$FAKE_SERVER/answer.json"; exit "${FAKE_QUERY_RC:-0}" ;;
  *"/api/v1/agent/drivers/"*"/files/"*)
    rel=${url#*/api/v1/agent/drivers/}; pkg=${rel%%/files/*}; path=${rel#*/files/}
    [ -f "$FAKE_SERVER/pkgs/$pkg/$path" ] || exit 22
    cat "$FAKE_SERVER/pkgs/$pkg/$path" ;;
  *) exit 22 ;;
esac
'''

#: ‏hivewrite מזויף: ה-"hive" הוא קובץ טקסט שמחזיק את DevicePath. ‏-g
#: מדפיס אותו; כתיבה עם -x מחליפה אותו ורושמת את הקריאה; כתיבה **בלי** -x
#: יוצאת 9 — ‏REG_SZ היה שובר את %SystemRoot%, ולכן הזיוף מסרב לה.
HIVEWRITE = r'''
echo "$*" >> "$FAKE_SERVER/hivewrite.calls"
case "$2" in */SYSTEM)
  # #1331: SYSTEM is a tree of files -- SYSTEM.d/<key, \ as />/<value>. -g of a
  # missing value is 3, like hivewrite; -d writes into an existing key only;
  # any other write to SYSTEM is refused (a DWORD written as a string is a bug).
  f="$2.d/$(printf '%s' "$3" | tr '\\' '/')/$4"
  case "$1" in
    -g) [ -z "${FAKE_SYS_GET_RC:-}" ] || exit "$FAKE_SYS_GET_RC"
        [ -f "$f" ] || exit 3; cat "$f"; exit 0 ;;
    -d) [ "${FAKE_SYS_RC:-0}" = 0 ] || exit "$FAKE_SYS_RC"
        [ -d "${f%/*}" ] || exit 3
        [ -n "${FAKE_SYS_NOOP:-}" ] || printf '%s\n' "$5" > "$f"; exit 0 ;;
    *)  echo "SYSTEM write without -d refused" >&2; exit 9 ;;
  esac ;;
esac
if [ "$1" = "-g" ]; then
  [ "$4" = "DevicePath" ] || exit 3
  cat "$2"; exit 0
fi
[ "$1" = "-x" ] || { echo "REG_SZ write refused" >&2; exit 9; }
[ "${FAKE_HIVE_RC:-0}" = 0 ] || exit "$FAKE_HIVE_RC"
[ "$4" = "DevicePath" ] || exit 3
printf '%s\n' "$5" > "$2"
'''


#: ‏#1331: ה-SYSTEM של אימג' שנקלט ממכונה שהותקנה במצב RAID — ‏storahci
#: ‏Start=3 עם ‏StartOverride\0=3, ‏stornvme ‏Start=3. ‏ControlSet002 קיים כדי
#: שכתיבה ל-ControlSet001 הקבוע (במקום Select\Current) תיתפס.
SYSTEM_RAID = {
    "Select/Current": "2",
    "ControlSet001/Services/storahci/Start": "3",
    "ControlSet002/Services/storahci/Start": "3",
    "ControlSet002/Services/storahci/StartOverride/0": "3",
    "ControlSet002/Services/stornvme/Start": "3",
}
SVC = "ControlSet002/Services"


def answer_for(packages: dict[str, dict[str, bytes]], inventory=True, tamper: str | None = None) -> dict:
    out = []
    for name, files in packages.items():
        entries = []
        for path, data in files.items():
            sha = hashlib.sha256(data).hexdigest()
            if tamper == f"{name}/{path}":
                sha = "0" * 64
            entries.append({"path": path, "sha256": sha,
                            "url": f"/api/v1/agent/drivers/{name}/files/{path}"})
        out.append({"name": name, "by": "pci", "files": entries})
    return {"ok": True, "inventory": inventory, "packages": out}


class Box:
    def __init__(self, tmp_path: Path, packages=FILES, *, answer: dict | None = None,
                 plan: str = WIN_PLAN, device_path: str | None = STOCK,
                 stubs: dict[str, str] | None = None, env: str = "", plan_rc: int = 0,
                 system: dict[str, str] | None = SYSTEM_RAID):
        self.run = tmp_path / "run"
        self.win = self.run / "win"
        self.server = tmp_path / "server"
        (self.win / "Windows/System32/config").mkdir(parents=True)
        self.hive = self.win / "Windows/System32/config/SOFTWARE"
        if device_path is not None:
            self.hive.write_text(device_path + "\n", encoding="utf-8")
        self.system = self.win / "Windows/System32/config/SYSTEM"
        if system is not None:
            self.system.write_text("fake SYSTEM hive\n", encoding="utf-8")
            for key, value in system.items():
                target = self.win / "Windows/System32/config/SYSTEM.d" / key
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(value + "\n", newline="\n")
        self.answer = answer or answer_for(packages)
        (self.server / "pkgs").mkdir(parents=True)
        for name, files in packages.items():
            for path, data in files.items():
                target = self.server / "pkgs" / name / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        (self.server / "answer.json").write_text(json.dumps(self.answer), encoding="utf-8")
        # ‏json_get מזויף מהתשובה — אותן שורות שהפילטרים האמיתיים היו נותנים.
        # ‏LF בלבד: write_text בווינדוס היה כותב CRLF, ו-`read` בסוכן היה
        # משאיר \r בסוף ה-URL — כשל שאינו קיים מול jq אמיתי.
        pk = self.answer.get("packages", [])
        (self.server / "names").write_text("".join(p["name"] + "\n" for p in pk), newline="\n")
        (self.server / "rows").write_text("".join(
            f'{p["name"]}|{f["path"]}|{f["sha256"]}|{f["url"]}\n' for p in pk for f in p["files"]),
            newline="\n")
        stub_dir = tmp_path / "stubs"; stub_dir.mkdir()
        all_stubs = {"curl": CURL, "hivewrite": HIVEWRITE, "ntfs-3g": "exit 0", "umount": "exit 0",
                     **(stubs or {})}
        self.script = "".join(stub(stub_dir / n, b) for n, b in all_stubs.items()) + (
            f'export PATH="$(cd {posix(stub_dir)!r} && pwd):$PATH"; '
            f'export RUN_DIR={posix(self.run)!r} DEVROOT={posix(self.run)!r}/dev '
            f'LOG_FILE={posix(self.run / "agent.log")!r} FAKE_SERVER={posix(self.server)!r} '
            # ‏#433: SYSROOT ריק היה מפנה את bootentry.sh ל-efivars **האמיתי** של
            # מכונת הבדיקות (מעבדת ה-VM היא UEFI) — ו-efibootmgr היה כותב ל-NVRAM שלה.
            f'SYSROOT={posix(self.run)!r}/sys LIB_DIR={posix(AGENT)!r}/lib '
            f'SERVER=http://10.44.12.10:8080 MAC=b4:2e:99:07:1a:c4 {env}; '
            f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/jsonq.sh; '
            f'. {posix(AGENT)}/lib/restore/restore.sh; . {posix(AGENT)}/lib/postdeploy/hostname.sh; . {posix(AGENT)}/lib/postdeploy/unattend.sh; '
            f'. {posix(AGENT)}/lib/postdeploy/hostname_linux.sh; . {posix(AGENT)}/lib/postdeploy/linuxdrivers.sh; '
            f'. {posix(AGENT)}/lib/postdeploy/bootstorage.sh; . {posix(AGENT)}/lib/postdeploy/postdeploy.sh; '
            f'manifest_plan() {{ printf \'%s\\n\' {plan!r}; return {plan_rc}; }}; '
            f'partition_node() {{ echo /dev/fake; }}; '
            f'json_get() {{ case "$2" in ".ok") echo {str(self.answer.get("ok", False)).lower()};; '
            f'".inventory") echo {str(self.answer.get("inventory", False)).lower()};; '
            f'".packages[].name") cat "$FAKE_SERVER/names";; '
            f'*files*) cat "$FAKE_SERVER/rows";; *) echo null;; esac; }}; '
        )

    def stage(self, disk: str = "sda") -> tuple[subprocess.CompletedProcess, dict]:
        out = subprocess.run(
            [BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + self.script
             + f'stage_drivers {disk} /dev/null; echo "rc=$?"'],
            capture_output=True, text=True, encoding="utf-8",
            cwd=str(AGENT.parent), stdin=subprocess.DEVNULL,
        )
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip().endswith("rc=0"), "stage_drivers לעולם אינו נכשל: " + out.stdout
        result = json.loads((self.run / "drivers.json").read_text(encoding="utf-8"))
        return out, result

    def state(self) -> str:
        return (self.run / "state").read_text().strip()

    def calls(self) -> list[str]:
        path = self.server / "hivewrite.calls"
        return path.read_text().splitlines() if path.exists() else []

    def sysval(self, key: str) -> str | None:
        path = self.win / "Windows/System32/config/SYSTEM.d" / key
        return path.read_text().strip() if path.exists() else None

    def staged_files(self) -> dict[str, bytes]:
        root = self.win / "ImageSrv/Drivers"
        return {p.relative_to(root).as_posix(): p.read_bytes()
                for p in root.rglob("*") if p.is_file()} if root.exists() else {}


def test_matched_packages_land_on_the_disk_and_device_path_keeps_the_stock_entry(tmp_path):
    box = Box(tmp_path)
    _, result = box.stage()
    assert result == {"state": "staged", "packages": ["intel-nic", "ahci-fix"],
                      "boot": {"state": "enabled", "service": "storahci"}}
    assert box.state() == "staging"   # #1935: not terminal -- done is the caller's, after the mark
    assert box.staged_files() == {f"{n}/{p}": d for n, fs in FILES.items() for p, d in fs.items()}
    assert box.hive.read_text().strip() == f"{STOCK};{ENTRY}"
    writes = [c for c in box.calls() if c.startswith("-x ")]
    assert len(writes) == 1 and "DevicePath" in writes[0], box.calls()
    log = (box.run / "agent.log").read_text(encoding="utf-8")
    assert "drivers: staging intel-nic ahci-fix" in log


def test_a_download_whose_sha256_differs_stops_before_the_disk_is_touched(tmp_path):
    box = Box(tmp_path, answer=answer_for(FILES, tamper="intel-nic/x64/e1d68x64.sys"))
    _, result = box.stage()
    assert result["state"] == "failed" and "אי-התאמת sha256: intel-nic/x64/e1d68x64.sys" in result["error"]
    assert result["packages"] == []
    assert box.staged_files() == {}, "בייט אחד לא הועתק לדיסק"
    assert box.hive.read_text().strip() == STOCK, "DevicePath לא נגע"
    assert box.calls() == [], "המחיצה לא עוגנה כלל"
    assert box.state() == "staging"   # #1935: not terminal -- done is the caller's, after the mark


def test_a_file_the_server_cannot_serve_is_a_failure_not_a_partial_stage(tmp_path):
    box = Box(tmp_path)
    (box.server / "pkgs/ahci-fix/storahci.inf").unlink()
    _, result = box.stage()
    assert result["state"] == "failed" and "ההורדה נכשלה: ahci-fix/storahci.inf" in result["error"]
    assert box.staged_files() == {}


def test_a_copy_that_does_not_read_back_is_a_failure(tmp_path):
    """‏cp יצא 0 והבייטים על הדיסק שונים — הראיה היא הקריאה חזרה, לא קוד היציאה."""
    box = Box(tmp_path, stubs={"cp": 'printf garbage > "$2"; exit 0',
                               "umount": 'echo umount "$@" >> "$FAKE_SERVER/umounts"; exit 0'})
    _, result = box.stage()
    assert result["state"] == "failed" and "אי-התאמה בקריאה חזרה מהדיסק" in result["error"]
    assert box.hive.read_text().strip() == STOCK
    assert box.calls() == [], "לא הגענו לרג'יסטרי"
    assert (box.server / "umounts").read_text().count("umount") == 1, "הכשל משחרר את המאונט"


def test_no_matching_package_is_no_match_and_touches_nothing(tmp_path):
    box = Box(tmp_path, packages={}, answer=answer_for({}))
    _, result = box.stage()
    assert result == {"state": "no_match", "packages": []}
    assert box.calls() == [] and box.staged_files() == {}
    assert box.state() == "staging"   # #1935: not terminal -- done is the caller's, after the mark


def test_no_inventory_on_the_server_is_failed_not_no_match(tmp_path):
    box = Box(tmp_path, packages={}, answer=answer_for({}, inventory=False))
    _, result = box.stage()
    assert result["state"] == "failed" and "אין מלאי" in result["error"]


def test_a_server_that_does_not_answer_is_failed(tmp_path):
    box = Box(tmp_path, env="FAKE_QUERY_RC=7")
    _, result = box.stage()
    assert result["state"] == "failed" and "לא ענה" in result["error"]
    assert box.state() == "staging"   # #1935: not terminal -- done is the caller's, after the mark


def test_a_linux_image_is_skipped_by_name(tmp_path):
    # ‏#1331: ה-initramfs נבדק (`drivers.linux`, ‏test_linuxdrivers.py) — כאן
    # העיגון נכשל, וה-staging עצמו נשאר skipped/not_windows.
    box = Box(tmp_path, plan=LINUX_PLAN, stubs={"mount": "exit 32"})
    _, result = box.stage()
    assert {k: v for k, v in result.items() if k != "linux"} == {
        "state": "skipped", "packages": [], "reason": "not_windows"}
    assert result["linux"]["state"] == "failed"
    assert box.calls() == []


def test_a_failed_manifest_plan_is_failed_even_after_partial_windows_output(tmp_path):
    """#1130 negative control: the old pipeline reported skipped/not_windows."""
    box = Box(tmp_path, plan=WIN_PLAN, plan_rc=7)
    _, result = box.stage()
    assert result["state"] == "failed"
    assert "תוכנית המחיצות" in result["error"]
    assert box.calls() == [] and box.staged_files() == {}


def test_device_path_is_idempotent(tmp_path):
    box = Box(tmp_path, device_path=f"{STOCK};{ENTRY}")
    _, result = box.stage()
    assert result["state"] == "staged"
    assert box.hive.read_text().strip() == f"{STOCK};{ENTRY}"
    assert not [c for c in box.calls() if c.startswith("-x ")], "לא נכתב שוב"


def test_an_empty_device_path_gets_only_our_entry(tmp_path):
    box = Box(tmp_path, device_path="")
    _, result = box.stage()
    assert result["state"] == "staged"
    assert box.hive.read_text().strip() == ENTRY


def test_a_registry_write_that_fails_is_reported_and_the_disk_is_released(tmp_path):
    box = Box(tmp_path, env="FAKE_HIVE_RC=4",
              stubs={"umount": 'echo umount "$@" >> "$FAKE_SERVER/umounts"; exit 0'})
    _, result = box.stage()
    assert result["state"] == "failed" and "כתיבת DevicePath לא אומתה" in result["error"]
    assert box.hive.read_text().strip() == STOCK
    assert (box.server / "umounts").read_text().count("umount") == 1
    assert box.state() == "staging"   # #1935: not terminal -- done is the caller's, after the mark


def test_a_missing_software_hive_is_a_failure(tmp_path):
    box = Box(tmp_path, device_path=None)
    _, result = box.stage()
    assert result["state"] == "failed" and "כוורת SOFTWARE" in result["error"]


def test_a_mount_that_fails_is_reported(tmp_path):
    box = Box(tmp_path, stubs={"ntfs-3g": "exit 1"})
    _, result = box.stage()
    assert result["state"] == "failed" and "לעגן" in result["error"]
    assert box.staged_files() == {}


def test_an_unmount_that_fails_is_not_success(tmp_path):
    box = Box(tmp_path, stubs={"umount": "exit 32"})
    _, result = box.stage()
    assert result["state"] == "failed" and "לנתק" in result["error"]


@pytest.mark.parametrize("path", ["../escape.inf", "/abs.inf", "x\\y.inf"])
def test_a_path_that_leaves_the_package_folder_is_refused(tmp_path, path):
    answer = answer_for({"pkg": {"a.inf": b"x"}})
    answer["packages"][0]["files"][0]["path"] = path
    box = Box(tmp_path, packages={"pkg": {"a.inf": b"x"}}, answer=answer)
    _, result = box.stage()
    assert result["state"] == "failed" and "נתיב קובץ לא תקין" in result["error"]
    assert not (box.run / "drivers").exists() or not list((box.run / "drivers").rglob("*"))


# --- הדיווח הסופי נושא את התוצאה ---------------------------------------------


def test_the_progress_report_carries_drivers_only_once_it_exists(tmp_path):
    run = tmp_path / "run"; (run / "targets" / "sda").mkdir(parents=True)
    for name, value in {"state": "done", "base": "0", "total": "10"}.items():
        (run / "targets/sda" / name).write_text(value + "\n")
    (run / "state").write_text("done\n")
    script = (f'export RUN_DIR={posix(run)!r}; . {posix(AGENT)}/lib/core/common.sh; '
              f'. {posix(AGENT)}/lib/core/progress.sh; build_progress ses_1 b4:2e:99:07:1a:c4')
    before = json.loads(subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                                       stdin=subprocess.DEVNULL, cwd=str(AGENT.parent)).stdout)
    assert "drivers" not in before
    (run / "drivers.json").write_text('{"state":"staged","packages":["intel-nic"]}\n')
    after = json.loads(subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                                      stdin=subprocess.DEVNULL, cwd=str(AGENT.parent)).stdout)
    assert after["drivers"] == {"state": "staged", "packages": ["intel-nic"]}
    assert after["state"] == "done" and after["targets"][0]["dev"] == "sda"


def test_the_agent_stages_after_the_name_and_before_the_closing_report():
    main = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    assert '"$LIB_DIR/postdeploy.sh"' in main
    i_name = main.index('name_this_machine "$_disk" "$_ses"')
    i_stage = main.index('stage_drivers "$_disk" "$RUN_DIR/manifest.json"')
    i_report = main.index('report_final "$_ppid" "$_ses"', i_name)
    assert i_name < i_stage < i_report
    single = (AGENT / "lib" / "restore" / "single_restore.sh").read_text(encoding="utf-8")
    assert single.index('stage_drivers "$_sr_disk"') < single.index("pull_close ")


JQ = shutil.which("jq")


@pytest.mark.skipif(JQ is None, reason="jq — הפילטרים האמיתיים רצים במעבדה")
def test_the_real_jq_filters_give_the_rows_the_stage_reads(tmp_path):
    answer = tmp_path / "answer.json"
    answer.write_text(json.dumps(answer_for(FILES)), encoding="utf-8")
    script = (f'. {posix(AGENT)}/lib/core/jsonq.sh; '
              f'json_get {posix(answer)!r} \'.packages[] as $p | $p.files[] | "\\($p.name)|\\(.path)|\\(.sha256)|\\(.url)"\'; '
              f'echo ---; json_get {posix(answer)!r} ".packages[].name"; echo ---; '
              f'json_get {posix(answer)!r} ".inventory"')
    out = subprocess.run([BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
    rows, names, inv = out.split("---\n")
    assert rows.splitlines()[0].startswith("intel-nic|e1d68x64.inf|")
    assert names.split() == ["intel-nic", "ahci-fix"]
    assert inv.strip() == "true"


# --- השרת: הדיווח הסופי נשמר ומוצג ------------------------------------------

pytest.importorskip("fastapi")

from server.agent import reports   # noqa: E402


@pytest.mark.parametrize("bad", [None, "staged", {"state": "installed", "packages": []},
                                 {"state": "staged"}, {"state": "staged", "packages": [1]}])
def test_a_malformed_drivers_field_is_ignored(bad):
    assert reports.well_formed_drivers(bad) is None


def test_the_drivers_outcome_reaches_the_session_view_and_the_journal(server):
    from test_server_api import hello, open_session   # noqa: PLC0415
    ids = open_session(server)
    hello(server, ids["mac1"])
    body = {"session_id": ids["session"], "mac": ids["mac1"], "state": "writing",
            "targets": [{"dev": "sda", "bytes_written": 1, "bytes_total": 2, "state": "writing"}]}
    assert server["anon"].post("/api/v1/agent/progress", json=body).status_code == 200
    final = {**body, "state": "done", "drivers": {"state": "failed", "packages": [],
                                                  "error": "sha256 mismatch: intel-nic/x.sys"}}
    assert server["anon"].post("/api/v1/agent/progress", json=final).status_code == 200
    member = next(m for m in server["admin"].get("/api/console/overview").json()["session"]["members"]
                  if m["mac"] == ids["mac1"])
    assert member["state"] == "done" and member["drivers"]["state"] == "failed"
    assert "sha256 mismatch" in member["drivers"]["error"]
    events = [e["event"] for e in server["admin"].get("/api/console/journal").json()]
    assert "drivers_failed" in events and "client_done" in events


def test_staging_is_a_state_the_server_accepts_and_a_report_without_drivers_keeps_the_last(server):
    from test_server_api import hello, open_session   # noqa: PLC0415
    ids = open_session(server)
    hello(server, ids["mac1"])
    body = {"session_id": ids["session"], "mac": ids["mac1"], "state": "staging",
            "targets": [{"dev": "sda", "bytes_written": 2, "bytes_total": 2, "state": "done"}],
            "drivers": {"state": "no_match", "packages": []}}
    assert server["anon"].post("/api/v1/agent/progress", json=body).json() == {"ok": True}
    assert server["anon"].post("/api/v1/agent/progress",
                               json={**body, "state": "done", "drivers": None}).json() == {"ok": True}
    member = next(m for m in server["admin"].get("/api/console/overview").json()["session"]["members"]
                  if m["mac"] == ids["mac1"])
    assert member["done"] is True and member["drivers"] == {"state": "no_match", "packages": []}
