"""DCUI rendering and host-action tests (no real systemctl or network writes)."""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from server import netcfg_host, netcfg_rollback, users
from server.db import connect, get_setting
from server.dcui import actions
from server.dcui import app as dcui_app
from server.dcui import data as dcui_data
from server.dcui.app import (Runtime, _current_lines, _handle_key, render_auth,
                             render_main, render_menu, render_network,
                             render_power, render_preinstall,
                             render_preinstall_network)
from server.dcui.data import (CommandResult, NicInfo, _round, collect_snapshot,
                              demo_snapshot)

ROOT = Path(__file__).resolve().parents[1]


def _db(tmp_path):
    conn = connect(tmp_path / "imagectl.db")
    users.create(conn, "admin", "before", "admin", by="test",
                 is_builtin=True, check_policy=False)
    return conn


def _assert_screen(lines, labels):
    assert len(lines) == 25
    assert all(len(line) <= 80 for line in lines)
    joined = "\n".join(lines)
    joined.encode("ascii")
    for label in labels:
        assert label in joined


def test_all_approved_screens_are_pure_80x25_ascii():
    snapshot = demo_snapshot()
    form = {"interface": "ens20", "mode": "static", "address": "10.44.9.12",
            "netmask": "255.255.255.0", "gateway": "10.44.9.254",
            "dns": "10.44.9.2"}
    cases = [
        (render_main(snapshot), ("ImageSrv Server Operations Console",   # #1302
                                 "Hostname:", "Certificate:", "<F2>", "<F12>")),
        (render_auth(1, 0, 8), ("ImageSrv Server Operations Console",
                                "Login Name:", "Password:", "30 s lockout")),
        (render_menu(snapshot), ("Configure Management Network", "Not available here")),
        (render_network(snapshot, list(snapshot.interfaces), form, rollback_seconds=47),
         ("Prefix / Mask:", "Apply (60 s window)", "auto-rollback")),
        (render_power(snapshot),   # #1203: the focus starts on Cancel
         ("Restart / Shut Down", ">> <Cancel>", "<Restart now>", "deploy round is running")),
        (render_preinstall(replace(snapshot, pre_install=True)),
         ("- installation", "Addresses obtained by DHCP:", "F2  Set a static address")),
        (render_preinstall_network(list(snapshot.interfaces), {
            "interface": "ens20", "address": "10.44.9.12/24",
            "gateway": "10.44.9.254"}),
         ("Temporary Static Address", "IPv4 address / prefix:", "ip route replace")),
    ]
    for lines, labels in cases:
        _assert_screen(lines, labels)


def test_three_wrong_passwords_lock_auth_for_30_seconds(tmp_path):
    conn = _db(tmp_path)
    guard = actions.AuthGuard()
    assert not guard.authenticate(conn, "wrong-1", now=100.0)
    assert not guard.authenticate(conn, "wrong-2", now=101.0)
    assert not guard.authenticate(conn, "wrong-3", now=102.0)
    assert guard.failures == 3
    assert guard.locked_until == 132.0
    assert guard.seconds_left(103.0) == 29
    events = conn.execute(
        "SELECT event, user FROM journal WHERE event = 'dcui_auth_failure'"
    ).fetchall()
    assert len(events) == 3 and {row["user"] for row in events} == {"dcui"}


def test_f2_menu_has_no_admin_admin_reset(tmp_path, monkeypatch):
    """הכרעת נדב 10/10 (#1249): האיפוס ל-admin/admin מתפריט F2 הוסר — רק F9.
    כל פריט בתפריט נפתח (גם עם "y"), והסיסמה לא זזה."""
    conn = _db(tmp_path)
    for name in ("restart_services", "restart_management_network"):
        monkeypatch.setattr(actions, name,
                            lambda *a, **k: actions.ActionResult(True, "stub"))
    monkeypatch.setattr(actions, "note_tls_display", lambda conn: None)
    # #1988: "Add Disk" reads the disks on entry -- never the machine's own.
    monkeypatch.setattr(actions, "blank_disks", lambda hooks=None: ({"items": [], "hidden": []}, None))
    snapshot = demo_snapshot()
    for index in range(len(dcui_app.MENU_ITEMS)):
        screen = "\n".join(render_menu(snapshot, selected=index))
        assert "Reset Admin Password" not in screen and "admin/admin" not in screen
        runtime = _runtime(tmp_path, conn)
        runtime.screen, runtime.menu_index = "menu", index
        _press(runtime, "\n", "y")
        assert runtime.screen != "reset_confirm", dcui_app.MENU_ITEMS[index]
    assert users.verify(conn, "admin", "before") == "admin"
    assert users.flags(conn, "admin")["must_change_password"] is False


