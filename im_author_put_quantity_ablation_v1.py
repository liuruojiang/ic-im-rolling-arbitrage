from __future__ import annotations

import hashlib
import io
import json
import math
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import requests

from ic_monthly_discount_roll_v1 import _decode_cffex_csv, fetch_csindex, third_friday


ROOT = Path(__file__).resolve().parent
VERSION = "im_author_put_quantity_ablation_v1"
START = pd.Timestamp("2022-07-22")
END = pd.Timestamp("2026-08-14")
INITIAL_CAPITAL = 10_000_000.0
IM_MULTIPLIER = 200.0
MO_MULTIPLIER = 100.0
FUTURES_SIDE_COST = 0.0001
PUT_FEE_PER_SIDE = 18.0
CASH_ANNUAL = 0.03
CASH_DAILY = (1.0 + CASH_ANNUAL) ** (1.0 / 252.0) - 1.0
MARGIN_BUFFER_RATE = 0.30
BASE_NOTIONAL_WEIGHT = 0.30
PUT_CAP_PER_IM_LOT = 160_000.0
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "b0e254ca27953fd3bd9ca9361ad25699bbc5effb294c5cfcf1670e9a90ed6db2"
DATA_DIR = ROOT / "data" / VERSION
RAW_DIR = DATA_DIR / "cffex_raw"
OUTPUT_DIR = ROOT / "artifacts" / VERSION
CFFEX_URL = "http://www.cffex.com.cn/sj/historysj/{ym}/zip/{ym}.zip"
USER_AGENT = "Mozilla/5.0 (Codex author-Put real-data research/1.0)"
IM_RE = re.compile(r"^IM(?P<yy>\d{2})(?P<mm>\d{2})$")
MO_PUT_RE = re.compile(r"^MO(?P<yy>\d{2})(?P<mm>\d{2})-P-(?P<strike>\d+)$")
VARIANTS = {
    "P0": {"scale": 0.0, "core_only": False, "allow_reduce": True},
    "P-core": {"scale": 1.0, "core_only": True, "allow_reduce": False},
    "P25": {"scale": 0.25, "core_only": False, "allow_reduce": False},
    "P50": {"scale": 0.50, "core_only": False, "allow_reduce": False},
    "P75": {"scale": 0.75, "core_only": False, "allow_reduce": False},
    "P100": {"scale": 1.0, "core_only": False, "allow_reduce": False},
    "P100-R": {"scale": 1.0, "core_only": False, "allow_reduce": True},
}
WINDOWS = {
    "full": None,
    "last_3y": pd.DateOffset(years=3),
    "last_1y": pd.DateOffset(years=1),
}


@dataclass
class FutureLot:
    lot_id: str
    kind: str
    contract: str
    mark: float
    equivalent_cost: float | None = None
    grid_level: float | None = None
    entry_date: pd.Timestamp | None = None


@dataclass
class PutState:
    contract: str | None = None
    qty: int = 0
    mark: float = 0.0
    book_cost: float = 0.0
    realized_pnl: float = 0.0
    cash: float = INITIAL_CAPITAL
    buy_premium: float = 0.0
    sell_proceeds: float = 0.0
    fees: float = 0.0
    slippage: float = 0.0
    interest_total: float = 0.0
    invalid_orders: int = 0
    cap_binding_days: int = 0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_spec() -> None:
    if not SPEC.exists() or not SPEC_HASH_FILE.exists():
        raise FileNotFoundError("Frozen preregistration specification is missing")
    expected_sidecar = SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower()
    actual = sha256_file(SPEC)
    if expected_sidecar != SPEC_SHA256 or actual != SPEC_SHA256:
        raise RuntimeError(
            f"Frozen specification mismatch: constant={SPEC_SHA256}, sidecar={expected_sidecar}, actual={actual}"
        )


def month_keys(start: pd.Timestamp, end: pd.Timestamp) -> list[str]:
    return [value.strftime("%Y%m") for value in pd.date_range(start.replace(day=1), end.replace(day=1), freq="MS")]


def _download_month(ym: str, refresh: bool = False) -> dict[str, Any]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"{ym}.zip"
    url = CFFEX_URL.format(ym=ym)
    if path.exists() and not refresh and zipfile.is_zipfile(path):
        source = "cache"
    else:
        source = "download"
        last_error: Exception | None = None
        for attempt in range(1, 6):
            try:
                response = requests.get(
                    url,
                    headers={"User-Agent": USER_AGENT},
                    timeout=(20, 180),
                )
                response.raise_for_status()
                payload = response.content
                if not zipfile.is_zipfile(io.BytesIO(payload)):
                    raise RuntimeError(f"CFFEX payload is not ZIP for {ym}")
                path.write_bytes(payload)
                break
            except Exception as exc:  # retry official endpoint failures
                last_error = exc
                if attempt == 5:
                    raise RuntimeError(f"Failed to download {url}: {exc}") from exc
                time.sleep(2.0 * attempt)
        if last_error is not None and not path.exists():
            raise last_error
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if re.fullmatch(r"\d{8}_1\.csv", Path(name).name)]
        if not members:
            raise RuntimeError(f"No daily settlement CSV in {path}")
        dates = sorted(pd.Timestamp(Path(name).name[:8]) for name in members)
    return {
        "month": ym,
        "path": str(path),
        "url": url,
        "source": source,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "member_count": len(members),
        "first_date": dates[0].date().isoformat(),
        "last_date": dates[-1].date().isoformat(),
    }


def download_archives() -> pd.DataFrame:
    keys = month_keys(pd.Timestamp("2022-07-01"), pd.Timestamp("2026-08-01"))
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(_download_month, ym): ym for ym in keys}
        for future in as_completed(futures):
            rows.append(future.result())
    frame = pd.DataFrame(rows).sort_values("month").reset_index(drop=True)
    if len(frame) != len(keys):
        raise RuntimeError("CFFEX archive download count mismatch")
    return frame


