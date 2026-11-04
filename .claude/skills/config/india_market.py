"""India market rules for the trader-* skills: one source of truth.

Nothing in here is advice and nothing here places an order. It is constants
plus small pure helpers, standard library only, so the calculation tools stay
dependency-free the way upstream's did.

HOW TO READ THE "VERIFY" MARKERS
--------------------------------
Every numeric value a trade depends on carries a "VERIFY:" comment naming the
official source it must be checked against (NSE, BSE, SEBI, or your broker's
published tariff). The values here are sensible defaults, not authority. Rates
change by circular, sometimes mid-year. Check them before you risk money, and
re-check them each year.

Run `python3 config/india_market.py` to print every value with its marker.
"""

import csv
import datetime
import math
import os

# ---------------------------------------------------------------- exchanges

NSE = "NSE"
BSE = "BSE"
EXCHANGES = (NSE, BSE)
DEFAULT_EXCHANGE = NSE

# Yahoo Finance suffixes. The canonical symbol everywhere in these skills is
# the bare NSE trading symbol (RELIANCE, TCS, INFY); adapters map it outward.
YAHOO_SUFFIX = {NSE: ".NS", BSE: ".BO"}

# Index symbols on Yahoo. VERIFY: confirm each still resolves; Yahoo renames
# index tickers occasionally. Source: finance.yahoo.com lookup.
BENCHMARKS = {
    "NIFTY 50": "^NSEI",
    "NIFTY BANK": "^NSEBANK",
    "NIFTY IT": "^CNXIT",
    "NIFTY FMCG": "^CNXFMCG",
    "NIFTY AUTO": "^CNXAUTO",
    "NIFTY PHARMA": "^CNXPHARMA",
    "NIFTY METAL": "^CNXMETAL",
    "INDIA VIX": "^INDIAVIX",
    "SENSEX": "^BSESN",
}
PRIMARY_BENCHMARK = "NIFTY 50"
VOLATILITY_INDEX = "INDIA VIX"

# ---------------------------------------------------------------- timezone

# IST is UTC+05:30 all year. India observes no daylight saving, so unlike the
# New York logic this replaces there is no DST branch and no ambiguous hour.
IST_OFFSET_MINUTES = 330
IST_NAME = "Asia/Kolkata"


def to_ist(utc):
    """Naive/aware UTC datetime -> naive IST datetime."""
    return utc.replace(tzinfo=None) + datetime.timedelta(minutes=IST_OFFSET_MINUTES)


def ist_to_utc(local):
    """Naive IST datetime -> naive UTC datetime."""
    return local - datetime.timedelta(minutes=IST_OFFSET_MINUTES)


def now_ist():
    return to_ist(datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0))


def ts_to_ist(epoch_seconds):
    """A Yahoo chart epoch timestamp -> naive IST datetime."""
    return to_ist(datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=int(epoch_seconds)))


# ---------------------------------------------------------------- sessions

# VERIFY: all four. Source: NSE Capital Market segment trading hours,
# https://www.nseindia.com/market-data/market-hours
PRE_OPEN_START = datetime.time(9, 0)     # order collection begins
PRE_OPEN_END = datetime.time(9, 15)      # call auction done, continuous trading starts
SESSION_OPEN = datetime.time(9, 15)
SESSION_CLOSE = datetime.time(15, 30)

# The post-close session (15:40-16:00) exists for delivery orders at the
# closing price. Intraday day trading does not use it, so the skills ignore it.
# VERIFY if you ever add CNC closing-price orders. Source: as above.
POST_CLOSE_START = datetime.time(15, 40)
POST_CLOSE_END = datetime.time(16, 0)

SESSION_MINUTES = 375  # 09:15 to 15:30

# Brokers auto-square-off open MIS (intraday) positions before the close, and
# the exact time is the broker's choice, not the exchange's. Anything still
# open is closed at market by them, at whatever price is there.
# VERIFY per broker: Zerodha, Upstox, Angel One and Dhan all publish this and
# they differ, typically 15:15 to 15:25 for equity intraday.
BROKER_SQUARE_OFF = datetime.time(15, 20)

# The skills aim to be flat with a margin of safety before the broker acts.
FLAT_BY = datetime.time(15, 15)

# No new intraday entries outside this window. The late bound keeps a trade
# from being opened with no room to work before square-off.
NO_ENTRIES_BEFORE = datetime.time(9, 20)   # let the opening auction settle
NO_ENTRIES_AFTER = datetime.time(14, 45)


