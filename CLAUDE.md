# Project notes for Claude

- Skills live in `.claude/skills/trader-*` (copied from Jakeschincariol/day-trader-skill, MIT).
  Each skill is plain Python 3 with no third-party dependencies.
- Skill tools write runtime state to `trader/` at the repo root. That folder is git-ignored.
- Goal of this repo: evaluate and, if viable, port the skills from US markets (NYSE hours,
  US tickers, SEC data, Alpaca) to Indian markets (NSE 9:15-15:30 IST, `.NS` / `.BO`
  tickers, NSE/BSE data, Indian brokers).
- Paper trading only. Nothing here may place a real order.
