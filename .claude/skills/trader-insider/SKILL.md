---
name: trader-insider
description: >-
  Insider Disclosures for Indian equities: who inside the company is buying or
  selling, from SEBI's mandatory filings. Reads Regulation 7(2) disclosures
  published by NSE and BSE, where a promoter, director or designated person
  whose trades exceed 10 lakh rupees in a quarter must disclose them and the
  company must pass that to the exchange within two trading days. Turns each
  filing into clean rows (symbol, person, category, acquired or disposed, the
  MODE, shares, rupee value in lakh and crore, holding before and after, trade
  date, disclosure date and the lag in trading days), flags a filing that
  missed the two-day window, flags stocks you hold, and alerts once per new
  disclosure. Replaces the US Congress-trade tracker, because India has no
  STOCK Act equivalent. Use when the user says "insider trades", "what are
  promoters buying", "promoter selling", "SEBI PIT disclosures", "insider
  disclosures for RELIANCE", "did any director sell", "alert me on insider
  filings", or "run the insider skill".
argument-hint: "[list | new] [--symbol SYMBOL]"
---

# trader-insider

Company insiders have to tell the exchange when they trade their own stock.
Those filings are public, and a promoter selling a large stake is a fact
about that company worth knowing before you trade it. Every number here comes
from `insider.py`. Day trading loses most people money.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/insider.py" list --symbol RELIANCE
python3 "${CLAUDE_SKILL_DIR}/insider.py" list --days 7 --min-value 10000000
python3 "${CLAUDE_SKILL_DIR}/insider.py" list --direction disposed --category promoter
python3 "${CLAUDE_SKILL_DIR}/insider.py" new                      # only what is new
python3 "${CLAUDE_SKILL_DIR}/insider.py" list --offline DIR --now "2026-11-02 10:00"
```

## What replaced the Pelosi tracker, and why

The US pack read the House Clerk's Periodic Transaction Reports: the trades
members of Congress must disclose under the STOCK Act. India has no
equivalent. Members of Parliament file no periodic transaction reports, so
there is nothing to port.

The closest real signal is company insiders, under SEBI's Prohibition of
Insider Trading regulations. Arguably it is a better one: a promoter cutting
a stake is a fact about that specific company, not about a legislator's
portfolio.

Phase 1, which is what this does, covers Regulation 7(2) disclosures. Bulk
and block deals and SAST substantial-acquisition filings are a later phase;
the loader is shaped to take them without rework.

## What a disclosure is, and is not

- It is **public**, filed under a legal obligation, and almost always
  **after** the trade. Reading it is not insider trading.
- It is **late by design**: up to two trading days after the company is told,
  which is itself after the trade. Context, not a signal to chase.
- **The mode matters more than the direction.** An ESOP allotment, a pledge
  invocation, an inter-se promoter transfer, a gift and an open-offer tender
  all appear here. A disposal is not always bearish and an acquisition is not
  always bullish, so the mode is kept and shown, and a caveat line is printed
  when it is one of those.

A `!` in the left margin marks a filing you should look at: one in a stock
you hold, or one that missed the two-day window.

## Honest status

**The live fetching is unverified.** The NSE endpoint follows its public JSON
and the fetcher primes a cookie the way NSE requires, but this port was
written where that host is unreachable, so no live call has been made. NSE
rate-limits hard and changes paths.

What is tested offline: the JSON parsing, date formats the exchanges use,
category and mode classification, the acquired-or-disposed reading, the
trading-day lag and the late flag, rupee formatting in lakh and crore, the
filters, and the alert-once state.

## Thresholds

| value | what | verify against |
| --- | --- | --- |
| 10 lakh rupees | the Regulation 7(2) disclosure threshold per quarter | SEBI (Prohibition of Insider Trading) Regulations, Reg 7(2) |
| 2 trading days | the window for the exchange to receive it | the same regulation |

Both live in `insider.py` with a VERIFY comment. Check them; SEBI amends.

## Steps

1. Run `new` on a schedule, or `list --symbol X` before trading a stock.
2. Read the mode before the direction.
3. A large promoter disposal in a stock you were about to buy is worth a
   pause, not a panic: it is already public and already in the price.
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
