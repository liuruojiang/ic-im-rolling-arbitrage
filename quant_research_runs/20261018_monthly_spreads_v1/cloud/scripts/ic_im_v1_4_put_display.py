"""Presentation of recorded Put evidence; no quote fetch or strategy calculation."""
from __future__ import annotations

import html
import math
import re
import ic_im_option_maintenance_notice as maintenance_notice
import ic_im_v1_4_delivery_contract as delivery_contract


EFFECTIVE_DAY = '2026-10-09'


def value(raw, suffix=''):
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
        return 'N/A（result未保存有效数值）'
    return f'{raw:g}{suffix}'


def pct(raw):
    return value(100 * raw, '%') if isinstance(raw, (int, float)) and not isinstance(raw, bool) else value(None)


def contract_terms(contract):
    code = str(contract or '')
    etf = re.fullmatch(r'510500P(\d{2})(\d{2})M(\d{5})', code)
    mo = re.fullmatch(r'MO(\d{2})(\d{2})-P-(\d+)', code)
    match = etf or mo
    if not match or not 1 <= int(match[2]) <= 12:
        return 'N/A（合约编码未包含可核验到期月/行权价）'
    strike = int(match[3]) / (1000 if etf else 1)
    return f'20{match[1]}-{match[2]} / {strike:g}（按合约编码）'


def monthly_candidate(signal):
    """A dated ordinary monthly preview is separate from a lifecycle 3x plan."""
    return signal.get('close_confirmed') is False and bool(maintenance_notice.reminder(
        signal.get('product'), signal, is_session=delivery_contract.is_session))


def ic_contract(signal, when, leg):
    """Read the producer's separate held/ordinary-target IC identities.

    The independent core target is also what the ordinary T+1 planner uses.
    Confirmed lifecycle transitions update that field. Intraday monthly
    candidates remain in the ordinary target field until close; an unexecuted
    3x replacement remains a separate lifecycle plan.
    """
    if signal.get(f'put_{when}_{leg}_qty') == 0:
        return None
    if leg == 'core':
        if when == 'current':
            return signal.get('v14_core_put_current_contract')
        if monthly_candidate(signal):
            return signal.get('put_target_contract')
        return signal.get('v14_core_put_contract') or signal.get('put_target_contract')
    return signal.get(f'put_{when}_contract')


