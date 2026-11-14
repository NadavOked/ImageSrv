"""#1734 — מסך "stay powered on" שבו `sleep` אינו נטען הוא לולאה בלי תקרה.

משכפל 2 (BIOS), 18/10: אחרי משיכת ה-initrd `pre-boot` לא הגיע, ואחרי 96
שניות GRUB משך את `sleep.mod` מה-TFTP כל שנייה, בלי סוף. מבחוץ זה נראה
כמו "כבוי ולא מגיב ל-WoL".

מה שהקוד מייצר מסביר את הצורה הזו: כל כשל סופי במשכפל BIOS (`_retry_load`
בלי נפילה לדיסק, ו-`try_local` על i386-pc) נגמר במסך העצירה של #345 —
`while [ "$stay_on" = "y" ]; do sleep --interruptible 60; done`. `sleep` הוא
מודול. כשהוא לא נטען (למשל זיכרון שנגמר, או מודול שלא מתאים ל-core),
הפקודה נכשלת מיד, הלולאה ממשיכה, ו-GRUB מנסה לטעון אותו שוב בכל סיבוב.
96 השניות מתאימות לשלושה ניסיונות initrd של כ-32 שניות כל אחד. זו הסקה
מהקוד, לא מדידה על הברזל.

**התיקון:** בודקים פעם אחת ש-`sleep` נטען (`sleep 0`) לפני הלולאה, ובתוך
הלולאה קוראים לו רק אם הבדיקה הצליחה. אם לא, נשארים במסך עם הודעה ולא
טוענים מודול שוב.

**הטסט מריץ את הבלוק שנרנדר** במפרש זעיר שמכיר רק את מה שיש בבלוק. פקודה
שהוא לא מכיר עוצרת את הטסט ולא מדולגת. `sleep` מזויף בשני מצבים: נטען או
לא נטען. **מה שלא נבדק כאן:** GRUB עצמו. ההוכחה היא משכפל 2 על הברזל.
"""

from __future__ import annotations

import re

import pytest

from boot.grub_menu import GrubConfig, render, render_bootstrap

CFG = GrubConfig(server_base="http://10.44.12.10:8080")
PROMISE = 'echo "Contact IT. This computer will stay powered on."'
STEPS = 2000          # תקרת הסימולציה: לולאת העצירה אמורה לרוץ לנצח


def _cloner_answer() -> dict:
    return {"schema": 1, "known": True, "role": "cloner",
            "group": {"id": "grp_CLONERS", "label": "x", "suffix": "01"},
            "task": None, "session": None, "allowed_images": ["img_7f3a91"],
            "ui": {"language": "he", "require_login": False}}


#: כל המקומות שבהם מסך העצירה מוגש: try_local בקובץ הסטטי (משכפל BIOS שהשרת
#: שקט מולו), ו-`_retry_load` בתפריט הדינמי של משכפל (initrd שלא נטען, #1734).
RENDERED = [
    pytest.param(render_bootstrap(CFG), id="bootstrap"),
    pytest.param(render(_cloner_answer(), CFG), id="cloner-menu"),
]


def _stop_blocks(text: str) -> list[list[str]]:
    """כל בלוק עצירה: מה-echo של ההבטחה ועד ה-`done` שסוגר את ה-while."""
    lines = [line.strip() for line in text.splitlines()]
    blocks = []
    for i, line in enumerate(lines):
        if line != PROMISE:
            continue
        depth, block = 0, []
        for item in lines[i:]:
            block.append(item)
            if item.startswith("while "):
                depth += 1
            elif item == "done":
                depth -= 1
                if depth == 0:
                    break
        else:
            raise AssertionError("בלוק עצירה בלי while/done סוגר")
        blocks.append(block)
    return blocks


