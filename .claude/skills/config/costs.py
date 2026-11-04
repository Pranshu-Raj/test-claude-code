"""Indian intraday trading costs: brokerage, STT, exchange, SEBI, stamp, GST.

Every rate comes from india_market.COSTS, where each one carries a VERIFY
comment naming its official source. Nothing here is advice.

Why this module exists at all: the US pack this is ported from models no
costs, because US retail equity is commission-free. Indian intraday charges
are small per trade but they are NOT symmetric and they are NOT negligible
against a tight stop:

  - STT falls on the SELL leg only (intraday), at 0.025% of sell turnover.
  - Stamp duty falls on the BUY leg only.
  - GST is charged on brokerage plus exchange and SEBI charges, not on STT.
  - Depository charges apply to a DELIVERY sell, never to intraday.

So a long and a short of the same size do not cost the same, and the cost of
a round trip can eat a real slice of a one-R move. cost_as_r() reports that
slice so sizing and simulation can stop pretending it is zero.
"""

import math

from . import india_market as mkt

BUY = "buy"
SELL = "sell"


def _rates(rates=None):
    r = dict(mkt.COSTS)
    if rates:
        r.update(rates)
    return r


def _paise(x):
    """Charges are billed to the paise."""
    return math.floor(float(x) * 100 + 0.5) / 100.0


def brokerage(turnover, rates=None):
    """Flat 20 or 0.03% of turnover, whichever is lower, per executed order."""
    r = _rates(rates)
    pct = float(r["brokerage_pct"]) * float(turnover)
    flat = float(r["brokerage_flat_inr"])
    mode = r.get("brokerage_mode", "lower_of")
    if mode == "flat":
        return _paise(flat)
    if mode == "pct":
        return _paise(pct)
    return _paise(min(flat, pct))


def leg_charges(side, qty, price, product=None, exchange=None, rates=None):
    """Charges for ONE leg. Returns a breakdown plus 'total'.

    side: 'buy' or 'sell'. product: MIS (intraday) or CNC (delivery).
    """
    r = _rates(rates)
    product = product or mkt.PRODUCT_INTRADAY
    exchange = (exchange or mkt.DEFAULT_EXCHANGE).upper()
    side = str(side).lower()
    if side not in (BUY, SELL):
        raise ValueError("side must be 'buy' or 'sell', got %r" % side)
    if product not in (mkt.PRODUCT_INTRADAY, mkt.PRODUCT_DELIVERY):
        raise ValueError("product must be %s or %s" % (mkt.PRODUCT_INTRADAY, mkt.PRODUCT_DELIVERY))

    turnover = float(qty) * float(price)
    intraday = product == mkt.PRODUCT_INTRADAY

    brok = brokerage(turnover, rates)

    if intraday:
        stt_pct = r["stt_intraday_sell_pct"] if side == SELL else r["stt_intraday_buy_pct"]
    else:
        stt_pct = r["stt_delivery_pct"]
    stt = _paise(turnover * float(stt_pct))

    ex_key = "exchange_txn_pct_bse" if exchange == mkt.BSE else "exchange_txn_pct_nse"
    exch = _paise(turnover * float(r[ex_key]))
    sebi = _paise(turnover * float(r["sebi_turnover_pct"]))

    if side == BUY:
        stamp_pct = r["stamp_duty_intraday_buy_pct"] if intraday else r["stamp_duty_delivery_buy_pct"]
    else:
        stamp_pct = 0.0
    stamp = _paise(turnover * float(stamp_pct))

    # GST applies to brokerage and the exchange/SEBI charges, not to STT or stamp duty.
    gst = _paise((brok + exch + sebi) * float(r["gst_pct"]))

    dp = _paise(float(r["dp_charge_delivery_sell_inr"])) if (not intraday and side == SELL) else 0.0

    total = _paise(brok + stt + exch + sebi + stamp + gst + dp)
    return {"side": side, "qty": float(qty), "price": float(price), "turnover": _paise(turnover),
            "product": product, "exchange": exchange,
            "brokerage": brok, "stt": stt, "exchange_txn": exch, "sebi_turnover": sebi,
            "stamp_duty": stamp, "gst": gst, "dp_charge": dp, "total": total}


