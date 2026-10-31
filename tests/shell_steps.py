"""Run a finite shell interaction with a watchdog per completed action (#2118).

No periodic heartbeat: only completion of the next real command advances the
deadline. All commands, including every GUI redraw, still run in one shell.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import time
from pathlib import Path

import hygiene
from test_agent import posix


def run_steps(argv: list[str], steps: list[str], directory: Path, *, timeout: float,
              cwd: str) -> subprocess.CompletedProcess:
    progress = directory / "shell-step"
    progress.unlink(missing_ok=True)
    quoted = shlex.quote(posix(progress))
    script = directory / "shell-steps.sh"
    script.write_text(
        "\n".join(
            f'{step}\n_test_step_rc=$?\nprintf "%s\\n" {i} > {quoted}.next\n'
            f'mv {quoted}.next {quoted} || exit 97'
            for i, step in enumerate(steps)
        ) + '\nexit "$_test_step_rc"\n', encoding="utf-8", newline="\n")
    command = [*argv, posix(script)]
    with subprocess.Popen(command, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding="utf-8", errors="replace", cwd=cwd,
                          start_new_session=hasattr(os, "killpg")) as proc:
        seen = ""
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    out, err = proc.communicate(timeout=min(0.25, max(0.001, deadline - time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    current = progress.read_text().strip() if progress.exists() else ""
                    if current != seen:
                        assert current.isdigit() and 0 <= int(current) < len(steps), current
                        assert not seen or int(current) > int(seen), (seen, current)
                        seen = current
                        deadline = time.monotonic() + timeout
                    if time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired(command, timeout,
                                                        stderr=f"stalled after step {seen or 'none'}")
            assert progress.exists() and progress.read_text().strip() == str(len(steps) - 1), (
                "shell exited before completing all actions", proc.returncode, out, err)
            return subprocess.CompletedProcess(command, proc.returncode, out, err)
        finally:
            if hasattr(os, "killpg"):
                hygiene.kill_group(proc.pid)
            elif proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=hygiene.RUN_GROUP_DRAIN_S)
