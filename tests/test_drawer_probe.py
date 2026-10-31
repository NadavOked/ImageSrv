"""‏#402 — `מגירות=0` אסור שיאמר שני דברים הפוכים.

מחשב שיכפול חסר-ראש עלה, הגיע לסוכן, ודיווח `מגירות=0`. נדב חיבר שישה
דיסקים והמספר נשאר `0`. רק אחרי כניסת SSH ידנית התברר שבקר ה-SATA
מדווח `0/6 ports implemented (port mask 0x0)` — **אף פורט אינו מופעל
בקושחה**, ואין דיסק שאפשר לחבר כדי לפתור זאת.

`0` היה אותה תשובה לשני מצבים שהפעולה עליהם הפוכה: "לא חוברו דיסקים"
(לחבר דיסקים) מול "אין פורטי SATA" (לגעת בביוס). זו בדיוק משפחת
עיקרון 5 — רשימה ריקה שנקראת כתשובה שלילית תקפה בשתי הסיבות.

הראיה שמפרידה בין השניים כבר קיימת בקרנל (`dmesg`), היא פשוט נזרקה.
`disk_probe` מחזיר אותה ל-hello:

  drives     — נמצא לפחות דיסק אחד (הזרימה הרגילה).
  no_disks   — אין דיסקים, אבל **יש** פורטי SATA מופעלים: חברו כונן.
  no_ports   — אין דיסקים ו**אף פורט אינו מופעל**: הקושחה, לא הכבל.
  unchecked  — לא נמצאה שורת ahci: לא הצלחנו לספור (עיקרון 5), לא "אפס".

הטסטים מריצים את `disk_probe` ו-`build_hello` **האמיתיים** דרך bash,
עם `dmesg` מזויף על ה-PATH — כמו שהם רצים ב-initramfs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix, sh

pytestmark = requires_native(("bash", BASH))


def _dmesg_stub(bindir: str, body: str) -> str:
    """קטע bash שכותב `dmesg` מזויף ומוסיף אותו ל-PATH.

    הזיוף חייב להיכתב **מתוך bash** (heredoc + chmod): קובץ שנוצר
    מווינדוס אינו נחשב בר-הרצה ב-Git Bash, וזה בדיוק מה ש-`make_stubs`
    עושה בשאר בדיקות הסוכן.
    """
    return (
        f"mkdir -p {bindir!r}\n"
        f"cat > {bindir}/dmesg <<'STUB'\n#!/bin/sh\n{body}\nSTUB\n"
        f"chmod 0755 {bindir}/dmesg\n"
        # ‏$(cd && pwd) הופך `C:/...` ל-`/c/...` — חיפוש PATH ב-Git Bash
        # לא מוצא רשומה בסגנון אות-כונן, בדיוק כמו ב-make_stubs.
        f'export PATH="$(cd {bindir!r} && pwd):$PATH"\n'
    )


def _empty_sysroot(tmp_path: Path) -> Path:
    """עץ /sys ריק מדיסקים — sys/block קיים אך בלי כונן אמיתי."""
    sysroot = tmp_path / "root"
    blk = sysroot / "sys/block"
    blk.mkdir(parents=True)
    (blk / "loop0").mkdir()          # חייב להיות מסונן ואינו כונן
    return sysroot


def probe(sysroot: Path, dmesg_body: str, bindir: Path) -> str:
    return sh(
        _dmesg_stub(posix(bindir), dmesg_body)
        + f'export SYSROOT={posix(sysroot)!r}; '
        f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/hw/sysinfo.sh; '
        f'disk_probe'
    ).strip()


#: השורה האמיתית מהברזל (#402), 05/09: אפס פורטים מתוך שישה.
NO_PORTS = "echo '[    1.234] ahci 0000:00:1f.2: 0/6 ports implemented (port mask 0x0)'"
#: אותו בקר, פורטים מופעלים — פשוט אין כונן מחובר.
PORTS_OK = "echo '[    1.234] ahci 0000:00:1f.2: 6/6 ports implemented (port mask 0x3f)'"
#: ‏dmesg שרץ אך בלי שורת ahci כלל — הראיה נעדרת, לא שלילית.
NO_AHCI = "echo '[    0.100] Linux version 6.1.0'"


def test_zero_drawers_from_disabled_ports_is_not_zero_drawers_from_no_disks(tmp_path):
    """הלב של #402: שני ה-`0` **חייבים** להיבדל.

    אותו עץ /sys בדיוק (אפס דיסקים), רק שורת ה-`dmesg` שונה. לפני
    התיקון `disk_probe` לא היה קיים והשניים היו בלתי-נבדלים.
    """
    sysroot = _empty_sysroot(tmp_path)
    disabled = probe(sysroot, NO_PORTS, tmp_path / "a")
    empty = probe(sysroot, PORTS_OK, tmp_path / "b")

    assert disabled == "no_ports", disabled
    assert empty == "no_disks", empty
    assert disabled != empty


def test_no_ahci_line_is_unchecked_not_no_disks(tmp_path):
    """‏dmesg בלי שורת ahci = "לא הצלחנו לספור", לא "אפס דיסקים" (עיקרון 5).

    ‏`unchecked` נבדל גם מ-`no_disks` וגם מ-`no_ports`: היעדר ראיה אינו
    ראיה שלילית.
    """
    sysroot = _empty_sysroot(tmp_path)
    got = probe(sysroot, NO_AHCI, tmp_path / "c")
    assert got == "unchecked", got


def test_a_disk_present_reports_drives_and_never_consults_dmesg(tmp_path):
    """רדיוס הפגיעה: כשיש דיסק, הזרימה הרגילה — ו-`dmesg` לא נקרא כלל.

    ‏`dmesg` המזויף כאן **נכשל** אם הורץ; מכונה עם כונן חייבת להחזיר
    `drives` בלי לגעת בו (הנתיב החם של כל תחנה רגילה).
    """
    sysroot = _empty_sysroot(tmp_path)
    blk = sysroot / "sys/block/sda"
    blk.mkdir(parents=True)
    (blk / "removable").write_text("0\n")
    assert probe(sysroot, "echo BOOM >&2\nexit 3", tmp_path / "d") == "drives"


def _hello(sysroot: Path, dev: Path, bindir: Path, dmesg_body: str) -> dict:
    out = sh(
        _dmesg_stub(posix(bindir), dmesg_body)
        + f'export SYSROOT={posix(sysroot)!r} DEVROOT={posix(dev)!r} '
        f'RUN_DIR={posix(sysroot)!r} IFACE=eth0 IP=10.44.12.9; '
        f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/hw/sysinfo.sh; '
        f'build_hello'
    )
    return json.loads(out)


def test_hello_carries_the_distinction(tmp_path):
    """ממשק 2: ה-hello **נושא** את ההבחנה, לא רק מספר.

    מכונה מינימלית בלי דיסקים, עם `dmesg` שמדווח אפס פורטים — ה-hello
    שלה חייב לשאת `disk_probe: no_ports`, כדי שהקונסולה תוכל לומר
    למפעיל לגעת בקושחה ולא לחפש כבל.
    """
    sysroot = tmp_path / "root"
    net = sysroot / "sys/class/net/eth0"
    net.mkdir(parents=True)
    (net / "address").write_text("b4:2e:99:07:1a:c4\n")
    (sysroot / "proc").mkdir()
    (sysroot / "proc/meminfo").write_text("MemTotal:        8388608 kB\n")
    (sysroot / "sys/block").mkdir(parents=True)
    dev = tmp_path / "dev"
    dev.mkdir()

    hello = _hello(sysroot, dev, tmp_path / "h", NO_PORTS)
    assert hello["disks"] == []
    assert hello["disk_probe"] == "no_ports", hello.get("disk_probe")
