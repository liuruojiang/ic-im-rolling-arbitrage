from __future__ import annotations

import argparse
import calendar as month_calendar
import hashlib
import io
import json
import math
import re
import sys
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests


ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = ROOT / "data" / "ic_monthly_discount_roll_v1"
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "ic_monthly_discount_roll_v1"
SPEC_PATH = ROOT / "docs" / "ic_monthly_discount_roll_v1_spec.md"
CFFEX_URL = "http://www.cffex.com.cn/sj/historysj/{ym}/zip/{ym}.zip"
CSINDEX_URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
START_DATE = pd.Timestamp("2015-04-16")
DEFAULT_END_DATE = pd.Timestamp("2026-08-14")
ONE_WAY_COST = 0.0001
TRADING_DAYS = 252
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) IC-roll-research/1.0"
CONTRACT_RE = re.compile(r"^IC(?P<yy>\d{2})(?P<mm>\d{2})$")


@dataclass(frozen=True)
class Cycle:
    contract: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    expected_expiry: pd.Timestamp
    complete: bool


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def month_starts(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    return list(pd.date_range(start.normalize().replace(day=1), end.normalize().replace(day=1), freq="MS"))


def download_cffex_months(
    data_dir: Path,
    end_date: pd.Timestamp,
    refresh: bool,
) -> tuple[list[Path], pd.DataFrame]:
    raw_dir = data_dir / "cffex_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    rows: list[dict[str, object]] = []
    paths: list[Path] = []

    for month_start in month_starts(START_DATE, end_date):
        ym = month_start.strftime("%Y%m")
        path = raw_dir / f"{ym}.zip"
        url = CFFEX_URL.format(ym=ym)
        source = "cache"
        status_code = 200
        if refresh or not path.exists():
            response = session.get(url, timeout=60)
            response.raise_for_status()
            payload = response.content
            if not zipfile.is_zipfile(io.BytesIO(payload)):
                raise RuntimeError(f"CFFEX response is not a ZIP archive: {ym}, {url}")
            path.write_bytes(payload)
            source = "download"
            status_code = response.status_code

        with zipfile.ZipFile(path) as archive:
            members = [name for name in archive.namelist() if re.fullmatch(r"\d{8}_1\.csv", Path(name).name)]
            if not members:
                raise RuntimeError(f"No daily CSV members in {path}")
            member_dates = sorted(pd.Timestamp(Path(name).name[:8]) for name in members)
        paths.append(path)
        rows.append(
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
    return paths, pd.DataFrame(rows)


def _decode_cffex_csv(payload: bytes) -> str:
    for encoding in ("gb18030", "gb2312", "utf-8-sig"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("cffex", payload, 0, min(20, len(payload)), "unknown encoding")


def parse_cffex_ic(zip_paths: list[Path], end_date: pd.Timestamp) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
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
                text = _decode_cffex_csv(archive.read(member))
                raw = pd.read_csv(
                    io.StringIO(text),
                    header=None,
                    skiprows=1,
                    dtype=str,
                    on_bad_lines="error",
                )
                if raw.shape[1] < 11:
                    raise RuntimeError(f"Unexpected CFFEX schema in {path.name}/{member}: {raw.shape[1]} columns")
                frame = raw[list(fields)].rename(columns=fields)
                frame["contract"] = frame["contract"].str.strip()
                frame = frame[frame["contract"].str.fullmatch(r"IC\d{4}", na=False)].copy()
                if frame.empty:
                    continue
                frame.insert(1, "date", trade_date)
                for column in fields.values():
                    if column != "contract":
                        frame[column] = pd.to_numeric(frame[column].replace({"--": np.nan, "null": np.nan}), errors="coerce")
                frames.append(frame)

    if not frames:
        raise RuntimeError("No IC contract rows were parsed from official CFFEX archives")
    futures = pd.concat(frames, ignore_index=True)
    futures = futures.sort_values(["date", "contract"]).reset_index(drop=True)
    duplicates = futures.duplicated(["date", "contract"], keep=False)
    if duplicates.any():
        sample = futures.loc[duplicates, ["date", "contract"]].head().to_dict("records")
        raise RuntimeError(f"Duplicate CFFEX date-contract rows: {sample}")
    if futures["settle"].isna().any():
        sample = futures.loc[futures["settle"].isna(), ["date", "contract"]].head().to_dict("records")
        raise RuntimeError(f"Missing IC settlement prices: {sample}")
    return futures


def fetch_csindex(symbol: str, start_date: pd.Timestamp, end_date: pd.Timestamp) -> pd.DataFrame:
    response = requests.get(
        CSINDEX_URL,
        params={
            "indexCode": symbol,
            "startDate": start_date.strftime("%Y%m%d"),
            "endDate": end_date.strftime("%Y%m%d"),
        },
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success") or not payload.get("data"):
        raise RuntimeError(f"CSIndex returned no successful data for {symbol}: {payload.get('msg')}")
    raw = pd.DataFrame(payload["data"])
    required = {"tradeDate", "indexCode", "close"}
    if not required.issubset(raw.columns):
        raise RuntimeError(f"Unexpected CSIndex schema for {symbol}: {sorted(raw.columns)}")
    frame = raw[["tradeDate", "indexCode", "close"]].rename(
        columns={"tradeDate": "date", "indexCode": "symbol"}
    )
    frame["date"] = pd.to_datetime(frame["date"], format="%Y%m%d", errors="raise")
    frame["close"] = pd.to_numeric(frame["close"], errors="raise")
    frame = frame.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    return frame


def contract_month(contract: str) -> pd.Timestamp:
    match = CONTRACT_RE.fullmatch(contract)
    if not match:
        raise ValueError(f"Invalid IC contract: {contract}")
    return pd.Timestamp(year=2000 + int(match.group("yy")), month=int(match.group("mm")), day=1)


def contract_code(month: pd.Timestamp) -> str:
    return f"IC{month.strftime('%y%m')}"


def third_friday(month: pd.Timestamp) -> pd.Timestamp:
    cal = month_calendar.monthcalendar(month.year, month.month)
    fridays = [week[month_calendar.FRIDAY] for week in cal if week[month_calendar.FRIDAY] != 0]
    return pd.Timestamp(year=month.year, month=month.month, day=fridays[2])


def build_cycles(futures: pd.DataFrame, end_date: pd.Timestamp) -> list[Cycle]:
    front_end_month = end_date.normalize().replace(day=1)
    while third_friday(front_end_month) < end_date:
        front_end_month += pd.offsets.MonthBegin(1)
    months = month_starts(pd.Timestamp("2015-05-01"), front_end_month)
    available = set(futures["contract"].unique())
    required = [contract_code(month) for month in months]
    missing = [contract for contract in required if contract not in available]
    if missing:
        raise RuntimeError(f"Missing required monthly IC contracts: {missing}")

    cycles: list[Cycle] = []
    prior_exit: pd.Timestamp | None = None
    for idx, month in enumerate(months):
        contract = contract_code(month)
        contract_rows = futures[futures["contract"].eq(contract)]
        observed_last = pd.Timestamp(contract_rows["date"].max())
        raw_third_friday = third_friday(month)
        is_last_cycle = idx == len(months) - 1
        last_open_interest = float(contract_rows.loc[contract_rows["date"].eq(observed_last), "open_interest"].iloc[0])
        complete = not is_last_cycle or (
            observed_last == end_date
            and raw_third_friday <= end_date
            and last_open_interest == 0.0
        )
        expected = observed_last if complete else raw_third_friday
        if complete and (
            observed_last.year != month.year
            or observed_last.month != month.month
            or abs((observed_last - raw_third_friday).days) > 7
        ):
            raise RuntimeError(
                f"Completed contract {contract} has implausible official last date {observed_last.date()} "
                f"versus rule date {raw_third_friday.date()}"
            )
        exit_date = expected if complete else end_date
        if exit_date not in set(contract_rows["date"]):
            raise RuntimeError(f"No settlement for {contract} on exit/mark date {exit_date.date()}")
        entry_date = START_DATE if idx == 0 else prior_exit
        if entry_date is None or entry_date not in set(contract_rows["date"]):
            raise RuntimeError(f"No settlement for {contract} on entry date {entry_date}")
        cycles.append(
            Cycle(
                contract=contract,
                entry_date=pd.Timestamp(entry_date),
                exit_date=pd.Timestamp(exit_date),
                expected_expiry=pd.Timestamp(expected),
                complete=complete,
            )
        )
        prior_exit = pd.Timestamp(exit_date)
    return cycles


def build_futures_daily(futures: pd.DataFrame, cycles: list[Cycle]) -> tuple[pd.DataFrame, pd.DataFrame]:
    contract_lookup = futures.set_index(["contract", "date"]).sort_index()
    daily_parts: list[pd.DataFrame] = []
    schedule_rows: list[dict[str, object]] = []

    for idx, cycle in enumerate(cycles):
        contract_rows = futures[
            futures["contract"].eq(cycle.contract)
            & futures["date"].between(cycle.entry_date, cycle.exit_date)
        ].sort_values("date")
        entry_settle = float(contract_lookup.loc[(cycle.contract, cycle.entry_date), "settle"])
        exit_settle = float(contract_lookup.loc[(cycle.contract, cycle.exit_date), "settle"])
        segment = contract_rows[["date", "contract", "settle", "close", "volume", "open_interest"]].copy()
        segment["ic_gross_ret"] = segment["settle"].pct_change()
        if idx == 0:
            segment.loc[segment.index[0], "ic_gross_ret"] = 0.0
        else:
            segment.loc[segment.index[0], "ic_gross_ret"] = segment.iloc[0]["settle"] / entry_settle - 1.0
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
                "observations_held": int(len(contract_rows)),
            }
        )

    daily = pd.concat(daily_parts, ignore_index=True).sort_values("date").reset_index(drop=True)
    if daily["date"].duplicated().any():
        raise RuntimeError("Continuous IC curve has duplicate dates")
    daily["cost_rate"] = 0.0
    daily.loc[daily.index[0], "cost_rate"] = ONE_WAY_COST
    daily["roll_from"] = pd.NA
    daily["roll_to"] = pd.NA
    for idx, cycle in enumerate(cycles[:-1]):
        if not cycle.complete:
            continue
        mask = daily["date"].eq(cycle.exit_date)
        if mask.sum() != 1:
            raise RuntimeError(f"Roll date missing or duplicated: {cycle.exit_date.date()}")
        daily.loc[mask, "cost_rate"] += 2 * ONE_WAY_COST
        daily.loc[mask, "roll_from"] = cycle.contract
        daily.loc[mask, "roll_to"] = cycles[idx + 1].contract
    daily["ic_net_ret"] = (1.0 + daily["ic_gross_ret"]) * (1.0 - daily["cost_rate"]) - 1.0
    return daily, pd.DataFrame(schedule_rows)


def validate_pre_settle(futures: pd.DataFrame) -> dict[str, object]:
    ordered = futures.sort_values(["contract", "date"]).copy()
    ordered["prior_observed_settle"] = ordered.groupby("contract")["settle"].shift(1)
    comparable = ordered.dropna(subset=["prior_observed_settle", "pre_settle"]).copy()
    comparable["difference"] = comparable["pre_settle"] - comparable["prior_observed_settle"]
    mismatches = comparable[comparable["difference"].abs() > 1e-8]
    return {
        "comparable_rows": int(len(comparable)),
        "mismatch_rows": int(len(mismatches)),
        "max_abs_difference": float(mismatches["difference"].abs().max()) if len(mismatches) else 0.0,
        "sample": mismatches[["date", "contract", "pre_settle", "prior_observed_settle", "difference"]]
        .head(20)
        .assign(date=lambda x: x["date"].dt.strftime("%Y-%m-%d"))
        .to_dict("records"),
    }


def merge_indices(
    futures_daily: pd.DataFrame,
    price_index: pd.DataFrame,
    total_return_index: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, object]]:
    price = price_index[["date", "close"]].rename(columns={"close": "csi500_price_close"})
    tri = total_return_index[["date", "close"]].rename(columns={"close": "csi500_tri_close"})
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
    if daily.empty:
        raise RuntimeError("No common dates among IC, CSI 500 price index, and CSI 500 total-return index")
    expected_futures = futures_daily[futures_daily["date"].between(daily["date"].min(), daily["date"].max())]
    if len(daily) != len(expected_futures):
        raise RuntimeError(f"Index alignment removed {len(expected_futures) - len(daily)} IC trading dates")

    daily["csi500_price_ret"] = daily["csi500_price_close"].pct_change().fillna(0.0)
    daily["csi500_tri_ret"] = daily["csi500_tri_close"].pct_change().fillna(0.0)
    daily["gross_vs_price_ret"] = (1.0 + daily["ic_gross_ret"]) / (1.0 + daily["csi500_price_ret"]) - 1.0
    daily["net_vs_price_ret"] = (1.0 + daily["ic_net_ret"]) / (1.0 + daily["csi500_price_ret"]) - 1.0
    daily["gross_vs_tri_ret"] = (1.0 + daily["ic_gross_ret"]) / (1.0 + daily["csi500_tri_ret"]) - 1.0

    return_columns = {
        "ic_gross": "ic_gross_ret",
        "ic_net_1bp_per_side": "ic_net_ret",
        "csi500_price": "csi500_price_ret",
        "csi500_total_return": "csi500_tri_ret",
        "ic_gross_vs_price": "gross_vs_price_ret",
        "ic_net_vs_price": "net_vs_price_ret",
        "ic_gross_vs_total_return": "gross_vs_tri_ret",
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
        return {"total_return": np.nan, "cagr": np.nan, "max_drawdown": np.nan, "annual_volatility": np.nan, "sharpe_0rf": np.nan}
    nav = pd.concat([pd.Series([1.0]), (1.0 + clean.reset_index(drop=True)).cumprod()], ignore_index=True)
    total_return = float(nav.iloc[-1] - 1.0)
    cagr = float(nav.iloc[-1] ** (TRADING_DAYS / len(clean)) - 1.0)
    max_drawdown = float((nav / nav.cummax() - 1.0).min())
    volatility = float(clean.std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(clean) > 1 else np.nan
    sharpe = float(clean.mean() / clean.std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(clean) > 1 and clean.std(ddof=1) > 0 else np.nan
    return {
        "total_return": total_return,
        "cagr": cagr,
        "max_drawdown": max_drawdown,
        "annual_volatility": volatility,
        "sharpe_0rf": sharpe,
    }


def window_starts(end_date: pd.Timestamp) -> list[tuple[str, pd.Timestamp]]:
    return [
        ("full", START_DATE),
        ("10y", end_date - pd.DateOffset(years=10)),
        ("5y", end_date - pd.DateOffset(years=5)),
        ("3y", end_date - pd.DateOffset(years=3)),
        ("1y", end_date - pd.DateOffset(years=1)),
    ]


def metrics_by_window(daily: pd.DataFrame) -> pd.DataFrame:
    series = {
        "ic_gross": "ic_gross_ret",
        "ic_net_1bp_per_side": "ic_net_ret",
        "csi500_price": "csi500_price_ret",
        "csi500_total_return": "csi500_tri_ret",
        "ic_gross_vs_price": "gross_vs_price_ret",
        "ic_net_vs_price": "net_vs_price_ret",
        "ic_gross_vs_total_return": "gross_vs_tri_ret",
    }
    end_date = pd.Timestamp(daily["date"].max())
    rows: list[dict[str, object]] = []
    for window, cutoff in window_starts(end_date):
        subset = daily[daily["date"] >= cutoff].copy()
        actual_start = pd.Timestamp(subset["date"].min())
        for label, column in series.items():
            metrics = metric_from_returns(subset[column])
            rows.append(
                {
                    "window": window,
                    "series": label,
                    "requested_start": cutoff.date().isoformat(),
                    "actual_start": actual_start.date().isoformat(),
                    "end": end_date.date().isoformat(),
                    "trading_days": int(len(subset)),
                    **metrics,
                }
            )
    return pd.DataFrame(rows)


def percent(value: float) -> str:
    return "N/A" if pd.isna(value) else f"{value:.2%}"


def render_window_table(metrics: pd.DataFrame, selected_series: list[str]) -> str:
    header = "| 窗口 | " + " | ".join(f"{series} CAGR / MaxDD" for series in selected_series) + " |"
    divider = "|---|" + "---|" * len(selected_series)
    lines = [header, divider]
    for window in ["full", "10y", "5y", "3y", "1y"]:
        cells = []
        for series in selected_series:
            row = metrics[(metrics["window"] == window) & (metrics["series"] == series)].iloc[0]
            cells.append(f"{percent(row['cagr'])} / {percent(row['max_drawdown'])}")
        lines.append(f"| {window} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def write_record(
    output_dir: Path,
    daily: pd.DataFrame,
    metrics: pd.DataFrame,
    monthly: pd.DataFrame,
    manifest: dict[str, object],
) -> None:
    completed = monthly[monthly["complete"]].copy()
    cycle_stats = {
        "count": int(len(completed)),
        "mean": float(completed["basis_excess_vs_price"].mean()),
        "median": float(completed["basis_excess_vs_price"].median()),
        "win_rate": float((completed["basis_excess_vs_price"] > 0).mean()),
        "mean_entry_discount": float(completed["entry_discount_over_futures"].mean()),
        "median_entry_discount": float(completed["entry_discount_over_futures"].median()),
    }
    current = monthly.iloc[-1]
    main_table = render_window_table(
        metrics,
        ["ic_gross", "ic_net_1bp_per_side", "csi500_price", "csi500_total_return"],
    )
    attribution_table = render_window_table(
        metrics,
        ["ic_gross_vs_price", "ic_net_vs_price", "ic_gross_vs_total_return"],
    )
    record = f"""# IC 逐月到期展期贴水研究 v1：结果记录

运行日期：{date.today().isoformat()}  
研究状态：研究审计；未获准实盘  
正式样本：{daily['date'].min().date().isoformat()} 至 {daily['date'].max().date().isoformat()}  
数据：中金所官方 IC 月合约结算价；中证指数官方 000905 与 H00905

## 结论摘要

- IC 逐月到期展期总收益必须与指数方向收益分开理解。下表中的 `ic_gross_vs_price` 才是本研究定义的历史贴水/基差超额近似。
- 完成周期 {cycle_stats['count']} 个；相对价格指数月度基差超额均值 {percent(cycle_stats['mean'])}，中位数 {percent(cycle_stats['median'])}，正值比例 {percent(cycle_stats['win_rate'])}。
- 完成周期的入场贴水均值 {percent(cycle_stats['mean_entry_discount'])}，中位数 {percent(cycle_stats['median_entry_discount'])}。
- 最新合约 {current['contract']} 截至 {pd.Timestamp(current['exit_date']).date().isoformat()} 为{'已完成' if current['complete'] else '未完成、仅盯市'}周期，不是交易信号。
- 相对价格指数的超额包含“价格指数不含现金股息”的口径效应；相对全收益指数的列更接近经济比较，但本版未给抵押现金计息。

## 强制窗口：总收益与基准

{main_table}

## 强制窗口：贴水/基差归因

{attribution_table}

## 执行与成本

- 1倍名义、完全抵押口径；主曲线用官方结算价，合约持有至最后交易日。
- 到期日以旧合约最终结算结束，并以同日下一自然月合约结算价进入；不把两合约价差计为即时收益。
- `ic_net_1bp_per_side` 为每边1bp统一敏感性：初始1bp、每次换月2bp。未使用具体券商历史费率。
- 不计保证金杠杆、抵押现金利息、税、盘口冲击或保证金追缴；最终结算价也不是可保证成交的盘中价格。

## 完整性检查

- 官方月包：{manifest['cffex']['archive_count']} 个，{manifest['cffex']['first_month']} 至 {manifest['cffex']['last_month']}；解析 IC 行数 {manifest['cffex']['rows']}。
- 共同交易日：{manifest['common_sample']['rows']}；日期对齐删除的期货日数 {len(manifest['calendar_alignment']['futures_dates_not_common'])}。
- `pre_settle` 与同合约上一日 `settle` 可比行 {manifest['pre_settle_check']['comparable_rows']}，不一致 {manifest['pre_settle_check']['mismatch_rows']}，最大绝对差 {manifest['pre_settle_check']['max_abs_difference']:.6f} 点。
- 绝对日收益超过10%的 IC 记录数：{manifest['extreme_return_count']}；详见 `extreme_returns.csv`。

## 证据与复现

- 预注册规格：`docs/ic_monthly_discount_roll_v1_spec.md`（SHA-256 `{manifest['spec_sha256']}`）。
- 脚本：`ic_monthly_discount_roll_v1.py`（SHA-256 `{manifest['script_sha256']}`）。
- 命令：`{manifest['command']}`。
- 原始月包和指数窄表保存在 `data/ic_monthly_discount_roll_v1/`；清单与每月 SHA-256 见本目录 `download_log.csv` 和 `data_manifest.json`。

## 限制与后续

- 本结果观察自真实官方日数据，不是推算；但“到期结算后同价换月”是机制研究口径，不等于可成交的实盘路径。
- 若研究可执行版本，应另建版本，在到期日前固定可交易时点换月，加入真实买卖价、佣金、滑点、保证金与抵押现金收益。
- IM、创业板ETF期权、科创50ETF期权没有混入本版回测；它们需要独立的数据审计与预注册。
"""
    (output_dir / "record.md").write_text(record, encoding="utf-8")


def run(end_date: pd.Timestamp, data_dir: Path, output_dir: Path, refresh: bool) -> None:
    if output_dir.exists():
        raise FileExistsError(f"Formal output directory already exists and will not be overwritten: {output_dir}")
    if end_date < START_DATE:
        raise ValueError("End date is before IC inception")
    if not SPEC_PATH.exists():
        raise FileNotFoundError(f"Frozen specification missing: {SPEC_PATH}")

    data_dir.mkdir(parents=True, exist_ok=True)
    zip_paths, download_log = download_cffex_months(data_dir, end_date, refresh)
    futures = parse_cffex_ic(zip_paths, end_date)
    price_index = fetch_csindex("000905", START_DATE, end_date)
    total_return_index = fetch_csindex("H00905", START_DATE, end_date)

    futures.to_csv(data_dir / "cffex_ic_contracts.csv", index=False, encoding="utf-8-sig")
    price_index.to_csv(data_dir / "csindex_000905.csv", index=False, encoding="utf-8-sig")
    total_return_index.to_csv(data_dir / "csindex_H00905.csv", index=False, encoding="utf-8-sig")

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
    pre_settle_check = validate_pre_settle(futures)
    extremes = daily.loc[
        daily["ic_gross_ret"].abs() > 0.10,
        ["date", "contract", "settle", "ic_gross_ret", "csi500_price_ret", "roll_from", "roll_to"],
    ].copy()

    command = f"{Path(sys.executable).name} {Path(__file__).name} --end-date {end_date.date().isoformat()}"
    if refresh:
        command += " --refresh"
    manifest: dict[str, object] = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "research_status": "research_only_not_approved_for_live_trading",
        "command": command,
        "script_sha256": sha256_file(Path(__file__)),
        "spec_sha256": sha256_file(SPEC_PATH),
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
            "price_symbol": "000905",
            "total_return_symbol": "H00905",
            "price_rows": int(len(price_index)),
            "total_return_rows": int(len(total_return_index)),
            "first_date": daily["date"].min().date().isoformat(),
            "last_date": daily["date"].max().date().isoformat(),
            "adjustment_mode": "official price index and official total-return index; no adjustment applied",
        },
        "common_sample": {
            "rows": int(len(daily)),
            "start": daily["date"].min().date().isoformat(),
            "end": daily["date"].max().date().isoformat(),
            "timezone": "Asia/Shanghai",
        },
        "execution": {
            "notional_exposure": "1x fully collateralized",
            "roll": "hold front calendar-month IC to final settlement; enter next calendar month at same-day settlement",
            "one_way_cost_sensitivity": ONE_WAY_COST,
            "collateral_interest": "excluded",
            "margin_leverage": "excluded",
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
    extremes.to_csv(output_dir / "extreme_returns.csv", index=False, encoding="utf-8-sig")
    (output_dir / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (output_dir / "command_log.txt").write_text(command + "\n", encoding="utf-8")
    write_record(output_dir, daily, metrics, monthly, manifest)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="IC monthly hold-to-expiry roll research v1")
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
