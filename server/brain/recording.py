"""#3320 (R14, R2): קובץ "הקלטה" של גל -- כל מה שהמוח ראה והחליט, לפי הסדר, לסימולציה חוזרת.

**מה נכתב.** שורת JSON אחת לכל פעם שהמוח מחליט על גל (הווטו בנקודה הרסנית, ה-tick של K1, K6): הצילום **המלא** שעליו
ההחלטה נשענה (`observe.Snapshot`, כולל הראיות, החושים שלא נקראו והזיכרון), הספים (`decide.Config`), ההחלטות שיצאו,
ומספרי השורות שלהן ב-``brain_decisions``. כך כל שורה בטבלת ההחלטות (R2) מצביעה, דרך ``snap`` (ה-hash של הצילום), על
הקלטים המלאים שלה בקובץ -- בלי שינוי סכמה.

**איפה.** ``<data_dir>/brain/waves/<מזהה הגל>.jsonl``, קובץ לכל גל. נשמרים ``KEEP`` הקבצים האחרונים; הישן נמחק כשנפתח
קובץ חדש (הקלטה היא כלי ניתוח, לא זיכרון: מה שנשמר לתמיד הוא ``brain_memory``).

**סימולציה חוזרת (`replay`).** קורא הקלטה, מריץ שוב את אותם כללים טהורים על כל צילום, ומחזיר כל מקום שבו ההחלטה של
היום שונה ממה שהוקלט. הקלטה מגל אמיתי היא fixture (I18), ושינוי בכלל נבדק מולה לפני שהוא נכנס.

**לעולם אינו זורק** אל הנקודה ההרסנית: כתיבה שנכשלה היא שורת לוג.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import asdict
from pathlib import Path

from server.brain import decide as brain, partition
from server.brain.observe import Cloner, Drawer, Evidence, Proposal, Recall, Snapshot

log = logging.getLogger("imagesrv.brain")

KEEP = 200
VERSION = 1
_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


class Recorder:
    """כותב ההקלטות. אחד לכל שרת (`attach`), ובטוח לתהליכונים."""

    def __init__(self, root: Path, keep: int = KEEP):
        self.root, self.keep = Path(root), keep
        self._lock = threading.Lock()

    def path(self, round_id: str) -> Path:
        return self.root / f"{_SAFE.sub('_', round_id) or '_'}.jsonl"

    def write(self, round_id: str, *, kind: str, snap: Snapshot, cfg: brain.Config, decisions, ids=(),
              mode: str = "", point: str = "", held_s: float = 0.0, members=()) -> bool:
        entry = {"v": VERSION, "kind": kind, "mode": mode, "point": point, "held_s": held_s,
                 "members": sorted(members),
                 "snap": snap.digest(), "snapshot": asdict(snap), "cfg": asdict(cfg),
                 "decisions": [asdict(d) for d in decisions], "ids": [i for i in ids if isinstance(i, int)]}
        try:
            line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
            with self._lock:
                self.root.mkdir(parents=True, exist_ok=True)
                target = self.path(round_id)
                fresh = not target.exists()
                with target.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
                if fresh:
                    self._prune()
            return True
        except Exception:                       # noqa: BLE001 — ההקלטה אינה מפילה החלטה
            log.exception("brain: the recording of %s was not written", round_id)
            return False

    def _prune(self) -> None:
        files = sorted(self.root.glob("*.jsonl"), key=lambda p: (p.stat().st_mtime, p.name))
        for old in files[:max(len(files) - self.keep, 0)]:
            old.unlink(missing_ok=True)


def attach(conn, recorder: Recorder | None) -> None:
    """כמו `veto.attach`: הכותב נישא על החיבור, וכל נקודה שמקבלת את ה-`conn` מקליטה. בלי -- אין הקלטה."""
    try:
        conn.brain_recorder = recorder
    except AttributeError:
        log.warning("brain: %s cannot carry the recorder; no wave recording", type(conn).__name__)


def write(conn, round_id: str, **kw) -> bool:
    recorder = getattr(conn, "brain_recorder", None)
    return recorder.write(round_id, **kw) if recorder is not None else False


# --- הקריאה והסימולציה -------------------------------------------------------------------------------------------


def snapshot(raw: dict) -> Snapshot:
    """`asdict(Snapshot)` בחזרה לצילום. ה-hash של מה שנקרא שווה ל-hash שהוקלט, אחרת הקריאה שגויה (נבדק ב-`load`)."""
    cloners = tuple(Cloner(c["mac"], c["state"], tuple(Evidence(**e) for e in c["evidence"]), tuple(c["unknown"]))
                    for c in raw["cloners"])
    return Snapshot(now=raw["now"], round_id=raw["round_id"], part=raw["part"], cloners=cloners,
                    drawers=tuple(Drawer(**d) for d in raw["drawers"]),
                    proposals=tuple(Proposal(**{**p, "targets": tuple(p["targets"]),
                                                "drawers": tuple(tuple(x) for x in p["drawers"])})
                                    for p in raw["proposals"]),
                    unknown=tuple(raw["unknown"]),
                    memory=tuple(Recall(r["mac"], tuple(tuple(x) for x in r["returns"]),
                                        tuple(tuple(x) for x in r["losses"]), r["unread"]) for r in raw["memory"]),
                    up_at=raw.get("up_at"))


def load(path: Path) -> list[dict]:
    """השורות של הקלטה, עם ``snapshot`` כצילום. שורה פגומה, או כזו שה-hash שלה אינו מה שהוקלט, נזרקת כ-ValueError:
    הקלטה שאי אפשר לקרוא אינה "הקלטה ריקה" (עיקרון 5)."""
    out = []
    for n, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        entry = json.loads(raw)
        snap = snapshot(entry["snapshot"])
        if snap.digest() != entry["snap"]:
            raise ValueError(f"{path}:{n}: the snapshot read back is not the one recorded")
        out.append({**entry, "snapshot": snap, "cfg": brain.Config(**entry["cfg"])})
    return out


def rerun(entry: dict) -> list[dict]:
    """אותם כללים טהורים, על אותו צילום ואותם ספים."""
    snap, cfg = entry["snapshot"], entry["cfg"]
    if entry["kind"] == partition.RULE:
        return [asdict(partition.decide(snap, cfg, members=entry.get("members", ()), held_s=entry.get("held_s", 0.0)))]
    out = brain.decide(snap, cfg)
    if entry["kind"] == "K1":
        out = [d for d in out if d.rule == "K1"]
    return [asdict(d) for d in out]


def replay(path: Path) -> list[tuple[int, dict, dict]]:
    """הסימולציה החוזרת: ``[(מספר השורה, מה שהוקלט, מה שיוצא היום)]`` לכל החלטה שהשתנתה. ריק = הכללים של היום
    מחליטים על הגל הזה בדיוק כמו אז."""
    diffs = []
    for n, entry in enumerate(load(path), start=1):
        got = rerun(entry)
        for i in range(max(len(got), len(entry["decisions"]))):
            was = _norm(entry["decisions"][i]) if i < len(entry["decisions"]) else {}
            now = _norm(got[i]) if i < len(got) else {}
            if was != now:
                diffs.append((n, was, now))
    return diffs


def _norm(d: dict) -> dict:
    return json.loads(json.dumps(d, ensure_ascii=False))
