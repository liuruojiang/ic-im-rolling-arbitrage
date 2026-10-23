from pathlib import Path
import json,base64
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib.dates as mdates

HERE=Path(__file__).resolve().parent
for p in ['C:/Windows/Fonts/msyh.ttc','C:/Windows/Fonts/simhei.ttf']:
    if Path(p).exists():
        fm.fontManager.addfont(p);plt.rcParams['font.family']=fm.FontProperties(fname=p).get_name();break
plt.rcParams['axes.unicode_minus']=False
stats=pd.read_csv(HERE/'window_metrics.csv')
daily=pd.read_csv(HERE/'daily_paths.csv.gz',parse_dates=['date'])
aud=json.loads((HERE/'independent_audit.json').read_text())
assert aud['status']=='PASS'
labels={'baseline':'现行：结算日收盘','carry_old':'周一开盘卖旧买新',
        'close_current':'周一收盘重新选约并换仓','close_frozen':'前日信号选约，周一收盘成交'}
colors={'baseline':'#204e80','carry_old':'#ce803e','close_current':'#087f79','close_frozen':'#8b78ab'}
fig,axs=plt.subplots(2,2,figsize=(14,8),sharex='col',gridspec_kw={'height_ratios':[2.2,1]})
for col,product in enumerate(['IC','IM']):
    for arm in labels:
        z=daily.loc[daily['product'].eq(product)&daily.arm.eq(arm)];nav=z.nav.to_numpy()
        style='--' if arm=='close_frozen' else '-'
        axs[0,col].plot(z.date,nav,label=labels[arm],color=colors[arm],ls=style,lw=1.5)
        axs[1,col].plot(z.date,100*(nav/np.maximum.accumulate(np.r_[1,nav])[1:]-1),color=colors[arm],ls=style,lw=1)
    axs[0,col].set_title(f'{product} 完整账户 · 真实挂牌期理论回测',loc='left',weight='bold')
    axs[0,col].set_ylabel('净值（起点=1）');axs[1,col].set_ylabel('回撤（%）')
    for ax in axs[:,col]:
        ax.grid(alpha=.15);ax.spines[['top','right']].set_visible(False)
    axs[1,col].xaxis.set_major_locator(mdates.YearLocator());axs[1,col].xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
axs[0,0].legend(frameon=False,loc='upper left',fontsize=9)
fig.suptitle('买Put展期：结算日收盘 vs 周一开盘 vs 周一收盘',x=.07,ha='left',weight='bold',fontsize=17)
fig.text(.07,.015,'IC：2022-09-19—2026-08-13；IM：2022-07-22—2026-08-14。每倍期货30%缓冲；收盘选约/同收盘成交为理论假设。',fontsize=9,color='#526276')
fig.tight_layout(rect=[.02,.04,1,.95]);png=HERE/'IC_IM_Put_Monday_Close_20261021.png'
fig.savefig(png,dpi=150,facecolor='white');plt.close(fig)

def table(window,include_frozen=False):
    z=stats.loc[stats.window.eq(window)].copy()
    if not include_frozen:z=z.loc[z.arm.ne('close_frozen')]
    z['方案']=z.arm.map(labels)
    z=z[['product','方案','cagr','max_dd','sharpe']].rename(columns={'product':'品种','cagr':'年化收益','max_dd':'最大回撤','sharpe':'夏普'})
    for c in ['年化收益','最大回撤']:z[c]=z[c].map(lambda x:f'{x:.2%}')
    z['夏普']=z['夏普'].map(lambda x:f'{x:.3f}')
    return z.to_html(index=False,border=0)

