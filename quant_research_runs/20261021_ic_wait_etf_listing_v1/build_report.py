from pathlib import Path
import json,base64
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

HERE=Path(__file__).resolve().parent
for p in ['C:/Windows/Fonts/msyh.ttc','C:/Windows/Fonts/simhei.ttf']:
    if Path(p).exists():
        fm.fontManager.addfont(p);plt.rcParams['font.family']=fm.FontProperties(fname=p).get_name();break
plt.rcParams['axes.unicode_minus']=False
d=pd.read_csv(HERE/'daily_paths.csv.gz',parse_dates=['date'])
m=pd.read_csv(HERE/'window_metrics.csv')
a=json.loads((HERE/'independent_audit.json').read_text());assert a['status']=='PASS'
names={'baseline':'现行：期货结算日收盘','settlement_frozen':'固定下一周一收盘',
       'wait_nearest':'等ETF新月份挂牌，仍选最近3个月','wait_min3':'等ETF新月份挂牌，买入至少3个月'}
colors=['#214f7f','#9a8bb7','#d08238','#087e76']
fig,axs=plt.subplots(2,1,figsize=(12,7),sharex=True,gridspec_kw={'height_ratios':[2.2,1]})
for (arm,label),color in zip(names.items(),colors):
    z=d.loc[d.arm.eq(arm)];nav=z.nav.to_numpy()
    axs[0].plot(z.date,nav,label=label,color=color,lw=1.6)
    axs[1].plot(z.date,100*(nav/np.maximum.accumulate(np.r_[1,nav])[1:]-1),color=color,lw=1)
for ax in axs:ax.grid(alpha=.15);ax.spines[['top','right']].set_visible(False)
axs[0].legend(frameon=False,loc='upper left',fontsize=9);axs[0].set_ylabel('净值（起点=1）');axs[1].set_ylabel('回撤（%）')
fig.suptitle('IC买Put：按ETF新月份挂牌节奏换仓',x=.08,ha='left',weight='bold',fontsize=17)
fig.text(.08,.012,'真实挂牌期理论完整账户：2022-09-19—2026-08-13；每倍期货30%缓冲；原结算日冻结保护信息。',fontsize=9,color='#58697e')
fig.tight_layout(rect=[.02,.04,1,.95]);png=HERE/'IC_Put_Wait_ETF_Listing_20261021.png';fig.savefig(png,dpi=150);plt.close(fig)

def table(window):
    z=m.loc[m.window.eq(window)].copy();z['方案']=z.arm.map(names)
    z=z[['方案','cagr','max_dd','sharpe']].rename(columns={'cagr':'年化收益','max_dd':'最大回撤','sharpe':'夏普'})
    for c in ['年化收益','最大回撤']:z[c]=z[c].map(lambda x:f'{x:.2%}')
    z['夏普']=z['夏普'].map(lambda x:f'{x:.3f}')
    return z.to_html(index=False,border=0)

