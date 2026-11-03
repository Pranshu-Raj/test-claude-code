"""trader-risk tests: the India rules, in rupees, charges included.

Worked example: TCS, entry 4026.50, stop 4008.00, target 4063.50, so 18.50 a
share at risk. Against a 1,000 max loss per trade:
    37 shares -> 684.50 gross,   784.00 net   -> PASS
    54 shares -> 999.00 gross, 1,122.56 net   -> BLOCK (the gross figure fits)
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

risk = load("trader-risk", "check")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from config import india_market as mkt

CFG = {"budget": 500000, "max_loss_per_trade_inr": 1000, "max_daily_loss_inr": 3000,
       "max_position_pct": 100, "max_order_inr": 500000, "min_order_inr": 500,
       "cash_buffer_pct": 0, "min_reward_risk": 1.0, "max_trades_per_day": 3,
       "exchange": "NSE", "product": "MIS"}
NOW = "2026-11-02 10:00"


def leg(role, **kw):
    prm = {"tradingsymbol": "TCS", "product": "MIS", "exchange": "NSE", "validity": "DAY",
           "variety": "regular"}
    prm.update(kw)
    return {"role": role, "params": prm}


def order(qty=37, stop=4008.00, target=4063.50, entry=4026.50, **over):
    o = {"symbol": "TCS", "side": "buy", "qty": str(qty), "type": "market",
         "product": "MIS", "exchange": "NSE",
         "legs": [leg("entry", order_type="MARKET", transaction_type="BUY", quantity=qty),
                  leg("stop", order_type="SL-M", transaction_type="SELL", quantity=qty,
                      trigger_price=stop),
                  leg("target", order_type="LIMIT", transaction_type="SELL", quantity=qty,
                      price=target)],
         "plan": {"entry": entry, "stop": stop, "target": target}}
    o.update(over)
    return o


def check(orders=None, cfg=None, **kw):
    c = dict(CFG)
    c.update(cfg or {})
    kw.setdefault("today", "2026-11-02")
    kw.setdefault("now", risk.parse_now(NOW))
    return risk.check(orders if orders is not None else [order()], c, **kw)


def verdict(res, i=0):
    return res["results"][i]


def reasons(res, i=0):
    return " | ".join(verdict(res, i)["reasons"])


class LossNetOfCharges(unittest.TestCase):
    def test_a_trade_inside_the_limit_passes(self):
        v = verdict(check())
        self.assertEqual(v["verdict"], "PASS", reasons(check()))
        self.assertEqual(v["max_loss"], 784.00)

    def test_the_gross_figure_fits_but_the_real_loss_blocks(self):
        res = check([order(qty=54)])
        self.assertEqual(verdict(res)["verdict"], "BLOCK")
        self.assertIn("loss:", reasons(res))
        self.assertIn("1,122.56", reasons(res))
        self.assertIn("charges", reasons(res))

    def test_gross_mode_lets_that_same_trade_through(self):
        res = check([order(qty=54)], {"max_loss_includes_costs": False})
        self.assertEqual(verdict(res)["verdict"], "PASS", reasons(res))
        self.assertEqual(verdict(res)["max_loss"], 999.00)

    def test_the_passing_trade_reports_the_charges_it_counted(self):
        v = verdict(check())
        info = " ".join(v.get("info") or [])
        self.assertIn("charges on the round trip", info)
        self.assertIn("784", info)

    def test_the_daily_loss_limit_is_also_net(self):
        # three different symbols, so the duplicate rule does not fire first.
        # 784 net each: two fit a 1,600 limit, the third does not.
        batch = [order(symbol=s) for s in ("TCS", "INFY", "WIPRO")]
        res = check(batch, {"max_daily_loss_inr": 1600, "max_trades_per_day": 9})
        verdicts = [o["verdict"] for o in res["results"]]
        self.assertEqual(verdicts[:2], ["PASS", "PASS"], reasons(res, 0) + reasons(res, 1))
        self.assertEqual(verdicts[2], "BLOCK")
        self.assertIn("daily loss", " ".join(res["results"][2]["reasons"]))


class Format(unittest.TestCase):
    def test_a_fractional_quantity_is_refused(self):
        o = order()
        o["qty"] = "10.5"
        res = check([o])
        self.assertIn("whole shares", reasons(res))
        self.assertIn("fractional", reasons(res))

    def test_a_missing_stop_leg_is_refused(self):
        o = order()
        o["legs"] = [l for l in o["legs"] if l["role"] != "stop"]
        o["plan"].pop("stop")
        res = check([o])
        self.assertEqual(verdict(res)["verdict"], "BLOCK")
        self.assertIn("stop", reasons(res))

    def test_an_sl_leg_without_a_trigger_price_is_refused(self):
        o = order()
        o["legs"][1]["params"].pop("trigger_price")
        res = check([o])
        self.assertIn("without a trigger_price", reasons(res))

    def test_a_limit_leg_without_a_price_is_refused(self):
        o = order()
        o["legs"][2]["params"].pop("price")
        res = check([o])
        self.assertIn("LIMIT without a price", reasons(res))

    def test_an_unknown_product_is_refused(self):
        try:
            res = check([order(product="NRML")])
        except (risk.RiskError, ValueError) as e:
            self.assertIn("product must be", str(e))
        else:
            self.assertIn("product must be", reasons(res))

    def test_an_unknown_exchange_is_refused(self):
        res = check([order(exchange="NASDAQ")])
        self.assertIn("exchange must be", reasons(res))

    def test_no_us_order_fields_are_expected_any_more(self):
        src = open(os.path.join(ROOT, "trader-risk", "check.py"), encoding="utf-8").read()
        for gone in ("ALPACA_FIELDS", "time_in_force", "order_class", "extended_hours",
                     "NYSE", "eastern_offset", "max_loss_per_trade_usd"):
            self.assertNotIn(gone, src, gone)
        # pdt_guard may only survive as the note saying it is gone
        self.assertNotIn('c.get("pdt_guard")', src)


class Asset(unittest.TestCase):
    def test_an_fno_instrument_is_refused_while_derivatives_are_off(self):
        res = check([order(symbol="NIFTY26NOV24000CE")])
        self.assertIn("F&O", reasons(res))
        self.assertIn("later phase", reasons(res))

    def test_a_cash_equity_symbol_is_fine(self):
        for sym in ("TCS", "RELIANCE", "M&M", "BAJAJ-AUTO"):
            res = check([order(symbol=sym)])
            self.assertNotIn("asset:", reasons(res), sym)


class Shorting(unittest.TestCase):
    def test_an_overnight_cash_short_is_refused_outright(self):
        o = order(product="CNC")
        o["side"] = "sell"
        o["legs"][0]["params"]["transaction_type"] = "SELL"
        res = check([o], {"allow_short": True})
        self.assertIn("no overnight short in the cash segment", reasons(res))

    def test_an_intraday_short_is_allowed_when_the_config_permits(self):
        o = order()
        o["side"] = "sell"
        o["legs"][0]["params"]["transaction_type"] = "SELL"
        res = check([o], {"allow_short": True})
        self.assertNotIn("short:", reasons(res))
        warns = " ".join(verdict(res).get("warnings") or [])
        self.assertIn("squares it off", warns)

    def test_an_intraday_short_is_refused_when_the_config_forbids_it(self):
        o = order()
        o["side"] = "sell"
        o["legs"][0]["params"]["transaction_type"] = "SELL"
        res = check([o], {"allow_short": False})
        self.assertIn("shorting is off", reasons(res))

    def test_settlement_is_reported_as_t_plus_1(self):
        self.assertEqual(check()["today"]["settlement"], "T+1")


class Window(unittest.TestCase):
    def test_before_the_open_is_closed(self):
        res = check(now=risk.parse_now("2026-11-02 09:05"))
        self.assertIn("the market is closed", reasons(res))
        self.assertIn("09:15", reasons(res))

    def test_after_the_close_is_closed(self):
        res = check(now=risk.parse_now("2026-11-02 15:45"))
        self.assertIn("the market is closed", reasons(res))

    def test_a_holiday_is_closed(self):
        res = check(today="2026-01-26", now=risk.parse_now("2026-01-26 10:00"))
        self.assertIn("closed today", reasons(res))
        self.assertIn("Republic Day", reasons(res))

    def test_an_entry_window_past_the_square_off_is_pulled_back(self):
        res = check(cfg={"no_entries_after": "15:28"},
                    now=risk.parse_now("2026-11-02 15:22"))
        self.assertIn("no new entries after", reasons(res))

    def test_an_entry_inside_the_window_is_fine(self):
        res = check(now=risk.parse_now("2026-11-02 10:00"))
        self.assertNotIn("window:", reasons(res))

    def test_the_window_is_ist_not_new_york(self):
        res = check(now=risk.parse_now("2026-11-02 09:05"))
        self.assertIn("IST", reasons(res))
        self.assertNotIn("New York", reasons(res))


class Exits(unittest.TestCase):
    def test_an_exit_is_never_blocked_by_a_day_limit(self):
        o = {"symbol": "TCS", "side": "sell", "qty": "37", "type": "market",
             "product": "MIS", "exchange": "NSE",
             "legs": [leg("entry", order_type="MARKET", transaction_type="SELL", quantity=37)]}
        res = check([o], {"max_trades_per_day": 0},
                    portfolio={"cash": 500000, "positions": [{"ticker": "TCS", "shares": 37}]})
        self.assertEqual(verdict(res)["verdict"], "PASS", reasons(res))


class Report(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.makedirs(os.path.join("trader", "state"))
        with open(os.path.join("trader", "orders.json"), "w") as fh:
            json.dump({"created_by": "trader-budget", "orders": [order()]}, fh)
        with open(os.path.join("trader", "config.json"), "w") as fh:
            json.dump(CFG, fh)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = risk.main(["--now", NOW] + list(args))
        return code, out.getvalue(), err.getvalue()

    def test_a_pass_is_recorded_so_trader_broker_can_trust_it(self):
        code, text, err = self.run_cli()
        self.assertEqual(code, 0, err + text)
        self.assertIn("PASS", text)
        with open(os.path.join("trader", "state", "risk.json")) as fh:
            state = json.load(fh)
        self.assertTrue(state["results"])
        self.assertEqual(state["results"][0]["verdict"], "PASS")
        self.assertTrue(state["results"][0]["fingerprint"])
        self.assertEqual(state["now_ist"], "2026-11-02 10:00")

    def test_the_report_is_rupees_and_ist(self):
        code, text, _ = self.run_cli()
        self.assertIn("₹", text)
        self.assertIn("IST", text)
        self.assertNotIn("$", text)
        self.assertNotIn("New York", text)

    def test_the_report_names_both_losses(self):
        code, text, _ = self.run_cli()
        self.assertIn("784", text)          # net
        self.assertIn("684.50", text)       # gross
        self.assertIn("not financial advice", text)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-risk\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertIn("## Safety", text)
        low = " ".join(text.lower().split())
        for must in ("charges included", "max_loss_per_trade_inr", "t+1", "square-off",
                     "overnight short", "1,122.56", "not financial advice",
                     "day trading loses most people money"):
            self.assertIn(must, low, must)
        for never in ("\u2014", "pattern day", "pdt_guard\": true", "nyse", "time_in_force"):
            self.assertNotIn(never, low, never)
        import re as _re
        for pattern, why in ((r"\$[0-9]", "a dollar amount"), (r"\bcents?\b", "US cents")):
            self.assertIsNone(_re.search(pattern, low), "%s is left in the doc" % why)


if __name__ == "__main__":
    unittest.main()
