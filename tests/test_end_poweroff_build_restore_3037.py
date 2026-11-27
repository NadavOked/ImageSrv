"""#3037 -- "ספירה לכיבוי בסוף משימה" (`gui_end_poweroff_seconds`) also reaches the build machine and a single restore.

The setting reached only the cloner (`idle.sh` `end_countdown`, via `END_POWEROFF_S`). The build
machine's end of a task (`build_end_ask`) had a fixed `CAPTURE_END_ASK_S:-20`, a single restore
(`sr_end_poweroff`) a fixed `SR_POWEROFF_S=20` -- while the console told the operator the card
covers "משכפל · מחשב בנייה · שחזור", and counted the build machine as "applied".

Nadav 12/11: the setting applies to the build machine too. The "ask" dialog of #1560 stays: only the
fixed 20 becomes the setting. The console's sentence about stopping the count said "the machine waits
5 minutes"; the code (idle.sh, #2024) keeps it on, with no ceiling, until "כבה".
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from console_src import console_js
from test_agent import BASH, posix
from test_final_report import cut_function, native_tools
from test_single_restore_progress_gui import END, PROGRESS, restore

def _ask_seconds(tmp_path: Path, env: str) -> str:
    """The seconds `build_end_ask` hands the GUI dialog (the last argument of gui_ask_choice)."""
    funcs = tmp_path / "funcs.sh"
    funcs.write_text(cut_function("build_end_ask") + "\n", encoding="utf-8", newline="\n")
    script = (
        f"{env}; log() {{ :; }}; finish_and_stop() {{ :; }}; task_done_stays_on() {{ :; }}; "
        'attended() { shift; echo "$@" > "$OUT"; echo menu; }; '
        f"OUT={posix(tmp_path / 'asked')!r}; . {posix(funcs)}; "
        'build_end_ask capture-end "q" "done" "stays"'
    )
    subprocess.run([BASH, "-c", script], stdin=subprocess.DEVNULL, capture_output=True, timeout=60, check=True)
    return (tmp_path / "asked").read_text(encoding="utf-8").split()[-1]


@native_tools
def test_the_build_machines_end_ask_counts_the_setting(tmp_path):
    assert _ask_seconds(tmp_path, "END_POWEROFF_S=77") == "77"


@native_tools
def test_without_the_setting_the_build_machine_still_counts_20(tmp_path):
    assert _ask_seconds(tmp_path, "unset END_POWEROFF_S CAPTURE_END_ASK_S") == "20"


@native_tools
def test_the_old_override_still_wins(tmp_path):
    assert _ask_seconds(tmp_path, "END_POWEROFF_S=77; CAPTURE_END_ASK_S=5") == "5"


@native_tools
def test_a_single_restore_counts_the_setting(tmp_path):
    r = restore(tmp_path, PROGRESS, then=END, env="END_POWEROFF_S=4")
    assert "END=0" in r["out"] and "TEST-POWEROFF" in r["out"], r["out"]
    assert r["beats"] == 4, r["beats"]
    assert "restore: ended -- powering off in 4s" in r["log"]


def test_the_console_does_not_promise_five_minutes():
    text = console_js()
    assert "מחכה 5 דקות" not in text
    assert "אז המחשב נשאר דלוק עד שלוחצים על כבה." in text
