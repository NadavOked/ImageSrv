"""שביל הפירורים של האתחול (#400).

‏2026-09-05, הבדיקה הראשונה על ברזל: מחשב שיכפול פיזי קיבל DHCP, טען
‏GRUB, משך את התפריט הדינמי — ואז שבע דקות שקט. הוא לא ביקש `linux.mod`
ולא `vmlinuz`, ואי אפשר היה לדעת למה: למחשבי שיכפול אין מסך **בהגדרה**
(#17). בין ‎`GET /boot/menu` ל-`POST /api/v1/agent/hello` השרת לא ידע כלום.

מה שנבדק כאן הוא בדיוק שני הצדדים של הדרישה, והם מושכים לכיוונים הפוכים:

1. **שביל שנכשל אינו מפיל את האתחול.** ‏`/boot/step` שאינו זמין, ‏MAC
   פגום, רושם שזורק — כולם מחזירים 200 ומשאירים מכונה שממשיכה לעלות.
2. **ואינו נבלע בשקט.** הרשימה מנויה מראש (`boot/trace.py`), ולכן
   "לא הגיע ל-http-ok" הוא **שם של שלב** ולא "לא ידוע".
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from boot import trace
from boot.grub_menu import GrubConfig, render, render_local_only
from boot.http import create_boot_asgi
from server.agent import boottrace
from server.db import connect

REPO = Path(__file__).resolve().parent.parent
AGENT = REPO / "agent"

MAC = "78:ac:c0:9b:11:c2"          # ה-HP הפיזי מ-#400
HOST = "10.44.0.10:8080"
CONFIG = GrubConfig(server_base=f"http://{HOST}")
CLONER = {"schema": 1, "known": True, "role": "cloner", "task": None, "session": None}


# --- שכבת ה-HTTP -------------------------------------------------------------


def call(path: str, query: str = "", record=None) -> tuple[int, bytes]:
    """בקשה אחת מול אפליקציית ה-ASGI הגולמית. מחזיר (סטטוס, גוף)."""
    app = create_boot_asgi(resolve=lambda mac, ip: dict(CLONER), config=CONFIG,
                           record=record)
    scope = {"type": "http", "method": "GET", "path": path, "root_path": "",
             "query_string": query.encode(), "headers": [],
             "client": ("10.44.0.59", 51321), "server": ("10.44.0.10", 8080)}
    seen: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        seen.append(message)

    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in seen if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in seen if m["type"] == "http.response.body")
    return status, body


class Spy:
    """רושם מזויף. מחזיר True — כמו הרושם האמיתי כשהוא הצליח."""

    def __init__(self, fail: bool = False):
        self.steps: list[tuple[str, str]] = []
        self.fail = fail

    def __call__(self, mac: str, step: str) -> bool:
        if self.fail:
            raise RuntimeError("db is down")
        self.steps.append((mac, step))
        return True


def test_a_declared_step_is_recorded_and_the_body_stays_tiny():
    spy = Spy()
    status, body = call("/step", f"mac={MAC}&s=entry", spy)
    assert status == 200
    assert body == trace.TINY_BODY and len(body) <= 4
    assert spy.steps == [(MAC, "entry")]


def test_a_step_that_is_not_in_the_list_is_refused():
    """הרשימה סגורה. שביל שמקבל כל מחרוזת הופך את "לא הגיע לשלב 3"
    לניחוש — ואז אין לו שום ערך."""
    spy = Spy()
    status, _ = call("/step", f"mac={MAC}&s=almost-there", spy)
    assert status == 200          # ‏GRUB לא רואה שגיאה
    assert spy.steps == []        # ...והצעד לא נרשם


def test_a_malformed_mac_is_refused_but_the_machine_gets_its_200():
    spy = Spy()
    status, body = call("/step", "mac=not-a-mac&s=entry", spy)
    assert status == 200 and body == trace.TINY_BODY
    assert spy.steps == []


def test_the_mac_is_normalised_before_it_is_recorded():
    spy = Spy()
    call("/step", "mac=78-AC-C0-9B-11-C2&s=pre-linux", spy)
    assert spy.steps == [(MAC, "pre-linux")]


def test_a_recorder_that_throws_does_not_reach_grub():
    """כלי אבחון שמפיל בקשת אתחול הוא נזק."""
    status, body = call("/step", f"mac={MAC}&s=entry", Spy(fail=True))
    assert status == 200 and body == trace.TINY_BODY


def test_the_menu_request_is_itself_the_first_step():
    """הצעד היחיד שאינו תלוי בכך שהמכונה עוד מדברת. אם הוא לבדו רשום —
    היא נעצרה בדיוק שם, וזה מה שקרה ל-HP."""
    spy = Spy()
    status, body = call("/menu", f"mac={MAC}", spy)
    assert status == 200 and b"menuentry" in body
    assert spy.steps == [(MAC, "menu")]


def test_the_menu_is_served_even_with_no_recorder_wired_in():
    status, body = call("/menu", f"mac={MAC}", None)
    assert status == 200 and b"menuentry" in body


# --- הפירורים בתוך קובץ ה-GRUB ----------------------------------------------


def agent_entry(text: str) -> list[str]:
    """שורות ערך ה-ImageSrv, בלי השורות העוטפות."""
    body = re.search(r'menuentry "ImageSrv" --id imagesrv \{\n(.*?)\n\}',
                     text, re.S)
    assert body, f"no ImageSrv entry in:\n{text}"
    return [line.strip() for line in body.group(1).splitlines()]


def test_the_entry_carries_the_five_breadcrumbs_in_order():
    lines = agent_entry(render(CLONER, CONFIG))
    calls = [line.split()[1] for line in lines
             if line.startswith(trace.GRUB_FUNCTION_NAME + " ")]
    assert calls == list(trace.GRUB_STEPS)


def test_each_breadcrumb_sits_between_the_commands_it_dates():
    """‏entry לפני `insmod http`, ‏http-ok אחריו, ואז לפני linux, לפני
    initrd, ואחריו. זה מה שהופך "נעצר" ל"נעצר **איפה**"."""
    lines = agent_entry(render(CLONER, CONFIG))
    where = {line.split()[1]: i for i, line in enumerate(lines)
             if line.startswith(trace.GRUB_FUNCTION_NAME + " ")}
    insmod = next(i for i, line in enumerate(lines) if line == "insmod http")
    linux = next(i for i, line in enumerate(lines) if line.startswith(("linux ", "if linux ")))
    initrd = next(i for i, line in enumerate(lines) if line.startswith(("initrd ", "if initrd ")))
    assert where["entry"] < insmod < where["http-ok"]
    assert where["http-ok"] < where["pre-linux"] < linux
    assert linux < where["pre-initrd"] < initrd < where["pre-boot"]


def play_entry(lines: list[str], *, fail: frozenset[str] = frozenset()
               ) -> tuple[list[str], list[str]]:
    """הרצה מדומה של ערך ImageSrv לפי סמנטיקת menuentry של GRUB.

    פקודה שנכשלה מדפיסה שגיאה וההרצה ממשיכה — אלא אם היא תנאי של `if`.
    מחזיר (פקודות שרצו, צעדי imagesrv_trace שנשלחו).
    """
    ran: list[str] = []
    steps: list[str] = []
    last_ok = True
    env = {"grub_platform": "pc"}
    skip = 0
    i = 0

    def test_bracket(expr: str) -> bool:
        inner = expr.strip()
        if inner.startswith("[") and inner.endswith("]"):
            inner = inner[1:-1].strip()
        if re.fullmatch(r'"?\$\?"?\s*=\s*"?0"?', inner):
            return last_ok
        m = re.fullmatch(r'"\$(\w+)"\s*=\s*"([^"]*)"', inner)
        return bool(m) and env.get(m.group(1), "") == m.group(2)

    def run_cmd(cmd: str) -> bool:
        nonlocal last_ok
        cmd = cmd.strip().rstrip(";")
        if not cmd:
            return True
        name = cmd.split()[0]
        ran.append(name)
        if name == trace.GRUB_FUNCTION_NAME:
            steps.append(cmd.split()[1])
            last_ok = name not in fail
            return last_ok
        last_ok = name not in fail
        return last_ok

    while i < len(lines):
        line = lines[i]
        oneline = re.fullmatch(r"if (.+); then (.+); fi", line)
        block = re.fullmatch(r"if (.+); then", line)
        if oneline:
            if skip == 0 and test_bracket(oneline.group(1)):
                run_cmd(oneline.group(2))
            i += 1
            continue
        if block:
            if skip:
                skip += 1
            elif not test_bracket(block.group(1)):
                skip = 1
            i += 1
            continue
        if line == "fi":
            if skip:
                skip -= 1
            i += 1
            continue
        if skip == 0:
            run_cmd(line)
        i += 1
    return ran, steps


def test_a_failed_linux_does_not_claim_the_kernel_loaded():
    """#503: GET /boot/vmlinuz 404 → linux נכשל → pre-initrd אסור להירשם.

    GRUB ממשיך לפקודה הבאה אחרי כישלון, ולכן פירור שעומד לבד אחרי
    `linux` מדווח «הקרנל נטען» בזמן שהקרנל לא נטען. עיקרון 5 בתוך
    כלי האבחון עצמו. החזרת הצורה הישנה (פירור בלי `if [ "$?" = "0" ]`)
    חייבת להפיל את הטסט הזה.
    """
    lines = agent_entry(render(CLONER, CONFIG))
    linux = next(i for i, line in enumerate(lines) if line.startswith("if linux "))
    loaded = lines.index("set imagesrv_kernel_loaded=y")
    failure = lines.index('if [ -z "$imagesrv_kernel_loaded" ]; then')
    pre_initrd = lines.index(f"{trace.GRUB_FUNCTION_NAME} pre-initrd")
    assert linux < loaded < failure < pre_initrd
    assert lines[linux + 1] == "then"         # `then` בשורה משלו מאז 54223f94


def test_a_failed_initrd_does_not_claim_the_initramfs_loaded():
    """אותו מסלול ל-pre-boot אחרי initrd שנכשל."""
    lines = agent_entry(render(CLONER, CONFIG))
    initrd = next(i for i, line in enumerate(lines) if line.startswith("if initrd "))
    loaded = lines.index("set imagesrv_initrd_loaded=y")
    failure = lines.index('if [ -z "$imagesrv_initrd_loaded" ]; then')
    pre_boot = lines.index(f"{trace.GRUB_FUNCTION_NAME} pre-boot")
    assert initrd < loaded < failure < pre_boot
    assert lines[initrd + 1] == "then"         # `then` בשורה משלו מאז 54223f94


def test_a_breadcrumb_never_guards_a_boot_command():
    """הבקרה השלילית של "האתחול ממשיך", בצד ה-GRUB.

    פקודה שנכשלה בתוך menuentry מדפיסה שגיאה וההרצה עוברת לבאה אחריה;
    מה שמפיל ערך הוא `linux`/`initrd` שנכשלים. לכן כל קריאת פירור
    חייבת לעמוד **לבדה** בשורה — לא לפני `&&`, ולא על אותה שורה עם
    פקודת אתחול. שרת שאינו עונה על ‎/boot/step מייצר שגיאה על המסך,
    לא מכונה שאינה עולה.

    ‏#503 מתיר `if [ "$?" = "0" ]` סביב הפירור (הפקודה שומרת על הפירור,
    לא להפך). השורה של הקריאה עצמה נשארת לבדה — אחרת הפירור היה יכול
    לשבת לפני `&& linux` ולמנוע אתחול.
    """
    for line in agent_entry(render(CLONER, CONFIG)):
        if trace.GRUB_FUNCTION_NAME not in line:
            continue
        assert re.fullmatch(rf"{trace.GRUB_FUNCTION_NAME} [a-z-]+", line), line


def test_the_trace_url_is_quoted_so_grub_does_not_choke_on_the_ampersand():
    """‏`&` הוא תו מיוחד בלקסר של GRUB. מחוץ למירכאות הוא שובר את השורה
    — כלומר את הפונקציה — כלומר את ערך האתחול של כל מכונה ברשת."""
    body = re.search(rf"function {trace.GRUB_FUNCTION_NAME} \{{\n(.*?)\n\}}",
                     render(CLONER, CONFIG), re.S).group(1).strip()
    assert body.startswith('cat "(http,') and body.endswith('"')
    assert "&s=$1" in body and "${net_default_mac}" in body


def test_a_file_with_no_agent_entry_has_no_trace_function():
    """מכונה לא רשומה מקבלת דיסק מקומי בלבד. אין שם מה לעקוב אחריו."""
    for text in (render_local_only("unregistered"),
                 render({"schema": 1, "known": False}, CONFIG)):
        assert trace.GRUB_FUNCTION_NAME not in text


def test_the_kernel_command_line_gained_nothing():
    """עיקרון 2. שביל הפירורים עובר ב-HTTP, ולא בשורת הפקודה."""
    line = next(l for l in agent_entry(render(CLONER, CONFIG))
                if l.startswith("if linux "))
    assert "step" not in line and "trace" not in line


def test_the_generator_refuses_a_step_it_never_declared():
    with pytest.raises(ValueError):
        trace.grub_call("nearly-booted")


def test_the_whole_file_is_still_ascii():
    text = render(CLONER, CONFIG)
    assert text.encode("ascii")           # זורק אם לא


# --- האחסון ----------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path):
    return connect(tmp_path / "trace.db")


def last(conn) -> tuple:
    row = conn.execute("SELECT mac, step, idx, at, first_at FROM boot_steps").fetchone()
    return tuple(row) if row else ()


def test_the_recorder_stores_the_step_and_says_so(conn):
    assert boottrace.record(conn, MAC, "entry") is True
    mac, step, idx, at, first_at = last(conn)
    assert (mac, step, idx) == (MAC, "entry", trace.STEP_INDEX["entry"])
    assert at == first_at


def test_the_recorder_refuses_a_step_that_is_not_declared(conn):
    assert boottrace.record(conn, MAC, "almost-there") is False
    assert last(conn) == ()


def test_progress_keeps_the_first_timestamp(conn):
    boottrace.record(conn, MAC, "menu")
    started = last(conn)[4]
    conn.execute("UPDATE boot_steps SET first_at = '2020-01-01T00:00:00+00:00'")
    conn.commit()
    boottrace.record(conn, MAC, "entry")
    assert last(conn)[1] == "entry"
    assert last(conn)[4] == "2020-01-01T00:00:00+00:00"
    assert started                       # נכתב מלכתחילה


def test_a_step_that_went_backwards_is_a_new_boot(conn):
    """המכונה אותחלה מחדש. ‏`first_at` מתאפס, אחרת "לפני 40 דקות" יתאר
    ניסיון שכבר נגמר."""
    boottrace.record(conn, MAC, "pre-boot")
    conn.execute("UPDATE boot_steps SET first_at = '2020-01-01T00:00:00+00:00'")
    conn.commit()
    boottrace.record(conn, MAC, "menu")
    assert last(conn)[1] == "menu"
    assert last(conn)[4] != "2020-01-01T00:00:00+00:00"


# --- #416: ענף הדיסק המקומי — הסתעפות, לא המשך --------------------------------


def test_a_local_step_after_the_menu_is_progress_not_a_new_boot(conn):
    boottrace.record(conn, MAC, "menu")
    conn.execute("UPDATE boot_steps SET first_at = '2020-01-01T00:00:00+00:00'")
    conn.commit()
    assert boottrace.record(conn, MAC, "local-entry") is True
    assert last(conn)[1] == "local-entry"
    assert last(conn)[4] == "2020-01-01T00:00:00+00:00"
    boottrace.record(conn, MAC, "local-no-disk")
    assert last(conn)[4] == "2020-01-01T00:00:00+00:00"


def test_a_menu_after_a_local_outcome_is_a_new_boot(conn):
    boottrace.record(conn, MAC, "local-search-empty")
    conn.execute("UPDATE boot_steps SET first_at = '2020-01-01T00:00:00+00:00'")
    conn.commit()
    boottrace.record(conn, MAC, "menu")
    assert last(conn)[1] == "menu" and last(conn)[4] != "2020-01-01T00:00:00+00:00"


@pytest.mark.parametrize("step", ["local-search-ok", "local-search-empty",
                                  "local-no-disk", "local-chain-refused"])
def test_a_local_outcome_is_final_and_never_stalled(step):
    """מסך עצירה אינו "תקוע", ומטען שעלה אינו משאיר פירור נוסף."""
    info = stalled(step, minutes=99)
    assert info["label"] == boottrace.STEP_LABELS[step]
    assert info["next_step"] is None and info["stalled"] is False
    assert info["next_label"] == boottrace.LOCAL_NEXT[step]
    assert (info["index"], info["total"]) == (2, 2)


def test_a_local_entry_that_never_reported_an_outcome_is_stalled():
    info = stalled("local-entry")
    assert info["stalled"] is True and info["next_step"] is None
    assert (info["index"], info["total"]) == (1, 2)


def test_the_two_no_os_states_have_two_different_labels():
    """הבאג של #416, בצד הקונסולה: "אין דיסק" ו"אין מערכת" הם שני שמות."""
    assert boottrace.STEP_LABELS["local-no-disk"] != boottrace.STEP_LABELS["local-search-empty"]
    assert all(step in boottrace.STEP_LABELS for step in trace.LOCAL_STEPS)


