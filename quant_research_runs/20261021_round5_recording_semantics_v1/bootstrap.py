"""Freeze the pre-edit baseline for the follow-up patch review."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PRIOR = ROOT / "quant_research_runs/20261021_round4_evidence_calendar_v1"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    target = HERE / "baseline.json"
    if target.exists():
        raise FileExistsError("baseline is immutable")
    binding = json.loads((PRIOR / "accepted_01/source_after.json").read_text(encoding="utf-8"))
    assert all(sha(ROOT / name) == digest for name, digest in binding.items())
    paths = ["poe_ic_im_mainline_v1_4_bot.py", "ic_im_evidence_adapter_v1.py", "ic_im_signal_evidence_v1.py",
             "ic_im_quarter_roll_v1_3.py", "ic_im_v1_4_policy.py", "notes/mainline_registry.md"]
    backup = {}
    for name in paths:
        destination = HERE / "backup" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / name).read_bytes())
        backup[name] = sha(destination)
    protected = {}
    for directory in [PRIOR, ROOT / "runtime"]:
        for path in directory.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and ".pytest_cache" not in path.parts:
                protected[path.relative_to(ROOT).as_posix()] = sha(path)
    for name in ["run_bound.py", "run_checks.py", "evidence_gate.py", "pytest.ini", "exclusions.json"]:
        (HERE / name).write_bytes((PRIOR / name).read_bytes())
    (HERE / "prior_args.json").write_bytes((PRIOR / "final_args.json").read_bytes())
    (HERE / "focused_args.json").write_text(json.dumps(["test_icim_round5_recording_semantics_20261021.py"]) + "\n", encoding="utf-8")
    spec = ROOT / "docs/ic_im_round5_recording_semantics_20261021_v1_spec.md"
    assert not spec.exists()
    spec.write_text("""# 第五轮续审：录制语义、重复 URL 与回放时钟 v1

日期：2026-10-21。依据：用户本轮粘贴的补丁 1/2/3/5/6/7 和五项核对请求。

先核对完整依赖并保存原字节；前轮 2635 项、22 项单列为基础。旧规范、测试和验收工件不改写。

1. 增加实际独立采集器在 11-23 IC 持有 2701 Put、无新到期日时的存证和历史恢复测试，不以今日链替代原日输入。只使用合成行情、临时账本及显式合成 2027 年日历。
2. 检查高层 role/key 的完整年月/交易日隔离，以及同一 URL/参数多次录制的读取语义。多份不一致 HTTP 证据不得静默选最后一份；最小修复不得通过未经证明的请求次序构造价格。
3. 验证 annotate 内部编码、current_session 生效、估值/恐慌/利率来源冻结。原日采集与补证的真实获取时间分开，不清空 historical_replay 标志。
4. 检查 load_original 返回的真实收集末端时钟及其新鲜度边界；不承诺与原日各调用时点完全相同，不回溯时钟放行过期报价。独立 IV 监测未存档继续 N/A。
5. 核对 policy 对 None 和不可交易 dict 的兼容、无需候选时的入场/展期门控；不改变策略动作、参数或配量。
6. 核对季度 T-3/T-1 调度；继续要求官方交易日历。恐慌缺值采用既有 A 口径，未确认不落账，允许独立历史补证。分页不能依据记忆放宽；外部真实 oracle 未取得时明确保留。

验收：保留新增反例修前失败；适用累计回归绑定源码/规范/夹具、实际选择/执行集合、XML 和退出码。检查前轮工件、备份、正式 runtime；不部署、不发邮件、不写正式账本。
""", encoding="utf-8")
    spec.with_suffix(spec.suffix + ".sha256").write_text(sha(spec) + "  " + spec.name + "\n", encoding="utf-8")
    dependency = HERE / "dependencies/ic_im_quarter_roll_v1_3.py"
    dependency.parent.mkdir(parents=True, exist_ok=True)
    dependency.write_bytes((ROOT / "ic_im_quarter_roll_v1_3.py").read_bytes())
    target.write_text(json.dumps({"created_at": datetime.now(timezone.utc).isoformat(),
        "prior_binding": binding, "protected": protected, "backup": backup,
        "quarter_roll_source_copy_sha256": sha(dependency)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"prior_binding": len(binding), "protected": len(protected), "backup": len(backup)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
