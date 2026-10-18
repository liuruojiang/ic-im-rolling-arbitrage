"""Seal saved research evidence without importing strategy or touching its state."""
from __future__ import annotations

import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo


OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read(name: str):
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def save(name: str, value):
    (OUT / name).write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def check_hash(path: Path, expected: str):
    assert path.is_file(), f"Missing: {path}"
    actual = digest(path)
    assert actual == expected, f"Hash changed: {path}: {actual} != {expected}"


def main():
    protected = read("protected_before.json")
    for name, expected in protected.items():
        check_hash(ROOT / name, expected)
    spec_sha = (OUT / "preregistered_spec.txt.sha256").read_text(encoding="utf-8").strip()
    check_hash(OUT / "preregistered_spec.txt", spec_sha)

    inputs = {}
    for name in ("input_manifest_listed.json", "input_manifest_model.json", "data_audit/input_manifest.json"):
        manifest = read(name)
        rows = manifest if isinstance(manifest, list) else manifest["inputs"]
        for row in rows:
            path = Path(row["path"])
            check_hash(path, row["sha256"])
            inputs[str(path)] = row["sha256"]

    audit = read("accounting_audit/replayed_listed_model_account_audit.json")
    assert audit["status"] == "PASS" and audit["runs_checked"] == 16
    a0_checks = 0
    for run in audit["results"]:
        assert run["status"] == "PASS", run["run"]
        for name, expected in run["files_sha256"].items():
            check_hash(OUT / run["run"] / name, expected)
        detail = read(run["run"] + "/audit.json")
        check_hash(OUT / "recovery_account.py", detail["engine_sha256"])
        check_hash(OUT / "recovery_cycle_controller.py", detail["controller_sha256"])
        assert detail["preregistered_spec_sha256"] == spec_sha
        if run["run"].endswith("/A0"):
            assert detail["saved_baseline_parity"]["status"] == "PASS"
            assert detail["saved_event_parity"]["status"] == "PASS"
            a0_checks += 1
    assert a0_checks == 4

    data = read("data_audit/results_review.json")
    assert data["status"] == "FINAL_INDEPENDENT_READ_ONLY_RESULTS_REVIEW_PASS_WITH_INTERPRETATION_LIMITS"
    assert len(data["all_16_account_metrics"]) == 16
    strategy = read("strategy_review/final_results_review.json")
    assert strategy["status"] == "ACCEPT_RESEARCH_RESULT_ARITHMETIC_AND_QUALIFIED_INTERPRETATION"
    assert strategy["unresolved_result_errors"] == []
    for name, expected in strategy["final_summary_file_hashes"].items():
        check_hash(ROOT / name, expected)
    probes = read("strategy_review/accounting_peer_probe_results.json")
    assert probes["status"] == "PASS" and len(probes["probes"]) == 8
    assert all(p["status"] == "PASS" for p in probes["probes"])
    assert "11 passed" in (OUT / "accounting_audit/tests.log").read_text(encoding="utf-8")

    sources = [
        "quant_research_runs/20261002_fix10_vs_fix11_full_history/run_account.py",
        "quant_research_runs/20261003_mom120_core_only_annual/run_full_model.py",
        "quant_param_scan_runs/20260926_icim_fix8_momentum_transfer_robustness/native_account.py",
        "quant_param_scan_runs/20260928_momentum_future_breakeven_fix9_asifcurrent_full_v2/run_candidate.py",
        "quant_param_scan_runs/20260927_fix9_model_fullaccount_corrected_input/model_source_v3.py",
        "quant_param_scan_runs/20260927_fix9_model_fullaccount_corrected_input/model_source_v2.py",
        "quant_param_scan_runs/20260927_fix9_model_fullaccount/model_source.py",
        "outputs/re_certification/icim_v14_fix4_recert_20260924/l5_native_signal_reconstruction_20260925/native_fix4_historical_sources_v2.py",
        "outputs/re_certification/icim_v14_fix4_recert_20260924/l2_account_ledger_repair_20260925/trade_account.py",
        "research_ic_short95_put_to_ic_recovery_full_model_v1.py",
        "research_im_v13_full_short95_profit3x_joint_v1.py",
        "im_mo_csi1000_put_protection_battery_v6.py",
        "ic_im_v1_4_policy.py",
        "poe_ic_im_mainline_v1_4_bot.py",
        "poe_ic_im_v1_4_state.py",
        "run_ic_im_v1_4_github_digest.py",
        "notes/mainline_registry.md",
        "docs/ic_im_system_mainlines_v1_spec.md",
        "outputs/ic_im_system_mainlines_v1/record.md",
    ]
    provenance = {
        "scope": "Critical replay/account/model/runtime sources; root Python and frozen files additionally covered by protected_before.json. This is not a claim of complete transitive dependency capture.",
        "sources": [{"path": str(ROOT / name), "sha256": digest(ROOT / name)} for name in sources],
        "python_and_commands": {panel: read(f"last_run_{panel}.json")["command"] for panel in ("listed", "model")},
    }
    save("critical_source_provenance.json", provenance)

    summary = read("results_summary.json")
    metrics = {(x["panel"], x["product"], x["arm"]): x for x in summary["metrics"]}
    assert len(metrics) == 16
    with (OUT / "recovery_episodes.csv").open(encoding="utf-8", newline="") as stream:
        episodes = list(csv.DictReader(stream))
    assert len(episodes) == 15
    im_cycles = {x["arm"]: x for x in episodes if x["panel"] == "model" and x["product"] == "IM"}
    with (OUT / "im_model_stress_comparison.csv").open(encoding="utf-8", newline="") as stream:
        stress = {x["arm"]: x for x in csv.DictReader(stream)}

    def pct(x):
        return f"{float(x):.2%}"

    im_table = []
    for arm, label in (("A", "不加恢复保险"), ("B", "普通条件保护尺度"), ("C", "强制最低保护")):
        e = im_cycles[arm]
        m = metrics["model", "IM", arm]
        im_table.append(
            f"{arm} {label}：共同恢复窗口最差日 {pct(stress[arm]['common_recovery_worst_return'])}；"
            f"恢复期 {e['recovery_future_trading_days']} 个交易日；退出执行 {e['exit_execution_date']}；"
            f"保险净损益/起轮权益 {pct(e['insurance_net_pnl_pct_cycle_equity'])}；"
            f"全账户年化 {pct(m['cagr_252'])}，最大回撤 {pct(m['max_drawdown'])}。"
        )
    whole_table = []
    for panel, panel_label in (("listed", "挂牌报价理论成交"), ("model", "上市前重建模型")):
        for product in ("IC", "IM"):
            for arm in ("A0", "A", "B", "C"):
                m = metrics[panel, product, arm]
                whole_table.append(
                    f"{panel_label} | {product} | {arm} | 年化{pct(m['cagr_252'])} | "
                    f"MaxDD{pct(m['max_drawdown'])} | 最差日{pct(m['worst_day'])} | "
                    f"期货恢复持有日{int(m['recovery_future_days'])}"
                )

    report = """IC/IM 恢复期 Put 保护研究 — 2026-10-13

结论：最低保护在 IM 的长恢复模型中显示局部尾险缓冲价值，但代价是保险净损耗和更长的回本等待。现有证据不支持把 IC/IM 生产规则统一改为恢复期强制买 Put。IM 最低保护可保留为研究候选；真实挂牌恢复样本太少，尚无独立样本外验证。

这里的“恢复期”是策略状态：卖 Put 经结算/交割后，策略用核心承接资产等待整轮含成本净损益达到退出条件。它不等于指数回到行权价，也不保证持有时间很长。IC 的 510500 Put 实物交割为 ETF，之后由策略转换成 IC；MO 为现金结算，之后由策略建立 IM，并非 Put 直接交割出期货。研究新增独立 recovery_put 保护，动量、网格及普通核心保护仍沿用原规则。合同机制参见文末交易所来源。

一、正式对象、数据与对照

正式身份为 v1.4-r1 fix11，build v1.4-20261008-r1-ic-csi500-abs40-fix11。本研究复用保存输入，以当前规则进行 as-if-current 反事实回放；不代表这些规则当年已可知或已实盘实施。

挂牌面板：IC 2022-09-19—2026-08-13，共945日；IM 2022-07-22—2026-08-14，共986日。这是历史报价上的理论成交，并非截至10月13日的最新完整实盘记录。Fear 历史是下载快照，没有 PIT 认证。
模型面板：IC/IM 均为2015-04-16—2022-07-21，共1770日/品种。期权价格含 BS 重建，IC 使用ETF/指数价格代理，IM 部分期货价格含上市后估计基差；这些不是当时真实可成交合约。两面板独立报告，不拼接净值。

A0：新开关全部关闭，沿用旧外生生命周期，只做保存基准复现。
A：使用统一的账户事件整轮净损益与内生恢复退出，不加新增恢复保险。
B：在 A 上按当期普通核心保护尺度买保险；IC 为 core_put_target_delta，IM 为规范化 MO 数量；目标为0则不买。
C：在 B 上设最低保护；IC绝对Delta不低于0.125，IM规范化MO数量不低于1.5，保留更高普通目标。IC0.125对应完整1倍名义基准，不等于承接ETF市值固定25%，也不是MOM120负值下限。

B/C覆盖IC确认承接ETF及期货恢复阶段，IM只覆盖期货恢复阶段，不覆盖现金等待。新增保险不采用3x盈利兑现；普通核心已有3x机制保持原样。B仅借用普通保护尺度与选约口径，不能称为完整复制普通核心保护。

T收盘只用当时已知风险、权益和合约链预选；T+1开盘按同一合约身份成交与核验。IC选接近ETF收盘95%的Put，IM选接近计划IM期货收盘102%的Put，到期日取接近T+3日历月的实际挂牌日。入场、从0变正、实际月度维护或尺度变化时重新选约，其余时间维护独立保险合约，不借用旧核心Put身份。IC月度预览不重复实际维护。

沿用期货单边1bp、买Put单边5bp、现金年息3%、每1倍期货30%保证金/缓冲及卖Put的15%净值资金缓冲。买入权利金是现金换资产，不重复计作净值损失；MTM、卖回和费用进入同一账户。15%不是本研究的期货绩效保证金假设。

整轮从首次卖Put执行转换开始，计seller、承接ETF、恢复核心及新增保险的实际账户 equity_change；排除独立动量/网格、现金利息及普通核心Put。T收盘达到含预计保险平仓费的非负净损益，计划T+1退出；实际开盘跳空及费用仍归原轮，不承诺开盘成交后一定非负。同数量核心可接续普通阶段，不人为制造期货往返。右删失单列；本次A/B/C完成结果均无未退出恢复轮。

二、真实挂牌报价的结果

两个品种各只有1轮恢复，各1个期货恢复日。B在这些恢复日的普通条件目标均为0，因此A、B和A0逐日完全一致。
C在IC承接ETF阶段另有1个持有日；期货恢复1日，于2025-01-27开盘退出，新增保险净损失占起轮权益0.2358%。
C在IM期货恢复持有1日，于2025-01-22开盘退出，新增保险净损失占起轮权益0.0633%。两者退出日期均未改变，没有观察到尾险指标改善。这不能证明长期不需要保险，只说明当前真实恢复样本不足。

新增真实报价保险共2组开平仓、4笔交易，全部独立核对同一合约当天原始开盘。IC为510500P2503M05250，IM为MO2504-P-5900；没有用次日新行权价合约替换原合约。

三、IM长恢复模型的成本与保护

这是1轮模型恢复：卖方循环始于2019-04-09，期货恢复始于2019-05-21。主对照如下：
__IM_TABLE__

共同恢复窗口是[2019-05-21, 2019-12-20)，三臂均含146个恢复持有日。A/B最差日为2019-06-06，C最差日为2019-09-17，因此表中是相同窗口内的最差日统计，不能误称同一天的纯Put减损。
C将恢复阶段最低整轮净PnL/起轮权益从A的-8.01%改善到-6.73%，但恢复期由146日增至200日，延长54个交易日；B增至181日。保险净损失约4.10%包含权利金、卖回、MTM与费用，并非手续费4.10%。B/C保险手续费分别约占起轮权益0.51%/0.75%，反复滚动的累计买入权利金约22.81%/36.97%，后者是累计现金流而非永久损失。

为什么保护会延长回本：期权可能在下跌时缓冲损失，但在等待反弹时产生时间价值损耗，且买Put抵消一部分上涨收益。统一整轮净损益把这些保险损耗也计入回本条件，所以“损失更小”和“更快回本”不必同时成立。

2020-02-03同日全账户回报为A -8.37%、B -8.52%、C -2.62%。但当天A已退出恢复，B/C仍处恢复状态，这是内生完整路径对照，不能全部归因于同仓位的Put保护。C全样本最差日已换成2015-07-27（-5.36%），也不能拿不同最差日期冒充同日因果实验。
三臂全样本MaxDD均为-21.27%，主要回撤谷底为2015-09-29，早于首次恢复保险；全样本MaxDD不变不能否定后续局部保护。B年化略高亦涉及后续状态与路径变化，不构成保险独立收益或更优策略证明。

四、IC旧恢复标签的会计影响

旧A0模型含744个 recovery_future 日末标签，2019-06-28延续至2022-07-20。最后一天标签缺失不能当作回本退出证明。统一账户事件口径的A只剩两轮、合计3个期货恢复日，退出为2019-07-02和2020-04-27。A0到A的年化及回撤变化是会计/路由口径变化，绝不能算成买Put效果。
B与A完全一致；C两轮保险净损益/各自起轮权益分别为-0.2698%和+0.2575%，退出日期不变，全账户年化13.54%附近略降，MaxDD未改善。当前没有足够IC长恢复样本支持最低保护晋级。

五、同次回放的全账户指标

以下年化均使用全部N条日收益按252/N计算；原引擎audit.json遗留的N-1口径不替代主报告。MaxDD为负值；A0恢复日标签不等于经过新会计认证的完成循环。
__WHOLE_TABLE__

六、独立复核、限制与复现

16个组合（2面板×2品种×4臂）全部完成。4个A0的逐日与事件基准对照通过；16臂现金、权益、费用、轮次与保险归属对账通过。重要不变量测试11项、独立对抗性探针8项通过；另由独立Agent重算16臂指标并核查恢复段和原始报价。有效数字与原文件一致，浮点容差见审计文件。

原引擎非新增保险部分仍有开盘缺价退回前收盘的观察调用：挂牌IC每臂2次、IM每臂17次；调用包括开盘估值与成交，不能当作交易笔数。IM另有4次既有买方sticky-IV开盘代理调用。新增C保险的4笔挂牌交易均为原始开盘。模型面板没有这类回退计数，也仍然是模型报价，不能解释为真实数据认证。成交量、冲击、真实保证金、PIT及样本外有效性均未因这些对账获得认证。

复现（在项目根目录；runner自行隔离研究状态）：
python -B -X utf8 research_reports/20261013_recovery_put_research/run_research.py --panel listed
python -B -X utf8 research_reports/20261013_recovery_put_research/run_research.py --panel model
python -B -X utf8 research_reports/20261013_recovery_put_research/summarize_research.py
python -B -X utf8 research_reports/20261013_recovery_put_research/accounting_audit/validate_research_outputs.py --panels listed model
python -B -X utf8 -m pytest -q -p no:cacheprovider research_reports/20261013_recovery_put_research/accounting_audit/test_recovery_accounting.py
python -B -X utf8 research_reports/20261013_recovery_put_research/strategy_review/accounting_peer_probes.py
python -B -X utf8 research_reports/20261013_recovery_put_research/finalize_research.py

主结果：summary_metrics.csv、recovery_episodes.csv、insurance_execution_audit.csv、im_model_stress_comparison.csv、results_summary.json及同次图im_model_recovery_tradeoff.png。
逐日/事件/审计：runs/{listed,model}/{IC,IM}/{A0,A,B,C}/。
独立复核：accounting_audit/replayed_listed_model_account_audit.json、data_audit/results_review.json、strategy_review/final_results_review.json。
冻结预注册SHA-256：__SPEC_SHA__。
输入/来源/交付文件哈希：input_manifest_*.json、data_audit/input_manifest.json、critical_source_provenance.json、research_manifest.json。
本次保护的820份生产代码、冻结规格/登记信息及账本文件逐份哈希未变化；先前609项通过的脚本修复保留。本次新增引擎、控制器与结果仅在独立研究目录。未改变正式参数、生产路由或持久账本，未进行外发、部署或交易。

判断：当前保留“IM恢复期最低Put保护”作为研究候选，证据等级为模型条件性支持；没有足够真实长恢复样本，不作生产晋级。IC暂缺明确增益证据。先承认成本与恢复时间的取舍，再讨论是否接受保险预算，而不是仅凭一次灾难日或整体年化作选择。

合同机制来源（只支持结算/交割说明，不为本回测收益背书）：
上海证券交易所《中证500ETF期权合约基本条款》，510500、实物交割：
https://www.sse.com.cn/assortment/options/contract/c/c_20230303_5717361.shtml
中金所《中证1000股指期权合约交易细则》，MO、现金交割（官方检索摘要核实，PDF直接访问超时）：
https://www.cffex.com.cn/u/cms/www/202207/1820311129r5.pdf
"""
    report = report.replace("__IM_TABLE__", "\n".join(im_table))
    report = report.replace("__WHOLE_TABLE__", "\n".join(whole_table)).replace("__SPEC_SHA__", spec_sha)
    (OUT / "research_report.txt").write_text(report, encoding="utf-8")

    verification = {
        "status": "PASS",
        "sealed_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "classification": "research_only_no_production_promotion",
        "protected_files_checked_unchanged": len(protected),
        "unique_source_inputs_hash_checked": len(inputs),
        "preregistered_spec_sha256": spec_sha,
        "completed_runs": 16,
        "baseline_daily_and_event_pairs": a0_checks,
        "account_reconciliations": 16,
        "controller_tests_passed": 11,
        "independent_probes_passed": 8,
        "independent_data_review": data["status"],
        "independent_strategy_review": strategy["status"],
    }
    save("final_verification.json", verification)
    artifacts = [
        {"path": str(p.relative_to(OUT)), "bytes": p.stat().st_size, "sha256": digest(p)}
        for p in sorted(OUT.rglob("*"))
        if p.is_file() and p.name != "research_manifest.json" and "__pycache__" not in p.parts
    ]
    save("research_manifest.json", {"verification": verification, "artifacts": artifacts})
    for row in artifacts:
        check_hash(OUT / row["path"], row["sha256"])
    print(json.dumps({**verification, "artifact_files_hash_verified": len(artifacts),
                      "report": str(OUT / "research_report.txt")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