def test_power_action_runs_the_choice_without_a_typed_name(tmp_path):
    """הכרעת נדב 10/10 (#1270): אין הקלדת מילה לאישור בשום מקום — גם לא
    שם המארח ב-F12. האישור הוא מסך F12 עצמו (אחרי סיסמת admin) ו-Enter."""
    conn = _db(tmp_path)
    calls = []

    def fake(argv):
        calls.append(argv)
        return CommandResult(0, "", "")

    assert actions.power_action(conn, "shutdown", fake).ok
    assert calls == [["systemctl", "poweroff"]]
    assert actions.power_action(conn, "restart", fake).ok
    assert calls[-1] == ["systemctl", "reboot"]
    assert _count(conn, "SELECT COUNT(*) FROM journal"
                        " WHERE event = 'dcui_power_action'") == 2


def test_f12_enter_confirms_without_typing_and_esc_runs_nothing(tmp_path, monkeypatch):
    """#1270 במסך השרת: F12 → סיסמת admin → Enter מבצע את הבחירה. אין שדה
    הקלדה על המסך, ו-Esc לא מריץ כלום."""
    conn = _db(tmp_path)
    calls = []
    real = actions.power_action
    monkeypatch.setattr(actions, "power_action", lambda conn, choice: real(
        conn, choice, lambda argv: calls.append(argv) or CommandResult(0, "", "")))
    runtime = _runtime(tmp_path, conn)
    _press(runtime, KEYS.KEY_F12)
    _type(runtime, "before")
    assert runtime.screen == "power", runtime.message
    screen = "\n".join(_current_lines(runtime, time.monotonic()))
    assert "Type" not in screen and "[ " not in screen
    _press(runtime, "\x1b")
    assert runtime.screen == "main" and calls == []
    _press(runtime, KEYS.KEY_F12)
    _type(runtime, "before")
    # #1203: the focus starts on Cancel -- Tab moves it to the action first.
    _press(runtime, KEYS.KEY_DOWN, "\t", "\n")
    assert calls == [["systemctl", "poweroff"]]
    assert runtime.screen == "result" and runtime.result.ok


def test_f12_a_stray_enter_runs_nothing_until_tab_marks_the_action(tmp_path, monkeypatch):
    """#1203 (Nadav 12/10): the F12 dialog opens with the focus on Cancel, so
    an Enter that was meant for something else restarts nothing; Tab moves
    the focus to the action, and only then does Enter run it."""
    conn = _db(tmp_path)
    calls = []
    real = actions.power_action
    monkeypatch.setattr(actions, "power_action", lambda conn, choice: real(
        conn, choice, lambda argv: calls.append(argv) or CommandResult(0, "", "")))
    runtime = _runtime(tmp_path, conn)
    _press(runtime, KEYS.KEY_F12)
    _type(runtime, "before")
    _press(runtime, "\n")
    assert calls == [] and runtime.screen == "main"
    _press(runtime, KEYS.KEY_F12)
    _type(runtime, "before")
    _press(runtime, "\t", "\n")
    assert calls == [["systemctl", "reboot"]]


def test_hebrew_letters_never_reach_a_password(tmp_path):
    """#1203: passwords are English only, in the F2/F12 password and in the
    F9 reset -- a Hebrew letter from a keymap is dropped, not typed."""
    conn = _db(tmp_path)
    runtime = _runtime(tmp_path, conn)
    _press(runtime, KEYS.KEY_F2, "a", "ש", "ב", "1")
    assert runtime.screen == "auth" and runtime.password == "a1"
    _press(runtime, "\x1b", KEYS.KEY_F9, "\n", "Z", "ד", "!")
    assert runtime.screen == "reset" and runtime.reset_typed == "Z!"


