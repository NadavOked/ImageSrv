"""#3675, part 1 (group (ב) of #3653: "המשתמש מחליט"): every change of a setting the brain may not invent or change by itself is a
decision in `brain_decisions`: the value, who changed it, when.

**The settings** (the owner's product choices; the brain reads them as inputs and may suggest, never write): `hdd_support` (A6, L5),
`tail_shrink_enabled` (A13), `min_free_gb` (S46, A12 part 2) and the agent timings (S44: lost / idle / end / the two drawer waits).
`stream_engine` (S1) is not here: #3173 retired it, there is one engine and nothing to choose.

**What is recorded.** One row per changed key: rule ``B3675`` (catalog item), action ``SETTING_CHANGE``, verdict ``SET``, entity
``settings`` / the key, evidence ``key=``, ``old=`` (``default`` when the key had no row), ``new=``, ``by=``. A write that leaves the
stored value as it was is not a change and records nothing. The row is a fact about the user's decision, not a brain judgment: it is
written whatever `brain_veto_mode` says (`off` is the brain's own rules, and the console's `setting_change` journal line is not
gated by it either) and it is ``enforce`` -- the choice stands, nothing waits for a shadow proof.

**Never raises into the write of the setting:** a brain that fell is a log line (`memory.record` returns None), the setting is
already stored.
"""

from __future__ import annotations

from collections.abc import Mapping

from server.agent import timing as agent_timing
from server.brain import memory

RULE = "B3675"
ACTION = "SETTING_CHANGE"
VERDICT = "SET"
#: the five settings of #3675 (the agent timings are five keys, the other two one each); `minfree.SETTING` is `min_free_gb`.
KEYS = ("hdd_support", agent_timing.TAIL_SHRINK, "min_free_gb", *agent_timing.KEYS)


def record(conn, before: Mapping[str, str | None], after: Mapping[str, str], who: str) -> list[int]:
    """The ids of the rows written for ``after`` (the validated body of the write) against ``before`` (the stored values read
    just before it, ``None`` = no row). Keys that are not user decisions of #3675, and keys whose value did not change, are skipped."""
    ids: list[int] = []
    for key in KEYS:
        if key not in after or before.get(key) == after[key]:
            continue
        old = before.get(key)
        row = memory.record(
            conn, mode="enforce", rule=RULE, action=ACTION, verdict=VERDICT, entity=("settings", key),
            reason=f"{who} שינה את ההגדרה {key} מ-{'ברירת המחדל' if old is None else old} ל-{after[key]}",
            evidence=(f"key={key}", f"old={'default' if old is None else old}", f"new={after[key]}", f"by={who}"),
            outcome="ההגדרה נשמרה", orphans=False)
        if row is not None:
            ids.append(row)
    return ids