def test_the_local_steps_do_not_change_the_imagesrv_chain():
    assert len(trace.STEPS) == 9 and not any(s.startswith("local-") for s in trace.STEPS)
    assert trace.GRUB_STEPS == trace.STEPS[1:6]


# --- התצוגה: מה **לא** הגיע --------------------------------------------------


def stalled(step: str, minutes: int = 7) -> dict:
    """מתאר מכונה שהשאירה `step` ומאז שתקה."""
    from datetime import datetime, timedelta, timezone
    at = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return boottrace.describe(step, at.isoformat(timespec="seconds"))


def test_a_machine_that_stopped_is_named_by_the_step_it_did_not_reach():
    """זו כל הבקשה של #400: לא "לא ידוע", אלא שם של שלב.

    ‏HP שהשאיר `entry` ושתק שבע דקות נעצר לפני `http-ok` — כלומר על
    ‏`insmod http`. זה משפט שאפשר לפעול לפיו.
    """
    info = stalled("entry")
    assert info["step"] == "entry"
    assert info["stalled"] is True
    assert info["next_step"] == "http-ok"
    assert info["next_label"] == boottrace.STEP_LABELS["http-ok"]
    assert info["label"] == boottrace.STEP_LABELS["entry"]
    assert info["seconds"] >= 7 * 60
    assert (info["index"], info["total"]) == (2, len(trace.STEPS))


