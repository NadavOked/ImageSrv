"""יומן הכתיבה ברמת חתיכה (#2533, #2528 שלב 2) — אילו חתיכות כבר על הדיסק.

**בשרת בלבד** (הכרעת נדב 12/11): על דיסק היעד נשאר רק הסימן "כתיבה לא הושלמה"
של #1798. דיסק שנשלף באמצע הוא המקום הכי פחות אמין לשמור עליו מה נכתב אליו.

שורה לכל (דיסק לפי המספר הסידורי והגודל, מחיצה): טווחים **סגורים** של מספרי חתיכות
(`image_format` 2 — חתיכה k היא 4MiB של זרם הנתונים, `interfaces.md` סעיף 7)
שהסוכן דיווח כ**עמידות**: נכתבו למקומן לפני שהתחיל fsync של הדיסק, וה-fsync
הזה חזר בהצלחה (`ledger_sync`, `agent/lib/core/datareport.sh`). הדיווח מצטבר
(השדה `durable` בדיווח ההתקדמות, סעיף 4), ולכן דיווח שאבד אינו משבש דבר.

שלושה כללים שמהם נגזר כל השאר:

* **איחוד בלבד.** דיווח מתאחד עם מה שרשום. דיווח ישן שהגיע באיחור אינו מוחק.
  היומן יכול רק **לפגר** אחרי הדיסק, לעולם לא להקדים אותו — ויומן מפגר בטוח:
  יותר חתיכות ייכתבו שוב.
* **כתיבה אחרת = יומן חדש.** השורה שייכת לסבב (`round_id`) ולתוכן (`image_id`
  ו-`index_xxh64` של המחיצה). דיווח מסבב אחר על אותו דיסק מחליף את מה שהיה:
  הדיסק נכתב עכשיו מחדש, ומה שהיה עליו אינו ראיה. המשך של אותה כתיבה בסבב חדש
  (שלב 4) הוא החלטה מפורשת (`adopt`), לא ברירת מחדל.
* **דיווח שאינו עקבי נדחה כולו**, בשמו — לא "מתוקן". מחיצה שאינה במניפסט,
  טווח מחוץ ל-`[0, chunks)`, טווחים שאינם ממוינים או חופפים, דיסק שהמספר
  הסידורי שלו אינו ידוע לשרת (בלי serial אין יומן), או מחיצה שהאינדקס שלה
  עוד לא אומת (אין `readback_chunks` — אין מול מה לבדוק).
* **זהות של דיסק = המספר הסידורי והגודל**, כפי שה-hello מדווח אותם. שני דיסקים
  שעונים לאותה זהות — באותה מכונה, או בשתי מכונות באותו סבב — הם **תאומים**,
  ולאף אחד מהם אין יומן (התכנון 2.3: "תאומים = אין המשך").
* **כל כתיבה לדיסק, בכל מנוע, מאפסת יומן ישן שלו.** גם סבב שאינו מדווח `durable`
  כלל: דיווח התקדמות שהתקבל על יעד הוא ראיה שהוא נכתב עכשיו.

מה ש**אינו** כאן, במכוון: מחיקה לפי זמן (`lost_wait_seconds`, הכרעות 6–7) ו-
"המשך מאותה נקודה" — אלה שלב 4. כאן `forget` ו-`adopt` שהוא יקרא להם.
"""

from __future__ import annotations

import json
import logging
import time

from server.db import _write_lock, now_iso, SqlConnection, writing

log = logging.getLogger("imagesrv.chunkledger")

