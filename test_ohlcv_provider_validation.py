"""Provider selection must never suppress fallback with invalid primary data."""
from datetime import datetime
from unittest.mock import patch

import pandas as pd
import pytest
from hypothesis import given, settings, example, strategies as st

import poe_ic_im_mainline_v1_3_bot as bot


@settings(max_examples=30, deadline=None)
@given(st.tuples(st.booleans(), st.booleans(), st.booleans()))
@example((False, True, True))
@example((False, False, False))
def test_first_valid_provider_is_selected(valid):
    """Every provider must pass the same validator; all invalid means failure."""
    rows = [{"day": str(day.date()), "open": "10", "high": "11",
             "low": "9", "close": "10", "volume": "2000000"}
            for day in pd.bdate_range(end="2026-09-04", periods=200)]
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return rows
    names = ["Sina", "Eastmoney", "Tencent"]
    checked = []
    def validate(product, frame, clock):
        name = frame.attrs["source"]
        checked.append(name)
        if not valid[names.index(name)]:
            raise RuntimeError(name + " OHLCV已过期或不完整")
        return frame
    def fallback(url, params):
        if "eastmoney" in url:
            return {"data": {"klines": [
                ",".join(row[key] for key in ("day", "open", "close", "high", "low", "volume"))
                for row in rows]}}
        return {"data": {"sh000905": {"day": [
            [row[key] for key in ("day", "open", "close", "high", "low", "volume")]
            for row in rows]}}}
    with patch.object(bot.requests, "get", return_value=Response()), \
         patch.object(bot, "_request_json", side_effect=fallback), \
         patch.object(bot, "_validate_v13_ohlcv", side_effect=validate):
        if any(valid):
            result = bot.fetch_ohlcv_history("IC")
            expected = names[valid.index(True)]
            assert result.attrs["source"] == expected
            assert checked == names[:valid.index(True) + 1]
            assert len(result) == len(rows)
        else:
            with pytest.raises(RuntimeError, match="OHLCV全部来源失败"):
                bot.fetch_ohlcv_history("IC")
            assert checked == names


def test_real_frozen_history_is_still_rejected_when_stale():
    from pathlib import Path
    root = Path(__file__).parent
    frame = pd.read_csv(
        root / "data/ic_510500_put_proxy_validation_v1/sina_000905_index.csv",
        parse_dates=["date"],
    ).set_index("date")
    with pytest.raises(RuntimeError, match="OHLCV已过期"):
        bot._validate_v13_ohlcv(
            "IC", frame, datetime(2026, 9, 4, 18, tzinfo=bot.BEIJING)
        )
