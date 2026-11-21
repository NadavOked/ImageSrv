"""#3181: השלמה ממגירה שכנה באותו משכפל -- `neighborfill_run <drawer> <missing-list-file>`.

מגירה שחזרה (או דיסק חדש באמצע סבב) חסרות לה חתיכות של המחיצה. השכנות שלה באותו משכפל מחזיקות אותן,
וה-ledger העמיד של כל אחת (targets/<n>/ledger/durable.<part>) אומר אילו. כאן רצים `neighborfill.sh`
האמיתי, `chunkcast serve` ו-`chunkcast apply` האמיתיים ו-curl האמיתי, מול "דיסקים" שהם קבצים
(CHUNKCAST_ALLOW_FILE), על אימג' שנחתך ב-chunkcut האמיתי (כמו ב-test_chunkcast_stdin_3161.py).
מזויף רק `blockdev` -- אין ריקון מטמון לקובץ.

מה שנבדק הוא מה שהמגירה מקבלת ומה שנשאר לשרת: הבייטים על "הדיסק", קובץ החסרים אחרי הריצה, וקוד היציאה.
0 רק כשכל החתיכות נכתבו ונקראו בחזרה שלמות; בכל מקרה אחר הרשימה נכתבת מחדש עם מה שנשאר.

**בקרה שלילית (התנהגותית):** הקובץ נשאר ונטען, ו-`neighborfill_run` מוחלף בהתנהגות של לפני #3181:
לא נקרא כלום מהשכנות, הרשימה נשארת שלמה ל-HTTP, rc=1. כל הטסטים כאן נופלים ב-AssertionError.
בקרה שנייה: הלב נשאר, והשומרים הוסרו (סינון מקור/אדום/מצב, התקרה, הקריאה החוזרת). נופלים הטסטים
של הסינון, התקרה והקריאה החוזרת. האימות ב-XXH64 הוא של `chunkcast` (C), ולא של הקובץ הזה.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

import chunkcast_rig as C
from agentlib import agent_sources
from native import requires_native
from test_agent import BASH, posix
from test_chunkcast_stdin_3161 import cut_disk

pytestmark = requires_native("gcc", "zstd", "curl", ("bash", BASH), posix=True,
                             why="chunkcast needs gcc and libzstd; the frames go over curl")

FAKE_BLOCKDEV = """#!/bin/sh
echo "blockdev $*" >> "$RUN_DIR/trace"
[ -z "${BLOCKDEV_FAIL:-}" ]
"""


@pytest.fixture(scope="module")
def cc(tmp_path_factory):
    return C.build_binaries(tmp_path_factory.mktemp("nf3181-bin"))


@pytest.fixture(scope="module")
def chunkcut(tmp_path_factory):
    binary = tmp_path_factory.mktemp("nf3181-cut") / "chunkcut"
    built = subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-pthread", "-o", str(binary),
                            str(C.REPO / "agent/chunkcut.c"), "-l:libzstd.so.1"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert built.returncode == 0, built.stderr
    return binary


@pytest.fixture(scope="module", params=["partclone", "raw"])
def cut(request, cc, chunkcut, tmp_path_factory):
    return cut_disk(tmp_path_factory.mktemp(f"nf-{request.param}"), cc, chunkcut, request.param)


@pytest.fixture(scope="module")
def pcl(cc, chunkcut, tmp_path_factory):
    return cut_disk(tmp_path_factory.mktemp("nf-pcl"), cc, chunkcut, "partclone")


def damaged(img: C.Image, disk: Path, k: int) -> bytes:
    """The neighbour's disk with one byte of chunk k flipped, at its own place."""
    raw = bytearray(disk.read_bytes())
    where = k * C.CH + 100 if img.kind == "raw" else img.used[(k * C.CH) // img.bs] * img.bs + 100
    raw[where] ^= 0xFF
    return bytes(raw)


def fill(tmp: Path, cc: Path, cut, neighbours: dict, *, missing: str = "0-4", env: dict | None = None,
         files: dict | None = None) -> dict:
    """A cloner: the drawer sda, its neighbours (name -> state, ledger, and its disk's bytes), and one call."""
    img, disk = cut
    run, dev, bin_ = tmp / "run", tmp / "dev", tmp / "bin"
    for d in (run / "chunk", dev, bin_):
        d.mkdir(parents=True, exist_ok=True)
    idx = run / "chunk" / "p3.idx"                     # the callers' name for it: the partition comes from it
    idx.write_bytes(img.idx.read_bytes())
    target = img.blank(dev / "sda3")
    (run / "targets" / "sda").mkdir(parents=True)
    (run / "targets" / "sda" / "state").write_text("writing\n")
    for name, spec in neighbours.items():
        t = run / "targets" / name
        (t / "ledger").mkdir(parents=True)
        (t / "state").write_text(spec.get("state", "writing") + "\n")
        if spec.get("ledger") is not None:
            (t / "ledger" / "durable.3").write_text("".join(f"{r}\n" for r in spec["ledger"]))
        (dev / f"{name}3").write_bytes(spec.get("bytes") or disk.read_bytes())
    for rel, text in (files or {}).items():
        (run / rel).parent.mkdir(parents=True, exist_ok=True)
        (run / rel).write_text(text)
    (bin_ / "blockdev").write_text(FAKE_BLOCKDEV, newline="\n")
    (bin_ / "blockdev").chmod(0o755)
    lst = tmp / "missing"
    lst.write_text(missing + "\n")
    args = f"--index {posix(idx)} --index-xxh64 {img.index_xxh} " + \
        ("--raw" if img.kind == "raw" else f"--head {posix(img.head_z)}")
    script = (f"export RUN_DIR={posix(run)!r} DEVROOT={posix(dev)!r} CHUNK_DIR={posix(run / 'chunk')!r} "
              f"CHUNK_IDX={posix(idx)!r} CHUNK_IMAGE_ARGS={args!r} NF_LEVEL=3 NF_PORT={C.free_port()} "
              f"CHUNKCAST_ALLOW_FILE=1 PATH={posix(bin_)!r}:{posix(cc)!r}:\"$PATH\"; "
              + agent_sources("common", "waits", "restore", "smart", "neighborfill")
              + f"neighborfill_run sda {posix(lst)!r}; echo \"rc=$?\"")
    began = time.monotonic()
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True, encoding="utf-8", timeout=180,
                          stdin=subprocess.DEVNULL, env={**os.environ, **(env or {})})
    took = time.monotonic() - began
    log = (run / "agent.log").read_text(encoding="utf-8") if (run / "agent.log").exists() else ""
    nf = run / "chunk" / "nf.sda"
    read = {n: C.state_of(nf / f"{n}.state").get(posix(target), {}).get("durable", set())
            for n in neighbours if (nf / f"{n}.state").exists()}
    trace = (run / "trace").read_text() if (run / "trace").exists() else ""
    return {"rc": proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "", "out": proc.stdout + proc.stderr,
            "log": log, "left": lst.read_text().split(), "target": target.read_bytes(), "read": read,
            "took": took, "trace": trace, "node": posix(target)}


