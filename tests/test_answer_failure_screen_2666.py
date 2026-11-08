"""תשובת שרת שאינה מובנת, בכרטיס שהשרת מפיץ בו: מסך הכישלון, לא דיסק בשקט (‏#2666).

הכרעת נדב (07/11, בסקירת מודל האמון #2578): **כל כשל אימות במכונה שמגיעה לרשת
שהשרת מפיץ בה אימג'ים מסתיים במסך הגרפי** — הסיבה בעברית וכפתור "אתחל עכשיו".
הפער היה תשובה לא תקינה או `schema` לא מוכר: ‏`grub_menu.decide` החזירה `LOCAL`
בשקט, וגם `decide.sh` של הסוכן (שם הסיבה על המסך הייתה "אין למחשב הזה משימה").

* **התפריט** — רק על דגל שהשרת שם על ראיה חיובית (‏`answer_failure.FLAG`):
  ‏`schema` לא מוכר → ערך הסוכן הרגיל, שורת קרנל נקייה (עיקרון 2).
* **השרת** — הדגל רק כשהבקשה הגיעה ל**כרטיס מפיץ** (‏`hello.serves_images`), ורק
  בתקציב של #75 (‏`bootguard.ATTEMPT_LIMIT`). אחריו — הדיסק. מחוץ לרשת המפיצה,
  או בכל ספק — כמו היום.
* **הסוכן** — `schema` שאינו 1 (כולל תשובה שלא נקראה) → `bad_answer` →
  ‏`die_local` עם הסיבה האמיתית, מסך הכישלון ו"אתחל עכשיו". לאן האתחול הבא —
  השרת מחליט (‏#75): הסוכן אינו יודע אם הכרטיס מפיץ, והוא אינו סומך על תשובה
  שלא הבין.

בקרה שלילית: ראו גוף ה-PR — כל קובצי הייצור של ה-PR הוחזרו יחד ל-origin/main.
"""

from __future__ import annotations

import re
import shlex
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from boot.grub_menu import AGENT, LOCAL, GrubConfig, decide, render

REPO = Path(__file__).resolve().parents[1]
FLAG = "answer_failure_screen"
CODE = "answer-failure-screen"
VLAN = "http://10.44.12.10:8080"
OFF_VLAN = "http://10.10.10.8:8080"
CFG = GrubConfig(server_base=VLAN)
#: מכונה שאינה רשומה — אין צורך בכיתה כדי שהתשובה תיבנה.
MAC = "5c:60:ba:3e:b3:22"
LIMIT = 3   # bootguard.ATTEMPT_LIMIT — נבדק גם מולו למטה


def linux_args(text: str) -> list[str]:
    line = next(ln for ln in text.splitlines() if ln.strip().startswith("if linux "))
    return [word for word in line.split() if word.startswith("imagesrv.")]


def decision(text: str) -> str:
    return next(line for line in text.splitlines() if line.startswith("# decision: "))


# --- GRUB ------------------------------------------------------------------------------


@pytest.mark.parametrize("schema", [2, 99, "x", "1", None, 1.5])
def test_the_flag_on_an_unknown_schema_boots_the_agent_with_a_clean_kernel_line(schema):
    answer = {"schema": schema, "known": True, "role": "classroom",
              "task": {"id": "tsk_secret"}, FLAG: True}
    d = decide(answer)
    assert (d.action, d.code) == (AGENT, CODE)
    assert d.gui_screen and not d.recovery_mode
    text = render(answer, CFG)
    assert "set default=imagesrv" in text and "set timeout=0" in text
    # עיקרון 2: רק הכתובת — בלי mode ובלי שום דבר מהתשובה
    assert linux_args(text) == ["imagesrv.server=http://10.44.12.10:8080"]
    assert "tsk_secret" not in text and "classroom" not in text
    assert "--id local" in text                                     # הדיסק נשאר כנפילה בקובץ
    text.encode("ascii")


