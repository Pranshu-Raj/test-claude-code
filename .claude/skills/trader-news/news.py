#!/usr/bin/env python3
"""News Monitor for trader-news: what is new on the stocks you watch, and
what moves them. NSE/BSE, IST. Standard library only. It never places an
order.

    python3 news.py --tickers RELIANCE,TCS --hours 12
    python3 news.py                                  # from trader/watchlist.json
    python3 news.py --offline DIR --now "2026-11-02 09:30"

Sources, all public:

  - Indian business news RSS (Economic Times, Business Standard, Moneycontrol,
    Livemint and the like). Free, unofficial, and they change without notice.
  - NSE and BSE corporate announcements for each symbol: results, board
    meetings, dividends, orders won, ratings, pledges, open offers.
  - The results calendar, so a stock reporting today is flagged before you
    trade it.
  - RBI policy dates from config/data/rbi_dates.csv, which you maintain,
    because a policy day moves banks and the whole index.
  - How far each stock is from the previous close, to flag what is moving.

Shows only what is NEW since the last check, newest first, in IST, tagged
(results, analyst, deal, order_win, pledge, regulatory, macro, rbi...) and
never repeated. State lives in trader/state/news.json.

NSE's own endpoints need a browser user agent and a session cookie, and they
rate-limit: the fetcher primes a cookie first and backs off. Expect this to
be the most fragile part of the pack, and prefer a broker feed or a paid
source if news latency matters to your trading.

UNVERIFIED: the endpoints below follow NSE's and BSE's public JSON, but this
port was written where those hosts are unreachable, so none of the live
fetching has been exercised. The offline path and all the parsing,
tagging and deduplication are tested. Point --offline at saved payloads, or
try a live run and expect to adjust.

Day trading loses most people money, and news you can read is already in the
price.
"""

import argparse
import datetime as _dt
import email.utils
import gzip
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import datasource as ds, india_market as mkt

# Indian business news RSS. VERIFY each: these are public feeds that move or
# change shape without notice. Source: each publisher's RSS index page.
NEWS_FEEDS = (
    ("Economic Times Markets",
     "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("Business Standard Markets",
     "https://www.business-standard.com/rss/markets-106.rss"),
    ("Moneycontrol Business",
     "https://www.moneycontrol.com/rss/business.xml"),
    ("Livemint Markets",
     "https://www.livemint.com/rss/markets"),
)

# NSE and BSE corporate announcements. VERIFY: NSE requires a browser user
# agent plus a cookie primed from its homepage, and both change paths.
NSE_HOME = "https://www.nseindia.com"
NSE_ANNOUNCE = ("https://www.nseindia.com/api/corporate-announcements"
                "?index=equities&symbol=%s")
NSE_RESULTS_CAL = "https://www.nseindia.com/api/event-calendar"
BSE_ANNOUNCE = ("https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
                "?pageno=1&strCat=-1&strPrevDate=%s&strScrip=%s&strSearch=P&strToDate=%s&strType=C")
SEC_FEED = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=%s&type=%s"
            "&dateb=&owner=include&count=%d&output=atom")
PLACEHOLDER = "you@example.com"
BROWSER_UA = "Mozilla/5.0 (compatible; day-trader-skill/2.0; +https://github.com/Jakeschincariol/day-trader-skill)"
KEEP_DAYS = 30
MOVE_PCT = 3.0  # a stock this far from yesterday's close is flagged as moving

# NSE and BSE announcement subjects, grouped. The US version keyed off 8-K
# item numbers; Indian announcements carry a free-text subject instead, so
# these are matched on the subject line.
ANNOUNCEMENT_KINDS = [
    ("results", r"financial result|quarterly result|audited result|unaudited result|\bQ[1-4] result"),
    ("board", r"board meeting|outcome of board"),
    ("dividend", r"\bdividend\b|record date"),
    ("buyback", r"\bbuy.?back\b"),
    ("split_bonus", r"\bstock split\b|\bsub.?division\b|\bbonus issue\b"),
    ("order_win", r"\border\b.*\b(won|received|bagged|secured)\b|\bletter of (award|intent)\b"),
    ("rating", r"\bcredit rating\b|\brating (action|revision|upgrade|downgrade)\b"),
    ("pledge", r"\bpledge[ds]?\b|\bencumbrance\b"),
    ("fundraise", r"\bqip\b|\bpreferential (issue|allotment)\b|\bfund rais|\bncd\b|\brights issue\b"),
    ("open_offer", r"\bopen offer\b|\bdelisting\b|\bscheme of (arrangement|amalgamation)\b"),
    ("people", r"\bresignation\b|\bappointment\b|\bcessation\b|\bmanaging director\b|\bchief (executive|financial)\b"),
    ("insider", r"\bsast\b|\bpit\b|regulation 7|insider trading"),
    ("litigation", r"\blitigation\b|\bsebi\b.*\b(order|notice|penalty)\b|\bshow cause\b"),
]
IMPORTANT_KINDS = {"results", "order_win", "rating", "pledge", "open_offer", "litigation",
                   "people", "fundraise"}
