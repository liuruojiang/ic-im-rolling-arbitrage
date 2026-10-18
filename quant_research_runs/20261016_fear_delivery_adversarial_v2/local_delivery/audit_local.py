"""Read-only production audit and reviewable configuration proposal."""
from pathlib import Path
import hashlib
import json
import os
import re
import sys
import tomllib
from datetime import datetime

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
os.environ.update(ICIM_STATE_DIR=str(HERE / 'isolated_state'), ICIM_REQUIRE_MIGRATION='0', PYTHONDONTWRITEBYTECODE='1')
sha = lambda b: hashlib.sha256(b).hexdigest()
cfgpath = Path('D:/Codex/home/automations/ic-im/automation.toml')
raw = cfgpath.read_bytes()
cfg = tomllib.loads(raw.decode('utf-8'))
secret_lines = [l for l in raw.decode('utf-8').splitlines() if re.match(r'\s*(?:token|password|secret|api_key)\s*=', l, re.I)]
if secret_lines:
    raise RuntimeError('Unexpected credential setting; do not save raw configuration')
(HERE/'automation.readonly.toml').write_bytes(raw)
prompt=cfg['prompt']
manifest=json.loads((ROOT/'docs/ic_im_v1_4_script_delivery_sync_20261013_source_manifest.json').read_text('utf-8'))
required=list(dict.fromkeys(manifest['required_local_tests']+[
    'test_ic_im_put_execution_repairs_20261016.py',
    'test_ic_im_option_maintenance_notice_20261016.py',
    'test_ic_im_put_delivery_completion_20261016.py',
    'test_ic_im_fear_source_delivery_20261016.py']))
