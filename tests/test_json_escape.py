"""‏`json_escape` — נבדק תחת ה-awk של busybox, ולא תחת זה של המכונה.

הבדיקות האחרות של הסוכן מריצות את קבצי ה-sh דרך bash מקומי, ומשם
‏`awk` הוא מה שהמכונה מביאה: ‏gawk בווינדוס וב-CI, ‏mawk על דביאן.
לגבי רוב הקוד זה בסדר. לגבי `json_escape` זה בדיוק ההבדל שבין ירוק
לבין סוכן שבור: ברצף ההחלפה של `gsub` לוכסן הוא בעצמו תו בריחה,
ו-**gawk, ‏mawk ו-busybox awk קוראים את הרצפים האלה אחרת**.

המדידה שהריצה את #145, על אותה מכונה, על אותו קובץ:

    dash + mawk        a"b  ->  a\\"b     ✓ נראה תקין
    busybox ash + awk  a"b  ->  a"b       ✗ בלי הברחה

‏`tests/test_agent.py::test_the_login_body_survives_hostile_passwords`
כבר בדק סיסמה עם גרשיים — והוא עבר, חודשים, בזמן שהמכונה הפיזית קיבלה
‏400 על גוף פגום והסיסמה לא נבדקה כלל. טסט שאינו רץ תחת busybox אינו
מוכיח דבר על הסוכן, ולכן החבילה הזאת מריצה הכול תחת `busybox ash`.

**ו-`busybox ash` לבדו אינו מספיק.** ה-busybox של דביאן נבנה כ-standalone
shell ומעדיף את ה-applets של עצמו; זה של אובונטו (‏CI) לא — שם `awk`
נפתר דרך PATH ונפל על gawk, וכל הטסטים כאן היו נצבעים ירוקים בלי לבדוק
את הסוכן. לכן `bb()` בונה תיקיית applets משלה ומקדימה אותה ל-PATH.

שתי בקרות עומדות על זה, וכל אחת מהן נכשלה כבר בפעם הראשונה:

* `test_the_awk_inside_busybox_ash_is_busyboxs_own` — ראיה חיובית שה-awk
  שבתוקף מזדהה בבאנר של BusyBox. בלעדיה אין לחבילה הזאת ערך.
* `test_the_fix_gives_the_same_answer_under_every_awk_here` — התוצאה חייבת
  להיות **זהה** בכל מנוע awk שיש על המכונה. זו הטענה שהמימוש הישן הפר,
  והיא נכונה בלי תלות בגרסה: כל awk וכל בנייה.

מה שאין כאן, במכוון: טסט שדורש שהמימוש **הישן** ייכשל. הוא נכשל תחת
busybox 1.37.0 (דביאן 13, מה שנארז ב-initrd) ואינו נכשל תחת ה-busybox
הישן יותר של אובונטו, כי הרצף `\\"` ברצף ההחלפה של gsub הוא התנהגות לא
מוגדרת שכל בנייה קוראת אחרת. הראיה שהטסטים האלה נכשלים על הקוד הלא-מתוקן
נעשתה ידנית על מעבדת ה-VM — ‏16 מתוך 24 — ומתועדת ב-PR של #145.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from agentlib import flat_lib

REPO = Path(__file__).resolve().parent.parent
AGENT = REPO / "agent"

#: ה-busybox שתחתיו רצות הבדיקות. ‏`IMAGESRV_BUSYBOX` גובר, כדי שאפשר
#: יהיה לכוון בדיוק לזה שחולץ מ-initrd מסוים.
BUSYBOX = os.environ.get("IMAGESRV_BUSYBOX") or shutil.which("busybox")

#: ה-initrd שנארז בפועל. כשהוא כאן — משווים אליו את הבינארי שבבדיקה.
INITRD = Path(os.environ.get("IMAGESRV_INITRD", "/srv/imagesrv/boot/initrd.img"))

pytestmark = requires_native(
    ("busybox", BUSYBOX), posix=True,
    why="בלי busybox אין מה לבדוק כאן — הסוכן רץ תחתיו, לא תחת awk של המכונה",
)

#: הגרסה הישנה, מילה במילה מ-#145. נשארת כאן כבקרה, לא כקוד חי.
PRE_FIX = r"""
json_escape_pre_fix() {
    printf '%s' "$1" | awk 'BEGIN { ORS="" } {
        gsub(/\\/, "\\\\"); gsub(/"/, "\\\"");
        gsub(/\r/, ""); gsub(/\t/, "\\t");
        if (NR > 1) printf "\\n"; printf "%s", $0 }'
}
json_escape_pre_fix "$1"
"""

LIBS = ("common.sh", "jsonq.sh", "sysinfo.sh", "restore.sh", "progress.sh", "ui.sh",
        "pull.sh")


def source_line(*names: str) -> str:
    return "".join(f'. "{flat_lib()}/{name}"\n' for name in names)


#: ה-applets שהסוכן משתמש בהם בנתיבים שנבדקים כאן. מקושרים ל-busybox
#: ומוקדמים ל-PATH, כי ‏`busybox ash` **אינו** מבטיח לבדו applets של
#: busybox: דביאן בונה standalone shell ואובונטו לא.
APPLETS = ("awk", "printf", "sed", "tr", "cat", "head", "tail", "basename")


def busybox_env(tmp_path: Path, awk: str | None = None) -> dict[str, str]:
    """סביבה שבה ה-applets האלה הם של busybox, בכל הפצה.

    ‏`awk` מחליף את מנוע ה-awk בלבד — ככה אותה פונקציה נמדדת תחת gawk
    ו-mawk בלי לשנות דבר אחר."""
    bin_dir = tmp_path / ("bin_" + (Path(awk).name if awk else "busybox"))
    if not bin_dir.exists():
        bin_dir.mkdir()
        for applet in APPLETS:
            (bin_dir / applet).symlink_to(BUSYBOX)
        if awk:
            (bin_dir / "awk").unlink()
            (bin_dir / "awk").symlink_to(awk)
    env = dict(os.environ)
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    return env


def awk_engines() -> dict[str, str]:
    """כל מנועי ה-awk שיש על המכונה. ‏busybox תמיד בפנים — הוא הנבדק."""
    engines = {"busybox": str(BUSYBOX)}
    for name in ("awk", "gawk", "mawk", "original-awk"):
        found = shutil.which(name)
        if found and Path(found).resolve() != Path(str(BUSYBOX)).resolve():
            engines.setdefault(Path(found).resolve().name, found)
    return engines


def bb(tmp_path: Path, body: str, *args: str, awk: str | None = None) -> str:
    """מריץ קטע sh תחת `busybox ash`, עם ה-applets של busybox, ומחזיר stdout."""
    script = tmp_path / "under_busybox.sh"
    script.write_text(body, encoding="utf-8")
    proc = subprocess.run(
        [BUSYBOX, "ash", str(script), *args],
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(REPO), stdin=subprocess.DEVNULL, env=busybox_env(tmp_path, awk),
    )
    assert proc.returncode == 0, f"נכשל תחת busybox:\n{proc.stderr}\n{proc.stdout}"
    return proc.stdout


def escape(tmp_path: Path, value: str) -> str:
    """התוצאה הגולמית של `json_escape` — לפני שעוטפים אותה בגרשיים."""
    return bb(tmp_path, source_line("common.sh") + 'json_escape "$1"', value)


def through(tmp_path: Path, value: str) -> str:
    """מה ש-`json.load` מוציא מהמחרוזת שהסוכן היה בונה. נכשל על JSON פגום."""
    return json.loads('"' + escape(tmp_path, value) + '"')


# --- הראיה שזה באמת busybox --------------------------------------------------


def test_the_awk_inside_busybox_ash_is_busyboxs_own(tmp_path):
    """‏הראיה החיובית: ה-awk שבתוקף מזדהה כ-BusyBox.

    בלי הראיה הזאת החבילה כולה חסרת ערך — היא הייתה עוברת בדיוק כמו
    שהיא עברה על mawk בזמן שהסוכן נשבר."""
    proc = subprocess.run(
        [BUSYBOX, "ash", "-c", "awk --help"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
        env=busybox_env(tmp_path),
    )
    banner = proc.stdout + proc.stderr
    assert "BusyBox" in banner, f"ה-awk כאן אינו של busybox: {banner[:200]!r}"


#: הקלטים שההפרש בין המנועים נמדד עליהם.
ACROSS = ('a"b', "a\\b", 'pa"ss\\word', "a\x01b")


def test_the_fix_gives_the_same_answer_under_every_awk_here(tmp_path):
    """‏השומר: אותה תוצאה בכל מנוע awk שיש על המכונה, ולא רק תוצאה נכונה
    תחת אחד מהם.

    זו בדיוק הטענה שהמימוש הישן הפר, והיא נכונה בלי תלות בגרסה או
    בהפצה: אם מישהו יחזיר `gsub` או יכניס רצף בריחה תלוי-מימוש, הטסט
    הזה ייפול ויראה איזה מנוע נבדל ובמה. ‏busybox תמיד ברשימה."""
    engines = awk_engines()
    assert "busybox" in engines
    for value in ACROSS:
        answers = {name: bb(tmp_path, source_line("common.sh") + 'json_escape "$1"',
                            value, awk=path)
                   for name, path in engines.items()}
        assert len(set(answers.values())) == 1, (
            f"מנועי awk נבדלו על {value!r}: {answers}")
        assert json.loads('"' + answers["busybox"] + '"') == value


def test_the_pre_fix_escape_is_not_what_the_fix_does(tmp_path):
    """תיעוד, לא אכיפה של "הישן שבור בכל מקום" — כי הוא לא.

    הרצף `\\"` ברצף ההחלפה של `gsub` הוא התנהגות **לא מוגדרת**: ‏busybox
    ‏1.37.0 (דביאן 13, מה שנארז ב-initrd) קורא אותו כגרש בודד ולכן לא
    מבריח, ובנייה ישנה יותר של busybox — זו שעל ה-runner של אובונטו —
    כן מבריחה. בדיוק זאת הסיבה ש-#145 שרד חודשים: אין תשובה אחת לשאלה
    "האם הקוד הישן שבור", והיא תלויה במכונה שמולה שואלים.

    מה שכן נכון בכל מנוע, ולכן נאכף כאן: הישן והמתוקן **אינם שקולים**.
    לפחות תו בקרה אחד יוצא גולמי מהישן, וזה JSON פסול בכל מקרה."""
    for name, path in awk_engines().items():
        old = bb(tmp_path, PRE_FIX, "a\x01b", awk=path)
        new = bb(tmp_path, source_line("common.sh") + 'json_escape "$1"',
                 "a\x01b", awk=path)
        assert new == "a\\u0001b", f"{name}: {new!r}"
        assert old != new, f"{name}: הישן והמתוקן זהים — {old!r}"


def test_the_busybox_under_test_is_the_one_the_initrd_ships(tmp_path):
    """‏`build_initramfs.sh` מעתיק את ה-busybox של המכונה לתוך ה-initrd,
    ולכן על מכונה שבנתה initrd שני העותקים חייבים להיות אותו בינארי.

    בלי initrd (‏CI, עמדת פיתוח) אין מה להשוות — ולא מדלגים, כי דילוג
    תחת `IMAGESRV_REQUIRE_NATIVE=1` מפיל את הריצה כולה (#52). מה שכן
    נבדק תמיד: הבינארי קיים ומזדהה."""
    assert BUSYBOX and Path(BUSYBOX).exists()
    if not INITRD.exists():
        return
    (tmp_path / "x").mkdir()
    with open(INITRD, "rb") as raw:
        gz = subprocess.Popen(["gzip", "-dc"], stdin=raw, stdout=subprocess.PIPE)
        subprocess.run(["cpio", "-id", "--quiet", "bin/busybox"],
                       stdin=gz.stdout, cwd=tmp_path / "x",
                       stderr=subprocess.DEVNULL, check=False)
        gz.stdout.close()
        gz.wait()
    packed = tmp_path / "x" / "bin" / "busybox"
    assert packed.exists(), f"אין bin/busybox בתוך {INITRD}"
    assert (hashlib.sha256(packed.read_bytes()).hexdigest()
            == hashlib.sha256(Path(BUSYBOX).read_bytes()).hexdigest()), (
        f"ה-busybox שבבדיקה ({BUSYBOX}) אינו זה שנארז ב-{INITRD}")


# --- ההברחה עצמה ------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "why"),
    [
        ('a"b', "גרש בודד — זה מה שהפיל את הכניסה על הלנובו"),
        ("a\\b", "לוכסן בודד"),
        ('pa"ss\\word', "שניהם יחד"),
        ('\\"', "לוכסן שנצמד לגרש — הסדר בין שתי ההחלפות"),
        ("a\tb", "טאב"),
        ("a\x01b", "תו בקרה — JSON אוסר אותו גולמי"),
        ("a\x1fb", "תו הבקרה האחרון לפני התווים הנראים"),
        ("a\x08\x0cb", "backspace ו-form feed"),
        ("line1\nline2", "שתי שורות"),
        ('שלום "עולם" \\ נדב', "עברית עם גרש ולוכסן"),
        ("café", "‏UTF-8 בן שני בייטים"),
        ('{"nested":"json"}', "מחרוזת שהיא בעצמה JSON"),
        ('partclone: cannot open "/dev/sda1": No such file', "הודעת כלי אמיתית"),
        ("plain text 123", "קלט רגיל — חייב לצאת כמו שנכנס"),
        ("", "מחרוזת ריקה"),
    ],
)
def test_the_escaped_value_survives_a_real_json_parser(tmp_path, value, why):
    assert through(tmp_path, value) == value, why


def test_a_quote_really_comes_back_escaped(tmp_path):
    """לא רק ש-`json.load` עובר — הלוכסן באמת שם. ‏#145 היה בדיוק המצב
    שבו "לא ראינו סימן כישלון" נחשב להצלחה."""
    assert escape(tmp_path, 'a"b') == 'a\\"b'
    assert escape(tmp_path, "a\\b") == "a\\\\b"
    assert escape(tmp_path, "a\x01b") == "a\\u0001b"


def test_carriage_return_is_still_dropped(tmp_path):
    """התנהגות קיימת שלא משתנה: ‏CR יוצא, שאר תווי הבקרה מוברחים."""
    assert through(tmp_path, "a\rb") == "ab"


def test_a_long_field_is_escaped_whole(tmp_path):
    """אין קיצוץ בשקט — שדה ארוך יוצא שלם."""
    value = ('partclone: cannot open "/dev/sda1" \\ ' * 200)
    assert through(tmp_path, value) == value


# --- שלושת האתרים שהבאג נראה בהם --------------------------------------------


def test_the_console_sign_in_body_survives_a_password_with_a_quote(tmp_path):
    """קצה לקצה על גוף הכניסה של הגואי (‏`console_signin`, ‏buildmenu.sh) —
    הסיסמה הולכת ל-curl בצינור, ו-curl מזויף כאן רושם את מה שקיבל.
    (עד 11/10 נבדקו כאן `login_body` ו-`open_round_body`, של הכניסה בטקסט
    ושל סבב הכיתה בטקסט — שניהם הוסרו, GUI בלבד.)"""
    run = tmp_path / "run"
    run.mkdir()
    out = bb(
        tmp_path,
        f'export RUN_DIR="{run}" MAC="b4:2e:99:07:1a:c4" SERVER=http://x '
        'HTTP_TIMEOUT=1 HTTP_RETRIES=0\n'
        'RECOVERY_USER="nadav"; RECOVERY_PASS="$1"\n'
        + source_line("common.sh", "jsonq.sh", "buildmenu.sh")
        + 'curl() { cat > "$RUN_DIR/body"; printf 401; }\n'
        'console_signin >/dev/null; cat "$RUN_DIR/body"\n',
        'pa"ss\\word',
    )
    assert json.loads(out) == {"username": "nadav", "password": 'pa"ss\\word'}


def test_the_pull_body_survives_a_quote(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    out = bb(
        tmp_path,
        f'export RUN_DIR="{run}"\n'
        + source_line("common.sh", "pull.sh")
        + 'pull_body b4:2e:99:07:1a:c4 img_7f3a91 "$1" ""\n',
        'lab"tech',
    )
    body = json.loads(out)
    assert body["username"] == 'lab"tech' and body["password"] == ""


def test_the_failure_reason_reaches_the_console_intact(tmp_path):
    """‏`progress.sh:89` — השדה החמור. זה הנתיב של #106: סיבת הכישלון
    שמגיעה לקונסולה. הודעת כלי עם גרש הפילה את כל הדיווח, ואיתו את
    הסיבה, בדיוק כשהיא הכי נחוצה."""
    run = tmp_path / "run"
    (run / "targets").mkdir(parents=True)
    reason = 'partclone: cannot open "/dev/sda1": No such file or directory'
    out = bb(
        tmp_path,
        f'export RUN_DIR="{run}"\n'
        + source_line("common.sh", "progress.sh")
        + 'target_init sda 1024\ntarget_set sda failed "$1"\n'
        "build_progress sess_1 b4:2e:99:07:1a:c4\n",
        reason,
    )
    body = json.loads(out)
    assert body["targets"][0]["error"] == reason
