#!/usr/bin/env python3
"""Insider Disclosures for trader-insider: who inside the company is buying
or selling, from SEBI's mandatory filings. NSE/BSE, IST. Standard library
only. It never places an order.

    python3 insider.py list --symbol RELIANCE
    python3 insider.py list --days 7 --min-value 10000000
    python3 insider.py new                       # only what is new since last check
    python3 insider.py list --offline DIR --now "2026-11-02 10:00"

WHAT THIS READS. Under SEBI's Prohibition of Insider Trading regulations,
Regulation 7(2), a promoter, director or designated person whose trades in a
quarter exceed 10 lakh rupees in value must disclose them to the company, and
the company must pass that to the exchange within two trading days. Those
disclosures are public, and they are what this skill reads.

This replaces the US pack's Congress-trade tracker. There is no Indian
equivalent of the STOCK Act: members of Parliament do not file periodic
transaction reports. The closest real signal is company insiders, which is
arguably a better one, since a promoter selling is a fact about that company.

WHAT A DISCLOSURE IS, AND IS NOT:

  - It is PUBLIC, filed under a legal obligation, and usually AFTER the trade.
    It is not inside information and acting on it is not insider trading.
  - It is late by design: up to two trading days after the company is told,
    which is itself after the trade. Treat it as context, not a signal to
    chase.
  - A disposal is not always bearish and an acquisition is not always
    bullish. ESOP allotments, pledge invocations, inter-promoter transfers,
    gifts and open-offer tenders all show up here, which is why the mode
    matters more than the direction.

Phase 1 covers Regulation 7(2) disclosures. Bulk and block deals, and SAST
substantial-acquisition filings, are a later phase; the loader is shaped to
take them without rework.

UNVERIFIED: the endpoints follow NSE's and BSE's public JSON, but this port
was written where those hosts are unreachable, so no live call has been made.
NSE needs a browser user agent and a primed cookie and rate-limits hard. All
the parsing, filtering, valuation and alert-once logic is tested offline.

Day trading loses most people money, and a disclosure you can read is already
in the price.
"""

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import india_market as mkt

# VERIFY both: NSE and BSE change these paths without notice.
# Source: NSE "Insider Trading Disclosures" page, BSE corporate announcements API.
NSE_HOME = "https://www.nseindia.com"
NSE_PIT = "https://www.nseindia.com/api/corporates-pit?index=equities"
NSE_PIT_SYMBOL = "https://www.nseindia.com/api/corporates-pit?index=equities&symbol=%s"
BSE_PIT = ("https://api.bseindia.com/BseIndiaAPI/api/InsiderTrading_new/w"
           "?scripcode=%s&strType=SAST")
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# SEBI's Reg 7(2) threshold: trades above this value in a quarter must be
# disclosed. VERIFY: Source: SEBI (Prohibition of Insider Trading)
# Regulations, Regulation 7(2).
DISCLOSURE_THRESHOLD_INR = 1000000      # 10 lakh

# The exchange must receive it within this many trading days of being told.
# VERIFY: Source: the same regulation.
DISCLOSURE_DAYS = 2

CATEGORIES = {
    "promoter": "promoter",
    "promoter group": "promoter group",
    "director": "director",
    "designated person": "designated person",
    "immediate relative": "relative of an insider",
    "employee": "employee",
}

# Acquisition and disposal modes. The mode changes what a trade MEANS, so it
# is kept and shown rather than collapsed into buy or sell.
MODES = [
    ("market", r"market (purchase|sale)|open market"),
    ("esop", r"\besop\b|employee stock|allotment"),
    ("off_market", r"off.?market|inter.?se transfer|gift"),
    ("pledge", r"pledge|invocation|encumbrance"),
    ("offer", r"open offer|buyback|tender"),
    ("rights", r"rights issue|preferential"),
]

ACQUIRED = re.compile(r"acquisi|acquir|purchase|buy|allot", re.I)
DISPOSED = re.compile(r"dispos|sale|sold|sell|transfer out", re.I)


class InsiderError(Exception):
    pass


# ---------------------------------------------------------------- helpers

def to_ist(utc):
    return mkt.to_ist(utc)


