"""Offline sanity tests: synthetic markets whose correct reading is known.

  python -m unittest discover -s tests
"""

import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import decide  # noqa: E402
import nodes as nodes_mod  # noqa: E402
import run  # noqa: E402

CONFIG = os.path.join(HERE, "..", "config")


def synthetic(drift, n=None, wave=2.5, period=14, interval="4h"):
    """Linear trend (drift per bar, in price units) plus regular swings, ending on the last bar
    of an impulse leg so the snapshot is an unambiguous trend. Volume is heavier on bars that
    move with the trend; wicks are longer on the side the bar moved toward."""
    if n is None:
        # price rises while sin > 0 (cycle bars 0..6) and falls after; end on the leg's last bar
        n = 497 if drift >= 0 else 504
    o, h, l, c, v = [], [], [], [], []
    price = 300.0 + max(0.0, -drift) * n
    for i in range(n):
        start = price
        price = price + drift + wave * math.sin(2 * math.pi * i / period)
        up = price > start
        o.append(start)
        c.append(price)
        h.append(max(start, price) + (0.3 if up else 0.1))
        l.append(min(start, price) - (0.1 if up else 0.3))
        v.append(1000 * (1.5 if up == (drift > 0) else 0.7))
    return {"pair": "TESTUSDT", "interval": interval, "time": list(range(n)), "close_time": list(range(n)),
            "open": o, "high": h, "low": l, "close": c, "volume": v}


def run_all(drift, symbol="TEST"):
    node_cfg, profile, _ = run.load_config(symbol, CONFIG)
    # synthetic swings are ~2%, below the chart's 5% Zig Zag deviation
    profile.setdefault("indicators", {})["zigzag"] = {"deviation": 1.0, "depth": 10}
    bars = synthetic(drift)
    htf = synthetic(drift * 6, n=60, wave=1.0, interval="1w")
    ctx = nodes_mod.Context(bars, htf, profile.get("indicators", {}))
    results = nodes_mod.evaluate(ctx, node_cfg)
    return ctx, results, decide.compose(node_cfg, results, profile), node_cfg, profile


class TrendTests(unittest.TestCase):
    TREND_NODES = ["mtp", "alligator", "psar", "vol_stop", "ma_20_50", "ema_20_50", "zigzag",
                   "supertrend", "linreg", "vwma", "obv"]

    def test_uptrend_reads_bullish(self):
        _, res, summary, _, _ = run_all(0.4)
        for nid in self.TREND_NODES:
            with self.subTest(node=nid):
                self.assertIsNotNone(res[nid].score, res[nid].evidence)
                self.assertGreater(res[nid].score, 0, res[nid].evidence)
        self.assertEqual(summary["composite_verdict"], decide.BUY)

    def test_downtrend_reads_bearish(self):
        _, res, summary, _, _ = run_all(-0.4)
        for nid in self.TREND_NODES:
            with self.subTest(node=nid):
                self.assertIsNotNone(res[nid].score, res[nid].evidence)
                self.assertLess(res[nid].score, 0, res[nid].evidence)
        self.assertEqual(summary["composite_verdict"], decide.SELL)

    def test_all_36_items_present(self):
        _, _, summary, _, _ = run_all(0.4)
        self.assertEqual(len(summary["sections"]["A"]["rows"]), 19)
        self.assertEqual(len(summary["sections"]["B"]["rows"]), 17)

    def test_flat_market_is_choppy(self):
        _, res, _, _, _ = run_all(0.0)
        self.assertEqual(res["linreg"].score, 0, res["linreg"].evidence)
        self.assertLess(res["trend_established"].noul, 0.5, res["trend_established"].evidence)


