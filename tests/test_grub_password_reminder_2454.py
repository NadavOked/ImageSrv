"""#2454: the console reminds the admin to keep the server's GRUB password off the server.

The password (#2437, #2450) locks the server's recovery entry, and lives only on the server:
``/etc/imagesrv/grub-password`` and "סיסמת GRUB" in the console. On the day the server is broken
and the console does not come up, neither is there. So the health page carries a yellow row until
an admin marks "שמרתי" -- an explicit action, in the journal with who did it.

What is marked is *this* password: the mark remembers the file (inode and mtime), not the value.
A password made later -- a new install, a server that made one on upgrade, a hand-changed file,
a backup restored onto a reinstalled server -- is not this one, and the row is yellow again. The
mark goes into backups like any setting; the password never does.
"""

from __future__ import annotations

import json
import re
import subprocess
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from console_src import console_js
from native import requires_native
from server.core.atomicfile import write_atomic

REPO = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


def _console(tmp_path: Path, images_root: Path, password_file):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from conftest import complete_console_login
    from server.app import create_app
    from server.console import users

    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     health_hooks={"interfaces": lambda: []}, grub_password_file=password_file)
    conn = app.state.ctx.conn
    users.create(conn, "noc", "admin-pass-123", "admin", by="test", check_policy=False)
    users.create(conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    admin, deploy = TestClient(app), TestClient(app)
    complete_console_login(admin, conn, "noc", "admin-pass-123")
    deploy.post("/api/console/login", json={"username": "labtech", "password": "deploy-pass-1"})
    return {"conn": conn, "admin": admin, "deploy": deploy, "anon": TestClient(app),
            "dir": tmp_path / "data"}


def _row(box) -> dict | None:
    r = box["admin"].get("/api/console/health")
    assert r.status_code == 200, r.text
    rows = [c for c in r.json() if c["id"] == "grub_password"]
    assert len(rows) <= 1, rows
    return rows[0] if rows else None


def _saved(conn) -> list[tuple[str, str]]:
    return [(r["user"], r["detail"]) for r in conn.execute(
        "SELECT user, detail FROM journal WHERE event = 'grub_password_saved'")]


def test_a_new_server_is_yellow_until_an_admin_marks_it_saved(tmp_path, images_root):
    path = tmp_path / "etc" / "grub-password"
    box = _console(tmp_path, images_root, path)        # the first start makes it (#2437)
    password = path.read_text(encoding="ascii").strip()
    row = _row(box)
    assert row["state"] == "warn", row
    assert "מחוץ לשרת" in row["detail"]
    assert _row(box)["state"] == "warn"                 # looking is not marking

    r = box["admin"].post("/api/console/grub-password/saved")
    assert r.status_code == 200, r.text
    assert r.json() == {"saved": True}
    assert _row(box)["state"] == "ok"
    saved = _saved(box["conn"])
    assert [user for user, _ in saved] == ["noc"]
    assert password not in saved[0][1]


def test_a_new_password_brings_the_reminder_back(tmp_path, images_root):
    """An upgrade or a reinstall that made a password, or a hand-changed file: not the one saved."""
    path = tmp_path / "etc" / "grub-password"
    box = _console(tmp_path, images_root, path)
    assert box["admin"].post("/api/console/grub-password/saved").status_code == 200
    assert _row(box)["state"] == "ok"
    write_atomic(path, "abcde-fghjk-mnpqr-stuvw\n", encoding="ascii", mode=0o600)
    assert _row(box)["state"] == "warn"


def test_only_an_admin_marks_it_and_a_refused_mark_changes_nothing(tmp_path, images_root):
    path = tmp_path / "etc" / "grub-password"
    box = _console(tmp_path, images_root, path)
    for who, status in (("deploy", 403), ("anon", 401)):
        r = box[who].post("/api/console/grub-password/saved")
        assert r.status_code == status, (who, r.status_code, r.text)
    assert box["admin"].get("/api/console/grub-password/saved").status_code == 405
    assert _saved(box["conn"]) == []
    assert _row(box)["state"] == "warn"


def test_no_password_file_has_no_row_and_an_unreadable_one_is_not_checked(tmp_path, images_root):
    box = _console(tmp_path, images_root, None)          # development station, tests
    assert _row(box) is None
    assert box["admin"].post("/api/console/grub-password/saved").status_code == 404

    path = tmp_path / "b" / "etc" / "grub-password"
    box = _console(tmp_path / "b", images_root, path)
    path.write_text("two words\n", encoding="ascii")     # not a password GRUB can take
    r = box["admin"].post("/api/console/grub-password/saved")
    assert r.status_code == 503, r.text
    path.unlink()
    assert _row(box)["state"] == "unknown"               # principle 5: not read is not "saved"
    assert box["admin"].post("/api/console/grub-password/saved").status_code == 503
    assert _saved(box["conn"]) == []


def test_the_mark_is_in_the_backup_and_the_password_is_not(tmp_path, images_root):
    from server.system import backup

    path = tmp_path / "etc" / "grub-password"
    box = _console(tmp_path, images_root, path)
    password = path.read_text(encoding="ascii").strip()
    box["admin"].post("/api/console/grub-password/saved")
    payload, _name = backup.build_archive(box["conn"], box["dir"], "v0.82.1",
                                          datetime(2026, 11, 5, tzinfo=timezone.utc))
    import io
    import tarfile
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        members = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
    assert all(password.encode() not in body for body in members.values())
    assert b"grub_password_saved" in members["data/imagesrv.db"]


def test_the_mark_has_a_hebrew_journal_label():
    from server.console import journal_he
    assert "grub_password_saved" in journal_he.EVENTS_HE


def _js(name: str, js: str) -> str:
    m = re.search(rf"^function {name}\(.*?^\}}", js, re.M | re.S)
    return m.group(0) if m else f"function {name}() {{ return ''; }}"


@requires_native(("node", NODE))
def test_the_health_row_offers_show_and_saved_and_the_mark_posts(tmp_path):
    js = console_js()
    probe = tmp_path / "probe.cjs"
    probe.write_text("""
const esc = (x) => String(x == null ? "" : x);
const UI = { acts: (list) => list.map(([l, f]) => `<a ${f}>${l}</a>`).join(""),
             pill: (c, l) => `<pill ${c}>${l}</pill>` };
const healthStatusClass = (s) => s, healthStatusLabel = (s) => s, healthDetailHtml = () => "";
const findMachine = () => null, encodeId = (x) => x, isRawTechnical = () => false;
const HEALTH_GOTO = {};
const impactList = () => "";
const isAdmin = () => true;
let SERVER_CTX = null, opened = [], posted = [], toasts = [], healthLoads = 0;
const sheet = (o) => opened.push(o);
const post = async (p, b) => { posted.push(p); return { saved: true }; };
const toast = (t) => toasts.push(t);
const loadHealth = async () => { healthLoads++; };
""" + _js("healthRow", js) + "\n" + _js("grubPasswordSavedSheet", js) + """
(async () => {
  const out = {};
  out.warn = healthRow({ id: "grub_password", label: "x", state: "warn", detail: "" })[3];
  out.ok = healthRow({ id: "grub_password", label: "x", state: "ok", detail: "" })[3];
  SERVER_CTX = "haifa";
  out.secondary = healthRow({ id: "grub_password", label: "x", state: "warn", detail: "" })[3];
  grubPasswordSavedSheet(); out.secondaryOpened = opened.length;
  SERVER_CTX = null; grubPasswordSavedSheet();
  const confirm = opened[0];
  out.submit = confirm ? confirm.submitLabel : null;
  const after = await confirm.onSubmit({});
  if (typeof after === "function") await after();
  out.posted = posted; out.healthLoads = healthLoads;
  console.log(JSON.stringify(out));
})();
""", encoding="utf-8", newline="\n")
    got = subprocess.run([NODE, str(probe)], capture_output=True, text=True, encoding="utf-8",
                         stdin=subprocess.DEVNULL, timeout=60)
    assert got.returncode == 0, got.stderr
    out = json.loads(got.stdout)
    assert "grubPasswordSheet()" in out["warn"] and "grubPasswordSavedSheet()" in out["warn"]
    assert "grubPasswordSavedSheet()" not in out["ok"]
    assert "grubPasswordSavedSheet()" not in out["secondary"]   # a secondary marks in its own console
    assert out["secondaryOpened"] == 0
    assert out["submit"] == "שמרתי"
    assert out["posted"] == ["/grub-password/saved"] and out["healthLoads"] == 1
