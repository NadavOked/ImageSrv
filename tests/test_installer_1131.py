"""‏#1131 / ‏#1121 — פונקציות המתקין וסקריפט השדרוג, מורצות **בפועל** ב-bash.

הפונקציות נשלפות מ-`install/setup-boot-server.sh` (לא עותק — סטייה בין
הסקריפט לבדיקה היא בדיוק החור, כמו test_installer_admin) ורצות עם כלים
מדומים על PATH (‏`ip`, ‏`nft`, ‏`systemctl`…) שרושמים מה נקרא. הבדיקה היא
קוד יציאה, קבצים שנכתבו, ומה **לא** נקרא.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import stat
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "install" / "setup-boot-server.sh"
UPGRADE = REPO / "tools" / "server-upgrade.sh"
LIVE = REPO / "install" / "console-live.sh"


def find_bash() -> str | None:
    if os.name == "nt":
        for candidate in (r"C:\Program Files\Git\usr\bin\bash.exe",
                          r"C:\Program Files\Git\bin\bash.exe"):
            if Path(candidate).exists():
                return candidate
    return shutil.which("bash")


BASH = find_bash()
pytestmark = requires_native(("bash", BASH), why="פונקציות המתקין הן bash")


def installer_function(name: str) -> str:
    """גוף הפונקציה `name() { … }` מתוך המתקין — רב-שורתית או בשורה אחת."""
    text = INSTALLER.read_text(encoding="utf-8")
    match = re.search(
        rf"^{re.escape(name)}\(\) *\{{[^\n]*\}}\n|^{re.escape(name)}\(\) *\{{.*?^\}}\n",
        text, flags=re.DOTALL | re.MULTILINE)
    assert match, f"לא נמצאה הפונקציה {name} במתקין"
    return match.group(0)


HELPERS = "GRN=''; YEL=''; DIM=''; OFF=''; RED=''\n" + "".join(
    installer_function(n) for n in ("say", "warn", "die", "run"))


def fake_tool(bindir: Path, name: str, body: str) -> None:
    """כלי מדומה על PATH — רושם את הארגומנטים ל-$FAKE_LOG ומריץ את `body`."""
    path = bindir / name
    path.write_text("#!/usr/bin/env bash\n"
                    'printf "%s %s\\n" "$(basename "$0")" "$*" >> "$FAKE_LOG"\n'
                    + body + "\n", encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def run_bash(script: str, tmp_path: Path, bindir: Path,
             env: dict | None = None) -> subprocess.CompletedProcess:
    log = tmp_path / "calls.log"
    log.touch()
    full_env = {**os.environ, "FAKE_LOG": str(log), "PYTHONIOENCODING": "utf-8",
                "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
                **(env or {})}
    return subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=full_env,
                          cwd=str(tmp_path), timeout=120, stdin=subprocess.DEVNULL)


def calls(tmp_path: Path) -> list[str]:
    return (tmp_path / "calls.log").read_text(encoding="utf-8").splitlines()


@pytest.fixture()
def bindir(tmp_path: Path) -> Path:
    d = tmp_path / "bin"
    d.mkdir()
    return d


def posix(path: Path) -> str:
    return path.resolve().as_posix()


# --- iface_ip (‏#1121 ס' 5) ----------------------------------------------------------

IP_TWO_ADDRS = (
    "2: eth0    inet 10.1.2.3/24 brd 10.1.2.255 scope global deprecated dynamic eth0"
    "\\       valid_lft 0sec preferred_lft 0sec\n"
    "2: eth0    inet 10.1.2.9/24 brd 10.1.2.255 scope global dynamic eth0"
    "\\       valid_lft 600sec preferred_lft 600sec\n"
)


def test_iface_ip_skips_the_deprecated_address_during_renewal(tmp_path, bindir):
    fake_tool(bindir, "ip", f"printf '%s' '{IP_TWO_ADDRS}'")
    proc = run_bash(installer_function("iface_ip") + 'iface_ip eth0', tmp_path, bindir)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "10.1.2.9"
    assert any(c.startswith("ip ") and "scope global" in c for c in calls(tmp_path))


def test_iface_ip_failure_of_ip_is_not_an_empty_address(tmp_path, bindir):
    """‏`ip` שנפל: שגיאה ב-stderr ו-`set -o pipefail` מפיל את ההשמה — לא
    "אין כתובת" בשקט (עיקרון 5)."""
    fake_tool(bindir, "ip", 'echo "Device \\"eth0\\" does not exist." >&2; exit 1')
    proc = run_bash("set -euo pipefail\n" + installer_function("iface_ip")
                    + 'ADDR="$(iface_ip eth0)"; echo "got=[$ADDR]"', tmp_path, bindir)
    assert proc.returncode != 0
    assert "does not exist" in proc.stderr and "got=" not in proc.stdout


# --- write_file (‏#1131 ס' 2 / ס' 9) --------------------------------------------------

WRITE_FILE = HELPERS + "DRY_RUN=0\nLAST_BACKUP=''\n" + installer_function("write_file")


def test_write_file_backs_up_our_own_file_too_and_writes_atomically(tmp_path, bindir):
    target = tmp_path / "etc" / "thing.conf"
    target.parent.mkdir()
    target.write_text("# ImageCtl old\n", encoding="utf-8")
    proc = run_bash(WRITE_FILE + f'write_file "{posix(target)}" 0600 <<\'EOF\'\nnew content\nEOF\n'
                    'echo "backup=$LAST_BACKUP"', tmp_path, bindir)
    assert proc.returncode == 0, proc.stderr
    assert target.read_text(encoding="utf-8") == "new content\n"
    backups = list(target.parent.glob("thing.conf.pre-imagectl.*"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == "# ImageCtl old\n"
    assert f"backup={posix(backups[0])}" in proc.stdout.replace("\\", "/")
    assert not list(target.parent.glob("thing.conf.??????")), "tmp של mktemp נשאר"
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_write_file_first_write_has_no_backup_and_default_mode(tmp_path, bindir):
    target = tmp_path / "etc" / "new.conf"
    proc = run_bash(WRITE_FILE + f'write_file "{posix(target)}" <<\'EOF\'\nx\nEOF\n'
                    'echo "backup=[$LAST_BACKUP]"', tmp_path, bindir)
    assert proc.returncode == 0, proc.stderr
    assert target.read_text(encoding="utf-8") == "x\n" and "backup=[]" in proc.stdout
    assert not list(target.parent.glob("new.conf.pre-imagectl.*"))
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o644


# --- שמות כרטיסים (‏#1131 ס' 8 / ס' 13) ------------------------------------------------

IFNAME = HELPERS + installer_function("require_ifname")


@pytest.mark.parametrize("name,ok,why", [
    ("eth0", True, ""),
    ("eth1.700", True, ""),
    ("eth0; echo x", False, "לא חוקי"),
    ("eth0\ninterface=eth1", False, "לא חוקי"),
    ("eth9", False, "לא קיים ממשק"),
])
def test_require_ifname_accepts_only_safe_existing_names(tmp_path, bindir, name, ok, why):
    sysnet = tmp_path / "sys-net"
    (sysnet / "eth0").mkdir(parents=True)
    (sysnet / "eth1.700").mkdir()
    # השם עובר ב-env ולא ב-argv: argv של ווינדוס קוטע בשורה חדשה, והבדיקה
    # הייתה רואה "eth0" ומאשרת (נמדד).
    script = f'SYS_NET="{posix(sysnet)}"\n' + IFNAME + 'require_ifname "$NAME" "כרטיס"; echo accepted'
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8", "NAME": name})
    assert (proc.returncode == 0) is ok, proc.stderr
    assert ("accepted" in proc.stdout) is ok
    if not ok:
        assert why in proc.stderr


def test_installer_validates_every_name_that_reaches_a_command_line():
    text = INSTALLER.read_text(encoding="utf-8")
    assert 'require_ifname "$SERVERS_IF"' in text
    assert 'require_ifname "$IFACE"' in text
    assert 'require_ifname "$CONSOLE_DEPLOY_IF"' in text
    assert '"$CONSOLE_HOST" =~ ^[A-Za-z0-9._:-]+$' in text
    assert '"$PRIMARY_URL" =~ ^https?://[A-Za-z0-9._:/-]+$' in text
    assert 'ip link show "$SERVERS_IF"' not in text


# --- כבר מותקן? (‏#1131 ס' 3 / ס' 10) + רשת ההפצה מה-DB (‏#1121 ס' 7) --------------

REFUSE = HELPERS + installer_function("refuse_if_installed")


def test_second_run_stops_in_name_unless_forced(tmp_path, bindir):
    data = tmp_path / "data"
    data.mkdir()
    (data / "imagectl.db").write_bytes(b"")
    base = f'DATA_DIR="{posix(data)}"\nUNIT_FILE="{posix(tmp_path / "no-unit")}"\nDRY_RUN=0\n'
    proc = run_bash(base + "FORCE=0\n" + REFUSE + "refuse_if_installed; echo continued",
                    tmp_path, bindir)
    assert proc.returncode != 0 and "continued" not in proc.stdout
    assert "כבר מותקן" in proc.stderr and "--force" in proc.stderr and "server-upgrade.sh" in proc.stderr
    proc = run_bash(base + "FORCE=1\n" + REFUSE + "refuse_if_installed; echo continued",
                    tmp_path, bindir)
    assert proc.returncode == 0 and "continued" in proc.stdout
    # מכונה נקייה — ממשיכים בלי --force.
    clean = f'DATA_DIR="{posix(tmp_path / "nowhere")}"\nUNIT_FILE="{posix(tmp_path / "no-unit")}"\nDRY_RUN=0\nFORCE=0\n'
    proc = run_bash(clean + REFUSE + "refuse_if_installed; echo continued", tmp_path, bindir)
    assert proc.returncode == 0 and "continued" in proc.stdout


def test_installer_reads_the_console_deploy_nic_from_the_db(tmp_path, bindir):
    """‏#1121 ס' 7: ‏deploy:interface+deploy:url ב-DB → הכרטיס; חצי-רשומה → ריק."""
    from server import deploy_net
    from server.db import connect
    data = tmp_path / "data"
    data.mkdir()
    conn = connect(data / "imagectl.db")
    script = f'DATA_DIR="{posix(data)}"\n' + installer_function("console_deploy_if_from_db") \
        + 'printf "[%s]" "$(console_deploy_if_from_db)"'
    proc = run_bash(script, tmp_path, bindir)
    assert proc.returncode == 0 and proc.stdout == "[]", proc.stderr
    deploy_net.record(conn, "eth1", "http://10.44.9.10:8080")
    proc = run_bash(script, tmp_path, bindir)
    assert proc.returncode == 0 and proc.stdout == "[eth1]", proc.stderr


