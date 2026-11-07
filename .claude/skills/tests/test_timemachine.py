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

    def test_the_replay_ends_at_the_plans_flat_by_not_1600(self):
        doc = run_tm()
        self.assertEqual(doc["mc"]["until"], "15:15")          # PLAN["flat_by"]
        self.assertNotIn("16:00", json.dumps(doc["mc"]))

    def test_until_can_still_run_to_the_session_close(self):
        doc = tm.time_machine(series(), tm.normalize_plan(dict(PLAN)), tm.parse_now(NOW),
                              200, 19, 2, "15:30", "offline", CFG)
        self.assertEqual(doc["mc"]["until"], "15:30")
        self.assertGreater(doc["mc"]["bars_left"], run_tm(replays=200)["mc"]["bars_left"])


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
        self.assertIn("15:15", text)                 # the plan's flat_by, where a replay ends
        self.assertNotIn("16:00", text)
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


class RealData(unittest.TestCase):
    """The real RELIANCE capture (tests/fixtures/india/real/README.md). These
    sessions have 72 to 74 bars, never 75: the rule that demanded 75 found
    none of them, and the time machine had no history on any real symbol."""

    REAL = os.path.join(INDIA_FIX, "real")
    NOW_REAL = "2026-11-06 16:00"

    def real(self):
        return ds.get_adapter("offline", directory=self.REAL).bars("RELIANCE", "5m")

    def real_plan(self):
        # the first bar after the 15-minute range to close above it, on the last real day
        meta, bars, _ = tm.parse_bars(self.real(), tm.parse_now(self.NOW_REAL))
        D = tm.split_sessions(bars)["2026-11-06"]
        oh = max(D[i]["h"] for i in range(3)); ol = min(D[i]["l"] for i in range(3))
        k = next(i for i in range(3, 21) if D[i]["c"] > oh)
        e = round(D[k]["c"], 2); stop = mkt.round_to_tick((oh + ol) / 2, "down")
        return {"created_by": "trader-exit", "symbol": "RELIANCE", "side": "buy", "date": "2026-11-06",
                "setup": "orb", "entry": e, "entry_time": tm.clock24(tm.OPEN_MIN + 5 * (k + 1)),
                "entry_bar": k, "interval": "5m", "opening_range": {"high": oh, "low": ol, "minutes": 15},
                "stop": stop, "stop_rule": "or_mid", "target": mkt.round_to_tick(e + 2 * (e - stop), "up"),
                "target_r": 2.0, "risk_per_share": round(e - stop, 2), "reward_risk": 2.0,
                "flat_by": "15:15", "exchange": "NSE", "product": "MIS"}

    def test_the_capture_is_real_and_says_so(self):
        s = self.real()
        self.assertFalse(s["meta"].get("synthetic"))
        self.assertEqual(len(s["bars"]), 4391)                 # 4,500 grid slots, 109 null

    def test_real_sessions_are_72_to_74_bars_and_are_kept(self):
        meta, bars, _ = tm.parse_bars(self.real(), tm.parse_now(self.NOW_REAL))
        full, short = tm.full_sessions(tm.split_sessions(bars), "2026-11-06")
        self.assertEqual(len(full), 58)                        # 59 before the 6th, minus the gap day
        self.assertEqual([x["date"] for x in short], ["2026-10-15"])   # missing 11:45 and 11:50
        lengths = set(len(D) for _, D in full)
        self.assertTrue(lengths <= {72, 73, 74}, lengths)
        self.assertNotIn(75, lengths)

    def test_the_pool_has_every_slot_even_the_null_tail(self):
        meta, bars, _ = tm.parse_bars(self.real(), tm.parse_now(self.NOW_REAL))
        full, _ = tm.full_sessions(tm.split_sessions(bars), "2026-11-06")
        pool = tm.build_pool(full)
        for i in range(1, tm.SESSION_BARS):
            self.assertTrue(pool[i], "slot %d is empty" % i)
        self.assertIs(pool[73], pool[72])                      # 15:20 borrows 15:15

    def test_the_time_machine_runs_on_a_real_breakout(self):
        plan = tm.normalize_plan(self.real_plan())
        self.assertEqual(plan["entry_bar"], 6)                 # the 09:45 bar, closing 09:50
        doc = tm.time_machine(self.real(), plan, tm.parse_now(self.NOW_REAL), 1000, 19, 2, None,
                              "offline", CFG)
        m, h = doc["mc"], doc["history"]
        self.assertEqual(m["until"], "15:15")
        self.assertEqual(m["pool_sessions"], 58)
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)
        self.assertEqual(h["sessions"], 58)
        self.assertEqual(h["triggered"], 28)
        self.assertEqual(h["target"] + h["stop"] + h["close"], 28)

    def test_until_past_the_null_tail_does_not_crash(self):
        plan = tm.normalize_plan(self.real_plan())
        doc = tm.time_machine(self.real(), plan, tm.parse_now(self.NOW_REAL), 200, 19, 2, "15:30",
                              "offline", CFG)
        self.assertEqual(doc["mc"]["until"], "15:30")
        self.assertGreater(doc["history"]["triggered"], 0)
