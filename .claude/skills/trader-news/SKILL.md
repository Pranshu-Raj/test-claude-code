---
name: trader-news
description: >-
  News Monitor for Indian equities. Tracks what is new on the stocks you
  watch and flags what moves them: headlines from Indian business RSS
  (Economic Times, Business Standard, Moneycontrol, Livemint), NSE and BSE
  corporate announcements per symbol (results, board meetings, dividends,
  order wins, credit ratings, promoter pledges, QIPs, open offers, SEBI
  notices), RBI policy dates from a file you maintain, and how far each stock
  is from the previous close. Shows only what is NEW since the last check,
  newest first, in IST, tagged (results, analyst, deal, order_win, pledge,
  rbi, macro, halt...) and never repeated. Matches bare NSE symbols in
  headlines, which is how Indian coverage names stocks. Use when the user says
  "any news on RELIANCE", "why is this moving", "what's the news on my
  watchlist", "morning briefing", "pre-open news", "monitor the news", "did
  anything break", "check the announcements", "is there an RBI policy today",
  or after /trader-scanner finds setups.
argument-hint: "[symbols]"
---

# trader-news

The News Monitor answers "what changed and does it touch my list". It shows
each item once, newest first, with a tag and a link. News you can read is
already in the price; this is for context and for avoiding a trade into a
result. Day trading loses most people money.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers RELIANCE,TCS --hours 12
python3 "${CLAUDE_SKILL_DIR}/news.py"                       # from trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/news.py" --offline DIR --now "2026-11-02T04:00:00Z"
python3 "${CLAUDE_SKILL_DIR}/news.py" --no-announcements     # headlines only
```

## Sources

| source | what |
| --- | --- |
| Indian business RSS | market headlines; symbols are attached by mention |
| NSE corporate announcements | per symbol: results, board, dividend, pledge, rating, order win, QIP, open offer, SEBI notice |
| BSE announcements | the same, for a `.BO` listing |
| `config/data/rbi_dates.csv` | RBI policy dates, flagged when today or tomorrow |
| a price adapter | distance from the previous close, to flag what is moving |

The US version read Yahoo's per-ticker RSS and SEC EDGAR 8-K filings keyed by
CIK. India has no CIK: NSE and BSE key announcements by the trading symbol,
which is already the canonical form here, so that whole lookup is gone. 8-K
item numbers become announcement subjects, matched on text.

## Honest status

**The live fetching is unverified.** The NSE and BSE endpoints follow their
public JSON, but this port was written in an environment that cannot reach
them, so no live call has been made. NSE in particular needs a browser user
agent and a session cookie and rate-limits hard, and both exchanges change
paths without notice. Expect this to be the most fragile skill in the pack.

What IS tested, offline and thoroughly: the RSS and announcement parsing, the
IST timestamp handling (including the exchanges' `02-Nov-2026 09:05:00`
format), the subject-to-kind mapping, tagging, symbol matching on bare
symbols, deduplication and the show-once state.

If news latency matters to your trading, use a broker feed or a paid source.

## Tags

`results`, `analyst`, `legal`, `deal`, `capital`, `people`, `order_win`,
`risk`, `halt`, `regulatory`, `rbi`, `macro`, `index`, plus the announcement
kind itself and `important` for the subjects that usually move a price
(results, order wins, rating actions, pledges, open offers, SEBI litigation,
fundraises, senior departures).

A `!` in the left margin marks an important item.

## Steps

1. Run it on the scanner's watchlist before you trade anything on it.
2. A stock reporting results today is a different trade: an intraday
   breakout through a result is a coin flip on the number, not a setup.
3. An RBI policy day moves banks and the index. Note it before sizing.
4. Open the story before acting. A headline is a lead, not a fact.
5. Nothing here places an order.

## Safety

Education and tooling, not financial advice. This skill only reads and
reports. Nothing places an order except `/trader-broker`, after
`/trader-risk` passes and the user types an explicit yes for that order.