def test_the_lack_is_shared_between_two_neighbours_written_and_read_back(tmp_path, cc, cut):
    """הליבה: שתי שכנות מחזיקות הכול. החסר מתחלק ביניהן (כל אחת לוקחת את החתיכה הבאה כשיש לה פחות),
    כל חתיכה נכתבת למקומה במגירה, נקראת בחזרה אחרי ריקון המטמון, והרשימה מתרוקנת. rc=0."""
    img, disk = cut
    got = fill(tmp_path, cc, cut, {"sdb": {"ledger": ["0-4"]}, "sdc": {"ledger": ["0-4"]}},
               env={"NF_BATCH": "1", "NF_CAP_MIBS": "500"})
    assert got["rc"] == "rc=0", got["out"] + got["log"]
    assert got["left"] == [], got["left"]
    assert got["target"] == img.expected()                                 # every chunk, at its own place
    assert got["read"] == {"sdb": {0, 2, 4}, "sdc": {1, 3}}, got["read"]   # shared: neither read it all
    assert f"blockdev --flushbufs {got['node']}" in got["trace"]          # the read-back went past the cache
    assert "2 neighbours read, 500MiB/s together at most" in got["log"]   # 500 asked: a word, and a word is not clipped (A27, #3659: 100 is only "no word")
    assert "5 of 5 chunks written from the neighbours and re-read whole; 0 left for HTTP" in got["log"], got["log"]


