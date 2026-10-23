"""Create a byte-bound release inventory; no production source or ledger writes."""
import ast
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
MANIFEST = 'docs/ic_im_daily_release_20261023_repairs_v3_r3_manifest.json'
SPEC = 'docs/ic_im_daily_release_v3_r3_20261023_spec.md'
RUNBOOK = 'docs/ic_im_v1_4_github_gmail_runbook.md'
BASE = '58986fda2c17df93eb3925bf09a1cebd55a7869e'

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def create(path, content):
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('x', encoding='utf-8', newline='\n') as out:
        out.write(content)

def identities(source):
    result = {}
    for n in ast.parse(source).body:
        if isinstance(n, ast.Assign):
            for target in n.targets:
                if isinstance(target, ast.Name) and target.id in {'BUILD_ID', 'RULE_REVISION'}:
                    result[target.id] = ast.literal_eval(n.value)
    return result

def main():
    manifest = json.loads((ROOT / MANIFEST).read_text(encoding='utf-8'))
    import sys
    sys.path.insert(0, str(ROOT))
    import run_ic_im_v1_4_delivery_tests as gate
    bindings = gate.bindings(manifest)
    assert len(bindings) == 175
    assert len(manifest['files']) == 29 and len(manifest['required_local_tests']) == 47
    assert all(bindings[k] == v for k, v in manifest['files'].items())
    assert all(bindings[k] == v for k, v in manifest['fixture_sha256'].items())
    assert all(not k.startswith(('runtime/', 'outputs/')) for k in bindings)
    old = subprocess.run(['git', 'show', BASE + ':ic_im_v1_4_policy.py'], cwd=ROOT,
                         check=True, capture_output=True).stdout.decode('utf-8')
    before = identities(old)
    current = identities((ROOT / 'ic_im_v1_4_policy.py').read_text(encoding='utf-8'))
    assert before == current
    consumers = [p.name for p in ROOT.glob('*.py') if 'build_identity_unchanged' in p.read_text(encoding='utf-8-sig')]
    assert not consumers
    identity = {'baseline_commit': BASE, 'baseline_identity': before, 'current_identity': current,
                'actual_build_and_rule_identity_unchanged': True,
                'frozen_manifest_descriptive_flag': manifest['build_identity_unchanged'],
                'root_python_consumers_of_flag': consumers,
                'interpretation': 'The frozen descriptive flag is inaccurate for BUILD/RULE identity. Preserve it; source constants and immutable byte pins are authoritative. Changed implementation bytes do not imply changed strategy identity.'}
    create(str((OUT / 'identity_verification.json').relative_to(ROOT)), json.dumps(identity, ensure_ascii=False, indent=2) + '\n')

    spec = '''# IC/IM v3-r3 research delivery release — 2026-10-23

Status: preregistered release and verification scope, not a claim of completed publication.

The user authorized publishing the existing audited v3-r3 repair, synchronizing the local and remote daily reports, and independent adversarial checks after publication. Release only the 29 execution files and complete 47-test gate closure fixed by `docs/ic_im_daily_release_20261023_repairs_v3_r3_manifest.json`. Its 175 bound files include 13 helper modules and necessary explicitly declared fixtures. Do not substitute the collector pin or omit fixture files merely because their paths are under quant_research_runs.

No strategy parameters, production runner logic, current manifest bytes, frozen specifications, historical outputs, ledger records, producer identity or historical signal identities are changed by this release preparation. IC contains no Call. The 30% performance convention and research-only authorization remain. Formal ledgers are restored only through the existing verified artifact/hash-chain workflow; no production ledger is packaged or overwritten by publishing code. Existing unresolved provenance findings are preserved and require separate acceptance; old replay tests do not authenticate different formal signals.

BUILD_ID remains `v1.4-20261008-r1-ic-csi500-abs40-fix11`; RULE_REVISION remains `ic_im_v1_4_ic_csi500_ma105_w16_abs40_static_20261008_v1`. The frozen manifest's `build_identity_unchanged=false` is inaccurate descriptive metadata for those constants, not a consumed execution switch. Do not rewrite it. `strategy_prepare/identity_verification.json` compares the actual constants to baseline 58986fda. Updated execution bytes are identified by the 29-file SHA map and the eventual immutable remote strategy commit, not by interpreting that flag or the unchanged fix11 label.

Local scheduled automation has separately authorized 180-second subprocess limits for both local test families as of 2026-10-23 14:51 Asia/Shanghai. If needed, adapt only a disposable test checkout's time limit and record its exact diff; preserve the source gate, source manifest, complete 47-file suite, evidence validation and original exclusions. This local limit does not authorize changing cloud CI: its current 60-second gate remains unchanged. The local schedule remains ACTIVE at its existing weekday 14:30 schedule; microcap logic is outside this strategy release scope.

Release inventory binds all 175 gate files plus this new specification/hash, existing repair specifications/hashes, updated runbook and compact evidence/identity summaries. Exclude formal runtime, historical outputs, test scratch directories, logs, caches, credentials and large unrelated research trees. Necessary fixture bytes are explicit inventory members, not an invitation to publish their parent trees.

Before acceptance: verify inventory bytes in an independent remote checkout, run the complete gate without new exclusions, preserve XML/executed-test/source-hash receipts, inspect cloud pin and packaged dependencies, run authorized no-send readiness with restored verified state, and independently adversarially test post-release behavior. Preserve failed runs. Actual email delivery must be demonstrated separately by the scheduled run and recipient evidence; no ad hoc resend or message is implied by the code publication scope.

Publishing code does not certify current market availability, resolve historical provenance differences, or grant trading/account execution authority. The parent release record supplies final commit/run identities and current acceptance status.
'''
    create(SPEC, spec)
    create(SPEC + '.sha256', sha(ROOT / SPEC) + '  ' + Path(SPEC).name + '\n')

    backup = OUT / 'backup' / Path(RUNBOOK).name
    backup.parent.mkdir(exist_ok=True)
    with backup.open('xb') as out:
        out.write((ROOT / RUNBOOK).read_bytes())
    raw = (ROOT / RUNBOOK).read_text(encoding='utf-8')
    title, rest = raw.split('\n', 1)
    intro = '''

## 2026-10-23 v3-r3 发布与 47 文件门禁覆盖（发布进度另以当次回执为准）

本节覆盖下方历史 38/43 文件清单及旧策略 pin。当前待发布修补固定使用 `docs/ic_im_daily_release_20261023_repairs_v3_r3_manifest.json`：**29 个执行文件、47 个测试文件、13 个测试辅助文件，门禁共绑定 175 个文件**。必须按清单保留必要 fixture 和证据 worker；不得以采集器 pin 代替日报执行清单。策略 BUILD_ID/RULE_REVISION 仍是 fix11，补丁身份由执行字节 SHA 与最终工作流固定的策略提交确定；不能把未完成的远端推送称作已发布。

冻结 manifest 的 `build_identity_unchanged=false` 是与实际 BUILD/RULE 常量不符的描述性元数据，根 Python 运行和门禁代码均不读取该字段；保留原字节，以 `quant_research_runs/20261023_daily_release_v3/strategy_prepare/identity_verification.json` 的源码对照说明为准，不据此更改策略身份。

本地 `automation.toml` 已在 2026-10-23 14:51（北京时间）得到授权：本地两族专项子进程各 **180 秒**；IC/IM 可在完整隔离副本适配超时，并保存差异和最终执行回执，不能改写这里冻结的源 manifest 或正式 runner。正式根门禁与云端 CI 当前仍为 **60 秒**，本次不擅自扩大云端限制。ACTIVE、原工作日 14:30、模型/项目/cwd 与微盘逻辑保持各自已授权配置。

标准门禁入口仍为 `python -B -X utf8 run_ic_im_v1_4_delivery_tests.py --out-dir <本次全新隔离目录>`；只允许原 22 项登记表与本 47 文件的 5 项交集单列，必须核对 XML、实际执行身份、进程状态、源码/fixture 前后 SHA。历史 3333/1193/148 或接手定向 131/192 均是对应审计证据，不能冒充每天执行计数，各组有重叠不得求和。最新 47 文件门禁以本次运行结果为准。

发布规范：`docs/ic_im_daily_release_v3_r3_20261023_spec.md`。发布 inventory 只含门禁必需内容及紧凑规范/摘要，不携带正式 runtime、历史 outputs 或整棵临时研究目录。既有 formal seq20 的来源差异与采集失败记录继续保留，由本次发布总记录另行验收；代码同步本身不能将其改写为通过。远端 20:00、正常恢复/去重流程、本地不另起 SMTP 和研究用途边界不变。
'''
    (ROOT / RUNBOOK).write_text(title + intro + '\n' + rest, encoding='utf-8', newline='\n')

    summary = {'release':manifest['release'], 'status':'PREPARED_NOT_YET_PUBLISHED',
               'baseline_commit':BASE, 'gate_binding_count':len(bindings), 'execution_files':29,
               'test_files':47,'helper_files':13,'historical_deselections':22,'gate_deselections':5,
               'prior_evidence':{'repair_cumulative_passed':3333,'repair_independent_passed':148,
                                 'takeover_daily_passed':1193,'takeover_scoped_groups_passed':[131,192],
                                 'counts_overlap':True,'not_new_release_acceptance':True},
               'prior_takeover_status':'BLOCKED_PROTECTED_DATA_CHANGED_AND_DELIVERY_INCOMPLETE',
               'open_items':'Formal seq20 provenance differs from saved replay, collector transport incomplete and no remote release existed at takeover; parent release owns resolution and current receipts.',
               'local_test_timeout_seconds_authorized':180,'cloud_test_timeout_seconds_unchanged':60,
               'runbook_backup_sha256':sha(backup),'identity':identity}
    create(str((OUT / 'evidence_summary.json').relative_to(ROOT)),json.dumps(summary,ensure_ascii=False,indent=2)+'\n')

    extras = [SPEC,SPEC+'.sha256',RUNBOOK,
              str((OUT/'identity_verification.json').relative_to(ROOT).as_posix()),
              str((OUT/'evidence_summary.json').relative_to(ROOT).as_posix())]
    for p in sorted((ROOT/'docs').glob('ic_im_redteam_repairs_20261023_v3*spec.md*')):
        extras.append(p.relative_to(ROOT).as_posix())
    items = []
    for name in sorted(set(bindings)|set(extras)):
        p = ROOT/name
        if name in manifest['files']: kind='execution'
        elif name in manifest['required_local_tests']: kind='test'
        elif name in manifest['required_test_helpers']: kind='test_helper'
        elif name in manifest['fixture_sha256']: kind='fixture'
        elif name in bindings: kind='gate_infrastructure'
        else: kind='release_documentation'
        items.append({'path':name,'sha256':sha(p),'bytes':p.stat().st_size,'category':kind,'gate_bound':name in bindings})
    inventory={'schema':'icim_strategy_release_inventory_v1','release':manifest['release'],
               'gate_manifest':MANIFEST,'gate_manifest_sha256':sha(ROOT/MANIFEST),
               'gate_binding_count':175,'total_files':len(items),'total_bytes':sum(x['bytes'] for x in items),
               'files':items,'excludes':['runtime/**','outputs/**','all undeclared files and parent research trees'],
               'assembly_rule':'Overlay exactly these relative paths into the strategy release checkout; do not mirror parent directories. Preserve required existing deployment infrastructure. Parent adds notes and final release provenance separately.'}
    create(str((OUT/'release_inventory.json').relative_to(ROOT)), json.dumps(inventory,ensure_ascii=False,indent=2)+'\n')
    create(str((OUT/'gate_bindings.json').relative_to(ROOT)),json.dumps(bindings,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'files':len(items),'gate_files':175,'total_bytes':inventory['total_bytes'],'identity_unchanged':before==current}))

if __name__ == '__main__':
    main()
