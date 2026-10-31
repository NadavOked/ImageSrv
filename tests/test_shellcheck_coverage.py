"""כל קובץ מעטפת בריפו נסרק על ידי shellcheck.

עד #371 הרשימה ב-`.github/workflows/shellcheck.yml` נכתבה ביד בחמישה
מקומות, וכל קובץ שנוסף אחריה נפל מהכיסוי **בשקט** — הוא לא הופיע
כאדום, הוא פשוט לא נבדק (#247). אותה רשימה ידנית גם נקבה בנתיבים
שהפרסום הנקי מוציא, ולכן הסורק כולו נעלם מהריפו הציבורי — הריפו
היחיד שמריץ CI (#371).

היום הרשימה **נגזרת מהעץ**, ולכן הטסט הזה כבר אינו יכול להשוות מול
רשימה כתובה. במקומה הוא **משחזר את כללי הגזירה מתוך ה-workflow עצמו**
— אילו תיקיות מנוכות, ואילו קבצים בלי סיומת נוספים — ומריץ אותם על
העץ. מכאן שהוא נופל בדיוק בשני המקרים שמעניינים:

* קובץ מעטפת בעץ שכללי הגזירה אינם תופסים;
* ניכוי חדש ב-workflow שאין לו נימוק ב-`EXEMPT` כאן.

הטסט אינו מריץ `shellcheck` — הוא רץ גם על ווינדוס, שם אין בינארי.
"""
import glob
import io
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows" / "shellcheck.yml"

# קבצים שמוחרגים במכוון. כל חריג חייב סיבה — "פשוט לא ברשימה" אינו
# חריג, הוא חור. מפתח שמסתיים ב-`/**` מחריג תת-עץ שלם.
EXEMPT: dict = {}


def _is_exempt(path: str) -> bool:
    for pattern in EXEMPT:
        if pattern.endswith("/**"):
            if path.startswith(pattern[:-2]):
                return True
        elif path == pattern:
            return True
    return False


CONT = re.compile(r"\\\s*\n\s*")          # המשך שורה של מעטפת
QUOTES = "\"'"

PRUNE = re.compile(r"-path \./([^\s]+) -prune")
EXTRAS = re.compile(r"for extra in ([^;]+); do")


def _flat():
    """תוכן ה-workflow אחרי פירוק המשכי שורה."""
    return CONT.sub(" ", io.open(WF, encoding="utf-8").read())


def _prunes():
    """התיקיות שהגזירה מנכה — נקראות מה-workflow, לא מונחות."""
    return set(PRUNE.findall(_flat()))


def _extras():
    """קובצי המעטפת שאין להם סיומת `.sh` ולכן נוספים במפורש."""
    match = EXTRAS.search(_flat())
    assert match, "לא נמצאה תוספת הקבצים שאין להם סיומת .sh"
    return [t for t in match.group(1).split() if "/" in t]


def _all_shell():
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        return {p.replace(os.sep, "/")
                for p in glob.glob("**/*.sh", recursive=True)
                if not p.startswith(".git")}
    finally:
        os.chdir(cwd)


def _derived():
    """מה שכללי הגזירה של ה-workflow יתפסו בעץ הזה.

    שכפול של ה-`find` שבקובץ: כל `*.sh` מלבד תת-עצים מנוכים, ועוד
    נקודות הכניסה של הסוכן שאין להן סיומת.
    """
    prunes = _prunes()
    out = {p for p in _all_shell()
           if not any(p == d or p.startswith(d + "/") for d in prunes)}
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        out |= {e for e in _extras() if Path(e).is_file()}
    finally:
        os.chdir(cwd)
    return out


def test_the_derivation_is_not_empty():
    """אפס קבצים פירושו שהגזירה נשברה — לא ש'הכול מכוסה'."""
    derived = _derived()
    assert len(derived) >= 20, f"רק {len(derived)} קבצים נגזרו — חשוד"
    assert "agent/imagesrv-agent" in derived and "agent/init" in derived, (
        "נקודות הכניסה של הסוכן אינן נגזרות — אין להן סיומת .sh")
    assert any(p.startswith("agent/lib/") for p in derived), (
        "‏agent/lib/ אינו נגזר — זו השכבה שרצה כ-root מול דיסקים")


def test_there_are_shell_files_to_check():
    assert len(_all_shell()) >= 25, "פחות מדי קבצי sh — הסריקה לא רצה"


