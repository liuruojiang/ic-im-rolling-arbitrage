"""Finalize follow-up test evidence; production code is intentionally unchanged."""
import difflib
import hashlib
import json
import os
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN = HERE.parent
ROOT = RUN.parents[1]
PRIOR = ROOT / 'quant_research_runs/20261022_historical_calendar_repair_v1'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, data):
    if path.exists():
        assert read(path) == data, path
        return
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    assert not (HERE / 'final_verification.json').exists()
    baseline = read(RUN / 'baseline.json')
    receipt = read(RUN / 'accepted_01/receipt.json')
    assert receipt['exit_code'] == 0 and receipt['source_before_equals_after']
    assert receipt['counts'] == dict(tests=2746, failures=0, errors=0, skipped=0)
    bindings = read(RUN / 'accepted_01/source_after.json')
    assert all(sha(ROOT / n) == h for n, h in bindings.items())
    for name, expected in receipt['artifacts'].items():
        assert sha(RUN / 'accepted_01' / name) == expected
    xml = ET.parse(RUN / 'accepted_01/regression.xml').getroot()
    counts = {}
    for case in xml.iter('testcase'):
        name = case.get('classname', '').split('.')[0]
        counts[name] = counts.get(name, 0) + 1
    assert counts['test_icim_round5_recording_semantics_20261021'] == 19
    assert counts['test_icim_expert_followup_replay_20261022'] == 8
    assert (RUN / 'exclusions.json').read_bytes() == (PRIOR / 'exclusions.json').read_bytes()
    assert all(n == 'notes/mainline_registry.md' or sha(ROOT / n) == h
               for n, h in baseline['protected'].items())
    assert all(sha(RUN / 'backup' / n) == h for n, h in baseline['backups'].items())
    spec = ROOT / 'docs/ic_im_expert_followup_replay_20261022_v1_spec.md'
    assert sha(spec) == baseline['spec_sha256'] == spec.with_suffix('.md.sha256').read_text().split()[0]

    configured = os.environ.get('ICIM_EVIDENCE_DIR')
    state_root = Path(os.environ.get('ICIM_STATE_DIR', str(ROOT / 'runtime/ic_im_v1_4_r1')))
    local_root = Path(configured) if configured else state_root / 'input_evidence'
    manifests = sorted(local_root.rglob('manifest*.json')) if local_root.is_dir() else []
    # A local absence says nothing about a separately deployed server volume.
    availability = {'scope': 'current workspace only; remote server volumes not inspected',
                    'configured_evidence_root': configured, 'default_or_configured_root': str(local_root),
                    'root_exists': local_root.exists(), 'matching_local_manifests': len(manifests),
                    'real_original_day_acceptance': 'NOT_PROVEN',
                    'reason': 'No verified real original-session IC bundle was supplied to this acceptance; fixtures are synthetic.'}
    save(HERE / 'evidence_availability.json', availability)

    report = '''# 专家续审：回放地址与连续桥接核对

2026-10-22（Asia/Shanghai），本地完成代码核对和补充测试。本轮没有修改生产策略、存证、服务端或账本源码。专家的合约修正和单次 annotation 编码在第五轮验收中已存在；本轮补足显式地址集合及连续两日桥接断言。

## 逐项核对

| 专家意见 | 当前事实与本轮处理 |
| --- | --- |
| 11-23 IC 使用 2703，2701 尚未挂牌 | 既有第五轮用例已使用 11-23/2703，并有 11-23/2701 未挂牌拒绝；11-26/2701 正常场景保留。新增日历函数测试明确用 2703。 |
| annotation 不应重复 encode | 实际采集器直接 session.annotate(dict)，annotate 内部唯一一次 encode；读取端 decode 一次。既有测试覆盖 typed dict 往返，本轮还拦截实际独立采集器写入值，逐品种与读取后的 dict 完全相等。 |
| 保留 historical_replay 后会不会转去未录历史地址 | 公共 builder 仍保留历史标志。install() 中具名 provider 和 held_quote 适配器在原 provider 分支前读取已录输入；IC 持仓历史报价通过归档链中的独立证券身份恢复，既不请求主表也不查当前链。新增公共入口测试显式验证此路径和 URL 集合。 |
| 恐慌缺值选 A 后是否能够连续恢复 | 新增完整两日测试：10-21 缺值不写账；10-22 即便新行情完整也被逐日桥接拦住；首次补录因缺证据失败；取得 10-21 历史 Fear 补证后补录该日，再确认 10-22 桥接及临时真实 StateStore 推进至 sequence=2。 |
| 同月跨年重复 URL | 新增 2612/2712 共用 tstyle/510500_12 但报文不同的明确拒绝用例；原相同报文复用及不同 body/header/status/encoding 拒绝测试仍在全量范围。 |

源码核对位置：ic_im_evidence_adapter_v1.install/_keyed/_chain_close/capture_daily、ic_im_signal_evidence_v1.CaptureSession.annotate/EvidenceReader.http_response，以及 bot 的原日证据递归回放分支。并没有直接采纳早期“清空 historical_replay”的补丁，也不需要这样做来通过本轮测试。

## 地址验收的准确边界

本轮两个公共 IC 回放场景为 10-21 持有 2612 独立核心 Put、11-23 持有 2703 独立核心 Put。使用真实独立采集器、SSE 链解析器、存证/读取器及公共 builder；行情、HTTP 正文、时钟及 2027 日历仍是合成 fixture。每个 IC 原日包在 HTTP 边界录下 4 个非空、不同月份的 SSE 地址，公共回放必须调用原日持仓报价适配器并保持历史标志；逐次捕获 typed/HTTP EvidenceUnavailable，结果均为 0，reader.missing_inputs=[]。

公共回放的底层 HTTP 请求集合实际为空：它在更上层读取具名归档输入，因此地址集合是原日 4 个地址集合的子集。这不是只检查最后信号标记，也不是把未消费的 rate.resolve 强制称作已调用；测试还检查必需的实际消费角色、独立核心合约身份和原包所含的利率角色。负例强制请求未录的 CFFEX 历史 zip，立即抛 EvidenceUnavailable、计入 missing_inputs，并且不能回落联网。

**真实录制日验收尚未证明。** 现有和本轮未来日恢复测试都是合成行情；工作区当前配置/默认原日存证位置盘点见 delivery/evidence_availability.json，未检查远端部署卷。没有将旧 SSE 单份真实报文拼装成所谓完整真实 IC 原日包。专家要求的“真实完整录制日 + 独立核心 Put”仍须取得完整来源包后执行相同断言，不能因为合成测试通过就宣布该实证已闭合。

## 本轮执行与保护

- focused_01：8 项中 2 条新测试素材假设失败（2612 种子证券 ID 与原链不符、误要求未消费的利率角色）；XML、日志和执行时测试源码保留。测试包装器另因 Windows 默认输出编码解码失败，未生成有效 receipt；没有把它冒充验收成功。
- 修正本轮新测试素材，不改生产身份门槛；测试工具显式给子进程设置 PYTHONIOENCODING=utf-8。focused_02 为 8 项全部通过。
- accepted_01：92 测试文件、2746 项通过，0 失败/错误/跳过；含原第五轮全部 19 项及本轮 8 项。原 22 项逐项单列字节完全不变，没有增加排除。源码与测试工件绑定前后一致，收集节点、XML 和退出码核对通过。
- 本轮修改仅新增规范、测试、复验工具/报告及登记表条目；原生产源码、历史回测结果、前次 190 项交付工件和冻结文件均核对保持。未重新调参或计算新绩效，也未部署、发邮件或写正式账本。此前 IC 34.8145%、IM 29.9331% 的历史修复结果不因本轮而改变。
- 正式 runtime 旧保护清单的 155 个路径及其本轮启动时哈希均不变；路径全集校验发现一份本轮开始前已存在的 journal/000019-2026-10-21.json，创建于本地 14:32，早于 baseline 的 15:53。其完整文件 SHA 等于本轮启动时已绑定的 latest.json SHA，且 sequence/day/digest 与 latest 对应，因此不是本轮测试新写出的日志。当前合计 156 文件；没有删除或回滚。例外证据见 delivery/preexisting_runtime_addition.json，初次清单校验失败及未封存报告均保留；不称旧 155 路径清单覆盖了本轮开始时的全部路径。

## 部署与恢复条件

1. 11-20 维护的名义选约会指向 IC2703 / IM2702，但缺日历时系统只生成诊断，不能据此声称已经确认买入并持有这些目标。季度 IC2703 的实际调度同样需要核验日历。
2. 专家要求 2026-11-20 收盘前部署采集修复，是后续部署期限；本次只做本地验收，不能称已经满足部署条件。正式信号在日历缺口下仍可能停住，独立采集仅保留未来补录所需证据。
3. 2027 日历保持未覆盖。本轮没有编造休市安排、自动加入年份或创建新监控任务。取得并核验交易所正式安排后，须同时补年份、休市集合和 oracle，再逐日 historical_replay 恢复；官方日历是必要条件，原日链/身份证据、Fear 和开盘证据完整也是条件，不能保证只补年份就必然恢复。

有效回执 accepted_01/receipt.json；独立补充测试 test_icim_expert_followup_replay_20261022.py；精确增量 delivery/source_delta.patch；最终保护清单 delivery/final_verification.json。
'''
    record = RUN / 'record.md'
    assert not record.exists()
    record.write_text(report, encoding='utf-8')
    registry = ROOT / 'notes/mainline_registry.md'
    original = (RUN / 'backup/notes/mainline_registry.md').read_bytes()
    entry = '''
## 专家续审核对：回放地址与两日桥接（2026-10-22，本地）

- IC2703 / 2701未挂牌拒绝及 annotation 单次编码在第五轮已完成。本轮不改生产代码，新增 8 项验证真实采集器 annotation dict 往返、公共 IC 独立核心回放零未命中/地址子集、2612/2712 同 URL 歧义拒绝、恐慌缺值日补证后次日桥接和临时真实账本连续恢复。
- 全量 92 文件、2746 项通过，包含原第五轮 19 项及本轮 8 项，原 22 项单列字节不变。初次新测试素材失败及工具输出编码失败证据保留；有效回执 quant_research_runs/20261022_expert_followup_replay_audit_v1/accepted_01/receipt.json。
- 公共回放通过具名存证输入拦住历史 provider 分支，底层 HTTP 请求集合为空，原日模拟采集的 4 个地址非空；强制未录历史 URL 仍拒绝。测试行情/时钟/未来日历是合成，真实完整 IC 原日包实证尚未闭合，不与合成测试混称。
- 生产源码、前次历史日历重算及正式 runtime 不变，未部署/发日报/写账。2027 日历保持未覆盖；11-20 收盘前部署仍为后续期限。规范 docs/ic_im_expert_followup_replay_20261022_v1_spec.md，逐项核对、证据与局限见本轮 record.md 及 delivery/final_verification.json。

'''.encode('utf-8')
    first, rest = original.split(b'\n', 1)
    intended = first + b'\n' + entry + rest
    assert registry.read_bytes() in (original, intended)
    registry.write_bytes(intended)
    assert registry.read_bytes().replace(entry, b'', 1) == original
    changed = [n for n, h in baseline['protected'].items() if sha(ROOT / n) != h]
    assert changed == ['notes/mainline_registry.md'], changed
    expected_runtime = {n.replace('\\', '/'): h for n, h in baseline['protected'].items() if n.startswith('runtime/')}
    runtime = {p.relative_to(ROOT).as_posix(): sha(p) for p in (ROOT / 'runtime').rglob('*') if p.is_file()}
    assert all(runtime.get(n) == h for n, h in expected_runtime.items())
    extra_path = 'runtime/ic_im_v1_4_r1/journal/000019-2026-10-21.json'
    assert set(runtime) - set(expected_runtime) == {extra_path}
    extra = ROOT / extra_path
    latest_name = 'runtime/ic_im_v1_4_r1/latest.json'
    assert sha(extra) == baseline['protected'][latest_name]
    assert extra.stat().st_ctime < (RUN / 'baseline.json').stat().st_ctime
    journal, latest = read(extra), read(ROOT / latest_name)
    assert journal == latest and journal['sequence'] == 19 and journal['verified_day'] == '2026-10-21'
    save(HERE / 'preexisting_runtime_addition.json', {
        'path': extra_path, 'sha256': sha(extra), 'matches_start_bound_latest_sha256': baseline['protected'][latest_name],
        'file_created_at_local': datetime.fromtimestamp(extra.stat().st_ctime).isoformat(),
        'baseline_created_at_local': datetime.fromtimestamp((RUN / 'baseline.json').stat().st_ctime).isoformat(),
        'known_runtime_paths_unchanged': len(expected_runtime), 'current_runtime_files': len(runtime),
        'sequence': journal['sequence'], 'verified_day': journal['verified_day'], 'digest': journal['digest'],
        'interpretation': 'Predates this turn; bytes equal the already-bound latest at start. Writer not identified; preserved, not rolled back.'})

    new_files = [spec, spec.with_suffix('.md.sha256'), ROOT / 'test_icim_expert_followup_replay_20261022.py', record]
    new_files += [p for p in RUN.glob('*') if p.is_file() and p.suffix in {'.py', '.ini', '.json'} and p.name != 'baseline.json']
    new_files += [Path(__file__).resolve()]
    pieces = []
    def diff(before, after, name, new=False):
        for line in difflib.unified_diff(before, after, fromfile='/dev/null' if new else 'a/' + name, tofile='b/' + name):
            pieces.append(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n')
    diff(original.decode('utf-8-sig').splitlines(keepends=True), registry.read_bytes().decode('utf-8-sig').splitlines(keepends=True),
         'notes/mainline_registry.md')
    for path in sorted(set(new_files)):
        diff([], path.read_bytes().decode('utf-8-sig').splitlines(keepends=True), path.relative_to(ROOT).as_posix(), new=True)
    patch = HERE / 'source_delta.patch'
    assert not patch.exists()
    patch.write_text(''.join(pieces), encoding='utf-8', newline='\n')
    result = subprocess.run(['git', '-c', 'safe.directory=' + ROOT.as_posix(), 'apply', '--check', '--reverse',
                             '--whitespace=nowarn', str(patch)], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
    save(HERE / 'patch_check.json', {'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr,
                                    'sha256': sha(patch)})
    assert result.returncode == 0, result.stderr
    assert all(sha(ROOT / n) == h for n, h in bindings.items())
    artifacts = {p.relative_to(RUN).as_posix(): sha(p) for p in RUN.rglob('*')
                 if p.is_file() and '__pycache__' not in p.parts and '.pytest_cache' not in p.parts}
    checks = {'accepted_2746_tests': True, 'original_19_round5_tests_included': True,
              'eight_new_tests_included': True, 'original_22_exclusions_unchanged': True,
              'tested_sources_current': True, 'test_artifacts_bound': True, 'production_sources_unchanged': True,
              'prior_research_and_delivery_unchanged': True, 'registry_only_prior_file_changed': True,
              'known_runtime_paths_and_hashes_unchanged': True, 'preexisting_runtime_extra_validated': True,
              'all_five_backups_verified': True,
              'preregistration_verified': True, 'patch_reverse_check_passed': True}
    save(HERE / 'final_verification.json', {'status': 'PASS', 'completed_at': datetime.now(timezone.utc).isoformat(),
        'checks': checks, 'protected_objects': len(baseline['protected']), 'expected_changes': changed,
        'runtime_files': len(runtime), 'bound_sources': len(bindings), 'counts': receipt['counts'],
        'source_files_changed': [], 'new_tests': 8, 'round5_tests': 19,
        'real_original_day_acceptance': 'NOT_PROVEN', 'deployed': False, 'artifacts': artifacts})
    print(json.dumps({'status': 'PASS', 'counts': receipt['counts'], 'bound_sources': len(bindings),
                      'protected_objects': len(baseline['protected']), 'runtime_files': len(runtime),
                      'real_original_day_acceptance': 'NOT_PROVEN'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
