"""‏#1826: המקלט מצטרף לשידור של **הסבב שלו**, ובודק מול המניפסט **שלו**.

ל-udpcast אין מזהה אימג' בפרוטוקול: המקלט מוצא את השולח לפי ה-portbase
בלבד. עד כאן הסוכן הניח 9000 קבוע (פורט-הפצה-מכוון: תיעוד הבאג) (‏`restore.sh:12`), כך ש-`--sender-portbase`
של השרת לא הגיע לאף מקלט, ושני שרתים באותו וילן היו בהכרח על אותו פורט.

שלוש שכבות, ושלושתן נבדקות:

* השרת — ‏`session.portbase` בתשובת ה-hello הוא הפורט ש-`SenderEngine` שלו
  באמת משדר בו (בחבילה הזאת: פורט אקראי של `hygiene`, לא 9000; פורט-הפצה-מכוון: תיעוד).
* ‏`session_stream` (‏`agent/lib/core/streamid.sh`) — מציב את ה-portbase של הסבב,
  ומסרב לפני הכתיבה כשהמניפסט אינו של האימג' של הסבב או כשהפורט אינו פורט.
* ‏`do_restore` / ‏`do_restore_drawers` — הפורט מגיע ל-udp-receiver, וסירוב
  עוצר לפני `run_restore` (‏`die_local`, "לא נכתב דבר").
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix

REPO = AGENT.parent
JQ_SHIM = posix(REPO / "tests" / "jq_shim.py")
IMAGE = "img_7f3a91"

#: jq אינו מותקן בתחנת הפיתוח (ווינדוס). התחליף הצר (‏tests/jq_shim.py) מבין
#: בדיוק את הנתיבים הפשוטים ש-json_get מריץ כאן; במעבדה jq האמיתי בשימוש.
JQ_FN = "" if shutil.which("jq") else f'jq() {{ python "{JQ_SHIM}" "$@"; }}; '

bash_only = requires_native(("bash", BASH))


# --- השרת: ‏session.portbase ---------------------------------------------------


def test_the_hello_names_the_port_this_servers_sender_really_uses(server):
    """**לב #1826, צד השרת.** ‏`ctx.sender.portbase` בחבילה הזאת הוא פורט
    אקראי (‏`hygiene.assign_test_portbase`) — לכן שוויון אליו הוא ראיה
    שהערך נקרא מהמנוע, ולא 9000 (פורט-הפצה-מכוון) שבמקרה שווה גם לו."""
    from conftest import hello_body, setup_classroom
    from server.rounds import sender

    ids = setup_classroom(server)
    opened = server["admin"].post("/api/console/sessions", json={
        "group_id": ids["group"], "image_id": IMAGE, "prefix": "LAB1",
        "expected_clients": 2})
    assert opened.status_code == 200, opened.text
    answer = server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"])).json()
    engine = server["ctx"].sender.portbase
    assert engine != sender.PRODUCTION_PORTBASE, "החבילה אמורה לרוץ על פורט אקראי (#156)"
    assert answer["session"]["portbase"] == engine, answer["session"]


def test_the_direct_source_and_the_receivers_are_told_the_same_port():
    """מחשב בנייה שמשדר (‏`task.direct.multicast.portbase`) והמקלטים
    (‏`session.portbase`) מקבלים את הערך מאותו מקור — גם בלי מנוע."""
    pytest.importorskip("fastapi")
    from server.rounds import direct, sender

    assert direct.default_multicast()["portbase"] == sender.DEFAULT_PORTBASE


# --- הסוכן: ‏session_stream ----------------------------------------------------


def _agent_sh(tmp_path, call: str, *, manifest_id=IMAGE, portbase=None, pre="") -> str:
    """טוען את ספריות הסוכן שמסלול הקבלה נשען עליהן, מריץ `session_stream`
    על תשובה ומניפסט מזויפים, ואחריו את `call`."""
    resp = {"session": {"id": "ses_1", "image_id": IMAGE, "state": "running"}}
    if portbase is not None:
        resp["session"]["portbase"] = portbase
    (tmp_path / "resp.json").write_text(json.dumps(resp), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({"id": manifest_id}), encoding="utf-8")
    script = (
        'export PATH="/usr/bin:$PATH"; ' + pre
        + f"export RUN_DIR={posix(tmp_path)!r} LOG_FILE={posix(tmp_path / 'agent.log')!r}; "
        f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/waits.sh; "
        f". {posix(AGENT)}/lib/core/jsonq.sh; . {posix(AGENT)}/lib/restore/restore.sh; "
        f". {posix(AGENT)}/lib/core/streamid.sh; " + JQ_FN
        + f'session_stream {IMAGE} {posix(tmp_path / "manifest.json")!r} '
        f'{posix(tmp_path / "resp.json")!r}; ' + call
    )
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                          encoding="utf-8", cwd=str(REPO), stdin=subprocess.DEVNULL)
    return proc.stdout + proc.stderr


def _session_stream(tmp_path, **kwargs) -> str:
    out = _agent_sh(tmp_path, 'echo "rc=$? pb=$UDPCAST_PORTBASE why=$STREAM_REFUSAL"', **kwargs)
    lines = [ln for ln in out.splitlines() if ln.startswith("rc=")]
    return lines[-1] if lines else out


@bash_only
def test_the_port_the_server_named_is_the_one_the_receiver_joins(tmp_path):
    out = _session_stream(tmp_path, portbase=31234)
    assert out.startswith("rc=0 pb=31234 "), out


@bash_only
def test_a_server_from_before_1826_keeps_the_port_it_always_sent_on(tmp_path):
    """שרת ישן אינו שולח את השדה; הוא משדר על 9000 (פורט-הפצה-מכוון: ברירת המחדל) — ‏`restore.sh` כבר מחזיק
    אותו. שום דבר לא משתנה מולו (עיקרון 1)."""
    out = _session_stream(tmp_path, portbase=None)
    assert out.startswith("rc=0 pb=9000 "), out   # פורט-הפצה-מכוון: ברירת המחדל של שרת ישן (restore.sh)


@bash_only
@pytest.mark.parametrize("bad", ["90x0", "", "80", "65535", "123456789012345678901"])
def test_a_port_that_is_not_a_port_is_refused_before_the_write(tmp_path, bad):
    """ערך שאינו פורט אינו נופל ל-9000 (פורט-הפצה-מכוון) בשקט: זה בדיוק "לא ידוע לאיזה שידור
    להצטרף", והתשובה היא סירוב בשם — לפני הטבלה."""
    out = _session_stream(tmp_path, portbase=bad)
    assert out.startswith("rc=1 pb=9000 why=restore: session portbase"), out   # פורט-הפצה-מכוון: ברירת המחדל של שרת ישן (restore.sh)
    assert "nothing was written" in out