#: מה ש-`note_report` מחזיר כשהכתיבה עצמה נכשלה (חריגה: DB נעול, דיסק מלא) — להבדיל
#: מסירוב בשמו (`LedgerProblem`). הדיווחים הם דלתא ממוספרת (שלב 3), והשרת מקדם את
#: המספר רק על דיווח שנרשם: כאן אותו דיווח, אם יישלח שוב, עשוי להירשם.
NOT_WRITTEN = "the ledger could not be written"
#: מה ש-`note_report` מוסיף לרשימה (פעם אחת, אחרי הסירובים בשמם) כשלפחות אחד מהם הוא
#: `NotYet`: סירוב שתלוי במה שהשרת יודע **עכשיו** ולא בדיווח. על `NOT_WRITTEN` ועל
#: `RETRY_LATER` המספר אינו מתקדם והסוכן שולח שוב (הכרעת המתאם, 13/11); על כל סירוב
#: אחר הוא מתקדם, כי שליחה חוזרת לא תשנה דבר.
RETRY_LATER = "the ledger cannot take this report yet"


class LedgerProblem(Exception):
    """הדיווח אינו עקבי — הסיבה בהודעה. דבר לא נרשם."""


class NotYet(LedgerProblem):
    """הדיווח עצמו תקין, והשרת עוד אינו יכול לרשום אותו: האינדקס של המחיצה עוד לא
    אומת, האימג' של הסבב אינו ידוע, או שה-hello האחרון לא נקב בדיסק. אותו דיווח,
    מאוחר יותר, עשוי להירשם."""


def well_formed(value: object, chunks: int) -> list[tuple[int, int]]:
    """`ranges` מהדיווח → [(ראשונה, אחרונה)] — או `LedgerProblem`. ממוינים, בלי
    חפיפה ובלי טווחים צמודים (שניים צמודים הם טווח אחד: הסוכן מאחד), בתוך
    `[0, chunks)`.

    התקרה על מספר הטווחים היא המבנה עצמו ולא קבוע: טווחים עם רווח ביניהם הם לכל
    היותר חתיכה-כן-חתיכה-לא, כלומר `(chunks + 1) // 2`. מפת חורים מפוזרת של אימג'
    גדול היא אלפי טווחים (20,000 חתיכות שכל רביעית בהן חסרה — 5,000), ותקרה קבועה
    דחתה אותה שלמה — ובלעדיה אין לשרת מה להשלים לדיסק."""
    if not isinstance(value, list):
        raise LedgerProblem("ranges is not a list")
    if len(value) > (chunks + 1) // 2:
        raise LedgerProblem("%d ranges are more than the %d ranges that fit, with gaps, in the partition's"
                            " %d chunks" % (len(value), (chunks + 1) // 2, chunks))
    out: list[tuple[int, int]] = []
    for item in value:
        if (not isinstance(item, list) or len(item) != 2
                or type(item[0]) is not int or type(item[1]) is not int):
            raise LedgerProblem(f"a range is not [first, last]: {item!r}"[:120])
        first, last = item
        if first < 0 or last < first or last >= chunks:
            raise LedgerProblem(f"range [{first}, {last}] is outside the partition's {chunks} chunks")
        if out and first <= out[-1][1] + 1:
            raise LedgerProblem(f"range [{first}, {last}] is not after [{out[-1][0]}, {out[-1][1]}] with a gap")
        out.append((first, last))
    return out


def union(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """האיחוד של שתי רשימות טווחים, מנורמל: ממוין, וצמודים וחופפים מאוחדים."""
    out: list[tuple[int, int]] = []
    for first, last in sorted([*a, *b]):
        if out and first <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], last))
        else:
            out.append((first, last))
    return out


def missing(ranges: list[tuple[int, int]], chunks: int) -> list[tuple[int, int]]:
    """מה שחסר: המשלים של `ranges` בתוך `[0, chunks)` — "מה חסר לדיסק הזה" של #2533."""
    out, at = [], 0
    for first, last in ranges:
        if first > at:
            out.append((at, first - 1))
        at = last + 1
    if at < chunks:
        out.append((at, chunks - 1))
    return out