def parse_cffex(download_log: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    im_frames: list[pd.DataFrame] = []
    put_frames: list[pd.DataFrame] = []
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
        13: "delta",
    }
    for raw_path in download_log["path"]:
        path = Path(raw_path)
        with zipfile.ZipFile(path) as archive:
            for member in archive.namelist():
                name = Path(member).name
                match = re.fullmatch(r"(?P<day>\d{8})_1\.csv", name)
                if not match:
                    continue
                trade_date = pd.Timestamp(match.group("day"))
                raw = pd.read_csv(
                    io.StringIO(_decode_cffex_csv(archive.read(member))),
                    header=None,
                    skiprows=1,
                    dtype=str,
                    on_bad_lines="error",
                )
                if raw.shape[1] < 14:
                    raise RuntimeError(f"Unexpected CFFEX schema {path.name}/{member}: {raw.shape[1]}")
                frame = raw[list(fields)].rename(columns=fields)
                frame["contract"] = frame["contract"].str.strip()
                for column in fields.values():
                    if column == "contract":
                        continue
                    frame[column] = pd.to_numeric(
                        frame[column].replace({"--": np.nan, "null": np.nan, "": np.nan}), errors="coerce"
                    )
                im = frame[frame["contract"].str.fullmatch(r"IM\d{4}", na=False)].copy()
                if not im.empty:
                    im.insert(1, "date", trade_date)
                    im_frames.append(im)
                put = frame[frame["contract"].str.fullmatch(r"MO\d{4}-P-\d+", na=False)].copy()
                if not put.empty:
                    parsed = put["contract"].str.extract(MO_PUT_RE)
                    put.insert(1, "date", trade_date)
                    put["expiry_month"] = pd.to_datetime(
                        "20" + parsed["yy"] + "-" + parsed["mm"] + "-01", errors="raise"
                    )
                    put["strike"] = pd.to_numeric(parsed["strike"], errors="raise")
                    put_frames.append(put)
    if not im_frames or not put_frames:
        raise RuntimeError("Official IM or MO Put rows are missing")
    im = pd.concat(im_frames, ignore_index=True).sort_values(["date", "contract"]).reset_index(drop=True)
    puts = pd.concat(put_frames, ignore_index=True).sort_values(["date", "contract"]).reset_index(drop=True)
    for name, frame in (("IM", im), ("MO", puts)):
        dup = frame.duplicated(["date", "contract"], keep=False)
        if dup.any():
            raise RuntimeError(f"Duplicate {name} date-contract rows: {frame.loc[dup, ['date','contract']].head().to_dict('records')}")
    im = im[im["settle"].gt(0)].copy()
    puts = puts[puts["settle"].gt(0)].copy()
    return im, puts


def contract_month(code: str) -> pd.Timestamp:
    match = IM_RE.fullmatch(code)
    if not match:
        raise ValueError(code)
    return pd.Timestamp(year=2000 + int(match.group("yy")), month=int(match.group("mm")), day=1)


def next_im_contract(code: str) -> str:
    month = contract_month(code) + pd.DateOffset(months=1)
    return f"IM{month.strftime('%y%m')}"


def observed_expiry_map(frame: pd.DataFrame, kind: str) -> dict[str, pd.Timestamp]:
    result: dict[str, pd.Timestamp] = {}
    for contract, rows in frame.groupby("contract", sort=False):
        if kind == "IM":
            month = contract_month(contract)
        else:
            parsed = MO_PUT_RE.fullmatch(contract)
            if parsed is None:
                continue
            month = pd.Timestamp(year=2000 + int(parsed.group("yy")), month=int(parsed.group("mm")), day=1)
        rule = third_friday(month)
        observed = pd.Timestamp(rows["date"].max())
        if observed.year == month.year and observed.month == month.month and abs((observed - rule).days) <= 7:
            result[contract] = observed
        else:
            result[contract] = rule
    return result


def build_market(im_all: pd.DataFrame, puts_all: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, pd.Timestamp], dict[str, pd.Timestamp]]:
    index = fetch_csindex("000852", START - pd.Timedelta(days=10), END)
    index = index[(index["date"] >= START) & (index["date"] <= END)][["date", "close"]].rename(
        columns={"close": "spot_close"}
    )
    im = im_all[(im_all["date"] >= START) & (im_all["date"] <= END)].copy()
    puts = puts_all[(puts_all["date"] >= START) & (puts_all["date"] <= END)].copy()
    trade_dates = pd.DatetimeIndex(sorted(set(im["date"]) & set(index["date"])))
    index = index[index["date"].isin(trade_dates)].sort_values("date").reset_index(drop=True)
    if len(index) < 950:
        raise RuntimeError(f"Too few common official IM/index dates: {len(index)}")
    im_expiry = observed_expiry_map(im_all, "IM")
    put_expiry = observed_expiry_map(puts_all, "MO")
    puts["expiry_date"] = puts["contract"].map(put_expiry)
    puts["dte"] = (puts["expiry_date"] - puts["date"]).dt.days
    return index, im, im_expiry, put_expiry


def im_value(im_lookup: pd.DataFrame, contract: str, day: pd.Timestamp, column: str) -> float:
    try:
        value = im_lookup.loc[(day, contract), column]
    except KeyError as exc:
        raise RuntimeError(f"Missing {column} for {contract} on {day.date()}") from exc
    if isinstance(value, pd.Series):
        value = value.iloc[-1]
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise RuntimeError(f"Invalid {column} for {contract} on {day.date()}: {result}")
    return result


def build_roll_schedule(dates: pd.DatetimeIndex, im: pd.DataFrame, expiries: dict[str, pd.Timestamp]) -> dict[pd.Timestamp, tuple[str, str]]:
    available = set(im["contract"])
    valid_close = set(
        zip(
            pd.to_datetime(im.loc[im["close"].gt(0), "date"]),
            im.loc[im["close"].gt(0), "contract"].astype(str),
        )
    )
    schedule: dict[pd.Timestamp, tuple[str, str]] = {}
    contract = "IM2208"
    while contract in available:
        nxt = next_im_contract(contract)
        if nxt not in available:
            break
        expiry = expiries[contract]
        target = expiry - pd.Timedelta(days=3)
        candidates = [
            day for day in dates[(dates >= target) & (dates <= expiry)]
            if (pd.Timestamp(day), contract) in valid_close and (pd.Timestamp(day), nxt) in valid_close
        ]
        if not candidates:
            raise RuntimeError(f"No executable roll date for {contract}->{nxt}")
        roll_day = pd.Timestamp(min(candidates))
        if START <= roll_day <= END:
            schedule[roll_day] = (contract, nxt)
        contract = nxt
    return schedule