def bars_per_session(interval_minutes):
    """Full-session bar count: 75 at 5m, 375 at 1m. Replaces the US 78."""
    return SESSION_MINUTES // int(interval_minutes)


def minutes_from_open(t):
    """Minutes since 09:15 for a datetime.time, negative before the open."""
    return (t.hour * 60 + t.minute) - (SESSION_OPEN.hour * 60 + SESSION_OPEN.minute)


# ---------------------------------------------------------------- calendar

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
HOLIDAY_FILE = os.path.join(_DATA, "nse_holidays.csv")
SPECIAL_FILE = os.path.join(_DATA, "nse_special_sessions.csv")


def _read_csv(path, require=None):
    """Rows of a data file, comment lines dropped.

    require: a column that must be non-empty for the row to count, so one
    reader can serve files with different shapes.
    """
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(ln for ln in fh if not ln.lstrip().startswith("#"))]
    if require:
        rows = [r for r in rows if (r.get(require) or "").strip()]
    return rows


def load_holidays(path=None):
    """{date_iso: name}. VERIFY the file itself: see its header."""
    return dict((r["date"], r.get("name") or "holiday")
                for r in _read_csv(path or HOLIDAY_FILE, "date"))


def holiday_confidence(path=None):
    """{date_iso: 'fixed'|'estimate'}. 'estimate' dates are NOT authoritative."""
    return dict((r["date"], (r.get("confidence") or "estimate"))
                for r in _read_csv(path or HOLIDAY_FILE, "date"))


def load_special_sessions(path=None):
    """{date_iso: {'name','kind','open','close'}} for Muhurat and the like."""
    out = {}
    for r in _read_csv(path or SPECIAL_FILE, "date"):
        out[r["date"]] = {"name": r.get("name") or "", "kind": r.get("kind") or "special",
                          "open": r.get("open") or "", "close": r.get("close") or "",
                          "date_confidence": (r.get("date_confidence") or "estimate").strip(),
                          "window_confidence": (r.get("window_confidence") or "estimate").strip()}
    return out


# data/nse_holidays.csv now holds NSE's published 2026 list, supplied by the
# repo owner on 2026-11-04, so the holiday dates are no longer estimates.
# Set this back to False if you replace the file with anything unchecked, and
# raise CALENDAR_YEARS when you add another year.
CALENDAR_VERIFIED = True

CALENDAR_YEARS = (2026,)  # years the shipped file covers at all


def calendar_warning(d=None):
    """A sentence to print when something about the calendar cannot be trusted.

    Three separate things can be wrong, and they clear independently:
    the year may not be covered at all, the holiday list may be unverified,
    or a special session's date may be official while its WINDOW is still a
    placeholder (which is exactly where NSE leaves Muhurat until it issues the
    timing circular).
    """
    if d is not None and d.year not in CALENDAR_YEARS:
        return ("No NSE holiday list for %d: every weekday looks like a trading day. "
                "Update config/data/nse_holidays.csv and CALENDAR_YEARS." % d.year)
    if not CALENDAR_VERIFIED:
        return ("The NSE holiday list has not been verified against NSE's circular "
                "(config.india_market.CALENDAR_VERIFIED is False); religious-holiday "
                "dates in it are estimates.")
    if d is not None:
        sp = load_special_sessions().get(d.isoformat())
        if sp and sp.get("window_confidence") != "official":
            return ("%s on %s: the date is official but the %s to %s window is a "
                    "placeholder, because NSE notifies Muhurat timings in a separate "
                    "circular. Check it before trading that session."
                    % (sp.get("name") or "special session", d.isoformat(),
                       sp.get("open"), sp.get("close")))
    return ""


def trading_day(d, holidays=None):
    """(is_trading_day, reason) for a datetime.date. Weekends plus the list."""
    if d.weekday() >= 5:
        return False, "weekend"
    name = (holidays if holidays is not None else load_holidays()).get(d.isoformat())
    if name:
        return False, "NSE holiday: " + name
    return True, ""


def session(d, holidays=None):
    """(open_time, close_time) in IST, or None when closed all day.

    A Muhurat day is closed for the normal session and open for its own
    window, so it returns that window.
    """
    special = load_special_sessions().get(d.isoformat())
    if special and special.get("open") and special.get("close"):
        return (_parse_hhmm(special["open"]), _parse_hhmm(special["close"]))
    ok, _ = trading_day(d, holidays)
    return (SESSION_OPEN, SESSION_CLOSE) if ok else None


