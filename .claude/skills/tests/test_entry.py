"""trader-entry tests, NSE cash intraday, against the SYNTHETIC India fixtures.

TCS-5m.json is built so the answer is known: the opening range is 3996.00 to
4020.00 (the 09:15, 09:20 and 09:25 bars), the 09:30, 09:35 and 09:40 bars
poke above 4020.00 but CLOSE under it, and the 09:45 bar closes clear at
4026.50. So the only trigger is the 09:45 bar.

CIRCUITCO-5m.json sits at its upper circuit all session: previous close
1000.00, a 10 per cent band, pinned at 1100.00.
"""

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

entry = load("trader-entry", "entry")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from config import india_market as mkt

INDIA_FIX = os.path.join(FIX, "india")
DT = datetime.datetime


def ist(text):
    return entry.parse_now(text)


def chart(sym="TCS", interval="5m"):
    return entry.get_chart(sym, interval, adapter="offline", data_dir=INDIA_FIX)


def ev(now, sym="TCS", **kw):
    return entry.evaluate(chart(sym, kw.pop("interval", "5m")), ist(now), **kw)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = entry.main(argv)
    return code, out.getvalue(), err.getvalue()


class OpeningRange(unittest.TestCase):
    def test_the_range_starts_at_0915_not_0930(self):
        res = ev("2026-11-02 09:31")
        self.assertEqual(res["opens"], "09:15")
        orr = res["opening_range"]
        self.assertEqual((orr["high"], orr["low"]), (4020.00, 3996.00))
        self.assertEqual((orr["bars"], orr["of"]), (3, 3))

    def test_fifteen_minutes_is_three_five_minute_bars(self):
        res = ev("2026-11-02 09:31")
        self.assertEqual(res["or_minutes"], 15)
        self.assertEqual(res["opening_range"]["of"], 3)

    def test_the_range_is_still_forming_before_0930(self):
        res = ev("2026-11-02 09:26")
        self.assertEqual(res["status"], "waiting")
        self.assertLess(res["opening_range"]["bars"], 3)

    def test_before_the_open_it_says_so(self):
        res = ev("2026-11-02 09:05")
        self.assertEqual(res["phase"], "pre-open")
        self.assertIn("09:15", res["reason"])

    def test_a_holiday_is_closed(self):
        res = ev("2026-01-26 10:00")
        self.assertEqual(res["status"], "no-data")
        self.assertIn("NSE is closed", res["reason"])
        self.assertIn("Republic Day", res["reason"])


class TheRule(unittest.TestCase):
    def test_it_triggers_on_the_0945_bar(self):
        res = ev("2026-11-02 09:51")
        self.assertEqual(res["status"], "triggered")
        t = res["trigger"]
        self.assertEqual(t["price"], 4026.50)
        self.assertEqual(t["bar_start"], "09:45")
        self.assertEqual(t["bar_index"], 6)
        self.assertEqual(t["time"], "09:50")          # 24-hour IST, no AM/PM

    def test_a_wick_through_the_level_is_not_a_trigger(self):
        # the 09:40 bar reaches 4031.00 and closes at 4019.00, under the range high
        res = ev("2026-11-02 09:46")
        self.assertEqual(res["status"], "waiting")
        self.assertEqual(res["last"]["close"], 4019.00)

    def test_the_forming_bar_never_counts(self):
        # at 09:48 the 09:45 bar has not closed, so there is no trigger yet
        res = ev("2026-11-02 09:48")
        self.assertEqual(res["status"], "waiting")

    def test_a_level_setup_uses_the_level_you_give(self):
        # the 09:15 bar closes at 4008.00, clear of a 4005.00 level
        res = ev("2026-11-02 09:31", setup="level", level=4005.0)
        self.assertEqual(res["status"], "triggered")
        self.assertEqual(res["level"], 4005.0)

    def test_a_close_exactly_at_the_level_is_not_above_it(self):
        res = ev("2026-11-02 09:31", setup="level", level=4010.0)
        self.assertEqual(res["status"], "waiting")

    def test_expired_after_the_last_entry_time(self):
        res = ev("2026-11-02 15:00", setup="level", level=9999.0)
        self.assertEqual(res["status"], "expired")
        self.assertIn("the last time for a new entry", res["reason"])

    def test_no_entries_before_holds_an_early_trigger(self):
        res = ev("2026-11-02 09:51", cfg={"no_entries_before": "09:55",
                                          "no_entries_after": "14:45"})
        self.assertEqual(res["status"], "waiting")
        self.assertIn("no entries before 9:55", res["reason"])


