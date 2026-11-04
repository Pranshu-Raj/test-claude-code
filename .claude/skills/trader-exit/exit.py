#!/usr/bin/env python3
"""Exit Planner for trader-exit: sets the stop and the target BEFORE you
enter, so you know when to get out. NSE/BSE cash intraday. Standard library
only. Long side only. It never places an order.

    python3 exit.py plan                                     # from trader/entry.json: or-mid stop, 2R target
    python3 exit.py plan --stop or-mid --target 1R --write    # -> trader/plan.json
    python3 exit.py plan --symbol TCS --entry 4026.50 --or-high 4020 --or-low 3996
    python3 exit.py plan --stop atr:1.5 --adapter offline --data tests/fixtures/india
    python3 exit.py show                                     # the current trader/plan.json

Stops (a long's stop rounds DOWN to a tradable tick, away from the entry):
  or-mid     the opening-range midpoint (the default for an orb entry)
  or-low     the opening-range low
  price:X    your price
  pct:X      X percent under the entry
  atr:K      K times the 14-bar ATR under the entry, on the session's bars up
             to the entry bar. With fewer than 14 bars it averages all of them
             and says so.
  A level setup has no opening-range default: give a stop.

Targets round half up to a tick: 2R (default), 1R, 1.5R ... or price:X.

Prices are rounded to the NSE tick from config.india_market (0.05 for most
scrips), never to the paisa, so every price in the plan is one you can
actually send. A stop must be at least one tick away.

Refused, with the reason: a stop at or above the entry, a target at or below
it, a risk under 0.05% of the price or under one tick (too tight to survive
the spread), an entry.json that has not triggered (unless you give --entry),
a short (this plans longs only; intraday shorts are legal in Indian cash
equity but are a later phase).

SQUARE-OFF, which has no US equivalent: your broker closes open MIS
positions itself, around 15:15 to 15:25, at whatever price is there. So
flat_by defaults to config.india_market.FLAT_BY (15:15) and is CLAMPED to sit
before BROKER_SQUARE_OFF. A flat-by at or after the broker's square-off is
refused: being closed by your broker is not an exit plan.

Costs are not optional in India. Intraday charges are asymmetric (STT on the
sell leg, stamp duty on the buy leg) and against a tight stop they eat a real
slice of one R, so the plan reports the indicative net reward:risk from
config.costs once a size is implied by your max loss per trade.
trader-budget computes the authoritative figure at the real size.

A stop is a plan, not a guarantee: a gap or a fast market can fill past it.
Exit codes: plan 0 = a plan, 1 = refused or an error. show 0 = a plan exists.
"""

import argparse
import datetime
import json
import os
import re
import sys
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import costs as inr_costs, datasource as ds, india_market as mkt

INTERVALS = {"1m": 60, "5m": 300}
DEFAULTS = {"flat_by": mkt.FLAT_BY.strftime("%H:%M"), "min_reward_risk": 1.0,
            "max_loss_per_trade_inr": None, "exchange": mkt.DEFAULT_EXCHANGE,
            "product": mkt.PRODUCT_INTRADAY}
ATR_BARS = 14
MIN_RISK_PCT = Decimal("0.05")     # risk under 0.05% of the price is inside the spread's noise
EPOCH = datetime.datetime(1970, 1, 1)
NOTE = "A stop is a plan, not a guarantee: a gap or a fast market can fill past it."
SQUARE_OFF_NOTE = ("Your broker squares off open intraday positions itself, around %s. "
                   "Be flat before that: a broker square-off is not an exit plan."
                   % mkt.BROKER_SQUARE_OFF.strftime("%H:%M"))

class PlanError(Exception):
    pass


# ---------------------------------------------------------------- time

# IST is a fixed +05:30 with no daylight saving, so the New York DST dance
# this replaces collapses to two one-line calls into the shared config.

def to_ist(utc):
    return mkt.to_ist(utc)


