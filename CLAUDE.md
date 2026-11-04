# Project notes for Claude

- Skills live in `.claude/skills/trader-*`: eleven Claude skills for Indian
  cash-equity intraday trading, ported from Jakeschincariol/day-trader-skill
  (MIT, US markets). `.claude/skills/UPSTREAM-README.md` and
  `.claude/skills/LICENSE` keep the original's attribution.
- Market rules live in ONE place: `.claude/skills/config/`.
  - `india_market.py` holds every session, calendar, tick, price-band,
    margin, settlement, benchmark and cost value, each with a `VERIFY`
    comment naming its official source. Never hardcode a market rule in a
    skill.
  - `costs.py` is the intraday cost calculator, `datasource.py` the swappable
    data adapters, `brokers.py` the Kite order shapes and the fingerprint
    both trader-risk and trader-broker use.
  - `config/data/*.csv` are the files a human maintains: NSE holidays,
    special (Muhurat) sessions, the NSE universe with price bands, RBI dates.
- Each skill is plain Python 3 with no third-party dependencies. Adapters may
  use optional ones; the calculation tools must not.
- Skill tools write runtime state to `trader/` at the repo root. That folder
  is git-ignored.
- Tests: `cd .claude/skills && python3 -m unittest discover -s tests`. They
  run entirely offline. The India fixtures under
  `tests/fixtures/india/` are SYNTHETIC and every file says so; a test
  asserts that marker, so never relabel one as real data.
- Paper trading only. Nothing here may place a real order. An order needs all
  three gates: a trader-risk PASS for that exact order today, the user's typed
  yes for that one order, and Claude's own Allow once card.
- Costs are not cosmetic here. `max_loss_per_trade_inr` means the NET loss,
  charges included, so trader-budget sizes down accordingly. If you change
  anything in the sizing or risk path, keep that true.
- Every trading number is computed by the Python tools, never by the model.
- Before relying on any value, see `docs/verify.md`. The NSE holiday calendar
  is the published 2026 list and `CALENDAR_VERIFIED` is True; the universe and
  the cost rates are still templates and the skills warn until replaced. The
  Muhurat DATE is official but its WINDOW is a placeholder until NSE issues
  the timing circular, and `calendar_warning()` says so on that date.
