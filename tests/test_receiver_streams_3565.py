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
                                ("wave", "RECEIVER_SPEED_STREAMS")).fetchone()
        self.assertIsNotNone(row, "wave ended without a shadow receiver decision")
        self.assertEqual(row["mode"], "shadow")
        return row["verdict"], json.loads(row["evidence_json"])[0]

    def test_groups_and_fill(self):
        rates = dict(zip("abcdef", [0, 7_999_999, 8_000_000, 39_000_000, 79_000_000, 80_000_000]))
        verdict, plan = self.observe(rates)
        self.assertEqual(verdict, "PROPOSE")
        self.assertEqual(plan["fill"], ["a", "b"])
        self.assertEqual(plan["streams"], {"slow": ["c"], "medium": ["d"], "fast": ["e", "f"]})

    def test_interrupted_wave(self):
        verdict, plan = self.observe(dict.fromkeys("abcdef", 100), ended="send_stopped")
        self.assertEqual(verdict, "UNDECIDED")
        self.assertNotIn("streams", plan)
