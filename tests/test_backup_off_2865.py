"""#2865 (mockup 2865-backup-off, Nadav 11/11): the database backup can be turned off, and that is seen.

Nadav, after the install at work: "it forces me to back up. And if I don't want to? I can always
turn it on in the console, can't I?" -- today the answer was no. Now:

* the console setting ``backup:enabled`` (only ``"false"`` turns it off; missing or garbled = on),
  set through ``POST /api/console/backup-policy`` with ``enabled``;
* off: ``backup_job`` makes no backup -- not the daily one and not on an image change -- and says
  so once (``last-backup.json``, the job's own line), not every minute; on again: it backs up as usual;
* the health row is ``off`` (the operator's choice, not a warning), so the server stays "תקין";
* the orange bar "הגיבוי כבוי" -- on the home page and in the settings (console.js), and above the
  hero of the server screen (DCUI ``banner`` record) -- only while it is off;
* the installer: ``backup_enabled=false`` in the answers keeps no space, and firstboot turns the
  setting off (``setup-boot-server.sh --backup-off``).

The native installer's screen is in ``test_native_installer_backup_off_2865.py``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server import db
from server.db import connect, get_setting
from server.dcui import samples
from server.system import backup_job, backup_target
from native import requires_native
from test_backup_policy_1956 import box, console, copies, health, status, tick   # noqa: F401 -- fixtures
from test_installer_backup_1956 import SH_ONLY, _sh, disk   # noqa: F401 -- fixture
from test_installer_gui_bridge import FORM, bridge
from test_installer_gui_bridge import pytestmark as BRIDGE_ONLY
from test_installer_gui_bridge import _machine as _bridge_machine

REPO = Path(__file__).resolve().parent.parent
KEY = "backup:enabled"
NODE = shutil.which("node")


def _off(box) -> None:
    db.set_setting(box["conn"], KEY, "false")


# --- the setting -------------------------------------------------------------------------

@pytest.mark.parametrize("value, on", [(None, True), ("true", True), ("false", False), ("False", True),
                                       ("0", True), ("", True), ("off", True)])
def test_only_false_turns_it_off(value, on):
    """A garbled value does not turn the backup off in silence -- backing up is the default."""
    assert backup_job.parse_enabled(value) is on


def test_the_console_turns_it_off_and_on_and_it_is_saved_and_journaled(console):
    r = console["admin"].post("/api/console/backup-policy", json={"enabled": False})
    assert r.status_code == 200, r.text
    assert r.json()["policy"]["enabled"] is False
    assert get_setting(console["conn"], KEY) == "false"
    assert console["admin"].get("/api/console/settings").json()["backup_policy"]["enabled"] is False
    assert get_setting(console["conn"], "backup_time") is None, "the switch alone touches nothing else"
    rows = [row["detail"] for row in console["conn"].execute(
        "SELECT detail FROM journal WHERE event = 'setting_change'").fetchall()]
    assert f"{KEY}=false" in rows, rows
    assert console["admin"].post("/api/console/backup-policy", json={"enabled": True}).status_code == 200
    assert get_setting(console["conn"], KEY) == "true"
    assert console["admin"].get("/api/console/settings").json()["backup_policy"]["enabled"] is True


def test_it_starts_on(console):
    assert console["admin"].get("/api/console/settings").json()["backup_policy"]["enabled"] is True


@pytest.mark.parametrize("value", ["false", 0, 1, None, "yes"])
def test_a_switch_that_is_not_a_boolean_is_refused_and_nothing_is_saved(console, value):
    r = console["admin"].post("/api/console/backup-policy", json={"enabled": value})
    assert r.status_code == 400, r.text
    assert get_setting(console["conn"], KEY) is None


def test_with_the_hour_and_the_space_all_are_checked_and_saved_together(console, monkeypatch):
    monkeypatch.setattr(backup_job, "drive_cap_bytes", lambda path: 93 * 10 ** 9, raising=False)
    bad = console["admin"].post("/api/console/backup-policy", json={"enabled": False, "time": "25:00", "reserve_gb": 5})
    assert bad.status_code == 400 and get_setting(console["conn"], KEY) is None
    ok = console["admin"].post("/api/console/backup-policy", json={"enabled": False, "time": "03:00", "reserve_gb": 6})
    assert ok.status_code == 200, ok.text
    assert [get_setting(console["conn"], k) for k in (KEY, "backup_time", "backup_reserve_gb")] == ["false", "03:00", "6"]


def test_only_an_admin_turns_it_off(console):
    assert console["deploy"].post("/api/console/backup-policy", json={"enabled": False}).status_code == 403
    assert get_setting(console["conn"], KEY) is None


# --- the job -----------------------------------------------------------------------------

def test_off_makes_no_backup_and_says_so_once(box, capsys):
    _off(box)
    assert tick(box) == 0
    assert copies(box) == [], "no first backup while it is off"
    first = status(box)
    assert first["disabled"] is True and first["disabled_since"]
    said = capsys.readouterr().out
    assert said.count("backups are off") == 1, said
    assert tick(box) == 0 and tick(box) == 0
    assert "backups are off" not in capsys.readouterr().out, "once, not every minute"
    assert status(box)["disabled_since"] == first["disabled_since"]


def test_off_makes_no_backup_on_an_image_change_either(box):
    assert tick(box) == 0 and len(copies(box)) == 1          # on: the first backup
    before = status(box)["at"]
    _off(box)
    db.journal(box["conn"], "image_delete", "win11")          # an event that backs up when it is on
    status_file = box["data"] / "last-backup.json"
    value = json.loads(status_file.read_text(encoding="utf-8"))
    value["at"] = "2000-01-01T00:00:00+00:00"                 # long ago: the daily one is due too
    value["last_ok"] = value["at"]
    status_file.write_text(json.dumps(value), encoding="utf-8")
    assert tick(box) == 0
    assert len(copies(box)) == 1, "nothing new while it is off"
    assert status(box)["at"] == "2000-01-01T00:00:00+00:00" != before


def test_on_again_it_backs_up_and_the_mark_goes(box):
    _off(box)
    assert tick(box) == 0 and copies(box) == []
    db.set_setting(box["conn"], KEY, "true")
    assert tick(box) == 0
    assert len(copies(box)) == 1, "the first backup, as soon as it is on"
    assert "disabled" not in status(box) and status(box)["ok"] is True


def test_the_manual_backup_still_runs_when_it_is_off(box):
    """`--now` is a person asking for one backup -- the switch is about the schedule."""
    _off(box)
    assert tick(box, "--now") == 0 and len(copies(box)) == 1


# --- the health row ----------------------------------------------------------------------

def test_the_health_row_is_off_not_a_warning(box):
    _off(box)
    row = health(box)
    assert row["state"] == "off", row
    assert "כבוי" in row["detail"]


def test_the_health_row_is_unchanged_when_on(box):
    assert health(box)["state"] != "off"


# --- the server screen (DCUI) ------------------------------------------------------------

def _records(frame: str, kind: str) -> list[list[str]]:
    return [line.split("\t") for line in frame.splitlines() if line.split("\t")[0] == kind]


def test_the_dcui_bar_is_there_only_when_it_is_off_and_the_hero_stays_green():
    frames = samples.frames()
    off, on = frames["18-backup-off"], frames["1-primary-ok"]
    banner = _records(off, "banner")
    assert banner == [["banner", "warn", "הגיבוי כבוי",
                       "תקלת כונן תמחק את רשימת המחשבים וההגדרות. להדלקה: קונסולה ← הגדרות ← גיבוי."]]
    assert _records(off, "hero")[0][1:3] == ["ok", "השרת תקין"]
    assert _records(off, "sb")[0][1:3] == ["ok", "תקין"]
    assert _records(off, "prob") == []
    assert _records(on, "banner") == []


def test_the_dcui_reads_the_setting(tmp_path, monkeypatch):
    from server.dcui import data
    conn = connect(tmp_path / "imagesrv.db")
    seen = []
    real = data.Snapshot

    def capture(**kw):
        seen.append(kw.get("backup_off"))
        return real(**kw)

    monkeypatch.setattr(data, "Snapshot", capture)
    monkeypatch.setattr(data, "is_pre_install", lambda *a, **k: False)
    monkeypatch.setattr(data, "_interface_rows", lambda *a, **k: [])
    monkeypatch.setattr(data, "_console_certificate", lambda *a, **k: ("fp", (), "never"))
    monkeypatch.setattr(data, "_version", lambda *a, **k: ("v0", True))
    monkeypatch.setattr(data, "clock_state", lambda *a, **k: ("synced", ""))
    monkeypatch.setattr(data, "service_state", lambda *a, **k: data.ServiceState("active", "enabled"))
    data.collect_snapshot(tmp_path, conn=conn, status_file=tmp_path / "none", override_file=tmp_path / "none",
                          manifest_file=tmp_path / "none")
    db.set_setting(conn, KEY, "false")
    data.collect_snapshot(tmp_path, conn=conn, status_file=tmp_path / "none", override_file=tmp_path / "none",
                          manifest_file=tmp_path / "none")
    assert seen == [False, True]


def test_the_native_dcui_reads_and_draws_the_banner():
    feed = (REPO / "native-gui" / "src" / "dcui.c").read_text(encoding="utf-8")
    assert 'if (!strcmp(k, "banner"))' in feed
    draw = (REPO / "native-gui" / "src" / "screens_dcui.c").read_text(encoding="utf-8")
    body = draw[draw.index("static void main_body("):]
    assert "if (f->banner_title[0]) y += banner(" in body.split("hero(g, f")[0], "above the hero"
    assert "t->warning_bg" in draw[draw.index("static double banner("):draw.index("static void main_body(")]


# --- the console (console.js) ------------------------------------------------------------

def _js(name: str, js: str) -> str:
    m = re.search(rf"^(?:async )?function {name}\(.*?^\}}", js, re.M | re.S)
    assert m, name
    return m.group(0)


@requires_native(("node", NODE))
def test_the_console_bar_is_shown_only_when_it_is_off(tmp_path):
    js = (REPO / "server" / "static" / "console.js").read_text(encoding="utf-8")
    probe = tmp_path / "probe.cjs"
    probe.write_text("""
