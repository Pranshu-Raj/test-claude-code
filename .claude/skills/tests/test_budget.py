"""trader-budget tests: INR sizing, whole shares, costs inside the max loss.

Worked example throughout, from the SYNTHETIC TCS plan:
    entry 4026.50, stop 4008.00, target 4063.50, so 18.50 a share at risk
    a 1,000 max loss gives 54 shares GROSS, but 54 loses 1,122.56 at the
    stop once charges land, so the answer is 47 (983.15 at the stop)
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT

size = load("trader-budget", "size")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from config import india_market as mkt

PLAN = {"created_by": "trader-exit", "symbol": "TCS", "side": "buy", "date": "2026-11-02",
        "setup": "orb", "entry": 4026.50, "entry_time": "09:50", "interval": "5m",
        "stop": 4008.00, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
        "target": 4063.50, "target_r": 2.0, "target_label": "2R (two times the risk)",
        "risk_per_share": 18.50, "reward_risk": 2.0, "flat_by": "15:15"}

CFG = {"budget": 500000, "max_loss_per_trade_inr": 1000, "max_position_pct": 100,
       "max_order_inr": 500000, "cash_buffer_pct": 0, "exchange": "NSE",
       "product": "MIS", "broker": "kite"}


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = size.main(argv)
    return code, out.getvalue(), err.getvalue()


def sized(cfg=None, **kw):
    c = dict(CFG)
    c.update(cfg or {})
    return size.size_trade(PLAN, c, today="2026-11-02", **kw)


class CostAwareSizing(unittest.TestCase):
    def test_the_gross_count(self):
        self.assertEqual(size.shares_for(1000, 18.50), 54)

    def test_the_gross_count_breaks_the_limit_once_charges_land(self):
        self.assertGreater(size.net_loss_at_stop(54, 4026.50, 4008.00), 1000)
        self.assertAlmostEqual(size.net_loss_at_stop(54, 4026.50, 4008.00), 1122.56, places=2)

    def test_the_net_count_holds_the_limit(self):
        n = size.shares_for_net_loss(1000, 4026.50, 4008.00)
        self.assertEqual(n, 47)
        self.assertLessEqual(size.net_loss_at_stop(n, 4026.50, 4008.00), 1000)

    def test_the_net_count_is_the_true_boundary(self):
        n = size.shares_for_net_loss(1000, 4026.50, 4008.00)
        self.assertGreater(size.net_loss_at_stop(n + 1, 4026.50, 4008.00), 1000)

    def test_the_max_loss_limit_uses_the_net_count_by_default(self):
        s = sized()
        ml = [l for l in s["limits"] if l["name"] == "max loss"][0]
        self.assertEqual(ml["shares"], 47)
        self.assertEqual(ml["gross_shares"], 54)
        self.assertTrue(ml["net_sized"])
        self.assertIn("cut to 47", ml["how"])

    def test_gross_sizing_can_be_asked_for_and_says_so(self):
        s = sized({"max_loss_includes_costs": False})
        ml = [l for l in s["limits"] if l["name"] == "max loss"][0]
        self.assertEqual(ml["shares"], 54)
        self.assertFalse(ml["net_sized"])
        self.assertIn("GROSS", ml["how"])
        self.assertGreater(s["net_loss_at_stop"], s["max_loss_per_trade_inr"])

    def test_a_wider_stop_costs_fewer_shares_to_the_charges(self):
        tight = size.shares_for_net_loss(1000, 4026.50, 4020.00)
        wide = size.shares_for_net_loss(1000, 4026.50, 3926.50)
        self.assertGreater(tight, wide)           # more shares fit a tighter stop
        for n, (e, st) in ((tight, (4026.50, 4020.00)), (wide, (4026.50, 3926.50))):
            self.assertLessEqual(size.net_loss_at_stop(n, e, st), 1000)

    def test_one_share_risking_more_than_the_max_loss_is_a_refusal(self):
        s = sized({"max_loss_per_trade_inr": 10})
        self.assertEqual(s["qty"], 0)
        self.assertIn("No order", s["refusal"])
        self.assertIsNone(size.bracket_order(s))


class Limits(unittest.TestCase):
    def test_the_position_cap_can_bind_instead(self):
        s = sized({"budget": 200000, "max_position_pct": 75})
        self.assertEqual(s["bound_by"], "position cap")
        self.assertEqual(s["qty"], 37)

    def test_the_cash_buffer_is_held_back(self):
        s = sized({"budget": 200000, "max_position_pct": 100, "cash_buffer_pct": 10})
        cash = [l for l in s["limits"] if l["name"] == "cash"][0]
        self.assertEqual(cash["inr"], 180000.0)

    def test_whole_shares_only(self):
        s = sized()
        self.assertEqual(s["qty"], int(s["qty"]))
        self.assertNotIn("fractional", size.DEFAULTS)

    def test_the_config_keys_are_rupees_not_dollars(self):
        for k in ("max_order_inr", "min_order_inr", "max_loss_per_trade_inr", "max_daily_loss_inr"):
            self.assertIn(k, size.DEFAULTS, k)
        for gone in ("max_order_usd", "max_loss_per_trade_usd", "pdt_guard"):
            self.assertNotIn(gone, size.DEFAULTS, gone)

    def test_the_currency_and_venue_travel_with_the_sizing(self):
        s = sized()
        self.assertEqual(s["currency"], "INR")
        self.assertEqual(s["exchange"], "NSE")
        self.assertEqual(s["product"], "MIS")


class Legs(unittest.TestCase):
    def test_three_legs_not_one_bracket(self):
        o = size.bracket_order(sized())
        roles = [l["role"] for l in o["legs"]]
        self.assertEqual(roles, ["entry", "stop", "target"])
        self.assertEqual(o["place_tool"], "place_order")
        self.assertEqual(o["broker"], "kite")

    def test_the_legs_carry_kite_field_names(self):
        o = size.bracket_order(sized())
        entry = o["legs"][0]["params"]
        self.assertEqual(entry["tradingsymbol"], "TCS")
        self.assertEqual(entry["transaction_type"], "BUY")
        self.assertEqual(entry["product"], "MIS")
        self.assertEqual(entry["exchange"], "NSE")
        self.assertEqual(entry["order_type"], "MARKET")
        self.assertEqual(entry["validity"], "DAY")
        for gone in ("symbol", "side", "time_in_force", "order_class",
                     "take_profit_limit_price", "stop_loss_stop_price"):
            self.assertNotIn(gone, entry, gone)

    def test_the_stop_is_sl_m_and_the_target_is_a_limit(self):
        o = size.bracket_order(sized())
        stop, target = o["legs"][1]["params"], o["legs"][2]["params"]
        self.assertEqual(stop["order_type"], "SL-M")
        self.assertEqual(stop["trigger_price"], 4008.00)
        self.assertEqual(stop["transaction_type"], "SELL")
        self.assertEqual(target["order_type"], "LIMIT")
        self.assertEqual(target["price"], 4063.50)

    def test_the_legs_wait_for_the_entry_and_warn_they_are_not_linked(self):
        o = size.bracket_order(sized())
        for leg in o["legs"][1:]:
            self.assertEqual(leg["after"], "the entry fills")
            self.assertIn("NOT", leg["note"])
        self.assertIn("CANCEL the other", o["oco_warning"])

    def test_every_leg_price_is_on_a_tradable_tick(self):
        o = size.bracket_order(sized({"max_loss_per_trade_inr": 5000}))
        for leg in o["legs"]:
            for key in ("price", "trigger_price"):
                v = leg["params"].get(key)
                if v is not None:
                    self.assertEqual(round(v * 100) % 5, 0, "%s %s" % (leg["role"], key))

    def test_a_limit_entry_rounds_down_so_you_never_pay_above_it(self):
        s = sized(limit=4026.53)
        o = size.bracket_order(s)
        self.assertEqual(o["legs"][0]["params"]["order_type"], "LIMIT")
        self.assertEqual(o["legs"][0]["params"]["price"], 4026.50)


class Report(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.mkdir("trader")
        with open(os.path.join("trader", "plan.json"), "w") as fh:
            json.dump(PLAN, fh)
        with open(os.path.join("trader", "config.json"), "w") as fh:
            json.dump(CFG, fh)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def test_the_report_is_rupees_and_names_both_losses(self):
        code, text, err = run(["--now", "2026-11-02 10:00"])
        self.assertEqual(code, 0, err)
        self.assertIn("₹", text)
        self.assertNotIn("$", text)
        self.assertIn("gross", text)
        self.assertIn("net of charges", text)
        self.assertIn("47 shares", text)

    def test_the_report_breaks_the_charges_down(self):
        code, text, _ = run(["--now", "2026-11-02 10:00"])
        for charge in ("Brokerage", "STT", "Stamp duty", "GST", "Exchange txn"):
            self.assertIn(charge, text, charge)
        self.assertIn("sell leg only", text)
        self.assertIn("buy leg only", text)

    def test_the_report_shows_three_legs_and_the_oco_warning(self):
        code, text, _ = run(["--now", "2026-11-02 10:00"])
        self.assertIn("THREE legs", text)
        self.assertIn("entry   buy", text)
        self.assertIn("stop    sell", text)
        self.assertIn("target  sell", text)
        self.assertIn("CANCEL the other", text)

    def test_the_report_names_the_square_off(self):
        code, text, _ = run(["--now", "2026-11-02 10:00"])
        self.assertIn("15:15 IST", text)
        self.assertIn("squares off", text)

    def test_the_report_carries_the_tax_note_and_the_disclaimer(self):
        code, text, _ = run(["--now", "2026-11-02 10:00"])
        self.assertIn("speculative business income", text)
        self.assertIn("not financial advice", text)

    def test_write_produces_orders_json_for_trader_risk(self):
        code, text, err = run(["--now", "2026-11-02 10:00", "--write"])
        self.assertEqual(code, 0, err)
        with open(os.path.join("trader", "orders.json")) as fh:
            doc = json.load(fh)
        self.assertEqual(len(doc["orders"]), 1)
        o = doc["orders"][0]
        self.assertEqual(o["qty"], "47")
        self.assertEqual(len(o["legs"]), 3)
        self.assertEqual(o["currency"], "INR")

    def test_json_output(self):
        code, text, err = run(["--now", "2026-11-02 10:00", "--json"])
        self.assertEqual(code, 0, err)
        doc = json.loads(text)
        self.assertEqual(doc["sizing"]["qty"], 47)
        self.assertEqual(doc["sizing"]["net_loss_at_stop"], 983.15)
        self.assertEqual(len(doc["orders"][0]["legs"]), 3)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-budget", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-budget\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertEqual(text[text.index("## Safety"):], risk[risk.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("whole shares", "max_loss_includes_costs", "three legs", "not linked",
                     "stt", "stamp duty", "gst", "47 shares", "nse tick", "not financial advice"):
            self.assertIn(must, low, must)
        for never in ("\u2014", "pattern day", "time_in_force", "order_class", "max_loss_per_trade_usd"):
            self.assertNotIn(never, low, never)
        import re as _re
        for pattern, why in ((r"\$[0-9]", "a dollar amount"), (r"\bcents?\b", "US cents")):
            self.assertIsNone(_re.search(pattern, low), "%s is left in the doc" % why)


if __name__ == "__main__":
    unittest.main()