def _parse_hhmm(text):
    h, m = str(text).strip().split(":")[:2]
    return datetime.time(int(h), int(m))


UNIVERSE_FILE = os.path.join(_DATA, "nse_universe.csv")

KNOWN_INDICES = ("nifty50", "nifty500", "fno")


def load_universe(path=None):
    """[{symbol, name, sector, indices, band_pct, confidence}], from the data file.

    VERIFY the file: index membership changes at every review, and the shipped
    rows were written from memory, not downloaded. Every row says so.
    """
    out = []
    for r in _read_csv(path or UNIVERSE_FILE, "symbol"):
        sym = normalise_symbol(r.get("symbol"))
        if not sym:
            continue
        band = (r.get("band_pct") or "").strip()
        series = (r.get("series") or "").strip().upper()
        out.append({"symbol": sym, "name": (r.get("name") or "").strip(),
                    "sector": (r.get("sector") or "").strip().lower(),
                    "indices": [i for i in (r.get("indices") or "").lower().split("|") if i],
                    "band_pct": float(band) if band else None,
                    "series": series,
                    "intraday": (r.get("intraday") or "").strip().lower() == "yes"
                                if r.get("intraday") else series_allows_intraday(series),
                    "surveillance": (r.get("surveillance") or "").strip(),
                    "isin": (r.get("isin") or "").strip(),
                    "confidence": (r.get("confidence") or "estimate").strip()})
    return out


def universe_symbols(index=None, sector=None, path=None, intraday_only=False,
                     exclude_surveillance=False):
    """Symbols in an index (nifty50, nifty500, fno) and/or a sector.

    intraday_only drops the trade-for-trade series, which cannot be traded
    MIS at all. exclude_surveillance drops anything NSE has GSM-flagged.
    """
    rows = load_universe(path)
    if index:
        key = str(index).lower().replace(" ", "").replace("-", "")
        rows = [r for r in rows if key in r["indices"]]
    if sector:
        want = str(sector).lower()
        rows = [r for r in rows if r["sector"] == want]
    if intraday_only:
        rows = [r for r in rows if r["intraday"]]
    if exclude_surveillance:
        rows = [r for r in rows if not r["surveillance"]]
    return [r["symbol"] for r in rows]


def universe_warning(path=None):
    """A sentence about whatever is still missing from the universe, else ''.

    Two independent things can be wrong: the rows themselves may be guessed,
    or they may be official while index membership is still absent (NSE's own
    files do not carry NIFTY constituents).
    """
    rows = load_universe(path)
    est = [r for r in rows if r["confidence"] not in ("official", "verified")]
    if est:
        return ("The NSE universe in config/data/nse_universe.csv is not from NSE: "
                "%d of %d rows are unverified. Rebuild it with "
                "config/import_nse.py." % (len(est), len(rows)))
    have_index = any(i in r["indices"] for r in rows for i in ("nifty50", "nifty500"))
    if not have_index:
        return ("The universe is NSE's official list, but NIFTY 50 and NIFTY 500 "
                "membership is not in NSE's files and is unset, so --index nifty50 "
                "matches nothing. Supply it from niftyindices.com: "
                "config/import_nse.py --indices FILE.")
    return ""


# ---------------------------------------------------------------- currency

RUPEE = "₹"
CURRENCY_CODE = "INR"
LAKH = 100000
CRORE = 10000000


def group_indian(digits):
    """'1234567' -> '12,34,567': last three digits, then pairs."""
    s = str(digits)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


def format_inr(x, paise=True):
    """12,34,567.89 with the rupee sign, Indian digit grouping."""
    neg = float(x) < 0
    v = abs(float(x))
    whole = int(v)
    if paise and abs(v - whole) >= 0.005:
        body = "%s.%02d" % (group_indian(whole), int(round((v - whole) * 100)))
    else:
        body = group_indian(int(round(v)))
    return ("-" if neg else "") + RUPEE + body


def format_inr_price(x):
    """A price always carries its paise: 4,008.00, never 4,008."""
    neg = float(x) < 0
    v = abs(float(x))
    whole = int(v)
    return ("-" if neg else "") + RUPEE + "%s.%02d" % (group_indian(whole),
                                                       int(round((v - whole) * 100)))


