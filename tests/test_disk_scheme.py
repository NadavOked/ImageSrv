"""‏#126: ‏LBA1 הוא הסקטור הלוגי השני, לא "בייט 512".

‏`disk_scheme` חיפש את `EFI PART` בבייט 512 קבוע. על כונן **4Kn**
(‏`logical_block_size=4096`) כותרת ה-GPT יושבת בבייט 4096, והקריאה
הקבועה נחתה בתוך ה-MBR המגונן — ומצאה שם אפסים. ואז השורה הבאה מצאה
`55aa` בבייט 510, כי **לכל** דיסק GPT יש MBR מגונן עם החתימה הזו,
והכריזה על כונן GPT תקין ומאותחל: `mbr`.

למה זה נשמע כמו פרט ונדלק דווקא עכשיו: ‏#106 (מוזג ב-30/08) הביא את
סירוב הקליטה לקונסולה **עם סיבה**. על כונן 4Kn ההודעה תהיה "הכונן אינו
GPT (נמצא: mbr)" — סיבה ספציפית, משכנעת ושגויה, על כונן תקין. מפעיל
שקורא אותה מחליף כבל, מחליף מגירה, ובסוף מחליף כונן. כשל סתום הוא בעיה;
כשל מטעה הוא בעיה שעולה שעת עבודה של טכנאי.

הבדיקות כאן בונות את הכונן כקובץ, בייט-בייט, כי בדיוק הבייטים האלה הם
הבאג — ולא מזייפות את `dd`: ‏`dd` על קובץ רגיל קורא בדיוק כמו על צומת
בלוקים, ולכן מה שהפונקציה מחזירה כאן הוא מה שהיא תחזיר מול חומרה.

מה **לא** מוכח כאן, ולכן ה-Issue מסומן `needs:metal`: שכונן 4Kn אמיתי
אכן חושף `4096` ב-sysfs באותו נתיב ושה-`dd` על צומת אמיתי מיישר כמצופה.
הצורה נבדקת, החומרה לא.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, fake_machine, posix, sh  # noqa: F401

pytestmark = requires_native(("bash", BASH))

GPT_SIG = b"EFI PART"
MBR_SIG = b"\x55\xaa"


def gpt_image(block_size: int) -> bytes:
    """דיסק GPT כפי שהוא באמת מונח על הפלטה.

    ‏LBA0 הוא ה-MBR המגונן — ‏`55aa` בבייט 510, בכל גודל סקטור, כי ה-MBR
    הוא מבנה קשיח של 512 בייט. ‏LBA1 — כלומר הבייט ה-`block_size` — הוא
    כותרת ה-GPT. שני אלה יחד הם המלכודת: החתימה שנראית כמו MBR נמצאת על
    כל דיסק GPT בעולם, וכל מי שיפספס את הכותרת יימשך אליה.
    """
    head = bytearray(block_size * 3)
    head[510:512] = MBR_SIG
    head[block_size:block_size + len(GPT_SIG)] = GPT_SIG
    return bytes(head)


def mbr_image(block_size: int) -> bytes:
    """‏MBR ותו לא: החתימה בבייט 510, ואין כותרת GPT באף סקטור."""
    head = bytearray(block_size * 3)
    head[510:512] = MBR_SIG
    return bytes(head)


def blank_image(block_size: int) -> bytes:
    """כונן שזה עתה יצא מהאריזה — לא זה ולא זה."""
    return bytes(block_size * 3)


def disk_box(tmp_path: Path, image: bytes, block_size: str | None = "512",
             name: str = "sda") -> Path:
    """עץ `/sys` ו-`/dev` מזערי לכונן אחד, בדיוק כמו שאר בדיקות הסוכן.

    ‏`block_size=None` = אין `queue/logical_block_size` בכלל, כלומר סקטור
    שאי אפשר לקרוא.
    """
    (tmp_path / "dev").mkdir(parents=True, exist_ok=True)
    (tmp_path / "dev" / name).write_bytes(image)
    queue = tmp_path / "sys/block" / name / "queue"
    queue.mkdir(parents=True)
    if block_size is not None:
        (queue / "logical_block_size").write_text(block_size + "\n")
    return tmp_path


def scheme_of(box: Path, name: str = "sda") -> str:
    return sh(
        f'export SYSROOT={posix(box)!r} DEVROOT={posix(box / "dev")!r}; '
        f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/hw/sysinfo.sh; '
        f'disk_scheme "$DEVROOT/{name}"'
    ).strip()


# --- הכונן הרגיל, זה שכל המעבדה עליו, לא זז ----------------------------------


@pytest.mark.parametrize(
    ("image", "expected"),
    [(gpt_image(512), "gpt"), (mbr_image(512), "mbr"), (blank_image(512), "none")],
    ids=["gpt", "mbr", "none"],
)
def test_a_512_byte_sector_disk_reads_exactly_as_it_always_did(tmp_path, image,
                                                               expected):
    """הרגרסיה שאסור לגרום: 512 הוא המקרה הנפוץ, וכל שלוש התשובות
    חייבות להישאר מה שהיו — כולל על דיסק GPT, שגם לו יש `55aa`."""
    assert scheme_of(disk_box(tmp_path, image)) == expected


# --- הבאג עצמו ---------------------------------------------------------------


def test_the_4kn_image_carries_the_very_trap_that_fooled_the_old_code():
    """בקרה על כלי הבדיקה: הכונן המזויף חייב להיות זה שמפיל את הקוד הישן.

    בלי שלוש הטענות האלה, טסט ה-4Kn היה יכול לעבור על תמונה שלא מייצגת
    כלום — למשל אחת בלי MBR מגונן, שהקוד הישן היה מחזיר עליה `none`
    ולא `mbr`, וכל הסיפור של #126 היה מתפספס.
    """
    image = gpt_image(4096)
    assert image[510:512] == MBR_SIG, "אין MBR מגונן — זו לא תמונת GPT אמיתית"
    assert image[512:520] != GPT_SIG, "הכותרת יושבת ב-512, כלומר זה לא 4Kn"
    assert image[4096:4104] == GPT_SIG, "אין כותרת GPT ב-LBA1 של כונן 4Kn"


def test_a_4kn_gpt_disk_is_gpt_and_not_mbr(tmp_path):
    """‏#126 מילה במילה. על הקוד הלא-מתוקן הטסט הזה מחזיר `mbr`."""
    box = disk_box(tmp_path, gpt_image(4096), block_size="4096")
    assert scheme_of(box) == "gpt"


