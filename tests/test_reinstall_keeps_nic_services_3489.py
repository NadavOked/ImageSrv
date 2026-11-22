"""#3489: התקנה חוזרת כותבת את חומת האש של ברירת המחדל ומתעלמת מהשירותים שהמפעיל שמר לכל כרטיס (`nicsvc:*`).

הכללים והמאזינים נגזרים מאותה רשומה, אבל המתקין בנה `NFT_ARGS` רק משני תפקידים -- כרטיס שהמפעיל סגר
(קונסולה כבויה, אתחול מהרשת כבוי) חזר פתוח. עכשיו המתקין קורא את `NicServices.firewall_services()` (לא עותק
שלו) ומעביר `--per-nic --service`. קריאה שנכשלה עוצרת את ההתקנה; היא אינה חומת אש של ברירת המחדל.

אין נגיעה במכונה אמיתית: כרטיסי הרשת הם עץ `/sys/class/net` מדומה (`SYS_NET`), והמתקין רץ ב-`DRY_RUN`
(מדפיס את פקודת המחולל, לא מריץ `nft`).
"""

from __future__ import annotations

import json
import shlex
import shutil
from pathlib import Path

import pytest

from server.db import connect, set_setting
from server.net import dhcp, nic_services

from test_installer_1131 import BASH as INSTALLER_BASH, bindir, installer_function, run_bash  # noqa: F401
from test_nftables_per_nic import SHELLS, rules, run

REPO = Path(__file__).resolve().parent.parent
needs_installer = pytest.mark.skipif(INSTALLER_BASH is None or shutil.which("python3") is None,
                                     reason="the installer runs bash + python3")
SERVERS, DEPLOY, OTHER = "eth-srv", "eth-deploy", "eth-lab"
#: #3486: the installer always hands the generator the /boot listener (`--boot-port "$BOOT_PORT"`, default 7711), after the per-card part.
BOOT = 7711
BOOT_ARGS = ["--boot-port", str(BOOT)]


def _server(tmp_path: Path, saved: dict[str, list[str]] | None, raw: dict[str, str] | None = None) -> Path:
    """DB של שרת + עץ sysfs עם שלושה כרטיסים. מחזיר את תיקיית ה-sysfs."""
    conn = connect(tmp_path / "imagesrv.db")
    for nic, services in (saved or {}).items():
        set_setting(conn, nic_services.SETTING_PREFIX + nic, json.dumps({"services": services}))
    for nic, value in (raw or {}).items():
        set_setting(conn, nic_services.SETTING_PREFIX + nic, value)
    conn.commit()
    conn.close()
    sysnet = tmp_path / "sysnet"
    for nic in (SERVERS, DEPLOY, OTHER):
        (sysnet / nic).mkdir(parents=True)
    return sysnet


def _function(name: str) -> str:
    """גוף הפונקציה מהמתקין, או ריק אם אינה קיימת -- כדי שהמתקין הישן ייכשל בהתנהגות (פקודת מחולל שגויה)
    ולא ב"לא נמצאה פונקציה" (שאינו בקרה שלילית)."""
    try:
        return installer_function(name)
    except AssertionError:
        return ""


def _install_firewall(tmp_path, bindir, sysnet):
    script = (
        "GRN=''; YEL=''; DIM=''; OFF=''; RED=''; DRY_RUN=1; NO_FIREWALL=0; STORAGE_ROLE=primary\n"
        f'APP_DIR="{REPO.as_posix()}"; SCRIPT_DIR="{REPO.as_posix()}/deploy"; DATA_DIR="{tmp_path.as_posix()}"\n'
        f'SERVERS_IF={SERVERS}; IFACE={DEPLOY}; CONSOLE_DEPLOY_IF=""; SYS_NET="{sysnet.as_posix()}"; BOOT_PORT={BOOT}\n'
        + "".join(_function(n) for n in (
            "say", "warn", "die", "run", "console_proxy_interfaces_from_db", "console_ntp_interfaces_from_db",
            "console_nic_services_from_db", "install_firewall"))
        + "install_firewall\n")
    return run_bash(script, tmp_path, bindir)


def _generator_args(stdout: str) -> list[str]:
    # python3 של ווינדוס מסיים שורה ב-CRLF, ו-text=True הופך את ה-CR לשורה חדשה באמצע הפקודה; בלינוקס אין מה לאחד.
    flat = " ".join(stdout.split())
    tail = flat.split("nftables-rules.sh ", 1)[1].split(" | nft", 1)[0]
    return shlex.split(tail)


def _console_args(sysnet: Path, tmp_path: Path) -> list[str]:
    """מה שהקונסולה (`regenerate_firewall`) מעבירה למחולל לאותו מצב שמור -- מ-`firewall_services()` עצמו."""
    service = nic_services.NicServices(
        connect(tmp_path / "imagesrv.db"), nic_services.Baseline(console=SERVERS, deploy=DEPLOY),
        {"interfaces": lambda: dhcp.list_interfaces(sysnet)})
    args = ["--per-nic"]
    for nic, names in service.firewall_services():
        args += ["--service", f"{nic}:{','.join(names)}"]
    return args


