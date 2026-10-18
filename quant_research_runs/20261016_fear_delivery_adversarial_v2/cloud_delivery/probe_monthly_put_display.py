import importlib.util,json,sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
mode=sys.argv[1]
completion=ROOT/'quant_research_runs/20261016_put_delivery_completion_v1'
cloud=completion/'cloud'
baseline=ROOT/'quant_research_runs/20261016_fear_single_source_delivery_v1/cloud'
sys.path.insert(0,str(baseline/'scripts') if mode=='original' else str(cloud/'scripts'))
if mode=='original':
    spec=importlib.util.spec_from_file_location('ic_im_v1_4_put_display',HERE/'original_db84_put_display.py')
    display=importlib.util.module_from_spec(spec)
    sys.modules['ic_im_v1_4_put_display']=display;spec.loader.exec_module(display)
    spec=importlib.util.spec_from_file_location('build_ic_im_v1_4_digest',HERE/'original_db84_build_digest.py')
    digest=importlib.util.module_from_spec(spec);spec.loader.exec_module(digest)
else:
    import build_ic_im_v1_4_digest as digest
    import ic_im_v1_4_put_display as display
result=json.loads((completion/'handoff-38853603565/report/result.json').read_text(encoding='utf8'))
signal=result['signals']['IC']
subject,plain,_=digest.build_success(result,'','')
html=digest.build_success_html(result,'')
(HERE/(mode+'_monthly_real_result.txt')).write_text(plain,encoding='utf8')
(HERE/(mode+'_monthly_real_result.html')).write_text(html,encoding='utf8')
out=dict(mode=mode,source_run=38853603565,market_date=result['market_date'],
    producer_held=signal['v14_core_put_current_contract'],
    producer_lifecycle_target=signal['v14_core_put_contract'],
    producer_monthly_candidate=signal['put_target_contract'],
    displayed_core_target=display.ic_contract(signal,'target','core'),
    target_summary=digest.put_text('IC',signal,True),
    has_today_put_label='Put：2026-10-16收盘维护候选（盘尾复核）' in plain,
    has_old_combined_next_day_label='下一交易日2026-10-19研究目标 期货 / Put' in plain,
    subject=subject)
(HERE/(mode+'_monthly_real_result.json')).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(out,ensure_ascii=False,indent=2))