def test_network_form_validation_reports_each_bad_field():
    errors = actions.validate_network_form({
        "interface": "missing", "mode": "static", "address": "999.1.1.1",
        "netmask": "255.0.255.0", "gateway": "bad", "dns": "10.0.0.1,nope",
    }, {"ens18"})
    assert set(errors) == {"interface", "address", "netmask", "gateway", "dns"}
    assert all(value.isascii() for value in errors.values())


def test_prefix_is_accepted_and_converted_to_netmask():
    form = {"interface": "ens18", "mode": "static", "address": "10.0.0.10",
            "netmask": "24", "gateway": "10.0.0.1", "dns": "10.0.0.2"}
    assert actions.validate_network_form(form, {"ens18"}) == {}
    assert actions.network_config(form).netmask == "255.255.255.0"


def test_network_apply_uses_injected_host_path_and_arms_then_confirms(tmp_path):
    conn = _db(tmp_path)
    calls = []
    hooks = {
        "netcfg_timer_active": lambda: (True, "active"),
        "now": lambda: 100.0,
        "boot_id": lambda: "boot-demo",
        "netcfg_read_conf": lambda name, root: "old config\n",
        "read_resolv": lambda: "nameserver 192.0.2.1\n",
        "netcfg_write_conf": lambda name, text, root: calls.append(
            ("write_conf", name, text, root)),
        "netcfg_write_resolv": lambda text, path: calls.append(
            ("write_resolv", text, path)),
        "netcfg_apply": lambda name: calls.append(("apply", name)),
        "netcfg_state": lambda: netcfg_host.LiveState(
            True, addresses={"ens18": ["192.0.2.10/24"]}),
        "hostname": lambda: "imagectl-test",
    }
    form = {"interface": "ens18", "mode": "dhcp", "address": "",
            "netmask": "255.255.255.0", "gateway": "", "dns": ""}
    result = actions.apply_network(conn, form, tmp_path, {"ens18"}, hooks)
    assert result.ok
    pending = netcfg_rollback.read_pending(tmp_path / "netcfg")
    assert pending is not None and pending.deadline == 160.0
    assert [entry[0] for entry in calls] == ["write_conf", "write_resolv", "apply"]
    assert actions.confirm_network(conn, tmp_path, now=120.0).ok
    assert netcfg_rollback.read_pending(tmp_path / "netcfg") is None
    assert get_setting(conn, "dcui:management_interface") == "ens18"


def test_running_round_from_db_is_rendered_as_power_warning(tmp_path):
    conn = _db(tmp_path)
    conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, "
        "wait_seconds, state, opened_by, created_at, last_join_at, started_at) "
        "VALUES ('ses_dcui', 'grp_CLONERS', 'img_demo', 'LAB303', 3, 30, "
        "'running', 'admin', '2026-09-19T08:30:00+03:00', 1, "
        "'2026-09-19T08:31:00+03:00')"
    )
    conn.execute(
        "INSERT INTO session_members (session_id, mac, state, updated_at) "
        "VALUES ('ses_dcui', '52:54:00:00:00:01', 'writing', 'now'), "
        "('ses_dcui', '52:54:00:00:00:02', 'writing', 'now')"
    )
    conn.commit()
    running = _round(conn)
    assert running is not None and running.writing == 2
    screen = "\n".join(render_power(replace(demo_snapshot(), deploy_round=running)))
    assert "A deploy round is running: 2 machines writing" in screen


def test_restart_services_only_restarts_enabled_dnsmasq(tmp_path):
    conn = _db(tmp_path)
    calls = []

    def fake(argv):
        calls.append(argv)
        if argv == ["systemctl", "is-enabled", "dnsmasq"]:
            return CommandResult(1, "disabled\n", "")
        if argv[:2] == ["systemctl", "is-active"]:
            return CommandResult(0, "active\n", "")
        return CommandResult(0, "", "")

    result = actions.restart_services(conn, fake)
    assert result.ok
    assert ["systemctl", "restart", "imagectl-server"] in calls
    assert ["systemctl", "restart", "dnsmasq"] not in calls


