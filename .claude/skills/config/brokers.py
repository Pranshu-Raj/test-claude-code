"""Indian broker order shapes, so trader-budget and trader-broker agree.

Zerodha Kite Connect is the default. The others are documented mappings of
the same fields; nothing here places an order, and nothing here holds a key.

ONE REAL DIFFERENCE FROM THE US PACK, worth understanding before you trade:
Alpaca takes a single bracket order carrying its own stop-loss and
take-profit legs, so one call is the whole trade. Kite has no live bracket
product (Bracket Order was withdrawn), so the same intent is THREE orders:

    1. the entry      MARKET or LIMIT, product MIS
    2. the stop       SL-M (or SL with a price), transaction_type SELL
    3. the target     LIMIT, transaction_type SELL

Legs 2 and 3 can only be placed once leg 1 has filled, and they are not
OCO: if the target fills, the stop is still live and must be cancelled, or
you are short. That is a real footgun with no US counterpart, so every
ticket this module builds carries the warning and the cancel step.

Some brokers offer a GTT (good till triggered) pair that does behave as OCO
for delivery; it is not available for intraday MIS, so it is not used here.
"""

from . import india_market as mkt

KITE = "kite"
UPSTOX = "upstox"
ANGEL = "angel"
DHAN = "dhan"
GENERIC = "generic"
BROKERS = (KITE, UPSTOX, ANGEL, DHAN, GENERIC)
DEFAULT_BROKER = KITE

# The tool each broker's MCP server or SDK exposes for placing an order.
# VERIFY against your broker's current API docs before going live.
PLACE_TOOL = {KITE: "place_order", UPSTOX: "place_order", ANGEL: "placeOrder",
              DHAN: "placeOrder", GENERIC: "place_order"}

# Kite's own field names, in the order its docs list them.
KITE_FIELDS = ("variety", "exchange", "tradingsymbol", "transaction_type", "quantity",
               "product", "order_type", "price", "trigger_price", "validity", "tag")

OCO_WARNING = ("The stop and the target are two separate orders and they are NOT "
               "linked: if one fills, CANCEL the other at once or you will be left "
               "holding the opposite position.")


def _q(x):
    """A quantity is whole shares. India has no fractional equity."""
    n = int(x)
    if n != float(x):
        raise ValueError("quantity must be whole shares, got %r" % (x,))
    return n


def kite_legs(symbol, qty, entry, stop, target, product=None, exchange=None,
              limit=None, tag=None):
    """The three Kite orders for one long intraday trade, entry first.

    entry  the reference price (used for a LIMIT entry; None price = MARKET)
    limit  a limit price for the entry, or None for a market entry
    """
    sym = mkt.normalise_symbol(symbol)
    qty = _q(qty)
    product = product or mkt.PRODUCT_INTRADAY
    exchange = (exchange or mkt.DEFAULT_EXCHANGE).upper()
    base = {"variety": "regular", "exchange": exchange, "tradingsymbol": sym,
            "product": product, "validity": "DAY"}
    if tag:
        base["tag"] = tag

    legs = []
    entry_leg = dict(base, transaction_type="BUY", quantity=qty)
    if limit is not None:
        entry_leg["order_type"] = "LIMIT"
        entry_leg["price"] = mkt.round_to_tick(limit, "down")   # never pay above your limit
    else:
        entry_leg["order_type"] = "MARKET"
    legs.append({"role": "entry", "params": entry_leg})

    if stop is not None:
        stop_leg = dict(base, transaction_type="SELL", quantity=qty, order_type="SL-M",
                        trigger_price=mkt.round_to_tick(stop, "down"))
        legs.append({"role": "stop", "params": stop_leg,
                     "after": "the entry fills", "note": OCO_WARNING})
    if target is not None:
        target_leg = dict(base, transaction_type="SELL", quantity=qty, order_type="LIMIT",
                          price=mkt.round_to_tick(target, "up"))
        legs.append({"role": "target", "params": target_leg,
                     "after": "the entry fills", "note": OCO_WARNING})
    return legs


def square_off_leg(symbol, qty, product=None, exchange=None, tag=None):
    """The market SELL that flattens an open intraday long before square-off."""
    sym = mkt.normalise_symbol(symbol)
    base = {"variety": "regular", "exchange": (exchange or mkt.DEFAULT_EXCHANGE).upper(),
            "tradingsymbol": sym, "product": product or mkt.PRODUCT_INTRADAY,
            "validity": "DAY"}
    if tag:
        base["tag"] = tag
    return {"role": "square_off", "params": dict(base, transaction_type="SELL",
                                                 quantity=_q(qty), order_type="MARKET"),
            "note": "Be flat before your broker does it for you at %s."
                    % mkt.BROKER_SQUARE_OFF.strftime("%H:%M")}


# How the same five ideas are named by each broker, for porting a ticket.
# VERIFY each row against that broker's current API docs.
FIELD_MAP = {
    KITE: {"symbol": "tradingsymbol", "qty": "quantity", "side": "transaction_type",
           "product": "product", "type": "order_type", "limit": "price",
           "trigger": "trigger_price", "exchange": "exchange"},
    UPSTOX: {"symbol": "instrument_token", "qty": "quantity", "side": "transaction_type",
             "product": "product", "type": "order_type", "limit": "price",
             "trigger": "trigger_price", "exchange": "exchange"},
    ANGEL: {"symbol": "tradingsymbol", "qty": "quantity", "side": "transactiontype",
            "product": "producttype", "type": "ordertype", "limit": "price",
            "trigger": "triggerprice", "exchange": "exchange"},
    DHAN: {"symbol": "securityId", "qty": "quantity", "side": "transactionType",
           "product": "productType", "type": "orderType", "limit": "price",
           "trigger": "triggerPrice", "exchange": "exchangeSegment"},
}

# Product names differ per broker for the same intraday/delivery idea.
# VERIFY per broker.
PRODUCT_MAP = {
    KITE: {"intraday": "MIS", "delivery": "CNC"},
    UPSTOX: {"intraday": "I", "delivery": "D"},
    ANGEL: {"intraday": "INTRADAY", "delivery": "DELIVERY"},
    DHAN: {"intraday": "INTRADAY", "delivery": "CNC"},
}


def product_for(broker, kind="intraday"):
    return PRODUCT_MAP.get(broker, PRODUCT_MAP[KITE]).get(kind)


def fingerprint(order):
    """A stable id for THIS exact order, legs included.

    trader-risk records a verdict against it and trader-broker will only show
    an order as ready if the fingerprint matches, so the two must compute it
    the same way. It lives here, imported by both, so they cannot drift.

    It must cover everything that changes what gets sent: the symbol, side,
    quantity, order type, venue and product, the entry limit, and every leg's
    own parameters. Change any of those and the order has to pass risk again.
    """
    import hashlib
    import json

    def _num(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return v
        return int(f) if f == int(f) else round(f, 4)

    top = {}
    for k in ("symbol", "side", "qty", "type", "product", "exchange", "validity",
              "limit_price", "stop_trigger_price", "target_limit_price"):
        v = order.get(k)
        if v is not None:
            top[k] = _num(v) if k != "symbol" else mkt.normalise_symbol(v)
    legs = []
    for leg in (order.get("legs") or []):
        if not isinstance(leg, dict):
            continue
        prm = leg.get("params") or {}
        legs.append({"role": str(leg.get("role") or ""),
                     "params": dict((k, _num(prm[k])) for k in sorted(prm) if prm[k] is not None)})
    legs.sort(key=lambda l: l["role"])
    blob = json.dumps({"order": top, "legs": legs}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