def _part_facts(manifest: dict, part: object) -> tuple[str, int]:
    """(`index_xxh64`, מספר החתיכות) של מחיצה במניפסט — או `LedgerProblem`."""
    found = next((p for p in manifest.get("partitions") or []
                  if isinstance(p, dict) and p.get("index") == part), None)
    if found is None or type(part) is not int:
        raise LedgerProblem(f"partition {part!r} is not in the image's manifest")
    xxh, chunks = found.get("index_xxh64"), found.get("readback_chunks")
    if not isinstance(xxh, str) or type(chunks) is not int or chunks < 0:
        raise NotYet(f"partition {part}: the image has no verified chunk index yet"
                     " -- nothing to check the ranges against")
    return xxh, chunks


TWIN = -1          # `part` של שורת הסימון "שני דיסקים עונים לזהות הזו בסבב הזה": אין יומן לאף אחד מהם


def record(conn: SqlConnection, *, round_id: str, mac: str, serial: str, size_bytes: int,
           image_id: str, manifest: dict, durable: object) -> None:
    """דיווח `durable` של יעד אחד → היומן. כולו או כלום: `LedgerProblem` = דבר לא נרשם.

    הזהות של דיסק היא **המספר הסידורי והגודל** (מה שה-hello מדווח; התכנון 2.3). שני
    דיסקים שעונים לאותה זהות באותו סבב — אותו serial משתי מכונות — הם תאומים: היומן
    של הזהות נמחק, נשארת שורת סימון (`TWIN`), ולאף אחד מהם אין ממה להמשיך."""
    if not isinstance(serial, str) or not serial or type(size_bytes) is not int or size_bytes <= 0:
        raise NotYet("the disk's serial and size are not known to the server -- no ledger without them")
    if not isinstance(durable, list) or len(durable) > 128:
        raise LedgerProblem("durable is not a list of partitions")
    rows = []
    for item in durable:
        if not isinstance(item, dict):
            raise LedgerProblem("a durable entry is not an object")
        xxh, chunks = _part_facts(manifest, item.get("part"))
        rows.append((item["part"], xxh, chunks, well_formed(item.get("ranges"), chunks)))
    if len({row[0] for row in rows}) != len(rows):
        raise LedgerProblem("a partition appears twice in durable")
    now, who, twin = now_iso(), (serial, size_bytes), None
    with _write_lock, writing(conn):
        # כתיבה אחרת על אותו דיסק: מה שהיה רשום אינו ראיה למה שנכתב עכשיו.
        conn.execute("DELETE FROM chunk_ledger WHERE serial = ? AND size_bytes = ?"
                     " AND (round_id != ? OR image_id != ?)", (*who, round_id, image_id))
        held = conn.execute("SELECT part, mac FROM chunk_ledger WHERE serial = ? AND size_bytes = ?",
                            who).fetchall()
        if any(row["part"] == TWIN for row in held):
            twin = "already"
        elif any(row["mac"] != mac for row in held):
            conn.execute("DELETE FROM chunk_ledger WHERE serial = ? AND size_bytes = ?", who)
            conn.execute(
                "INSERT INTO chunk_ledger (serial, size_bytes, part, round_id, mac, image_id, index_xxh64,"
                " chunks, ranges_json, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (*who, TWIN, round_id, mac, image_id, "", 0, "[]", now))
            twin = "found"
        else:
            for part, xxh, chunks, ranges in rows:
                before = conn.execute(
                    "SELECT index_xxh64, ranges_json FROM chunk_ledger"
                    " WHERE serial = ? AND size_bytes = ? AND part = ?", (*who, part)).fetchone()
                kept = ([tuple(r) for r in json.loads(before["ranges_json"])]
                        if before is not None and before["index_xxh64"] == xxh else [])
                conn.execute(
                    "INSERT OR REPLACE INTO chunk_ledger (serial, size_bytes, part, round_id, mac, image_id,"
                    " index_xxh64, chunks, ranges_json, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (*who, part, round_id, mac, image_id, xxh, chunks,
                     json.dumps([list(r) for r in union(kept, ranges)]), now))
    if twin is not None:                # אחרי ה-commit: שורת הסימון נשארת
        raise LedgerProblem("two disks answer to this serial and size in this round -- twins,"
                            " no ledger for either of them")


