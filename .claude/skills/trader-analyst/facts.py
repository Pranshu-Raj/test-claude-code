#!/usr/bin/env python3
"""Earnings and Flows Analyst for trader-analyst: what changed in the numbers,
and where the money is going. Indian equities, INR crore. Standard library
only. It never places an order.

    python3 facts.py results RELIANCE --from DIR      # quarter by quarter
    python3 facts.py results RELIANCE --paste FILE    # a results table you paste in
    python3 facts.py flows                            # FII and DII net, India VIX, sectors
    python3 facts.py flows --from DIR --now "2026-11-02 10:00"

TWO HALVES, as agreed when this was ported.

FUNDAMENTALS. The arithmetic is the US pack's, unchanged and still tested:
quarterly revenue, gross, operating and net margins, net income and diluted
EPS, each against the same quarter a year earlier, plus the trailing twelve
months. What changed is where the numbers come from. The US version read
SEC's XBRL company-facts API, keyed by CIK, in us-gaap tags. India has no
equivalent free structured API: companies file results with the exchanges as
PDFs and XBRL of varying quality. So this reads a results table in a simple
shape you can save or paste, in rupee crore, and converts it internally to
the same structure the arithmetic already understood.

FLOWS, which the US pack has no concept of and which matter more to an Indian
intraday trader than a quarterly margin: net FII and DII cash-market activity,
India VIX, and which sector indices are leading or lagging. A heavy FII sell
day with VIX rising is a different market from a quiet one, whatever a chart
says.

Everything is in rupees, shown in lakh and crore.

UNVERIFIED: the flows and results endpoints follow NSE's published JSON, but
this port was written where those hosts are unreachable, so no live call has
been made. Saved and pasted inputs are the tested path.

Not investment advice, and a quarterly number is not a day-trading signal.
Day trading loses most people money.
"""

import argparse
import datetime as _dt
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import india_market as mkt

# VERIFY each: NSE changes these paths, and all of them need a browser user
# agent plus a primed cookie. Source: NSE's own pages for each dataset.
NSE_HOME = "https://www.nseindia.com"
NSE_FII_DII = "https://www.nseindia.com/api/fiidiiTradeReact"
NSE_VIX = "https://www.nseindia.com/api/allIndices"
NSE_RESULTS = "https://www.nseindia.com/api/corporates-financial-results?index=equities&symbol=%s"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
PLACEHOLDER = "you@example.com"

# Indian results are reported in rupee crore almost universally.
CRORE = 10000000

# The sector indices worth watching for rotation. VERIFY the symbol map in
# config/india_market.py BENCHMARKS.
SECTOR_INDICES = ("NIFTY BANK", "NIFTY IT", "NIFTY FMCG", "NIFTY AUTO",
                  "NIFTY PHARMA", "NIFTY METAL")

# Which filing labels count as a reporting period. The US version hardcoded
# SEC form types; an Indian results table carries no form, so "results" is
# the label this skill's own adapter writes. The SEC ones stay so saved US
# data still parses.
DURATION_FORMS = ("results", "quarterly", "10-Q", "10-K", "20-F", "40-F")
UA_HELP = ("SEC requires a User-Agent with your name and email. Set \"sec_user_agent\": "
           "\"Your Name you@example.com\" in trader/config.json (your real name and email), "
           "or export SEC_USER_AGENT, or pass --user-agent.")

# The US version keyed these to us-gaap XBRL tags. An Indian results table
# uses plain line names, and filings differ, so each metric accepts several.
CONCEPTS = {
    "revenue": ["revenue", "revenue_from_operations", "total_income", "net_sales",
                "income_from_operations", "RevenueFromOperations"],
    "cost_of_revenue": ["cost_of_materials", "cost_of_revenue", "cost_of_goods_sold",
                        "CostOfMaterialsConsumed"],
    "gross_profit": ["gross_profit", "GrossProfit"],
    "operating_income": ["operating_income", "ebit", "profit_before_tax_and_exceptional",
                         "ProfitBeforeInterestAndTax", "ebitda"],
    "net_income": ["net_income", "net_profit", "profit_after_tax", "pat",
                   "ProfitLossForPeriod"],
    "eps_diluted": ["eps_diluted", "diluted_eps", "DilutedEarningsPerShare"],
}
LINES = ("revenue", "cost_of_revenue", "gross_profit", "operating_income", "net_income")
FLOWS = LINES   # kept under the old name


