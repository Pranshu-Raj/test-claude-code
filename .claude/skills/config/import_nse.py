#!/usr/bin/env python3
"""Build data/nse_universe.csv from NSE's own published files.

    python3 config/import_nse.py
    python3 config/import_nse.py --equity FILE --bands FILE --out FILE

Inputs, both in data/source/ and refreshed by hand from NSE:

  EQUITY_L.csv   every cash-market security: symbol, name, series, ISIN
  sec_list.csv   the daily price band per symbol, plus GSM remarks

What it works out, and why each matters to an intraday trader:

  series        EQ can be traded intraday. BE and BZ are TRADE-FOR-TRADE:
                every trade settles by compulsory delivery, so an MIS order
                in one is rejected by the broker. 235 of NSE's 2,627 cash
                symbols are in that state, and the US pack this was ported
                from has no equivalent concept at all.
  band_pct      the daily price band, which the scanner uses to skip a stock
                sitting at its circuit.
  fno_eligible  DERIVED, not official: NSE publishes no fixed band for a
                security in the F&O segment, so "No Band" means F&O-eligible
                (or an index-linked instrument). Good enough to populate the
                fno index tag, and labelled derived so nobody mistakes it for
                the official F&O circular.
  surveillance  GSM stage from the Remarks column. A stage I or worse name
                carries extra restrictions and is usually a bad intraday
                candidate whatever the chart says.

NIFTY 50 and NIFTY 500 membership is NOT in these files. It comes from
niftyindices.com and stays unset until you supply it; --indices takes a CSV
of `symbol,index` rows if you have one.
"""

import argparse
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "data", "source")
OUT = os.path.join(HERE, "data", "nse_universe.csv")

# Only these series can be traded intraday. VERIFY: Source: NSE's series
# definitions. EQ is rolling settlement; BE and BZ are trade-for-trade.
INTRADAY_SERIES = ("EQ",)
TRADE_FOR_TRADE = ("BE", "BZ")


def read(path, key):
    if not os.path.exists(path):
        raise SystemExit("import_nse.py: no %s" % path)
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise SystemExit("import_nse.py: %s is empty" % path)
    sample = rows[0]
    if not any(k and k.strip() == key for k in sample):
        raise SystemExit("import_nse.py: %s has no %s column (got %s)"
                         % (path, key, ", ".join(str(k).strip() for k in sample)))
    return rows


def get(row, name):
    """NSE pads some headers with a leading space."""
    for k in (name, " " + name):
        if k in row and row[k] is not None:
            return str(row[k]).strip()
    for k, v in row.items():
        if k and k.strip() == name:
            return str(v or "").strip()
    return ""


def sector_guess(name):
    """A coarse sector from the company name, so --sector has something to
    work with until you fill real sectors in. Deliberately crude: it is a
    convenience, not a classification."""
    low = " " + name.lower() + " "
    for sector, words in (
            ("financials", (" bank ", " finance ", " financial ", " capital ", " nbfc ",
                            " housing finance ", " securities ", " investment ")),
            ("insurance", (" insurance ", " life insurance ", " assurance ")),
            ("it", (" technolog", " software ", " infotech ", " systems ", " computer ",
                    " digital ", " info ")),
            ("pharma", (" pharma", " laborator", " life science", " healthcare ",
                        " drugs ", " biotech")),
            ("auto", (" motors ", " automobile", " auto ", " tyre", " tyres ")),
            ("metals", (" steel ", " metal", " iron ", " alloy", " aluminium ", " copper ")),
            ("energy", (" power ", " energy ", " petroleum ", " oil ", " gas ", " coal ",
                        " solar ", " renewab")),
            ("materials", (" cement ", " chemical", " paints ", " fertiliser", " fertilizer")),
            ("realty", (" realty ", " estate ", " infrastructure ", " construction ",
                        " builders ", " developers ")),
            ("fmcg", (" foods ", " beverage", " consumer ", " dairy ", " sugar ", " tea ")),
            ("textiles", (" textile", " spinning ", " fabrics ", " apparel", " garments ")),
    ):
        for w in words:
            if w in low:
                return sector
    return ""


