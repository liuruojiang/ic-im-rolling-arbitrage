# 第五轮 ledger 最终续验（r2）

根协调者修复 `LEDGER-V5-PROVENANCE-1` 后，本角色只追加实际政策 entry/roll 的两项受影响验证，均通过。未重跑旧 38 项，未改产品代码、根测试、旧证据或正式账本。

最终 pin 为 `candidate_source_pin_r2.json`，文件 SHA-256 `05ae4d5a238ae619b90cef7755085a1ec55043da32bba8571ad95601944dc0b6`。主程序 SHA 为 `5db134c87075e9241ac380fa59d07bfb83d275e040ddbba387b7fdc1dc05deef`，adapter SHA 为 `c98327b6bd0cf773a17a649dc517dbec43b8eaa751f3fc41381b9563eedbeaaf`。

## 本角色补充验证

| 实际政策入口 | 采集与回放结果 |
| --- | --- |
| entry | SSE Timeout→Sina 有效挂牌；封套保存 SSE 失败分类，仍为 available；实际 `ENTER_SHORT_PUT` 采集/回放相同 |
| roll | 同一来源回退；实际 `ROLL_SHORT_PUT` 采集/回放相同，旧仓锚点不变 |

两项都经过真实请求解析、CaptureSession、EvidenceReader、实际候选及 policy requirements 链。封套通过正式 `validate_listing_outcome`；采集完整且无未解决失败；回放无新增 HTTP、无缺失输入；查询缓存恢复原上下文。进程退出码 0、两节点通过、无跳过；60 秒 watchdog 保持。217 项源、审计脚本、测试、控制 fixture 与运行入口哈希前后一致，213 项正式候选源匹配 r2。

原始回执、XML、节点、网络门禁与来源观察保存在 `closure_r2/`；只读汇总为 `summary_final_r2.json`。

## 复用依据

adapter 相对根保存的 r1 字节备份，只修改嵌套 `collect_listing_result`。把该函数体屏蔽后，整模块 AST 完全相同；该函数内原 `ExpiryEvidenceConflict` 与 `ListingProbeUnavailable` 的两个异常处理分支 AST 也保持。公开查询缓存、生效日、旧日分支、加载就绪、校验器、实际候选和 policy 的 AST/字节未变化。新作用域用 `finally` 恢复缓存，成功路径读取经过正式校验的真实结果封套。因此仅对成功 Sina 回退的来源存证差异补两项，未重复不受影响的旧检查；根的直接 API/缓存/异常复验另由其回执记录。

旧 ledger 工件已独立核验：attempt_1 的 33 个成功节点保留；两个 SSE 14:50 夹具错误只以真实未来时戳反例纠正，attempt_2 共 5 项通过。40 个保留包的 manifest 文件/正文哈希与索引一致，33 完整、7 原负控不完整；220 有效 blob 路径，故意缺失/破坏各 1 个保持。2347 保护对象和38正式 runtime 文件核对保持。

旧 38 个节点包含一个“观察错误来源被丢弃”的诊断测试，它原来通过正是证明 P2 存在；该旧断言现在被本次两项“来源必须保留”的正确断言替代，不能声称旧诊断节点继续在 r2 通过。其余旧成功契约按上述影响闭包复用，测试数不与根/其它角色累计相加。

## 结论与限制

本角色报告的 P2 来源存证遗漏已在 r2 关闭，未发现新增需要产品修改的 ledger 问题。`report.md` 和 `summary.json` 是补修前的 r1 历史审计结果，开放问题状态由本最终报告更新；旧文件不覆盖。

11 月行情、价格与网络报文仍是合成控制，采用真实保存报文的格式。外层市场 builder 和 eligibility 为存证/政策测试接缝，若干加载就绪 role 是明确未消费占位，不能据此声称完整真实市场 builder、StateStore 新写入、当日日报或真实交易已验证。15:00 双源定稿、CFFEX 逐合约月度原文、2027 交易所年度公告均不因本审计关闭。未执行部署、生产日报、SMTP、正式续账或下单。