class FactsError(Exception):
    pass


def user_agent(cli=None, config="trader/config.json"):
    ua = (cli or os.environ.get("SEC_USER_AGENT") or "").strip()
    if not ua and config and os.path.isfile(config):
        try:
            with open(config, encoding="utf-8") as fh:
                ua = str(json.load(fh).get("sec_user_agent") or "").strip()
        except (OSError, ValueError):
            ua = ""
    if not ua or "@" not in ua or PLACEHOLDER in ua:
        return None
    return ua


class Fetcher(object):
    def __init__(self, ua=None, offline=None, pause=0.15):
        self.ua, self.offline, self.pause, self._last = ua, offline, pause, 0.0

    def json(self, url, name):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise FactsError("offline: no file %s" % path)
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        if not self.ua:
            raise FactsError(UA_HELP)
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": self.ua,
                                                   "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise FactsError("SEC has no data at %s (no XBRL filings for this CIK?)" % url)
            raise FactsError("HTTP %s for %s" % (exc.code, url))
        except urllib.error.URLError as exc:
            raise FactsError("could not reach %s: %s" % (url, exc.reason))
        return json.loads(data.decode("utf-8"))


# ---------------------------------------------------------------- facts

def _date(s):
    return _dt.date.fromisoformat(s)


def facts_for(companyfacts, concept, unit_hint=None):
    """Raw facts for one us-gaap concept (USD, or USD/shares for EPS)."""
    node = companyfacts.get("facts", {}).get("us-gaap", {}).get(concept)
    if not node:
        return []
    units = node.get("units", {})
    unit = unit_hint if unit_hint in units else next(iter(units), None)
    return units.get(unit, []) if unit else []


def periods(facts):
    """{(start, end): fact} for duration facts from a reporting filing.

    The value comes from the latest filing (restatements win). The fiscal
    label (fy, fp) comes from the first filing that reported the period: a
    later 10-Q that repeats it as a comparison carries its own fy and fp."""
    latest, first = {}, {}
    for f in facts:
        if "start" not in f or f.get("form", "").split("/")[0] not in DURATION_FORMS:
            continue
        key = (f["start"], f["end"])
        stamp = (f.get("filed", ""), f.get("accn", ""))
        if key not in latest or stamp > (latest[key].get("filed", ""), latest[key].get("accn", "")):
            latest[key] = f
        if key not in first or stamp < (first[key].get("filed", ""), first[key].get("accn", "")):
            first[key] = f
    out = {}
    for key, f in latest.items():
        g = dict(f)
        g["fy"], g["fp"] = first[key].get("fy"), first[key].get("fp")
        g["first_filed"] = first[key].get("filed")
        out[key] = g
    return out


def quarterly(facts, derive_q4=True):
    """{end: {"value", "start", "end", "derived", "fy", "fp", "form"}} three-month values."""
    per = periods(facts)
    q = {}
    years = []
    for (start, end), f in per.items():
        days = (_date(end) - _date(start)).days
        if 80 <= days <= 100:
            q[end] = {"value": f["val"], "start": start, "end": end, "derived": False,
                      "fy": f.get("fy"), "fp": f.get("fp"), "form": f.get("form"),
                      "filed": f.get("filed")}
        elif 350 <= days <= 380:
            years.append((start, end, f))
    if derive_q4:
        for start, end, f in years:
            if end in q:
                continue
            inside = [v for v in q.values() if v["start"] >= start and v["end"] < end]
            inside.sort(key=lambda v: v["end"])
            if len(inside) != 3:
                continue
            last_q_start = _date(inside[-1]["end"]) + _dt.timedelta(days=1)
            q[end] = {"value": f["val"] - sum(v["value"] for v in inside),
                      "start": last_q_start.isoformat(), "end": end, "derived": True,
                      "fy": f.get("fy"), "fp": "Q4", "form": f.get("form"),
                      "filed": f.get("filed")}
    return q


