"""‏#424 — מחיצה בלי קובץ אינה מחיצת swap, והצירוף הזה מחק מחיצות ווינדוס.

```sh
is_swap_partition() {     # $1 = fs, $2 = file
    [ "$1" = "swap" ] || [ "$2" = "null" ] || [ -z "$2" ]
}
```

התנאי הוא **או**: שדה `file` ריק הפך כל מחיצה ל-swap, בלי קשר ל-`fs`
שלה, ומשם היא הגיעה ישר ל-`make_swap` — כלומר `mkswap` על המיקום הסופי.
מניפסט עם `{"fs":"ntfs","file":""}` פירושו `mkswap /dev/sdaN` על מחיצת
ווינדוס. זה עיקרון 5 במקום שבו הוא הכי יקר: הפעולה שנגזרה מהניחוש
הרסנית, ואין ממנה חזרה.

"אין מה לחכות לו בזרם" ו"זו מחיצת swap" הם שני מצבים שונים. הראשון הוא
מניפסט פגום וחייב להיכשל בקול; רק השני מצדיק `mkswap`.

**שני צדדים, ושניהם נדרשים כאן.** הקליטה פוסלת מניפסט כזה בשם (עיקרון
6 — פגום נתפס שם, לא מול כיתה), *וגם* השחזור אינו סומך על כך שהקליטה
עבדה: אימג' שכבר יושב בספרייה מלפני התיקון נעצר לפני שנגעו בדיסק,
מסמן `failed`, ואומר איזו מחיצה ולמה (עיקרון 4).

מה שנמדד כאן אינו קוד יציאה אלא **מה שהטכנאי רואה ומה שקרה ליעד**:
‏`targets/<dev>/error`, קובץ הקריאות של `mkswap`, וקובץ הקריאות של
‏`sgdisk` — שהוא הראיה ש"הדיסק לא נגע". ‏`sgdisk --zap-all` הוא הנגיעה
הראשונה בדיסק בכל מסלול שחזור, ולכן השער חייב לשבת **לפניו**.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from conftest import MANIFEST_256, MANIFEST_LINUX, write_image
from native import requires_native
from test_agent import BASH, posix, sh
from test_restore_evidence import (
    build_box, rc_of, state_of, target_error,
)

requires_bash = requires_native(("bash", BASH))

WINDOWS_GUID = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
SWAP_GUID = "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F"

#: שורת תוכנית (ממשק 1) של מחיצת ווינדוס שה-`file` שלה **מחרוזת ריקה**.
#: זה בדיוק המניפסט מהאיסיו, בצורה שבה השחזור פוגש אותו.
NTFS_EMPTY_FILE = (
    f"3|{WINDOWS_GUID}|windows|ntfs|206848|53687091200"
    "|||false|33333333-3333-3333-3333-333333333333|"
)

#: אותה מחיצה, אבל `file` הוא JSON `null`. ‏`jq -r` מרנדר null כמחרוזת
#: `null`, ולכן זו צורה **שנייה ונפרדת** של אותו כשל — ושתיהן מופיעות
#: בתנאי המקורי.
NTFS_NULL_FILE = (
    f"3|{WINDOWS_GUID}|windows|ntfs|206848|53687091200"
    "|null|null|false|33333333-3333-3333-3333-333333333333|"
)

#: מחיצת swap אמיתית: ‏`fs=swap`, בלי קובץ ובלי sha — בדיוק מה שהקליטה
#: כותבת (אפיון סעיף 14). היא **חייבת** להמשיך לקבל `mkswap`.
SWAP_ROW = (
    f"2|{SWAP_GUID}|swap|swap|206848|8589934592"
    "|null|null|false|22222222-2222-2222-2222-222222222222|3f7c-swap"
)


def stub_mkswap(box: Path) -> None:
    """‏`mkswap` שמתעד ואינו נוגע בדבר.

    בלי הזיוף "לא הופעל" אינו מצב שאפשר לקרוא: על לינוקס `mkswap` אמיתי
    קיים ורץ, ובווינדוס הוא חסר ולכן *כל* קריאה נראית ככישלון. הקובץ
    הזה הופך את השאלה לראיה שנקראת מהדיסק.
    """
    path = box / "stubs" / "mkswap"
    path.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> \"{posix(box)}/mkswap.calls\"\n"
        "exit 0\n"
    )
    path.chmod(0o755)


def calls(box: Path, tool: str) -> str:
    path = box / f"{tool}.calls"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def live_nodes(box: Path, *indexes: int) -> None:
    """אילו `/dev/sdaN` "חזרו" מהדיסק — בצורה שהסוכן באמת שואל עליה.

    ‏`build_box` כותב את הרשימה עם מפריד הנתיבים של המערכת, ואילו הסוכן
    מקבל `DEVROOT` בצורת POSIX. בווינדוס השניים אינם משתווים, ולכן
    ‏`node_is_block` מחזירה שקר על כל צומת ו-`apply_gpt` נכשלת עוד לפני
    שהגיעה ללולאת הכתיבה — כלומר הבדיקה הייתה "עוברת" מסיבה שאינה
    הסיבה שנבדקת. הרשימה נכתבת כאן שוב, כדי שמה שנמדד יהיה מה שנטען.
    """
    (box / "nodes").write_text(
        "".join(f"{posix(box)}/dev/sda{i}\n" for i in indexes), encoding="utf-8"
    )


# --- הצד של הסוכן: מה שקורה מול הדיסק ---------------------------------------


@requires_bash
@pytest.mark.parametrize("row, shape", [(NTFS_EMPTY_FILE, "מחרוזת ריקה"),
                                        (NTFS_NULL_FILE, "null")])
def test_an_ntfs_partition_without_a_file_never_reaches_mkswap(tmp_path, row, shape):
    """הכשל בצורתו המלאה, מקצה לקצה.

    עד התיקון: ‏`is_swap_partition` החזירה אמת, ‏`make_swap` הריצה
    ‏`mkswap` על `/dev/sda3`, ‏`target_partition_done` נספרה, והמכונה
    סיימה ב-`done` — מחיצת ווינדוס מפורמטת, ודיווח של הצלחה מלאה.

    אחרי התיקון אין `mkswap`, אין `sgdisk` (כלומר הטבלה כלל לא נמחקה),
    והיעד נכשל בגלוי — מאז #1408 בשער התוכנית, לפני שנגעו בדיסק.
    """
    box, run, prelude = build_box(tmp_path, plan=[row])
    live_nodes(box, 3)
    stub_mkswap(box)
    out = sh(prelude + 'run_restore multicast sda http://s img m.json; echo "rc=$?"')

    assert rc_of(out) == "rc=1", out
    assert state_of(run) == "failed"
    assert calls(box, "mkswap") == "", f"‏mkswap רץ על מחיצת NTFS ({shape})"
    assert calls(box, "sgdisk") == "", "הדיסק נגע לפני שהמניפסט נבדק"
    # ‏#1408 (77557596): `validate_restore_plan` דוחה את השורה לפני
    # ‏`--zap-all`; ‏#1435: ההודעה נוקבת במחיצה ובכלל (test_restore_plan_1418).
    assert target_error(run) == ("תוכנית השחזור פגומה (מחיצה 3: קובץ או sha256 חסרים או "
                                 "לא חוקיים) — הדיסק לא שונה")


@requires_bash
def test_a_real_swap_partition_is_still_created_with_mkswap(tmp_path):
    """הבקרה החיובית, ובלעדיה התיקון עלול לשבור swap תקין בשקט.

    ‏`fs=swap` בלי קובץ הוא המצב הרגיל של מחיצת swap (אפיון סעיף 14),
    והיא נבראת ב-`mkswap -U` על המיקום שההרחבה קבעה (#48).
    """
    box, _run, prelude = build_box(tmp_path, plan=[SWAP_ROW])
    live_nodes(box, 2)
    stub_mkswap(box)
    out = sh(prelude + "restore_partition multicast http://s img sda 2 swap "
             'null null 3f7c-swap; echo "rc=$?"')

    assert rc_of(out) == "rc=0", out
    recorded = calls(box, "mkswap")
    assert "-U 3f7c-swap" in recorded, f"‏mkswap בלי ה-UUID מהמניפסט: {recorded!r}"
    assert "sda2" in recorded, f"‏mkswap על הצומת הלא נכון: {recorded!r}"


@requires_bash
def test_only_the_fs_field_decides_that_a_partition_is_swap(tmp_path):
    """הפונקציה עצמה, שורה-שורה: ‏`fs` מכריע, ‏`file` אינו מכריע דבר."""
    _box, _run, prelude = build_box(tmp_path)
    out = sh(prelude + 'for c in "swap|null" "swap|" "swap|p2.swap.zst" '
             '"ntfs|" "ntfs|null" "vfat|"; do f=${c%%|*}; n=${c#*|}; '
             'if is_swap_partition "$f" "$n"; then echo "$f/$n=swap"; '
             'else echo "$f/$n=stream"; fi; done')

    assert out.split() == ["swap/null=swap", "swap/=swap", "swap/p2.swap.zst=swap",
                           "ntfs/=stream", "ntfs/null=stream", "vfat/=stream"]


@requires_bash
def test_no_drawer_is_touched_when_the_plan_is_refused(tmp_path):
    """אותו שער בחדר השיכפולים, ושם המחיר מוכפל במספר המגירות.

    ‏`restore_partition_drawers` מריצה `make_swap` על **כל** מגירה חיה
    בלולאה אחת, ולכן מניפסט פגום אחד היה מפרמט שלוש מחיצות ווינדוס
    בבת אחת. השער יושב לפני לולאת ה-`apply_gpt`, וכל מגירה מקבלת את
    הסיבה בשמה.
    """
    from test_drawer_progress import drawer_box

    run, prelude = drawer_box(tmp_path)
    # השער חי בתוך `apply_gpt`, לפני ה---zap-all. כאן הוא מועמד לירות:
    # מה שנבדק הוא שהסיבה מגיעה למגירה, ולא נבלעת ב"לא הצלחנו לכתוב טבלה".
    prelude += ('apply_gpt() { PLAN_ERROR="למחיצה 3 (ntfs) אין קובץ והיא אינה swap'
                ' — המניפסט פגום, הדיסק לא שונה"; return 1; }; ')

    out = sh(prelude + "run_restore_drawers multicast http://s img m.json "
             'sda sdb sdc; echo "rc=$?"')

    assert out.strip().endswith("rc=1"), out
    assert (run / "state").read_text().strip() == "failed"
    for dev in ("sda", "sdb", "sdc"):
        error = (run / "targets" / dev / "error").read_text(encoding="utf-8")
        assert "3 (ntfs)" in error, f"{dev} אינו יודע איזו מחיצה: {error!r}"


# --- הצד של הקליטה: מה שנכנס לספרייה ----------------------------------------


def library_ids(server) -> set[str]:
    return {image["id"] for image in server["admin"].get("/api/console/images").json()}


def test_a_windows_partition_with_an_empty_file_never_enters_the_library(
        server, images_root):
    """הצורה מהאיסיו — `{"fs":"ntfs","file":""}`.

    היא חמקה משלוש שכבות: אין קובץ ולכן אין sha256 לאמת, מחיצה אחרת
    נשאה נתונים ולכן "יש מה לשדר", והשחזור קרא לה swap. אף אחת מהן לא
    שאלה למה מחיצת NTFS היא בלי קובץ.
    """
    from server.library.images import ImageLibrary

    broken = copy.deepcopy(MANIFEST_256)
    broken["id"] = "img_bad001"
    broken["partitions"][1]["file"] = ""
    write_image(images_root, broken)

    assert "img_bad001" not in library_ids(server)
    problem = ImageLibrary._validate(broken)
    assert problem and "3" in problem and "ntfs" in problem, \
        f"הסירוב אינו נוקב במחיצה ובסיבה: {problem!r}"


def test_a_partition_labelled_swap_but_holding_ntfs_is_refused(server, images_root):
    """אותו כשל בצורת `null`, ובמקום שבו שני הצדדים לא הסכימו.

    הקליטה פטרה מקובץ לפי `role`, והסוכן מחליט לפי `fs` — ולכן רשומה
    עם `role: "swap"` ו-`fs: "ntfs"` עברה את הקליטה בשלמותה והגיעה
    ל-`mkswap`. ‏`fs` הוא המכריע בשני הצדדים, ורשומה כזו נפסלת.
    """
    from server.library.images import ImageLibrary

    broken = copy.deepcopy(MANIFEST_256)
    broken["id"] = "img_bad002"
    broken["partitions"][1].update({"role": "swap", "file": None, "sha256": None})
    write_image(images_root, broken)

    assert "img_bad002" not in library_ids(server)
    assert ImageLibrary._validate(broken), "מחיצת NTFS בלי קובץ התקבלה כ-swap"


def test_a_real_swap_image_still_enters_the_library(server, images_root):
    """בקרה חיובית לצד הקליטה: אימג' Linux עם swap אמיתי נשאר תקין."""
    from server.library.images import ImageLibrary

    write_image(images_root, MANIFEST_LINUX)
    assert ImageLibrary._validate(MANIFEST_LINUX) is None
    assert MANIFEST_LINUX["id"] in library_ids(server)


def test_capture_refuses_a_manifest_whose_ntfs_partition_carries_no_file(server):
    """אותו שער במסלול שמייצר את האימג' — מחשב הבנייה מול השרת.

    כאן ההודעה מגיעה למפעיל כ-400 עם `detail`, וזו כל הנקודה של עיקרון
    6: האימג' הפגום אינו נכנס לספרייה מלכתחילה.
    """
    from test_capture import PART_A, do_capture, make_task, manifest_for, \
        setup_build_machine

    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    bad = manifest_for()
    bad["partitions"][1]["file"] = ""

    response = do_capture(server, created["id"], manifest=bad,
                          files={"p1.esp.pcl.zst": PART_A})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "3" in detail and "swap" in detail, \
        f"הסירוב אינו אומר איזו מחיצה ולמה: {detail!r}"


def test_capture_refuses_a_partition_that_calls_itself_swap_without_being_one(server):
    """‏`role: "swap"` היה פטור מלא מבדיקת הקובץ — גם כשה-`fs` אמר ntfs.

    זו הדלת שדרכה מניפסט כזה נכנס לספרייה **מאומת**, ומשם למחשב בכיתה.
    """
    from test_capture import PART_A, do_capture, make_task, manifest_for, \
        setup_build_machine

    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    bad = manifest_for()
    bad["partitions"][1].update({"role": "swap", "file": None, "sha256": None})

    response = do_capture(server, created["id"], manifest=bad,
                          files={"p1.esp.pcl.zst": PART_A})
    assert response.status_code == 400, \
        "מחיצת NTFS שהצהירה על עצמה כ-swap נקלטה"
