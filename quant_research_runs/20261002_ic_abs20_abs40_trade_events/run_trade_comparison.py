"""Matched Abs20/Abs40 paper exposure-event comparison; no production edits."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
PRIOR = REPO / "quant_research_runs" / "20261002_ic_csi500_abs40_debounce"
INPUT = PRIOR / "sina_sh000905_ohlcv.csv"
SHA = "40ede7733d0be851016a7749a7d864fdc3b1d4003241c0fb18d46f76af943d32"

sys.path.insert(0, str(REPO))
sys.path.insert(0, str(PRIOR))
import poe_ic_im_mainline_v1_4_bot as icim  # noqa: E402
import run_research as prior  # noqa: E402


def event_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Calculate events on the full historical path before selecting a window."""
    positive = frame["score"].gt(0.0)
    raw_on = frame["static_abs_on"]
    effective_on = frame["abs_on"]
    raw_flip = raw_on.ne(raw_on.shift(1)).fillna(False)
    effective_flip = effective_on.ne(effective_on.shift(1)).fillna(False)
    raw_flip.iloc[0] = False
    effective_flip.iloc[0] = False
    nav_flip = frame["nav_gate"].ne(frame["nav_gate"].shift(1))
    nav_flip.iloc[0] = False
    abs_only_base = effective_flip & positive & positive.shift(1, fill_value=False)
    nav_unchanged = frame["nav_gate"].eq(frame["nav_gate"].shift(1))
    clean_abs_signal = abs_only_base & nav_unchanged
    delta = frame["execution_weight"].diff().fillna(frame["execution_weight"])
    traded = delta.abs().gt(1e-12)
    return pd.DataFrame({
        "raw_abs_on": raw_on, "effective_abs_on": effective_on,
        "raw_abs_flip": raw_flip, "raw_abs_up": raw_flip & raw_on,
        "raw_abs_down": raw_flip & ~raw_on,
        "effective_abs_flip": effective_flip,
        "effective_abs_up": effective_flip & effective_on,
        "effective_abs_down": effective_flip & ~effective_on,
        "score_positive": positive, "abs_only_base_signal": abs_only_base,
        "clean_abs_signal": clean_abs_signal,
        "clean_abs_execution_trade": clean_abs_signal.shift(1, fill_value=False) & traded,
        "nav_gate_flip": nav_flip,
        "execution_delta": delta, "trade_day": traded, "buy_day": delta.gt(1e-12),
        "sell_day": delta.lt(-1e-12), "turnover": delta.abs(),
        "target": frame["signal_target"], "execution_weight": frame["execution_weight"],
        "strategy_ret": frame["strategy_ret"], "nav_gate": frame["nav_gate"],
    }, index=frame.index)


def metrics(events: pd.DataFrame, start: pd.Timestamp | None, *, end: pd.Timestamp | None = None) -> dict:
    sample = events
    if start is not None:
        sample = sample.loc[sample.index >= start]
    if end is not None:
        sample = sample.loc[sample.index <= end]
    return {
        "start": str(sample.index[0].date()), "end": str(sample.index[-1].date()),
        "rows": len(sample),
        "raw_abs_flips": int(sample["raw_abs_flip"].sum()),
        "raw_abs_up": int(sample["raw_abs_up"].sum()),
        "raw_abs_down": int(sample["raw_abs_down"].sum()),
        "effective_abs_flips": int(sample["effective_abs_flip"].sum()),
        "effective_abs_up": int(sample["effective_abs_up"].sum()),
        "effective_abs_down": int(sample["effective_abs_down"].sum()),
        "abs_only_base_signals": int(sample["abs_only_base_signal"].sum()),
        "clean_abs_execution_trades": int(sample["clean_abs_execution_trade"].sum()),
        "trade_days": int(sample["trade_day"].sum()),
        "buy_days": int(sample["buy_day"].sum()),
        "sell_days": int(sample["sell_day"].sum()),
        "turnover": float(sample["turnover"].sum()),
        "model_cost_sum": float(0.001 * sample["turnover"].sum()),
        "nav_gate_flip_days": int(sample["nav_gate_flip"].sum()),
    }


def pairwise(a: pd.DataFrame, b: pd.DataFrame, a_name: str, b_name: str) -> dict:
    assert a.index.equals(b.index)
    a_trade, b_trade = a["trade_day"], b["trade_day"]
    both = a_trade & b_trade
    return {
        "left": a_name, "right": b_name,
        "left_only_trade_days": int((a_trade & ~b_trade).sum()),
        "right_only_trade_days": int((b_trade & ~a_trade).sum()),
        "shared_trade_days": int(both.sum()),
        "shared_trade_different_size_or_direction": int(
            (both & (a["execution_delta"] - b["execution_delta"]).abs().gt(1e-12)).sum()
        ),
        "target_different_days": int((a["target"] - b["target"]).abs().gt(1e-12).sum()),
        "execution_weight_different_days": int(
            (a["execution_weight"] - b["execution_weight"]).abs().gt(1e-12).sum()
        ),
        "nav_gate_different_days": int((a["nav_gate"] != b["nav_gate"]).sum()),
    }