def parse_now(text):
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = str(text).strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise InsiderError('--now wants a time like "2026-11-02 10:00" (IST) or ...Z (UTC)')
    if dt.tzinfo is None:
        return mkt.ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_date(value):
    """The exchanges use several shapes. -> a date, or None."""
    v = " ".join(str(value or "").split())
    if not v:
        return None
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(v[:len(fmt) + 4] if "%H" in fmt else v, fmt).date()
        except ValueError:
            continue
    m = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})", v)
    if m:
        months = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                  "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
        mon = months.get(m.group(2).lower())
        if mon:
            return datetime.date(int(m.group(3)), mon, int(m.group(1)))
    return None


def _num(value):
    v = re.sub(r"[^\d.\-]", "", str(value or ""))
    try:
        return float(v) if v not in ("", "-", ".") else None
    except ValueError:
        return None


def mode_of(text):
    low = " ".join(str(text or "").lower().split())
    for name, rx in MODES:
        if re.search(rx, low):
            return name
    return "other"


def direction_of(row_text):
    if DISPOSED.search(row_text or ""):
        return "disposed"
    if ACQUIRED.search(row_text or ""):
        return "acquired"
    return "unknown"


# ---------------------------------------------------------------- fetching

class Fetcher(object):
    """Primes an NSE cookie, then fetches. Offline reads saved payloads."""

    def __init__(self, offline=None, timeout=30):
        self.offline = offline
        self.timeout = timeout
        self._opener = None

    def _live(self, url, name):
        if self._opener is None:
            import http.cookiejar
            jar = http.cookiejar.CookieJar()
            self._opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(jar))
            if "nseindia" in url:
                try:
                    self._opener.open(urllib.request.Request(
                        NSE_HOME, headers={"User-Agent": BROWSER_UA}), timeout=self.timeout).read()
                except (urllib.error.URLError, OSError):
                    pass    # the cookie prime is best effort
        req = urllib.request.Request(url, headers={
            "User-Agent": BROWSER_UA, "Accept": "application/json",
            "Referer": NSE_HOME + "/companies-listing/corporate-filings-insider-trading"})
        try:
            with self._opener.open(req, timeout=self.timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise InsiderError("%s answered HTTP %s (NSE rate-limits and needs a cookie)"
                               % (url.split("/")[2], e.code))
        except (urllib.error.URLError, OSError) as e:
            raise InsiderError("could not reach %s: %s" % (url.split("/")[2],
                                                           getattr(e, "reason", e)))

    def get(self, url, name):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.exists(path):
                raise InsiderError("offline: no file %s" % path)
            with open(path, "rb") as fh:
                return fh.read()
        return self._live(url, name)


def pdf_to_text(pdf_path):
    """An attachment's text, when pdftotext is installed. None otherwise."""
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-layout", pdf_path, "-"], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.decode("utf-8", "replace") if out.returncode == 0 else None


# ---------------------------------------------------------------- parsing

def parse_nse_pit(data):
    """NSE's corporates-pit JSON -> disclosure rows.

    UNVERIFIED against the live endpoint. The shape follows NSE's public API:
    records with symbol, company, acqName, personCategory, secAcq, secVal,
    acqMode, befAcqSharesNo, afterAcqSharesNo, date and an attachment.
    """
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
    except ValueError as exc:
        raise InsiderError("NSE insider data is not JSON: %s" % exc)
    rows = doc if isinstance(doc, list) else (doc.get("data") or doc.get("rows") or [])
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        sym = mkt.normalise_symbol(r.get("symbol") or "")
        if not sym:
            continue
        mode_text = r.get("acqMode") or r.get("tdpTransactionType") or ""
        qty = _num(r.get("secAcq") or r.get("secVal") and None)
        value = _num(r.get("secVal") or r.get("tdpTransactionValue"))
        when = (parse_date(r.get("date")) or parse_date(r.get("intimDt"))
                or parse_date(r.get("acqfromDt")))
        traded = parse_date(r.get("acqfromDt")) or parse_date(r.get("fromDate"))
        cat = " ".join(str(r.get("personCategory") or "").lower().split())
        out.append({
            "id": "nsepit:%s" % (r.get("dissemDT") or "%s|%s|%s|%s"
                                 % (sym, r.get("acqName"), r.get("secAcq"), r.get("date"))),
            "symbol": sym,
            "company": " ".join(str(r.get("company") or "").split()),
            "person": " ".join(str(r.get("acqName") or "").split()),
            "category": CATEGORIES.get(cat, cat or "unknown"),
            "direction": direction_of("%s %s" % (r.get("tdpTransactionType") or "", mode_text)),
            "mode": mode_of(mode_text),
            "mode_text": " ".join(str(mode_text).split()),
            "shares": qty,
            "value_inr": value,
            "held_before": _num(r.get("befAcqSharesNo")),
            "held_after": _num(r.get("afterAcqSharesNo")),
            "traded_on": traded.isoformat() if traded else None,
            "disclosed_on": when.isoformat() if when else None,
            "attachment": r.get("attachment") or r.get("attchmntFile") or "",
            "source": "NSE Reg 7(2)",
        })
    return out


def lag_days(row):
    """Trading days between the trade and the disclosure, or None."""
    t, d = parse_date(row.get("traded_on")), parse_date(row.get("disclosed_on"))
    if not t or not d:
        return None
    n, cur = 0, t
    while cur < d:
        cur += datetime.timedelta(days=1)
        if mkt.trading_day(cur)[0]:
            n += 1
    return n


def annotate(rows, holdings=None):
    """Add the late flag and whether you hold the stock."""
    held = {mkt.normalise_symbol(h) for h in (holdings or [])}
    for r in rows:
        lag = lag_days(r)
        r["lag_trading_days"] = lag
        r["late"] = bool(lag is not None and lag > DISCLOSURE_DAYS)
        r["you_hold"] = r["symbol"] in held
        r["below_threshold"] = bool(r.get("value_inr") is not None
                                    and r["value_inr"] < DISCLOSURE_THRESHOLD_INR)
    return rows


def select(rows, symbols=None, days=None, min_value=None, direction=None,
           category=None, today=None):
    """Filter disclosures. `days` counts back from today on disclosed_on."""
    want = {mkt.normalise_symbol(s) for s in (symbols or [])}
    out = []
    for r in rows:
        if want and r["symbol"] not in want:
            continue
        if direction and r["direction"] != direction:
            continue
        if category and category.lower() not in (r["category"] or "").lower():
            continue
        if min_value is not None and (r.get("value_inr") is None
                                      or r["value_inr"] < float(min_value)):
            continue
        if days is not None:
            d = parse_date(r.get("disclosed_on"))
            if not d or not today or (today - d).days > int(days):
                continue
        out.append(r)
    out.sort(key=lambda r: (r.get("disclosed_on") or "", -(r.get("value_inr") or 0)),
             reverse=True)
    return out


# ---------------------------------------------------------------- state

def load_state(path):
    if not path or not os.path.exists(path):
        return {"seen": {}}
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except ValueError:
        return {"seen": {}}
    doc.setdefault("seen", {})
    return doc


def save_state(path, state):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def new_since(rows, state, stamp):
    """Rows not shown before. Each disclosure alerts once."""
    seen = state.setdefault("seen", {})
    fresh = [r for r in rows if r["id"] not in seen]
    for r in fresh:
        seen[r["id"]] = stamp
    return fresh


# ---------------------------------------------------------------- output

def money(v):
    if v is None:
        return "value not stated"
    return mkt.format_inr_words(v)


def describe(rows, now_utc, note=None):
    lines = []
    if not rows:
        lines.append("No insider disclosures matched.")
    for r in rows:
        flag = "!" if (r["you_hold"] or r["late"]) else " "
        lines.append("%s %-10s %-11s %-9s %-9s %s" % (
            flag, r["symbol"], (r.get("disclosed_on") or "date unknown"),
            r["direction"], r["mode"], money(r.get("value_inr"))))
        who = r["person"] or "an insider"
        detail = "    %s (%s)" % (who, r["category"])
        if r.get("shares"):
            detail += ", %s shares" % "{:,}".format(int(r["shares"]))
        if r.get("traded_on"):
            detail += ", traded %s" % r["traded_on"]
        if r.get("lag_trading_days") is not None:
            detail += ", disclosed %d trading day%s later" % (
                r["lag_trading_days"], "" if r["lag_trading_days"] == 1 else "s")
        lines.append(detail)
        if r["late"]:
            lines.append("    LATE: past the %d trading day window in Regulation 7(2)."
                         % DISCLOSURE_DAYS)
        if r["you_hold"]:
            lines.append("    You hold %s." % r["symbol"])
        if r["mode"] in ("esop", "pledge", "off_market"):
            lines.append("    Mode is %s, so the direction may not mean what it looks like."
                         % r["mode_text"] or r["mode"])
    lines.append("")
    lines.append("Checked %s IST. These are PUBLIC filings under SEBI's insider trading "
                 "regulations, made AFTER the trade: context, not a signal to chase."
                 % to_ist(now_utc).strftime("%Y-%m-%d %H:%M"))
    if note:
        lines.append(note)
    lines.append("Not financial advice.")
    return "\n".join(lines)


# ---------------------------------------------------------------- commands

def holdings_from(path):
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except ValueError:
        return []
    out = []
    for p in (doc.get("positions") or []):
        sym = p.get("ticker") or p.get("symbol")
        if sym:
            out.append(mkt.normalise_symbol(sym))
    return out


def gather(a, now):
    fetcher = Fetcher(offline=a.offline)
    notes = []
    if not a.offline:
        note = ("The NSE insider endpoint needs a cookie and rate-limits, and this path "
                "has not been exercised: check what comes back before relying on it.")
        notes.append(note)
        print("note: " + note, file=sys.stderr)   # before the fetch, so a failure keeps it
    syms = []
    for chunk in (a.symbol or []):
        syms += [s for s in re.split(r"[,\s]+", chunk) if s]
    rows = []
    if syms:
        for s in syms:
            sym = mkt.normalise_symbol(s)
            rows += parse_nse_pit(fetcher.get(NSE_PIT_SYMBOL % sym, "nse-pit-%s.json" % sym))
    else:
        rows += parse_nse_pit(fetcher.get(NSE_PIT, "nse-pit.json"))
    annotate(rows, holdings_from(a.portfolio))
    return rows, syms, notes


def cmd_list(a):
    now = parse_now(a.now)
    today = to_ist(now).date()
    rows, syms, notes = gather(a, now)
    picked = select(rows, syms, a.days, a.min_value, a.direction, a.category, today)
    if a.json:
        print(json.dumps({"checked": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                          "rows": picked, "notes": notes}, indent=2))
        return 0 if picked else 1
    print(describe(picked, now, " ".join(notes) if notes else None))
    return 0 if picked else 1


def cmd_new(a):
    now = parse_now(a.now)
    today = to_ist(now).date()
    rows, syms, notes = gather(a, now)
    picked = select(rows, syms, a.days, a.min_value, a.direction, a.category, today)
    state = load_state(a.state)
    fresh = new_since(picked, state, now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    if not a.dry_run:
        save_state(a.state, state)
    if a.json:
        print(json.dumps({"checked": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                          "new": fresh, "notes": notes}, indent=2))
        return 0 if fresh else 1
    if not fresh:
        print("Nothing new since the last check.")
        print("Not financial advice.")
        return 1
    print(describe(fresh, now, " ".join(notes) if notes else None))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--symbol", nargs="*", help="one or more NSE symbols")
        p.add_argument("--days", type=int, help="only disclosures from the last N days")
        p.add_argument("--min-value", type=float, metavar="INR",
                       help="only trades worth at least this much")
        p.add_argument("--direction", choices=["acquired", "disposed"])
        p.add_argument("--category", help="promoter, director, designated person ...")
        p.add_argument("--portfolio", default="trader/portfolio.json")
        p.add_argument("--offline", metavar="DIR", help="folder of saved payloads")
        p.add_argument("--now", help='pretend it is this IST time, e.g. "2026-11-02 10:00"')
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("list", help="disclosures, newest first")
    common(p)
    p = sub.add_parser("new", help="only what is new since the last check")
    common(p)
    p.add_argument("--state", default="trader/state/insider.json")
    p.add_argument("--dry-run", action="store_true")

    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 2
    try:
        return {"list": cmd_list, "new": cmd_new}[a.cmd](a)
    except InsiderError as e:
        print("insider.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
