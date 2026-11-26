"""#4114: the agent's real `tls_config` fails closed when its trust files are missing or damaged.

test_agent_tls_3580 only ever feeds it a valid CA and a valid name (a bad server address is the one thing
it varies). Here the CA file and the agent name are what is wrong, and the real tls.sh runs.
"""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
GOOD_NAME = "imagesrv-agent-0123456789abcdef"
PEM = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
SCRIPT = """
. agent/lib/core/tls.sh
tls_config; rc=$?
printf 'rc=%s\\nSERVER=[%s]\\nWHY=[%s]\\n' "$rc" "$SERVER" "$TLS_WHY"
"""
# (case id, CA file content or None for missing, name file content or None for missing)
BROKEN = [
    ("ca-missing", None, GOOD_NAME + "\n"),
    ("ca-empty", "", GOOD_NAME + "\n"),
    ("name-file-missing", PEM, None),
    ("name-file-empty", PEM, ""),
    ("name-without-newline", PEM, GOOD_NAME),
    ("name-too-short", PEM, GOOD_NAME[:-1] + "\n"),
    ("name-too-long", PEM, GOOD_NAME + "0\n"),
    ("name-non-hex", PEM, GOOD_NAME[:-1] + "g\n"),
    ("name-upper-hex", PEM, GOOD_NAME[:-1] + "F\n"),
    ("name-wrong-prefix", PEM, "imagesrv-server-0123456789abcdef\n"),
    # 16 hex digits and no prefix: only the prefix check refuses it (the suffix equals the whole name)
    ("name-bare-16-hex", PEM, "0123456789abcdef\n"),
]


def _run(tmp_path, ca, name):
    ca_file, name_file, run_dir = tmp_path / "agent-ca.pem", tmp_path / "agent-name", tmp_path / "run"
    run_dir.mkdir()
    if ca is not None:
        ca_file.write_text(ca, encoding="ascii", newline="\n")
    if name is not None:
        name_file.write_text(name, encoding="ascii", newline="\n")
    env = {k: v for k, v in os.environ.items() if k not in ("SERVER", "CURL_HOME", "TLS_CA")}
    env.update(IMAGESRV_SERVER="https://10.44.0.1:7712", SERVER_CA_FILE=ca_file.as_posix(),
               AGENT_NAME_FILE=name_file.as_posix(), RUN_DIR=run_dir.as_posix())
    result = subprocess.run([BASH, "-c", SCRIPT], env=env, cwd=ROOT, capture_output=True, encoding="utf-8",
                            errors="replace", timeout=10, stdin=subprocess.DEVNULL)
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    return result, fields, run_dir / "agent-tls"


@pytest.mark.parametrize("ca,name", [c[1:] for c in BROKEN], ids=[c[0] for c in BROKEN])
def test_tls_config_fails_closed_without_valid_trust_files(tmp_path, ca, name):
    result, fields, curl_home = _run(tmp_path, ca, name)
    assert fields.get("rc") == "1", (result.stdout, result.stderr)
    assert fields["SERVER"] == "[]", "no server address may be offered without verified trust"
    assert fields["WHY"].startswith("[TLS: פרטי האמון"), fields["WHY"]
    assert not curl_home.exists() or not any(curl_home.iterdir()), "no curl config may be written"


def test_tls_config_succeeds_with_a_valid_ca_and_name(tmp_path):
    """The control for the cases above: the same harness, valid files. A rejecting-everything tls_config fails here."""
    result, fields, curl_home = _run(tmp_path, PEM, GOOD_NAME + "\n")
    assert fields.get("rc") == "0", (result.stdout, result.stderr)
    assert fields["SERVER"] == f"[https://{GOOD_NAME}:7712]"
    curlrc = (curl_home / ".curlrc").read_text(encoding="utf-8")
    assert 'proto-redir = "=https"' in curlrc and "agent-ca.pem" in curlrc
