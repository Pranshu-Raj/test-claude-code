# How the Day Trader skills work, and what you must set up first

This explains the whole pipeline: what each of the eleven skills does, how they
hand work to each other, and every integration you need in place before any of
it is useful.

If you only read one section, read [Integrations you need
beforehand](#integrations-you-need-beforehand).

---

## 1. The core idea

Every trading number is computed by Python, never by the model.

Claude's job is to pick which skill to run, read you the output and ask you
questions. The arithmetic (where the stop goes, how many shares, what the
charges are, whether a rule is broken) happens in plain Python files you can
read and test. That is deliberate: a language model that guesses a position
size is a liability, and a model that runs `size.py` and reads you the answer
is not.

Three consequences worth understanding:

1. **You can audit everything.** Every figure traces to a line of Python and a
   value in `config/india_market.py`.
2. **The model cannot invent a number.** If the tool refuses, there is no
   answer, and Claude will tell you the refusal rather than estimate.
3. **Nothing reaches a broker on the model's own say-so.** See
   [the three gates](#5-the-three-approval-gates).

---

## 2. The shape of the thing

```
.claude/skills/
  config/                     the ONLY place market rules live
    india_market.py           sessions, calendar, tick, bands, margin, costs
    costs.py                  the intraday charge calculator
    datasource.py             six swappable data adapters
    brokers.py                Kite order shapes + the shared order fingerprint
    data/                     files YOU maintain
      nse_holidays.csv        NSE trading holidays
      nse_special_sessions.csv  Muhurat window
      nse_universe.csv        symbols, sector, index membership, price bands
      rbi_dates.csv           RBI policy dates
  trader-scanner/  trader-news/  trader-entry/  trader-exit/
  trader-timemachine/  trader-budget/  trader-risk/  trader-broker/
  trader-alerts/  trader-analyst/  trader-insider/
  tests/                      414 tests, all offline

trader/                       runtime state, at the repo root, git-ignored
```

Two rules hold this together:

- **No market rule is ever hardcoded in a skill.** Session times, the tick,
  circuit bands, costs and the square-off all come from `config/`. Change the
  config once and every skill follows.
- **Skills talk to each other only through files** in `trader/`. No skill
  imports another. That is why you can run one on its own, replay a day, or
  swap a skill out.

---

## 3. The pipeline

One trade flows left to right. Each box reads the previous box's file.

```
scanner ──> news ──> entry ──> exit ──> timemachine ──> budget ──> risk ──> broker
   │                   │         │                         │          │        │
watchlist.json    entry.json  plan.json            orders.json   risk.json  broker.json

alerts      stop / target / flat-by / square-off      (while the trade is open)
analyst     results, FII-DII, India VIX, sectors      (on a schedule)
insider     SEBI insider disclosures                  (on a schedule)
```

### A worked example, end to end

These are the real numbers from the repo's own fixtures, which is why they
appear in the tests too.

**1. Scan.** `scan.py orb --at 10:15` reads the universe, fetches 5-minute
bars, builds the 09:15 opening range and finds TCS closed above it. Stocks at
or near their circuit are skipped here, because a breakout into a price band
cannot run. Writes `trader/watchlist.json`.

**2. News.** `news.py --tickers TCS` checks Indian business RSS, NSE and BSE
announcements and RBI dates. If TCS reports results today, that is not a
breakout trade, it is a coin flip on a number.

**3. Entry.** `entry.py check TCS` confirms the trigger: the 09:45 bar CLOSED
at ₹4,026.50, above the range high of ₹4,020.00. A wick through the level is
not a trigger. Writes `trader/entry.json`.

**4. Exit.** `exit.py plan --target 2R` sets the stop at the opening-range
midpoint, ₹4,008.00, and the target at ₹4,063.50. Both land on the NSE tick
(0.05), so they are prices you can actually send. The flat-by time is forced
before your broker's square-off. Writes `trader/plan.json`.

**5. Time machine.** `timemachine.py run` replays the rest of the session
10,000 times from this stock's own last 60 sessions, and re-runs the same
rule on each of those real days. It reports the odds **net of charges**, which
on a tight stop is the difference between a setup that looks positive and one
that is not.

It needs at least **20 complete prior sessions** of 5-minute bars for that
symbol, and it counts only full 75-bar days, so a half session or a data gap
is dropped rather than averaged in. A recent listing, a thinly traded scrip
or a short history will be refused with that reason instead of given a thin
answer. The rest of the pipeline still works; you just trade without the
odds.

**6. Budget.** `size.py` sizes to your ₹1,000 max loss. Gross arithmetic says
54 shares. At 54 shares the loss at the stop is **₹1,122** once charges land,
12% past your limit, so it gives **47**. Writes `trader/orders.json` as three
Kite legs.

**7. Risk.** `check.py` checks every rule and records a PASS or BLOCK against
a fingerprint of that exact order. Writes `trader/state/risk.json`.

**8. Broker.** `ticket.py show` prints the ticket and the exact `place_order`
arguments per leg. It will not mark anything READY unless risk passed that
same fingerprint today. Then it waits for your yes.

---

## 4. What each skill does

| skill | command | reads | writes |
| --- | --- | --- | --- |
| **scanner** | `scan.py universe \| daily \| orb` | `config/data/nse_universe.csv`, a data adapter | `trader/universe.txt`, `trader/watchlist.json` |
| **news** | `news.py` | watchlist, plan, portfolio, RSS, NSE/BSE announcements, `rbi_dates.csv` | `trader/state/news.json` |
| **entry** | `entry.py check \| watch \| watchlist` | watchlist, a data adapter | `trader/entry.json` |
| **exit** | `exit.py plan \| show` | `trader/entry.json`, config | `trader/plan.json` |
| **timemachine** | `timemachine.py run` | `trader/plan.json`, 60 days of bars (needs 20+ full sessions) | `trader/timemachine.json` |
| **budget** | `size.py` | plan, portfolio, prices, config | `trader/orders.json` |
| **risk** | `check.py` | orders, portfolio, prices, `state/broker.json` | `trader/state/risk.json` |
| **broker** | `ticket.py show \| approve \| result \| fill \| flatten` | orders, `state/risk.json` | `trader/state/broker.json`, `trader/portfolio.json` |
| **alerts** | `alerts.py clock \| quote \| add \| check` | plan, `trader/alerts.json`, a data adapter | `trader/prices.json`, `trader/state/alerts.json` |
| **analyst** | `facts.py results \| flows` | a results table you supply, NSE flows | `trader/metrics.json` (optional) |
| **insider** | `insider.py list \| new` | NSE insider disclosures, portfolio | `trader/state/insider.json` |

### The file contracts

| file | written by | read by |
| --- | --- | --- |
| `trader/config.json` | **you** | everything |
| `trader/universe.txt` | scanner | scanner |
| `trader/watchlist.json` | scanner | news, entry |
| `trader/entry.json` | entry | exit |
| `trader/plan.json` | exit | timemachine, budget, alerts, broker, news |
| `trader/orders.json` | budget (or `ticket.py flatten`) | risk, broker |
| `trader/state/risk.json` | risk | broker |
| `trader/state/broker.json` | broker | risk (duplicate and daily-limit checks) |
| `trader/portfolio.json` | broker, on a recorded fill | budget, risk, insider, news |
| `trader/prices.json` | alerts | budget, risk |

`trader/` is git-ignored. Deleting it resets all state and loses your recorded
fills and portfolio, so back it up if you care about the history.

---

## 5. The three approval gates

Only `trader-broker` can lead to an order, and only when **all three** hold:

1. **`trader-risk` recorded a PASS for that exact order, today.** The match is
   a fingerprint over the symbol, side, quantity, type, venue, product and
   every leg's parameters. Change a price or a quantity by one paisa and it
   must pass again.
2. **You typed `yes` for that one order.** Not "yes to all", not a hedged
   "yes but wait", not a yes naming a different symbol. The reply is recorded
   verbatim.
3. **You clicked Allow once on Claude's own permission card.** This requires
   your broker's order tool to be set to "Needs approval" in Claude. Never set
   it to "Always allow".

Paper trading is the default (`"paper": true`). The ticket says LIVE ACCOUNT,
REAL MONEY when it is not.

### One thing to understand about Kite

Kite withdrew Bracket Orders, so a day trade is **three separate orders**:

| leg | type | when |
| --- | --- | --- |
| entry | MARKET or LIMIT, BUY | now |
| stop | SL-M with a trigger price, SELL | only after the entry fills |
| target | LIMIT, SELL | only after the entry fills |

**The stop and target are not linked.** If the target fills, the stop is still
live, and if it then triggers you are short a stock you no longer own.
Cancelling the survivor is your job. Every ticket repeats this.

---

## 6. Costs are part of the arithmetic

The US pack this was ported from models no costs, because US retail equity is
commission-free. Indian intraday charges are asymmetric and, against a tight
stop, large:

- **STT** falls on the sell leg only (intraday).
- **Stamp duty** falls on the buy leg only.
- **GST** applies to brokerage plus exchange and SEBI charges, not to STT.
- **Depository charges** hit a delivery sell, never intraday.

So a long and a short of the same size do not cost the same, and
`max_loss_per_trade_inr` is treated as **what you actually lose**:

| | gross | net of charges |
| --- | --- | --- |
| shares for a ₹1,000 max loss (18.50 stop) | 54 | **47** |
| loss at the stop at 54 shares | ₹999 | **₹1,122** |
| a 2R plan's reward:risk | 2.00 | **1.67** |
| a setup's average replayed outcome | +0.167R | **−0.216R** |

That last row is the point: a setup that looks profitable gross loses money
net at that stop distance, and the time machine says so rather than leaving
you to notice.

Set `"max_loss_includes_costs": false` to size on gross risk instead. The
reports then say the figure is gross and name the real loss, so it cannot
mislead you quietly.

---

## Integrations you need beforehand

Six things. Only the first two are needed to run anything at all.

### 1. Python 3.8 or newer (required)

```bash
python3 --version
```

The calculation tools are standard library only. Nothing to `pip install`.

### 2. The skills themselves (required)

They are already project-local in `.claude/skills/`, so in Claude Code from
this repo `/trader-scanner` and the rest work with no install step. Confirm:

```bash
cd .claude/skills && python3 -m unittest discover -s tests
```

414 tests, offline. If they pass, the arithmetic works.

### 3. A data adapter (required for anything live)

Everything that needs prices goes through `config/datasource.py`. Pick one:

| adapter | needs | good for |
| --- | --- | --- |
| `offline` | a folder of saved payloads | tests, replaying a day, no network |
| `yahoo` | nothing | the default. Free, delayed, unofficial |
| `kite` | `KITE_API_KEY`, `KITE_ACCESS_TOKEN` | real broker data |
| `upstox` | `UPSTOX_ACCESS_TOKEN` | the same |
| `angel` | `SMARTAPI_API_KEY`, `SMARTAPI_ACCESS_TOKEN` | the same |
| `dhan` | `DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN` | the same |

Choose per command with `--adapter`, or set a default:

```bash
export TRADER_DATA_ADAPTER=yahoo
```

**Network access.** The free path needs `query2.finance.yahoo.com`. The
exchange features additionally need `www.nseindia.com` and
`api.bseindia.com`. If you are in a sandboxed or proxied environment, those
hosts must be allowed or every data command fails.

### 4. A broker, only when you want to place orders (optional)

Zerodha Kite Connect is the default. Keys come from environment variables
**only**, never from a file in this repo and never from the chat:

```bash
export KITE_API_KEY=your_api_key
export KITE_ACCESS_TOKEN=your_daily_token
```

The Kite access token is regenerated daily. Nothing here stores it.

Then, in Claude: set the broker's order tool to **"Needs approval"** under
Customize, Connectors, tool permissions. This is gate 3 and it is the one you
configure rather than the code.

You can run the whole pipeline with no broker at all. Everything up to and
including `ticket.py show` works; only the actual placement needs one.

### 5. The data files you maintain (required before real money)

These are the ones that silently give wrong answers if stale. The holiday
calendar is done: it is NSE's published 2026 list. The other three are still
templates, and the skills warn until you replace them.

| file | from | how often |
| --- | --- | --- |
| `config/data/nse_holidays.csv` | **done for 2026** (NSE's published list). Redo yearly and raise `CALENDAR_YEARS` | yearly |
| `config/data/nse_special_sessions.csv` | date done for 2026; the WINDOW needs NSE's Muhurat timing circular | yearly |
| `config/data/nse_universe.csv` | niftyindices.com factsheets, NSE `EQUITY_L.csv`, NSE's daily price-bands file | every index review |
| `config/data/rbi_dates.csv` | RBI's MPC schedule | yearly |

The universe file's `band_pct` column is what makes circuit filtering work.
Leave it blank and the scanner reports that symbol as band-unknown instead of
pretending it is clear.

### 6. Your own rules (required)

```bash
mkdir -p trader
cp .claude/skills/trader-budget/config.example.json trader/config.json
```

Then edit it. The values that matter most:

| key | what it decides |
| --- | --- |
| `budget` | your account size, in rupees |
| `max_loss_per_trade_inr` | the most one trade may lose, charges included |
| `max_daily_loss_inr` | the most the day may lose |
| `max_trades_per_day` | how many entries |
| `min_reward_risk` | the minimum reward:risk `trader-risk` will pass |
| `flat_by` | when you intend to be out, before the square-off |
| `broker`, `exchange`, `product` | `kite`, `NSE`, `MIS` |
| `paper` | keep this `true` until you mean it |

**And check `BROKER_SQUARE_OFF`** in `config/india_market.py` against your own
broker's policy. They differ, roughly 15:15 to 15:25, and `trader-exit` and
`trader-risk` both key off it.

### Optional: `pdftotext`

`trader-insider` can read a PDF attachment when `pdftotext` (poppler-utils) is
installed. Without it that one feature degrades gracefully; nothing else cares.

### The setup order, as a checklist

```
[ ] 1. python3 --version                          (3.8+)
[ ] 2. run the tests                              (414 pass, offline)
[ ] 3. pick a data adapter, allow its hosts
[ ] 4. replace the four data files, flip CALENDAR_VERIFIED
[ ] 5. verify every cost rate                     (see docs/verify.md)
[ ] 6. check BROKER_SQUARE_OFF against your broker
[ ] 7. write trader/config.json, keep paper: true
[ ] 8. run the pipeline end to end on paper
[ ] 9. only then: broker keys, tool set to Needs approval
```

---

## 7. Verify the numbers before you trust them

```bash
python3 .claude/skills/config/india_market.py
```

That prints all 46 values carrying a `VERIFY` marker. The grouped list with
sources is in [verify.md](verify.md). Rates change by circular; index
membership changes at review.

---

## 8. What has not been verified live

The environment this port was written in blocks every market-data host, so:

- The **four broker adapters** build their requests from each vendor's
  published API but have **never touched a live endpoint**.
- The **NSE and BSE endpoints** in `trader-news`, `trader-insider` and
  `trader-analyst` follow the published JSON but have not been called. NSE
  needs a browser user agent and a primed cookie and rate-limits hard.
- The **Yahoo adapter's** `.NS` payload shape is assumed to match the US one.

All the arithmetic, all the parsing and every offline path **are** tested.
Treat the live paths as scaffolding to confirm with your own key first. Each
skill says so in its own docs and at runtime.

The test fixtures under `tests/fixtures/india/` are **synthetic**. They are
shaped exactly like NSE data so the arithmetic is genuinely exercised, but no
price in them ever traded. Every file carries `"synthetic": true` and a test
asserts that marker.

---

## 9. Running it day to day

**Before the open**

```bash
python3 .claude/skills/trader-scanner/scan.py daily --gap 2
python3 .claude/skills/trader-analyst/facts.py flows --from DIR
python3 .claude/skills/trader-insider/insider.py new
python3 .claude/skills/trader-alerts/alerts.py clock
```

**During the session**

```bash
python3 .claude/skills/trader-scanner/scan.py orb --at 10:15
python3 .claude/skills/trader-news/news.py                  # on what it found
python3 .claude/skills/trader-entry/entry.py watch TCS
# on a trigger: exit -> timemachine -> budget -> risk -> broker
python3 .claude/skills/trader-alerts/alerts.py add --from-plan
python3 .claude/skills/trader-alerts/alerts.py check         # every few minutes
```

**Near the close**

The square-off alert rings ten minutes before your broker acts.
`ticket.py flatten --symbol TCS` builds the exit ticket; it still needs
`trader-risk` and your yes.

---

## 10. Fine print

**Not investment advice.** This is software that reads public data and does
arithmetic. It does not know your situation and cannot see the future. Most
people who trade intraday lose money.

**A stop is a plan, not a guarantee.** Across a gap or in a fast market an
SL-M order can fill well past its trigger. And your broker will square off an
open intraday position itself, at whatever price is there.

**The free data is unofficial.** It can lag, change shape or rate-limit you.
Check anything that matters against your broker.

**Taxes.** Intraday equity profit in India is speculative business income,
taxed at your slab rate, not capital gains. Nothing here is tax advice.

Ported from [Jakeschincariol/day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill),
MIT. See the repo [README](../README.md) and [verify.md](verify.md).
