import json
import unittest

from server import db
from server.rounds import chunk_rate


class ReceiverPlanTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.addCleanup(self.conn.close)

    def observe(self, rates, **changes):
        roster = [{"mac": mac, "serial": mac} for mac in rates]
        decision = {"roster": roster, "max_bitrate": "90m"}
        self.conn.execute("INSERT INTO wave_rates(session_id, decision, at) VALUES (?,?,?)",
                          ("wave", json.dumps(decision), "now"))
        self.conn.commit()
        outcome = {"ended": "send_done", "cloners": len(roster), "away_macs": [],
                   "first_pass_s": 8, "stream_zbytes": 80_000_000,
                   "drives": {mac: {mac: rate} for mac, rate in rates.items()}, **changes}
        chunk_rate.record(self.conn, "wave", outcome)
        row = self.conn.execute("SELECT * FROM brain_decisions WHERE round_id=? AND action=?",
                                ("wave", "REPRESENTATIVE_RECEIVER")).fetchone()
        self.assertIsNotNone(row, "wave ended without a shadow receiver decision")
        self.assertEqual(row["mode"], "shadow")
        return row["verdict"], json.loads(row["evidence_json"])[0]

    def test_ties_and_zero(self):
        verdict, plan = self.observe(dict(zip("abcdef", [0, 10_000_000, 10_000_000, 40_000_000, 80_000_000, 80_000_000])))
        self.assertEqual(verdict, "PROPOSE")
        self.assertEqual((plan["proposed_bps"], plan["limiting_mac"]), (10_000_000, "b"))
        self.assertEqual(plan["leave_behind"], ["a"])
        self.assertEqual((plan["asked_bps"], plan["sent_bps"]), (90_000_000, 80_000_000))

    def test_missing_drawer(self):
        verdict, plan = self.observe(dict.fromkeys("abcdef", 100), drives={"a": {"a": 100}})
        self.assertEqual(verdict, "NOT_MEASURED")
        self.assertNotIn("leave_behind", plan)

    def test_small_wave(self):
        self.assertEqual(self.observe({"a": 0, "b": 100})[0], "UNDECIDED")
