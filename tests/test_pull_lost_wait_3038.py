"""#3038 -- a pull ("restore from the server") waits as long as the operator's lost_wait setting.

The setting "המתנה למחשב שהתנתק באמצע שיכפול" (`lost_wait_seconds`, #2056) runs from 3
to 30 minutes, and the agent holds a pull's download for `lost + 120` s
(agent/lib/core/timing.sh). The server closed a silent pull as failed after a fixed
600 s (`pulls.SILENT_SECONDS`, #1573): with 15 minutes set, a cloner whose switch
restarted for 11 minutes found its pull failed on the server while it still waited.
Nadav, 12/11: a pull too waits by the setting.

Now the ceiling is `max(600, lost_wait + 180)`: never under the agent's own, and at the
default (180 s) exactly what it was -- the guards below pass with and without the fix.
A ceiling stays (#1573): a pull that never reports still fails, by name.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

pytestmark = pytest.mark.usefixtures("classrooms_on")      # the helpers make a classroom, as test_server_streams

from server.db import set_setting  # noqa: E402
from test_server_streams import overview, pull, report, second_class, session_row, silence  # noqa: E402


def _silent_pull(server, lost: int | None, seconds: int) -> tuple:
    if lost is not None:
        set_setting(server["ctx"].conn, "lost_wait_seconds", str(lost))
    lab2 = second_class(server)
    pull_id = pull(server, lab2["mac1"]).json()["id"]
    assert report(server, pull_id, lab2["mac1"], "writing").json()["ok"] is True
    silence(server, pull_id, seconds)
    overview(server)                                           # the console's look sweeps the pulls
    return session_row(server, pull_id)


@pytest.mark.parametrize("lost, quiet", [(900, 700), (900, 1060), (1800, 1960)])
def test_a_pull_silent_longer_than_600s_but_within_the_setting_is_not_failed(server, lost, quiet):
    """The case of the issue: 15 minutes set, 11-17 minutes of silence -- the agent still waits, and so does
    the server. 30 minutes set: up to lost + 180."""
    row = _silent_pull(server, lost, quiet)
    assert row["state"] == "running", dict(row)


@pytest.mark.parametrize("lost, quiet", [(900, 1100), (1800, 2000)])
def test_a_pull_silent_past_the_setting_and_its_margin_still_fails_by_name(server, lost, quiet):
    """The ceiling is moved, not removed (#1573: a pull left running blocks the nightly reboot and the update)."""
    row = _silent_pull(server, lost, quiet)
    assert row["state"] == "failed" and row["failed_reason"].startswith("אין דיווח מהמכונה מאז"), dict(row)


@pytest.mark.parametrize("lost, quiet, state", [(None, 590, "running"), (None, 620, "failed"),
                                                 (300, 620, "failed")])
def test_at_the_default_and_below_600s_the_ceiling_is_the_600s_it_was(server, lost, quiet, state):
    """Guard: unchanged where lost + 180 is under 600 (the default 180, and 300). Passes before the fix too."""
    assert _silent_pull(server, lost, quiet)["state"] == state
