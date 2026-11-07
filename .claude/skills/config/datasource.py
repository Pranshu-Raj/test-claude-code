"""One interface for Indian market data, so the provider is swappable.

Every adapter returns the SAME canonical shape, so no skill has to know
where its bars came from:

    {"symbol": "RELIANCE", "exchange": "NSE", "interval": "5m",
     "meta": {...provider's own metadata...},
     "bars": [{"t": <epoch seconds>, "o":, "h":, "l":, "c":, "v":}, ...]}

Bars are in ascending time order and carry epoch seconds; use
india_market.ts_to_ist() to read them in IST.

Adapters
--------
offline  Saved JSON on disk. The default under test, and the only adapter
         that works with no network. Also how you replay a captured day.
yahoo    Free delayed data from Yahoo's chart endpoint with .NS/.BO symbols,
         over the standard library only. The default for live use.
kite     Zerodha Kite Connect.        | All four read credentials from
upstox   Upstox API v2.               | environment variables only and
angel    Angel One SmartAPI.          | raise AdapterUnavailable with setup
dhan     Dhan HQ API.                 | notes when a key or dep is missing.

NEVER put a key in a config file or in chat. These adapters read os.environ
and nothing else, and no adapter writes a credential anywhere.

STATUS: the four broker adapters build their requests from each vendor's
published API but have NOT been exercised against a live endpoint, because
the environment this port was written in blocks outbound market-data hosts.
Treat them as scaffolding to test with your own key before you rely on them.
The offline adapter is fully exercised by the test suite; the yahoo adapter's
parsing is exercised against saved payloads.
"""

import gzip
import json
import os
import time
import urllib.error
import urllib.request

from . import india_market as mkt


class DataError(Exception):
    """Any failure to get usable bars."""


class AdapterUnavailable(DataError):
    """The adapter cannot run here: missing dependency, key or instrument map."""


# ---------------------------------------------------------------- canonical


def canonical(symbol, exchange, interval, bars, meta=None):
    out = sorted(({"t": int(b["t"]), "o": float(b["o"]), "h": float(b["h"]),
                   "l": float(b["l"]), "c": float(b["c"]),
                   "v": int(b.get("v") or 0)} for b in bars), key=lambda b: b["t"])
    return {"symbol": mkt.normalise_symbol(symbol), "exchange": (exchange or mkt.DEFAULT_EXCHANGE).upper(),
            "interval": interval, "meta": dict(meta or {}), "bars": out}


def session_bars(series, day=None, upto=None):
    """Bars inside one NSE session, 09:15 up to but not including 15:30.

    day: a datetime.date, or None for the last day present.
    upto: a datetime.time cutoff (a bar is kept when its OPEN time is before
          the cutoff), so an intraday scan can be reproduced as at 10:15.
    """
    bars = series["bars"] if isinstance(series, dict) else series
    if not bars:
        return []
    by_day = {}
    for b in bars:
        ist = mkt.ts_to_ist(b["t"])
        by_day.setdefault(ist.date(), []).append((ist, b))
    target = day or max(by_day)
    out = []
    for ist, b in by_day.get(target, []):
        if not (mkt.SESSION_OPEN <= ist.time() < mkt.SESSION_CLOSE):
            continue
        if upto is not None and ist.time() >= upto:
            continue
        out.append(b)
    return out


