"""‏#1372 — עדכון v0.71→v0.73.2 לא הכין את המארח ל-VM ולא הזהיר: "אומת" בירוק
בלי qemu/ovmf/guestfs ובלי imagesrv-vm. הסיבה: `server-upgrade.sh` של התג **הישן**
רץ, ובו אין צעד 3ב.

מה שנבדק כאן — **הגרסה שרצה** מוודאת את מה שהיא צריכה, בלי קשר למי שהעלה אותה:
* ‏`VmUpdate.host_problem` — אותן בדיקות שההפעלה מסרבת לפיהן, כמשפט אחד.
* ‏`VmHost.ensure` — בעלייה, מארח שאינו מוכן → deploy/vmupdate-host.sh של
  הגרסה שרצה, ביחידה מנותקת; ומה שקרה לה נאמר.
* ‏`GET /update/status` — ‏`done` שאומת, ומארח שאינו מוכן → `warning` (הקונסולה
  מציגה "אומת — אבל …"), גם כשהסקריפט הישן כתב `done` בלי אזהרה.
* ‏`GET /health` — שורת "עדכון ב-VM" צהובה, לא שקט.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from server import update as update_mod
from server.vm import vmupdate
from server.db import set_setting
from server.vm.vmhost import UNIT, VmHost
from test_vmupdate import FakeMachine, lab   # noqa: F401 — fixture

REPO = Path(__file__).resolve().parent.parent


# --- המנוע: מה חסר -------------------------------------------------------------------


def test_a_ready_host_has_no_problem(lab):
    assert lab["make"](FakeMachine()).host_problem() is None


def test_every_missing_piece_is_named_in_one_sentence(lab):
    """מה ש-v0.73.2 ראה אחרי העדכון: אין QEMU, אין OVMF של Secure Boot, אין guestfs,
    ואין imagesrv-vm — הכול בשורה אחת, לשני מצבי ה-SB."""
    engine = lab["make"](FakeMachine(missing={"qemu-system-x86_64", "OVMF_VARS_4M.ms.fd",
                                              "python3-guestfs"}, user=False))
    why = engine.host_problem()
    assert why.startswith("חסרים בשרת כלים להפעלת VM: ")
    for piece in ("qemu-system-x86_64", "OVMF_VARS_4M.ms.fd", "python3-guestfs",
                  "משתמש המערכת imagesrv-vm אינו קיים"):
        assert piece in why
    assert why.count("qemu-system-x86_64") == 1   # שני מצבי SB — שם אחד


def test_only_the_user_missing_is_still_a_problem(lab):
    why = lab["make"](FakeMachine(user=False)).host_problem()
    assert why == "משתמש המערכת imagesrv-vm אינו קיים"


# --- ההכנה בעלייה ---------------------------------------------------------------------


def _repo(tmp_path: Path, with_script=True) -> Path:
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    if with_script:
        (repo / "deploy" / "vmupdate-host.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    return repo


def test_a_host_that_is_not_ready_gets_this_versions_preparation_in_the_background(tmp_path):
    runs = []
    host = VmHost(lambda: "חסרים בשרת כלים להפעלת VM: qemu-system-x86_64", _repo(tmp_path),
                  run=lambda argv: runs.append(argv) or (True, ""), can_launch=lambda: None)
    host.ensure()
    assert len(runs) == 1
    argv = runs[0]
    assert argv[:4] == ["systemd-run", f"--unit={UNIT}", "--collect", "bash"]
    assert Path(argv[4]) == tmp_path / "repo" / "deploy" / "vmupdate-host.sh"
    assert "הופעלה ברקע" in host.warning() and f"journalctl -u {UNIT}" in host.warning()


def test_a_ready_host_runs_nothing(tmp_path):
    runs = []
    host = VmHost(lambda: None, _repo(tmp_path), run=lambda argv: runs.append(argv) or (True, ""),
                  can_launch=lambda: None)
    host.ensure()
    assert runs == [] and host.warning() is None
    assert host.health()[0] == "ok"


@pytest.mark.parametrize(("kwargs", "said"), [
    ({"run": lambda argv: (False, "Failed to connect to bus")}, "הפעלת ההכנה ברקע נכשלה: Failed to connect to bus"),
    ({"can_launch": lambda: "השרת אינו רץ כ-root"}, "ההכנה לא הופעלה בעליית השרת (השרת אינו רץ כ-root)"),
    ({"with_script": False}, "ההכנה לא הופעלה בעליית השרת (deploy/vmupdate-host.sh אינו בגרסה הזו)"),
])
def test_a_preparation_that_did_not_start_is_said_not_swallowed(tmp_path, kwargs, said):
    repo = _repo(tmp_path, with_script=kwargs.pop("with_script", True))
    host = VmHost(lambda: "x", repo, **{"run": lambda argv: (True, ""), "can_launch": lambda: None, **kwargs})
    host.ensure()
    said = said.replace("deploy/vmupdate-host.sh", str(Path("deploy") / "vmupdate-host.sh"))
    assert said in host.warning()
    assert host.health()[0] == "warn"


def test_a_readiness_check_that_breaks_is_not_ready():
    def broken():
        raise OSError("boom")
    host = VmHost(broken, ".")
    assert host.health() == ("warn", "מצב המארח לא נבדק (boom)")


# --- מה המפעיל רואה --------------------------------------------------------------------


@pytest.fixture()
def server(tmp_path, images_root, clock, monkeypatch):
    from server.console import users
    from server.app import create_app
    host = {"problem": "חסרים בשרת כלים להפעלת VM: qemu-system-x86_64"}
    monkeypatch.setattr(vmupdate.VmUpdate, "host_problem", lambda self: host["problem"])
    state = {"current": "v0.73.2"}
    hooks = {"describe": lambda repo_dir: state["current"],
             "ls_remote_tags": lambda url: (True, "", ""),
             "verify_tag": lambda url, tag, repo_dir: {"state": "signed", "detail": "test"},
             "run_upgrade": lambda repo_dir, tag: (True, "")}
    # מסך הבריאות לעולם אינו קורא את המכונה שהטסט רץ עליה (‏hostguard, ‏#113).
    health = {"ss": lambda: "", "nft_ruleset": lambda: None, "unit_active": lambda name: "",
              "http_get": lambda url: None, "http_size": lambda url: (None, None),
              "interfaces": lambda: [], "tftp_root": lambda: tmp_path / "tftp",
              "udp_sender_pids": lambda: []}
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080", now_fn=clock,
                     update_hooks=hooks, repo_dir="/repo", health_hooks=health, vm_host=True)
    users.create(app.state.ctx.conn, "nadav", "admin-pass-123", "admin", by="test",
                 is_builtin=True, check_policy=False)
    admin = TestClient(app)
    assert admin.post("/api/console/login",
                      json={"username": "nadav", "password": "admin-pass-123"}).status_code == 200
    return {"admin": admin, "app": app, "host": host, "state": state}


def _old_script_wrote_done(app, tag="v0.73.2"):
    """מה ש-server-upgrade.sh של v0.71 כותב בסוף: done, בלי warning."""
    set_setting(app.state.ctx.conn, update_mod.STATUS_KEY,
                json.dumps({"state": "done", "tag": tag, "stage": "done"}))


def test_a_verified_update_on_a_host_that_is_not_ready_is_not_green(server):
    _old_script_wrote_done(server["app"])
    status = server["admin"].get("/api/console/update/status").json()
    assert status["state"] == "done" and status["verified"] is True
    assert status["warning"].startswith("עדכון ב-VM לא זמין: חסרים בשרת כלים להפעלת VM: qemu-system-x86_64")


def test_the_warning_goes_away_when_the_host_is_ready(server):
    _old_script_wrote_done(server["app"])
    server["host"]["problem"] = None
    status = server["admin"].get("/api/console/update/status").json()
    assert status["verified"] is True and "warning" not in status


def test_the_scripts_own_warning_is_kept(server):
    set_setting(server["app"].state.ctx.conn, update_mod.STATUS_KEY,
                json.dumps({"state": "done", "tag": "v0.73.2", "warning": "עדכון ב-VM לא זמין: apt-get install נכשל"}))
    status = server["admin"].get("/api/console/update/status").json()
    assert status["warning"] == "עדכון ב-VM לא זמין: apt-get install נכשל"


def test_an_update_that_did_not_reach_this_version_is_not_blamed_on_the_vm(server):
    _old_script_wrote_done(server["app"], tag="v0.74.0")   # רץ v0.73.2 — לא אומת
    status = server["admin"].get("/api/console/update/status").json()
    assert status["verified"] is False and "warning" not in status


def test_health_says_the_vm_host_is_not_ready(server):
    rows = {row["id"]: row for row in server["admin"].get("/api/console/health").json()}
    assert rows["vm_host"]["state"] == "warn"
    assert "qemu-system-x86_64" in rows["vm_host"]["detail"]
    server["host"]["problem"] = None
    rows = {row["id"]: row for row in server["admin"].get("/api/console/health").json()}
    assert rows["vm_host"]["state"] == "ok"


def test_main_turns_the_check_on_and_starts_the_preparation():
    """‏create_runtime כבוי כברירת מחדל (בדיקות לא שואלות את המכונה) — רק main מדליק."""
    main = (REPO / "server" / "main.py").read_text(encoding="utf-8")
    assert "vm_host=True" in main
    assert "target=vm_host.ensure" in main