def series(companyfacts, key, derive_q4=True):
    """Quarterly series for a metric, trying its concepts in order and filling gaps."""
    unit = "INR/shares" if key == "eps_diluted" else "INR"
    merged, used = {}, []
    for concept in CONCEPTS[key]:
        q = quarterly(facts_for(companyfacts, concept, unit), derive_q4)
        if not q:
            continue
        added = False
        for end, v in q.items():
            if end not in merged:
                merged[end] = dict(v, concept=concept)
                added = True
        if added:
            used.append(concept)
    return merged, used


def results_to_facts(doc):
    """An Indian quarterly results table -> the facts structure series() reads.

    Accepts a dict with "quarters", each carrying "end" (YYYY-MM-DD) and any
    of the line names in CONCEPTS. Values are in rupee CRORE unless
    "unit" says "inr", in which case they are absolute rupees. EPS is always
    absolute rupees per share.

    This is the seam that replaces SEC's XBRL company-facts API. The
    arithmetic above it is unchanged.
    """
    if not isinstance(doc, dict):
        raise FactsError("the results file is not a JSON object")
    quarters = doc.get("quarters")
    if not isinstance(quarters, list) or not quarters:
        raise FactsError("the results file has no \"quarters\" list")
    scale = 1.0 if str(doc.get("unit") or "crore").lower() == "inr" else float(CRORE)
    facts = {}
    for q in quarters:
        if not isinstance(q, dict):
            continue
        end = str(q.get("end") or "").strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", end):
            raise FactsError("a quarter has no valid \"end\" date: %r" % q.get("end"))
        start = q.get("start")
        if not start:
            d = _date(end)
            start = (d - _dt.timedelta(days=91)).isoformat()
        for key, names in CONCEPTS.items():
            for name in names:
                if name in q and q[name] is not None:
                    unit = "INR/shares" if key == "eps_diluted" else "INR"
                    mult = 1.0 if key == "eps_diluted" else scale
                    node = facts.setdefault(name, {"units": {}})
                    node["units"].setdefault(unit, []).append({
                        "start": start, "end": end, "val": float(q[name]) * mult,
                        "form": q.get("form") or "results", "fp": q.get("fp") or "",
                        "fy": _date(end).year, "frame": "",
                    })
                    break
    if not facts:
        raise FactsError("no known result lines found. Expected some of: %s"
                         % ", ".join(sorted(set(n for v in CONCEPTS.values() for n in v))))
    return {"facts": {"us-gaap": facts}, "entityName": doc.get("company") or doc.get("symbol") or ""}


def load_results(symbol, from_dir=None, paste=None):
    """Read a results table from --paste FILE or <DIR>/<SYMBOL>-results.json."""
    path = paste
    if not path:
        if not from_dir:
            raise FactsError("give --paste FILE or --from DIR with %s-results.json"
                             % mkt.normalise_symbol(symbol))
        path = os.path.join(from_dir, "%s-results.json" % mkt.normalise_symbol(symbol))
    if not os.path.exists(path):
        raise FactsError("no results file at %s" % path)
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except ValueError as exc:
        raise FactsError("%s is not valid JSON: %s" % (path, exc))
    return results_to_facts(doc), doc


def _yoy_match(series_, end, days=365, slack=10):
    target = _date(end) - _dt.timedelta(days=days)
    best = None
    for e in series_:
        gap = abs((_date(e) - target).days)
        if gap <= slack and (best is None or gap < best[0]):
            best = (gap, e)
    return series_[best[1]] if best else None


def pct(a, b):
    if a is None or b in (None, 0):
        return None
    return round(100.0 * (a - b) / abs(b), 1)


def margin(part, whole):
    if part is None or not whole:
        return None
    return round(100.0 * part / whole, 1)


