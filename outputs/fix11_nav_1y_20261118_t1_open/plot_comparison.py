"""Build report and chart from verified, same-start FIX11 paths."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter
import numpy as np
import pandas as pd

from repair_guards.replay_guards import FROZEN, expected_exchange_days, read_receipt, sha256, verify_chart_gate


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
import poe_ic_im_mainline_v1_4_bot as formal


def load_day_file(path, parent):
    if sha256(path) != parent['outputs_sha256'][path.name]:
        raise RuntimeError(f'comparison output does not match its passed account receipt: {path.name}')
    return pd.read_csv(path, float_precision='round_trip')


def series_metrics(nav, dd, daily):
    return {'ending_nav': float(nav.iloc[-1]), 'return': float(nav.iloc[-1] - 1),
            'max_drawdown': float(dd.min()), 'current_drawdown': float(dd.iloc[-1]),
            'fees_window_cny': float(daily.fees_cumulative.iloc[-1] - daily.fees_cumulative.iloc[0])}


def main():
    parent = read_receipt(HERE / 'full_account_verification.json')
    prior = read_receipt(FROZEN / 'full_account_verification.json')
    if sha256(Path(formal.__file__)) != prior['formal_source_sha256']:
        raise RuntimeError('official calendar source differs from the validated replay')
    days = expected_exchange_days(formal, '2025-11-17', '2026-11-18')
    if len(days) != 244 or parent['comparison_calendar'] != days:
        raise RuntimeError('comparison window differs from the official requested exchange dates')
    table = pd.DataFrame(index=pd.Index(days, name='date'))
    metrics = []
    for name in ('baseline', 't1_open'):
        p = HERE / f'{name}_daily.csv.gz'
        daily = load_day_file(p, parent)
        verify_chart_gate(daily, parent, officially_expected_days=days)
        for product in ('IC', 'IM'):
            selected = daily.loc[daily['product'].eq(product)].set_index('date', verify_integrity=True).loc[days]
            nav = selected.nav / selected.nav.iloc[0]
            dd = nav / nav.cummax() - 1
            market_returns = selected.return_net.iloc[1:]
            if not np.allclose(nav.iloc[1:], (1 + market_returns).cumprod(), atol=1e-12, rtol=0):
                raise RuntimeError(f'NAV and daily returns disagree: {product} {name}')
            table[f'{product}_{name}_nav'] = nav
            table[f'{product}_{name}_drawdown'] = dd
            table[f'{product}_{name}_daily_return'] = selected.return_net.where(selected.index != days[0], 0.)
            metrics.append({'product': product, 'execution': name, **series_metrics(nav, dd, selected)})
    metric_table = pd.DataFrame(metrics).set_index(['product', 'execution'])
    differences = []
    for product in ('IC', 'IM'):
        values = metric_table.loc[(product, 't1_open')] - metric_table.loc[(product, 'baseline')]
        differences.append({'product': product, 'execution': 'difference', **values.to_dict()})
    metric_table = pd.concat([metric_table, pd.DataFrame(differences).set_index(['product', 'execution'])])
    table_file = HERE / 'nav_comparison_1y.csv'
    metrics_file = HERE / 'metrics_comparison_1y.csv'
    table.to_csv(table_file, float_format='%.17g', encoding='utf-8-sig')
    metric_table.sort_index().to_csv(metrics_file, float_format='%.17g', encoding='utf-8-sig')

    plt.rcParams.update({'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'DejaVu Sans'],
                         'axes.unicode_minus': False, 'font.size': 10, 'axes.spines.top': False,
                         'axes.spines.right': False, 'figure.facecolor': 'white'})
    fig, axes = plt.subplots(2, 2, figsize=(12, 11), sharex=True,
                             gridspec_kw={'height_ratios': [1.75, 1]})
    fig.subplots_adjust(left=.085, right=.96, top=.865, bottom=.155, hspace=.14, wspace=.22)
    fig.suptitle('FIX11｜月度买 Put 次日开盘成交对照', x=.085, y=.966, ha='left', fontsize=21, weight='bold')
    fig.text(.085, .920, '2025-11-18 至 2026-11-18 收盘  ·  243 个交易日  ·  期初账户与行情相同',
             fontsize=11, color='#475467')
    dates = pd.to_datetime(table.index)
    styles = {'baseline': ('#68758b', '--', '原口径：当日收盘'),
              't1_open': ('#1567ad', '-', '新口径：次日开盘')}
    for col, product in enumerate(('IC', 'IM')):
        top, dd_ax = axes[:, col]
        top.set_title(product, loc='left', fontweight='bold', fontsize=14)
        for name, (color, linestyle, label) in styles.items():
            stats = metric_table.loc[(product, name)]
            top.plot(dates, table[f'{product}_{name}_nav'], color=color, ls=linestyle, lw=1.85,
                     label=f'{label}  {stats["return"]:+.2%}')
            dd_ax.plot(dates, table[f'{product}_{name}_drawdown'], color=color, ls=linestyle, lw=1.4,
                       label=f'最大回撤 {stats.max_drawdown:.2%}')
        top.axhline(1, color='#c2c9d3', lw=.7)
        dd_ax.axhline(0, color='#c2c9d3', lw=.7)
        top.legend(loc='upper left', framealpha=.9, fontsize=9)
        dd_ax.legend(loc='lower left', framealpha=.9, fontsize=9)
        for axis in (top, dd_ax):
            axis.grid(axis='y', color='#ebedf0', lw=.8)
        dd_ax.yaxis.set_major_formatter(PercentFormatter(xmax=1, decimals=0))
        dd_ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        dd_ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    axes[0, 0].set_ylabel('归一净值')
    axes[1, 0].set_ylabel('回撤')
    fig.text(.085, .089, '2025-11-17 收盘归一为 1。候选在维护日收盘冻结张数和合约，用下一交易日同合约开盘价成交。',
             color='#475467', fontsize=9)
    fig.text(.085, .058, '期货及 ETF 单边 1 bp，期权按标的名义金额单边 5 bp。研究净值未计滑点及冲击成本。',
             color='#475467', fontsize=9)
    image = HERE / 'nav_drawdown_comparison_1y.png'
    fig.savefig(image, dpi=170)
    plt.close(fig)
    (HERE / 'chart_verification.json').write_text(json.dumps({
        'status': 'PASS', 'required_exchange_dates': days, 'rows': len(table),
        'account_receipt_sha256': sha256(HERE / 'full_account_verification.json'),
        'files': {path.name: sha256(path) for path in (metrics_file, table_file, image)},
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(metric_table.sort_index().to_string())


if __name__ == '__main__':
    main()
