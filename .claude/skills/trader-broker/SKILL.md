---
name: trader-broker
description: >-
  Order Executor for Indian cash equity intraday. Shows each order as a plain
  ticket and sends nothing without your explicit yes, paper trading by
  default. Because Kite has no bracket product, a day trade is THREE orders,
  the entry then an SL-M stop then a LIMIT target, and the ticket spells out
  that the stop and the target are not linked, so cancelling the survivor is
  your job. Prints the exact place_order arguments per leg, records your yes
  word for word, and only then does Claude's own Allow once card ask again.
  Builds the square-off ticket that flattens an open intraday position before
  your broker does it for you around 15:20, records fills into
  trader/portfolio.json, and logs each trade's loss so trader-risk can hold
  the daily limit. Covers Zerodha Kite Connect setup with keys in environment
  variables, and the field mapping for Upstox, Angel One and Dhan. Use when
  the user says "place the order", "buy it", "execute", "send it to my
  broker", "close my position", "square off", "flatten", "get me out",
  "connect Kite", "set up paper trading", or after /trader-risk passes.
argument-hint: "[order number]"
---

# trader-broker

The Order Executor is the only skill in this pack that can lead to a real
order, and it cannot do so on its own. It prints a ticket, waits for your
typed yes for that one order, and then Claude's own permission card asks
again. Nothing here is advice, and day trading loses most people money.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show                     # every ticket, READY or not
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show --index 1 --json
python3 "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "yes"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --status placed --broker-id 2510...
python3 "${CLAUDE_SKILL_DIR}/ticket.py" flatten --symbol TCS     # the square-off ticket
python3 "${CLAUDE_SKILL_DIR}/ticket.py" fill --index 1 --price 4026.50
```

## Three gates, none skippable

1. `/trader-risk` recorded a PASS for THAT exact order (same fingerprint)
   today. Change a price or a quantity and it must pass again.
2. You type `yes` for that one order in the chat. A batch word, a hedged
   reply or a yes aimed at a different order does not count.
3. Claude's own Allow once card. Set your broker's order tool to "Needs
   approval", never "Always allow".

## Three legs, not a bracket

Alpaca takes one bracket order carrying its own stop and target. Kite
withdrew Bracket Orders, so the same trade is three separate orders:

| leg | what | when |
| --- | --- | --- |
| entry | MARKET or LIMIT, BUY, product MIS | now |
| stop | SL-M with a trigger price, SELL | only after the entry fills |
| target | LIMIT with a price, SELL | only after the entry fills |

The stop and the target are NOT linked. If the target fills, the stop is
still live, and if it then triggers you are short a stock you no longer own.
Cancel the survivor yourself. Every ticket repeats this, because it is the
sharpest difference from the US pack and the easiest way to lose money by
accident.

## Square-off

Your broker closes open intraday positions itself, around 15:20, at whatever
price is there. `flatten` builds the market SELL that gets you out first, on
your terms, and warns you to cancel any open stop or target leg before
sending it, since those legs still hold the shares.

## Connecting a broker

Zerodha Kite Connect is the default. Create an app at developers.kite.trade
and export the keys:

```bash
export KITE_API_KEY=...
export KITE_ACCESS_TOKEN=...     # regenerated daily
```

Keys live in environment variables, never in the chat, never in a file in
this repo. Upstox, Angel One and Dhan use the same ticket with different
field names; `config/brokers.py` holds the mapping, each row marked VERIFY
against that broker's current API docs.

Paper trading is the default (`"paper": true`). Going live is your own
decision, made in your own config, and the ticket says LIVE ACCOUNT, REAL
MONEY when it is.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders from trader-budget |
| `trader/state/risk.json` | the verdicts, matched by fingerprint |
| `trader/state/broker.json` | your yes, the result, and the fills |
| `trader/portfolio.json` | updated on a recorded fill: cash, shares, cost-basis lots |

## Steps

1. `/trader-risk` first, always.
2. `show` the ticket. Read the max loss, which includes charges, and the
   not-linked warning.
3. Type `yes` for that one order if you want it. `no` skips it.
4. Place the entry. Once it fills, place the stop and the target, and
   remember that cancelling the survivor is yours to do.
5. `result` records what happened; `fill` updates the portfolio.
6. Be flat before the square-off.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
