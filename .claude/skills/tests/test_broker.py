"""trader-broker tests: the three gates, three legs, rupees, square-off."""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT

ticket = load("trader-broker", "ticket")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from config import india_market as mkt

NOW = "2026-11-02 10:05"
CFG = {"budget": 500000, "paper": True, "broker": "kite", "exchange": "NSE", "product": "MIS",
       "max_loss_per_trade_inr": 1000, "flat_by": "15:15"}


def leg(role, **kw):
    prm = {"tradingsymbol": "TCS", "product": "MIS", "exchange": "NSE", "validity": "DAY",
           "variety": "regular"}
    prm.update(kw)
    return {"role": role, "params": prm}


def order(qty=37):
    return {"symbol": "TCS", "side": "buy", "qty": str(qty), "type": "market", "product": "MIS",
            "exchange": "NSE", "validity": "DAY", "broker": "kite",
            "legs": [leg("entry", order_type="MARKET", transaction_type="BUY", quantity=qty),
                     leg("stop", order_type="SL-M", transaction_type="SELL", quantity=qty,
                         trigger_price=4008.00),
                     leg("target", order_type="LIMIT", transaction_type="SELL", quantity=qty,
                         price=4063.50)],
            "why": "opening-range breakout on TCS",
            "plan": {"entry": 4026.50, "stop": 4008.00, "target": 4063.50,
                     "net_loss_at_stop": 784.00, "max_loss": 684.50, "flat_by": "15:15"}}


class Legs(unittest.TestCase):
    def test_a_day_trade_is_recognised_by_its_legs_not_a_bracket_flag(self):
        self.assertTrue(ticket.is_bracket(order()))
        bare = order()
        bare["legs"] = [bare["legs"][0]]
        self.assertFalse(ticket.is_bracket(bare))

    def test_the_plain_ticket_names_both_exit_legs_and_when_they_go_in(self):
        parts = ticket.plain_parts(order())
        text = " / ".join(parts)
        self.assertIn("stop SL-M ₹4,008.00", text)
        self.assertIn("target LIMIT ₹4,063.50", text)
        self.assertEqual(text.count("placed after the entry fills"), 2)

    def test_the_ticket_warns_they_are_not_linked(self):
        text = ticket.plain(order())
        self.assertIn("NOT linked", text)
        self.assertIn("CANCEL the other", text)

    def test_the_max_loss_shown_is_the_one_with_charges(self):
        loss, stop = ticket.max_loss(order())
        self.assertEqual(loss, 784.00)        # not the 684.50 gross figure
        self.assertEqual(stop, 4008.00)
        self.assertIn("charges included", ticket.plain(order()))

    def test_prices_and_amounts_are_rupees(self):
        text = ticket.plain(order())
        self.assertIn("₹", text)
        self.assertNotIn("$", text)

    def test_the_product_and_exchange_are_on_the_ticket(self):
        self.assertIn("MIS on NSE", ticket.plain(order()))

    def test_no_us_order_plumbing_survives(self):
        src = open(os.path.join(ROOT, "trader-broker", "ticket.py"), encoding="utf-8").read()
        for gone in ("eastern_offset", "NYSE", "place_stock_order", "order_class",
                     "take_profit_limit_price", "stop_loss_stop_price"):
            self.assertNotIn(gone, src, gone)

    def test_risk_and_broker_compute_the_same_fingerprint(self):
        risk = load("trader-risk", "check")
        self.assertEqual(ticket.fingerprint(order()), risk.fingerprint(order()))

    def test_the_fingerprint_changes_with_anything_that_matters(self):
        base = ticket.fingerprint(order())
        self.assertNotEqual(base, ticket.fingerprint(order(qty=40)))
        other = order()
        other["legs"][1]["params"]["trigger_price"] = 4000.00
        self.assertNotEqual(base, ticket.fingerprint(other))
        venue = order()
        venue["exchange"] = "BSE"
        self.assertNotEqual(base, ticket.fingerprint(venue))


class Yes(unittest.TestCase):
    def test_a_plain_yes_counts(self):
        ok, why = ticket.parse_yes("yes", order())
        self.assertTrue(ok, why)

    def test_a_batch_word_does_not_count(self):
        for reply in ("yes to all", "do them all", "yes, place everything"):
            ok, why = ticket.parse_yes(reply, order())
            self.assertFalse(ok, reply)

    def test_a_hedged_reply_does_not_count(self):
        for reply in ("yes but wait", "maybe yes", "yes later", "no"):
            ok, _ = ticket.parse_yes(reply, order())
            self.assertFalse(ok, reply)

    def test_a_yes_naming_another_order_in_the_batch_does_not_count(self):
        ok, why = ticket.parse_yes("yes, buy INFY", order(), batch_symbols=("TCS", "INFY"))
        self.assertFalse(ok, why)
        self.assertIn("INFY", why)


