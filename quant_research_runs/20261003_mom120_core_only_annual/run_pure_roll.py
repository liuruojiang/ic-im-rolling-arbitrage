"""Same-calendar, same-engine 1x futures-only rolling baseline for IC and IM."""
from __future__ import annotations

import argparse
import calendar
import json
import sys
from datetime import date

import numpy as np
import pandas as pd

from run_core_only import HERE, ROOT, MODEL_SOURCE, NATIVE, STUDY, REFERENCE, FIX11, digest, load

MODEL = ROOT / "quant_param_scan_runs/20260928_fix9_no_momentum_futures_no_grid_model_extension_v1"
ENGINE = STUDY / "native_account.py"


def run(layer: str) -> None:
    sys.path[:0] = [str(MODEL_SOURCE), str(MODEL), str(REFERENCE), str(NATIVE), str(STUDY), str(ROOT)]
    import run_study as study  # noqa: E402
    from native_fix4_historical_sources_v1 import formal

    runner = load(f"pure_roll_config_{layer}", REFERENCE / "run_candidate.py")
    engine = load(f"pure_roll_engine_{layer}", ENGINE)
    if layer == "model":
        from model_source_v3 import ListedCorrectedModelSources

        source = ListedCorrectedModelSources()
        model_dates = pd.DatetimeIndex(source.ic_market.index)
        original_third_friday = formal._third_friday

        def model_third_friday(year: int, month: int) -> date:
            weeks = calendar.monthcalendar(year, month)
            fridays = [week[calendar.FRIDAY] for week in weeks if week[calendar.FRIDAY]]
            civil = pd.Timestamp(year, month, fridays[2])
            if model_dates.min() <= civil <= model_dates.max():
                position = model_dates.searchsorted(civil)
                if position >= len(model_dates):
                    raise RuntimeError("model expiry outside synthetic calendar")
                return model_dates[position].date()
            return original_third_friday(year, month)

        formal._third_friday = model_third_friday
        folder = HERE / "latest_model_inputs"
        candidates = pd.read_csv(folder / "native_fix4_seller_candidates_v1.csv.gz")
        lifecycles = {p: pd.read_csv(folder / "native_fix4_seller_lifecycle_v4.csv.gz") for p in ("IC", "IM")}
        expected = pd.read_csv(HERE / "model_latest_full_daily.csv.gz")
    else:
        source = study.base.DatedSources()
        candidates = pd.read_csv(study.NATIVE / "native_fix4_seller_candidates_v1.csv.gz")
        ic_lifecycle, _ = runner.build_fix9_ic_lifecycle(
            source, candidates,
            pd.read_csv(ROOT / "data/ic_im_valuation_risk_premium_forecast_v4/chinabond_government_10y.csv",
                        parse_dates=["date"]).set_index("date").gov10y_yield,
            pd.read_csv(study.base.OHLCV["IM"], parse_dates=["date"]).set_index("date").close,
        )
        lifecycles = {"IC": ic_lifecycle,
                      "IM": pd.read_csv(STUDY / "input_variants/im/formal/native_fix4_seller_lifecycle_v4.csv.gz")}
        expected = pd.concat((
            pd.read_csv(FIX11 / "account_daily_paths.csv.gz").query("arm == 'FIX11_full' and product == 'IC'"),
            pd.read_csv(HERE / "real_no_grid_daily.csv.gz").query("product == 'IM'"),
        ), ignore_index=True)
    gov = pd.read_csv(ROOT / "data/ic_im_valuation_risk_premium_forecast_v4/chinabond_government_10y.csv",
                      parse_dates=["date"]).set_index("date").gov10y_yield
    spot = pd.read_csv(study.base.OHLCV["IM"], parse_dates=["date"]).set_index("date").close
    parts, event_parts, audits = [], [], {}
    for product in ("IC", "IM"):
        input_folder = (folder if layer == "model" else
                        FIX11 / "account_inputs/FIX11_full" if product == "IC" else
                        HERE / "real_im_fear50_inputs")
        runner.configure_engine(engine, input_folder)
        engine.HERE = input_folder
        engine.DISABLED_MODULES = frozenset({"buyer_put", "momentum", "grid", "call"})
        engine.CORE_UNITS = 1.0
        engine.BUYER_OPEN_SLEEVES = frozenset()
        life = lifecycles[product].copy()
        life.loc[:, "route_before"] = "future"
        life.loc[:, "route_after_execution"] = "future"
        life.loc[:, "action"] = "HOLD"
        daily, events, _ = engine.replay(product, "repeat_roll", source, life, candidates, gov, spot)
        reference = expected.loc[expected["product"].eq(product)].reset_index(drop=True)
        if daily.date.astype(str).tolist() != reference.date.astype(str).tolist():
            raise RuntimeError(f"{layer}/{product}: pure-roll calendar mismatch")
        trades = events.loc[events.event.eq("trade")].symbol.fillna("").astype(str)
        if trades.empty or not trades.str.startswith("future|core|").all():
            raise RuntimeError(f"{layer}/{product}: non-core-future trade in pure roll")
        if (daily.option_value.abs().max() > 1e-9 or daily.etf_value.abs().max() > 1e-9 or
                daily.seller_buffer.abs().max() > 1e-9 or daily.call_buffer.abs().max() > 1e-9):
            raise RuntimeError(f"{layer}/{product}: unexpected option or ETF exposure")
        nav_error = float(np.max(np.abs(np.cumprod(1 + daily.return_net.to_numpy(float)) - daily.nav.to_numpy(float))))
        min_free_cash = float((daily.cash_close - daily.margin_reserved).min())
        if nav_error > 2e-9 or min_free_cash < -1e-7:
            raise RuntimeError(f"{layer}/{product}: pure-roll account identity failed")
        daily.insert(0, "data_layer", layer)
        parts.append(daily)
        events.insert(0, "product", product)
        events.insert(0, "data_layer", layer)
        event_parts.append(events)
        audits[product] = {"start": str(daily.date.iloc[0]), "end": str(daily.date.iloc[-1]),
                           "rows": int(len(daily)), "future_core_trades": int(len(trades)),
                           "option_trades": 0, "nav_identity_max_error": nav_error,
                           "min_free_cash": min_free_cash,
                           "return_full_period": float(daily.nav.iloc[-1] - 1),
                           "input_folder": str(input_folder.relative_to(ROOT))}
        print(json.dumps({"layer": layer, "product": product, **audits[product]}, ensure_ascii=False), flush=True)
    pd.concat(parts, ignore_index=True).to_csv(HERE / f"{layer}_pure_roll_daily.csv.gz", index=False, compression="gzip")
    pd.concat(event_parts, ignore_index=True).to_csv(HERE / f"{layer}_pure_roll_events.csv.gz", index=False, compression="gzip")
    (HERE / f"{layer}_pure_roll_audit.json").write_text(json.dumps({
        "classification": "research_only_1x_quarter_roll_no_options_no_momentum_no_grid",
        "engine_sha256": digest(ENGINE), "audits": audits,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--layer", choices=("real", "model"), required=True)
    run(parser.parse_args().layer)
