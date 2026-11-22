"""#2533: `tools/server-tools-build.sh` -- the server's own `chunkcast`.

The sender of the chunk engine runs on the server ("chunkcast send", found by PATH
like udp-sender). The initrd carries the same program for the stations; this script
is what puts it on the server, at installation and at every upgrade -- also when the
boot payload was taken ready-made and nothing was rebuilt.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "tools" / "server-tools-build.sh"
needs_gcc = requires_native("gcc", "bash", posix=True, why="the script compiles chunkcast")
needs_bash = requires_native("bash", posix=True, why="the upgrade script's own functions, run by bash")


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)


@needs_gcc
def test_it_builds_a_chunkcast_that_runs_where_the_server_looks_for_it(tmp_path):
    done = _run("--source", str(REPO), "--bin-dir", str(tmp_path / "bin"))
    assert done.returncode == 0, done.stdout + done.stderr
    binary = tmp_path / "bin" / "chunkcast"
    assert binary.is_file() and not (tmp_path / "bin" / "chunkcast.new").exists()
    said = subprocess.run([str(binary)], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert said.returncode == 2 and "usage: chunkcast send|recv|apply" in said.stderr


@needs_gcc
def test_a_build_that_fails_leaves_the_previous_binary_and_says_so(tmp_path):
    """A broken source is not a new sender: non-zero, by name, and the old binary byte for byte."""
    assert _run("--source", str(REPO), "--bin-dir", str(tmp_path / "bin")).returncode == 0
    before = (tmp_path / "bin" / "chunkcast").read_bytes()
    broken = tmp_path / "src" / "agent"
    broken.mkdir(parents=True)
    shutil.copy(REPO / "agent" / "fanout.c", broken / "fanout.c")
    shutil.copytree(REPO / "agent" / "blake3", broken / "blake3")      # #3332: built beside chunkcast.c
    (broken / "chunkcast.c").write_text("int main(void) { return not_a_symbol; }\n", encoding="ascii")
    done = _run("--source", str(tmp_path / "src"), "--bin-dir", str(tmp_path / "bin"))
    assert done.returncode != 0 and "did not compile; the previous one is untouched" in done.stderr
    assert (tmp_path / "bin" / "chunkcast").read_bytes() == before
    assert not (tmp_path / "bin" / "chunkcast.new").exists()


@needs_gcc
def test_a_program_that_is_not_chunkcast_is_not_installed(tmp_path):
    """It compiles, but it does not say chunkcast's usage: the evidence is what it says when run."""
    other = tmp_path / "src" / "agent"
    other.mkdir(parents=True)
    shutil.copy(REPO / "agent" / "fanout.c", other / "fanout.c")
    shutil.copytree(REPO / "agent" / "blake3", other / "blake3")        # #3332: built beside chunkcast.c
    (other / "chunkcast.c").write_text("int main(void) { return 0; }\n", encoding="ascii")
    done = _run("--source", str(tmp_path / "src"), "--bin-dir", str(tmp_path / "bin"))
    assert done.returncode != 0 and "did not run as expected" in done.stderr
    assert not (tmp_path / "bin" / "chunkcast").exists()


def test_installation_and_upgrade_both_build_it_after_the_boot_payload():
    """Both paths, and after the payload block -- so a prebuilt payload does not skip it."""
    for rel, call in (("tools/iso/firstboot.sh", 'bash "$SRC/tools/server-tools-build.sh" --source "$SRC"'),
                      ("tools/server-upgrade.sh", 'bash tools/server-tools-build.sh --source "$REPO_DIR"')):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert text.count(call) == 1, rel
        assert text.index(call) > text.rindex("boot-payload-build.sh"), rel


def test_the_server_runs_the_sender_by_the_name_the_script_installs():
    from server.rounds import chunksend
    engine = chunksend.ChunkEngine(None, live_members=lambda sid: [], lost_wait=lambda: 180,
                                   write_floor=lambda: 50, ledger=None)
    wave = chunksend._Wave("s", "img", "0" * 16)
    wave.round = 1
    cmd = engine._command(wave, {"index": 3}, Path("/x/p3"), 1000, 1000, None)
    assert cmd[:2] == ["chunkcast", "send"]


# --- the adversarial review of #3063 (13/11) -------------------------------------------


def test_the_installation_keeps_the_build_scripts_reason():
    """ממצא 6: `>/dev/null 2>&1` בלע את הסיבה. ה-firstboot קורא את מה שהסקריפט אמר, כותב את כולו ליומן,
    ושם את השורה האחרונה שלו -- זו שאומרת למה -- בהודעת הכישלון. ההתנהגות נבדקת בהרצה
    (test_failure_gui_server_1741, chunkcast-failed)."""
    text = (REPO / "tools/iso/firstboot.sh").read_text(encoding="utf-8")
    start = text.index('bash "$SRC/tools/server-tools-build.sh"') - 40
    call = text[start:text.index("\n# ----", start)]
    assert ">/dev/null" not in call, "the build script's reason is thrown away again"
    assert "tools_said=$(" in call and "2>&1" in call and "${tools_said##*$" in call


@needs_gcc
def test_a_program_that_says_the_usage_but_not_the_version_is_not_installed(tmp_path):
    """#3173: the server asks `chunkcast --version` before every wave (binary_problem). A binary that would
    fail that question is not installed -- the installation and the upgrade then fail on this exit."""
    other = tmp_path / "src" / "agent"
    other.mkdir(parents=True)
    shutil.copy(REPO / "agent" / "fanout.c", other / "fanout.c")
    shutil.copytree(REPO / "agent" / "blake3", other / "blake3")
    (other / "chunkcast.c").write_text(
        '#include <stdio.h>\nint main(void) { fprintf(stderr, "usage: chunkcast send|recv|apply\\n"); return 2; }\n',
        encoding="ascii")
    done = _run("--source", str(tmp_path / "src"), "--bin-dir", str(tmp_path / "bin"))
    assert done.returncode != 0 and "did not answer --version" in done.stderr, done.stdout + done.stderr
    assert not (tmp_path / "bin" / "chunkcast").exists()
    assert not (tmp_path / "bin" / "chunkcast.new").exists()


