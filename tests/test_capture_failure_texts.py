"""#2052: capture recovery steps follow reason codes, not translated prose."""

import shlex

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix, sh

pytestmark = requires_native(("bash", BASH))


def capture_texts(reason, code=None):
    args = ["capture", reason, ""]
    if code is not None:
        args.append(code)
    return sh(
        f'. {shlex.quote(posix(AGENT / "lib/failscreen.sh"))}; '
        + "fail_texts " + " ".join(map(shlex.quote, args))
        + '; printf "%s\\n" "$_ft_why" "$_ft_do" "$_ft_btn"'   # #2068: one sentence, then the buttons
    ).splitlines()


@pytest.mark.parametrize("suffix,remaining", [("", ""), (".", ""), ("..", ".")])
def test_capture_strips_exactly_one_trailing_period(suffix, remaining):
    why, *_ = capture_texts("ההעלאה נכשלה" + suffix, "network_server")
    assert why == f"הדיסק נקרא, אבל ההעלאה נכשלה{remaining}, ולכן האימג׳ לא נכנס לספרייה."


def test_network_failure_says_what_stopped_and_offers_a_retry():
    # #2068 (mockup 09): the sentence says what happened; the next step is a button
    _, do, btn = capture_texts("ההעלאה נכשלה.", "network_server")
    assert do == "ההעלאה לשרת נעצרה. הדיסק של המחשב הזה לא שונה."
    assert btn.split(";")[0] == "retry|נסה שוב"


def test_fast_startup_steps_treat_the_source_then_retry():
    # Deliberately generic prose: only the code can select the instructions.
    _, do, btn = capture_texts("הבדיקה סירבה.", "source_ntfs")
    assert "כבל" not in do
    assert "Windows" in do and "chkdsk C: /f" in do   # #2068: the one command, in a short sentence
    assert btn == "windows|הפעל את Windows;retry|נסה שוב"


@pytest.mark.parametrize("code,expected", [
    ("source_bitlocker", "manage-bde -off C:"), ("source_disk", "בדוק את הדיסק"),
    (None, "המשימה נעצרה"), ("future_reason", "המשימה נעצרה"),   # #2068: never "פנו ל-IT"
])
def test_other_source_and_unknown_reasons_do_not_prescribe_a_cable(code, expected):
    _, do, btn = capture_texts("סיבת הכשל.", code)
    assert expected in do and btn
    assert "כבל" not in do and "IT" not in do


@pytest.mark.parametrize("code,expected", [
    ("source_ntfs", "chkdsk"), ("network_server", "ההעלאה לשרת נעצרה"),
    ("source_bitlocker", "manage-bde"), (None, "בדוק את הדיסק"),
])
def test_capture_failure_code_reaches_screen_records(tmp_path, code, expected):
    # Real failure writer -> persisted code -> real screen record generator.
    arg = "" if code is None else " " + shlex.quote(code)
    output = sh(
        f'RUN_DIR={shlex.quote(posix(tmp_path))}; '
        f'. {shlex.quote(posix(AGENT / "lib/capturesink.sh"))}; '
        f'. {shlex.quote(posix(AGENT / "lib/failscreen.sh"))}; '
        'log() { :; }; shrink_restore_source() { return 0; }; target_set() { :; }; '
        'json_get() { echo null; }; '
        '_capture_failed sda "סיבת הכשל."' + arg + '; '
        'fail_records capture "capture did not complete" bad 1 "סיבת הכשל."'
    )
    steps = [line for line in output.splitlines() if line.startswith("fail_step=")]
    assert len(steps) == 1   # #2068: one sentence; the next step is a button (fail_btn)
    assert expected in steps[0]
    assert [line for line in output.splitlines() if line.startswith("fail_btn=")]
    assert ".," not in output


def test_manifest_upload_failure_records_network_reason(tmp_path):
    output = sh(
        f'RUN_DIR={shlex.quote(posix(tmp_path))}; LOG_FILE="$RUN_DIR/log"; SERVER=http://test; '
        f'. {shlex.quote(posix(AGENT / "lib/capturesink.sh"))}; '
        'curl() { return 7; }; upload_manifest task /unused; rc=$?; '
        'printf "rc=%s\\n" "$rc"; cat "$RUN_DIR/capture.reason"'
    )
    assert output.splitlines() == ["rc=1", "network_server"]


@pytest.mark.parametrize("post,head,reply,want", [
    pytest.param("return 7", "", "", "network_server", id="no-answer"),
    pytest.param("return 22", "HTTP/1.1 404 Not Found", "", "unknown", id="task-closed"),
    pytest.param("return 0", "HTTP/1.1 200 OK", "refused|אין מקום פנוי בשרת", "unknown", id="refused"),
])
def test_the_space_check_says_cable_only_when_the_server_did_not_answer(tmp_path, post, head, reply, want):
    """A refusal from the server (no room) is not "check the network cable"."""
    output = sh(
        f'RUN_DIR={shlex.quote(posix(tmp_path))}; SERVER=http://test; '
        f'. {shlex.quote(posix(AGENT / "lib/capturesink.sh"))}; '
        'log() { :; }; target_set() { :; }; cd_used() { :; }; partition_node() { echo /dev/x; }; '
        '_fs_of() { echo ntfs; }; _partition_role() { echo data; }; partclone_for_fs() { echo partclone.ntfs; }; '
        '_used_bytes() { echo 100; }; '
        f'http_post_json() {{ printf "%s\n" {shlex.quote(head)} > "$4"; {post}; }}; '
        f'json_get() {{ r={shlex.quote(reply)}; case "$2" in .status) echo "${{r%%|*}}";; .reason) echo "${{r#*|}}";; esac; }}; '
        'printf "1|g|u|2048|100\n" > "$RUN_DIR/parts"; '
        'capture_space_preflight task sda "$RUN_DIR/parts" 512; echo "rc=$? code=$CAPTURE_SPACE_CODE"'
    )
    assert output.splitlines()[-1] == f"rc=1 code={want}", output