@pytest.mark.parametrize("step", trace.STEPS[:-1])
def test_every_step_but_the_last_can_name_its_successor(step):
    info = stalled(step)
    assert info["next_step"] == trace.STEPS[trace.STEP_INDEX[step] + 1]
    assert info["next_label"] in boottrace.STEP_LABELS.values()


def test_a_fresh_step_is_not_called_stalled():
    from datetime import datetime, timezone
    info = boottrace.describe("entry", datetime.now(timezone.utc).isoformat())
    assert info["stalled"] is False and info["seconds"] < boottrace.STALL_SECONDS


def test_the_last_step_is_never_stalled():
    info = stalled(trace.STEPS[-1], minutes=99)
    assert info["next_step"] is None and info["stalled"] is False
    assert info["next_label"] == boottrace.AFTER_LAST


def test_a_machine_with_no_trail_gets_nothing_rather_than_a_guess():
    assert boottrace.describe(None, None) is None
    assert boottrace.describe("almost-there", "2026-09-05T11:15:49+00:00") is None


@pytest.mark.parametrize("at", [None, "not a time", "2026-09-05T11:15:49",
                                "2099-01-01T00:00:00+00:00"])
def test_a_timestamp_that_could_not_be_read_counts_as_stalled(at):
    """עיקרון 5. "לא הצלחנו לבדוק כמה זמן עבר" אינו "עבר אפס"."""
    info = boottrace.describe("entry", at)
    assert info["seconds"] is None and info["stalled"] is True


