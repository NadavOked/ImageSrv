"""#1380: a certificate the server generated before `generated.sha256` existed
(v0.75) is replaced once, when the console address is not in its SAN.

Operator certificates are never touched; a failed replacement keeps the old one
and says so; the interserver identity (the SPKI the secondary pins, #740) is a
separate identity and is not touched.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from server.console import tls as console_tls
from server.interserver.auth import generate_self_signed


def _legacy(data: Path, cn: str = "imagesrv-console", host: str = "127.0.0.1"):
    """What a pre-v0.75 server left: our cert and key, no marker at all."""
    directory = data / console_tls.CONSOLE_TLS_DIRNAME
    directory.mkdir(parents=True)
    cert, key = generate_self_signed(cn, ip_sans=[host], dns_sans=["TA"])
    (directory / "console.crt").write_bytes(cert)
    (directory / "console.key").write_text(key.decode())
    return directory, cert


@pytest.mark.parametrize("cn", ["imagesrv-console", "imagectl-console"])
def test_legacy_generated_cert_is_replaced_with_the_address(tmp_path, cn):
    directory, old = _legacy(tmp_path, cn)
    tls = console_tls.ensure_console_cert(tmp_path, "10.10.10.8", "TA")
    assert tls.regenerated and tls.migrated_legacy and tls.migration_error is None
    assert "10.10.10.8" in tls.sans
    assert tls.previous_fingerprint_sha256 == console_tls.fingerprint_sha256(old)
    assert tls.fingerprint_sha256 != tls.previous_fingerprint_sha256
    assert (directory / "console.crt.prev").read_bytes() == old
    marker = (directory / console_tls.GENERATED_MARKER).read_text().strip()
    assert marker == tls.fingerprint_sha256
    again = console_tls.ensure_console_cert(tmp_path, "10.10.10.8", "TA")
    assert not again.regenerated and again.fingerprint_sha256 == tls.fingerprint_sha256


def test_legacy_cert_that_already_covers_the_address_is_kept(tmp_path):
    _, old = _legacy(tmp_path, host="10.10.10.8")
    tls = console_tls.ensure_console_cert(tmp_path, "10.10.10.8", "TA")
    assert not tls.regenerated and tls.fingerprint_sha256 == console_tls.fingerprint_sha256(old)


def test_legacy_cert_is_not_replaced_for_loopback(tmp_path):
    _, old = _legacy(tmp_path)
    tls = console_tls.ensure_console_cert(tmp_path, "127.0.0.1", "TA")
    assert not tls.regenerated and tls.fingerprint_sha256 == console_tls.fingerprint_sha256(old)


def test_self_signed_cert_with_a_foreign_cn_is_the_operators(tmp_path):
    directory, old = _legacy(tmp_path, cn="college-console")
    tls = console_tls.ensure_console_cert(tmp_path, "10.10.10.8", "TA")
    assert not tls.regenerated and (directory / "console.crt").read_bytes() == old


def test_uploaded_cert_with_our_cn_is_never_replaced(tmp_path):
    """The operator pasted a self-signed cert whose CN happens to be ours."""
    cert, key = generate_self_signed("imagesrv-console", dns_sans=["imagesrv.college.local"])
    console_tls.install_upload(tmp_path, cert, key)
    tls = console_tls.ensure_console_cert(tmp_path, "10.10.10.8", "TA")
    assert not tls.regenerated and tls.fingerprint_sha256 == console_tls.fingerprint_sha256(cert)


def test_a_stale_generated_marker_means_replaced_by_someone_so_not_migrated(tmp_path):
    directory, old = _legacy(tmp_path)
    (directory / console_tls.GENERATED_MARKER).write_text("00:11\n")
    tls = console_tls.ensure_console_cert(tmp_path, "10.10.10.8", "TA")
    assert not tls.regenerated and (directory / "console.crt").read_bytes() == old


def test_failed_generation_keeps_the_old_pair_and_says_so(tmp_path, monkeypatch):
    directory, old = _legacy(tmp_path)
    old_key = (directory / "console.key").read_text()

    def boom(*a, **k):
        raise RuntimeError("entropy source gone")
    monkeypatch.setattr(console_tls, "generate_self_signed", boom)
    tls = console_tls.ensure_console_cert(tmp_path, "10.10.10.8", "TA")
    assert not tls.regenerated and "entropy source gone" in (getattr(tls, "migration_error", None) or "")
    assert (directory / "console.crt").read_bytes() == old
    assert (directory / "console.key").read_text() == old_key
    assert not (directory / console_tls.GENERATED_MARKER).exists()
    assert tls.fingerprint_sha256 == console_tls.fingerprint_sha256(old)


def test_failed_write_midway_restores_the_old_pair(tmp_path, monkeypatch):
    directory, old = _legacy(tmp_path)
    old_key = (directory / "console.key").read_text()
    real = console_tls.write_atomic

    def fail_on_new_cert(path, data, **kw):
        if Path(path).name == "console.crt" and data != old:
            raise OSError("disk full")
        return real(path, data, **kw)
    monkeypatch.setattr(console_tls, "write_atomic", fail_on_new_cert)
    tls = console_tls.ensure_console_cert(tmp_path, "10.10.10.8", "TA")
    assert "disk full" in (getattr(tls, "migration_error", None) or "") and not tls.regenerated
    assert (directory / "console.crt").read_bytes() == old
    assert (directory / "console.key").read_text() == old_key


def test_migration_does_not_touch_the_interserver_identity(tmp_path):
    """The secondary pins the primary's interserver SPKI (#740). The real
    identity (``storage_nodes.ensure_identity``) lives outside console-tls/ and
    must keep the same SPKI and the same bytes after a migration."""
    from server import db as db_mod
    from server.interserver import auth as interserver_auth, storage_nodes

    conn = db_mod.connect(tmp_path / "t.db")
    ident = storage_nodes.ensure_identity(conn, tmp_path, host_sans=("10.10.10.8",))
    inter = tmp_path / storage_nodes.INTERSERVER_DIRNAME
    before = {p.name: p.read_bytes() for p in inter.iterdir()}
    _legacy(tmp_path)
    assert console_tls.ensure_console_cert(tmp_path, "10.10.10.8", "TA").regenerated
    assert {p.name: p.read_bytes() for p in inter.iterdir()} == before
    again = storage_nodes.ensure_identity(conn, tmp_path, host_sans=("10.10.10.8",))
    assert again["server_spki"] == ident["server_spki"]
    assert interserver_auth.spki_sha256((inter / "server.crt").read_bytes()) == ident["server_spki"]
