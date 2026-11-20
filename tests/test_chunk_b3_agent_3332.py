"""#3332: הסוכן מוריד את רשימת ה-BLAKE3 של המחיצה ומעביר אותה לכל chunkcast שכותב (`agent/lib/restore/chunkb3.sh`,
`chunk_partition_drawers` ב-`chunkrecv.sh`).

רצה הלולאה האמיתית של `chunkrecv_2533` (אותו harness, אותו chunkcast מזויף שרושם את ה-argv שלו), עם `http_get` שמגיש
את האינדקס ואת הרשימה לפי ה-URL. הרשימה **חובה** (נדב 15/11): המניפסט נוקב ב-`chunk_b3` והרשימה הגיעה --
`--b3-file`/`--b3-file-hash` בכל קריאה ובקובץ ה-`.args` (מגירה שחוזרת משלימה איתם); המניפסט אינו נוקב, נוקב במשהו
שאינו BLAKE3, או שהרשימה לא הגיעה שלמה -- המגירות נכשלות בשמן ואין מקלט, לעולם לא XXH64 לבדו במקומה (עיקרון 5).

**בקרה שלילית:** `agent/lib/restore/chunkrecv.sh` ו-`agent/imagesrv-agent` חוזרים לבסיס, ו-`chunkb3.sh` נמחק. הרשימה
אינה מורדת ואינה מועברת, ומגירה אינה נכשלת על רשימה חסרה -- כל טסט כאן נופל ב-AssertionError.
"""

from __future__ import annotations

import test_chunkrecv_2533 as R
from test_agent import posix

pytestmark = R.pytestmark


def _fails_by_name(got: dict, why: str) -> None:
    assert not any(t.startswith("chunkcast recv") for t in got["trace"]), got["trace"]
    for disk in ("sda", "sdb"):
        assert any(t.startswith(f"FAIL {disk} ") and why in t for t in got["trace"]), got["trace"]
    assert got["trace"][-1] == "rc=1", got["trace"]


def test_the_list_the_manifest_names_is_fetched_and_every_writer_gets_it(tmp_path):
    got = R._run(tmp_path, [R.RECEIVE1, R.DONE])
    recv = [t for t in got["trace"] if t.startswith("chunkcast recv")]
    want = f"--b3-file {posix(tmp_path)}/chunk/p3.b3 --b3-file-hash {R.B3_HASH}"
    assert recv and all(want in t for t in recv), got["trace"]
    assert want in (tmp_path / "chunk" / "p3.args").read_text()      # a drawer back in completes with it (drawerback.sh)
    assert "GET http://srv/api/v1/images/img_1/b3/3" in got["trace"]
    assert (tmp_path / "chunk" / "p3.b3").read_bytes() == R.B3_LIST
    assert got["trace"][-1] == "rc=0", got["trace"]


def test_a_manifest_that_names_no_list_fails_the_drawers_by_name(tmp_path):
    got = R._run(tmp_path, [R.RECEIVE1, R.DONE], part={"index": 3, "index_xxh64": "abc"})
    _fails_by_name(got, "למניפסט אין רשימת BLAKE3 למחיצה (chunk_b3)")
    assert not any("/b3/" in t for t in got["trace"]), got["trace"]


def test_a_list_that_did_not_arrive_fails_the_drawers_by_name(tmp_path):
    _fails_by_name(R._run(tmp_path, [R.RECEIVE1, R.DONE], env="B3_FAILS=1"), "רשימת ה-BLAKE3 של המחיצה לא הורדה מהשרת")


def test_a_list_of_another_length_than_the_index_fails_the_drawers(tmp_path):
    _fails_by_name(R._run(tmp_path, [R.RECEIVE1, R.DONE], b3=R.B3_LIST[:-32]),
                   "אינה באורך האינדקס (128 בייט, 5 חתיכות)")


def test_a_named_list_that_is_not_a_blake3_fails_before_anything_is_fetched(tmp_path):
    got = R._run(tmp_path, [R.RECEIVE1, R.DONE], part={"index": 3, "index_xxh64": "abc", "chunk_b3": "not-hex"})
    _fails_by_name(got, "אינה 64 ספרות hex")
    assert not any("/b3/" in t for t in got["trace"]), got["trace"]
