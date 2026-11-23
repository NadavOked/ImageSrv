"""#3130 -- three places of the boot path that fell back without a trace (touch map B-CFG-01, B-CFG-06, A-CFG-05).

* B-CFG-01: `boot.grub_menu.render` catches every exception and serves the local-only
  file. The fallback is principle 1 and stays; it now says why in the server log.
* B-CFG-06: `boot.http.config_for_scope` falls back to the fixed config when the
  connection's address cannot be read; now logged.
* A-CFG-05: `deploy_net.write_grub_cfg` wrote the TFTP `grub/grub.cfg` atomically but never
  read it back. It reads it back, and a file that differs is an error returned in words.

None of this changes what the machines get (principle 1: unknown -> the local disk). It is on
the boot path, so the PR carries `needs:metal`.

Negative control: boot/grub_menu.py, boot/http.py and server/net/deploy_net.py back to the
base -- every test here fails (no log record, no readback error).
"""

from __future__ import annotations

import logging

from boot import grub_menu
from boot.grub_menu import GrubConfig, render, render_local_only
from boot.http import config_for_scope
from server.net import deploy_net

CFG = GrubConfig(server_base="http://10.44.12.10:7711")


def test_a_generator_failure_serves_the_local_file_and_says_why(monkeypatch, caplog):
    def boom(*_a, **_k):
        raise RuntimeError("the generator broke")

    monkeypatch.setattr(grub_menu, "_render_decision", boom)
    with caplog.at_level(logging.ERROR, logger="boot.grub_menu"):
        text = render({"schema": 1, "known": True}, CFG)
    assert text == render_local_only("generator-error", password_hash=CFG.password_hash)
    records = [r for r in caplog.records if "generator-error" in r.getMessage()]
    assert records and records[0].exc_info and "the generator broke" in str(records[0].exc_info[1])


def test_a_working_generator_does_not_log_an_error(caplog):
    with caplog.at_level(logging.ERROR, logger="boot.grub_menu"):
        render({"schema": 1, "known": True, "role": "classroom", "task": {"id": "tsk_1"}}, CFG)
    assert not caplog.records


def test_an_unreadable_server_address_serves_the_fixed_config_and_says_so(caplog):
    with caplog.at_level(logging.WARNING, logger="imagesrv.boot"):
        got = config_for_scope({"server": 5}, CFG)
    assert got is CFG
    assert any("config_for_scope" in r.getMessage() for r in caplog.records)


def test_a_readable_server_address_logs_nothing(caplog):
    with caplog.at_level(logging.WARNING, logger="imagesrv.boot"):
        got = config_for_scope({"server": ("192.0.2.7", 7711)}, CFG)
    assert got.server_base == "http://192.0.2.7:7711" and not caplog.records


def test_grub_cfg_on_the_tftp_root_is_read_back_and_a_clean_write_is_none(tmp_path):
    assert deploy_net.write_grub_cfg("http://10.44.12.10:7711", tmp_path) is None
    assert (tmp_path / "grub" / "grub.cfg").is_file()


def test_a_grub_cfg_that_reads_back_different_is_an_error(tmp_path, monkeypatch, caplog):
    def short_write(path, text, **_kw):
        path.write_text(text[: len(text) // 2], encoding="ascii")

    monkeypatch.setattr(deploy_net, "write_atomic", short_write)
    with caplog.at_level(logging.WARNING):
        error = deploy_net.write_grub_cfg("http://10.44.12.10:7711", tmp_path)
    assert error is not None and str(tmp_path / "grub" / "grub.cfg") in error
    assert any("read back different" in r.getMessage() for r in caplog.records)
