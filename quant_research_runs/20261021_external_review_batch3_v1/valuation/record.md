# 第三批独立估值调用链分析

2026-10-21，a3_cash。已读工作区主线规则、第三批预注册及`docs/ic_im_daily_valuation_sync.md`。本组只新增独立测试及valuation证据；未修改生产、旧测试、旧证据或正式账本，未联网、部署或发送邮件。

## 结论与不修理由

**尚未发现真实PE/PB被legacy代理覆盖、进而污染正常生产目标或新账本的实际可达路径。本项不做策略/数量修复，不将静态混用字段直接升级为第一批缺陷。**

用户2026-09-08已明确授权：VIP缺失、损坏、不合日期或尚未可用时，继续原代理，并在报告披露。代理回退不是本轮发现的新bug。原代理历史、冻结收益和已签名账本不因后来真实数据可用而追改。

## 分支调用链

| 阶段 | 实际数据流 | 代理值能否盖住正式目标 |
| --- | --- | --- |
| `daily_valuation.resolve` | 先构造价格比代理；同日完整VIP、已过收盘且fetched_at不晚于本次时刻时覆盖PE/PB并标vip_actual；其他情况明确proxy和原因 | 真实值可用时按真实PE/PB返回；已授权回退须保持披露 |
| `live_proxy`当前score | 早期proxy_pb/proxy_pe/proxy_erp仅为兼容诊断。随后用resolve的PB、PE及选定中债利率重新计算score，再生成下一Put目标 | 实际score及下一目标由该次已选输入生成。旧代理分数没有最后覆盖它 |
| `prior_targets` | 上一价格的legacy代理只先构造v13_current_*等兼容“当前”字段，不生成下一Put目标 | 这些中间值不是持久化仓位证据，需继续看锚点，而非单独据此认定污染 |
| `_replay_v13_grid` | 兼容历史全代理重放先给grid_current_units/grid_target_units | 正常生产后续不直接采用它作最终当前网格和目标 |
| 日期与锚点 | `_validate_signal_market_date`拒绝过期行情和跨过未核验交易日；下一未核验日由`_apply_next_unverified_session_anchor(next_session=True)`读取前日next状态；同一已核验日由False读取当日current状态 | 当前Put、动量、网格状态从独立持久锚点覆盖；score、真实来源及下一Put目标保持当次计算值 |
| 网格目标 | fix10之后按持久入场来源、实际score及Fear重做transition；此前有估值provenance时按`_daily_grid_target`从锚点继续迟滞 | 不用全代理历史抹掉已存的真实值驱动网格状态 |
| 最终builder与StateStore | signal保存实际score/provenance及目标；独立持久层校验网格规则和当前状态必须承接前日账本，只允许逐交易日续接 | 极端代理干预实验已穿过完整IC builder、实际临时append及重启读取，目标仍按真实输入保存 |
| 历史回放 | 使用参考日及该次回放时刻匹配估值；今日快照不能替代昨日。若提供同历史日、回放时已可用的完整快照，仍能使用真实估值 | 缺历史VIP或回放时点尚不可用时proxy属已授权保守回退；不称历史真实估值。任意旧日期输出不能回写当前账本 |
| 已存日报 | v1.4 `render_stored_close_report`只使用存量signal的valuation_provenance，不调用resolve | 后上传真实快照不会将旧proxy记录重新标真；后缺快照也不会将已存vip_actual改回代理 |

生产正常路径与直接调用`live_proxy`不同：后者确实还带有受代理历史影响的兼容当前字段，不能脱离builder日期/锚点门禁把它当正式仓位。直接用未来锚点跑任意旧日期也不能获得新写账权限；本组临时StateStore验证旧日不能推进当前链。

## 独立反例与证据

- `initial_independent/`：24项中22通过、2失败。两处完整builder失败在本组合成中债fixture缺少disclosure必需的used_date；保留原始失败，随后只补该schema字段，没有改变业务断言或生产代码。
- `corrected_rate_fixture/`：24通过；真实输入与极端代理相反时，IC/IM直接计算及锚点覆盖通过，完整IC builder通过。
- `with_ledger_roundtrip/`：24通过，3.53秒；将同一完整IC builder结果与合法合成IM同伴信号写入临时统一StateStore，再重启读取，确认score=3.5、vip_actual及核心Put目标Delta=0.5保持。T+1待执行意图不冒充成交。
- `expanded_replay_and_report/`：28通过，3.63秒；追加IC/IM同日历史真实快照与v1.4已存报告不重新取估值的验证。
- 独立测试`test_icim_batch3_valuation_20261021.py`另追加4个日期门禁反例，最终共32项。全部VIP/利率/价格/极端代理数字为合成压力输入；保存SSE真实fixture仅用于独立约束期权身份，不代表这些测试是账户交易。

两组-100/+100代理干预故意把legacy当前字段推到相反状态；当前score仍为真实合成PE/PB计算的3.5，下一Put目标不变。IC完整builder同时核对持久当前网格、source锚点及最终实际来源，临时写账不旁路任何账本验证器。

## 范围限制

- 本项没有进行历史收益重算或证明历史VIP数据完整；不补造当日实际可用时间。
- StateStore验证producer的策略/状态连贯性，不独立访问私有VIP原始数据；来源哈希不是外部真实性签名。本项证明legacy正常调用链的覆盖边界，不把任意伪造producer输入当作已证代理混用路径。
- 日历/并发生产修复冻结后，本组已完成`final_independent/`最终独立复验：**32通过，零失败/错误/跳过，3.67秒**，1条既有Pydantic警告。21份相关生产源码、runner、测试及真实fixture的前后哈希一致，strict evidence_gate对XML计数/进程退出/源码绑定/工件哈希全部通过；收据为`final_independent/receipt.json`。
- 最终绑定bot SHA `194cb2f1dec12ed5829cbeae6957103fd69f975af919a60a28f274ad2713664e`、server SHA `640e33bae36d1043b144bfe50a7a0d08138946041f354e9cd0fd23000b2f2816`。本轮**没有修改估值生产逻辑**；不修结论基于上述调用链与独立反例，不是因静态字段命名相似而统一代理/真实历史口径。
- `final_accepted_source_match.json`是首次累计阶段`accepted_batch3`的字节观察：21份当时相等，但该累计阶段因测试进程非零退出而被strict gate拒绝，**该观察只证明字节，不构成最终累计验收**。旧观察未改写。
- 真正最终采用`accepted_batch3_2`：主线程严格累计1652通过、3项明确单列、零失败/错误/跳过、897份源码绑定。新增`final_accepted_batch3_2_match.json`逐项核对本组21份绑定，独立最终复验、`accepted_batch3_2/source_after.json`及当前工作区三者全部一致。本组32项通过及不修结论与该最终版本一致；本组无需改断言或复用失败累计的“通过”标签。