def test_installer_keeps_dnsmasq_and_the_deploy_rules_when_the_console_configured_them():
    text = INSTALLER.read_text(encoding="utf-8")
    # dnsmasq: שלושה מסלולים — כרטיס מהדגל/שאלה, כרטיס מהקונסולה (לא נוגעים), אף אחד (disable).
    assert 'elif [[ -n "$CONSOLE_DEPLOY_IF" ]]; then' in text
    assert text.index('elif [[ -n "$CONSOLE_DEPLOY_IF" ]]') < text.index('run systemctl disable --now dnsmasq')
    assert 'DEPLOY_FOR_NFT="${IFACE:-$CONSOLE_DEPLOY_IF}"' in text
    assert 'NFT_ARGS+=(--deploy-if "$DEPLOY_FOR_NFT")' in text
    assert '-z "$IFACE" && -z "$CONSOLE_DEPLOY_IF"' in text        # לא שואלים "לא עכשיו"


# --- חומת האש אחרונה, עם החזרה (‏#1131 ס' 1 / ס' 8) -------------------------------------

def test_firewall_is_the_last_step_after_the_server_answers():
    text = INSTALLER.read_text(encoding="utf-8")
    server_up = text.index("run systemctl enable --now imagectl-server")
    live = text.index('console-live.sh" "https://127.0.0.1:8081"')
    firewall = text.index("\ninstall_firewall\n")
    ready = text.index("\nREADY=1\n")
    assert server_up < live < firewall < ready
    assert "|| true" not in text.split("journalctl -u dnsmasq")[1].split("\n")[0]
    assert "|| true" not in text.split("journalctl -u nftables")[1].split("\n")[0]


