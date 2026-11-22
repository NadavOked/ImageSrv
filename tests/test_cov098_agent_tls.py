"""Coverage of the failure branches of server/agent/tls.py (#3580): a broken or
partial identity is refused by name, never repaired in silence."""
from __future__ import annotations

import datetime as dt
import os
import sys

import pytest

pytest.importorskip("cryptography")
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID

from server.agent import tls


@pytest.fixture
def site(tmp_path):
    """A full identity: (data_dir, ca_pem, ca_key_pem, key_pem, name, leaf_pem)."""
    tls.ensure_identity(tmp_path)
    ca_pem, ca_key_pem = tls.ensure_ca(tmp_path)
    key_pem = tls.ensure_agent_key(tmp_path)
    name = tls.server_name(ca_pem)
    leaf_pem = (tmp_path / tls.AGENT_TLS_DIRNAME / tls.LEAF_CERT_NAME).read_bytes()
    return tmp_path, ca_pem, ca_key_pem, key_pem, name, leaf_pem


def _custom_cert(key, *, cn="x", san=None, not_before=tls.NOT_BEFORE, days=30, basic=None):
    """A hand-built certificate, for the shapes sign_leaf would never produce."""
    builder = (x509.CertificateBuilder()
               .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
               .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(not_before).not_valid_after(not_before + dt.timedelta(days=days)))
    if san is not None:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in san]),
                                        critical=False)
    if basic is not None:
        builder = builder.add_extension(x509.BasicConstraints(ca=basic, path_length=None), critical=True)
    return builder.sign(key, hashes.SHA256()).public_bytes(Encoding.PEM)


@pytest.mark.parametrize("keep,gone", [(tls.CA_CERT_NAME, tls.CA_KEY_NAME),
                                       (tls.CA_KEY_NAME, tls.CA_CERT_NAME)])
def test_a_half_ca_is_refused_by_name_and_never_completed(tmp_path, keep, gone):
    tls.ensure_ca(tmp_path)
    ca_dir = tmp_path / tls.AGENT_TLS_DIRNAME / tls.CA_DIRNAME
    (ca_dir / gone).unlink()
    before = (ca_dir / keep).read_bytes()
    with pytest.raises(tls.AgentTLSError, match="חלקי"):
        tls.ensure_ca(tmp_path)
    assert not (ca_dir / gone).exists()                 # a new CA was NOT minted in silence
    assert (ca_dir / keep).read_bytes() == before


def test_a_complete_identity_has_no_problems(site):
    _, ca_pem, _, key_pem, name, leaf_pem = site
    assert tls.leaf_problems(leaf_pem, ca_pem, key_pem=key_pem, name=name) == []


def test_an_unreadable_leaf_is_one_named_problem_not_an_exception(site):
    _, ca_pem, *_ = site
    problems = tls.leaf_problems(b"not a certificate", ca_pem)
    assert len(problems) == 1 and "לא קריאה" in problems[0]


def test_a_leaf_of_another_name_or_another_key_or_another_ca_is_named(site, tmp_path_factory):
    _, ca_pem, _, _, _, leaf_pem = site
    other = tmp_path_factory.mktemp("other")
    tls.ensure_identity(other)
    other_key = tls.ensure_agent_key(other)
    other_ca, _ = tls.ensure_ca(other)
    assert any("לא של" in p for p in tls.leaf_problems(leaf_pem, ca_pem, name="imagesrv-agent-0000"))
    assert any("אינו של המפתח" in p for p in tls.leaf_problems(leaf_pem, ca_pem, key_pem=other_key))
    other_leaf = (other / tls.AGENT_TLS_DIRNAME / tls.LEAF_CERT_NAME).read_bytes()
    assert any("אינו חתום" in p for p in tls.leaf_problems(other_leaf, ca_pem))
    assert tls.leaf_problems(other_leaf, other_ca) == []


def test_an_expired_identity_names_both_the_leaf_and_the_ca(site):
    _, ca_pem, _, _, _, leaf_pem = site
    far = tls._now() + dt.timedelta(days=tls.CA_DAYS + 5)
    problems = tls.leaf_problems(leaf_pem, ca_pem, now=far)
    assert any(p.startswith("העלה פג") for p in problems)
    assert any(p.startswith("ה-CA פג") for p in problems)


def test_a_certificate_that_starts_after_2000_is_refused_for_a_slow_clock(site):
    _, ca_pem, _, _, name, _ = site
    late = _custom_cert(tls._new_key(), san=[name], not_before=tls._now() - dt.timedelta(days=1))
    assert any("לא ב-2000-01-01" in p for p in tls.leaf_problems(late, ca_pem))


def test_a_leaf_without_one_correct_san_is_named(site):
    _, ca_pem, *_ = site
    key = tls._new_key()
    for san in (None, ["imagesrv-agent-aa", "imagesrv-agent-bb"], ["wrong.example"]):
        problems = tls.leaf_problems(_custom_cert(key, san=san), ca_pem)
        assert any("SAN" in p for p in problems), san
    assert tls.leaf_names(x509.load_pem_x509_certificate(_custom_cert(key))) == []