def analyse(companyfacts, quarters=6, name=None):
    """Every number in the report, as a dict."""
    s = {}
    concepts = {}
    for key in CONCEPTS:
        s[key], concepts[key] = series(companyfacts, key, derive_q4=True)
    rev = s["revenue"]
    if not rev and not s["net_income"]:
        raise FactsError("no quarterly revenue or net income in the XBRL facts")
    ends = sorted(set(rev) | set(s["net_income"]), reverse=True)[:quarters]
    rows = []
    for end in ends:
        r = rev.get(end, {}).get("value")
        gp = s["gross_profit"].get(end, {}).get("value")
        if gp is None and r is not None and end in s["cost_of_revenue"]:
            gp = r - s["cost_of_revenue"][end]["value"]
        oi = s["operating_income"].get(end, {}).get("value")
        ni = s["net_income"].get(end, {}).get("value")
        eps = s["eps_diluted"].get(end, {}).get("value")
        prior = {k: _yoy_match(s[k], end) for k in ("revenue", "net_income", "eps_diluted")}
        base = rev.get(end) or s["net_income"].get(end) or {}
        rows.append({
            "end": end, "start": base.get("start"),
            "fiscal": "%s %s" % (base.get("fp") or "", base.get("fy") or ""),
            "derived": any(s[k].get(end, {}).get("derived") for k in ("revenue", "net_income")),
            "revenue": r, "gross_profit": gp, "operating_income": oi, "net_income": ni,
            "eps_diluted": round(eps, 2) if eps is not None else None,
            "gross_margin": margin(gp, r), "operating_margin": margin(oi, r),
            "net_margin": margin(ni, r),
            "revenue_yoy": pct(r, (prior["revenue"] or {}).get("value")),
            "net_income_yoy": pct(ni, (prior["net_income"] or {}).get("value")),
            "eps_yoy": pct(eps, (prior["eps_diluted"] or {}).get("value")),
            "filed": base.get("filed"), "form": base.get("form"),
        })
    ttm = {}
    for key in ("revenue", "net_income", "eps_diluted"):
        last4 = sorted(s[key])[-4:]
        if len(last4) == 4 and (_date(last4[-1]) - _date(last4[0])).days < 300:
            ttm[key] = sum(s[key][e]["value"] for e in last4)
    if "eps_diluted" in ttm:
        ttm["eps_diluted"] = round(ttm["eps_diluted"], 2)
    return {"entity": name or companyfacts.get("entityName"),
            "cik": str(companyfacts.get("cik", "")).zfill(10),
            "rows": rows, "ttm": ttm, "concepts": concepts}



def changes(result):
    """Plain sentences: what changed in the latest quarter."""
    rows = result["rows"]
    if not rows:
        return []
    cur = rows[0]
    prev = rows[1] if len(rows) > 1 else None
    out = []

    def money(v):
        return "n/a" if v is None else mkt.format_inr_words(v)

    label = "Quarter ending %s%s" % (cur["end"], " (derived from the 10-K)" if cur["derived"] else "")
    line = "%s: revenue %s" % (label, money(cur["revenue"]))
    if cur["revenue_yoy"] is not None:
        line += " (%+.1f%% vs a year earlier)" % cur["revenue_yoy"]
    line += ", net income %s" % money(cur["net_income"])
    if cur["net_income_yoy"] is not None:
        line += " (%+.1f%%)" % cur["net_income_yoy"]
    if cur["eps_diluted"] is not None:
        line += ", diluted EPS %s" % mkt.format_inr_price(cur["eps_diluted"])
        if cur["eps_yoy"] is not None:
            line += " (%+.1f%%)" % cur["eps_yoy"]
    out.append(line + ".")
    for key, name in (("gross_margin", "Gross margin"), ("operating_margin", "Operating margin"),
                      ("net_margin", "Net margin")):
        if cur[key] is None:
            continue
        s = "%s %.1f%%" % (name, cur[key])
        if prev and prev[key] is not None:
            s += " (%+.1f points vs the quarter before)" % (cur[key] - prev[key])
        out.append(s + ".")
    if cur["net_income"] is not None and cur["operating_income"] is not None \
            and cur["net_income"] > cur["operating_income"]:
        out.append("Net income is above operating income: it includes gains outside the "
                   "business (investments, tax items). Check the 10-Q before reading it as growth.")
    if prev and cur["revenue"] is not None and prev["revenue"]:
        out.append("Revenue %+.1f%% vs the quarter before (many businesses are seasonal: "
                   "compare with a year earlier first)." % pct(cur["revenue"], prev["revenue"]))
    return out



