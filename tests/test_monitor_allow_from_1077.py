"""#1077: 5900 נפתח רק לכתובת השרת — ובלי כתובת שרת שאפשר לפרסר, לא עולה.

הטסט הקיים (`test_agent.py::test_monitor_does_not_start_without_a_server_ip`)
עבר גם כשהשער הוסר (בקרה רטרואקטיבית, 24/10): הוא לא כיבה את ההמתנה לגואי
(‏#1475), ולכן `monitor_start` הלך להמתין ברקע ולא קרא ל-spawn — ה"לא עלה"
שלו נבע מההמתנה, לא מהשער. כאן ההמתנה כבויה (`MONITOR_MIRROR_WAIT_S=0`),
כך שהדבר היחיד שעוצר את ה-spawn הוא השער של #1077, והראיה החיובית היא
ההודעה ביומן וקוד היציאה — לא היעדר קובץ.
"""

from __future__ import annotations

import subprocess

import pytest

from native import requires_native
from test_agent import AGENT, BASH, REPO, posix

pytestmark = requires_native(("bash", BASH))


def _start(tmp_path, server: str) -> tuple[int, str | None, str]:
    """`monitor_start` עם spawn מזויף: (קוד יציאה, ארגומנטי ה-spawn או None, היומן)."""
    out = tmp_path / "spawn-args"
    logf = tmp_path / "log"
    run = tmp_path / "run"
    run.mkdir()
    (run / "monitor.secret").write_text("00112233445566778899aabbccddeeff\n")
    script = (
        'OUT=' + repr(posix(out)) + '; LOGF=' + repr(posix(logf)) + '; '
        'export IMAGECTL_MONITOR=1 IMAGECTL_SERVER=' + repr(server) + '; '
        'export RUN_DIR=' + repr(posix(run)) + '; '
        'export D_ROLE=build IP=10.0.0.9 MONITOR_BIN=/bin/sh FB_DEV=/dev/null; '
        'export MONITOR_MIRROR_WAIT_S=0; '
        'log() { echo "$*" >> "$LOGF"; }; '
        '. ' + posix(AGENT) + '/lib/monitor.sh; '
        '_monitor_spawn() { echo "$@" > "$OUT"; _monitor_pid=4242; }; '
        'monitor_start')
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                          cwd=str(REPO), stdin=subprocess.DEVNULL, timeout=60)
    args = out.read_text(encoding="utf-8").strip() if out.exists() else None
    log = logf.read_text(encoding="utf-8") if logf.exists() else ""
    return proc.returncode, args, log


@pytest.mark.parametrize("server", [
    "",                                   # אין imagectl.server בשורת הקרנל
    "http://imagectl.college:8080",       # שם מארח — אין IPv4 להגביל אליו
])
def test_no_parsable_server_ip_means_no_monitor(tmp_path, server):
    rc, args, log = _start(tmp_path, server)
    assert args is None, f"המוניטור עלה בלי כתובת שרת להגביל אליה: {args}"
    assert rc == 1
    assert "cannot parse server IP" in log, log


def test_with_a_server_ip_the_monitor_starts_limited_to_it(tmp_path):
    """הצד החיובי — אותה הרצה עם כתובת: עולה, ו-5900 מוגבל אליה בלבד."""
    rc, args, log = _start(tmp_path, "http://10.0.0.1:8080")
    assert rc == 0, log
    assert args is not None, log
    words = args.split()
    assert words[words.index("--allow-from") + 1] == "10.0.0.1"