FIREWALL_PRELUDE = (
    "set -euo pipefail\n" + HELPERS + "DRY_RUN=0\nNO_FIREWALL=0\nLAST_BACKUP=''\n"
    "IFACE=eth1\nCONSOLE_DEPLOY_IF=''\nSERVERS_IF=eth0\nSTORAGE_ROLE=standalone\nPRIMARY_URL=''\n"
    f"APP_DIR='{posix(REPO)}'\nSCRIPT_DIR='{posix(REPO / 'install')}'\n"
    + installer_function("write_file")
)


def _firewall_script(tmp_path: Path) -> str:
    text = INSTALLER.read_text(encoding="utf-8")
    start = text.index('NFT_CONF="${NFT_CONF:-/etc/nftables.conf}"')
    end = text.index("\n}\n", text.index("install_firewall() {")) + 3
    block = text[start:end]
    return (FIREWALL_PRELUDE + f'NFT_CONF="{posix(tmp_path / "nftables.conf")}"\n'
            + block + "install_firewall\necho FIREWALL_DONE\n")


def _firewall_fakes(bindir: Path, *, enable_fails: bool) -> None:
    fake_tool(bindir, "apt-get", "")
    fake_tool(bindir, "journalctl", "echo 'unit log' >&2")
    fake_tool(bindir, "nft", 'case "$1" in list) printf "table inet old {}\\n" ;; esac')
    fail = "exit 1" if enable_fails else "exit 0"
    fake_tool(bindir, "systemctl",
              f'case "$1 $2" in "enable --now") {fail} ;; "is-active --quiet") exit 0 ;; esac')


