"""trader-exit tests, NSE cash intraday.

Worked example, from the SYNTHETIC TCS fixture: opening range 3996.00 to
4020.00, entry 4026.50 on the 09:45 bar of 2026-11-02.
    or-mid stop = (4020.00 + 3996.00) / 2 = 4008.00
    risk       = 4026.50 - 4008.00 = 18.50 a share
    1R target  = 4045.00     2R target = 4063.50
Prices land on the 0.05 NSE tick, never the paisa.
"""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal

from _load import load, FIX, ROOT

exitp = load("trader-exit", "exit")
entry = load("trader-entry", "entry")

INDIA_FIX = os.path.join(FIX, "india")

INDIA_ENTRY = {"created_by": "trader-entry", "symbol": "TCS", "setup": "orb", "side": "buy",
               "date": "2026-11-02", "status": "triggered", "time": "09:45", "bar_start": "09:45",
               "bar_index": 6, "price": 4026.50, "level": 4020.00, "interval": "5m",
               "opening_range": {"high": 4020.00, "low": 3996.00, "minutes": 15},
               "source": "offline", "checked_at": "2026-11-02T04:15:00Z"}

EXPECT_1R = {"symbol": "TCS", "side": "buy", "date": "2026-11-02", "setup": "orb",
             "entry": 4026.50, "stop": 4008.00, "stop_rule": "or_mid",
             "stop_label": "opening-range midpoint", "target": 4045.00, "target_r": 1.0,
             "risk_per_share": 18.50, "reward_risk": 1.0, "flat_by": "15:15",
             "currency": "INR", "exchange": "NSE", "product": "MIS", "square_off": "15:20"}