@bash_only
def test_a_manifest_of_another_image_is_refused_by_its_id(tmp_path):
    """ה-sha256 של כל מחיצה נבדק מול המניפסט הזה (‏restore.sh). מניפסט של
    אימג' אחר היה מאשר בדיוק את הזרם הלא נכון — ולכן הוא נפסל לפני הכול."""
    out = _session_stream(tmp_path, manifest_id="img_other1", portbase=31234)
    assert out.startswith("rc=1 pb=9000 why=restore: manifest is not of the session's image"), out   # פורט-הפצה-מכוון: ברירת המחדל של שרת ישן (restore.sh)
    assert "img_other1" in out and IMAGE in out


@bash_only
def test_a_manifest_without_an_id_is_not_taken_as_the_sessions(tmp_path):
    """היעדר ראיה אינו ראיה: ‏`.id` חסר (או jq שנכשל — ‏json_get מחזיר `null`)
    אינו "מתאים"."""
    out = _session_stream(tmp_path, manifest_id=None, portbase=31234)
    assert out.startswith("rc=1 "), out


def _recording_receiver(stub_dir, argv) -> str:
    """‏udp-receiver מזויף שרושם את ה-argv שלו. נכתב מתוך bash (קובץ שנוצר
    מווינדוס אינו בר-הרצה ב-Git Bash), כמו `make_stubs` ב-test_timeouts."""
    return (f"mkdir -p {posix(stub_dir)!r}\n"
            f"cat > {posix(stub_dir)}/udp-receiver <<'STUB'\n"
            "#!/bin/sh\n"
            f'printf "%s\\n" "$@" > {posix(argv)!r}\n'
            "STUB\n"
            f"chmod 0755 {posix(stub_dir)}/udp-receiver\n"
            # ‏`cd && pwd`: ‏"C:/…" ב-PATH נחתך על הנקודתיים שאחרי האות.
            f'export PATH="$(cd {posix(stub_dir)!r} && pwd):$PATH"\n')