def test_install_firewall_loads_after_a_syntax_check_and_enables_the_unit(tmp_path, bindir):
    conf = tmp_path / "nftables.conf"
    conf.write_text("# debian default\n", encoding="utf-8")
    _firewall_fakes(bindir, enable_fails=False)
    proc = run_bash(_firewall_script(tmp_path), tmp_path, bindir)
    assert proc.returncode == 0 and "FIREWALL_DONE" in proc.stdout, proc.stderr
    text = conf.read_text(encoding="utf-8")
    assert "table inet imagectl" in text and 'iifname "eth1"' in text
    seen = calls(tmp_path)
    assert seen.index(next(c for c in seen if c.startswith("nft -c -f"))) \
        < seen.index("nft list ruleset") < seen.index("systemctl enable --now nftables")
    assert not [c for c in seen if c.startswith("systemctl disable")]
    assert list(tmp_path.glob("nftables.conf.pre-imagectl.*"))          # גיבוי גם כאן


def test_failed_install_firewall_leaves_tmpdir_empty(tmp_path, bindir):
    """גם rollback מוחק את עותק ה-ruleset ששימש לשחזור."""
    temp = tmp_path / "tmp"
    temp.mkdir()
    _firewall_fakes(bindir, enable_fails=True)
    proc = run_bash(_firewall_script(tmp_path), tmp_path, bindir,
                    env={"TMPDIR": posix(temp)})
    assert proc.returncode != 0
    assert list(temp.iterdir()) == []


def test_install_firewall_failure_restores_the_previous_ruleset_and_file(tmp_path, bindir):
    """‏`systemctl enable --now nftables` נכשל → ה-ruleset הקודם נטען חזרה,
    הקובץ הקודם חוזר מהגיבוי, והשגיאה נאמרת בלי `|| true` שמסתיר."""
    conf = tmp_path / "nftables.conf"
    conf.write_text("# debian default\n", encoding="utf-8")
    _firewall_fakes(bindir, enable_fails=True)
    proc = run_bash(_firewall_script(tmp_path), tmp_path, bindir)
    assert proc.returncode != 0 and "FIREWALL_DONE" not in proc.stdout
    assert "nftables לא עלה" in proc.stderr and "unit log" in proc.stderr
    assert "מחזיר את ה-ruleset הקודם" in proc.stderr
    seen = calls(tmp_path)
    assert "systemctl disable --now nftables" in seen
    restore = [c for c in seen if c.startswith("nft -f ") and not c.endswith("nftables.conf")]
    assert restore, seen                                                 # nft -f <prev>
    assert seen.index("systemctl disable --now nftables") < seen.index(restore[0])
    assert conf.read_text(encoding="utf-8") == "# debian default\n"


# --- console-live.sh + /api/console/health/live (‏#1131 ס' 1 / ס' 5) ------------------

