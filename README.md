# The Day Trader skill, for Indian markets

Eleven Claude skills for intraday trading on NSE and BSE cash equity: scan
charts, read announcements and flows, time an entry, plan the exit, replay the
odds, size to your rupee risk, check every rule, and place nothing without
your explicit yes.

This is a port of [Jake Schincariol's Day Trader
skill](https://github.com/Jakeschincariol/day-trader-skill) (MIT) from US
markets to Indian ones. The original design is kept: **every trading number is
computed by Python, not by the model**, and no order is ever placed without
the human approval steps the original had.

**Paper trading only by default. Nothing here can place a real order on its
own.**

## Install

The skills live in `.claude/skills/`. If you have this repo, they are already
project-local, so `/trader-scanner` and the rest work in Claude Code here.

```bash
python3 .claude/skills/config/india_market.py     # print the values you must verify
cd .claude/skills && python3 -m unittest discover -s tests
```

Python 3.8 or newer. The calculation tools are standard library only, with
nothing to `pip install`. Data adapters may use optional dependencies.

## The eleven

| command | job |
| --- | --- |
| `/trader-scanner` | scans the NSE universe for setups, skipping stocks at their circuit |
| `/trader-news` | Indian business RSS, NSE/BSE announcements, RBI dates, what is moving |
| `/trader-entry` | watches a setup bar by bar from the 09:15 opening range |
| `/trader-exit` | sets the stop and target on the NSE tick, before the square-off |
| `/trader-timemachine` | replays the session 10,000 times, net of charges |
| `/trader-budget` | sizes the position in rupees, net of charges |
| `/trader-risk` | checks every rule and blocks what breaks one |
| `/trader-broker` | shows the ticket, waits for your yes, places nothing alone |
| `/trader-alerts` | the NSE clock, your stop and target, and the square-off warning |
| `/trader-analyst` | quarterly results in crore, plus FII/DII, India VIX and sector rotation |
| `/trader-insider` | SEBI insider-trading disclosures: who inside the company traded |

One trade runs through them in order:

```
scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> broker
alerts: stop, target, flat-by, square-off          (while it is open)
news / analyst / insider                            (on a schedule)
```

## Before you trade: verify the numbers

**Most shipped values are defaults, not authority.** Rates change by circular
and index membership changes at review.

The NSE **holiday calendar is now the published 2026 list** and
`CALENDAR_VERIFIED` is `True`, so the skills no longer warn about it. One
thing is still open: NSE gives the Muhurat DATE with the holiday list but its
TIMINGS in a separate circular, so the 08-Nov-2026 window is a placeholder and
the skills say so on that date.

```bash
python3 .claude/skills/config/india_market.py
```

That prints every value needing a human check. The full list, with sources, is
in [docs/verify.md](docs/verify.md). At minimum, before real money:

1. ~~`config/data/nse_holidays.csv`~~ **done**: NSE's published 2026 list,
   16 holidays, `CALENDAR_VERIFIED = True`.
2. ~~`config/data/nse_universe.csv`~~ **mostly done**: NSE's own
   `EQUITY_L.csv` and `sec_list.csv`, 2,627 symbols with real price bands,
   series and GSM flags. Only NIFTY 50/500 membership is still missing, since
   NSE's files do not carry it.
3. Every rate in `COSTS` against your broker's tariff and the current STT,
   stamp duty and GST rates.
4. `BROKER_SQUARE_OFF` against your own broker's policy. They differ.
5. The Muhurat window, once NSE issues the timing circular.

Until you do, the skills warn that NIFTY membership is unset, and warn on the
Muhurat date about its window. That is deliberate.

Refreshing the universe later is one command:

```bash
# drop new EQUITY_L.csv and sec_list.csv into config/data/source/, then
python3 .claude/skills/config/import_nse.py
```

## Setting up a broker

Zerodha Kite Connect is the default. Keys come from environment variables only,
never from a file in this repo and never from the chat:

```bash
export KITE_API_KEY=your_api_key
export KITE_ACCESS_TOKEN=your_daily_token
```

Upstox, Angel One and Dhan use the same ticket with different field names;
`config/brokers.py` holds the mapping. Set your broker's order tool to "Needs
approval" in Claude, never "Always allow".

**Kite has no bracket order.** A day trade is three separate orders: the
entry, an SL-M stop and a LIMIT target. The stop and target are not linked, so
if one fills you must cancel the other yourself. Every ticket says so.

## What is different from the US original

| | US original | this port |
| --- | --- | --- |
| Session | 09:30 to 16:00 ET, DST | 09:15 to 15:30 IST, fixed +05:30 |
| Full 5m session | 78 bars | 75 bars |
| Exit deadline | the closing bell | your broker's square-off, around 15:20 |
| Rounding | the cent | the NSE tick, 0.05 for most scrips |
| Costs | none modelled | brokerage, STT, exchange, SEBI, stamp duty, GST |
| Circuits | no concept | stocks at or near a price band are skipped |
| Order | one Alpaca bracket | three Kite legs, not linked |
| Shorting | off by default | intraday allowed, overnight in cash impossible |
| PDT rule | `pdt_guard` | removed; no such rule in India |
| Universe | Nasdaq Trader files | NSE's `EQUITY_L.csv` + `sec_list.csv`, imported |
| Trade-for-trade | no concept | BE and BZ series blocked for intraday |
| Fundamentals | SEC XBRL by CIK | a results table in crore you supply |
| Congress trades | House Clerk PTRs | SEBI insider disclosures |
| New | | FII/DII flows, India VIX, sector rotation |

### Costs change the answer, not just the display

This is the change most likely to affect what you do. The US pack models no
costs because US retail equity is commission-free. Indian intraday charges are
asymmetric (STT on the sell leg, stamp duty on the buy leg, GST on brokerage
and fees) and against a tight stop they are not noise:

- A ₹1,000 max loss with an 18.50 stop gives 54 shares gross. Those 54 shares
  lose **₹1,122** at the stop, 12% past your limit. So `trader-budget` sizes
  **47**.
- A plan showing **2.0 R** gross is **1.67 R** net.
- A setup whose average replayed outcome is **+0.167 R** gross is **−0.216 R**
  net, because charges are 38% of one R at that stop distance. The time
  machine says so instead of leaving you to notice.

Set `"max_loss_includes_costs": false` to size on gross risk instead; the
reports then say the figure is gross and name the real loss.

## Tests

```bash
cd .claude/skills && python3 -m unittest discover -s tests
```

451 tests, all offline on saved fixtures, so they pass with no network.

**The India fixtures are SYNTHETIC, plus a folder of real captures.**
`tests/fixtures/india/real/` holds real Yahoo payloads from 2026-11-07 and the
tests run on them too; that folder is what showed a real NSE session has 72 to
74 five-minute bars, not 75. The synthetic ones are shaped exactly like NSE data
(IST timestamps, 75 bars a session, 0.05 tick, real payload layouts) so the
arithmetic is genuinely tested, but no price in them ever traded. Every file
carries `"synthetic": true` and a test asserts that marker.
`tests/fixtures/india/README.md` explains how to drop in a real capture.

## What has been verified live, and what has not

Until 2026-11-07 this port was written in an environment that blocked every
market-data host. That day the hosts were allowed and the live paths were
exercised for the first time:

- **Yahoo `.NS` data: verified.** Real RELIANCE, TCS, HDFCBANK, NIFTY 50 and
  India VIX payloads parse through the adapter unchanged. The feed returns the
  full 75-slot grid but the **15:20 bar is null on every session** (15:15 and
  15:25 often), so a session has 72 to 74 bars, never 75. The old "75 bars or
  nothing" rule found zero real sessions and the time machine had no history;
  it now requires every bar up to the 15:15 flat-by and tolerates the tail.
  Real captures live in `tests/fixtures/india/real/` and the tests run on them.
- **Kite MCP: reachable.** Handshake and `tools/list` succeed. The hosted
  server exposes `place_order` and five other order-mutating tools, so
  `.claude/settings.json` denies them by name.
- **BSE API: answered 403 Access Denied** to a plain client. The BSE
  endpoints in `trader-news` and `trader-insider` are unlikely to work without
  a browser session, and should be treated as dead until shown otherwise.
- **NSE API: has not been verified.** It needs a primed cookie and a browser user agent,
  which is how `trader-insider` is written. That approach is fragile by design
  and may not be welcome by NSE; the robust route for anything that matters is
  your broker's API.
- **The four broker data adapters are stubs.** `bars()` and `quote()` raise
  "not wired up yet". They hold credentials correctly and nothing else. The
  only live price feed today is Yahoo.

The offline paths, all the arithmetic and all the parsing are tested. Each
skill says this in its own docs and at runtime.

## F&O

Later phase. `config/india_market.py` reserves the keys (lot size, expiry,
margin) and `FNO_ENABLED` is `False`. `trader-risk` refuses an F&O instrument
while derivatives are off.

## Fine print

**Not investment advice.** This is software that reads public data and does
arithmetic. It does not know your situation and it cannot see the future. Most
people who trade intraday lose money. Decide for yourself, and talk to a
SEBI-registered adviser if you need advice.

**No order without your yes.** Only `/trader-broker` can lead to an order, and
only when `/trader-risk` passed that exact order today, you typed yes for that
one order, and you clicked Allow once on Claude's own card.

**Paper first.** `"paper": true` is the default.

**A stop is a plan, not a guarantee.** In a fast market or across a gap, an
SL-M order can fill well past its trigger. And your broker will square off an
open intraday position itself, at whatever price is there.

**The free data is unofficial.** It can lag, change shape or rate-limit you.
Check anything that matters against your broker.

**Taxes.** Intraday equity profit in India is speculative business income,
taxed at your slab rate, not capital gains. F&O is non-speculative business
income. Nothing here is tax advice.

## Credit and licence

Ported from [Jakeschincariol/day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill),
MIT, copyright (c) 2026 Jake Schincariol. The original licence is kept
verbatim at `.claude/skills/LICENSE` and the original README at
`.claude/skills/UPSTREAM-README.md`. This port is under the same MIT licence.
