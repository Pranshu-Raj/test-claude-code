"""trader-timemachine tests: 75-bar IST sessions, deterministic replays,
outcomes net of Indian charges. All offline, on the SYNTHETIC fixtures."""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX, ROOT

tm = load("trader-timemachine", "timemachine")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from config import datasource as ds, india_market as mkt

INDIA_FIX = os.path.join(FIX, "india")
NOW = "2026-11-02 16:00"

PLAN = {"created_by": "trader-exit", "symbol": "RELIANCE", "side": "buy", "date": "2026-11-02",
        "setup": "orb", "entry": 1570.65, "entry_time": "09:30", "entry_bar": 2, "interval": "5m",
        "opening_range": {"high": 1570.80, "low": 1567.20, "minutes": 15},
        "stop": 1569.00, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
        "target": 1573.95, "target_r": 2.0, "risk_per_share": 1.65, "reward_risk": 2.0,
        "flat_by": "15:15", "exchange": "NSE", "product": "MIS"}

CFG = {"max_loss_per_trade_inr": 1000, "exchange": "NSE", "product": "MIS"}


def series():
    return ds.get_adapter("offline", directory=INDIA_FIX).bars("RELIANCE", "5m")


def run_tm(plan=None, cfg=None, replays=500, seed=19):
    return tm.time_machine(series(), tm.normalise_plan(dict(plan or PLAN))
                           if hasattr(tm, "normalise_plan") else dict(plan or PLAN),
                           tm.parse_now(NOW), replays, seed, 2, None, "offline",
                           CFG if cfg is None else cfg)


class Session(unittest.TestCase):
    def test_a_full_session_is_75_bars_not_78(self):
        self.assertEqual(tm.SESSION_BARS, 75)
        self.assertEqual(tm.SESSION_BARS, mkt.bars_per_session(5))

    def test_the_session_runs_0915_to_1530(self):
        self.assertEqual(tm.OPEN_MIN, 9 * 60 + 15)
        self.assertEqual(tm.CLOSE_MIN, 15 * 60 + 30)
        self.assertEqual(tm.CLOSE_MIN - tm.OPEN_MIN, 375)

    def test_bars_outside_the_session_are_dropped(self):
        meta, bars, dropped = tm.parse_bars(series(), tm.parse_now(NOW))
        self.assertEqual(dropped["outside_hours"], 0)       # the fixture is session-only
        for b in bars:
            self.assertLess(b["slot"], 75)

    def test_only_complete_sessions_reach_the_pool(self):
        meta, bars, _ = tm.parse_bars(series(), tm.parse_now(NOW))
        days = tm.split_sessions(bars)
        full, short = tm.full_sessions(days, "2026-11-02")
        self.assertEqual(len(full), 59)
        self.assertEqual(short, [])
        for _date, sess in full:
            self.assertEqual(len(sess), 75)

    def test_a_short_session_is_left_out(self):
        s = series()
        s["bars"] = [b for b in s["bars"]
                     if not (mkt.ts_to_ist(b["t"]).date().isoformat() == "2026-10-30"
                             and mkt.ts_to_ist(b["t"]).hour >= 14)]
        meta, bars, _ = tm.parse_bars(s, tm.parse_now(NOW))
        full, short = tm.full_sessions(tm.split_sessions(bars), "2026-11-02")
        self.assertEqual(len(full), 58)
        self.assertEqual([x["date"] for x in short], ["2026-10-30"])

    def test_the_us_calendar_and_fetcher_are_gone(self):
        src = open(os.path.join(ROOT, "trader-timemachine", "timemachine.py"), encoding="utf-8").read()
        for gone in ("NYSE_EARLY_CLOSE", "eastern_offset", "EARLY_MIN", "fetch_chart",
                     "urllib", "SESSION_BARS = 78"):
            self.assertNotIn(gone, src, gone)


