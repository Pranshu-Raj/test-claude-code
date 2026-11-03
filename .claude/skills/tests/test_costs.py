"""The Indian intraday cost calculator.

Expected values here are computed by hand from the rates in
india_market.COSTS, not by calling the module under test.

Worked example used throughout: 10 shares, buy 1500, sell 1520, MIS, NSE.
  buy leg  turnover 15000: brokerage min(20, 0.03%*15000=4.50)=4.50
                           STT 0 (intraday buy), exch 0.4455->0.45,
                           SEBI 0.015->0.02, stamp 0.00003*15000=0.45,
                           GST 0.18*(4.50+0.45+0.02)=0.8946->0.89
                           total 6.31
  sell leg turnover 15200: brokerage min(20, 4.56)=4.56, STT 0.025%*15200=3.80,
                           exch 0.45144->0.45, SEBI 0.0152->0.02, stamp 0,
                           GST 0.18*(4.56+0.45+0.02)=0.9054->0.91
                           total 9.74
  round trip 16.05
"""

import sys
import unittest

from _load import ROOT

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import costs, india_market as mkt


class Legs(unittest.TestCase):
    def test_intraday_buy_leg(self):
        leg = costs.leg_charges("buy", 10, 1500, mkt.PRODUCT_INTRADAY, mkt.NSE)
        self.assertEqual(leg["brokerage"], 4.50)
        self.assertEqual(leg["stt"], 0.0)            # intraday STT is sell-side only
        self.assertEqual(leg["exchange_txn"], 0.45)
        self.assertEqual(leg["sebi_turnover"], 0.02)
        self.assertEqual(leg["stamp_duty"], 0.45)    # buy-side only
        self.assertEqual(leg["gst"], 0.89)
        self.assertEqual(leg["dp_charge"], 0.0)      # never on intraday
        self.assertEqual(leg["total"], 6.31)

    def test_intraday_sell_leg_carries_the_stt_and_no_stamp_duty(self):
        leg = costs.leg_charges("sell", 10, 1520, mkt.PRODUCT_INTRADAY, mkt.NSE)
        self.assertEqual(leg["brokerage"], 4.56)
        self.assertEqual(leg["stt"], 3.80)
        self.assertEqual(leg["stamp_duty"], 0.0)
        self.assertEqual(leg["gst"], 0.91)
        self.assertEqual(leg["total"], 9.74)

    def test_the_two_legs_do_not_cost_the_same(self):
        # the asymmetry the US pack has no concept of
        b = costs.leg_charges("buy", 10, 1500)
        s = costs.leg_charges("sell", 10, 1500)
        self.assertNotEqual(b["total"], s["total"])
        self.assertGreater(s["total"], b["total"])

    def test_gst_is_not_charged_on_stt_or_stamp_duty(self):
        leg = costs.leg_charges("sell", 10, 1520)
        base = leg["brokerage"] + leg["exchange_txn"] + leg["sebi_turnover"]
        self.assertAlmostEqual(leg["gst"], round(base * 0.18, 2), places=2)
        self.assertLess(leg["gst"], (base + leg["stt"]) * 0.18)

    def test_brokerage_is_capped_at_the_flat_fee(self):
        # 0.03% passes 20 rupees above a turnover of 66,666.67
        self.assertEqual(costs.brokerage(15000), 4.50)
        self.assertEqual(costs.brokerage(100000), 20.0)
        self.assertEqual(costs.brokerage(10000000), 20.0)

    def test_bse_and_nse_exchange_charges_differ(self):
        n = costs.leg_charges("buy", 100, 1500, mkt.PRODUCT_INTRADAY, mkt.NSE)
        b = costs.leg_charges("buy", 100, 1500, mkt.PRODUCT_INTRADAY, mkt.BSE)
        self.assertNotEqual(n["exchange_txn"], b["exchange_txn"])

    def test_delivery_charges_stt_on_both_sides_and_a_dp_fee_on_the_sell(self):
        buy = costs.leg_charges("buy", 10, 1500, mkt.PRODUCT_DELIVERY)
        sell = costs.leg_charges("sell", 10, 1520, mkt.PRODUCT_DELIVERY)
        self.assertEqual(buy["stt"], 15.0)           # 0.1% of 15000
        self.assertEqual(sell["stt"], 15.2)          # 0.1% of 15200
        self.assertEqual(buy["dp_charge"], 0.0)
        self.assertEqual(sell["dp_charge"], 13.5)
        self.assertEqual(buy["stamp_duty"], 2.25)    # 0.015% of 15000

    def test_bad_side_and_product_are_refused(self):
        with self.assertRaises(ValueError):
            costs.leg_charges("hold", 10, 1500)
        with self.assertRaises(ValueError):
            costs.leg_charges("buy", 10, 1500, "NRML")


