# Values you must verify before trading real money

Every value below is a default I set, not a fact I confirmed. Rates change by
circular, index membership changes at review, and the religious-holiday dates
in this repo were written from memory because the environment this port was
built in cannot reach NSE.

Run `python3 .claude/skills/config/india_market.py` to print this list from
the code itself. There are **46 VERIFY markers** across the port.

## 1. Blocking: do these first

These are wrong often enough, or dangerous enough, that the skills warn about
them until you act.

| what | where | source to check against |
| --- | --- | --- |
| The NSE holiday calendar, every row | `config/data/nse_holidays.csv` | NSE's annual Trading Holidays circular. The `confidence=estimate` rows are lunar-calendar dates and are NOT reliable. Then set `CALENDAR_VERIFIED = True` |
| The Muhurat session window | `config/data/nse_special_sessions.csv` | NSE's Muhurat Trading circular for that year |
| The universe, every row and band | `config/data/nse_universe.csv` | niftyindices.com factsheets for index membership; NSE `EQUITY_L.csv` for the symbol list; NSE's daily price-bands file for `band_pct` |
| Your broker's square-off time | `BROKER_SQUARE_OFF` in `config/india_market.py` | your broker's own intraday policy page. Zerodha, Upstox, Angel One and Dhan differ, roughly 15:15 to 15:25 |

## 2. Costs: every rate feeds position size

All in `config/india_market.py`, `COSTS`. These decide how many shares
`trader-budget` gives you, so an error here changes your real risk.

| key | shipped | source |
| --- | --- | --- |
| `brokerage_pct` | 0.03% | your broker's tariff sheet |
| `brokerage_flat_inr` | ₹20 | the same |
| `brokerage_per` | per executed order, each leg | your broker's tariff: some charge per order, some per execution |
| `brokerage_mode` | `lower_of` | the same. Delivery brokerage is zero at most discount brokers; this charges it, which overstates CNC costs |
| `stt_intraday_sell_pct` | 0.025% | Income Tax Dept / NSE charges page. Sell leg only |
| `stt_intraday_buy_pct` | 0 | the same |
| `stt_delivery_pct` | 0.1% | the same, both sides for CNC |
| `exchange_txn_pct_nse` | 0.00297% | NSE transaction charges circular. NSE has revised this |
| `exchange_txn_pct_bse` | 0.00375% | BSE's own circular; rates vary by group |
| `sebi_turnover_pct` | 0.0001% | SEBI fee circular (per crore) |
| `stamp_duty_intraday_buy_pct` | 0.003% | Indian Stamp Act rates as notified. Buy leg only |
| `stamp_duty_delivery_buy_pct` | 0.015% | the same |
| `gst_pct` | 18% | current GST rate on broking services |
| `dp_charge_delivery_sell_inr` | ₹13.5 | your broker plus depository tariff. Delivery sell only |

## 3. Market structure

| what | shipped | source |
| --- | --- | --- |
| `PRE_OPEN_START` / `PRE_OPEN_END` | 09:00 / 09:15 | NSE market hours page |
| `SESSION_OPEN` / `SESSION_CLOSE` | 09:15 / 15:30 | the same |
| `POST_CLOSE_START` / `END` | 15:40 / 16:00 | the same. Only matters if you add CNC closing-price orders |
| `FLAT_BY` | 15:15 | your own choice, but it must sit before the square-off |
| `NO_ENTRIES_BEFORE` / `AFTER` | 09:20 / 14:45 | your own choice |
| `DEFAULT_TICK`, `TICK_BY_PRICE` | 0.05, with 0.01 under ₹250 | NSE trading parameters and SEBI's tick-size circular. Confirm per symbol if a stop depends on it |
| `PRICE_BANDS_PCT` | 2 / 5 / 10 / 20% | NSE price bands file |
| `FNO_DYNAMIC_OPERATING_RANGE_PCT` | 10% | NSE operating-range circular |
| `MARKET_WIDE_BREAKER_PCT` | 10 / 15 / 20% | SEBI and NSE index-based market-wide circuit breaker circular |
| `INTRADAY_MARGIN_MULTIPLIER` | 5x | SEBI's margin framework plus your broker's leverage page. Not a promise of leverage |
| `SETTLEMENT_CYCLE` | T+1 | SEBI's rolling settlement circular |
| `INTRADAY_SHORT_ALLOWED` | true | SEBI short-selling framework |
| `OVERNIGHT_SHORT_ALLOWED_CASH` | false | the same. This one is structural, not a preference |
| `BENCHMARKS` symbol map | `^NSEI` and friends | confirm each still resolves on your data provider |

## 4. Regulatory thresholds

| what | shipped | source |
| --- | --- | --- |
| `DISCLOSURE_THRESHOLD_INR` | ₹10 lakh a quarter | SEBI (Prohibition of Insider Trading) Regulations, Reg 7(2) |
| `DISCLOSURE_DAYS` | 2 trading days | the same |

In `trader-insider/insider.py`.

## 5. Endpoints and broker APIs, none of them exercised

The environment this port was written in blocks every market-data host, so
none of the live paths below has been called even once. They follow each
vendor's published API. Confirm them with your own key before relying on them.

| where | what |
| --- | --- |
| `config/datasource.py` | the Kite, Upstox, Angel One and Dhan adapters |
| `config/brokers.py` | `PLACE_TOOL`, `FIELD_MAP` and `PRODUCT_MAP` per broker |
| `trader-news/news.py` | `NEWS_FEEDS`, `NSE_ANNOUNCE`, `BSE_ANNOUNCE` |
| `trader-insider/insider.py` | `NSE_PIT`, `NSE_PIT_SYMBOL`, `BSE_PIT` |
| `trader-analyst/facts.py` | `NSE_FII_DII`, `NSE_VIX`, `NSE_RESULTS` |
| `config/datasource.py` | the Yahoo `.NS` payload shape, assumed to match the US one |

## 6. Maintained by you, ongoing

| file | when |
| --- | --- |
| `config/data/nse_holidays.csv` | every year |
| `config/data/nse_special_sessions.csv` | every year |
| `config/data/nse_universe.csv` | every index review, and whenever a band changes |
| `config/data/rbi_dates.csv` | every year, from RBI's MPC schedule |

## A note on the tax line

Reports say intraday equity profit is speculative business income taxed at
your slab rate, not capital gains. That is the general position and it is in
the code as a note, not as advice. Confirm your own treatment with a
qualified professional and the Income Tax Act for your assessment year.