img=base64.b64encode(png.read_bytes()).decode();csv=base64.b64encode((HERE/'window_metrics.csv').read_bytes()).decode()
text=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>IC等ETF新月份挂牌换Put测试 · 2026-10-21</title><style>body{{font-family:"Microsoft YaHei",sans-serif;line-height:1.8;background:#f1f4f8;color:#24374b;margin:0}}main{{max-width:1050px;margin:28px auto;background:white;padding:40px 48px;border-radius:14px}}h1{{font-size:29px}}h2{{font-size:21px;margin-top:30px;color:#22566e}}.lead{{padding:18px 22px;background:#eaf5f2;border-left:4px solid #087e76}}.note{{font-size:13px;color:#65768a}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{padding:9px 12px;text-align:left;border-bottom:1px solid #e0e7ef}}th{{background:#f0f5f9}}img{{width:100%;height:auto}}a{{color:#195e96}}@media(max-width:700px){{main{{margin:0;padding:20px 14px}}h1{{font-size:25px}}td,th{{padding:6px 4px;font-size:12px}}}}</style>
<main><p class="note">独立研究候选 · 2026-10-21 · 原规则、历史输出及账本未修改</p><h1>IC应按ETF期权新月份挂牌节奏维护</h1>
<p class="lead"><b>日历应与IM分开；但仅等待新月份挂牌，仍不能保证买到3个月以上的Put。</b>增加买入期限下限后，本样本维护选约的期限为97—181天、平均约150天；全期年化34.21%，最大回撤7.76%，相对现行35.39%／9.00%体现收益与风险取舍。</p>
<p class="note">相同IC fix11完整历史反事实账户，真实期权样本2022-09-19—2026-08-13，945日；没有更新至今天。其他核心、动量、网格、卖Put、三倍兑现与费用规则保持，IC无Call。每倍期货按30%缓冲。</p>
<h2>测试规则</h2><p>原期货月度结算日（通常周五）冻结保护信号和ETF价格/95%行权价依据，保留旧Put。逐日只检查当时已观测的标准合约月份；相对原结算日真正多出一个到期月份后，才在该日收盘卖旧买新，节假日按真实行情面板处理。普通风险退出、卖方与恢复路由仍优先。</p>
<ul><li><b>只等挂牌：</b>继续取执行日+3个月最近的挂牌到期日，可向前或向后。</li><li><b>等挂牌＋期限下限：</b>同一时点只允许到期日不早于执行日+3个月，在满足条件的挂牌月份中取最近一个；不强行买最新或最远月份。</li></ul>
<p>保护目标与行权价锚点冻结原日；新合约收盘权利金、IC Delta和实际资金规模在执行收盘计算。月度维护之外的新进入和三倍兑现继续原规则，因此期限下限没有自动扩大到所有其他买入事件。</p>
<h2>为什么“等挂牌”本身不够：2023年1月与2月</h2><table><tr><th>实际换仓日</th><th>原信号日</th><th>只等挂牌</th><th>加不少于3个月下限</th></tr>
<tr><td>2023-01-31</td><td>2023-01-20</td><td>3月22日到期，剩50天</td><td>6月28日到期，剩148天</td></tr>
<tr><td>2023-02-23</td><td>2023-02-17</td><td>4月26日到期，剩62天</td><td>6月28日到期，剩125天</td></tr></table>
<p>ETF期权挂牌当月、下月及随后两个季月，新的挂牌月份不一定正好位于3个月后。1月底新增的是更远季月；2月底虽新增4月，但距离买入仍只有约两个月。原“最近”规则可以反复选择偏短月份。官方条款：<a href="https://www.sse.com.cn/assortment/options/contract/c/c_20230303_5717361.shtml">上交所中证500ETF期权合约基本条款</a>。</p>
<h2>全期完整账户结果</h2>{table('full')}
<img src="data:image/png;base64,{img}" alt="IC四组净值和回撤对照">
<h2>近3年</h2>{table('3Y')}
<h2>最近1年</h2>{table('1Y')}
<p>期限下限组全期年化比现行低1.18个百分点，最大回撤缩小约1.24个百分点；近3年年化45.75%对47.80%，最近1年57.35%对62.42%。保护期限更长未形成所有窗口收益优势。</p>
<h2>期限目标实现了多少</h2><table><tr><th>指标</th><th>只等挂牌</th><th>等挂牌＋期限下限</th></tr>
<tr><td>有正保护目标的维护选约记录</td><td>28</td><td>28</td></tr><tr><td>选约时不足3个日历月</td><td>23次</td><td>0次</td></tr>
<tr><td>买入候选剩余期限范围</td><td>50—118天</td><td>97—181天</td></tr><tr><td>平均剩余期限</td><td>79.6天</td><td>149.8天</td></tr>
<tr><td>有Put的交易日中，全部在持Put均至少3个月</td><td>14.1%</td><td>76.6%</td></tr></table>
<p><b>“维护买入时不少于3个月”不等于“任何时刻都在3个月以上”。</b>持有期会自然衰减；本次还保留普通重新进入与三倍兑现的原期限规则。例如期限下限组2023年1月31日换为6月Put，2月6日按原风险信号退出、2月9日普通重入仍按原最近期限选3月Put，直到2月23日新月份挂牌维护才换回6月。若要求所有重新买入均满足下限，须另行注册统一新选约期限规则，不能用本次月度维护结果冒称实现。</p>
<h2>核验与限制</h2><ul><li>现行及原结算日冻结/周一收盘控制逐日、逐事件复现旧结果；4条账户账本、净值、费用与指标独立复算一致。</li><li>两条等待候选各45个维护日历事件。新增月份首次出现日逐日独立核对，与第四周三到期、遇休市顺延后下一实际交易日的月度规则映射无偏差。</li><li>两候选合计100条维护买卖成交边均逐笔核对真实正收盘价和费用；无维护收盘缺价。28条期限下限维护选约全部符合下限，原信号日绑定正确。</li><li>154份受保护正式文件、旧结果与账本保持原哈希；59份原始数据/输入文件重新核对一致。</li><li>历史合约主表为重建，面板首次出现与月度规则一致并不等于取得全历史交易所点时挂牌通知；部分到期语义与日历近似、后下载Fear、模型分数数量等限制继承。</li><li>收盘价格及Delta用于同收盘维护，是理论成交假设，未验证尾盘可得价和盘口容量；没有新增账户下单授权、生产部署或日报变更。</li></ul>
<p><a download="IC_Wait_ETF_Listing_metrics_20261021.csv" href="data:text/csv;base64,{csv}">下载全期／3年／1年指标CSV</a></p></main></html>'''
(HERE/'IC_Put_Wait_ETF_Listing_20261021.html').write_text(text,encoding='utf-8')
record='''# IC等待ETF期权新月份挂牌：完成记录

2026-10-21，研究完成，独立复验PASS；正式策略、历史链、日报任务均未改。规范docs/ic_put_wait_etf_listing_20261021_v1_spec.md，SHA见completed.json。

冻结原期货月度结算日保护目标和95%ETF行权价依据，保留旧Put，实际标准合约面板新增到期月份后当日收盘维护。两臂分别wait_nearest与wait_min3（新到期不得早于执行日+3个月）。只对月度维护施加期限下限，普通入场/三倍兑现不变；不保证所有在持Put恒有3个月以上。

完整账户945日，2022-09-19—2026-08-13。baseline年化35.3924%、MaxDD-9.0013%；旧周一冻结35.5291%/-8.0870%；wait_nearest33.8481%/-7.8934%；wait_min3 34.2147%/-7.7605%。min3近3年45.7471%/-7.1204%，近1年57.3516%/-6.0279%。维持每倍期货30%缓冲。

每臂28条维护选约；nearest23条不足3个月、平均79.57天、最短50；min3全部符合下限、97—181天、平均149.82天。有Put日中全部在持至少3个月的比例：baseline41.4%、旧周一39.8%、nearest14.1%、min3 76.6%。其他普通进入仍可能重选近月，不能把月度下限当作全路径下限。

2023-01-31挂牌后，nearest仍选3月22日（50天）；min3选6月28日（148天）。2023-02-23，nearest选4月26日（62天）；min3选6月28日（125天）。真实月份新增并不保证最近3个月候选偏远。

独立账本/费用/收益复核4条路径；控制逐日逐事件复现。每候选45个事件的首次新月份观察日与规则日历一致（含春节顺延），100条新维护买卖边真实收盘价/费用核对。154份旧文件/链/结果、59份数据哈希不变。原重建主表、部分到期日近似、Fear后下载、理论分数仓位与同收盘成交局限继承。

run_wait.py拒绝覆盖完成输出；audit_wait.py独立核验；逐日、逐笔、维护映射、listing_observations、选约、daily_protection_tenor、calendar_rule_crosscheck、指标、图/HTML、备份及审计完整保留。
'''
(HERE/'record.md').write_text(record,encoding='utf-8')
print(HERE/'IC_Put_Wait_ETF_Listing_20261021.html')
