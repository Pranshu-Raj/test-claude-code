---
name: trader-risk
description: >-
  Risk Manager for Indian cash equity intraday. Checks every order against
  your limits and stops anything over them before it can reach the broker.
  For a day trade: a stop leg is required, the loss at the stop must fit your
  max_loss_per_trade_inr INCLUDING brokerage, STT, exchange and SEBI charges,
  stamp duty and GST, reward:risk must meet your minimum, the day's total risk
  must fit max_daily_loss_inr, no more than your trades per day, no new
  entries outside the session window or past your broker's square-off, and
  nothing at or near a daily price band. It also keeps the basics: blocked
  symbols, order size, position cap, cash buffer, F&O instruments, malformed
  legs, duplicates and a daily order limit. An intraday short is allowed and
  must be squared off the same session; an overnight cash short is refused
  outright. Getting out is never blocked by a day limit. Prints PASS or BLOCK
  with the reason and records the verdicts so trader-broker only shows an
  order that passed. Use when the user says "check my order", "is this safe",
  "risk check", "does this break my rules", "can I take this trade", "how much
  have I risked today", or before /trader-broker, always.
argument-hint: "[path to orders.json]"
---

# trader-risk

The Risk Manager is the gate. Nothing reaches a broker without passing it,
and it says no with a reason and a number rather than a shrug. Every figure
comes from `check.py`, not from the model reading this. A PASS is a rules
check, not a recommendation, and day trading loses most people money.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py"                      # trader/orders.json
python3 "${CLAUDE_SKILL_DIR}/check.py" --json
python3 "${CLAUDE_SKILL_DIR}/check.py" --no-write            # check without recording verdicts
```

## What blocks an entry

| rule | what it catches |
| --- | --- |
| format | no symbol, a quantity that is not whole shares, a LIMIT leg with no price, an SL or SL-M leg with no trigger price, an unknown type, product or validity, a missing stop leg |
| asset | an F&O instrument while `allow_options` is false |
| short | an overnight cash short (impossible), or an intraday short while `allow_short` is false |
| blocked | a symbol on your `blocked` list |
| size | under `min_order_inr`, over `max_order_inr` |
| position | the stock would pass `max_position_pct` of the account |
| cash | the buy would dip into your cash buffer, or need margin |
| duplicate | the same symbol and side twice in a batch, or already placed today |
| daily limit | `max_orders_per_day` already placed |
| stop | no stop while `require_stop` is true, or a stop on the wrong side of the entry |
| loss | the loss at the stop, CHARGES INCLUDED, over `max_loss_per_trade_inr` |
| reward:risk | under `min_reward_risk` |
| daily loss | today's risk plus this trade over `max_daily_loss_inr` |
| trades | `max_trades_per_day` entries already placed |
| window | outside 09:15 to 15:30 IST, outside your entry window, or past the broker square-off |

Exits are different. Getting out must always be possible, so an exit is
checked for format, asset and short sales only; every other rule becomes a
warning.

## The loss rule counts charges

This is the rule most changed from the US pack, which has no cost model at
all. In India the charges on a round trip are asymmetric and real, so the
limit is applied to what you actually lose.

A worked example. 54 shares of TCS, entry 4026.50, stop 4008.00:

- Gross: 54 x 18.50 = 999.00, which fits a 1,000 limit.
- Real: 1,122.56 once 123.56 of charges land. BLOCKED, and the message names
  the charges.

Set `"max_loss_includes_costs": false` to apply the limit to the gross figure
instead. The same applies to `max_daily_loss_inr`.

## Shorting, which works differently from the US

There is no overnight short in the Indian cash segment. So:

- An intraday (MIS) short beyond what you hold is allowed when
  `allow_short` is true, with a warning that it must be bought back before
  your broker squares it off.
- A delivery (CNC) short beyond what you hold is refused outright, with a
  pointer to MIS or SLB. It is not a config choice; it cannot settle.

Cash equity settles T+1.

## The window and the square-off

No new entry outside the session, outside `no_entries_before` to
`no_entries_after`, or past `BROKER_SQUARE_OFF`. A `no_entries_after` later
than the square-off is pulled back with a warning: an entry that cannot be
managed before the broker closes it is not a trade you control. There is no
pattern-day-trader rule in India, so `pdt_guard` is gone.

## Verdicts

PASS or BLOCK per order, with every reason listed. The verdicts go to
`trader/state/risk.json` with a fingerprint of each order, so
`/trader-broker` will only show an order as ready if THAT exact order passed
today. Change a price or a quantity and it must pass again.

## Steps

1. Run it on every `trader/orders.json` before `/trader-broker`, always.
2. Read the reasons. A BLOCK is the system working, not an obstacle.
3. Fix the order (usually with `/trader-exit` or `/trader-budget`) or fix
   your rules in `trader/config.json`, then check again.
4. A PASS still means nothing is placed. `/trader-broker` asks you yes or no.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
