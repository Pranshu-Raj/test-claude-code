"""trader-news tests: Indian sources, IST stamps, announcement kinds.
All offline, against SYNTHETIC fixtures."""

import datetime as _dt
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX, ROOT

news = load("trader-news", "news")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from config import india_market as mkt

INDIA_FIX = os.path.join(FIX, "india")


def run(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = news.main(list(args))
    return code, out.getvalue(), err.getvalue()


def rss_bytes():
    with open(os.path.join(INDIA_FIX, "rss-economic-times-markets.xml"), "rb") as fh:
        return fh.read()


def ann_bytes():
    with open(os.path.join(INDIA_FIX, "nse-announce-RELIANCE.json"), "rb") as fh:
        return fh.read()


class Times(unittest.TestCase):
    def test_the_exchange_stamp_format_is_understood_as_ist(self):
        got = news.parse_time("02-Nov-2026 09:05:00")
        self.assertIsNotNone(got)
        # 09:05 IST is 03:35 UTC
        self.assertEqual(got.strftime("%Y-%m-%d %H:%M"), "2026-11-02 03:35")

    def test_rfc822_still_works(self):
        got = news.parse_time("Mon, 02 Nov 2026 03:34:00 GMT")
        self.assertEqual(got.strftime("%H:%M"), "03:34")

    def test_junk_is_none_not_an_error(self):
        self.assertIsNone(news.parse_time("sometime last week"))

    def test_display_is_ist(self):
        src = open(os.path.join(ROOT, "trader-news", "news.py"), encoding="utf-8").read()
        self.assertIn("IST", src)
        self.assertNotIn("%H:%M ET", src)


class Announcements(unittest.TestCase):
    def test_subjects_map_to_kinds(self):
        cases = [("Financial Results for the quarter ended September 2026", "results", True),
                 ("Pledge of shares by promoter", "pledge", True),
                 ("Disclosure under Regulation 7(2) of SEBI (Prohibition of Insider Trading)",
                  "insider", False),
                 ("Intimation of Board Meeting", "board", False),
                 ("Order received from Ministry of Railways", "order_win", True),
                 ("Credit rating upgrade by CRISIL", "rating", True),
                 ("Something entirely unremarkable", "announcement", False)]
        for subject, kind, important in cases:
            got_kind, got_imp = news.announcement_kind(subject)
            self.assertEqual(got_kind, kind, subject)
            self.assertEqual(got_imp, important, subject)

    def test_nse_json_is_parsed(self):
        items = news.parse_nse_announcements(ann_bytes(), "RELIANCE")
        self.assertEqual(len(items), 3)
        kinds = sorted(i["kind"] for i in items)
        self.assertEqual(kinds, ["insider", "pledge", "results"])
        for it in items:
            self.assertEqual(it["ticker"], "RELIANCE")
            self.assertEqual(it["source"], "NSE announcement")
            self.assertIsNotNone(it["time"])

    def test_important_subjects_are_flagged(self):
        items = {i["kind"]: i for i in news.parse_nse_announcements(ann_bytes(), "RELIANCE")}
        self.assertTrue(items["results"]["important"])
        self.assertTrue(items["pledge"]["important"])
        self.assertFalse(items["insider"]["important"])

    def test_bad_json_is_a_news_error(self):
        with self.assertRaises(news.NewsError):
            news.parse_nse_announcements(b"not json", "RELIANCE")

    def test_there_is_no_cik_lookup_any_more(self):
        src = open(os.path.join(ROOT, "trader-news", "news.py"), encoding="utf-8").read()
        for gone in ("cik_map", "TICKERS_URL", "parse_sec_atom", "ITEMS_8K",
                     "eastern_offset", "feeds.finance.yahoo.com"):
            self.assertNotIn(gone, src, gone)


class Headlines(unittest.TestCase):
    def test_rss_is_parsed_with_its_source_label(self):
        items = news.parse_rss(rss_bytes(), "Economic Times Markets")
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["source"], "Economic Times Markets")
        self.assertIsNone(items[0]["ticker"])

    def test_a_bare_symbol_in_a_headline_is_matched(self):
        items = news.tag(news.parse_rss(rss_bytes(), "feed"), {"TCS": None, "RELIANCE": None})
        tcs = [i for i in items if "TCS" in i["touches"]]
        self.assertTrue(tcs)
        self.assertEqual(tcs[0]["ticker"], "TCS")

    def test_a_company_name_is_matched_too(self):
        items = news.tag(news.parse_rss(rss_bytes(), "feed"),
                         {"RELIANCE": "Reliance Industries"})
        hit = [i for i in items if "RELIANCE" in i["touches"]]
        self.assertTrue(hit)

    def test_a_short_symbol_does_not_match_loosely(self):
        pats = news.mention_patterns({"M&M": None})
        self.assertIsNone(pats["M&M"].search("the mm band of spectrum"))

    def test_tags_are_indian(self):
        items = news.tag(news.parse_rss(rss_bytes(), "feed"), {"TCS": None, "RELIANCE": None})
        all_tags = set()
        for i in items:
            all_tags.update(i["tags"])
        self.assertIn("rbi", all_tags)
        self.assertIn("results", all_tags)
        self.assertIn("order_win", all_tags)


