"""First-pass packet loss evidence for a hypothetical FEC policy (#3514)."""

from functools import reduce
from operator import and_, or_

from server.brain import memory

K = 32
PAYLOAD = 1440


def _masks(report, sizes, packets):
    """Full coverage is needed to count a missing identity as private."""
    if not isinstance(report, dict) or any(type(report.get(key)) is not int for key in
                                           ("k", "chunks", "packets", "groups", "lost", "longest")):
        return None
    rows = report.get("masks")
    if (report["k"] != K or report["chunks"] != len(sizes) or report["packets"] != packets
            or not isinstance(rows, list) or len(rows) != report["groups"] or len(rows) > 128):
        return None
    masks = {}
    for row in rows:
        if not isinstance(row, list) or len(row) != 3 or any(type(n) is not int for n in row):
            return None
        chunk, group, mask = row
        if not 0 <= chunk < len(sizes) or group < 0 or not 0 < mask < 1 << K:
            return None
        count = (sizes[chunk] + PAYLOAD - 1) // PAYLOAD
        if group * K + mask.bit_length() > count or (chunk, group) in masks:
            return None
        masks[chunk, group] = mask
    lost = sum(m.bit_count() for m in masks.values())
    if lost != report["lost"] or not int(lost > 0) <= report["longest"] <= lost:
        return None
    return masks


def partition(reports, sizes):
    counts = [(n + PAYLOAD - 1) // PAYLOAD for n in sizes]
    packets = sum(counts)
    masks = [_masks(report, sizes, packets) for report in reports]
    evidence = {"receivers": len(masks), "measured": sum(m is not None for m in masks),
                "source_groups": sum((n + K - 1) // K for n in counts)}
    if not masks or None in masks:
        return evidence
    shared = private = repairs = peak = 0
    for key in set().union(*masks):
        values = [m.get(key, 0) for m in masks]
        common = reduce(and_, values).bit_count()
        shared += common
        private += reduce(or_, values).bit_count() - common
        depth = max(v.bit_count() for v in values)
        repairs += depth
        peak = max(peak, depth)
    return dict(evidence, shared=shared, private=private, repairs=repairs, peak=peak,
                longest=max(r["longest"] for r in reports))


def judge(parts):
    if not parts or any("peak" not in p for p in parts):
        return {"method": "unmeasured", "packets": None, "timing": None}
    peak = max(p["peak"] for p in parts)
    proactive = any(p["shared"] for p in parts)
    return {"method": "none" if not peak else "xor" if peak == 1 else "reed-solomon",
            "packets": (peak * sum(p["source_groups"] for p in parts) if proactive
                        else sum(p["repairs"] for p in parts)),
            "timing": "proactive" if proactive else "on-demand"}


def record(conn, wave, partitions):
    if conn is None:
        return
    choice = judge(list(partitions.values()))
    memory.record(conn, mode="shadow", rule="K-FEC", action="FEC_POLICY", verdict=choice["method"],
                  reason="הערכת תיקון חבילות לפי צורת האובדן במעבר הראשון",
                  evidence=[{"partitions": partitions, **choice}], round_id=wave,
                  entity=("round", wave), outcome="n/a: shadow")
