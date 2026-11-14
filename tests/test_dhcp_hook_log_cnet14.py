"""C-NET-14 (מפת המגע, הסיכון הגבוה היחיד): כשל ב-hook של udhcpc נבלע.

`udhcpc.script` (`tools/build_initramfs.sh`) רץ בכל מכונה, וגם באמצע שחזור
(#1949: udhcpc נשאר ברקע ומחדש). `ip addr flush 2>/dev/null`, `ifconfig`
בלי בדיקה, `route add 2>/dev/null` ו-`exit 0` תמיד — והפלט של udhcpc עצמו
הולך ל-`/dev/null` ב-`agent/init`. `ifconfig` שנכשל אחרי flush השאיר את
הכרטיס בלי כתובת מתחת לשחזור, בלי אף שורה בשום מקום.

עכשיו כל כשל הוא שורה ב-`agent.log` עם ה-rc והפלט. ומה שלא משתנה:
ה-hook ממשיך ויוצא 0 (udhcpc לא פועל לפי הקוד), חידוש של אותה כתובת לא
נוגע בה (#1949), ו-renew שבו `route add` עונה "File exists" על נתיב קיים
אינו שורת כשל.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from native import requires_native
from test_agent import BASH, posix

pytestmark = requires_native(("bash", BASH))

BUILD = Path(__file__).resolve().parent.parent / "tools" / "build_initramfs.sh"


def _hook(resolv: Path) -> str:
    text = BUILD.read_text(encoding="utf-8")
    m = re.search(r"cat > \"\$ROOT/etc/imagesrv/udhcpc\.script\" << 'EOF'\n(.*?)\nEOF\n",
                  text, re.S)
    assert m, "the udhcpc hook heredoc moved -- update the extractor"
    return m.group(1).replace("/etc/resolv.conf", posix(resolv))


def run_hook(tmp_path: Path, event: str, *, current: str | None = None, fail: dict | None = None,
             default_route: bool = False, run_dir: Path | None = None,
             resolv: Path | None = None) -> tuple[subprocess.CompletedProcess, list[str], str]:
    """`fail` = {"ifconfig": (rc, stderr), "flush": ..., "link": ..., "route": ...}.
    מחזיר (התהליך, מה שהזיופים קיבלו, תוכן agent.log)."""
    fail = fail or {}
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    calls = tmp_path / "calls"
    calls.write_text("", encoding="utf-8")
    run_dir = tmp_path / "run" if run_dir is None else run_dir
    shown = tmp_path / "addr_show"
    shown.write_text(f"    inet {current}/24 brd 10.44.0.255 scope global eth0\n"
                     if current else "", encoding="utf-8", newline="\n")
    routes = tmp_path / "route_show"
    routes.write_text("default via 10.44.0.1 dev eth0 \n" if default_route else "",
                      encoding="utf-8", newline="\n")

    def failing(key: str) -> str:
        if key not in fail:
            return ""
        rc, err = fail[key]
        return f"echo {err!r} >&2; exit {rc}"

    stubs = {
        "ip": ("#!/bin/sh\n"
               f'echo "ip $*" >> {posix(calls)!r}\n'
               f'if [ "$1 $2 $3" = "-4 addr show" ]; then cat {posix(shown)!r}; exit 0; fi\n'
               f'if [ "$1 $2 $3" = "-4 route show" ]; then cat {posix(routes)!r}; exit 0; fi\n'
               f'case "$1 $2" in "addr flush") {failing("flush")} ;; "link set") {failing("link")} ;; esac\n'
               "exit 0\n"),
        "ifconfig": f'#!/bin/sh\necho "ifconfig $*" >> {posix(calls)!r}\n{failing("ifconfig")}\nexit 0\n',
        "route": f'#!/bin/sh\necho "route $*" >> {posix(calls)!r}\n{failing("route")}\nexit 0\n',
    }
    for name, body in stubs.items():
        path = bin_dir / name
        path.write_text(body, encoding="utf-8", newline="\n")
        path.chmod(0o755)
    hook = tmp_path / "udhcpc.script"
    hook.write_text(_hook(resolv or tmp_path / "resolv.conf"), encoding="utf-8", newline="\n")
    env = {**os.environ, "PATH": f"{posix(bin_dir)}:/usr/bin:/bin", "RUN_DIR": posix(run_dir),
           "interface": "eth0", "ip": "10.44.0.161", "subnet": "255.255.255.0",
           "router": "10.44.0.1", "dns": "10.44.0.1"}
    proc = subprocess.run([BASH, posix(hook), event], env=env, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=30)
    log = run_dir / "agent.log"
    return (proc, calls.read_text(encoding="utf-8").splitlines(),
            log.read_text(encoding="utf-8") if log.exists() else "")


def _run_dir(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    return run


def test_a_failed_ifconfig_on_a_new_address_is_a_line_in_the_agent_log(tmp_path):
    """הנתיב של הסיכון: renew עם כתובת אחרת, flush עבר ו-ifconfig נכשל — הכרטיס
    בלי כתובת. היום: שום שורה. עכשיו: מה נכשל, ה-rc והפלט."""
    _run_dir(tmp_path)
    proc, calls, log = run_hook(tmp_path, "renew", current="10.44.0.77",
                                fail={"ifconfig": (1, "SIOCSIFADDR: Cannot assign")})
    assert proc.returncode == 0, proc.stderr
    assert "ifconfig eth0 10.44.0.161 netmask 255.255.255.0" in calls, calls
    line = [ln for ln in log.splitlines() if "FAILED" in ln and "ifconfig" in ln]
    assert line, log
    assert "rc=1" in line[0] and "SIOCSIFADDR: Cannot assign" in line[0], line
    assert "dhcp renew eth0" in line[0], line


def test_a_failed_flush_is_said_and_the_address_is_still_set(tmp_path):
    _run_dir(tmp_path)
    proc, calls, log = run_hook(tmp_path, "bound", fail={"flush": (2, "RTNETLINK answers: busy")})
    assert proc.returncode == 0, proc.stderr
    assert "ifconfig eth0 10.44.0.161 netmask 255.255.255.0" in calls, calls
    assert re.search(r"FAILED \(rc=2\): ip addr flush dev eth0 -- RTNETLINK answers: busy", log), log


def test_a_failed_link_up_on_deconfig_is_said(tmp_path):
    _run_dir(tmp_path)
    proc, _calls, log = run_hook(tmp_path, "deconfig", current="10.44.0.161",
                                 fail={"link": (1, "Cannot find device")})
    assert proc.returncode == 0, proc.stderr
    assert re.search(r"dhcp deconfig eth0: FAILED \(rc=1\): ip link set eth0 up", log), log


def test_a_default_route_that_was_not_added_is_said(tmp_path):
    _run_dir(tmp_path)
    proc, _calls, log = run_hook(tmp_path, "bound",
                                 fail={"route": (1, "route: SIOCADDRT: Network unreachable")})
    assert proc.returncode == 0, proc.stderr
    assert "FAILED: route add default gw 10.44.0.1 -- route: SIOCADDRT: Network unreachable" in log, log


def test_a_renewal_whose_route_is_already_there_is_not_a_failure(tmp_path):
    """שומר נגד הצפה: כל renew של אותה חכירה מקבל "File exists" מ-route add —
    הנתיב קיים. זה אינו כשל, ואין שורה (ואין שורת "כתובת" — היא כבר שלנו)."""
    _run_dir(tmp_path)
    proc, calls, log = run_hook(tmp_path, "renew", current="10.44.0.161", default_route=True,
                                fail={"route": (1, "route: SIOCADDRT: File exists")})
    assert proc.returncode == 0, proc.stderr
    assert not [c for c in calls if c.startswith(("ip addr flush", "ifconfig"))], calls
    assert log == "", log


def test_a_new_address_is_one_line(tmp_path):
    """הראיה החיובית: הכתובת שהוגדרה נרשמת פעם אחת, ובלי FAILED."""
    _run_dir(tmp_path)
    proc, _calls, log = run_hook(tmp_path, "bound")
    assert proc.returncode == 0, proc.stderr
    lines = log.splitlines()
    assert len(lines) == 1 and "address 10.44.0.161/255.255.255.0" in lines[0], lines
    assert "FAILED" not in log


def test_a_log_that_cannot_be_written_never_stops_the_address(tmp_path):
    """#1949 לא נשבר: אין /run/imagesrv (או שהוא לא ניתן לכתיבה) — ה-hook עדיין
    מגדיר כתובת, נתיב ו-DNS, ויוצא 0."""
    resolv = tmp_path / "resolv.conf"
    proc, calls, log = run_hook(tmp_path, "bound", run_dir=tmp_path / "no" / "such" / "dir",
                                fail={"ifconfig": (1, "boom")}, resolv=resolv)
    assert proc.returncode == 0, proc.stderr
    assert "ifconfig eth0 10.44.0.161 netmask 255.255.255.0" in calls, calls
    assert "route add default gw 10.44.0.1" in calls, calls
    assert resolv.read_text(encoding="utf-8") == "nameserver 10.44.0.1\n"
    assert log == ""


def test_an_unwritable_resolv_conf_is_said(tmp_path):
    _run_dir(tmp_path)
    proc, _calls, log = run_hook(tmp_path, "bound", resolv=tmp_path / "no-such-dir" / "resolv.conf")
    assert proc.returncode == 0, proc.stderr
    assert "resolv.conf not written" in log, log
