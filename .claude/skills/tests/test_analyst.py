"""trader-analyst tests: Indian results in crore, plus flows.
All offline, against SYNTHETIC fixtures."""

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX, ROOT

fa = load("trader-analyst", "facts")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from config import india_market as mkt

INDIA_FIX = os.path.join(FIX, "india")


def run(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = fa.main(list(args))
    return code, out.getvalue(), err.getvalue()


def analysed():
    cf, doc = fa.load_results("RELIANCE", INDIA_FIX)
    return fa.analyse(cf, quarters=6, name=doc.get("company"))


class ResultsAdapter(unittest.TestCase):
    def test_crore_values_become_absolute_rupees(self):
        cf, doc = fa.load_results("RELIANCE", INDIA_FIX)
        node = cf["facts"]["us-gaap"]["revenue"]["units"]["INR"]
        latest = max(node, key=lambda f: f["end"])
        self.assertEqual(latest["val"], 258000 * fa.CRORE)

    def test_eps_stays_rupees_a_share(self):
        cf, _ = fa.load_results("RELIANCE", INDIA_FIX)
        node = cf["facts"]["us-gaap"]["eps_diluted"]["units"]["INR/shares"]
        latest = max(node, key=lambda f: f["end"])
        self.assertEqual(latest["val"], 28.8)

    def test_an_absolute_rupee_table_is_not_rescaled(self):
        cf = fa.results_to_facts({"symbol": "X", "unit": "inr",
                                  "quarters": [{"end": "2026-09-30", "revenue": 1234.0}]})
        node = cf["facts"]["us-gaap"]["revenue"]["units"]["INR"]
        self.assertEqual(node[0]["val"], 1234.0)

    def test_several_line_names_are_accepted(self):
        for name in ("revenue", "revenue_from_operations", "total_income", "net_sales"):
            cf = fa.results_to_facts({"quarters": [{"end": "2026-09-30", name: 100}]})
            self.assertIn(name, cf["facts"]["us-gaap"])

    def test_a_missing_end_date_is_refused(self):
        with self.assertRaises(fa.FactsError):
            fa.results_to_facts({"quarters": [{"revenue": 100}]})

    def test_an_unknown_table_is_refused_with_the_names_it_wanted(self):
        with self.assertRaises(fa.FactsError) as cm:
            fa.results_to_facts({"quarters": [{"end": "2026-09-30", "mystery": 1}]})
        self.assertIn("revenue", str(cm.exception))

    def test_a_missing_file_says_where_it_looked(self):
        with self.assertRaises(fa.FactsError) as cm:
            fa.load_results("NOSUCH", INDIA_FIX)
        self.assertIn("NOSUCH-results.json", str(cm.exception))

    def test_the_sec_endpoints_are_gone(self):
        src = open(os.path.join(ROOT, "trader-analyst", "facts.py"), encoding="utf-8").read()
        for gone in ("data.sec.gov", "company_tickers.json", "ticker_ciks",
                     "RevenueFromContractWithCustomer", "SUBMISSIONS_URL"):
            self.assertNotIn(gone, src, gone)


class Arithmetic(unittest.TestCase):
    def test_six_quarters_are_read(self):
        self.assertEqual(len(analysed()["rows"]), 6)

    def test_year_on_year_revenue(self):
        q = analysed()["rows"][0]
        self.assertEqual(q["end"], "2026-09-30")
        # 258000 against 232000 a year earlier
        self.assertAlmostEqual(q["revenue_yoy"], 11.2, places=1)

    def test_margins_are_computed(self):
        q = analysed()["rows"][0]
        self.assertAlmostEqual(q["gross_margin"], 33.3, places=1)
        self.assertAlmostEqual(q["operating_margin"], 16.3, places=1)
        self.assertAlmostEqual(q["net_margin"], 7.6, places=1)

    def test_the_trailing_twelve_months_adds_four_quarters(self):
        ttm = analysed()["ttm"]
        self.assertAlmostEqual(ttm["revenue"] / fa.CRORE, 984000, places=0)
        self.assertAlmostEqual(ttm["eps_diluted"], 106.70, places=2)


class Presentation(unittest.TestCase):
    def test_the_report_is_in_crore_not_dollars(self):
        text = fa.render("RELIANCE", analysed(), [])
        self.assertIn("CRORE", text)
        self.assertIn("₹", text)
        self.assertNotIn("$", text)
        self.assertNotIn("US$", text)
        self.assertNotIn("SEC", text)
        self.assertNotIn("CIK", text)

    def test_indian_digit_grouping_in_the_table(self):
        self.assertEqual(fa._m(258000 * fa.CRORE), "2,58,000")

    def test_the_ttm_line_is_in_crore(self):
        text = fa.render("RELIANCE", analysed(), [])
        self.assertIn("₹9,84,000 crore", text)


class Flows(unittest.TestCase):
    def fii(self):
        with open(os.path.join(INDIA_FIX, "fii-dii.json"), "rb") as fh:
            return fa.parse_fii_dii(fh.read())

    def idx(self):
        with open(os.path.join(INDIA_FIX, "indices.json"), "rb") as fh:
            return fa.parse_indices(fh.read())

    def test_fii_and_dii_net_are_read(self):
        by = {r["category"]: r for r in self.fii()}
        self.assertAlmostEqual(by["FII/FPI"]["net_crore"], -3730.35, places=2)
        self.assertAlmostEqual(by["DII"]["net_crore"], 3289.50, places=2)
        self.assertAlmostEqual(by["FII/FPI"]["net_inr"], -3730.35 * fa.CRORE, places=0)

    def test_net_is_derived_when_absent(self):
        rows = fa.parse_fii_dii(json.dumps({"data": [
            {"category": "FII/FPI", "buyValue": "100", "sellValue": "40"}]}))
        self.assertAlmostEqual(rows[0]["net_crore"], 60.0, places=2)

    def test_india_vix_is_read(self):
        vix = self.idx()[mkt.VOLATILITY_INDEX]
        self.assertAlmostEqual(vix["last"], 14.82, places=2)
        self.assertAlmostEqual(vix["change_pct"], 8.40, places=2)

    def test_sectors_rank_against_the_benchmark(self):
        rot = fa.rotation(self.idx())
        self.assertEqual(rot["benchmark"], "NIFTY 50")
        self.assertAlmostEqual(rot["benchmark_pct"], -0.62, places=2)
        self.assertEqual(rot["sectors"][0]["index"], "NIFTY PHARMA")
        self.assertEqual(rot["sectors"][-1]["index"], "NIFTY METAL")
        self.assertAlmostEqual(rot["sectors"][0]["vs_benchmark"], 2.04, places=2)

    def test_the_report_warns_when_volatility_is_rising(self):
        text = fa.flows_report(self.fii(), self.idx(), fa._now("2026-11-02 10:00"))
        self.assertIn("India VIX 14.82", text)
        self.assertIn("Volatility is rising", text)
        self.assertIn("stops get hit more often", text)

    def test_the_report_says_who_bought_and_sold(self):
        text = fa.flows_report(self.fii(), self.idx(), fa._now("2026-11-02 10:00"))
        self.assertIn("FII/FPI", text)
        self.assertIn("sold", text)
        self.assertIn("DII", text)
        self.assertIn("bought", text)
        self.assertIn("crore", text)

    def test_the_report_disclaims_prediction(self):
        text = fa.flows_report(self.fii(), self.idx(), fa._now("2026-11-02 10:00"))
        self.assertIn("do not predict tomorrow", text)
        self.assertIn("Not investment advice", text)

    def test_bad_json_is_an_error(self):
        with self.assertRaises(fa.FactsError):
            fa.parse_fii_dii(b"<html>rate limited</html>")
        with self.assertRaises(fa.FactsError):
            fa.parse_indices(b"nope")


class Cli(unittest.TestCase):
    def test_results_runs_offline(self):
        code, text, err = run("results", "RELIANCE", "--from", INDIA_FIX)
        self.assertEqual(code, 0, err)
        self.assertIn("2026-09-30", text)
        self.assertIn("crore", text)

    def test_flows_runs_offline(self):
        code, text, err = run("flows", "--from", INDIA_FIX, "--now", "2026-11-02 10:00")
        self.assertEqual(code, 0, err)
        self.assertIn("India VIX", text)
        self.assertIn("NIFTY PHARMA", text)

    def test_flows_without_a_folder_says_why(self):
        code, text, err = run("flows")
        self.assertEqual(code, 2)
        self.assertIn("have not been exercised", text + err)

    def test_json_output(self):
        code, text, err = run("flows", "--from", INDIA_FIX, "--now", "2026-11-02 10:00", "--json")
        self.assertEqual(code, 0, err)
        doc = json.loads(text)
        self.assertIn("fii_dii", doc)
        self.assertIn("rotation", doc)
        self.assertIn("india_vix", doc)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-analyst", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-analyst\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertEqual(text[text.index("## Safety"):], risk[risk.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("crore", "fii", "dii", "india vix", "sector rotation",
                     "india has no equivalent", "unverified",
                     "not investment advice", "day trading loses most people money"):
            self.assertIn(must, low, must)
        for never in ("\u2014", "nyse", "10-q"):
            self.assertNotIn(never, low, never)
        import re as _re
        self.assertIsNone(_re.search(r"\$[0-9]", low), "a dollar amount is left in the doc")


if __name__ == "__main__":
    unittest.main()