@pytest.mark.parametrize("flag", [None, False, "true", 1, "yes"])
def test_anything_but_true_is_todays_local_disk(flag):
    answer = {"schema": 2, "known": True}
    if flag is not None:
        answer[FLAG] = flag
    assert (decide(answer).action, decide(answer).code) == (LOCAL, "bad-schema")
    text = render(answer, CFG)
    assert "set timeout=0" in text and "--id imagesrv" not in text and "vmlinuz" not in text


def test_an_answer_that_is_not_an_object_carries_no_evidence_and_stays_local():
    """אין על מה לשים דגל: תשובה שאינה אובייקט אינה יכולה לומר מאיפה הגיעה.
    ה-resolver של השרת תמיד מחזיר אובייקט (‏`build_answer`)."""
    for junk in ("not a dict", None, [FLAG]):
        assert (decide(junk).action, decide(junk).code) == (LOCAL, "bad-answer")


def test_the_flag_on_an_understood_answer_changes_nothing():
    """הדגל חל רק על `schema` שאינו מובן. על תשובה תקינה — אותה החלטה בדיוק."""
    for answer in ({"schema": 1, "known": True, "role": "classroom", "task": None, "session": None},
                   {"schema": 1, "known": False},
                   {"schema": 1, "known": True, "role": "classroom", "boot_guard": "exhausted"}):
        assert decide({**answer, FLAG: True}) == decide(answer)


# --- השרת: התקציב והכרטיס המפיץ, יחידה ----------------------------------------------------


def test_the_budget_is_the_one_of_75():
    pytest.importorskip("fastapi")
    from server.agent import answer_failure, bootguard
    assert answer_failure.FLAG == FLAG
    assert bootguard.ATTEMPT_LIMIT == LIMIT


def test_a_count_that_could_not_be_written_is_no_screen():
    """עיקרון 5: לא הצלחנו לספור — אין מסך (הכיוון הבטוח הוא הדיסק), ולא "נספר"."""
    pytest.importorskip("fastapi")
    from server.agent import answer_failure
    conn = sqlite3.connect(":memory:")                  # בלי הטבלה: הכתיבה נכשלת
    conn.row_factory = sqlite3.Row
    answer = {"schema": 2, "known": True}
    assert answer_failure.offer(conn, MAC, answer) == answer


# --- השרת, דרך /boot/menu כמו GRUB --------------------------------------------------------


@pytest.fixture()
def bad_schema(server, monkeypatch):
    """השרת בונה תשובה שהמחולל אינו מבין — כמו גרסת ממשק שאינה נתמכת."""
    from server.agent import hello as hello_module
    real = hello_module.build_answer
    monkeypatch.setattr(hello_module, "build_answer",
                        lambda *a, **k: {**real(*a, **k), "schema": 2})
    return server


def menu(server, mac: str, base: str | None = VLAN) -> str:
    url = f"/boot/menu?mac={mac}" if base is None else f"{base}/boot/menu?mac={mac}"
    response = server["anon"].get(url)
    assert response.status_code == 200
    return response.text


def age_mark(server, mac: str, minutes: int) -> None:
    at = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    conn = server["ctx"].conn
    conn.execute("UPDATE boot_attempts SET last_at = ?, first_at = ? WHERE mac = ?", (at, at, mac))
    conn.commit()


def test_on_the_distributing_card_the_menu_serves_the_screen(bad_schema):
    text = menu(bad_schema, MAC)
    assert decision(text) == f"# decision: {CODE}", text
    assert "set default=imagesrv" in text and "set timeout=0" in text
    assert linux_args(text) == ["imagesrv.server=http://10.44.12.10:8080"]
    assert MAC not in text.split('menuentry "ImageSrv"')[1]           # ה-MAC אינו בערך הסוכן
    text.encode("ascii")


