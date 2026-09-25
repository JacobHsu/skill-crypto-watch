"""Offline tests for the tuning page logic (no network, no dataset needed).

  python -m unittest discover -s tests
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import tune_core as tc  # noqa: E402


def nodes():
    return [
        {"id": "rsi", "section": "B", "name": "RSI", "group": "momentum", "type": "score", "rule": "rsi",
         "weight": 1.0, "params": {"overbought": 70, "neutral_band": [45, 55], "kind": "sma", "flag": True}},
        {"id": "dmi", "section": "B", "name": "DMI", "group": "trend", "type": "score", "rule": "dmi",
         "weight": 0.5, "enabled": False, "params": {}},
    ]


def profile():
    return {"gate": [{"node": "choppy", "groups": ["trend"], "floor": 0.5},
                     {"node": "trend_established", "groups": ["trend", "ma"], "floor": 0.75, "invert": True}]}


class ApplyPatchTest(unittest.TestCase):
    def test_applies_weight_enabled_params_and_gate_floor_on_copies(self):
        n, p = nodes(), profile()
        patch = {"nodes": {"rsi": {"weight": 2.0, "params": {"overbought": 75, "neutral_band": [40, 60]}},
                           "dmi": {"enabled": True}},
                 "gates": {"choppy": {"floor": 0.3}}}
        n2, p2 = tc.apply_patch(n, p, patch)
        self.assertEqual(n2[0]["weight"], 2.0)
        self.assertEqual(n2[0]["params"]["neutral_band"], [40, 60])
        self.assertTrue(n2[1]["enabled"])
        self.assertEqual(p2["gate"][0]["floor"], 0.3)
        self.assertEqual((n[0]["weight"], n[0]["params"]["overbought"], p["gate"][0]["floor"]), (1.0, 70, 0.5))

    def test_rejects_bad_patches(self):
        bad = [
            {"nodes": {"nope": {"weight": 1}}},
            {"nodes": {"rsi": {"weight": -1}}},
            {"nodes": {"rsi": {"weight": 99}}},
            {"nodes": {"rsi": {"weight": True}}},
            {"nodes": {"rsi": {"enabled": "yes"}}},
            {"nodes": {"rsi": {"rule": "other"}}},
            {"nodes": {"rsi": {"params": {"unknown": 1}}}},
            {"nodes": {"rsi": {"params": {"overbought": "high"}}}},
            {"nodes": {"rsi": {"params": {"neutral_band": [40]}}}},
            {"nodes": {"rsi": {"params": {"kind": 3}}}},
            {"nodes": {"rsi": {"params": {"flag": 1}}}},
            {"gates": {"choppy": {"floor": 1.5}}},
            {"gates": {"choppy": {"groups": []}}},
            {"gates": {"nope": {"floor": 0.5}}},
            {"other": {}},
        ]
        for patch in bad:
            with self.subTest(patch=patch), self.assertRaises(tc.PatchError):
                tc.apply_patch(nodes(), profile(), patch)


class PruneAndTomlTest(unittest.TestCase):
    def test_prune_drops_values_equal_to_the_baseline(self):
        patch = {"nodes": {"rsi": {"weight": 1.0, "enabled": True, "params": {"overbought": 70, "kind": "ema"}},
                           "dmi": {"enabled": False}},
                 "gates": {"choppy": {"floor": 0.5}, "trend_established": {"floor": 0.6}}}
        out = tc.prune_patch(patch, nodes(), profile())
        self.assertEqual(out, {"nodes": {"rsi": {"params": {"kind": "ema"}}},
                               "gates": {"trend_established": {"floor": 0.6}}})

    def test_no_change_prunes_to_empty(self):
        self.assertEqual(tc.prune_patch({"nodes": {"rsi": {"weight": 1.0}}}, nodes(), profile()), {})

    def test_toml_output_round_trips_through_the_config_loader(self):
        import tomllib
        patch = {"nodes": {"rsi": {"weight": 2, "enabled": True,
                                   "params": {"neutral_band": [40, 60], "kind": "ema", "flag": False, "overbought": 72.5}}},
                 "gates": {"choppy": {"floor": 0.3}}}
        _, p2 = tc.apply_patch(nodes(), profile(), patch)
        parsed = tomllib.loads(tc.to_toml("BTC", patch, p2))
        self.assertEqual(parsed["nodes"]["rsi"]["weight"], 2.0)
        self.assertEqual(parsed["nodes"]["rsi"]["params"], {"neutral_band": [40, 60], "kind": "ema", "flag": False,
                                                            "overbought": 72.5})
        gates = parsed["profile"]["gate"]
        self.assertEqual([g["floor"] for g in gates], [0.3, 0.75])
        self.assertTrue(gates[1]["invert"])


class SummariseTest(unittest.TestCase):
    def test_hit_rate_ignores_zero_lean_and_splits_halves(self):
        recs = [{"avg": 1.0, "up": True}, {"avg": -1.0, "up": True}, {"avg": 0.0, "up": True},
                {"avg": 0.5, "up": True}]
        s = tc.summarise(recs)
        self.assertEqual((s["all"]["n"], s["all"]["hit"]), (3, 2 / 3))
        self.assertEqual(s["first_half"]["hit"], 0.5)   # records 0..1: one right, one wrong
        self.assertEqual(s["second_half"]["hit"], 1.0)  # records 2..3: the 0 lean is skipped, 0.5 is right
        self.assertEqual(s["actual_up"], 1.0)
        self.assertEqual(s["lean_up"], 0.5)

    def test_empty(self):
        s = tc.summarise([])
        self.assertEqual((s["questions"], s["all"]["hit"]), (0, None))


class FlipsTest(unittest.TestCase):
    def test_counts_only_lean_changes(self):
        before = [{"avg": 0.5}, {"avg": -0.2}, {"avg": 0.1}, {"avg": 0.0}]
        after = [{"avg": 0.9}, {"avg": 0.3}, {"avg": -0.1}, {"avg": 0.0}]
        self.assertEqual(tc.count_flips(before, after), 2)


class FlowSourcesTest(unittest.TestCase):
    def test_reads_diagrams_and_shared_rule_ids_from_the_docs(self):
        flows = tc.flow_sources()
        if not flows:
            self.skipTest("docs/tv-ta not present")
        self.assertIn("flowchart", flows["mtp"]["mermaid"])
        self.assertIn("較高級別", flows["mtp"]["look"])
        self.assertIs(flows["bb"], flows["bb_b"])           # B-10 follows A-6's rule
        self.assertIn("rsi", flows)
        self.assertGreaterEqual(len(flows), 30)


class DocsTest(unittest.TestCase):
    def test_docs_pick_up_comments_above_the_header_and_before_params(self):
        docs = tc.node_docs()
        self.assertIn("higher-timeframe", docs["mtp"])
        self.assertIn("MACD golden / death cross", docs["macd_cross"])
        self.assertNotIn("----------", docs["alligator"])


if __name__ == "__main__":
    unittest.main()
