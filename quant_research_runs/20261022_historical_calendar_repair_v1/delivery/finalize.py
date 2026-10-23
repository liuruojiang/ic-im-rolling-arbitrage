"""Seal this local repair without modifying frozen studies or formal ledgers."""
from __future__ import annotations

import difflib
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN = HERE.parent
ROOT = RUN.parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    if (HERE / 'final_verification.json').exists():
        raise FileExistsError('final verification already sealed')
    baseline = read(RUN / 'baseline.json')
    accepted = read(RUN / 'accepted_02/receipt.json')
    analysis = read(HERE / 'analysis.json')
    rebuilt = read(RUN / 'rebuilt_02/receipt.json')
    assert accepted['exit_code'] == 0 and accepted['source_before_equals_after']
    assert accepted['counts'] == dict(tests=2738, failures=0, errors=0, skipped=0)
    assert rebuilt['status'] == 'PASS' and rebuilt['anchors_unchanged']
    assert rebuilt['network_attempts'] == []
    assert analysis['original_unchanged_entry_control']['status'] == 'PASS'
    assert analysis['results']['IM']['return_changed_days'] == 0
    # This manifest covers the exact tested revision, not just a reported count.
    bindings = read(RUN / 'accepted_02/source_after.json')
    assert len(bindings) == 973
    assert all(sha(ROOT / n) == h for n, h in bindings.items())
    for name, expected in accepted['artifacts'].items():
        assert sha(RUN / 'accepted_02' / name) == expected, name
    for name, expected in rebuilt['loaded_sources'].items():
        assert sha(ROOT / name) == expected, name

    registry = ROOT / 'notes/mainline_registry.md'
    original_registry = (RUN / 'backup/notes/mainline_registry.md').read_bytes()
    entry = '''
## 历史日历修复与完整账户重算（2026-10-22，本地）

- 补齐并交叉核验 2022—2025 SSE/CFFEX 日历：四年实际交易日为 242/242/242/243，48 个实际期货到期日 oracle；原 2026 日历不变，2027 仍未核验、严格拒绝正式执行。
- 原 10-16 历史入口恢复运行，对照 18 文件、73884 行数值零差异；另用当前规则从风险输入、选约、数量、卖方生命周期到完整理论账户重新计算，并非只复用旧目标。
- 新重算补回旧 IC 回测漏掉的 2024-02-19 春节顺延维护，19 个目标日受影响。统一 252/N 年化口径下，IC 全期 CAGR 35.3924%→34.8145%，最大回撤均约 -9.0013%；IM 逐日收益完全一致，CAGR 29.9331%。风险公式、策略参数和卖方实际财务路径不变，不恢复新卖 Call。
- 本轮累计 91 文件、2738 项通过，原 22 项单列保持；973 项源文件/输入/回执绑定前后一致。旧跨年缺口用例显式限制模拟覆盖年份，保留原断言并备份原文件；新增真实已覆盖跨年用例。accepted_01 的旧用例失败及 rebuilt_01 的混合生效口径记录保留，后者不用于最终比较。
- 旧冻结输出、原始行情和正式 runtime 保持不变；独立新版本预注册及结果位于 docs/ic_im_historical_calendar_repair_20261022_v1_spec.md 和 quant_research_runs/20261022_historical_calendar_repair_v1/record.md。修复前备份、独立账户核算、补丁、测试回执和最终保护清单均存档。只修本地，未部署、未发日报、未续写正式账本；历史身份重建、后下载 Fear 和既有代理成交限制仍保留。

'''.encode('utf-8')
    first, rest = original_registry.split(b'\n', 1)
    intended_registry = first + b'\n' + entry + rest
    if registry.read_bytes() != intended_registry:
        assert registry.read_bytes() == original_registry
        registry.write_bytes(intended_registry)
    assert registry.read_bytes().replace(entry, b'', 1) == original_registry

    rows = []
    for w in analysis['windows']:
        rows.append(f"| {w['product']} | {w['window']} | {100*w['before_cagr']:.6f}% | {100*w['after_cagr']:.6f}% | {w['cagr_change_pp']:+.6f} | {100*w['before_max_dd']:.6f}% | {100*w['after_max_dd']:.6f}% |")
    report = '''# 历史日历修复与完整账户重算 v1

2026-10-22，本地完成。历史回测入口已恢复；补齐真实历史日历后，IC 的理论回测收益需要下调，IM 逐日收益不变。原冻结研究结果保留，新的实算结果单独存档，不回写旧记录或正式账本。

## 修复与日期依据

生产源码只修改 poe_ic_im_mainline_v1_4_bot.py 的已核验年份集合和 2022—2025 年休市日期，覆盖扩展为 2022—2026。没有改参数、风险公式、数量算法、策略生效日期、schema 或执行/持久化门槛；2027 及其他未覆盖年份仍严格拒绝正式执行。2026 全年会话逐日与原版本一致。

SSE 依据官网年度休市通知原文；CFFEX 依据其原通知的保留转载，并与冻结官方历史行情中的全年度交易日期及 48 个期货合约最后交易日交叉验证。CFFEX 官网通知请求未成功，未声称取得其官网原始 HTTP 报文。calendar_notices.json 是带来源、文号和发布日期的事实摘录，不是原始报文。四年会话数分别为 242、242、242、243；全部日期和 48 个到期日通过独立 oracle。证据见 tests/fixtures/icim_historical_calendar_20261022/，原始 IC 月归档 48 份和既有历史输入 55 份均核对 SHA-256。

新研究入口 history/rebuild_history.py 同时补上旧研究适配器的持仓身份传递：由开仓时选定的 security_id 建立持仓身份，缩放张数时校验历史合约及该 ID，不从当前报价反向认领身份。调用原正式身份门槛前在 RLock 内暂投影历史持仓，finally 恢复原锚点。21 次历史 IC 缩放检查全部通过，异常、错配和并发反例另有测试。

## 实算结果

对照为 2026-10-16 已修复普通 Put/卖方执行成本的 both 账户。两边使用同一日收益、同一 252/N 年化、初始 NAV=1 的回撤口径；full 样本 IC 为 2022-09-19—2026-08-13（945 日），IM 为 2022-07-22—2026-08-14（986 日）。3Y/1Y 是各品种样本终点回溯的日历窗口。

| 品种 | 窗口 | 修前 CAGR | 修后 CAGR | 变化（百分点） | 修前最大回撤 | 修后最大回撤 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
''' + '\n'.join(rows) + '''

账户引擎原生 account.cagr 使用另一计数口径（N-1），其摘要数值会略有差别；本报告、receipt.phases.*_account.metrics 及 delivery/analysis.json 均统一使用旧对照入口的 252/N 口径，不混合比较。全期 IC 最大回撤不变；3Y 回撤存在约 -0.00000787 个百分点的微小变化，不能据四舍五入称所有窗口完全不变。

差异集中在 IC 买 Put：旧研究路径将 2024 年 2 月月到期参照落在休市中的 2 月 16 日，漏掉实际顺延到 2 月 19 日的维护。修复后包含 2 月 8 日维护预览在内的 19 个目标日发生合约/张数变化，直至 3 月 13 日；实际收益从 2 月 19 日起产生差异，受持仓及后续状态影响共有 198 个日收益变化。逐字段差异见 delivery/target_differences.csv，逐日账户见 rebuilt_02/daily_paths.csv.gz。期末 IC NAV 从 3.115146768 调整为 3.065580541，全期 CAGR 下降约 0.577857 个百分点。

估值、动量、网格和保护 Delta 风险输入均与旧对照一致（浮点误差小于 1e-12）。卖方实际动作、持仓、现金成交成本、到期结算及恢复路径一致；IC 有 34 条候选解释、22 条未改变准入结果的候选 IV 元数据差异，已保留在 delivery/lifecycle_differences.csv。IM 原目标表中的旧 Call 元数据按现行规则清除，而旧账户已经禁用这些 Call，故其 986 个日收益逐值完全一致；没有恢复新卖 Call。

## 验证路径与保留记录

1. calendar_before：修前 72 项中 15 失败，缺失年份阻断证据保留。补齐后该组 72 项通过；最终新增已覆盖跨年边界后为 73 项。
2. original_entry_runs/execution_history_after：原 10-16 入口不改代码恢复运行，128.89 秒，对照 18 文件、73884 行数值零差异。这是复用原目标且沿用该入口钉住旧 policy 的对照，不能代表重新选约后的收益无变化。
3. rebuilt_02：70.20 秒完成当前风险函数、945/986 日目标、1929 条卖方候选、完整生命周期及含费用/保证金的账户重建；零 HTTP 尝试、无缺证据目标、无 HOLD_FAIL_CLOSED 生命周期，运行后锚点恢复。重新计算日收益累乘 NAV、费用事件汇总和现金扣保证金非负，均通过独立保存文件核算。
4. 为对齐旧研究“现行规则用于全历史”的反事实口径，仅研究进程内将 IC 动量及卖方 MOM120 生效日期置为 1900，并最终恢复；生产源码生效日期未改。rebuilt_01 遗漏卖方生效日期对齐，其算术完成记录保留，但 interpretation.json 明确 comparison_eligible=false，不能作为最终收益结论；原执行源码另存 history/rebuild_history_stage01.py.txt。
5. accepted_01：2736 通过、1 失败，保留原失败。失败是旧测试假定 2025 永远未覆盖；原文件备份后，只显式模拟单年覆盖，原跨年拒绝断言不变，并新增真实覆盖年份双向跨年测试。
6. accepted_02：91 文件、2738 通过，0 失败/错误/跳过，原 22 项单列不扩展。973 项源码/配置/研究输入和回执绑定前后一致；XML、退出码、收集节点及日志可追溯。本轮未新启多智能体，累计范围含此前对抗性用例。

有效测试回执为 accepted_02/receipt.json；财务比较以 rebuilt_02 和 delivery/analysis.json 为准。所有失败和比较无效阶段原样保留，不覆盖重跑。

## 保护与复跑

补前备份七个原文件，另备份一份旧测试文件。原冻结规格/SHA、首次研究输出、既有五轮审计文件、全部原行情输入及正式 runtime 保护清单均核对；预期修改仅 bot、旧测试的缺口场景构造、登记表，以及本轮新增规范、oracle、测试和研究文件。正式 runtime 的路径集合和文件哈希均不变，155 文件（142 JSON）。最终检查见 delivery/final_verification.json，精确增量见 delivery/source_delta.patch。

可在项目目录以新的 stage 名离线重跑（已存在目录会拒绝覆盖）：

```powershell
python -X utf8 -B quant_research_runs/20261022_historical_calendar_repair_v1/history/rebuild_history.py --stage rebuilt_review_03
```

研究仍属于真实挂牌日线上的理论账户：保留 30% 保证金/缓冲口径、既定交易费用和旧账户执行模型；15% 卖方 NAV 可行性是继承假设，不是券商验证。历史 IC 合约身份有重建、Fear 使用后下载数据，普通买 Put 仍继承代理定价/开盘处理，不能升级为点时或真实成交认证。没有据此调整参数，也没有部署、发送日报或续写正式账本。
'''
    record = RUN / 'record.md'
    if record.exists():
        assert record.read_text(encoding='utf-8') == report
    else:
        record.write_text(report, encoding='utf-8')

    expected_changes = {'poe_ic_im_mainline_v1_4_bot.py',
                        'test_icim_batch3_calendar_redteam_20261021.py', 'notes/mainline_registry.md'}
    changes = {n.replace('\\', '/'): {'before': h, 'after': sha(ROOT / n)}
               for n, h in baseline['protected'].items() if sha(ROOT / n) != h}
    assert set(changes) == expected_changes, changes
    prior_changes = [n for n, h in baseline['prior_bindings'].items() if sha(ROOT / n) != h]
    assert set(prior_changes) == expected_changes - {'notes/mainline_registry.md'}
    for name, expected in baseline['backup'].items():
        assert sha(RUN / 'backup' / name) == expected
    extra = read(HERE / 'additional_backups.json')
    for info in extra.values():
        assert sha(ROOT / info['backup']) == info['sha256']
    assert all(sha(ROOT / n) == h for n, h in baseline['raw_inputs'].items())
    fixture_dir = ROOT / 'tests/fixtures/icim_historical_calendar_20261022'
    assert all(sha(fixture_dir / n) == h for n, h in baseline['calendar_fixture_sha256'].items())
    oracle = read(fixture_dir / 'cffex_sessions_and_expiries.json')
    assert all(sha(ROOT / n) == h for n, h in oracle['official_monthly_archive_sha256'].items())
    spec = ROOT / 'docs/ic_im_historical_calendar_repair_20261022_v1_spec.md'
    assert sha(spec) == baseline['spec_sha256'] == spec.with_suffix('.md.sha256').read_text().split()[0]
    runtime_expected = {n.replace('\\', '/'): h for n, h in baseline['protected'].items() if n.replace('\\', '/').startswith('runtime/')}
    runtime_now = {p.relative_to(ROOT).as_posix(): sha(p) for p in (ROOT / 'runtime').rglob('*') if p.is_file()}
    assert runtime_now == runtime_expected
    assert len(runtime_now) == 155
    assert (RUN / 'exclusions.json').read_bytes() == (ROOT / 'quant_research_runs/20261021_round5_recording_semantics_v1/exclusions.json').read_bytes()
    rejected = read(RUN / 'rebuilt_01/receipt.json')
    original_script_key = str((RUN / 'history/rebuild_history.py').relative_to(ROOT))
    assert sha(RUN / 'history/rebuild_history_stage01.py.txt') == rejected['loaded_sources'][original_script_key]

    # Generate a reviewable delta from this repair's backups, not from an old git HEAD.
    patch = []
    def add_diff(before, after, old_name, new_name):
        for line in difflib.unified_diff(before, after, fromfile=old_name, tofile=new_name):
            patch.append(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n')
    for name in sorted(expected_changes):
        before = (RUN / 'backup' / name).read_bytes().decode('utf-8-sig').splitlines(keepends=True)
        after = (ROOT / name).read_bytes().decode('utf-8-sig').splitlines(keepends=True)
        add_diff(before, after, 'a/' + name, 'b/' + name)
    new_files = [ROOT / 'test_icim_historical_calendar_20261022.py',
                 ROOT / 'test_icim_historical_replay_adapter_20261022.py', spec, spec.with_suffix('.md.sha256'), record]
    new_files += sorted(fixture_dir.glob('*.json'))
    new_files += sorted(RUN.glob('*.py'))
    new_files += sorted(RUN.glob('*args.json'))
    new_files += [RUN / 'pytest.ini', RUN / 'exclusions.json']
    new_files += sorted((RUN / 'history').glob('*.py'))
    new_files += sorted(HERE.glob('*.py'))
    for path in sorted(set(new_files)):
        name = path.relative_to(ROOT).as_posix()
        after = path.read_bytes().decode('utf-8-sig').splitlines(keepends=True)
        add_diff([], after, '/dev/null', 'b/' + name)
    delta = HERE / 'source_delta.patch'
    if delta.exists():
        previous = read(HERE / 'patch_check.json')
        assert previous['exit_code'] != 0
        attempt = 1
        while (HERE / f'source_delta_failed_{attempt:02d}.patch').exists():
            attempt += 1
        delta.rename(HERE / f'source_delta_failed_{attempt:02d}.patch')
        (HERE / 'patch_check.json').rename(HERE / f'patch_check_failed_{attempt:02d}.json')
    assert not delta.exists()
    delta.write_text(''.join(patch), encoding='utf-8', newline='\n')
    checked = subprocess.run(['git', '-c', 'safe.directory=' + ROOT.as_posix(), 'apply', '--check', '--reverse',
                              '--whitespace=nowarn', str(delta)], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
    save(HERE / 'patch_check.json', {'exit_code': checked.returncode, 'stdout': checked.stdout, 'stderr': checked.stderr,
                                    'sha256': sha(delta), 'files': len(expected_changes) + len(set(new_files))})
    assert checked.returncode == 0, checked.stderr
    assert all(sha(ROOT / n) == h for n, h in bindings.items())
    checks = dict(bound_test_sources_current=True, test_artifacts_verified=True,
                  rebuilt_loaded_sources_current=True, protected_changes_allowlist_only=True,
                  all_eight_backups_verified=True, original_55_raw_inputs_unchanged=True,
                  original_48_ic_archives_unchanged=True, calendar_oracles_unchanged=True,
                  preregistration_and_sha_unchanged=True, runtime_paths_and_bytes_unchanged=True,
                  original_22_exclusions_unchanged=True, rejected_run_source_archived=True,
                  registry_old_tail_preserved=True, patch_reverse_check_passed=True,
                  independent_account_checks_passed=True, no_deploy_or_ledger_write=True)
    artifacts = {}
    for path in RUN.rglob('*'):
        if path.is_file() and not any(x in path.parts for x in ('__pycache__', '.pytest_cache')):
            # Disposable pytest state is not part of the deliverable.
            relative = path.relative_to(RUN)
            if any(x.startswith('pytest') or x in {'temporary_state', 'isolated_state'} for x in relative.parts[:-1]):
                continue
            artifacts[relative.as_posix()] = sha(path)
    final = dict(status='PASS', completed_at=datetime.now(timezone.utc).isoformat(), checks=checks,
                 protected_objects=len(baseline['protected']), expected_changes=changes,
                 unchanged_protected_objects=len(baseline['protected']) - len(changes),
                 runtime_files=len(runtime_now), runtime_json_files=sum(n.endswith('.json') for n in runtime_now),
                 accepted_stage='accepted_02', accepted_tests=accepted['counts'], bound_sources=len(bindings),
                 accepted_financial_stage='rebuilt_02', rejected_comparison_stage='rebuilt_01',
                 bot_sha256=sha(ROOT / 'poe_ic_im_mainline_v1_4_bot.py'),
                 financial_results={p: {k: analysis['results'][p][k] for k in ('before', 'after', 'return_changed_days')}
                                    for p in ('IC', 'IM')}, artifacts=artifacts)
    save(HERE / 'final_verification.json', final)
    print(json.dumps({k: v for k, v in final.items() if k not in ('artifacts', 'financial_results')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
