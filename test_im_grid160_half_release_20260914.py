from datetime import date
import pytest
import poe_ic_im_mainline_v1_3_bot as bot

def test_versioned_historical_rules():
    assert bot.grid_policy_revision(date(2026,9,11)) == 'legacy_grid_1x'
    assert bot.grid_policy_revision(date(2026,9,14)) == 'ic_im_grid_half_20260913_v1'
    assert bot.grid_policy_revision(date(2026,9,15)) == bot.GRID_POLICY_REVISION
    assert bot.grid_rule('IM',date(2026,9,11)) == {'entry':1.6,'exit':2.,'units':1.}
    assert bot.grid_rule('IM',date(2026,9,14)) == {'entry':.9,'exit':1.7,'units':.5}
    assert bot.grid_rule('IM',date(2026,9,15)) == {'entry':1.6,'exit':2.,'units':.5}
    assert bot.grid_rule('IC',date(2026,9,15)) == bot.grid_rule('IC',date(2026,9,14))

@pytest.mark.parametrize('score,current,target',[(1.6,0.,.5),(1.5,0.,.5),(2.,.5,0.),(1.8,.5,.5),(1.8,0.,0.)])
def test_valuation_only_half_targets(score,current,target):
    live={'score':score,'grid_current_units':current,'history_date':date(2026,9,15),'momentum_120':-1.}
    assert bot._daily_grid_target('IM',live)==target
