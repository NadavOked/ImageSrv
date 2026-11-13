"""#2044 — שאלת ה-SMART של המשכפל הוסרה (הכרעה: הסרה, 03/11).

אחרי #1732 (אדום אינו נשאל) ו-#2042 (כתום נכתב בלי שאלה) לשאלה לא היה קורא:
`smart_preflight` אינו קורא ל-`smart_choice`. השרשרת כולה הוסרה —
`smart_choice`/`smart_ask`/`gui_smart_choice`, `ask` ב-hello, `ask_answered`/
`ask_answer`, `POST /api/console/room/answer`, `pending_asks` של השולח ומסלול
"החלף" (`smart_replace_stop`, `awaiting_replace`).

טבלת `smart_asks` **נשארת בסכמה**, ריקה ובלי קורא (הכרעת המתאם 03/11): הורדתה
היא שינוי סכמה ו-bump, והגרסה הקודמת הייתה מסרבת לפתוח את ה-DB (#1894) — "חזור
לגרסה" נשבר. מה שנבדק כאן הוא שאף קוד אינו כותב או קורא אותה.

**סוכן ישן** (initramfs שלא עודכן, USB ישן) ששואל עדיין: ה-hello שלו מתקבל (200),
השדות `ask`/`ask_answered` נזנחים, ואין `ask_answer` בתשובה — ההחלטה המקומית שלו
נשארת ("the server did not confirm the answer -- keeping the local choice"), ובלי
תשובה הוא לוקח אחרי `WAIT_SMART_GUI_S` את ברירת הצבע. ה-`prompt` שהוא שולח באותו
hello עדיין עוצר את השולח (#2202, `prompt_hold.py`) עם אותן תקרות.

**בקרה שלילית:** כל טסט כאן שמסומן RED נופל על v0.80.6 (origin/main לפני השינוי)
על התנהגות — ה-hello מחזיר `ask_answer`, `GET /room` נושא `ask`, `/room/answer`
עונה 200, קוד השרת קורא וכותב `smart_asks`, ו-`attended_hello` שולח `ask`. הפקודות — בגוף ה-PR.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from conftest import hello_body

REPO = Path(__file__).resolve().parent.parent
AGENT = REPO / "agent"
CLONER = "aa:bb:cc:00:00:31"
OLD_ASK = {"nonce": "3-2", "port": 2, "verdict": "warn", "reason": "crc 4"}


# --- השרת ---------------------------------------------------------------------


@pytest.fixture()
def server(tmp_path: Path, images_root):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from server.app import create_app
    from server.console import users

    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     wol_send=lambda _packet: None)
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    admin = TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    assert admin.post("/api/console/machines", json={
        "mac": CLONER, "name": "01", "group_id": "grp_CLONERS"}).status_code == 200
    yield {"ctx": ctx, "admin": admin, "anon": TestClient(app)}
    ctx.sender.stop()


def _hello(server, **extra) -> dict:
    r = server["anon"].post("/api/v1/agent/hello", json={**hello_body(CLONER), "joining": False, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_an_old_agents_question_and_claim_are_accepted_and_ignored(server):
    """RED: על v0.80.6 ה-hello השני מחזיר `ask_answer: {"nonce": "3-2", "answer": "skip"}`
    — השרת הכריע. עכשיו אין הכרעה: ההחלטה המקומית של הסוכן הישן היא שנשארת."""
    first = _hello(server, ask=OLD_ASK, waiting_for="operator", prompt="Disk 2: SMART warn")
    assert "ask_answer" not in first
    claim = _hello(server, ask=OLD_ASK, ask_answered={"nonce": "3-2", "answer": "skip"})
    assert "ask_answer" not in claim, claim


def test_the_room_view_carries_no_question(server):
    """RED: על v0.80.6 המכונה ב-`GET /room` נושאת `ask` עם השאלה הפתוחה."""
    _hello(server, ask=OLD_ASK)
    machines = server["admin"].get("/api/console/room").json()["machines"]
    mine = next(m for m in machines if m["mac"] == CLONER)
    assert "ask" not in mine, mine


def test_the_console_answer_endpoint_is_gone(server):
    """RED: על v0.80.6, אחרי שאלה פתוחה, התשובה מהקונסולה מתקבלת (200)."""
    _hello(server, ask=OLD_ASK)
    r = server["admin"].post("/api/console/room/answer",
                             json={"mac": CLONER, "nonce": "3-2", "answer": "skip"})
    assert r.status_code in (404, 405), r.text
    assert r.json().get("detail") != "למכונה הזו אין שאלה פתוחה", r.text   # the route's own 404 = still there


def test_no_code_writes_or_reads_the_smart_asks_table():
    """הטבלה נשארת בסכמה (db.py — שם מותר לה להופיע, ב-CREATE ובהערה) ואין לה
    קורא או כותב באף מקום אחר: לא בשרת, לא בכלים ולא בסוכן. RED: על v0.80.6
    `smart_ask.py`, `prompt_hold.py` ו-`clockstep.py` מריצים SQL עליה."""
    hits = []
    for root in ("server", "tools", "agent", "boot"):
        for path in sorted((REPO / root).rglob("*")):
            if not path.is_file() or path.suffix not in (".py", ".sh", ".js", ".sql") \
                    or path == REPO / "server" / "db.py":
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                # SQL on the table -- a docstring or a comment that names it is not a reader
                if re.search(r"\b(FROM|INTO|UPDATE|JOIN|TABLE)\s+(IF\s+(NOT\s+)?EXISTS\s+)?smart_asks\b",
                             line, re.I):
                    hits.append(f"{path.relative_to(REPO).as_posix()}:{n}: {line.strip()}")
    assert hits == [], hits


def test_the_table_stays_in_the_schema_without_a_version_bump():
    """השומר של ההכרעה: הטבלה עדיין נוצרת ב-DB טרי (כך שגרסה קודמת פותחת אותו),
    ו-`SCHEMA_VERSION` לא זז בשבילה (test_schema_version_1894 נועל את הטביעה)."""
    from server import db
    assert "CREATE TABLE IF NOT EXISTS smart_asks (" in db.SCHEMA
    assert "DROP TABLE IF EXISTS smart_asks" not in (REPO / "server" / "db.py").read_text(encoding="utf-8")


def test_the_server_ceiling_still_outlasts_the_agents():
    """הסוכן מוותר ראשון (`WAIT_SMART_GUI_S`, היום התקרה של שאלות המגירה);
    השרת הוא רשת הביטחון. עבר מ-test_smart_ask.py אל התקרה של `prompt_hold`."""
    from server.rounds import prompt_hold
    waits = (AGENT / "lib/core/waits.sh").read_text(encoding="utf-8")
    agent = int(re.search(r'WAIT_SMART_GUI_S="\$\{WAIT_SMART_GUI_S:-(\d+)\}"', waits).group(1))
    assert prompt_hold.ASK_HOLD_MAX_SECONDS > agent


# --- הסוכן ----------------------------------------------------------------------

#: הפונקציות שהוסרו. RED: על v0.80.6 כולן מוגדרות ב-agent/.
REMOVED = ("smart_choice", "smart_ask", "gui_smart_choice", "smart_ask_answer",
           "attended_claim", "smart_awaiting_replace", "smart_replace_screen", "smart_replace_stop")


def test_no_agent_file_defines_the_removed_functions():
    sources = {p: p.read_text(encoding="utf-8") for p in AGENT.rglob("*.sh")}
    sources[AGENT / "imagesrv-agent"] = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    found = [(p.relative_to(REPO).as_posix(), name) for p, text in sources.items() for name in REMOVED
             if re.search(rf"^\s*{name}\(\)\s*\{{", text, re.M)]
    assert found == []
    calls = [(p.relative_to(REPO).as_posix(), name) for p, text in sources.items() for name in REMOVED
             if re.search(rf"(?<![\w-]){name}(?![\w-])\s", re.sub(r"#.*", "", text))]
    assert calls == [], calls


def _bash():
    from test_cloner_gui import BASH
    return BASH


def test_a_leftover_gui_request_puts_no_ask_in_the_hello(tmp_path):
    """`attended_hello` עם `smart-request` שנשאר בתיקיית הגואי: ה-hello נושא את
    ה-`prompt` (השאלה שעל המסך), ולא `ask`. RED: על v0.80.6 יש `ask`."""
    bash = _bash()
    if bash is None:
        pytest.skip("bash לא נמצא")
    from test_cloner_gui import posix, sh
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    (gui / "smart-request").write_text("3-2|2|warn|crc 4|WDC|WD-1\n", newline="\n")
    script = (f'export RUN_DIR={posix(run)!r} GUI_DIR={posix(gui)!r} SERVER=http://s; '
              f'. {posix(AGENT / "lib" / "core" / "attended.sh")}; '
              "build_hello() { printf '{\"mac\":\"aa:bb:cc:dd:ee:ff\",\"joining\":false}'; }; "
              "json_escape() { printf '%s' \"$1\"; }; log() { echo \"$*\" >&2; }; "
              'http_post_json() { cp "$2" "$RUN_DIR/req.json"; echo "{}"; }; '
              'jq() { return 1; }; '
              'attended_hello "Disk 2: question"; echo "rc=$?"')
    out = sh(script)
    assert "rc=0" in out.stdout, (out.stdout, out.stderr)
    body = json.loads((run / "req.json").read_text(encoding="utf-8"))
    assert body["prompt"] == "Disk 2: question" and body["waiting_for"] == "operator", body
    assert "ask" not in body, body
    assert not (gui / "smart-decision").exists()
