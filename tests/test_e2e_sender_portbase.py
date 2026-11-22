"""#201: השרת שהסימולציה מרימה לא ישדר על פורטי ההפצה של הייצור.

הסימולציה מריצה **שרת אמיתי** בתת-תהליך. ההגנה שאינה תלויה בכך שמישהו יזכור לנקות
היא portbase שאינו יכול להתנגש (#79, #156). מאז #3173 המנוע היחיד הוא מנוע החתיכות,
והוא גוזר את הפורטים שלו מאותו portbase: הגל על `portbase + PORT_OFFSET`, פורט הבקרה
אחריו, ושרתי הפריימים של מקור בהפצה ישירה (`serve_port`) — אחד לכל מחיצה — אחריהם.

שרשרת הראיות כאן היא שלוש חוליות, וכל אחת נבדקת בנפרד:
`harness` מרכיב argv → `server.main` מפרס אותו → `create_runtime` מעביר את הערך
ל-`ChunkEngine`. חוליה שנשברת מפילה טסט משלה, ולא נבלעת.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import server.app
import server.main
from server.rounds import chunksend, directchunk
from tools.e2e import harness

#: כמה שרתי פריימים יש לכל היותר (`directchunk.serve_ports`, #3871): פורט לכל מחיצה שנשלחת, לפי הסדר.
PARTS = directchunk.MAX_SERVED_PARTITIONS


def _app(tmp_path: Path, **kwargs):
    return server.app.create_app(
        tmp_path / "data", tmp_path / "images",
        "http://10.44.12.10:7712", **kwargs,
    )


def _ports(base_port: int) -> set[int]:
    """כל פורט שהמנוע נוגע בו כש-`port` הוא `base_port`: הגל, הבקרה, ושרת פריימים לכל מחיצה שנשלחת."""
    first = base_port + directchunk.SERVE_OFFSET + 1
    return {base_port, base_port + 1} | set(range(first, first + PARTS))


def _production_port(tmp_path: Path) -> int:
    """בלי בקשה מפורשת — הפורט של המנוע כשהוא נבנה בלי portbase (ברירת המחדל שלו, לא מספר שמשוכפל לכאן)."""
    return _app(tmp_path / "prod").state.ctx.chunk.port


def test_create_runtime_passes_portbase_to_the_chunk_engine(tmp_path: Path) -> None:
    """הערך שהתקבל מגיע עד `ChunkEngine` — ולא נשאר פרמטר מעוטר."""
    ctx = _app(tmp_path, sender_portbase=30199).state.ctx
    assert ctx.chunk.port == 30199 + chunksend.PORT_OFFSET
    assert ctx.direct_chunk.serve_port() == ctx.chunk.port + directchunk.SERVE_OFFSET
    assert ctx.chunk.port != _production_port(tmp_path)


def test_harness_ports_cannot_collide_with_production(tmp_path: Path) -> None:
    """הגל, הבקרה ושרתי הפריימים של הסימולציה — כולם מחוץ לטווח של שרת ייצור."""
    production = _ports(_production_port(tmp_path))
    ours = _ports(harness.SENDER_PORTBASE + chunksend.PORT_OFFSET)
    assert not production & ours
    assert not any(7700 <= port <= 7739 for port in ours)    # כל הטווח של המוצר (server/core/ports.py), לא רק מה שמאזין היום


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

    argv = captured[0][3:]              # אחרי python -c <bootstrap>
    args = server.main.build_parser().parse_args(argv)
    assert args.sender_portbase == harness.SENDER_PORTBASE


def test_main_wires_the_flag_into_the_runtime(tmp_path: Path, monkeypatch) -> None:
    """`main` הוא החוליה שאין לה טסט אחר: דגל שמתפרס ואינו מועבר הלאה
    נראה בדיוק כמו דגל שעובד — השרת עולה, ומשדר על פורט הייצור — עיקרון 5.

    #731/#738: מאז פיצול המאזינים `main` מעביר את הדגל ל-`create_runtime`
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
    monkeypatch.setattr(server.app, "create_boot_app", lambda rt: "boot")   # #3486
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


def test_flag_defaults_to_none_so_production_keeps_its_port() -> None:
    """ברירת המחדל של הדגל היא היעדר בקשה — לא מספר שמשוכפל לכאן."""
    parser = server.main.build_parser()
    args = parser.parse_args(["--server-url", "http://10.44.12.10:7712"])
    assert args.sender_portbase is None