# ---------------------------------------------------------------- output

# ---------------------------------------------------------------- flows

def parse_fii_dii(data):
    """NSE's fiidiiTradeReact JSON -> rows. UNVERIFIED against the live endpoint.

    Shape follows NSE's public API: a list with category, date, buyValue,
    sellValue and netValue, all in rupee CRORE.
    """
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
    except ValueError as exc:
        raise FactsError("FII/DII data is not JSON: %s" % exc)
    rows = doc if isinstance(doc, list) else (doc.get("data") or [])
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        cat = " ".join(str(r.get("category") or "").split())
        net = r.get("netValue")
        if net is None and r.get("buyValue") is not None and r.get("sellValue") is not None:
            try:
                net = float(str(r["buyValue"]).replace(",", "")) - \
                      float(str(r["sellValue"]).replace(",", ""))
            except ValueError:
                net = None
        def _f(x):
            try:
                return float(str(x).replace(",", ""))
            except (TypeError, ValueError):
                return None
        out.append({"category": cat, "date": str(r.get("date") or ""),
                    "buy_crore": _f(r.get("buyValue")), "sell_crore": _f(r.get("sellValue")),
                    "net_crore": _f(net),
                    "net_inr": (_f(net) * CRORE) if _f(net) is not None else None})
    return out


def parse_indices(data):
    """NSE's allIndices JSON -> {index name: {last, change_pct}}, India VIX included."""
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
    except ValueError as exc:
        raise FactsError("index data is not JSON: %s" % exc)
    rows = doc.get("data") if isinstance(doc, dict) else doc
    out = {}
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        name = " ".join(str(r.get("index") or r.get("indexSymbol") or "").upper().split())
        if not name:
            continue
        def _f(x):
            try:
                return float(str(x).replace(",", ""))
            except (TypeError, ValueError):
                return None
        out[name] = {"last": _f(r.get("last") or r.get("lastPrice")),
                     "change_pct": _f(r.get("percentChange") or r.get("pChange")),
                     "prev_close": _f(r.get("previousClose"))}
    return out


def rotation(indices, sectors=SECTOR_INDICES, benchmark=None):
    """Sector indices ranked by how far they beat or lag the benchmark today."""
    bench_name = benchmark or mkt.PRIMARY_BENCHMARK
    bench = (indices.get(bench_name) or {}).get("change_pct")
    out = []
    for name in sectors:
        row = indices.get(name)
        if not row or row.get("change_pct") is None:
            continue
        rel = None if bench is None else round(row["change_pct"] - bench, 2)
        out.append({"index": name, "change_pct": row["change_pct"], "vs_benchmark": rel})
    out.sort(key=lambda r: (r["vs_benchmark"] if r["vs_benchmark"] is not None
                            else r["change_pct"]), reverse=True)
    return {"benchmark": bench_name, "benchmark_pct": bench, "sectors": out}


def flows_report(fii_dii, indices, now_utc):
    """Plain lines: who bought, what volatility did, which sectors led."""
    lines = ["Flows, %s IST" % mkt.to_ist(now_utc).strftime("%Y-%m-%d %H:%M")]
    if fii_dii:
        for r in fii_dii:
            if r.get("net_crore") is None:
                continue
            side = "bought" if r["net_crore"] > 0 else "sold"
            lines.append("  %-28s %s %s net%s" % (
                r["category"][:28], side, mkt.format_inr_words(abs(r["net_inr"])),
                (" on %s" % r["date"]) if r.get("date") else ""))
    else:
        lines.append("  no FII or DII data")
    vix = indices.get(mkt.VOLATILITY_INDEX) or indices.get("INDIA VIX")
    if vix and vix.get("last") is not None:
        move = ("" if vix.get("change_pct") is None
                else " (%+.2f%% today)" % vix["change_pct"])
        lines.append("  India VIX %.2f%s" % (vix["last"], move))
        if vix.get("change_pct") is not None and vix["change_pct"] >= 5:
            lines.append("    Volatility is rising: stops get hit more often on days like this.")
    rot = rotation(indices)
    if rot["sectors"]:
        bench = ("" if rot["benchmark_pct"] is None
                 else " (%s %+.2f%%)" % (rot["benchmark"], rot["benchmark_pct"]))
        lines.append("  Sectors against the benchmark%s:" % bench)
        for r in rot["sectors"]:
            rel = "" if r["vs_benchmark"] is None else "  %+.2f%% relative" % r["vs_benchmark"]
            lines.append("    %-14s %+.2f%%%s" % (r["index"], r["change_pct"], rel))
    lines.append("")
    lines.append("Flows are one day's cash-market activity, reported after the close, and "
                 "they do not predict tomorrow. Not investment advice.")
    return "\n".join(lines)