class Replay(unittest.TestCase):
    def test_the_same_seed_gives_the_same_numbers(self):
        a = run_tm()["mc"]
        b = run_tm()["mc"]
        self.assertEqual(a["p_target"], b["p_target"])
        self.assertEqual(a["avg_R"], b["avg_R"])

    def test_a_different_seed_gives_different_numbers(self):
        a = run_tm(seed=19)["mc"]
        b = run_tm(seed=7)["mc"]
        self.assertNotEqual(a["p_target"], b["p_target"])

    def test_the_probabilities_sum_to_one(self):
        m = run_tm()["mc"]
        total = m["p_target"] + m["p_stop"] + m["p_neither"]
        self.assertAlmostEqual(total, 1.0, places=6)

    def test_the_history_replay_runs_on_the_real_sessions(self):
        h = run_tm()["history"]
        self.assertEqual(h["sessions"], 59)
        self.assertGreater(h["triggered"], 0)
        self.assertEqual(h["target"] + h["stop"] + h["close"], h["triggered"])

    def test_the_replay_ends_at_the_session_close_not_1600(self):
        doc = run_tm()
        self.assertIn("15:30", json.dumps(doc["mc"]))


class Charges(unittest.TestCase):
    def test_outcomes_carry_a_cost_block(self):
        ce = run_tm()["costs"]
        self.assertIsNotNone(ce)
        self.assertEqual(ce["qty"], 606)              # 1000 // 1.65
        self.assertTrue(ce["indicative"])
        self.assertGreater(ce["round_trip_inr"], 0)

    def test_charges_are_a_large_share_of_one_r_on_a_tight_stop(self):
        ce = run_tm()["costs"]
        self.assertGreater(ce["cost_as_r"], 0.3)

    def test_charges_flip_this_setup_from_positive_to_negative(self):
        doc = run_tm()
        gross = doc["mc"]["avg_R"]
        net = gross - doc["costs"]["cost_as_r"]
        self.assertGreater(gross, 0)
        self.assertLess(net, 0)

    def test_the_verdict_says_so_rather_than_leaving_it_to_be_noticed(self):
        text = "\n".join(tm.report(run_tm()))
        self.assertIn("NOT AFTER CHARGES", text)
        self.assertIn("Net of charges", text)

    def test_without_a_max_loss_there_is_no_cost_block(self):
        self.assertIsNone(run_tm(cfg={})["costs"])

    def test_a_wider_stop_gives_charges_a_smaller_bite(self):
        wide = dict(PLAN, stop=1520.65, target=1670.65, risk_per_share=50.00)
        self.assertLess(run_tm(plan=wide)["costs"]["cost_as_r"],
                        run_tm()["costs"]["cost_as_r"])


class Output(unittest.TestCase):
    def test_the_report_is_ist_and_rupees(self):
        text = "\n".join(tm.report(run_tm()))
        self.assertIn("15:30", text)
        self.assertIn("₹", text)
        self.assertNotIn("$", text)
        self.assertNotIn(" ET", text)
        self.assertNotIn("AM", text)

    def test_the_report_calls_itself_a_simulation(self):
        text = "\n".join(tm.report(run_tm()))
        self.assertIn("not a prediction", text)
        self.assertIn("circuit", text)

    def test_the_cli_runs_offline(self):
        d = tempfile.mkdtemp()
        cwd = os.getcwd()
        try:
            os.chdir(d)
            os.mkdir("trader")
            with open(os.path.join("trader", "plan.json"), "w") as fh:
                json.dump(PLAN, fh)
            with open(os.path.join("trader", "config.json"), "w") as fh:
                json.dump(CFG, fh)
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = tm.main(["run", "--adapter", "offline", "--data", INDIA_FIX,
                                "--replays", "500", "--now", NOW])
            self.assertEqual(code, 0, err.getvalue())
            self.assertIn("Net of charges", out.getvalue())
        finally:
            os.chdir(cwd)
            shutil.rmtree(d)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-timemachine", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-timemachine\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertEqual(text[text.index("## Safety"):], risk[risk.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("75 bars", "09:15", "not a prediction", "cost_as_r", "net r",
                     "day trading loses most people money", "/trader-budget"):
            self.assertIn(must, low, must)
        # the doc explains the 78-bar US session it replaced, so only the
        # live facts are asserted positively above
        for never in ("\u2014", "nyse holiday", "alpaca"):
            self.assertNotIn(never, low, never)
        import re as _re
        self.assertIsNone(_re.search(r"\$[0-9]", low), "a dollar amount is left in the doc")


if __name__ == "__main__":
    unittest.main()