KEYWORDS = [
    ("results", r"\bresults?\b|\bearnings\b|\bEPS\b|\bguidance\b|\bQ[1-4]\b|\btopline\b|\bebitda\b"),
    ("analyst", r"\bupgrade[sd]?\b|\bdowngrade[sd]?\b|price target|\boutperform\b|\bunderperform\b|\bbrokerage\b"),
    ("legal", r"\blawsuit\b|\bprobe\b|\binvestigation\b|\bSEBI\b|\bNCLT\b|\bCCI\b|\bED\b|\bincome tax\b|\braid\b"),
    ("deal", r"\bacqui(?:re|res|red|sition)\b|\bmerger\b|\bbuyout\b|\btakeover\b|\bstake sale\b|\bopen offer\b"),
    ("capital", r"\bdividend\b|\bbuyback\b|\bbonus issue\b|\bstock split\b|\bQIP\b|\brights issue\b"),
    ("people", r"\bCEO\b|\bCFO\b|\bMD\b|\bresign|\bsteps down\b|\bappoint"),
    ("order_win", r"\border win\b|\bbags\b|\bwins\b.*\border\b|\bcontract\b"),
    ("risk", r"\brecall\b|\bdefault\b|\bdelist|\bfraud\b|\bpledge\b|\bdowngrade\b.*\brating\b"),
    ("halt", r"\bhalt(?:s|ed)?\b|\bcircuit\b|\bupper circuit\b|\blower circuit\b|\bsuspend"),
    ("regulatory", r"\bUSFDA\b|\bFDA\b|\bapproval\b|\bapproves\b|\bCDSCO\b|\bTRAI\b|\bIRDAI\b"),
    ("rbi", r"\bRBI\b|\brepo rate\b|\bMPC\b|\bmonetary policy\b|\bCRR\b|\bSLR\b"),
    ("macro", r"\bGST\b|\bbudget\b|\bCPI\b|\binflation\b|\bIIP\b|\bGDP\b|\bFII\b|\bDII\b|\bcrude\b|\brupee\b"),
    ("index", r"\bNIFTY\b|\bSENSEX\b|\bBANKNIFTY\b|\bindia vix\b"),
]


class NewsError(Exception):
    pass


# ---------------------------------------------------------------- time

# IST is a fixed +05:30 with no daylight saving.

def to_ist(utc):
    return mkt.to_ist(utc)


def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def watch_from_files(portfolio="trader/portfolio.json", watchlist="trader/watchlist.json",
                     plan="trader/plan.json"):
    """{ticker: name or None}: the scanner's hits, the planned trade and your holdings."""
    out = {}
    wl = load_json(watchlist) or {}
    for h in wl.get("hits", []):
        sym = h.get("symbol") or h.get("ticker")
        if sym:
            out[sym.upper()] = h.get("name")
    pl = load_json(plan) or {}
    if pl.get("symbol"):
        out.setdefault(pl["symbol"].upper(), pl.get("name"))
    pf = load_json(portfolio) or {}
    for p in pf.get("positions", []):
        sym = p.get("ticker") or p.get("symbol")
        if sym:
            out[sym.upper()] = p.get("name") or out.get(sym.upper())
    return out


def sec_user_agent(cli=None, config="trader/config.json"):
    """Kept for the saved-state path; NSE and BSE want a browser UA, not an
    identifying one, and there is no SEC here."""
    ua = (cli or os.environ.get("SEC_USER_AGENT") or "").strip()
    if not ua:
        cfg = load_json(config) or {}
        ua = str(cfg.get("sec_user_agent") or "").strip()
    if not ua or "@" not in ua or PLACEHOLDER in ua:
        return None
    return ua


# ---------------------------------------------------------------- fetching

