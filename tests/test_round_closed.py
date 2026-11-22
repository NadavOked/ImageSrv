"""ברזל 17/10: סבב שנסגר בשרת עוצר את מסלול המגירות כולו.

מחשב שיכפול 2, ses_d6e79f65: השולח נכשל ב-08:16:10 והסבב נסגר. לולאת
הדיווח ראתה את זה (`progress: the server says this session is closed --
stopping`) — ועצרה **רק את עצמה**. המסלול הראשי המשיך: ב-08:16:12
"partition 1 (esp): feeding 1 drawers", udp-receiver ו-partclone מול שולח
שכבר יצא, המכונה נשארה "כותבת" והדיסק חצי-מוכן.

עכשיו: לולאת הדיווח משאירה סמן (`$RUN_DIR/session-closed`), והמסלול הראשי
עוצר עליו בשלוש נקודות — לפני כל נגיעה בדיסק, בין מחיצות, ובאמצע ההמתנה
לזרם — וכל מגירה חיה נכשלת **בשמה** עם הסיבה.
"""

from __future__ import annotations


import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix, sh
from test_drawer_progress import drawer_box
from test_timeouts import run_sh
from agentlib import agent_sources

pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required")

#: הסיבה כפי שהיא ב-verdict.sh — מה שהמפעיל רואה על המגירה.
WHY = "השרת סגר את הסבב -- הכתיבה הופסקה"


def _targets(run) -> dict[str, tuple[str, str]]:
    out = {}
    for t in sorted((run / "targets").iterdir()):
        error = t / "error"
        out[t.name] = ((t / "state").read_text().strip(),
                       error.read_text(encoding="utf-8").strip() if error.exists() else "")
    return out


@requires_native(("bash", BASH), "jq")
def test_the_reporter_tells_the_main_path_that_the_session_closed(tmp_path):
    """הלולאה שראתה `not_open` משאירה סמן עם מזהה הסבב — עד 17/10 היא רק
    עצרה, והמסלול הראשי לא ידע."""
    run = tmp_path / "run"
    run.mkdir()
    out = run_sh(
        f"export RUN_DIR={posix(run)!r}; "
        f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/jsonq.sh; "
        f"{agent_sources('sysinfo', 'progress', 'failmark')}"
        "build_progress() { echo '{}'; }; "
        "http_post_json() { echo '{\"ok\":false,\"code\":\"not_open\"}'; return 22; }; "
        'progress_loop ses_d6e79f65 78:ac:c0:9b:11:c2 http://s; echo "rc=$?"')
    assert out.strip().endswith("rc=0"), out
    assert (run / "session-closed").read_text().strip() == "ses_d6e79f65"


@requires_native(("bash", BASH), "jq")
def test_a_reply_that_is_not_not_open_leaves_no_mark(tmp_path):
    """הצד השני (#557): שרת שלא ענה אינו "הסבב נסגר" — אין סמן. הלולאה
    ממשיכה, ולכן היא מוגבלת כאן בזמן ונהרגת."""
    run = tmp_path / "run"
    run.mkdir()
    run_sh(
        f"export RUN_DIR={posix(run)!r} PROGRESS_INTERVAL_S=0.2; "
        f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/jsonq.sh; "
        f"{agent_sources('sysinfo', 'progress', 'failmark')}"
        "build_progress() { echo '{}'; }; "
        "http_post_json() { return 7; }; "
        "progress_loop ses_1 m http://s & p=$!; sleep 2; kill $p; wait $p 2>/dev/null; :")
    assert not (run / "session-closed").exists()


def test_a_round_closed_before_writing_touches_no_disk(tmp_path):
    """הסמן כבר שם כשהשער (SMART) שחרר את המכונה — בלי בדיקת כתיבה, בלי
    טבלה, בלי זרם. כל מגירה נכשלת בשמה, והמכונה `failed`."""
    run, prelude = drawer_box(tmp_path, disks=("sda", "sdb"))
    (run / "session-closed").write_text("ses_d6e79f65\n")
    out = sh(prelude
             + 'write_check_round() { echo wc >> "$RUN_DIR/touched"; }; '
             + 'plan_gate() { return 0; }; apply_gpt() { echo "gpt $1" >> "$RUN_DIR/touched"; }; '
             + 'restore_partition_drawers() { echo "fed $4" >> "$RUN_DIR/touched"; }; '
             + 'run_restore_drawers chunk http://s img m.json sda sdb; echo "rc=$?"')
    assert out.strip().endswith("rc=1"), out
    assert not (run / "touched").exists(), (run / "touched").read_text()
    assert (run / "state").read_text().strip() == "failed"
    assert _targets(run) == {"sda": ("failed", WHY), "sdb": ("failed", WHY)}


def test_a_round_closed_between_partitions_feeds_no_more(tmp_path):
    """הסבב נסגר אחרי מחיצה 1: מחיצה 2 לא מוזנת, והמגירות נכשלות עם
    הסיבה — לא "נכתבו 1 מתוך 2" ולא done."""
    run, prelude = drawer_box(tmp_path, disks=("sda",))
    out = sh(prelude
             + "manifest_plan() { echo '1|G|esp|vfat|2048|1024|p1.zst|s|false|U|'; "
             + "echo '2|G|win|ntfs|4096|1024|p2.zst|s|false|U|'; }; "
             + 'restore_partition_drawers() { echo "$4" >> "$RUN_DIR/fed"; '
             + 'echo ses > "$RUN_DIR/session-closed"; return 0; }; '
             + 'run_restore_drawers chunk http://s img m.json sda; echo "rc=$?"')
    assert out.strip().endswith("rc=1"), out
    assert (run / "fed").read_text().split() == ["1"]
    assert (run / "state").read_text().strip() == "failed"
    assert _targets(run) == {"sda": ("failed", WHY)}


def test_the_machine_hold_names_the_closed_round():
    """המסך/הקונסולה (HOLD_PROMPT) אומרים למה המכונה עצרה — לא "no drawer
    completed" על סבב שהשרת סגר."""
    agent = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    body = agent[agent.index("do_restore_drawers() {"):agent.index("do_restore() {")]
    assert 'round_closed && _why="the server closed this round -- writing stopped"' in body
    assert '"$RUN_DIR/session-closed"' in body.split("progress_loop", 1)[0], \
        "הסמן של סבב קודם חייב להימחק לפני שהלולאה החדשה מתחילה"
