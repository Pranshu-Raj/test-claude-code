"""The data-source adapter layer, against the SYNTHETIC India fixtures."""

import datetime
import os
import sys
import unittest

from _load import ROOT

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import datasource as ds, india_market as mkt

FIX = os.path.join(ROOT, "tests", "fixtures", "india")


def offline():
    return ds.get_adapter("offline", directory=FIX)


class Fixtures(unittest.TestCase):
    def test_every_fixture_declares_itself_synthetic(self):
        # the guard: invented prices must never pass as a capture
        for sym, interval in (("RELIANCE", "5m"), ("RELIANCE", "1m"),
                              ("TCS", "5m"), ("CIRCUITCO", "5m")):
            series = offline().bars(sym, interval)
            self.assertTrue(series["meta"].get("synthetic"), "%s %s" % (sym, interval))
            self.assertIn("never traded", series["meta"].get("note", ""))

    def test_the_raw_yahoo_fixture_is_marked_too(self):
        import json
        with open(os.path.join(FIX, "yahoo-RELIANCE-5m.json"), encoding="utf-8") as fh:
            payload = json.load(fh)
        self.assertTrue(payload["chart"]["result"][0]["meta"]["synthetic"])


class Offline(unittest.TestCase):
    def test_reads_a_canonical_series(self):
        s = offline().bars("TCS", "5m")
        self.assertEqual(s["symbol"], "TCS")
        self.assertEqual(s["exchange"], "NSE")
        self.assertEqual(s["interval"], "5m")
        self.assertEqual(len(s["bars"]), 75)

    def test_reads_a_gzipped_series(self):
        s = offline().bars("RELIANCE", "5m")
        self.assertEqual(len(s["bars"]), 60 * 75)

    def test_a_symbol_with_a_suffix_is_normalised(self):
        self.assertEqual(offline().bars("tcs.ns", "5m")["symbol"], "TCS")

    def test_bars_are_ascending_and_well_formed(self):
        for b in offline().bars("TCS", "5m")["bars"]:
            self.assertLessEqual(b["l"], b["o"])
            self.assertLessEqual(b["l"], b["c"])
            self.assertGreaterEqual(b["h"], b["o"])
            self.assertGreaterEqual(b["h"], b["c"])
        ts = [b["t"] for b in offline().bars("TCS", "5m")["bars"]]
        self.assertEqual(ts, sorted(ts))

    def test_missing_data_says_what_it_looked_for(self):
        with self.assertRaises(ds.DataError) as cm:
            offline().bars("NOSUCHSTOCK", "5m")
        self.assertIn("NOSUCHSTOCK-5m.json", str(cm.exception))

    def test_quote_comes_off_the_last_bar(self):
        q = offline().quote("TCS")
        self.assertEqual(q["symbol"], "TCS")
        self.assertEqual(q["prev_close"], 4002.75)
        self.assertEqual(q["source"], "offline")


class YahooParsing(unittest.TestCase):
    def test_a_raw_yahoo_payload_is_detected_and_converted(self):
        import json
        with open(os.path.join(FIX, "yahoo-RELIANCE-5m.json"), encoding="utf-8") as fh:
            s = ds.parse_yahoo_chart(json.load(fh), "RELIANCE", "5m")
        self.assertEqual(s["symbol"], "RELIANCE")
        self.assertEqual(s["exchange"], "NSE")
        self.assertEqual(s["meta"]["prev_close"], 1496.20)
        self.assertEqual(s["meta"]["timezone"], "Asia/Kolkata")
        self.assertEqual(s["meta"]["gmtoffset"], 19800)

    def test_null_padded_bars_are_dropped_not_zeroed(self):
        import json
        with open(os.path.join(FIX, "yahoo-RELIANCE-5m.json"), encoding="utf-8") as fh:
            s = ds.parse_yahoo_chart(json.load(fh), "RELIANCE", "5m")
        self.assertEqual(len(s["bars"]), 73)          # 75 stamps, 2 nulls
        self.assertFalse(any(b["c"] == 0 for b in s["bars"]))

    def test_an_error_payload_explains_itself(self):
        with self.assertRaises(ds.DataError):
            ds.parse_yahoo_chart({"chart": {"result": None, "error": "Not Found"}})

    def test_yahoo_adapter_builds_the_india_url_and_never_calls_out_in_tests(self):
        seen = {}

        def fake(url):
            seen["url"] = url
            import json
            with open(os.path.join(FIX, "yahoo-RELIANCE-5m.json"), encoding="utf-8") as fh:
                return json.load(fh)

        a = ds.YahooAdapter(pause=0, opener=fake)
        s = a.bars("RELIANCE", "5m", days=60)
        self.assertIn("RELIANCE.NS", seen["url"])
        self.assertIn("interval=5m", seen["url"])
        self.assertEqual(len(s["bars"]), 73)

    def test_bse_symbols_route_to_the_bo_suffix(self):
        seen = {}

        def fake(url):
            seen["url"] = url
            return {"chart": {"result": [{"meta": {"symbol": "RELIANCE.BO"}, "timestamp": [],
                                          "indicators": {"quote": [{}]}}]}}

        ds.YahooAdapter(pause=0, opener=fake).bars("RELIANCE", "5m", exchange="BSE")
        self.assertIn("RELIANCE.BO", seen["url"])