def test_a_4kn_disk_with_only_an_mbr_is_still_mbr(tmp_path):
    """התיקון אינו "להחזיר gpt על 4Kn" — כונן 4Kn מחולק MBR קיים,
    והוא עדיין `mbr`. בלי הטסט הזה, `echo gpt` היה עובר."""
    box = disk_box(tmp_path, mbr_image(4096), block_size="4096")
    assert scheme_of(box) == "mbr"


def test_a_blank_4kn_disk_is_none(tmp_path):
    box = disk_box(tmp_path, blank_image(4096), block_size="4096")
    assert scheme_of(box) == "none"


@pytest.mark.parametrize("block_size", ["512", "1024", "2048", "4096"])
def test_the_header_is_looked_for_at_lba1_whatever_the_sector_is(tmp_path,
                                                                 block_size):
    """הכלל, ולא שני המקרים: הכותרת נמצאת איפה שהסקטור אומר שהיא."""
    box = disk_box(tmp_path, gpt_image(int(block_size)), block_size=block_size)
    assert scheme_of(box) == "gpt"


# --- מה שקורה כשאי אפשר לקרוא את גודל הסקטור (עיקרון 5) ----------------------


@pytest.mark.parametrize(
    "block_size",
    [None,        # אין קובץ בכלל — sysfs לא מאונט, או כונן בלי queue
     "",          # קובץ ריק
     "   ",       # רווחים
     "abc",       # לא מספר
     "0",         # מספר שאינו גודל סקטור
     "256",       # מתחת למינימום של הקרנל
     "513"],      # לא כפולה של 8 — לא גודל סקטור של אף כונן
    ids=lambda v: {None: "missing"}.get(v, repr(v)),
)
def test_a_sector_size_that_cannot_be_read_is_unknown_and_never_a_guess(
        tmp_path, block_size):
    """הלב של התיקון, ולא ה-offset עצמו.

    ‏512 כברירת מחדל הוא בדיוק מה שיצר את #126 — ההנחה השקטה שסקטור הוא
    512. הוא היה "עובד" על כל המעבדה, ואז חוזר בדיוק על הכונן שבגללו
    התיקון נכתב. ו-`none` גרוע ממנו: הוא טענה חיובית — "אין כאן טבלת
    מחיצות" — שמסמנת כונן מלא נתונים כריק (‏`has_data:false`), ו"ריק"
    בקונסולה הוא כונן שמותר לדרוס.

    ‏`unknown` הוא מה שידענו: לא ידענו. אותה הכרעה בדיוק כמו `null`
    של ‏`secure_boot` (‏#84).
    """
    box = disk_box(tmp_path, gpt_image(512), block_size=block_size)
    scheme = scheme_of(box)
    assert scheme == "unknown", (
        f"גודל סקטור לא קריא ({block_size!r}) הוכרע כ-{scheme!r} — "
        "זו הנחה, לא ידיעה"
    )


