"""‏#1230 — כפתור העדכון בונה את ה-initrd, מול הקרנל של עכשיו.

שני הצדדים של אותו חוזה, מורצים **בפועל** ב-bash:

* ‏firstboot (שלב א', נשלף מ-`tools/iso/firstboot.sh` — לא עותק) בונה דרך
  ‏`tools/boot-payload-build.sh` ורושם את `initrd.flags` — רק תפקיד ו-`--output`.
* ‏`tools/server-upgrade.sh` קורא את אותו קובץ, ובונה את שני ה-initrd מול
  הקרנל שנגזר **בזמן העדכון** (לא מהקובץ), ומעתיק את ה-vmlinuz שלו.
* קובץ חסר: בשרת ISO — ברירת המחדל של ה-ISO; בלי ראיה — חזרה לתג הקודם.

‏`build_initramfs.sh` מוחלף ב-stub שרושם את הדגלים וכותב ל-`--output` את גרסת
הקרנל שקיבל, כך שהתוכן על הדיסק הוא הראיה לאיזה קרנל נבנה.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from native import requires_native
from test_installer_1131 import (
    BASH,
    REPO,
    _fake_repo,
    _seed_db,
    _status,
    _upgrade_env,
    calls,
    fake_tool,
    posix,
    run_bash,
)

pytestmark = requires_native(("bash", BASH), why="firstboot/server-upgrade/boot-payload-build are bash")

HELPER = REPO / "tools" / "boot-payload-build.sh"
FIRSTBOOT = REPO / "tools" / "iso" / "firstboot.sh"
OLD_KVER = "6.12.9-amd64"
NEW_KVER = "6.12.22-amd64"   # ‏sort -V: ‏22 אחרי 9 — מיון מילוני היה בוחר את 9
ISO_EPOCH = 1700000000
GIT_EPOCH = 1800000000

STUB_BUILD = """#!/usr/bin/env bash
printf 'build_initramfs %s\\n' "$*" >> "$FAKE_LOG"
out=""; kver=""; gui=0
while [ $# -gt 0 ]; do
  case "$1" in
    --output) out="$2"; shift 2 ;;
    --kernel-version) kver="$2"; shift 2 ;;
    --with-gui) gui=1; shift ;;
    *) shift ;;
  esac