def futures_side_cost(price: float, lots: int) -> float:
    return abs(lots) * price * IM_MULTIPLIER * FUTURES_SIDE_COST


def close_future_lot(lot: FutureLot, day: pd.Timestamp, price: float, current_mark: float) -> tuple[float, float]:
    execution_adjustment = (price - current_mark) * IM_MULTIPLIER
    cost = futures_side_cost(price, 1)
    return execution_adjustment, cost


def simulate_common_futures(index: pd.DataFrame, im: pd.DataFrame, expiries: dict[str, pd.Timestamp]) -> pd.DataFrame:
    dates = pd.DatetimeIndex(index["date"])
    spot_map = index.set_index("date")["spot_close"]
    im_lookup = im.set_index(["date", "contract"]).sort_index()
    rolls = build_roll_schedule(dates, im, expiries)
    current_contract = "IM2208"
    lots: list[FutureLot] = []
    pending_entries: list[float] = []
    pending_exits: set[str] = set()
    anchor: float | None = None
    frozen_anchor: float | None = None
    previous_spot: float | None = None
    p0_cash = INITIAL_CAPITAL
    prior_nav = INITIAL_CAPITAL
    prior_margin = 0.0
    rows: list[dict[str, Any]] = []
    grid_counter = 0

    for idx, day in enumerate(dates):
        spot = float(spot_map.loc[day])
        interest = max(p0_cash - prior_margin, 0.0) * CASH_DAILY if idx > 0 else 0.0
        p0_cash += interest
        flow = 0.0
        futures_pnl = 0.0
        futures_cost = 0.0

        # Mark all carried futures lots to official settlement.
        for lot in lots:
            settle = im_value(im_lookup, lot.contract, day, "settle")
            pnl = (settle - lot.mark) * IM_MULTIPLIER
            p0_cash += pnl
            flow += pnl
            futures_pnl += pnl
            lot.mark = settle

        rolled = False
        roll_from = ""
        roll_to = ""
        if day in rolls:
            old, new = rolls[day]
            if current_contract != old:
                raise RuntimeError(f"Roll-state mismatch on {day.date()}: {current_contract} vs {old}")
            old_close = im_value(im_lookup, old, day, "close")
            new_close = im_value(im_lookup, new, day, "close")
            for lot in lots:
                if lot.contract != old:
                    raise RuntimeError("Mixed futures contracts before roll")
                adjust, close_cost = close_future_lot(lot, day, old_close, lot.mark)
                open_cost = futures_side_cost(new_close, 1)
                p0_cash += adjust - close_cost - open_cost
                flow += adjust - close_cost - open_cost
                futures_pnl += adjust
                futures_cost += close_cost + open_cost
                lot.contract = new
                lot.mark = new_close
                if lot.kind == "grid" and lot.equivalent_cost is not None:
                    lot.equivalent_cost += new_close - old_close
            current_contract = new
            rolled = True
            roll_from, roll_to = old, new

        execution_price = im_value(im_lookup, current_contract, day, "close")
        execution_mark = execution_price if rolled else im_value(im_lookup, current_contract, day, "settle")

        # Execute pending grid exits first.
        if pending_exits:
            survivors: list[FutureLot] = []
            for lot in lots:
                if lot.lot_id in pending_exits:
                    adjust, cost = close_future_lot(lot, day, execution_price, lot.mark)
                    p0_cash += adjust - cost
                    flow += adjust - cost
                    futures_pnl += adjust
                    futures_cost += cost
                else:
                    survivors.append(lot)
            lots = survivors
            pending_exits.clear()

        # Annual base rebalance at the first common trading day of each year, plus initial day.
        current_base = sum(lot.kind == "base" for lot in lots)
        first_of_year = idx == 0 or (idx > 0 and dates[idx - 1].year != day.year)
        if first_of_year:
            target_base = int(math.floor(BASE_NOTIONAL_WEIGHT * prior_nav / (execution_price * IM_MULTIPLIER)))
            if spot < 5000.0 and target_base > current_base:
                target_base = current_base
            if target_base < current_base:
                reduction = current_base - target_base
                survivors = []
                for lot in lots:
                    if lot.kind == "base" and reduction > 0:
                        adjust, cost = close_future_lot(lot, day, execution_price, lot.mark)
                        p0_cash += adjust - cost
                        flow += adjust - cost
                        futures_pnl += adjust
                        futures_cost += cost
                        reduction -= 1
                    else:
                        survivors.append(lot)
                lots = survivors
            elif target_base > current_base:
                for number in range(target_base - current_base):
                    cost = futures_side_cost(execution_price, 1)
                    p0_cash -= cost
                    flow -= cost
                    futures_cost += cost
                    lots.append(
                        FutureLot(
                            lot_id=f"base-{day.strftime('%Y%m%d')}-{number}",
                            kind="base",
                            contract=current_contract,
                            mark=execution_price,
                            entry_date=day,
                        )
                    )

        # Execute grid entries generated on the prior day.
        if pending_entries:
            for level in sorted(pending_entries, reverse=True):
                cost = futures_side_cost(execution_price, 1)
                p0_cash -= cost
                flow -= cost
                futures_cost += cost
                grid_counter += 1
                lots.append(
                    FutureLot(
                        lot_id=f"grid-{grid_counter:04d}",
                        kind="grid",
                        contract=current_contract,
                        mark=execution_price,
                        equivalent_cost=execution_price,
                        grid_level=level,
                        entry_date=day,
                    )
                )
            if frozen_anchor is None:
                frozen_anchor = anchor
            pending_entries = []

        base_lots = sum(lot.kind == "base" for lot in lots)
        grid_lots = sum(lot.kind == "grid" for lot in lots)
        total_lots = base_lots + grid_lots
        margin_end = MARGIN_BUFFER_RATE * total_lots * execution_price * IM_MULTIPLIER
        p0_nav = p0_cash
        buffer_shortfall = max(margin_end - p0_cash, 0.0)

        # Generate next-day exits from today's activity contract close.
        for lot in lots:
            if lot.kind == "grid" and lot.equivalent_cost is not None:
                if execution_price >= lot.equivalent_cost + 1000.0 - 1e-12:
                    pending_exits.add(lot.lot_id)

        # Reset or update anchor after actual executions.
        open_grid_levels = {float(lot.grid_level) for lot in lots if lot.kind == "grid" and lot.grid_level is not None}
        if grid_lots == 0 and not pending_entries:
            if frozen_anchor is not None:
                anchor = spot
                frozen_anchor = None
            else:
                anchor = spot if anchor is None else max(anchor, spot)
        else:
            if frozen_anchor is None:
                frozen_anchor = anchor

        # Generate next-day entries only on a fresh downward crossing; never chase below 5000.
        top = frozen_anchor if frozen_anchor is not None else anchor
        if top is not None and previous_spot is not None:
            level = float(top) - 1000.0
            levels: list[float] = []
            while level > 0:
                levels.append(level)
                level -= 1000.0
            pending_level_set = set(pending_entries)
            for candidate in levels:
                crossed = previous_spot > candidate >= spot
                if not crossed:
                    continue
                if candidate in open_grid_levels or candidate in pending_level_set:
                    continue
                if spot < 5000.0:
                    continue
                pending_entries.append(candidate)
                pending_level_set.add(candidate)
            if pending_entries and frozen_anchor is None:
                frozen_anchor = anchor

        rows.append(
            {
                "date": day,
                "spot_close": spot,
                "active_contract_after_close": current_contract,
                "im_close_after_actions": execution_price,
                "base_lots": base_lots,
                "grid_lots": grid_lots,
                "total_lots": total_lots,
                "futures_pnl": futures_pnl,
                "futures_cost": futures_cost,
                "futures_net_flow": flow,
                "p0_interest": interest,
                "p0_nav": p0_nav,
                "margin_reserve_end": margin_end,
                "buffer_shortfall": buffer_shortfall,
                "grid_anchor": anchor,
                "frozen_grid_anchor": frozen_anchor,
                "pending_grid_entries": len(pending_entries),
                "pending_grid_exits": len(pending_exits),
                "rolled": rolled,
                "roll_from": roll_from,
                "roll_to": roll_to,
            }
        )
        prior_nav = p0_nav
        prior_margin = margin_end
        previous_spot = spot

    result = pd.DataFrame(rows)
    if (result["base_lots"] <= 0).all():
        raise RuntimeError("Base IM path never opened")
    if not np.allclose(result["p0_nav"].values, INITIAL_CAPITAL + np.cumsum(result["futures_net_flow"] + result["p0_interest"]), atol=1e-6):
        raise RuntimeError("P0 cash-flow identity failed")
    return result


