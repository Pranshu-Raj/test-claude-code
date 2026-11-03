#!/usr/bin/env python3
"""Pre-trade risk check for trader-risk. Standard library only, no network.

Reads the proposed orders (trader/orders.json) and checks every one against
your rules (trader/config.json), your account (trader/portfolio.json),
prices (trader/prices.json) and what was placed today (trader/state/broker.json).
Each order gets PASS or BLOCK with the reasons. Orders are checked in file
order as if each PASS had filled, so buys that are fine one by one but break
a limit together are caught.

    python3 check.py                                 # the trader/ files, writes trader/state/risk.json
    python3 check.py --now "2026-10-19 10:15"        # pin the clock (IST time)
    python3 check.py --orders trader/orders.json --json
    python3 check.py --no-write                      # check without recording the verdicts

Entries and exits. An ENTRY opens or adds to a position (a buy, or a sell
that would go short). An EXIT only reduces what you hold. Getting out must
always be possible: an exit is checked for format, asset and short sales
only; every other rule turns into a warning for it.

What blocks an order (every order):

  format        missing symbol or transaction_type, a quantity that is not
                whole shares, a LIMIT leg without a price, an SL or SL-M leg
                without a trigger price, an unknown order type or product,
                a trade whose stop or target leg is missing
  asset         derivatives (F&O instruments) while "allow_options" is false;
                anything that is not a cash-segment equity
  short         an overnight short in the cash segment is impossible, so a
                sell beyond what you hold must be intraday (MIS) and must be
                squared off the same day

What blocks an entry (on top):

  blocked       a ticker on your "blocked" list
  size          under min_order_inr, over max_order_inr
  position      the stock would be over max_position_pct of the account
  cash          the buy would dip into your cash buffer, or need margin
  duplicate     the same symbol and side twice in this batch, or already
                placed today (trader/state/broker.json)
  daily limit   max_orders_per_day orders placed today
  stop          no stop while "require_stop": true (a bracket stop_loss_stop_price
                or the order's plan stop); a stop on the wrong side of the entry
  loss          the loss at the stop over max_loss_per_trade_inr. With
                "max_loss_includes_costs" (the default) that loss INCLUDES
                brokerage, STT, exchange and SEBI charges, stamp duty and GST,
                so the limit means what you actually lose. The entry is the
                limit price for a limit order, else the HIGHER of the plan's
                entry and a fresh price (under 15 minutes old), so a stale
                plan cannot understate the risk
  reward:risk   (target - entry) / (entry - stop) under min_reward_risk
  daily loss    the losses at the stops of today's entries plus this one over
                max_daily_loss_inr (win or lose, each counts in full), charges
                included
  trades        max_trades_per_day entries already placed today
  window        before no_entries_before or after no_entries_after (IST), or
                the market is closed (weekend, NSE holiday, outside 09:15 to
                15:30). Never later than the broker square-off either: an
                entry with no room to work before the broker closes it is
                refused
  circuit       the stock is at or near its daily price band, so the move the
                setup needs cannot happen

There is no pattern-day-trader rule in India, so pdt_guard is gone. Indian
cash equity settles T+1 and intraday shorts are legal provided they are
squared off the same session.

Exit code 0 when every order passes, 1 when any order is blocked.
The verdicts go to trader/state/risk.json with a fingerprint of each order,
so trader-broker only shows an order as ready if THIS exact order passed today.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import brokers, costs as inr_costs, india_market as mkt

DEFAULTS = {
    "budget": None, "cash_buffer_pct": 10, "max_position_pct": 75, "max_order_inr": 150000,
    "min_order_inr": 500, "max_loss_per_trade_inr": 1000, "max_daily_loss_inr": 3000,
    "max_trades_per_day": 3, "max_orders_per_day": 10, "min_reward_risk": 1.0,
    "require_stop": True, "max_loss_includes_costs": True,
    "no_entries_before": mkt.NO_ENTRIES_BEFORE.strftime("%H:%M"),
    "no_entries_after": mkt.NO_ENTRIES_AFTER.strftime("%H:%M"),
    "flat_by": mkt.FLAT_BY.strftime("%H:%M"),
    "exchange": mkt.DEFAULT_EXCHANGE, "product": mkt.PRODUCT_INTRADAY,
    "broker": brokers.DEFAULT_BROKER,
    "paper": True, "blocked": [], "allow_options": False, "allow_margin": False,
    "allow_short": False,
}
ORDER_FIELDS = brokers.KITE_FIELDS
PRICE_FIELDS = ("quantity", "price", "trigger_price")
TYPES = {"MARKET", "LIMIT", "SL", "SL-M"}
PRODUCTS = {mkt.PRODUCT_INTRADAY, mkt.PRODUCT_DELIVERY}
VALIDITIES = {"DAY", "IOC", "TTL"}
# an NSE trading symbol: letters and digits, with & - and the series suffixes
TICKER = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")
# an F&O instrument carries an expiry and a strike or FUT in the symbol
FNO = re.compile(r"^[A-Z]+\d{2}[A-Z]{3}\d*(CE|PE|FUT)$")
HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
CENT = 0.005
FRESH_MINUTES = 15
REF_WORDS = {"plan": "the plan's entry", "price": "the latest price", "limit": "the limit price",
             "stop price": "the stop price", "stale price": "an old price"}

class RiskError(Exception):
    pass


# ---------------------------------------------------------------- time (IST, no tz files)

# IST is a fixed +05:30 with no daylight saving.
CALENDAR_YEARS = mkt.CALENDAR_YEARS


def to_ist(utc):
    return mkt.to_ist(utc)


def ist_to_utc(local):
    return mkt.ist_to_utc(local)


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as IST time."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise RiskError("--now wants a time like \"2026-10-19 10:15\" (IST) or ...Z (UTC)")
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_utc(text):
    if not text:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt


def trading_day(d):
    """(is_trading_day, reason) from the shared NSE calendar."""
    return mkt.trading_day(d)



def hhmm(value, key):
    """"09:45" -> time(9, 45); None or "" -> None (that bound is off)."""
    if value in (None, "", False):
        return None
    m = HHMM.match(str(value).strip())
    if not m:
        raise RiskError("%s must be a IST time like \"09:45\", got %r" % (key, value))
    return datetime.time(int(m.group(1)), int(m.group(2)))


def _shift(t, hours):
    dt = datetime.datetime.combine(datetime.date(2000, 1, 1), t) - datetime.timedelta(hours=hours)
    return dt.time()


def window_check(now_et, c):
    """Reasons an entry cannot be placed now (IST time). Empty when the window is off."""
    before = hhmm(c.get("no_entries_before"), "no_entries_before")
    after = hhmm(c.get("no_entries_after"), "no_entries_after")
    if before is None and after is None:
        return [], []
    warns = []
    d, t = now_et.date(), now_et.time().replace(second=0, microsecond=0)
    if d.year not in CALENDAR_YEARS:
        warns.append(mkt.calendar_warning(d))
    ok, why = trading_day(d)
    stamp = now_et.strftime("%a %Y-%m-%d %H:%M")
    if not ok:
        return ["window: the market is closed today (%s, %s IST)" % (why, stamp)], warns
    s = mkt.session(d)
    open_t, close = (s if s else (mkt.SESSION_OPEN, mkt.SESSION_CLOSE))
    if t < open_t or now_et.time() >= close:
        return ["window: the market is closed (%s IST; the session is %s to %s)"
                % (stamp, open_t.strftime("%H:%M"), close.strftime("%H:%M"))], warns
    # India: an entry must have room before the broker squares it off, so the
    # window never runs later than that, whatever the config says.
    squared = mkt.BROKER_SQUARE_OFF
    if after is None or after >= squared:
        if after is not None and after > squared:
            warns.append("no_entries_after %s is past your broker square-off %s: pulled back"
                         % (after.strftime("%H:%M"), squared.strftime("%H:%M")))
        after = min(after or squared, squared)
    if close != mkt.SESSION_CLOSE:
        warns.append("special session today (%s to %s), not the usual timetable"
                     % (open_t.strftime("%H:%M"), close.strftime("%H:%M")))
    if before is not None and t < before:
        return ["window: no new entries before %s IST (it is %s)" % (before.strftime("%H:%M"),
                                                                          t.strftime("%H:%M"))], warns
    if after is not None and t > after:
        return ["window: no new entries after %s IST (it is %s)" % (after.strftime("%H:%M"),
                                                                         t.strftime("%H:%M"))], warns
    return [], warns


def business_days(today, n=5):
    """The last n NSE trading days up to and including today, newest first."""
    out, d = [], today
    for _ in range(40):
        if trading_day(d)[0]:
            out.append(d.isoformat())
            if len(out) == n:
                break
        d -= datetime.timedelta(days=1)
    return out


# ---------------------------------------------------------------- files and helpers

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise RiskError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise RiskError("%s is not valid JSON: %s" % (path, e))


def fingerprint(order):
    """Shared with trader-broker, from config.brokers, so they cannot drift."""
    return brokers.fingerprint(order)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def money(x):
    """A rupee amount, Indian grouping."""
    return mkt.format_inr(float(x))


def price(x):
    """A price, always with its paise."""
    return mkt.format_inr_price(float(x))


def _cent(x):
    """A price on the NSE tick. Kept under the old name."""
    return mkt.round_to_tick(float(x))


def _p(x):
    return "%.2f" % float(x)


def account(cfg, portfolio, prices):
    """Starting account: cash and position values. No portfolio = a fresh
    account funded with the budget."""
    warnings = []
    if portfolio is None:
        budget = _f(cfg.get("budget"))
        if not budget:
            raise RiskError("no trader/portfolio.json and no budget in the config: "
                            "nothing to check the orders against")
        return {"cash": budget, "shares": {}, "values": {}}, warnings
    cash = _f(portfolio.get("cash"))
    if cash is None:
        raise RiskError("portfolio.json needs a \"cash\" number")
    shares, values = {}, {}
    for p in portfolio.get("positions", []):
        t = str(p.get("ticker") or p.get("symbol") or "").upper()
        if not t:
            continue
        n = _f(p.get("shares", p.get("qty")))
        if n is None:
            n = sum(_f(l.get("shares")) or 0 for l in p.get("lots", []))
        shares[t] = shares.get(t, 0) + n
        if prices.get(t):
            values[t] = shares[t] * prices[t]
        else:
            basis = sum(_f(l.get("cost")) or 0 for l in p.get("lots", []))
            values[t] = basis
            warnings.append("no price for %s: valued at its cost basis %s" % (t, money(basis)))
    return {"cash": cash, "shares": shares, "values": values}, warnings


def rec_day(rec):
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


def rec_is_entry(rec):
    """trader-broker logs the kind trader-risk gave; older records: a buy is an entry."""
    k = rec.get("kind")
    if k:
        return k == "entry"
    return str(rec.get("side", "")).lower() == "buy"


def placed_today(state, today):
    """Orders recorded as placed today by trader-broker."""
    out = []
    for rec in (state or {}).get("orders", []):
        if rec.get("status") == "placed" and rec_day(rec) == today:
            out.append(rec)
    return out


def day_trades(state, today):
    """Day trades in the last 5 business days: a (day, symbol) with both a buy
    and a sell placed or filled that day. An entry placed today counts too: it
    is meant to be flat by the close. Returns (count, sorted list of "day SYM")."""
    days = set(business_days(datetime.date.fromisoformat(today)))
    sides = {}
    for rec in (state or {}).get("orders", []):
        if rec.get("status") not in ("placed", "filled"):
            continue
        d = rec_day(rec)
        if d not in days:
            continue
        key = (d, str(rec.get("symbol", "")).upper())
        sides.setdefault(key, set()).add(str(rec.get("side", "")).lower())
        if d == today and rec.get("status") == "placed" and rec_is_entry(rec):
            sides[key].add("pending")
    hits = sorted("%s %s" % k for k, s in sides.items()
                  if ("buy" in s and "sell" in s) or (k[0] == today and "pending" in s))
    return len(hits), hits


# ---------------------------------------------------------------- the check

def check(orders, cfg, portfolio=None, prices=None, state=None, today=None, now=None, price_times=None):
    """Pure function. Returns {"results": [...], "pass": n, "block": n, "warnings": [...]}.

    now          naive UTC (default: the real clock); its IST date is "today"
    price_times  {SYM: naive UTC time of that price}; None = every price counts as fresh"""
    c = dict(DEFAULTS)
    c.update(cfg or {})
    prices = {str(k).upper(): float(v) for k, v in (prices or {}).items() if _f(v)}
    now_utc = now or parse_now(None)
    now_et = to_ist(now_utc)
    today = today or now_et.date().isoformat()
    acct, top_warn = account(c, portfolio, prices)
    blocked = {str(b).upper() for b in c.get("blocked") or []}
    done_today = placed_today(state, today)
    count_today = len(done_today)
    entries_today = [r for r in done_today if rec_is_entry(r)]
    loss_today = 0.0
    for r in entries_today:
        ml = _f(r.get("max_loss"))
        if ml is None:
            top_warn.append("an entry placed today (%s %s) has no max loss in the log; not counted in "
                            "the daily loss" % (r.get("side"), r.get("symbol")))
        else:
            loss_today += ml
    placed_keys = {(str(r.get("symbol", "")).upper(), r.get("side")) for r in done_today}
    win_reasons, win_warns = window_check(now_et, c)
    seen = set()
    results = []
    batch_entries, batch_loss = 0, 0.0
    if not c.get("paper", True):
        top_warn.append("LIVE account (\"paper\": false): these orders use real money")
    top_warn.extend(win_warns)

    def fresh(sym):
        if sym not in prices:
            return None
        if price_times is None:
            return prices[sym]
        t = price_times.get(sym)
        if t is None:
            return None
        age = (now_utc - t).total_seconds()
        return prices[sym] if -60 <= age <= FRESH_MINUTES * 60 else None

    for i, o in enumerate(orders, 1):
        reasons, warns, info = [], [], []
        later = []                       # entry-only reasons, turned into warnings for an exit
        sym = mkt.normalise_symbol(o.get("symbol") or "")
        side = str(o.get("side", "")).strip().lower()
        typ = str(o.get("type", "market")).strip().lower()
        qty, notional = o.get("qty"), None
        plan = o.get("plan") if isinstance(o.get("plan"), dict) else {}
        product = str(o.get("product") or c.get("product") or mkt.PRODUCT_INTRADAY).strip().upper()
        exchange = str(o.get("exchange") or c.get("exchange") or mkt.DEFAULT_EXCHANGE).strip().upper()
        order_legs = o.get("legs") if isinstance(o.get("legs"), list) else []
        by_role = dict((str(l.get("role")), l.get("params") or {})
                       for l in order_legs if isinstance(l, dict))
        # a complete day trade carries all three Kite legs; Kite has no bracket
        bracket = bool(by_role.get("stop") or by_role.get("target")
                       or o.get("stop_trigger_price") or o.get("target_limit_price"))
        value = None

        # format
        if not sym:
            reasons.append("format: no symbol")
        if side not in ("buy", "sell"):
            reasons.append("format: side must be buy or sell, got %r" % o.get("side"))
        if qty is None:
            reasons.append("format: no quantity")
        elif _f(qty) is None or _f(qty) <= 0 or abs(_f(qty) - round(_f(qty))) > 1e-9:
            reasons.append("format: quantity must be whole shares above 0, got %r "
                           "(Indian cash equity has no fractional quantity)" % qty)
        for k in PRICE_FIELDS:
            if o.get(k) is not None:
                v = _f(o.get(k))
                if v is None or v <= 0:
                    reasons.append("format: %s must be a positive number, got %r" % (k, o.get(k)))
                elif not isinstance(o.get(k), str):
                    warns.append("send %s as a string (\"%s\"), the way place_stock_order types it"
                                 % (k, o.get(k)))
        if typ not in ("market", "limit"):
            reasons.append("format: an entry is market or limit, got %r" % typ)
        if product not in PRODUCTS:
            reasons.append("format: product must be %s (intraday) or %s (delivery), got %r"
                           % (mkt.PRODUCT_INTRADAY, mkt.PRODUCT_DELIVERY, product))
        if exchange not in mkt.EXCHANGES:
            reasons.append("format: exchange must be one of %s, got %r"
                           % (", ".join(mkt.EXCHANGES), exchange))
        if typ == "limit" and o.get("limit_price") is None:
            reasons.append("format: a limit order needs a limit_price")
        # every leg must be a sendable order in its own right
        for role, prm in sorted(by_role.items()):
            lt = str(prm.get("order_type") or "").upper()
            if lt not in TYPES:
                reasons.append("format: the %s leg has an unknown order_type %r" % (role, prm.get("order_type")))
            if lt == "LIMIT" and prm.get("price") is None:
                reasons.append("format: the %s leg is a LIMIT without a price" % role)
            if lt in ("SL", "SL-M") and prm.get("trigger_price") is None:
                reasons.append("format: the %s leg is %s without a trigger_price" % (role, lt))
            if str(prm.get("validity") or "DAY").upper() not in VALIDITIES:
                reasons.append("format: the %s leg has an unknown validity %r" % (role, prm.get("validity")))
            for key in ("price", "trigger_price"):
                v = _f(prm.get(key))
                if v is not None and abs(round(v * 100) % 5) > 1e-6 and v >= 250:
                    warns.append("the %s leg's %s %s is not on the 0.05 tick: your broker may reject it"
                                 % (role, key, price(v)))
        if bracket and not (by_role.get("stop") or o.get("stop_trigger_price")):
            reasons.append("format: a day trade needs a stop leg (SL-M with a trigger price)")
        if bracket and not (by_role.get("target") or o.get("target_limit_price")):
            warns.append("no target leg: the trade has a stop but nothing to take it off at")
        if order_legs and len(by_role) < 3:
            warns.append("only %d of the 3 legs (entry, stop, target) are present" % len(by_role))

        # asset
        if sym and FNO.match(sym.replace(" ", "")):
            if not c.get("allow_options"):
                reasons.append("asset: %s looks like an F&O instrument; derivatives are off "
                               "(\"allow_options\": false). F&O is a later phase." % sym)
            else:
                warns.append("derivative order: an option can lose everything you pay for it")
        elif sym and ("/" in sym or not TICKER.match(sym)):
            reasons.append("asset: %s does not look like a US stock or ETF ticker; "
                           "this pack trades stocks and ETFs only" % sym)

        # the reference price: what one share costs to get in
        plan_entry = _f(plan.get("entry"))
        live = fresh(sym)
        ref, ref_from = None, None
        if typ in ("limit", "stop_limit"):
            ref, ref_from = _f(o.get("limit_price")), "limit"
        elif typ == "stop":
            ref, ref_from = _f(o.get("stop_price")), "stop price"
        if ref is None:
            cands = [(p, w) for p, w in ((plan_entry, "plan"), (live, "price")) if p]
            if cands:
                ref, ref_from = max(cands, key=lambda x: x[0])
                ref = _cent(ref)
                if live and ref_from == "price" and plan_entry and live > plan_entry + CENT:
                    info.append("%s last %s is above the plan's entry %s: the risk is measured from %s"
                                % (sym, _p(ref), _p(plan_entry), _p(ref)))
            elif sym in prices:
                ref, ref_from = prices[sym], "stale price"
        if plan_entry and live is None and sym in prices and price_times is not None and typ == "market":
            warns.append("the %s price in trader/prices.json is older than %d minutes: not used"
                         % (sym, FRESH_MINUTES))
        q = _f(qty)
        if notional is not None and _f(notional):
            value = _f(notional)
        elif q:
            if ref:
                value = q * ref

        # entry or exit
        held = acct["shares"].get(sym, 0.0)
        held_value = acct["values"].get(sym, 0.0)
        exiting = False
        if side == "sell" and held > 1e-9:
            if q is not None:
                exiting = q <= held + 1e-9
            elif value is not None:
                exiting = value <= held_value + CENT
        elif side == "buy" and held < -1e-9 and q is not None:
            exiting = q <= -held + 1e-9
        kind = "exit" if exiting else "entry"
        if kind == "entry" and value is None and q:
            later.append("price: no price for %s to size a %s-share order; add it to "
                         "trader/prices.json (trader-alerts: alerts.py quote %s --write)" % (sym, qty, sym))

        # blocked list and size
        if sym in blocked:
            later.append("blocked: %s is on your blocked list" % sym)
        if value is not None:
            if value < float(c.get("min_order_inr") or 0) - CENT:
                later.append("size: %s is under your %s minimum order"
                             % (money(value), money(c["min_order_inr"])))
            if c.get("max_order_inr") and value > float(c["max_order_inr"]) + CENT:
                later.append("size: %s is over your %s max order"
                             % (money(value), money(c["max_order_inr"])))

        # account effects, simulated in order
        cash = acct["cash"]
        total = cash + sum(acct["values"].values())
        buffer_inr = total * float(c.get("cash_buffer_pct") or 0) / 100.0
        if value is not None and side == "buy" and kind == "entry":
            after_cash = cash - value
            if after_cash < -CENT and not c.get("allow_margin"):
                later.append("cash: needs %s but only %s cash; that would be margin (borrowed money)"
                             % (money(value), money(cash)))
            elif after_cash < buffer_inr - CENT:
                later.append("cash: leaves %s cash, under your %s%% buffer of %s"
                             % (money(max(after_cash, 0)), _pct(c["cash_buffer_pct"]), money(buffer_inr)))
            pos_after = held_value + value
            limit = total * float(c.get("max_position_pct") or 100) / 100.0
            if total > 0 and pos_after > limit + CENT:
                later.append("position: %s would be %s, %.1f%% of the %s account, over your %s%% cap"
                             % (sym, money(pos_after), 100.0 * pos_after / total, money(total),
                                _pct(c["max_position_pct"])))
        if side == "sell" and sym and kind == "entry":
            if q is not None:
                over = q > held + 1e-9
            elif value is not None:
                over = value > held_value + CENT
            else:
                over = held <= 0
            if over:
                # India: an intraday short IS legal in cash equity, but it MUST be
                # squared off the same session; there is no overnight cash short.
                if product == mkt.PRODUCT_DELIVERY:
                    reasons.append("short: selling more %s than you hold (%s shares) as %s. There is no "
                                   "overnight short in the cash segment: use %s and square off the same "
                                   "day, or use SLB." % (sym, _num(held), mkt.PRODUCT_DELIVERY,
                                                         mkt.PRODUCT_INTRADAY))
                elif not c.get("allow_short"):
                    reasons.append("short: selling more %s than you hold (%s shares); shorting is off in "
                                   "your config (\"allow_short\": false). If you bought it, record the "
                                   "fill first (ticket.py fill)" % (sym, _num(held)))
                else:
                    warns.append("intraday short: %s must be bought back before the broker squares it off "
                                 "at %s, or it is squared off for you at whatever price is there"
                                 % (sym, mkt.BROKER_SQUARE_OFF.strftime("%H:%M")))

        # day-trade checks: stop, loss, reward:risk, daily loss, trades, window, circuit
        max_loss = risk_ps = rr = stop = target = None
        if kind == "entry" and side in ("buy", "sell"):
            long = side == "buy"
            leg_stop = _f((by_role.get("stop") or {}).get("trigger_price")) or _f(o.get("stop_trigger_price"))
            plan_stop = _f(plan.get("stop"))
            stop = leg_stop if leg_stop else plan_stop
            target = (_f((by_role.get("target") or {}).get("price"))
                      or _f(o.get("target_limit_price")) or _f(plan.get("target")))
            if leg_stop and plan_stop and abs(leg_stop - plan_stop) > CENT:
                warns.append("the stop leg %s differs from the plan's stop %s: the leg is the one the "
                             "broker uses" % (_p(leg_stop), _p(plan_stop)))
            if stop and not leg_stop:
                warns.append("the stop %s is in the plan only, not at the broker. Kite has no bracket "
                             "product, so place the SL-M leg yourself once the entry fills, and set a "
                             "stop alert (trader-alerts: alerts.py add --from-plan)" % _p(stop))
            if not stop:
                if c.get("require_stop", True):
                    later.append("stop: no stop. Every entry needs one (\"require_stop\": true): an SL-M "
                                 "stop leg, from trader-exit and trader-budget")
                else:
                    warns.append("no stop: the max loss is not capped, so it is not counted in the daily loss")
            elif ref:
                if (long and stop >= ref - CENT) or (not long and stop <= ref + CENT):
                    later.append("stop: the stop %s must be %s the entry %s for a %s"
                                 % (_p(stop), "below" if long else "above", _p(ref), side))
                else:
                    risk_c = int(round(abs(ref - stop) * 100))
                    risk_ps = risk_c / 100.0
                    shares = q if q is not None else ((value or 0) / ref)
                    gross_loss = round(shares * risk_ps, 2)
                    net_sizing = bool(c.get("max_loss_includes_costs", True))
                    net_loss = gross_loss
                    charges = None
                    if shares and shares == int(shares):
                        t = inr_costs.net_pnl(ref, stop, int(shares), "buy" if long else "sell",
                                              product, exchange)
                        net_loss = round(abs(t["net"]), 2)
                        charges = t["costs"]
                    # the limit means what you actually lose, charges included
                    max_loss = net_loss if net_sizing else gross_loss
                    cap = _f(c.get("max_loss_per_trade_inr"))
                    if cap and max_loss > cap + CENT:
                        later.append("loss: %s shares x %s risk a share = %s at the stop%s, over your %s "
                                     "max loss per trade"
                                     % (_num(shares), _p(risk_ps), money(max_loss),
                                        (" once %s of charges land" % money(charges))
                                        if (net_sizing and charges) else "",
                                        money(cap)))
                    elif net_sizing and charges and gross_loss <= (cap or 0) + CENT and net_loss > (cap or 0) + CENT:
                        later.append("loss: the gross loss %s fits your %s limit but the real loss is %s "
                                     "with charges" % (money(gross_loss), money(cap), money(net_loss)))
                    elif charges:
                        info.append("charges on the round trip %s, so the loss at the stop is %s not %s"
                                    % (money(charges), money(net_loss), money(gross_loss)))
                    min_rr = _f(c.get("min_reward_risk"))
                    if target:
                        if (long and target <= ref + CENT) or (not long and target >= ref - CENT):
                            later.append("target: the target %s must be %s the entry %s"
                                         % (_p(target), "above" if long else "below", _p(ref)))
                        else:
                            rr = round(int(round(abs(target - ref) * 100)) / float(risk_c), 2)
                            if min_rr and rr < min_rr - 1e-9:
                                later.append("reward:risk: %.2f (target %s, entry %s, stop %s) is under your "
                                             "minimum %s" % (rr, _p(target), _p(ref), _p(stop), _num(min_rr)))
                    elif min_rr:
                        later.append("reward:risk: no target to measure it against (min_reward_risk %s)"
                                     % _num(min_rr))
            daily = _f(c.get("max_daily_loss_inr"))
            if daily and max_loss is not None:
                used = loss_today + batch_loss
                if used + max_loss > daily + CENT:
                    later.append("daily loss: %s already at risk today plus %s for this one = %s, over your %s "
                                 "daily loss limit" % (money(used), money(max_loss), money(used + max_loss),
                                                       money(daily)))
            cap_t = int(c.get("max_trades_per_day") or 0)
            if cap_t and len(entries_today) + batch_entries >= cap_t:
                later.append("trades: %d entries placed today plus %d passed earlier in this batch, your limit "
                             "is %d a day" % (len(entries_today), batch_entries, cap_t))
            later.extend(win_reasons)
            if stop and ref and risk_ps:
                info.append("entry %s (%s), stop %s, target %s: risk %s a share"
                            % (_p(ref), REF_WORDS.get(ref_from, ref_from), _p(stop),
                               _p(target) if target else "none", _p(risk_ps)))
                info.append("max loss %s%s, reward:risk %s%s"
                            % (money(max_loss), (" (limit %s)" % money(c["max_loss_per_trade_inr"]))
                               if _f(c.get("max_loss_per_trade_inr")) else "",
                               ("%.2f" % rr) if rr is not None else "n/a",
                               (" (min %s)" % _num(c["min_reward_risk"])) if _f(c.get("min_reward_risk")) else ""))
            if daily or cap_t:
                info.append("today before this: %d of %s trades, %s of %s daily loss"
                            % (len(entries_today) + batch_entries, cap_t or "unlimited",
                               money(loss_today + batch_loss), money(daily) if daily else "no limit on"))

        # duplicates and the daily order limit
        key = (sym, side)
        if key in seen:
            later.append("duplicate: a second %s %s in this batch" % (side, sym))
        if key in placed_keys:
            later.append("duplicate: a %s %s was already placed today" % (side, sym))
        passed_so_far = sum(1 for r in results if r["verdict"] == "PASS")
        cap_n = int(c.get("max_orders_per_day") or 0)
        if cap_n and count_today + passed_so_far >= cap_n:
            later.append("daily limit: %d placed today plus %d passed earlier in this batch, "
                         "your limit is %d a day" % (count_today, passed_so_far, cap_n))
        seen.add(key)

        if kind == "entry":
            reasons.extend(later)
        else:
            warns.extend("exit, not blocked: " + x for x in later)

        verdict = "BLOCK" if reasons else "PASS"
        if verdict == "PASS":
            if kind == "entry" and side in ("buy", "sell"):
                batch_entries += 1
                batch_loss += max_loss or 0.0
            if side == "buy":
                if value is not None:
                    acct["cash"] -= value
                    acct["values"][sym] = acct["values"].get(sym, 0.0) + value
                if q is not None:
                    acct["shares"][sym] = held + q
                elif ref and value is not None:
                    acct["shares"][sym] = held + value / ref
            elif side == "sell":
                if value is not None:
                    acct["cash"] += value
                    acct["values"][sym] = max(acct["values"].get(sym, 0.0) - value, 0.0)
                if q is not None:
                    acct["shares"][sym] = held - q
                elif ref and value is not None:
                    acct["shares"][sym] = held - value / ref
        results.append({
            "index": i, "symbol": sym, "side": side, "kind": kind,
            "amount": money(_f(notional) or 0) if notional is not None else ("%s shares" % qty),
            "value_inr": round(value, 2) if value is not None else None,
            "entry_ref": round(ref, 2) if ref else None, "entry_ref_from": ref_from,
            "stop": stop, "target": target, "risk_per_share": risk_ps, "max_loss": max_loss,
            "reward_risk": rr, "fingerprint": fingerprint(o), "verdict": verdict,
            "reasons": reasons, "warnings": warns, "info": info,
        })

    return {
        "date": today,
        "now_ist": now_et.strftime("%Y-%m-%d %H:%M"),
        "paper": bool(c.get("paper", True)),
        "results": results,
        "pass": sum(1 for r in results if r["verdict"] == "PASS"),
        "block": sum(1 for r in results if r["verdict"] == "BLOCK"),
        "warnings": top_warn,
        "cash_after": round(acct["cash"], 2),
        "today": {"orders_placed": count_today, "entries_placed": len(entries_today),
                  "loss_at_risk": round(loss_today, 2), "settlement": mkt.SETTLEMENT_CYCLE},
    }


def _pct(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.2f" % x)


def _num(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.4f" % x).rstrip("0")


def report(res):
    lines = []
    for w in res["warnings"]:
        lines.append("WARNING: " + w)
    if res["warnings"]:
        lines.append("")
    for r in res["results"]:
        lines.append("%-5s #%d %s %s %s, %s" % (r["verdict"], r["index"], r["side"], r["symbol"], r["amount"],
                                                r["kind"]))
        for x in r.get("info", []):
            lines.append("          " + x)
        for x in r["reasons"]:
            lines.append("        - " + x)
        for x in r["warnings"]:
            lines.append("        ~ " + x)
    lines.append("")
    lines.append("%d passed, %d blocked at %s IST. Cash after the passed orders: %s (%s account)."
                 % (res["pass"], res["block"], res["now_ist"], money(res["cash_after"]),
                    "paper" if res["paper"] else "LIVE"))
    if res["block"]:
        lines.append("Blocked orders do not go to the broker. Fix the order or your rules, then check again.")
    else:
        lines.append("Every order passed. trader-broker still asks you yes or no for each one.")
    lines.append("Education and tooling, not financial advice.")
    return "\n".join(lines)


def load_prices(pdoc):
    """prices.json -> ({SYM: price}, {SYM: price time as naive UTC or None}).

    The price time is the quote's own time (detail.time), else when it was fetched
    (detail.fetched), else the file's as_of."""
    if not isinstance(pdoc, dict):
        return {}, {}
    prices = pdoc.get("prices", pdoc)
    if not isinstance(prices, dict):
        return {}, {}
    out, times = {}, {}
    as_of = parse_utc(pdoc.get("as_of"))
    detail = pdoc.get("detail") or {}
    for s, p in prices.items():
        if _f(p) is None:
            continue
        s = str(s).upper()
        out[s] = float(p)
        d = detail.get(s) or {}
        times[s] = parse_utc(d.get("time")) or parse_utc(d.get("fetched")) or as_of
    return out, times


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check proposed orders against your limits (trader-risk).")
    ap.add_argument("--orders", default="trader/orders.json")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--portfolio", default="trader/portfolio.json",
                    help="your account; if missing, a fresh account funded with the budget")
    ap.add_argument("--prices", default="trader/prices.json")
    ap.add_argument("--state", default="trader/state/broker.json", help="what trader-broker placed today")
    ap.add_argument("--now", help="pretend it is this IST time, e.g. \"2026-10-19 10:15\"")
    ap.add_argument("--today", help="YYYY-MM-DD (default: today in IST)")
    ap.add_argument("--out", default="trader/state/risk.json", help="where the verdicts are recorded")
    ap.add_argument("--no-write", action="store_true", help="do not record the verdicts")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        now = parse_now(a.now)
        if a.today and not re.match(r"^\d{4}-\d{2}-\d{2}$", a.today):
            raise RiskError("--today wants a date like 2026-10-19")
        doc = read_json(a.orders, "orders file (run trader-budget first)")
        orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
        if not isinstance(orders, list) or not orders:
            raise RiskError("no orders in %s" % a.orders)
        cfg = read_json(a.config, "config", required=False) or {}
        portfolio = read_json(a.portfolio, "portfolio", required=False)
        pdoc = read_json(a.prices, "prices", required=False) or {}
        prices, times = load_prices(pdoc)
        state = read_json(a.state, "broker state", required=False)
        res = check(orders, cfg, portfolio, prices, state, a.today, now, times)
        if pdoc.get("as_of"):
            res["prices_as_of"] = pdoc["as_of"]
        if not a.no_write:
            d = os.path.dirname(a.out)
            if d:
                os.makedirs(d, exist_ok=True)
            rec = dict(res)
            rec["checked_at"] = datetime.datetime.now(datetime.timezone.utc).replace(
                microsecond=0, tzinfo=None).strftime("%Y-%m-%dT%H:%M:%SZ")
            rec["orders_file"] = a.orders
            tmp = a.out + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(rec, fh, indent=2)
                fh.write("\n")
            os.replace(tmp, a.out)
    except RiskError as e:
        print("check.py: %s" % e, file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2) if a.json else report(res))
    return 1 if res["block"] else 0


if __name__ == "__main__":
    sys.exit(main())
