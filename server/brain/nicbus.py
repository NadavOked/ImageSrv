"""K-NICBUS (#3776). Compare the same receiver's loss at observed wire rates."""

import json
import logging

from server.brain import memory
from server.learn.netloss import SCAN
from server.rounds import nic_live


def _object(raw):
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _samples(conn, mac):
    for row in conn.execute("SELECT session_id, stream, drops FROM wave_rates "
                            "ORDER BY rowid DESC LIMIT ?", (SCAN,)):
        chunk = _object(row["stream"]).get("chunk")
        receiver = _object(row["drops"]).get(mac)
        # chunk_rate's own rule for a wave that shows the rate and nothing else (a lost machine, a held-back
        # library disk or a too-short first pass is not the bus's doing)
        if (not isinstance(chunk, dict) or chunk.get("ended") != "send_done" or chunk.get("teaches") is not True
                or chunk.get("skip") or not isinstance(receiver, dict)):
            continue
        lost = receiver.get("abandoned")
        if type(lost) is int and lost >= 0:
            yield row["session_id"], lost, chunk.get("chunks"), chunk.get("sent_bps")


def decide(conn, mac, nic_bus, nic_link):
    """Return advisory fields only. Repeat a ledger entry only when its evidence changes."""
    bus = nic_live.clean_bus(nic_bus)
    if bus is None:
        return {}
    try:
        kind = bus["bus"]
        speed = nic_link.get("speed_mbps") if isinstance(nic_link, dict) else None
        speed = speed if type(speed) is int and 0 < speed <= 400_000 else None
        evidence = [f"bus={kind}", f"link_mbps={speed}"]
        samples = list(_samples(conn, mac)) if kind == "pci" else []
        rates = {}
        for wave, lost, chunks, bps in samples:
            evidence.append(f"{wave}: abandoned={lost}, chunks={chunks}, sent_bps={bps}")
            if (type(chunks) is int and chunks > 0 and type(bps) is int and speed
                    and 0 < bps <= speed * 1_000_000):
                losses, total = rates.get(bps, (0, 0))
                rates[bps] = losses + lost, total + chunks
        ceiling = None
        if kind == "pcie":
            verdict = "NONE"
        elif not samples or speed is None:
            verdict = "NOT_MEASURED"
        elif any(lost for _, lost, _, _ in samples):
            verdict = "SHOW"
            # Prefer the fastest rate at the lowest observed normalized loss.
            # A faster, worse observation is required to support a ceiling.
            if rates:
                ratios = {bps: lost / chunks for bps, (lost, chunks) in rates.items()}
                best = min(rates, key=lambda bps: (ratios[bps], -bps))
                if any(bps > best and ratio > ratios[best] for bps, ratio in ratios.items()):
                    ceiling = best / 1_000_000
        else:
            verdict = "NONE"
        evidence.append(f"ceiling_mbps={ceiling}")
        previous = memory.decisions(conn, kind="machine", key=mac, rule="K-NICBUS", limit=1)
        if not previous or previous[0]["evidence"] != evidence:
            memory.record(conn, mode="shadow", rule="K-NICBUS", action="BUS_BANNER", verdict=verdict,
                          reason=f"אפיק כרטיס הרשת {kind}", evidence=evidence, entity=("machine", mac),
                          outcome="n/a: shadow")
        return {"bus_banner": verdict, "ceiling_mbps": ceiling}
    except Exception:
        logging.getLogger("imagesrv.brain").exception("K-NICBUS failed for %s", mac)
        return {}