def select_put(day: pd.Timestamp, spot: float, puts_by_date: dict[pd.Timestamp, pd.DataFrame]) -> dict[str, Any] | None:
    chain = puts_by_date.get(day)
    if chain is None or chain.empty:
        return None
    eligible = chain[
        chain["settle"].gt(0)
        & (chain["volume"].fillna(0).gt(0) | chain["open_interest"].fillna(0).gt(0))
        & chain["dte"].gt(0)
    ].copy()
    if eligible.empty:
        return None
    months = eligible[["expiry_month", "expiry_date", "dte"]].drop_duplicates("expiry_month")
    in_window = months[months["dte"].between(30, 59)]
    if not in_window.empty:
        chosen_month = in_window.sort_values(["expiry_date"]).iloc[0]["expiry_month"]
    else:
        months = months.copy()
        months["window_distance"] = np.where(
            months["dte"] < 30, 30 - months["dte"], np.where(months["dte"] > 59, months["dte"] - 59, 0)
        )
        chosen_month = months.sort_values(["window_distance", "expiry_date"]).iloc[0]["expiry_month"]
    month_chain = eligible[eligible["expiry_month"].eq(chosen_month)].copy()
    selected = month_chain.sort_values(["strike", "contract"]).iloc[0]
    month_chain["atm_distance"] = (month_chain["strike"] - spot).abs()
    atm = month_chain.sort_values(["atm_distance", "strike", "contract"]).iloc[0]
    gain_points = max(float(atm["settle"]) - float(selected["settle"]), 0.0)
    return {
        "selected_contract": str(selected["contract"]),
        "selected_strike": float(selected["strike"]),
        "selected_settle": float(selected["settle"]),
        "selected_close": float(selected["close"]) if pd.notna(selected["close"]) else np.nan,
        "selected_volume": float(selected["volume"]) if pd.notna(selected["volume"]) else 0.0,
        "selected_open_interest": float(selected["open_interest"]) if pd.notna(selected["open_interest"]) else 0.0,
        "expiry_date": pd.Timestamp(selected["expiry_date"]),
        "dte": int(selected["dte"]),
        "atm_contract": str(atm["contract"]),
        "atm_strike": float(atm["strike"]),
        "atm_settle": float(atm["settle"]),
        "gain_points": gain_points,
    }


def put_quote(put_lookup: pd.DataFrame, day: pd.Timestamp, contract: str, column: str) -> float | None:
    try:
        value = put_lookup.loc[(day, contract), column]
    except KeyError:
        return None
    if isinstance(value, pd.Series):
        value = value.iloc[-1]
    if pd.isna(value):
        return None
    return float(value)


def signal_for_variant(
    variant: str,
    rule: dict[str, Any],
    selection: dict[str, Any] | None,
    base_lots: int,
    total_lots: int,
    spot: float,
) -> dict[str, Any]:
    if variant == "P0" or selection is None or total_lots <= 0:
        return {
            "desired_contract": None,
            "target_qty": 0,
            "cap_qty": 0,
            "raw_full_qty": 0,
            "protected_lots": 0,
            "cap_binding": False,
            "selection": selection,
        }
    protected_lots = base_lots if rule["core_only"] else total_lots
    if protected_lots <= 0 or selection["gain_points"] <= 0:
        raw_full = 0
    else:
        im_loss = max(spot - selection["selected_strike"], 0.0) * IM_MULTIPLIER * protected_lots
        per_put_gain = selection["gain_points"] * MO_MULTIPLIER
        raw_full = int(math.ceil(im_loss / per_put_gain - 1e-12)) if per_put_gain > 0 else 0
    scaled = int(math.ceil(raw_full * float(rule["scale"]) - 1e-12)) if raw_full > 0 else 0
    cap_lots = protected_lots if rule["core_only"] else total_lots
    cap_value = PUT_CAP_PER_IM_LOT * cap_lots
    selected_mv = selection["selected_settle"] * MO_MULTIPLIER
    cap_qty = int(math.floor(cap_value / selected_mv + 1e-12)) if selected_mv > 0 else 0
    target = min(scaled, cap_qty)
    return {
        "desired_contract": selection["selected_contract"],
        "target_qty": target,
        "cap_qty": cap_qty,
        "raw_full_qty": raw_full,
        "protected_lots": protected_lots,
        "cap_binding": scaled > cap_qty,
        "selection": selection,
    }


