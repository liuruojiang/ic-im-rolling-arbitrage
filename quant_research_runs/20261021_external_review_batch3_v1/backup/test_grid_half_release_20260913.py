from datetime import date
import pytest
import poe_ic_im_mainline_v1_3_bot as bot

@pytest.mark.parametrize('product,entry,exit', [('IC',.5,1.),('IM',.9,1.7)])
def test_boundary_and_legacy_transition(product,entry,exit):
    assert bot.grid_rule(product,date(2026,9,11))['units']==1.
    rule=bot.grid_rule(product,date(2026,9,14))
    assert rule=={'entry':entry,'exit':exit,'units':.5}
    for score,current,target in [(entry,0.,.5),(exit,.5,0.),((entry+exit)/2,1.,.5),((entry+exit)/2,.5,.5)]:
        live={'score':score,'grid_current_units':current,'history_date':date(2026,9,14)}
        assert bot._daily_grid_target(product,live)==target

def test_fractional_action_and_report():
    assert bot._grid_target('IC',{'grid_current_units':0.,'grid_target_units':.5})==(.5,'ADD_GRID')
    assert bot._grid_target('IC',{'grid_current_units':1.,'grid_target_units':.5})==(.5,'EXIT_GRID')
    assert bot._grid_target('IM',{'grid_current_units':.5,'grid_target_units':.5})==(.5,'HOLD')