class _LiveHandler(BaseHTTPRequestHandler):
    body: dict | None = None
    status = 200

    def do_GET(self):  # noqa: N802
        if self.path != "/api/console/health/live" or self.body is None:
            self.send_response(404)
            self.end_headers()
            return
        payload = json.dumps(self.body).encode()
        self.send_response(self.status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@contextlib.contextmanager
def serving_live(body: dict | None, status: int = 200):
    handler = type("_Bound", (_LiveHandler,), {"body": body, "status": status})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _live(url: str, want: str, wait: str = "2") -> subprocess.CompletedProcess:
    # ‏stdin=DEVNULL: בריצה רב-קבצית בווינדוס ה-handle של stdin נשבר (#14).
    return subprocess.run([BASH, str(LIVE), url, want, wait], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60, stdin=subprocess.DEVNULL,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def test_console_live_exits_zero_only_on_ok_with_the_expected_version():
    with serving_live({"ok": True, "version": "v0.49.0"}) as url:
        good = _live(url, "v0.49.0")
        assert good.returncode == 0 and good.stdout.strip() == "v0.49.0", good.stderr
        any_version = _live(url, "")
        assert any_version.returncode == 0
        wrong = _live(url, "v0.50.0")
        assert wrong.returncode != 0 and "v0.50.0" in wrong.stderr
    with serving_live({"ok": False, "version": "v0.49.0"}) as url:
        assert _live(url, "v0.49.0").returncode != 0
    with serving_live(None) as url:                                       # 404
        assert _live(url, "").returncode != 0
    with serving_live({"ok": True}) as dead:
        pass
    down = _live(dead, "")
    assert down.returncode != 0 and "לא ענה" in down.stderr


def test_health_live_is_unauthenticated_and_reports_the_version(tmp_path, images_root, clock):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from server.app import create_app
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080", now_fn=clock,
                     update_hooks={"describe": lambda repo_dir: "v0.49.0"}, repo_dir="/repo")
    client = TestClient(app)
    r = client.get("/api/console/health/live")
    assert r.status_code == 200 and r.json() == {"ok": True, "version": "v0.49.0",
                                                 "version_error": None}   # #1159
    assert client.get("/api/console/health").status_code == 401           # הבריאות עצמה — admin


def test_health_live_reports_the_version_the_process_loaded_not_the_tree_on_disk(
        tmp_path, images_root, clock):
    """‏#1546: בשרת חיפה העץ עבר ל-v0.75.0, השירות לא אותחל, ו-`/health/live`
    אמר v0.75.0 — הוא קרא את הדיסק בכל בקשה. זו הראיה ש-server-upgrade.sh
    נשען עליה ("החדש רץ"), ולכן היא חייבת להיות של התהליך, לא של העץ."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from server.app import create_app
    on_disk = {"tag": "v0.74.2"}
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080", now_fn=clock,
                     update_hooks={"describe": lambda repo_dir: on_disk["tag"]}, repo_dir="/repo")
    client = TestClient(app)
    on_disk["tag"] = "v0.75.0"                     # checkout בלי restart
    assert client.get("/api/console/health/live").json()["version"] == "v0.74.2"


def test_health_live_unread_at_startup_is_reread_until_it_succeeds_then_locked(
        tmp_path, images_root, clock):
    """‏#1546: גרסה שלא נקראה בעלייה אינה ננעלת כ"לא ידוע" — `/health/live`
    קורא שוב עד שהצליח (וכל עוד נכשל, מחזיר `version_error` בשם), ואז נועל."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from server.app import create_app
    from server.update import VersionError
    on_disk = {"tag": None}

    def describe(repo_dir):
        if on_disk["tag"] is None:
            raise VersionError("not-a-git-tree", "no git here")
        return on_disk["tag"]
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080", now_fn=clock,
                     update_hooks={"describe": describe}, repo_dir="/repo")
    client = TestClient(app)
    assert client.get("/api/console/health/live").json() == {
        "ok": True, "version": None, "version_error": "not-a-git-tree"}
    on_disk["tag"] = "v0.75.0"
    assert client.get("/api/console/health/live").json()["version"] == "v0.75.0"
    on_disk["tag"] = "v0.76.0"                     # checkout בלי restart — כבר נעול
    assert client.get("/api/console/health/live").json()["version"] == "v0.75.0"


# --- tools/server-upgrade.sh (‏#1131 ס' 5–6 / ס' 12) ---------------------------------

def _fake_repo(tmp_path: Path, *, live_ok: bool, payload_ok: bool = True) -> Path:
    repo = tmp_path / "repo"
    (repo / "install").mkdir(parents=True)
    (repo / "tools").mkdir()
    (repo / "install" / "imagectl-server.service").write_text("[Unit]\n", encoding="utf-8")
    shutil.copy(REPO / "install" / "imagectl-upgrade-recover.service", repo / "install")
    shutil.copy(UPGRADE, repo / "tools" / "server-upgrade.sh")
    (repo / "install" / "console-live.sh").write_text(
        '#!/usr/bin/env bash\nprintf "console-live %s\\n" "$*" >> "$FAKE_LOG"\n'
        + ("exit 0" if live_ok else 'echo "no answer" >&2; exit 1') + "\n",
        encoding="utf-8", newline="\n")
    (repo / "install" / "verify-boot-payload.sh").write_text(
        '#!/usr/bin/env bash\nprintf "verify-boot-payload %s\\n" "$*" >> "$FAKE_LOG"\n'
        + ("exit 0" if payload_ok else "exit 1") + "\n", encoding="utf-8", newline="\n")
    # ‏#1230: הבנייה עצמה נבדקת ב-test_boot_payload_1230; כאן רק שהיא נקראה.
    (repo / "tools" / "boot-payload-build.sh").write_text(
        '#!/usr/bin/env bash\nprintf "boot-payload-build %s\\n" "$*" >> "$FAKE_LOG"\n',
        encoding="utf-8", newline="\n")
    # ‏#1277: אימות החתימה נבדק ב-test_release_signing_1277; כאן — תג "חתום".
    (repo / "tools" / "verify-release-tag.py").write_text(
        'print("signed: stub SHA256:stub")\n', encoding="utf-8", newline="\n")
    return repo