def trade_price(center: float, side: str) -> tuple[float, float]:
    slip = max(center * 0.05, 0.2)
    if side == "buy":
        return center + slip, slip
    if side == "sell":
        return max(center - slip, 0.0), min(slip, center)
    raise ValueError(side)


def execute_sell(
    state: PutState,
    day: pd.Timestamp,
    contract: str,
    qty: int,
    center: float,
    transactions: list[dict[str, Any]],
    reason: str,
) -> None:
    if qty <= 0:
        return
    if qty > state.qty or state.qty <= 0:
        raise RuntimeError("Invalid Put sell quantity")
    price, slip = trade_price(center, "sell")
    proceeds = qty * price * MO_MULTIPLIER
    fee = qty * PUT_FEE_PER_SIDE
    allocated_cost = state.book_cost * qty / state.qty
    realized = proceeds - fee - allocated_cost
    state.cash += proceeds - fee
    state.sell_proceeds += proceeds
    state.fees += fee
    state.slippage += qty * slip * MO_MULTIPLIER
    state.realized_pnl += realized
    state.book_cost -= allocated_cost
    state.qty -= qty
    transactions.append(
        {
            "date": day,
            "contract": contract,
            "side": "sell",
            "qty": qty,
            "center_close": center,
            "exec_price": price,
            "fee": fee,
            "slippage_cash": qty * slip * MO_MULTIPLIER,
            "reason": reason,
        }
    )
    if state.qty == 0:
        state.contract = None
        state.mark = 0.0
        state.book_cost = 0.0


def execute_buy(
    state: PutState,
    day: pd.Timestamp,
    contract: str,
    qty: int,
    center: float,
    transactions: list[dict[str, Any]],
    reason: str,
) -> None:
    if qty <= 0:
        return
    if state.contract not in (None, contract):
        raise RuntimeError("Cannot buy a second Put contract before closing the old contract")
    price, slip = trade_price(center, "buy")
    premium = qty * price * MO_MULTIPLIER
    fee = qty * PUT_FEE_PER_SIDE
    state.cash -= premium + fee
    state.buy_premium += premium
    state.fees += fee
    state.slippage += qty * slip * MO_MULTIPLIER
    state.book_cost += premium + fee
    state.qty += qty
    state.contract = contract
    transactions.append(
        {
            "date": day,
            "contract": contract,
            "side": "buy",
            "qty": qty,
            "center_close": center,
            "exec_price": price,
            "fee": fee,
            "slippage_cash": qty * slip * MO_MULTIPLIER,
            "reason": reason,
        }
    )


