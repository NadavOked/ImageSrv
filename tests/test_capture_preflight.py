"""#1248: server capacity is checked before asking or changing the source."""
import json

import pytest

from server.library import capture
from server.storage.disk_reserve import SYSTEM_RESERVE_BYTES
from server.core.tasks import TOKEN_HEADER
from test_capture import make_task, setup_build_machine, task_token
from test_capture_refusals import (
    CURL_SINK, ONE_PARTITION, capture_run, refusal_reason,
)
from native import requires_native
from test_agent import BASH
from test_agent import AGENT, posix
from test_timeouts import run_sh

#: #1737: this module reasons about the system reserve -- it stays 10GiB here
#: (conftest `_host_free_space` sets it to 0 elsewhere).
pytestmark = pytest.mark.system_reserve


def preflight(server, monkeypatch, parts, free=53_300_000_000, token=None):
    task = make_task(server, setup_build_machine(server)).json()["id"]
    def capacity(path):
        assert path == server["ctx"].library.root
        if isinstance(free, Exception):
            raise free
        return free
    monkeypatch.setattr(capture, "_free_bytes", capacity)
    response = server["anon"].post(
        f"/api/v1/capture/{task}/preflight", json={"partitions": parts},
        headers={TOKEN_HEADER: task_token(server, task) if token is None else token})
    return task, response


#: ‏#1737: מה שהקליטה רשאית לתפוס — הפנוי פחות הרזרבה למערכת (53.3GB − 10GiB).
ROOM = 53_300_000_000 - SYSTEM_RESERVE_BYTES


@pytest.mark.parametrize("used,status", [
    (223_000_000_000, "refused"), (53_300_000_000, "refused"),
    # ‏#1737: הגבול זז ברזרבה — בית אחד מעליו נדחה, בדיוק עליו מותר (עם אזהרה).
    (ROOM + 1, "refused"), (ROOM, "warning"),
    (ROOM // 2 + 1, "warning"), (ROOM // 2, "ok"), (0, "ok"),
])
def test_capacity_thresholds_and_operator_reason(server, monkeypatch, used, status):
    task, response = preflight(server, monkeypatch, [{"index": 2, "used_bytes": used}])
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == status
    assert result["used_bytes"] == used
    assert result["free_bytes"] == 53_300_000_000
    assert f"תפוס {used / 1e9:.1f} GB · פנוי בשרת 53.3 GB" in result["reason"]
    assert "נשמרים למערכת 10.7 GB" in result["reason"]
    row = server["ctx"].conn.execute("SELECT * FROM tasks WHERE id = ?", (task,)).fetchone()
    assert row["state"] == ("failed" if status == "refused" else "pending")
    if status in ("refused", "warning"):
        visible = server["admin"].get("/api/console/tasks").json()
        assert next(t for t in visible if t["id"] == task)["error"] == result["reason"]
        station = server["anon"].get(f'/api/v1/agent/state?mac={row["mac"]}').json()
        assert station["task"]["error"] == result["reason"]
    assert not (server["ctx"].library.root / f".capture-{task}").exists()


def test_all_partitions_contribute(server, monkeypatch):
    _, response = preflight(server, monkeypatch, [
        {"index": 1, "used_bytes": 20_000_000_000},
        {"index": 2, "used_bytes": 40_000_000_000},
        {"index": 3, "used_bytes": 0},
    ])
    assert response.status_code == 200
    assert response.json()["status"] == "refused"
    assert response.json()["used_bytes"] == 60_000_000_000


@pytest.mark.parametrize("used", [None, -1, True, "223000000000"])
def test_unread_used_bytes_are_named_and_stop_capture(server, monkeypatch, used):
    task, response = preflight(server, monkeypatch, [{"index": 2, "used_bytes": used}])
    assert response.status_code == 200
    assert response.json()["status"] == "unchecked"
    assert "לא נבדק" in response.json()["reason"]
    assert "מחיצות 2" in response.json()["reason"]
    assert server["ctx"].conn.execute("SELECT state FROM tasks WHERE id=?", (task,)).fetchone()[0] == "failed"


def test_unread_free_space_is_not_a_pass(server, monkeypatch):
    _, response = preflight(server, monkeypatch, [{"index": 1, "used_bytes": 1}], OSError("unavailable"))
    assert response.status_code == 200
    assert response.json()["status"] == "unchecked"
    assert response.json()["free_bytes"] is None
    assert "loc_local" in response.json()["reason"]


@pytest.mark.parametrize("token,code", [("", 401), ("wrong", 403)])
def test_preflight_requires_task_ownership(server, monkeypatch, token, code):
    _, response = preflight(server, monkeypatch, [{"index": 1, "used_bytes": 1}], token=token)
    assert response.status_code == code


@requires_native(("bash", BASH), "jq")
@pytest.mark.parametrize("status", ["refused", "unchecked"])
def test_refusal_reaches_target_before_any_shrink_question(tmp_path, status):
    reason = "לא נבדק: נפח תפוס במחיצה 1" if status == "unchecked" else "תפוס 223.0 GB · פנוי בשרת 53.3 GB"
    reply = json.dumps({"status": status, "reason": reason}, ensure_ascii=False)
    curl = '#!/bin/sh\nprintf \'%s\\n\' \'' + reply + "'\n"
    box, run, out = capture_run(tmp_path, stubs={"sgdisk": ONE_PARTITION, "curl": curl},
        shell_pre='shrink_before_capture() { echo asked > "$RUN_DIR/asked"; return 1; }; ')
    assert reason in refusal_reason(box, run, out)
    assert not (run / "asked").exists()
    assert not (run / "new-manifest.json").exists()


@requires_native(("bash", BASH), "jq")
def test_warning_allows_shrink_and_reports_all_partition_bytes(tmp_path):
    curl = CURL_SINK.replace('"status":"ok"', '"status":"warning"')
    box, run, out = capture_run(tmp_path, stubs={"sgdisk": ONE_PARTITION, "curl": curl},
        shell_pre='shrink_before_capture() { echo asked > "$RUN_DIR/asked"; : > "$RUN_DIR/empty-plan"; shrink_question "$RUN_DIR/empty-plan" > "$RUN_DIR/question"; SHRINK_ERROR=stop-after-preflight; return 1; }; ')
    assert (run / "asked").exists()
    assert "space checked" in (run / "question").read_text(encoding="utf-8")
    sent = json.loads((run / "capture-space.json").read_text())
    # This fixture has an unknown filesystem: raw capture must count its full size.
    assert sent == {"partitions": [{"index": 1, "used_bytes": 204800 * 512}]}
    assert "stop-after-preflight" in refusal_reason(box, run, out)


@requires_native(("bash", BASH))
@pytest.mark.parametrize("df_rc,expected", [(0, "223000000512"), (1, "null")])
def test_used_byte_probe_requires_success_and_never_replays_journal(tmp_path, df_rc, expected):
    out = run_sh(f'''
RUN_DIR={posix(tmp_path)!r}; LOG_FILE="$RUN_DIR/probe.log"
. {posix(AGENT / 'lib/capture/manifest.sh')!r}
mount() {{ printf '%s\\n' "$*" > "$RUN_DIR/mount-args"; }}
umount() {{ return 0; }}
log() {{ :; }}
df() {{ printf 'Filesystem 1K-blocks Used Available Use%% Mounted\\n/dev/fake 999999999 217773438 1 99%% /probe\\n'; return {df_rc}; }}
_used_bytes /dev/fake ext4
''')
    assert out.strip() == expected
    assert "ro,noload" in (tmp_path / "mount-args").read_text()
