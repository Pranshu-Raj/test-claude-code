"""The shared India market config: sessions, calendar, money, ticks, bands."""

import datetime
import os
import sys
import unittest

from _load import ROOT

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import india_market as mkt


class Timezone(unittest.TestCase):
    def test_ist_is_utc_plus_5_30(self):
        utc = datetime.datetime(2026, 11, 3, 4, 0)
        self.assertEqual(mkt.to_ist(utc), datetime.datetime(2026, 11, 3, 9, 30))

    def test_no_daylight_saving_anywhere_in_the_year(self):
        # the US module this replaces shifted by an hour in March and November
        for month in range(1, 13):
            utc = datetime.datetime(2026, month, 15, 4, 0)
            self.assertEqual(mkt.to_ist(utc).hour, 9, "month %d" % month)
            self.assertEqual(mkt.to_ist(utc).minute, 30, "month %d" % month)

    def test_round_trip(self):
        local = datetime.datetime(2026, 11, 3, 9, 15)
        self.assertEqual(mkt.to_ist(mkt.ist_to_utc(local)), local)

    def test_yahoo_epoch_to_ist(self):
        # 2026-11-03 03:45:00 UTC is the 09:15 IST opening bar
        self.assertEqual(mkt.ts_to_ist(1793677500), datetime.datetime(2026, 11, 3, 9, 15))


class Session(unittest.TestCase):
    def test_nse_hours(self):
        self.assertEqual(mkt.SESSION_OPEN, datetime.time(9, 15))
        self.assertEqual(mkt.SESSION_CLOSE, datetime.time(15, 30))
        self.assertEqual(mkt.PRE_OPEN_START, datetime.time(9, 0))
        self.assertEqual(mkt.PRE_OPEN_END, mkt.SESSION_OPEN)

    def test_session_is_375_minutes_and_75_five_minute_bars(self):
        self.assertEqual(mkt.SESSION_MINUTES, 375)
        self.assertEqual(mkt.bars_per_session(5), 75)   # the US code assumed 78
        self.assertEqual(mkt.bars_per_session(1), 375)
        self.assertEqual(mkt.bars_per_session(15), 25)

    def test_minutes_from_open(self):
        self.assertEqual(mkt.minutes_from_open(datetime.time(9, 15)), 0)
        self.assertEqual(mkt.minutes_from_open(datetime.time(15, 30)), 375)
        self.assertLess(mkt.minutes_from_open(datetime.time(9, 0)), 0)

    def test_square_off_leaves_room_before_the_close(self):
        self.assertLess(mkt.FLAT_BY, mkt.BROKER_SQUARE_OFF)
        self.assertLess(mkt.BROKER_SQUARE_OFF, mkt.SESSION_CLOSE)

    def test_no_entry_window_sits_inside_the_session(self):
        self.assertGreaterEqual(mkt.NO_ENTRIES_BEFORE, mkt.SESSION_OPEN)
        self.assertLess(mkt.NO_ENTRIES_AFTER, mkt.FLAT_BY)