@bash_only
def test_the_receiver_is_started_on_the_sessions_port(tmp_path):
    """מ-`session_stream` ועד `udp-receiver --portbase`: ה-argv של המקלט."""
    argv = tmp_path / "udp.argv"
    out = _agent_sh(tmp_path, f"stream_source multicast http://s {IMAGE} p1.zst 600",
                    portbase=31234, pre=_recording_receiver(tmp_path / "stubs", argv))
    assert argv.exists(), out
    args = argv.read_text(encoding="utf-8").split()
    assert args[args.index("--portbase") + 1] == "31234", args


# --- הסוכן: ‏do_restore / ‏do_restore_drawers ------------------------------------


def _run_path(tmp_path, call, *, session_image=IMAGE, portbase=31234, role="classroom"):
    from test_final_report import ANSWER, run_path

    answer = json.loads(json.dumps(ANSWER))
    answer["session"]["image_id"] = session_image
    if portbase is not None:
        answer["session"]["portbase"] = portbase
    # הכתיבה עצמה אינה הנבדק: היא מזויפת ככישלון שרושם מאיזה פורט היה מקבל.
    record = (
        JQ_FN
        + 'sleep() { :; }; '
        + 'run_restore() { echo "RESTORE pb=$UDPCAST_PORTBASE" >> "$RUN_DIR/trace"; return 1; }; '
        + 'run_restore_drawers() { echo "DRAWERS pb=$UDPCAST_PORTBASE" >> "$RUN_DIR/trace"; return 1; }; '
        + 'ui_error_hold() { echo "HOLD $1" >> "$RUN_DIR/trace"; }; '
        + 'end_failed_round() { echo "ENDED $1" >> "$RUN_DIR/trace"; }; '
        + 'crc_delta_after() { return 0; }; crc_delta_measure() { :; }; '
        # מיפוי המגירות (‏#695) אינו הנבדק, וה-jq שלו מעבר לתחליף הצר.
        + 'resolve_target_drawers() { _disks="sdb sdc"; }; '
    )
    # הדיווח לשרת אינו הנבדק כאן — כתובת שאיש אינו מאזין לה.
    return run_path(tmp_path, "http://127.0.0.1:9", call, answer=answer, stubs=record,
                    env=f"D_ROLE={role}")


@bash_only
def test_a_station_round_receives_on_the_sessions_port(tmp_path):
    result = _run_path(tmp_path, "do_restore")
    assert "RESTORE pb=31234" in result["trace"], result


@bash_only
def test_a_cloning_round_receives_on_the_sessions_port(tmp_path):
    from test_final_report import SESSION
    result = _run_path(tmp_path, f"do_restore_drawers {IMAGE} {SESSION}", role="cloner")
    assert "DRAWERS pb=31234" in result["trace"], result


@bash_only
def test_a_station_whose_manifest_is_of_another_image_writes_nothing(tmp_path):
    """השרת ענה על מניפסט, אבל לא של האימג' שבסבב. הקוד הישן היה ממשיך
    לטבלה ולזרם, ובודק את ה-sha מול מניפסט של אימג' אחר."""
    result = _run_path(tmp_path, "do_restore", session_image="img_other1")
    assert not any(t.startswith("RESTORE") for t in result["trace"]), result["trace"]
    assert "TEST-REBOOT: restore: manifest is not of the session's image" in result["out"], result["out"]


@bash_only
def test_a_cloner_whose_manifest_is_of_another_image_writes_nothing(tmp_path):
    from test_final_report import SESSION
    result = _run_path(tmp_path, f"do_restore_drawers img_other1 {SESSION}", role="cloner")
    assert not any(t.startswith("DRAWERS") for t in result["trace"]), result["trace"]
    assert "TEST-REBOOT: restore: manifest is not of the session's image" in result["out"], result["out"]


# --- המסמכים -------------------------------------------------------------------


def test_the_field_is_documented_in_the_interface():
    text = (REPO / "docs" / "interfaces.md").read_text(encoding="utf-8")
    assert "**`session.portbase`**" in text
    example = text.split("## 3. תשובת השרת", 1)[1].split("### כללים", 1)[0]
    assert '"portbase": 9000' in example   # פורט-הפצה-מכוון: הדוגמה ב-interfaces.md
