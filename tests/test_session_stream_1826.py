"""#1826: המקלט בודק את הזרם מול המניפסט **של הסבב שלו**.

`session_stream` (`agent/lib/core/streamid.sh`) מסרב לפני הכתיבה כשהמניפסט אינו של האימג' של
הסבב -- ה-sha256 (וה-BLAKE3 של החתיכות) של כל מחיצה נבדקים מולו, ומניפסט של אימג' אחר היה מאשר
בדיוק את הזרם הלא נכון. `do_restore_drawers` עוצר לפני `run_restore_drawers` (`die_local`, "לא
נכתב דבר").

#3173: החצי השני של #1826 -- `session.portbase`, הפורט שהמקלט הישן הצטרף אליו -- נמחק יחד עם
המקלט הישן. גל החתיכות נושא את הקבוצה, הפורט, המפתח והמקור ב-`session.chunk` (chunkgate.sh,
test_chunk_gate_2533.py), ותחנת כיתה אינה מקבלת סבב ב-v1 (test_station_round_refused_3173.py).
"""

from __future__ import annotations

import json
import shutil
import subprocess

from native import requires_native
from test_agent import AGENT, BASH, posix

REPO = AGENT.parent
JQ_SHIM = posix(REPO / "tests" / "jq_shim.py")
IMAGE = "img_7f3a91"

#: jq אינו מותקן בתחנת הפיתוח (ווינדוס). התחליף הצר (tests/jq_shim.py) מבין
#: בדיוק את הנתיבים הפשוטים ש-json_get מריץ כאן; במעבדה jq האמיתי בשימוש.
JQ_FN = "" if shutil.which("jq") else f'jq() {{ python "{JQ_SHIM}" "$@"; }}; '

bash_only = requires_native(("bash", BASH))


# --- הסוכן: session_stream ----------------------------------------------------


def _agent_sh(tmp_path, call: str, *, manifest_id=IMAGE, pre="") -> str:
    """טוען את ספריות הסוכן שמסלול הקבלה נשען עליהן, מריץ `session_stream`
    על תשובה ומניפסט מזויפים, ואחריו את `call`."""
    resp = {"session": {"id": "ses_1", "image_id": IMAGE, "state": "running"}}
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
    out = _agent_sh(tmp_path, 'echo "rc=$? why=$STREAM_REFUSAL"', **kwargs)
    lines = [ln for ln in out.splitlines() if ln.startswith("rc=")]
    return lines[-1] if lines else out


@bash_only
def test_the_sessions_own_manifest_is_taken(tmp_path):
    assert _session_stream(tmp_path).startswith("rc=0 why="), _session_stream(tmp_path)


@bash_only
def test_a_manifest_of_another_image_is_refused_by_its_id(tmp_path):
    """ה-sha256 של כל מחיצה נבדק מול המניפסט הזה (restore.sh). מניפסט של
    אימג' אחר היה מאשר בדיוק את הזרם הלא נכון — ולכן הוא נפסל לפני הכול."""
    out = _session_stream(tmp_path, manifest_id="img_other1")
    assert out.startswith("rc=1 why=restore: manifest is not of the session's image"), out
    assert "img_other1" in out and IMAGE in out


@bash_only
def test_a_manifest_without_an_id_is_not_taken_as_the_sessions(tmp_path):
    """היעדר ראיה אינו ראיה: `.id` חסר (או jq שנכשל — json_get מחזיר `null`)
    אינו "מתאים"."""
    out = _session_stream(tmp_path, manifest_id=None)
    assert out.startswith("rc=1 "), out


# --- הסוכן: do_restore / do_restore_drawers ------------------------------------


def _run_path(tmp_path, call, *, session_image=IMAGE, role="cloner"):
    from test_final_report import ANSWER, run_path

    answer = json.loads(json.dumps(ANSWER))
    answer["session"]["image_id"] = session_image
    answer["session"]["engine"] = "chunk"   # #3173: the only engine (chunkgate.sh); its parameters are faked below
    # הכתיבה עצמה אינה הנבדק: היא מזויפת ככישלון שנרשם.
    record = (
        JQ_FN
        + 'sleep() { :; }; '
        + 'chunk_gate() { STREAM_MODE=chunk; return 0; }; '   # the gate has its own tests (test_chunk_gate_2533.py)
        + 'run_restore_drawers() { echo "DRAWERS mode=$1" >> "$RUN_DIR/trace"; return 1; }; '
        + 'ui_error_hold() { echo "HOLD $1" >> "$RUN_DIR/trace"; }; '
        + 'end_failed_round() { echo "ENDED $1" >> "$RUN_DIR/trace"; }; '
        + 'crc_delta_after() { return 0; }; crc_delta_measure() { :; }; '
        # מיפוי המגירות (#695) אינו הנבדק, וה-jq שלו מעבר לתחליף הצר.
        + 'resolve_target_drawers() { _disks="sdb sdc"; }; '
    )
    # הדיווח לשרת אינו הנבדק כאן — כתובת שאיש אינו מאזין לה.
    return run_path(tmp_path, "http://127.0.0.1:9", call, answer=answer, stubs=record,
                    env=f"D_ROLE={role}")


@bash_only
def test_a_cloning_round_on_its_own_manifest_goes_on_to_the_drawers(tmp_path):
    from test_final_report import SESSION
    result = _run_path(tmp_path, f"do_restore_drawers {IMAGE} {SESSION}")
    assert "DRAWERS mode=chunk" in result["trace"], result


@bash_only
def test_a_cloner_whose_manifest_is_of_another_image_writes_nothing(tmp_path):
    from test_final_report import SESSION
    result = _run_path(tmp_path, f"do_restore_drawers img_other1 {SESSION}")
    assert not any(t.startswith("DRAWERS") for t in result["trace"]), result["trace"]
    assert "TEST-REBOOT: restore: manifest is not of the session's image" in result["out"], result["out"]