class Calendar(unittest.TestCase):
    def test_weekend_is_not_a_trading_day(self):
        ok, why = mkt.trading_day(datetime.date(2026, 11, 8))  # Sunday
        self.assertFalse(ok)
        self.assertEqual(why, "weekend")

    def test_a_listed_holiday_is_not_a_trading_day(self):
        ok, why = mkt.trading_day(datetime.date(2026, 1, 26))  # Republic Day, a Monday
        self.assertFalse(ok)
        self.assertIn("Republic Day", why)

    def test_an_ordinary_weekday_is_a_trading_day(self):
        ok, why = mkt.trading_day(datetime.date(2026, 11, 3))  # Tuesday
        self.assertTrue(ok)
        self.assertEqual(why, "")

    def test_session_is_none_when_closed_and_the_window_when_open(self):
        self.assertIsNone(mkt.session(datetime.date(2026, 1, 26)))
        self.assertEqual(mkt.session(datetime.date(2026, 11, 3)),
                         (mkt.SESSION_OPEN, mkt.SESSION_CLOSE))

    def test_muhurat_day_returns_its_own_window_not_the_normal_one(self):
        # 2026-11-08 is a Sunday carrying an evening Muhurat session
        s = mkt.session(datetime.date(2026, 11, 8))
        self.assertIsNotNone(s)
        self.assertEqual(s, (datetime.time(18, 15), datetime.time(19, 15)))

    def test_the_calendar_is_the_official_list(self):
        self.assertTrue(mkt.CALENDAR_VERIFIED)
        self.assertEqual(mkt.calendar_warning(), "")        # nothing left to warn about

    def test_every_holiday_row_is_official_not_an_estimate(self):
        conf = mkt.holiday_confidence()
        self.assertEqual(len(conf), 16)
        self.assertEqual(sorted(set(conf.values())), ["official"])

    def test_the_official_dates_that_my_estimates_had_wrong(self):
        """These five were guessed before the published list arrived: three
        lunar dates off by one, one holiday that is not on the list at all,
        and one that falls on a Saturday. Pinned so they cannot drift back."""
        h = mkt.load_holidays()
        self.assertIn("2026-03-03", h)          # Holi, not 03-04
        self.assertNotIn("2026-03-04", h)
        self.assertIn("2026-05-28", h)          # Bakri Id, not 05-27
        self.assertNotIn("2026-05-27", h)
        self.assertIn("2026-11-10", h)          # Diwali-Balipratipada, not 11-09
        self.assertNotIn("2026-11-09", h)
        self.assertNotIn("2026-03-21", h)       # Ramzan Id is not on the list
        self.assertNotIn("2026-08-15", h)       # Independence Day is a Saturday

    def test_the_dates_only_the_official_list_could_supply(self):
        h = mkt.load_holidays()
        self.assertIn("Municipal Corporation Election", h["2026-01-15"])
        self.assertIn("Ram Navami", h["2026-03-26"])
        self.assertIn("Muharram", h["2026-06-26"])

    def test_the_muhurat_date_is_official_but_its_window_is_not(self):
        import datetime as _d
        sp = mkt.load_special_sessions()["2026-11-08"]
        self.assertEqual(sp["date_confidence"], "official")
        self.assertEqual(sp["window_confidence"], "placeholder")
        warn = mkt.calendar_warning(_d.date(2026, 11, 8))
        self.assertIn("placeholder", warn)
        self.assertIn("circular", warn)

    def test_an_ordinary_day_warns_about_nothing(self):
        import datetime as _d
        self.assertEqual(mkt.calendar_warning(_d.date(2026, 11, 3)), "")

    def test_a_year_with_no_calendar_warns_loudly(self):
        w = mkt.calendar_warning(datetime.date(2031, 6, 2))
        self.assertIn("2031", w)


class Money(unittest.TestCase):
    def test_indian_digit_grouping(self):
        self.assertEqual(mkt.group_indian("1234567"), "12,34,567")
        self.assertEqual(mkt.group_indian("100000"), "1,00,000")
        self.assertEqual(mkt.group_indian("999"), "999")
        self.assertEqual(mkt.group_indian("1000"), "1,000")
        self.assertEqual(mkt.group_indian("10000000"), "1,00,00,000")

    def test_format_inr(self):
        self.assertEqual(mkt.format_inr(1234567.89), "₹12,34,567.89")
        self.assertEqual(mkt.format_inr(2913.44), "₹2,913.44")
        self.assertEqual(mkt.format_inr(5000), "₹5,000")
        self.assertEqual(mkt.format_inr(-46.88), "-₹46.88")

    def test_lakh_and_crore_words(self):
        self.assertEqual(mkt.format_inr_words(1234567), "₹12.35 lakh")
        self.assertEqual(mkt.format_inr_words(12345678), "₹1.23 crore")
        self.assertEqual(mkt.format_inr_words(5000), "₹5,000")

    def test_currency_is_inr(self):
        self.assertEqual(mkt.CURRENCY_CODE, "INR")
        self.assertEqual(mkt.RUPEE, "₹")


class Ticks(unittest.TestCase):
    def test_default_tick(self):
        self.assertEqual(mkt.tick_size(1500), 0.05)

    def test_rounding_respects_the_tick(self):
        self.assertEqual(mkt.round_to_tick(1500.03), 1500.05)
        self.assertEqual(mkt.round_to_tick(1500.02), 1500.0)

    def test_a_stop_rounds_down_and_a_target_rounds_up(self):
        # rounding must never tighten a long's stop or shrink its target
        self.assertEqual(mkt.round_to_tick(1499.97, "down"), 1499.95)
        self.assertEqual(mkt.round_to_tick(1510.01, "up"), 1510.05)

    def test_rounding_an_exact_tick_is_a_no_op(self):
        for mode in ("nearest", "down", "up"):
            self.assertEqual(mkt.round_to_tick(1500.05, mode), 1500.05, mode)