def test_a_ca_that_is_not_a_ca_is_named(site):
    name = site[4]
    key = tls._new_key()
    leaf = _custom_cert(key, san=[name])
    for basic in (False, None):                       # ca=False, and no extension at all
        assert "תעודת ה-CA אינה CA" in tls.leaf_problems(leaf, _custom_cert(key, basic=basic))


def test_sign_leaf_refuses_a_bad_name_a_bad_csr_and_a_foreign_ca_key(site):
    _, ca_pem, ca_key_pem, key_pem, name, _ = site
    for bad in ("", None, "Not_A_Name", "imagesrv-agent-" + "a" * 60):
        with pytest.raises(tls.AgentTLSError, match="שם עלה"):
            tls.sign_leaf(tls.make_csr(key_pem), ca_pem, ca_key_pem, name=bad)
    with pytest.raises(tls.AgentTLSError, match="CSR לא תקין"):
        tls.sign_leaf(b"garbage", ca_pem, ca_key_pem, name=name)
    foreign = tls._key_pem(tls._new_key())
    with pytest.raises(tls.AgentTLSError, match="אינו תואם"):
        tls.sign_leaf(tls.make_csr(key_pem), ca_pem, foreign, name=name)


def test_sign_leaf_refuses_a_csr_whose_signature_does_not_verify(site):
    _, ca_pem, ca_key_pem, key_pem, name, _ = site
    der = bytearray(x509.load_pem_x509_csr(tls.make_csr(key_pem)).public_bytes(Encoding.DER))
    der[-1] ^= 0xFF                                   # the signature is the tail of the DER
    forged = x509.load_der_x509_csr(bytes(der)).public_bytes(Encoding.PEM)
    with pytest.raises(tls.AgentTLSError, match="חתימת ה-CSR"):
        tls.sign_leaf(forged, ca_pem, ca_key_pem, name=name)


def test_a_signed_leaf_never_outlives_its_ca(site):
    _, ca_pem, ca_key_pem, key_pem, name, _ = site
    ca = x509.load_pem_x509_certificate(ca_pem)
    late = ca.not_valid_after_utc - dt.timedelta(days=5)
    leaf = x509.load_pem_x509_certificate(
        tls.sign_leaf(tls.make_csr(key_pem), ca_pem, ca_key_pem, name=name, now=late))
    assert leaf.not_valid_after_utc == ca.not_valid_after_utc


def test_renewal_is_due_only_in_the_last_third_and_only_if_the_new_leaf_would_be_longer(site):
    _, ca_pem, ca_key_pem, key_pem, name, leaf_pem = site
    now = tls._now()
    assert tls.renewal_due(leaf_pem, ca_pem, now) is False
    assert tls.renewal_due(leaf_pem, ca_pem, now + dt.timedelta(days=70)) is True
    # a leaf already capped at the CA's end: renewing gains nothing, so not due
    ca_end = x509.load_pem_x509_certificate(ca_pem).not_valid_after_utc
    capped = tls.sign_leaf(tls.make_csr(key_pem), ca_pem, ca_key_pem, name=name,
                           now=ca_end - dt.timedelta(days=5))
    assert tls.renewal_due(capped, ca_pem, ca_end - dt.timedelta(days=1)) is False


def test_identity_is_stable_until_renewal_then_reissued_under_the_same_ca_and_name(site):
    data_dir, ca_pem, _, _, name, leaf_pem = site
    ident, issued = tls.ensure_identity(data_dir)
    assert issued is False and ident.name == name
    ident2, issued = tls.ensure_identity(data_dir, now=tls._now() + dt.timedelta(days=70))
    assert issued is True and ident2.name == name
    assert ident2.cert_path.read_bytes() != leaf_pem
    assert ident2.ca_path.read_bytes() == ca_pem        # the CA is never replaced here


def test_a_tampered_leaf_file_is_reissued_not_served(site):
    data_dir, ca_pem, _, key_pem, name, _ = site
    (data_dir / tls.AGENT_TLS_DIRNAME / tls.LEAF_CERT_NAME).write_bytes(b"PLANTED")
    ident, issued = tls.ensure_identity(data_dir)
    assert issued is True
    assert tls.leaf_problems(ident.cert_path.read_bytes(), ca_pem, key_pem=key_pem, name=name) == []


def test_an_expired_ca_stops_the_identity_by_name(site):
    with pytest.raises(tls.AgentTLSError, match="CA פג"):
        tls.ensure_identity(site[0], now=tls._now() + dt.timedelta(days=tls.CA_DAYS + 5))


def test_the_command_line_prints_the_ca_path_and_the_name(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["tls", "ca", "--data-dir", str(tmp_path)])
    tls.main()
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == str(tmp_path / tls.AGENT_TLS_DIRNAME / tls.CA_DIRNAME / tls.CA_CERT_NAME)
    assert tls.NAME_RE.fullmatch(lines[1])
    monkeypatch.setattr(sys, "argv", ["tls", "bogus", "--data-dir", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        tls.main()
    assert exc.value.code == 2


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_a_loosened_key_is_tightened_back_to_0600(tmp_path):
    tls.ensure_agent_key(tmp_path)
    key = tmp_path / tls.AGENT_TLS_DIRNAME / tls.LEAF_KEY_NAME
    key.chmod(0o644)
    tls.ensure_agent_key(tmp_path)
    assert (key.stat().st_mode & 0o777) == 0o600