class Rbi(unittest.TestCase):
    def test_a_policy_date_today_is_an_item(self):
        items = news.rbi_items(now_utc=_dt.datetime(2026, 12, 4, 4, 0))
        self.assertTrue(items)
        self.assertEqual(items[0]["kind"], "rbi")
        self.assertTrue(items[0]["important"])
        self.assertIn("today", items[0]["title"])

    def test_tomorrow_is_also_flagged(self):
        items = news.rbi_items(now_utc=_dt.datetime(2026, 12, 3, 4, 0))
        self.assertIn("tomorrow", items[0]["title"])

    def test_an_ordinary_day_has_none(self):
        self.assertEqual(news.rbi_items(now_utc=_dt.datetime(2026, 11, 2, 4, 0)), [])


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.makedirs(os.path.join("trader", "state"))

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def test_a_run_shows_headlines_announcements_and_prices_in_ist(self):
        code, text, err = run("--tickers", "RELIANCE,TCS", "--offline", INDIA_FIX,
                              "--now", "2026-11-02T04:00:00Z")
        self.assertEqual(code, 0, err)
        self.assertIn("IST", text)
        self.assertIn("NSE announcement", text)
        self.assertIn("RELIANCE", text)
        self.assertIn("MOVING", text)
        self.assertNotIn("$", text)
        self.assertNotIn("SEC", text)

    def test_comma_separated_tickers_work(self):
        code, text, _ = run("--tickers", "RELIANCE,TCS", "--offline", INDIA_FIX,
                            "--now", "2026-11-02T04:00:00Z")
        self.assertIn("TCS", text)
        self.assertIn("RELIANCE", text)

    def test_an_item_is_shown_once(self):
        run("--tickers", "RELIANCE", "--offline", INDIA_FIX, "--now", "2026-11-02T04:00:00Z")
        code, text, _ = run("--tickers", "RELIANCE", "--offline", INDIA_FIX,
                            "--now", "2026-11-02T04:05:00Z")
        self.assertIn("Nothing new", text)

    def test_the_unverified_warning_appears_on_a_live_run(self):
        code, text, err = run("--tickers", "RELIANCE", "--now", "2026-11-02T04:00:00Z",
                              "--no-moves")
        self.assertIn("has not been exercised", text + err)

    def test_json_output(self):
        code, text, err = run("--tickers", "RELIANCE", "--offline", INDIA_FIX,
                              "--now", "2026-11-02T04:00:00Z", "--json")
        self.assertEqual(code, 0, err)
        doc = json.loads(text)
        self.assertIn("items", doc)
        self.assertIn("moves", doc)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-news", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-news\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertIn("## Safety", text)
        low = " ".join(text.lower().split())
        for must in ("nse", "bse", "rbi", "unverified", "bare nse symbols",
                     "day trading loses most people money"):
            self.assertIn(must, low, must)
        # the doc explains the EDGAR/CIK/8-K machinery it replaced, so the ban
        # is on live US plumbing, asserted against the script above
        for never in ("\u2014", "nyse", "sec_user_agent"):
            self.assertNotIn(never, low, never)


if __name__ == "__main__":
    unittest.main()
