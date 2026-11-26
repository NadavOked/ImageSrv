"""#2874 (נדב 23/11): בסבב פעיל מסך צפייה-בלבד אינו מתנתק אחרי 10 דקות בלי מגע; בלי סבב הניתוק הרגיל נשאר.

מה שהקונסולה עושה בדפדפן נבדק ב-`view_only_keepalive_2874.test.cjs` (node), והטסט הראשון כאן מריץ אותו, כדי
שהתור יראה אותו. השרת לא משתנה, והטסט השני נועל את מה שהפתרון נשען עליו: `GET /room` הוא פעילות
(לא `poll` של המוניטור, #2493), ולכן אות אחד בדקה מחזיק את העוגייה, ובלעדיו היא מתבטלת כרגיל (#2414).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from native import requires_native
from server.console import auth
from test_user_hardening_2414 import _idle_limit, idle_clock  # noqa: F401

NODE = shutil.which("node")
ROOT = Path(__file__).resolve().parent.parent
ROOM = "/api/console/room"


@requires_native(("node", NODE))
def test_the_console_keeps_a_view_only_session_alive_and_logs_out_without_a_round():
    got = subprocess.run([NODE, "--test", "tests/view_only_keepalive_2874.test.cjs"], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL,
                         timeout=120)
    assert got.returncode == 0, got.stdout[-3000:] + got.stderr[-1000:]
    assert "fail 0" in got.stdout and "pass 6" in got.stdout, got.stdout[-1500:]


def test_a_room_read_every_minute_keeps_the_cookie_and_silence_loses_it(server, idle_clock):
    admin = server["admin"]
    assert admin.get(ROOM).status_code == 200
    # ארבעה גבולות של חוסר פעילות, ואות אחד בדקה: העוגייה חיה
    for _ in range(4 * (_idle_limit(server) // 60)):
        idle_clock["now"] += 60
        assert admin.get(ROOM).status_code == 200, "the room read did not count as activity"
    assert admin.get("/api/console/me").status_code == 200
    # אותה עוגייה בלי אות: מתבטלת, ואז גם /room ו-/me נדחים
    idle_clock["now"] += _idle_limit(server) + 1
    assert admin.get(ROOM).status_code == 401
    assert admin.get("/api/console/me").status_code == 401


def test_the_room_read_is_not_a_monitor_poll():
    class Conn:
        def __init__(self, method, path):
            self.scope = {"method": method, "path": path}
    assert not auth._is_poll(Conn("GET", ROOM))
