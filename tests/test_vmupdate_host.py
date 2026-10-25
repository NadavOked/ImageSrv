"""‏#1334 — המארח מוכן לעדכון ב-VM גם כשהשרת מתעדכן מהכפתור ולא מה-ISO.

‏`install/vmupdate-host.sh` מתקין את install/vmupdate-packages.txt (המקור היחיד)
ויוצר את imagectl-vm, באופן אידמפוטנטי, ומצליח רק על **ראיה חיובית** (dpkg-query
לכל חבילה, ‏id למשתמש). ‏`tools/server-upgrade.sh` מריץ אותו אחרי ה-checkout:
כשל **אינו** מפיל את העדכון וגם **אינו** נבלע — הסיבה בסטטוס (`warning`), והעדכון
מגיע ל-"אומת — אבל עדכון ב-VM לא זמין".
"""

from __future__ import annotations

import gzip
import sys
from pathlib import Path

import pytest

from native import requires_native
from test_installer_1131 import (
    BASH, _fake_repo, _seed_db, _status, _upgrade_env, calls, fake_tool, posix, run_bash,
)
from test_server_upgrade_1193 import _success_tools

pytestmark = requires_native(("bash", BASH), why="the host/upgrade scripts are bash")

REPO = Path(__file__).resolve().parent.parent
HOST = REPO / "install" / "vmupdate-host.sh"
LIST = REPO / "install" / "vmupdate-packages.txt"


