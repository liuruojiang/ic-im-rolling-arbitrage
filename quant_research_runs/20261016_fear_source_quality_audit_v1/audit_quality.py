"""Read-only source-quality audit; no requests, strategy changes, or ledger writes."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib,json
import pandas as pd
import numpy as np

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def dump(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
def main():
    if (HERE/'record.md').exists():raise FileExistsError('First audit report cannot be overwritten')
    jp=ROOT/'quant_research_runs/20261016_jiu_fear_grid_threshold_v1/inputs/jiu_fear_greed.csv'
    bp=ROOT/'quant_research_runs/20260928_csi1000_fear_greed_reproduction/inputs/fear_greed_full.csv'
    mp=jp.parent/'source_manifest.json';cp=ROOT/'ic_im_fear_grid_v1.py'
    j=pd.read_csv(jp);b=pd.read_csv(bp);hashes={str(p.relative_to(ROOT)):sha(p) for p in (jp,bp,mp,cp)}
    sources={}
    for name,d in [('jiu',j),('baifenwei',b)]:
        parsed=pd.to_datetime(d.date,errors='coerce');values=pd.to_numeric(d.fear_greed_index,errors='coerce')
        sources[name]=dict(rows=len(d),first=d.date.iloc[0],last=d.date.iloc[-1],duplicate_dates=int(d.date.duplicated().sum()),invalid_dates=int(parsed.isna().sum()),missing_values=int(values.isna().sum()),nonfinite_or_out_of_range=int((~np.isfinite(values)|values.lt(0)|values.gt(100)).sum()),date_ordered=bool(d.date.is_monotonic_increasing))
    lo=max(j.date.min(),b.date.min());hi=min(j.date.max(),b.date.max())
    jj=j.loc[j.date.between(lo,hi)];bb=b.loc[b.date.between(lo,hi)]
    js=set(jj.date);bs=set(bb.date)
    overlap=dict(start=lo,end=hi,jiu_rows=len(jj),baifenwei_rows=len(bb),dates_in_jiu_not_baifenwei=sorted(js-bs),dates_in_baifenwei_not_jiu=sorted(bs-js),scope='different retrieval times; set comparison is not independent exchange-calendar certification or current website completeness')
    observations=[]
    keys=['market_date','fear_greed_index','fear_data_date','fear_data_status','fear_page_updated_at','fear_retrieved_at','fear_fallback_reason','fear_csv_sha256']
    for path in sorted((ROOT/'runtime/ic_im_v1_4_r1/journal').glob('*.json')):
        x=json.loads(path.read_text(encoding='utf-8'));s=x.get('signals',{}).get('IM',{})
        if s.get('fear_data_status'):
            observations.append(dict(file=str(path.relative_to(ROOT)),**{k:s.get(k) for k in keys}));hashes[str(path.relative_to(ROOT))]=sha(path)
    frame=pd.DataFrame(observations);frame.to_csv(HERE/'saved_publication_observations.csv',index=False)
    jmeta=json.loads(mp.read_text(encoding='utf-8'))
    now=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
    live=dict(checked_at=now,baifenwei_page={'url':'https://baifenwei.com/indicator/fear-greed/','data_date':'2026-10-16','index_value':48.33,'displayed_update':'2026-10-16 11:00','method':'live web-page retrieval; CSV was not retrievable through this tool'},baifenwei_methodology={'url':'https://baifenwei.com/methodology/','publisher_statement':'Static pages built about hourly; data may lag about one hour; not a guaranteed closing-index release SLA'},jiu_last_successful_capture={'capture_time':jmeta['extraction_timestamp'],'latest_data_date':jmeta['last']['date'],'value':jmeta['last']['fear_greed_index'],'url':jmeta['source_url']},limitations=['Current shell requests to both hosts failed at the configured local proxy; these are environment failures and excluded from site-availability inference.','The web retrieval tool could read the Baifenwei page, could not parse CSV, and could not access Jiu indicator/API. These tool limits are not website uptime measurements.','No matched continuous timestamped availability logs exist for Jiu and Baifenwei; no relative uptime percentage or guaranteed Jiu release time can be established.'])
    dump(HERE/'quality_findings.json',{'audited_at':now,'sources':sources,'matched_snapshot_window':overlap,'live_observations':live,'publication_log_scope':'saved successful ledger records only; failed attempts and unpublished rows are not an uptime denominator'})
    dump(HERE/'input_hashes.json',hashes)
    report='''# 韭圈与百分位数据质量核查

结论：保存的历史快照中，韭圈完整度略好，结构化JSON也较便于程序读取；现有证据不足以证明它每天取数或准时发布更稳定。

## 历史快照

共同区间2020-08-03至2026-09-24，韭圈1493行、百分位1492行。百分位快照缺2026-07-27，韭圈相对这份快照没有少日。两份数据均无重复日期、非法日期、空数值或超出0—100的数值，日期递增。

两份快照下载时间不同，百分位样本止于9月24日不能算作后来交易日的线上缺失；也不能断言该站现版仍缺7月27日。上述集合比较不是独立全交易日日历认证。此前IC/IM实际回测窗口里，韭圈无缺口、百分位缺7月27日。

## 已保存的发布滞后

正式本地账本2026-10-14信号的IM来源记录：实际采集2026-10-15 01:48:08+08:00，源最新数据日2026-10-13，值39.32；网页更新时间2026-10-14 21:08。说明网页更新时钟与数据日不同，页面可访问并不代表当日指数已发布。

10月8、9、12、13日的已保存记录包含同日收盘后读数，但这些是成功入账记录，不能作为所有取数尝试的分母计算可用率。

## 当前在线信息与取数结构

- 本次网页核查：百分位显示2026-10-16、48.33、11:00更新，为盘中读数，不可据此宣称今日最终收盘值已发布。来源：https://baifenwei.com/indicator/fear-greed/ 。
- 百分位自己的说明称静态页面约每小时构建一次、数据存在至多约一小时延迟：https://baifenwei.com/methodology/ 。这不是固定收盘恐贪发布时间承诺，也不能解释所有源数据滞后。
- 当前生产采集器需要CSV和网页两次请求，均为8秒超时，没有在该函数内配置重试；网页用固定HTML结构解析，且两处最新日期、数值必须一致。因此网络问题、页面结构变化、两端更新不同步，都可能导致程序拒绝确认；需和“源当天未发布”分开统计。
- 韭圈早上10:33取得1502行结构化JSON历史，最新数据日10月15日，值2.28。可读的日期和数值不等于历史首次发布时间证据；这是页面内部实际调用接口，不代表获得长期对外API契约或可用性保证。
- 本次命令行访问两站都在本地已配置代理处失败，不能算两站故障。网页检索工具能读百分位页面，但不能解析CSV，且不能访问韭圈指标页/API；这些也是工具能力边界，不是站点可用率。未绕过网络限制。

## 对策略的含义

没有同时间窗口持续采集的成功/失败、到达时刻、数据日期与修订快照，暂不能认定韭圈线上更稳定。历史完整度、网络可访问性、盘中/收盘更新及时性和历史可追溯性必须分开。

AND需要两个来源满足条件，会受较慢或缺失的来源限制。OR也仍须按冻结的入场来源核对退出。两个指数的算法和阈值不同，不能在百分位失败时直接把韭圈数值填入百分位字段。

如要量化可靠性，应在相同采集时间窗口分别记录HTTP结果、数据日期、首次取得时刻、收盘状态、原始响应哈希及历史修订；本次仅做现有证据核查，没有创建定时任务或修改取数与策略逻辑。

## 文件

quality_findings.json保存结构化结果；saved_publication_observations.csv为账本中抽取的恐慌来源证据；input_hashes.json保留原始输入哈希。没有修改原始数据、账本、日报和策略。
'''
    (HERE/'record.md').write_text(report,encoding='utf-8')
    files=[p for p in HERE.iterdir() if p.is_file() and p.name!='audit_manifest.json']
    dump(HERE/'audit_manifest.json',{p.name:sha(p) for p in files})
    print(json.dumps({'matched_snapshot_window':overlap,'saved_publication_records':len(observations),'report':str(HERE/'record.md')},ensure_ascii=False))

if __name__=='__main__':main()
