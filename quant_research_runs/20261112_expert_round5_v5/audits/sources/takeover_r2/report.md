# 第五轮 r2 来源审计影响闭包核验

**原37项来源对抗测试可以复用；本次未重复执行。** 新r2的213项源码已逐项绑定，核验前后与 `candidate_source_pin_r2.json` 精确相同，pin SHA为 `05ae4d5a238ae619b90cef7755085a1ec55043da32bba8571ad95601944dc0b6`。

必须区分两项事实：r1的“213项整体相同”是当时验收结果，**不再适用于当前r2**；当前r2相对r1确实改了 `ic_im_evidence_adapter_v1.py` 和根 `test_icim_expert_round5_20261112.py`。来源37项使用的实际依赖闭包仍然相同。

| 影响检查 | 实际结果 |
| --- | --- |
| 主程序、到期核验、真实SSE/Sina解析及报价时钟 | 文件字节与r1相同；主SHA仍为 `5db134c87075e9241ac380fa59d07bfb83d275e040ddbba387b7fdc1dc05deef`。 |
| adapter补丁边界 | 使用保存的r1原字节与当前r2解析AST，仅 `_keyed.adapted.collect_listing_result` 改变；将该函数体单独剔除后，**整个adapter模块AST完全相同**。 |
| 来源37项实际路径 | 原测试调用月链与到期来源核验，不调用挂牌列表采集或capture_session，不导入根测试文件。adapter无存证直返、月链回放/采集和record_expiry_proof/validate_chain路径均在未变AST中。 |
| 原始测试证据 | 再核原37个收集/选择/执行节点、XML实际用例名、0失败/错误/跳过、原工件及测试源哈希均保持。 |
| 新挂牌成功封套的SSE失败披露 | 这是变化的helper范围，**不使用原37项替代新验证**；由根、surface及ledger角色的受影响反例与最终回归验收。 |

`verify_impact_closure.py` 实际退出码0，详细前后源绑定及新旧AST哈希见本目录 `summary.json`、`binding_before.json` 和 `binding_after.json`。来源范围未发现新增缺陷。

原37项的身份、价格和到期字段来自保存真实报文，日期/时刻是明确的人工对抗控制；本次不联网，不能证明当前行情可用。未修改产品、旧工件、根测试或正式账本，也未运行生产入口、邮件发送或部署。
