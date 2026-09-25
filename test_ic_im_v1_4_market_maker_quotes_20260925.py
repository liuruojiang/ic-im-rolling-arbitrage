"""Zero recorded prints/OI must not veto a positive dated model quote."""
from datetime import date

import pandas as pd

import poe_ic_im_mainline_v1_4_bot as bot


def _quotes(contract: str, price: float) -> pd.DataFrame:
    frame = pd.DataFrame([{"instrument": contract, "lastprice": price, "volume": 0.0,
                           "position": 0.0}])
    frame.attrs["listed_instruments"] = [contract]
    return frame


def test_im_short_put_positive_quote_is_eligible_without_recorded_prints(monkeypatch):
    monkeypatch.setattr(bot, "_implied_volatility", lambda *args: .40)
    signal = {"market_date": date(2026, 9, 28), "next_trade_date": date(2026, 9, 29),
              "index_price": 7500.0}
    chosen = bot._v14_im_short_put_candidate(signal, _quotes("MO2610-P-7200", 80.0))
    assert chosen["contract"] == "MO2610-P-7200"
    assert chosen["tradable"] is True


def test_im_d10_call_positive_quote_is_eligible_without_recorded_prints(monkeypatch):
    monkeypatch.setattr(bot, "_implied_volatility", lambda *args: .30)
    monkeypatch.setattr(bot, "_bs_price_delta", lambda *args: (100.0, .10))
    monkeypatch.setattr(bot, "_gov10y_for_day", lambda *args: .02)
    chosen = bot.select_im_call_d10(_quotes("MO2611-C-8500", 100.0),
                                    date(2026, 9, 28), 7500.0, date(2026, 10, 16))
    assert chosen is not None
    assert chosen["row"]["instrument"] == "MO2611-C-8500"


def test_im_call_rescue_positive_quote_is_eligible_without_recorded_prints(monkeypatch):
    monkeypatch.setattr(bot, "_implied_volatility", lambda *args: .30)
    monkeypatch.setattr(bot, "_bs_price_delta", lambda *args: (100.0, .10))
    monkeypatch.setattr(bot, "_gov10y_for_day", lambda *args: .02)
    chosen = bot.select_im_call_rescue(_quotes("MO2611-C-8500", 100.0),
        date(2026, 9, 28), 7500.0, date(2026, 10, 16), 8000.0)
    assert chosen is not None
    assert chosen["row"]["instrument"] == "MO2611-C-8500"