class ConfigTests(unittest.TestCase):
    def test_weight_change_needs_no_recompute(self):
        """Composite scoring: changing a weight only re-weights stored answers."""
        _, res, before, cfg, profile = run_all(0.4)
        for n in cfg:
            if n["id"] == "supertrend":
                n["weight"] = 10.0
        after = decide.compose(cfg, res, profile)
        self.assertGreater(after["composite"], before["composite"])

    def test_symbol_override_merges_params(self):
        cfg, _, applied = run.load_config("ETH", CONFIG)
        dmi = next(n for n in cfg if n["id"] == "dmi")
        self.assertEqual(dmi["params"]["bull_score"], 0)
        self.assertEqual(dmi["params"]["adx_min"], 25)  # untouched default survives the merge
        self.assertEqual(applied, ["config", "config/symbols/eth.toml"])

    def test_gate_scales_trend_groups(self):
        _, res, _, _, profile = run_all(0.0)
        mult, _ = decide.gate_multipliers(res, profile["gate"])
        self.assertLess(mult.get("trend", 1.0), 1.0)
        self.assertNotIn("volume", mult)

    def test_routing_downgrades_low_agreement(self):
        _, res, before, cfg, profile = run_all(0.4)
        self.assertEqual(before["composite_verdict"], decide.BUY)
        self.assertIsNone(before["routed"])  # off by default
        profile["decision"]["routing"] = {"enabled": True, "min_agreement": before["agreement"] + 0.01}
        after = decide.compose(cfg, res, profile)
        self.assertEqual(after["composite_verdict"], decide.WAIT)
        self.assertEqual(after["raw_composite_verdict"], decide.BUY)
        self.assertEqual(after["routed"]["from"], decide.BUY)
        split = after["weight_split"]
        self.assertAlmostEqual(split["agree"] + split["neutral"] + split["oppose"], 1.0, places=2)

    def test_macd_cross_is_an_event(self):
        ctx, _, _, cfg, _ = run_all(0.4)
        node = next(n for n in cfg if n["id"] == "macd_cross")
        self.assertFalse(node.get("enabled", True))  # off by default
        h, lb = ctx.macd_hist, node["params"]["lookback"]
        r = nodes_mod.RULES["macd_cross"](ctx, node["params"], {})
        crossed = any((h[i - 1] <= 0 < h[i]) or (h[i - 1] >= 0 > h[i]) for i in range(-lb, 0))
        self.assertEqual(r.score != 0, crossed, r.evidence)

    def test_idle_event_does_not_dilute(self):
        _, res, before, cfg, profile = run_all(0.4)
        next(n for n in cfg if n["id"] == "macd_cross")["enabled"] = True
        res["macd_cross"] = nodes_mod.Result(0, "no cross")
        idle = decide.compose(cfg, res, profile)
        self.assertEqual(idle["composite"], before["composite"])  # not counted as a neutral vote
        self.assertEqual(idle["sections"]["E"]["rows"][0]["signal"], decide.IDLE)
        self.assertEqual(idle["checklist"]["total"], 36)
        res["macd_cross"] = nodes_mod.Result(-1, "death cross")
        fired = decide.compose(cfg, res, profile)
        self.assertLess(fired["composite"], before["composite"])
        self.assertEqual(fired["checklist"]["tally"], before["checklist"]["tally"])  # events stay out of the tally

    def test_polymarket_rapid_cross_is_enabled_choice_event(self):
        cfg, _, _ = run.load_config("BTC", CONFIG)
        node = next(n for n in cfg if n["id"] == "polymarket_rapid_cross")
        self.assertEqual(node["section"], "E")
        self.assertEqual(node["type"], "choice")
        self.assertEqual(node["weight"], 1.0)
        self.assertTrue(node.get("enabled", True))

    def test_polymarket_confirmed_choice_is_weighted_and_idle_is_not(self):
        import json
        import tempfile

        _, res, before, cfg, profile = run_all(0.4)
        node = next(n for n in cfg if n["id"] == "polymarket_rapid_cross")

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({
                "status": "triggered", "asset": "BTC", "direction": "DOWN",
                "confirmed": True, "cross_at_t_plus_min": 8,
                "signal_note": "Polymarket快速反向交叉 → DOWN（T+8m；歷史樣本命中64.8%）",
            }, f, ensure_ascii=False)
            path = f.name
        try:
            ext = run.load_event_results(path, "BTC")
        finally:
            os.unlink(path)

        self.assertEqual(ext["polymarket_rapid_cross"].choice, "down")
        self.assertEqual(ext["polymarket_rapid_cross"].score, -1)
        res["polymarket_rapid_cross"] = ext["polymarket_rapid_cross"]
        after = decide.compose(cfg, res, profile)
        row = next(r for r in after["sections"]["E"]["rows"] if r["id"] == "polymarket_rapid_cross")
        self.assertEqual(row["signal"], decide.SELL)
        self.assertEqual(row["effective_weight"], 1.0)
        self.assertLess(after["composite"], before["composite"])

        res["polymarket_rapid_cross"] = nodes_mod.Result(
            0, "未出現已確認的快速反向交叉", choice="no_signal", source="external"
        )
        idle = decide.compose(cfg, res, profile)
        row = next(r for r in idle["sections"]["E"]["rows"] if r["id"] == "polymarket_rapid_cross")
        self.assertEqual(row["signal"], decide.IDLE)
        self.assertEqual(row["effective_weight"], 0.0)

    def test_polymarket_event_rejects_asset_mismatch(self):
        import json
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"status": "triggered", "asset": "ETH", "direction": "UP", "confirmed": True}, f)
            path = f.name
        try:
            with self.assertRaisesRegex(ValueError, "asset ETH.*BTC"):
                run.load_event_results(path, "BTC")
        finally:
            os.unlink(path)

    def test_trade_plan_long_is_consistent(self):
        ctx, _, _, _, profile = run_all(0.4)
        plan = decide.trade_plan(ctx, decide.BUY, profile["plan"])
        self.assertEqual(plan["type"], "long")
        self.assertLess(plan["stop"], plan["entry_zone"][0])
        self.assertGreater(plan["t2"], plan["entry"])
        self.assertGreaterEqual(plan["entry"] - plan["stop"], profile["plan"]["stop_atr_min"] * ctx.atr[-1] - 1e-9)


class JevTests(unittest.TestCase):
    def test_score_answer_maps_to_node_scale(self):
        import io
        import json
        from unittest import mock

        import jev_client

        _, res, _, cfg, _ = run_all(0.4)
        zz = next(n for n in cfg if n["id"] == "zigzag")
        zz["jev"] = {"instructions": "trend structure in `zigzag`?", "criteria": ["a", "b", "c", "d", "e"]}
        fake = io.BytesIO(json.dumps({"answers": {"zigzag": {"score": 3.0, "confidence": 0.8}}}).encode())
        fake.__enter__ = lambda s=fake: s
        fake.__exit__ = lambda *a: None
        with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "k"}), \
                mock.patch.object(jev_client.urllib.request, "urlopen", return_value=fake) as call:
            warnings = jev_client.apply(cfg, res, {"enabled": True})
        self.assertEqual(warnings, [])
        self.assertAlmostEqual(res["zigzag"].score, 1.0)   # level 3 of 0..4 -> +1 on -2..+2
        self.assertEqual(res["zigzag"].source, "jev")
        sent = json.loads(call.call_args[0][0].data)
        self.assertIn("pivots", sent["state"]["zigzag"])   # the node's numbers are the state
        self.assertEqual(sent["questions"]["zigzag"]["type"], "score")  # inferred from list criteria


if __name__ == "__main__":
    unittest.main()
