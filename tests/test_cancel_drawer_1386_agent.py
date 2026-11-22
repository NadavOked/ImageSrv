"""#1386 "בטל דיסק זה" באמצע גל — צד הסוכן.

מגירה אחת יוצאת מזרם חי, והשאר ממשיכות. המנגנון של #1603: ה-zstd של
המגירה נהרג, ה-fifo מאבד את הקורא, fanout מקבל EPIPE ומפיל רק אותה. אחר
כך טבלת המחיצות נמחקת (ראשית וגיבוי — `sgdisk --zap-all`), והמגירה מסומנת
`cancelled` — לא `failed`: בלי ראיית ATA, ולכן בלי disk_failure בשרת, ובסבב
הבא היא ירוקה.

#3173: הביטול באמצע הזרם של המקלט הישן (fanout האמיתי, מגירה איטית) יצא עם המקלט הישן; בגל
החתיכות הוא test_chunk_drawer_cancel_back_2533.py. כאן נשאר מה שאינו תלוי במנוע: הבקשה מהשרת,
מצב המכונה, המסך והטעינה.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from test_agent import AGENT, BASH, posix
from test_cloner_gui import bridge_setup, sh
from test_timeouts import log_of
from agentlib import agent_sources

JQ = shutil.which("jq")
requires_jq = pytest.mark.skipif(JQ is None, reason="jq אינו מותקן — הביטוי לא היה רץ בכלל")

LIBS = ("common", "sysinfo", "diskinfo", "waits", "jsonq", "progress", "restore", "writeback", "drawers", "verdict",
        "cancel", "failmark", "ntfsboot")


def target(run: Path, dev: str, name: str) -> str:
    path = run / "targets" / dev / name
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def _agent(tmp_path: Path, script: str) -> subprocess.CompletedProcess:
    run = tmp_path / "run"
    for d, st in (("sda", "writing"), ("sdb", "writing"), ("sdc", "done")):
        (run / "targets" / d).mkdir(parents=True, exist_ok=True)
        (run / "targets" / d / "state").write_text(st + "\n")
    libs = agent_sources(*LIBS)   # #2272: imagesrv-agent's order
    return subprocess.run([BASH, "-c", f"export RUN_DIR={posix(run)!r}; {libs}{script}"],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)


@requires_jq
def test_the_progress_answer_asks_only_drawers_that_are_writing(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "progress.reply").write_text(
        '{"ok":true,"cancel":[{"dev":"sdb","who_he":"על ידי nadav"},'
        '{"dev":"sdc","who_he":"x"},{"dev":"../sda","who_he":"x"}]}', encoding="utf-8")
    out = _agent(tmp_path, "cancel_from_reply; cancel_from_reply; echo rc=$?")
    assert "rc=0" in out.stdout, out.stderr
    assert target(run, "sdb", "cancel") == "על ידי nadav"
    assert not (run / "targets" / "sdc" / "cancel").exists(), "a done drawer has nothing to cancel"
    assert not (run / "targets" / "sda" / "cancel").exists()
    assert log_of(run).count("cancel asked") == 1, "asked once, not every two seconds"


def test_the_machine_state_counts_a_cancel_as_neither_written_nor_failed(tmp_path):
    cases = {("done", "cancelled"): "done", ("cancelled", "cancelled"): "partial",
             ("failed", "cancelled"): "failed", ("done", "failed"): "partial"}
    for (a, b), want in cases.items():
        script = (f'echo {a} > "$RUN_DIR/targets/sda/state"; echo {b} > "$RUN_DIR/targets/sdb/state"; '
                  '_machine_state "sda sdb"; _any_alive "sda sdb"; echo alive=$?')
        out = _agent(tmp_path, script).stdout.split()
        assert out[0] == want, (a, b, out)
        assert out[1] == f"alive={0 if 'done' in (a, b) else 1}", (a, b, out)


def test_the_cloner_screen_asks_without_a_sign_in_and_only_on_the_cloner(tmp_path):
    setup, gui = bridge_setup(tmp_path, "cloner")
    run = tmp_path / "run"
    (run / "targets" / "sdb").mkdir(parents=True)
    (run / "targets" / "sdb" / "state").write_text("writing\n")
    setup += f". {posix(AGENT)}/lib/restore/cancel.sh; "
    out = sh(setup + 'token="drawer-cancel|sdb"; gui_dispatch; echo "rc=$?"; gui_dispatch; echo "rc=$?"')
    assert out.stdout.split() == ["rc=0", "rc=0"], out.stderr   # asked again = 0, never the handoff's 2
    assert target(run, "sdb", "cancel") == "ממסך מחשב השיכפול"
    assert "GUI_ROLE_CALLED" not in out.stderr
    (run / "targets" / "sdb" / "cancel").unlink()
    (tmp_path / "b").mkdir()
    setup2, _ = bridge_setup(tmp_path / "b", "build")
    out = sh(setup2 + f'export RUN_DIR={posix(run)!r}; . {posix(AGENT)}/lib/restore/cancel.sh; '
             'token="drawer-cancel|sdb"; gui_dispatch; echo "rc=$?"')
    assert "rc=1" in out.stdout and not (run / "targets" / "sdb" / "cancel").exists()


def test_the_libs_load_cancel_sh_where_its_writers_run():
    agent = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    bridge = (AGENT / "lib" / "gui" / "guibridge.sh").read_text(encoding="utf-8")
    assert '. "$LIB_DIR/cancel.sh"' in agent, "the progress loop's reader"
    libs = bridge[bridge.index("gui_libs() {"):]
    assert '. "$LIB_DIR/cancel.sh"' in libs[:libs.index("\n}\n")], "the cloner screen's writer"


@requires_jq
def test_the_build_machine_posts_the_cancel_with_its_session(tmp_path):
    """X / "בטל…" במחשב הבנייה: אותו POST כמו הקונסולה, בסשן המחובר (role
    admin/deploy — `room_operator` בשרת). dev פגום — לא נשלח כלום; 409 — הסיבה
    של השרת על המסך."""
    setup, gui = bridge_setup(tmp_path, "build")
    run = tmp_path / "run"
    stub = ('gui_role() { GUI_ROLE=deploy; return 0; }; '
            'console_post() { echo "$1" >> "$RUN_DIR/posted"; cp "$2" "$RUN_DIR/body.json"; '
            '  printf "%s" "${REPLY_BODY:-{\\"ok\\":true}}" > "$3"; echo "${REPLY_CODE:-200}"; }; ')
    out = sh(setup + stub + 'token=room-drawer-cancel; cloner=aa:bb:cc:00:00:21; dev=sdb; gui_dispatch; echo "rc=$?"')
    assert "rc=0" in out.stdout, out.stderr
    assert (run / "posted").read_text().split() == ["room/drawer/cancel"]
    assert '"mac": "aa:bb:cc:00:00:21"' in (run / "body.json").read_text() and '"dev": "sdb"' in (run / "body.json").read_text()
    out = sh(setup + stub + 'token=room-drawer-cancel; cloner=aa:bb:cc:00:00:21; dev="sdb;x"; gui_dispatch; echo "rc=$?"')
    assert "rc=1" in out.stdout and (run / "posted").read_text().split() == ["room/drawer/cancel"]
    out = sh(setup + stub + "export REPLY_CODE=409 REPLY_BODY='{\"detail\":\"הדיסק אינו כותב עכשיו\"}'; "
             'token=room-drawer-cancel; cloner=aa:bb:cc:00:00:21; dev=sdb; room_drawer_cancel "$cloner" "$dev"; echo "rc=$?"')
    assert "rc=1" in out.stdout
    assert "הדיסק אינו כותב עכשיו" in (gui / "error").read_text(encoding="utf-8")