class Circuits(unittest.TestCase):
    """India-specific: a breakout into a price band cannot run."""

    def test_a_stock_at_its_upper_circuit_is_blocked_not_triggered(self):
        res = ev("2026-11-02 10:30", sym="CIRCUITCO", setup="level",
                 level=1050.0, band_pct=10.0)
        self.assertEqual(res["status"], "blocked")
        self.assertIn("at its upper", res["reason"])
        self.assertEqual(res["circuit"]["state"], "at_upper")
        self.assertEqual((res["circuit"]["lower"], res["circuit"]["upper"]), (900.0, 1100.0))

    def test_without_a_band_it_cannot_know_and_says_nothing(self):
        res = ev("2026-11-02 10:30", sym="CIRCUITCO", setup="level", level=1050.0)
        self.assertEqual(res["status"], "triggered")
        self.assertIsNone(res.get("circuit"))

    def test_a_stock_mid_band_is_not_blocked(self):
        res = ev("2026-11-02 09:51", band_pct=20.0)   # TCS prev close 4002.75
        self.assertEqual(res["status"], "triggered")

    def test_the_band_can_come_from_the_config(self):
        res = ev("2026-11-02 10:30", sym="CIRCUITCO", setup="level", level=1050.0,
                 cfg={"no_entries_before": "09:20", "no_entries_after": "14:45",
                      "band_pct": 10.0})
        self.assertEqual(res["status"], "blocked")


class Window(unittest.TestCase):
    def test_the_entry_cutoff_never_passes_the_broker_square_off(self):
        day = datetime.date(2026, 11, 2)
        close_dt = DT.combine(day, mkt.SESSION_CLOSE)
        # a config that asks for entries until 15:28, past the 15:20 square-off
        got = entry.entry_cutoff(day, datetime.time(15, 28), close_dt)
        self.assertEqual(got, DT.combine(day, mkt.BROKER_SQUARE_OFF))

    def test_an_earlier_cutoff_is_left_alone(self):
        day = datetime.date(2026, 11, 2)
        close_dt = DT.combine(day, mkt.SESSION_CLOSE)
        got = entry.entry_cutoff(day, datetime.time(14, 45), close_dt)
        self.assertEqual(got, DT.combine(day, datetime.time(14, 45)))

    def test_the_defaults_come_from_the_shared_config(self):
        self.assertEqual(entry.DEFAULTS["no_entries_before"], "09:20")
        self.assertEqual(entry.DEFAULTS["no_entries_after"], "14:45")

    def test_next_open_uses_the_session_open_not_0930(self):
        nxt = entry.next_open(datetime.date(2026, 11, 6))   # Friday
        self.assertTrue(nxt.endswith("09:15"), nxt)

    def test_next_open_skips_weekends_and_holidays(self):
        nxt = entry.next_open(datetime.date(2026, 11, 6))   # Friday
        day = datetime.date.fromisoformat(nxt.split()[0])
        self.assertLess(day.weekday(), 5, nxt)
        self.assertTrue(mkt.trading_day(day)[0], nxt)

    def test_next_open_does_not_offer_the_muhurat_session(self):
        # 2026-11-08 is a Sunday carrying an evening Muhurat window. NSE is
        # open then, but an hour of ceremony is not the next day-trading
        # session, so next_open must step over it.
        nxt = entry.next_open(datetime.date(2026, 11, 6))
        self.assertNotIn("2026-11-08", nxt)
        self.assertNotIn("18:15", nxt)


class Time(unittest.TestCase):
    def test_ist_is_a_fixed_offset(self):
        self.assertEqual(entry.to_ist(DT(2026, 11, 3, 4, 0)), DT(2026, 11, 3, 9, 30))
        self.assertEqual(entry.to_ist(DT(2026, 6, 3, 4, 0)), DT(2026, 6, 3, 9, 30))

    def test_a_bare_time_is_read_as_ist(self):
        self.assertEqual(entry.parse_now("2026-11-02 09:51"), DT(2026, 11, 2, 4, 21))

    def test_clocks_are_24_hour(self):
        self.assertEqual(entry.ampm(datetime.time(15, 5)), "15:05")
        self.assertEqual(entry.ampm(datetime.time(9, 50)), "09:50")

    def test_the_us_calendar_and_dst_code_are_gone(self):
        src = open(os.path.join(ROOT, "trader-entry", "entry.py"), encoding="utf-8").read()
        for gone in ("NYSE_HOLIDAYS", "NYSE_EARLY_CLOSE", "eastern_offset", "to_eastern",
                     "yahoo_get", "chart_range", "urllib"):
            self.assertNotIn(gone, src, gone)


