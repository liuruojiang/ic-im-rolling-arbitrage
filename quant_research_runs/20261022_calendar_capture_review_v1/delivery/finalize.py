"""Seal exact source provenance, official oracle and capture-isolation tests."""
import difflib
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
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
    assert not path.exists(), path
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    baseline = read(RUN / 'baseline.json')
    accepted = read(RUN / 'accepted_01/receipt.json')
    assert accepted['exit_code'] == 0 and accepted['source_before_equals_after']
    assert accepted['counts'] == dict(tests=2766, failures=0, errors=0, skipped=0)
    sources = read(RUN / 'accepted_01/source_after.json')
    assert all(sha(ROOT / n) == h for n, h in sources.items())
    for name, expected in accepted['artifacts'].items():
        assert sha(RUN / 'accepted_01' / name) == expected
    cases = list(ET.parse(RUN / 'accepted_01/regression.xml').getroot().iter('testcase'))
    assert sum(c.get('classname', '').startswith('test_icim_calendar_capture_review_20261022') for c in cases) == 20
    assert sum(c.get('classname', '').startswith('test_icim_round5_recording_semantics_20261021') for c in cases) == 19
    assert sum(c.get('classname', '').startswith('test_icim_expert_followup_replay_20261022') for c in cases) == 8
    selection = read(RUN / 'accepted_01/collection.json')
    assert len({node.split('::')[0] for node in selection['executed']}) == 93
    previous = ROOT / 'quant_research_runs/20261022_expert_followup_replay_audit_v1'
    assert (RUN / 'exclusions.json').read_bytes() == (previous / 'exclusions.json').read_bytes()
    expected = {'ic_im_evidence_adapter_v1.py'}
    changed_before_registry = {n for n, h in baseline['protected'].items() if sha(ROOT / n) != h}
    assert changed_before_registry == expected, changed_before_registry
    for name, value in baseline['backups'].items():
        assert sha(RUN / 'backup' / name) == value
    spec = ROOT / 'docs/ic_im_calendar_provenance_capture_20261022_v1_spec.md'
    assert sha(spec) == baseline['spec_sha256'] == spec.with_suffix('.md.sha256').read_text().split()[0]
    runtime = {p.relative_to(ROOT).as_posix(): sha(p) for p in (ROOT / 'runtime').rglob('*') if p.is_file()}
    assert runtime == baseline['runtime']

    calendar = ROOT / 'quant_research_runs/20261022_historical_calendar_repair_v1'
    bot = 'poe_ic_im_mainline_v1_4_bot.py'
    calendar_bound = read(calendar / 'accepted_02/source_after.json')[bot]
    previous_bound = read(previous / 'accepted_01/source_after.json')[bot]
    assert sha(ROOT / bot) == calendar_bound == previous_bound
    fixture = ROOT / 'tests/fixtures/icim_sse_2301_expiry_20261022.json'
    oracle = read(fixture)
    assert oracle['expiry_day'] == '2023-01-30'
    provenance = {'classification': 'Earlier separately preregistered and accepted production-behavior repair',
        'introduced_in': '20261022_historical_calendar_repair_v1',
        'preregistration': 'docs/ic_im_historical_calendar_repair_20261022_v1_spec.md',
        'accepted_receipt': 'quant_research_runs/20261022_historical_calendar_repair_v1/accepted_02/receipt.json',
        'accepted_tests': 2738, 'before_calendar_bot_sha256': read(calendar/'baseline.json')['backup'][bot],
        'after_calendar_bot_sha256': calendar_bound, 'last_followup_bot_sha256': previous_bound,
        'current_bot_sha256': sha(ROOT/bot), 'same_in_all_three_stages': True,
        'current_new_production_change': 'Independent collector marks conflicting raw HTTP inputs incomplete; does not alter calendar',
        'new_production_source': 'ic_im_evidence_adapter_v1.py',
        'new_production_sha256': sha(ROOT/'ic_im_evidence_adapter_v1.py'),
        'etf_oracle_fixture': str(fixture.relative_to(ROOT)), 'etf_oracle_sha256': sha(fixture),
        'official_etf_announcements': oracle['sources']}
    save(HERE / 'calendar_provenance.json', provenance)

    report = '''# 日历变更归属、ETF 到期 oracle 与存证隔离复核

2026-10-22（Asia/Shanghai），本地完成。本轮明确区分“打包版本累计改动”和“单轮新增改动”：历史日历扩展是生产行为变更，发生于此前单独验收的历史日历修复；本轮新增的生产改动则仅是独立采集的一致性/完整性分类。

## 1. 日历扩展的准确归属

2022—2025 年日历由 quant_research_runs/20261022_historical_calendar_repair_v1 单独预注册、备份、修复并以 accepted_02 的 2738 项测试验收。其 docs/ic_im_historical_calendar_repair_20261022_v1_spec.md、delivery/source_delta.patch、receipt 和完整账户重算均保留。

该轮修后的 bot SHA-256 为 0075c16aec68cb56b49569d03c1b89bb2707873fbd309971461082b623d0d19f。上一轮8项续审补测的验收记录与本轮当前 bot 都是同一哈希。因此，上一轮“未改生产逻辑”只表示相对历史日历修复后的起点无新增生产修改，不能概括整个累计交付版本。部署清单必须单列此前日历变更。本轮没有再次修改年份/休市集合。

历史影响已在那一轮单独重建：2402 到期顺延到 2024-02-19，修正 IC 漏维护，19 个目标日受影响；统一口径 IC 全期理论年化从35.3924%改为34.8145%，IM逐日收益不变、年化29.9331%。本轮未重新调参或重写这些结果。2026 全年交易会话和 DATA_CUTOFF 后 OHLCV 校验不因本轮改变，2027仍未加入覆盖。

## 2. 510500/2301 的独立官方 oracle

已有测试虽然断言 2023-01-30，但此前48个“已观测期货到期日”不能充当 ETF 期权 oracle。本轮另存 tests/fixtures/icim_sse_2301_expiry_20261022.json，期望日期直接来自以下已打开核对的上交所官网公告，不由日期函数产生：

- [上证公告(衍生品)〔2022〕73号，2022-12-09](https://www.sse.com.cn/assortment/options/disclo/update/c/c_20221209_5713793.shtml)：所有标的2301期权的最后交易日、行权日、到期日从1月25日调整为1月30日，交收日为1月31日。
- [上证公告（衍生品）〔2023〕5号，2023-01-18](https://www.sse.com.cn/assortment/options/disclo/update/c/c_20230117_5715310.shtml)：明确包括500ETF期权，最后交易日、行权日、到期日为1月30日。

新用例将510500/2301对应的 _fourth_wednesday(2023,1) 与该独立公告期望值比对。fixture 是带来源的规范化事实，不冒称原始HTTP正文；期货48个 oracle 与期权公告 oracle 分开标注。

## 3. 重复报文的真实调用链与本轮生产修复

专家所述“raw_response遇到不同重复报文就抛错”与当前代码不符：raw_response负责保存；歧义拒绝位于 EvidenceReader.http_response 的回放读取阶段。CaptureSession.call 对同一 role/key 复用已存结果，ic.chain 的键含完整年月和交易日。新增测试在同一会话中依次运行既有链、独立核心 Put 叠加、卖 Put 身份核验三个真实函数，确认只发生一次 SSE 链 HTTP 请求。

原日独立 capture_daily 在官方已覆盖的实际当日、交易日、收盘后才启动。收盘前和周末不会打开原日采集会话。正常 build_live_trade_signal 查询自身不打开独立采集会话；服务端独立采集返回结果后，才另行查询/补录。通用 capture_session 容器也供后日 Fear/开盘价补证使用，不能笼统声称所有补证会话都只在收盘后启用。

**不假定收盘后 HTTP 报文必然稳定。** 同链复用降低重复取数，但不能代替稳定性证明。修前反例发现：若独立采集确实取得同URL/参数的不同响应，包仍可能标成完整，直到读取原始 HTTP 时才拒绝。focused_before 的20项中5项失败，原始失败保留。

本轮唯一生产文件修改为 ic_im_evidence_adapter_v1.py：独立采集结束、写入完整性 annotation 之前，新增 _record_http_conflicts(session)，按 URL/参数分组，检查正文SHA、状态、编码及保留的响应头是否一致。不同则记录 raw_http_conflict、resolved=False，IC存证标为 incomplete，提供可重试/恢复风险提示；不因这个一致性问题向正常查询抛错。相同报文允许复用；回放仍拒绝歧义，不回退到当前数据。

body变化、time字段刷新、响应头、状态、编码变化五类均验证“存证不完整、正常IC信号仍可返回”；独立存盘异常也验证这一隔离。其他行情格式、身份、下载预算及日历校验没有放宽。不会把无效行情或缺官方日历冒充正式目标。

## 4. 真实完整IC包验收标准

接受专家标准：独立核心 Put、保留 historical_replay、包括被捕获尝试在内的 EvidenceUnavailable=0、回放请求地址集合为原日集合子集，且与原日落账信号逐字段相等，只有事先明示的时间戳例外。

另交付 replay_acceptance.py 作为严格字段比较工具。目前只例外根字段 signal_generated_at；价格、数量、动作、原因、来源、market_date、执行日、完整性及新增/缺失字段均检查，不能静默忽略差异。工具可读完整账本JSON中的 signals.IC，或单个IC信号JSON，并将差异列出、以非零退出码拒绝不相等：

```powershell
python -X utf8 -B quant_research_runs/20261022_calendar_capture_review_v1/replay_acceptance.py --original original_record.json --replayed replayed_ic.json
```

该工具只验证保存值相等，不能单独证明原包真实、独立核心持仓、HTTP地址及零未命中。真实包尚未取得/运行，不能宣布通过；若回放新增 original_evidence 元数据、来源说明或 generation_timing 等字段不同，严格比较会如实报告，不能先放宽成“都忽略”再宣布相等。上一轮合成回放仍只证明机制。

## 5. 验收与保护

- 新增20项：修前15通过/5失败；修后20通过。
- 累计 accepted_01：93文件、2766通过，0失败/错误/跳过；原第五轮19项、上一轮8项和本轮20项均包含。原22项单列字节保持，无扩大排除。
- 978项源码、规范、夹具、配置及研究输入绑定前后一致；收集节点、XML、退出码和日志核对通过。
- 修前5份备份和8912个既有保护对象核对；旧文件预期变化仅 adapter 与登记表。本轮开始时实际 runtime 全路径集合已单独保存，当前156文件（143JSON）路径及字节一致；没有沿用上一轮遗漏既有journal的155路径清单。
- bot日历、旧官方原始输入、冻结规范、旧测试/回执、已交付历史账户及绩效结果未覆盖。仅本地修改，未部署、发日报或写正式账本；没有新增多智能体或监控任务。

2027官方交易所日历、真实完整IC原日包和2026-11-20收盘前部署仍未闭合。使用方应在部署沟通中获知：11-20维护可能起即进入跨年日历诊断，持续时间取决于官方安排及证据完整性，不能承诺几天自动恢复。部署只能保住后续原日证据；补齐官方日历后仍须每个缺口交易日证据完整，并逐日 historical_replay，不能跳日或用当日链补造过去。

来源归属机器核对见 delivery/calendar_provenance.json；生产精确增量见 delivery/source_delta.patch；最终保护和验收绑定见 delivery/final_verification.json。有效累计回执仅 accepted_01/receipt.json。
'''
    record = RUN / 'record.md'
    assert not record.exists()
    record.write_text(report, encoding='utf-8')
    registry = ROOT / 'notes/mainline_registry.md'
    before = (RUN / 'backup/notes/mainline_registry.md').read_bytes()
    assert registry.read_bytes() == before
    entry = '''
## 日历归属、ETF到期与采集冲突隔离（2026-10-22，本地）

- 明确累计版本的2022—2025日历扩展属于此前 historical_calendar_repair_v1 生产修复（2738项验收）；上一轮8项补测及当前bot均为同一0075c16a…源码，不应将“补测轮无新增生产改动”解释为累计版本没有日历变更。
- 上交所2022年73号调整公告与2023年5号到期提醒独立确认510500/2301到期日2023-01-30，另存期权oracle；原48期货oracle不冒称ETF验证。
- 本轮唯一生产变更为ic_im_evidence_adapter_v1.py：独立采集结束前将同URL/参数不同原始响应标为不完整，可重试；正常查询独立，回放严格拒绝不变。不假定收盘后报文稳定；同会话既有链/核心叠加/卖方身份核验复用一次链HTTP已测试。
- 新增20项（修前5失败保留），累计93文件2766通过，原第五轮19项/上一轮8项包含，22项单列不变；978份绑定前后一致。8912既有保护对象及5备份核对，实际runtime156文件全路径/字节不变。未部署、发日报、续账或重写历史收益。
- 规范docs/ic_im_calendar_provenance_capture_20261022_v1_spec.md；证据、精确增量和未结项见quant_research_runs/20261022_calendar_capture_review_v1/record.md及delivery/final_verification.json。真实IC原日包、2027官方日历和11-20前部署继续待办，严格字段比较工具不能代替真实包验收。

'''.encode('utf-8')
    first, rest = before.split(b'\n', 1)
    registry.write_bytes(first + b'\n' + entry + rest)
    assert registry.read_bytes().replace(entry, b'', 1) == before
    changes = {n: {'before': h, 'after': sha(ROOT / n)} for n, h in baseline['protected'].items() if sha(ROOT / n) != h}
    assert set(changes) == {'ic_im_evidence_adapter_v1.py', 'notes/mainline_registry.md'}

    patch = []
    def add(before_lines, after_lines, name, new=False):
        for line in difflib.unified_diff(before_lines, after_lines, fromfile='/dev/null' if new else 'a/'+name, tofile='b/'+name):
            patch.append(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n')
    for name in sorted(changes):
        add((RUN/'backup'/name).read_bytes().decode('utf-8-sig').splitlines(keepends=True),
            (ROOT/name).read_bytes().decode('utf-8-sig').splitlines(keepends=True), name)
    new_paths = [record, spec, spec.with_suffix('.md.sha256'), fixture, ROOT/'test_icim_calendar_capture_review_20261022.py', Path(__file__).resolve()]
    new_paths += [p for p in RUN.glob('*') if p.is_file() and p.suffix in {'.py', '.ini', '.json'} and p.name != 'baseline.json']
    for p in sorted(set(new_paths)):
        add([], p.read_bytes().decode('utf-8-sig').splitlines(keepends=True), p.relative_to(ROOT).as_posix(), new=True)
    delta = HERE/'source_delta.patch'
    delta.write_text(''.join(patch), encoding='utf-8', newline='\n')
    result = subprocess.run(['git','-c','safe.directory='+ROOT.as_posix(),'apply','--check','--reverse','--whitespace=nowarn',str(delta)],
                            cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
    save(HERE/'patch_check.json', {'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr, 'sha256': sha(delta)})
    assert result.returncode == 0, result.stderr
    assert all(sha(ROOT/n) == h for n,h in sources.items())
    checks = {'accepted_2766_tests': True, 'old_19_and_8_tests_included': True, 'twenty_new_tests_included': True,
              'old_22_exclusions_unchanged': True, 'tested_sources_current': True, 'artifacts_verified': True,
              'calendar_change_provenance_proven': True, 'separate_official_etf_oracle': True,
              'only_adapter_and_registry_changed': True, 'all_backups_verified': True,
              'runtime_full_paths_and_hashes_unchanged': True, 'frozen_outputs_unchanged': True,
              'spec_and_sha_unchanged': True, 'source_patch_reverse_check_passed': True}
    artifacts = {p.relative_to(RUN).as_posix(): sha(p) for p in RUN.rglob('*') if p.is_file() and '__pycache__' not in p.parts and '.pytest_cache' not in p.parts}
    save(HERE/'final_verification.json', {'status':'PASS', 'completed_at':datetime.now(timezone.utc).isoformat(),
        'checks':checks, 'counts':accepted['counts'], 'bound_sources':len(sources), 'protected_objects':len(baseline['protected']),
        'expected_changes':changes, 'runtime_files':len(runtime), 'runtime_json':sum(n.endswith('.json') for n in runtime),
        'production_change':'independent capture completeness only; calendar predates this round',
        'real_original_ic_bundle_acceptance':'NOT_PROVEN', 'deployed':False, 'artifacts':artifacts})
    print(json.dumps({'status':'PASS', 'tests':accepted['counts']['tests'], 'protected':len(baseline['protected']),
                      'runtime':len(runtime), 'source_patch_exit':result.returncode},ensure_ascii=False))


if __name__ == '__main__':
    main()
