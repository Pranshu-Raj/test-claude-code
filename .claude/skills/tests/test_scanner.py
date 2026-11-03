"""trader-scanner tests: the NSE universe file, circuit skipping, IST ranges.
All offline, against the SYNTHETIC India fixtures."""

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

scan = load("trader-scanner", "scan")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from config import datasource as ds, india_market as mkt

INDIA_FIX = os.path.join(FIX, "india")


def run(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = scan.main(list(args))
    return code, out.getvalue(), err.getvalue()


def series(sym, interval="5m"):
    s = ds.get_adapter("offline", directory=INDIA_FIX).bars(sym, interval)
    return scan.series_from_canonical(s)


class Universe(unittest.TestCase):
    def test_it_comes_from_the_data_file_not_a_symbol_directory(self):
        kept, counts, source = scan.build_universe()
        self.assertTrue(source.endswith("nse_universe.csv"))
        self.assertGreater(len(kept), 40)
        self.assertIn(("TCS", "Tata Consultancy Services"), kept)

    def test_an_index_filter(self):
        kept, counts, _ = scan.build_universe(index="nifty50")
        self.assertEqual(len(kept), 50)
        self.assertTrue(counts["not in nifty50"] > 0)

    def test_a_sector_filter(self):
        kept, _, _ = scan.build_universe(sector="auto")
        syms = [k for k, _ in kept]
        self.assertIn("MARUTI", syms)
        self.assertNotIn("TCS", syms)

    def test_nse_symbols_with_punctuation_survive(self):
        syms = [k for k, _ in scan.build_universe()[0]]
        self.assertIn("M&M", syms)
        self.assertIn("BAJAJ-AUTO", syms)
        for s in syms:
            self.assertRegex(s, scan.TICKER.pattern)

    def test_bands_are_only_known_where_filled_in(self):
        bands = scan.bands_by_symbol()
        self.assertIn("SUZLON", bands)
        self.assertEqual(bands["SUZLON"], 10.0)
        self.assertNotIn("TCS", bands)       # an F&O name has no fixed band

    def test_the_shipped_universe_declares_itself_unverified(self):
        self.assertIn("not downloaded", mkt.universe_warning())

    def test_the_cli_writes_a_universe_file_and_warns(self):
        d = tempfile.mkdtemp()
        try:
            out = os.path.join(d, "universe.txt")
            code, text, err = run("universe", "--index", "nifty50", "--out", out)
            self.assertEqual(code, 0, err)
            self.assertIn("kept 50", text)
            self.assertIn("WARNING", text)
            with open(out) as fh:
                self.assertEqual(len(fh.read().strip().splitlines()), 50)
        finally:
            shutil.rmtree(d)

    def test_the_us_name_filters_are_gone(self):
        src = open(os.path.join(ROOT, "trader-scanner", "scan.py"), encoding="utf-8").read()
        # the docstring explains what the US version filtered, so the ban is
        # on live code, not on the words
        for gone in ("nasdaqlisted", "NASDAQ_URL", "NextShares", "ALWAYS_OUT", "NYSE_HOLIDAYS",
                     "eastern_offset", "SOFT_OUT", "def classify"):
            self.assertNotIn(gone, src, gone)


class Circuits(unittest.TestCase):
    def test_a_stock_at_its_band_is_skipped(self):
        s = series("CIRCUITCO")
        skip, state = scan.circuit_skip("CIRCUITCO", 1100.0, s, {"CIRCUITCO": 10.0})
        self.assertTrue(skip)
        self.assertEqual(state, "at_upper")

    def test_a_stock_mid_band_is_not_skipped(self):
        s = series("TCS")
        skip, state = scan.circuit_skip("TCS", 4026.50, s, {"TCS": 20.0})
        self.assertFalse(skip)
        self.assertEqual(state, "clear")

    def test_a_symbol_with_no_band_is_reported_not_assumed_clear(self):
        s = series("TCS")
        skip, state = scan.circuit_skip("TCS", 4026.50, s, {})
        self.assertFalse(skip)
        self.assertEqual(state, "band_unknown")

    def test_the_orb_scan_skips_a_banded_stock_at_its_circuit(self):
        data = {"TCS": series("TCS"), "CIRCUITCO": series("CIRCUITCO")}
        hits, counts, failed = scan.scan_orb(
            data, ["TCS", "CIRCUITCO"], datetime.date(2026, 11, 2),
            datetime.datetime(2026, 11, 2, 10, 15), 15, bands={"CIRCUITCO": 10.0})
        self.assertEqual(counts["at_circuit"], 1)
        self.assertEqual([h["symbol"] for h in hits], ["TCS"])

    def test_the_counts_separate_skipped_from_unjudged(self):
        data = {"TCS": series("TCS"), "CIRCUITCO": series("CIRCUITCO")}
        hits, counts, _ = scan.scan_orb(data, ["TCS", "CIRCUITCO"],
                                        datetime.date(2026, 11, 2),
                                        datetime.datetime(2026, 11, 2, 10, 15), 15,
                                        bands={"CIRCUITCO": 10.0})
        self.assertEqual(counts["at_circuit"], 1)
        self.assertEqual(counts["band_unknown"], 1)      # TCS has no band here


class Orb(unittest.TestCase):
    def test_the_range_is_built_from_0915(self):
        data = {"TCS": series("TCS")}
        hits, counts, _ = scan.scan_orb(data, ["TCS"], datetime.date(2026, 11, 2),
                                        datetime.datetime(2026, 11, 2, 10, 15), 15, bands={})
        self.assertEqual(len(hits), 1)
        h = hits[0]
        self.assertEqual((h["or_low"], h["or_high"]), (3996.00, 4020.00))

    def test_the_breakout_bar_is_the_0945_one(self):
        data = {"TCS": series("TCS")}
        hits, _, _ = scan.scan_orb(data, ["TCS"], datetime.date(2026, 11, 2),
                                   datetime.datetime(2026, 11, 2, 10, 15), 15, bands={})
        self.assertEqual(hits[0]["price"], 4026.50)

    def test_a_cutoff_before_the_breakout_finds_nothing(self):
        data = {"TCS": series("TCS")}
        hits, _, _ = scan.scan_orb(data, ["TCS"], datetime.date(2026, 11, 2),
                                   datetime.datetime(2026, 11, 2, 9, 40), 15, bands={})
        self.assertEqual(hits, [])

    def test_the_cli_runs_offline_and_reports_in_rupees(self):
        d = tempfile.mkdtemp()
        cwd = os.getcwd()
        try:
            os.chdir(d)
            os.mkdir("trader")
            code, text, err = run("orb", "--at", "10:15", "--symbols", "TCS,RELIANCE",
                                  "--adapter", "offline", "--data", INDIA_FIX,
                                  "--now", "2026-11-02 10:16")
            self.assertEqual(code, 0, err)
            self.assertIn("IST", text)
            self.assertIn("₹", text)
            self.assertNotIn("$", text)
            self.assertIn("09:15 to 09:30", text)
            self.assertIn("Circuits:", text)
            with open(os.path.join("trader", "watchlist.json")) as fh:
                doc = json.load(fh)
            self.assertEqual(doc["mode"], "orb")
            self.assertTrue(doc["hits"])
        finally:
            os.chdir(cwd)
            shutil.rmtree(d)


class Gaps(unittest.TestCase):
    def test_gap_pct_is_measured_off_the_previous_close(self):
        s = series("TCS")
        s["meta"]["prev_close"] = 4000.00
        s["o"] = [4080.00]
        self.assertAlmostEqual(scan.gap_pct(s), 2.0, places=4)

    def test_no_previous_close_means_no_gap(self):
        s = series("TCS")
        s["meta"].pop("prev_close", None)
        self.assertIsNone(scan.gap_pct(s))

    def test_gap_up_and_gap_down_are_setups(self):
        self.assertIn("gap_up", dict(scan.SETUPS))
        self.assertIn("gap_down", dict(scan.SETUPS))


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-scanner", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-scanner\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertEqual(text[text.index("## Safety"):], risk[risk.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("09:15", "nse_universe.csv", "band_pct", "band-unknown", "nifty50",
                     "not a list of stocks to buy", "day trading loses most people money"):
            self.assertIn(must, low, must)
        for never in ("\u2014", "nyse", "nasdaqlisted"):
            self.assertNotIn(never, low, never)
        import re as _re
        self.assertIsNone(_re.search(r"\$[0-9]", low), "a dollar amount is left in the doc")


if __name__ == "__main__":
    unittest.main()
