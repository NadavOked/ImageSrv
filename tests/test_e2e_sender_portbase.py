"""‏#201: השרת שהסימולציה מרימה לא ישדר על פורטי ההפצה של הייצור.

הסימולציה מריצה **שרת אמיתי** בתת-תהליך, ולכן אין לאן להזריק לה שולח
מזויף כמו ב-`conftest`. ההגנה היחידה שאינה תלויה בכך שמישהו יזכור לנקות
היא portbase שאינו יכול להתנגש (#79, ‏#156).

שרשרת הראיות כאן היא שלושה חוליות, וכל אחת נבדקת בנפרד:
‏`harness` מרכיב argv → ‏`server.main` מפרס אותו → ‏`create_app` מעביר
את הערך ל-`SenderEngine`. חוליה שנשברת מפילה טסט משלה, ולא נבלעת.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import server.app
import server.main
from server.rounds.sender import DEFAULT_PORTBASE
from tools.e2e import harness

PRODUCTION_PORTS = {DEFAULT_PORTBASE, DEFAULT_PORTBASE + 1}


def _app(tmp_path: Path, **kwargs):
    return server.app.create_app(
        tmp_path / "data", tmp_path / "images",
        "http://10.44.12.10:8080", **kwargs,
    )


def test_production_default_is_unchanged(tmp_path: Path) -> None:
    """בלי בקשה מפורשת — 9000, בדיוק כמו קודם (פורט-הפצה-מכוון: תיעוד ברירת המחדל)."""
    assert _app(tmp_path).state.ctx.sender.portbase == DEFAULT_PORTBASE


def test_create_app_passes_portbase_to_sender(tmp_path: Path) -> None:
    """הערך שהתקבל מגיע עד `SenderEngine` — ולא נשאר פרמטר מעוטר."""
    app = _app(tmp_path, sender_portbase=30199)
    assert app.state.ctx.sender.portbase == 30199


def test_command_uses_the_given_portbase(tmp_path: Path) -> None:
    """ראיה חיובית על מה ש-udp-sender באמת יקבל, לא רק על התכונה."""
    sender = _app(tmp_path, sender_portbase=30199).state.ctx.sender
    cmd = sender.command_for(Path("p1.esp.pcl.zst"), receivers=2)
    assert "--portbase" in cmd
    assert cmd[cmd.index("--portbase") + 1] == "30199"
    assert not PRODUCTION_PORTS & {int(part) for part in cmd if part.isdigit()}


def test_harness_portbase_cannot_collide_with_production() -> None:
    """‏udpcast תופס portbase ו-portbase+1 — שניהם חייבים להיות מחוץ להפצה."""
    assert not PRODUCTION_PORTS & {harness.SENDER_PORTBASE,
                                   harness.SENDER_PORTBASE + 1}


def test_harness_starts_the_server_with_the_flag(tmp_path: Path, monkeypatch) -> None:
    """שורת הפקודה שה-harness מרכיב — נלכדת, לא מורצת."""
    captured: list[list[str]] = []

    def fake_popen(cmd, **kwargs):
        captured.append(list(cmd))
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    harness.start_server(tmp_path)

    assert captured, "start_server לא ניסה להריץ שרת בכלל"
    argv = captured[0]
    assert "--sender-portbase" in argv, " ".join(argv)
    assert argv[argv.index("--sender-portbase") + 1] == str(harness.SENDER_PORTBASE)


def test_server_main_parses_the_harness_argv(tmp_path: Path, monkeypatch) -> None:
    """החוליה שקל לפספס: הדגל קיים ב-argv **ו**-`server.main` מכיר אותו.

    בלי זה תת-התהליך היה נופל על `unrecognized arguments`, והסימולציה
    הייתה מדווחת "השרת לא עלה" בלי לרמוז למה.
    """
    captured: list[list[str]] = []
    monkeypatch.setattr(subprocess, "Popen",
                        lambda cmd, **kw: captured.append(list(cmd)) or object())
    harness.start_server(tmp_path)

    argv = captured[0][3:]              # אחרי python -m server.main
    args = server.main.build_parser().parse_args(argv)
    assert args.sender_portbase == harness.SENDER_PORTBASE


def test_main_wires_the_flag_into_the_runtime(tmp_path: Path, monkeypatch) -> None:
    """‏`main` הוא החוליה שאין לה טסט אחר: דגל שמתפרס ואינו מועבר הלאה
    נראה בדיוק כמו דגל שעובד — השרת עולה, ומשדר על 9000 (פורט-הפצה-מכוון:
    תיעוד ברירת המחדל, לא בקשה) — עיקרון 5.

    ‏#731/#738: מאז פיצול המאזינים `main` מעביר את הדגל ל-`create_runtime`
    (בעל המצב המשותף), ולא ל-`create_app`. שאר החוליות (יצירת שלוש
    האפליקציות וההרצה בפועל דרך `serve_all`) מנוטרלות — נבדק רק שהערך
    הגיע ל-runtime."""
    import asyncio
    import uvicorn

    seen: dict = {}

    def fake_create_runtime(*args, **kwargs):
        seen.update(kwargs)
        return "rt"

    monkeypatch.setattr(server.app, "create_runtime", fake_create_runtime)
    monkeypatch.setattr(server.app, "create_agent_app", lambda rt: "agent")
    monkeypatch.setattr(server.app, "create_console_app", lambda rt: "console")
    monkeypatch.setattr(uvicorn, "Config", lambda *a, **k: None)
    monkeypatch.setattr(uvicorn, "Server", lambda config: object())
    monkeypatch.setattr(server.main, "serve_all", lambda servers: None)
    monkeypatch.setattr(asyncio, "run", lambda coro=None: None)
    monkeypatch.setattr(server.main, "_interface_for", lambda url: None)
    monkeypatch.setattr("sys.argv", [
        "server.main", "--server-url", "http://127.0.0.1:8199",
        "--data-dir", str(tmp_path / "data"), "--images", str(tmp_path / "img"),
        "--sender-portbase", str(harness.SENDER_PORTBASE),
    ])

    server.main.main()

    assert seen.get("sender_portbase") == harness.SENDER_PORTBASE


def test_flag_defaults_to_none_so_production_keeps_9000() -> None:
    """ברירת המחדל של הדגל היא היעדר בקשה — לא מספר שמשוכפל לכאן."""
    parser = server.main.build_parser()
    args = parser.parse_args(["--server-url", "http://10.44.12.10:8080"])
    assert args.sender_portbase is None
