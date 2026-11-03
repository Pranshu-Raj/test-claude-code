---
name: trader-entry
description: >-
  Entry Timer for Indian cash equity intraday. Watches a setup bar by bar and
  says the moment to get in. Reads 1-minute or 5-minute bars through a
  swappable data adapter, builds the opening range from 09:15 IST (the first
  15 minutes by default, so the 09:15, 09:20 and 09:25 candles) and calls the
  entry on the first COMPLETED bar that CLOSES above the range high, or above
  a level you give it, with an optional volume check. Refuses to trigger on a
  stock sitting at or near its daily price band, because a breakout into a
  circuit cannot run. Holds new entries inside the session window (09:20 to
  14:45) so a trade has room before the broker squares it off. Says waiting
  and how far, triggered with the time and the price, blocked at a circuit,
  expired, or not open yet, and writes trader/entry.json for /trader-exit.
  Long side only. It never places an order. Use when the user says "tell me
  when to get in", "watch RELIANCE", "has it broken out", "did it break the
  opening range", "entry signal", "time my entry", "check my watchlist", or
  after /trader-scanner, before /trader-exit.
argument-hint: "[SYMBOL] [--setup orb|level] [--interval 5m|1m]"
---

# trader-entry

The Entry Timer answers one question: has this setup actually triggered yet.
Not "is it close", not "does it look good". A bar either closed above the
level or it did not. Everything here is computed by `entry.py`, not by the
model reading this. Day trading loses most people money.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --setup orb
python3 "${CLAUDE_SKILL_DIR}/entry.py" check TCS --level 4020 --setup level --band-pct 10
python3 "${CLAUDE_SKILL_DIR}/entry.py" watch RELIANCE --every 30 --until 14:45
python3 "${CLAUDE_SKILL_DIR}/entry.py" watchlist                  # trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/entry.py" check TCS --adapter offline --data DIR --now "2026-11-02 09:51"
```

Other flags: `--or-minutes 15`, `--interval 5m|1m`, `--min-volume-x 1.5`,
`--write`, `--json`, `--now "2026-11-02 09:51"` (IST), `--band-pct`.

## The rule

| status | what it means |
| --- | --- |
| `triggered` | a completed bar CLOSED above the level. The time, the price and the bar index are reported |
| `waiting` | no bar has closed above it yet, and how far away the last close is |
| `blocked` | it closed above the level, but the stock is at or near its daily circuit, so the move cannot continue |
| `expired` | no trigger by the last entry time, so no entry on this setup today |
| `no-data` | the session has no usable bars yet |

- **The opening range starts at 09:15**, which is when NSE opens. A
  15-minute range is therefore the 09:15, 09:20 and 09:25 bars on 5-minute data. The
  range comes from the session in `config/india_market.py`, so it follows the
  calendar, including a shorter special session.
- **A close, not a wick.** A bar that pokes through the level and closes back
  under it is not a trigger. Only completed bars on the interval grid count,
  and the forming bar is never used.
- **Pre-open is not the session.** The 09:00 to 09:15 call auction is not
  traded continuously and no bar from it feeds the range.

## Circuit limits, which have no US equivalent

A stock at its daily price band cannot trade through it. A breakout into the
upper circuit has nowhere to go, and a stop under the lower circuit cannot be
reached. So given a previous close and a band percent, this skill reports
`blocked` instead of `triggered` and names the band.

Pass `--band-pct 10`, or put `band_pct` in the config, or let
`/trader-scanner` carry it in `trader/watchlist.json`. Without a band it
cannot know, so it triggers and says nothing about circuits: supply the band
if the stock is anywhere near one. Securities in the F&O segment have no
fixed band but do have a dynamic operating range.

## The entry window

New entries are held to `no_entries_before` (09:20) and `no_entries_after`
(14:45) from the shared config. The early bound lets the opening auction
settle; the late bound leaves room to work before your broker squares the
position off around 15:20. An entry cutoff is never later than that
square-off, whatever the config says.

## What it reads and writes

| file | what |
| --- | --- |
| a data adapter | bars for one symbol: yahoo by default, offline for saved payloads, or a broker |
| `trader/watchlist.json` | from trader-scanner, for the `watchlist` command |
| `trader/config.json` | `no_entries_before`, `no_entries_after`, `band_pct` |
| `trader/entry.json` | with `--write`: symbol, status, trigger time and price, bar index, the opening range, the circuit state, the data source. /trader-exit reads it |

## Steps

1. Run `check` once, or `watch` to follow it bar by bar until it triggers.
2. On `triggered`, go straight to `/trader-exit` and set the stop and target
   BEFORE you buy. A trigger with no plan is not a trade.
3. On `blocked`, leave it. A stock pinned at a circuit is not an entry.
4. On `expired`, the setup did not fire. There is no late version of it: a
   later entry is a different trade with a different stop and different odds.
5. Nothing here places an order, and no order happens without your explicit
   yes at `/trader-broker`.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