def of_disk(conn: SqlConnection, serial: str, size_bytes: int) -> dict[int, dict]:
    """היומן של דיסק: {מחיצה: {round_id, mac, image_id, index_xxh64, chunks, ranges,
    missing, updated_at}}. ריק = אין יומן (או תאומים), ולכן אין ממה להמשיך."""
    out = {}
    for row in conn.execute("SELECT * FROM chunk_ledger WHERE serial = ? AND size_bytes = ? ORDER BY part",
                            (serial, size_bytes)):
        if row["part"] == TWIN:
            return {}
        ranges = [tuple(r) for r in json.loads(row["ranges_json"])]
        out[row["part"]] = {"round_id": row["round_id"], "mac": row["mac"], "image_id": row["image_id"],
                            "index_xxh64": row["index_xxh64"], "chunks": row["chunks"],
                            "ranges": ranges, "missing": missing(ranges, row["chunks"]),
                            "updated_at": row["updated_at"]}
    return out


def forget(conn: SqlConnection, *, serial: str | None = None, size_bytes: int | None = None,
           round_id: str | None = None) -> int:
    """מוחק את היומן של דיסק (serial וגודל), או של סבב שלם ("התחל מחדש", סגירת הסבב,
    פקיעת ההמתנה). מחזיר כמה שורות נמחקו. בלי דיסק ובלי סבב — כלום (לא "הכול")."""
    args: tuple[str | int, ...]
    if round_id is not None and serial is None:
        clause, args = "round_id = ?", (round_id,)
    elif serial is not None and size_bytes is not None:
        clause, args = "serial = ? AND size_bytes = ?", (serial, size_bytes)
    else:
        return 0
    with _write_lock, writing(conn):
        return conn.execute(f"DELETE FROM chunk_ledger WHERE {clause}", args).rowcount


def forget_others(conn: SqlConnection, disks: list[tuple[str, int]], round_id: str) -> int:
    """כתיבה נפתחה על הדיסקים האלה בסבב `round_id`: יומן שלהם מסבב **אחר** נמחק. בכל
    מנוע — גם מי שאינו מדווח `durable`: דיסק שנכתב מחדש אינו ממשיך מיומן ישן.
    קריאה אחת, בלי נעילת כתיבה, כשאין מה למחוק (הדיווח מגיע כל 2 שניות)."""
    stale = [d for d in disks if conn.execute(
        "SELECT 1 FROM chunk_ledger WHERE serial = ? AND size_bytes = ? AND round_id != ? LIMIT 1",
        (*d, round_id)).fetchone() is not None]
    if not stale:
        return 0
    with _write_lock, writing(conn):
        return sum(conn.execute("DELETE FROM chunk_ledger WHERE serial = ? AND size_bytes = ? AND round_id != ?",
                                (*d, round_id)).rowcount for d in stale)


