"""‏#2039: ‏`udp-receiver` מצטרף לזרם על הכרטיס שקיבל DHCP — לא על ברירת המחדל.

‏`udp-sender` של ההפצה הישירה כבר קיבל `--interface "$IFACE"`
(‏`agent/lib/directsend.sh`), אבל המקלט (‏`stream_source`, ‏`agent/lib/restore.sh`)
— שמשמש גם את התחנה (‏`restore_partition`) וגם את המגירות (‏`drawers.sh`) —
נקרא בלי הדגל, ו-udpcast בחר כרטיס בעצמו. במחשב עם שני כרטיסי רשת (מחשבי
הבנייה במכללה) ההצטרפות לקבוצת ה-multicast יכלה לקרות על הכרטיס הלא נכון.

‏`$IFACE` נקבע ב-`agent/init`: הכרטיס הראשון עם carrier ש-`udhcpc` קיבל
עליו חכירה, נכתב ל-`net.conf` ונטען בסוכן. הטסטים בודקים את ה-argv של
המקלט, ואת המסלול מלולאת ה-DHCP של init ועד הדגל.
"""

from __future__ import annotations

import subprocess

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_init_network import _make_net, _network_block, udhcpc_prelude

pytestmark = requires_native(("bash", BASH))

EMPTY_LINE = "udp-receiver: IFACE is empty"


def _receiver_stub(stub_dir, argv) -> str:
    """‏udp-receiver מזויף שרושם את ה-argv שלו ולא כותב דבר ל-stdout. נכתב
    מתוך bash (קובץ שנוצר מווינדוס אינו בר-הרצה ב-Git Bash)."""
    return (f"mkdir -p {posix(stub_dir)!r}\n"
            f"cat > {posix(stub_dir)}/udp-receiver <<'STUB'\n"
            "#!/bin/sh\n"
            f'printf "%s\\n" "$@" > {posix(argv)!r}\n'
            "STUB\n"
            f"chmod 0755 {posix(stub_dir)}/udp-receiver\n"
            f'export PATH="$(cd {posix(stub_dir)!r} && pwd):$PATH"\n')


def _libs(tmp_path) -> str:
    return (f"export RUN_DIR={posix(tmp_path)!r} LOG_FILE={posix(tmp_path / 'agent.log')!r}\n"
            f". {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/waits.sh; "
            f". {posix(AGENT)}/lib/restore.sh\n")


def _receive(tmp_path, before: str):
    """מריץ `stream_source multicast` ומחזיר (argv, stdout של הזרם, הלוג)."""
    argv = tmp_path / "udp.argv"
    script = ('export PATH="/usr/bin:$PATH"\n' + _receiver_stub(tmp_path / "stubs", argv)
              + before + "\n" + _libs(tmp_path)
              + "stream_source multicast http://s img_1 p1.zst 600 > \"$RUN_DIR/stream.out\"\n")
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                          encoding="utf-8", stdin=subprocess.DEVNULL, timeout=60)
    assert argv.exists(), proc.stdout + proc.stderr
    log = tmp_path / "agent.log"
    return (argv.read_text(encoding="utf-8").split(),
            (tmp_path / "stream.out").read_text(encoding="utf-8"),
            log.read_text(encoding="utf-8") if log.exists() else "")


def test_the_receiver_joins_on_the_nic_dhcp_answered_on(tmp_path):
    args, stream, log = _receive(tmp_path, "IFACE=enp3s0")
    assert "--interface" in args, args
    assert args[args.index("--interface") + 1] == "enp3s0", args
    assert EMPTY_LINE not in log
    assert stream == ""


def test_without_iface_no_flag_and_the_log_says_so(tmp_path):
    """‏`IFACE` ריק: ההתנהגות של לפני #2039 (בלי הדגל — ‏`--interface ""` היה
    נכשל), ושורה ביומן. השורה לא נכתבת ל-stdout: שם עובר הזרם עצמו."""
    args, stream, log = _receive(tmp_path, "unset IFACE")
    assert "--interface" not in args, args
    assert "" not in args, args   # no empty word where the interface would be
    assert args[:3] == ["--nokbd", "--portbase", "9000"], args   # פורט-הפצה-מכוון: ברירת המחדל (restore.sh)
    assert EMPTY_LINE in log, log
    assert stream == "", f"the log line went into the stream: {stream!r}"


def _init_then_receive(tmp_path, carriers: dict[str, int], ok_ifs: str):
    """לולאת ה-DHCP האמיתית של `agent/init` על /sys/class/net מזויף, ואחריה
    המקלט עם ה-`IFACE` שהיא בחרה — אותו משתנה ש-net.conf מעביר לסוכן."""
    net = tmp_path / "net"
    _make_net(net, carriers)
    uptime = tmp_path / "uptime"
    uptime.write_text("12.34 50.00\n", encoding="utf-8", newline="\n")
    dhcp_log = tmp_path / "dhcp.log"
    init_block = (udhcpc_prelude(net, dhcp_log, ok_ifs)   # #2040: probe + server-id
                  + 'fail() { echo "FAIL: $1"; exit 3; }\n'
                  + _network_block(net, uptime))
    return _receive(tmp_path, init_block)


def test_two_nics_the_receiver_gets_the_one_with_the_lease(tmp_path):
    """שני כרטיסים עם קישור; רק השני ברשת שיש בה DHCP של השרת. המקלט מקבל
    את השני — לא את הראשון ברשימה, שהוא מה ש-udpcast עלול לבחור לבד."""
    args, _, log = _init_then_receive(tmp_path, {"lo": 1, "eth0": 1, "eth1": 1}, "eth1")
    assert "--interface" in args, args
    assert args[args.index("--interface") + 1] == "eth1", args
    assert EMPTY_LINE not in log


def test_two_nics_one_without_a_cable_the_receiver_gets_the_cabled_one(tmp_path):
    args, _, _ = _init_then_receive(tmp_path, {"lo": 1, "eth0": 0, "eth1": 1}, "eth0 eth1")
    assert "--interface" in args, args
    assert args[args.index("--interface") + 1] == "eth1", args
