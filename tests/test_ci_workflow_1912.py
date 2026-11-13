"""#1912: שומרי רגרסיה לתשתית ה-CI — #1210 ו-#1356.

#1210: tests.yml/nightly.yml הריצו `apt-get install` בלי `apt-get update`,
נשענו על אינדקס החבילות השמור בתמונת ה-runner, וכשהמראה של Ubuntu התקדמה
— 404, ו-pytest לא רץ בכלל (אדום שפירושו "לא נבדק", לא "שבור").

#1356: מנוע השיכפול המקומי (#1322) צריך partclone/sgdisk/ntfs-3g/mkfs.vfat/pv,
וה-runner הציבורי לא התקין אותם; IMAGESRV_REQUIRE_NATIVE הפך את זה לאדום
רק **אחרי** פרסום. כאן זה נתפס לפני: כל כלי שהטסט של המנוע דורש ממופה
לחבילה שה-workflow מתקין, או מוצהר כחלק מתמונת ה-runner.

בקרה שלילית (מוטציות על tests.yml/nightly.yml, ראו גוף ה-PR):
  `sudo apt-get update -qq` הוסר → טסט #1210 נופל, בשמו של הקובץ.
  שורת `partclone gdisk ntfs-3g …` הוסרה (tests.yml שלפני #1356) → טסט #1356 נופל.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"
ENGINE_TEST = REPO / "tests" / "test_local_clone_engine.py"

#: שני ה-workflows שמריצים את הסוויטה המלאה — שניהם חייבים להתקין משהו, אחרת
#: הבדיקה של #1210 עוברת בריק.
SUITE_WORKFLOWS = ("tests.yml", "nightly.yml")

#: כלי של מנוע השיכפול → חבילת apt שמספקת אותו (דביאן/Ubuntu).
PACKAGE_OF = {
    "sgdisk": "gdisk",
    "partclone.ntfs": "partclone", "partclone.fat": "partclone",
    "partclone.ext4": "partclone", "partclone.dd": "partclone",
    "ntfsresize": "ntfs-3g", "mkntfs": "ntfs-3g", "ntfs-3g": "ntfs-3g",
    "mkfs.vfat": "dosfstools", "mkfs.ext4": "e2fsprogs",
    "pv": "pv", "jq": "jq", "curl": "curl",
}
#: כלים שתמונת ubuntu-latest נושאת בלי התקנה. המקור: אחרי 4c6c1b75 (#1356)
#: הציבורי עבר ירוק עם IMAGESRV_REQUIRE_NATIVE בלי להתקין אותם.
RUNNER_IMAGE = {"losetup", "gcc", "flock"}


def _steps(name: str) -> list[list[str]]:
    """שורות הפקודה של כל צעד — בלי הערות, עם המשכי `\\` מחוברים לשורה אחת."""
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    steps = []
    for chunk in re.split(r"^\s*- (?:name|uses):", text, flags=re.MULTILINE)[1:]:
        lines = [line for line in chunk.splitlines() if not line.lstrip().startswith("#")]
        joined = re.sub(r"\\\n\s*", " ", "\n".join(lines))
        steps.append(joined.splitlines())
    return steps


def _apt_installs(name: str) -> list[str]:
    return [line for step in _steps(name) for line in step if "apt-get install" in line]


def test_every_apt_install_in_a_workflow_follows_an_apt_update():
    """#1210: בכל workflow, כל `apt-get install` מקדים לו `apt-get update` באותו צעד."""
    for name in SUITE_WORKFLOWS:
        assert _apt_installs(name), f"{name}: לא נמצא apt-get install — הבדיקה הייתה עוברת בריק"
    stale: list[str] = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for step in _steps(path.name):
            updated = False
            for line in step:
                head = line.split("apt-get install", 1)[0]
                if "apt-get install" in line and not (updated or "apt-get update" in head):
                    stale.append(f"{path.name}: {line.strip()[:90]}")
                updated = updated or "apt-get update" in line
    assert stale == [], ("apt-get install בלי apt-get update לפניו — האינדקס השמור של ה-runner "
                         "יחזיר 404 כשהמראה תתקדם (#1210):\n" + "\n".join(stale))


def _engine_tools() -> list[str]:
    text = ENGINE_TEST.read_text(encoding="utf-8")
    match = re.search(r"for t in \(([^)]*)\)\]", text)
    assert match, f"{ENGINE_TEST.name}: רשימת הכלים של requires_native לא נמצאה"
    tools = re.findall(r'"([^"]+)"', match.group(1))
    assert len(tools) >= 10, tools
    return tools


def test_the_runner_installs_every_tool_the_local_clone_engine_requires():
    """#1356: כל כלי שהטסט של המנוע דורש — מותקן ב-workflow, או מוצהר בתמונה."""
    tools = _engine_tools()
    unmapped = sorted(t for t in tools if t not in PACKAGE_OF and t not in RUNNER_IMAGE)
    assert unmapped == [], (f"כלי חדש ב-{ENGINE_TEST.name} בלי חבילה ידועה — הוסף אותו ל-PACKAGE_OF "
                            f"ולהתקנה ב-workflow: {unmapped}")
    needed = sorted({PACKAGE_OF[t] for t in tools if t in PACKAGE_OF})
    for name in SUITE_WORKFLOWS:
        installed = {word for line in _apt_installs(name) for word in line.split()}
        missing = [pkg for pkg in needed if pkg not in installed]
        assert missing == [], (f"{name}: חבילות של מנוע השיכפול המקומי אינן מותקנות — "
                               f"REQUIRE_NATIVE יאדים את הציבורי (#1356): {missing}")
