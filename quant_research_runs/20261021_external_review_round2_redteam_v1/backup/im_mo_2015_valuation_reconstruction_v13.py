from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import im_mo_adaptive_valuation_mom120_floor_v12 as v12
import im_mo_adaptive_valuation_tier_put_v10 as v10
import im_mo_close_execution_v8 as v8
import im_mo_csi1000_put_protection_battery_v6 as v6


ROOT = Path(__file__).resolve().parent
VERSION = "im_mo_2015_valuation_reconstruction_v13"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "e5eadc1aa7fba471ae937b9242fef2fbb2610546516ebfa0ace4572d2f9b4574"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}.staging"

DATA = ROOT / "data" / "ic_im_valuation_risk_premium_forecast_v4"
VALUATION = DATA / "legulegu_000852_valuation.csv"
OFFICIAL = DATA / "csindex_000852.csv"
GOV10Y = DATA / "chinabond_government_10y.csv"
V12_DAILY = ROOT / "outputs" / "im_mo_adaptive_valuation_mom120_floor_v12" / "daily_candidates.csv.gz"
V12_SCHEDULES = ROOT / "outputs" / "im_mo_adaptive_valuation_mom120_floor_v12" / "signal_schedules.csv.gz"

INPUT_HASHES = {
    VALUATION: "2967d0d85cb9678e7db13544f9c64304c5b1cedf41f8605b7648f1e6bae8c475",
    OFFICIAL: "2022d89da20cb4e81e63c82999ed1deb2488353199d3f40fa0f1f7d44401dd89",
    GOV10Y: "84ac6df41432c850ab29748f3bb36eb5c7fd99c40872bd7cb472d2f851d40661",
    V12_DAILY: "5a75a9a6aa15d56f023a77b336b660f010b1f87834985385295bfba51e29ba9c",
    V12_SCHEDULES: "f4928e0175cc6ca698ccbc7c31dfc471c66d13fb1f0030cf3c6bf3f8d6c29ef4",
    ROOT / "im_mo_adaptive_valuation_mom120_floor_v12.py": "4690d00d05751c321ad84096939511f3aeb1846c786bcd146b75815b9cfabee0",
    ROOT / "im_mo_close_execution_v8.py": "4ac38a47dac471bcaea77e817f6d74a5fe8ccb65484aa79a4844c80b2226eace",
    ROOT / "im_mo_csi1000_put_protection_battery_v6.py": "7a1043bc5add7bb7d7f09e448dd715715befe08e2ce42dbcf36af849f7999f3d",
}

CUTOVER_EVAL = pd.Timestamp("2015-10-19")
EARLY_START = pd.Timestamp("2014-10-17")
MODEL_START = pd.Timestamp("2015-04-16")
PEAK_2015 = pd.Timestamp("2015-06-12")
PRECRASH_START = pd.Timestamp("2015-04-01")

