---
name: trader-timemachine
description: >-
  Time Machine for Indian cash equity intraday. Replays the rest of the
  session before you risk a rupee: 10,000 simulated sessions plus every past
  time the setup fired. Takes the plan from trader-exit, rebuilds the day from
  the stock's own 5-minute bars over the last 60 NSE sessions (75 bars a
  session, 09:15 to 15:30 IST), and re-runs the same rule on every past
  session. Reports the odds of target first, stop first, or neither by the
  flat-by time, where the price sits at the close, the average R, the history,
  and one honest verdict. Every outcome is also given NET of Indian charges,
  which matters more than it sounds: on a tight stop charges can be a third of
  one R, turning an average of +0.167R gross into -0.216R net, and the verdict
  says so plainly when that happens. Use when the user says "run the time
  machine", "replay this trade", "what are the odds", "simulate this trade",
  "monte carlo", "backtest this setup", "how often does this hit the target",
  "is this a coin flip", or after /trader-exit writes a plan, before
  /trader-budget.
argument-hint: "[path to plan.json, or SYMBOL]"
---

# trader-timemachine

The Time Machine answers "what usually happens from here" with arithmetic
instead of a feeling. Every number comes from `timemachine.py`, not from the
model reading this. Day trading loses most people money, and a simulation
does not change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run                      # trader/plan.json, 10,000 replays
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --seed 7 --replays 20000 --json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --adapter offline --data DIR
```

## How it works

1. **60 days of 5-minute bars**, through the data adapter.
2. **Full sessions only.** An NSE session is 375 minutes, so a complete
   5-minute day is 75 bars. A half day, a data gap or today-so-far is dropped,
   because a partial session would bias every draw. The US pack used 78 bars
   for a 09:30 to 16:00 day; that number is now computed from the shared
   config, not written down.
3. **A pool per time of day.** The 10:05 bar is drawn from what this stock
   actually did around 10:05, not from an average of the whole day.
4. **Replay** the rest of the session from the entry bar, 10,000 times, and
   see which comes first: the target, the stop, or the flat-by time.
5. **History** re-runs the same rule on each of those real sessions, so the
   simulated odds sit next to what actually happened.

A path that reaches neither level is closed at the plan's flat-by time, which
trader-exit already holds before the broker's square-off. There is no
overnight hold to model.

## Charges change the answer

This is the part the US version cannot show, because it models no costs. Each
replayed path pays one round trip: brokerage, STT on the sell leg, stamp duty
on the buy leg, exchange and SEBI charges, and GST. Expressed in R that is
`cost_as_r`, and net R is gross R minus it.

A worked example from the fixtures: RELIANCE with a 1.65 stop, 2R target.

| | gross | net |
| --- | --- | --- |
| average R | +0.167 | -0.216 |
| at the target | +2.00R | +1.62R |
| at the stop | -1.00R | -1.38R |

Charges are 38 per cent of one R at that stop distance, so a setup that
"leans toward the target" still loses money. When that happens the verdict
says BUT NOT AFTER CHARGES rather than leaving you to notice.

The size used comes from `max_loss_per_trade_inr` in your config, so the
figures appear only when that is set, and they are marked indicative:
`/trader-budget` sizes the trade for real and charges move with the size.

## Reading the verdict

The verdict compares the hit rate the R multiple needs against the hit rate
the replays produced, then checks whether charges flip the sign. Close to
even is reported as close to even. That is the point of looking.

It is a SIMULATION, not a prediction. It is built from this stock's own last
60 sessions. A result, a circuit, a block deal or a policy announcement can
do things those sessions never did. Same plan, same data and same `--seed`
give the same numbers on any machine, so anyone can repeat a run.

## Steps

1. Run it after `/trader-exit` writes a plan, before sizing.
2. Read the net row, not the gross one. The net row is the trade you take.
3. If net average R is negative, the honest move is usually no trade, a wider
   stop, or a higher R target.
4. Then `/trader-budget`, `/trader-risk`, and only then `/trader-broker`.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