img=base64.b64encode(png.read_bytes()).decode()
csv=base64.b64encode((HERE/'window_metrics.csv').read_bytes()).decode()
html=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>IC/IM Put周一收盘换仓测试 · 2026-10-21</title>
<style>body{{font-family:"Microsoft YaHei",sans-serif;margin:0;background:#f1f5f8;color:#24354b;line-height:1.8}}main{{max-width:1120px;margin:28px auto;padding:42px 48px;background:white;border-radius:14px}}h1{{font-size:30px;line-height:1.4}}h2{{font-size:21px;color:#1c5575;margin-top:32px}}.lead{{background:#eaf4f2;padding:18px 22px;border-left:4px solid #087f79;font-size:17px}}.note{{color:#627184;font-size:13px}}table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{padding:10px 12px;text-align:left;border-bottom:1px solid #e1e8ef}}th{{background:#f1f5f9}}img{{width:100%;height:auto}}a{{color:#175d9b}}@media(max-width:700px){{main{{padding:20px 14px;margin:0}}h1{{font-size:25px}}td,th{{padding:6px 4px;font-size:12px}}}}</style>
<main><p class="note">独立候选研究 · 2026-10-21 · 收盘比较 v1 · 独立复验通过</p>
<h1>旧Put留到周一收盘，卖旧买新</h1>
<p class="lead"><b>周一收盘重新选约的全期收益略高，但没有各窗口一致的风险收益优势。</b>IC年化35.88%、最大回撤7.99%；IM年化30.70%、最大回撤13.95%。相同前日信号只改为收盘成交时，IM年化为28.58%，低于周一开盘29.33%；不能把主候选改善简单解释成收盘价更便宜。</p>
<p class="note">相同已挂牌真实数据重建的理论完整账户：IC 2022-09-19—2026-08-13，945日；IM 2022-07-22—2026-08-14，986日。未更新至今天。期货收益按每倍30%保证金/缓冲；原核心、动量、Fear25/50配对网格、卖Put路由、三倍兑现、无Call及费用保持。</p>
<h2>本次“收盘价”的定义</h2>
<p>旧Put保留到原结算节点后的第一个周一，收盘卖出旧Put、买入新Put；周一休市顺延到其后首个实际共同交易日。期间原普通风险退出、卖方/恢复路由优先，旧合约先到期则先结算。</p>
<ul><li><b>主候选：</b>周一收盘按该日已知风险信号、标的收盘与新挂牌链重新选约，并按同日两腿真实正收盘价记账；沿用现行维护的理论同收盘假设。</li>
<li><b>冻结控制：</b>前一完成交易日信号及行权价锚点保持，与开盘组一致，只将维护成交放到周一收盘；IC Delta按实际收盘价反解，资金规模也在收盘重新确定。因此仍包含成交时点对应的Delta/规模变化，不是固定手数的纯价格套利检验。</li>
<li>两组期限均以实际买入日+3个日历月最近挂牌到期日为目标；IC95% ETF、IM102%价格指数；核心及独立动量Put都按同一日历维护。</li></ul>
<h2>全期结果</h2>{table('full')}
<p>相对现行规则，周一收盘主候选：IC年化提高<b>0.49个百分点</b>，最大回撤从9.00%缩至7.99%；IM年化提高<b>0.76个百分点</b>，最大回撤从12.92%扩大到13.95%。</p>
<img src="data:image/png;base64,{img}" alt="四组完整账户净值和回撤">
<h2>近3年：IM收益几乎没有差别</h2>{table('3Y')}
<p>IC收盘组年化48.19%，原规则47.80%，回撤均约7.12%。IM收盘组42.28%，原规则42.23%，仅提高0.05个百分点；回撤则从12.92%扩大到13.95%。</p>
<h2>最近1年：品种分化</h2>{table('1Y')}
<p>IC收盘组年化60.16%，低于原规则62.42%，回撤均约6.04%。IM收盘组年化38.51%，高于原规则36.82%，回撤从10.15%缩至8.22%。窗口差异说明不能仅用全期增益作晋级依据。</p>
<h2>冻结前日信号的控制：不是单纯“收盘更划算”</h2>{table('full',True)}
<p>IC冻结控制年化35.56%、回撤8.09%；IM冻结控制年化28.58%、回撤14.07%。IM主候选的改善与周一收盘重新确定风险配置、行权价和后续持仓路径有关；本次没有进一步把这些贡献逐项完全分解，不能断言唯一原因。</p>
<h2>数据与执行验证</h2>
<ul><li>现行与周一开盘两条控制，在IC/IM中均逐日、逐事件复现上次保存结果；原窗口、费用和计算方式一致。</li>
<li>8条完整账户路径均按逐笔账本权益/现金变化独立复算；净值、费用、年化与回撤一致。新增收盘维护共<b>238笔买卖成交边</b>逐笔核对真实收盘价和费用，均在close阶段。</li>
<li>20个真实样本中污染执行日开盘、成交量、持仓量和结算价，收盘选约及数量保持；10个冻结控制样本中改变当日标的价格，合约身份不变（IC Delta数量可变化）。</li>
<li>159份受保护正式文件、旧研究输出与账本保持原哈希；59份原始数据/输入文件重新核对一致。生产策略、任务、日报和账户未修改。</li>
<li>IC周一仍早于ETF自身到期，新月份挂牌的目的尚未实现；本次收盘效果不应解释成IC新月份优势。IM收盘候选可以使用新挂牌月份。</li></ul>
<h2>限制与解释边界</h2>
<ul><li><b>收盘选约并按同一收盘价成交是理论假设。</b>日线不能证明等最终风险信号、最终收盘价出齐后仍能按该价完成双腿。实际尾盘选约与成交偏差需另验。</li>
<li>本次两条收盘候选未遇到目标维护收盘价缺失；周一开盘控制仍有2022年8月连续8个交易日目标新合约缺正开盘价而延期。近3年窗口排除该上市初期段。</li>
<li>非维护普通IM交易仍继承少量sticky-IV开盘代理：现行4笔、周一开盘4笔、两条收盘候选各3笔；新增收盘维护本身全部核验实际收盘，无价格代理。</li>
<li>继承IC重建合约主表、部分到期日近似、后下载Fear快照和理论分数数量；不等于全程点时实盘认证。卖方路由继承15% NAV预留参数，期货缓冲仍为每倍30%，未宣称经纪商验证。</li></ul>
<p><b>研究结论：</b>周一收盘可继续保留为候选；现有结果尚不足以直接替换正式维护规则。</p>
<p><a download="IC_IM_Put_Monday_Close_metrics_20261021.csv" href="data:text/csv;base64,{csv}">下载全期／3年／1年指标CSV</a></p></main></html>'''
out=HERE/'IC_IM_Put_Monday_Close_20261021.html';out.write_text(html,encoding='utf-8')
record='''# 买Put月度展期：下一周一收盘卖旧买新 完成记录

2026-10-21。仅研究；正式策略、自动任务与账本未改。规范和SHA见docs/ic_im_put_monday_close_20261021_v1_spec.md及completed.json。

同20261021_put_after_settlement_monday_v2基准及数据。新增close_current使用延期维护日收盘信号/标的/期权价重新选约成交；close_frozen用前日风险和行权价锚点、延期维护日收盘价成交、IC收盘Delta。原普通风险退出与卖方/恢复路线优先。

IC full CAGR/MaxDD：baseline 35.3924%/-9.0013%；周一开盘34.6438%/-9.0601%；周一收盘current35.8776%/-7.9929%；frozen35.5590%/-8.0870%。IM：29.9331%/-12.9182%；29.3300%/-13.8322%；30.6959%/-13.9527%；28.5821%/-14.0655%。

主收盘组3Y IC48.1932%/-7.1205%，IM42.2804%/-13.9527%；1Y IC60.1642%/-6.0399%，IM38.5127%/-8.2223%。全期略优不等于窗口一致或足以晋级。IM冻结控制反而逊于开盘组；不能简单归因于收盘价格更便宜。

独立复验PASS：8条账户路径按账本资金变化重算；238条新增收盘维护买卖边真实正收盘价及费用复核；20个开盘/成交量污染、10个冻结合约身份因果用例。现行与旧开盘逐日逐笔复现；159份保护文件、59份数据哈希不变。无新增维护收盘缺价；IM普通交易继承开盘代理4/4/3/3，按baseline/open/current/frozen顺序。

同收盘信号确认及成交为理论假设，不是全程实盘可成交；IC重建主表/部分日历近似、后下载Fear、模型分数仓位等局限继承。未延长真实数据至今天。图、HTML、逐日/逐笔、选约及missing_quotes、独立审计全部本目录保留。
'''
(HERE/'record.md').write_text(record,encoding='utf-8')
print(out)