def evidence(product, signal):
    """Return human tables from this result only, keeping lifecycle scopes apart."""
    if str(signal.get('market_date', '')) < EFFECTIVE_DAY:
        return []  # Do not change previously delivered historical rendering.
    s = signal
    monthly = monthly_candidate(s)
    target_label = '当日维护候选' if monthly else '下一目标'
    names = {'core': '核心', 'momentum': '动量', 'grid': '网格', 'total': '合计'}
    if product == 'IC':
        tier = f"估值分{value(s.get('score'))}；{s.get('valuation_tier_label', 'N/A（估值档缺失）')}；估值Delta {pct(s.get('valuation_put_delta'))}"
        final = f"MOM120 {pct(s.get('momentum_120'))}；下限{pct(s.get('mom120_floor_delta'))}；最终核心/动量/组合Delta {pct(s.get('core_put_target_delta'))}/{pct(s.get('momentum_put_target_delta'))}/{pct(s.get('total_put_target_delta'))}；{s.get('core_put_driver', 'N/A（主导来源缺失）')}"
        qty = lambda when, leg: s.get(f'put_{when}_{leg}_qty')
        contracts = lambda when, leg: ic_contract(s, when, leg)
        ratio_note = 'IC当日动态比例按IC名义金额/ETF期权名义金额×目标Delta/所选Put绝对Delta并取整；result未保存所选Put绝对Delta，本次动态重算N/A。下表比例仅为已记录数量/已记录期货单位，沿用数量不冒称今日重选。'
    else:
        tier = f"估值分{value(s.get('score'))}；绝对轴{s.get('absolute_valuation_tier_label', 'N/A')}；57个月相对轴{s.get('relative_valuation_tier_label', 'N/A')}；估值每完整IM {value(s.get('valuation_puts_per_full_core'))}张"
        final = f"MOM120 {pct(s.get('momentum_120'))}；下限每完整IM {value(s.get('mom120_floor_puts_per_full_core'))}张；当前/下一离散档 {value(s.get('v13_current_parent_puts_per_full_core'))}/{value(s.get('v13_parent_puts_per_full_core'))}张；{s.get('core_put_driver', 'N/A（主导来源缺失）')}"
        qty = lambda when, leg: 0 if leg == 'grid' else s.get(f'{leg}_put_{when}_qty_normalized')
        contracts = lambda when, leg: s.get(f'{leg}_put_{when}_contract')
        ratio_note = 'IM核心Put=绝对估值轴、57个月相对轴、MOM120下限三者取高×0.5；动量Put仅MOM120<0时按3×0.5×原动量多头执行权重，空仓为0。独立生命周期数量另列，不能替代普通核心保护。'
    tables = [
        ('Put 档位、期货对应数量与行权价', ['估值档（独立）', '最终生效保护档与主导来源'], [[tier, final]]),
        ('当前与下一交易日规范化数量', ['袖', f"当前（账本来源{s.get('state_anchor_day', 'N/A')}）期货 / Put", f"下一交易日{s.get('next_trade_date', 'N/A')}研究目标 期货 / Put"], [
            [names[leg], f"{value(s.get(f'{leg}_units_current') if leg in ('core', 'momentum') else s.get('grid_current') if leg == 'grid' else s.get('total_units_current'))} / {value(qty('current', leg))}",
             f"{value(s.get(f'{leg}_units_target') if leg in ('core', 'momentum') else s.get('grid_target') if leg == 'grid' else s.get('total_units_target'))} / {value(qty('target', leg))}"]
            for leg in ('core', 'momentum', 'grid', 'total')]),
    ]
    if monthly:
        tables[1] = ('当前与分时点研究目标规范化数量',
            ['袖', f"当前（账本来源{s.get('state_anchor_day', 'N/A')}）期货 / Put",
             f"期货：下一交易日{s.get('next_trade_date', 'N/A')}研究目标",
             f"Put：{s.get('market_date', 'N/A')}收盘维护候选（盘尾复核）"],
            [[names[leg], tables[1][2][i][1],
              value(s.get(f'{leg}_units_target') if leg in ('core', 'momentum') else s.get('grid_target') if leg == 'grid' else s.get('total_units_target')),
              value(qty('target', leg))]
             for i, leg in enumerate(('core', 'momentum', 'grid', 'total'))])
    ratios = []
    for when in ('current', 'target'):
        units, puts = s.get(f'total_units_{when}'), qty(when, 'total')
        ratio = puts / units if isinstance(units, (int, float)) and units > 0 and isinstance(puts, (int, float)) else None
        ratios.append(['当前' if when == 'current' else target_label, value(ratio, f'张 / 每1张{product}（账本规范化数量之比）') if ratio is not None else 'N/A（期货为空仓或数量缺失）'])
    tables.append(('每1张期货对应Put', ['状态', '记录数量比例'], ratios))
    rows = []
    for when in ('current', 'target'):
        for leg in ('core', 'momentum'):
            contract = contracts(when, leg)
            q = qty(when, leg)
            empty = q == 0
            pricing = s.get('put_pricing_evidence') or {}
            if product == 'IC':
                field = ('v14_core_put_current_contract' if when == 'current' else 'v14_core_put_contract') if leg == 'core' else f'put_{when}_contract'
                if monthly and when == 'target' and leg == 'core':
                    field = 'put_target_contract'
            else:
                field = 'put_current_contract' if when == 'current' and leg == 'core' else f'{leg}_put_{when}_contract'
            quote = pricing.get(field) or {}
            raw = quote.get('market_text') if quote.get('contract') == contract else None
            raw = raw or s.get('put_market')
            raw = str(raw) if contract and f'`{contract}`' in str(raw) else 'N/A（result未保存该合约报价原文）'
            if '买/卖' not in raw:
                raw += '；买/卖价N/A（result未保存）'
            rows.append([f"{'当前' if when == 'current' else target_label} {names[leg]}", value(q), str(contract or ('无（数量0）' if empty else 'N/A（result合约缺失）')),
                         '无（数量0）' if empty else contract_terms(contract),
                         '无（数量0）' if empty else raw,
                         '无（数量0）' if empty else 'N/A（result未保存该合约独立的报价日期、时间及来源字段；不借用IV参考合约）'])
    tables.append(('当前Put与当日维护候选合约证据' if monthly else '当前Put与下一目标Put合约证据', ['状态 / 袖', '规范化张数', '完整合约', '到期月 / 行权价', '当次result报价原文（最新/买卖价）', '独立行情日 / 快照 / 来源'], rows))
    reference = (f"ETF价格{value(s.get('etf_price'))}；报价日{s.get('etf_quote_date', 'N/A')}；时间{s.get('etf_quote_time', 'N/A')}" if product == 'IC'
                 else f"参考期货{s.get('put_reference_future', 'N/A')}；价格{value(s.get('put_reference_price'))}；取价日{s.get('put_reference_price_date', 'N/A')}；指数{value(s.get('index_price'))}")
    notes = [ratio_note, reference,
             f"独立生命周期核心Put：{value(s.get('v14_core_put_qty'))}张；普通核心保护当前{value(qty('current', 'core'))}张，两者是不同字段，不相加或互相回填。",
             '规范化小数张数用于研究模型；实际账户整数张数N/A（未提供账户规模与整数化信息），不表示可成交小数合约。',
             '报价证据缺项保留N/A；完整原始Put报价描述见result的put_market/put_pricing_evidence，未从stdout或其他合约补值。']
    tables.append(('数量与证据说明', ['说明'], [[note] for note in notes]))
    return tables


def markdown(product, signal):
    lines = []
    for title, headers, rows in evidence(product, signal):
        cell = lambda x: str(x).replace('|', '\\|').replace('\n', ' ')
        lines += [f'#### {title}', '', '| ' + ' | '.join(map(cell, headers)) + ' |', '| ' + ' | '.join('---' for _ in headers) + ' |']
        lines += ['| ' + ' | '.join(map(cell, row)) + ' |' for row in rows]
        lines.append('')
    return '\n'.join(lines)


def html_tables(product, signal):
    parts = []
    for title, headers, rows in evidence(product, signal):
        tr = lambda cells, tag: '<tr>' + ''.join(f'<{tag} style="padding:6px;border:1px solid #e4e7ec;vertical-align:top;">{html.escape(str(x))}</{tag}>' for x in cells) + '</tr>'
        parts.append(f'<h4>{html.escape(title)}</h4><table style="width:100%;border-collapse:collapse;font-size:12px;">{tr(headers, "th")}' + ''.join(tr(row, 'td') for row in rows) + '</table>')
    return ''.join(parts)