def test_the_trail_view_carries_every_machine(conn):
    boottrace.record(conn, MAC, "entry")
    boottrace.record(conn, "b4:2e:99:07:1a:c4", "agent-hello")
    seen = boottrace.trail(conn)
    assert seen[MAC]["step"] == "entry"
    assert seen["b4:2e:99:07:1a:c4"]["step"] == "agent-hello"


# --- הקונסולה: מקצה לקצה ----------------------------------------------------


def test_the_console_names_the_step_a_stuck_machine_stopped_at(server):
    """הדרישה ההתנהגותית של #400, מהחוט ועד המסך.

    מכונה מבקשת תפריט, משאירה `entry`, ונעלמת. מה שהמפעיל מקבל בקונסולה
    הוא **שם השלב** ושם השלב הבא שלא הגיע — לא "לא ידוע".
    """
    anon, admin = server["anon"], server["admin"]
    assert anon.get(f"/boot/menu?mac={MAC}").status_code == 200
    assert anon.get(f"/boot/step?mac={MAC}&s=entry").status_code == 200

    # ...ואז שקט. מזיזים את החותמת אחורה במקום לחכות שבע דקות.
    server["ctx"].conn.execute(
        "UPDATE boot_steps SET at = '2026-09-05T08:15:49+00:00' WHERE mac = ?", (MAC,))
    server["ctx"].conn.commit()

    row = next(d for d in admin.get("/api/console/net").json() if d["mac"] == MAC)
    assert row["boot"]["label"] == boottrace.STEP_LABELS["entry"]
    assert row["boot"]["next_label"] == boottrace.STEP_LABELS["http-ok"]
    assert row["boot"]["stalled"] is True


