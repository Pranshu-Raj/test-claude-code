---
name: trader-scanner
description: >-
  Chart Scanner for the NSE universe. Scans for setups on daily closes before
  the open (20-day closing-high breakout, bull flag, reclaimed 20-day average,
  oversold, oversold bounce, gap up or down) and for opening-range breakouts
  on 5-minute bars from 09:15 IST. Builds its universe from
  config/data/nse_universe.csv, which you maintain: index membership
  (nifty50, nifty500, fno), sector, and the daily price band per symbol.
  SKIPS any stock at or near its circuit, because a breakout into a price band
  cannot run, and reports symbols whose band you have not filled in as
  band-unknown rather than treating them as clear. Filters on price, volume,
  sector and gap. Writes trader/watchlist.json for trader-news and
  trader-entry. Use when the user says "scan the market", "find me setups",
  "what is breaking out", "opening range breakouts", "scan NIFTY 50", "scan
  for bull flags", "oversold stocks", "pre-open scan", "gap up", "build my
  watchlist", or at the start of a trading day before /trader-news.
argument-hint: "[universe | daily | orb] [--at HH:MM]"
---

# trader-scanner

The Chart Scanner turns a universe into a short list. A scan is a list of
charts that match a rule, not a list of stocks to buy. Every number comes from
`scan.py`. Day trading loses most people money.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe --index nifty50
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe --sector auto
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --gap 2
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --setup near_high_20d --near 1.5
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --at 10:15
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --symbols TCS,RELIANCE --adapter offline --data DIR
```

## The universe is a file you own

The US version downloaded Nasdaq Trader's two symbol directories and filtered
out ETFs, units, warrants and test issues with name patterns. NSE publishes no
equivalent single free file, so the universe lives in
`config/data/nse_universe.csv` with one row per symbol:

| column | what |
| --- | --- |
| `symbol` | the NSE trading symbol, the canonical form everywhere in this pack |
| `sector` | free text, used by `--sector` |
| `indices` | pipe separated: `nifty50`, `nifty500`, `fno` |
| `band_pct` | the daily price band, if you know it. Blank means the circuit filter cannot judge that symbol |

**Every shipped row is marked unverified**, because it was written from memory
rather than downloaded. Replace the file from niftyindices.com and NSE's
`EQUITY_L.csv` before you trade off a scan; the scanner prints a warning until
you do.

## Circuit limits, which have no US equivalent

A stock at or near its daily price band cannot make the move a breakout needs:
into the upper circuit there is nowhere to go, and under the lower one a stop
cannot be reached. So the scanner skips those and counts them separately.

It can only do that where you have filled in `band_pct`. Anything else is
reported as band-unknown and scanned anyway, never silently assumed clear.
F&O names carry no fixed band (they have a dynamic operating range instead),
so those rows are blank by design.

## The setups

| setup | rule |
| --- | --- |
| `breakout_20d` | closed above its highest close of the last 20 sessions |
| `near_high_20d` | closed within `--near` percent (default 2) UNDER that high, not yet through it: the brink, not the breakout. `--setup near_high_20d` keeps only those |
| `bull_flag` | a strong run, then a shallow pause that holds |
| `reclaim_ma20` | closed under the 20-day average, then back above it |
| `oversold` | RSI 14 (Wilder) under 30 |
| `oversold_bounce` | RSI was under 30 yesterday and today closed up |
| `gap_up` / `gap_down` | opened clear of the previous close by `--gap` percent |
| `orb` | closed above the 09:15 opening range, on 5-minute bars |

The opening range starts at 09:15 because that is when NSE opens; a
15-minute range is the 09:15, 09:20 and 09:25 bars. The 09:00 to 09:15
pre-open auction is not continuous trading and never feeds a range.

## Data

Bars come through `config/datasource.py`, one symbol at a time, so any
provider works. The US version used Yahoo's spark endpoint in batches of 20,
which only Yahoo offers; going one at a time is slower but keeps providers
interchangeable, and it returns whole sessions so there is no second
confirmation pass for highs and lows.

Prices and the price filter are in rupees.

## Steps

1. `universe` once (and again after an index review) to build
   `trader/universe.txt`.
2. `daily` before the open, or `orb --at HH:MM` during the session.
3. Read the circuit line. A stock skipped at its band is not a missed trade.
4. Pick ONE chart. Then `/trader-news` on it, then `/trader-entry`.
5. A scan is not a trade. Nothing here places an order.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
