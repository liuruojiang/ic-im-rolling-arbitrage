from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from ic_monthly_discount_roll_v1 import _decode_cffex_csv, fetch_csindex, third_friday


ROOT = Path(__file__).resolve().parent
VERSION = "im_monthly_discount_roll_v1"
START_DATE = pd.Timestamp("2022-07-22")
DEFAULT_END_DATE = pd.Timestamp("2026-08-14")
DEFAULT_DATA_DIR = ROOT / "data" / VERSION
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / VERSION
SHARED_CFFEX_CACHE = ROOT / "data" / "ic_monthly_discount_roll_v1" / "cffex_raw"
SPEC_PATH = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_PATH = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
CFFEX_URL = "http://www.cffex.com.cn/sj/historysj/{ym}/zip/{ym}.zip"
CSINDEX_URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
ONE_WAY_COST = 0.0001
TRADING_DAYS = 252
CASH_WEIGHT = 0.70
CASH_ASSET_ANNUAL_RETURN = 0.03
CASH_ASSET_DAILY_RETURN = (1.0 + CASH_ASSET_ANNUAL_RETURN) ** (1.0 / TRADING_DAYS) - 1.0
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) IM-roll-research/1.0"
CONTRACT_RE = re.compile(r"^IM(?P<yy>\d{2})(?P<mm>\d{2})$")


@dataclass(frozen=True)
class Cycle:
    contract: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    expected_expiry: pd.Timestamp
    complete: bool


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_frozen_spec() -> str:
    if not SPEC_PATH.exists() or not SPEC_HASH_PATH.exists():
        raise FileNotFoundError("Frozen IM specification or its SHA-256 file is missing")
    expected = SPEC_HASH_PATH.read_text(encoding="utf-8").split()[0].lower()
    actual = sha256_file(SPEC_PATH)
    if expected != actual:
        raise RuntimeError(f"Frozen specification hash mismatch: expected {expected}, actual {actual}")
    return actual


