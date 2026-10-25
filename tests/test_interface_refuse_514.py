"""#514: שרת שלא זיהה את ממשק השידור **אינו עולה** — לא "מדפיס וממשיך".

‏`tests/test_interface_detect.py` מוכיח ש-`_interface_for` מעלה
``InterfaceDetectionError`` (‏`ip` שנכשל, פלט פגום, אין התאמה). הגדרת
"גמור" של #514 דורשת גם את הצד השני: **החזרת ה-print-והמשך חייבת להפיל
טסט**. ב-main, השגיאה הופכת ל-``parser.error`` לפני ``create_runtime``;
בלי זה udp-sender היה משדר על ברירת המחדל של הניתוב — ברשת עם שני כרטיסים,
הרשת הרגילה (#19).

‏main רץ עם כל מה שאחרי הפרסור מזויף (כמו ב-test_server_url_ip_only.py),
והראיה היא אם ``create_runtime`` נקרא.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import server.app
import server.main


@pytest.fixture()
def fake_startup(tmp_path: Path, monkeypatch) -> dict:
    import asyncio

    import uvicorn

    captured: dict = {}

    def fake_create_runtime(*args, **kwargs):
        captured["runtime"] = (args, kwargs)
        return "rt"

    monkeypatch.setattr(server.app, "create_runtime", fake_create_runtime)
    monkeypatch.setattr(server.app, "create_agent_app", lambda rt: "agent")
    monkeypatch.setattr(server.app, "create_console_app", lambda rt: "console")
    monkeypatch.setattr(uvicorn, "Config", lambda app, **kwargs: object())
    monkeypatch.setattr(uvicorn, "Server", lambda config: object())
    monkeypatch.setattr(server.main, "serve_all", lambda servers: None)
    monkeypatch.setattr(asyncio, "run", lambda coro=None: None)
    # ‏#1074: הכתובת של הכרטיס נקראת מ-`ip` אמיתי — על לינוקס eth0/eth1 של
    # הטסט אינם קיימים. הטסט הזה על #514, לא על ה-bind.
    monkeypatch.setattr(server.main, "_address_for_interface", lambda name: "10.44.12.10")
    captured["argv"] = ["server.main", "--data-dir", str(tmp_path / "data"),
                        "--images", str(tmp_path / "img"),
                        "--server-url", "http://10.44.12.10:8080"]
    return captured


def _detection_fails(url):
    raise server.main.InterfaceDetectionError(
        "אף כרטיס אינו נושא את 10.44.12.10 — העבר --interface במפורש.")


def test_undetected_broadcast_interface_stops_the_server(monkeypatch, fake_startup, capsys):
    monkeypatch.setattr(server.main, "_interface_for", _detection_fails)
    monkeypatch.setattr("sys.argv", fake_startup["argv"])
    with pytest.raises(SystemExit) as exc:
        server.main.main()
    assert exc.value.code == 2                         # parser.error, לא קריסה
    assert "runtime" not in fake_startup               # לא הגיע ל-create_runtime
    assert "--interface" in capsys.readouterr().err    # אומר למפעיל מה לעשות


def test_explicit_interface_is_the_way_out(monkeypatch, fake_startup):
    """‏--interface הוא המוצא: הזיהוי אינו נשאל, והשרת עולה על הכרטיס שנאמר."""
    monkeypatch.setattr(server.main, "_interface_for", _detection_fails)
    monkeypatch.setattr("sys.argv", fake_startup["argv"] + ["--interface", "eth0"])
    server.main.main()
    args, kwargs = fake_startup["runtime"]
    assert kwargs.get("interface") == "eth0"


def test_detected_interface_reaches_the_runtime(monkeypatch, fake_startup):
    monkeypatch.setattr(server.main, "_interface_for", lambda url: "eth1")
    monkeypatch.setattr("sys.argv", fake_startup["argv"])
    server.main.main()
    args, kwargs = fake_startup["runtime"]
    assert kwargs.get("interface") == "eth1"