@pytest.fixture()
def bindir(tmp_path: Path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    return path


def packages() -> list[str]:
    return [w for line in LIST.read_text(encoding="utf-8").splitlines()
            for w in line.split("#", 1)[0].split()]


def tools(bindir: Path, *, apt="exit 0", installed=None, user_exists=False, useradd="true"):
    """‏apt/dpkg-query/id/useradd מדומים. ‏`installed` — מה dpkg-query מכיר (ברירת
    מחדל: הכול). המשתמש "נוצר" ב-useradd: id מצליח אחריו."""
    done = posix(bindir / "apt.done")
    if apt == "exit 0":   # ‏apt מצליח: אחרי install מה שביקשנו מותקן (כמו apt אמיתי)
        apt = f'[ "$1" = install ] && touch "{done}"; exit 0'
    fake_tool(bindir, "fakeapt", apt)
    known = packages() if installed is None else installed
    fake_tool(bindir, "fakedpkg",
              f'[ -e "{done}" ] && {{ printf "install ok installed"; exit 0; }}\n'
              'pkg="${@: -1}"; case " ' + " ".join(known) + ' " in *" $pkg "*) printf "install ok installed" ;; *) exit 1 ;; esac')
    marker = bindir / "user.made"
    if user_exists:
        marker.write_text("1")
    fake_tool(bindir, "fakeid", f'[ -e "{posix(marker)}" ]')
    fake_tool(bindir, "fakeuseradd", f'{useradd} && touch "{posix(marker)}"')


def host_env(bindir: Path) -> dict:
    """‏/etc/apt של הטסט — תיקייה ריקה (שרת שאינו מה-ISO), לעולם לא של המכונה."""
    apt_etc = bindir.parent / "etc-apt"
    (apt_etc / "sources.list.d").mkdir(parents=True, exist_ok=True)
    return {"IMAGECTL_APT": "fakeapt", "IMAGECTL_DPKG_QUERY": "fakedpkg",
            "IMAGECTL_ID": "fakeid", "IMAGECTL_USERADD": "fakeuseradd",
            "IMAGECTL_APT_ETC": posix(apt_etc), "IMAGECTL_PYTHON": posix(Path(sys.executable)),
            "IMAGECTL_APT_REPO": posix(bindir.parent / "apt-repo")}


def run_host(tmp_path, bindir):
    return run_bash(f'bash "{posix(HOST)}"', tmp_path, bindir, env=host_env(bindir))


def test_the_list_installs_everything_the_engine_checks_for():
    names = packages()
    for pkg in ("qemu-system-x86", "ovmf", "python3-guestfs", "nftables", "gdisk", "partclone"):
        assert pkg in names


def test_installs_the_list_and_creates_the_user(tmp_path, bindir):
    tools(bindir, installed=[])
    proc = run_host(tmp_path, bindir)
    log = calls(tmp_path)
    assert proc.returncode == 0, proc.stdout
    apt = [row for row in log if row.startswith("fakeapt ")]
    # ‏#1459: רשימות עדכניות, ואז התקנה של מה ש-dpkg-query אומר שחסר (כאן: הכול)
    assert apt[0].split()[1:3] == ["update", "-qq"] and not any("https" in a for a in apt)
    # ‏#1987: בלי Recommends — רק הרשימה ומה שהיא חייבת.
    assert apt[1].split()[1:5] == ["install", "-y", "-qq", "--no-install-recommends"] and apt[1].split()[5:] == packages()
    assert any(row.startswith("fakeuseradd --system") and row.endswith("imagectl-vm") for row in log)


def test_is_idempotent_with_an_existing_user(tmp_path, bindir):
    tools(bindir, user_exists=True)
    assert run_host(tmp_path, bindir).returncode == 0
    assert not any(row.startswith("fakeuseradd") for row in calls(tmp_path))


def test_apt_failure_is_named_and_creates_nothing(tmp_path, bindir):
    tools(bindir, installed=[], apt='echo "E: Unable to locate package ovmf" >&2; exit 100')
    proc = run_host(tmp_path, bindir)
    assert proc.returncode == 3
    assert "apt-get install נכשל" in proc.stdout and "Unable to locate package ovmf" in proc.stdout
    assert not any(row.startswith("fakeuseradd") for row in calls(tmp_path))


def test_apt_exit_0_is_not_evidence(tmp_path, bindir):
    """‏apt יצא 0 אבל ovmf אינו מותקן — "יצא 0" אינו "מותקן" (עיקרון 5)."""
    tools(bindir, installed=[p for p in packages() if p != "ovmf"], apt="exit 0  # no marker")
    proc = run_host(tmp_path, bindir)


def test_a_user_that_was_not_created_fails(tmp_path, bindir):
    tools(bindir, useradd="false")
    proc = run_host(tmp_path, bindir)
    assert proc.returncode == 5 and "imagectl-vm" in proc.stdout.splitlines()[-1]


# --- ‏#1371/#1459: שרת מה-ISO — המאגר המקומי עקבי, ואין מקור apt חי ---------


def iso_server(tmp_path: Path, bindir: Path) -> tuple[dict, Path, Path]:
    """שרת מה-ISO כמו שרת ת"א (‏13/10): imagectl-iso.list, ומאגר מקומי שהאינדקס
    שלו מונה חבילה (‏openssh-server) שהקובץ שלה לא הועתק."""
    env = host_env(bindir)
    apt_etc = Path(env["IMAGECTL_APT_ETC"])
    (apt_etc / "sources.list.d" / "imagectl-iso.list").write_text(
        "deb [trusted=yes] file:/var/lib/imagectl/apt-repo trixie main contrib\n", encoding="utf-8")
    repo = Path(env["IMAGECTL_APT_REPO"])
    (repo / "pool" / "main" / "imagectl").mkdir(parents=True)
    (repo / "pool" / "main" / "imagectl" / "qemu_1_amd64.deb").write_bytes(b"deb")
    index = repo / "dists" / "trixie" / "main" / "binary-amd64" / "Packages.gz"
    index.parent.mkdir(parents=True)
    index.write_bytes(gzip.compress(
        b"Package: qemu\nFilename: pool/main/imagectl/qemu_1_amd64.deb\n\n"
        b"Package: openssh-server\nFilename: pool/main/o/openssh/openssh-server_1_amd64.deb\n"))
    (repo / "dists" / "trixie" / "Release").write_text("Suite: stable\nSHA256:\n", encoding="utf-8")
    return env, apt_etc, index


def test_an_iso_server_gets_no_live_apt_source(tmp_path, bindir):
    """‏#1459: שרת מה-ISO לא מקבל deb.debian.org — לא קובץ מקור ולא pin."""
    tools(bindir, installed=[])
    env, apt_etc, _index = iso_server(tmp_path, bindir)
    proc = run_bash(f'bash "{posix(HOST)}"', tmp_path, bindir, env=env)
    assert proc.returncode == 0, proc.stdout
    assert not (apt_etc / "sources.list.d" / "imagectl-debian.list").exists()
    assert not (apt_etc / "preferences.d" / "imagectl-debian.pref").exists()
    written = "".join(p.read_text(encoding="utf-8") for p in apt_etc.rglob("*") if p.is_file())
    assert "debian.org" not in written and "http" not in written
    log = " ".join(calls(tmp_path))
    assert "debian.org" not in log and "Acquire::https" not in log


def test_an_iso_server_that_got_the_live_source_before_has_it_removed(tmp_path, bindir):
    """שרת שעודכן בגרסה שהוסיפה את המקור (#1371) — העדכון הבא מנקה אותו."""
    tools(bindir)
    env, apt_etc, _index = iso_server(tmp_path, bindir)
    (apt_etc / "preferences.d").mkdir()
    (apt_etc / "sources.list.d" / "imagectl-debian.list").write_text(
        "deb https://deb.debian.org/debian trixie main contrib\n", encoding="utf-8")
    (apt_etc / "preferences.d" / "imagectl-debian.pref").write_text("Pin: origin deb.debian.org\n", encoding="utf-8")
    assert run_bash(f'bash "{posix(HOST)}"', tmp_path, bindir, env=env).returncode == 0
    assert not (apt_etc / "sources.list.d" / "imagectl-debian.list").exists()
    assert not (apt_etc / "preferences.d" / "imagectl-debian.pref").exists()


def test_everything_installed_never_touches_apt(tmp_path, bindir):
    """‏#1459: dpkg-query קודם — כשהכול מותקן אין update ואין install (גם לא רשת)."""
    tools(bindir)
    proc = run_host(tmp_path, bindir)
    assert proc.returncode == 0, proc.stdout
    assert not any(row.startswith("fakeapt ") for row in calls(tmp_path))


def test_only_the_missing_packages_are_installed(tmp_path, bindir):
    tools(bindir, installed=[p for p in packages() if p not in ("ovmf", "jq")])
    proc = run_host(tmp_path, bindir)
    assert proc.returncode == 0, proc.stdout
    install = [row for row in calls(tmp_path) if row.startswith("fakeapt install")]
    assert len(install) == 1 and install[0].split()[4:] == ["--no-install-recommends", "ovmf", "jq"]


def test_an_iso_server_local_repo_stops_publishing_files_it_does_not_have(tmp_path, bindir):
    tools(bindir)
    env, _apt_etc, index = iso_server(tmp_path, bindir)
    assert run_bash(f'bash "{posix(HOST)}"', tmp_path, bindir, env=env).returncode == 0
    listed = gzip.decompress(index.read_bytes()).decode("utf-8")
    assert "Package: qemu\n" in listed and "openssh-server" not in listed


def test_a_server_not_from_the_iso_keeps_its_own_sources(tmp_path, bindir):
    tools(bindir)
    env = host_env(bindir)
    assert run_bash(f'bash "{posix(HOST)}"', tmp_path, bindir, env=env).returncode == 0
    apt_etc = Path(env["IMAGECTL_APT_ETC"])
    assert not (apt_etc / "sources.list.d" / "imagectl-debian.list").exists()
    assert not (apt_etc / "preferences.d").exists()


def test_a_package_the_release_does_not_carry_is_a_named_failure_not_a_fetch(tmp_path, bindir):
    """חבילה שאין במאגר המקומי: כשל עם שמה (#1459) — ושום ניסיון להגיע לרשת."""
    tools(bindir, installed=[p for p in packages() if p != "qemu-system-x86"],
          apt='echo "E: Unable to locate package qemu-system-x86" >&2; exit 100')
    env, _apt_etc, _index = iso_server(tmp_path, bindir)
    proc = run_bash(f'bash "{posix(HOST)}"', tmp_path, bindir, env=env)
    assert proc.returncode == 3
    last = proc.stdout.splitlines()[-1]
    assert "Unable to locate package qemu-system-x86" in last and "deb.debian.org" not in proc.stdout


def test_a_local_repo_that_cannot_be_checked_fails_by_name(tmp_path, bindir):
    tools(bindir)
    env = host_env(bindir)
    (Path(env["IMAGECTL_APT_ETC"]) / "sources.list.d" / "imagectl-iso.list").write_text("x\n", encoding="utf-8")
    proc = run_bash(f'bash "{posix(HOST)}"', tmp_path, bindir, env=env)   # אין apt-repo בכלל
    assert proc.returncode == 6 and "לא נבדק" in proc.stdout.splitlines()[-1]
    assert not any(row.startswith("fakeapt ") for row in calls(tmp_path))


# --- העדכון מהכפתור -------------------------------------------------------------------


def upgrade_repo(tmp_path: Path, *, host_rc: int | None) -> Path:
    repo = _fake_repo(tmp_path, live_ok=True)
    if host_rc is not None:
        (repo / "install" / "vmupdate-host.sh").write_text(
            '#!/usr/bin/env bash\nprintf "vmupdate-host\\n" >> "$FAKE_LOG"\n'
            + ('echo "עדכון ב-VM: 9 חבילות מותקנות"\nexit 0\n' if host_rc == 0
               else f'echo "apt-get install נכשל: E: Unable to fetch"\nexit {host_rc}\n'),
            encoding="utf-8", newline="\n")
    return repo


def run_upgrade(tmp_path, bindir, repo, data, tag="v0.73.0"):
    (tmp_path / "units").mkdir(exist_ok=True)
    _success_tools(bindir)   # git/systemctl/curl כמו בטסטים של #1193/#1301 — HEAD על התג
    return run_bash(f'bash "{posix(repo / "tools" / "server-upgrade.sh")}" {tag} "{posix(repo)}"',
                    tmp_path, bindir, env={**_upgrade_env(tmp_path, repo, data), "FAKE_TAG": tag})


def test_the_upgrade_prepares_the_host_before_the_restart(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data)
    repo = upgrade_repo(tmp_path, host_rc=0)
    proc = run_upgrade(tmp_path, bindir, repo, data)
    log = calls(tmp_path)
    assert proc.returncode == 0, (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    assert log.index("vmupdate-host") < log.index("systemctl restart imagectl-server")
    status = _status(data) or {}
    assert status.get("stage") == "done" and "warning" not in status


def test_a_failed_host_preparation_does_not_fail_the_upgrade_and_is_reported(tmp_path, bindir):
    """הלב של הדרישה: השרת מתעדכן (restart + ראיה), והסטטוס אומר שהפיצ'ר לא זמין."""
    data = tmp_path / "data"
    _seed_db(data)
    repo = upgrade_repo(tmp_path, host_rc=3)
    proc = run_upgrade(tmp_path, bindir, repo, data)
    log = calls(tmp_path)
    assert proc.returncode == 0, (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    assert "systemctl restart imagectl-server" in log
    assert not any(" checkout --detach v0.48.0" in row for row in log)   # לא חזר לתג הקודם
    status = _status(data)
    # ‏#1301: העדכון הסתיים ואומת (state/stage done) — והאזהרה נשמרת עד הסוף.
    assert status["state"] == "done" and status["stage"] == "done" and status["tag"] == "v0.73.0"
    assert status["warning"].startswith("עדכון ב-VM לא זמין: apt-get install נכשל")
    assert "קוד 3" in status["warning"]


def test_a_vm_warning_keeps_an_earlier_upgrade_warning(tmp_path, bindir):
    """#1755: a later VM warning must append, not replace the package warning."""
    data = tmp_path / "data"
    _seed_db(data)
    repo = upgrade_repo(tmp_path, host_rc=3)
    (repo / "install" / "security-upgrade.sh").write_text(
        '#!/usr/bin/env bash\necho "אזהרה: kernel-warning"\nexit 0\n',
        encoding="utf-8", newline="\n")
    proc = run_upgrade(tmp_path, bindir, repo, data)
    status = _status(data)
    assert proc.returncode == 0, (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    assert "kernel-warning" in status["warning"]
    assert "עדכון ב-VM לא זמין" in status["warning"]


def test_a_tag_without_the_host_script_is_a_version_without_the_feature(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data)
    repo = upgrade_repo(tmp_path, host_rc=None)
    proc = run_upgrade(tmp_path, bindir, repo, data)
    assert proc.returncode == 0
    assert "warning" not in (_status(data) or {})
    assert "גרסה בלי עדכון ב-VM" in (tmp_path / "upgrade.log").read_text(encoding="utf-8")


def test_the_console_says_verified_but_when_there_is_a_warning():
    js = (REPO / "server" / "static" / "console.js").read_text(encoding="utf-8")
    body = js[js.index("function updateStatusHtml"):js.index("function fmtWhenShort")]
    warn = body.index("status.verified && status.warning)")
    assert 'return UI.note("warn"' in body[warn:warn + 120]
    assert warn < body.index('status.verified && status.stage === "done")')   # לפני ה"עודכן" הירוק
    assert "esc(status.warning)" in body
