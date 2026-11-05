"""#2425 -- uploading the console's TLS certificate (security scan 04/11, F3 and F4).

F3: a PEM holding both the key and the certificate, pasted into the *certificate* field, passed
-- ``load_pem_x509_certificates`` skips the key block -- and the private key landed in
``console.crt`` and ``serving/console.crt`` at 0644 (``write_atomic``'s default).

F4: ``load_cert_chain`` is all the server checked, and it loads certificates no browser takes
(ed25519, secp256k1, an EKU of clientAuth only). Port 8081 does not fall back to HTTP, so after
the restart the console was locked until someone put ``.prev`` back from the server's terminal.
Accepted now: RSA, EC on P-256/P-384, and an EKU that includes serverAuth.
"""

from __future__ import annotations

import datetime
import stat
import sys
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from server.console import tls as console_tls

NOW = datetime.datetime.now(datetime.timezone.utc)
SERVER, CLIENT = ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH

KEYS = {
    "rsa-2048": lambda: rsa.generate_private_key(public_exponent=65537, key_size=2048),
    "p-256": lambda: ec.generate_private_key(ec.SECP256R1()),
    "p-384": lambda: ec.generate_private_key(ec.SECP384R1()),
    "p-521": lambda: ec.generate_private_key(ec.SECP521R1()),
    "secp256k1": lambda: ec.generate_private_key(ec.SECP256K1()),
    "ed25519": ed25519.Ed25519PrivateKey.generate,
}


def pair(kind: str = "p-256", eku=(SERVER,), key_format=serialization.PrivateFormat.PKCS8):
    key = KEYS[kind]()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "imagesrv.college.ac.il")])
    builder = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(NOW - datetime.timedelta(days=1))
               .not_valid_after(NOW + datetime.timedelta(days=365))
               .add_extension(x509.SubjectAlternativeName([x509.DNSName("imagesrv.college.ac.il")]),
                              critical=False))
    if eku is not None:
        builder = builder.add_extension(x509.ExtendedKeyUsage(list(eku)), critical=False)
    cert = builder.sign(key, None if kind == "ed25519" else hashes.SHA256())
    key_pem = key.private_bytes(serialization.Encoding.PEM, key_format, serialization.NoEncryption())
    return cert.public_bytes(serialization.Encoding.PEM), key_pem


def _files(data_dir: Path) -> dict[str, bytes]:
    folder = data_dir / console_tls.CONSOLE_TLS_DIRNAME
    return {p.name: p.read_bytes() for p in folder.rglob("*") if p.is_file()} if folder.is_dir() else {}


# --- F3: the private key in the certificate field ------------------------------------------


@pytest.mark.parametrize("key_format", [serialization.PrivateFormat.PKCS8,
                                        serialization.PrivateFormat.TraditionalOpenSSL])
@pytest.mark.parametrize("order", ["key-first", "cert-first"])
def test_a_private_key_pasted_into_the_certificate_field_is_refused_and_nothing_is_written(
        tmp_path, key_format, order):
    cert, key = pair(key_format=key_format)
    combined = key + cert if order == "key-first" else cert + key
    with pytest.raises(console_tls.UploadRefused, match="מפתח פרטי"):
        console_tls.validate_upload(combined, key)
    with pytest.raises(console_tls.UploadRefused):
        console_tls.install_upload(tmp_path, combined, key)
    leaked = [name for name, body in _files(tmp_path).items() if b"PRIVATE KEY" in body
              and not name.startswith(console_tls.KEY_NAME)]
    assert leaked == [], f"the private key is in {leaked}"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes: Linux testrunner / CI")
def test_the_uploaded_certificate_and_its_serving_copy_are_0600(tmp_path):
    cert, key = pair()
    console_tls.install_upload(tmp_path, cert, key)
    loaded = console_tls.ensure_console_cert(tmp_path, "10.0.0.1", "srv")
    served = Path(loaded.uvicorn_kwargs()["ssl_certfile"])
    for path in (loaded.cert_path, served, loaded.key_path):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, path


# --- F4: what the server loads and a browser refuses -----------------------------------------


@pytest.mark.parametrize("kind,eku,why", [
    ("ed25519", (SERVER,), "סוג המפתח"),
    ("secp256k1", (SERVER,), "secp256k1"),
    ("p-521", (SERVER,), "secp521r1"),
    ("p-256", (CLIENT,), "serverAuth"),
    ("p-256", None, "serverAuth"),
])
def test_a_certificate_browsers_refuse_is_refused_before_it_locks_the_console(tmp_path, kind, eku, why):
    cert, key = pair(kind, eku)
    with pytest.raises(console_tls.UploadRefused, match=why):
        console_tls.validate_upload(cert, key)
    with pytest.raises(console_tls.UploadRefused):
        console_tls.install_upload(tmp_path, cert, key)
    assert _files(tmp_path).get(console_tls.CERT_NAME) is None, "the refused pair replaced the certificate"


@pytest.mark.parametrize("kind,eku", [("rsa-2048", (SERVER,)), ("p-256", (SERVER,)),
                                      ("p-384", (SERVER, CLIENT))])
def test_what_browsers_take_is_still_accepted_and_installed(tmp_path, kind, eku):
    cert, key = pair(kind, eku)
    assert console_tls.validate_upload(cert, key)["subject"] == "imagesrv.college.ac.il"
    console_tls.install_upload(tmp_path, cert, key)
    assert _files(tmp_path)[console_tls.CERT_NAME] == cert