append='''2026-10-16 用户授权的Put执行与日报交付闭环及恐慌来源最新覆盖（明确覆盖此前IC/IM旧pin、33文件清单及“来源冲突继续关闭”等冲突条款；微盘全部规则不改）：先读 docs/ic_im_put_delivery_completion_20261016.md、docs/ic_im_fear_single_source_delivery_20261016_v1_spec.md 及登记表最新节。使用当次自动化远端main生产workflow固定策略SHA，完整checkout并逐文件核验；本轮已发布策略SHA be4173440daace20ee698103d05bc75838316a30 是审计记录，未来不得硬编码为永远最新。必须分发 ic_im_put_execution_v1.py、ic_im_option_maintenance_notice.py 及所有实际import依赖。完整专项在原33文件上加入以下4文件，共37文件：test_ic_im_put_execution_repairs_20261016.py、test_ic_im_option_maintenance_notice_20261016.py、test_ic_im_put_delivery_completion_20261016.py、test_ic_im_fear_source_delivery_20261016.py；零失败/错误/跳过、隔离state/迁移0、60秒外层硬限不变，生产恢复迁移1。不能使用旧formal_runtime快照运行新来源策略。
恐慌输入修订 baifenwei_either_source_20261016_v1：百分位CSV、网页任一自身校验合格即可用于原25/50阈值与0.5倍网格；仅有一侧时必须显示“仅取得单侧数据，未完成双侧验证”，CSV无页面发布时间须披露未取得收盘后发布证明。两侧可用继续核对，日期不同优先较新合格数据，同日冲突以CSV为准并报警；不因单侧或冲突单独BLOCKED整份日报。仍禁止非法/未来值、无界沿用T-2；严格上一实际交易日口径、真实时区/采集时钟和15:00边界保持。两侧无合格T/T-1数据时，内部网格冻结；发布status=partial，网格/完整合计目标为null/未确认，其余已核验模块继续展示，不能写网格零仓或HOLD冒充已确认。9圈列真实指数、数据日、采集时点和官网，仅供参考、不进入任何策略入场/退出/目标；日期不同明确提示，取不到只警告，不阻断。
本地日报消费者从当次自动化远端main固定SHA获取完整模块并记录raw/LF哈希，标题/纯文本/HTML与附件分别验收。直接网络不可用时按completion文档走新的无发送云端research handoff，校验真实run、当日完整IC/IM快照、ZIP/manifest/SHA、完整官方账本与有效窗口；明确标为“本地任务发起的云端计算”，不冒称本地联网取数。只接受完整官方工件链，先备份再整体恢复，不跨分支拼接；盘中不续账。已有成功研究快照不重跑，按新source规则重新展示与新取数分开。ACTIVE/工作日14:15、15:00边界、有限重试、云端20:00、邮件去重、本地不另起SMTP和交易权限边界不变。代码测试、无发送报告、正式SMTP提交和INBOX分开核验，不把当前预检称为今日14:15执行或20:00实际投递。
'''
updated=raw.decode('utf-8')
new_prompt=prompt+'\n\n'+append
# re.sub replacement text interprets backslashes; use a callable instead.
updated=re.sub(r'^prompt = .*$', lambda _: 'prompt = '+json.dumps(new_prompt,ensure_ascii=False), raw.decode('utf-8'), count=1, flags=re.M)
assert tomllib.loads(updated)['prompt']==new_prompt
(HERE/'automation.proposed.toml').write_text(updated,encoding='utf-8')
(HERE/'proposed_appendix.txt').write_text(append,encoding='utf-8')
files=['ic_im_fear_grid_v1.py','poe_ic_im_mainline_v1_4_bot.py','poe_ic_im_v1_4_state.py','run_ic_im_v1_4_github_digest.py']
published=json.loads((ROOT/'quant_research_runs/20261016_fear_single_source_delivery_v1/after_manifest.json').read_text('utf-8'))
hashes={f:{'actual':sha((ROOT/f).read_bytes()),'published':published[f]['sha256_raw']}for f in files}
ledger=ROOT/'runtime/ic_im_v1_4_r1'
protected=[ledger/'latest.json',ledger/'migration_record.json',*sorted((ledger/'journal').glob('*.json'))]
before={str(p):sha(p.read_bytes())for p in protected}
import poe_ic_im_v1_4_state as state
import run_ic_im_v1_4_github_digest as runner
latest=state.StateStore(ledger).load_latest()
report=runner.render_stored_close_report(latest)
(HERE/'stored_close_readonly_render.md').write_text(report,encoding='utf-8')
after={str(p):sha(p.read_bytes())for p in protected}
assert before==after
local_report=ROOT/'quant_research_runs/20261016_put_delivery_completion_v1/local_daily_verified/ic_im_v1_4_digest.md'
report_text=local_report.read_text('utf-8')
registry=(ROOT/'notes/mainline_registry.md').read_text('utf-8')
result={
 'audit_at':datetime.now().astimezone().isoformat(),
 'automation_path':str(cfgpath),'automation_raw_sha256':sha(raw),'source_credentials_detected':False,
 'exact_required_put_heading_present':'2026-10-16 用户授权的Put执行与日报交付闭环覆盖' in prompt,
 'new_source_revision_in_prompt':'baifenwei_either_source_20261016_v1' in prompt,
 'jiu_in_prompt':'9圈' in prompt,'old_conflict_blocking_clause_present':'来源冲突、未来、无时区、日历不覆盖均继续关闭' in prompt,
 'registry_has_new_source_overrides':'baifenwei_either_source_20261016_v1' in registry,
 'missing_latest_tests_from_prompt':[f for f in required if f not in prompt],
 'latest_required_tests':required,'proposed_appended_rules_only':True,
 'production_source_hashes':hashes,
 'ledger_readonly_validated':True,'ledger_verified_day':latest['verified_day'],'ledger_sequence':latest['sequence'],
 'ledger_digest':latest['digest'],'journal_count':len(list((ledger/'journal').glob('*.json'))),
 'production_json_unchanged':before==after,'protected_hashes':before,
 'existing_local_daily_receipt':json.loads((local_report.parent/'render_receipt.json').read_text('utf-8')),
 'existing_local_daily_has_jiu':'9圈' in report_text,
 'existing_local_daily_has_flexible_source_revision':'baifenwei_either_source_20261016_v1' in report_text,
 'local_autorun_directory_today_exists':Path('D:/Codex/home/automations/ic-im/runs/20261016').exists(),
 'scope':'Read-only ledger and stored-report rendering. No market fetch, production continuation, SMTP or task configuration mutation.'}
(HERE/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items()if k not in ['protected_hashes','latest_required_tests','existing_local_daily_receipt']},ensure_ascii=False,indent=2))
