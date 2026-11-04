---
name: trader-analyst
description: >-
  Earnings and Flows Analyst for Indian equities. Two halves. FUNDAMENTALS:
  reads a quarterly results table in rupee crore and sums up what changed,
  quarter by quarter, each against the same quarter a year earlier, with
  gross, operating and net margins, net income, diluted EPS and the trailing
  twelve months. FLOWS, which the US pack has no concept of: net FII and DII
  cash-market activity, India VIX with a warning when volatility is rising,
  and which sector indices are leading or lagging NIFTY 50 today. Amounts in
  lakh and crore. Use when the user says "how did Reliance's results look",
  "analyze the results", "what changed this quarter", "is revenue growing",
  "compare margins", "FII DII data", "who is buying", "India VIX", "sector
  rotation", "which sectors are leading", or before trading a stock around
  its results.
argument-hint: "[results SYMBOL | flows]"
---

# trader-analyst

Two questions, one skill. What changed in this company's numbers, and where
is the money going today. Every figure comes from `facts.py`. A quarterly
number is not a day-trading signal, and this is not investment advice.
Day trading loses most people money.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/facts.py" results RELIANCE --from DIR
python3 "${CLAUDE_SKILL_DIR}/facts.py" results RELIANCE --paste results.json
python3 "${CLAUDE_SKILL_DIR}/facts.py" flows --from DIR
python3 "${CLAUDE_SKILL_DIR}/facts.py" results RELIANCE TCS --from DIR --metrics trader/metrics.json
```

## Fundamentals, and why the input changed

The arithmetic is the US pack's, unchanged and still tested: quarter on
quarter, year on year, margins at three levels, and a trailing twelve
months with Q4 derived from the annual figure where a quarter is missing.

What changed is the source. The US version read SEC's XBRL company-facts
API: one free, structured, uniform endpoint keyed by CIK, in us-gaap tags.
**India has no equivalent.** Companies file results with the exchanges as
PDFs and XBRL of uneven quality, and there is no free API that normalises
them across companies. Pretending otherwise would have produced a skill that
breaks on the second symbol you try.

So the input is a small results table you save or paste:

```json
{"symbol": "RELIANCE", "unit": "crore",
 "quarters": [
   {"end": "2026-09-30", "revenue": 258000, "cost_of_materials": 172000,
    "operating_income": 42000, "net_income": 19500, "eps_diluted": 28.8}
 ]}
```

Values are in rupee crore unless `"unit": "inr"`. EPS is always rupees a
share. Several line names are accepted per metric
(`revenue_from_operations`, `total_income`, `net_sales`, `profit_after_tax`,
`pat` and so on) because filings differ. Put the file at
`<DIR>/<SYMBOL>-results.json` and use `--from DIR`, or pass one with
`--paste`.

## Flows, which matter more intraday

| what | why it matters |
| --- | --- |
| FII and DII net | who was actually buying the cash market, in crore |
| India VIX | with a flag when it jumps, because rising volatility hits stops |
| Sector rotation | each sector index against NIFTY 50 today, ranked |

A heavy FII sell day with VIX up is a different market from a quiet one,
whatever an individual chart says. The US pack has no equivalent: it never
modelled flows or a volatility index at all.

Flows are one day's activity, reported after the close. They describe what
happened; they do not predict tomorrow.

## Honest status

**Unverified live.** The NSE endpoints for FII/DII, indices and results
follow NSE's published JSON, but this port was written where those hosts are
unreachable, so no live call has been made, and NSE needs a browser user
agent and a primed cookie. Saved and pasted inputs are the tested path, and
`--from DIR` is how the skill is meant to be used until you have confirmed a
live route.

Tested offline: the results adapter, the quarterly and year-on-year
arithmetic, margins, the trailing twelve months, crore formatting, FII/DII
parsing, VIX reading, and the sector ranking against the benchmark.

## Steps

1. Before trading a stock around its results, run `results` on it. A
   breakout through a result is a coin flip on the number.
2. Run `flows` in the morning for the market's mood.
3. Neither is a signal. They are context for a decision `/trader-exit` and
   `/trader-risk` still have to pass.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