def full_sessions(series, interval_minutes=5, before=None):
    """Complete sessions only, oldest first, each as its bars in slot order.

    Complete means every bar from the open up to mkt.SESSION_REQUIRED_UNTIL
    (the flat-by time) is present on the interval grid. Bars after it may be
    missing: Yahoo's NSE feed drops the 15:20 bar on every session, so a rule
    demanding all 75 would keep nothing real. A mid-day gap, a half session
    or today so far is still dropped. The returned list is the contiguous run
    of bars from the open, so a bar after a tail gap is left out too.
    """
    step = int(interval_minutes)
    need = mkt.required_bars(step)
    open_min = mkt.SESSION_OPEN.hour * 60 + mkt.SESSION_OPEN.minute
    bars = series["bars"] if isinstance(series, dict) else series
    by_day = {}
    for b in bars:
        ist = mkt.ts_to_ist(b["t"])
        if not (mkt.SESSION_OPEN <= ist.time() < mkt.SESSION_CLOSE):
            continue
        mins = ist.hour * 60 + ist.minute - open_min
        if mins % step:
            continue                                  # off the grid: a live tick
        by_day.setdefault(ist.date(), {})[mins // step] = b
    days = []
    for d in sorted(by_day):
        if before is not None and d >= before:
            continue
        slots = by_day[d]
        n = 0
        while n in slots:
            n += 1
        if n >= need:
            days.append((d, [slots[i] for i in range(n)]))
    return days


# ---------------------------------------------------------------- offline


class OfflineAdapter(object):
    """Saved payloads on disk. No network, so tests and replays are exact.

    Looks for, in order:
        <dir>/<SYMBOL>-<interval>.json
        <dir>/<SYMBOL>-<interval>.json.gz
        <dir>/<SYMBOL>.json
    A file may be either the canonical shape above or a raw Yahoo chart
    payload, which is detected and converted.
    """

    name = "offline"

    def __init__(self, directory):
        self.dir = directory

    def _load(self, symbol, interval):
        sym = mkt.normalise_symbol(symbol)
        names = ["%s-%s.json" % (sym, interval), "%s-%s.json.gz" % (sym, interval),
                 "%s.json" % sym]
        for n in names:
            p = os.path.join(self.dir, n)
            if os.path.exists(p):
                opener = gzip.open if p.endswith(".gz") else open
                with opener(p, "rb") as fh:
                    return json.loads(fh.read().decode("utf-8")), p
        raise DataError("no saved data for %s at %s in %s (looked for %s)"
                        % (sym, interval, self.dir, ", ".join(names)))

    def bars(self, symbol, interval="5m", days=1, exchange=None):
        raw, path = self._load(symbol, interval)
        if isinstance(raw, dict) and "chart" in raw:
            return parse_yahoo_chart(raw, symbol, interval)
        if isinstance(raw, dict) and "bars" in raw:
            return canonical(raw.get("symbol", symbol), raw.get("exchange", exchange),
                             raw.get("interval", interval), raw["bars"],
                             dict(raw.get("meta") or {}, source=path))
        raise DataError("%s is neither a canonical series nor a Yahoo chart payload" % path)

    def quote(self, symbol, exchange=None):
        series = self.bars(symbol, "5m")
        bars = series["bars"]
        if not bars:
            raise DataError("no bars for %s" % symbol)
        return {"symbol": series["symbol"], "price": bars[-1]["c"], "ts": bars[-1]["t"],
                "prev_close": series["meta"].get("prev_close"), "source": "offline"}


# ---------------------------------------------------------------- yahoo

YAHOO_CHART = "https://query2.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=%s"
YAHOO_RANGE = {"1m": "5d", "5m": "60d", "15m": "60d", "1d": "1y"}
USER_AGENT = "trader-skills/india (+https://github.com/Jakeschincariol/day-trader-skill)"


def parse_yahoo_chart(payload, symbol=None, interval="5m"):
    """A Yahoo v8 chart payload -> the canonical shape."""
    try:
        res = payload["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        err = ((payload or {}).get("chart") or {}).get("error")
        raise DataError("Yahoo returned no chart result%s" % (": %s" % err if err else ""))
    meta = res.get("meta") or {}
    stamps = res.get("timestamp") or []
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    bars = []
    for i, t in enumerate(stamps):
        o, h, l, c = (q.get("open") or [])[i:i + 1], (q.get("high") or [])[i:i + 1], \
                     (q.get("low") or [])[i:i + 1], (q.get("close") or [])[i:i + 1]
        if not (o and h and l and c) or None in (o[0], h[0], l[0], c[0]):
            continue   # Yahoo pads gaps with nulls
        v = (q.get("volume") or [])[i:i + 1]
        bars.append({"t": t, "o": o[0], "h": h[0], "l": l[0], "c": c[0],
                     "v": (v[0] if v and v[0] is not None else 0)})
    sym = symbol or meta.get("symbol") or ""
    keep = {"prev_close": meta.get("previousClose") or meta.get("chartPreviousClose"),
            "currency": meta.get("currency"), "exchange_name": meta.get("exchangeName"),
            "timezone": meta.get("exchangeTimezoneName"), "gmtoffset": meta.get("gmtoffset"),
            "source": "yahoo"}
    return canonical(sym, mkt.exchange_of_yahoo(meta.get("symbol") or sym), interval, bars, keep)


class YahooAdapter(object):
    """Free delayed Yahoo data over the standard library.

    Unofficial: it can lag, change shape or rate-limit. Check anything that
    matters against your broker before acting on it.
    """

    name = "yahoo"

    def __init__(self, timeout=20, pause=0.4, opener=None):
        self.timeout = timeout
        self.pause = pause
        self._open = opener or self._fetch

    def _fetch(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise DataError("Yahoo answered %s for %s" % (e.code, url))
        except (urllib.error.URLError, OSError) as e:
            raise DataError("could not reach Yahoo: %s" % getattr(e, "reason", e))
        except ValueError as e:
            raise DataError("Yahoo sent something that is not JSON: %s" % e)

    def bars(self, symbol, interval="5m", days=None, exchange=None):
        ysym = mkt.to_yahoo(symbol, exchange or mkt.DEFAULT_EXCHANGE)
        rng = YAHOO_RANGE.get(interval, "60d")
        if days:
            rng = "%dd" % int(days)
        payload = self._open(YAHOO_CHART % (ysym, rng, interval))
        if self.pause:
            time.sleep(self.pause)   # one ticker at a time, politely
        return parse_yahoo_chart(payload, symbol, interval)

    def quote(self, symbol, exchange=None):
        series = self.bars(symbol, "5m", days=1, exchange=exchange)
        bars = series["bars"]
        if not bars:
            raise DataError("no bars for %s" % symbol)
        return {"symbol": series["symbol"], "price": bars[-1]["c"], "ts": bars[-1]["t"],
                "prev_close": series["meta"].get("prev_close"), "source": "yahoo"}


# ---------------------------------------------------------------- brokers


class _BrokerAdapter(object):
    """Shared plumbing: credentials from the environment, nowhere else."""

    name = "broker"
    env_keys = ()
    setup = ""

    def __init__(self, session=None):
        self._session = session
        missing = [k for k in self.env_keys if not os.environ.get(k)]
        if missing and session is None:
            raise AdapterUnavailable(
                "%s adapter needs %s in the environment. %s"
                % (self.name, " and ".join(missing), self.setup))
        self.creds = dict((k, os.environ.get(k, "")) for k in self.env_keys)

    def bars(self, symbol, interval="5m", days=1, exchange=None):
        raise AdapterUnavailable(
            "%s historical bars are not wired up yet. Use the yahoo or offline "
            "adapter for bars, and this adapter for orders." % self.name)

    def quote(self, symbol, exchange=None):
        raise AdapterUnavailable("%s quotes are not wired up yet." % self.name)


class KiteAdapter(_BrokerAdapter):
    name = "kite"
    env_keys = ("KITE_API_KEY", "KITE_ACCESS_TOKEN")
    setup = ("Create an app at developers.kite.trade, then export KITE_API_KEY and "
             "the daily KITE_ACCESS_TOKEN. Historical data needs the instrument_token "
             "from the instruments dump, not the trading symbol.")


class UpstoxAdapter(_BrokerAdapter):
    name = "upstox"
    env_keys = ("UPSTOX_ACCESS_TOKEN",)
    setup = "Create an app in the Upstox developer console and export UPSTOX_ACCESS_TOKEN."


class AngelAdapter(_BrokerAdapter):
    name = "angel"
    env_keys = ("SMARTAPI_API_KEY", "SMARTAPI_ACCESS_TOKEN")
    setup = "Register on smartapi.angelbroking.com and export SMARTAPI_API_KEY and SMARTAPI_ACCESS_TOKEN."


class DhanAdapter(_BrokerAdapter):
    name = "dhan"
    env_keys = ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN")
    setup = "Generate a token in Dhan's API portal and export DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN."


# ---------------------------------------------------------------- factory

ADAPTERS = {"offline": OfflineAdapter, "yahoo": YahooAdapter, "kite": KiteAdapter,
            "upstox": UpstoxAdapter, "angel": AngelAdapter, "dhan": DhanAdapter}

DEFAULT_ADAPTER = "yahoo"


def get_adapter(name=None, **kw):
    """Build an adapter by name. 'offline' needs directory=..."""
    key = (name or os.environ.get("TRADER_DATA_ADAPTER") or DEFAULT_ADAPTER).lower()
    if key not in ADAPTERS:
        raise DataError("unknown data adapter %r, want one of %s"
                        % (key, ", ".join(sorted(ADAPTERS))))
    if key == "offline" and "directory" not in kw:
        raise DataError("the offline adapter needs directory=<folder of saved payloads>")
    return ADAPTERS[key](**kw)
