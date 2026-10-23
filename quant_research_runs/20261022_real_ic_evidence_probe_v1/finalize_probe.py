"""Record a genuine failed live attempt without declaring an acceptance pass."""
import hashlib
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    session = HERE / 'real_session_01'
    receipt = json.loads((session/'receipt.json').read_text(encoding='utf-8'))
    baseline = json.loads((session/'baseline.json').read_text(encoding='utf-8'))
    assert receipt['status'] == 'NOT_ACCEPTED'
    assert receipt['synthetic_market_inputs'] is False and receipt['clock_is_actual'] is True
    assert receipt['formal_runtime_unchanged'] and receipt['anchors_unchanged']
    now_runtime = {p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'runtime').rglob('*') if p.is_file()}
    assert now_runtime == baseline['runtime']
    assert all(sha(ROOT/n) == h for n,h in receipt['source_sha256'].items())
    report = '''# 真实 IC 原日证据包：实际取证未通过

2026-10-22（Asia/Shanghai）。这项验证不需要等待2027日历，也不要求先部署到生产；可以在隔离目录完成。此前主要完成代码、合成场景和比较工具，真实取证未跑通，应明确列为尚未完成的工作。

## 本次实际执行

- 16:38 的实际挂牌列表探针收到 HTTP 200，月份为 202610、202611、202612、202703，说明不是全部网络请求均不可用；单个成功不代表全链路稳定。
- 16:43:01—16:45:07，实际运行当前独立 capture_daily，实际北京时间、实际行情，未替换价格、日期或2027日历。
- 正式账本只读副本的前日为2026-10-21，IC独立核心为510500P2612M06500、15张，证券身份10012149；没有创建假持仓来满足场景。
- 采集会话尚有效时，以同一批具名原日输入测量正常公共builder结果，计划只在研究账本副本通过正常资格校验后追加，再以 historical_replay 回放IC并严格比较字段。生产源码未改。
- 完整采集失败：IC的ic.months、ic.etf发生ProxyError；核心期权链的SSE及新浪回退也在代理连接阶段超时，抛SignalTransportError。部分期货/指数/列表响应成功并存档，但不足以构成完整IC包。
- IM原日信号可生成且有持久化资格，但MO新浪交叉核验原始报文采集不完整；不能据此声明整个原日包完成。
- 因IC原日正常信号未生成、原包不完整，研究账本副本也未追加。load_original明确抛EvidenceUnavailable，逐字段比较阶段未执行，不能将“未运行”解释为“零差异”。

## 为什么此前不能验收

本地正式runtime没有input_evidence目录，环境未配置其他证据根；旧账本保留的是结果，不能反推出原日完整链和HTTP报文。当前查询的价格或后下载历史行也不能冒充过去当天的原始快照。GitHub默认分支检查未见本地新采集模块，但这不足以证明远端服务当前运行版本，未访问其运行卷。

本次将“缺真实包”的笼统状态推进到实际取证失败，具体阻塞为行情代理链路不稳定。官方2027日历不参与这个2026年10月22日、既有2612核心Put样本的阻塞原因。后续应先恢复并验证这些必需源的稳定读取，再于真实收盘日取完整包和原日落账结果，最后跑零未命中、地址子集及逐字段相等。合成测试通过不能替代这次实证。

## 工件与保护

真实尝试回执 real_session_01/receipt.json；原始成功响应、具名输入及失败manifest保存在 real_session_01/evidence/；原日IM信号及类型化版本也保留。失败包不改完整标记，不以当前链补造过去数据。

正式runtime全部156文件的路径和哈希与尝试前一致，锚点恢复；没有部署、发日报、追加正式账本、调整策略或回测收益。所有失败及源码版本均保留。验收状态为 NOT_ACCEPTED，而非测试通过。
'''
    path = HERE/'record.md'
    assert not path.exists()
    path.write_text(report,encoding='utf-8')
    registry = ROOT/'notes/mainline_registry.md'
    backup = HERE/'backup/mainline_registry.md'
    backup.parent.mkdir(parents=True,exist_ok=True)
    assert not backup.exists()
    shutil.copyfile(registry,backup)
    before = registry.read_bytes()
    entry = '''
## 真实IC原日存证尝试（2026-10-22，未通过）

- 此项不依赖2027官方日历，也不必先部署生产。实际16:38挂牌探针HTTP200后，16:43—16:45运行真实独立采集和公共builder测量；采用前日正式账本只读副本中的独立核心510500P2612M06500、15张，无合成行情或日期。
- IC挂牌/ETF及核心链SSE、新浪回退出现ProxyError/SignalTransportError，必需输入缺失，原包不完整；load_original拒绝。IM信号虽生成，但其MO新浪交叉核验也缺原报文。隔离账本未追加，IC逐字段比较未进入，不称零差异或验收通过。
- 保留真实失败包、成功响应、来源哈希和NOT_ACCEPTED回执；正式runtime156文件全路径/字节不变，未部署/发邮件/写正式账本。具体证据见quant_research_runs/20261022_real_ic_evidence_probe_v1/record.md及real_session_01/receipt.json。下一步是恢复实际取数稳定性后重采完整包，不是等待2027日历。

'''.encode('utf-8')
    first, rest = before.split(b'\n',1)
    registry.write_bytes(first+b'\n'+entry+rest)
    assert registry.read_bytes().replace(entry,b'',1)==before
    artifacts = {p.relative_to(HERE).as_posix():sha(p) for p in HERE.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    result = {'acceptance_status':'NOT_ACCEPTED','reason':'Real IC original evidence incomplete: actual proxy/transport failures',
        'live_attempt_performed':True,'synthetic_inputs':False,'field_comparison_executed':False,
        'formal_runtime_unchanged':True,'runtime_files':len(now_runtime),
        'source_files_unchanged':True,'registry_backup_sha256':sha(backup),'artifacts':artifacts}
    target = HERE/'verification.json'
    assert not target.exists()
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='artifacts'},ensure_ascii=False))


if __name__ == '__main__':
    main()