def _upgrade_env(tmp_path: Path, repo: Path, data: Path) -> dict:
    flags = tmp_path / "initrd.flags"
    if not flags.exists():
        flags.write_text("--skip-apt --output /srv/imagectl/boot/initrd.img\n",
                         encoding="utf-8", newline="\n")
    return {"IMAGECTL_DATA_DIR": posix(data), "IMAGECTL_UNIT_DIR": posix(tmp_path / "units"),
            "IMAGECTL_JOURNALD_DIR": posix(tmp_path / "journald.conf.d"),
            "IMAGECTL_UPGRADE_LOG": posix(tmp_path / "upgrade.log"),
            "IMAGECTL_INITRD_FLAGS": posix(flags),
            "IMAGECTL_ISO_MANIFEST": posix(tmp_path / "no-iso-release.json"),
            "IMAGECTL_HTTP_ROOT": posix(tmp_path / "boot"),
            "IMAGECTL_LIVE_WAIT": "1", "PYTHONPATH": posix(REPO)}


def _upgrade_fakes(bindir: Path, *, restart_fails: bool = False, enable_fails: bool = False) -> None:
    fake_tool(bindir, "git", 'case "$1" in describe) echo "$FAKE_TAG" ;; checkout) exit 0 ;; '
                             'rev-parse) echo "commit-of-$FAKE_TAG" ;; rev-list) echo "commit-of-$4" ;; esac')
    restart = 'echo restart failed >&2; exit 1' if restart_fails else "exit 0"
    enable = 'echo "Failed to enable unit" >&2; exit 1' if enable_fails else "exit 0"
    fake_tool(bindir, "systemctl",
              f'case "$1" in restart) {restart} ;; enable) {enable} ;; '
              'show) echo "IMAGECTL_URL=http://10.10.10.8:8080 X=y" ;; esac')


def _status(data: Path) -> dict | None:
    from server import update
    from server.db import connect
    return update.get_status(connect(data / "imagectl.db"))


def _seed_db(data: Path, *, previous: str | None = "v0.48.0", open_round: bool = False):
    from server import update
    from server.db import connect, set_setting
    data.mkdir()
    conn = connect(data / "imagectl.db")
    if previous:
        set_setting(conn, update.PREVIOUS_KEY, previous)
    if open_round:
        conn.execute("INSERT INTO groups (id, label, role) VALUES ('g', 'g', 'classroom')")
        conn.execute(
            "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients,"
            " wait_seconds, state, opened_by, created_at, last_join_at, kind)"
            " VALUES ('s1', 'g', 'img', 'ROOM', 2, 300, 'running', 'nadav', 'now', 0.0, 'multicast')")
        conn.commit()
    conn.close()


def _run_upgrade(tmp_path, bindir, repo, data, tag="v0.49.0"):
    (tmp_path / "units").mkdir(exist_ok=True)
    return run_bash(f'bash "{posix(repo / "tools" / "server-upgrade.sh")}" {tag} "{posix(repo)}"',
                    tmp_path, bindir, env={**_upgrade_env(tmp_path, repo, data), "FAKE_TAG": tag})