class RoundTrip(unittest.TestCase):
    def test_round_trip_is_the_sum_of_the_legs(self):
        rt = costs.round_trip(1500, 1520, 10)
        self.assertEqual(rt["buy"]["total"], 6.31)
        self.assertEqual(rt["sell"]["total"], 9.74)
        self.assertEqual(rt["total"], 16.05)

    def test_a_long_and_a_short_over_the_same_prices_cost_the_same(self):
        long_t = costs.net_pnl(1500, 1520, 10, side="buy")
        short_t = costs.net_pnl(1520, 1500, 10, side="sell")
        self.assertEqual(long_t["costs"], short_t["costs"])
        self.assertEqual(long_t["gross"], short_t["gross"])

    def test_net_is_gross_minus_costs(self):
        t = costs.net_pnl(1500, 1520, 10)
        self.assertEqual(t["gross"], 200.0)
        self.assertEqual(t["costs"], 16.05)
        self.assertEqual(t["net"], 183.95)

    def test_a_losing_trade_loses_more_than_the_price_move(self):
        t = costs.net_pnl(1500, 1495, 10)
        self.assertEqual(t["gross"], -50.0)
        self.assertLess(t["net"], -50.0)

    def test_every_report_carries_the_speculative_income_note(self):
        self.assertIn("speculative", costs.net_pnl(1500, 1520, 10)["tax_note"])


class Impact(unittest.TestCase):
    def test_costs_eat_a_real_slice_of_one_r(self):
        # 10 shares, 5 rupee stop, so 50 of risk. Charges are priced at the
        # trade's own level (buy 1495, sell 1500): 6.27 + 9.61 = 15.88.
        r = costs.cost_as_r(1500, 1495, 10)
        self.assertAlmostEqual(r, 15.88 / 50.0, places=4)
        # nearly a third of a one-R win goes in charges at this size, which is
        # exactly what the US pack's zero-cost model hides
        self.assertGreater(r, 0.3)

    def test_a_wider_stop_dilutes_the_cost_in_r_terms(self):
        tight = costs.cost_as_r(1500, 1495, 10)
        wide = costs.cost_as_r(1500, 1450, 10)
        self.assertLess(wide, tight)

    def test_cost_as_r_needs_a_real_stop(self):
        self.assertIsNone(costs.cost_as_r(1500, 1500, 10))
        self.assertIsNone(costs.cost_as_r(1500, 1495, 0))

    def test_breakeven_sits_above_the_entry_for_a_long(self):
        be = costs.breakeven_price(1500, 10, side="buy")
        self.assertGreater(be, 1500)
        self.assertLess(be, 1502)
        self.assertEqual(be, mkt.round_to_tick(be))   # a tradable price

    def test_breakeven_sits_below_the_entry_for_a_short(self):
        self.assertLess(costs.breakeven_price(1500, 10, side="sell"), 1500)

    def test_at_breakeven_the_trade_is_not_a_loss(self):
        be = costs.breakeven_price(1500, 10, side="buy")
        self.assertGreaterEqual(costs.net_pnl(1500, be, 10)["net"], 0)


class Overrides(unittest.TestCase):
    def test_rates_can_be_overridden_per_broker(self):
        zero = costs.leg_charges("buy", 10, 1500, rates={"brokerage_flat_inr": 0.0,
                                                         "brokerage_mode": "flat"})
        self.assertEqual(zero["brokerage"], 0.0)
        self.assertEqual(zero["gst"], 0.08)   # 0.18*(0+0.45+0.02)=0.0846

    def test_overriding_does_not_mutate_the_shared_config(self):
        before = dict(mkt.COSTS)
        costs.leg_charges("buy", 10, 1500, rates={"gst_pct": 0.5})
        self.assertEqual(dict(mkt.COSTS), before)


class Reports(unittest.TestCase):
    def test_summary_line_is_in_rupees(self):
        line = costs.summary_line(costs.net_pnl(1500, 1520, 10))
        self.assertIn("₹200", line)
        self.assertIn("₹183.95", line)
        self.assertNotIn("$", line)

    def test_breakdown_names_the_asymmetric_charges_and_hides_zeroes(self):
        lines = costs.breakdown_lines(costs.round_trip(1500, 1520, 10))
        text = "\n".join(lines)
        self.assertIn("sell leg only", text)      # STT
        self.assertIn("buy leg only", text)       # stamp duty
        self.assertNotIn("Depository", text)      # zero on intraday, so omitted
        self.assertIn("₹16.05", text)


if __name__ == "__main__":
    unittest.main()
