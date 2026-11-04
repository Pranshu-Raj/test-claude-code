"""The India README and the verification checklist stay honest and in sync."""

import os
import re
import sys
import unittest

from _load import ROOT

REPO = os.path.dirname(os.path.dirname(ROOT))     # the repo root, above .claude/skills

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from config import india_market as mkt

SKILLS = ["trader-scanner", "trader-news", "trader-entry", "trader-exit", "trader-timemachine",
          "trader-risk", "trader-budget", "trader-broker", "trader-alerts", "trader-analyst",
          "trader-insider"]


def readme():
    with open(os.path.join(REPO, "README.md"), encoding="utf-8") as fh:
        return fh.read()


def verify_doc():
    with open(os.path.join(REPO, "docs", "verify.md"), encoding="utf-8") as fh:
        return fh.read()


class Readme(unittest.TestCase):
    def test_it_names_every_skill(self):
        low = readme().lower()
        for s in SKILLS:
            self.assertIn("/" + s, low, s)

    def test_it_does_not_name_the_retired_skill_as_current(self):
        self.assertNotIn("/trader-pelosi", readme())

    def test_it_credits_the_original_and_its_licence(self):
        text = readme()
        self.assertIn("Jake Schincariol", text)
        self.assertIn("MIT", text)
        self.assertIn("day-trader-skill", text)

    def test_it_carries_the_disclaimer(self):
        low = " ".join(readme().lower().split())
        self.assertIn("not investment advice", low)
        self.assertIn("lose money", low)
        self.assertIn("paper", low)

    def test_it_says_setup_and_keys_come_from_the_environment(self):
        text = readme()
        flat = " ".join(text.split())
        self.assertIn("KITE_API_KEY", text)
        self.assertIn("environment variables only", flat)
        self.assertNotRegex(text, r"KITE_API_KEY=[A-Za-z0-9]{6,}")   # never a real key

    def test_it_is_indian_not_american(self):
        low = " ".join(readme().lower().split())
        for must in ("nse", "bse", "ist", "09:15", "15:30", "rupee", "stt", "square-off"):
            self.assertIn(must, low, must)
        # the README deliberately COMPARES with the US original, so US terms
        # appear by design. What matters is that no US value is presented as
        # current, which the positive assertions above cover, and that no
        # dollar amount or em-dash survives.
        self.assertNotIn("\u2014", low)
        self.assertIsNone(re.search(r"\$[0-9]", low), "a dollar amount is in the README")

    def test_it_states_what_was_not_verified_live(self):
        low = " ".join(readme().lower().split())
        self.assertIn("not been verified", low)
        self.assertIn("synthetic", low)

    def test_it_points_at_the_verification_checklist(self):
        self.assertIn("docs/verify.md", readme())


class VerifyChecklist(unittest.TestCase):
    def test_every_cost_key_is_listed(self):
        text = verify_doc()
        for key in mkt.COSTS:
            self.assertIn(key, text, key)

    def test_every_data_file_is_listed(self):
        text = verify_doc()
        for name in ("nse_holidays.csv", "nse_special_sessions.csv", "nse_universe.csv",
                     "rbi_dates.csv"):
            self.assertIn(name, text, name)

    def test_the_structural_values_are_listed(self):
        text = verify_doc()
        for key in ("SESSION_OPEN", "BROKER_SQUARE_OFF", "DEFAULT_TICK", "PRICE_BANDS_PCT",
                    "MARKET_WIDE_BREAKER_PCT", "INTRADAY_MARGIN_MULTIPLIER",
                    "SETTLEMENT_CYCLE", "OVERNIGHT_SHORT_ALLOWED_CASH"):
            self.assertIn(key, text, key)

    def test_the_regulatory_thresholds_are_listed(self):
        text = verify_doc()
        self.assertIn("DISCLOSURE_THRESHOLD_INR", text)
        self.assertIn("DISCLOSURE_DAYS", text)
        self.assertIn("7(2)", text)

    def test_it_names_the_unexercised_endpoints(self):
        text = verify_doc()
        for key in ("NSE_PIT", "NSE_FII_DII", "NSE_ANNOUNCE", "FIELD_MAP"):
            self.assertIn(key, text, key)

    def test_the_enumerated_count_is_honest(self):
        """The doc also states how many values verify_list() names. Count those."""
        claimed = re.search(r"\*\*(\d+) values\*\*", verify_doc())
        self.assertIsNotNone(claimed, "the doc should state the enumerated count")
        self.assertEqual(int(claimed.group(1)), len(mkt.verify_list()))
        costs = re.search(r"(\d+) of them cost rates", verify_doc())
        self.assertIsNotNone(costs, "the doc should state how many are cost rates")
        self.assertEqual(int(costs.group(1)),
                         sum(1 for row in mkt.verify_list() if row[0] == "costs"))

    def test_the_marker_count_is_honest(self):
        """The README and this doc both claim a VERIFY marker count. Count them."""
        found = 0
        for base, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "fixtures")]
            for f in files:
                if f.endswith((".py", ".csv")):
                    with open(os.path.join(base, f), encoding="utf-8", errors="replace") as fh:
                        found += sum(1 for ln in fh if "VERIFY" in ln)
        claimed = re.search(r"\*\*(\d+) VERIFY markers\*\*", verify_doc())
        self.assertIsNotNone(claimed, "the doc should state the marker count")
        self.assertEqual(int(claimed.group(1)), found,
                         "the doc claims %s markers, the code has %d"
                         % (claimed.group(1) if claimed else "?", found))


class CalendarHonesty(unittest.TestCase):
    def test_the_shipped_calendar_is_still_flagged_unverified(self):
        """If someone verifies the calendar they flip this flag, and this test
        is the reminder that the README's warning has to change with it."""
        if mkt.CALENDAR_VERIFIED:
            low = " ".join(readme().lower().split())
            self.assertNotIn("the calendar and universe are unverified", low)
        else:
            self.assertIn("unverified", " ".join(readme().lower().split()))


if __name__ == "__main__":
    unittest.main()