def test_a_machine_that_never_left_a_breadcrumb_shows_no_trail(server):
    anon, admin = server["anon"], server["admin"]
    # פנייה שאינה עוברת ב-/boot בכלל: המכונה נראית ברשת, בלי שביל.
    anon.post("/api/v1/agent/hello", json=_hello(MAC))
    row = next(d for d in admin.get("/api/console/net").json() if d["mac"] == MAC)
    assert row["boot"] is None


def _hello(mac: str) -> dict:
    from conftest import hello_body                        # noqa: PLC0415
    return hello_body(mac)


# --- מי רשאי לכתוב פירור אתחול (‏#584) ----------------------------------------
#
# ‏`GET /boot/step?mac=<MAC>&s=<צעד>` רשם פירור לפי ה-MAC שבשאילתה, בלי
# אימות הפונה. אותה משפחה בדיוק כמו #536: ‏MAC בשאילתה אינו זהות, ומחוץ
# לווילן ההפצה אין ראיה שהפונה הוא המכונה. פירור מזויף הופך "לא ידוע"
# ל"בדקנו, הגיע" (עיקרון 5) ומאפס את `first_at` של שביל אמיתי. ההכרעה
# המודעת (#584): פירור מחוץ לווילן **אינו נרשם** — כמו שבקשת תפריט מחוץ
# לווילן אינה נספרת — והמחיר הוא ששביל תחנת תרחיש-3 (#39) אינו נרשם.

