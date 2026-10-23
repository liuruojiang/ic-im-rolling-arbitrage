# IC + 510500 ETF Put 无界固定估值门控 v18 首次正式运行失败记录

- 日期：2026-08-18，Asia/Shanghai
- 失败阶段：核心计算和CSV写入后，生成`record.md`时失败。
- 原因：单文件PEP 723依赖只声明了`numpy`和`pandas`，但`DataFrame.to_markdown()`还需要`tabulate`，运行环境中未安装该可选依赖。
- 失败目录：`outputs/ic_510500_put_unbounded_valuation_gate_v18/`。
- 处置：失败目录原样保留，不覆盖、不作为研究证据；补充`tabulate`依赖后，将相同冻结规格、候选、数据、成本和执行规则重跑至`outputs/ic_510500_put_unbounded_valuation_gate_v18_formal_retry1/`。
- 边界：本次失败发生在最终报告写入阶段，但由于正式产物不完整，不读取或解释其中的绩效结果。

