"""‏#705 — מחולל nftables במצב "לפי כרטיס": הכללים נגזרים מהשירותים שהמפעיל
בחר לכל כרטיס, ולא משני התפקידים הקבועים. בלי הדגלים — הפלט זהה בית-בבית
(הצילומים ב-`test_nftables_rules.py` הם הבקרה).

רץ גם ב-`sh` של המערכת (‏dash בדביאן — זה מה ש-`deploy_net` מריץ), לא רק bash.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from test_nftables_rules import BASH, GEN, needs_bash

SHELLS = [s for s in (BASH, shutil.which("dash")) if s]


def run(shell: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([shell, str(GEN), *args], capture_output=True, text=True,
                          encoding="utf-8", check=False, stdin=subprocess.DEVNULL)


def rules(out: str) -> list[str]:
    return [line.strip() for line in out.replace("\r\n", "\n").splitlines()
            if line.strip().startswith("iifname")]


@needs_bash
@pytest.mark.parametrize("shell", SHELLS)
def test_rules_follow_the_services_of_each_nic(shell) -> None:
    # טווח שידור לא-ייצורי (#156: טסט אינו נוקב בפורט ההפצה)
    proc = run(shell, "--per-nic", "--mcast-ports", "7000-7001", "--service", "ens33:monitor,interserver",
               "--service", "ens34:multicast,netboot", "--service", "ens35:console,monitor",
               "--service", "ens36:")
    assert proc.returncode == 0, proc.stderr
    got = rules(proc.stdout)
    assert 'iifname "ens35" tcp dport 8081 accept' in got
    assert not any('"ens33" tcp dport 8081' in r for r in got), "console off on ens33 = no rule"
    assert 'iifname "ens33" tcp dport 8443 accept' in got
    assert {'iifname "ens34" udp dport 67 accept', 'iifname "ens34" udp dport 69 accept',
            'iifname "ens34" tcp dport 8080 accept',
            'iifname "ens34" udp dport 7000-7001 accept'} <= set(got)
    assert not any('"ens36"' in r for r in got)
    assert "policy drop" in proc.stdout and "flush ruleset" in proc.stdout


@needs_bash
@pytest.mark.parametrize("shell", SHELLS)
def test_secondary_keeps_8443_to_the_primary_only(shell) -> None:
    proc = run(shell, "--per-nic", "--primary-ip", "10.10.10.1",
               "--service", "ens33:console,interserver")
    assert proc.returncode == 0, proc.stderr
    assert 'iifname "ens33" ip saddr 10.10.10.1 tcp dport 8443 accept' in rules(proc.stdout)


@needs_bash
@pytest.mark.parametrize("shell", SHELLS)
def test_no_service_anywhere_closes_everything_but_loopback(shell) -> None:
    proc = run(shell, "--per-nic")
    assert proc.returncode == 0, proc.stderr
    assert rules(proc.stdout) == ['iifname "lo" accept']


@needs_bash
@pytest.mark.parametrize("shell", SHELLS)
def test_proxy_1829_requires_both_proxy_mode_and_netboot_on_each_nic(shell) -> None:
    args = ("--per-nic", "--service", "ens33:console,interserver",
            "--service", "ens34:netboot", "--service", "ens35:netboot",
            "--service", "ens36:")
    proc = run(shell, *args, "--proxy-if", "ens34", "--proxy-if", "ens36")
    assert proc.returncode == 0, proc.stderr
    assert [r for r in rules(proc.stdout) if "dport 4011" in r] == [
        'iifname "ens34" udp dport 4011 accept']
    assert 'iifname "ens35" udp dport 69 accept' in rules(proc.stdout)
    off = run(shell, *args)
    assert off.returncode == 0, off.stderr
    assert "dport 4011" not in off.stdout


@needs_bash
@pytest.mark.parametrize("args", [
    ("--service", "ens33:console,ssh"),          # שירות לא מוכר — סירוב, לא השמטה
    ("--service", 'ens"33:console'),             # שם שובר ציטוט
    ("--service", "ens33"),                      # בלי ':'
])
def test_bad_service_specs_are_refused(args) -> None:
    proc = run(BASH, "--per-nic", *args)
    assert proc.returncode != 0 and proc.stdout == ""
