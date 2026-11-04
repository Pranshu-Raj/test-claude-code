# Official NSE source files

These are NSE's own published files, kept here so `import_nse.py` can be
re-run and the generated universe can be diffed against a previous build.
They are inputs, not hand-edited data.

| file | what | where it comes from |
| --- | --- | --- |
| `EQUITY_L.csv` | every security in the NSE cash market: symbol, name, series, listing date, ISIN, face value | NSE, "Securities available for trading" (`nsearchives.nseindia.com/content/equities/EQUITY_L.csv`) |
| `sec_list.csv` | the daily price band per symbol, plus GSM surveillance remarks | NSE, "Price Bands" / securities list |

Supplied by the repo owner on 2026-11-04.

## Refreshing them

Download both from NSE, drop them here replacing these, then:

```bash
python3 .claude/skills/config/import_nse.py
```

That rewrites `../nse_universe.csv`. Read the summary it prints: it reports
how many symbols moved series or changed band, which is what you want to see
before trusting a refreshed universe.

## What these files do NOT contain

Index membership. NIFTY 50 and NIFTY 500 constituents come from
niftyindices.com and are still unset. F&O eligibility is DERIVED here from
the band column, because NSE publishes no fixed price band for securities in
the F&O segment, so `Band == "No Band"` is a reliable proxy. It is marked as
derived, not official.
