from test_ind_strategy import no_network
from test_icim_round3_policy_repairs_20261021 import wire_adapter, short_anchor, signal
import pytest
import poe_ic_im_mainline_v1_4_bot as bot

@pytest.mark.parametrize("product", ["IC", "IM"])
def test_available_low_mark_sets_waiting_roll_trigger(monkeypatch,product):
    a,frame=wire_adapter(monkeypatch,product,short_anchor(product),mark=.1)
    monkeypatch.setattr(bot,"_v14_ic_short_put_candidate",lambda *a,**k:{"tradable":False})
    monkeypatch.setattr(bot,"_v14_im_short_put_candidate",lambda *a,**k:{"tradable":False})
    out=bot._apply_v14_live_policy(product,signal(product),mo_quotes=frame)
    assert out["v14_action"]=="WAIT_SHORT_PUT_ROLL"
    assert out["v14_roll_pending"] is True