def month_starts(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    first = start.normalize().replace(day=1)
    last = end.normalize().replace(day=1)
    return list(pd.date_range(first, last, freq="MS"))


def download_cffex_months(
    data_dir: Path,
    end_date: pd.Timestamp,
    refresh: bool,
) -> tuple[list[Path], pd.DataFrame]:
    raw_dir = data_dir / "cffex_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    paths: list[Path] = []
    log_rows: list[dict[str, object]] = []

    for month_start in month_starts(START_DATE, end_date):
        ym = month_start.strftime("%Y%m")
        path = raw_dir / f"{ym}.zip"
        url = CFFEX_URL.format(ym=ym)
        source = "cache"
        status_code = 200
        if refresh or not path.exists():
            shared_path = SHARED_CFFEX_CACHE / f"{ym}.zip"
            if not refresh and shared_path.exists():
                shutil.copy2(shared_path, path)
                source = "shared_official_cache"
            else:
                response = session.get(url, timeout=60)
                response.raise_for_status()
                path.write_bytes(response.content)
                source = "download"
                status_code = response.status_code

        if not zipfile.is_zipfile(path):
            raise RuntimeError(f"CFFEX response/cache is not a ZIP archive: {path}")
        with zipfile.ZipFile(path) as archive:
            members = [name for name in archive.namelist() if re.fullmatch(r"\d{8}_1\.csv", Path(name).name)]
            if not members:
                raise RuntimeError(f"No daily CSV members in {path}")
            member_dates = sorted(pd.Timestamp(Path(name).name[:8]) for name in members)
        paths.append(path)
        log_rows.append(
            {
                "month": ym,
                "url": url,
                "status_code": status_code,
                "source": source,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "member_count": len(members),
                "first_member_date": member_dates[0].date().isoformat(),
                "last_member_date": member_dates[-1].date().isoformat(),
            }
        )
    return paths, pd.DataFrame(log_rows)


def parse_cffex_im(zip_paths: list[Path], end_date: pd.Timestamp) -> pd.DataFrame:
    fields = {
        0: "contract",
        1: "open",
        2: "high",
        3: "low",
        4: "volume",
        5: "turnover",
        6: "open_interest",
        8: "close",
        9: "settle",
        10: "pre_settle",
    }
    frames: list[pd.DataFrame] = []
    for path in zip_paths:
        with zipfile.ZipFile(path) as archive:
            for member in archive.namelist():
                name = Path(member).name
                match = re.fullmatch(r"(?P<day>\d{8})_1\.csv", name)
                if not match:
                    continue
                trade_date = pd.Timestamp(match.group("day"))
                if trade_date < START_DATE or trade_date > end_date:
                    continue
                raw = pd.read_csv(
                    io.StringIO(_decode_cffex_csv(archive.read(member))),
                    header=None,
                    skiprows=1,
                    dtype=str,
                    on_bad_lines="error",
                )
                if raw.shape[1] < 11:
                    raise RuntimeError(f"Unexpected CFFEX schema in {path.name}/{member}: {raw.shape[1]} columns")
                frame = raw[list(fields)].rename(columns=fields)
                frame["contract"] = frame["contract"].str.strip()
                frame = frame[frame["contract"].str.fullmatch(r"IM\d{4}", na=False)].copy()
                if frame.empty:
                    continue
                frame.insert(1, "date", trade_date)
                for column in fields.values():
                    if column != "contract":
                        frame[column] = pd.to_numeric(
                            frame[column].replace({"--": np.nan, "null": np.nan}), errors="coerce"
                        )
                frames.append(frame)

    if not frames:
        raise RuntimeError("No IM contract rows were parsed from official CFFEX archives")
    futures = pd.concat(frames, ignore_index=True).sort_values(["date", "contract"]).reset_index(drop=True)
    duplicates = futures.duplicated(["date", "contract"], keep=False)
    if duplicates.any():
        sample = futures.loc[duplicates, ["date", "contract"]].head().to_dict("records")
        raise RuntimeError(f"Duplicate CFFEX date-contract rows: {sample}")
    if futures["settle"].isna().any():
        sample = futures.loc[futures["settle"].isna(), ["date", "contract"]].head().to_dict("records")
        raise RuntimeError(f"Missing IM settlement prices: {sample}")
    bad_codes = ~futures["contract"].str.fullmatch(r"IM\d{4}")
    if bad_codes.any():
        raise RuntimeError("Unexpected non-IM contract code after parsing")
    return futures


def contract_month(contract: str) -> pd.Timestamp:
    match = CONTRACT_RE.fullmatch(contract)
    if not match:
        raise ValueError(f"Invalid IM contract: {contract}")
    return pd.Timestamp(year=2000 + int(match.group("yy")), month=int(match.group("mm")), day=1)


def contract_code(month: pd.Timestamp) -> str:
    return f"IM{month.strftime('%y%m')}"


def build_cycles(futures: pd.DataFrame, end_date: pd.Timestamp) -> list[Cycle]:
    front_end_month = end_date.normalize().replace(day=1)
    while third_friday(front_end_month) < end_date:
        front_end_month += pd.offsets.MonthBegin(1)
    months = month_starts(pd.Timestamp("2022-08-01"), front_end_month)
    available = set(futures["contract"].unique())
    required = [contract_code(month) for month in months]
    missing = [contract for contract in required if contract not in available]
    if missing:
        raise RuntimeError(f"Missing required monthly IM contracts: {missing}")

    cycles: list[Cycle] = []
    prior_exit: pd.Timestamp | None = None
    for idx, month in enumerate(months):
        contract = contract_code(month)
        contract_rows = futures[futures["contract"].eq(contract)]
        observed_last = pd.Timestamp(contract_rows["date"].max())
        rule_date = third_friday(month)
        is_last = idx == len(months) - 1
        last_oi = float(contract_rows.loc[contract_rows["date"].eq(observed_last), "open_interest"].iloc[0])
        complete = not is_last or (observed_last == end_date and rule_date <= end_date and last_oi == 0.0)
        expected_expiry = observed_last if complete else rule_date
        if complete and (
            observed_last.year != month.year
            or observed_last.month != month.month
            or abs((observed_last - rule_date).days) > 7
        ):
            raise RuntimeError(
                f"Completed {contract} has implausible official last date {observed_last.date()} "
                f"versus third-Friday rule {rule_date.date()}"
            )
        exit_date = expected_expiry if complete else end_date
        dates = set(contract_rows["date"])
        if exit_date not in dates:
            raise RuntimeError(f"No settlement for {contract} on exit/mark date {exit_date.date()}")
        entry_date = START_DATE if idx == 0 else prior_exit
        if entry_date is None or entry_date not in dates:
            raise RuntimeError(f"No settlement for {contract} on entry date {entry_date}")
        cycles.append(Cycle(contract, pd.Timestamp(entry_date), pd.Timestamp(exit_date), expected_expiry, complete))
        prior_exit = pd.Timestamp(exit_date)
    return cycles


def build_futures_daily(futures: pd.DataFrame, cycles: list[Cycle]) -> tuple[pd.DataFrame, pd.DataFrame]:
    lookup = futures.set_index(["contract", "date"]).sort_index()
    daily_parts: list[pd.DataFrame] = []
    schedule_rows: list[dict[str, object]] = []

    for idx, cycle in enumerate(cycles):
        rows = futures[
            futures["contract"].eq(cycle.contract)
            & futures["date"].between(cycle.entry_date, cycle.exit_date)
        ].sort_values("date")
        entry_settle = float(lookup.loc[(cycle.contract, cycle.entry_date), "settle"])
        exit_settle = float(lookup.loc[(cycle.contract, cycle.exit_date), "settle"])
        segment = rows[["date", "contract", "settle", "close", "volume", "open_interest"]].copy()
        segment["im_gross_ret"] = segment["settle"].pct_change()
        if idx == 0:
            segment.loc[segment.index[0], "im_gross_ret"] = 0.0
        else:
            segment = segment.iloc[1:].copy()
        daily_parts.append(segment)
        schedule_rows.append(
            {
                "contract": cycle.contract,
                "entry_date": cycle.entry_date,
                "exit_date": cycle.exit_date,
                "expected_expiry": cycle.expected_expiry,
                "complete": cycle.complete,
                "entry_settle": entry_settle,
                "exit_settle": exit_settle,
                "observations_held": int(len(rows)),
            }
        )

    daily = pd.concat(daily_parts, ignore_index=True).sort_values("date").reset_index(drop=True)
    if daily["date"].duplicated().any():
        raise RuntimeError("Duplicate dates after IM roll construction")
    if daily["im_gross_ret"].isna().any():
        raise RuntimeError("Missing returns after IM roll construction")
    daily["cost_rate"] = 0.0
    daily["roll_from"] = ""
    daily["roll_to"] = ""
    daily.loc[daily.index[0], "cost_rate"] = ONE_WAY_COST
    for idx, cycle in enumerate(cycles[:-1]):
        if not cycle.complete:
            continue
        roll_mask = daily["date"].eq(cycle.exit_date)
        if roll_mask.sum() != 1:
            raise RuntimeError(f"Roll date missing/duplicated in daily series: {cycle.exit_date.date()}")
        daily.loc[roll_mask, "cost_rate"] += 2.0 * ONE_WAY_COST
        daily.loc[roll_mask, "roll_from"] = cycle.contract
        daily.loc[roll_mask, "roll_to"] = cycles[idx + 1].contract
    daily["im_net_ret"] = (1.0 + daily["im_gross_ret"]) * (1.0 - daily["cost_rate"]) - 1.0
    return daily, pd.DataFrame(schedule_rows)


def merge_indices(
    futures_daily: pd.DataFrame,
    price_index: pd.DataFrame,
    total_return_index: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    price = price_index[["date", "close"]].rename(columns={"close": "csi1000_price_close"})
    tri = total_return_index[["date", "close"]].rename(columns={"close": "csi1000_tri_close"})
    futures_dates = set(futures_daily["date"])
    price_dates = set(price["date"])
    tri_dates = set(tri["date"])
    common_dates = futures_dates & price_dates & tri_dates
    dropped = {
        "futures_dates_not_common": sorted(d.date().isoformat() for d in futures_dates - common_dates),
        "price_dates_not_common": sorted(d.date().isoformat() for d in price_dates - common_dates),
        "tri_dates_not_common": sorted(d.date().isoformat() for d in tri_dates - common_dates),
    }
    daily = futures_daily.merge(price, on="date", how="inner", validate="one_to_one")
    daily = daily.merge(tri, on="date", how="inner", validate="one_to_one")
    daily = daily.sort_values("date").reset_index(drop=True)
    if daily.empty or len(daily) != len(futures_daily):
        raise RuntimeError(f"Index alignment removed {len(futures_daily) - len(daily)} IM trading dates")

    daily["csi1000_price_ret"] = daily["csi1000_price_close"].pct_change().fillna(0.0)
    daily["csi1000_tri_ret"] = daily["csi1000_tri_close"].pct_change().fillna(0.0)
    daily["gross_vs_price_ret"] = (1.0 + daily["im_gross_ret"]) / (1.0 + daily["csi1000_price_ret"]) - 1.0
    daily["net_vs_price_ret"] = (1.0 + daily["im_net_ret"]) / (1.0 + daily["csi1000_price_ret"]) - 1.0
    daily["gross_vs_tri_ret"] = (1.0 + daily["im_gross_ret"]) / (1.0 + daily["csi1000_tri_ret"]) - 1.0
    daily["cash_asset_ret"] = CASH_ASSET_DAILY_RETURN
    daily["cash_contribution_ret"] = CASH_WEIGHT * daily["cash_asset_ret"]
    daily["im_net_plus_cash_ret"] = daily["im_net_ret"] + daily["cash_contribution_ret"]
    daily["net_basis_plus_cash_ret"] = daily["net_vs_price_ret"] + daily["cash_contribution_ret"]

    return_columns = {
        "im_gross": "im_gross_ret",
        "im_net_1bp_per_side": "im_net_ret",
        "csi1000_price": "csi1000_price_ret",
        "csi1000_total_return": "csi1000_tri_ret",
        "im_gross_vs_price": "gross_vs_price_ret",
        "im_net_vs_price": "net_vs_price_ret",
        "im_gross_vs_total_return": "gross_vs_tri_ret",
        "im_net_plus_70pct_cash_at_3pct": "im_net_plus_cash_ret",
        "im_net_basis_plus_70pct_cash_at_3pct": "net_basis_plus_cash_ret",
    }
    for label, column in return_columns.items():
        daily[f"nav_{label}"] = (1.0 + daily[column]).cumprod()
    return daily, dropped


def build_monthly_cycles(
    futures: pd.DataFrame,
    cycles: list[Cycle],
    price_index: pd.DataFrame,
    total_return_index: pd.DataFrame,
) -> pd.DataFrame:
    f_lookup = futures.set_index(["contract", "date"])["settle"]
    s_lookup = price_index.set_index("date")["close"]
    tr_lookup = total_return_index.set_index("date")["close"]
    rows: list[dict[str, object]] = []
    for cycle in cycles:
        f_entry = float(f_lookup.loc[(cycle.contract, cycle.entry_date)])
        f_exit = float(f_lookup.loc[(cycle.contract, cycle.exit_date)])
        s_entry = float(s_lookup.loc[cycle.entry_date])
        s_exit = float(s_lookup.loc[cycle.exit_date])
        tr_entry = float(tr_lookup.loc[cycle.entry_date])
        tr_exit = float(tr_lookup.loc[cycle.exit_date])
        futures_factor = f_exit / f_entry
        spot_factor = s_exit / s_entry
        tri_factor = tr_exit / tr_entry
        side_count = 2 if cycle.complete else 1
        net_futures_factor = futures_factor * ((1.0 - ONE_WAY_COST) ** side_count)
        rows.append(
            {
                "contract": cycle.contract,
                "entry_date": cycle.entry_date,
                "exit_date": cycle.exit_date,
                "expected_expiry": cycle.expected_expiry,
                "complete": cycle.complete,
                "calendar_days": int((cycle.exit_date - cycle.entry_date).days),
                "futures_entry_settle": f_entry,
                "futures_exit_settle": f_exit,
                "spot_entry_close": s_entry,
                "spot_exit_close": s_exit,
                "tri_entry_close": tr_entry,
                "tri_exit_close": tr_exit,
                "entry_discount_over_futures": s_entry / f_entry - 1.0,
                "expiry_settle_vs_spot_residual": f_exit / s_exit - 1.0,
                "futures_return": futures_factor - 1.0,
                "futures_net_return": net_futures_factor - 1.0,
                "spot_price_return": spot_factor - 1.0,
                "spot_total_return": tri_factor - 1.0,
                "basis_excess_vs_price": futures_factor / spot_factor - 1.0,
                "net_basis_excess_vs_price": net_futures_factor / spot_factor - 1.0,
                "excess_vs_total_return": futures_factor / tri_factor - 1.0,
            }
        )
    return pd.DataFrame(rows)


def metric_from_returns(returns: pd.Series) -> dict[str, float]:
    clean = returns.astype(float).dropna()
    if clean.empty:
        return {
            "total_return": np.nan,
            "cagr": np.nan,
            "max_drawdown": np.nan,
            "annual_volatility": np.nan,
            "sharpe_0rf": np.nan,
        }
    nav = pd.concat([pd.Series([1.0]), (1.0 + clean.reset_index(drop=True)).cumprod()], ignore_index=True)
    total_return = float(nav.iloc[-1] - 1.0)
    cagr = float(nav.iloc[-1] ** (TRADING_DAYS / len(clean)) - 1.0)
    max_drawdown = float((nav / nav.cummax() - 1.0).min())
    volatility = float(clean.std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(clean) > 1 else np.nan
    sharpe = (
        float(clean.mean() / clean.std(ddof=1) * math.sqrt(TRADING_DAYS))
        if len(clean) > 1 and clean.std(ddof=1) > 0
        else np.nan
    )
    return {
        "total_return": total_return,
        "cagr": cagr,
        "max_drawdown": max_drawdown,
        "annual_volatility": volatility,
        "sharpe_0rf": sharpe,
    }


SERIES = {
    "im_gross": "im_gross_ret",
    "im_net_1bp_per_side": "im_net_ret",
    "csi1000_price": "csi1000_price_ret",
    "csi1000_total_return": "csi1000_tri_ret",
    "im_gross_vs_price": "gross_vs_price_ret",
    "im_net_vs_price": "net_vs_price_ret",
    "im_gross_vs_total_return": "gross_vs_tri_ret",
    "im_net_plus_70pct_cash_at_3pct": "im_net_plus_cash_ret",
    "im_net_basis_plus_70pct_cash_at_3pct": "net_basis_plus_cash_ret",
}


def metrics_by_window(daily: pd.DataFrame) -> pd.DataFrame:
    end_date = pd.Timestamp(daily["date"].max())
    sample_start = pd.Timestamp(daily["date"].min())
    windows = [
        ("full", sample_start),
        ("10y", end_date - pd.DateOffset(years=10)),
        ("5y", end_date - pd.DateOffset(years=5)),
        ("3y", end_date - pd.DateOffset(years=3)),
        ("1y", end_date - pd.DateOffset(years=1)),
    ]
    rows: list[dict[str, object]] = []
    for window, cutoff in windows:
        available = window == "full" or sample_start <= cutoff
        subset = daily[daily["date"] >= cutoff].copy() if available else daily.iloc[0:0].copy()
        reason = "" if available else f"IM history starts {sample_start.date()}, shorter than requested {window} window"
        for label, column in SERIES.items():
            metrics = metric_from_returns(subset[column])
            rows.append(
                {
                    "window": window,
                    "series": label,
                    "available": available,
                    "unavailable_reason": reason,
                    "requested_start": cutoff.date().isoformat(),
                    "actual_start": subset["date"].min().date().isoformat() if available else "",
                    "end": end_date.date().isoformat(),
                    "trading_days": int(len(subset)),
                    **metrics,
                }
            )
    return pd.DataFrame(rows)


def annual_metrics(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    sample_start = pd.Timestamp(daily["date"].min())
    sample_end = pd.Timestamp(daily["date"].max())
    for year, subset in daily.groupby(daily["date"].dt.year, sort=True):
        partial_year = year == sample_start.year or year == sample_end.year
        for label, column in SERIES.items():
            metrics = metric_from_returns(subset[column])
            rows.append(
                {
                    "year": int(year),
                    "series": label,
                    "partial_year": partial_year,
                    "period_start": subset["date"].min().date().isoformat(),
                    "period_end": subset["date"].max().date().isoformat(),
                    "trading_days": int(len(subset)),
                    **metrics,
                }
            )
    return pd.DataFrame(rows)


def scenario_projections(metrics: pd.DataFrame, annual: pd.DataFrame) -> pd.DataFrame:
    full_years = annual[
        annual["series"].eq("im_net_vs_price")
        & annual["year"].between(2023, 2025)
        & ~annual["partial_year"]
    ]
    if len(full_years) != 3:
        raise RuntimeError("Scenario anchor requires complete 2023-2025 IM annual metrics")
    full_rate = float(
        metrics.loc[
            metrics["window"].eq("full") & metrics["series"].eq("im_net_vs_price"), "cagr"
        ].iloc[0]
    )
    trailing_rate = float(
        metrics.loc[
            metrics["window"].eq("1y") & metrics["series"].eq("im_net_vs_price"), "cagr"
        ].iloc[0]
    )
    scenarios = [
        ("悲观", float(full_years["cagr"].min()), "2023-2025完整自然年中最低净贴水年化"),
        ("中等", full_rate, "全样本净贴水CAGR"),
        ("乐观", trailing_rate, "最近1年净贴水CAGR"),
    ]
    cash_contribution = CASH_WEIGHT * CASH_ASSET_ANNUAL_RETURN
    rows: list[dict[str, object]] = []
    for scenario, basis_rate, anchor in scenarios:
        combined_rate = basis_rate + cash_contribution
        for horizon in (3, 5):
            rows.append(
                {
                    "scenario": scenario,
                    "anchor": anchor,
                    "flat_index_assumption": True,
                    "net_basis_annualized": basis_rate,
                    "cash_contribution_to_nav_annualized": cash_contribution,
                    "combined_annualized": combined_rate,
                    "horizon_years": horizon,
                    "net_basis_cumulative": (1.0 + basis_rate) ** horizon - 1.0,
                    "combined_cumulative": (1.0 + combined_rate) ** horizon - 1.0,
                }
            )
    return pd.DataFrame(rows)


def current_term_structure(futures: pd.DataFrame, price_index: pd.DataFrame, end_date: pd.Timestamp) -> pd.DataFrame:
    spot = float(price_index.loc[price_index["date"].eq(end_date), "close"].iloc[0])
    current = futures[futures["date"].eq(end_date)].copy()
    current["contract_month"] = current["contract"].map(contract_month)
    current = current[current["contract_month"] >= end_date.replace(day=1)].copy()
    rows: list[dict[str, object]] = []
    for row in current.itertuples(index=False):
        expiry = third_friday(row.contract_month)
        days = int((expiry - end_date).days)
        discount = spot / float(row.settle) - 1.0
        annualized = (spot / float(row.settle)) ** (365.0 / days) - 1.0 if days > 0 else np.nan
        rows.append(
            {
                "as_of": end_date.date().isoformat(),
                "contract": row.contract,
                "settle": float(row.settle),
                "spot_close": spot,
                "rule_expiry": expiry.date().isoformat(),
                "calendar_days": days,
                "spot_over_futures_discount": discount,
                "simple_convergence_annualized": annualized,
                "note": "third-Friday rule, not a return forecast",
            }
        )
    return pd.DataFrame(rows).sort_values("contract").reset_index(drop=True)


def validate_pre_settle(futures: pd.DataFrame) -> dict[str, object]:
    check = futures.sort_values(["contract", "date"]).copy()
    check["prior_observed_settle"] = check.groupby("contract")["settle"].shift(1)
    comparable = check["prior_observed_settle"].notna() & check["pre_settle"].notna()
    differences = (check.loc[comparable, "pre_settle"] - check.loc[comparable, "prior_observed_settle"]).abs()
    mismatches = differences > 1e-8
    return {
        "comparable_rows": int(comparable.sum()),
        "mismatch_rows": int(mismatches.sum()),
        "max_abs_difference": float(differences.max()) if len(differences) else 0.0,
    }


def percent(value: float) -> str:
    return "N/A" if pd.isna(value) else f"{value:.2%}"


def render_window_table(metrics: pd.DataFrame, selected_series: list[str]) -> str:
    labels = {
        "im_gross": "IM毛收益",
        "im_net_1bp_per_side": "IM净收益",
        "csi1000_price": "中证1000价格",
        "csi1000_total_return": "中证1000全收益",
        "im_net_vs_price": "净贴水",
        "im_gross_vs_total_return": "毛收益相对全收益指数",
        "im_net_plus_70pct_cash_at_3pct": "IM净收益+现金",
        "im_net_basis_plus_70pct_cash_at_3pct": "净贴水+现金",
    }
    header = "| 窗口 | " + " | ".join(f"{labels[s]} CAGR / MaxDD" for s in selected_series) + " |"
    divider = "|---|" + "---|" * len(selected_series)
    lines = [header, divider]
    for window in ["full", "10y", "5y", "3y", "1y"]:
        cells: list[str] = []
        for series in selected_series:
            row = metrics[(metrics["window"].eq(window)) & (metrics["series"].eq(series))].iloc[0]
            cells.append(f"{percent(row['cagr'])} / {percent(row['max_drawdown'])}")
        lines.append(f"| {window} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def render_annual_table(annual: pd.DataFrame) -> str:
    selected = [
        ("im_net_1bp_per_side", "IM净收益"),
        ("im_net_vs_price", "净贴水"),
        ("im_net_plus_70pct_cash_at_3pct", "IM净收益+现金"),
        ("im_net_basis_plus_70pct_cash_at_3pct", "净贴水+现金"),
    ]
    lines = [
        "| 年份 | 样本 | " + " | ".join(f"{label} CAGR / MaxDD" for _, label in selected) + " |",
        "|---|---|" + "---|" * len(selected),
    ]
    for year in sorted(annual["year"].unique()):
        subset = annual[annual["year"].eq(year)]
        period = "部分年度" if bool(subset["partial_year"].iloc[0]) else "完整年度"
        cells = []
        for series, _ in selected:
            row = subset[subset["series"].eq(series)].iloc[0]
            cells.append(f"{percent(row['cagr'])} / {percent(row['max_drawdown'])}")
        lines.append(f"| {year} | {period} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def render_scenario_table(projections: pd.DataFrame) -> str:
    lines = [
        "| 情景 | 纯贴水年化 | 加现金年化 | 3年累计 | 5年累计 |",
        "|---|---:|---:|---:|---:|",
    ]
    for scenario in ["悲观", "中等", "乐观"]:
        subset = projections[projections["scenario"].eq(scenario)]
        row3 = subset[subset["horizon_years"].eq(3)].iloc[0]
        row5 = subset[subset["horizon_years"].eq(5)].iloc[0]
        lines.append(
            f"| {scenario} | {percent(row3['net_basis_annualized'])} | "
            f"{percent(row3['combined_annualized'])} | {percent(row3['combined_cumulative'])} | "
            f"{percent(row5['combined_cumulative'])} |"
        )
    return "\n".join(lines)


def write_record(
    output_dir: Path,
    daily: pd.DataFrame,
    metrics: pd.DataFrame,
    annual: pd.DataFrame,
    monthly: pd.DataFrame,
    projections: pd.DataFrame,
    term_structure: pd.DataFrame,
    manifest: dict[str, object],
) -> None:
    completed = monthly[monthly["complete"]].copy()
    current = monthly.iloc[-1]
    cycle_mean = float(completed["basis_excess_vs_price"].mean())
    cycle_median = float(completed["basis_excess_vs_price"].median())
    cycle_win = float((completed["basis_excess_vs_price"] > 0).mean())
    entry_mean = float(completed["entry_discount_over_futures"].mean())
    main_table = render_window_table(
        metrics, ["im_gross", "im_net_1bp_per_side", "csi1000_price", "csi1000_total_return"]
    )
    carry_table = render_window_table(
        metrics,
        [
            "im_net_vs_price",
            "im_gross_vs_total_return",
            "im_net_plus_70pct_cash_at_3pct",
            "im_net_basis_plus_70pct_cash_at_3pct",
        ],
    )
    annual_table = render_annual_table(annual)
    scenario_table = render_scenario_table(projections)
    near = term_structure.iloc[0]
    record = f"""# IM 逐月到期展期贴水研究 v1：结果记录

运行日期：{date.today().isoformat()}  
研究状态：研究审计；未获准实盘  
正式样本：{daily['date'].min().date().isoformat()} 至 {daily['date'].max().date().isoformat()}  
数据：中金所官方 IM 月合约结算价；中证指数官方 000852 与 H00852

## 结论摘要

- IM 从 2022-07-22 才上市，因此全样本约4年；10年和5年窗口按预注册规则显示 `N/A`，不缩短窗口冒充完整历史。
- 完成周期 {len(completed)} 个；相对价格指数月度基差超额均值 {percent(cycle_mean)}，中位数 {percent(cycle_median)}，正值比例 {percent(cycle_win)}，入场贴水均值 {percent(entry_mean)}。
- 现金层按用户假设：1倍IM名义，30%保证金及缓冲，70%现金管理资产净年化3%；理论上为总净资产贡献约2.10个百分点。它是情景假设，不是历史理财/打新收益实测。
- 最新合约 {current['contract']} 截至 {pd.Timestamp(current['exit_date']).date().isoformat()} 为{'已完成' if current['complete'] else '未完成、仅盯市'}周期。最新近月 {near['contract']} 结算价相对现货贴水 {percent(near['spot_over_futures_discount'])}；这不是交易信号。

## 强制窗口：总收益与基准

{main_table}

## 强制窗口：贴水与资金效率

{carry_table}

## 每年年化收益与最大回撤

2022从7月22日起，2026截至8月14日，二者是部分年度；表内仍按252个交易日年化。

{annual_table}

## 平指数的未来3年/5年情景

以下只把历史净贴水锚点与总净资产2.10个百分点现金贡献相加，假设中证1000指数期末不涨不跌；累计收益按对应年化复合。

{scenario_table}

## 执行、保证金与风险

- 期货方向敞口恒为净资产1倍；30%是保证金与缓冲占用，不把100万元放大成333万元名义敞口。
- IM每日盯市。70%现金必须高流动、可用于补足保证金；固定3%忽略赎回限制、打新中签不确定性和经纪商追加保证金，属于偏理想化资金管理层。
- 主曲线使用官方结算价，持有至最后交易日；每边1bp成本敏感性，初始1bp、每次换月2bp。
- 最终结算价不保证可成交；实盘版本需另测到期日前固定时点、盘口、滑点、手续费和压力保证金。

## 完整性检查

- 中金所官方月包 {manifest['cffex']['archive_count']} 个，{manifest['cffex']['first_month']} 至 {manifest['cffex']['last_month']}；IM记录 {manifest['cffex']['rows']} 行、{manifest['cffex']['contracts']} 张合约。
- 共同交易日 {manifest['common_sample']['rows']}；日期对齐删除期货交易日 {len(manifest['calendar_alignment']['futures_dates_not_common'])} 个。
- `pre_settle` 可比 {manifest['pre_settle_check']['comparable_rows']} 行，不一致 {manifest['pre_settle_check']['mismatch_rows']} 行，最大绝对差 {manifest['pre_settle_check']['max_abs_difference']:.6f} 点。
- IM绝对日收益超过10%的记录 {manifest['extreme_return_count']} 条，详见 `extreme_returns.csv`。

## 证据与复现

- 冻结规格：`docs/{VERSION}_spec.md`（SHA-256 `{manifest['spec_sha256']}`）。
- 脚本：`{VERSION}.py`（SHA-256 `{manifest['script_sha256']}`）。
- 命令：`{manifest['command']}`。
- 原始月包和指数窄表保存在 `data/{VERSION}/`；完整清单见本目录 `download_log.csv` 与 `data_manifest.json`。

## 结论边界

这是历史研究审计，不是交易建议。IM样本只跨越约4年，尚未覆盖完整10年周期；贴水受市场风格、对冲需求、股息、融资利率和监管环境共同影响，不能把历史CAGR直接外推为保证收益。
"""
    (output_dir / "record.md").write_text(record, encoding="utf-8")


def run(end_date: pd.Timestamp, data_dir: Path, output_dir: Path, refresh: bool) -> None:
    if output_dir.exists():
        raise FileExistsError(f"Formal output directory already exists and will not be overwritten: {output_dir}")
    if end_date < START_DATE:
        raise ValueError("End date is before IM inception")
    spec_hash = verify_frozen_spec()
    data_dir.mkdir(parents=True, exist_ok=True)

    zip_paths, download_log = download_cffex_months(data_dir, end_date, refresh)
    futures = parse_cffex_im(zip_paths, end_date)
    price_index = fetch_csindex("000852", START_DATE, end_date)
    total_return_index = fetch_csindex("H00852", START_DATE, end_date)
    futures.to_csv(data_dir / "cffex_im_contracts.csv", index=False, encoding="utf-8-sig")
    price_index.to_csv(data_dir / "csindex_000852.csv", index=False, encoding="utf-8-sig")
    total_return_index.to_csv(data_dir / "csindex_H00852.csv", index=False, encoding="utf-8-sig")

    actual_end = min(
        pd.Timestamp(futures["date"].max()),
        pd.Timestamp(price_index["date"].max()),
        pd.Timestamp(total_return_index["date"].max()),
    )
    if actual_end != end_date:
        raise RuntimeError(f"Requested end {end_date.date()} is not common latest date; actual {actual_end.date()}")

    cycles = build_cycles(futures, end_date)
    futures_daily, schedule = build_futures_daily(futures, cycles)
    daily, dropped_dates = merge_indices(futures_daily, price_index, total_return_index)
    monthly = build_monthly_cycles(futures, cycles, price_index, total_return_index)
    metrics = metrics_by_window(daily)
    annual = annual_metrics(daily)
    projections = scenario_projections(metrics, annual)
    term_structure = current_term_structure(futures, price_index, end_date)
    pre_settle_check = validate_pre_settle(futures)
    extremes = daily.loc[
        daily["im_gross_ret"].abs() > 0.10,
        ["date", "contract", "settle", "im_gross_ret", "csi1000_price_ret", "roll_from", "roll_to"],
    ].copy()

    command = f"{Path(sys.executable).name} {Path(__file__).name} --end-date {end_date.date().isoformat()}"
    if refresh:
        command += " --refresh"
    manifest: dict[str, object] = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "research_status": "research_only_not_approved_for_live_trading",
        "command": command,
        "script_sha256": sha256_file(Path(__file__)),
        "spec_sha256": spec_hash,
        "cffex": {
            "source": CFFEX_URL,
            "archive_count": int(len(download_log)),
            "first_month": str(download_log["month"].min()),
            "last_month": str(download_log["month"].max()),
            "rows": int(len(futures)),
            "contracts": int(futures["contract"].nunique()),
            "first_date": futures["date"].min().date().isoformat(),
            "last_date": futures["date"].max().date().isoformat(),
            "price_field": "official settlement",
        },
        "csindex": {
            "source": CSINDEX_URL,
            "price_symbol": "000852",
            "total_return_symbol": "H00852",
            "price_rows": int(len(price_index)),
            "total_return_rows": int(len(total_return_index)),
            "adjustment_mode": "official price and total-return indices; no adjustment",
        },
        "common_sample": {
            "rows": int(len(daily)),
            "start": daily["date"].min().date().isoformat(),
            "end": daily["date"].max().date().isoformat(),
            "timezone": "Asia/Shanghai",
        },
        "execution": {
            "notional_exposure": "1x NAV",
            "margin_and_buffer_weight": 1.0 - CASH_WEIGHT,
            "cash_management_weight": CASH_WEIGHT,
            "cash_asset_assumed_net_annual_return": CASH_ASSET_ANNUAL_RETURN,
            "cash_contribution_to_nav_simple_annual": CASH_WEIGHT * CASH_ASSET_ANNUAL_RETURN,
            "daily_rebalanced": True,
            "one_way_cost_sensitivity": ONE_WAY_COST,
            "roll": "hold front calendar-month IM to final settlement; enter next calendar month at same-day settlement",
        },
        "window_availability": {
            row.window: {"available": bool(row.available), "reason": row.unavailable_reason}
            for row in metrics.drop_duplicates("window").itertuples(index=False)
        },
        "calendar_alignment": dropped_dates,
        "pre_settle_check": pre_settle_check,
        "extreme_return_count": int(len(extremes)),
        "completed_cycles": int(monthly["complete"].sum()),
        "incomplete_cycles": int((~monthly["complete"]).sum()),
    }

    output_dir.mkdir(parents=True, exist_ok=False)
    download_log.to_csv(output_dir / "download_log.csv", index=False, encoding="utf-8-sig")
    schedule.to_csv(output_dir / "roll_schedule.csv", index=False, encoding="utf-8-sig")
    daily.to_csv(output_dir / "daily_nav.csv", index=False, encoding="utf-8-sig")
    monthly.to_csv(output_dir / "monthly_cycles.csv", index=False, encoding="utf-8-sig")
    metrics.to_csv(output_dir / "metrics_by_window.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(output_dir / "annual_metrics.csv", index=False, encoding="utf-8-sig")
    projections.to_csv(output_dir / "scenario_projections.csv", index=False, encoding="utf-8-sig")
    term_structure.to_csv(output_dir / "current_term_structure.csv", index=False, encoding="utf-8-sig")
    extremes.to_csv(output_dir / "extreme_returns.csv", index=False, encoding="utf-8-sig")
    (output_dir / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (output_dir / "command_log.txt").write_text(command + "\n", encoding="utf-8")
    write_record(output_dir, daily, metrics, annual, monthly, projections, term_structure, manifest)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="IM monthly hold-to-expiry roll research v1")
    parser.add_argument("--end-date", default=DEFAULT_END_DATE.date().isoformat())
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--refresh", action="store_true", help="Re-download official monthly ZIP archives")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(
        end_date=pd.Timestamp(args.end_date),
        data_dir=args.data_dir.resolve(),
        output_dir=args.output_dir.resolve(),
        refresh=args.refresh,
    )