def adopt(conn: SqlConnection, serial: str, size_bytes: int, round_id: str, *,
          image_id: str, manifest: dict) -> int:
    """שלב 4: סבב חדש ממשיך את הכתיבה של הדיסק הזה — היומן עובר אליו כמו שהוא,
    במקום להימחק בדיווח הראשון שלו. מחזיר כמה שורות עברו.

    **השומר כאן, לא אצל הקורא (#3117):** `image_id` והמניפסט הם של הסבב החדש, והם חובה.
    שורה של אימג' אחר, או של מחיצה שהאינדקס שלה (`index_xxh64`) שונה, אינה המשך של
    אותה כתיבה — `LedgerProblem` בשמה, **ושום שורה לא עוברת** (הכול או כלום: סבב שיעביר
    חצי יומן ימשיך מדיסק שחצי ממנו של תוכן אחר). בלי השומר המנוע היה רואה חתיכות של האימג'
    הישן כעמידות לחדש ולא היה משלים אותן."""
    with _write_lock, writing(conn):
        for row in conn.execute("SELECT part, image_id, index_xxh64 FROM chunk_ledger WHERE serial = ?"
                                " AND size_bytes = ? AND part != ? ORDER BY part", (serial, size_bytes, TWIN)):
            if row["image_id"] != image_id:
                raise LedgerProblem(f"partition {row['part']}: the ledger is of image {row['image_id']},"
                                    f" the round writes {image_id} -- not a continuation of the same write")
            xxh, _chunks = _part_facts(manifest, row["part"])
            if row["index_xxh64"] != xxh:
                raise LedgerProblem(f"partition {row['part']}: the ledger's chunk index is not the round's"
                                    " -- not a continuation of the same write")
        return conn.execute("UPDATE chunk_ledger SET round_id = ? WHERE serial = ? AND size_bytes = ?"
                            " AND part != ? AND image_id = ?",
                            (round_id, serial, size_bytes, TWIN, image_id)).rowcount


_SEEN: dict[tuple[str, str], tuple[float, dict | None]] = {}
MANIFEST_TTL_S = 15.0


def _manifest(ctx, image_id: str, direct) -> dict | None:
    """המניפסט של אימג' הסבב. `library.get` סורק את כל הספרייה מהדיסק, והדיווח מגיע כל
    2 שניות מכל מכונה — לכן התשובה נשמרת ל-MANIFEST_TTL_S (מספר החתיכות של מחיצה אינו
    משתנה; אינדקס שאומת בינתיים נראה אחרי רבע דקה)."""
    key, now = (str(ctx.library.root), image_id), time.monotonic()
    kept = _SEEN.get(key)
    if kept is None or kept[0] < now:
        kept = (now + MANIFEST_TTL_S, ctx.library.get(image_id) or direct.live_manifest(ctx.conn, image_id))
        _SEEN[key] = kept
    return kept[1]


def _hello_disks(conn: SqlConnection, mac: str) -> list[dict]:
    """הדיסקים שה-hello האחרון של המכונה דיווח — כל רשומה שנוקבת בהתקן. `[]` = אין hello,
    אין בו דיסקים, או שאינו נקרא."""
    row = conn.execute("SELECT disks_json FROM net_devices WHERE mac = ?", (mac,)).fetchone()
    try:
        disks = json.loads(row["disks_json"]) if row is not None and row["disks_json"] else []
    except (TypeError, ValueError):
        # #3124 (A-DB-07): `[]` = "אין דיסקים" וגם "לא נקרא"; השני נאמר ביומן ולא נבלע
        log.warning("chunkledger: the disks_json of %s is unreadable; no disks are taken from its hello", mac)
        return []
    if not isinstance(disks, list):
        return []
    return [d for d in disks if isinstance(d, dict) and isinstance(d.get("dev"), str)]


def _named(disk: dict) -> bool:
    return (isinstance(disk.get("serial"), str) and bool(disk["serial"])
            and type(disk.get("size_bytes")) is int and disk["size_bytes"] > 0)


def disks_of(conn: SqlConnection, mac: str) -> dict[str, tuple[str, int]]:
    """{התקן: (serial, גודל)} מה-hello האחרון של המכונה — רק דיסקים שדיווחו את שניהם."""
    return {d["dev"]: (d["serial"], d["size_bytes"]) for d in _hello_disks(conn, mac) if _named(d)}


def nameless_of(conn: SqlConnection, mac: str) -> set[str]:
    """ההתקנים שה-hello האחרון **כן** דיווח, אבל בלי serial או בלי גודל. לדיסק כזה אין
    יומן ואין המשך (התכנון 2.3; סבב חדר אינו כותב אותו כלל, #2448), ושום דיווח מאוחר
    לא ישנה זאת — סירוב קבוע. להבדיל מהתקן שאינו ב-`disks_of` וגם אינו כאן: ה-hello לא
    נקב בו בכלל, ו-hello הבא עשוי לנקוב (`NotYet`)."""
    disks = _hello_disks(conn, mac)
    named = {d["dev"] for d in disks if _named(d)}
    return {d["dev"] for d in disks} - named


