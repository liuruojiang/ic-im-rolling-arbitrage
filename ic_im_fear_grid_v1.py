"""Source-paired IC/IM grid rule and public Fear/Greed data provenance."""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
from datetime import date, datetime, time
from html import unescape
from typing import Any
from zoneinfo import ZoneInfo

import requests


EFFECTIVE_SIGNAL_DATE = date(2026, 10, 8)
ENTRY_MAX = 25.0
EXIT_MIN = 50.0
GRID_UNITS = 0.5
SOURCE_NONE = "none"
SOURCES = frozenset({SOURCE_NONE, "valuation_only", "fear_only", "both"})
CSV_URL = "https://baifenwei.com/data/fear-greed/fear-greed-full.csv"
PAGE_URL = "https://baifenwei.com/indicator/fear-greed/"
BEIJING = ZoneInfo("Asia/Shanghai")
RULES = {
    "IC": {"entry": 0.5, "exit": 1.0},
    "IM": {"entry": 1.6, "exit": 2.0},
}


def source_for_legacy_units(units: float) -> str:
    """Existing nonzero grid positions were entered by valuation only."""
    if units == 0.0:
        return SOURCE_NONE
    if units == GRID_UNITS:
        return "valuation_only"
    raise ValueError(f"legacy grid units outside fix9 domain: {units}")


def transition(
    product: str, current_units: float, current_source: str,
    valuation_score: float, fear_score: float | None,
) -> tuple[float, str, str]:
    """Return next-open units, frozen entry source, and transition reason."""
    if product not in RULES:
        raise ValueError(f"unsupported grid product: {product}")
    if current_units not in {0.0, GRID_UNITS} or current_source not in SOURCES:
        raise ValueError("invalid grid holding/source")
    if (current_units == 0.0) != (current_source == SOURCE_NONE):
        raise ValueError("grid units and frozen entry source disagree")
    valuation = float(valuation_score)
    if not math.isfinite(valuation):
        raise ValueError("nonfinite valuation score")
    fear = None if fear_score is None else float(fear_score)
    if fear is not None and (not math.isfinite(fear) or not 0.0 <= fear <= 100.0):
        raise ValueError("invalid Fear/Greed score")

    rule = RULES[product]
    if current_units == 0.0:
        valuation_entry = valuation <= rule["entry"]
        fear_entry = fear is not None and fear <= ENTRY_MAX
        if valuation_entry or fear_entry:
            source = ("both" if valuation_entry and fear_entry else
                      "valuation_only" if valuation_entry else "fear_only")
            return GRID_UNITS, source, f"enter_{source}"
        return 0.0, SOURCE_NONE, "hold_flat"

    valuation_exit = current_source in {"valuation_only", "both"} and valuation >= rule["exit"]
    fear_exit = current_source in {"fear_only", "both"} and fear is not None and fear >= EXIT_MIN
    if valuation_exit or fear_exit:
        reason = "both" if valuation_exit and fear_exit else "valuation" if valuation_exit else "fear"
        return 0.0, SOURCE_NONE, f"exit_{reason}"
    return GRID_UNITS, current_source, "hold_grid"


def _bounded_get(url: str, maximum: int) -> bytes:
    response = requests.get(
        url, timeout=8,
        headers={"User-Agent": "ICIM-Fix10-Research-Signal/1.0"},
    )
    response.raise_for_status()
    data = response.content
    if not data or len(data) > maximum:
        raise RuntimeError(f"恐贪来源文件大小异常: {url}")
    return data


def _parse_page(page: bytes) -> tuple[date, float, datetime]:
    html = unescape(page.decode("utf-8"))
    day_match = re.search(
        r'<p class="fg-stat-label">最新日期</p>\s*<p class="fg-stat-value"[^>]*>(\d{4}-\d{2}-\d{2})</p>',
        html,
    )
    score_match = re.search(
        r'<p class="fg-stat-label">最新指数值</p>\s*<p class="fg-stat-value[^\"]*">([0-9]+(?:\.[0-9]+)?)</p>',
        html,
    )
    update_match = re.search(
        r'<p class="fg-update">数据日 (\d{4}-\d{2}-\d{2}) · 更新 (\d{4}-\d{2}-\d{2} \d{2}:\d{2})</p>',
        html,
    )
    if not day_match or not score_match or not update_match:
        raise RuntimeError("恐贪指标页缺少可核验的数据日、分数或更新时间")
    day = date.fromisoformat(day_match.group(1))
    if update_match.group(1) != day.isoformat():
        raise RuntimeError("恐贪指标页数据日互相冲突")
    score = float(score_match.group(1))
    updated = datetime.strptime(update_match.group(2), "%Y-%m-%d %H:%M").replace(tzinfo=BEIJING)
    return day, score, updated


def fetch_score(signal_day: date, mode: str) -> dict[str, Any]:
    """Read an exact dated row; distinguish missing history from unpublished today."""
    if mode not in {"intraday", "close"}:
        raise ValueError("unsupported Fear fetch mode")
    fetched = datetime.now(BEIJING)
    raw_csv = _bounded_get(CSV_URL, 1024 * 1024)
    rows = list(csv.DictReader(io.StringIO(raw_csv.decode("utf-8-sig"))))
    if not rows or set(rows[0]) != {"date", "fear_greed_index"}:
        raise RuntimeError("恐贪 CSV 字段不符")
    values: dict[date, float] = {}
    previous: date | None = None
    for row in rows:
        day = date.fromisoformat(row["date"])
        value = float(row["fear_greed_index"])
        if (previous is not None and day <= previous) or not math.isfinite(value) or not 0.0 <= value <= 100.0:
            raise RuntimeError("恐贪 CSV 日期顺序或读数非法")
        values[day] = value
        previous = day
    latest = previous
    if latest is None:
        raise RuntimeError("恐贪 CSV 为空")
    page_day, page_score, page_updated = _parse_page(_bounded_get(PAGE_URL, 512 * 1024))
    if page_day != latest or not math.isclose(page_score, values[latest], abs_tol=0.005, rel_tol=0.0):
        raise RuntimeError("恐贪网页与 CSV 最新数据不一致，暂停确认信号")

    value = values.get(signal_day)
    if signal_day > latest:
        if mode == "close":
            raise RuntimeError(f"恐贪 {signal_day} 收盘数据尚未发布，暂停确认信号")
        status = "intraday_unpublished"
    elif value is None:
        # A later published date proves this is an actual hole in the public series.
        status = "missing_published_history"
    elif signal_day < fetched.date() or signal_day < latest:
        status = "retrospective_replay"
    elif mode == "intraday":
        status = "intraday_provisional"
    else:
        if page_updated.date() != signal_day or page_updated.time() < time(15, 0):
            raise RuntimeError(f"恐贪 {signal_day} 指标页尚无收盘后更新，暂停确认信号")
        if page_updated > fetched:
            raise RuntimeError("恐贪网页更新时间晚于实际获取时刻")
        status = "same_day_post_close"
    return {
        "fear_greed_index": value,
        "fear_data_date": signal_day.isoformat() if value is not None else None,
        "fear_data_status": status,
        "fear_retrieved_at": fetched.isoformat(timespec="seconds"),
        "fear_page_updated_at": page_updated.isoformat(timespec="minutes"),
        "fear_csv_sha256": hashlib.sha256(raw_csv).hexdigest(),
        "fear_source_url": CSV_URL,
    }