def build(equity_path, bands_path, indices_path=None):
    equity = read(equity_path, "SYMBOL")
    bands = read(bands_path, "Symbol")

    band_of, remark_of = {}, {}
    for r in bands:
        sym = get(r, "Symbol")
        if sym:
            band_of[sym] = get(r, "Band")
            remark_of[sym] = get(r, "Remarks").strip('"')

    index_of = {}
    if indices_path and os.path.exists(indices_path):
        with open(indices_path, encoding="utf-8-sig", errors="replace") as fh:
            for r in csv.DictReader(fh):
                sym = (r.get("symbol") or r.get("Symbol") or "").strip().upper()
                idx = (r.get("index") or r.get("Index") or "").strip().lower()
                if sym and idx:
                    index_of.setdefault(sym, set()).add(idx.replace(" ", "").replace("-", ""))

    out, counts = [], {"rows": 0, "no_band": 0, "banded": 0, "unlisted_band": 0,
                       "trade_for_trade": 0, "surveillance": 0}
    for r in equity:
        sym = get(r, "SYMBOL")
        if not sym:
            continue
        counts["rows"] += 1
        series = get(r, "SERIES").upper()
        raw = band_of.get(sym, "")
        fno = raw.lower().replace(" ", "") == "noband"
        band_pct = "" if (fno or not raw) else raw
        if fno:
            counts["no_band"] += 1
        elif band_pct:
            counts["banded"] += 1
        else:
            counts["unlisted_band"] += 1
        if series in TRADE_FOR_TRADE:
            counts["trade_for_trade"] += 1
        remark = remark_of.get(sym, "")
        surveillance = "" if remark in ("", "-") else remark
        if surveillance:
            counts["surveillance"] += 1
        idx = set(index_of.get(sym, ()))
        if fno:
            idx.add("fno")
        out.append({
            "symbol": sym,
            "name": " ".join(get(r, "NAME OF COMPANY").split()),
            "sector": sector_guess(get(r, "NAME OF COMPANY")),
            "indices": "|".join(sorted(idx)),
            "band_pct": band_pct,
            "series": series,
            "intraday": "yes" if series in INTRADAY_SERIES else "no",
            "surveillance": surveillance,
            "isin": get(r, "ISIN NUMBER"),
            "confidence": "official",
        })
    out.sort(key=lambda r: r["symbol"])
    return out, counts


HEADER = """# NSE cash-equity universe for trader-scanner.
#
# GENERATED by config/import_nse.py from NSE's own files in data/source/.
# Do not hand-edit: re-run the importer instead, or your changes are lost on
# the next refresh.
#
# symbol        the NSE trading symbol, the canonical form in these skills
# name          for display
# sector        a COARSE guess from the company name, for the --sector filter.
#               Replace with a real classification if you care about it.
# indices       pipe separated. "fno" is DERIVED from the band column (NSE
#               publishes no fixed band for an F&O security). nifty50 and
#               nifty500 come from niftyindices.com and are NOT in NSE's
#               files, so they stay empty until you supply them.
# band_pct      the daily price band. EMPTY means NSE publishes none, which
#               for a cash equity means it is in the F&O segment and has a
#               dynamic operating range instead. The scanner reports those as
#               band-unknown rather than assuming they are clear.
# series        EQ, BE or BZ.
# intraday      yes only for EQ. BE and BZ are TRADE-FOR-TRADE: compulsory
#               delivery on every trade, so an MIS order is rejected. The
#               scanner skips them and trader-risk blocks them.
# surveillance  GSM stage, when NSE has flagged the security. Blank is normal.
# confidence    official: straight from NSE's published files.
"""


def write(rows, path):
    cols = ["symbol", "name", "sector", "indices", "band_pct", "series",
            "intraday", "surveillance", "isin", "confidence"]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(HEADER)
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--equity", default=os.path.join(SRC, "EQUITY_L.csv"))
    ap.add_argument("--bands", default=os.path.join(SRC, "sec_list.csv"))
    ap.add_argument("--indices", help="optional CSV of symbol,index rows")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    rows, c = build(a.equity, a.bands, a.indices)
    if not a.dry_run:
        write(rows, a.out)
    print("Read %s and %s." % (os.path.basename(a.equity), os.path.basename(a.bands)))
    print("  %-26s %5d" % ("cash symbols", c["rows"]))
    print("  %-26s %5d  (scanner can judge a circuit)" % ("with a price band", c["banded"]))
    print("  %-26s %5d  (F&O segment: dynamic range, no fixed band)"
          % ("no band published", c["no_band"]))
    if c["unlisted_band"]:
        print("  %-26s %5d  (not in the band file at all)" % ("band unknown", c["unlisted_band"]))
    print("  %-26s %5d  (BE/BZ: delivery only, NO intraday)"
          % ("trade-for-trade", c["trade_for_trade"]))
    print("  %-26s %5d  (GSM flagged)" % ("under surveillance", c["surveillance"]))
    print("  %-26s %5d  (EQ)" % ("intraday tradable", c["rows"] - c["trade_for_trade"]))
    with_sector = sum(1 for r in rows if r["sector"])
    print("  %-26s %5d  of %d. The sector is GUESSED from the company name and"
          % ("sector guessed", with_sector, c["rows"]))
    print("  %-26s        misses most large caps (Tata Consultancy Services has no"
          % "")
    print("  %-26s        sector word in it). Supply a real classification if you"
          % "")
    print("  %-26s        intend to use --sector." % "")
    if not a.indices:
        print("\nNIFTY 50 and NIFTY 500 membership is not in NSE's files and stays empty.")
        print("Supply it with --indices CSV (symbol,index) from niftyindices.com.")
    if not a.dry_run:
        print("\nWrote %s." % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
