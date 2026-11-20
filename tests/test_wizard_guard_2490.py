"""#2490 — אשף האתחול הראשון: 8081 רק על loopback ועל כרטיס ההתקנה.

`POST /api/wizard/apply` אינו דורש הזדהות, והקריאה הראשונה קובעת את סיסמת
המנהל. firstboot מבקש DHCP זמני לכל כרטיס עם קישור (גם להפצה), והאשף
מאזין על 0.0.0.0 — וחומת האש הקבועה עולה רק בסוף ההתקנה. כלומר כל מחשב
באותו קטע רשת יכול היה לקבוע את סיסמת המנהל לפני המפעיל.

התיקון אינו משנה מסך: טבלת nftables קטנה (``inet imagesrv_wizard``) נטענת
ונקראת בחזרה **לפני** שהאשף עולה, ומתירה 8081 רק על ``lo`` (הגואי על
tty1 מדבר עם האשף דרך 127.0.0.1) ועל הכרטיס ש-``/etc/imagesrv/installer-nic``
נוקב בו לפי MAC. היחידה עצמה מסרבת לעלות בלי הטבלה. חומת האש הקבועה
(``flush ruleset``) מחליפה אותה בסוף ההתקנה.

למה לא bind לכתובת הכרטיס: הגואי המקומי מתחבר ל-127.0.0.1 (היה נשבר), ו-
לינוקס מקבל חבילה לכתובת של כרטיס אחד גם כשהיא נכנסת בכרטיס אחר (weak
host) — ``iifname`` בודק את כרטיס הכניסה, bind לא.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_nftables_rules import BASH, GEN as FULL_GEN, _nft_check, needs_bash

REPO = Path(__file__).resolve().parent.parent
GUARD = REPO / "deploy" / "nftables-wizard.sh"
WIZARD_UNIT = REPO / "deploy" / "imagesrv-wizard.service"

SHELLS = [s for s in (BASH, shutil.which("dash")) if s]
MAC = "52:54:00:12:34:56"
OTHER_MAC = "52:54:00:ab:cd:ef"
LO_RULE = 'iifname "lo" tcp dport 8081 accept'
DROP_RULE = "tcp dport 8081 counter drop"
#: #2518: DNS and rpcbind dropped off loopback (test_wizard_guard_dns_2518.py).
LOCAL_ONLY = [f'iifname != "lo" {proto} dport {port} counter drop'
              for port in (53, 111) for proto in ("udp", "tcp")]


def _sys_net(tmp_path: Path, nics: dict[str, str]) -> Path:
    root = tmp_path / "sys-class-net"
    for name, mac in nics.items():
        (root / name).mkdir(parents=True)
        # LF כמו ב-sysfs; ב-Windows write_text היה כותב CRLF.
        (root / name / "address").write_text(mac + "\n", encoding="utf-8", newline="\n")
    root.mkdir(exist_ok=True)
    return root


def _fact(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "installer-nic"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _run(shell: str, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([shell, str(GUARD), *args], capture_output=True, text=True,
                          encoding="utf-8", check=False, stdin=subprocess.DEVNULL,
                          env=env)


def _rules(out: str) -> list[str]:
    return [line.strip() for line in out.replace("\r\n", "\n").splitlines()
            if "dport" in line and not line.strip().startswith("#")]


# --- המחולל ------------------------------------------------------------------

@needs_bash
@pytest.mark.parametrize("shell", SHELLS)
def test_guard_opens_8081_on_loopback_and_the_installer_nic_only(tmp_path, shell) -> None:
    """הכרטיס נמצא לפי MAC ולא לפי שם (d-i קרא לו eth0, המערכת ens19), וכרטיס
    ההפצה — שקיבל גם הוא DHCP זמני — אינו מקבל כלל."""
    sys_net = _sys_net(tmp_path, {"ens18": OTHER_MAC, "ens19": MAC, "lo": "00:00:00:00:00:00"})
    fact = _fact(tmp_path, f"state=configured\ninterface=eth0\nmac={MAC.upper()}\nreason=dhcp\n")
    proc = _run(shell, "--nic-fact", str(fact), "--sys-net", str(sys_net))
    assert proc.returncode == 0, proc.stderr
    assert _rules(proc.stdout) == [LO_RULE, 'iifname "ens19" tcp dport 8081 accept', DROP_RULE,
                                   *LOCAL_ONLY]
    assert "ens18" not in proc.stdout
    out = proc.stdout
    # שאר התעבורה (תשובות DHCP, apt המקומי) אינה עניינה של הטבלה הזו.
    assert "policy accept" in out and "policy drop" not in out
    # לא נוגעת בטבלאות אחרות, ונטענת שוב בלי שגיאה (firstboot שרץ פעמיים).
    statements = [line for line in out.splitlines() if not line.strip().startswith("#")]
    assert not any("flush" in line for line in statements)
    assert out.index("table inet imagesrv_wizard\n") < out.index("delete table inet imagesrv_wizard") \
        < out.index("table inet imagesrv_wizard {")
    assert out.isascii()


@needs_bash
@pytest.mark.parametrize("fact_text,nics", [
    (None, {"ens18": MAC}),                                                  # אין קובץ
    ("state=undecidable\nreason=no-network\n", {"ens18": MAC}),              # d-i לא הגדיר רשת
    (f"state=configured\ninterface=eth0\nmac={MAC}\n", {"ens19": OTHER_MAC}),  # ה-MAC אינו על אף כרטיס
])
def test_guard_without_a_known_installer_nic_stays_closed_to_the_network(tmp_path, fact_text, nics) -> None:
    """עיקרון 1: מצב לא ברור נסגר. הגואי המקומי עדיין עובד (lo), והסיבה נאמרת בשם."""
    sys_net = _sys_net(tmp_path, nics)
    fact = _fact(tmp_path, fact_text) if fact_text is not None else tmp_path / "missing"
    proc = _run(BASH, "--nic-fact", str(fact), "--sys-net", str(sys_net))
    assert proc.returncode == 0, proc.stderr
    assert _rules(proc.stdout) == [LO_RULE, DROP_RULE, *LOCAL_ONLY]
    assert "[network-nic-undecidable]" in proc.stderr


@needs_bash
def test_guard_that_cannot_look_at_the_nics_fails_instead_of_opening(tmp_path) -> None:
    """עיקרון 5: "לא הצלחתי לבדוק" אינו "אין כרטיס" — יציאה ≠0 ובלי ruleset."""
    fact = _fact(tmp_path, f"state=configured\nmac={MAC}\n")
    proc = _run(BASH, "--nic-fact", str(fact), "--sys-net", str(tmp_path / "no-sysfs"))
    assert proc.returncode == 2 and proc.stdout == ""
    assert "check-error" in proc.stderr


@needs_bash
def test_guard_refuses_a_nic_name_that_could_break_the_ruleset(tmp_path) -> None:
    sys_net = _sys_net(tmp_path, {"bad name": MAC})
    fact = _fact(tmp_path, f"state=configured\nmac={MAC}\n")
    proc = _run(BASH, "--nic-fact", str(fact), "--sys-net", str(sys_net))
    assert proc.returncode == 2 and proc.stdout == ""


@needs_bash
def test_the_permanent_firewall_replaces_the_guard() -> None:
    """הטבלה זמנית: חומת האש הקבועה פותחת ב-``flush ruleset``, כך שכרטיס
    שהמפעיל בחר באשף (ואינו כרטיס ההתקנה) אינו נשאר חסום אחרי ההתקנה."""
    full = subprocess.run([BASH, str(FULL_GEN), "--servers-if", "eth-srv"], capture_output=True,
                          text=True, encoding="utf-8", stdin=subprocess.DEVNULL, check=True).stdout
    assert full.index("flush ruleset") < full.index("table inet imagesrv {")
    assert "imagesrv_wizard" not in full


@requires_native("nft", why="בדיקת תחביר nftables על דביאן — דילוג בווינדוס")
@needs_bash
@pytest.mark.parametrize("with_nic", [True, False])
def test_nft_accepts_the_guard(tmp_path, with_nic) -> None:
    sys_net = _sys_net(tmp_path, {"ens18": MAC})
    fact = _fact(tmp_path, f"state=configured\nmac={MAC if with_nic else OTHER_MAC}\n")
    proc = _run(BASH, "--nic-fact", str(fact), "--sys-net", str(sys_net))
    assert proc.returncode == 0, proc.stderr
    candidate = tmp_path / "guard.nft"
    candidate.write_text(proc.stdout, encoding="utf-8")
    check = _nft_check(candidate)
    assert check.returncode == 0, check.stderr


# --- היחידה (firstboot כבר אינו מפעיל אותה: #2763) -----------------------------------------------------------

def test_wizard_unit_refuses_to_start_without_the_guard() -> None:
    """גם `systemctl start` מכל מקום אחר (Wants= של הגואי) — לא בלי הטבלה."""
    unit = WIZARD_UNIT.read_text(encoding="utf-8")
    pre = unit.index("ExecStartPre=/usr/sbin/nft list table inet imagesrv_wizard")
    assert pre < unit.index("ExecStart=/usr/bin/python3 -m server.wizard")


def test_the_local_gui_reaches_the_wizard_through_loopback() -> None:
    """המסלול הלגיטימי על מסך השרת: הגשר מתחבר ל-127.0.0.1, ו-lo פתוח בטבלה."""
    from server.wizard import bridge
    assert bridge.API_BASE.startswith("https://127.0.0.1:8081/")
