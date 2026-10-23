from pathlib import Path
import base64
import json
import html
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib.dates as mdates

HERE=Path(__file__).resolve().parent
for font in [Path('C:/Windows/Fonts/msyh.ttc'),Path('C:/Windows/Fonts/simhei.ttf')]:
    if font.exists():
        fm.fontManager.addfont(str(font))
        plt.rcParams['font.family']=fm.FontProperties(fname=str(font)).get_name()
        break
plt.rcParams['axes.unicode_minus']=False
daily=pd.read_csv(HERE/'daily_paths.csv.gz',parse_dates=['date'])
stats=pd.read_csv(HERE/'window_metrics.csv')
audit=json.loads((HERE/'independent_audit.json').read_text())
assert audit['status']=='PASS'
labels={'baseline':'现行：结算日收盘维护','buy_only':'结算日卖旧，周一开盘买新','carry_old':'旧Put留到周一，开盘卖旧买新'}
colors={'baseline':'#1b4e82','buy_only':'#d2742e','carry_old':'#167f76'}
plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
fig,axs=plt.subplots(2,2,figsize=(14,8),gridspec_kw={'height_ratios':[2.2,1]},sharex='col')
for col,product in enumerate(['IC','IM']):
    for arm in labels:
        z=daily.loc[daily['product'].eq(product)&daily.arm.eq(arm)]
        nav=z.nav.to_numpy()
        dd=nav/np.maximum.accumulate(np.r_[1.,nav])[1:]-1
        axs[0,col].plot(z.date,nav,color=colors[arm],label=labels[arm],lw=1.8)
        axs[1,col].plot(z.date,dd*100,color=colors[arm],lw=1.1)
    axs[0,col].set_title(f'{product} 完整账户 · 真实挂牌期理论回测',loc='left',fontweight='bold')
    axs[0,col].set_ylabel('净值（起点=1）')
    axs[1,col].set_ylabel('回撤（%）')
    for ax in axs[:,col]:
        ax.grid(alpha=.16)
    axs[1,col].xaxis.set_major_locator(mdates.YearLocator())
    axs[1,col].xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
axs[0,0].legend(loc='upper left',frameon=False,fontsize=9)
fig.suptitle('买Put展期延后到结算后下一周一开盘',x=.07,ha='left',fontsize=17,fontweight='bold')
fig.text(.07,.015,'IC：2022-09-19—2026-08-13；IM：2022-07-22—2026-08-14。期货按每倍30%保证金/缓冲；未更改正式研究信号。',fontsize=9,color='#536171')
fig.tight_layout(rect=[.02,.04,1,.95])
chart=HERE/'IC_IM_Put_Monday_Test_20261021.png'
fig.savefig(chart,dpi=160,facecolor='white')
plt.close(fig)

def table(window):
    z=stats.loc[stats.window.eq(window)].copy()
    z['方案']=z.arm.map(labels)
    out=z[['product','方案','cagr','max_dd','sharpe']].rename(columns={
        'product':'品种','cagr':'年化收益','max_dd':'最大回撤','sharpe':'夏普'})
    out['年化收益']=out['年化收益'].map(lambda x:f'{x:.2%}')
    out['最大回撤']=out['最大回撤'].map(lambda x:f'{x:.2%}')
    out['夏普']=out['夏普'].map(lambda x:f'{x:.3f}')
    return out.to_html(index=False,border=0,classes='metrics',escape=True)