def test_a_neighbour_is_read_only_for_what_its_ledger_holds(tmp_path, cc, pcl):
    """הדיסקים של שתי השכנות מחזיקים הכול, אבל ה-ledger של sdb אומר 0-1 ושל sdc 0-2. נקרא רק מה שה-ledger
    מעיד עליו. חתיכות 3-4 אין לאף אחת: הן נשארות ברשימה לשרת, rc אינו 0."""
    img, _ = pcl
    got = fill(tmp_path, cc, pcl, {"sdb": {"ledger": ["0-1"]}, "sdc": {"ledger": ["0-2"]}})
    assert got["rc"] == "rc=1", got["out"] + got["log"]
    assert got["left"] == ["3-4"]
    assert got["read"]["sdb"] <= {0, 1} and got["read"]["sdc"] <= {0, 1, 2}, got["read"]
    assert got["read"]["sdb"] | got["read"]["sdc"] == {0, 1, 2}
    assert got["target"] == img.expected({0, 1, 2})                       # 3-4 untouched: still the garbage


def test_a_damaged_chunk_on_a_neighbour_is_caught_by_xxh64_and_never_written(tmp_path, cc, cut):
    """ה-ledger של sdb אומר 0-4, אבל חתיכה 2 על הדיסק שלה שונה (בייט אחד). היא נבדקת מול ה-XXH64 של האינדקס
    לפני הכתיבה ונדחית: על המגירה נשאר במקומה מה שהיה, והיא נשארת ברשימה. 3-4 אחריה בכל זאת נכתבות."""
    img, disk = cut
    got = fill(tmp_path, cc, cut, {"sdb": {"ledger": ["0-4"], "bytes": damaged(img, disk, 2)}},
               env={"NF_BATCH": "1"})
    assert got["rc"] == "rc=1", got["out"] + got["log"]
    assert got["left"] == ["2-2"], got["left"]
    assert got["target"] == img.expected({0, 1, 3, 4})                    # not a byte of chunk 2
    assert "the disk changed" in got["log"], got["log"]                   # chunkcast's word, in the agent's log
    assert "the piece from chunk 2 was not read whole from sdb" in got["log"]


def test_the_reads_keep_the_ceiling_together(tmp_path, cc, pcl):
    """התקרה היא לכל השכנות יחד, והמוח יכול להוריד אותה בקובץ. 16MiB/s בקובץ, שתי שכנות, 5 חתיכות
    (20MiB לפי 4MiB לחתיכה): הקריאה מהשכנות לבדה לוקחת לפחות 1.25 שניות, והקריאה החוזרת מהמגירה עוד אותו
    זמן. בלי התקרה זה עשיריות שנייה."""
    got = fill(tmp_path / "capped", cc, pcl, {"sdb": {"ledger": ["0-4"]}, "sdc": {"ledger": ["0-4"]}},
               env={"NF_BATCH": "1"}, files={"neighborfill.cap": "16\n"})
    assert got["rc"] == "rc=0", got["out"] + got["log"]
    assert "2 neighbours read, 16MiB/s together at most" in got["log"]
    assert got["took"] >= 2.3, got["took"]
    # a word of the brain that is there and is not a number is not "no limit": nothing is read at all
    bad = fill(tmp_path / "bad", cc, pcl, {"sdb": {"ledger": ["0-4"]}}, files={"neighborfill.cap": "fast\n"})
    assert bad["rc"] == "rc=1" and bad["left"] == ["0-4"], bad["out"] + bad["log"]
    assert bad["target"] == pcl[0].expected(set()) and bad["read"] == {}
    assert "is not a number -- nothing is read" in bad["log"]