class SessionSlicing(unittest.TestCase):
    def test_session_bars_keeps_only_the_nse_window(self):
        s = offline().bars("TCS", "5m")
        bars = ds.session_bars(s)
        self.assertEqual(len(bars), 75)
        first = mkt.ts_to_ist(bars[0]["t"])
        last = mkt.ts_to_ist(bars[-1]["t"])
        self.assertEqual(first.time(), datetime.time(9, 15))
        self.assertEqual(last.time(), datetime.time(15, 25))   # the last bar OPENS at 15:25

    def test_an_intraday_cutoff_reproduces_a_scan_as_at_a_time(self):
        s = offline().bars("TCS", "5m")
        upto = ds.session_bars(s, upto=datetime.time(10, 15))
        self.assertEqual(len(upto), 12)                        # 09:15 through 10:10
        self.assertEqual(mkt.ts_to_ist(upto[-1]["t"]).time(), datetime.time(10, 10))

    def test_full_sessions_counts_75_not_78(self):
        s = offline().bars("RELIANCE", "5m")
        days = ds.full_sessions(s, 5)
        self.assertEqual(len(days), 60)
        for _, bars in days:
            self.assertEqual(len(bars), 75)

    def test_full_sessions_can_stop_before_a_date(self):
        s = offline().bars("RELIANCE", "5m")
        days = ds.full_sessions(s, 5, before=datetime.date(2026, 11, 2))
        self.assertEqual(len(days), 59)
        self.assertTrue(all(d < datetime.date(2026, 11, 2) for d, _ in days))

    def test_a_partial_session_is_dropped(self):
        s = offline().bars("TCS", "5m")
        s["bars"] = s["bars"][:40]                             # half a day
        self.assertEqual(ds.full_sessions(s, 5), [])

    def test_one_minute_bars_give_375_a_session(self):
        s = offline().bars("RELIANCE", "1m")
        self.assertEqual(len(ds.session_bars(s)), 375)


class Factory(unittest.TestCase):
    def test_default_is_yahoo(self):
        self.assertEqual(ds.DEFAULT_ADAPTER, "yahoo")

    def test_all_six_adapters_are_registered(self):
        self.assertEqual(sorted(ds.ADAPTERS),
                         ["angel", "dhan", "kite", "offline", "upstox", "yahoo"])

    def test_an_unknown_adapter_lists_the_real_ones(self):
        with self.assertRaises(ds.DataError) as cm:
            ds.get_adapter("robinhood")
        self.assertIn("yahoo", str(cm.exception))

    def test_offline_without_a_directory_is_refused(self):
        with self.assertRaises(ds.DataError):
            ds.get_adapter("offline")

    def test_the_adapter_can_be_chosen_by_environment_variable(self):
        old = os.environ.get("TRADER_DATA_ADAPTER")
        os.environ["TRADER_DATA_ADAPTER"] = "offline"
        try:
            a = ds.get_adapter(directory=FIX)
            self.assertEqual(a.name, "offline")
        finally:
            if old is None:
                del os.environ["TRADER_DATA_ADAPTER"]
            else:
                os.environ["TRADER_DATA_ADAPTER"] = old