class Contract(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.mkdir("trader")

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def test_check_writes_an_entry_json_exit_can_read(self):
        code, text, err = run(["check", "TCS", "--adapter", "offline", "--data", INDIA_FIX,
                               "--now", "2026-11-02 09:51", "--write"])
        self.assertEqual(code, 0, err)
        with open(os.path.join("trader", "entry.json")) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["symbol"], "TCS")
        self.assertEqual(doc["status"], "triggered")
        self.assertEqual(doc["price"], 4026.50)
        self.assertEqual(doc["time"], "09:50")
        self.assertEqual(doc["opening_range"], {"high": 4020.00, "low": 3996.00, "minutes": 15})
        self.assertEqual(doc["exchange"], "NSE")
        self.assertEqual(doc["currency"], "INR")
        self.assertEqual(doc["source"], "offline")

    def test_the_screen_output_is_ist_and_rupees(self):
        code, text, _ = run(["check", "TCS", "--adapter", "offline", "--data", INDIA_FIX,
                             "--now", "2026-11-02 09:51"])
        self.assertEqual(code, 0)
        self.assertIn("IST", text)
        self.assertIn("₹4,026.50", text)
        self.assertIn("₹4,020.00", text)
        self.assertNotIn("$", text)
        self.assertNotIn(" ET ", text)
        self.assertNotIn("AM", text)
        self.assertNotIn("Calendar:", text)   # the list is official now

    def test_the_muhurat_window_warning_reaches_the_user(self):
        """NSE publishes the Muhurat DATE with the holiday list but its
        TIMINGS in a later circular, so that one warning must survive even
        though the rest of the calendar is now official."""
        code, text, err = run(["check", "TCS", "--adapter", "offline", "--data", INDIA_FIX,
                               "--now", "2026-11-08 18:30"])
        self.assertIn("Calendar:", text)
        self.assertIn("placeholder", text)
        self.assertIn("circular", text)

    def test_json_output(self):
        code, text, err = run(["check", "TCS", "--adapter", "offline", "--data", INDIA_FIX,
                               "--now", "2026-11-02 09:51", "--json"])
        self.assertEqual(code, 0, err)
        res = json.loads(text)
        self.assertEqual(res["status"], "triggered")
        self.assertEqual(res["trigger"]["price"], 4026.50)

    def test_a_blocked_entry_exits_non_zero_and_is_not_a_trigger(self):
        code, text, _ = run(["check", "CIRCUITCO", "--setup", "level", "--level", "1050",
                             "--band-pct", "10", "--adapter", "offline", "--data", INDIA_FIX,
                             "--now", "2026-11-02 10:30"])
        self.assertNotEqual(code, 0)
        self.assertIn("BLOCKED", text)
        self.assertIn("circuit", text)

    def test_a_short_is_refused(self):
        code, text, err = run(["check", "TCS", "--side", "short", "--adapter", "offline",
                               "--data", INDIA_FIX, "--now", "2026-11-02 09:51"])
        self.assertEqual(code, 1)
        self.assertIn("longs only", text + err)

    def test_a_missing_symbol_says_what_it_looked_for(self):
        code, text, err = run(["check", "NOSUCH", "--adapter", "offline", "--data", INDIA_FIX,
                               "--now", "2026-11-02 09:51"])
        self.assertEqual(code, 1)
        self.assertIn("NOSUCH", text + err)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-entry", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-entry\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertEqual(text[text.index("## Safety"):], risk[risk.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("09:15", "/trader-exit", "circuit", "square", "a close, not a wick",
                     "day trading loses most people money", "long side only", "14:45"):
            self.assertIn(must, low, must)
        for never in ("\u2014", "nyse", "pattern day", "alpaca"):
            self.assertNotIn(never, low, never)
        import re as _re
        for pattern, why in ((r"\$[0-9]", "a dollar amount"), (r"\bcents?\b", "US cents")):
            self.assertIsNone(_re.search(pattern, low), "%s is left in the doc" % why)


if __name__ == "__main__":
    unittest.main()
