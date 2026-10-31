"""ייצוא וייבוא של אימג' כקובץ tar אחד — גיבוי, העברה בין שרתים, ארכיון.

אימג' הוא תיקייה, ותיקייה לא נשלחת בדפדפן. tar נבחר כי הוא זורם: אפשר
לכתוב אותו בייט-בייט בלי להחזיק 60GB בזיכרון ובלי קובץ ביניים בשרת.

הכתיבה כאן ידנית ולא דרך tarfile, כי tarfile דוחף את כל הקובץ דרך
אובייקט הפלט בקריאה אחת, ומחולל שמזרים לרשת חייב לקבל את הבייטים
תוך כדי. הפורמט הוא ustar תקני — כל כלי tar קורא את התוצאה.
"""

from __future__ import annotations

import hashlib
import json
import logging
import lzma
import secrets
import shutil
import tarfile
import zlib
from pathlib import Path
from typing import Iterator

from server.core.atomicfile import fsync_dir, fsync_file

from server.library.images import ImageLibrary, guarded_image_path, inside, streamed_partitions

log = logging.getLogger("imagesrv.archive")

BLOCK = 512
CHUNK = 1024 * 1024
NAME_LIMIT = 100          # שדה השם ב-ustar; שמות האימג'ים קצרים בהרבה
#: אזור הביניים של הייבוא. **בתוך** שורש הספרייה, כדי שהכניסה לספרייה
#: תהיה `rename` באותה מערכת קבצים ולא העתקה של עשרות ג'יגה — ובשם
#: שמתחיל בנקודה, שהוא אזור עבודה שהספרייה מדלגת עליו. אסימון לכל ייבוא:
#: שם קבוע היה מאפשר לייבוא שני למחוק את אזור הביניים של הראשון תוך כדי
#: האימות שלו.
STAGING_PREFIX = ".import-"
#: השם שהמניפסט שוכב תחתיו עד שכל ה-sha256 נבדקו.
UNVERIFIED = "manifest.unverified"


class ArchiveError(ValueError):
    pass


def unreadable_message(exc: BaseException) -> str:
    """הודעה בעברית לארכיון שלא נקרא (#1016). הטקסט הגולמי של Python
    (``ReadError('not a gzip file')``) הולך ללוג בלבד; כישלון נשאר כישלון
    גלוי — רק הנוסח משתנה."""
    log.warning("archive unreadable: %s: %s", type(exc).__name__, exc)
    if isinstance(exc, (tarfile.TarError, EOFError, zlib.error, lzma.LZMAError)):
        return "הקובץ אינו ארכיון תקין (tar, אפשר דחוס) — בדקו שהועלה הקובץ הנכון ושלא נקטע"
    return "הארכיון לא נקרא — הפרטים ביומן השרת"


# --- ייצוא -------------------------------------------------------------------


def _header(name: str, size: int, mtime: int) -> bytes:
    encoded = name.encode("utf-8")
    if len(encoded) > NAME_LIMIT:
        raise ArchiveError(f"שם ארוך מדי לארכיון: {name}")

    def octal(value: int, width: int) -> bytes:
        return f"{value:0{width - 1}o}\0".encode()

    header = bytearray(BLOCK)
    header[0:len(encoded)] = encoded
    header[100:108] = octal(0o644, 8)          # mode
    header[108:116] = octal(0, 8)              # uid
    header[116:124] = octal(0, 8)              # gid
    header[124:136] = octal(size, 12)
    header[136:148] = octal(mtime, 12)
    header[148:156] = b" " * 8                 # מקום לסכום הביקורת
    header[156:157] = b"0"                     # קובץ רגיל
    header[257:263] = b"ustar\0"
    header[263:265] = b"00"
    header[148:156] = f"{sum(header):06o}\0 ".encode()
    return bytes(header)


def tar_size(directory: Path) -> int:
    """אורך ה-tar ש-``tar_stream`` ינפיק — לכותרת ``Content-Length`` של
    העברה בין שרתים (#655). אותם קבצים, אותו סדר, אותו ריפוד."""
    total = 0
    for entry in sorted(directory.iterdir()):
        path = guarded_image_path(directory, entry.name)
        if path is None:
            if entry.is_symlink():
                raise ArchiveError(f"קישור סימבולי אסור בארכיון אימג': {entry.name}")
            continue
        size = path.stat().st_size
        total += BLOCK + size + (-size % BLOCK)
    return total + BLOCK * 2