def round_trip(buy_price, sell_price, qty, product=None, exchange=None, rates=None):
    """Both legs of a round trip. Direction-independent: pass the buy price
    and the sell price whichever came first."""
    b = leg_charges(BUY, qty, buy_price, product, exchange, rates)
    s = leg_charges(SELL, qty, sell_price, product, exchange, rates)
    return {"buy": b, "sell": s, "total": _paise(b["total"] + s["total"])}


def net_pnl(entry, exit_price, qty, side=BUY, product=None, exchange=None, rates=None):
    """Gross and net P&L for a closed trade. 'side' is the ENTRY side, so
    side='sell' is an intraday short."""
    side = str(side).lower()
    entry, exit_price, qty = float(entry), float(exit_price), float(qty)
    if side == BUY:
        gross = (exit_price - entry) * qty
        rt = round_trip(entry, exit_price, qty, product, exchange, rates)
    elif side == SELL:
        gross = (entry - exit_price) * qty
        rt = round_trip(exit_price, entry, qty, product, exchange, rates)
    else:
        raise ValueError("side must be 'buy' or 'sell', got %r" % side)
    costs = rt["total"]
    return {"side": side, "qty": qty, "entry": entry, "exit": exit_price,
            "gross": _paise(gross), "costs": costs, "net": _paise(gross - costs),
            "legs": rt, "tax_note": mkt.TAX_TREATMENT_NOTE}


def cost_as_r(entry, stop, qty, side=BUY, product=None, exchange=None, rates=None):
    """Round-trip costs as a fraction of one R (the risk entry-to-stop).

    0.12 means charges eat 12 per cent of a one-R win. Sizing and the time
    machine use this so a 1:1 trade is not quietly a losing one.
    """
    risk_per_share = abs(float(entry) - float(stop))
    if risk_per_share <= 0 or float(qty) <= 0:
        return None
    rt = round_trip(min(entry, stop), max(entry, stop), qty, product, exchange, rates)
    return rt["total"] / (risk_per_share * float(qty))


def breakeven_price(entry, qty, side=BUY, product=None, exchange=None, rates=None):
    """The exit price at which the trade nets zero, costs included.

    Costs depend slightly on the exit price, so this iterates to a fixed
    point; it converges in a couple of rounds.
    """
    entry, qty = float(entry), float(qty)
    x = entry
    for _ in range(8):
        rt = (round_trip(entry, x, qty, product, exchange, rates) if str(side).lower() == BUY
              else round_trip(x, entry, qty, product, exchange, rates))
        move = rt["total"] / qty
        nxt = entry + move if str(side).lower() == BUY else entry - move
        if abs(nxt - x) < 0.0001:
            x = nxt
            break
        x = nxt
    return mkt.round_to_tick(x, "up" if str(side).lower() == BUY else "down")


def summary_line(trade):
    """One plain line for a report, from net_pnl()'s result."""
    f = mkt.format_inr
    return ("Gross %s, costs %s, net %s (%s %d at %s, out at %s)"
            % (f(trade["gross"]), f(trade["costs"]), f(trade["net"]),
               trade["side"], int(trade["qty"]), f(trade["entry"]), f(trade["exit"])))


def breakdown_lines(legs):
    """Report lines for a round_trip() result, charges that are zero omitted."""
    names = (("brokerage", "Brokerage"), ("stt", "STT"), ("exchange_txn", "Exchange txn"),
             ("sebi_turnover", "SEBI turnover"), ("stamp_duty", "Stamp duty"),
             ("gst", "GST"), ("dp_charge", "Depository"))
    out = []
    for key, label in names:
        total = legs["buy"][key] + legs["sell"][key]
        if total > 0:
            note = ""
            if key == "stt":
                note = " (sell leg only)"
            elif key == "stamp_duty":
                note = " (buy leg only)"
            out.append("  %-14s %10s%s" % (label, mkt.format_inr(total), note))
    out.append("  %-14s %10s" % ("Total", mkt.format_inr(legs["total"])))
    return out