def _m(v):
    """A rupee figure in crore, the unit Indian results are reported in."""
    if v is None:
        return "-"
    return mkt.group_indian(int(round(v / float(CRORE))))


def _p(v):
    return "-" if v is None else "%+.1f%%" % v if v < 0 or v > 0 else "0.0%"


def _pm(v):
    return "-" if v is None else "%.1f%%" % v


def render(ticker, result, filings):
    out = ["%s (%s). Rupee CRORE except EPS, which is rupees a share. "
           "Source: the results table you supplied." % (result["entity"] or ticker, ticker), ""]
    out.append("%-11s %-9s %10s %8s %7s %7s %7s %10s %8s %7s" % (
        "quarter end", "fiscal", "revenue", "rev yoy", "gross", "oper", "net",
        "net inc", "EPS dil", "EPS yoy"))
    for r in result["rows"]:
        out.append("%-11s %-9s %10s %8s %7s %7s %7s %10s %8s %7s%s" % (
            r["end"], r["fiscal"].strip()[:9], _m(r["revenue"]), _p(r["revenue_yoy"]),
            _pm(r["gross_margin"]), _pm(r["operating_margin"]), _pm(r["net_margin"]),
            _m(r["net_income"]), "-" if r["eps_diluted"] is None else "%.2f" % r["eps_diluted"],
            _p(r["eps_yoy"]), "  derived" if r["derived"] else ""))
    t = result["ttm"]
    if t:
        out.append("")
        out.append("Last 4 quarters: revenue %s, net income %s, diluted EPS %s." % (
            mkt.RUPEE + _m(t.get("revenue")) + " crore" if t.get("revenue") is not None else "-",
            mkt.RUPEE + _m(t.get("net_income")) + " crore" if t.get("net_income") is not None else "-",
            mkt.format_inr_price(t["eps_diluted"]) if t.get("eps_diluted") is not None else "-"))
    out.append("")
    out.append("What changed:")
    for line in changes(result):
        out.append("  - " + line)
    if any(r["derived"] for r in result["rows"]):
        out.append("")
        out.append("A derived quarter is the annual figure minus the three reported quarters; "
                   "its EPS is approximate.")
    out.append("")
    out.append("A quarterly number is not a day-trading signal. Not investment advice.")
    return "\n".join(out) + "\n"


def metrics_row(ticker, result):
    rows = result["rows"]
    cur = rows[0] if rows else {}
    t = result["ttm"]
    return {"ticker": ticker, "name": result["entity"], "cik": result["cik"],
            "period_end": cur.get("end"), "revenue_ttm": t.get("revenue"),
            "net_income_ttm": t.get("net_income"), "eps_ttm": t.get("eps_diluted"),
            "revenue_growth_yoy": cur.get("revenue_yoy"), "eps_growth_yoy": cur.get("eps_yoy"),
            "net_income_growth_yoy": cur.get("net_income_yoy"),
            "gross_margin": cur.get("gross_margin"), "operating_margin": cur.get("operating_margin"),
            "net_margin": cur.get("net_margin"), "source": "SEC XBRL company facts"}


def clean_title(title):
    return re.sub(r"\s*/[A-Z]{2,5}/?\s*$", "", title or "").strip() or None



