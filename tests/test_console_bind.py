"""‏#770 (tracer-4 של #703): הקונסולה נקשרת פר-ממשק, לא ל-0.0.0.0.

בפריסת המעבדה שרת הקונסולה (8081) נקשר ל-``0.0.0.0`` ולכן (א) התנגש עם
שרת ה-preseed על ``10.44.3.1:8081`` ו-(ב) חשף את API הניהול על וילן
ההפצה/הכיתות — בדיוק מה שמודל ה-FW אוסר (#703: "הגבול הוא ה-socket").

מודל ה-FW: הקונסולה על ממשק המשרדים/שרתים **בלבד**. לכן הקונסולה מקבלת
דגל ``--console-host`` משלה, בנפרד מ-``--host`` (הסוכן על וילן
ההפצה). ברירת המחדל fail-closed — ‏loopback, לא ``0.0.0.0``: בלי כתובת
ניהול מפורשת הקונסולה אינה נגישה מאף כרטיס חיצוני, ואינה תופסת 8081
בממשקי ההפצה (עיקרון 5 — השומר עובד כברירת מחדל, לא רק אם זכרו דגל).

שרשרת הראיות שתי חוליות, כל אחת בטסט משלה: ‏``build_parser`` נותן
ברירת מחדל שאינה ``0.0.0.0``, ו-``main`` מוליך את הערך של ``--console-host``
דווקא למאזין הקונסולה — ולא לזה של הסוכן.
"""

from __future__ import annotations

from pathlib import Path

import server.app
import server.main


def test_agent_host_defaults_to_unset_not_all_interfaces() -> None:
    """#1074: ברירת המחדל של ``--host`` אינה 0.0.0.0 — ‏None, ו-main
    ממלא מכתובת כרטיס ההפצה."""
    args = server.main.build_parser().parse_args(
        ["--server-url", "http://10.44.12.10:8080"])
    assert getattr(args, "host", "0.0.0.0") is None


def test_console_host_defaults_to_loopback_not_all_interfaces() -> None:
    """הראיה הישירה: ברירת המחדל של ``--console-host`` היא loopback,
    ובוודאי לא ``0.0.0.0`` — הקונסולה לא עולה על כל הכרטיסים בלי שביקשו.

    ‏getattr עם ברירת מחדל ``0.0.0.0`` נותן כשל **התנהגותי** (AssertionError
    עם הערך בפועל) גם כשהדגל עדיין לא קיים, במקום AttributeError."""
    args = server.main.build_parser().parse_args(
        ["--server-url", "http://10.44.12.10:8080"])
    console_host = getattr(args, "console_host", "0.0.0.0")
    assert console_host == "127.0.0.1"
    assert console_host != "0.0.0.0"


def run_main(tmp_path: Path, monkeypatch, *extra_argv: str) -> dict[str, list[dict]]:
    """מריץ את ``main`` עם כל החוליות מסביב מנוטרלות (runtime, uvicorn,
    asyncio) ומחזיר, לכל אפליקציה, את רשימת ה-``uvicorn.Config`` שנבנו
    לה — **רשימה**, כי הקונסולה יכולה להיות קשורה ליותר ממאזין אחד (#904)."""
    import asyncio

    import uvicorn

    configs: dict[str, list[dict]] = {}

    def fake_config(app, **kwargs):
        configs.setdefault(app, []).append(kwargs)
        return object()

    monkeypatch.setattr(server.app, "create_runtime", lambda *a, **k: "rt")
    monkeypatch.setattr(server.app, "create_agent_app", lambda rt: "agent")
    monkeypatch.setattr(server.app, "create_console_app", lambda rt: "console")
    monkeypatch.setattr(uvicorn, "Config", fake_config)
    monkeypatch.setattr(uvicorn, "Server", lambda config: object())
    monkeypatch.setattr(server.main, "serve_all", lambda servers: None)
    # #1310: the primary alive listener has its own test (test_primary_alive.py)
    monkeypatch.setattr(server.main, "_primary_alive", lambda *a, **k: None)
    monkeypatch.setattr(asyncio, "run", lambda coro=None: None)
    monkeypatch.setattr(server.main, "_interface_for", lambda url: None)
    monkeypatch.setattr("sys.argv", [
        "server.main", "--server-url", "http://10.44.12.10:8080",
        "--data-dir", str(tmp_path / "data"), "--images", str(tmp_path / "img"),
        *extra_argv,
    ])
    server.main.main()
    return configs