def test_f2_menu_has_no_wizard_rerun(tmp_path, monkeypatch):
    """הכרעת נדב 11/10 (#1190): אשף ה-web הוא רק לשרת **בלי מסך**. לשרת
    שיש לו מסך אין "Re-run Initial Setup Wizard" ב-F2 — לא בתפריט, לא
    בתיאור, ושום פריט (גם עם "y") לא מגיע ל-systemctl של יחידת האשף.
    היחידה עצמה (`imagectl-wizard-rerun.service`) נשארת לשרת בלי מסך."""
    conn = _db(tmp_path)
    calls = []
    # כל פקודה של מסך השרת עוברת ב-`data.run_argv` → `subprocess.run`.
    monkeypatch.setattr(dcui_data.subprocess, "run", lambda argv, **kw: calls.append(argv)
                        or SimpleNamespace(returncode=0, stdout="", stderr=""))
    for name in ("restart_services", "restart_management_network"):
        monkeypatch.setattr(actions, name,
                            lambda *a, **k: actions.ActionResult(True, "stub"))
    monkeypatch.setattr(actions, "note_tls_display", lambda conn: None)
    # #1988: "Add Disk" reads the disks on entry -- never the machine's own.
    monkeypatch.setattr(actions, "blank_disks", lambda hooks=None: ({"items": [], "hidden": []}, None))
    assert not any("Wizard" in item for item in dcui_app.MENU_ITEMS)
    assert not hasattr(actions, "start_wizard")
    snapshot = demo_snapshot()
    for index in range(len(dcui_app.MENU_ITEMS)):
        screen = "\n".join(render_menu(snapshot, selected=index))
        assert "Wizard" not in screen and "wizard-rerun" not in screen
        runtime = _runtime(tmp_path, conn)
        runtime.screen, runtime.menu_index = "menu", index
        _press(runtime, "\n", "y")
        shown = "\n".join(_current_lines(runtime, time.monotonic()))
        assert "Wizard" not in shown, dcui_app.MENU_ITEMS[index]
    assert not [c for c in calls if any("wizard" in arg for arg in c)], calls


def test_demo_snapshot_exposes_real_picker_shape():
    snapshot = demo_snapshot()
    assert len(snapshot.interfaces) == 3
    assert all(isinstance(nic, NicInfo) and nic.name != "lo"
               for nic in snapshot.interfaces)


