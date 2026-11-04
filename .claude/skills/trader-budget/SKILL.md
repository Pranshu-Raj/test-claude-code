---
name: trader-budget
description: >-
  Budget Manager for Indian cash equity intraday. Sizes every position to your
  budget and your max loss per trade, in rupees and in whole shares, NET of
  Indian charges. Reads the trade plan from trader-exit, finds the shares that
  fit inside four limits (max loss per trade, position cap, max order, cash
  after your buffer), says which limit decided it, and writes the proposed
  orders to trader/orders.json. The max-loss limit is solved against the loss
  you actually take: brokerage, STT on the sell leg, stamp duty on the buy
  leg, exchange and SEBI charges and GST are all counted, so a 1,000 max loss
  with an 18.50 stop gives 47 shares, not the 54 the gross arithmetic would
  suggest. Because Kite has no bracket product the order is three legs, entry
  then stop then target, and they are not linked. A stop too far away for your
  max loss is a plain refusal, not a smaller order. Nothing is placed. Use
  when the user says "how many shares", "size this trade", "how much should I
  buy", "I have 2 lakh", "I only want to risk 1000 a trade", "set my rules",
  "keep 10% in cash", or after /trader-exit writes a plan.
argument-hint: "[budget in rupees]"
---

# trader-budget

The Budget Manager turns a plan into a number of shares. It does the
arithmetic so nobody has to do it in their head while a stock is moving, and
every figure here comes from `size.py`, not from the model reading this.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py"                       # trader/plan.json -> the sizing
python3 "${CLAUDE_SKILL_DIR}/size.py" --write               # -> trader/orders.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --budget 200000       # override the config budget
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit 4027 --json   # a limit entry, as JSON
```

## The four limits

The smallest wins, and the report names it. Whole shares only: Indian cash
equity has no fractional quantity.

| limit | how |
| --- | --- |
| max loss | `max_loss_per_trade_inr` against the risk a share, then cut until the loss at the stop still fits WITH charges |
| position cap | `max_position_pct` of the budget, over the entry |
| max order | `max_order_inr` over the entry |
| cash | the budget (or `trader/portfolio.json` cash) minus the cash buffer, over the entry |

## Costs are part of the size, not a footnote

This is the biggest change from the US pack, which models no costs at all
because US retail equity is commission-free. Indian intraday charges are
asymmetric and, against a tight stop, large in R terms.

A worked example. Entry 4026.50, stop 4008.00, so 18.50 a share at risk, with
a 1,000 max loss per trade:

- Gross arithmetic: 1000 / 18.50 = 54 shares.
- At 54 shares the loss at the stop is 1,122, which is 12% past your limit.
- So the size comes down to 47 shares, where the loss is 983 and your limit
  holds.

`"max_loss_includes_costs": true` is the default and does this. Set it to
false to size the old gross way; the report then says the number is gross and
names the real loss, so it cannot mislead you quietly.

The report also prints the charge breakdown (brokerage, STT, exchange
transaction, SEBI turnover, stamp duty, GST), the net result at the target,
and the note that intraday equity gains are speculative business income.

## Three legs, not a bracket

Alpaca takes one bracket order with its own stop and target legs. Kite
withdrew Bracket Orders, so the same intent is three separate orders:

1. the entry, MARKET or LIMIT, product MIS
2. the stop, SL-M with a trigger price, SELL
3. the target, LIMIT, SELL

Legs 2 and 3 can only go in once the entry has filled, and they are NOT
linked: if the target fills, the stop is still live and must be cancelled or
you end up short. Every ticket repeats that warning, because there is no US
equivalent of this footgun.

## Config

| key | what |
| --- | --- |
| `budget` | the account size in rupees |
| `max_loss_per_trade_inr` | the most one trade may lose, charges included |
| `max_loss_includes_costs` | true by default; false sizes on the gross risk |
| `max_position_pct`, `max_order_inr`, `min_order_inr` | the other three limits |
| `cash_buffer_pct` | kept back, not deployed |
| `exchange`, `product`, `broker` | NSE or BSE, MIS or CNC, and which broker's fields to emit |

Prices are rounded to the NSE tick from `config/india_market.py`, so every
price in an order is one you can actually send.

## Refusals

A refusal is not a smaller order. If one share risks more than your max loss,
there is no trade: skip it, or plan a closer stop with `/trader-exit`. The
same goes for a reward:risk under your minimum, or an order under your
minimum order value. The reason is always printed with the numbers.

## Steps

1. Run it after `/trader-exit` writes a plan.
2. Read which limit bound the size. If it is the position cap or cash, your
   budget is the constraint, not the trade.
3. `--write` hands `trader/orders.json` to `/trader-risk`, which checks it
   against your limits, and only then `/trader-broker` shows you a ticket and
   waits for your explicit yes.
4. Nothing here places an order.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