class Bands(unittest.TestCase):
    def test_band_edges(self):
        lo, hi = mkt.band_edges(1000.0, 10.0)
        self.assertEqual((lo, hi), (900.0, 1100.0))

    def test_a_stock_at_its_upper_circuit_is_not_tradable_as_a_breakout(self):
        self.assertEqual(mkt.circuit_state(1100.0, 1000.0, 10.0), "at_upper")
        self.assertEqual(mkt.circuit_state(900.0, 1000.0, 10.0), "at_lower")

    def test_near_circuit_is_flagged_so_the_scanner_can_skip_it(self):
        self.assertEqual(mkt.circuit_state(1095.0, 1000.0, 10.0), "near_upper")
        self.assertEqual(mkt.circuit_state(905.0, 1000.0, 10.0), "near_lower")

    def test_mid_band_is_clear(self):
        self.assertEqual(mkt.circuit_state(1000.0, 1000.0, 10.0), "clear")
        self.assertEqual(mkt.circuit_state(1050.0, 1000.0, 10.0), "clear")

    def test_no_previous_close_does_not_crash_the_filter(self):
        self.assertEqual(mkt.circuit_state(1000.0, None, 10.0), "clear")

    def test_the_bands_are_the_ones_nse_publishes(self):
        """NSE's own sec_list.csv carries a 40 band as well as the usual four,
        so the tuple is what the exchange publishes, not the textbook set."""
        self.assertEqual(mkt.PRICE_BANDS_PCT, (2.0, 5.0, 10.0, 20.0, 40.0))
        self.assertEqual(mkt.MARKET_WIDE_BREAKER_PCT, (10.0, 15.0, 20.0))

    def test_every_band_in_the_universe_is_one_the_config_knows(self):
        seen = {r["band_pct"] for r in mkt.load_universe() if r["band_pct"]}
        self.assertTrue(seen)
        for b in sorted(seen):
            self.assertIn(b, mkt.PRICE_BANDS_PCT, "band %s is in the data but not the config" % b)


class Series(unittest.TestCase):
    """EQ can be traded intraday. BE and BZ settle by compulsory delivery, so
    an MIS order in one is rejected by the broker. No US equivalent."""

    def test_eq_allows_intraday(self):
        self.assertTrue(mkt.series_allows_intraday("EQ"))
        self.assertEqual(mkt.series_note("EQ"), "")

    def test_trade_for_trade_series_do_not(self):
        for s in ("BE", "BZ"):
            self.assertFalse(mkt.series_allows_intraday(s), s)
            note = mkt.series_note(s)
            self.assertIn("trade-for-trade", note)
            self.assertIn("compulsory delivery", note)
            self.assertIn("MIS", note)

    def test_an_unknown_series_is_not_assumed_tradable(self):
        self.assertFalse(mkt.series_allows_intraday("SM"))
        self.assertIn("not a rolling-settlement", mkt.series_note("SM"))

    def test_the_universe_carries_the_series_and_the_flag(self):
        rows = {r["symbol"]: r for r in mkt.load_universe()}
        eq = [r for r in rows.values() if r["series"] == "EQ"]
        tft = [r for r in rows.values() if r["series"] in mkt.TRADE_FOR_TRADE_SERIES]
        self.assertGreater(len(eq), 2000)
        self.assertGreater(len(tft), 100)
        self.assertTrue(all(r["intraday"] for r in eq))
        self.assertFalse(any(r["intraday"] for r in tft))

    def test_filtering_to_intraday_drops_exactly_those(self):
        all_syms = set(mkt.universe_symbols())
        intraday = set(mkt.universe_symbols(intraday_only=True))
        dropped = all_syms - intraday
        self.assertTrue(dropped)
        rows = {r["symbol"]: r for r in mkt.load_universe()}
        for s in dropped:
            self.assertIn(rows[s]["series"], mkt.TRADE_FOR_TRADE_SERIES, s)

    def test_surveillance_can_be_excluded(self):
        with_s = set(mkt.universe_symbols(intraday_only=True))
        without = set(mkt.universe_symbols(intraday_only=True, exclude_surveillance=True))
        self.assertTrue(with_s - without)
        rows = {r["symbol"]: r for r in mkt.load_universe()}
        for s in with_s - without:
            self.assertTrue(rows[s]["surveillance"], s)