#: כתובת וילן ההפצה — זו שאיתה נוצר השרת ב-conftest.
VLAN = "http://10.44.12.10:8080"
#: כתובת מקומית אחרת של אותו שרת — תרחיש 3 (#39).
OFF_VLAN = "http://10.10.10.8:8080"


def step_row(server, mac: str):
    """שורת `boot_steps` של ה-MAC — **המצב עצמו**. ‏None = לא נרשם פירור."""
    return server["ctx"].conn.execute(
        "SELECT step, first_at FROM boot_steps WHERE mac = ?", (mac,)).fetchone()


def test_a_step_from_off_vlan_is_not_recorded(server):
    """הבאג עצמו: ‏`GET /boot/step?mac=&s=` מרשת שאינה וילן ההפצה, למכונה
    שמעולם לא אתחלה — אסור שישאיר לה פירור."""
    anon = server["anon"]
    assert step_row(server, MAC) is None

    resp = anon.get(f"{OFF_VLAN}/boot/step?mac={MAC}&s=agent-start")
    assert resp.status_code == 200              # ‏GRUB מקבל 200 בכל מקרה
    assert resp.content == trace.TINY_BODY

    assert step_row(server, MAC) is None


def test_a_menu_breadcrumb_from_off_vlan_is_not_recorded(server):
    """גם פירור ה-"menu" של מסלול התפריט מותנה באותו אופן: תפריט מחוץ
    לווילן מוגש (תרחיש 3) אך אינו משאיר פירור."""
    anon = server["anon"]
    assert anon.get(f"{OFF_VLAN}/boot/menu?mac={MAC}").status_code == 200
    assert step_row(server, MAC) is None


