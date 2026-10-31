"""#1395: console power requests are consumed by every attended wait."""

from pathlib import Path

from test_agent import AGENT, posix, sh
from agentlib import lib_path


def test_attended_power_check_executes_the_saved_response(tmp_path: Path):
    run = tmp_path / "run"; run.mkdir()
    (run / "power.resp").write_text('{"power_action":"reboot"}\n')
    out = sh(
        f'RUN_DIR={posix(run)!r}; RESP=original; '
        'json_get() { echo reboot; }; power_check() { echo "POWER:$RESP"; }; '
        f'. {posix(AGENT)}/lib/core/attended.sh; attended_power_check; '
        'echo "RESP:$RESP"; test ! -e "$RUN_DIR/power.resp"'
    )
    assert f"POWER:{posix(run)}/power.taken" in out
    assert "RESP:original" in out


def test_all_person_wait_loops_check_for_power_requests():
    for name in ("clonergui.sh", "shrinkgui.sh", "askgui.sh", "hold.sh"):
        text = (lib_path(name)).read_text(encoding="utf-8")
        assert "attended_power_check" in text, name
