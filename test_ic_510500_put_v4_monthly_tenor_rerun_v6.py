from pathlib import Path

import pandas as pd

import ic_510500_put_full_cycle_valuation_v2 as v2
import ic_510500_put_v4_monthly_tenor_rerun_v6 as research


def test_frozen_spec_dependencies_and_grid() -> None:
    assert research.sha256(research.SPEC) == research.SPEC_SHA256
    assert (
        research.SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0]
        == research.SPEC_SHA256
    )
    assert research.sha256(research.V4_PATH) == research.V4_SHA256
    assert research.sha256(research.V3_PATH) == research.V3_SHA256
    assert research.sha256(research.PROXY_PATH) == research.PROXY_SHA256
    assert len(research.GRID_VARIANTS) == 36
    assert len(research.ALL_GRID_VARIANTS) == 37
    candidates = {
        f"{layer}_{variant}"
        for layer in ["model", "real"]
        for variant in research.ALL_GRID_VARIANTS
    }
    assert len(candidates) == 74


def test_candidate_parser_keeps_tenor_and_v4_signal_separate() -> None:
    parts = research.candidate_parts("model_3m_monthly_econ_m90_l50_h90")
    assert parts["layer"] == "model"
    assert parts["tenor"] == "3m_monthly"
    assert parts["signal_variant"] == "econ_m90_l50_h90"
    assert parts["window_months"] == 90
    assert parts["lower_risk"] == 0.5
    assert parts["full_risk"] == 0.9


def test_target_expiry_selection_orders_two_and_three_month_tenors() -> None:
    trade_dates = pd.bdate_range("2024-01-01", "2024-12-31")
    day = pd.Timestamp("2024-01-19")
    two_month = research.desired_model_month(day, "2m_monthly", trade_dates)
    three_month = research.desired_model_month(day, "3m_monthly", trade_dates)
    two_expiry = research.proxy.fourth_wednesday(two_month, trade_dates)
    three_expiry = research.proxy.fourth_wednesday(three_month, trade_dates)
    assert two_expiry > day
    assert three_expiry >= two_expiry
    listed = research.proxy.model_listed_months(day, trade_dates)
    for tenor, selected_expiry in [
        ("2m_monthly", two_expiry),
        ("3m_monthly", three_expiry),
    ]:
        target = day + pd.DateOffset(months=research.TENOR_MONTHS[tenor])
        distances = [
            abs((research.proxy.fourth_wednesday(month, trade_dates) - target).days)
            for month in listed
        ]
        assert abs((selected_expiry - target).days) == min(distances)


def test_model_permanent_paths_roll_on_every_frozen_ic_roll_date() -> None:
    frames = v2.load_inputs()
    ic = frames["ic"]
    roll_dates = research.forced_roll_dates(ic)
    market = ic[["date", "settle"]].copy()
    market["spot_open"] = 100.0
    market["spot_close"] = 100.0
    market["sigma_open"] = 0.25
    market["sigma_close"] = 0.25
    market["rate_open"] = 0.02
    market["rate_close"] = 0.02
    market["dividend_open"] = 0.01
    market["dividend_close"] = 0.01
    schedule = pd.DataFrame(
        [
            {
                "layer": "model",
                "frequency": "daily",
                "eval_date": research.core.MODEL_START,
                "execution_date": research.core.MODEL_START,
                "three_tier_target_fraction": 1.0,
            }
        ]
    )
    for tenor in research.MONTHLY_TENORS:
        _, trades = research.run_model_monthly_tenor(
            ic,
            schedule,
            market,
            tenor,
            f"model_{tenor}_always_100",
            roll_dates,
        )
        rolls = trades[trades["action"].eq("open_roll_monthly")]
        applicable = {
            day
            for day in roll_dates
            if research.core.MODEL_START <= day <= research.core.END
        }
        assert set(pd.to_datetime(rolls["roll_request_date"])) == applicable
        assert rolls["delay_trading_days"].eq(0).all()


def test_output_audits_after_optional_formal_run() -> None:
    assert isinstance(research.OUTPUT, Path)
    if research.OUTPUT.exists():
        assert (research.OUTPUT / "v4_front_parity.csv").exists()
        assert (research.OUTPUT / "monthly_roll_audit.csv").exists()
        assert (research.OUTPUT / "period_attribution.csv").exists()
        daily = pd.read_csv(
            research.OUTPUT / "daily_candidates.csv.gz", parse_dates=["date"]
        )
        assert daily["candidate"].nunique() == 74
        assert not daily.duplicated(["candidate", "date"]).any()
        assert not daily[["ret", "cash_ret"]].isna().any().any()
        assert (daily[["ret", "cash_ret"]] > -1.0).all().all()
        parity = pd.read_csv(research.OUTPUT / "v4_front_parity.csv")
        diff_columns = [column for column in parity if column.startswith("max_abs_")]
        assert parity[diff_columns].to_numpy().max() <= 1e-14
        roll_audit = pd.read_csv(research.OUTPUT / "monthly_roll_audit.csv")
        assert roll_audit["passed"].all()
        assert roll_audit["completion_ratio"].eq(1.0).all()
        assert roll_audit["max_delay_trading_days"].le(5).all()
