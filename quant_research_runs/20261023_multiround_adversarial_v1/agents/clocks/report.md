# E/F 独立对抗审计（修前）

审计范围：单查询 510500 月链缓存、具名 ic.chain 会话去重、采集/回放观察时钟、选中证券 20 分钟门禁、恐慌 observation_batch 嵌套。未修改生产、既有测试、冻结规范或输出；没有联网，没有读取/写入正式账本。

## 结果

新增 `test_icim_ma23_clocks_20261023.py`，共 19 项。有效修前 `clocks_before_02` 为 2 失败、17 通过；28 生产模块加本文件共 29 项 SHA 前后完全一致。两个失败属于同一项接受输入边界问题，不计作两个独立发现。

### P1：声明为新浪来源的链可以省略消费证券的逐行时间而绕过门禁

当前 `fetch_sina_510500_chain` 正常 parser 会构建 source_stamp。没有证据说真实正常报文被 parser 漏掉该字段，本发现不是这种声称。

但是当前归档/回放语义门禁接受错误/旧 producer 的以下输入：链 stamp.source 明确为 `新浪财经期权详报价（上交所回退）`，六个标准身份/价格列齐全，source_stamp 整列缺失。`adapter.validate_chain` 仅验证六列与聚合 stamp；返回后 `_require_selected_510500_quote_time` 看到该列不在选中行，直接 return。真实 `_v14_ic_short_put_candidate` 因而继续反解/配量并返回 tradable=True。

两个实际路径已复现：

1. 独立 capture 中，注入错误 producer 的来源声明和 DataFrame，经实际 `_fetch_510500_chain_for_day`/adapter/候选函数消费。
2. 使用真实 CaptureSession 生成 hash-valid typed ic.chain 包，通过实际 EvidenceReader 再消费。没有通过重算一个损坏清单来掩盖错误；包内存的是明示合成的旧 producer 输入。

失败测试：

- `test_confirmed_sina_candidate_cannot_omit_consumed_row_timestamp_in_capture`
- `test_confirmed_sina_candidate_cannot_omit_consumed_row_timestamp_in_typed_replay`

影响：对错误/旧 producer 已生成且哈希完整的包，当前消费者可能把缺少逐行时间的新浪价格用于正式候选排序、IV/数量计算；聚合时间不能替代当前明确要求的新浪选中证券 20 分钟证据。当前正常 parser 和现有真实已验收包未证实触发，所以不能称已观察到正式持仓被污染。

建议：保留确证的来源随链/消费行传递；仅当来源为新浪且实际消费的行缺少逐行时间时拒绝，不误杀无逐行证据的 SSE 契约，不因为某条未消费行陈旧而删行/换行权价。补丁由 root 统一实施，agent 不改生产。

## 通过的对抗面

- SSE 来源缺逐行时间继续按既有聚合源契约处理。
- 陈旧但不消费的行不会污染全部链；最近行陈旧时拒绝，不跳选另一较远的新鲜行。
- 原始月份缓存命中重验日期，返回对象与 attrs 嵌套值、stamp 均不暴露内部缓存供调用者修改。
- `_validate_value` 和 `_validate_chain_stamp_matches` 连续调用观察时钟不会重复累计 elapsed。
- 20 分钟精确边界：19:59 与 20:00 接受、20:01 拒绝；午休不作为已交易分钟累计。
- copy_context 到真实 worker 后观察时钟推进，策略冻结计算时钟不推进。
- historical_replay 下原始回放时钟保持不变。
- collection_clock/fear observation_batch 嵌套在异常退出后恢复同一外层对象与原锚点，没有另开 batch 丢证据。
- 缺时间、午夜、09:15 前和未来时间不能借同一行情日通过链 stamp 校验。

## 工具与素材边界

所有价格、合约行和时钟均为明示合成，用真实函数/缓存/CaptureSession/EvidenceReader/worker 线程测试，不冒充实际行情或生产落账。报价与 BS stubs 只隔离门禁问题，不验证财务数值。

`initial_01` 的 typed replay fixture 误将规范 month 键写成 202611，导致查找失败而非时间门禁失败。原测试副本、初始回执保留于 agents/clocks/initial_01；修成 2611 后 `before_02` 两个测试均稳定显示 DID NOT RAISE。有效反例使用 before_02，不隐去素材错误。

独立运行器 agents/clocks/run_agent.py 仅绑定生产 28 模块和本 agent 测试，避免其他 agent 正在创建新测试使测试文件集合漂移。最终全量统一绑定由 root 完成。