def test_a_step_on_the_deployment_vlan_is_still_recorded(server):
    """הבקרה לכיוון השני: הרישום אינו מבוטל. פירור שהתקבל על וילן
    ההפצה נרשם כמו היום, אחרת התיקון מוחק את כל האבחון של #400."""
    anon = server["anon"]
    assert anon.get(f"{VLAN}/boot/step?mac={MAC}&s=agent-start").status_code == 200
    row = step_row(server, MAC)
    assert row is not None and row["step"] == "agent-start"


def test_a_forged_step_from_off_vlan_does_not_reset_a_real_trail(server):
    """הנזק שנמנע (#584, סעיף 2): שביל אמיתי נרשם על הווילן, ואז פירור
    זר מרשת אחרת מנסה להחזיר אותו אחורה ("אתחול חדש") ולאפס את
    `first_at`. מחוץ לווילן הוא נדחה, והשביל האמיתי נשאר כפי שהיה."""
    anon = server["anon"]
    assert anon.get(f"{VLAN}/boot/step?mac={MAC}&s=agent-start").status_code == 200
    real = step_row(server, MAC)

    # פירור זר, צעד מוקדם יותר — לפני התיקון היה מאפס את `first_at`.
    assert anon.get(f"{OFF_VLAN}/boot/step?mac={MAC}&s=menu").status_code == 200

    after = step_row(server, MAC)
    assert after["step"] == "agent-start"          # לא נסוג ל-menu
    assert after["first_at"] == real["first_at"]   # ולא אופס


# --- הצד של הסוכן ------------------------------------------------------------


def find_bash() -> str | None:
    if os.name == "nt":
        for candidate in (r"C:\Program Files\Git\usr\bin\bash.exe",
                          r"C:\Program Files\Git\bin\bash.exe"):
            if Path(candidate).exists():
                return candidate
    return shutil.which("bash")


BASH = find_bash()
requires_bash = pytest.mark.skipif(BASH is None, reason="bash חסר")


def sh(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
        capture_output=True, text=True, cwd=str(REPO),
        stdin=subprocess.DEVNULL,
    )


def posix(p: Path) -> str:
    return str(p).replace("\\", "/")


@requires_bash
def test_a_dead_step_endpoint_does_not_stop_the_agent():
    """הבקרה השלילית של "האתחול ממשיך", בצד הסוכן.

    ‏127.0.0.1:9 הוא discard — אין שם מאזין. ‏`trace_step` נכשל, אומר
    זאת, מחזיר 0, והפקודה שאחריו רצה. מכונה שנעצרת בגלל כלי אבחון היא
    בדיוק מה ש-#400 אוסר.
    """
    result = sh(
        f'set -e; RUN_DIR=$(mktemp -d); export RUN_DIR; '
        f'. {posix(AGENT)}/lib/core/common.sh; '
        f'SERVER="http://127.0.0.1:9"; MAC="{MAC}"; TRACE_TIMEOUT=2; '
        f'trace_step entry; echo "STILL-BOOTING"'
    )
    assert result.returncode == 0, result.stderr
    assert "STILL-BOOTING" in result.stdout
    assert "was not delivered" in result.stdout       # ולא בשקט


@requires_bash
def test_trace_step_is_a_no_op_without_a_server_or_a_mac():
    result = sh(
        f'set -e; RUN_DIR=$(mktemp -d); export RUN_DIR; '
        f'. {posix(AGENT)}/lib/core/common.sh; '
        f'SERVER=""; MAC=""; trace_step entry; echo "STILL-BOOTING"'
    )
    assert result.returncode == 0 and "STILL-BOOTING" in result.stdout