class Fetcher(object):
    def __init__(self, sec_ua=None, offline=None, pause=0.15):
        self.sec_ua = sec_ua
        self.offline = offline
        self.pause = pause
        self._last = 0.0

    def get(self, url, name, sec=False):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise NewsError("offline: no file %s" % path)
            with open(path, "rb") as fh:
                return fh.read()
        if sec and not self.sec_ua:
            raise NewsError("no SEC User-Agent set")
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()
        headers = {"User-Agent": self.sec_ua if sec else BROWSER_UA, "Accept-Encoding": "gzip"}
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as exc:
            raise NewsError("HTTP %s for %s" % (exc.code, url))
        except urllib.error.URLError as exc:
            raise NewsError("could not reach %s: %s" % (url, exc.reason))
        except OSError as exc:
            # A read timeout surfaces as a bare TimeoutError, not a URLError.
            # NSE in particular accepts the connection and then never answers.
            raise NewsError("timed out or lost the connection to %s: %s" % (url, exc))


# No CIK lookup is needed: NSE and BSE key announcements by the trading
# symbol, which is already the canonical form in this pack.


# ---------------------------------------------------------------- parsing

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _text(el, name):
    for c in el.iter():
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def parse_time(value):
    """RFC 822 (RSS), ISO 8601, or NSE/BSE "02-Nov-2026 09:05:00" (IST)
    -> aware UTC datetime, or None."""
    value = (value or "").strip()
    if not value:
        return None
    m = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})[ T]*(\d{2}):(\d{2})(?::(\d{2}))?$", value)
    if m:
        months = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                  "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
        mon = months.get(m.group(2).lower())
        if mon:
            # the exchanges stamp these in IST
            local = _dt.datetime(int(m.group(3)), mon, int(m.group(1)),
                                 int(m.group(4)), int(m.group(5)), int(m.group(6) or 0))
            return mkt.ist_to_utc(local).replace(tzinfo=_dt.timezone.utc)
    try:
        d = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        d = None
    if d is None:
        try:
            d = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(_dt.timezone.utc)


def parse_rss(data, source="news", ticker=None):
    """Any publisher's RSS -> items. A market-wide feed carries no ticker, so
    `ticker` is left None and mention matching attaches symbols later."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NewsError("RSS from %s is not valid XML: %s" % (source, exc))
    out = []
    for it in root.iter("item"):
        title = _text(it, "title")
        link = _text(it, "link")
        guid = _text(it, "guid") or link
        out.append({"id": "rss:" + guid, "source": source, "kind": "headline",
                    "ticker": ticker, "title": title, "summary": _text(it, "description"),
                    "link": link, "time": parse_time(_text(it, "pubDate"))})
    return out


def announcement_kind(subject):
    """An NSE or BSE announcement subject -> (kind, important)."""
    low = " ".join(str(subject or "").lower().split())
    for kind, rx in ANNOUNCEMENT_KINDS:
        if re.search(rx, low):
            return kind, kind in IMPORTANT_KINDS
    return "announcement", False


def parse_nse_announcements(data, ticker):
    """NSE's corporate-announcements JSON -> items.

    UNVERIFIED against the live endpoint; the shape follows NSE's public API
    (a list of records with symbol, desc/subject, attchmntFile and an
    exchdisstime or sort_date).
    """
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
    except ValueError as exc:
        raise NewsError("NSE announcements for %s are not JSON: %s" % (ticker, exc))
    rows = doc if isinstance(doc, list) else (doc.get("data") or doc.get("rows") or [])
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        subject = r.get("desc") or r.get("subject") or r.get("attchmntText") or ""
        when = r.get("exchdisstime") or r.get("sort_date") or r.get("an_dt") or ""
        kind, important = announcement_kind(subject)
        rid = "nse:%s:%s" % (mkt.normalise_symbol(r.get("symbol") or ticker),
                             r.get("attchmntFile") or (str(when) + subject[:40]))
        out.append({"id": rid, "source": "NSE announcement", "kind": kind,
                    "important": important, "ticker": mkt.normalise_symbol(r.get("symbol") or ticker),
                    "title": "%s: %s" % (mkt.normalise_symbol(r.get("symbol") or ticker),
                                         " ".join(str(subject).split())[:160]),
                    "summary": " ".join(str(r.get("attchmntText") or "").split())[:400],
                    "link": r.get("attchmntFile") or "", "time": parse_time(str(when))})
    return out


def parse_bse_announcements(data, ticker):
    """BSE's AnnSubCategoryGetData JSON -> items. UNVERIFIED, same caveat."""
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
    except ValueError as exc:
        raise NewsError("BSE announcements for %s are not JSON: %s" % (ticker, exc))
    rows = (doc.get("Table") or doc.get("data") or []) if isinstance(doc, dict) else doc
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        subject = r.get("NEWSSUB") or r.get("HEADLINE") or ""
        when = r.get("News_submission_dt") or r.get("DT_TM") or ""
        kind, important = announcement_kind(subject)
        out.append({"id": "bse:%s" % (r.get("NEWSID") or (str(when) + str(subject)[:40])),
                    "source": "BSE announcement", "kind": kind, "important": important,
                    "ticker": mkt.normalise_symbol(ticker),
                    "title": "%s: %s" % (mkt.normalise_symbol(ticker),
                                         " ".join(str(subject).split())[:160]),
                    "summary": " ".join(str(r.get("MORE") or "").split())[:400],
                    "link": r.get("ATTACHMENTNAME") or "", "time": parse_time(str(when))})
    return out


