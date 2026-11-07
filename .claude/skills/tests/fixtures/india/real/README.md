# Real NSE captures (not synthetic)

Every file in this folder is a raw Yahoo Finance chart payload, gzipped,
captured on 2026-11-07 from `query2.finance.yahoo.com/v8/finance/chart/`.
These are real traded prices, kept only so the tests can prove the tools
handle the shape of real data. They are a small sample for testing, not a
dataset, and Yahoo's feed is unofficial and delayed.

Unlike the folder above, nothing here carries `"synthetic": true`, and a test
asserts that too.

| file | request | what the real feed showed |
| --- | --- | --- |
| `RELIANCE-5m.json.gz` | `RELIANCE.NS?range=60d&interval=5m` | 60 sessions, 4,500 grid slots, 109 null bars. **72 to 74 non-null bars a day, never 75**: the 15:20 bar is null on every session, 15:15 and 15:25 often. 2026-10-15 is also missing 11:45 and 11:50 (a mid-day gap) |
| `TCS-1m.json.gz` | `TCS.NS?range=1d&interval=1m` | 361 of 375 minute bars, 09:15 to 15:29 |
| `NIFTY50-5m.json.gz` | `^NSEI?range=5d&interval=5m` | 75 bars a day plus a 76th at 15:30 on the latest day (the closing print) |
| `INDIAVIX-5m.json.gz` | `^INDIAVIX?range=5d&interval=5m` | the same shape as NIFTY50 |

Source payload SHA-256 prefixes, before gzip: RELIANCE `dabb0c8fbfcc5dac`,
TCS `6aa62e7df79faada`, NSEI `da274e3c8be31923`, INDIAVIX `4844f574d456720f`.

## What they changed

The synthetic fixtures have 75 bars a session, so a rule that demanded all 75
passed every test and found **zero** real sessions. That rule now asks for
every bar from the open up to the flat-by time (`SESSION_REQUIRED_UNTIL` in
`config/india_market.py`, 72 bars at 5m) and lets the tail be missing. On this
RELIANCE capture that keeps 59 of 60 sessions and drops only 2026-10-15.

## Refreshing

```bash
curl -s -A 'Mozilla/5.0' \
  'https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=60d&interval=5m' \
  | gzip -9 > RELIANCE-5m.json.gz
```

Tests that pin a count or a date from these files will need updating after a
refresh; that is the point of them.