def tar_stream(directory: Path, arcname: str) -> Iterator[bytes]:
    """מזרים את תוכן התיקייה כ-tar. קבצים רגילים בלבד, ללא רקורסיה."""
    for entry in sorted(directory.iterdir()):
        path = guarded_image_path(directory, entry.name)
        if path is None:
            if entry.is_symlink():
                raise ArchiveError(f"קישור סימבולי אסור בארכיון אימג': {entry.name}")
            continue
        size = path.stat().st_size
        yield _header(f"{arcname}/{path.name}", size, int(path.stat().st_mtime))
        with path.open("rb") as handle:
            sent = 0
            while chunk := handle.read(CHUNK):
                sent += len(chunk)
                yield chunk
        if sent != size:
            # הקובץ השתנה תוך כדי קריאה; ארכיון קטוע גרוע משגיאה.
            raise ArchiveError(f"הקובץ {path.name} השתנה במהלך הייצוא")
        padding = -size % BLOCK
        if padding:
            yield b"\0" * padding
    yield b"\0" * (BLOCK * 2)                  # סוף ארכיון


# --- ייבוא -------------------------------------------------------------------


def _safe_name(member: tarfile.TarInfo) -> str:
    """שם הפריט אם הוא קובץ רגיל בנתיב יחסי שאינו יוצא מהתיקייה; אחרת נדחה."""
    name = member.name.replace("\\", "/")
    if not member.isfile():
        raise ArchiveError(f"הארכיון מכיל פריט שאינו קובץ: {name}")
    # ‏``isfile`` מקבל גם sparse ו-CONTTYPE. פריט sparse נפרש לבייטים רבים
    # בהרבה ממה שנשלח, ועוקף את בדיקת המקום הפנוי ואת תקרת הגודל (#1457).
    if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE):
        raise ArchiveError(f"הארכיון מכיל פריט מסוג שאינו נתמך "
                           f"(sparse/contiguous): {name}")
    if name.startswith("/") or ".." in Path(name).parts:
        raise ArchiveError(f"נתיב לא בטוח בארכיון: {name}")
    return name


def _safe_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """רק קבצים רגילים, בלי נתיבים מוחלטים, יציאה מהתיקייה או קישורים."""
    members = []
    for member in tar.getmembers():
        _safe_name(member)
        members.append(member)
    if not members:
        raise ArchiveError("הארכיון ריק")
    return members


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _shown(value: object) -> str:
    """ערך מהמניפסט בתוך הודעת שגיאה — קצוץ ומצוטט.

    מה שנדחה מוצג, אחרת אי אפשר לתקן את הארכיון; אבל הוא בא מקובץ של
    מישהו אחר, ולכן הוא לא נכנס להודעה כטקסט חופשי באורך שהוא בחר.
    """
    text = str(value)
    return repr(text[:60] + "…" if len(text) > 60 else text)


def _refuse_taken(image_id: str, taken: set[str]) -> None:
    if image_id in taken:
        raise ArchiveError(f"אימג' עם המזהה {image_id} כבר קיים בספרייה")


def _clear(staging: Path) -> None:
    """מנקה את אזור הביניים, ואומר ביומן אם לא הצליח.

    ‏`ignore_errors` לבדו הופך "לא הצלחנו למחוק" ל"נמחק": מה שנשאר כבר
    אינו נראה כאימג', אבל הוא תופס מקום עד שמישהו יידע עליו (עיקרון 5).
    """
    shutil.rmtree(staging, ignore_errors=True)
    if staging.exists():
        log.warning("import staging was left behind at %s", staging)


def import_tar(archive: Path, images_root: Path, existing_ids: set[str]) -> dict:
    """פורק ארכיון לספרייה. מחזיר את המניפסט שנקלט.

    האימות כאן הוא הסיבה שהפונקציה קיימת: אימג' שנכנס מבחוץ נבדק מול
    ה-sha256 שבמניפסט שלו לפני שהוא מוצע לכיתה. אימג' פגום שמתגלה רק
    באמצע שחזור הוא בדיוק מה שאסור.

    לכן שלושה דברים קורים כאן ולא במקום אחר (‏#71): החילוץ הוא לאזור
    עבודה שהספרייה מדלגת עליו, המניפסט אינו קיים בשמו כל עוד לא נבדק,
    ובדיקת ההתנגשות חוזרת מול הספרייה **ברגע הכניסה** — לא רק מול צילום
    שצולם לפני האימות, שאורך דקות על אימג' אמיתי.
    """
    staging = images_root / f"{STAGING_PREFIX}{secrets.token_hex(4)}"
    staging.mkdir(parents=True)
    try:
        with tarfile.open(archive, "r:*") as tar:
            members = _safe_members(tar)
            tar.extractall(staging, members=members, filter="data")
        return _admit(staging, [m.name.replace("\\", "/") for m in members],
                      images_root, existing_ids, lambda path, _key: _sha256(path))
    finally:
        _clear(staging)


