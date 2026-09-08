"""User-approved IM Put parameters; old ledger days retain their own policy."""
from datetime import date
import math

REVISION = "im_mom120_put102_20260908_v1"
EFFECTIVE_DATE = date(2026, 9, 8)
TARGET_MONEYNESS = 1.02
IV_REFERENCE_MONEYNESS = 0.95  # Same monitoring series as the threshold research.


def active(day):
    return date.fromisoformat(str(day)[:10]) >= EFFECTIVE_DATE


def moneyness(day):
    return TARGET_MONEYNESS if active(day) else 0.95


def momentum_parent(momentum_120):
    value = float(momentum_120)
    if not math.isfinite(value):
        raise ValueError("MOM120 must be finite")
    return 3 if value < 0 else 0


def momentum_quantity(day, momentum_120, weight, core_quantity):
    if not math.isfinite(float(weight)) or not 0 <= float(weight) <= 1:
        raise ValueError("Momentum weight must be finite and in [0, 1]")
    if active(day):
        return 0.5 * momentum_parent(momentum_120) * float(weight)
    return float(core_quantity) * float(weight)


def description(day):
    if active(day):
        return "核心Put保留估值与MOM120取高；动量Put仅MOM120<0时按3×0.5×动量执行权重配置；两条Put新选约均为当前持有IM价格的102%"
    return "历史政策：动量Put=核心Put目标×动量执行权重；两条Put新选约为95%"


def iv_warning(iv, *, source, market_date, snapshot_time, contract="", reason=""):
    valid = iv is not None and math.isfinite(float(iv)) and float(iv) > 0
    level = "unavailable" if not valid else "critical" if iv > 0.50 else "high" if iv > 0.40 else "normal"
    label = {"unavailable": "IV预警无法判断", "critical": "IV超过50%：强预警", "high": "IV超过40%：高波动预警", "normal": "IV未超过40%"}[level]
    text = f"{label}；IV {float(iv):.2%}" if valid else f"{label}；N/A（{reason or '缺少当日有效报价或无法反解IV'}）"
    text += f"；参考合约 {contract or 'N/A'}；行情日 {market_date}；时间 {snapshot_time or 'N/A'}；来源 {source}。仅预警，不改变仓位。"
    return dict(level=level, iv=float(iv) if valid else None, thresholds=[0.4, 0.5], comparison="strictly_greater", source=source, market_date=str(market_date), snapshot_time=str(snapshot_time) if snapshot_time else None, contract=contract, text=text, position_effect="none")