def format_inr_words(x):
    """Report-friendly scale: 'Rs 12.35 lakh', 'Rs 1.23 crore'."""
    v = float(x)
    a = abs(v)
    if a >= CRORE:
        return "%s%.2f crore" % (RUPEE, v / CRORE)
    if a >= LAKH:
        return "%s%.2f lakh" % (RUPEE, v / LAKH)
    return format_inr(v)


# ---------------------------------------------------------------- tick size

# VERIFY: Source: NSE Capital Market trading parameters, and SEBI's circular
# on tick size. Most equities tick at 0.05; SEBI has allowed 0.01 for some
# lower-priced scrips, so confirm per symbol if it matters to your stop.
DEFAULT_TICK = 0.05
TICK_BY_PRICE = (
    # (upper price bound exclusive, tick)
    (250.0, 0.01),
    (float("inf"), 0.05),
)


def tick_size(price):
    for bound, tick in TICK_BY_PRICE:
        if float(price) < bound:
            return tick
    return DEFAULT_TICK


def round_to_tick(price, mode="nearest"):
    """Round a price to a tradable tick. mode: nearest | down | up.

    Stops for a long round DOWN (away from the entry) and targets round UP,
    so rounding never quietly tightens a stop.
    """
    t = tick_size(price)
    n = float(price) / t
    if mode == "down":
        n = math.floor(n + 1e-9)
    elif mode == "up":
        n = math.ceil(n - 1e-9)
    else:
        n = math.floor(n + 0.5 + 1e-9)
    return round(n * t, 2)


# ---------------------------------------------------------------- price bands

# Per-stock daily price bands (circuit limits). A stock at its band cannot
# trade past it, so a breakout entry there can fill badly or not at all, and a
# stop below the lower band cannot be hit.
# VERIFY: Source: NSE "Price Bands" daily file and BSE equivalent. Securities
# in the F&O segment have no fixed band but do have a dynamic operating range.
# NSE's own sec_list.csv carries a 40 band as well as the usual four, so the
# tuple below is what the exchange publishes, not the textbook set.
PRICE_BANDS_PCT = (2.0, 5.0, 10.0, 20.0, 40.0)
FNO_STOCKS_HAVE_NO_BAND = True
FNO_DYNAMIC_OPERATING_RANGE_PCT = 10.0  # VERIFY: NSE operating-range circular

# Skip a setup when the last price sits within this much of either band. A
# breakout into a ceiling is not a breakout.
CIRCUIT_PROXIMITY_PCT = 1.0


def band_edges(prev_close, band_pct):
    """(lower, upper) circuit prices for a previous close and band percent."""
    p, b = float(prev_close), float(band_pct) / 100.0
    return round_to_tick(p * (1 - b), "up"), round_to_tick(p * (1 + b), "down")


def circuit_state(price, prev_close, band_pct, proximity_pct=None):
    """'clear' | 'near_upper' | 'near_lower' | 'at_upper' | 'at_lower'.

    Scanner and entry skip anything that is not 'clear'.
    """
    if not prev_close or not band_pct:
        return "clear"
    lo, hi = band_edges(prev_close, band_pct)
    prox = (proximity_pct if proximity_pct is not None else CIRCUIT_PROXIMITY_PCT) / 100.0
    p = float(price)
    if p >= hi:
        return "at_upper"
    if p <= lo:
        return "at_lower"
    if p >= hi * (1 - prox):
        return "near_upper"
    if p <= lo * (1 + prox):
        return "near_lower"
    return "clear"


# Market-wide circuit breakers halt the whole market, not one stock.
# VERIFY: Source: SEBI/NSE index-based market-wide circuit breaker circular.
# The halt length depends on which level trips and at what time of day.
MARKET_WIDE_BREAKER_PCT = (10.0, 15.0, 20.0)
MARKET_WIDE_BREAKER_INDICES = ("NIFTY 50", "SENSEX")  # whichever breaches first

# ---------------------------------------------------------------- margin

# Intraday leverage is capped by SEBI's margin framework (VAR + ELM, with
# peak-margin reporting), and brokers may offer less than the cap.
# VERIFY per broker and per stock: Source: SEBI margin circulars and your
# broker's margin/leverage page. Do not treat this as a promise of leverage.
INTRADAY_MARGIN_MULTIPLIER = 5.0        # VERIFY: broker's MIS leverage for liquid equity
DELIVERY_MARGIN_MULTIPLIER = 1.0        # CNC is fully paid
MARGIN_SOURCE_NOTE = "SEBI VAR+ELM framework; broker may give less"

# ---------------------------------------------------------------- settlement

