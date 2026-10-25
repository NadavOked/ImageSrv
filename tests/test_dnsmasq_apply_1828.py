"""‏#1828 — קובץ ה-dnsmasq נבדק (‏--test) לפני ה-restart, ובכשל הקודם חוזר.

‏``subprocess.run`` של ``dhcp_host`` מוחלף ברשם: הבדיקות לעולם לא מריצות
‏dnsmasq או systemctl אמיתיים. כל תרחיש נבנה מתשובות מוזרקות.
"""

from __future__ import annotations

import os

import pytest

from server import dhcp_host

OLD = "interface=eth1\ndhcp-range=10.0.0.10,10.0.0.200\n"
NEW = "interface=eth1\ndhcp-range=10.0.0.10,banana\n"


class _Done:
    def __init__(self, rc: int, err: str = ""):
        self.returncode, self.stdout, self.stderr = rc, "", err


@pytest.fixture()
def machine(monkeypatch):
    """‏`script[argv0]` — רשימת תשובות לפי הסדר (rc או חריגה); הקריאות נרשמות."""
    calls: list[list[str]] = []
    script: dict[str, list] = {"dnsmasq": [], "systemctl": []}

    def run(cmd, *a, **k):
        calls.append(list(cmd))
        answers = script[cmd[0]]
        answer = answers.pop(0) if answers else 0
        if isinstance(answer, BaseException):
            raise answer
        return _Done(answer, "bad option")

    monkeypatch.setattr(dhcp_host.subprocess, "run", run)
    return {"calls": calls, "script": script}


def _conf(tmp_path, text=OLD):
    conf = tmp_path / "dnsmasq.d" / "imagectl-dhcp.conf"
    conf.parent.mkdir()
    if text is not None:
        conf.write_text(text, encoding="utf-8")
    return conf


def test_a_config_dnsmasq_rejects_is_never_restarted_and_the_old_one_returns(tmp_path, machine):
    conf = _conf(tmp_path)
    machine["script"]["dnsmasq"] = [1]
    error = dhcp_host.apply(NEW, conf)
    assert error is not None and "dnsmasq --test" in error and "הוחזר" in error
    assert conf.read_text(encoding="utf-8") == OLD
    assert [c[0] for c in machine["calls"]] == ["dnsmasq"]          # בלי restart
    assert machine["calls"][0][:2] == ["dnsmasq", "--test"]
    assert f"--conf-dir={conf.parent}" in machine["calls"][0][2]


def test_a_test_that_could_not_run_is_a_failure_not_a_pass(tmp_path, machine):
    conf = _conf(tmp_path)
    machine["script"]["dnsmasq"] = [FileNotFoundError("dnsmasq")]
    error = dhcp_host.apply(NEW, conf)
    assert error is not None and "לא ניתן היה לבדוק" in error
    assert conf.read_text(encoding="utf-8") == OLD
    assert [c[0] for c in machine["calls"]] == ["dnsmasq"]


def test_a_failed_restart_brings_back_the_old_file_and_restarts_with_it(tmp_path, machine):
    conf = _conf(tmp_path)
    machine["script"]["systemctl"] = [1, 0]
    error = dhcp_host.apply(NEW, conf)
    assert error is not None and "הוחזר" in error and "הופעל איתו" in error
    assert conf.read_text(encoding="utf-8") == OLD
    assert [c[:2] for c in machine["calls"]] == [
        ["dnsmasq", "--test"], ["systemctl", "restart"], ["systemctl", "restart"]]


def test_a_first_config_that_fails_leaves_no_file_behind(tmp_path, machine):
    conf = _conf(tmp_path, text=None)
    machine["script"]["dnsmasq"] = [1]
    assert dhcp_host.apply(NEW, conf) is not None
    assert not conf.exists()


def test_a_good_config_is_tested_then_restarted(tmp_path, machine):
    conf = _conf(tmp_path)
    assert dhcp_host.apply(NEW, conf) is None
    assert conf.read_text(encoding="utf-8") == NEW
    assert [c[:2] for c in machine["calls"]] == [["dnsmasq", "--test"],
                                                 ["systemctl", "restart"]]


def test_the_proxy_instance_tests_its_own_file_and_rolls_back(tmp_path, machine):
    conf = _conf(tmp_path)
    machine["script"]["dnsmasq"] = [1]
    assert dhcp_host.apply_proxy(NEW, True, conf) is not None
    assert conf.read_text(encoding="utf-8") == OLD
    assert machine["calls"] == [["dnsmasq", "--test", f"--conf-file={conf}"]]


def test_a_failed_known_macs_reload_brings_back_the_old_list(tmp_path, machine):
    conf = _conf(tmp_path, text="aa:bb:cc:dd:ee:01,set:known\n")
    machine["script"]["systemctl"] = [1, 0]
    assert dhcp_host.apply_known_macs("aa:bb:cc:dd:ee:02,set:known\n", conf) is not None
    assert conf.read_text(encoding="utf-8") == "aa:bb:cc:dd:ee:01,set:known\n"


# --- אותה משפחה: מפתח 0600 וקובץ הבחירה ---------------------------------------

def test_the_private_key_writer_is_atomic(tmp_path, monkeypatch):
    from server import storage_nodes
    from test_atomic_writes_1908 import disk_fills_mid_write

    key = tmp_path / "node.key"
    key.write_bytes(b"-----OLD KEY-----\n")
    with disk_fills_mid_write(monkeypatch), pytest.raises(OSError):
        storage_nodes._write_0600(key, b"-----NEW KEY-----\n" * 40)
    assert key.read_bytes() == b"-----OLD KEY-----\n"
    storage_nodes._write_0600(key, b"-----NEW KEY-----\n")
    assert key.read_bytes() == b"-----NEW KEY-----\n"
    if os.name == "posix":
        assert (key.stat().st_mode & 0o777) == 0o600


def test_the_toolbox_selection_file_is_atomic(tmp_path, monkeypatch):
    from server import console_tools
    from test_atomic_writes_1908 import disk_fills_mid_write

    path = console_tools.write_selection_file(tmp_path, {"tools": ["a"]})
    before = path.read_text(encoding="utf-8")
    with disk_fills_mid_write(monkeypatch), pytest.raises(OSError):
        console_tools.write_selection_file(tmp_path, {"tools": ["b" * 50] * 40})
    assert path.read_text(encoding="utf-8") == before