def test_upgrade_verifies_the_new_version_answers_before_declaring_done(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    _upgrade_fakes(bindir)
    proc = _run_upgrade(tmp_path, bindir, repo, data)
    log = (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    assert proc.returncode == 0, proc.stderr + log
    assert "done and verified" in log
    seen = calls(tmp_path)
    assert seen.count("systemctl restart imagectl-server") == 1
    assert "systemctl is-active --quiet imagectl-server" in seen
    assert any(c.startswith("console-live ") and " v0.49.0 " in c for c in seen)
    assert any(c.startswith("verify-boot-payload ") and "http://10.10.10.8:8080" in c for c in seen)
    assert not [c for c in seen if c.startswith("git checkout")]
    assert (_status(data) or {}).get("state") != "failed"
    assert (tmp_path / "units" / "imagectl-server.service").exists()


def test_upgrade_to_a_broken_tag_rolls_back_to_the_previous_tag_and_records_failed(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data, previous="v0.48.0")
    repo = _fake_repo(tmp_path, live_ok=False)
    _upgrade_fakes(bindir)
    proc = _run_upgrade(tmp_path, bindir, repo, data)
    log = (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    assert proc.returncode != 0
    seen = calls(tmp_path)
    assert "git checkout --detach v0.48.0" in seen
    assert seen.count("systemctl restart imagectl-server") == 2          # החדש, ואז החזרה
    assert seen.index("git checkout --detach v0.48.0") > seen.index("systemctl is-active --quiet imagectl-server")
    status = _status(data)
    assert status and status["state"] == "failed" and status["tag"] == "v0.49.0"
    assert "health/live" in status["error"] and "הוחזר ל-v0.48.0" in status["error"]
    assert "FAILED" in log


def test_upgrade_restart_failure_rolls_back_too(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    _upgrade_fakes(bindir, restart_fails=True)
    proc = _run_upgrade(tmp_path, bindir, repo, data)
    assert proc.returncode != 0
    assert "git checkout --detach v0.48.0" in calls(tmp_path)
    status = _status(data)
    assert status and status["state"] == "failed" and "restart" in status["error"]


def test_upgrade_refuses_while_a_round_is_open_even_when_run_by_hand(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data, open_round=True)
    repo = _fake_repo(tmp_path, live_ok=True)
    _upgrade_fakes(bindir)
    proc = _run_upgrade(tmp_path, bindir, repo, data)
    assert proc.returncode != 0
    seen = calls(tmp_path)
    assert not [c for c in seen if c.startswith("systemctl restart")]
    status = _status(data)
    assert status and status["state"] == "failed" and "סבב הפצה פתוח" in status["error"]   # #1891: busy_reason


def test_upgrade_without_a_saved_previous_tag_rolls_back_to_the_commit_it_started_from(tmp_path, bindir):
    """‏#1546: בהרצה ידנית אין `update_previous` ב-DB. עד #1546 הכשל יצא בלי
    checkout ובלי restart — העץ נשאר על התג החדש והתהליך הישן רץ (שרת חיפה,
    17/10). יעד החזרה הוא הקומיט שהעץ היה עליו לפני הריצה."""
    data = tmp_path / "data"
    _seed_db(data, previous=None)
    repo = _fake_repo(tmp_path, live_ok=False)
    _upgrade_fakes(bindir)
    proc = _run_upgrade(tmp_path, bindir, repo, data)
    assert proc.returncode != 0
    seen = calls(tmp_path)
    assert "git checkout --detach commit-of-v0.49.0" in seen, seen
    assert seen.count("systemctl restart imagectl-server") == 2          # החדש, ואז החזרה
    status = _status(data)
    assert status and status["state"] == "failed"
    assert "הוחזר ל-commit-of-v0.49.0" in status["error"] and "אין תג קודם" not in status["error"]


def test_a_stale_saved_previous_tag_is_not_the_rollback_target(tmp_path, bindir):
    """‏#1546: ‏`update_previous` אינו נמחק לעולם. הרצה ידנית אחרי עדכון קודם
    מהקונסולה הייתה "חוזרת" לתג שלפני **אותו** עדכון — לא למה שרץ. יעד
    החזרה הוא מה שהעץ עליו, ו-`update_previous` רק כשהוא מצביע עליו."""
    data = tmp_path / "data"
    _seed_db(data, previous="v0.47.0")
    repo = _fake_repo(tmp_path, live_ok=False)
    _upgrade_fakes(bindir)
    git = bindir / "git"   # ‏v0.47.0 מצביע על קומיט אחר מזה שהעץ עליו
    git.write_text(git.read_text(encoding="utf-8").replace(
        'case "$1" in describe)',
        'case "$*" in *"v0.47.0^{commit}"*) echo "commit-of-v0.47.0"; exit 0 ;; esac\n'
        'case "$1" in -C) shift 2 ;; esac\n'
        'case "$1" in describe)'), encoding="utf-8", newline="\n")
    proc = _run_upgrade(tmp_path, bindir, repo, data)
    assert proc.returncode != 0
    seen = calls(tmp_path)
    assert "git checkout --detach v0.47.0" not in seen
    assert "git checkout --detach commit-of-v0.49.0" in seen, seen
    status = _status(data)
    assert status and status["state"] == "failed" and "הוחזר ל-commit-of-v0.49.0" in status["error"]


# --- ‏#1957: טיימרים — מותקנים ומופעלים בכל עדכון, ולא רק בהתקנה נקייה ---------------

TIMER = "imagectl-backup.timer"


def _with_timer(repo: Path) -> None:
    (repo / "install" / TIMER).write_text("[Timer]\nOnCalendar=hourly\n", encoding="utf-8")
    (repo / "install" / "imagectl-backup.service").write_text("[Unit]\n", encoding="utf-8")


def test_upgrade_installs_and_enables_every_timer_of_the_new_version(tmp_path, bindir):
    """שרת שמתעדכן לגרסה עם `imagectl-backup.timer` (#1895) מקבל אותו — מותקן
    ומופעל, אחרי daemon-reload. בלי זה הגיבוי השעתי קיים רק בהתקנה נקייה."""
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    _with_timer(repo)
    _upgrade_fakes(bindir)
    proc = _run_upgrade(tmp_path, bindir, repo, data)
    assert proc.returncode == 0, proc.stderr + (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    assert (tmp_path / "units" / TIMER).exists()
    assert (tmp_path / "units" / "imagectl-backup.service").exists()
    seen = calls(tmp_path)
    enable = f"systemctl enable --now {TIMER}"
    assert enable in seen, seen
    assert seen.index("systemctl daemon-reload") < seen.index(enable)
    assert (_status(data) or {}).get("state") != "failed"


def test_a_timer_that_does_not_enable_fails_the_upgrade_and_rolls_back(tmp_path, bindir):
    """כשל בהפעלה אינו שקט: חזרה לתג הקודם, וסטטוס `failed` שנוקב בטיימר."""
    data = tmp_path / "data"
    _seed_db(data, previous="v0.48.0")
    repo = _fake_repo(tmp_path, live_ok=True)
    _with_timer(repo)
    _upgrade_fakes(bindir, enable_fails=True)
    proc = _run_upgrade(tmp_path, bindir, repo, data)
    assert proc.returncode != 0
    assert "git checkout --detach v0.48.0" in calls(tmp_path)
    status = _status(data)
    assert status and status["state"] == "failed" and TIMER in status["error"], status


def test_rollback_disables_a_timer_the_previous_version_does_not_have(tmp_path, bindir):
    """חזרה לגרסה שאין לה את הטיימר לא משאירה אותו מופעל מול מודול שאינו
    קיים: הוא מכובה ומוסר. טיימר שקיים גם בגרסה הישנה נשאר."""
    data = tmp_path / "data"
    _seed_db(data, previous="v0.48.0")
    repo = _fake_repo(tmp_path, live_ok=False)
    _with_timer(repo)
    _upgrade_fakes(bindir)
    git = bindir / "git"   # העץ של v0.48.0 — בלי הטיימר
    git.write_text(git.read_text(encoding="utf-8").replace(
        "checkout) exit 0 ;;",
        f'checkout) if [ "$3" = v0.48.0 ]; then rm -f install/{TIMER}; fi; exit 0 ;;'),
        encoding="utf-8", newline="\n")
    proc = _run_upgrade(tmp_path, bindir, repo, data)
    assert proc.returncode != 0
    seen = calls(tmp_path)
    disable = f"systemctl disable --now {TIMER}"
    assert disable in seen, seen
    assert seen.index("git checkout --detach v0.48.0") < seen.index(disable)
    assert not (tmp_path / "units" / TIMER).exists()
    status = _status(data)
    assert status and status["state"] == "failed" and "הוחזר ל-v0.48.0" in status["error"], status


def test_console_live_rereads_the_pinned_cert_on_every_attempt():
    """‏#1417: השרת מחליף את התעודה בעלייה הראשונה (#1380); בודק שקרא את
    ה-cafile פעם אחת נשאר מצמיד את הישנה ונכשל — והשדרוג נחזר."""
    text = LIVE.read_text(encoding="utf-8")
    loop = text.index("while True:")
    assert "create_default_context(cafile=" not in text[:text.index("def context()")]
    assert "context=context()" in text[loop:], "ה-context נבנה פעם אחת מחוץ ללולאה"