@pytest.mark.parametrize("step", trace.AGENT_STEPS)
def test_every_agent_step_is_actually_reported_by_the_agent(step):
    """הרשימה המנויה שווה משהו רק אם מישהו באמת שולח אותה. צעד שאיש
    אינו מדווח הופך "לא הגיע" לרעש קבוע."""
    sources = "\n".join(
        (AGENT / name).read_text(encoding="utf-8")
        for name in ("init", "imagesrv-agent")
    )
    assert step in sources


def test_the_agent_sends_its_steps_to_the_declared_path():
    for name in ("init", "imagesrv-agent", "lib/core/common.sh"):
        text = (AGENT / name).read_text(encoding="utf-8")
        for hit in re.findall(r"/boot/step\?[^\"' ]*", text):
            assert hit.startswith("/boot/step?mac=") and "&s=" in hit


def test_parse_marks_accepts_the_init_markers():
    assert trace.parse_marks(
        "init:1.50,modules:9.02,nic:9.10,link:11.40,dhcp:12.30"
    ) == {"init": 1.5, "modules": 9.02, "nic": 9.1,
          "link": 11.4, "dhcp": 12.3}


@pytest.mark.parametrize("raw", [
    "", "init", "init:", "init:abc", "bogus:1.0", "init:1.0,init:2.0",
    "init:1.0;rm -rf /", "x" * 201, None, 5,
])
def test_parse_marks_refuses_garbage(raw):
    assert trace.parse_marks(raw) is None


def test_init_marks_ride_on_agent_net_and_are_logged(caplog):
    spy = Spy()
    with caplog.at_level("INFO", logger="imagesrv.boot"):
        status, body = call(
            "/step",
            f"mac={MAC}&s=agent-net&up=init:1.50,modules:9.02,dhcp:12.30",
            spy,
        )
    assert status == 200 and body == trace.TINY_BODY
    assert spy.steps == [(MAC, "agent-net")]
    assert any("modules=9.02" in record.message and "dhcp=12.30" in record.message
               for record in caplog.records if record.levelname == "INFO")


def test_malformed_marks_do_not_cost_the_step(caplog):
    spy = Spy()
    with caplog.at_level("WARNING", logger="imagesrv.boot"):
        call("/step", f"mac={MAC}&s=agent-net&up=init:abc", spy)
    assert spy.steps == [(MAC, "agent-net")]
    assert any("malformed" in record.message for record in caplog.records
               if record.levelname == "WARNING")


def test_marks_of_a_step_that_was_not_recorded_are_not_logged(caplog):
    with caplog.at_level("INFO", logger="imagesrv.boot"):
        call("/step", f"mac={MAC}&s=agent-net&up=init:1.50",
             lambda mac, step: False)
    assert not any("boot marks" in record.message for record in caplog.records
                   if record.levelname == "INFO")


def test_a_step_without_marks_logs_no_marks(caplog):
    with caplog.at_level("INFO", logger="imagesrv.boot"):
        call("/step", f"mac={MAC}&s=agent-net", Spy())
    assert not any("boot marks" in record.message for record in caplog.records)


@requires_bash
def test_trace_step_carries_the_marks_in_the_url():
    result = sh(
        f'set -e; RUN_DIR=$(mktemp -d); export RUN_DIR; '
        f'. {posix(AGENT)}/lib/core/common.sh; '
        'curl() { for a in "$@"; do echo "$a"; done > "$RUN_DIR/url"; return 0; }; '
        f'SERVER=http://s; MAC={MAC}; '
        'trace_step agent-net "up=init:1.50,dhcp:3.20"; cat "$RUN_DIR/url"; '
        'trace_step agent-start; cat "$RUN_DIR/url"'
    )
    assert result.returncode == 0, result.stderr
    urls = [line for line in result.stdout.splitlines() if line.startswith("http://")]
    assert f"http://s/boot/step?mac={MAC}&s=agent-net&up=init:1.50,dhcp:3.20" in urls
    assert urls[-1].endswith("&s=agent-start") and not urls[-1].endswith("&")


def test_init_sends_its_marks_on_agent_net():
    text = (AGENT / "init").read_text(encoding="utf-8")
    assert re.search(r'trace_step agent-net "up=\$_up"', text)