def ist_to_utc(local):
    return mkt.ist_to_utc(local)


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)


def utc_of(ts):
    return EPOCH + datetime.timedelta(seconds=int(ts))


def parse_now(text):
    """--now -> naive UTC. A time without an offset is IST."""
    if not text:
        return utc_now()
    t = text.strip()
    if t.upper().endswith(" ET"):
        t = t[:-3].strip()
    if re.match(r"^\d{1,2}:\d{2}$", t):
        t = "%s %s" % (to_ist(utc_now()).date().isoformat(), t.zfill(5))
    t = t.replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise PlanError('--now wants an IST time like "2026-11-03 10:15" (or ...Z for UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_hhmm(text, what):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(text))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise PlanError("%s wants an IST time like 15:15, not %r" % (what, text))
    return datetime.time(int(m.group(1)), int(m.group(2)))


def parse_clock(text, what):
    """10:15 AM, 10:15, 3:05 PM -> time."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*([AaPp][Mm])?\s*$", str(text))
    if not m:
        raise PlanError("%s wants a time like 10:15 AM, not %r" % (what, text))
    h, mi = int(m.group(1)), int(m.group(2))
    if m.group(3):
        if not 1 <= h <= 12:
            raise PlanError("%s wants a time like 10:15 AM, not %r" % (what, text))
        h = h % 12 + (12 if m.group(3).upper() == "PM" else 0)
    if h > 23 or mi > 59:
        raise PlanError("%s wants a time like 10:15 AM, not %r" % (what, text))
    return datetime.time(h, mi)


def hm(t):
    return "%02d:%02d" % (t.hour, t.minute)


def ampm(t):
    """IST, 24-hour. Kept under the old name."""
    return "%02d:%02d" % (t.hour, t.minute)


def session(d):
    """(open, close) in IST from the shared calendar, or None when closed."""
    return mkt.session(d)


# ------------------------------------------------- money math (Decimal, on the NSE tick)

def D(x, what="a number"):
    try:
        if isinstance(x, float):
            return Decimal(repr(x))
        return Decimal(str(x).strip().lstrip("\u20b9").lstrip("$").replace(",", ""))
    except (InvalidOperation, ValueError):
        raise PlanError("%s is not a number: %r" % (what, x))


PAISA = Decimal("0.01")
ONE = Decimal("1")


def tick_of(price):
    """The tradable tick at this price, from the shared config."""
    return Decimal(str(mkt.tick_size(float(D(price)))))


def to_tick(x, ref=None):
    """Round half up to a tradable tick. Targets use this."""
    t = tick_of(ref if ref is not None else x)
    return ((D(x) / t).quantize(ONE, rounding=ROUND_HALF_UP) * t).quantize(PAISA)


def floor_tick(x, ref=None):
    """A long's stop: down to a tick, away from the entry."""
    t = tick_of(ref if ref is not None else x)
    return ((D(x) / t).quantize(ONE, rounding=ROUND_FLOOR) * t).quantize(PAISA)


# kept under the old names so the rest of the module reads unchanged
cents = to_tick
floor_cents = floor_tick


def money(x):
    """A price, always with its paise: \u20b94,008.00."""
    return mkt.format_inr_price(float(D(x)))


def amount(x):
    """A rupee amount, trailing zeros dropped: \u20b912,34,567."""
    return mkt.format_inr(float(D(x)))


def num(x):
    """1, 1.5, 2.25: a Decimal without trailing zeros."""
    s = format(D(x).normalize(), "f")
    return s


def ratio(x):
    """reward:risk for people: 1.0, 1.5, 1.37."""
    s = "%.2f" % float(x)
    return s[:-1] if s.endswith("0") else s


WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


def parse_stop(text):
    t = str(text).strip().lower()
    if t in ("or-mid", "or_mid", "mid", "or-midpoint"):
        return "or_mid", None
    if t in ("or-low", "or_low", "low"):
        return "or_low", None
    m = re.match(r"^(price|pct|atr):\s*[₹$]?([0-9][0-9,]*\.?[0-9]*)$", t)
    if not m:
        raise PlanError("--stop is or-mid, or-low, price:X, pct:X or atr:K (got %r)" % text)
    kind, val = m.group(1), D(m.group(2), "--stop")
    if val <= 0:
        raise PlanError("--stop %s needs a number above 0" % kind)
    if kind == "pct" and val >= 100:
        raise PlanError("--stop pct:X needs X under 100")
    return kind, val


def parse_target(text):
    t = str(text).strip()
    m = re.match(r"^([0-9]*\.?[0-9]+)\s*[Rr]$", t)
    if m:
        r = D(m.group(1), "--target")
        if r <= 0:
            raise PlanError("--target needs an R multiple above 0, like 1R or 2R")
        return "r", r
    m = re.match(r"^price:\s*[₹$]?([0-9][0-9,]*\.?[0-9]*)$", t.lower())
    if m:
        return "price", D(m.group(1), "--target")
    raise PlanError("--target is 2R, 1R, 1.5R ... or price:X (got %r)" % text)


def r_label(r):
    n = int(r) if r == r.to_integral_value() else None
    return "%sR (%s times the risk)" % (num(r), WORDS.get(n, num(r)))


def compute(entry, stop_rule, stop_param, target_rule, target_param, or_high=None, or_low=None,
            atr=None, atr_bars=None):
    """The stop, the target and the risk, on the NSE tick. Raises PlanError with the reason."""
    e = cents(entry)
    if e <= 0:
        raise PlanError("the entry price must be above zero")
    detail = ""
    if stop_rule in ("or_mid", "or_low"):
        if or_high is None or or_low is None:
            raise PlanError("an %s stop needs the opening range (--or-high and --or-low)" % stop_rule.replace("_", "-"))
        oh, ol = cents(or_high), cents(or_low)
        if oh <= ol:
            raise PlanError("the opening-range high %s must be above its low %s" % (money(oh), money(ol)))
        if stop_rule == "or_mid":
            raw = (oh + ol) / 2
            label = "opening-range midpoint"
            detail = "(%s + %s) / 2 = %s" % (money(oh), money(ol), money(raw))
        else:
            raw, label = ol, "opening-range low"
    elif stop_rule == "price":
        raw, label = stop_param, "your price"
    elif stop_rule == "pct":
        raw = e * (1 - stop_param / 100)
        label = "%s%% under the entry" % num(stop_param)
        detail = "%s x (1 - %s%%) = %s" % (money(e), num(stop_param), money(raw))
    elif stop_rule == "atr":
        if atr is None:
            raise PlanError("an atr stop needs the session's bars")
        a = D(round(atr, 6))
        raw = e - stop_param * a
        label = "%s x ATR(%d) under the entry" % (num(stop_param), atr_bars)
        detail = "ATR %s over the %d bars to the entry%s" % (
            money(a.quantize(Decimal("0.0001"))), atr_bars, " (fewer than 14 so far)" if atr_bars < ATR_BARS else "")
    else:
        raise PlanError("unknown stop rule %r" % stop_rule)
    stop = floor_cents(raw)
    if stop != D(raw):
        detail = (detail + ", rounded down to the tick" if detail
                  else "%s rounded down to the tick" % money(raw))
    if stop >= e:
        raise PlanError("the stop %s (%s) is at or above the entry %s: a long's stop goes under the entry"
                        % (money(stop), label, money(e)))
    if stop <= 0:
        raise PlanError("the stop works out to %s: pick a stop above zero" % money(stop))
    risk = e - stop
    tick = tick_of(e)
    if risk < tick:
        raise PlanError("the stop %s is less than one tick (%s) under the entry %s: that is not a "
                        "tradable distance. Widen it."
                        % (money(stop), money(tick), money(e)))
    floor_risk = e * MIN_RISK_PCT / 100
    if risk < floor_risk:
        raise PlanError("the stop is %s under the entry, less than 0.05%% of the price (%s on %s): too tight to "
                        "survive the spread, normal wiggle would stop you out. Widen it."
                        % (money(risk), money(to_tick(floor_risk, e)), money(e)))
    if target_rule == "r":
        target = cents(e + target_param * risk)
        t_label = r_label(target_param)
    else:
        target = cents(target_param)
        t_label = None
    if target <= e:
        raise PlanError("the target %s is at or below the entry %s: a long's target goes above it"
                        % (money(target), money(e)))
    rr = ((target - e) / risk).quantize(PAISA, rounding=ROUND_HALF_UP)
    if t_label is None:
        t_label = "your price (%sR)" % ratio(rr)
    return {"entry": e, "stop": stop, "stop_label": label, "stop_detail": detail, "target": target,
            "target_r": target_param if target_rule == "r" else rr, "target_label": t_label,
            "risk": risk, "reward_risk": rr}


# -------------------------------------------- bars for ATR (through the adapter)

def load_bars(sym, interval, day, adapter=None, data_dir=None, exchange=None):
    """One NSE session of bars for the ATR stop, via config.datasource.

    Every provider is reached the same way, so this skill does not know or
    care whether the bars came from Yahoo, a saved payload or a broker.
    """
    try:
        a = adapter if hasattr(adapter, "bars") else ds.get_adapter(
            adapter, **({"directory": data_dir} if (adapter or "") == "offline" else {}))
    except ds.DataError as e:
        raise PlanError(str(e))
    try:
        series = a.bars(sym, interval, exchange=exchange or mkt.DEFAULT_EXCHANGE)
    except ds.DataError as e:
        raise PlanError("no bars for %s: %s" % (sym, e))
    return series


def session_bars(series, day, interval, upto_slot=None, now_utc=None):
    """Closed, on-grid bars of one NSE session, oldest first.

    The forming bar is dropped: a stop must not be set off a bar that can
    still move. upto_slot counts 5-minute (or 1-minute) slots from 09:15.
    """
    step = INTERVALS[interval]
    s = session(day)
    if not s:
        return []
    open_dt = datetime.datetime.combine(day, s[0])
    close_dt = datetime.datetime.combine(day, s[1])
    local = to_ist(now_utc) if now_utc else None
    out = {}
    for b in (series["bars"] if isinstance(series, dict) else series):
        if b["t"] % step:
            continue
        ist = mkt.ts_to_ist(b["t"])
        if ist.date() != day or not (open_dt <= ist < close_dt):
            continue
        if local and ist + datetime.timedelta(seconds=step) > local:
            continue
        slot = int((ist - open_dt).total_seconds()) // step
        if upto_slot is not None and slot > upto_slot:
            continue
        out[slot] = b
    return [out[k] for k in sorted(out)]


def atr_of(bars, n=ATR_BARS):
    """Average true range of the last n bars (all of them if fewer). The first
    bar of the session uses its own high - low (no overnight gap)."""
    trs, prev = [], None
    for b in bars:
        tr = b["h"] - b["l"]
        if prev is not None:
            tr = max(tr, abs(b["h"] - prev), abs(b["l"] - prev))
        trs.append(tr)
        prev = b["c"]
    use = trs[-n:]
    return sum(use) / len(use), len(use)


# ---------------------------------------------------------------- plan

def read_json(path, what):
    if not path or not os.path.exists(path):
        raise PlanError("no %s at %s" % (what, path))
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise PlanError("%s is not valid JSON: %s" % (path, e))


def read_config(path):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        doc = read_json(path, "config")
        for k in DEFAULTS:
            if doc.get(k) is not None:
                cfg[k] = doc[k]
    return cfg


def resolve_flat_by(day, explicit, cfg, entry_time):
    """(HH:MM, note), forced to sit before the broker's intraday square-off.

    India-specific. A US plan only had to beat the closing bell; here the
    broker closes an open MIS position itself, around 15:15 to 15:25, at
    whatever price is there. So an explicit flat-by at or after the
    square-off is refused, and a configured one is pulled back to it.
    """
    t = parse_hhmm(explicit or cfg.get("flat_by") or DEFAULTS["flat_by"],
                   "--flat-by" if explicit else "flat_by")
    note = ""
    squared = mkt.BROKER_SQUARE_OFF
    if t >= squared:
        if explicit:
            raise PlanError("--flat-by %s is at or after your broker's intraday square-off (%s): it would "
                            "close the position for you at whatever price is there. Pick an earlier time."
                            % (hm(t), hm(squared)))
        t = mkt.FLAT_BY
        note = ("pulled back to %s: the configured flat_by was at or after the broker square-off %s"
                % (hm(t), hm(squared)))
    s = session(day) if day else None
    if s:
        if t >= s[1]:
            raise PlanError("flat-by %s is at or after the %s close on %s" % (hm(t), hm(s[1]), day))
        if s[1] != mkt.SESSION_CLOSE:
            # a special window (Muhurat): hold the flat-by inside it
            inside = min(t, (datetime.datetime.combine(day, s[1])
                             - datetime.timedelta(minutes=5)).time())
            if inside != t:
                t = inside
                note = "moved up for the %s-%s special session on %s" % (hm(s[0]), hm(s[1]), day)
            if t <= s[0]:
                raise PlanError("the %s-%s session on %s is too short to day trade with a flat-by"
                                % (hm(s[0]), hm(s[1]), day))
    if entry_time and entry_time >= t:
        raise PlanError("the entry at %s is at or after your flat-by %s: there is no time left to hold it"
                        % (hm(entry_time), hm(t)))
    return hm(t), note


def gather(a):
    """The entry facts, from trader/entry.json or the flags."""
    if a.symbol and a.from_path:
        raise PlanError("use --from or --symbol, not both")
    if a.side in ("sell", "short"):
        raise PlanError("v2 plans longs only: no stops above a short entry. Plan a buy instead.")
    if a.symbol:
        if a.entry is None:
            raise PlanError("--symbol needs --entry PRICE")
        if (a.or_high is None) != (a.or_low is None):
            raise PlanError("give both --or-high and --or-low, or neither")
        info = {"symbol": a.symbol.upper(), "entry": a.entry, "setup": a.setup or ("orb" if a.or_high is not None else "level"),
                "date": a.date, "entry_time": a.time, "entry_bar": a.entry_bar, "interval": a.interval or "5m",
                "or_high": a.or_high, "or_low": a.or_low, "or_minutes": a.or_minutes, "source": "flags"}
    else:
        path = a.from_path or "trader/entry.json"
        doc = read_json(path, "entry (run /trader-entry first, or give --symbol and --entry)")
        if str(doc.get("side") or "buy").lower() not in ("buy", "long"):
            raise PlanError("%s is a %s: v2 plans longs only" % (path, doc.get("side")))
        if doc.get("status") != "triggered" and a.entry is None:
            raise PlanError("%s says %s is %s, not triggered: plan once it triggers, or give --entry PRICE to plan ahead"
                            % (path, doc.get("symbol"), doc.get("status") or "unknown"))
        orr = doc.get("opening_range") or {}
        info = {"symbol": str(doc.get("symbol") or "").upper(), "entry": a.entry if a.entry is not None else doc.get("price"),
                "setup": a.setup or doc.get("setup") or "orb", "date": a.date or doc.get("date"),
                "entry_time": a.time or (doc.get("time") if a.entry is None else None),
                "entry_bar": a.entry_bar if a.entry_bar is not None else (doc.get("bar_index") if a.entry is None else None),
                "interval": a.interval or doc.get("interval") or "5m",
                "or_high": a.or_high if a.or_high is not None else orr.get("high"),
                "or_low": a.or_low if a.or_low is not None else orr.get("low"),
                "or_minutes": orr.get("minutes") or a.or_minutes, "source": path}
        if not info["symbol"] or info["entry"] is None:
            raise PlanError("%s has no symbol or price" % path)
    if info["interval"] not in INTERVALS:
        raise PlanError("interval is 5m or 1m")
    return info


def build_plan(a, now_utc):
    info = gather(a)
    cfg = read_config(a.config)
    stop_text = a.stop
    if not stop_text:
        if info["setup"] != "orb":
            raise PlanError("a %s setup has no opening-range stop to default to: give --stop price:X, pct:X or atr:K"
                            % info["setup"])
        stop_text = "or-mid"
    stop_rule, stop_param = parse_stop(stop_text)
    target_rule, target_param = parse_target(a.target or "2R")
    day = None
    if info["date"]:
        try:
            day = datetime.date.fromisoformat(str(info["date"]))
        except ValueError:
            raise PlanError("date wants YYYY-MM-DD, not %r" % info["date"])
    else:
        day = to_ist(now_utc).date()
    entry_time = parse_clock(info["entry_time"], "the entry time") if info["entry_time"] else None
    atr = atr_n = None
    if stop_rule == "atr":
        series = load_bars(info["symbol"], info["interval"], day,
                           getattr(a, "adapter", None), getattr(a, "data", None),
                           cfg.get("exchange") or mkt.DEFAULT_EXCHANGE)
        bars = session_bars(series, day, info["interval"], info["entry_bar"],
                            parse_now(a.now) if a.now else None)
        if len(bars) < 3:
            raise PlanError("an atr stop needs at least 3 closed %s bars of %s on %s by the entry; found %d"
                            % (info["interval"], info["symbol"], day, len(bars)))
        atr, atr_n = atr_of(bars)
    c = compute(info["entry"], stop_rule, stop_param, target_rule, target_param, info["or_high"], info["or_low"],
                atr, atr_n)
    flat_by, flat_note = resolve_flat_by(day, a.flat_by, cfg, entry_time)
    orr = None
    if info["or_high"] is not None and info["or_low"] is not None:
        orr = {"high": float(cents(info["or_high"])), "low": float(cents(info["or_low"])),
               "minutes": int(info["or_minutes"] or 15)}
    plan = {"created_by": "trader-exit", "symbol": info["symbol"], "side": "buy", "date": day.isoformat(),
            "setup": info["setup"], "entry": float(c["entry"]),
            "entry_time": ampm(entry_time) if entry_time else None,
            "entry_bar": info["entry_bar"], "interval": info["interval"], "opening_range": orr,
            "stop": float(c["stop"]), "stop_rule": stop_rule, "stop_label": c["stop_label"],
            "target": float(c["target"]), "target_r": float(c["target_r"]), "target_label": c["target_label"],
            "risk_per_share": float(c["risk"]), "reward_risk": float(c["reward_risk"]), "flat_by": flat_by,
            "stop_detail": c["stop_detail"], "created_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "note": NOTE,
            "square_off": mkt.BROKER_SQUARE_OFF.strftime("%H:%M"), "currency": mkt.CURRENCY_CODE,
            "exchange": cfg.get("exchange") or mkt.DEFAULT_EXCHANGE,
            "product": cfg.get("product") or mkt.PRODUCT_INTRADAY,
            "tax_note": mkt.TAX_TREATMENT_NOTE}
    plan["costs"] = cost_estimate(c["entry"], c["stop"], c["target"], cfg)
    warn = mkt.calendar_warning(day)
    if warn:
        plan["calendar_warning"] = warn
    if atr is not None:
        plan["atr"] = round(atr, 4)
        plan["atr_bars"] = atr_n
    warnings = []
    if flat_note:
        warnings.append("Flat-by %s: %s." % (flat_by, flat_note))
    mrr = cfg.get("min_reward_risk")
    try:
        if mrr is not None and c["reward_risk"] < D(mrr):
            warnings.append("Reward:risk %s is under your min_reward_risk %s in the config: trader-risk checks that."
                            % (ratio(c["reward_risk"]), num(D(mrr))))
    except PlanError:
        pass
    today = to_ist(now_utc).date()
    if day != today:
        warnings.append("This plan is for %s, not today (%s)." % (day.isoformat(), today.isoformat()))
    return plan, warnings


def cost_estimate(entry, stop, target, cfg):
    """Indicative net reward:risk after Indian intraday charges, or None.

    Costs need a size, and sizing is trader-budget's job, so this uses the
    size your max_loss_per_trade_inr implies at this stop and says it is
    indicative. Without that config key there is nothing honest to compute.
    """
    try:
        max_loss = cfg.get("max_loss_per_trade_inr")
        if max_loss in (None, ""):
            return None
        risk_ps = float(D(entry) - D(stop))
        if risk_ps <= 0:
            return None
        qty = int(float(max_loss) // risk_ps)
        if qty < 1:
            return None
        product = cfg.get("product") or mkt.PRODUCT_INTRADAY
        exchange = cfg.get("exchange") or mkt.DEFAULT_EXCHANGE
        win = inr_costs.net_pnl(float(entry), float(target), qty, "buy", product, exchange)
        lose = inr_costs.net_pnl(float(entry), float(stop), qty, "buy", product, exchange)
        cost_r = inr_costs.cost_as_r(float(entry), float(stop), qty, "buy", product, exchange)
        net_rr = (win["net"] / abs(lose["net"])) if lose["net"] else None
        return {"qty": qty, "indicative": True, "costs_round_trip": win["costs"],
                "net_win": win["net"], "net_loss": lose["net"],
                "cost_as_r": round(cost_r, 4) if cost_r is not None else None,
                "net_reward_risk": round(net_rr, 2) if net_rr else None,
                "breakeven": inr_costs.breakeven_price(float(entry), qty, "buy", product, exchange)}
    except (ValueError, TypeError, ZeroDivisionError, PlanError):
        return None


def describe(plan):
    day = datetime.date.fromisoformat(plan["date"])
    setup = {"orb": "opening-range breakout", "level": "close above a level"}.get(plan["setup"], plan["setup"])
    e = D(plan["entry"])
    risk = D(plan["risk_per_share"])
    pct = (risk / e * 100).quantize(PAISA, rounding=ROUND_HALF_UP)
    when = ""
    if plan.get("entry_time"):
        when = " at %s" % plan["entry_time"]
        if plan.get("entry_bar") is not None:
            when += " (bar %d)" % plan["entry_bar"]
    out = ["Exit plan: %s long, %s, %s on %s bars" % (
        plan["symbol"], day.strftime("%a %Y-%m-%d"), setup, "5-minute" if plan["interval"] == "5m" else "1-minute")]
    out.append("  Entry         %s%s" % (money(plan["entry"]), when))
    out.append("  Stop          %s  %s%s" % (money(plan["stop"]), plan["stop_label"],
                                             (": " + plan["stop_detail"]) if plan.get("stop_detail") else ""))
    out.append("  Target        %s  %s" % (money(plan["target"]), plan["target_label"]))
    out.append("  Risk a share  %s (%s%% of the entry)" % (money(plan["risk_per_share"]), pct))
    out.append("  Reward:risk   %s" % ratio(plan["reward_risk"]))
    out.append("  Flat by       %s IST, win or lose: no overnight hold" % plan["flat_by"])
    ce = plan.get("costs")
    if ce:
        out.append("  Net of costs  reward:risk %s at %d shares (gross %s), charges %s round trip"
                   % (ratio(ce["net_reward_risk"]) if ce.get("net_reward_risk") else "n/a",
                      ce["qty"], ratio(plan["reward_risk"]), amount(ce["costs_round_trip"])))
        out.append("                win %s, loss %s, breakeven %s. Indicative: trader-budget sizes it."
                   % (amount(ce["net_win"]), amount(ce["net_loss"]), money(ce["breakeven"])))
    out.append("Set before the entry. If price hits the stop, you are out; never move it further away.")
    out.append(SQUARE_OFF_NOTE)
    out.append(NOTE)
    if plan.get("calendar_warning"):
        out.append("Calendar: " + plan["calendar_warning"])
    return out


def cmd_plan(a):
    now = parse_now(a.now)
    plan, warnings = build_plan(a, now)
    if a.write:
        d = os.path.dirname(a.write)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.write, "w", encoding="utf-8") as fh:
            json.dump(plan, fh, indent=2)
            fh.write("\n")
    if a.json:
        print(json.dumps(dict(plan, warnings=warnings), indent=2))
        return 0
    for line in describe(plan):
        print(line)
    for w in warnings:
        print("Note: " + w)
    if a.write:
        print("Wrote %s." % a.write)
    print("Next: /trader-timemachine replays it, then /trader-budget sizes it to your max loss. Nothing was placed.")
    return 0


def cmd_show(a):
    if not os.path.exists(a.plan):
        print("No plan at %s yet. Make one: exit.py plan --write" % a.plan)
        return 1
    plan = read_json(a.plan, "plan")
    for k in ("symbol", "date", "entry", "stop", "target", "risk_per_share", "reward_risk", "flat_by"):
        if plan.get(k) is None:
            raise PlanError("%s is missing %r: make a new plan with exit.py plan --write" % (a.plan, k))
    if a.json:
        print(json.dumps(plan, indent=2))
        return 0
    for line in describe(dict({"setup": "orb", "interval": "5m", "stop_label": "", "target_label": ""}, **plan)):
        print(line)
    today = to_ist(parse_now(a.now)).date().isoformat()
    if plan["date"] != today:
        print("Note: this plan is for %s, not today (%s)." % (plan["date"], today))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Exit Planner (trader-exit): the stop and target before the entry.")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("plan", help="set the stop and target")
    p.add_argument("--from", dest="from_path", metavar="PATH", help="entry.json to plan from (default trader/entry.json)")
    p.add_argument("--symbol")
    p.add_argument("--entry", type=float, help="entry price (or a plan-ahead price with --from)")
    p.add_argument("--or-high", type=float)
    p.add_argument("--or-low", type=float)
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--setup", choices=["orb", "level"])
    p.add_argument("--date", help="session date YYYY-MM-DD (default: the entry's, or today)")
    p.add_argument("--time", help="entry time, e.g. 10:15 AM")
    p.add_argument("--entry-bar", type=int)
    p.add_argument("--interval", choices=["5m", "1m"])
    p.add_argument("--side", default="buy", choices=["buy", "long", "sell", "short"], help="v2: buy only")
    p.add_argument("--stop", help="or-mid (orb default), or-low, price:X, pct:X, atr:K")
    p.add_argument("--target", help="2R (default), 1R, 1.5R, price:X")
    p.add_argument("--flat-by", help="HH:MM IST, before the broker square-off (default: config flat_by, %s)"
                   % DEFAULTS["flat_by"])
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--adapter", help="data adapter for an atr stop: yahoo (default), offline, kite, upstox, angel, dhan")
    p.add_argument("--data", metavar="DIR", help="folder of saved payloads, with --adapter offline")
    p.add_argument("--now", help='pretend it is this IST time, e.g. "2026-11-03 10:15"')
    p.add_argument("--write", nargs="?", const="trader/plan.json", metavar="PATH",
                   help="save the plan (default trader/plan.json)")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("show", help="print the current plan")
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--now")
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"plan": cmd_plan, "show": cmd_show}[a.cmd](a)
    except PlanError as e:
        print("exit.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