def note_report(ctx, mac: str, body: dict) -> list[str]:
    """מדיווח התקדמות של סבב שהתקבל → היומן. מחזיר את הסירובים (ריק = הכול נרשם, או
    שלא היה מה). **אינו זורק לעולם**: הדיווח עצמו כבר התקבל, ויומן שנדחה או שנכשל
    אינו מכשיל כתיבה — הוא רק אינו נותן ממה להמשיך, והסיבה ביומן השרת.
    `NOT_WRITTEN` ברשימה = הכתיבה נכשלה, וייתכן שחלק מהיעדים כן נרשמו (היומן הוא
    איחוד, ולכן שליחה חוזרת של הכול אינה מזיקה). `RETRY_LATER` ברשימה = לפחות אחד
    הסירובים הוא `NotYet`. כל פריט אחר הוא סירוב ליעד אחד, בשמו.

    שני דברים: (1) כל יעד שבדיווח — עם `durable` או בלעדיו — נכתב עכשיו בסבב הזה,
    ולכן יומן שלו מסבב אחר נמחק (`forget_others`); (2) יעד שנושא `durable` נרשם."""
    try:
        return _note_report(ctx, mac, body)
    except Exception:                               # noqa: BLE001 -- the report stands; never a 500 for a ledger
        log.exception("chunk ledger: the report from %s was not recorded", mac)
        return [NOT_WRITTEN]


def _note_report(ctx, mac: str, body: dict) -> list[str]:
    targets, session_id = body.get("targets"), body.get("session_id")
    if not isinstance(targets, list) or not isinstance(session_id, str):
        return []
    targets = [t for t in targets if isinstance(t, dict)]
    carrying = [t for t in targets if "durable" in t]
    if not carrying and ctx.conn.execute("SELECT 1 FROM chunk_ledger LIMIT 1").fetchone() is None:
        return []                                   # the usual report: nothing to record, nothing to forget
    disks, nameless = disks_of(ctx.conn, mac), nameless_of(ctx.conn, mac)
    forget_others(ctx.conn, [disks[t["dev"]] for t in targets
                             if isinstance(t.get("dev"), str) and t["dev"] in disks], session_id)
    if not carrying:
        return []
    from server.rounds import direct
    row = ctx.conn.execute("SELECT image_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
    manifest = _manifest(ctx, row["image_id"], direct) if row is not None else None
    twins = {who for who in disks.values() if list(disks.values()).count(who) > 1}
    problems, not_yet = [], False
    for target in carrying:
        dev = target.get("dev")
        try:
            if manifest is None:
                raise NotYet("the round's image is not known")
            if not isinstance(dev, str):
                raise LedgerProblem("the target names no device")
            if dev in nameless:
                raise LedgerProblem("the hello reported this disk with no serial or no size -- no ledger"
                                    " and nothing to continue from")
            serial, size_bytes = disks.get(dev, ("", 0))
            if (serial, size_bytes) in twins:
                raise LedgerProblem("two disks of this machine carry this serial and size -- twins,"
                                    " no ledger for either of them")
            record(ctx.conn, round_id=session_id, mac=mac, serial=serial, size_bytes=size_bytes,
                   image_id=row["image_id"], manifest=manifest, durable=target["durable"])
        except LedgerProblem as exc:
            problems.append(f"{dev!r}: {exc}"[:300])
            not_yet = not_yet or isinstance(exc, NotYet)
    for problem in problems:
        log.warning("chunk ledger: the durable chunks from %s were refused -- %s", mac, problem)
    return problems + [RETRY_LATER] if not_yet else problems
