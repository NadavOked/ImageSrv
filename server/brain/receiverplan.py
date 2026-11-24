"""Retrospective proposals from one wave's first-pass durable receiver rates."""

from collections import defaultdict
from math import ceil

from server.brain import memory, veto

MIN_RECEIVERS = 6
LIMIT_PERCENTILE = 25
FILL_THRESHOLD_BPS = 8_000_000
SPEED_CLASSES = ("slow", "medium", "fast")


def _measurement(decision, sample):
    roster = defaultdict(set)
    for drawer in decision.get("roster") or []:
        if not isinstance(drawer, dict) or not drawer.get("mac") or not drawer.get("serial"):
            return "NOT_MEASURED", "incomplete_roster", {}
        roster[drawer["mac"]].add(drawer["serial"])
    if sample["ended"] != "send_done" or sample["away_macs"]:
        return "UNDECIDED", "interrupted_wave", {}
    if not roster or sample["cloners"] != len(roster) or sample["away_macs"] is None:
        return "NOT_MEASURED", "incomplete_membership", {}
    rates = sample["goodput"] or {}
    drives = sample["drives"] or {}
    if (rates.keys() != roster.keys() or sample["sent_bps"] is None
            or any(set(drives[mac]) != serials or rates[mac]["drives"] != len(serials)
                   for mac, serials in roster.items())):
        return "NOT_MEASURED", "incomplete_first_pass", {}
    measured = {mac: rates[mac]["bps"] for mac in sorted(rates)}
    if len(measured) < MIN_RECEIVERS:
        return "UNDECIDED", "small_wave", measured
    return "PROPOSE", "complete_first_pass", measured


def observe(conn, wave, decision, sample):
    if veto.mode(conn) == "off":
        return
    verdict, reason, rates = _measurement(decision, sample)
    evidence = {"asked_bps": sample["asked_bps"], "sent_bps": sample["sent_bps"], "receiver_bps": rates}
    stream_plan = evidence.copy()
    if verdict == "PROPOSE":
        rank = ceil(len(rates) * LIMIT_PERCENTILE / 100)
        rate = sorted(rates.values())[rank - 1]
        evidence.update(proposed_bps=rate, limiting_mac=min(m for m in rates if rates[m] == rate),
                        leave_behind=[m for m in rates if rates[m] < rate])
        groups = {name: [] for name in SPEED_CLASSES}
        fill = []
        fastest = max(rates.values())
        for mac, bps in rates.items():
            if bps < FILL_THRESHOLD_BPS:
                fill.append(mac)
            else:
                # Equal-width bands (0, peak/3], (peak/3, 2*peak/3], (2*peak/3, peak].
                band = (len(SPEED_CLASSES) * bps - 1) // fastest
                groups[SPEED_CLASSES[band]].append(mac)
        stream_plan.update(streams=groups, fill=fill, fill_threshold_bps=FILL_THRESHOLD_BPS)
    memory.record(conn, mode="shadow", rule="#3564", action="REPRESENTATIVE_RECEIVER",
                  round_id=wave, entity=("round", wave), verdict=verdict, reason=reason,
                  evidence=[evidence], outcome="shadow")
    memory.record(conn, mode="shadow", rule="#3565", action="RECEIVER_SPEED_STREAMS",
                  round_id=wave, entity=("round", wave), verdict=verdict, reason=reason,
                  evidence=[stream_plan], outcome="shadow")
