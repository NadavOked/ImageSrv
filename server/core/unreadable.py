"""A stored value that could not be read is said once, not folded away in silence (#3129).

`except ValueError: return {}` on a JSON column, or on a timestamp, makes "the row is corrupt" look like
"the row says nothing" (principle 5). The caller keeps its safe answer -- this module only makes sure the
server log says that the value was bad, where, and how big, **once per distinct value** (a poll that reads
the same bad row every second must not fill the log). The value itself is never logged: a storage
location's `params_json` carries secrets, so the line holds its length and a short hash.
"""
from __future__ import annotations

import hashlib
import logging

log = logging.getLogger("imagesrv.unreadable")

#: Distinct (where, value) pairs said so far. Past the cap nothing new is remembered, so a flood of
#: different bad values cannot grow the process without end -- they are counted in `suppressed`.
MAX_SEEN = 256
_seen: set[tuple[str, str]] = set()
suppressed = 0


def said(where: str, raw: object) -> None:
    """Log that `raw`, read from `where`, was not readable (once per distinct value)."""
    global suppressed
    text = raw if isinstance(raw, str) else repr(raw)
    key = (where, hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:8])
    if key in _seen:
        return
    if len(_seen) >= MAX_SEEN:
        suppressed += 1
        return
    _seen.add(key)
    log.warning("unreadable value in %s (%s, %d chars, id %s): the reader used its safe answer",
                where, type(raw).__name__, len(text), key[1])