def test_main_binds_console_to_console_host_not_agent_host(
        tmp_path: Path, monkeypatch) -> None:
    """‏main היא החוליה שאין לה טסט אחר: המאזין של הקונסולה חייב להיקשר
    ל-``--console-host`` (כרטיס הניהול), ולא ל-``--host`` (ההפצה). דגל
    שמתפרס ואינו מגיע דווקא למאזין הנכון נראה כמו תיקון שעובד.

    שאר החוליות (runtime, הרצת uvicorn בפועל) מנוטרלות — נבדק רק לאיזה
    ``host`` נבנה כל ``uvicorn.Config``. הקונסולה על כרטיס ניהול נפרד,
    הסוכן על 0.0.0.0 של ההפצה — ו-``--host`` **אינו** בין מאזיני הקונסולה."""
    # ‏--host 0.0.0.0 (ההפצה) לצד ``--console-host`` מפורש (כרטיס הניהול):
    # הערך של ``--console-host`` חייב להגיע דווקא למאזין הקונסולה, והסוכן
    # נשאר על ``--host``.
    configs = run_main(tmp_path, monkeypatch,
                       "--host", "0.0.0.0", "--console-host", "10.44.12.10")

    console_hosts = [c["host"] for c in configs["console"]]
    assert "10.44.12.10" in console_hosts
    assert [c["host"] for c in configs["agent"]] == ["0.0.0.0"]
    assert "0.0.0.0" not in console_hosts


def test_console_on_a_management_nic_also_listens_on_loopback(
        tmp_path: Path, monkeypatch) -> None:
    """‏#904: חלון ה-pairing של המשני (``require_local``, #740) נאכף על
    כתובת ה-peer — loopback בלבד. במעבדה הקונסולה של המשני נקשרה ל-
    ‏10.30.0.8 בלבד, ומשם ה-peer לעולם אינו loopback: ‏403 תמיד, וה-ssh -L
    היה המוצא היחיד. לכן כשכרטיס הניהול אינו loopback הקונסולה מקבלת
    מאזין **שני** על 127.0.0.1, על אותו פורט — ורק היא: לסוכן
    אין מה לחפש על loopback."""
    configs = run_main(tmp_path, monkeypatch,
                       "--console-host", "10.30.0.8", "--console-port", "8081")

    console = configs["console"]
    assert sorted(c["host"] for c in console) == ["10.30.0.8", "127.0.0.1"]
    assert {c["port"] for c in console} == {8081}
    assert len(configs["agent"]) == 1 and "kiosk" not in configs   # #1247


def test_console_on_loopback_gets_a_single_listener(
        tmp_path: Path, monkeypatch) -> None:
    """ברירת המחדל (loopback, #770) אינה מקבלת מאזין כפול — ‏127.0.0.1
    פעמיים על אותו פורט הוא כשל bind, ו-``serve_all`` היה מפיל את
    כל השרת בגללו."""
    configs = run_main(tmp_path, monkeypatch)

    assert [c["host"] for c in configs["console"]] == ["127.0.0.1"]


def test_agent_binds_to_interface_address_by_default(
        tmp_path: Path, monkeypatch) -> None:
    """#1074: ברירת המחדל של 8080 היא כתובת ``--interface``, לא
    0.0.0.0. ‏R20-F1 — bind לכל הכרטיסים היה החשיפה הרחבה."""
    monkeypatch.setattr(server.main, "_address_for_interface",
                        lambda name: "10.44.9.1")
    configs = run_main(tmp_path, monkeypatch, "--interface", "eth-deploy")
    assert [c["host"] for c in configs["agent"]] == ["10.44.9.1"]


def test_without_host_flag_dev_station_binds_to_server_url_ip(
        tmp_path: Path, monkeypatch) -> None:
    """אין ``ip`` (תחנת פיתוח): ``_interface_for`` מחזיר None, וה-bind
    נופל לכתובת ``--server-url`` — לא ל-0.0.0.0 בשקט."""
    configs = run_main(tmp_path, monkeypatch)
    assert [c["host"] for c in configs["agent"]] == ["10.44.12.10"]


def test_host_all_interfaces_only_when_explicit_and_warns(
        tmp_path: Path, monkeypatch, capsys) -> None:
    """0.0.0.0 רק במפורש, ועם אזהרה שנוקבת ב-R20-F1."""
    configs = run_main(tmp_path, monkeypatch, "--host", "0.0.0.0")
    assert [c["host"] for c in configs["agent"]] == ["0.0.0.0"]
    out = capsys.readouterr().out
    assert "0.0.0.0" in out
    assert "R20-F1" in out


def test_console_source_allowlist_flag_is_gone_and_refused_by_name(capsys) -> None:
    """‏#1303: השומר לפי כתובת מקור (#151) נמחק — ההפרדה היא בחומת האש
    בלבד (`deploy/firewall-rules.md`). יחידה ישנה שעוד מעבירה את הדגל
    נעצרת בעלייה ואומרת את שמו; אחרת היה נראה כאילו הקונסולה מוגבלת."""
    import pytest

    with pytest.raises(SystemExit):
        server.main.build_parser().parse_args([
            "--server-url", "http://10.44.12.10:8080",
            "--console-allow-from", "10.10.10.0/24",
        ])
    assert "--console-allow-from" in capsys.readouterr().err