def test_every_shell_file_is_scanned():
    missing = sorted(p for p in _all_shell() - _derived() if not _is_exempt(p))
    assert not missing, (
        "קבצי מעטפת שאינם נסרקים על ידי shellcheck: %s. הגזירה ב-"
        ".github/workflows/shellcheck.yml אינה תופסת אותם — או שהוסף שם "
        "ניכוי, או שהקובץ יושב מחוץ לתחום ה-find. קובץ שאינו נסרק אינו "
        "'נקי' — הוא לא נבדק (#247)." % missing)


def test_every_prune_carries_a_declared_reason():
    """ניכוי ב-workflow חייב נימוק כתוב כאן.

    זה מה שמחליף את הרשימה הידנית כשומר: אפשר לצמצם את הסריקה, אבל
    לא בשקט. ניכוי `agent/` היה מעלים ~3,900 שורות שרצות כ-root, וזה
    היה נראה בדיוק כמו ריצה ירוקה.
    """
    for pruned in _prunes():
        assert _is_exempt(pruned + "/x.sh") or _is_exempt(pruned), (
            f"‏{pruned} מנוכה מהסריקה ב-shellcheck.yml בלי נימוק ב-EXEMPT "
            "כאן. ניכוי בלי נימוק הוא חור שקט.")


def test_the_file_list_is_derived_and_not_written_by_hand():
    """אף קריאה ל-shellcheck אינה נוקבת בנתיב.

    שתי התקלות של #371 ו-#247 נולדו מרשימה כתובה; הרשימה נוקבת
    בנתיבים שהפרסום הנקי מוציא, ולכן הקובץ סווג כפרטי והסורק ירד
    מהריפו היחיד שמריץ CI. נתיב קשיח בשורת shellcheck הוא חזרה לשם.
    """
    for line in _flat().split("\n"):
        if line.lstrip().startswith("#") or "shellcheck " not in line:
            continue
        for tok in re.split(r"\s+", line.strip().strip(QUOTES)):
            tok = tok.strip().strip(QUOTES)
            assert not tok.endswith(".sh"), (
                f"‏shellcheck.yml נוקב בנתיב קשיח: {tok!r}. הרשימה נגזרת "
                "מהעץ — נתיב כתוב ביד מחזיר את #247 ואת #371.")


def test_the_shell_dialect_is_derived_and_not_guessed():
    """הסוג נגזר מה-shebang, מלבד הסוכן שבו הוא מוצהר.

    הניסיון הראשון פיצל את הקבצים ל-bash ול-sh לפי ניחוש. כל השמונה
    היו bash, וה-CI נפל על bashisms שהוצהרו כ-POSIX. החלטה שאפשר
    לגזור אין סיבה לנחש — והטסט שומר שלא נחזור לנחש.
    """
    flat = _flat()
    assert re.search(r"head -n? ?1 ", flat), (
        "השלב אינו גוזר את סוג המעטפת מה-shebang")
    assert re.search(r'shellcheck -s "\$\w+"', flat), (
        "‏shellcheck נקרא עם סוג קבוע ולא עם הסוג שנגזר")
    assert "agent/lib/*.sh|agent/imagesrv-agent|agent/init)" in flat, (
        "‏agent/lib/ נטען ב-`.` ואין לו shebang — הניב שלו חייב להיות "
        "מוצהר, ולא נגזר מקובץ שאין ממה לגזור בו")


def test_the_derivation_fails_loudly_when_it_returns_nothing():
    """סריקה של רשימה ריקה יוצאת 0 ונראית כמו סריקה שעברה."""
    flat = _flat()
    assert re.search(r'\[ "\$n" -lt \d+ \]', flat) and "::error::" in flat, (
        "אין רצפה על מספר הקבצים הנגזרים — גזירה שבורה תעבור בירוק")


def test_exemptions_carry_a_reason():
    for path, reason in EXEMPT.items():
        assert isinstance(reason, str) and reason.strip(), (
            f"{path} מוחרג בלי סיבה — חריג בלי נימוק הוא חור שקט")


def test_no_exemption_is_stale():
    """חריג שאינו תואם לאף קובץ הוא חור פתוח לקבצים עתידיים.

    ‏`vendor/**` בפרט: אם התת-עץ הוסר או שונה שמו, החריג נשאר ומכסה
    מראש כל מה שיגיע לשם אחר כך — הרשאה שאיש לא הכריע עליה.
    """
    shell = _all_shell()
    for pattern in EXEMPT:
        assert any(_is_exempt(p) for p in shell if _match(pattern, p)), (
            f"{pattern} אינו תואם לאף קובץ מעטפת — חריג מת שיכסה קבצים "
            "שטרם נכתבו. מחק אותו.")


def _match(pattern: str, path: str) -> bool:
    if pattern.endswith("/**"):
        return path.startswith(pattern[:-2])
    return path == pattern
