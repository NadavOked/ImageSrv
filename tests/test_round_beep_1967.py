"""#1967/#2067 -- the cloner's approved round-result tunes on the PC speaker.

Verified 22/10: Debian's busybox (1:1.37.0-6+b9) has no `beep` applet and the
initramfs has no udev -- the first version called `beep`, which does not exist
there, so it could never sound. The tone is now written as input events to the
"PC Speaker" evdev node pcspkr.ko registers. These tests read back the BYTES
written (struct input_event, x86_64: 16 bytes time + u16 type + u16 code + s32
value) and decode them: EV_SND=0x12, SND_TONE=0x02, value = Hz, 0 = silence.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_timeouts import run_sh

pytestmark = requires_native(("bash", BASH))


def _beep(tmp_path: Path, result: str, ui: dict | None = None, speaker: bool = True,
          role: str = "cloner") -> tuple[list[tuple[int, int, int]], str]:
    root, dev = tmp_path / "root", tmp_path / "dev"
    (dev / "input").mkdir(parents=True, exist_ok=True)
    for ev, name in (("event2", "AT Translated Set 2 keyboard"), ("event5", "PC Speaker" if speaker else "Power Button")):
        d = root / "sys/class/input" / ev / "device"
        d.mkdir(parents=True, exist_ok=True)
        (d / "name").write_text(name + "\n", encoding="utf-8")
    resp = tmp_path / "resp.json"
    resp.write_text(json.dumps({"ui": ui} if ui is not None else {}), encoding="utf-8")
    log = tmp_path / "agent.log"   # common.sh: LOG_FILE=$RUN_DIR/agent.log
    run_sh(f"export SYSROOT={posix(root)!r} DEVROOT={posix(dev)!r} RUN_DIR={posix(tmp_path)!r} "
           f"RESP={posix(resp)!r} D_ROLE={role}; "
           f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/jsonq.sh; . {posix(AGENT)}/lib/core/roundbeep.sh; "
           f"sleep() {{ :; }}; round_beep {result}; wait")
    node = dev / "input/event5"
    data = node.read_bytes() if node.exists() else b""
    assert len(data) % 24 == 0, len(data)
    events = [struct.unpack("<qqHHi", data[i:i + 24])[2:] for i in range(0, len(data), 24)]
    return events, log.read_text(encoding="utf-8") if log.exists() else ""


def _tones(events) -> list[int]:
    assert all(t == 0x12 and c == 0x02 for t, c, _ in events), events
    return [v for _, _, v in events]


def test_success_is_the_approved_ascending_tune(tmp_path):
    events, _ = _beep(tmp_path, "success")
    assert _tones(events) == [523, 0, 659, 0, 784, 0, 1047, 0]


def test_failure_alternates_six_tones(tmp_path):
    events, _ = _beep(tmp_path, "failure")
    assert _tones(events) == [880, 0, 587, 0] * 3


def test_the_console_setting_false_silences_it_and_missing_is_on(tmp_path):
    events, log = _beep(tmp_path, "success", ui={"cloner_beep": False})
    assert events == [] and "off in the console settings" in log
    events, _ = _beep(tmp_path / "old", "failure", ui={"language": "he"})   # an old server: no field = on
    assert _tones(events) == [880, 0, 587, 0] * 3


def test_no_speaker_is_said_in_the_log_and_never_fails(tmp_path):
    events, log = _beep(tmp_path, "success", speaker=False)
    assert events == [] and "no PC speaker (pcspkr)" in log


def test_the_cloner_and_the_build_machine_beep_a_classroom_does_not(tmp_path):
    events, _ = _beep(tmp_path, "success", role="build")   # #2067 (Nadav 24/10): the build machine too
    assert _tones(events) == [523, 0, 659, 0, 784, 0, 1047, 0]
    events, log = _beep(tmp_path / "class", "success", role="classroom")
    assert events == [] and "not a cloner or a build machine" in log


def test_server_default_is_on_and_reaches_the_cloner_and_build_hello(tmp_path, monkeypatch):
    from server import db
    from server.agent import hello
    assert db.DEFAULT_SETTINGS["cloner_beep"] == "true"
    monkeypatch.setattr(hello, "login_required", lambda *a: False)
    monkeypatch.setattr(hello, "get_setting", lambda conn, key: "false" if key == "cloner_beep" else None)
    assert hello._ui(object(), False, role="cloner")["cloner_beep"] is False
    monkeypatch.setattr(hello, "get_setting", lambda conn, key: db.DEFAULT_SETTINGS.get(key))
    assert hello._ui(object(), False, role="cloner")["cloner_beep"] is True
    assert hello._ui(object(), False, role="build")["cloner_beep"] is True    # #2067: the build machine too
    assert "cloner_beep" not in hello._ui(object(), False, role="classroom")  # not a classroom
    assert "cloner_beep" not in hello._ui(object(), False)                    # unknown MAC: nothing new


def test_pcspkr_is_a_required_module_of_the_initramfs():
    build = (AGENT.parent / "tools/build_initramfs.sh").read_text(encoding="utf-8")
    req = build[build.index("REQUIRED_MODULES=("):]
    assert "pcspkr" in req[:req.index(")")]