const esc = (x) => String(x == null ? "" : x);
let admin = true; const isAdmin = () => admin;
const settingUnreadHtml = (why) => `<span class="muted">לא נקרא: ${why}</span>`;
let HEALTH = null, SETTINGS = null;
""" + "\n".join(_js(n, js) for n in ("backupOffBarHtml", "backupOffFromHealth", "backupOffFromSettings",
                                     "backupSwitchHtml")) + """
const out = {};
out.noHealth = backupOffBarHtml(backupOffFromHealth());
HEALTH = [{ id: "backup", state: "ok" }]; out.healthOn = backupOffBarHtml(backupOffFromHealth());
HEALTH = [{ id: "backup", state: "warn" }]; out.healthWarn = backupOffBarHtml(backupOffFromHealth());
HEALTH = [{ id: "backup", state: "off" }]; out.healthOff = backupOffBarHtml(backupOffFromHealth());
admin = false; out.deployOff = backupOffBarHtml(backupOffFromHealth()); admin = true;
out.noSettings = backupOffBarHtml(backupOffFromSettings());
SETTINGS = { backup_policy: { enabled: true } }; out.settingsOn = backupOffBarHtml(backupOffFromSettings());
out.switchOn = backupSwitchHtml({ key: "backup_enabled", label: "גיבוי אוטומטי" });
SETTINGS = { backup_policy: { enabled: false } }; out.settingsOff = backupOffBarHtml(backupOffFromSettings());
out.switchOff = backupSwitchHtml({ key: "backup_enabled", label: "גיבוי אוטומטי" });
SETTINGS = { backup_policy: { time: "02:00" } }; out.switchUnknown = backupSwitchHtml({ key: "backup_enabled", label: "x" });
console.log(JSON.stringify(out));
""", encoding="utf-8", newline="\n")
    got = subprocess.run([NODE, str(probe)], capture_output=True, text=True, encoding="utf-8",
                         stdin=subprocess.DEVNULL, timeout=60)
    assert got.returncode == 0, got.stderr
    out = json.loads(got.stdout)
    for shown in ("healthOff", "settingsOff"):
        assert "<b>הגיבוי כבוי</b>" in out[shown] and "הדלקה בהגדרות" in out[shown], out[shown]
        assert "תקלת כונן תמחק את רשימת המחשבים וההגדרות." in out[shown]
    for hidden in ("noHealth", "healthOn", "healthWarn", "deployOff", "noSettings", "settingsOn"):
        assert out[hidden] == "", (hidden, out[hidden])
    assert " checked" in out["switchOn"] and ">דלוק<" in out["switchOn"]
    assert " checked" not in out["switchOff"] and ">כבוי<" in out["switchOff"]
    assert "לא נקרא" in out["switchUnknown"], "a server that did not say is not 'on'"


def test_the_console_wiring():
    js = (REPO / "server" / "static" / "console.js").read_text(encoding="utf-8")
    assert '{ key: "backup_enabled", card: "update", label: "גיבוי אוטומטי", type: "backupswitch",' in js
    assert 'await post("/backup-policy", { enabled: want });' in js
    assert "body = backupOffBarHtml(backupOffFromHealth()) +" in js, "the home page"
    assert "`${backupOffBarHtml(backupOffFromSettings())}<div class=\"set-col\">" in js, "the settings page"
    css = (REPO / "server" / "static" / "console.css").read_text(encoding="utf-8")
    assert ".page .bar-warn{" in css and "var(--warn-soft)" in css


def test_the_home_health_does_not_count_off_as_a_warning():
    js = (REPO / "server" / "static" / "console.js").read_text(encoding="utf-8")
    assert 'HEALTH.filter((c) => c.state === "bad" || c.state === "warn")' in _js("homeHealth", js)


# --- the installer -----------------------------------------------------------------------

def _validate(sysroot: Path, enabled: str, gb: str) -> subprocess.CompletedProcess:
    return _sh('ANSWER_BACKUP_TIME=02:00; ANSWER_BACKUP_ENABLED=$1; ANSWER_BACKUP_RESERVE_GB=$2; validate_backup; echo ok',
               sysroot, enabled, gb)


@SH_ONLY
@pytest.mark.parametrize("enabled, gb, ok", [("", "5", True), ("false", "", True), ("false", "5", False),
                                             ("true", "5", False), ("no", "", False), ("FALSE", "", False)])
def test_the_engine_takes_off_only_without_a_reserved_space(disk, enabled, gb, ok):
    got = _validate(disk, enabled, gb)
    assert (got.stdout.strip() == "ok") is ok, got.stderr
    if not ok:
        assert "invalid-backup-enabled" in got.stderr


@BRIDGE_ONLY
def test_the_bridge_writes_the_choice(tmp_path):
    m = _bridge_machine(tmp_path)
    rc, _, err = bridge(m, "apply", {**FORM, "backup_time": "02:00", "backup_reserve_gb": "", "backup_enabled": "false"})
    assert rc == 0, err
    assert "backup_reserve_gb=\nbackup_enabled=false\n" in (m["run"] / "answers").read_text()


@BRIDGE_ONLY
def test_the_engines_refusal_lands_on_the_steps_field(tmp_path):
    m = _bridge_machine(tmp_path)
    engine = tmp_path / "engine"
    engine.write_text("#!/bin/sh\necho 'installer: [invalid-backup-enabled] no' >&2; exit 2\n")
    engine.chmod(0o755)
    rc, out, err = bridge(m, "validate", {**FORM, "step": "7"}, ENGINE=str(engine))
    assert rc == 0, err
    assert "אינו שומר מקום" in out["errors.backup_reserve_gb"][0], out


def test_the_answers_and_firstboot_carry_it():
    answers = (REPO / "installer" / "lib" / "answers.sh").read_text(encoding="utf-8")
    assert "backup_enabled) ANSWER_BACKUP_ENABLED=$_as_value ;;" in answers
    finish = (REPO / "installer" / "lib" / "finish.sh").read_text(encoding="utf-8")
    assert "\nbackup_enabled=${ANSWER_BACKUP_ENABLED:-}\n" in finish
    firstboot = (REPO / "tools" / "iso" / "firstboot-answers.sh").read_text(encoding="utf-8")
    assert 'if [[ "$(answers_get backup_enabled)" == false ]]; then argv+=(--backup-off); fi' in firstboot


def _setup_off(tmp_path: Path) -> subprocess.CompletedProcess:
    setup = (REPO / "deploy" / "setup-boot-server.sh").read_text(encoding="utf-8")
    assert "        --backup-off) BACKUP_OFF=1; shift ;;\n" in setup
    block = re.search(r'if \(\( BACKUP_OFF \)\) && \(\( ! DRY_RUN \)\); then\n.*?python3 - <<PYEOF\n(.*?)\nPYEOF\n',
                      setup, re.S)
    assert block, "the setting is written by its own heredoc"
    code = block.group(1).replace("$APP_DIR", REPO.as_posix()).replace("$DATA_DIR", tmp_path.as_posix())
    connect(tmp_path / "imagesrv.db").close()
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"}, stdin=subprocess.DEVNULL, timeout=60)


def test_setup_turns_the_setting_off(tmp_path):
    got = _setup_off(tmp_path)
    assert got.returncode == 0, got.stderr
    conn = connect(tmp_path / "imagesrv.db")
    assert get_setting(conn, KEY) == "false"
    assert backup_target.enabled(conn) is False
