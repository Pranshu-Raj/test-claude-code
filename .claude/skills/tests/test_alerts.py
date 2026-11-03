"""trader-alerts tests: the NSE clock, four plan alerts, the square-off ring."""

import datetime
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX, ROOT

al = load("trader-alerts", "alerts")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from config import india_market as mkt

INDIA_FIX = os.path.join(FIX, "india")

PLAN = {"symbol": "TCS", "date": "2026-11-02", "entry": 4026.50, "stop": 4008.00,
        "target": 4063.50, "stop_label": "opening-range midpoint",
        "target_label": "2R (two times the risk)", "flat_by": "15:15"}


def run(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = al.main(list(args))
    return code, out.getvalue(), err.getvalue()


def status(when):
    return al.market_status(al.parse_now(when))


class Clock(unittest.TestCase):
    def test_the_session_is_0915_to_1530(self):
        st = status("2026-11-02 10:00")
        self.assertTrue(st["open"])
        self.assertEqual((st["opens"], st["closes"]), ("09:15", "15:30"))

    def test_before_the_open_is_closed(self):
        self.assertFalse(status("2026-11-02 09:05")["open"])

    def test_after_the_close_is_closed(self):
        self.assertFalse(status("2026-11-02 15:45")["open"])

    def test_a_weekend_is_closed(self):
        self.assertFalse(status("2026-11-07 10:00")["open"])

    def test_a_holiday_is_closed_and_named(self):
        st = status("2026-01-26 10:00")
        self.assertFalse(st["open"])
        self.assertIn("Republic Day", st["reason"])

    def test_a_muhurat_day_reports_its_own_window(self):
        st = status("2026-11-08 18:30")
        self.assertEqual((st["opens"], st["closes"]), ("18:15", "19:15"))
        self.assertIn("Muhurat", st["special_session"])

    def test_the_square_off_time_is_on_the_status(self):
        self.assertEqual(status("2026-11-02 10:00")["square_off"], "15:20")

    def test_ist_has_no_daylight_saving(self):
        for when in ("2026-01-15 10:00", "2026-06-15 10:00", "2026-11-15 10:00"):
            self.assertEqual(al.to_ist(al.parse_now(when)).hour, 10, when)

    def test_the_clock_command_says_ist_and_nse(self):
        code, text, _ = run("clock", "--now", "2026-11-02 10:00")
        self.assertEqual(code, 0)
        self.assertIn("IST", text)
        self.assertIn("NSE OPEN", text)
        self.assertIn("09:15 to 15:30", text)
        self.assertNotIn("NYSE", text)


class PlanRules(unittest.TestCase):
    def test_a_plan_becomes_four_alerts(self):
        rules = al.rules_from_plan(PLAN)
        self.assertEqual([r["type"] for r in rules],
                         ["stop", "target", "time", "square_off"])

    def test_the_stop_and_target_carry_their_prices(self):
        rules = {r["type"]: r for r in al.rules_from_plan(PLAN)}
        self.assertEqual(rules["stop"]["price"], 4008.00)
        self.assertEqual(rules["target"]["price"], 4063.50)
        self.assertIn("opening-range midpoint", rules["stop"]["note"])

    def test_the_flat_by_comes_from_the_plan(self):
        rules = {r["type"]: r for r in al.rules_from_plan(PLAN)}
        self.assertEqual(rules["time"]["at"], "15:15")

    def test_the_square_off_rule_names_the_broker_time(self):
        rules = {r["type"]: r for r in al.rules_from_plan(PLAN)}
        self.assertEqual(rules["square_off"]["minutes_before"], 10)
        self.assertIn("15:20", rules["square_off"]["note"])

    def test_the_default_flat_by_is_the_shared_one_not_1555(self):
        plan = dict(PLAN)
        plan.pop("flat_by")
        rules = {r["type"]: r for r in al.rules_from_plan(plan)}
        self.assertEqual(rules["time"]["at"], mkt.FLAT_BY.strftime("%H:%M"))
        self.assertEqual(rules["time"]["at"], "15:15")

    def test_square_off_is_a_known_type_that_needs_no_quote(self):
        self.assertIn("square_off", al.PRICE_TYPES)
        self.assertIn("square_off", al.NO_QUOTE)


class Ringing(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.makedirs(os.path.join("trader", "state"))
        with open(os.path.join("trader", "plan.json"), "w") as fh:
            json.dump(PLAN, fh)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def test_add_from_plan_writes_four_rules(self):
        code, text, err = run("add", "--from-plan")
        self.assertEqual(code, 0, err)
        self.assertEqual(text.count("Added "), 4)
        self.assertIn("square-off", text)

    def test_the_square_off_alert_rings_before_the_broker_acts(self):
        run("add", "--from-plan")
        code, text, err = run("check", "--adapter", "offline", "--data", INDIA_FIX,
                              "--now", "2026-11-02 15:12")
        self.assertIn("Square-off in", text)
        self.assertIn("15:20", text)
        self.assertIn("TCS", text)

    def test_it_does_not_ring_early_in_the_session(self):
        run("add", "--from-plan")
        code, text, _ = run("check", "--adapter", "offline", "--data", INDIA_FIX,
                            "--now", "2026-11-02 10:00")
        self.assertNotIn("Square-off in", text)

    def test_an_alert_rings_once(self):
        run("add", "--from-plan")
        run("check", "--adapter", "offline", "--data", INDIA_FIX, "--now", "2026-11-02 15:12")
        code, text, _ = run("check", "--adapter", "offline", "--data", INDIA_FIX,
                            "--now", "2026-11-02 15:13")
        self.assertNotIn("Square-off in", text)

    def test_the_opening_bell_is_0915_and_mentions_nse(self):
        code, _, _ = run("add", "--opening-bell")
        code, text, _ = run("check", "--adapter", "offline", "--data", INDIA_FIX,
                            "--now", "2026-11-02 09:20")
        self.assertIn("Opening bell: NSE is open", text)

    def test_the_footer_does_not_promise_a_bracket(self):
        run("add", "--from-plan")
        code, text, _ = run("check", "--adapter", "offline", "--data", INDIA_FIX,
                            "--now", "2026-11-02 15:12")
        self.assertIn("NOT linked", text)
        self.assertNotIn("bracket", text.lower())


class Quotes(unittest.TestCase):
    def test_quotes_come_through_the_adapter_in_rupees(self):
        quotes, failed = al.get_quotes(["TCS"], source="offline", offline=INDIA_FIX, pause=0)
        self.assertEqual(failed, {})
        q = quotes["TCS"]
        self.assertEqual(q["symbol"], "TCS")
        self.assertEqual(q["currency"], "INR")
        self.assertEqual(q["prev_close"], 4002.75)
        self.assertGreater(q["price"], 0)

    def test_a_suffixed_symbol_is_normalised(self):
        quotes, _ = al.get_quotes(["tcs.ns"], source="offline", offline=INDIA_FIX, pause=0)
        self.assertIn("TCS", quotes)

    def test_an_unknown_symbol_is_reported_not_raised(self):
        quotes, failed = al.get_quotes(["NOSUCH"], source="offline", offline=INDIA_FIX, pause=0)
        self.assertEqual(quotes, {})
        self.assertIn("NOSUCH", failed)

    def test_the_us_only_stooq_path_is_gone(self):
        src = open(os.path.join(ROOT, "trader-alerts", "alerts.py"), encoding="utf-8").read()
        for gone in ("stooq", "NYSE_HOLIDAYS", "eastern_offset", "YAHOO_HOSTS", ".us&f="):
            self.assertNotIn(gone, src, gone)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-alerts", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-alerts\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertEqual(text[text.index("## Safety"):], risk[risk.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("09:15", "15:30", "square_off", "four alerts", "muhurat",
                     "an alert is information, not an order",
                     "day trading loses most people money"):
            self.assertIn(must, low, must)
        for never in ("\u2014", "nyse", "15:55"):
            self.assertNotIn(never, low, never)
        import re as _re
        self.assertIsNone(_re.search(r"\$[0-9]", low), "a dollar amount is left in the doc")


if __name__ == "__main__":
    unittest.main()