class BrokerAdapters(unittest.TestCase):
    def test_a_broker_adapter_without_its_keys_says_how_to_set_them_up(self):
        for name, env in (("kite", "KITE_API_KEY"), ("upstox", "UPSTOX_ACCESS_TOKEN"),
                          ("angel", "SMARTAPI_API_KEY"), ("dhan", "DHAN_CLIENT_ID")):
            saved = dict((k, os.environ.pop(k, None)) for k in ds.ADAPTERS[name].env_keys)
            try:
                with self.assertRaises(ds.AdapterUnavailable) as cm:
                    ds.get_adapter(name)
                self.assertIn(env, str(cm.exception), name)
            finally:
                for k, v in saved.items():
                    if v is not None:
                        os.environ[k] = v

    def test_credentials_come_only_from_the_environment(self):
        src = open(os.path.join(ROOT, "config", "datasource.py"), encoding="utf-8").read()
        self.assertIn("os.environ", src)
        # no key may be read from, or written to, a file
        for bad in ("api_key =", "access_token =", "secret"):
            self.assertNotIn(bad + " \"", src.lower())

    def test_broker_bars_are_honest_about_not_being_wired_up(self):
        os.environ["KITE_API_KEY"] = "x"
        os.environ["KITE_ACCESS_TOKEN"] = "y"
        try:
            a = ds.get_adapter("kite")
            with self.assertRaises(ds.AdapterUnavailable):
                a.bars("RELIANCE")
        finally:
            del os.environ["KITE_API_KEY"], os.environ["KITE_ACCESS_TOKEN"]


if __name__ == "__main__":
    unittest.main()


class RealCaptures(unittest.TestCase):
    """Real Yahoo payloads from 2026-11-07 (tests/fixtures/india/real/README.md).
    They carry no synthetic marker, and they are why the completeness rule is
    "every bar up to the flat-by" rather than "all 75"."""

    REAL = os.path.join(FIX, "real")

    def real(self, sym, interval):
        return ds.get_adapter("offline", directory=self.REAL).bars(sym, interval)

    def test_the_real_files_are_not_marked_synthetic(self):
        for sym, interval in (("RELIANCE", "5m"), ("TCS", "1m"), ("NIFTY50", "5m"), ("INDIAVIX", "5m")):
            s = self.real(sym, interval)
            self.assertFalse(s["meta"].get("synthetic"), "%s %s" % (sym, interval))
            self.assertTrue(s["bars"], "%s %s" % (sym, interval))

    def test_a_real_session_never_has_75_bars(self):
        s = self.real("RELIANCE", "5m")
        per = {}
        for b in s["bars"]:
            per.setdefault(mkt.ts_to_ist(b["t"]).date(), 0)
            per[mkt.ts_to_ist(b["t"]).date()] += 1
        self.assertEqual(len(per), 60)
        self.assertEqual(set(per.values()), {72, 73, 74})

    def test_full_sessions_keeps_real_days_complete_to_the_flat_by(self):
        s = self.real("RELIANCE", "5m")
        days = ds.full_sessions(s, 5)
        self.assertEqual(len(days), 59)
        self.assertNotIn(datetime.date(2026, 10, 15), [d for d, _ in days])   # 11:45 and 11:50 missing
        for d, bars in days:
            self.assertGreaterEqual(len(bars), mkt.required_bars(5))
            self.assertEqual(mkt.ts_to_ist(bars[0]["t"]).time(), datetime.time(9, 15))
            # contiguous from the open: bar i opens i*5 minutes after 09:15
            for i, b in enumerate(bars):
                self.assertEqual(b["t"] - bars[0]["t"], 300 * i, (d, i))

    def test_the_real_minute_feed_loses_its_tail_too(self):
        s = self.real("TCS", "1m")
        self.assertEqual(len(s["bars"]), 361)                    # of 375
        missing = sorted(set(range(375)) - set((mkt.ts_to_ist(b["t"]).hour * 60
                                                + mkt.ts_to_ist(b["t"]).minute - 555) for b in s["bars"]))
        self.assertEqual(missing, list(range(360, 374)))         # 15:15 to 15:28: the same null tail as 5m
        days = ds.full_sessions(s, 1)
        self.assertEqual(len(days), 1)                           # complete up to the 15:15 flat-by
        self.assertEqual(len(days[0][1]), 360)

    def test_an_index_closing_print_at_1530_is_outside_the_session(self):
        s = self.real("NIFTY50", "5m")
        last_day = max(mkt.ts_to_ist(b["t"]).date() for b in s["bars"])
        inside = ds.session_bars(s, day=last_day)
        self.assertEqual(len(inside), 75)
        self.assertEqual(mkt.ts_to_ist(inside[-1]["t"]).time(), datetime.time(15, 25))
        raw_last = max(b["t"] for b in s["bars"])
        self.assertEqual(mkt.ts_to_ist(raw_last).time(), datetime.time(15, 30))   # present, and dropped