# --- המתקין ---------------------------------------------------------------------------------


@needs_installer
def test_reinstall_hands_the_saved_nic_services_to_the_generator(tmp_path, bindir) -> None:
    # המפעיל סגר את הקונסולה בכרטיס השרתים ואת אתחול-הרשת מכרטיס ההפצה; כרטיס שלישי נתן רק זמן.
    sysnet = _server(tmp_path, {SERVERS: ["monitor"], DEPLOY: ["multicast"], OTHER: ["ntp"]})
    proc = _install_firewall(tmp_path, bindir, sysnet)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    args = _generator_args(proc.stdout)
    assert args == _console_args(sysnet, tmp_path) + BOOT_ARGS
    assert args == ["--per-nic", "--service", f"{DEPLOY}:multicast", "--service", f"{OTHER}:ntp",
                    "--service", f"{SERVERS}:monitor", *BOOT_ARGS]
    assert "--ntp-if" not in args and "--servers-if" not in args     # ב-per-nic המחולל מסרב להם


@needs_installer
def test_reinstall_without_saved_services_writes_todays_default_rules(tmp_path, bindir) -> None:
    """מה שאסור לשבור: שרת שאיש לא ערך -- אותה פקודה כמו קודם."""
    sysnet = _server(tmp_path, None)
    proc = _install_firewall(tmp_path, bindir, sysnet)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _generator_args(proc.stdout) == ["--servers-if", SERVERS, "--deploy-if", DEPLOY, *BOOT_ARGS]


@needs_installer
@pytest.mark.parametrize("shell", SHELLS)
def test_the_rules_written_on_reinstall_contain_the_saved_services_and_not_the_closed_ones(
        tmp_path, bindir, shell) -> None:
    sysnet = _server(tmp_path, {SERVERS: ["monitor"], DEPLOY: ["multicast", "netboot", "ntp"], OTHER: []})
    proc = _install_firewall(tmp_path, bindir, sysnet)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    got = rules(run(shell, *_generator_args(proc.stdout)).stdout)
    assert f'iifname "{DEPLOY}" udp dport 69 accept' in got            # אתחול מהרשת נשמר
    assert f'iifname "{DEPLOY}" udp dport 123 accept' in got           # וגם הזמן
    assert f'iifname "{DEPLOY}" tcp dport 7712 accept' in got
    assert not any(f'"{SERVERS}" tcp dport 443' in r for r in got), "המפעיל סגר את הקונסולה בכרטיס הזה"
    # הניגוד: ברירת המחדל (מה שנכתב לפני התיקון) כן פותחת אותה
    default = rules(run(shell, "--servers-if", SERVERS, "--deploy-if", DEPLOY).stdout)
    assert f'iifname "{SERVERS}" tcp dport 443 accept' in default
    assert not any(f'"{OTHER}"' in r for r in got), "כרטיס בלי שירותים נשאר סגור"


@needs_installer
def test_reinstall_fails_loudly_when_a_saved_record_cannot_be_read(tmp_path, bindir) -> None:
    """עיקרון 5: "לא הצלחנו לקרוא" אינו "המפעיל לא שמר" -- חומת אש שנכתבת בשקט פתוחה יותר ממה שנבחר."""
    sysnet = _server(tmp_path, {DEPLOY: ["multicast"]}, raw={SERVERS: "{not json"})
    proc = _install_firewall(tmp_path, bindir, sysnet)
    assert proc.returncode != 0
    assert "would run: sh" not in proc.stdout, "ההתקנה המשיכה לחומת אש בלי הבחירות"
    assert "שירותי הכרטיסים" in proc.stderr


@needs_installer
def test_reinstall_fails_when_services_are_saved_but_no_nic_is_visible(tmp_path, bindir) -> None:
    sysnet = _server(tmp_path, {SERVERS: ["monitor"]})
    shutil.rmtree(sysnet)
    sysnet.mkdir()
    proc = _install_firewall(tmp_path, bindir, sysnet)
    assert proc.returncode != 0
    assert "would run: sh" not in proc.stdout


# --- ההיגיון בצד השרת -----------------------------------------------------------------------


def test_installer_services_is_the_same_answer_the_console_gets(tmp_path) -> None:
    sysnet = _server(tmp_path, {SERVERS: ["monitor"], OTHER: ["netboot"]})
    conn = connect(tmp_path / "imagesrv.db")
    got = nic_services.installer_firewall_services(conn, SERVERS, DEPLOY, str(sysnet))
    want = nic_services.NicServices(conn, nic_services.Baseline(console=SERVERS, deploy=DEPLOY),
                                    {"interfaces": lambda: dhcp.list_interfaces(sysnet)}).firewall_services()
    assert got == want and got


def test_installer_services_of_an_unedited_server_is_none(tmp_path) -> None:
    sysnet = _server(tmp_path, None)
    assert nic_services.installer_firewall_services(
        connect(tmp_path / "imagesrv.db"), SERVERS, DEPLOY, str(sysnet)) is None