@pytest.mark.parametrize("base", [OFF_VLAN, None], ids=["not-distributing", "no-evidence"])
def test_on_a_card_that_does_not_distribute_it_boots_the_disk(bad_schema, base):
    """רשת שהשרת לא מפיץ בה — וגם ב-TestClient, שה-scope שלו אינו כתובת (ספק):
    ערך אחד, ‏`timeout=0`, בלי ImageSrv, ובלי לספור (תרחיש 7 בהדגמה)."""
    for _ in range(LIMIT + 1):
        text = menu(bad_schema, MAC, base)
        assert decision(text) == "# decision: bad-schema"
        assert text.count("menuentry ") == 1 and "--id imagesrv" not in text
    assert bad_schema["ctx"].conn.execute(
        "SELECT 1 FROM boot_attempts WHERE mac = ?", (MAC,)).fetchone() is None


def test_after_the_budget_of_75_the_disk(bad_schema):
    for _ in range(LIMIT):
        assert decision(menu(bad_schema, MAC)) == f"# decision: {CODE}"
    for _ in range(2):                                                # ממשיך בלולאה — נשאר בדיסק
        text = menu(bad_schema, MAC)
        assert decision(text) == "# decision: bad-schema"
        assert "--id imagesrv" not in text and "vmlinuz" not in text


def test_a_quiet_hour_starts_a_new_budget(bad_schema):
    """המונה תחום בזמן (בלי זה מכונה הייתה נחסמת לנצח אחרי תקלה אחת): אתחול
    שבא אחרי שקט ארוך מהחלון מקבל שוב את המסך."""
    for _ in range(LIMIT + 1):
        menu(bad_schema, MAC)
    age_mark(bad_schema, MAC, 11)
    assert decision(menu(bad_schema, MAC)) == f"# decision: {CODE}"


def test_a_good_answer_on_the_distributing_card_is_unchanged(server):
    """בלי תקלה — אותה החלטה של היום (‏#1802), ואין ספירה של #2666."""
    assert decision(menu(server, MAC)) == "# decision: unregistered-screen"
    row = server["ctx"].conn.execute(
        "SELECT context FROM boot_attempts WHERE mac = ?", (MAC,)).fetchone()
    assert row["context"] == "unregistered-screen"


def test_the_screen_condition_is_one_function_for_both_screens():
    """"הכרטיס הזה מפיץ" — תנאי אחד (‏`serves_images`), לשני המסכים, כדי שכרטיס
    כיתות מפיץ (v2) ייכנס במקום אחד (הבהרת נדב, 07/11)."""
    source = (REPO / "server/agent/hello.py").read_text(encoding="utf-8")
    resolve = source[source.index("    def resolve("):]
    assert "serves_images(scope, server_base)" in resolve
    assert "on_deploy_vlan(" not in resolve


# --- הסוכן ------------------------------------------------------------------------------


def _agent_decide(schema: str) -> str:
    from test_agent import decide as sh_decide
    return sh_decide(schema=schema, session="running")


@pytest.mark.parametrize("schema", ["2", "", "null", "x", "1x"])
def test_the_agent_names_an_answer_it_does_not_understand(schema):
    """גם תשובה שלא נקראה בכלל (`json_get` ריק / null) — לא "אין משימה"."""
    assert _agent_decide(schema) == "bad_answer"


def test_the_agent_dies_with_the_true_reason_on_the_failure_screen(tmp_path):
    from test_failure_screen_1569 import run_agent, values
    src = (REPO / "agent/imagesrv-agent").read_text(encoding="utf-8")
    arm = re.search(r'^\s*bad_answer\)\s*die_local "([^"]+)"', src, re.M)
    assert arm, "decide's bad_answer has no die_local arm"
    line = arm.group(1)
    r = run_agent(tmp_path, f"die_local {shlex.quote(line)}")
    assert f"TEST-REBOOT: {line}" in r["out"]
    why = " ".join(values(r["recs"], "fail_why"))
    assert "תשובת השרת" in why and "אין למחשב הזה משימה" not in why, why
    assert "reboot|אתחל עכשיו" in values(r["recs"], "fail_btn")
