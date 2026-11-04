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
        self.assertGreater(len(kept), 2000)          # NSE's real cash list
        self.assertIn("TCS", [k for k, _ in kept])

    def test_the_derived_fno_index_filter_works(self):
        kept, counts, _ = scan.build_universe(index="fno")
        self.assertGreater(len(kept), 100)
        self.assertIn("RELIANCE", [k for k, _ in kept])
        self.assertTrue(counts["not in fno"] > 0)

    def test_nifty_membership_is_absent_and_says_so(self):
        """NSE's own files carry no NIFTY constituents, so the filter matches
        nothing and the warning names the source that would fix it."""
        kept, _, _ = scan.build_universe(index="nifty50")
        self.assertEqual(kept, [])
        self.assertIn("niftyindices.com", mkt.universe_warning())

    def test_trade_for_trade_symbols_are_left_out_by_default(self):
        kept, counts, _ = scan.build_universe()
        self.assertGreater(counts["trade-for-trade"], 100)
        rows = {r["symbol"]: r for r in mkt.load_universe()}
        for sym, _ in kept:
            self.assertNotIn(rows[sym]["series"], mkt.TRADE_FOR_TRADE_SERIES, sym)

    def test_they_can_be_kept_deliberately(self):
        kept, _, _ = scan.build_universe(intraday_only=False)
        rows = {r["symbol"]: r for r in mkt.load_universe()}
        self.assertTrue(any(rows[s]["series"] in mkt.TRADE_FOR_TRADE_SERIES for s, _ in kept))

    def test_surveillance_names_can_be_dropped(self):
        plain, _, _ = scan.build_universe()
        clean, counts, _ = scan.build_universe(exclude_surveillance=True)
        self.assertLess(len(clean), len(plain))
        self.assertGreater(counts["under surveillance"], 0)

    def test_a_sector_filter(self):
        kept, _, _ = scan.build_universe(sector="financials")
        syms = [k for k, _ in kept]
        self.assertIn("HDFCBANK", syms)          # the name says "Bank"
        self.assertNotIn("RELIANCE", syms)

    def test_the_sector_guess_is_sparse_and_the_cli_admits_it(self):
        """It is derived from the company name, so it misses most large caps:
        "Tata Consultancy Services Limited" contains no sector word. Better to
        say that than to let --sector quietly return a third of the market."""
        rows = mkt.load_universe()
        known = sum(1 for r in rows if r["sector"])
        self.assertLess(known, len(rows) * 0.75)
        self.assertEqual(next(r for r in rows if r["symbol"] == "TCS")["sector"], "")
        code, text, _ = run("universe", "--sector", "financials", "--no-write")
        self.assertIn("the sector column is a guess", text)

    def test_nse_symbols_with_punctuation_survive(self):
        syms = [k for k, _ in scan.build_universe()[0]]
        self.assertIn("M&M", syms)
        self.assertIn("BAJAJ-AUTO", syms)
        for s in syms:
            self.assertRegex(s, scan.TICKER.pattern)

    def test_bands_come_from_nses_own_file(self):
        bands = scan.bands_by_symbol()
        self.assertGreater(len(bands), 2000)
        # an F&O name has no fixed band, only a dynamic operating range
        self.assertNotIn("TCS", bands)
        self.assertNotIn("RELIANCE", bands)
        for b in set(bands.values()):
            self.assertIn(b, mkt.PRICE_BANDS_PCT)

    def test_the_universe_is_official_and_only_indices_are_missing(self):
        rows = mkt.load_universe()
        self.assertEqual(sorted({r["confidence"] for r in rows}), ["official"])
        self.assertIn("NIFTY", mkt.universe_warning())

    def test_the_cli_writes_a_universe_file_and_warns(self):
        d = tempfile.mkdtemp()
        try:
            out = os.path.join(d, "universe.txt")
            code, text, err = run("universe", "--out", out)
            self.assertEqual(code, 0, err)
            self.assertIn("kept 2392", text)
            self.assertIn("trade-for-trade", text)
            self.assertIn("WARNING", text)
            with open(out) as fh:
                self.assertEqual(len(fh.read().strip().splitlines()), 2392)
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