def import_tar_stream(source, images_root: Path, existing_ids: set[str], *,
                      max_bytes: int) -> dict:
    """כמו ``import_tar``, אבל מזרם שנקרא **פעם אחת** (‏#1457, קליטה במשני).

    כל קובץ נכתב ישר לאזור הביניים, וה-sha256 שלו מחושב **על אותם בייטים
    בזמן הכתיבה** — בלי קובץ ``.transfer`` זמני, בלי העתקה של ``extractall``
    ובלי קריאה שנייה לאימות. ההשוואה מול המניפסט, השם הזמני של המניפסט,
    ובדיקות ההתנגשות והנתיב — אותו ``_admit`` של ``import_tar``: אימג' נכנס
    לספרייה רק אחרי שכל מחיצה תאמה (עיקרון 6). ``source`` הוא אובייקט עם
    ``read(n)``; ספירת הבייטים מול Content-Length היא של הקורא.
    ``max_bytes`` — תקרה לסך הבייטים שנכתבים לדיסק (אורך הגוף המוצהר, שעליו
    נבדק המקום הפנוי): כתיבה שעוברת אותה נעצרת בשמה.
    """
    staging = images_root / f"{STAGING_PREFIX}{secrets.token_hex(4)}"
    staging.mkdir(parents=True)
    try:
        base = staging.resolve()
        names: list[str] = []
        digests: dict[str, str] = {}
        total = 0
        with tarfile.open(fileobj=source, mode="r|", bufsize=CHUNK) as tar:
            for member in tar:
                name = _safe_name(member)
                dest = inside(staging / name, staging)
                if dest is None or dest == base:
                    raise ArchiveError(f"נתיב לא בטוח בארכיון: {_shown(name)}")
                if dest.exists():
                    raise ArchiveError(f"קובץ כפול בארכיון: {_shown(name)}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                data = tar.extractfile(member)
                digest = hashlib.sha256()
                written = 0
                with dest.open("xb") as out:
                    while chunk := data.read(CHUNK):
                        total += len(chunk)
                        if total > max_bytes:
                            raise ArchiveError(f"הארכיון נפרש מעבר ל-{max_bytes} "
                                               f"בייטים המוצהרים")
                        digest.update(chunk)
                        out.write(chunk)
                        written += len(chunk)
                if written != member.size:
                    raise ArchiveError(f"הקובץ {_shown(name)} נקטע בארכיון: "
                                       f"{written} מתוך {member.size} בייטים")
                rel = dest.relative_to(base).parts
                digests["/".join(rel[1:])] = digest.hexdigest()
                names.append(name)
        if not names:
            raise ArchiveError("הארכיון ריק")
        return _admit(staging, names, images_root, existing_ids,
                      lambda _path, key: digests.get(key))
    finally:
        _clear(staging)


def _admit(staging: Path, names: list[str], images_root: Path,
           existing_ids: set[str], digest_of) -> dict:
    """מה שאחרי החילוץ, משותף ל-``import_tar`` ול-``import_tar_stream``:
    מניפסט לא-מאומת, בדיקות מזהה/התנגשות/נתיב, ‏sha256 של כל מחיצה מול
    המניפסט, ורק אז ``manifest.json``. ``digest_of(path, key)`` מחזיר את
    ה-sha256 של קובץ המחיצה (``key`` = הנתיב היחסי בתיקיית האימג'), או
    ``None`` אם לא חושב — וזה כשל, לא התאמה."""
    roots = {Path(name).parts[0] for name in names}
    if len(roots) != 1:
        raise ArchiveError("הארכיון חייב להכיל תיקיית אימג' אחת")
    folder = staging / roots.pop()

    manifest_path = folder / "manifest.json"
    if not manifest_path.is_file():
        raise ArchiveError("אין manifest.json בארכיון")
    # תיקייה עם manifest.json מצהירה "כאן מונח אימג'". כל עוד הבייטים
    # לא נבדקו ההצהרה אינה נכונה, ולכן המניפסט שוכב תחת שם אחר: ייבוא
    # שנקטע — נפילת שרת, הפסקת חשמל — אינו משאיר על הדיסק, שהוא מקור
    # האמת, תיקייה חלקית שנראית כמו אימג' שלם.
    unverified = folder / UNVERIFIED
    manifest_path.replace(unverified)
    manifest = json.loads(unverified.read_text(encoding="utf-8"))
    problem = ImageLibrary._validate(manifest)
    if problem:
        raise ArchiveError(f"מניפסט לא תקין: {_shown(problem)}")
    # המזהה שבמניפסט הוא שם התיקייה שהאימג' ייכנס אליה, והמניפסט הזה
    # הגיע מקובץ שמישהו העלה: `../evil` בשדה הזה הוא כתיבה אל מחוץ
    # לספרייה (‏#110). ‏`_validate` למעלה דוחה אותו בשמו — מזהה שאינו בצורת
    # המזהים הוא מניפסט לא תקין, לא מנוקה בשקט לצורה שכן תעבור. בדיקה
    # נוספת כאן לא יכלה לרוץ לעולם (‏#1620).
    _refuse_taken(manifest["id"], existing_ids)

    # קריאה טרייה, אחרי האימות: אימג' מאומת בעל אותו מזהה אינו נדחק
    # ואינו נדרס — הייבוא נדחה, והקיים נשאר כפי שהוא. תיקיית האימג'
    # אינה חייבת להיקרא בשם המזהה, ולכן `target.exists()` לבדו אינו
    # מוצא התנגשות מזהים.
    _refuse_taken(manifest["id"], set(ImageLibrary(images_root).scan()))
    # חגורה שנייה, על הנתיב עצמו ולא על המזהה: מה שנכנס לספרייה חייב
    # לנחות בתוכה. ‏`target.exists()` לבדו אינו גבול — יעד מחוץ לשורש
    # שאינו קיים עובר אותו בשקט, וזה בדיוק המקרה המסוכן.
    target = inside(images_root / manifest["id"], images_root)
    if target is None:
        raise ArchiveError(
            f"יעד האימג' יוצא משורש הספרייה: {_shown(manifest['id'])}")
    if target.exists():
        raise ArchiveError(f"התיקייה {manifest['id']} כבר קיימת")

    try:
        folder.rename(target)
    except OSError as exc:
        if target.exists():
            raise ArchiveError(
                f"התיקייה {manifest['id']} כבר קיימת") from exc
        raise

    verified = False
    try:
        for part in streamed_partitions(manifest):
            # ‏`_safe_members` שמר על מה שנכתב לדיסק, אבל השם הזה נקרא
            # מהמניפסט ולא מחברי ה-tar. הבדיקה קודמת לכל נגיעה בקובץ:
            # ‏`is_file` על נתיב שהתוקף בחר הוא כבר תשובה על מה קיים בשרת.
            name = part["file"]
            path = inside(target / name, target) if isinstance(name, str) else None
            if path is None:
                raise ArchiveError(f"נתיב קובץ מחיצה לא בטוח במניפסט: {_shown(name)}")
            if not path.is_file():
                raise ArchiveError(f"חסר קובץ מחיצה בארכיון: {_shown(name)}")
            key = path.relative_to(target.resolve()).as_posix()
            if digest_of(path, key) != part["sha256"]:
                raise ArchiveError(f"אימות נכשל: {_shown(name)} אינו תואם ל-sha256")

        # ‏#1813: המחיצות, המניפסט והתיקייה לדיסק **לפני** שהמניפסט מקבל את
        # שמו — אחרת נפילת חשמל משאירה מניפסט תקין מעל קבצים באורך 0.
        for part in streamed_partitions(manifest):
            fsync_file(target / part["file"])
        fsync_file(target / UNVERIFIED)
        fsync_dir(target)
        (target / UNVERIFIED).replace(target / "manifest.json")
        fsync_dir(target)
        fsync_dir(images_root)
        verified = True
    finally:
        if not verified:
            _clear(target)

    return manifest
