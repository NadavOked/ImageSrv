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
    # #2044: clonergui.sh is not on the list any more -- its only wait for a person
    # (gui_smart_choice, the SMART question) was removed; the drawer questions on the
    # cloner wait in askgui.sh. The scan below keeps the rule for any wait that comes back.
    for name in ("shrinkgui.sh", "askgui.sh", "hold.sh"):
        text = (lib_path(name)).read_text(encoding="utf-8")
        assert "attended_power_check" in text, name
    assert "smart-decision" not in lib_path("clonergui.sh").read_text(encoding="utf-8")


def test_every_wait_on_a_gui_decision_checks_for_power_requests():
    """A loop that waits for the person's answer in `$GUI_DIR/<x>-decision` is a
    person wait wherever it lives: it must consume a console power request
    (#1395) like the named ones above. Found by scanning, not by a hand list."""
    import re
    readers = []
    for path in sorted(Path(AGENT).rglob("*.sh")):
        text = path.read_text(encoding="utf-8")
        if re.search(r'<\s*"\$GUI_DIR/[a-z]+-decision"', text):
            readers.append(path.name)
            assert "attended_power_check" in text, path
    assert {"askgui.sh", "shrinkgui.sh"} <= set(readers), readers   # the scan sees what it was written for