def test_a_reader_that_never_finishes_is_stopped_at_its_ceiling_and_its_chunks_stay_missing(tmp_path, cc, pcl):
    """#3281, עיקרון 5: `chunkcast apply` של השכנה לא מסתיים (כאן: `sleep` שלא קורא את הזרם). לכל קורא
    יש תקרה (wait_pid): כשהיא נחצית הוא נעצר, וגם שרת המסגרות שלו, ומה שלא נכתב ונקרא בחזרה נשאר ברשימה
    ל-HTTP, rc=1 -- לא "נגמר". התקרה כאן היא 5*4*3/100 + 120 = 120 שניות (כמו ב-#3303); ה-sleep הוא 150, ובלי
    התקרה ה-wait תקוע עד שהוא נגמר."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "chunkcast").write_text(
        "#!/bin/sh\n"
        '[ "$1" = apply ] && exec sleep 150 < /dev/null > /dev/null 2>&1   # a reader that never ends\n'
        f'exec {posix(cc / "chunkcast")!r} "$@"\n', newline="\n")
    (bin_ / "chunkcast").chmod(0o755)
    got = fill(tmp_path, cc, pcl, {"sdb": {"ledger": ["0-4"]}}, env={"WAIT_STREAM_STALL_S": "2"})
    assert 115 < got["took"] < 140, (got["took"], got["log"])            # stopped at the 120s ceiling, not at the sleep's end
    assert got["rc"] == "rc=1" and got["left"] == ["0-4"], got["out"] + got["log"]
    assert "פג הזמן: the neighbour fill of sda" in got["log"], got["log"]
    assert "sdb.serve.pid: the frame server of a reader stopped at the ceiling is stopped" in got["log"], got["log"]
    pid = (tmp_path / "run" / "chunk" / "nf.sda" / "sdb.serve.pid").read_text().strip()
    deadline = time.monotonic() + 5
    while Path(f"/proc/{pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert pid and not Path(f"/proc/{pid}").exists(), f"the frame server of sdb ({pid}) is still running"


def test_the_source_disk_a_red_one_and_a_failed_one_are_never_read(tmp_path, cc, pcl):
    """שלוש שכנות שה-ledger והדיסק שלהן שלמים, ואסור לקרוא מהן: דיסק המקור ($LC/source), דיסק אדום ב-SMART,
    ומגירה שנכשלה. הרביעית (sde) כותבת כרגיל -- והכול מגיע ממנה. בלעדיה לא נקרא כלום, והרשימה נשארת שלמה."""
    img, _ = pcl
    full = {"ledger": ["0-4"]}
    banned = {"sdb": full, "sdc": full, "sdd": {**full, "state": "failed"}}
    marks = {"lc/source": "sdb\n", "smart/sdc.v": "fail realloc 9 0 0 0\n"}
    got = fill(tmp_path / "with", cc, pcl, {**banned, "sde": full}, files=marks)
    assert got["rc"] == "rc=0", got["out"] + got["log"]
    assert got["read"] == {"sde": {0, 1, 2, 3, 4}}, got["read"]
    for why in ("sdb is not read for sda -- it is the source disk", "sdc is not read for sda -- it is red (SMART)",
                "sdd is not read for sda -- its state is 'failed'"):
        assert why in got["log"], got["log"]
    alone = fill(tmp_path / "without", cc, pcl, banned, files=marks)
    assert alone["rc"] == "rc=1" and alone["left"] == ["0-4"], alone["out"] + alone["log"]
    assert alone["read"] == {} and alone["target"] == img.expected(set())


def test_what_was_not_read_back_from_the_disk_is_not_written(tmp_path, cc, pcl):
    """עיקרון 5: המטמון של המגירה לא רוקן, ולכן אין קריאה חוזרת מהדיסק עצמו. החתיכות נכתבו, אבל "לא הצלחנו
    לבדוק" אינו "נכתב": כולן נשארות ברשימה, rc אינו 0."""
    got = fill(tmp_path, cc, pcl, {"sdb": {"ledger": ["0-4"]}}, env={"BLOCKDEV_FAIL": "1"})
    assert got["rc"] == "rc=1", got["out"] + got["log"]
    assert got["left"] == ["0-4"]
    assert got["read"] == {"sdb": {0, 1, 2, 3, 4}}                        # it was written: the evidence is what is missing
    assert "its cache was not dropped" in got["log"] and "0 of 5 chunks" in got["log"], got["log"]


def test_only_the_neighbours_the_brain_named_are_read(tmp_path, cc, pcl):
    """#3181 (כותב E): המוח בחר מאיזו שכנה להשלים (`NF_ONLY`, מ-`fillword.sh`). sdb מחזיקה הכול ו-sdc רק 0-2, והמוח
    נקב ב-sdc: רק היא נקראת, 3-4 נשארות לשרת, ונאמר למה sdb לא נקראה. `NF_ONLY` ריק = כל השכנות, כמו קודם."""
    img, _ = pcl
    both = {"sdb": {"ledger": ["0-4"]}, "sdc": {"ledger": ["0-2"]}}
    got = fill(tmp_path / "named", cc, pcl, both, env={"NF_ONLY": "sdc"})
    assert got["rc"] == "rc=1", got["out"] + got["log"]
    assert got["left"] == ["3-4"], got["left"]                             # sdb holds them, and was not asked
    assert got["read"] == {"sdc": {0, 1, 2}}, got["read"]
    assert got["target"] == img.expected({0, 1, 2})
    assert "sdb is not read for sda -- the brain did not name it" in got["log"], got["log"]
    assert "1 neighbours read" in got["log"], got["log"]
    every = fill(tmp_path / "all", cc, pcl, both, env={"NF_ONLY": ""})
    assert every["rc"] == "rc=0" and every["left"] == [], every["out"] + every["log"]
    assert every["target"] == img.expected()
