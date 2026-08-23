# IC / IM 滚动套利叠加动量研究候选 v1.2

预注册日期：2026-08-23（Asia/Shanghai）  
状态：双腿统一研究入口；未批准实盘；不修改冻结 IC/IM V2 主线、Poe 或交易配置。

## 1. 范围

本规范把 `ic_mainline_v1_2` 与 `im_mainline_v1_2` 统一登记为 IC/IM 动量分袖 v1.2。两腿分别生成目标日程和审计；本版本不定义IC与IM之间的资本分配、不跨品种净额结算、不合成组合收益。

## 2. IC腿

- 50%裸滚IC核心袖 + 50%中证500动量袖；
- 动量继承 A 股多头 v1.3 的 `MA110 / Mom24 / W2 / 50% OFF + 50% Abs20>0`；
- 核心袖使用完整冻结IC V2 Put；动量袖只使用估值Put，删除MOM120最低Delta；
- 冻结 `0.375/1.000` 网格独立运行，新增仓不加Put；
- IC不卖Call；
- 完整定义以 `docs/ic_mainline_v1_2_spec.md` 为准。

## 3. IM腿

- 50%裸滚IM核心袖 + 50%中证1000动量袖；
- 动量继承 A 股多头 v1.3 的 `MA35 / Mom18 / W2.5 / 50% OFF + 50% Abs20>0`；
- 完整继承 `im_mainline_v1_1` 的Put、Call及 `1.60/2.00` 独立网格；
- Put与Call只覆盖0.5倍核心袖；动量袖和网格不配置Put/Call；
- Call救援期限仍为 `rescue_next_listed`；
- 完整定义以 `docs/im_mainline_v1_2_spec.md` 为准。

## 4. 权限与绩效边界

- 两腿均为 `research_candidate_not_live_authority`；
- 冻结V2仍是登记表当前默认研究主线；
- 统一入口不生成订单、不输出当前下单建议；
- 统一入口不把不同父规则、不同期权真实期或不同网格事件直接合成为组合绩效；
- 若后续需要IC/IM资本配比、相关性、保证金共用或组合回测，必须另开版本预注册。

## 5. 强制验证

1. IC与IM目标日程起止日、行数和日期索引一致。
2. 两腿各自规范、公式、T+1、Put/Call/网格隔离测试全部通过。
3. IC Call恒为0；IM Call只覆盖核心袖。
4. 两腿状态均保留研究候选标签。
5. 主线登记表、冻结V2规范、正式输出和Poe文件不得修改。

## 6. 产物

- 统一入口：`ic_im_mainline_v1_2.py`；
- 测试：`test_ic_im_mainline_v1_2.py`；
- 输出：`outputs/ic_im_mainline_v1_2/audit.json`与`record.md`；
- 本规范及SHA-256：`docs/ic_im_mainline_v1_2_spec.md`、`docs/ic_im_mainline_v1_2_spec.md.sha256`。