def test_preinstall_snapshot_uses_status_without_creating_a_database(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    status = tmp_path / "firstboot.status"
    status.write_text("state=wizard-running\ntimestamp=2026-09-20T10:00:00Z\n",
                      encoding="utf-8")
    manifest = tmp_path / "iso-release.json"
    manifest.write_text('{"imagectl_tag":"v0.52.0"}', encoding="utf-8")
    sys_net = tmp_path / "net"
    for name, mac, carrier, state in (
        ("ens33", "00:50:56:01:02:01", "1", "up"),
        ("ens34", "00:50:56:01:02:02", "0", "down"),
    ):
        nic = sys_net / name
        nic.mkdir(parents=True)
        (nic / "address").write_text(mac, encoding="ascii")
        (nic / "carrier").write_text(carrier, encoding="ascii")
        (nic / "operstate").write_text(state, encoding="ascii")
        (nic / "speed").write_text("1000", encoding="ascii")

    def fake(argv):
        if argv[:4] == ["ip", "-j", "address", "show"]:
            return CommandResult(0, '[{"ifname":"ens33","operstate":"UP",'
                                 '"addr_info":[{"family":"inet",'
                                 '"local":"10.10.10.8","prefixlen":24}]},'
                                 '{"ifname":"ens34","operstate":"DOWN",'
                                 '"addr_info":[]}]')
        return CommandResult(1, "", "git unavailable")

    snapshot = collect_snapshot(
        data_dir, runner=fake, sys_net=sys_net, status_file=status,
        repo_dir=tmp_path / "source", manifest_file=manifest)
    assert snapshot.pre_install
    assert not (data_dir / "imagectl.db").exists()
    screen = "\n".join(render_preinstall(snapshot))
    assert "ImageSrv v0.52.0 [UNVERIFIED] - installation" in screen
    assert "ens33   00:50:56:01:02:01   10.10.10.8/24" in screen
    assert "ens34   00:50:56:01:02:02   -" in screen
    assert "https://10.10.10.8:8081" in screen
    assert "(link up)" in screen and "(no carrier)" in screen


def test_preinstall_static_address_is_temporary_ip_state_only():
    calls = []

    def fake(argv):
        calls.append(argv)
        return CommandResult(0)

    result = actions.apply_preinstall_static({
        "interface": "ens33", "address": "10.10.10.9/24",
        "gateway": "10.10.10.1",
    }, {"ens33"}, fake)
    assert result.ok
    assert calls == [
        ["ip", "addr", "flush", "dev", "ens33"],
        ["ip", "addr", "add", "10.10.10.9/24", "dev", "ens33"],
        ["ip", "route", "replace", "default", "via", "10.10.10.1",
         "dev", "ens33"],
    ]


#: מקשי curses בלי curses — ‏get_wch מחזיר int למקשי פונקציה ו-str לתווים.
KEYS = SimpleNamespace(KEY_ENTER=343, KEY_F2=266, KEY_F4=268, KEY_F5=269, KEY_F9=273,
                       KEY_F12=276, KEY_BACKSPACE=263, KEY_UP=259, KEY_DOWN=258,
                       KEY_LEFT=260, KEY_RIGHT=261, KEY_BTAB=353)   # #1203: F12's keys
NEW_PW = "Nn12345!"


def _runtime(tmp_path, conn, **snapshot):
    return Runtime(tmp_path, conn, replace(demo_snapshot(), **snapshot),
                   last_key_at=time.monotonic())


def _press(runtime, *keys):
    for key in keys:
        _handle_key(runtime, key, KEYS)


def _type(runtime, text, enter=True):
    """כמו ש-get_wch מחזיר על tty1: תווים, ו-Enter כ-"\\n" (לא 10)."""
    _press(runtime, *text)
    if enter:
        _press(runtime, "\n")


def _admin_with_mfa_and_sessions(conn):
    """admin עם MFA, קודי גיבוי, אתגר פתוח ונעילת כניסה בקונסולה."""
    conn.execute("UPDATE users SET mfa_enabled = 1, mfa_secret = 'JBSWY3DP',"
                 " mfa_enrolled_at = 'x', mfa_last_step = 7, must_change_password = 1"
                 " WHERE username = 'admin'")
    conn.execute("INSERT INTO mfa_backup_codes (username, code_hash) VALUES ('admin', 'h')")
    conn.execute("INSERT INTO mfa_challenges (token_hash, username, expires_at)"
                 " VALUES ('c', 'admin', 9e12)")
    conn.execute("INSERT INTO login_attempts (key, failures, locked_until, last_at)"
                 " VALUES ('admin|10.0.0.5', 10, 9e12, 1)")
    conn.commit()
    return conn.execute("SELECT auth_epoch FROM users WHERE username = 'admin'"
                        ).fetchone()["auth_epoch"]


def _count(conn, sql):
    return conn.execute(sql).fetchone()[0]


def test_physical_reset_replaces_the_hash_ends_sessions_and_drops_mfa(tmp_path):
    """#1249: איפוס בנוכחות פיזית — סיסמה חדשה, כל ה-sessions מתבטלות,
    ה-MFA נמחק (מי ששכח סיסמה איבד כנראה גם את הטלפון), והיומן כותב."""
    conn = _db(tmp_path)
    epoch = _admin_with_mfa_and_sessions(conn)
    result = actions.physical_reset_admin_password(conn, NEW_PW)
    assert result.ok, result
    assert users.verify(conn, "admin", NEW_PW) == "admin"
    assert users.verify(conn, "admin", "before") is None
    info = users.flags(conn, "admin")
    assert info["mfa_enabled"] is False and info["mfa_secret"] is None
    assert info["must_change_password"] is False       # המפעיל בחר את הסיסמה בעצמו
    assert _count(conn, "SELECT auth_epoch FROM users WHERE username = 'admin'") == epoch + 1
    for table in ("mfa_backup_codes", "mfa_challenges"):
        assert _count(conn, f"SELECT COUNT(*) FROM {table} WHERE username = 'admin'") == 0, table
    assert _count(conn, "SELECT COUNT(*) FROM login_attempts WHERE key LIKE 'admin|%'") == 0
    row = conn.execute("SELECT detail, user FROM journal"
                       " WHERE event = 'admin_password_reset_dcui'").fetchone()
    assert "admin password reset from the server console (DCUI)" in row["detail"]
    assert "verified=true" in row["detail"] and row["user"] == "dcui"


def test_physical_reset_uses_the_server_password_policy(tmp_path, monkeypatch):
    """אותה מדיניות כמו המתקין והקונסולה — ‏`users.validate_password`, לא עותק."""
    conn = _db(tmp_path)
    for weak in ("Aa1!", "abcdefgh!", "abcd1234", "12345678!", "Aa1!" + "x" * 125):
        result = actions.physical_reset_admin_password(conn, weak)
        assert not result.ok, weak
        assert result.message.isascii()
    assert users.verify(conn, "admin", "before") == "admin"

    def refuse(password):
        raise ValueError("nope")
    monkeypatch.setattr(users, "validate_password", refuse)
    assert not actions.physical_reset_admin_password(conn, NEW_PW).ok
    assert users.verify(conn, "admin", "before") == "admin"


def test_f9_reset_needs_no_old_password_and_returns_to_main_not_menu(tmp_path):
    conn = _db(tmp_path)
    runtime = _runtime(tmp_path, conn)
    _press(runtime, KEYS.KEY_F9)
    assert runtime.screen == "reset"                    # לא "auth" — אין סיסמה ישנה
    screen = "\n".join(_current_lines(runtime, time.monotonic()))
    assert "WARNING:" in screen and "<Enter>" in screen
    assert "Type" not in screen                         # #1270: בלי מילת אישור
    _press(runtime, *"RESET")                           # הקלדה בשלב האישור לא נאספת
    assert runtime.reset_typed == ""
    _press(runtime, "\n")                               # Enter הוא האישור
    assert runtime.reset_stage == "password", runtime.message
    _press(runtime, *"x", "\x7f")                       # Backspace של tty1 = "\x7f"
    assert runtime.reset_typed == ""
    _type(runtime, NEW_PW)
    _type(runtime, NEW_PW)
    assert runtime.result is not None and runtime.result.ok, runtime.result
    assert users.verify(conn, "admin", NEW_PW) == "admin"
    assert runtime.auth.failures == 0
    _press(runtime, "\n")
    assert runtime.screen == "main"                     # בלי אימות — לא לתפריט F2


def test_reset_three_bad_passwords_lock_for_30_seconds(tmp_path):
    conn = _db(tmp_path)
    runtime = _runtime(tmp_path, conn)
    _press(runtime, KEYS.KEY_F9, "\n")
    for weak in ("reset", "RESE", "yes"):
        assert runtime.screen == "reset" and runtime.reset_stage == "password"
        _type(runtime, weak)
    assert runtime.screen == "main"
    assert runtime.reset_guard.seconds_left(time.time()) > 0
    assert "locked" in runtime.message
    _press(runtime, KEYS.KEY_F9)
    assert runtime.screen == "main"                     # נעול — F9 לא נפתח
    assert users.verify(conn, "admin", "before") == "admin"
    assert _count(conn, "SELECT COUNT(*) FROM journal"
                        " WHERE event = 'dcui_reset_failure' AND user = 'dcui'") == 3


def test_reset_mismatch_or_weak_password_changes_nothing(tmp_path):
    conn = _db(tmp_path)
    runtime = _runtime(tmp_path, conn)
    _press(runtime, KEYS.KEY_F9)
    assert runtime.screen == "reset"
    _press(runtime, "\n")
    _type(runtime, "abcdefgh")                        # בלי ספרה ותו מיוחד
    assert runtime.reset_stage == "password"
    _type(runtime, NEW_PW)
    _type(runtime, NEW_PW + "x")                        # האימות לא תואם
    assert runtime.screen == "reset" and runtime.reset_stage == "password"
    assert "differ" in runtime.message
    assert users.verify(conn, "admin", "before") == "admin"
    _press(runtime, "\x1b")                             # Esc של get_wch
    assert runtime.screen == "main"
    assert users.verify(conn, "admin", "before") == "admin"


def test_reset_screens_are_80x25_ascii_and_main_offers_f9():
    _assert_screen(render_main(demo_snapshot()), ("<F9> Reset Password", "<F2>", "<F12>"))
    render_reset = dcui_app.render_reset         # בתוך הטסט: בקרה שלילית אינה ImportError
    for stage in ("confirm", "password", "repeat"):
        lines = render_reset(stage, "abc")
        _assert_screen(lines, ("Reset Admin Password", "WARNING:", "special character"))
        assert "Type" not in "\n".join(lines)           # #1270: בלי מילת אישור
    done = render_reset("done", result=actions.ActionResult(True, "Admin password reset."))
    _assert_screen(done, ("Admin password reset.", "Back to main screen"))
    assert "abc" not in "\n".join(render_reset("password", "abc"))   # סיסמה לא מוצגת


def test_dcui_unit_owns_tty1_without_requiring_the_web_server():
    unit = (ROOT / "install" / "imagectl-dcui.service").read_text(encoding="utf-8")
    for required in (
        "Conflicts=getty@tty1.service", "After=imagectl-server.service",
        "TTYPath=/dev/tty1", "TTYReset=yes", "Restart=always", "User=root",
        "ProtectSystem=strict", "ReadWritePaths=/var/lib/imagectl",
    ):
        assert required in unit
    assert "Requires=imagectl-server.service" not in unit


def test_wizard_units_separate_first_boot_from_explicit_rerun():
    unit = (ROOT / "install" / "imagectl-wizard.service").read_text(encoding="utf-8")
    rerun = (ROOT / "install" / "imagectl-wizard-rerun.service").read_text(encoding="utf-8")
    assert "ConditionPathExists=!/var/lib/imagectl/.firstboot-done" in unit
    assert "--rerun" not in unit and "$IMAGECTL_WIZARD_MODE" not in unit
    assert "ConditionPathExists" not in rerun
    assert "python3 -m server.wizard --rerun" in rerun


#: #1258: every form a key reaches _handle_key in -- get_wch() on tty1 gives
#: str for ordinary keys, keypad mode and the GUI (server/dcui/gui.py) give int.
BACKSPACES = ("\x7f", "\b", 127, 8, KEYS.KEY_BACKSPACE)
ENTERS = ("\n", "\r", 10, 13, KEYS.KEY_ENTER)
ESCAPES = ("\x1b", 27)


@pytest.mark.parametrize("backspace", BACKSPACES, ids=repr)
def test_backspace_erases_in_every_text_field_as_str_and_int(tmp_path, backspace):
    """#1258: Backspace erases one character in all four places that take
    typing -- the F2/F12 password, the F9 reset, the network form and the
    pre-install address -- whatever form the key arrives in."""
    conn = _db(tmp_path)
    runtime = _runtime(tmp_path, conn)
    _press(runtime, KEYS.KEY_F2, "a", "b", backspace)
    assert runtime.screen == "auth" and runtime.password == "a"
    _press(runtime, "\x1b", KEYS.KEY_F9, "\n", "x", "y", backspace)
    assert runtime.screen == "reset" and runtime.reset_typed == "x"

    runtime.screen, runtime.form_field = "network", 2           # Address
    runtime.form = {"interface": "ens18", "mode": "static", "address": "10.0.0.1",
                    "netmask": "24", "gateway": "", "dns": ""}
    _press(runtime, backspace)
    assert runtime.form["address"] == "10.0.0."

    runtime = _runtime(tmp_path, conn, pre_install=True)
    _press(runtime, KEYS.KEY_F2, "\t")                          # address / prefix
    assert runtime.screen == "preinstall_network" and runtime.form_field == 1
    runtime.form["address"] = "10.0.0.9/24"
    _press(runtime, backspace)
    assert runtime.form["address"] == "10.0.0.9/2"


@pytest.mark.parametrize("enter", ENTERS, ids=repr)
def test_enter_submits_the_password_as_str_and_int(tmp_path, enter):
    """#1258: Enter is "\\n" from get_wch() on tty1, 10/13/KEY_ENTER elsewhere."""
    conn = _db(tmp_path)
    runtime = _runtime(tmp_path, conn)
    _press(runtime, KEYS.KEY_F2, *"before", enter)
    assert runtime.screen == "menu", runtime.message


@pytest.mark.parametrize("escape", ESCAPES, ids=repr)
def test_escape_leaves_every_screen_as_str_and_int(tmp_path, escape):
    """#1258: Esc is "\\x1b" from get_wch() on tty1 and 27 elsewhere."""
    conn = _db(tmp_path)
    runtime = _runtime(tmp_path, conn)
    _press(runtime, KEYS.KEY_F2, escape)
    assert runtime.screen == "main"
    _press(runtime, KEYS.KEY_F9, escape)
    assert runtime.screen == "main"
    _press(runtime, KEYS.KEY_F2, *"before", "\n", escape)
    assert runtime.screen == "main"