THRESHOLDS = {1: 2.45, 2: 2.50, 3: 2.60}
CANDIDATE_MAP = {
    "v12_valmom_floor2": "valmom_center_floor2",
    "v12_valmom_floor3": "valmom_center_floor3",
}
EXTENDED = {2: "reconstructed_valmom_floor2", 3: "reconstructed_valmom_floor3"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_inputs(*, require_fresh_output: bool) -> None:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen v13 specification hash mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Frozen v13 specification sidecar mismatch")
    for path, expected in INPUT_HASHES.items():
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(f"Frozen v13 input changed: {path.name}: {actual}")
    if require_fresh_output and (OUTPUT.exists() or STAGING.exists()):
        raise FileExistsError("Formal v13 output or staging directory already exists")


def certify_tier(pb: pd.Series, erp: pd.Series) -> pd.Series:
    result = pd.Series(0, index=pb.index, dtype=int)
    for tier, threshold in THRESHOLDS.items():
        pb_limit = 1.50 + 0.50 * threshold
        erp_limit = 0.045 - 0.015 * threshold
        result.loc[pb.ge(pb_limit - 1e-12) & erp.le(erp_limit + 1e-12)] = tier
    return result


def build_early_valuation() -> tuple[pd.DataFrame, dict[str, Any]]:
    valuation = pd.read_csv(VALUATION, parse_dates=["date"])
    official = pd.read_csv(OFFICIAL, parse_dates=["date"]).rename(
        columns={"close": "official_close"}
    )
    gov = pd.read_csv(GOV10Y, parse_dates=["date"]).rename(
        columns={"date": "gov10y_date"}
    )
    daily = valuation.merge(
        official[["date", "official_close", "official_rolling_pe"]],
        on="date",
        how="inner",
        validate="one_to_one",
    ).sort_values("date")
    daily = pd.merge_asof(
        daily,
        gov.sort_values("gov10y_date"),
        left_on="date",
        right_on="gov10y_date",
        direction="backward",
        allow_exact_matches=True,
    )
    daily = daily[daily["date"].between(EARLY_START, CUTOVER_EVAL - pd.Timedelta(days=1))].copy()
    if daily.empty or daily["official_rolling_pe"].isna().any():
        raise RuntimeError("Early official PE history is incomplete")
    daily["gov10y_staleness_days"] = (daily["date"] - daily["gov10y_date"]).dt.days
    daily["future_gov_row"] = daily["gov10y_date"].gt(daily["date"])
    daily["close_relative_error"] = (
        daily["close_pe_source"] / daily["official_close"] - 1.0
    ).abs()
    daily["official_erp"] = 1.0 / daily["official_rolling_pe"] - daily["gov10y_yield"]
    daily["third_party_erp"] = 1.0 / daily["pe_aggregate_ttm"] - daily["gov10y_yield"]
    daily["pb_pressure"] = (daily["pb_aggregate"] - 1.50) / 0.50
    daily["official_erp_pressure"] = (0.045 - daily["official_erp"]) / 0.015
    daily["certified_tier"] = certify_tier(daily["pb_aggregate"], daily["official_erp"])
    daily["tier3_pb_margin"] = daily["pb_aggregate"] - (1.50 + 0.50 * 2.60)
    daily["tier3_erp_margin"] = (0.045 - 0.015 * 2.60) - daily["official_erp"]
    daily["pe_relative_difference"] = (
        daily["pe_aggregate_ttm"] / daily["official_rolling_pe"] - 1.0
    )
    if daily["future_gov_row"].any():
        raise RuntimeError("Future ChinaBond row used")
    if float(daily["close_relative_error"].max()) > 1e-12:
        raise RuntimeError("Historical valuation and official close mismatch")
    if daily[["pb_aggregate", "official_rolling_pe", "gov10y_yield"]].le(0).any().any():
        raise RuntimeError("Non-positive early valuation input")
    audit = {
        "rows": int(len(daily)),
        "start": daily["date"].min().date().isoformat(),
        "end": daily["date"].max().date().isoformat(),
        "future_gov_rows": int(daily["future_gov_row"].sum()),
        "max_gov10y_staleness_days": int(daily["gov10y_staleness_days"].max()),
        "max_close_relative_error": float(daily["close_relative_error"].max()),
        "max_abs_pe_relative_difference": float(daily["pe_relative_difference"].abs().max()),
    }
    return daily.reset_index(drop=True), audit


def load_v12_schedule(candidate: str) -> pd.DataFrame:
    schedules = pd.read_csv(
        V12_SCHEDULES,
        parse_dates=["eval_date", "execution_date"],
        low_memory=False,
    )
    result = schedules[
        schedules["layer"].eq("model")
        & schedules["schedule_candidate"].eq(candidate)
    ].copy()
    if result.empty or result.duplicated("execution_date").any():
        raise RuntimeError(f"Invalid v12 schedule: {candidate}")
    return result.sort_values("execution_date").reset_index(drop=True)


def build_extended_schedule(
    floor: int, early: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, Any]]:
    base_name = f"valmom_center_floor{floor}"
    schedule = load_v12_schedule(base_name)
    schedule["v12_target_qty"] = schedule["binary_target_qty"].astype(int)
    early_tier = early.set_index("date")["certified_tier"]
    pre = schedule["eval_date"].lt(CUTOVER_EVAL)
    mapped = schedule.loc[pre, "eval_date"].map(early_tier)
    if mapped.isna().any():
        missing = schedule.loc[pre & schedule["eval_date"].map(early_tier).isna(), "eval_date"]
        raise RuntimeError(f"Missing early valuation dates: {missing.head().tolist()}")
    schedule["reconstructed_certified_tier"] = 0
    schedule.loc[pre, "reconstructed_certified_tier"] = mapped.astype(int).to_numpy()
    schedule["binary_target_qty"] = np.maximum(
        schedule["v12_target_qty"], schedule["reconstructed_certified_tier"]
    ).astype(int)
    schedule["three_tier_target_qty"] = schedule["binary_target_qty"]
    schedule["candidate"] = EXTENDED[floor]
    schedule["schedule_candidate"] = EXTENDED[floor]
    schedule["source_state"] = f"v12_plus_pre20151019_pb_official_erp_certified_floor{floor}"
    post = schedule["eval_date"].ge(CUTOVER_EVAL)
    post_errors = int(
        schedule.loc[post, "binary_target_qty"].ne(
            schedule.loc[post, "v12_target_qty"]
        ).sum()
    )
    if post_errors:
        raise RuntimeError("Post-cutover v12 target changed")
    audit = {
        "floor": floor,
        "pre_cutover_rows": int(pre.sum()),
        "pre_cutover_changed_rows": int(
            schedule.loc[pre, "binary_target_qty"].ne(
                schedule.loc[pre, "v12_target_qty"]
            ).sum()
        ),
        "post_cutover_target_errors": post_errors,
        "first_positive_execution": schedule.loc[
            schedule["binary_target_qty"].gt(0), "execution_date"
        ].min().date().isoformat(),
    }
    return schedule, audit


