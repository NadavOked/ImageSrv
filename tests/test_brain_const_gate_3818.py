"""#3818 (אפיק #3653): קבוע-החלטה חדש בקוד ההחלטות של השרת חייב שורה ב-`server/brain/catalog.toml`.

הקטלוג מצביע על כל כלל לפי קובץ וסימבול, אבל `test_brain_catalog.py` בודק רק את הכיוון
"כל מה שבקטלוג קיים". הכיוון ההפוך, קבוע שאינו בקטלוג, לא הפיל דבר, וכל שבוע נולד קבוע
זמן חדש בלי מוח. הטסט הזה סוגר אותו.

מה נחשב קבוע: שם באותיות גדולות שמסתיים ב-`_S`, `_MS`, `_MBPS`, `_PCT` או `_MAX`, שמוגדר
ברמת המודול כמספר (גם ביטוי של מספרים, כמו `60 * 60`), ב-`server/rounds/` וב-`server/brain/`.
הזיהוי הוא ניתוח AST, לא grep. הקטלוג מכסה קבוע אם יש פריט (או `also`) עם אותו `file`
ובו `symbol` נוקב בשם.

הקבועים שכבר היו בקוד כשהטסט נכתב מוקפאים ב-`FROZEN` למטה, והרשימה רק מתקצרת:
- קבוע חדש שאינו בקטלוג ואינו ב-`FROZEN` מפיל את הטסט.
- ערך ב-`FROZEN` שכבר אינו קבוע, או שכבר בקטלוג, מפיל את הטסט עד שמוציאים אותו.
- הוספת שם ל-`FROZEN` מפילה את `test_the_frozen_list_only_shrinks`.

אל תוסיף ל-`FROZEN`. תוסיף שורה לקטלוג.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CATALOG = REPO / "server" / "brain" / "catalog.toml"
SCOPE = ("server/rounds", "server/brain")
NAME = re.compile(r"^[A-Z][A-Z0-9_]*_(S|MS|MBPS|PCT|MAX)$")

# הקבועים שהיו לפני הטסט, ללא שורה בקטלוג. ההעברה לקטלוג: #3817 ו-#3820 (אפיק #3653).
# הספירה: סריקת AST של הטסט הזה עצמו על lab/vm-1114f ב-bd946e4cf, 19/11/2026.
FROZEN_COUNT = 56   # 47 + 9 tuple-assigned constants the scan did not see before (#3932, added once, on purpose)
FROZEN = {
    ("server/rounds/chunksend.py", "SHUTDOWN_GRACE_S"),
    ("server/rounds/cloner_round.py", "NONCE_TTL_S"),
    ("server/rounds/cloner_round.py", "RATE_S"),
    ("server/rounds/directchunk.py", "INDEX_MAX"),
    ("server/rounds/directchunk.py", "HEAD_MAX"),
    ("server/rounds/indexprep.py", "POLL_S"),
    ("server/rounds/indexprep.py", "PULL_WAIT_S"),
    ("server/rounds/nic_live.py", "LIVE_MAX_AGE_S"),
    ("server/rounds/nic_live.py", "LOW_FLOOR_MBPS"),
    ("server/rounds/nic_speed.py", "PROBE_MAX_AGE_S"),
    ("server/rounds/prodline.py", "WAIT_FRESH_S"),
    ("server/rounds/room.py", "RATE_STALE_S"),
    ("server/rounds/room.py", "ETA_MIN_SAMPLE_S"),
    ("server/rounds/room_console.py", "HISTORY_MAX"),
    ("server/rounds/room_names.py", "INSTRUCTOR_MAX"),
    ("server/brain/ask.py", "RECENT_CLOSED_S"),
    ("server/brain/budget.py", "TICK_S"),
    ("server/brain/budget.py", "BURST_S"),
    ("server/brain/budget.py", "MAX_WAIT_S"),
    ("server/brain/carousel.py", "OVERHEAD_S"),
    ("server/brain/cpucap.py", "DEDUPE_S"),
    ("server/brain/decide.py", "DAY_S"),
    ("server/brain/fill.py", "WINDOW_S"),
    ("server/brain/fill.py", "BAD_MAX"),
    ("server/brain/fill.py", "PEER_SPEED_S"),
    ("server/brain/fill.py", "WORD_S"),
    ("server/brain/gates.py", "TIMEOUT_S"),
    ("server/brain/ingest.py", "POLL_S"),
    ("server/brain/ingest.py", "EVICT_MARGIN_S"),
    ("server/brain/l2sense.py", "QUERIER_WINDOW_S"),
    ("server/brain/l2sense.py", "SERVER_LISTEN_S"),
    ("server/brain/linkwarn.py", "MIN_ADDED_S"),
    ("server/brain/peer.py", "UNTIL_SLACK_S"),
    ("server/brain/peer.py", "UNTIL_MAX_S"),
    ("server/brain/resume.py", "KEEP_S"),
    ("server/brain/served.py", "KEEP_S"),
    ("server/brain/services.py", "UNIT_TIMEOUT_S"),
    ("server/brain/services.py", "CACHE_S"),
    ("server/brain/services.py", "DEDUPE_S"),
    ("server/brain/slowdisk.py", "MAX_MBPS"),
    ("server/brain/veto.py", "STALE_HOLD_S"),
    # #3932: tuple-assigned, found once the gate reads tuple targets; they move to the catalog with the rest (#3817, #3820)
    ("server/brain/ask.py", "MAX_TTL_S"),
    ("server/brain/ask.py", "MIN_TTL_S"),
    ("server/brain/joiner.py", "X1_CEIL_S"),
    ("server/brain/joiner.py", "X1_FLOOR_S"),
    ("server/brain/route.py", "HTTP_PER_RANGE_S"),
    ("server/brain/route.py", "LEASE_FLOOR_S"),
    ("server/rounds/chunksend.py", "DEFAULT_LEAD_MS"),
    ("server/rounds/chunksend.py", "DEFAULT_TAIL_MS"),
    ("server/rounds/room_names.py", "PAD_MAX"),
}


def _is_number(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float)) and not isinstance(node.value, bool)
    if isinstance(node, ast.UnaryOp):
        return _is_number(node.operand)
    if isinstance(node, ast.BinOp):
        return _is_number(node.left) and _is_number(node.right)
    return False


def _in_tree(tree: ast.Module, rel: str) -> set[tuple[str, str]]:
    found = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        for t in targets:
            if isinstance(t, ast.Name) and NAME.match(t.id) and _is_number(value):
                found.add((rel, t.id))
            # #3932: `A_S, B_S = 1, 2` -- a tuple target counts only when the value is a tuple of the same length
            elif isinstance(t, ast.Tuple) and isinstance(value, ast.Tuple) and len(t.elts) == len(value.elts):
                found.update((rel, n.id) for n, v in zip(t.elts, value.elts)
                             if isinstance(n, ast.Name) and NAME.match(n.id) and _is_number(v))
    return found


def _constants() -> set[tuple[str, str]]:
    found = set()
    for scope in SCOPE:
        files = sorted((REPO / scope).rglob("*.py"))
        # סריקה שלא מצאה קבצים אינה "אין קבועים" (עיקרון 5)
        assert files, f"לא נמצאו קבצי py תחת {scope}"
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            found |= _in_tree(tree, path.relative_to(REPO).as_posix())
    return found


def _cataloged() -> set[tuple[str, str]]:
    with CATALOG.open("rb") as f:
        items = tomllib.load(f)["item"]
    out = set()
    for it in items:
        for loc in [it, *it.get("also", [])]:
            if "file" in loc and "symbol" in loc:
                out.update((loc["file"], s) for s in re.findall(r"[A-Za-z_]\w*", loc["symbol"]))
    return out


def test_the_scan_sees_constants():
    # סריקה שאיבדה את כל הקבועים הייתה עוברת את כל מה שלמטה בלי לבדוק כלום (עיקרון 5)
    assert len(_constants()) > 60


def test_every_decision_constant_is_in_the_catalog():
    new = sorted(_constants() - _cataloged() - FROZEN)
    assert not new, (
        "קבוע חדש בקוד ההחלטות בלי שורה ב-server/brain/catalog.toml: "
        + ", ".join(f"{n} ({f})" for f, n in new)
        + ". הוסף פריט לקטלוג עם file ו-symbol (אל תוסיף ל-FROZEN: הוא מוקפא, "
        "והקבועים הישנים עוברים לקטלוג ב-#3817 ו-#3820)."
    )


def test_the_frozen_list_has_no_stale_entries():
    constants, cataloged = _constants(), _cataloged()
    gone = sorted(FROZEN - constants)
    done = sorted(FROZEN & cataloged)
    assert not gone, f"ב-FROZEN יש שמות שאינם עוד קבוע: {gone}. הוצא אותם."
    assert not done, f"ב-FROZEN יש שמות שכבר בקטלוג: {done}. הוצא אותם."


def test_the_frozen_list_only_shrinks():
    assert len(FROZEN) <= FROZEN_COUNT, (
        f"נוספו שמות ל-FROZEN ({len(FROZEN)} > {FROZEN_COUNT}). הרשימה רק מתקצרת: "
        "קבוע חדש הולך לקטלוג, לא לרשימה."
    )


def test_a_tuple_assigned_constant_is_seen():
    # #3932: before, `A_S, B_S = 1, 2` was invisible to the gate. A name that is not a constant, or a value that is not
    # a number, is not counted.
    tree = ast.parse("A_S, B_S = 1, 2\nC_MS, d = 3, 4\nE_S, F_S = make(), 5\n")
    assert _in_tree(tree, "x.py") == {("x.py", "A_S"), ("x.py", "B_S"), ("x.py", "C_MS"), ("x.py", "F_S")}