encoded=base64.b64encode(chart.read_bytes()).decode()
csv=base64.b64encode((HERE/'window_metrics.csv').read_bytes()).decode()
content=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>IC/IM Put周一展期测试 · 2026-10-21</title><style>
body{{font-family:"Microsoft YaHei",sans-serif;background:#f1f4f8;color:#203348;margin:0;line-height:1.75}}
main{{max-width:1120px;margin:32px auto;background:white;padding:42px 50px;border-radius:16px}}
h1{{font-size:30px;line-height:1.3;margin:8px 0 18px}}h2{{font-size:21px;margin-top:32px;color:#174e75}}
.tag{{color:#64758b;font-size:13px}}.lead{{background:#eaf3f7;border-left:4px solid #167f76;padding:18px 22px;font-size:17px}}
table{{width:100%;border-collapse:collapse;font-size:14px}}th{{text-align:left;background:#f0f5f9}}td,th{{padding:10px 12px;border-bottom:1px solid #e3eaf1}}
img{{width:100%;height:auto}}.note{{font-size:13px;color:#596a7e}}a{{color:#135d98}}.box{{padding:12px 18px;border:1px solid #e0e7ef;border-radius:8px}}
@media(max-width:700px){{main{{padding:24px 16px;margin:0;border-radius:0}}h1{{font-size:25px}}td,th{{padding:7px 5px;font-size:12px}}}}
</style><main><div class="tag">独立候选研究 · 2026年10月21日 · v2执行复核通过</div>
<h1>买Put展期推迟到下一周一开盘，是否更好？</h1>
<p class="lead"><b>本次样本不支持两套策略统一延后。</b>IM确实能选到更接近3个月的新挂牌月份，但“周五卖旧、周一买新”降低收益并扩大回撤。旧Put保留到周一再换，差距显著缩小；IM近3年收益几乎相同，但回撤仍大。IC在这个周一通常还没有ETF期权新月份挂牌。</p>
<p class="note">真实挂牌期理论完整账户：IC 2022-09-19—2026-08-13（945日），IM 2022-07-22—2026-08-14（986日）。没有把行情更新到今天；不是实盘收益或当天交易信号。</p>
<h2>三个方案只改变什么</h2>
<table><thead><tr><th>方案</th><th>旧Put卖出</th><th>新Put买入</th><th>等待期</th></tr></thead><tbody>
<tr><td>现行基准</td><td>月度结算日收盘</td><td>同日收盘</td><td>同次维护</td></tr>
<tr><td>只推迟买入</td><td>月度结算日收盘</td><td>结算后下一周一开盘</td><td>旧保护已卖出，有保护空档</td></tr>
<tr><td>卖旧买新一起推迟</td><td>下一周一开盘</td><td>同一周一开盘</td><td>保留旧Put；到期先按原规则结算</td></tr></tbody></table>
<p>周一休市则顺延到其后第一个实际共同交易日。期限以新买入日加3个日历月为目标，选最近已挂牌到期日；行权价使用前一实际交易日已知价格，IC为ETF的95%，IM为中证1000价格指数的102%。IC张数按新合约实际开盘价及ETF开盘反解Delta。其余风险信号、固定核心0.5倍、动量、Fear25/50网格、卖Put路由、三倍兑现、费用与现金规则保持。</p>
<h2>完整样本结果</h2>{table('full')}
<p>仅推迟买入：IC年化降低<b>1.47个百分点</b>；IM降低<b>4.20个百分点</b>。旧Put留到周一再换：IC降低<b>0.75个百分点</b>；IM降低<b>0.60个百分点</b>。这些是完整账户差异，包含仓位路径、资金与后续三倍兑现变化，不能全归因为单一成本。</p>
<img src="data:image/png;base64,{encoded}" alt="IC与IM三个展期方案净值与回撤对照">
<h2>近3年：避开IM上市初期的缺开盘价段</h2>{table('3Y')}
<p>IM留旧到周一方案：年化<b>42.11%</b>，基准<b>42.23%</b>，仅差<b>0.12个百分点</b>；最大回撤从<b>12.92%</b>增至<b>13.83%</b>。这支持“效果接近、风险收益有取舍”，不足以支持晋级为新规则。</p>
<h2>最近1年：结果存在阶段差异</h2>{table('1Y')}
<p>IM留旧到周一在最近1年略优：年化37.15%对36.82%，最大回撤8.66%对10.15%；与全期和近3年一起看，没有稳定的全面优势。</p>
<h2>是否达到了新合约上市的目的</h2>
<p><b>IM达到了。</b>两种延期方案中，各有36条实际发生买入的维护选约记录（31个维护月份，包含核心与独立动量两腿），其中22条到期月份相对基准改变，20条使用前一交易日尚未挂牌的月份。所选合约平均剩余期限约<b>87.8天</b>，同批基准约81.0天；距离“执行日+3个月”的平均误差约<b>4.1天</b>。月份更接近目标，并没有自动带来更高收益。</p>
<p><b>IC没有达到。</b>本次实际成交维护选约中，延期方案没有改变到期月份，也没有选中新挂牌月份。原因是原IC维护按期货月度结算节点（通常第三个周五）触发，下一周一通常还在ETF期权第四个周三到期之前。若要专门测试“等ETF新月份挂牌”，应另行预注册ETF自身到期后的交易日方案，不能把本次结果当作该方案的结果。</p>
<h2>核验与数据限制</h2>
<div class="box"><p>6条账户路径均已独立按账本现金及权益变化复算，指标一致；145笔与延期维护选约匹配的买入逐笔核对真实日线开盘价。IC/IM各5个真实选约污染测试，把执行日收盘、结算价、成交量和持仓量改为空值后，选约结果不变；原基准逐日复现误差小于5×10⁻¹⁶。147份受保护文件保持不变。</p></div>
<ul><li>IM 2022年8月22—31日目标新月份连续8个交易日缺正开盘价，两种候选都延至取得真实开盘证据才买入。完整样本受此数据限制影响；近3年窗口不包含该缺价段。</li>
<li>普通交易继承的IM sticky-IV开盘代理：基准4笔、仅推迟买入2笔、留旧方案4笔；本次新增挂牌月份的维护买入均用真实正开盘价，没有以当日收盘代替。</li>
<li>日线“开盘价”只是该合约日线首笔成交参考，不证明恰在9:30即可足量成交；新挂牌合约可能成交较晚，严格钟点开盘可执行性尚未验证。</li>
<li>IC合约主表为历史重建，部分到期日继承原引擎日历近似；Fear为后下载快照。结果是理论完整账户，不是全程点时可成交认证。</li>
<li>每1倍期货收益按30%保证金/缓冲计算。继承基准卖Put路由的15% NAV预留参数，不是将期货保证金降至15%，也不代表经纪商验证。</li>
<li>v1输出和首次审计完整保留；v2修正延期维护时的稳定张数缓存和成本锚点处理，候选定义未改变。正式研究信号、生产脚本与持久账本未修改。</li></ul>
<p><a download="IC_IM_Put_Monday_metrics_20261021.csv" href="data:text/csv;base64,{csv}">下载全期／3年／1年指标CSV</a></p>
<p class="note">结论：本次不升级正式规则。若继续研究，IM“保留旧Put、周一一起换仓”可作为独立观察候选；IC需要另外定义与ETF期权自身挂牌日匹配的维护时点。</p>
</main></html>'''
(HERE/'IC_IM_Put_Monday_Test_20261021.html').write_text(content,encoding='utf-8')
record='''# 买Put展期延后至结算后下一周一开盘：v2完成记录

状态：研究比较完成、独立复验PASS；未修改正式策略、账本、自动任务或发布渠道。

预注册：docs/ic_im_put_after_settlement_monday_20261021_v1_spec.md；执行验证修订v2_spec.md；SHA见completed.json。v1结果保留，最终以本v2目录为准。

主候选：原结算收盘卖旧，下一周一开盘买新。辅助候选：留旧到周一一起换。节假日顺延至其后首个实际交易日。沿用期货月度结算节点，不改ETF自身到期日。实际新挂牌月份、已知前收盘行权价、真实正开盘价和IC开盘Delta，不以当日收盘选约。

完整样本CAGR/MaxDD：IC基准35.3924%/-9.0013%；只延后买入33.9201%/-9.4049%；留旧34.6438%/-9.0601%。IM基准29.9331%/-12.9182%；只延后买入25.7353%/-13.9430%；留旧29.3300%/-13.8322%。

近3年IM留旧42.1149%/-13.8322%，基准42.2300%/-12.9182%；近1年留旧37.1529%/-8.6561%，基准36.8159%/-10.1494%。阶段结果不一致，不支持正式晋级。

IM每臂36条实际买入维护选约（31个维护月份/核心和动量两腿），22条到期月改变，20条选择新挂牌月份，平均期限87.78天；IC无到期月改变。IC下一周一早于ETF到期，未实现等待新月份的目的。

真实数据限制：2022-08-22—31 IM目标新月份缺正开盘价，候选均延期买入；普通IM继承基准/只延后/留旧4/2/4笔sticky-IV开盘代理，新增维护买入无代理。日线首笔成交不代表严格9:30可成交；IC重建合约主表与原到期日近似、后下载Fear快照均继承。不更新至今天。

复现run_study.py拒绝覆盖完成输出；复验audit_study.py。独立账本复算6条路径；145笔新增维护选约实际买入核对开盘；10个真实收盘字段污染用例通过；147份源/旧输出/账本哈希不变。数据文件SHA另见本目录data_manifest.json。图与完整报告见IC_IM_Put_Monday_Test_20261021.html/png，指标window_metrics.csv，daily_paths/events/open_selections/missing_opens/maintenance_calendar及独立审计均保留。
'''
(HERE/'record.md').write_text(record,encoding='utf-8')
print(str(HERE/'IC_IM_Put_Monday_Test_20261021.html'))
