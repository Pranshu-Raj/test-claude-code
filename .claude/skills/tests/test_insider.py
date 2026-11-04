"""trader-insider tests: SEBI Reg 7(2) disclosures, offline on SYNTHETIC data."""

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

ins = load("trader-insider", "insider")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from config import india_market as mkt

INDIA_FIX = os.path.join(FIX, "india")
NOW = "2026-11-02 10:00"


def run(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ins.main(list(args))
    return code, out.getvalue(), err.getvalue()


def rows():
    with open(os.path.join(INDIA_FIX, "nse-pit.json"), "rb") as fh:
        return ins.annotate(ins.parse_nse_pit(fh.read()))


class Parsing(unittest.TestCase):
    def test_four_disclosures_are_read(self):
        self.assertEqual(len(rows()), 4)

    def test_symbols_person_and_category(self):
        by = {r["symbol"]: r for r in rows()}
        self.assertEqual(by["RELIANCE"]["category"], "promoter")
        self.assertEqual(by["TCS"]["category"], "director")
        self.assertEqual(by["INFY"]["category"], "designated person")
        self.assertEqual(by["SUZLON"]["category"], "promoter group")
        self.assertIn("Synthetic", by["RELIANCE"]["person"])

    def test_direction_is_read_from_the_filing(self):
        by = {r["symbol"]: r for r in rows()}
        self.assertEqual(by["RELIANCE"]["direction"], "disposed")
        self.assertEqual(by["TCS"]["direction"], "acquired")

    def test_the_mode_is_kept_because_it_changes_the_meaning(self):
        by = {r["symbol"]: r for r in rows()}
        self.assertEqual(by["RELIANCE"]["mode"], "market")
        self.assertEqual(by["TCS"]["mode"], "esop")
        self.assertEqual(by["SUZLON"]["mode"], "off_market")

    def test_values_and_holdings(self):
        by = {r["symbol"]: r for r in rows()}
        self.assertEqual(by["RELIANCE"]["value_inr"], 392500000.0)
        self.assertEqual(by["RELIANCE"]["shares"], 250000.0)
        self.assertEqual(by["RELIANCE"]["held_before"], 1250000.0)
        self.assertEqual(by["RELIANCE"]["held_after"], 1000000.0)

    def test_the_exchange_date_formats_are_understood(self):
        self.assertEqual(ins.parse_date("28-Oct-2026"), datetime.date(2026, 10, 28))
        self.assertEqual(ins.parse_date("2026-10-28"), datetime.date(2026, 10, 28))
        self.assertEqual(ins.parse_date("28/10/2026"), datetime.date(2026, 10, 28))
        self.assertIsNone(ins.parse_date("whenever"))

    def test_bad_json_is_an_error(self):
        with self.assertRaises(ins.InsiderError):
            ins.parse_nse_pit(b"<html>rate limited</html>")


class Lag(unittest.TestCase):
    def test_the_lag_is_counted_in_trading_days(self):
        by = {r["symbol"]: r for r in rows()}
        # traded 28 Oct, disclosed 30 Oct: two trading days
        self.assertEqual(by["RELIANCE"]["lag_trading_days"], 2)
        self.assertFalse(by["RELIANCE"]["late"])

    def test_a_filing_past_the_window_is_flagged_late(self):
        by = {r["symbol"]: r for r in rows()}
        # traded 20 Oct, disclosed 30 Oct
        self.assertGreater(by["INFY"]["lag_trading_days"], ins.DISCLOSURE_DAYS)
        self.assertTrue(by["INFY"]["late"])

    def test_the_window_and_threshold_are_the_sebi_ones(self):
        self.assertEqual(ins.DISCLOSURE_DAYS, 2)
        self.assertEqual(ins.DISCLOSURE_THRESHOLD_INR, 1000000)

    def test_a_trade_under_the_threshold_is_marked(self):
        by = {r["symbol"]: r for r in rows()}
        self.assertTrue(by["SUZLON"]["below_threshold"])    # 84,000 rupees
        self.assertFalse(by["RELIANCE"]["below_threshold"])


class Filters(unittest.TestCase):
    def test_by_symbol(self):
        got = ins.select(rows(), symbols=["TCS"])
        self.assertEqual([r["symbol"] for r in got], ["TCS"])

    def test_by_direction(self):
        got = ins.select(rows(), direction="disposed")
        self.assertEqual([r["symbol"] for r in got], ["RELIANCE"])

    def test_by_category(self):
        got = ins.select(rows(), category="promoter")
        self.assertEqual(sorted(r["symbol"] for r in got), ["RELIANCE", "SUZLON"])

    def test_by_minimum_value(self):
        got = ins.select(rows(), min_value=10000000)
        self.assertEqual(sorted(r["symbol"] for r in got), ["RELIANCE", "TCS"])

    def test_by_days(self):
        today = datetime.date(2026, 11, 2)
        self.assertEqual(len(ins.select(rows(), days=5, today=today)), 4)
        self.assertEqual(len(ins.select(rows(), days=1, today=today)), 0)

    def test_holdings_are_flagged(self):
        annotated = ins.annotate(ins.parse_nse_pit(
            open(os.path.join(INDIA_FIX, "nse-pit.json"), "rb").read()), ["TCS"])
        by = {r["symbol"]: r for r in annotated}
        self.assertTrue(by["TCS"]["you_hold"])
        self.assertFalse(by["RELIANCE"]["you_hold"])


class Output(unittest.TestCase):
    def test_amounts_are_in_lakh_and_crore(self):
        text = ins.describe(ins.select(rows()), ins.parse_now(NOW))
        self.assertIn("crore", text)
        self.assertIn("lakh", text)
        self.assertNotIn("$", text)

    def test_the_mode_caveat_is_printed(self):
        text = ins.describe(ins.select(rows()), ins.parse_now(NOW))
        self.assertIn("may not mean what it looks like", text)

    def test_it_says_these_are_public_filings_after_the_trade(self):
        text = ins.describe(ins.select(rows()), ins.parse_now(NOW))
        self.assertIn("PUBLIC filings", text)
        self.assertIn("after the trade", text.lower())
        self.assertIn("Not financial advice", text)

    def test_the_time_is_ist(self):
        text = ins.describe([], ins.parse_now(NOW))
        self.assertIn("2026-11-02 10:00 IST", text)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.makedirs(os.path.join("trader", "state"))

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def test_list_runs_offline(self):
        code, text, err = run("list", "--offline", INDIA_FIX, "--now", NOW)
        self.assertEqual(code, 0, err)
        self.assertIn("RELIANCE", text)
        self.assertIn("disposed", text)

    def test_new_alerts_once(self):
        code, text, err = run("new", "--offline", INDIA_FIX, "--now", NOW)
        self.assertEqual(code, 0, err)
        self.assertIn("RELIANCE", text)
        code, text, _ = run("new", "--offline", INDIA_FIX, "--now", "2026-11-02 10:05")
        self.assertEqual(code, 1)
        self.assertIn("Nothing new", text)

    def test_the_unverified_warning_appears_on_a_live_run(self):
        code, text, err = run("list", "--now", NOW)
        self.assertIn("has not been exercised", text + err)

    def test_json_output(self):
        code, text, err = run("list", "--offline", INDIA_FIX, "--now", NOW, "--json")
        self.assertEqual(code, 0, err)
        doc = json.loads(text)
        self.assertEqual(len(doc["rows"]), 4)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-insider", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-insider\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertEqual(text[text.index("## Safety"):], risk[risk.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("regulation 7(2)", "10 lakh", "two trading days", "the mode matters",
                     "not insider trading", "unverified",
                     "day trading loses most people money"):
            self.assertIn(must, low, must)
        # the doc explains which US tracker it replaced and why, so naming
        # it is fine; what must not survive is US machinery as live behaviour
        for never in ("\u2014", "ptr.py", "efdsearch", "45 days"):
            self.assertNotIn(never, low, never)

    def test_the_old_skill_folder_is_gone(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "trader-pelosi")))


if __name__ == "__main__":
    unittest.main()