def run(mod, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = mod.main(argv)
    return code, out.getvalue(), err.getvalue()


def c(entry_px, stop, target="2R", oh=4020.00, ol=3996.00, atr=None, n=None):
    sr, sp = exitp.parse_stop(stop)
    tr, tp = exitp.parse_target(target)
    return exitp.compute(entry_px, sr, sp, tr, tp, oh, ol, atr, n)


class Math(unittest.TestCase):
    def test_or_mid_1r(self):
        p = c(4026.50, "or-mid", "1R")
        self.assertEqual(p["stop"], Decimal("4008.00"))
        self.assertEqual(p["risk"], Decimal("18.50"))
        self.assertEqual(p["target"], Decimal("4045.00"))
        self.assertEqual(p["reward_risk"], Decimal("1.00"))
        self.assertEqual(p["target_label"], "1R (one times the risk)")

    def test_the_default_2r(self):
        p = c(4026.50, "or-mid")
        self.assertEqual(p["target"], Decimal("4063.50"))
        self.assertEqual(p["target_label"], "2R (two times the risk)")

    def test_a_stop_rounds_down_to_the_tick_not_the_paisa(self):
        # midpoint 4008.035 is not a sendable price: down to 4008.00
        p = c(4026.50, "or-mid", "1R", oh=4020.07)
        self.assertEqual(p["stop"], Decimal("4008.00"))
        self.assertIn("rounded down to the tick", p["stop_detail"])

    def test_a_stop_price_is_tick_floored_too(self):
        # 993.33 is not sendable, so the stop becomes 993.30 and the risk 6.70
        p = c(1000.00, "price:993.33", "2R")
        self.assertEqual(p["stop"], Decimal("993.30"))
        self.assertEqual(p["risk"], Decimal("6.70"))

    def test_a_target_rounds_half_up_to_the_tick(self):
        # risk 6.65, so 1.5R is 9.975 over the entry: 1009.975 rounds to 1010.00
        p = c(1000.00, "price:993.35", "1.5R")
        self.assertEqual(p["risk"], Decimal("6.65"))
        self.assertEqual(p["target"], Decimal("1010.00"))

    def test_every_price_out_is_on_a_tradable_tick(self):
        for stop in ("or-mid", "or-low", "price:4000.01", "pct:1.3"):
            p = c(4026.50, stop, "1.5R")
            for key in ("stop", "target"):
                cents = int((p[key] * 100) % 5)
                self.assertEqual(cents, 0, "%s %s = %s" % (stop, key, p[key]))

    def test_other_stops(self):
        self.assertEqual(c(4026.50, "or-low", "1R")["stop"], Decimal("3996.00"))
        self.assertEqual(c(4026.50, "price:4000", "1R")["stop"], Decimal("4000.00"))
        p = c(4000.00, "pct:1", "1R")
        self.assertEqual(p["stop"], Decimal("3960.00"))
        self.assertEqual(p["risk"], Decimal("40.00"))

    def test_atr_stop(self):
        p = c(4026.50, "atr:1.5", "1R", atr=13.2143, n=7)
        self.assertEqual(p["stop"], Decimal("4006.65"))
        self.assertIn("fewer than 14 so far", p["stop_detail"])

    def test_rupee_signs_are_accepted_in_a_price(self):
        self.assertEqual(exitp.parse_stop("price:₹4,000.50")[1], Decimal("4000.50"))
        self.assertEqual(exitp.parse_target("price:₹4,100")[1], Decimal("4100"))

    def test_refusals_say_why(self):
        cases = [
            (("or-mid", "1R", 4020.00, 4020.00), "must be above its low"),
            (("price:4030", "1R", 4020.00, 3996.00), "at or above the entry"),
            (("or-mid", "price:4000", 4020.00, 3996.00), "at or below the entry"),
            (("price:4026.45", "1R", 4020.00, 3996.00), "too tight to survive the spread"),
        ]
        for (stop, target, oh, ol), want in cases:
            with self.assertRaises(exitp.PlanError) as cm:
                c(4026.50, stop, target, oh, ol)
            self.assertIn(want, str(cm.exception), "%s %s" % (stop, target))

    def test_a_stop_inside_one_tick_is_refused_at_a_band_boundary(self):
        # 250.00 ticks at 0.05, 249.99 at 0.01, so the risk is under one tick
        with self.assertRaises(exitp.PlanError) as cm:
            c(250.00, "price:249.99", "1R")
        self.assertIn("less than one tick", str(cm.exception))

    def test_money_is_rupees_with_indian_grouping(self):
        self.assertEqual(exitp.money(1234567.89), "₹12,34,567.89")
        self.assertEqual(exitp.money(4008), "₹4,008.00")       # a price keeps its paise
        self.assertEqual(exitp.amount(5000), "₹5,000")         # an amount drops them
        self.assertNotIn("$", exitp.money(100))


class SquareOff(unittest.TestCase):
    """India-specific: the broker closes an open intraday position itself."""

    def test_flat_by_defaults_before_the_broker_square_off(self):
        t, note = exitp.resolve_flat_by(None, None, {}, None)
        self.assertEqual(t, "15:15")
        self.assertEqual(note, "")

    def test_an_explicit_flat_by_at_the_square_off_is_refused(self):
        with self.assertRaises(exitp.PlanError) as cm:
            exitp.resolve_flat_by(None, "15:25", {}, None)
        self.assertIn("square-off", str(cm.exception))
        self.assertIn("15:20", str(cm.exception))

    def test_a_configured_flat_by_past_the_square_off_is_pulled_back(self):
        t, note = exitp.resolve_flat_by(None, None, {"flat_by": "15:28"}, None)
        self.assertEqual(t, "15:15")
        self.assertIn("pulled back", note)

    def test_an_earlier_flat_by_is_left_alone(self):
        t, note = exitp.resolve_flat_by(None, "14:30", {}, None)
        self.assertEqual(t, "14:30")
        self.assertEqual(note, "")

    def test_an_entry_after_the_flat_by_has_no_time_to_hold(self):
        import datetime
        with self.assertRaises(exitp.PlanError) as cm:
            exitp.resolve_flat_by(None, "15:10", {}, datetime.time(15, 12))
        self.assertIn("no time left", str(cm.exception))

    def test_the_us_early_close_logic_is_gone(self):
        src = open(os.path.join(ROOT, "trader-exit", "exit.py"), encoding="utf-8").read()
        for gone in ("NYSE_HOLIDAYS", "NYSE_EARLY_CLOSE", "eastern_offset", "1:00 p.m."):
            self.assertNotIn(gone, src, gone)


class Costs(unittest.TestCase):
    def test_net_reward_risk_is_worse_than_gross(self):
        ce = exitp.cost_estimate(4026.50, 4008.00, 4063.50, {"max_loss_per_trade_inr": 1000})
        self.assertIsNotNone(ce)
        self.assertEqual(ce["qty"], 54)                 # 1000 // 18.50
        self.assertLess(ce["net_reward_risk"], 2.0)     # gross is 2.0
        self.assertEqual(ce["net_reward_risk"], 1.67)
        self.assertTrue(ce["indicative"])

    def test_charges_push_the_real_loss_past_the_configured_max(self):
        ce = exitp.cost_estimate(4026.50, 4008.00, 4063.50, {"max_loss_per_trade_inr": 1000})
        self.assertLess(ce["net_loss"], -1000)          # worse than the limit, once charges land
        self.assertGreater(ce["costs_round_trip"], 0)

    def test_breakeven_sits_above_the_entry(self):
        ce = exitp.cost_estimate(4026.50, 4008.00, 4063.50, {"max_loss_per_trade_inr": 1000})
        self.assertGreater(ce["breakeven"], 4026.50)

    def test_without_a_max_loss_there_is_nothing_honest_to_compute(self):
        self.assertIsNone(exitp.cost_estimate(4026.50, 4008.00, 4063.50, {}))

    def test_a_tighter_stop_gives_costs_a_bigger_bite(self):
        wide = exitp.cost_estimate(4026.50, 3996.00, 4100.00, {"max_loss_per_trade_inr": 1000})
        tight = exitp.cost_estimate(4026.50, 4020.00, 4040.00, {"max_loss_per_trade_inr": 1000})
        self.assertGreater(tight["cost_as_r"], wide["cost_as_r"])


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.mkdir("trader")

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def write(self, path, doc):
        with open(path, "w") as fh:
            json.dump(doc, fh)

    def plan(self, *args):
        return run(exitp, ["plan", "--now", "2026-11-02 09:46"] + list(args))

    def read_plan(self):
        with open(os.path.join("trader", "plan.json")) as fh:
            return json.load(fh)

    def test_trader_entry_writes_what_this_skill_reads(self):
        # the real chain: entry.py --write, then exit.py with no flags
        code, _, err = run(entry, ["check", "TCS", "--adapter", "offline", "--data", INDIA_FIX,
                                   "--now", "2026-11-02 09:51", "--write"])
        self.assertEqual(code, 0, err)
        code, text, err = self.plan("--target", "1R", "--write")
        self.assertEqual(code, 0, err)
        p = self.read_plan()
        self.assertEqual(p["entry"], 4026.50)
        self.assertEqual(p["stop"], 4008.00)
        self.assertEqual(p["target"], 4045.00)
        self.assertEqual(p["entry_time"], "09:50")

    def test_entry_json_to_plan_json(self):
        self.write(os.path.join("trader", "entry.json"), INDIA_ENTRY)
        code, text, err = self.plan("--stop", "or-mid", "--target", "1R", "--write")
        self.assertEqual(code, 0, err)
        p = self.read_plan()
        for k, v in EXPECT_1R.items():
            self.assertEqual(p[k], v, k)
        self.assertIn("Stop          ₹4,008.00  opening-range midpoint", text)
        self.assertIn("Target        ₹4,045.00  1R (one times the risk)", text)
        self.assertIn("Risk a share  ₹18.50 (0.46% of the entry)", text)
        self.assertIn("Flat by       15:15 IST", text)
        self.assertIn("squares off open intraday positions", text)
        self.assertIn("not a guarantee", text)
        self.assertNotIn("$", text)
        self.assertNotIn("pro" + "fit", text.lower())

    def test_defaults_give_2r(self):
        self.write(os.path.join("trader", "entry.json"), INDIA_ENTRY)
        self.plan("--write")
        self.assertEqual(self.read_plan()["target"], 4063.50)
        self.assertEqual(self.read_plan()["target_r"], 2.0)

    def test_manual_flags_and_json(self):
        code, text, err = self.plan("--symbol", "tcs", "--entry", "4026.50", "--or-high", "4020",
                                    "--or-low", "3996", "--time", "09:45", "--entry-bar", "6",
                                    "--date", "2026-11-02", "--target", "1R", "--json")
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        for k, v in EXPECT_1R.items():
            self.assertEqual(p[k], v, k)
        self.assertEqual(p["tax_note"][:9], "Intraday ")

    def test_atr_stop_through_the_offline_adapter(self):
        self.write(os.path.join("trader", "entry.json"), INDIA_ENTRY)
        code, text, err = self.plan("--stop", "atr:1.5", "--adapter", "offline",
                                    "--data", INDIA_FIX, "--json")
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        self.assertEqual(p["stop_rule"], "atr")
        # the 09:45 bar closes at 09:50, after the --now of 09:46, so it is
        # not used: a stop must never be set off a bar that can still move
        self.assertEqual(p["atr_bars"], 6)
        self.assertLess(p["stop"], p["entry"])
        self.assertEqual(p["stop"] * 100 % 5, 0)        # on a tradable tick

    def test_a_cost_line_appears_once_a_max_loss_is_configured(self):
        self.write(os.path.join("trader", "entry.json"), INDIA_ENTRY)
        self.write(os.path.join("trader", "config.json"), {"max_loss_per_trade_inr": 1000})
        code, text, err = self.plan("--target", "2R")
        self.assertEqual(code, 0, err)
        self.assertIn("Net of costs", text)
        self.assertIn("54 shares", text)
        self.assertIn("Indicative", text)

    def test_refusals_from_the_cli(self):
        self.write(os.path.join("trader", "entry.json"), INDIA_ENTRY)
        code, text, err = self.plan("--flat-by", "15:25")
        self.assertEqual(code, 1)
        self.assertIn("square-off", text + err)
        code, text, err = self.plan("--stop", "price:4030")
        self.assertEqual(code, 1)
        self.assertIn("at or above the entry", text + err)

    def test_a_waiting_entry_needs_an_explicit_price(self):
        waiting = dict(INDIA_ENTRY, status="waiting")
        self.write(os.path.join("trader", "entry.json"), waiting)
        code, text, _ = self.plan()
        self.assertEqual(code, 1)
        code, _, err = self.plan("--entry", "4026.50")
        self.assertEqual(code, 0, err)

    def test_the_calendar_warning_reaches_the_user(self):
        self.write(os.path.join("trader", "entry.json"), INDIA_ENTRY)
        code, text, _ = self.plan()
        self.assertIn("Calendar:", text)
        self.assertIn("not been verified", text)

    def test_show_without_a_plan(self):
        code, text, _ = run(exitp, ["show"])
        self.assertEqual(code, 1)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-exit", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-exit\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertEqual(text[text.index("## Safety"):], risk[risk.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("before the entry", "a stop is a plan, not a guarantee",
                     "day trading loses most people money", "/trader-timemachine",
                     "/trader-budget", "longs only", "too tight to survive the spread",
                     "4008.00", "4045.00", "18.50", "4063.50", "square-off", "nse tick"):
            self.assertIn(must, low, must)
        for never in ("\u2014", "tick " + "by tick", "real " + "time", "pro" + "fit",
                      "nyse", "pattern day", "alpaca"):
            self.assertNotIn(never, low, never)
        import re as _re
        for pattern, why in ((r"\$[0-9]", "a dollar amount"),
                             (r"\bcents?\b", "US cents"),
                             (r"\b(9:30|16:00|15:55)\b", "a US session time")):
            self.assertIsNone(_re.search(pattern, low), "%s is left in the doc" % why)


if __name__ == "__main__":
    unittest.main()