# Indian cash equity settles T+1 (rolling). It matters for CNC: shares sold
# today are deliverable against today's settlement, and cash from a sale is
# usable per your broker's rules.
# VERIFY: Source: SEBI T+1 rolling settlement circular.
SETTLEMENT_CYCLE = "T+1"

# ---------------------------------------------------------------- shorting

# Intraday short selling IS allowed in cash equity and MUST be squared off the
# same day: there is no overnight short in the cash segment (that needs SLB or
# the F&O segment). This is the opposite default from the US module it
# replaces, where "allow_short" meant selling more than you hold.
# VERIFY: Source: SEBI short-selling framework and NSE SLB.
INTRADAY_SHORT_ALLOWED = True
OVERNIGHT_SHORT_ALLOWED_CASH = False

# ---------------------------------------------------------------- products

# SERIES, and whether intraday is possible at all.
# EQ is rolling settlement and can be traded intraday. BE and BZ are
# TRADE-FOR-TRADE: every trade settles by compulsory delivery, so an MIS
# order in one is rejected by the broker. 235 of NSE's 2,627 cash symbols sit
# in that state. The US pack has no equivalent concept.
# VERIFY: Source: NSE's series definitions and its surveillance circulars.
INTRADAY_SERIES = ("EQ",)
TRADE_FOR_TRADE_SERIES = ("BE", "BZ")


def series_allows_intraday(series):
    """Can this series be traded MIS at all?"""
    return str(series or "").strip().upper() in INTRADAY_SERIES


def series_note(series):
    """Why a series cannot be traded intraday, or ''."""
    s = str(series or "").strip().upper()
    if s in TRADE_FOR_TRADE_SERIES:
        return ("series %s is trade-for-trade: every trade settles by compulsory "
                "delivery, so an intraday (%s) order is rejected" % (s, PRODUCT_INTRADAY))
    if s and s not in INTRADAY_SERIES:
        return "series %s is not a rolling-settlement cash series" % s
    return ""


PRODUCT_INTRADAY = "MIS"
PRODUCT_DELIVERY = "CNC"
ORDER_TYPES = ("MARKET", "LIMIT", "SL", "SL-M")

# ---------------------------------------------------------------- costs

# Rates for the cost calculator in config/costs.py. Intraday equity charges
# are ASYMMETRIC: STT falls on the sell side only, stamp duty on the buy side.
# Every rate here is a fraction (0.0003 == 0.03%), not a percent.
#
# VERIFY EVERY ONE OF THESE. Sources named per line. They change by circular.
COSTS = {
    # Brokerage: Zerodha-style "flat 20 or 0.03%, whichever is lower" per
    # executed order. VERIFY: your broker's tariff sheet.
    "brokerage_pct": 0.0003,
    "brokerage_flat_inr": 20.0,
    "brokerage_mode": "lower_of",          # lower_of | flat | pct
    "brokerage_per": "order",              # charged per executed order, each leg

    # Securities Transaction Tax. VERIFY: Source: Income Tax Dept / NSE
    # charges page. Intraday (non-delivery) equity: sell side only.
    "stt_intraday_sell_pct": 0.00025,      # 0.025% on sell turnover
    "stt_intraday_buy_pct": 0.0,
    "stt_delivery_pct": 0.001,             # 0.1% both sides for CNC

    # Exchange transaction charges. VERIFY: Source: NSE/BSE transaction
    # charges circular. NSE and BSE differ and NSE has revised this.
    "exchange_txn_pct_nse": 0.0000297,     # 0.00297%
    "exchange_txn_pct_bse": 0.0000375,     # VERIFY: BSE equity group rates vary

    # SEBI turnover fee. VERIFY: Source: SEBI fee circular (per crore).
    "sebi_turnover_pct": 0.000001,         # 0.0001%, i.e. 10 per crore

    # Stamp duty, buy side only. VERIFY: Source: Indian Stamp Act rates as
    # notified; intraday and delivery differ.
    "stamp_duty_intraday_buy_pct": 0.00003,   # 0.003%
    "stamp_duty_delivery_buy_pct": 0.00015,   # 0.015%

    # GST on (brokerage + exchange transaction charges + SEBI fee).
    # VERIFY: Source: current GST rate on broking services.
    "gst_pct": 0.18,

    # Depository charges apply to DELIVERY SELL only, flat per scrip per day.
    # Not charged on intraday. VERIFY: your broker + depository tariff.
    "dp_charge_delivery_sell_inr": 13.5,
}