def test_a_partition_path_never_answers_for_the_whole_disk(tmp_path):
    """מחיצה אינה בעלת סכימה, ואין לה `queue` ב-`/sys/block`.

    שני הקוראים (`build_disk_entry` ו-`capture_disk`) מעבירים לכאן שם
    מ-`list_disks`, כלומר כונן שלם. אם מישהו יעביר מחיצה — התשובה היא
    "לא ידוע" ולא סכימה שנשאבה בטעות מהבייטים של המחיצה.
    """
    box = disk_box(tmp_path, gpt_image(512))
    (box / "dev" / "sda1").write_bytes(gpt_image(512))
    assert scheme_of(box, name="sda1") == "unknown"


# --- מקצה לקצה: מה שמגיע ל-hello ---------------------------------------------


def hello_of(fake) -> dict:
    out = sh(
        f'export SYSROOT={posix(fake["sysroot"])!r} '
        f'DEVROOT={posix(fake["dev"])!r} '
        f'RUN_DIR={posix(fake["run"])!r} IFACE=eth0 IP=10.44.12.187 D_ROLE=build; '   # #1924: only the build machine says what is on a disk
        f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/hw/sysinfo.sh; '
        f'build_hello'
    )
    return json.loads(out)


def test_hello_of_a_4kn_machine_reports_a_gpt_disk(fake_machine):  # noqa: F811
    """הדרך המלאה: מכונה שכל מה ששונה בה הוא גודל הסקטור, וה-hello
    שהשרת מקבל ממנה."""
    queue = fake_machine["sysroot"] / "sys/block/sda/queue"
    (queue / "logical_block_size").write_text("4096\n")
    (fake_machine["dev"] / "sda").write_bytes(gpt_image(4096))

    (disk,) = hello_of(fake_machine)["disks"]
    assert disk["scheme"] == "gpt"
    assert disk["has_data"] is True
    # ‏`size_bytes` **לא** משתנה: `/sys/block/<dev>/size` הוא ביחידות של
    # 512 בכל כונן, גם 4Kn — זו מוסכמה של הקרנל ולא גודל הסקטור.
    assert disk["size_bytes"] == 500118192 * 512


def test_an_unknown_scheme_reaches_hello_as_a_disk_that_may_hold_data(
        fake_machine):  # noqa: F811
    """‏`unknown` חייב לשמור על `has_data: true`. הכיוון השני — לסמן
    כונן שלא ידענו לקרוא כ"ריק" — הוא הכיוון שמוחק נתונים."""
    (fake_machine["sysroot"] / "sys/block/sda/queue/logical_block_size").unlink()

    (disk,) = hello_of(fake_machine)["disks"]
    assert disk["scheme"] == "unknown"
    assert disk["has_data"] is True
