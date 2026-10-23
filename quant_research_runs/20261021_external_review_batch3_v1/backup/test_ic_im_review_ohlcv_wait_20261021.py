from copy import deepcopy
from datetime import datetime
import pandas as pd
import pytest
import poe_ic_im_mainline_v1_4_bot as bot


@pytest.mark.parametrize('product', ['IC', 'IM'])
@pytest.mark.parametrize('hour,minute', [(15,0),(15,1),(15,29),(15,30)])
def test_daily_bar_lag_waits_without_synthesizing_a_close(product,hour,minute,monkeypatch):
    # Two synthetic, well-formed bars suffice: the freshness boundary must
    # reject them before history length, frozen-index or strategy calculations.
    frame = pd.DataFrame(dict(open=[7000.,7000.],high=[7100.,7100.],low=[6900.,6900.],
                              close=[7050.,7050.],volume=[100_000_000.,200_000_000.]),
                         index=pd.to_datetime(['2026-10-19','2026-10-20']))
    before = frame.copy(deep=True)
    anchors = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    monkeypatch.setattr(bot,'fetch_ohlcv_history',lambda _: frame)
    def forbidden(*args,**kwargs):
        raise AssertionError('daily-bar wait must not fetch a replacement live price or compute targets')
    monkeypatch.setattr(bot,'fetch_live_price_quote',forbidden)
    monkeypatch.setattr(bot,'v13_momentum_schedule',forbidden)
    clock = datetime(2026,10,21,hour,minute,tzinfo=bot.BEIJING)
    with pytest.raises(RuntimeError,match='OHLCV已过期') as caught:
        bot.live_proxy(product,clock)
    assert getattr(caught.value,'status',None) == 'waiting_for_completed_daily_ohlcv'
    assert '成交量' in str(caught.value) and '等待' in str(caught.value)
    pd.testing.assert_frame_equal(frame,before)
    assert bot.LIVE_CONTINUATION_ANCHOR == anchors