# Intraday equity gains in India are taxed as SPECULATIVE BUSINESS INCOME,
# not capital gains, and are slab-rated. Reports carry this as a note.
# This is not tax advice. VERIFY with a qualified professional and the
# Income Tax Act as it stands in your assessment year.
TAX_TREATMENT_NOTE = ("Intraday equity profit in India is treated as speculative "
                      "business income and taxed at your slab rate, not as capital "
                      "gains. Losses have their own set-off rules. Not tax advice.")

# ---------------------------------------------------------------- F&O (later)

# Reserved for the F&O phase. Nothing reads these yet.
FNO_ENABLED = False
FNO_LOT_SIZE_FILE = os.path.join(_DATA, "fno_lot_sizes.csv")  # not shipped yet
FNO_EXPIRY_NOTE = "weekly and monthly expiries; lot sizes revised by NSE circular"

# ---------------------------------------------------------------- symbols


def normalise_symbol(sym):
    """'reliance.ns' / ' RELIANCE ' -> 'RELIANCE'. The canonical form."""
    s = str(sym or "").strip().upper()
    for suf in YAHOO_SUFFIX.values():
        if s.endswith(suf):
            return s[: -len(suf)]
    return s


def to_yahoo(sym, exchange=DEFAULT_EXCHANGE):
    """'RELIANCE' -> 'RELIANCE.NS' (or .BO for BSE)."""
    ex = str(exchange or DEFAULT_EXCHANGE).upper()
    if ex not in YAHOO_SUFFIX:
        raise ValueError("unknown exchange %r, want one of %s" % (exchange, ", ".join(EXCHANGES)))
    return normalise_symbol(sym) + YAHOO_SUFFIX[ex]


def exchange_of_yahoo(sym):
    """'RELIANCE.BO' -> 'BSE'. Defaults to NSE when there is no suffix."""
    s = str(sym or "").strip().upper()
    for ex, suf in YAHOO_SUFFIX.items():
        if s.endswith(suf):
            return ex
    return DEFAULT_EXCHANGE


# ---------------------------------------------------------------- self-report


def verify_list():
    """Every value a human must check, for the end-of-port checklist."""
    return [
        ("sessions", "PRE_OPEN_START/END, SESSION_OPEN/CLOSE", "NSE market hours page"),
        ("square-off", "BROKER_SQUARE_OFF, FLAT_BY", "your broker's intraday square-off policy"),
        ("calendar", "data/nse_holidays.csv (every row)", "NSE annual holiday circular"),
        ("calendar", "data/nse_special_sessions.csv (Muhurat window)", "NSE Muhurat circular"),
        ("tick", "DEFAULT_TICK, TICK_BY_PRICE", "NSE trading parameters, SEBI tick-size circular"),
        ("bands", "PRICE_BANDS_PCT, FNO_DYNAMIC_OPERATING_RANGE_PCT", "NSE price bands file"),
        ("bands", "MARKET_WIDE_BREAKER_PCT", "SEBI/NSE market-wide circuit breaker circular"),
        ("margin", "INTRADAY_MARGIN_MULTIPLIER", "SEBI margin framework + broker leverage page"),
        ("settlement", "SETTLEMENT_CYCLE", "SEBI T+1 circular"),
        ("shorting", "INTRADAY_SHORT_ALLOWED, OVERNIGHT_SHORT_ALLOWED_CASH", "SEBI short-selling framework"),
        ("benchmarks", "BENCHMARKS symbol map", "Yahoo Finance index symbols"),
        ("universe", "data/nse_universe.csv (every row and band)", "niftyindices.com, NSE EQUITY_L.csv, NSE price bands"),
    ] + [("costs", "COSTS[%r]" % k, "see the comment on that line") for k in sorted(COSTS)]


if __name__ == "__main__":
    print("India market config. Values needing a human check:\n")
    for area, what, src in verify_list():
        print("  VERIFY  %-12s %-44s %s" % (area, what, src))
    print("\nSession: %s-%s IST, %d minutes, %d five-minute bars."
          % (SESSION_OPEN.strftime("%H:%M"), SESSION_CLOSE.strftime("%H:%M"),
             SESSION_MINUTES, bars_per_session(5)))
    print("Square-off: broker %s, skills aim flat by %s."
          % (BROKER_SQUARE_OFF.strftime("%H:%M"), FLAT_BY.strftime("%H:%M")))
    w = calendar_warning()
    if w:
        print("\nWARNING: " + w)
