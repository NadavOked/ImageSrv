"""‏#1949 — חכירת DHCP שפגה באמצע סבב, והצפת `identity_unverifiable` ביומן.

‏R2 (מעבדה, ‏22/10): ‏VCLONE-2 רץ יותר מ-12 שעות על חכירה אחת. הסוכן הריץ
‏`udhcpc -q` — חכירה אחת ויציאה — ולכן איש לא חידש אותה. ‏dnsmasq מחק אותה
מהקובץ כשפגה, שומר הזהות (#855) סירב לכל דיווח (`no DHCP lease for this
mac`), והמכונה סומנה `client_lost`. ‏346 שורות סירוב ב-10 דקות.

שני תיקונים, ושומר הזהות עצמו **לא** משתנה:
1. ‏`udhcpc` בלי `-q`: נשאר ברקע ומחדש ב-T1. ה-hook לא מוריד כתובת שכבר
   שלנו — ‏`renew` באמצע שחזור אינו `flush`.
2. סירוב זהה נרשם ביומן פעם אחת ב-`IDENTITY_JOURNAL_WINDOW_S`, עם מונה.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from conftest import setup_classroom
from native import requires_native
from test_agent import BASH, posix
from test_init_network import _make_net, _run
from test_machine_identity import hello, identity_server, journal_details  # noqa: F401

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "tools" / "build_initramfs.sh"


# --- הסוכן: udhcpc נשאר ומחדש ------------------------------------------------

@requires_native(("bash", BASH))
def test_the_lease_holder_stays_resident_to_renew(tmp_path):
    """‏busybox udhcpc: ‏`-q` = "Exit after obtaining lease" — ואז אין מי
    שיחדש. בלי `-q` הוא עובר לרקע אחרי `bound` ומחדש ב-T1 (נמדד על busybox
    1.37 ב-netns, ‏22/10). ‏`-n` נשאר: כרטיס בלי DHCP עדיין מוותר ועובר הלאה.
    ‏#2040: לפני המחזיק יש בדיקה חד-פעמית (‏`-q`, ‏hook שרק רושם) — היא אינה
    מחזיקה חכירה; המחזיק הוא הקריאה עם `udhcpc.script`, אחת, על הכרטיס שנבחר."""
    net = tmp_path / "net"
    _make_net(net, {"lo": 1, "eth0": 1, "eth1": 1})
    proc, _, log = _run(net, "eth1")
    assert proc.returncode == 0, proc.stdout
    calls = [line.split() for line in
             Path(f"{log}.args").read_text(encoding="utf-8").splitlines()]
    assert [c[1] for c in calls] == ["eth0", "eth1", "eth1"]
    holders = [c for c in calls if "/etc/imagesrv/udhcpc.script" in c]
    assert [c[1] for c in holders] == ["eth1"], calls
    for call in holders:
        assert "-q" not in call, f"udhcpc יוצא אחרי החכירה ואינו מחדש אותה: {call}"
    for call in calls:
        assert "-n" in call, call


def _hook_text(resolv: Path) -> str:
    text = BUILD.read_text(encoding="utf-8")
    m = re.search(r"cat > \"\$ROOT/etc/imagesrv/udhcpc\.script\" << 'EOF'\n(.*?)\nEOF\n",
                  text, re.S)
    assert m, "the udhcpc hook heredoc moved -- update the extractor"
    return m.group(1).replace("/etc/resolv.conf", posix(resolv))


def _run_hook(tmp_path: Path, event: str, ip: str, current: str | None,
              show_fails: bool = False) -> list[str]:
    """מריץ את ה-hook האמיתי עם `ip`/`ifconfig`/`route` מזויפים. ‏`current` —
    הכתובת שכבר על הכרטיס (‏None = אין). מחזיר את מה שהזיופים קיבלו."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    calls = tmp_path / "calls"
    calls.write_text("", encoding="utf-8")
    shown = tmp_path / "addr_show"
    shown.write_text(f"    inet {current}/24 brd 10.44.0.255 scope global eth0\n"
                     if current else "", encoding="utf-8", newline="\n")
    show = "exit 1" if show_fails else f"cat {posix(shown)!r}; exit 0"
    stubs = {
        "ip": ("#!/bin/sh\n"
               'if [ "$1 $2 $3" = "-4 addr show" ]; then ' + show + "; fi\n"
               f'echo "ip $*" >> {posix(calls)!r}\n'),
        "ifconfig": f'#!/bin/sh\necho "ifconfig $*" >> {posix(calls)!r}\n',
        "route": f'#!/bin/sh\necho "route $*" >> {posix(calls)!r}\n',
    }
    for name, body in stubs.items():
        path = bin_dir / name
        path.write_text(body, encoding="utf-8", newline="\n")
        path.chmod(0o755)
    hook = tmp_path / "udhcpc.script"
    hook.write_text(_hook_text(tmp_path / "resolv.conf"), encoding="utf-8", newline="\n")
    env = {**os.environ, "PATH": f"{posix(bin_dir)}:/usr/bin:/bin", "interface": "eth0",
           "ip": ip, "subnet": "255.255.255.0", "router": "10.44.0.1", "dns": "10.44.0.1"}
    proc = subprocess.run([BASH, posix(hook), event], env=env, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return calls.read_text(encoding="utf-8").splitlines()


@requires_native(("bash", BASH))
def test_a_renewal_of_our_own_address_does_not_take_it_down(tmp_path):
    """‏`renew` מגיע באמצע שחזור (כל 6 שעות על חכירה של 12). ‏`flush` מוריד את
    הכתובת ואת הנתיבים מתחת לזרם ולדיווחים — על אותה כתובת אין מה להחליף."""
    calls = _run_hook(tmp_path, "renew", "10.44.0.161", current="10.44.0.161")
    assert not [c for c in calls if c.startswith(("ip addr flush", "ifconfig"))], calls


@requires_native(("bash", BASH))
def test_a_lost_lease_keeps_the_address(tmp_path):
    """‏`deconfig` של udhcpc תושב: חכירה שלא חודשה עד הסוף. הכתובת נשארת —
    כמו כש-udhcpc יצא (`-q`) — וה-`bound` הבא מחליף אותה אם השתנתה."""
    calls = _run_hook(tmp_path, "deconfig", "", current="10.44.0.161")
    assert "ip link set eth0 up" in calls
    assert not [c for c in calls if c.startswith("ip addr flush")], calls


@requires_native(("bash", BASH))
@pytest.mark.parametrize("current, show_fails", [(None, False), ("10.44.0.77", False),
                                                 ("10.44.0.161", True)])
def test_a_new_or_unknown_address_is_still_configured(tmp_path, current, show_fails):
    """שומר: כרטיס בלי כתובת, עם כתובת אחרת, או ש-`ip addr show` נכשל עליו
    (לא נבדק ≠ כבר שלנו) — ‏flush ו-ifconfig, כמו לפני."""
    calls = _run_hook(tmp_path, "bound", "10.44.0.161", current=current, show_fails=show_fails)
    assert "ip addr flush dev eth0" in calls, calls
    assert "ifconfig eth0 10.44.0.161 netmask 255.255.255.0" in calls, calls


# --- השרת: סירוב זהה נרשם פעם אחת בחלון ---------------------------------------

def test_a_repeated_refusal_is_journaled_once_per_window_with_a_count(
        identity_server, monkeypatch):  # noqa: F811
    """‏R2: שתי שורות כל 2 שניות. כל בקשה עדיין מסורבת (403) — רק הרישום
    מרוסן — והשורה שאחרי החלון אומרת כמה נבלעו."""
    from server.agent import api

    ids = setup_classroom(identity_server)
    for _ in range(5):
        assert hello(identity_server["station"], ids["mac2"]).status_code == 403
    rows = journal_details(identity_server, "identity_unverifiable")
    assert len(rows) == 1, rows
    assert "+" not in rows[0][1]

    now = api._clock()
    monkeypatch.setattr(api, "_clock", lambda: now + api.IDENTITY_JOURNAL_WINDOW_S + 1)
    assert hello(identity_server["station"], ids["mac2"]).status_code == 403
    rows = journal_details(identity_server, "identity_unverifiable")
    assert len(rows) == 2, rows
    assert "[+4 identical refusals in the last" in rows[1][1], rows[1][1]


def test_a_different_refusal_is_journaled_at_once(identity_server):  # noqa: F811
    """שומר: הריסון הוא לכל (אירוע, MAC/כתובת, סיבה). מכונה אחרת, או אותה
    מכונה מכתובת זרה, נרשמות מיד — התחזות אינה נבלעת בצל של סירוב ישן."""
    ids = setup_classroom(identity_server)
    assert hello(identity_server["station"], ids["mac2"]).status_code == 403
    assert hello(identity_server["stranger"], ids["mac2"]).status_code == 403
    assert hello(identity_server["stranger"], ids["mac1"]).status_code == 403
    assert len(journal_details(identity_server, "identity_unverifiable")) == 2
    assert len(journal_details(identity_server, "identity_refused")) == 1
