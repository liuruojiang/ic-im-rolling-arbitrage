from pathlib import Path

import pandas as pd

import ic_510500_put_full_cycle_valuation_v2 as v2
import ic_510500_put_persistent_stress_hold3m_v7 as research


def _constant_model_market(ic: pd.DataFrame) -> pd.DataFrame:
    market = ic[["date", "settle"]].copy()
    market["spot_open"] = 100.0
    market["spot_close"] = 100.0
    market["sigma_open"] = 0.25
    market["sigma_close"] = 0.25
    market["rate_open"] = 0.02
    market["rate_close"] = 0.02
    market["dividend_open"] = 0.01
    market["dividend_close"] = 0.01
    return market


def _schedule(events: list[tuple[pd.Timestamp, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "layer": "model",
                "frequency": "daily",
                "eval_date": day,
                "execution_date": day,
                "three_tier_target_fraction": target,
            }
            for day, target in events
        ]
    )


def test_frozen_spec_dependencies_and_grid() -> None:
    assert research.sha256(research.SPEC) == research.SPEC_SHA256
    assert (
        research.SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0]
        == research.SPEC_SHA256
    )
    assert research.sha256(research.V5_PATH) == research.V5_SHA256
    assert research.sha256(research.V6_PATH) == research.V6_SHA256
    assert research.sha256(research.V3_PATH) == research.V3_SHA256
    assert research.sha256(research.PROXY_PATH) == research.PROXY_SHA256
    assert len(research.GRID_VARIANTS) == 12
    assert len(
        {
            f"{layer}_{variant}"
            for layer in ["model", "real"]
            for variant in research.ALL_GRID_VARIANTS
        }
    ) == 26


def test_stress_latch_is_causal_and_obeys_frozen_state_rules() -> None:
    frames = v2.load_inputs()
    daily, _ = v2.build_daily_valuation_full(
        frames["states_full"], frames["states_legacy"]
    )
    full = research._latch_frame(daily)
    cutoff = pd.Timestamp("2024-02-05")
    prefix = research._latch_frame(daily[daily["date"] <= cutoff])
    columns = [
        "latch_armed",
        "latch_carried_low_stress",
        "latch_target_fraction",
    ]
    left = full.loc[full["date"].eq(cutoff), columns].iloc[0]
    right = prefix.loc[prefix["date"].eq(cutoff), columns].iloc[0]
    assert left.to_dict() == right.to_dict()
    carried = full[full["latch_carried_low_stress"]]
    assert len(carried) > 0
    assert carried["latch_target_fraction"].eq(1.0).all()
    low_no_stress = full.apply(
        lambda row: research.v5.absolute_state(row)["valuation_state"] == "low"
        and not bool(row["stress"]),
        axis=1,
    )
    assert full.loc[low_no_stress, "latch_target_fraction"].eq(0.0).all()


def test_actual_signal_schedule_and_current_state() -> None:
    frames = v2.load_inputs()
    daily, _ = v2.build_daily_valuation_full(
        frames["states_full"], frames["states_legacy"]
    )
    schedule, signals, current, _, stats = research.build_signal_panel(
        frames["ic"], daily
    )
    assert set(schedule["signal_variant"]) == set(research.SIGNAL_MODES)
    assert set(signals["signal_variant"]) == set(research.SIGNAL_MODES)
    regular = schedule[~schedule["initial_exception"]]
    assert (regular["execution_date"] > regular["eval_date"]).all()
    assert not schedule.duplicated(
        ["layer", "signal_variant", "execution_date"]
    ).any()
    observed = current.set_index("signal_variant")[
        "research_target_fraction"
    ].to_dict()
    assert observed == {"v5_original": 1.0, "stress_latch": 1.0}
    assert stats["all_carried_days_full_target"]
    assert stats["all_low_no_stress_days_zero"]


def test_hold_to_expiry_permanent_path_covers_three_ic_rolls() -> None:
    frames = v2.load_inputs()
    ic = frames["ic"]
    roll_dates = research.v6.forced_roll_dates(ic)
    schedule = _schedule([(research.core.MODEL_START, 1.0)])
    daily, trades, lifecycles = research.run_model_hold_expiry(
        ic,
        schedule,
        _constant_model_market(ic),
        "model_3m_hold_expiry_always_100",
        roll_dates,
    )
    completed = lifecycles[
        lifecycles["completed"]
        & (pd.to_datetime(lifecycles["entry_date"]) > research.core.MODEL_START)
    ]
    assert len(completed) > 0
    assert completed["ic_rolls_covered"].eq(3).all()
    assert not trades["early_exit"].any()
    assert set(trades["action"]).issubset({"open_buy", "open_renewal"})
    assert daily["expired"].sum() == len(lifecycles[lifecycles["completed"]])


def test_hold_path_does_not_sell_when_signal_falls_to_zero() -> None:
    frames = v2.load_inputs()
    ic = frames["ic"]
    roll_dates = research.v6.forced_roll_dates(ic)
    start = research.core.MODEL_START
    zero_day = pd.Timestamp("2015-05-04")
    schedule = _schedule([(start, 1.0), (zero_day, 0.0)])
    daily, trades, lifecycles = research.run_model_hold_expiry(
        ic,
        schedule,
        _constant_model_market(ic),
        "model_3m_hold_expiry_test",
        roll_dates,
    )
    first_expiry = pd.Timestamp(lifecycles.iloc[0]["expiry"])
    retained = daily[daily["date"].between(zero_day, first_expiry)]
    assert retained["signal_target_fraction"].eq(0.0).all()
    assert retained["target_fraction"].eq(1.0).all()
    assert not trades["action"].isin(["open_exit", "open_resize"]).any()


def test_formal_output_is_absent_or_tied_to_v7() -> None:
    assert isinstance(research.OUTPUT, Path)
    if research.OUTPUT.exists():
        assert (research.OUTPUT / "baseline_parity.csv").exists()
        assert (research.OUTPUT / "hold_expiry_lifecycle_audit.csv").exists()
        daily = pd.read_csv(
            research.OUTPUT / "daily_candidates.csv.gz", parse_dates=["date"]
        )
        assert daily["candidate"].nunique() == 26
        assert not daily.duplicated(["candidate", "date"]).any()
        assert not daily[["ret", "cash_ret"]].isna().any().any()
        parity = pd.read_csv(research.OUTPUT / "baseline_parity.csv")
        diff_columns = [column for column in parity if column.startswith("max_abs_")]
        assert parity[diff_columns].to_numpy().max() <= 1e-14
        lifecycle = pd.read_csv(
            research.OUTPUT / "hold_expiry_lifecycle_audit.csv"
        )
        assert lifecycle["passed"].all()
        assert lifecycle["early_exits"].eq(0).all()
        manifest = pd.read_json(research.OUTPUT / "data_manifest.json", typ="series")
        assert manifest["version"] == research.VERSION
        assert manifest["spec_sha256"] == research.SPEC_SHA256
        assert manifest["script_sha256"] == research.sha256(Path(research.__file__))