def rbi_items(path=None, now_utc=None):
    """RBI policy dates from the data file, as items for today and tomorrow."""
    out = []
    rows = mkt._read_csv(path or os.path.join(os.path.dirname(os.path.abspath(mkt.__file__)),
                                              "data", "rbi_dates.csv"), "date")
    today = to_ist(now_utc or _dt.datetime.utcnow()).date()
    for r in rows:
        try:
            d = _dt.date.fromisoformat(r["date"])
        except ValueError:
            continue
        delta = (d - today).days
        if 0 <= delta <= 1:
            out.append({"id": "rbi:%s" % r["date"], "source": "RBI calendar", "kind": "rbi",
                        "important": True, "ticker": None,
                        "title": "RBI %s %s (%s)" % (r.get("event") or "policy",
                                                     "today" if delta == 0 else "tomorrow",
                                                     r["date"]),
                        "summary": r.get("note") or "", "link": "", "time": None})
    return out


def parse_chart(data, ticker):
    """Yahoo chart JSON -> {"ticker", "price", "prev_close", "pct", "time"} or raise."""
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        meta = doc["chart"]["result"][0]["meta"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise NewsError("chart for %s is not usable" % ticker)
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if not price or not prev:
        raise NewsError("chart for %s has no price" % ticker)
    when = meta.get("regularMarketTime")
    return {"ticker": ticker, "price": round(float(price), 2), "prev_close": round(float(prev), 2),
            "pct": round((float(price) / float(prev) - 1) * 100, 2),
            "time": _dt.datetime.fromtimestamp(when, _dt.timezone.utc) if when else None}


def collect_moves(fetcher, watch, errors=None, adapter=None, data_dir=None):
    """How far each watched stock is from its previous close, via the adapter."""
    errors = [] if errors is None else errors
    out = []
    try:
        ad = adapter if hasattr(adapter, "quote") else ds.get_adapter(
            adapter or ("offline" if fetcher.offline else None),
            **({"directory": data_dir or fetcher.offline}
               if (adapter or ("offline" if fetcher.offline else None)) == "offline" else {}))
    except ds.DataError as exc:
        errors.append("prices: %s" % exc)
        return out
    for t in sorted(watch):
        sym = mkt.normalise_symbol(t)
        try:
            q = ad.quote(sym)
        except ds.DataError as exc:
            errors.append("price for %s: %s" % (sym, exc))
            continue
        price, prev = q.get("price"), q.get("prev_close")
        pct = (100.0 * (float(price) / float(prev) - 1.0)) if (price and prev) else None
        out.append({"ticker": sym, "price": price, "prev_close": prev,
                    "pct": round(pct, 2) if pct is not None else None,
                    "moving": bool(pct is not None and abs(pct) >= MOVE_PCT),
                    "time": (_dt.datetime(1970, 1, 1) + _dt.timedelta(seconds=int(q["ts"]))
                             if q.get("ts") else None)})
    return out


def mention_patterns(watch):
    """Regexes that spot each watched symbol in a headline.

    Indian coverage names the bare trading symbol (TCS, RELIANCE) far more
    often than a $TICKER or an exchange prefix, so a word-boundary match on
    the symbol is included. Short symbols would be noisy, so only symbols of
    three characters or more get the bare form.
    """
    pats = {}
    for t, name in watch.items():
        esc = re.escape(t)
        alts = [r"\(%s\)" % esc, r"\$%s\b" % esc,
                r"\b(?:NSE|BSE)\s*:\s*%s\b" % esc]
        if len(t) >= 3:
            alts.append(r"\b%s\b" % esc)
        if name and len(name) >= 4:
            alts.append(r"\b%s\b" % re.escape(name).replace(r"\ ", r"[\s-]"))
        pats[t] = re.compile("|".join(alts), re.I)
    return pats


def tag(items, watch):
    """Which of your tickers each item touches, and why it matters."""
    pats = mention_patterns(watch)
    for it in items:
        hay = "%s %s" % (it["title"], it.get("summary") or "")
        touches = {it["ticker"]} if it["ticker"] in watch else set()
        for t, rx in pats.items():
            if rx.search(hay):
                touches.add(t)
        it["touches"] = sorted(touches)
        it["tags"] = [label for label, rx in KEYWORDS if re.search(rx, hay, re.I)]
        # an exchange announcement already knows its own kind
        if it.get("kind") and it["kind"] not in ("headline", "announcement") and it["kind"] not in it["tags"]:
            it["tags"].insert(0, it["kind"])
        if it.get("important") and "important" not in it["tags"]:
            it["tags"].append("important")
        # a market-wide headline carries no symbol: take the first mention
        if not it.get("ticker") and it["touches"]:
            it["ticker"] = it["touches"][0]
    return items


def dedupe(items):
    """One item per story: same id, or same link, or same title the same day."""
    seen, out = {}, []
    for it in items:
        keys = [it["id"], it["link"],
                "%s|%s" % (re.sub(r"\W+", " ", it["title"].lower()).strip(),
                           it["time"].date() if it["time"] else "")]
        hit = next((seen[k] for k in keys if k and k in seen), None)
        if hit is not None:
            for t in it.get("touches", []):
                if t not in hit["touches"]:
                    hit["touches"].append(t)
            hit["touches"].sort()
            continue
        for k in keys:
            if k:
                seen[k] = it
        out.append(it)
    return out


def fingerprint(it):
    return hashlib.sha1(it["id"].encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- the check

def collect(fetcher, watch, state, announcements=True, feeds=NEWS_FEEDS, count=10,
            errors=None, now_utc=None):
    """Headlines from the market-wide Indian feeds, plus per-symbol exchange
    announcements, plus any RBI policy date that is today or tomorrow."""
    errors = [] if errors is None else errors
    items = []
    for label, url in feeds:
        name = "rss-%s.xml" % re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
        try:
            items += parse_rss(fetcher.get(url, name), label)
        except NewsError as exc:
            errors.append("%s: %s" % (label, exc))
    if announcements:
        for t in sorted(watch):
            sym = mkt.normalise_symbol(t)
            try:
                items += parse_nse_announcements(
                    fetcher.get(NSE_ANNOUNCE % sym, "nse-announce-%s.json" % sym), sym)
            except NewsError as exc:
                errors.append("NSE announcements for %s: %s" % (sym, exc))
    try:
        items += rbi_items(now_utc=now_utc)
    except (OSError, ValueError) as exc:
        errors.append("RBI dates: %s" % exc)
    return items


def select(items, state, now, hours=48, show_all=False):
    """Items within the window; only unseen ones unless show_all."""
    seen = state.setdefault("seen", {})
    cutoff = now - _dt.timedelta(hours=hours)
    ahead = now + _dt.timedelta(minutes=10)  # a pinned --now: nothing from after it
    out = []
    for it in items:
        if it["time"] is not None and (it["time"] < cutoff or it["time"] > ahead):
            continue
        fp = fingerprint(it)
        it["new"] = fp not in seen
        if it["new"] or show_all:
            out.append(it)
    out.sort(key=lambda i: (i["time"] or now), reverse=True)
    return out


def remember(items, state, now):
    seen = state.setdefault("seen", {})
    stamp = now.isoformat()
    for it in items:
        seen.setdefault(fingerprint(it), stamp)
    cutoff = (now - _dt.timedelta(days=KEEP_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    state["last_check"] = stamp


def render_moves(moves, move_pct=MOVE_PCT):
    out = []
    for m in moves:
        flag = "MOVING" if abs(m["pct"]) >= move_pct else ""
        when = (" at %s IST" % to_ist(m["time"]).strftime("%H:%M")) if m.get("time") else ""
        out.append("%-6s %s %+.2f%% vs yesterday's close %s%s  %s" % (
            m["ticker"], "{:,.2f}".format(m["price"]), m["pct"], "{:,.2f}".format(m["prev_close"]),
            when, flag))
    return out


def render(items, errors, now, moves=None, move_pct=MOVE_PCT):
    out = render_moves(moves or [], move_pct)
    if out:
        out.append("")
    if not items:
        out.append("Nothing new.")
    for it in items:
        when = to_ist(it["time"]).strftime("%Y-%m-%d %H:%M IST") if it["time"] else "time unknown"
        mark = "!" if it.get("important") else " "
        who = ",".join(it["touches"]) or it["ticker"]
        out.append("%s %s  %-11s %-13s %s" % (mark, when, who, it["source"], it["title"]))
        extra = []
        if it["tags"]:
            extra.append("tags: " + ", ".join(it["tags"]))
        extra.append(it["link"])
        out.append("      " + "  ".join(extra))
    for e in errors:
        out.append("note: %s" % e)
    out.append("Checked %s. Headlines are leads, not facts; open the story before acting. "
               "Not financial advice." % to_ist(now).strftime("%Y-%m-%d %H:%M IST"))
    return "\n".join(out) + "\n"


def _json_ready(items):
    out = []
    for it in items:
        d = dict(it)
        d["time"] = it["time"].isoformat() if it["time"] else None
        out.append(d)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tickers", nargs="*", help="default: watchlist + plan + portfolio")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--watchlist", default="trader/watchlist.json")
    ap.add_argument("--plan", default="trader/plan.json")
    ap.add_argument("--state", default="trader/state/news.json")
    ap.add_argument("--hours", type=float, default=48, help="ignore items older than this")
    ap.add_argument("--all", action="store_true", help="show seen items too")
    ap.add_argument("--no-announcements", action="store_true",
                    help="headlines only, skip the per-symbol exchange announcements")
    ap.add_argument("--no-moves", action="store_true", help="skip the price line per ticker")
    ap.add_argument("--move-pct", type=float, default=MOVE_PCT, help="flag a move this big (percent)")
    ap.add_argument("--user-agent", help="override the browser User-Agent sent to NSE and BSE")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--adapter", help="price adapter: yahoo (default), offline, kite, upstox, angel, dhan")
    ap.add_argument("--offline", metavar="DIR",
                    help="folder of saved payloads: news, announcements and prices")
    ap.add_argument("--now", help="ISO time, for reproducible runs")
    ap.add_argument("--dry-run", action="store_true", help="do not update the state file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.tickers:
        raw = []
        for chunk in args.tickers:
            raw += [x for x in re.split(r"[,\s]+", chunk) if x]
        watch = {mkt.normalise_symbol(t): None for t in raw}
        known = watch_from_files(args.portfolio, args.watchlist, args.plan)
        watch.update({t: known[t] for t in watch if known.get(t)})
    else:
        watch = watch_from_files(args.portfolio, args.watchlist, args.plan)
    if not watch:
        print("news: no tickers. Pass --tickers, or run /trader-scanner first (it writes "
              "trader/watchlist.json).", file=sys.stderr)
        return 2
    now = parse_time(args.now) if args.now else _dt.datetime.now(_dt.timezone.utc)
    state = load_json(args.state) or {}
    errors = []
    ua = args.user_agent or BROWSER_UA
    fetcher = Fetcher(ua, offline=args.offline)
    if not args.offline:
        errors.append("NSE and BSE announcement endpoints need a cookie and rate-limit hard, "
                      "and this path has not been exercised against them: check what comes back "
                      "before you rely on it")
    items = collect(fetcher, watch, state, announcements=not args.no_announcements,
                    errors=errors, now_utc=now)
    items = dedupe(tag(items, watch))
    shown = select(items, state, now, hours=args.hours, show_all=args.all)
    moves = ([] if args.no_moves else
             collect_moves(fetcher, watch, errors, getattr(args, "adapter", None), args.offline))
    if args.json:
        mv = [dict(m, time=m["time"].isoformat() if m.get("time") else None,
                   moving=bool(m.get("pct") is not None and abs(m["pct"]) >= args.move_pct))
              for m in moves]
        print(json.dumps({"checked": now.isoformat(), "moves": mv, "items": _json_ready(shown),
                          "notes": errors}, indent=2))
    else:
        sys.stdout.write(render(shown, errors, now, moves, args.move_pct))
    if not args.dry_run:
        remember(items, state, now)
        folder = os.path.dirname(args.state)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.state, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
    return 0 if any(i["new"] for i in shown) else 1


if __name__ == "__main__":
    sys.exit(main())
