"""Read-only independent checks of saved full-history sleeves and account outputs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
RAW = HERE.parent / "20261002_ic_csi500_abs40_debounce/sina_sh000905_ohlcv.csv"


def check_sleeve(name: str, z: pd.DataFrame, raw: pd.DataFrame) -> dict:
    z = z.sort_values("date").reset_index(drop=True)
    assert len(z) == 4792 and z.date.iloc[0] == "2007-01-15" and z.date.iloc[-1] == "2026-09-30"
    n = 20 if name == "FIX10_full" else 40
    original = raw.close.div(raw.close.shift(n)).sub(1).reindex(pd.to_datetime(z.date)).to_numpy(float)
    assert np.nanmax(np.abs(original - z.abs_mom.to_numpy(float))) < 1e-12
    if n == 20:
        active = False
        streak = 0
        states = []
        for value in original:
            if not np.isfinite(value) or value <= 0:
                active, streak = False, 0
            elif active:
                streak = 0
            elif value > 0.01:
                streak += 1
                if streak == 2:
                    active, streak = True, 0
            else:
                streak = 0
            states.append(active)
    else:
        states = (original > 0).tolist()
    assert np.array_equal(states, z.abs_on.to_numpy(bool))
    base = z.base_target.to_numpy(float)
    execution_base = np.r_[0.0, base[:-1]]
    actual_execution = np.r_[0.0, z.signal_target.to_numpy(float)[:-1]]
    close = z.close.to_numpy(float)
    price_ret = np.r_[0.0, close[1:] / close[:-1] - 1]
    cash = 1.02 ** (1 / 244) - 1
    base_turn = np.abs(execution_base - np.r_[0.0, execution_base[:-1]])
    base_return = execution_base * price_ret + (1 - execution_base) * cash - 0.001 * base_turn
    base_return[0] = 0.0
    base_nav = np.cumprod(1 + base_return)
    base_dd = base_nav / np.maximum.accumulate(base_nav) - 1
    assert np.max(np.abs(base_nav - z.base_nav)) < 1e-10
    assert np.max(np.abs(base_dd - z.base_dd)) < 1e-10
    turn = np.abs(actual_execution - np.r_[0.0, actual_execution[:-1]])
    net_return = actual_execution * price_ret + (1 - actual_execution) * cash - 0.001 * turn
    net_return[0] = 0.0
    assert np.max(np.abs(net_return - z.strategy_ret)) < 1e-12
    assert np.max(np.abs(np.cumprod(1 + net_return) - z.nav)) < 1e-10
    assert np.all(z.nav <= z.no_cost_nav_same_path + 1e-10)
    return {"abs_state_mismatch": 0, "base_nav_max_error": float(np.max(np.abs(base_nav - z.base_nav))),
            "return_max_error": float(np.max(np.abs(net_return - z.strategy_ret))),
            "trade_days": int(np.count_nonzero(turn > 1e-12)), "turnover": float(turn.sum())}


def check_account(name: str, daily: pd.DataFrame, events: pd.DataFrame) -> dict:
    daily = daily.sort_values("date").reset_index(drop=True)
    assert len(daily) == 945 and daily.date.iloc[0] == "2022-09-19" and daily.date.iloc[-1] == "2026-08-13"
    ret = daily.return_net.to_numpy(float)
    nav = np.cumprod(1 + ret)
    assert np.max(np.abs(nav - daily.nav)) < 1e-10
    trade = events.loc[events.event.eq("trade")]
    fee = float(trade.fee.sum())
    assert abs(fee - daily.fees_cumulative.iloc[-1]) < 1e-6
    return {"nav_max_error": float(np.max(np.abs(nav - daily.nav))), "trade_events": len(trade),
            "future_trades": int(trade.kind.eq("future").sum()),
            "option_trades": int(trade.kind.eq("option").sum()),
            "fee": fee, "fee_reconciliation_error": abs(fee - daily.fees_cumulative.iloc[-1])}


def main() -> None:
    assert hashlib.sha256(RAW.read_bytes()).hexdigest() == "40ede7733d0be851016a7749a7d864fdc3b1d4003241c0fb18d46f76af943d32"
    raw = pd.read_csv(RAW, parse_dates=["date"]).set_index("date")
    sleeve = pd.read_csv(HERE / "daily_paths.csv")
    account = pd.read_csv(HERE / "account_daily_paths.csv.gz")
    events = pd.read_csv(HERE / "account_events.csv.gz")
    grid = pd.read_csv(HERE / "account_grid_signals.csv")
    names = ("FIX10_full", "FIX11_full")
    result = {"sleeve": {}, "account": {}, "grid_equal": False}
    for name in names:
        result["sleeve"][name] = check_sleeve(name, sleeve.loc[sleeve.arm.eq(name)], raw)
        result["account"][name] = check_account(name, account.loc[account.arm.eq(name)],
                                                events.loc[events.arm.eq(name)])
    left = grid.loc[grid.arm.eq(names[0])].drop(columns="arm").reset_index(drop=True)
    right = grid.loc[grid.arm.eq(names[1])].drop(columns="arm").reset_index(drop=True)
    assert left.equals(right) and len(left) == 24
    result["grid_equal"] = True
    (HERE / "independent_audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