# --- #3173, owner 21/11: no sender = the upgrade fails, visibly -----------------------------

FAILING_TOOLS = """#!/usr/bin/env bash
echo "gcc: error: agent/chunkcast.c: compile failed" >&2
echo "server-tools-build: chunkcast did not compile; the previous one is untouched" >&2
exit 1
"""


@needs_bash
def test_an_upgrade_whose_chunkcast_was_not_built_rolls_back_and_says_why(tmp_path):
    """Since #3173 there is no other sender: a new tag that cannot build chunkcast is a server that cannot clone.
    The upgrade goes back to the previous tag, and the status says it was the sender and why (the script's last line)."""
    from test_installer_1131 import _fake_repo, _run_upgrade, _seed_db, _status, _upgrade_fakes, calls
    data = tmp_path / "data"
    _seed_db(data, previous="v0.48.0")
    repo = _fake_repo(tmp_path, live_ok=True)
    (repo / "tools" / "server-tools-build.sh").write_text(FAILING_TOOLS, encoding="utf-8", newline="\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _upgrade_fakes(bindir)
    proc = _run_upgrade(tmp_path, bindir, repo, data)
    log = (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    assert proc.returncode != 0, log
    assert "git checkout --detach v0.48.0" in calls(tmp_path), log
    assert "done and verified" not in log
    status = _status(data)
    assert status and status["state"] == "failed", status
    assert "מנוע השידור (chunkcast) לא נבנה" in status["error"], status["error"]
    assert "chunkcast did not compile" in status["error"] and "הוחזר ל-v0.48.0" in status["error"]
    assert "gcc: error" in log, "the build's whole output is in the upgrade log"


@needs_bash
def test_an_upgrade_whose_chunkcast_was_built_goes_on(tmp_path):
    """The guard: a build that succeeded (the stub of _fake_repo) does not stop the upgrade."""
    from test_installer_1131 import _fake_repo, _run_upgrade, _seed_db, _status, _upgrade_fakes
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _upgrade_fakes(bindir)
    proc = _run_upgrade(tmp_path, bindir, repo, data)
    log = (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    assert proc.returncode == 0, proc.stderr + log
    assert "server-tools-build: (stub) chunkcast is ready" in log and "done and verified" in log
    assert (_status(data) or {}).get("state") != "failed"


def _upgrade_functions(tmp_path: Path, script: str) -> subprocess.CompletedProcess:
    """save_chunkcast / restore_chunkcast as server-upgrade.sh defines them, run against a bin dir of the test."""
    text = (REPO / "tools/server-upgrade.sh").read_text(encoding="utf-8")
    start = text.index('CHUNKCAST_BIN="${IMAGESRV_SERVER_BIN_DIR')
    cut = tmp_path / "functions.sh"
    cut.write_text(text[start:text.index("rollback() {", start)], encoding="utf-8", newline="\n")
    return subprocess.run(["bash", "-c", f'set -euo pipefail; . "{cut.as_posix()}"; {script}'],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          env={**os.environ, "IMAGESRV_SERVER_BIN_DIR": (tmp_path / "bin").as_posix()})


@needs_bash
def test_a_rolled_back_upgrade_puts_the_previous_sender_back(tmp_path):
    """ממצא 8: העץ חוזר לתג הקודם, והבינארי שנבנה מהתג החדש נשאר. עכשיו הוא חוזר עם העץ: הקודם, בית
    בבית; ואם לא היה בינארי לפני העדכון -- זה שנבנה בו נמחק."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "chunkcast").write_bytes(b"the sender of the previous tag")
    done = _upgrade_functions(tmp_path, 'save_chunkcast; printf new > "$CHUNKCAST_BIN"; restore_chunkcast; echo "saved=[$CHUNKCAST_SAVED]"')
    assert done.returncode == 0 and done.stdout.strip() == "saved=[]", done.stdout + done.stderr
    assert (bin_dir / "chunkcast").read_bytes() == b"the sender of the previous tag"
    assert not (bin_dir / "chunkcast.before-upgrade").exists()

    (bin_dir / "chunkcast").unlink()
    done = _upgrade_functions(tmp_path, 'save_chunkcast; printf new > "$CHUNKCAST_BIN"; restore_chunkcast')
    assert done.returncode == 0, done.stderr
    assert not (bin_dir / "chunkcast").exists(), "a sender of the tag that was rolled back stayed on the server"

    (bin_dir / "chunkcast").write_bytes(b"untouched")
    done = _upgrade_functions(tmp_path, "restore_chunkcast")                 # a rollback before the build step
    assert done.returncode == 0 and (bin_dir / "chunkcast").read_bytes() == b"untouched"


def test_the_upgrade_saves_the_sender_before_it_builds_and_restores_it_in_the_rollback():
    text = (REPO / "tools/server-upgrade.sh").read_text(encoding="utf-8")
    assert text.index("\nsave_chunkcast ") < text.index("bash tools/server-tools-build.sh --source")
    rollback = text[text.index("\nrollback() {"):]
    rollback = rollback[:rollback.index("\n}\n")]
    assert "restore_chunkcast" in rollback and rollback.index("git checkout --detach") < rollback.index("restore_chunkcast")