class Flow(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.makedirs(os.path.join("trader", "state"))
        self.write(os.path.join("trader", "orders.json"),
                   {"created_by": "trader-budget", "orders": [order()]})
        self.write(os.path.join("trader", "config.json"), CFG)
        self.pass_risk()

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def write(self, path, doc):
        with open(path, "w") as fh:
            json.dump(doc, fh)

    def pass_risk(self, verdict="PASS", reasons=()):
        fp = ticket.fingerprint(order())
        self.write(os.path.join("trader", "state", "risk.json"),
                   {"date": "2026-11-02", "results": [{"index": 1, "fingerprint": fp,
                                                       "verdict": verdict, "kind": "entry",
                                                       "reasons": list(reasons), "warnings": []}]})

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = ticket.main(list(args) + ["--now", NOW])
        return code, out.getvalue(), err.getvalue()

    def test_a_risk_pass_makes_it_ready_and_names_the_broker(self):
        code, text, err = self.run_cli("show")
        self.assertEqual(code, 0, err)
        self.assertIn("READY", text)
        self.assertIn("kite", text)
        self.assertIn("PAPER account", text)
        self.assertIn("place_order once per leg", text)

    def test_blocked_orders_are_never_ready(self):
        self.pass_risk("BLOCK", ["loss: over your ₹1,000 max loss per trade"])
        code, text, _ = self.run_cli("show")
        self.assertIn("BLOCKED by trader-risk", text)
        self.assertIn("max loss", text)
        self.assertNotIn("READY", text)

    def test_the_full_yes_flow_records_the_reply(self):
        code, text, err = self.run_cli("approve", "--index", "1", "--reply", "yes")
        self.assertEqual(code, 0, err + text)
        with open(os.path.join("trader", "state", "broker.json")) as fh:
            state = json.load(fh)
        rec = state["orders"][-1]
        self.assertEqual(rec["status"], "approved")
        self.assertEqual(rec["reply"], "yes")
        self.assertIn("Allow once", text)

    def test_a_decline_is_not_an_approval(self):
        code, text, err = self.run_cli("approve", "--index", "1", "--reply", "no")
        self.assertNotEqual(code, 0, text)
        self.assertNotIn("Allow once", text)
        # nothing was recorded as approved, so it is still only risk-passed
        if os.path.exists(os.path.join("trader", "state", "broker.json")):
            with open(os.path.join("trader", "state", "broker.json")) as fh:
                state = json.load(fh)
            self.assertFalse([r for r in state.get("orders", [])
                              if r.get("status") == "approved"])

    def test_an_approved_order_is_not_ready_a_second_time(self):
        self.run_cli("approve", "--index", "1", "--reply", "yes")
        code, text, _ = self.run_cli("show")
        self.assertIn("APPROVED by you", text)
        self.assertNotIn("READY:", text)

    def test_a_changed_order_must_pass_risk_again(self):
        self.write(os.path.join("trader", "orders.json"),
                   {"created_by": "trader-budget", "orders": [order(qty=40)]})
        code, text, _ = self.run_cli("show")
        self.assertIn("NOT READY", text)

    def test_flatten_builds_a_square_off_ticket(self):
        code, text, err = self.run_cli("flatten", "--symbol", "TCS", "--qty", "37")
        self.assertEqual(code, 0, err)
        self.assertIn("SELL 37 shares of TCS", text)
        self.assertIn("MIS on NSE", text)
        self.assertIn("15:15 IST", text)
        self.assertIn("squares off", text)
        self.assertIn("CANCEL the open leg first", text)
        with open(os.path.join("trader", "orders.json")) as fh:
            doc = json.load(fh)
        o = doc["orders"][0]
        self.assertEqual(o["side"], "sell")
        self.assertEqual(o["legs"][0]["params"]["transaction_type"], "SELL")
        self.assertEqual(o["legs"][0]["params"]["order_type"], "MARKET")

    def test_the_json_ticket_carries_the_india_facts(self):
        code, text, err = self.run_cli("show", "--index", "1", "--json")
        self.assertEqual(code, 0, err)
        doc = json.loads(text)
        self.assertEqual(doc["broker"], "kite")
        self.assertEqual(doc["tool"], "place_order")
        self.assertEqual(doc["currency"], "INR")
        self.assertEqual(doc["square_off"], "15:20")
        self.assertEqual(len(doc["legs"]), 3)
        self.assertIn("CANCEL", doc["oco_warning"])
        self.assertEqual(doc["max_loss"], 784.00)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-broker", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-broker\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertEqual(text[text.index("## Safety"):], risk[risk.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("three gates", "not linked", "sl-m", "kite_api_key", "square-off",
                     "paper trading is the default", "needs approval",
                     "day trading loses most people money"):
            self.assertIn(must, low, must)
        for never in ("\u2014", "place_stock_order", "time_in_force", "nyse"):
            self.assertNotIn(never, low, never)
        import re as _re
        self.assertIsNone(_re.search(r"\$[0-9]", low), "a dollar amount is left in the doc")
        # a key must never be shown with a value
        self.assertIsNone(_re.search(r"kite_api_key=[a-z0-9]", low), "a key value is in the doc")


if __name__ == "__main__":
    unittest.main()