def drawdown_details(returns: pd.Series, dates: pd.Series) -> dict[str, Any]:
    wealth = (1.0 + returns.astype(float)).cumprod()
    drawdown = wealth / wealth.cummax() - 1.0
    trough_index = drawdown.idxmin()
    peak_index = wealth.loc[:trough_index].idxmax()
    result = v6.metrics(returns)
    result.update(
        {
            "peak_date": pd.Timestamp(dates.loc[peak_index]).date().isoformat(),
            "trough_date": pd.Timestamp(dates.loc[trough_index]).date().isoformat(),
        }
    )
    return result


def metrics_table(daily: pd.DataFrame) -> pd.DataFrame:
    end = pd.Timestamp(daily["date"].max())
    windows = {
        "full": MODEL_START,
        "post_mom_first_entry": pd.Timestamp("2015-09-02"),
        "post_valuation_cutover": pd.Timestamp("2015-10-20"),
        "last_10y": end - pd.DateOffset(years=10),
        "last_5y": end - pd.DateOffset(years=5),
        "last_3y": end - pd.DateOffset(years=3),
        "last_1y": end - pd.DateOffset(years=1),
    }
    rows: list[dict[str, Any]] = []
    for candidate, group in daily.groupby("candidate", sort=True):
        group = group.sort_values("date")
        for window, start in windows.items():
            sample = group[group["date"].ge(start)].copy()
            values = drawdown_details(sample["cash_ret"], sample["date"])
            rows.append(
                {
                    "candidate": candidate,
                    "window": window,
                    "requested_start": start.date().isoformat(),
                    "actual_start": sample["date"].min().date().isoformat(),
                    "end": sample["date"].max().date().isoformat(),
                    "rows": int(len(sample)),
                    **values,
                }
            )
    return pd.DataFrame(rows)


