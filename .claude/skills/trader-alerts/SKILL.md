---
name: trader-alerts
description: >-
  Price Alerts for Indian cash equity intraday. Rings when a price, a stop,
  the opening bell or the broker square-off hits. Turns the trade plan into
  FOUR alerts in one go: the stop, the target, the flat-by time, and a
  square-off warning before your broker closes the position itself around
  15:20. Also above / below / stop / target levels, percent moves from the
  previous close, any IST time of day, and the 09:15 opening bell, each
  ringing once so you are not spammed. Knows NSE hours and the holiday
  calendar from config/india_market.py, including the Muhurat session, works
  out IST itself with no timezone files, fetches quotes through a swappable
  data adapter one symbol at a time, and writes trader/prices.json in rupees
  for sizing and the risk check. Use when the user says "alert me when", "tell
  me if RELIANCE hits", "watch my stop", "watch my trade", "remind me to close
  by 15:10", "square off", "is the market open", "when does the market open",
  "get me a price", or right after /trader-exit writes a plan.
argument-hint: "[symbol] [price]"
---

# trader-alerts

Price Alerts is a clock and a tripwire. It does not trade and it does not
decide; it tells you a level came. Every number comes from `alerts.py`.
An alert is information, not an order. Day trading loses most people money.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock                     # open or closed, and when next
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote RELIANCE TCS
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan            # the four plan alerts
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol TCS --above 4050
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check                      # rings what hit, once each
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
```

## The alert types

| type | rings when |
| --- | --- |
| `above` / `below` | the price crossed your level |
| `stop` / `target` | the same, labelled from the plan so the message says which |
| `pct_move` | the price moved this percent or more from the previous close |
| `time` | a plain IST time of day |
| `opening_bell` | NSE opened today, at 09:15 |
| `square_off` | your broker's intraday square-off is near |

Each rings once. A `pct_move` rings at most once a day.

## The square-off alert, which has no US equivalent

A US day trade only had to beat the closing bell. In India your broker closes
open MIS positions itself, around 15:20, at whatever price is there. That is
not an exit you chose, so `add --from-plan` sets a fourth alert that rings
ten minutes before it (configurable with `minutes_before`), naming the symbol
and the time, so you can get out on your own terms.

`add --from-plan` therefore writes four rules, not three: the stop, the
target, the flat-by time from the plan, and this one.

## Hours and the calendar

`clock` reports the NSE session (09:15 to 15:30 IST), whether today is a
trading day, and when the next open is. Weekends and holidays come from
`config/data/nse_holidays.csv`, and a Muhurat day reports its own evening
window rather than the usual timetable. IST is a fixed +05:30, so there are
no timezone files and no daylight-saving edge cases.

The shipped holiday list is unverified and says so until you replace it from
NSE's circular.

## Quotes

Quotes come through `config/datasource.py`, one symbol at a time, so Yahoo,
a saved payload or a broker feed all work the same way. The US version had
Yahoo and Stooq wired in directly, and Stooq's `.us` suffix is US-only.

Each check writes the last price per symbol to `trader/prices.json` in
rupees, which `/trader-budget` and `/trader-risk` read so they size and check
against a fresh price rather than a stale plan.

Free delayed data is unofficial and can lag. Your own SL-M stop leg at the
broker is what actually stops you out, not this.

## Steps

1. `/trader-exit` writes a plan, then `add --from-plan`.
2. Run `check` every few minutes while the trade is open (a scheduled task
   or `/loop`).
3. On the stop or target alert, check the broker: the two legs are separate
   orders and are not linked, so if one filled, cancel the other.
4. On the square-off alert, be flat. `/trader-broker flatten` builds the
   ticket; it still needs `/trader-risk` and your yes.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
