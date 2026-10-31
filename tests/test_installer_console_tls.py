"""‏#703 (tracer 5): המתקין מייצר/מוודא את תעודת הקונסולה ומדפיס את טביעת
האצבע — לפני ``systemctl enable --now``, כדי שההשוואה בדפדפן בכניסה
הראשונה תהיה מול מספר שהודפס בהתקנה.

כמו ``test_installer_admin.py``: בלוק ה-Python **מוטמע** ב-
`deploy/setup-boot-server.sh` ומורץ מכאן, לא מועתק — סטייה בין השניים
היא בדיוק החור.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from server.console import console_tls

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "deploy" / "setup-boot-server.sh"


def installer_tls_python() -> str:
    text = INSTALLER.read_text(encoding="utf-8")
    match = re.search(r"<<TLSEOF\n(.*?)\nTLSEOF\b", text, flags=re.DOTALL)
    assert match, "לא נמצא heredoc TLSEOF (תעודת הקונסולה) בסקריפט ההתקנה"
    return match.group(1)


def run_installer_tls(data_dir: Path, console_host: str, monkeypatch, capsys) -> str:
    code = installer_tls_python()
    code = code.replace("$APP_DIR", REPO.resolve().as_posix())
    code = code.replace("$DATA_DIR", data_dir.resolve().as_posix())
    monkeypatch.setenv("CONSOLE_HOST", console_host)
    exec(compile(code, "deploy/setup-boot-server.sh", "exec"), {})
    return capsys.readouterr().out


def test_installer_creates_the_console_cert_and_prints_its_fingerprint(
        tmp_path, monkeypatch, capsys):
    out = run_installer_tls(tmp_path / "data", "10.10.10.8", monkeypatch, capsys)

    tls = console_tls.ensure_console_cert(tmp_path / "data", "10.10.10.8")
    assert tls.fingerprint_sha256 in out, out
    assert "10.10.10.8" in tls.sans


def test_installer_rerun_keeps_the_same_cert(tmp_path, monkeypatch, capsys):
    """התקנה חוזרת (שדרוג) אינה מחליפה תעודה שהדפדפן כבר אישר."""
    first = run_installer_tls(tmp_path / "data", "10.10.10.8", monkeypatch, capsys)
    second = run_installer_tls(tmp_path / "data", "10.10.10.8", monkeypatch, capsys)
    fp = re.search(r"([0-9A-F]{2}:){31}[0-9A-F]{2}", first).group(0)
    assert fp in second


def test_installer_has_a_console_host_flag_that_reaches_server_main():
    """הדגל קיים, ומצורף לארגומנטים של היחידה — לא רק מודפס."""
    text = INSTALLER.read_text(encoding="utf-8")
    assert re.search(r"--console-host\)", text)
    assert re.search(r'STORAGE_ARGS\+=" --console-host \$CONSOLE_HOST"', text)