def annual_table(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (candidate, year), group in daily.groupby(
        ["candidate", daily["date"].dt.year], sort=True
    ):
        values = drawdown_details(group["cash_ret"], group["date"])
        rows.append({"candidate": candidate, "year": int(year), **values})
    return pd.DataFrame(rows)


def event_table(daily: pd.DataFrame) -> pd.DataFrame:
    event_dates = [
        "2015-06-12",
        "2015-09-02",
        "2016-01-28",
        "2018-10-18",
        "2019-01-31",
    ]
    rows: list[dict[str, Any]] = []
    for candidate, group in daily.groupby("candidate", sort=True):
        group = group.sort_values("date").set_index("date")
        peak_nav = float(group.loc[PEAK_2015, "cash_nav"])
        for text_date in event_dates:
            day = pd.Timestamp(text_date)
            rows.append(
                {
                    "candidate": candidate,
                    "date": text_date,
                    "cash_nav": float(group.loc[day, "cash_nav"]),
                    "return_since_2015_06_12": float(
                        group.loc[day, "cash_nav"] / peak_nav - 1.0
                    ),
                    "put_fraction": float(group.loc[day, "put_fraction"]),
                }
            )
    return pd.DataFrame(rows)


def make_record(
    early: pd.DataFrame,
    early_audit: dict[str, Any],
    schedule_audits: list[dict[str, Any]],
    metrics: pd.DataFrame,
    events: pd.DataFrame,
    decision: dict[str, Any],
    parity: dict[str, float],
) -> str:
    focus = metrics[
        metrics["candidate"].isin(
            ["no_put", *CANDIDATE_MAP, *EXTENDED.values()]
        )
        & metrics["window"].isin(
            ["full", "post_valuation_cutover", "last_10y", "last_5y"]
        )
    ][["candidate", "window", "ann_return", "max_dd", "peak_date", "trough_date"]]
    june = early[early["date"].eq(PEAK_2015)].iloc[0]
    precrash = early[early["date"].between(PRECRASH_START, PEAK_2015)]
    event_focus = events[events["date"].isin(["2015-06-12", "2015-09-02", "2018-10-18", "2019-01-31"])]
    return "\n".join(
        [
            f"# {VERSION} 正式记录",
            "",
            "> 历史诊断；早期PB为2026年取得的历史序列，不是2015年保存的vintage快照；未批准实盘。",
            "",
            "## Decision",
            "",
            f"- Conclusion: `{decision['conclusion']}`.",
            f"- User memory confirmed: `{decision['memory_confirmed']}`.",
            f"- Full-sample boundary explained: `{decision['full_sample_boundary_explained']}`.",
            "",
            "## 2015 Valuation Reconstruction",
            "",
            f"- 2015-06-12 official PE {june.official_rolling_pe:.2f}, third-party aggregate PE {june.pe_aggregate_ttm:.2f}, PB {june.pb_aggregate:.2f}, official-PE ERP {june.official_erp:.2%}.",
            f"- Tier-3 limits: PB >= 2.80 and ERP <= 0.60%; observed margins were {june.tier3_pb_margin:.2f} PB turns and {june.tier3_erp_margin:.2%} ERP.",
            f"- 2015-04-01 to 2015-06-12 certified tier-3 days: {int(precrash['certified_tier'].eq(3).sum())}/{len(precrash)} ({precrash['certified_tier'].eq(3).mean():.2%}).",
            f"- Data audit: `{json.dumps(early_audit, ensure_ascii=False)}`.",
            "",
            "## Metrics",
            "",
            focus.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## 2015 Peak-Aligned Events",
            "",
            event_focus.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## Integrity",
            "",
            f"- Schedule audit: `{json.dumps(schedule_audits, ensure_ascii=False)}`.",
            f"- Parity: `{json.dumps(parity, ensure_ascii=False)}`.",
            "- Theoretical Put, T close signal and T+1 close execution are unchanged from v12; no real IM/MO layer was rerun because the reconstruction ends before listed IM/MO history.",
            "",
        ]
    )


def main() -> None:
    verify_inputs(require_fresh_output=True)
    early, early_audit = build_early_valuation()
    schedules: dict[int, pd.DataFrame] = {}
    schedule_audits: list[dict[str, Any]] = []
    for floor in [2, 3]:
        schedules[floor], audit = build_extended_schedule(floor, early)
        schedule_audits.append(audit)

    market, market_checks = v6.model_market()
    model_base = v6.model_baseline(market)
    overlays: dict[str, pd.DataFrame] = {}
    trade_parts: list[pd.DataFrame] = []
    life_parts: list[pd.DataFrame] = []
    for floor, label in EXTENDED.items():
        overlay, trades, lives = v8.run_model_normal_close(
            market, schedules[floor], "3m", 0.95, label
        )
        overlays[label] = overlay
        if len(trades):
            trade_parts.append(trades)
        if len(lives):
            copy = lives.copy()
            copy["layer"] = "model"
            life_parts.append(copy)
    reconstructed = v10.add_nav(v6.assemble_layer("model", model_base, overlays))

    source_daily = pd.read_csv(V12_DAILY, parse_dates=["date"])
    source_daily = source_daily[source_daily["layer"].eq("model")].copy()
    baseline_parts: list[pd.DataFrame] = []
    no_put = source_daily[source_daily["candidate"].eq("no_put")].copy()
    baseline_parts.append(no_put)
    for new_name, old_name in CANDIDATE_MAP.items():
        part = source_daily[source_daily["candidate"].eq(old_name)].copy()
        part["candidate"] = new_name
        baseline_parts.append(part)
    baselines = pd.concat(baseline_parts, ignore_index=True)
    generated_no_put = reconstructed[reconstructed["candidate"].eq("no_put")]
    parity_join = no_put.merge(
        generated_no_put,
        on="date",
        suffixes=("_v12", "_generated"),
        validate="one_to_one",
    )
    parity = {
        column: float(
            (
                parity_join[f"{column}_v12"]
                - parity_join[f"{column}_generated"]
            ).abs().max()
        )
        for column in ["cash_ret", "cash_nav", "ret", "nav"]
    }
    if max(parity.values()) > 1e-14:
        raise RuntimeError(f"v12 no-Put parity failed: {parity}")
    extended_only = reconstructed[reconstructed["candidate"].isin(EXTENDED.values())]
    daily = pd.concat([baselines, extended_only], ignore_index=True).sort_values(
        ["candidate", "date"]
    )
    if daily.duplicated(["candidate", "date"]).any():
        raise RuntimeError("Duplicate v13 candidate/date")

    metrics = metrics_table(daily)
    annual = annual_table(daily)
    events = event_table(daily)
    june = early[early["date"].eq(PEAK_2015)].iloc[0]
    precrash = early[early["date"].between(PRECRASH_START, PEAK_2015)]
    memory_confirmed = bool(
        int(june["certified_tier"]) == 3
        and float(precrash["certified_tier"].eq(3).mean()) >= 0.90
    )
    full = metrics[metrics["window"].eq("full")].set_index("candidate")
    improvements = {
        str(floor): float(
            (
                full.loc[EXTENDED[floor], "max_dd"]
                - full.loc[f"v12_valmom_floor{floor}", "max_dd"]
            )
            * 100.0
        )
        for floor in [2, 3]
    }
    explained = bool(max(improvements.values()) >= 10.0)
    if not memory_confirmed:
        conclusion = "memory_not_confirmed"
    elif explained:
        conclusion = "memory_confirmed_and_v12_full_sample_boundary_explained"
    else:
        conclusion = "memory_confirmed_but_not_main_dd_explanation"
    decision = {
        "conclusion": conclusion,
        "memory_confirmed": memory_confirmed,
        "full_sample_boundary_explained": explained,
        "full_maxdd_improvement_vs_v12_pp": improvements,
        "live_approved": False,
        "research_status": "historical_diagnostic_only",
    }
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    lifecycles = pd.concat(life_parts, ignore_index=True, sort=False)
    schedules_frame = pd.concat(
        [frame.assign(floor_qty=floor) for floor, frame in schedules.items()],
        ignore_index=True,
        sort=False,
    )
    record = make_record(
        early,
        early_audit,
        schedule_audits,
        metrics,
        events,
        decision,
        parity,
    )
    source_hashes = {
        str(path.relative_to(ROOT)): sha256(path)
        for path in [SPEC, Path(__file__), *INPUT_HASHES]
    }
    manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "spec_sha256": SPEC_SHA256,
        "script_sha256": sha256(Path(__file__)),
        "source_hashes": source_hashes,
        "early_valuation_audit": early_audit,
        "model_market_checks": market_checks,
        "schedule_audits": schedule_audits,
        "parity": parity,
        "decision": decision,
        "limitations": [
            "early PB is a 2026-fetched historical series, not a 2015 vintage snapshot",
            "official rolling PE and official close provide independent direction checks",
            "model Put is theoretical; no pre-listing IM/MO is claimed",
            "bid/ask, close impact and capacity are excluded",
            "not live approved",
        ],
    }

    STAGING.mkdir(parents=True, exist_ok=False)
    early.to_csv(STAGING / "early_valuation_reconstruction.csv", index=False)
    schedules_frame.to_csv(
        STAGING / "extended_signal_schedules.csv.gz", index=False, compression="gzip"
    )
    daily.to_csv(STAGING / "daily_candidates.csv.gz", index=False, compression="gzip")
    metrics.to_csv(STAGING / "metrics_by_window.csv", index=False)
    annual.to_csv(STAGING / "annual_metrics.csv", index=False)
    events.to_csv(STAGING / "drawdown_event_metrics.csv", index=False)
    trades.to_csv(STAGING / "trade_audit.csv.gz", index=False, compression="gzip")
    lifecycles.to_csv(STAGING / "lifecycle_audit.csv", index=False)
    (STAGING / "decision_summary.json").write_text(
        json.dumps(decision, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (STAGING / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (STAGING / "record.md").write_text(record, encoding="utf-8")
    (STAGING / "command_log.txt").write_text(
        "python -m pytest -q test_im_mo_2015_valuation_reconstruction_v13.py\n"
        "python im_mo_2015_valuation_reconstruction_v13.py\n",
        encoding="utf-8",
    )
    STAGING.replace(OUTPUT)
    print(json.dumps(decision, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
