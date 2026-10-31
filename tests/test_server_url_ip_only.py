"""#498: ‏``--server-url`` חייב לשאת כתובת IP מספרית — שם מארח נדחה בעלייה.

‏``hello.off_deploy_vlan`` משווה את ה-sockname של החיבור (תמיד כתובת) ל-
hostname של ‎--server-url. עם שם מארח ההשוואה זורקת, ה-except עונה
``False``, וכל מכונה — גם ממשרד — נחשבת "בתוך וילן ההפצה": חובת הכניסה
מחוץ לווילן (#42) לא נורית לעולם. "לא הצלחנו לסווג" אינו "בפנים"
(עיקרון 5), ולכן השרת מסרב לעלות ואומר למה — לפני ``create_runtime``.

‏main רץ עם כל מה שאחרי
הפרסור מזויף, והראיה היא אם ``create_runtime`` נקרא או לא.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import server.app
import server.main


@pytest.fixture()
def fake_startup(tmp_path: Path, monkeypatch) -> dict:
    """מזייף את כל מה שאחרי אימות הדגלים; ``captured["runtime"]`` הוא
    הראיה שהשרת עבר את שער העלייה."""
    import asyncio

    import uvicorn

    captured: dict = {}

    def fake_create_runtime(*args, **kwargs):
        captured["runtime"] = args
        return "rt"

    monkeypatch.setattr(server.app, "create_runtime", fake_create_runtime)
    monkeypatch.setattr(server.app, "create_agent_app", lambda rt: "agent")
    monkeypatch.setattr(server.app, "create_console_app", lambda rt: "console")
    monkeypatch.setattr(uvicorn, "Config", lambda app, **kwargs: object())
    monkeypatch.setattr(uvicorn, "Server", lambda config: object())
    monkeypatch.setattr(server.main, "serve_all", lambda servers: None)
    monkeypatch.setattr(asyncio, "run", lambda coro=None: None)
    monkeypatch.setattr(server.main, "_interface_for", lambda url: None)
    captured["argv"] = ["server.main", "--data-dir", str(tmp_path / "data"),
                        "--images", str(tmp_path / "img")]
    return captured


def _start(monkeypatch, fake_startup: dict, server_url: str) -> None:
    monkeypatch.setattr("sys.argv",
                        fake_startup["argv"] + ["--server-url", server_url])
    server.main.main()


def test_hostname_server_url_is_refused_before_runtime(
        monkeypatch, fake_startup, capsys):
    with pytest.raises(SystemExit) as exc:
        _start(monkeypatch, fake_startup, "http://imagesrv.college:8080")
    assert exc.value.code == 2                        # parser.error, לא קריסה
    assert "runtime" not in fake_startup              # לא הגיע ל-create_runtime
    err = capsys.readouterr().err
    assert "--server-url" in err and "498" in err
    assert "imagesrv.college" in err                  # אומר מה נדחה


@pytest.mark.parametrize("server_url", [
    "http://10.44.12.10:8080",
    "http://[fd00:44::10]:8080",                      # IPv6 מילולי גם כן כתובת
])
def test_ip_literal_server_url_starts(monkeypatch, fake_startup, server_url):
    _start(monkeypatch, fake_startup, server_url)
    assert fake_startup["runtime"][2] == server_url   # הכתובת עוברת כמו שהיא