class OfficialUniverse(unittest.TestCase):
    def test_it_is_nses_list_not_a_template(self):
        rows = mkt.load_universe()
        self.assertGreater(len(rows), 2000)
        self.assertEqual(sorted({r["confidence"] for r in rows}), ["official"])

    def test_no_band_means_fno_not_unknown(self):
        """NSE publishes no fixed band for a security in the F&O segment, so a
        blank band on a cash equity means dynamic range, and the importer tags
        it fno."""
        rows = [r for r in mkt.load_universe() if not r["band_pct"]]
        self.assertTrue(rows)
        self.assertTrue(all("fno" in r["indices"] for r in rows))

    def test_most_symbols_do_have_a_band_the_scanner_can_use(self):
        rows = mkt.load_universe()
        banded = [r for r in rows if r["band_pct"]]
        self.assertGreater(len(banded), 2000)

    def test_isins_came_through(self):
        r = next(x for x in mkt.load_universe() if x["symbol"] == "RELIANCE")
        self.assertTrue(r["isin"].startswith("INE"))

    def test_the_warning_now_only_names_the_missing_index_membership(self):
        w = mkt.universe_warning()
        self.assertIn("NIFTY", w)
        self.assertIn("niftyindices.com", w)
        self.assertNotIn("written from memory", w)


class Symbols(unittest.TestCase):
    def test_canonical_form_is_the_bare_nse_symbol(self):
        self.assertEqual(mkt.normalise_symbol(" reliance.ns "), "RELIANCE")
        self.assertEqual(mkt.normalise_symbol("TCS.BO"), "TCS")
        self.assertEqual(mkt.normalise_symbol("infy"), "INFY")

    def test_mapping_out_to_yahoo(self):
        self.assertEqual(mkt.to_yahoo("RELIANCE"), "RELIANCE.NS")
        self.assertEqual(mkt.to_yahoo("RELIANCE", "BSE"), "RELIANCE.BO")
        self.assertEqual(mkt.to_yahoo("reliance.ns", "NSE"), "RELIANCE.NS")

    def test_an_unknown_exchange_is_refused(self):
        with self.assertRaises(ValueError):
            mkt.to_yahoo("RELIANCE", "NASDAQ")

    def test_reading_the_exchange_back_off_a_symbol(self):
        self.assertEqual(mkt.exchange_of_yahoo("RELIANCE.BO"), "BSE")
        self.assertEqual(mkt.exchange_of_yahoo("RELIANCE.NS"), "NSE")
        self.assertEqual(mkt.exchange_of_yahoo("RELIANCE"), "NSE")


class Rules(unittest.TestCase):
    def test_intraday_shorts_allowed_but_never_overnight_in_cash(self):
        self.assertTrue(mkt.INTRADAY_SHORT_ALLOWED)
        self.assertFalse(mkt.OVERNIGHT_SHORT_ALLOWED_CASH)

    def test_settlement_is_t_plus_1(self):
        self.assertEqual(mkt.SETTLEMENT_CYCLE, "T+1")

    def test_products_and_order_types_are_indian(self):
        self.assertEqual(mkt.PRODUCT_INTRADAY, "MIS")
        self.assertEqual(mkt.PRODUCT_DELIVERY, "CNC")
        self.assertIn("SL-M", mkt.ORDER_TYPES)

    def test_benchmarks_are_indian_indices(self):
        self.assertEqual(mkt.PRIMARY_BENCHMARK, "NIFTY 50")
        self.assertEqual(mkt.BENCHMARKS["NIFTY 50"], "^NSEI")
        self.assertIn("INDIA VIX", mkt.BENCHMARKS)
        self.assertNotIn("SPY", mkt.BENCHMARKS)

    def test_fno_is_reserved_but_off(self):
        self.assertFalse(mkt.FNO_ENABLED)

    def test_speculative_income_note_is_present_and_disclaims_advice(self):
        self.assertIn("speculative", mkt.TAX_TREATMENT_NOTE)
        self.assertIn("Not tax advice", mkt.TAX_TREATMENT_NOTE)

    def test_every_cost_rate_and_market_rule_is_on_the_verify_checklist(self):
        items = mkt.verify_list()
        self.assertTrue(items)
        covered = " ".join(w for _, w, _ in items)
        for key in mkt.COSTS:
            self.assertIn(key, covered, key)
        for area in ("sessions", "calendar", "tick", "bands", "margin", "settlement"):
            self.assertIn(area, [a for a, _, _ in items], area)

    def test_no_us_leftovers_in_the_config(self):
        src = open(os.path.join(ROOT, "config", "india_market.py"), encoding="utf-8").read()
        for bad in ("NYSE_HOLIDAYS", "eastern_offset", "pdt_guard", "25000", "alpaca"):
            self.assertNotIn(bad, src, bad)


if __name__ == "__main__":
    unittest.main()