done
if [ "$gui" = 1 ] && [ -n "${STUB_FAIL_GUI:-}" ]; then echo "gui build broke" >&2; exit 3; fi
printf 'initrd gui=%s kver=%s\\n' "$gui" "$kver" > "$out"
"""


def _install_kernel(root: Path, kver: str) -> None:
    (root / "modules" / kver).mkdir(parents=True)
    (root / "kernels").mkdir(exist_ok=True)
    (root / "kernels" / f"vmlinuz-{kver}").write_text(f"kernel {kver}\n", encoding="utf-8", newline="\n")


def _host(tmp_path: Path) -> Path:
    """מכונה מדומה: קרנל רגיל, קרנל cloud חדש יותר (אסור לבחור בו, #904), מניפסט ISO."""
    host = tmp_path / "host"
    _install_kernel(host, OLD_KVER)
    _install_kernel(host, "6.12.30-cloud-amd64")
    (host / "etc").mkdir()
    (host / "etc" / "iso-release.json").write_text(
        json.dumps({"source_date_epoch": ISO_EPOCH}), encoding="utf-8", newline="\n")
    (host / "boot").mkdir()
    (host / "var-tmp").mkdir()                   # ‏#1465: ‏/var/tmp של הבנייה
    return host


def _host_env(host: Path) -> dict:
    return {"IMAGESRV_MODULES_DIR": posix(host / "modules"),
            "IMAGESRV_KERNEL_DIR": posix(host / "kernels"),
            "IMAGESRV_BUILD_TMPDIR": posix(host / "var-tmp")}


def _with_payload_builder(tree: Path) -> None:
    """העוזר האמיתי + stub של build_initramfs.sh, בתוך עץ קוד מדומה."""
    (tree / "tools").mkdir(parents=True, exist_ok=True)
    shutil.copy(HELPER, tree / "tools" / "boot-payload-build.sh")
    (tree / "tools" / "build_initramfs.sh").write_text(STUB_BUILD, encoding="utf-8", newline="\n")


# --- firstboot, שלב א' ------------------------------------------------------------

def _firstboot_stage_a() -> str:
    """‏log(), ‏fail() ובלוק שלב א' — מתוך firstboot.sh עצמו."""
    text = FIRSTBOOT.read_text(encoding="utf-8")
    log = re.search(r"^log\(\) \{.*?^\}\n", text, flags=re.MULTILINE | re.DOTALL)
    fail = re.search(r"^fail\(\) \{.*?^\}\n", text, flags=re.MULTILINE | re.DOTALL)
    block = re.search(r'^if \[\[ -f "\$STAGE_A_STAMP" \]\]; then\n.*?^fi\n', text,
                      flags=re.MULTILINE | re.DOTALL)
    assert log and fail and block, "firstboot.sh: log/fail/stage A not found"
    return log.group(0) + fail.group(0) + block.group(0)


def _run_firstboot(tmp_path: Path, host: Path, bindir: Path, env: dict | None = None):
    src = tmp_path / "src"                       # ‏/opt/imagesrv-src של בניית --source: בלי .git
    _with_payload_builder(src)
    script = (
        "set -euo pipefail\n"
        f'SRC="{posix(src)}"; ETC="{posix(host / "etc")}"; HTTP_ROOT="{posix(host / "boot")}"\n'
        f'BUILD_LOG="{posix(tmp_path / "build.log")}"; STAGE_A_STAMP="{posix(tmp_path / "stage-a")}"\n'
        'STATUS="$ETC/firstboot.status"\n'
        # ‏#1240: ההתקדמות נכתבת ל-tty1 של המכונה; בטסט — לקובץ.
        f'FIRSTBOOT_TTY="{posix(tmp_path / "tty1")}"; TTY_PROGRESS=1\n'
        + _firstboot_stage_a()
    )
    return run_bash(script, tmp_path, bindir, env={**_host_env(host), **(env or {})})


def _flag_lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")]


def test_firstboot_records_the_two_builds_without_kernel_or_epoch(tmp_path):
    host = _host(tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    proc = _run_firstboot(tmp_path, host, bindir)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    boot = posix(host / "boot")
    flags = host / "etc" / "initrd.flags"
    assert _flag_lines(flags) == [f"--skip-apt --output {boot}/initrd.img",
                                  f"--skip-apt --with-gui --output {boot}/initrd.img.gui"]
    text = flags.read_text(encoding="utf-8")
    assert "--kernel-version" not in "".join(_flag_lines(flags)) and OLD_KVER not in text
    assert "--source-date-epoch" not in "".join(_flag_lines(flags)) and str(ISO_EPOCH) not in text
    builds = [c for c in calls(tmp_path) if c.startswith("build_initramfs ")]
    assert len(builds) == 2
    assert all(f"--kernel-version {OLD_KVER} --source-date-epoch {ISO_EPOCH}" in b for b in builds)
    assert (host / "boot" / "initrd.img.gui").read_text(encoding="utf-8") == f"initrd gui=1 kver={OLD_KVER}\n"
    assert (host / "boot" / "vmlinuz").read_text(encoding="utf-8") == f"kernel {OLD_KVER}\n"


def test_firstboot_build_failure_records_no_flags_and_names_the_failure(tmp_path):
    host = _host(tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    proc = _run_firstboot(tmp_path, host, bindir, env={"STUB_FAIL_GUI": "1"})
    assert proc.returncode != 0
    assert not (host / "etc" / "initrd.flags").exists(), "flags recorded for a build that failed"
    status = (host / "etc" / "firstboot.status").read_text(encoding="utf-8")
    assert "state=payload-failed" in status and "Build failed" in status
    assert not (tmp_path / "stage-a").exists()
    # ‏#1240: הכישלון מוצג על tty1, לא רק ביומן.
    tty = (tmp_path / "tty1").read_text(encoding="utf-8")
    assert "first boot failed [payload-failed]" in tty and "Alt+F2" in tty


# --- server-upgrade.sh -----------------------------------------------------------

def _upgrade_tools(bindir: Path) -> None:
    fake_tool(bindir, "git", f'''\
if [ "$1" = -C ]; then shift 2; fi
case "$1" in
  fetch|checkout) exit 0 ;;
  describe) echo "$FAKE_TAG" ;;
  rev-parse) echo "commit-of-$FAKE_TAG" ;;
  rev-list) echo "commit-of-$4" ;;
  log) echo {GIT_EPOCH} ;;
  *) echo "unexpected git $*" >&2; exit 64 ;;
esac''')
    fake_tool(bindir, "systemctl",
              'case "$1" in show) echo "IMAGESRV_URL=http://10.10.10.8:8080 X=y" ;; esac; exit 0')
    fake_tool(bindir, "curl", "exit 0")


def _upgrade(tmp_path: Path, host: Path, bindir: Path, *, flags: Path | None,
             iso_manifest: bool = False, extra: dict | None = None, tag: str = "v0.60.0"):
    data = tmp_path / "data"
    _seed_db(data, previous="v0.59.0")
    repo = _fake_repo(tmp_path, live_ok=True)
    _with_payload_builder(repo)
    (repo / ".git").mkdir()                      # ‏/opt/imagesrv אחרי #1185: עץ git
    _upgrade_tools(bindir)
    (tmp_path / "units").mkdir(exist_ok=True)
    env = {**_upgrade_env(tmp_path, repo, data), **_host_env(host), "FAKE_TAG": tag,
           "IMAGESRV_HTTP_ROOT": posix(host / "boot"),
           "IMAGESRV_INITRD_FLAGS": posix(flags or tmp_path / "missing.flags"),
           "IMAGESRV_ISO_MANIFEST": posix(host / "etc" / "iso-release.json") if iso_manifest
           else posix(tmp_path / "not-an-iso-server.json"),
           **(extra or {})}
    proc = run_bash(f'bash "{posix(repo / "tools" / "server-upgrade.sh")}" {tag} "{posix(repo)}"',
                    tmp_path, bindir, env=env)
    log = (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    return proc, log, _status(data)


def _installed_by_firstboot(tmp_path: Path) -> tuple[Path, Path]:
    """שרת שהותקן מה-ISO: firstboot רץ, בנה מול OLD_KVER ורשם דגלים."""
    host = _host(tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    assert _run_firstboot(tmp_path, host, bindir).returncode == 0
    (tmp_path / "calls.log").write_text("", encoding="utf-8")
    return host, bindir


def test_upgrade_rebuilds_both_initrds_against_the_kernel_installed_now(tmp_path):
    host, bindir = _installed_by_firstboot(tmp_path)
    _install_kernel(host, NEW_KVER)              # apt שדרג את הקרנל אחרי ההתקנה

    proc, log, status = _upgrade(tmp_path, host, bindir, flags=host / "etc" / "initrd.flags")
    assert proc.returncode == 0, log
    assert "done and verified" in log and (status or {}).get("state") != "failed"

    seen = calls(tmp_path)
    builds = [c for c in seen if c.startswith("build_initramfs ")]
    assert len(builds) == 2 and sum("--with-gui" in b for b in builds) == 1
    assert all(f"--kernel-version {NEW_KVER} --source-date-epoch {GIT_EPOCH}" in b for b in builds)
    boot = host / "boot"
    assert (boot / "initrd.img").read_text(encoding="utf-8") == f"initrd gui=0 kver={NEW_KVER}\n"
    assert (boot / "initrd.img.gui").read_text(encoding="utf-8") == f"initrd gui=1 kver={NEW_KVER}\n"
    assert (boot / "vmlinuz").read_text(encoding="utf-8") == f"kernel {NEW_KVER}\n"
    assert not list(boot.glob("*.new"))
    # האימות רץ אחרי הבנייה, על אותה תיקייה.
    verify = next(i for i, c in enumerate(seen) if c.startswith("verify-boot-payload "))
    assert f"--http-root {posix(boot)}" in seen[verify]
    assert verify > max(i for i, c in enumerate(seen) if c.startswith("build_initramfs "))


def test_upgrade_without_flags_on_a_non_iso_server_builds_the_documented_default_and_records_it(tmp_path):
    """‏#1546: שרת שהותקן ב-setup-boot-server.sh (שרת חיפה, 17/10) — אין
    ‏initrd.flags ואין iso-release.json. עד #1546 כל עדכון שלו חזר אחורה.
    ברירת המחדל מתועדת (docs/server-install.md, ‏verify-boot-payload.sh):
    ‏initrd.img + initrd.img.gui ב-HTTP_ROOT — אותן שורות של ה-ISO."""
    host = _host(tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (host / "boot" / "initrd.img").write_text("old initrd\n", encoding="utf-8")
    flags = tmp_path / "etc-imagesrv" / "initrd.flags"

    proc, log, status = _upgrade(tmp_path, host, bindir, flags=flags)
    assert proc.returncode == 0, log
    assert (status or {}).get("state") != "failed"
    assert "ברירת המחדל המתועדת" in log
    seen = calls(tmp_path)
    assert not [c for c in seen if c.startswith("git checkout")]
    builds = [c for c in seen if c.startswith("build_initramfs ")]
    assert len(builds) == 2 and sum("--with-gui" in b for b in builds) == 1
    boot = posix(host / "boot")
    assert _flag_lines(flags) == [f"--skip-apt --output {boot}/initrd.img",
                                  f"--skip-apt --with-gui --output {boot}/initrd.img.gui"]
    assert (host / "boot" / "initrd.img").read_text(encoding="utf-8") == f"initrd gui=0 kver={OLD_KVER}\n"


def test_upgrade_without_flags_on_an_iso_server_builds_the_iso_default_and_records_it(tmp_path):
    host = _host(tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    flags = tmp_path / "etc-imagesrv" / "initrd.flags"   # firstboot מלפני #1230 לא כתב אותו

    proc, log, status = _upgrade(tmp_path, host, bindir, flags=flags, iso_manifest=True)
    assert proc.returncode == 0, log
    builds = [c for c in calls(tmp_path) if c.startswith("build_initramfs ")]
    assert len(builds) == 2 and all("--skip-apt" in b for b in builds)
    assert all(f"--kernel-version {OLD_KVER}" in b for b in builds)
    boot = posix(host / "boot")
    assert _flag_lines(flags) == [f"--skip-apt --output {boot}/initrd.img",
                                  f"--skip-apt --with-gui --output {boot}/initrd.img.gui"]


def test_upgrade_build_failure_rolls_back_and_leaves_the_old_payload_whole(tmp_path):
    host, bindir = _installed_by_firstboot(tmp_path)
    _install_kernel(host, NEW_KVER)

    proc, log, status = _upgrade(tmp_path, host, bindir, flags=host / "etc" / "initrd.flags",
                                 extra={"STUB_FAIL_GUI": "1"})
    assert proc.returncode != 0
    assert status and status["state"] == "failed" and "initrd" in status["error"]
    assert "git checkout --detach v0.59.0" in calls(tmp_path)
    assert not [c for c in calls(tmp_path) if c.startswith("verify-boot-payload ")]
    boot = host / "boot"
    # הטקסטואלי נבנה בהצלחה לפני שהגרפי נפל — ובכל זאת לא הוחלף: מטען אחד, קרנל אחד.
    assert (boot / "initrd.img").read_text(encoding="utf-8") == f"initrd gui=0 kver={OLD_KVER}\n"
    assert (boot / "vmlinuz").read_text(encoding="utf-8") == f"kernel {OLD_KVER}\n"


def test_a_flags_file_that_pins_the_kernel_is_refused(tmp_path):
    host = _host(tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    flags = tmp_path / "pinned.flags"
    flags.write_text(f"--kernel-version {OLD_KVER} --output {posix(host / 'boot')}/initrd.img\n",
                     encoding="utf-8", newline="\n")
    _install_kernel(host, NEW_KVER)

    proc, log, status = _upgrade(tmp_path, host, bindir, flags=flags)
    assert proc.returncode != 0
    assert "--kernel-version" in log and "derived at build time" in log
    assert not [c for c in calls(tmp_path) if c.startswith("build_initramfs ")]
    assert status and status["state"] == "failed"
