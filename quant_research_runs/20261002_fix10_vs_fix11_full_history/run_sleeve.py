"""Recompute full-history FIX10/FIX11 IC momentum sleeves without date splicing."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PRIOR = ROOT / "quant_research_runs/20261002_ic_csi500_abs40_debounce"
INPUT = PRIOR / "sina_sh000905_ohlcv.csv"
EXPECTED_SHA = "40ede7733d0be851016a7749a7d864fdc3b1d4003241c0fb18d46f76af943d32"
sys.path[:0] = [str(ROOT), str(PRIOR)]
import poe_ic_im_mainline_v1_4_bot as formal  # noqa: E402
import run_research as prior  # noqa: E402


def window_metrics(frame: pd.DataFrame, start: pd.Timestamp | None) -> dict:
    sample = frame if start is None else frame.loc[frame.index >= start]
    returns = sample.strategy_ret.to_numpy(float)
    nav = np.r_[1.0, np.cumprod(1.0 + returns)]
    dd = nav / np.maximum.accumulate(nav) - 1.0
    delta = sample.execution_weight.diff().fillna(sample.execution_weight).to_numpy(float)
    # At a window boundary, compare to the actual prior historical execution weight.
    if start is not None:
        first = frame.index.get_loc(sample.index[0])
        if first > 0:
            delta[0] = sample.execution_weight.iloc[0] - frame.execution_weight.iloc[first - 1]
    return {
        "start": str(sample.index[0].date()), "end": str(sample.index[-1].date()),
        "rows": len(sample), "cumulative_return": float(nav[-1] - 1.0),
        "annual_return_244": float(nav[-1] ** (244 / len(sample)) - 1.0),
        "max_drawdown": float(dd.min()),
        "trade_days": int(np.count_nonzero(np.abs(delta) > 1e-12)),
        "turnover": float(np.abs(delta).sum()),
        "mean_execution_weight": float(sample.execution_weight.mean()),
        "nav_gate_days": int(sample.nav_gate.sum()),
    }


def main() -> None:
    assert hashlib.sha256(INPUT.read_bytes()).hexdigest() == EXPECTED_SHA
    raw = pd.read_csv(INPUT, parse_dates=["date"]).set_index("date", verify_integrity=True)
    assert len(raw) == 5282 and raw.index.max() == pd.Timestamp("2026-09-30")
    arms = {
        "FIX10_full": prior.build_arm(raw, ma=110, weight_end=2.0, abs_days=20, mode="debounce_all"),
        "FIX11_full": prior.build_arm(raw, ma=105, weight_end=1.6, abs_days=40, mode="static"),
    }
    # Prior, independently saved FIX11 full-history paper path must remain identical.
    saved = pd.read_csv(PRIOR / "daily_comparison.csv", parse_dates=["date"]).set_index("date")
    for col in ("score", "base_target", "base_dd", "signal_target", "execution_weight", "strategy_ret", "nav"):
        # The archived CSV rounds score to about 1e-9; weight/return parity is tighter.
        tolerance = 1e-9 if col == "score" else 1e-10
        assert np.allclose(arms["FIX11_full"][col], saved[f"new_static__{col}"], rtol=0, atol=tolerance)
    for name, frame in arms.items():
        assert frame.index[0] == pd.Timestamp("2007-01-15") and frame.index[-1] == pd.Timestamp("2026-09-30")
        assert frame.index.equals(next(iter(arms.values())).index)
        assert frame["execution_weight"].equals(frame["signal_target"].shift(1, fill_value=0.0))
        assert np.isfinite(frame["strategy_ret"]).all() and (frame["nav"] > 0).all()

    end = raw.index[-1]
    windows = {"Full": None, **{f"{years}Y": end - pd.DateOffset(years=years) for years in (10, 5, 3, 1)}}
    rows = [{"arm": name, "window": label, **window_metrics(frame, start)}
            for name, frame in arms.items() for label, start in windows.items()]
    pd.DataFrame(rows).to_csv(HERE / "window_metrics.csv", index=False)
    daily = pd.concat({name: frame for name, frame in arms.items()}, names=["arm", "date"])
    daily.reset_index().to_csv(HERE / "daily_paths.csv", index=False)
    summary = {
        "input_sha256": EXPECTED_SHA,
        "producer_sha256": hashlib.sha256((ROOT / "poe_ic_im_mainline_v1_4_bot.py").read_bytes()).hexdigest(),
        "helper_sha256": hashlib.sha256((PRIOR / "run_research.py").read_bytes()).hexdigest(),
        "research_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "spec_sha256": hashlib.sha256((HERE / "preregistered_spec.md").read_bytes()).hexdigest(),
        "fix11_saved_path_parity": "PASS",
        "scope": "IC price-index momentum sleeve, full-history independent arm paths, no account/Put/grid",
        "windows": windows.keys(),
    }
    summary["windows"] = list(summary["windows"])
    (HERE / "manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