def simulate_variant(
    variant: str,
    rule: dict[str, Any],
    common: pd.DataFrame,
    puts: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    state = PutState()
    put_lookup = puts.set_index(["date", "contract"]).sort_index()
    puts_by_date = {day: frame.copy() for day, frame in puts.groupby("date", sort=False)}
    instruction: dict[str, Any] | None = None
    daily_rows: list[dict[str, Any]] = []
    signal_rows: list[dict[str, Any]] = []
    transactions: list[dict[str, Any]] = []
    prior_margin = 0.0

    for idx, row in common.iterrows():
        day = pd.Timestamp(row["date"])
        interest = max(state.cash - prior_margin, 0.0) * CASH_DAILY if idx > 0 else 0.0
        state.cash += interest + float(row["futures_net_flow"])
        state.interest_total += interest

        # Existing option is marked to today's official settlement before close execution.
        current_settle = 0.0
        if state.contract is not None and state.qty > 0:
            quote = put_quote(put_lookup, day, state.contract, "settle")
            if quote is None or quote <= 0:
                quote = state.mark
            current_settle = float(quote)
            state.mark = current_settle

        executed = False
        execution_note = ""
        if instruction is not None and variant != "P0":
            desired = instruction["desired_contract"]
            target = int(instruction["target_qty"])
            cap_qty = int(instruction["cap_qty"])
            allow_reduce = bool(rule["allow_reduce"])
            old_contract = state.contract
            need_switch = old_contract is not None and old_contract != desired
            contracts_needed = [contract for contract in [old_contract if need_switch else None, desired if target > 0 else None] if contract]
            valid = True
            centers: dict[str, float] = {}
            for contract in contracts_needed:
                close = put_quote(put_lookup, day, contract, "close")
                volume = put_quote(put_lookup, day, contract, "volume")
                if close is None or close <= 0 or volume is None or volume <= 0:
                    valid = False
                    break
                centers[contract] = close
            if valid:
                if need_switch and old_contract is not None:
                    execute_sell(state, day, old_contract, state.qty, centers[old_contract], transactions, "contract_switch")
                if desired is None or target <= 0:
                    if state.contract is not None and state.qty > 0 and (allow_reduce or state.qty > cap_qty):
                        center = put_quote(put_lookup, day, state.contract, "close")
                        volume = put_quote(put_lookup, day, state.contract, "volume")
                        if center is not None and center > 0 and volume is not None and volume > 0:
                            execute_sell(state, day, state.contract, state.qty, center, transactions, "target_zero")
                else:
                    if state.contract is None:
                        execute_buy(state, day, desired, target, centers[desired], transactions, "new_contract")
                    elif state.contract == desired:
                        desired_qty = target if allow_reduce else max(state.qty, target)
                        desired_qty = min(desired_qty, cap_qty) if state.qty > cap_qty else desired_qty
                        if desired_qty > state.qty:
                            execute_buy(state, day, desired, desired_qty - state.qty, centers[desired], transactions, "target_increase")
                        elif desired_qty < state.qty:
                            execute_sell(state, day, desired, state.qty - desired_qty, centers[desired], transactions, "target_reduce_or_cap")
                executed = True
                execution_note = "executed"
            else:
                state.invalid_orders += 1
                execution_note = "invalid_t1_quote"

        # End-of-day option mark after any close transaction.
        option_mv = 0.0
        if state.contract is not None and state.qty > 0:
            settle = put_quote(put_lookup, day, state.contract, "settle")
            if settle is None or settle <= 0:
                settle = state.mark
            state.mark = float(settle)
            option_mv = state.qty * state.mark * MO_MULTIPLIER
        nav = state.cash + option_mv
        margin = float(row["margin_reserve_end"])
        capital_shortfall = max(margin - state.cash, 0.0)

        selection = select_put(day, float(row["spot_close"]), puts_by_date)
        instruction = signal_for_variant(
            variant,
            rule,
            selection,
            int(row["base_lots"]),
            int(row["total_lots"]),
            float(row["spot_close"]),
        )
        if instruction["cap_binding"]:
            state.cap_binding_days += 1
        sel = instruction["selection"] or {}
        signal_rows.append(
            {
                "variant": variant,
                "date": day,
                "desired_contract": instruction["desired_contract"] or "",
                "target_qty": instruction["target_qty"],
                "cap_qty": instruction["cap_qty"],
                "raw_full_qty": instruction["raw_full_qty"],
                "protected_lots": instruction["protected_lots"],
                "cap_binding": instruction["cap_binding"],
                "selected_strike": sel.get("selected_strike", np.nan),
                "selected_settle": sel.get("selected_settle", np.nan),
                "atm_strike": sel.get("atm_strike", np.nan),
                "atm_settle": sel.get("atm_settle", np.nan),
                "gain_points": sel.get("gain_points", np.nan),
                "dte": sel.get("dte", np.nan),
            }
        )
        daily_rows.append(
            {
                "variant": variant,
                "date": day,
                "nav": nav,
                "cash": state.cash,
                "option_mv": option_mv,
                "put_contract": state.contract or "",
                "put_qty": state.qty,
                "put_mark": state.mark,
                "book_cost": state.book_cost,
                "realized_put_pnl": state.realized_pnl,
                "unrealized_put_pnl": option_mv - state.book_cost,
                "put_total_net_pnl": state.realized_pnl + option_mv - state.book_cost,
                "cash_interest": interest,
                "margin_reserve": margin,
                "capital_shortfall": capital_shortfall,
                "instruction_executed": executed,
                "execution_note": execution_note,
                "base_lots": int(row["base_lots"]),
                "grid_lots": int(row["grid_lots"]),
                "total_lots": int(row["total_lots"]),
                "spot_close": float(row["spot_close"]),
            }
        )
        prior_margin = margin

    daily = pd.DataFrame(daily_rows)
    daily["ret"] = daily["nav"].pct_change()
    daily.loc[daily.index[0], "ret"] = daily.loc[daily.index[0], "nav"] / INITIAL_CAPITAL - 1.0
    signals = pd.DataFrame(signal_rows)
    tx = pd.DataFrame(transactions)
    ending_mv = float(daily.iloc[-1]["option_mv"])
    total_put_net = state.realized_pnl + ending_mv - state.book_cost
    summary = {
        "variant": variant,
        "ending_nav": float(daily.iloc[-1]["nav"]),
        "ending_option_mv": ending_mv,
        "realized_put_pnl": state.realized_pnl,
        "unrealized_put_pnl": ending_mv - state.book_cost,
        "put_leg_net_pnl": total_put_net,
        "buy_premium": state.buy_premium,
        "sell_proceeds": state.sell_proceeds,
        "fees": state.fees,
        "slippage": state.slippage,
        "interest_total": state.interest_total,
        "invalid_orders": state.invalid_orders,
        "cap_binding_days": state.cap_binding_days,
        "buy_contracts": int(tx.loc[tx.get("side", pd.Series(dtype=str)).eq("buy"), "qty"].sum()) if not tx.empty else 0,
        "sell_contracts": int(tx.loc[tx.get("side", pd.Series(dtype=str)).eq("sell"), "qty"].sum()) if not tx.empty else 0,
        "trade_legs": int(len(tx)),
    }
    return daily, signals, tx, summary


def performance(frame: pd.DataFrame) -> dict[str, float]:
    if frame.empty:
        return {key: np.nan for key in ["cagr", "max_dd", "vol", "sharpe", "calmar", "worst_5d", "worst_20d", "worst_60d"]}
    nav = frame["nav"].astype(float)
    ret = frame["ret"].astype(float)
    first_ret = float(ret.iloc[0])
    start_equity = float(nav.iloc[0] / (1.0 + first_ret)) if first_ret > -1.0 else float(nav.iloc[0])
    years = max((pd.Timestamp(frame["date"].iloc[-1]) - pd.Timestamp(frame["date"].iloc[0])).days / 365.25, 1 / 365.25)
    cagr = (nav.iloc[-1] / start_equity) ** (1.0 / years) - 1.0
    nav_with_start = pd.concat([pd.Series([start_equity]), nav.reset_index(drop=True)], ignore_index=True)
    drawdown = nav_with_start / nav_with_start.cummax() - 1.0
    vol = ret.std(ddof=1) * math.sqrt(252.0)
    sharpe = ret.mean() / ret.std(ddof=1) * math.sqrt(252.0) if ret.std(ddof=1) > 0 else np.nan
    calmar = cagr / abs(drawdown.min()) if drawdown.min() < 0 else np.nan
    result = {
        "cagr": float(cagr),
        "max_dd": float(drawdown.min()),
        "vol": float(vol),
        "sharpe": float(sharpe),
        "calmar": float(calmar),
    }
    for horizon in (5, 20, 60):
        rolling = (1.0 + ret).rolling(horizon).apply(np.prod, raw=True) - 1.0
        result[f"worst_{horizon}d"] = float(rolling.min()) if rolling.notna().any() else np.nan
    return result


def window_slice(frame: pd.DataFrame, offset: pd.DateOffset | None) -> pd.DataFrame:
    if offset is None:
        return frame.copy()
    end = pd.Timestamp(frame["date"].max())
    return frame[frame["date"] >= end - offset].copy()


def build_metrics(all_daily: pd.DataFrame, summaries: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    p0 = all_daily[all_daily["variant"].eq("P0")]
    p0_metrics = {window: performance(window_slice(p0, offset)) for window, offset in WINDOWS.items()}
    p0_end = float(p0.iloc[-1]["nav"])
    p0_interest = float(summaries.loc[summaries["variant"].eq("P0"), "interest_total"].iloc[0])
    for variant, frame in all_daily.groupby("variant", sort=False):
        summary = summaries[summaries["variant"].eq(variant)].iloc[0]
        for window, offset in WINDOWS.items():
            subset = window_slice(frame, offset)
            stats = performance(subset)
            base = p0_metrics[window]
            rows.append(
                {
                    "variant": variant,
                    "window": window,
                    **stats,
                    "cagr_vs_p0": stats["cagr"] - base["cagr"],
                    "max_dd_improvement_vs_p0": stats["max_dd"] - base["max_dd"],
                    "ending_nav": float(subset.iloc[-1]["nav"]),
                    "ending_nav_diff_vs_p0_full": float(frame.iloc[-1]["nav"] - p0_end),
                    "put_leg_net_pnl": float(summary["put_leg_net_pnl"]),
                    "interest_opportunity_cost": p0_interest - float(summary["interest_total"]),
                    "avg_put_qty": float(frame["put_qty"].mean()),
                    "median_put_qty": float(frame["put_qty"].median()),
                    "max_put_qty": int(frame["put_qty"].max()),
                    "avg_put_mv_nav": float((frame["option_mv"] / frame["nav"]).replace([np.inf, -np.inf], np.nan).fillna(0).mean()),
                    "max_put_mv_nav": float((frame["option_mv"] / frame["nav"]).replace([np.inf, -np.inf], np.nan).fillna(0).max()),
                    "capital_shortfall_days": int(frame["capital_shortfall"].gt(0).sum()),
                    "cap_binding_days": int(summary["cap_binding_days"]),
                    "invalid_orders": int(summary["invalid_orders"]),
                    "trade_legs": int(summary["trade_legs"]),
                }
            )
    metrics = pd.DataFrame(rows)
    return metrics


def annual_table(all_daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    annual_returns: dict[tuple[str, int], float] = {}
    for variant, frame in all_daily.groupby("variant", sort=False):
        frame = frame.copy()
        frame["year"] = pd.to_datetime(frame["date"]).dt.year
        prior_nav = INITIAL_CAPITAL
        for year, group in frame.groupby("year"):
            end_nav = float(group.iloc[-1]["nav"])
            value = end_nav / prior_nav - 1.0
            annual_returns[(variant, int(year))] = value
            prior_nav = end_nav
    for (variant, year), value in annual_returns.items():
        rows.append(
            {
                "variant": variant,
                "year": year,
                "return": value,
                "return_vs_p0": value - annual_returns.get(("P0", year), np.nan),
            }
        )
    return pd.DataFrame(rows)


def decision(metrics: pd.DataFrame) -> dict[str, Any]:
    full = metrics[metrics["window"].eq("full")].set_index("variant")
    three = metrics[metrics["window"].eq("last_3y")].set_index("variant")
    labels: dict[str, str] = {}
    for variant in VARIANTS:
        if variant == "P0":
            labels[variant] = "baseline"
            continue
        cagr_delta = float(full.loc[variant, "cagr_vs_p0"])
        dd_delta = float(full.loc[variant, "max_dd_improvement_vs_p0"])
        if cagr_delta > 0 and float(three.loc[variant, "cagr_vs_p0"]) >= 0:
            labels[variant] = "positive_return_contribution"
        elif dd_delta > 0 and cagr_delta <= 0:
            labels[variant] = "insurance_only"
        else:
            labels[variant] = "not_supported"
    excessive_reasons: list[str] = []
    for lower in ("P25", "P50", "P75"):
        cagr_adv = float(full.loc[lower, "cagr"] - full.loc["P100", "cagr"])
        dd_worse = float(full.loc["P100", "max_dd"] - full.loc[lower, "max_dd"])
        if cagr_adv >= 0.01 - 1e-12 and dd_worse <= 0.005 + 1e-12:
            excessive_reasons.append(f"{lower}_dominates_P100_under_preregistered_tolerance")
    cagr_adv_r = float(full.loc["P100-R", "cagr"] - full.loc["P100", "cagr"])
    dd_worse_r = float(full.loc["P100", "max_dd"] - full.loc["P100-R", "max_dd"])
    if cagr_adv_r >= 0.005 - 1e-12 and dd_worse_r <= 0.005 + 1e-12:
        excessive_reasons.append("P100-R_dominates_P100_under_preregistered_tolerance")
    ordered = ["P25", "P50", "P75", "P100"]
    avg_qty = [float(full.loc[name, "avg_put_qty"]) for name in ordered]
    cagr = [float(full.loc[name, "cagr"]) for name in ordered]
    dd = [float(full.loc[name, "max_dd"]) for name in ordered]
    if all(avg_qty[idx] <= avg_qty[idx + 1] + 1e-12 for idx in range(len(avg_qty) - 1)):
        if cagr[-1] < max(cagr[:-1]) and dd[-1] <= max(dd[:-1]) + 0.005:
            excessive_reasons.append("higher_quantity_without_material_drawdown_gain")
    return {
        "labels": labels,
        "put_quantity_excessive": bool(excessive_reasons),
        "excessive_reasons": excessive_reasons,
    }


def pct(value: float) -> str:
    return "N/A" if pd.isna(value) else f"{value * 100:.2f}%"


def write_record(metrics: pd.DataFrame, summaries: pd.DataFrame, decisions: dict[str, Any], manifest: dict[str, Any]) -> None:
    full = metrics[metrics["window"].eq("full")].set_index("variant")
    three = metrics[metrics["window"].eq("last_3y")].set_index("variant")
    one = metrics[metrics["window"].eq("last_1y")].set_index("variant")
    lines = [
        "# IM 作者式 Put 数量消融：真实 IM/MO 数据结果 v1",
        "",
        f"- 正式样本：{START.date()} 至 {END.date()}，共 {manifest['common_rows']} 个共同交易日。",
        "- 数据：中金所官方IM/MO与中证指数官方中证1000价格指数。",
        "- 组合：人民币1000万元；固定30%名义IM基础仓、作者式IM网格、其余正现金3%；无卖Call。",
        "- Put数量按预注册的“当前最虚值Put涨到同月近平值Put”的价格差重建。",
        "",
        "## 核心结果",
        "",
        "| 方案 | 全样本CAGR | 全样本MaxDD | 对P0年化增量 | 回撤改善 | 近3年CAGR | 近1年CAGR | 平均/最高Put张数 | Put净损益 | 判定 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    summary_lookup = summaries.set_index("variant")
    for variant in VARIANTS:
        lines.append(
            "| {v} | {cagr} | {dd} | {delta} | {ddi} | {c3} | {c1} | {avg:.1f}/{mx:d} | {pnl:,.0f}元 | {label} |".format(
                v=variant,
                cagr=pct(float(full.loc[variant, "cagr"])),
                dd=pct(float(full.loc[variant, "max_dd"])),
                delta=pct(float(full.loc[variant, "cagr_vs_p0"])),
                ddi=pct(float(full.loc[variant, "max_dd_improvement_vs_p0"])),
                c3=pct(float(three.loc[variant, "cagr"])),
                c1=pct(float(one.loc[variant, "cagr"])),
                avg=float(full.loc[variant, "avg_put_qty"]),
                mx=int(full.loc[variant, "max_put_qty"]),
                pnl=float(summary_lookup.loc[variant, "put_leg_net_pnl"]),
                label=decisions["labels"][variant],
            )
        )
    lines.extend(
        [
            "",
            "## 预注册结论",
            "",
            f"- Put数量过多证据：{'是' if decisions['put_quantity_excessive'] else '否'}。",
            f"- 触发理由：{', '.join(decisions['excessive_reasons']) if decisions['excessive_reasons'] else '无'}。",
            "",
            "## 解释边界",
            "",
            "1. 这是2022-07-22以后真实MO可测期，不是2015年以来作者完整家庭组合的逐行复刻。",
            "2. 原文没有披露额外隐含波动率路径公式；本版只使用同日同月近平值Put与最虚值Put的官方结算价差。",
            "3. 第一阶段不模拟危机中卖Put维持保证金的间接流动性价值。",
            "4. 所有结果均为研究用途，未获准实盘。",
        ]
    )
    (OUTPUT_DIR / "record.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    verify_spec()
    if OUTPUT_DIR.exists():
        raise FileExistsError(f"Output directory already exists: {OUTPUT_DIR}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=False)
    download_log = download_archives()
    im_all, puts_all = parse_cffex(download_log)
    index, im, im_expiry, put_expiry = build_market(im_all, puts_all)
    common = simulate_common_futures(index, im, im_expiry)

    daily_frames: list[pd.DataFrame] = []
    signal_frames: list[pd.DataFrame] = []
    tx_frames: list[pd.DataFrame] = []
    summaries: list[dict[str, Any]] = []
    for variant, rule in VARIANTS.items():
        daily, signals, tx, summary = simulate_variant(variant, rule, common, puts)
        daily_frames.append(daily)
        signal_frames.append(signals)
        if not tx.empty:
            tx.insert(0, "variant", variant)
            tx_frames.append(tx)
        summaries.append(summary)

    all_daily = pd.concat(daily_frames, ignore_index=True)
    all_signals = pd.concat(signal_frames, ignore_index=True)
    all_tx = pd.concat(tx_frames, ignore_index=True) if tx_frames else pd.DataFrame()
    summary_frame = pd.DataFrame(summaries)
    metrics = build_metrics(all_daily, summary_frame)
    annual = annual_table(all_daily)
    decisions = decision(metrics)

    p0_from_variant = all_daily[all_daily["variant"].eq("P0")]["nav"].to_numpy()
    if not np.allclose(p0_from_variant, common["p0_nav"].to_numpy(), atol=1e-6):
        raise RuntimeError("P0 variant does not reproduce the frozen common futures path")
    if any(len(frame) != len(common) for frame in daily_frames):
        raise RuntimeError("Candidate daily row counts differ")

    manifest = {
        "version": VERSION,
        "spec_sha256": SPEC_SHA256,
        "sample_start": START.date().isoformat(),
        "sample_end": END.date().isoformat(),
        "common_rows": int(len(common)),
        "im_rows": int(len(im)),
        "mo_put_rows": int(len(puts)),
        "mo_contracts": int(puts["contract"].nunique()),
        "archives": download_log.drop(columns=["path"]).to_dict("records"),
        "official_index_rows": int(len(index)),
        "checks": {
            "p0_parity_max_abs": float(np.max(np.abs(p0_from_variant - common["p0_nav"].to_numpy()))),
            "candidate_count": len(VARIANTS),
            "same_daily_rows": True,
            "put_settle_nonpositive_rows": int(puts["settle"].le(0).sum()),
            "im_settle_nonpositive_rows": int(im["settle"].le(0).sum()),
        },
        "decision": decisions,
    }

    common.to_csv(OUTPUT_DIR / "common_futures_grid_path.csv", index=False)
    all_daily.to_csv(OUTPUT_DIR / "daily_nav.csv", index=False)
    all_signals.to_csv(OUTPUT_DIR / "put_signals.csv", index=False)
    all_tx.to_csv(OUTPUT_DIR / "put_transactions.csv", index=False)
    summary_frame.to_csv(OUTPUT_DIR / "put_accounting_summary.csv", index=False)
    metrics.to_csv(OUTPUT_DIR / "metrics.csv", index=False)
    annual.to_csv(OUTPUT_DIR / "annual_returns.csv", index=False)
    download_log.drop(columns=["path"]).to_csv(OUTPUT_DIR / "cffex_download_manifest.csv", index=False)
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps({"decision": decisions, "summaries": summaries}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUTPUT_DIR / "data_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    write_record(metrics, summary_frame, decisions, manifest)

    output_hashes = {
        path.name: sha256_file(path)
        for path in sorted(OUTPUT_DIR.iterdir())
        if path.is_file()
    }
    (OUTPUT_DIR / "output_hashes.json").write_text(
        json.dumps(output_hashes, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print((OUTPUT_DIR / "record.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
