---
name: trader-exit
description: >-
  Exit Planner for Indian cash equity intraday. Sets your stop and your target
  before you enter, so you know when to get out. Reads the entry from
  trader-entry (or a price you give), puts the stop at the opening-range
  midpoint by default (or the range low, your price, a percent, or a multiple
  of the ATR), rounds it DOWN to a tradable NSE tick, and sets the target at
  2R (or 1R, or your price) rounded up to a tick. Forces the flat-by time
  before your broker's intraday square-off, because a broker square-off is not
  an exit plan. Reports the reward:risk net of Indian charges (STT on the sell
  leg, stamp duty on the buy leg, GST on brokerage), which can turn a 2R plan
  into 1.67R. Refuses a stop at or above the entry, a target at or below it, a
  stop inside one tick or too tight to survive the spread, and says why.
  Writes trader/plan.json for the Time Machine, the Budget Manager and Price
  Alerts. Long side only. Use when the user says "where's my stop", "set my
  stop and target", "plan the exit", "when do I get out", "stop loss", "what's
  my R", "risk reward", "square off", or right after /trader-entry triggers,
  before /trader-timemachine.
argument-hint: "[--stop or-mid|or-low|price:X|pct:X|atr:K] [--target 2R|1R|price:X]"
---

# trader-exit

The Exit Planner decides how a trade ends before it starts. The stop is where
the setup has failed. The target is where you take what you came for. Both
are set BEFORE the entry, written down and handed on, so nobody has to decide
anything in the middle of a fast move. A stop is a plan, not a guarantee: a
gap or a fast market can fill past it. Day trading loses most people money,
and a neat plan does not change that.

Every number here is computed by `exit.py`, not by the model reading this.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan                          # trader/entry.json: midpoint stop, 2R target
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop or-mid --target 1R --write
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop atr:1.5           # needs the session's bars
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol TCS --entry 4026.50 --or-high 4020 --or-low 3996
python3 "${CLAUDE_SKILL_DIR}/exit.py" show                          # the current trader/plan.json
```

Other flags: `--flat-by 15:10`, `--json`, `--now "2026-11-02 10:15"` (IST),
`--entry PRICE` with a waiting entry.json to plan ahead, `--setup level`,
`--adapter offline --data DIR` for saved bars, `--date YYYY-MM-DD`.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/entry.json` | the trigger from trader-entry: symbol, entry price, time, bar, opening range |
| `trader/config.json` | `flat_by` (15:15 IST), `min_reward_risk` (1.0: a note here, trader-risk enforces it), `max_loss_per_trade_inr` (enables the net-of-cost figures), `exchange`, `product` |
| `config/india_market.py` | session hours, the NSE tick, the broker square-off time, the holiday calendar |
| a data adapter | only for an `atr:K` stop: the session's bars up to the entry bar |
| `trader/plan.json` | with `--write`: entry, stop and its rule, target and its R, risk a share, reward:risk, flat by, square-off, the cost estimate. trader-timemachine, trader-budget and trader-alerts read it |

## Stops and targets

| stop | where it goes |
| --- | --- |
| `or-mid` | the opening-range midpoint (the default for an opening-range breakout) |
| `or-low` | the opening-range low: wider, so fewer shares for the same max loss |
| `price:X` | your price |
| `pct:X` | X percent under the entry |
| `atr:K` | K times the 14-bar ATR under the entry, on the session's bars up to the entry bar. With fewer than 14 bars by then it averages all of them and says so |

A level setup has no opening range to lean on, so it needs a stop you name.

- **Rounding is to the NSE tick, not the paisa.** Most scrips tick at 0.05,
  so a stop of 4008.03 is not a price you can send. The stop rounds AWAY from
  the entry (down, for a long) to a tradable tick, and the target rounds up,
  so rounding never quietly tightens your stop or shrinks your target. The
  tick comes from `config/india_market.py`, where it is set per price band.
- **Risk and reward.** Risk = entry minus stop. Target = entry + R x risk.
  `2R` by default; `1R`, `1.5R` or `price:X` also work. A worked example: TCS
  with the opening range 3996.00 to 4020.00 and an entry at 4026.50 gives a
  midpoint stop of 4008.00, so 18.50 a share at risk, a 1R target of 4045.00
  and a 2R target of 4063.50.
- **Refused, with the reason.** A stop at or above the entry. A target at or
  below it. A stop inside one tick of the entry. A risk under 0.05% of the
  price: too tight to survive the spread, normal wiggle would stop you out.
  An entry.json that has not triggered (unless you give `--entry`). A short:
  intraday shorts are legal in Indian cash equity but this plans longs only.

## Square-off, which has no US equivalent

Your broker closes open MIS positions itself, around 15:15 to 15:25, at
whatever price is there. That is not an exit you chose, so:

- `flat_by` defaults to 15:15 IST, before `BROKER_SQUARE_OFF` (15:20).
- A `--flat-by` at or after the square-off is refused outright.
- A `flat_by` in your config at or after it is pulled back, with a note.
- An entry at or after the flat-by time is refused: there is no time to hold it.
- Check your own broker's square-off time and set it in the config. They differ.

## Costs change the plan

Indian intraday charges are not symmetric: STT falls on the sell leg, stamp
duty on the buy leg, and GST applies to brokerage plus exchange and SEBI
charges. Against a tight stop they are not noise. With
`max_loss_per_trade_inr` in your config this skill reports the indicative
reward:risk net of charges at the size that max loss implies, plus the
breakeven price. A plan showing 2R gross can be 1.67R net. trader-budget
computes the authoritative figure at the real size, and trader-risk checks
the net loss against your limit.

Intraday equity gains in India are speculative business income, taxed at your
slab rate, not capital gains. That is a note in the output, not tax advice.

## Steps

1. Run it right after `/trader-entry` triggers. Before that, `--entry PRICE`
   plans ahead off a waiting entry.
2. Read the refusal if there is one. It names the rule and the number.
3. `--write` hands `trader/plan.json` to `/trader-timemachine`, then
   `/trader-budget` sizes it, then `/trader-risk` checks it.
4. Never widen a stop after the entry. Moving it further away is how a small
   loss becomes a large one.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