def main() -> None:
    assert hashlib.sha256(INPUT.read_bytes()).hexdigest() == SHA
    data = pd.read_csv(INPUT, parse_dates=["date"]).set_index("date")
    assert len(data) == 5282 and data.index[-1] == pd.Timestamp("2026-09-30")
    assert data.index.is_monotonic_increasing and not data.index.has_duplicates
    with icim.runtime_clock(pd.Timestamp("2026-10-02 14:00", tz="Asia/Shanghai").to_pydatetime()):
        icim._validate_v13_ohlcv("IC", data, icim._now_beijing())
    specs = {
        "new_abs20_static": (105, 1.6, 20, "static"),
        "new_abs20_debounce_all": (105, 1.6, 20, "debounce_all"),
        "new_abs20_debounce_dated": (105, 1.6, 20, "icim_effective"),
        "new_abs40_static": (105, 1.6, 40, "static"),
        "new_abs40_debounce_all": (105, 1.6, 40, "debounce_all"),
        "old_abs20_static": (110, 2.0, 20, "static"),
        "old_abs20_debounce_dated": (110, 2.0, 20, "icim_effective"),
    }
    frames = {name: prior.build_arm(data, ma=ma, weight_end=w, abs_days=days, mode=mode)
              for name, (ma, w, days, mode) in specs.items()}
    events = {name: event_columns(frame) for name, frame in frames.items()}
    official = icim.v13_momentum_schedule("IC", data["close"], ohlcv=data)
    old = frames["old_abs20_debounce_dated"]
    formal_parity = {
        "target_max_error": float((old["signal_target"] - official["signal_target"]).abs().max()),
        "execution_max_error": float((old["execution_weight"] - official["execution_weight"]).abs().max()),
    }
    assert max(formal_parity.values()) <= 1e-12
    previous = pd.read_csv(PRIOR / "daily_comparison.csv", parse_dates=["date"]).set_index("date")
    prior_names = {"new_abs40_static": "new_static", "new_abs40_debounce_all": "new_debounce_all",
                   "old_abs20_static": "old_static", "old_abs20_debounce_dated": "old_icim_effective"}
    for name, earlier in prior_names.items():
        for column in ("signal_target", "execution_weight", "strategy_ret"):
            assert (frames[name][column] - previous[f"{earlier}__{column}"]).abs().max() <= 1e-10

    end = data.index[-1]
    windows = {"Full": (None, None),
               **{f"{years}Y": (end - pd.DateOffset(years=years), None)
                  for years in (10, 5, 3, 1)},
               "since_effective_signal": (pd.Timestamp("2026-09-16"), None),
               "before_effective_signal": (None, pd.Timestamp("2026-09-15"))}
    rows = [{"arm": name, "window": window, **metrics(event, start, end=stop)}
            for name, event in events.items() for window, (start, stop) in windows.items()]
    summary = pd.DataFrame(rows)
    summary.to_csv(ROOT / "event_metrics.csv", index=False, float_format="%.12g")
    daily = pd.concat(events, axis=1)
    daily.columns = [f"{name}__{field}" for name, field in daily.columns]
    daily.to_csv(ROOT / "daily_events.csv", index_label="date", float_format="%.12g")

    pairs = [("new_abs20_static", "new_abs20_debounce_all"),
             ("new_abs20_static", "new_abs20_debounce_dated"),
             ("new_abs20_static", "new_abs40_static"),
             ("new_abs40_static", "new_abs40_debounce_all"),
             ("old_abs20_static", "old_abs20_debounce_dated")]
    pd.DataFrame([pairwise(events[a], events[b], a, b) for a, b in pairs]).to_csv(
        ROOT / "pairwise_events.csv", index=False
    )
    post = events["new_abs20_static"].index >= pd.Timestamp("2026-09-16")
    post_dated_equal = bool((frames["new_abs20_static"].loc[post, "signal_target"]
                             == frames["new_abs20_debounce_dated"].loc[post, "signal_target"]).all())
    manifest = {
        "input": str(INPUT), "sha256": SHA, "source": "Sina sh000905 via frozen IC/IM input",
        "formal_source_sha256": hashlib.sha256(Path(icim.__file__).read_bytes()).hexdigest(),
        "prior_research_runner_sha256": hashlib.sha256(Path(prior.__file__).read_bytes()).hexdigest(),
        "raw_rows": len(data), "formal_rows": len(old),
        "formal_start": str(old.index[0].date()), "end": str(end.date()),
        "specs": specs, "formal_parity": formal_parity,
        "since_2026_09_16_new_abs20_static_equals_dated_target": post_dated_equal,
        "event_definitions": {
            "trade_days": "full-path execution_weight delta != 0; count by execution date including first window date",
            "turnover": "sum abs(full-path execution_weight delta)",
            "effective_abs_flips": "abs_on changes on signal day, first formal row excluded",
            "abs_only_base_signals": "effective_abs flip while Score > 0 on both adjacent signal days",
            "clean_abs_execution_trades": "above plus unchanged NAV gate, shifted to next execution row and trade observed",
        },
        "assumptions": "price-index paper sleeve, T close signal to next close-to-close return row; 2% idle cash, 10bp one-way turnover, 6%/50% NAV defense; excludes futures roll and real fills",
    }
    (ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(summary.loc[summary.window.isin(["Full", "5Y", "1Y", "since_effective_signal"]),
                      ["arm", "window", "raw_abs_flips", "effective_abs_flips",
                       "abs_only_base_signals", "clean_abs_execution_trades",
                       "trade_days", "buy_days", "sell_days", "turnover"]].to_string(index=False))
    print(json.dumps({"formal_parity": formal_parity, "post_dated_equal": post_dated_equal},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
