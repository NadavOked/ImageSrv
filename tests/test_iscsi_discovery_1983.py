"""#1983: discovery אינו כותב רשומות בשם שהיעד בחר, וסוד ה-CHAP אינו ב-argv.

CVE-2026-44943: בלי `-o nonpersistent`, iscsiadm 2.1.11 כותב כ-root רשומת node
לכל יעד ש-SendTargets החזיר, בשם שהיעד בחר — ו-`../` יוצא מה-DB. נמדד 06/11 על
הבינארי של Debian 13 מול stub על loopback. הטסטים כאן מזייפים את `_run` בלבד, ולכן
בודקים מה נשלח ל-iscsiadm ומה נעשה בתשובה שלו, ולא מה iscsiadm עושה בפועל.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.storage import locations as sl
from server.storage.locations import FAILED, OK, UNCHECKED, HookResult
from test_storage_locations import IQN, PORTAL, env   # noqa: F401

SECRET = "s3cret-chap-value"
PASSWORD = "node.session.auth.password"


class FakeIscsiadm:
    """`_run` מזויף: רושם כל argv, ומחזיק רשומת node כקובץ כמו iscsiadm האמיתי —
    שטוח, `<iqn>/<host>,<port>`, כפי שנמדד אחרי `-o new` בלי tpgt."""

    def __init__(self, nodes: Path, discovered: str = ""):
        self.nodes = nodes
        self.discovered = discovered
        self.calls: list[list[str]] = []
        self.fail: dict[str, HookResult] = {}

    def record(self, iqn: str = IQN) -> Path:
        return self.nodes / iqn / "10.44.3.75,3260"

    def __call__(self, cmd, timeout=30, input_text=None):
        del timeout, input_text
        if cmd[0] == "udevadm":   # #2649: לא iscsiadm, ולא ב-`calls`
            return HookResult(OK, payload={"stdout": "", "stderr": ""})
        self.calls.append(list(cmd))
        for needle, result in self.fail.items():
            if needle in cmd:
                return result
        if "discovery" in cmd:
            return HookResult(OK, payload={"stdout": self.discovered, "stderr": ""})
        record = self.record(cmd[cmd.index("-T") + 1])
        if "new" in cmd:
            record.parent.mkdir(parents=True, exist_ok=True)
            record.write_text("# BEGIN RECORD 2.1.11\n# END RECORD\n", newline="\n")
        elif not record.exists():
            return HookResult(FAILED, "iscsiadm: No records found",
                              {"stdout": "", "stderr": "", "returncode": 21})
        if "update" in cmd:
            name, value = cmd[cmd.index("-n") + 1], cmd[cmd.index("-v") + 1]
            text = record.read_text().replace("# END RECORD", f"{name} = {value}\n# END RECORD")
            record.write_text(text, newline="\n")
        return HookResult(OK, payload={"stdout": "", "stderr": ""})

    def ops(self) -> list[str]:
        """סדר הפעולות: `new`, שם הפרמטר ב-update, `show`, `login`."""
        out = []
        for cmd in self.calls:
            if "-l" in cmd:
                out.append("login")
            elif "-n" in cmd:
                out.append(cmd[cmd.index("-n") + 1])
            elif "new" in cmd:
                out.append("new")
            else:
                out.append("show")
        return out


@pytest.fixture
def iscsiadm(tmp_path, monkeypatch):
    fake = FakeIscsiadm(tmp_path / "nodes")
    monkeypatch.setattr(sl, "_run", fake)
    monkeypatch.setattr(sl, "ISCSI_NODES_DIR", fake.nodes, raising=False)
    monkeypatch.setattr(sl, "_wait_iscsi_by_path", lambda portal, iqn: "/dev/disk/by-path/x")
    return fake


# --- discovery ---------------------------------------------------------------

def test_discovery_asks_iscsiadm_not_to_write_records(iscsiadm):
    iscsiadm.discovered = f"{PORTAL},1 {IQN}\n"
    result = sl.iscsi_discover(PORTAL)
    assert result.status == OK
    cmd = iscsiadm.calls[0]
    assert "-o" in cmd and cmd[cmd.index("-o") + 1] == "nonpersistent"
    assert len(iscsiadm.calls) == 1, "discovery must not register anything by itself"
    assert result.payload == {"items": [{"portal": PORTAL, "iqn": IQN}], "rejected": 0}


@pytest.mark.parametrize("name", [
    "../../../../../tmp/pwned",
    "iqn.2005-10.org.x:a/../../../etc/cron.d/x",
    "iqn.2005-10.org.x/../../y",
    "iqn.bad",
    "not-an-iqn",
    "iqn.2005-10.org.x:a b",
])
def test_a_hostile_sendtargets_name_is_refused(iscsiadm, name):
    iscsiadm.discovered = f"{PORTAL},1 {name}\n{PORTAL},1 {IQN}\n"
    result = sl.iscsi_discover(PORTAL)
    assert result.status == OK
    assert result.payload == {"items": [{"portal": PORTAL, "iqn": IQN}], "rejected": 1}


def test_only_hostile_names_is_a_named_failure_not_an_empty_list(iscsiadm):
    iscsiadm.discovered = f"{PORTAL},1 ../../tmp/a\n{PORTAL},1 ../../tmp/b\n"
    result = sl.iscsi_discover(PORTAL)
    assert result.status == FAILED
    assert "2" in result.reason and "לא תקינ" in result.reason


def test_a_real_empty_answer_is_still_ok_and_empty(iscsiadm):
    result = sl.iscsi_discover(PORTAL)
    assert result.status == OK
    assert result.payload == {"items": [], "rejected": 0}


@pytest.mark.parametrize("name", ["eui.02004567A425678D", "naa.52004567BA64678D"])
def test_the_other_rfc3720_names_validate_params_accepts_are_kept(iscsiadm, name):
    iscsiadm.discovered = f"{PORTAL},1 {name}\n"
    assert sl.iscsi_discover(PORTAL).payload["items"] == [{"portal": PORTAL, "iqn": name}]


def test_a_portal_the_target_reported_is_not_trusted(iscsiadm):
    iscsiadm.discovered = f"../../x,1 {IQN}\n"
    result = sl.iscsi_discover(PORTAL)
    assert result.payload["items"] == [{"portal": PORTAL, "iqn": IQN}]


def test_a_failed_discovery_stays_failed(iscsiadm):
    iscsiadm.fail["discovery"] = HookResult(FAILED, "iscsiadm: No portals found")
    assert sl.iscsi_discover(PORTAL).status == FAILED


def test_test_connection_warns_about_a_refused_name(iscsiadm):
    iscsiadm.discovered = f"{PORTAL},1 ../../tmp/a\n{PORTAL},1 {IQN}\n"
    outcome = sl.test_connection("iscsi", {"portal": PORTAL, "iqn": IQN},
                                 {"iscsi_discover": sl.iscsi_discover},
                                 tmp_target=Path("unused"), server_base="")
    assert outcome["ok"] is True
    assert any("1" in warning and "לא תקינ" in warning for warning in outcome["warnings"])


def test_scan_tells_the_console_how_many_names_were_refused(env):
    env["fake"].iscsi_rejected = 2
    body = env["admin"].post("/api/console/storage-locations/scan",
                             json={"type": "iscsi", "portal": PORTAL}).json()
    assert body == {"ok": True, "items": [{"portal": PORTAL, "iqn": IQN}], "rejected": 2}


# --- login: הרשומה נוצרת רק אחרי בדיקה ---------------------------------------

@pytest.mark.parametrize("iqn,portal", [
    ("../../../../../tmp/pwned", PORTAL),
    ("iqn.2005-10.org.x:a/../../b", PORTAL),
    (IQN + "\nnode.startup = automatic", PORTAL),
    (IQN, "../../x"),
    (IQN, PORTAL + " -o delete"),
])
def test_login_refuses_before_iscsiadm_ever_runs(iscsiadm, iqn, portal):
    result = sl.iscsi_login(iqn, portal)
    assert result.status == FAILED
    assert iscsiadm.calls == []


def test_login_creates_the_record_it_needs_then_updates_then_logs_in(iscsiadm):
    assert sl.iscsi_login(IQN, PORTAL, None, True).status == OK
    assert iscsiadm.ops() == ["show", "new", "node.startup", "login"]


def test_an_existing_record_is_not_replaced(iscsiadm):
    """`-o new` על רשומה קיימת מוחק אותה (`idbm_add_node`, overwrite=1), ואיתה סוד
    ששרד כששרת שוחזר מגיבוי בלי `chap_secret` (#2460)."""
    iscsiadm.record().parent.mkdir(parents=True)
    iscsiadm.record().write_text(f"{PASSWORD} = kept\n# END RECORD\n", newline="\n")
    chap = {"user": "u1", "secret": None}
    assert sl.iscsi_login(IQN, PORTAL, chap, True).status == OK
    assert "new" not in iscsiadm.ops()
    assert f"{PASSWORD} = kept\n" in iscsiadm.record().read_text()


def test_a_broken_record_lookup_is_not_taken_for_a_missing_record(iscsiadm):
    iscsiadm.fail["-T"] = HookResult(FAILED, "iscsiadm: lock failed", {"returncode": 3})
    result = sl.iscsi_login(IQN, PORTAL)
    assert result.status == FAILED and "lock failed" in result.reason
    assert iscsiadm.ops() == ["show"]


def test_a_missing_tool_stays_unchecked(iscsiadm):
    iscsiadm.fail["-T"] = HookResult(UNCHECKED, "iscsiadm לא נמצא במערכת")
    assert sl.iscsi_login(IQN, PORTAL).status == UNCHECKED


@pytest.mark.parametrize("step", ["new", "node.session.auth.authmethod",
                                  "node.session.auth.username", PASSWORD, "node.startup"])
def test_no_step_failure_is_swallowed_on_the_way_to_login(iscsiadm, step):
    """לפני: `_run` של ה-update בלי בדיקה — אימות שלא נרשם, ו-login שרץ בכל זאת."""
    iscsiadm.fail[step] = HookResult(FAILED, f"failed at {step}")
    result = sl.iscsi_login(IQN, PORTAL, {"user": "u1", "secret": SECRET}, True)
    assert result.status == FAILED and step in result.reason
    assert "login" not in iscsiadm.ops()


# --- הסוד --------------------------------------------------------------------

def test_the_chap_secret_never_reaches_argv_and_lands_in_the_record(iscsiadm):
    assert sl.iscsi_login(IQN, PORTAL, {"user": "u1", "secret": SECRET}, True).status == OK
    assert not any(SECRET in arg for cmd in iscsiadm.calls for arg in cmd)
    text = iscsiadm.record().read_text()
    assert f"{PASSWORD} = {SECRET}\n" in text
    assert text.count(PASSWORD) == 1, "the placeholder must not survive next to the secret"


@pytest.mark.parametrize("secret", ["with space # hash = eq", '"quoted"', "עברית123456"])
def test_a_secret_with_awkward_characters_is_written_as_is(iscsiadm, secret):
    assert sl.iscsi_login(IQN, PORTAL, {"user": "u1", "secret": secret}, True).status == OK
    assert f"{PASSWORD} = {secret}\n" in iscsiadm.record().read_text(encoding="utf-8")


@pytest.mark.skipif(sl.os.name != "posix", reason="POSIX mode bits; the suite runs on the lab")
def test_the_record_stays_unreadable_to_others(iscsiadm):
    iscsiadm.record().parent.mkdir(parents=True)
    iscsiadm.record().write_text("# END RECORD\n", newline="\n")
    iscsiadm.record().chmod(0o600)
    assert sl.iscsi_login(IQN, PORTAL, {"user": "u1", "secret": SECRET}, True).status == OK
    assert stat.S_IMODE(iscsiadm.record().stat().st_mode) == 0o600


def test_every_record_iscsiadm_marked_gets_the_secret(iscsiadm, monkeypatch):
    """`-p` בלי tpgt תואם גם רשומה מ-discovery ישן: `<host>,<port>,<tpgt>/<iface>`."""
    old = iscsiadm.nodes / IQN / "10.44.3.75,3260,1" / "default"
    old.parent.mkdir(parents=True)
    iscsiadm.record().write_text("# END RECORD\n", newline="\n")

    def marks_both(cmd, timeout=30, input_text=None):
        result = iscsiadm(cmd, timeout, input_text)
        if PASSWORD in cmd:
            old.write_text(f"{PASSWORD} = {cmd[cmd.index('-v') + 1]}\n", newline="\n")
        return result

    monkeypatch.setattr(sl, "_run", marks_both)
    assert sl.iscsi_login(IQN, PORTAL, {"user": "u1", "secret": SECRET}, True).status == OK
    assert old.read_text() == f"{PASSWORD} = {SECRET}\n"
    assert f"{PASSWORD} = {SECRET}\n" in iscsiadm.record().read_text()


def test_a_record_that_was_not_marked_fails_by_name_and_login_does_not_run(
        iscsiadm, tmp_path, monkeypatch):
    """DB במקום אחר (גרסה אחרת של open-iscsi): עדיף כישלון בשם על login בלי סוד."""
    monkeypatch.setattr(sl, "ISCSI_NODES_DIR", tmp_path / "elsewhere")
    result = sl.iscsi_login(IQN, PORTAL, {"user": "u1", "secret": SECRET}, True)
    assert result.status == FAILED and "רשומ" in result.reason
    assert SECRET not in result.reason
    assert "login" not in iscsiadm.ops()


def test_a_control_character_in_the_secret_cannot_add_a_record_line(iscsiadm):
    """הכתיבה לרשומה היא שלנו עכשיו, ושורה חדשה בסוד היא פרמטר חדש ברשומה."""
    secret = "x\nnode.startup = automatic"
    result = sl.iscsi_login(IQN, PORTAL, {"user": "u1", "secret": secret}, False)
    assert result.status == FAILED
    assert "login" not in iscsiadm.ops()
    assert not any(secret in arg for cmd in iscsiadm.calls for arg in cmd)
    assert secret not in result.reason


def test_a_write_failure_is_named_without_the_secret(iscsiadm, monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(sl, "write_atomic", refuse)
    result = sl.iscsi_login(IQN, PORTAL, {"user": "u1", "secret": SECRET}, True)
    assert result.status == FAILED and "No space left" in result.reason
    assert SECRET not in result.reason
    assert "login" not in iscsiadm.ops()


def test_no_secret_means_no_password_step(iscsiadm):
    assert sl.iscsi_login(IQN, PORTAL, {"user": "u1", "secret": ""}, True).status == OK
    assert PASSWORD not in iscsiadm.ops()
    assert "node.session.auth.username" in iscsiadm.ops()
