"""#3600 -- how fast the NETWORK can give one cloner's target, from what the brain already measures. Read-only.

Nadav 17/11 and 18/11: a slow SATA port (1.5, 3.0 or 6.0) is not a warning and not a "better port" suggestion **unless it is the bottleneck**.
On a 1G network nothing is said at all; the port is named only when the network that was measured is faster than the port.

**What is measured, and nothing else** (principle 5: "not known" is not "bottleneck", and 1G is never assumed):

- the cloner's own card: `nic_speed.measured` -- the live `nic_link` of its hello, or its probe when that is fresh and from this boot.
  The operator's manual choice and the 1G assumption are NOT a measurement and are not read here.
- the server's distribution card: what the last wave recorded in its decision (`wave_rates.decision.link`, `drive_rates.read_link`).
  A direct wave (a build machine sends) says nothing about the server's card; a card that was not read (`virtual`, `absent`, ...) is not known.

Both must be known. The slower one is the wire (`by`), as `drive_rates.choose` decides a wave's ceiling, and the rate is the brain's own
(`LINK_CEILING_PER_MBPS`: 90% of the link, room for ACKs and retransmissions): 1G = 112MB/s, 2.5G = 281MB/s, 10G = 1125MB/s.
Either one unknown -> `mbs` None and `why` says which. That is "no recommendation", never "the network is slow".

**The margin** (`wiring_net_margin`, default 1.1): a port is the bottleneck only when the wire is at least that many times its ceiling (`is_bottleneck`).
2.5G (281MB/s) against a 3.0 port (280MB/s) is a tie, not a reason.

**Not read:** an old measurement. A cloner that is off for longer than `nic_speed.PROBE_MAX_AGE_S` has no measured card, and the wiring map says nothing
about speed until it is on again. The cost of that is silence; the alternative was a "1000 reported" for a link that fell to 100 (#3277).
"""

from __future__ import annotations

import json
import logging
import math

from server.db import get_setting, SqlConnection
from server.learn import portcap
from server.rounds import drive_rates, nic_speed

log = logging.getLogger("imagesrv.netrate")

#: מרווח (נדב 14/11: ספים בהגדרות, ברירת מחדל והמפעיל משנה): יציאה נקראת צוואר הבקבוק רק כשקצב הרשת **לפחות** פי `margin` מהתקרה שלה.
#: בלי מרווח, 2.5G (281MB/s) מול 3.0 (280MB/s) היה "המלצה" שאינה נותנת שום רווח. ⚠️ **1.1 הוא הכרעת המתאם (18/11), לא מדידה.**
MARGIN_SETTING = "wiring_net_margin"
DEFAULT_MARGIN = 1.1
MARGIN_RANGE = (1.0, 10.0)
LABEL_HE = "מפת החיבורים: פי כמה מהיר מהיציאה צריך להיות הרשת כדי להמליץ על יציאה מהירה יותר"

#: how many of the latest waves are read for the server's card (a wave that has none -- a direct one -- is skipped, not "unknown").
SCAN = 20


def _server_card(conn: SqlConnection) -> tuple[int | None, str | None]:
    """`(Mb/s, None)` or `(None, why)` -- the card of the latest wave that recorded the server's own link."""
    for row in conn.execute("SELECT decision FROM wave_rates ORDER BY at DESC, rowid DESC LIMIT ?", (SCAN,)):
        try:
            decision = json.loads(row["decision"])
        except (TypeError, ValueError):
            continue
        link = decision.get("link") if isinstance(decision, dict) else None
        if not isinstance(link, dict) or decision.get("direct"):
            continue
        mbps = link.get("speed_mbps")
        if isinstance(mbps, int) and not isinstance(mbps, bool) and mbps > 0:
            return mbps, None
        return None, "server_unmeasured"            # the latest wave that looked at the card could not read it: that is the answer
    return None, "server_no_wave"


def wire(conn: SqlConnection, mac: str) -> dict:
    """`{"mbs", "mbps", "by", "cloner_mbps", "server_mbps", "why"}` for the cloner `mac`. `mbs` (MB/s the wire can give one target) is None
    unless both cards were measured; `why` is then `cloner_<why of nic_speed.measured>` / `server_unmeasured` / `server_no_wave`. A failed read is raised:
    "could not check" is not "nothing to say"."""
    cloner, cloner_why = nic_speed.measured(conn, mac)
    server, server_why = _server_card(conn)
    out = {"mbs": None, "mbps": None, "by": None, "cloner_mbps": cloner, "server_mbps": server, "why": None}
    if cloner is None or server is None:
        return {**out, "why": f"cloner_{cloner_why}" if cloner is None else server_why}
    mbps = min(cloner, server)
    return {**out, "mbps": mbps, "by": "cloner" if cloner <= server else "server",
            "mbs": mbps * drive_rates.LINK_CEILING_PER_MBPS / 8_000_000}


def parse_margin(raw: object) -> float | None:
    """ערך תקין (מספר 1.0-10.0, עד שתי ספרות אחרי הנקודה) או None. `bool`, NaN, אינסוף ומחרוזת שאינה מספר -- לא."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return None
    try:
        value = float(raw.strip() if isinstance(raw, str) else raw)
    except ValueError:
        return None
    if not math.isfinite(value) or not MARGIN_RANGE[0] <= value <= MARGIN_RANGE[1] or round(value, 2) != value:
        return None
    return value


def margin(conn: SqlConnection) -> float:
    """ההגדרה, או `DEFAULT_MARGIN`. פגומה = ברירת המחדל **עם שורת יומן**, לעולם לא 0 ("כל יציאה היא הצוואר")."""
    raw = get_setting(conn, MARGIN_SETTING)
    if raw is None:
        return DEFAULT_MARGIN
    value = parse_margin(raw)
    if value is None:
        log.warning("setting %s=%r is not %g-%g -- using %g", MARGIN_SETTING, raw, *MARGIN_RANGE, DEFAULT_MARGIN)
        return DEFAULT_MARGIN
    return value


def is_bottleneck(wire_mbs: float | None, port_gbps: object, factor: float) -> bool:
    """הרשת (`wire`) מהירה מהיציאה לפחות פי `factor`. רשת שלא נמדדה, או יציאה בלי תקרה מוכרת (`portcap.ceiling_mbps`) -- לא: "לא ידוע"
    אינו "צוואר בקבוק"."""
    ceiling = portcap.ceiling_mbps(port_gbps)
    return wire_mbs is not None and ceiling is not None and wire_mbs >= factor * ceiling
