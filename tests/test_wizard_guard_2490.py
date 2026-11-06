"""‏#2490 — אשף האתחול הראשון: ‏8081 רק על loopback ועל כרטיס ההתקנה.

‏`POST /api/wizard/apply` אינו דורש הזדהות, והקריאה הראשונה קובעת את סיסמת
המנהל. ‏firstboot מבקש DHCP זמני לכל כרטיס עם קישור (גם להפצה), והאשף
מאזין על ‏0.0.0.0 — וחומת האש הקבועה עולה רק בסוף ההתקנה. כלומר כל מחשב
באותו קטע רשת יכול היה לקבוע את סיסמת המנהל לפני המפעיל.

התיקון אינו משנה מסך: טבלת nftables קטנה (``inet imagesrv_wizard``) נטענת
ונקראת בחזרה **לפני** שהאשף עולה, ומתירה 8081 רק על ‏``lo`` (הגואי על
tty1 מדבר עם האשף דרך 127.0.0.1) ועל הכרטיס ש-``/etc/imagesrv/installer-nic``
נוקב בו לפי MAC. היחידה עצמה מסרבת לעלות בלי הטבלה. חומת האש הקבועה
(``flush ruleset``) מחליפה אותה בסוף ההתקנה.

למה לא bind לכתובת הכרטיס: הגואי המקומי מתחבר ל-127.0.0.1 (היה נשבר), ו-
לינוקס מקבל חבילה לכתובת של כרטיס אחד גם כשהיא נכנסת בכרטיס אחר (weak
host) — ‏``iifname`` בודק את כרטיס הכניסה, bind לא.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_nftables_rules import BASH, GEN as FULL_GEN, _nft_check, needs_bash

REPO = Path(__file__).resolve().parent.parent
GUARD = REPO / "deploy" / "nftables-wizard.sh"
FIRSTBOOT = REPO / "tools" / "iso" / "firstboot.sh"
WIZARD_UNIT = REPO / "deploy" / "imagesrv-wizard.service"

SHELLS = [s for s in (BASH, shutil.which("dash")) if s]
MAC = "52:54:00:12:34:56"
OTHER_MAC = "52:54:00:ab:cd:ef"
LO_RULE = 'iifname "lo" tcp dport 8081 accept'
DROP_RULE = "tcp dport 8081 counter drop"


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
    assert _rules(proc.stdout) == [LO_RULE, 'iifname "ens19" tcp dport 8081 accept', DROP_RULE]
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
    assert _rules(proc.stdout) == [LO_RULE, DROP_RULE]
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


# --- firstboot והיחידה ----------------------------------------------------------

def test_firstboot_loads_and_reads_back_the_guard_before_the_wizard_starts() -> None:
    code = FIRSTBOOT.read_text(encoding="utf-8")
    headless = code[code.index('NIC_FILE="$ETC/installer-nic"'):]
    dhcp = headless.index('temporary_dhcp "$nic" &')
    build = headless.index('deploy/nftables-wizard.sh" --nic-fact "$NIC_FILE"')
    load = headless.index("nft -f -")
    readback = headless.index("nft list chain inet imagesrv_wizard input")
    start = headless.index("systemctl start imagesrv-wizard")
    assert dhcp < build < load < readback < start
    assert headless[build:start].count("fail wizard-guard-failed") == 4, "כל שלב בשומר נכשל בשמו"


def test_wizard_unit_refuses_to_start_without_the_guard() -> None:
    """גם `systemctl start` מכל מקום אחר (‏Wants= של הגואי) — לא בלי הטבלה."""
    unit = WIZARD_UNIT.read_text(encoding="utf-8")
    pre = unit.index("ExecStartPre=/usr/sbin/nft list table inet imagesrv_wizard")
    assert pre < unit.index("ExecStart=/usr/bin/python3 -m server.wizard")


def test_the_local_gui_reaches_the_wizard_through_loopback() -> None:
    """המסלול הלגיטימי על מסך השרת: הגשר מתחבר ל-127.0.0.1, ו-lo פתוח בטבלה."""
    from server.wizard import bridge
    assert bridge.API_BASE.startswith("https://127.0.0.1:8081/")


# --- הרצת firstboot (מסוף ה-DHCP ועד שהאשף עולה) מול nft/systemctl מדומים ----

#: ‏nft ו-systemctl כפונקציות bash: עוברות גם לתת-מעטפות ולצינורות, ובלי PATH.
STUBS = r'''set -euo pipefail
fail() { printf "FAIL %s\n" "$1"; exit 1; }
log() { printf "LOG %s\n" "$*"; }
install() { :; }
systemctl() { printf "systemctl %s\n" "$*" >> "$CALLS"; }
nft() {
    printf "nft %s\n" "$*" >> "$CALLS"
    case "$1" in
        -f) [ "$MODE" = load-fails ] && return 1; cat > "$LOADED" ;;
        list) [ "$MODE" = not-listed ] && return 1
              if [ "$MODE" = no-drop ]; then grep -v drop "$LOADED"; else cat "$LOADED"; fi ;;
    esac
}
'''


def _wizard_start_segment() -> str:
    """הקטע האמיתי מ-firstboot: מסוף לולאת ה-DHCP הזמני ועד בדיקת is-active
    של האשף. שני הסמנים קיימים גם לפני התיקון — כך שבבקרה השלילית הטסט רץ
    ונכשל על התנהגות ("האשף עלה בלי שומר"), לא על סמן חסר."""
    code = FIRSTBOOT.read_text(encoding="utf-8")
    head = 'wait "$dhcp_pid" || fail check-error "A temporary DHCP worker failed"\ndone\n'
    start = code.index(head) + len(head)
    tail = code.index("systemctl is-active --quiet imagesrv-wizard", start)
    return code[start:code.index("\n", tail) + 1]


@needs_bash
@pytest.mark.parametrize("nft_mode", ["ok", "load-fails", "not-listed", "no-drop"])
def test_firstboot_starts_the_wizard_only_after_the_guard_is_read_back(tmp_path, nft_mode) -> None:
    sys_net = _sys_net(tmp_path, {"ens18": OTHER_MAC, "ens19": MAC})
    fact = _fact(tmp_path, f"state=configured\nmac={MAC}\n")
    calls, loaded = tmp_path / "calls", tmp_path / "loaded.nft"
    script = tmp_path / "segment.sh"
    # הקטע כותב drop-in ל-/etc/systemd/system — מופנה לתיקיית הטסט, לעולם לא למכונה.
    units = tmp_path / "systemd"
    (units / "imagesrv-dcui.service.d").mkdir(parents=True)
    segment = _wizard_start_segment().replace("/etc/systemd/system/", f"{units.as_posix()}/")
    assert "/etc/" not in segment, "the segment would write to the real /etc"
    script.write_text(STUBS + segment, encoding="utf-8", newline="\n")
    env = {**os.environ, "SRC": REPO.as_posix(), "NIC_FILE": fact.as_posix(),
           "IMAGESRV_SYS_NET": sys_net.as_posix(), "STATUS": (tmp_path / "status").as_posix(),
           "ROLE": "standalone", "MODE": nft_mode, "CALLS": calls.as_posix(),
           "LOADED": loaded.as_posix()}
    proc = subprocess.run([BASH, str(script)], capture_output=True, text=True, encoding="utf-8",
                          stdin=subprocess.DEVNULL, env=env, check=False)
    log = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    started = "systemctl start imagesrv-wizard" in log
    if nft_mode == "ok":
        assert proc.returncode == 0, (proc.stdout, proc.stderr)
        assert started, log
        before = log[:log.index("systemctl start imagesrv-wizard")]
        assert "nft -f -" in before and "nft list chain inet imagesrv_wizard input" in before, \
            f"the wizard started with no firewall guard loaded and read back: {log}"
        guard = loaded.read_text(encoding="utf-8")
        assert 'iifname "ens19" tcp dport 8081 accept' in guard and "ens18" not in guard
        assert "LOG wizard guard: port 8081 open on loopback and ens19" in proc.stdout
    else:
        assert not started, f"the wizard started although the guard was not proven: {log}"
        assert "FAIL wizard-guard-failed" in proc.stdout, (proc.stdout, proc.stderr)
