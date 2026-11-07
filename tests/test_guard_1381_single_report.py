"""#1381: a closed task ends final-report retries (53ed4fb2).

Exercise the production report_final and progress_refused_for_good functions.
Only the report transport and JSON decoder are faked; no network or jq is
needed. Temporary failures must still use the configured retry budget.
"""

import shlex
import shutil
import subprocess
from pathlib import Path


PROGRESS = Path(__file__).resolve().parents[1] / "agent/lib/core/progress.sh"


def _report(tmp_path, *, code="not_open", send_rc=22, session="", task="task_1381"):
    # Use a script file, as long bash -c arguments can be truncated on Windows.
    script = tmp_path / "report.sh"
    script.write_text(
        f"""export PATH="/usr/bin:$PATH"
RUN_DIR={shlex.quote(tmp_path.as_posix())}
. {shlex.quote(PROGRESS.as_posix())}
FINAL_REPORT_TRIES=3
FINAL_REPORT_GAP_S=0
log() {{ printf '%s\\n' "$*" >> "$RUN_DIR/log"; }}
http_post_json() {{ echo 'unexpected real transport' >&2; exit 90; }}
progress_send() {{
    printf '%s|%s|%s|%s\\n' "$1" "$2" "$3" "$4" >> "$RUN_DIR/posts"
    printf '%s\\n' '{{"code":"{code}"}}' > "$RUN_DIR/progress.reply"
    return {send_rc}
}}
# The JSON boundary is stubbed, not the production refusal predicate.
json_get() {{
    [ "$1" = "$RUN_DIR/progress.reply" ] && [ "$2" = .code ] || exit 91
    printf '%s\\n' {shlex.quote(code)}
}}
sleep() {{ printf '%s\\n' "$1" >> "$RUN_DIR/waits"; }}
report_final '' {shlex.quote(session)} aa:bb:cc:dd:ee:ff http://unused {shlex.quote(task)}
""",
        encoding="utf-8",
    )
    bash = shutil.which("bash")
    assert bash is not None, "bash is required to exercise the production reporter"
    result = subprocess.run(
        [bash, script.as_posix()], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding="utf-8", timeout=10,
    )
    assert result.returncode in (0, 1), result.stderr
    assert not result.stderr, result.stderr
    posts = (tmp_path / "posts").read_text(encoding="utf-8").splitlines()
    waits = tmp_path / "waits"
    return result.returncode, posts, waits.read_text().splitlines() if waits.exists() else []


def test_closed_capture_task_reports_exactly_once(tmp_path):
    rc, posts, waits = _report(tmp_path)

    assert posts == ["|aa:bb:cc:dd:ee:ff|http://unused|task_1381"]
    assert rc == 0
    assert waits == []


def test_closed_session_reports_exactly_once(tmp_path):
    rc, posts, waits = _report(tmp_path, session="session_1381", task="")

    assert posts == ["session_1381|aa:bb:cc:dd:ee:ff|http://unused|"]
    assert rc == 0
    assert waits == []


def test_accepted_report_needs_no_retry(tmp_path):
    rc, posts, waits = _report(tmp_path, code="", send_rc=0)

    assert posts == ["|aa:bb:cc:dd:ee:ff|http://unused|task_1381"]
    assert rc == 0
    assert waits == []


def test_other_refusal_exhausts_retries_and_reports_failure(tmp_path):
    rc, posts, waits = _report(tmp_path, code="not_member")

    assert posts == ["|aa:bb:cc:dd:ee:ff|http://unused|task_1381"] * 3
    assert rc == 1
    assert waits == ["0", "0"]


def test_transport_failure_without_a_code_is_retried(tmp_path):
    rc, posts, waits = _report(tmp_path, code="", send_rc=7)

    assert posts == ["|aa:bb:cc:dd:ee:ff|http://unused|task_1381"] * 3
    assert rc == 1
    assert waits == ["0", "0"]