class _Grub:
    """מפרש זעיר לתת-השפה של בלוק העצירה. `sleep` אינו builtin: ``loadable``
    קובע אם הוא נטען. כל קריאה שלו נספרת — בהצלחה או בכישלון."""

    def __init__(self, loadable: bool):
        self.loadable = loadable
        self.env: dict[str, str] = {}
        self.sleep_calls = 0
        self.steps = 0

    def run(self, lines: list[str]) -> None:
        self._block(lines, 0, len(lines))

    def _step(self) -> None:
        # גם `[` הוא צעד: לולאה שגופה ריק הייתה רצה כאן לנצח בלי לספור
        self.steps += 1
        if self.steps > STEPS:
            raise _Forever

    def _cond(self, text: str) -> bool:
        m = re.fullmatch(r'\[ "\$(\w+)" = "(\w+)" \]', text)
        if m:
            self._step()
            return self.env.get(m.group(1), "") == m.group(2)
        return self._command(text)

    def _command(self, line: str) -> bool:
        self._step()
        word = line.split()[0]
        if word == "echo":
            return True
        if word == "set":
            name, value = line.split(None, 1)[1].split("=", 1)
            self.env[name] = value
            return True
        if word == "unset":
            self.env.pop(line.split()[1], None)
            return True
        if word == "sleep":
            self.sleep_calls += 1
            return self.loadable
        raise AssertionError(f"פקודה שהמפרש לא מכיר: {line!r} — הרחב אותו, אל תדלג")

    def _end(self, lines, i, opener) -> tuple[int, int | None]:
        """אינדקס הסוגר של מבנה שנפתח ב-i, ושל ה-else שלו (אם יש)."""
        depth, other = 0, None
        for j in range(i, len(lines)):
            line = lines[j]
            if line.startswith(("if ", "while ")):
                depth += 1
            elif line in ("fi", "done"):
                depth -= 1
                if depth == 0:
                    return j, other
            elif line == "else" and depth == 1 and opener == "if":
                other = j
        raise AssertionError(f"{opener} בלי סוגר")

    def _block(self, lines, start, stop) -> None:
        i = start
        while i < stop:
            line = lines[i]
            if line.startswith("if ") and line.endswith("; then"):
                end, other = self._end(lines, i, "if")
                if self._cond(line[3:-len("; then")]):
                    self._block(lines, i + 1, other if other is not None else end)
                elif other is not None:
                    self._block(lines, other + 1, end)
                i = end + 1
            elif line.startswith("while ") and line.endswith("; do"):
                end, _ = self._end(lines, i, "while")
                while self._cond(line[6:-len("; do")]):
                    self._block(lines, i + 1, end)
                i = end + 1
            else:
                self._command(line)
                i += 1


class _Forever(Exception):
    """הסימולציה הגיעה לתקרה — הלולאה עדיין רצה (הצפוי במסך העצירה)."""


def _simulate(block: list[str], loadable: bool) -> _Grub:
    grub = _Grub(loadable)
    with pytest.raises(_Forever):
        grub.run(block)
    return grub


@pytest.mark.parametrize("text", RENDERED)
def test_a_sleep_that_cannot_load_is_tried_once_not_every_round(text):
    """הבאג של #1734: `sleep` שאינו נטען נקרא בכל סיבוב — ו-GRUB מושך את
    `sleep.mod` מה-TFTP בכל קריאה, בלי סוף. אחרי התיקון: ניסיון אחד."""
    blocks = _stop_blocks(text)
    assert blocks, "לא נמצא מסך עצירה בקובץ"
    for block in blocks:
        grub = _simulate(block, loadable=False)
        assert grub.sleep_calls == 1, (
            f"sleep שלא נטען נקרא {grub.sleep_calls} פעמים ב-{STEPS} צעדים — "
            "כל קריאה היא משיכה של sleep.mod מה-TFTP")


@pytest.mark.parametrize("text", RENDERED)
def test_a_sleep_that_loads_still_waits_in_every_round_and_never_leaves(text):
    """הכיוון ההפוך (#345 לא זז): כש-`sleep` נטען, המסך ממתין בכל סיבוב
    ולעולם לא יוצא מהלולאה — `_Forever` הוא הראיה."""
    for block in _stop_blocks(text):
        grub = _simulate(block, loadable=True)
        assert grub.sleep_calls > 100, "ההמתנה אינה בכל סיבוב — לולאה עסוקה"


@pytest.mark.parametrize("text", RENDERED)
def test_the_screen_says_when_it_holds_without_sleep(text):
    """מסך שנשאר בלי `sleep` אומר זאת: הטכנאי רואה מסך שלא מגיב למקשים."""
    for block in _stop_blocks(text):
        assert any("sleep" in line and line.startswith("echo ") for line in block), block


def test_the_cloner_initrd_failure_ends_on_the_stop_screen():
    """המסלול שנמדד ב-#1734: משכפל BIOS שה-initrd שלו לא נטען אחרי שלושה
    ניסיונות מגיע למסך העצירה, לא לדיסק המקומי."""
    text = render(_cloner_answer(), CFG)
    failure = text[text.index("initrd could not be loaded"):]
    assert failure.index(PROMISE) < failure.index("pre-boot")
    assert "chain_local" not in text