def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("results", help="quarter by quarter from a results table")
    p.add_argument("symbols", nargs="*", help="NSE symbols, e.g. RELIANCE TCS")
    p.add_argument("--from", dest="from_dir", metavar="DIR",
                   help="folder holding <SYMBOL>-results.json")
    p.add_argument("--paste", metavar="FILE", help="one results table, for one symbol")
    p.add_argument("--quarters", type=int, default=6)
    p.add_argument("--metrics", metavar="FILE", help="merge a row per symbol into this JSON")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("flows", help="FII and DII net, India VIX, sector rotation")
    p.add_argument("--from", dest="from_dir", metavar="DIR",
                   help="folder holding fii-dii.json and indices.json")
    p.add_argument("--now", help='pretend it is this IST time, e.g. "2026-11-02 10:00"')
    p.add_argument("--json", action="store_true")

    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 2
    if a.cmd == "flows":
        return cmd_flows(a)
    return cmd_results(a)


def _now(text):
    if not text:
        return _dt.datetime.utcnow().replace(microsecond=0)
    t = str(text).strip().replace("Z", "+00:00")
    try:
        dt = _dt.datetime.fromisoformat(t)
    except ValueError:
        raise FactsError('--now wants a time like "2026-11-02 10:00" (IST) or ...Z (UTC)')
    if dt.tzinfo is None:
        return mkt.ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def _read_local(from_dir, name):
    if not from_dir:
        raise FactsError("give --from DIR with %s (the live NSE endpoints need a cookie and "
                         "have not been exercised from here)" % name)
    path = os.path.join(from_dir, name)
    if not os.path.exists(path):
        raise FactsError("no %s" % path)
    with open(path, "rb") as fh:
        return fh.read()


def cmd_flows(a):
    try:
        now = _now(a.now)
        fii = parse_fii_dii(_read_local(a.from_dir, "fii-dii.json"))
        idx = parse_indices(_read_local(a.from_dir, "indices.json"))
    except FactsError as exc:
        print("facts: %s" % exc, file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps({"checked": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "fii_dii": fii,
                          "india_vix": idx.get(mkt.VOLATILITY_INDEX),
                          "rotation": rotation(idx)}, indent=2))
        return 0
    print(flows_report(fii, idx, now))
    return 0


def cmd_results(a):
    symbols = []
    for chunk in (a.symbols or []):
        symbols += [x for x in re.split(r"[,\s]+", chunk) if x]
    if not symbols and a.paste:
        symbols = ["PASTED"]
    if not symbols:
        print("facts: give one or more NSE symbols, with --from DIR or --paste FILE",
              file=sys.stderr)
        return 2
    out, rows, failed = [], [], 0
    for raw in symbols:
        sym = mkt.normalise_symbol(raw)
        try:
            cf, doc = load_results(sym, a.from_dir, a.paste)
            result = analyse(cf, quarters=a.quarters, name=doc.get("company"))
        except FactsError as exc:
            print("facts: %s: %s" % (sym, exc), file=sys.stderr)
            failed += 1
            continue
        out.append((sym, result))
        rows.append(metrics_row(sym, result))
    if not out:
        return 1
    if a.json:
        print(json.dumps({"results": [dict(r, symbol=s) for s, r in out]}, indent=2,
                         default=str))
    else:
        for i, (sym, result) in enumerate(out):
            if i:
                print()
            print(render(sym, result, []))
    if a.metrics:
        merged = {}
        if os.path.exists(a.metrics):
            try:
                with open(a.metrics, encoding="utf-8") as fh:
                    merged = json.load(fh)
            except ValueError:
                merged = {}
        merged.setdefault("rows", [])
        by = {r.get("symbol") or r.get("ticker"): r for r in merged["rows"]}
        for r in rows:
            by[r.get("symbol") or r.get("ticker")] = r
        merged["rows"] = [by[k] for k in sorted(by)]
        with open(a.metrics, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, indent=1, sort_keys=True, default=str)
            fh.write("\n")
        print("\nWrote %s (%d rows)." % (a.metrics, len(merged["rows"])))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
