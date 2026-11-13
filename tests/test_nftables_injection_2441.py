"""#2441: `--mcast-ports` ו-`--primary-ip` נבדקים בגלוב שלילי, כמו שאר הדגלים.

הגלוב החיובי `[0-9]*-[0-9]*` מאפשר ל-`*` לתפוס רווח ושורה חדשה, ולכן ערך עם
שורת כלל נוספת עבר ונכתב לחומת האש כמו שהוא (סקירת Fable, 04/11,
`06-generator-validation.sh`: `dport 8081` ו-`dport 22` על כרטיס ההפצה). מהרשת
זה לא נגיש היום — זה פער אימות. כאן: סירוב, וגם שהערכים התקינים עוברים.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from test_nftables_rules import BASH, GEN, PRIMARY_ARGS, generate, needs_bash

INJECT_MCAST = '39000-39001 accept\n\t\tiifname "eth-deploy" tcp dport 8081 accept #INJECTED'
INJECT_IP = "1.2.3.4 tcp dport 22 accept #INJECTED"


def _refused(flag: str, value: str) -> subprocess.CompletedProcess:
    """הערך עובר בסביבה ולא ב-argv: ב-Git Bash של ווינדוס argv נחתך בשורה חדשה
    ("39000-39001\n" מגיע כ-"39000-39001"), ואז טסט השורה החדשה לא בודק דבר."""
    return subprocess.run(
        [BASH, "-c", 'exec bash "$0" "$@" "$IMAGESRV_VALUE"', str(GEN), *PRIMARY_ARGS, flag],
        capture_output=True, text=True, encoding="utf-8", check=False,
        stdin=subprocess.DEVNULL, env={**os.environ, "IMAGESRV_VALUE": value})


@needs_bash
@pytest.mark.parametrize("value", [
    INJECT_MCAST, "39000-39001 39002-39003", "39000 -39001", "39000-39001\n", "1-2-3",
])
def test_mcast_ports_with_anything_but_digits_and_one_dash_is_refused(value):
    proc = _refused("--mcast-ports", value)
    assert proc.returncode != 0, proc.stdout
    assert "INJECTED" not in proc.stdout and "mcast-ports" in proc.stderr


@needs_bash
@pytest.mark.parametrize("value", [
    INJECT_IP, "10.0.0.1 ", "10.0.0.1\n", "10.0.0.1.5", "10.0.0.1;",
])
def test_primary_ip_with_anything_but_an_address_is_refused(value):
    proc = _refused("--primary-ip", value)
    assert proc.returncode != 0, proc.stdout
    assert "dport 22" not in proc.stdout and "primary-ip" in proc.stderr


@needs_bash
def test_valid_values_still_pass():
    """הצד החיובי — עובר גם בלי התיקון."""
    assert "39000-39001" in generate(*PRIMARY_ARGS, "--mcast-ports", "39000-39001")
    assert "10.10.10.8" in generate(*PRIMARY_ARGS, "--primary-ip", "10.10.10.8")
