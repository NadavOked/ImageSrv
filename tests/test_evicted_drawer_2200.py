"""‏#2200 — מגירה ש-fanout הוציא מהזרם נכשלת בסוף המחיצה, בלי 300 שניות המתנה.

נמצא על ברזל ב-25/10 (משכפל 2, ‏v0.76.4): ‏partclone של ה-Toshiba נעצר
(‏SIGSTOP), ‏fanout הוציא אותו אחרי 45 שניות (#1796), והמגירה השנייה המשיכה.
אבל בסוף המחיצה הסוכן המתין לצינור של המגירה שהוצאה את מלוא ‏`WAIT_DRAWER_S`
(‏300ש'), ורק אז התחילה מחיצה 4: ‏`wall_s` ‏635.8 מול 311.6 בלי הזרקה.
ואחרי ההמתנה ההריגה הגיעה למעטפת הצינור (`pipeline.pid`), וה-partclone
(`reader.pid`) נשאר בחיים.

כאן ‏`restore_partition_drawers` האמיתי, עם fanout מזויף שמדווח את מה
שהבינארי מדווח על מגירה שהוציא, ו-partclone מזויף שמפסיק לקרוא בלי לצאת.
רץ גם בווינדוס (Git Bash, ‏mkfifo), כמו `test_timeouts.py`.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_timeouts import STUBS as BASE_STUBS, log_of, make_stubs, run_sh
from agentlib import agent_sources

pytestmark = requires_native(("bash", BASH))

#: ‏partclone מזויף: ל-sda כותב הכול; ל-sdb קורא לפי ‏$SDB_READ ואז נתקע
#: בלי לצאת ובלי לקרוא עוד -- כמו partclone שנעצר מול כונן תקוע.
PARTCLONE = (
    '#!/bin/sh\n'
    'node=\n'
    'while [ $# -gt 0 ]; do case "$1" in -O) node=$2; shift 2 ;; *) shift ;; esac; done\n'
    'case "$node" in\n'
    '  */sdb3) $SDB_READ > /dev/null; exec sleep 60 ;;\n'
    '  *) cat > "$node" ;;\n'
    'esac\n'
)

#: ‏fanout מזויף. הזרם (יעד אחד: ‏mid) עובר כמו שהוא. ה-fanout של המגירות
#: מזין את sdb ב-4KB בלבד וסוגר אותה -- זו ההוצאה -- ואת sda בהכול, ומדווח
#: את השורה שהבינארי כותב על מגירה שהוציא (fanout.c, ‏make_room).
EVICTING_FANOUT = (
    '#!/bin/sh\n'
    'shift\n'
    'tmp="$(mktemp)"\n'
    'cat > "$tmp"\n'
    'fanout_eof "$tmp"\n'
    'if [ $# -eq 1 ]; then cat "$tmp" > "$1"; rm -f "$tmp"; echo "$1 ok"; exit 0; fi\n'
    'head -c 4096 "$tmp" > "$2"\n'
    'cat "$tmp" > "$1"\n'
    'rm -f "$tmp"\n'
    'echo "$1 ok"\n'
    'echo "$2 failed stalled the stream (too slow to drain)"\n'
    'exit 1\n'
)


def _room(tmp_path: Path, fanout: str, sdb_read: str, drawer_s: int) -> tuple[str, Path]:
    box = tmp_path / "box"
    run = box / "run"
    payload = box / "part.bin"
    box.mkdir(parents=True)
    payload.write_bytes(b"imagesrv-2200" * 8192)
    sha = hashlib.sha256(payload.read_bytes()).hexdigest()
    for dev in ("sda", "sdb"):
        target = run / "targets" / dev
        target.mkdir(parents=True)
        (target / "state").write_text("writing\n")
        (target / "base").write_text("0\n")
        (target / "bytes.raw").write_text("")
    reader = posix(run / "targets" / "sdb" / "reader.pid")
    out = run_sh(
        make_stubs(box / "stubs", {"fanout": fanout, "partclone.dd": PARTCLONE})
        + f"export RUN_DIR={posix(run)!r} DEVROOT={posix(box)!r} SDB_READ={sdb_read!r} "
        f"WAIT_POLL_S=1 WAIT_DRAWER_S={drawer_s} WAIT_HELPER_S=3 "
        "WAIT_STREAM_START_S=20 WAIT_STREAM_STALL_S=20; "
        + agent_sources("common", "sysinfo", "waits", "progress", "restore", "writeback", "drawers",
                        "drawerfeed", "verdict", "failmark", "ntfsboot")   # #2272: imagesrv-agent's order
        + f"stream_source() {{ cat {posix(payload)!r}; }}; "
        "target_bind sda; target_bind sdb; "   # #2434: the drawers chosen, as target_init binds them
        "s=$(date +%s); "
        "restore_partition_drawers unicast http://s img 3 dd part.zst "
        f"{sha} '' sda sdb > {posix(box)}/pipe.out 2>&1; rc=$?; e=$(date +%s); "
        # ‏kill -9 אינו מיידי ליתום שמחכה ל-init: עד 3 שניות לפני שמכריזים "חי".
        f'r=$(cat {reader!r} 2>/dev/null); n=0; '
        'while [ -n "$r" ] && kill -0 "$r" 2>/dev/null && [ $n -lt 30 ]; do sleep 0.1; n=$((n + 1)); done; '
        'if [ -n "$r" ] && kill -0 "$r" 2>/dev/null; then alive=yes; else alive=no; fi; '
        'echo "rc=$rc elapsed=$((e - s)) reader_alive=$alive reader=${r:-none}"',
    )
    return out, run


def _fields(out: str) -> dict[str, str]:
    m = re.search(r"rc=\S+ elapsed=\S+ reader_alive=\S+ reader=\S+", out)
    assert m, out
    return dict(f.split("=", 1) for f in m.group(0).split())


def _target(run: Path, dev: str, name: str) -> str:
    path = run / "targets" / dev / name
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def test_an_evicted_drawer_fails_at_the_streams_end_not_after_the_drawer_ceiling(tmp_path):
    """התרחיש של 25/10. ‏WAIT_DRAWER_S=20 עומד כאן במקום 300: בלי התיקון
    המחיצה נגמרת אחרי 20 שניות לפחות, והמגירה נכשלת ב"פג הזמן"."""
    out, run = _room(tmp_path, EVICTING_FANOUT, "head -c 1", drawer_s=20)
    f = _fields(out)
    log = log_of(run)
    assert f["rc"] == "0", out + log
    # המגירה התקינה גמרה, וקיבלה כל בייט.
    assert _target(run, "sda", "state") != "failed", _target(run, "sda", "error") + log
    payload = (tmp_path / "box" / "part.bin").read_bytes()
    assert (tmp_path / "box" / "sda3").read_bytes() == payload
    # זו שהוצאה -- נכשלת בשמה ובסיבה שלה, לא "פג הזמן", ובלי להמתין לתקרה.
    assert _target(run, "sdb", "state") == "failed", log
    error = _target(run, "sdb", "error")
    assert "הוצאה מהזרם" in error and "stalled the stream" in error, error
    assert "פג הזמן" not in error, error
    assert int(f["elapsed"]) < 12, (f, log)
    # וה-partclone שלה (הקורא של ה-fifo) נהרג, לא רק המעטפת.
    assert f["reader"] != "none", out
    assert f["reader_alive"] == "no", (f, log)


def test_a_drawer_past_its_ceiling_has_its_reader_killed_too(tmp_path):
    """המסלול שלא השתנה בזמן: מגירה ש-fanout **לא** הוציא (קיבלה הכול, ‏ok)
    ועדיין לא גמרה ממתינה את ‏WAIT_DRAWER_S ונכשלת ב"פג הזמן". מה שהשתנה:
    גם כאן הקורא נהרג, ולא רק ‏pipeline.pid (הממצא הנלווה של #2200)."""
    out, run = _room(tmp_path, BASE_STUBS["fanout"], "cat", drawer_s=3)
    f = _fields(out)
    log = log_of(run)
    assert f["rc"] == "0", out + log
    assert _target(run, "sda", "state") != "failed", _target(run, "sda", "error") + log
    assert _target(run, "sdb", "state") == "failed", log
    assert "פג הזמן" in _target(run, "sdb", "error"), _target(run, "sdb", "error")
    assert int(f["elapsed"]) >= 3, f   # השומר מפני "תיקון" שמקצר את התקרה לכולם
    assert f["reader"] != "none", out
    assert f["reader_alive"] == "no", (f, log)


def test_the_eviction_line_the_agent_reads_is_the_one_fanout_writes():
    """הזיוף למעלה כותב ‏`failed stalled ...`, והסוכן מחפש ‏`feed failed stalled`.
    שתי סיבות ההוצאה ב-fanout.c חייבות להתחיל ב-`stalled`, אחרת הזיוף בודק
    את עצמו ולא את הבינארי."""
    src = (AGENT / "fanout.c").read_text(encoding="utf-8")
    evict = re.findall(r'fail_target\([^,]+, "([^"]*stall[^"]*)"', src)
    assert sorted(evict) == ["stalled the stream (too slow to drain)", "stalled with no write progress"], evict
    assert 'printf("%s failed %s\\n", t->path' in src
    verdict = (AGENT / "lib" / "restore" / "verdict.sh").read_text(encoding="utf-8")
    assert '/feed failed stalled"' in verdict
