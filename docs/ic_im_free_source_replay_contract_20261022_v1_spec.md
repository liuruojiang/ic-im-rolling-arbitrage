# 免费源接入：实证修正与原日信号比较契约 v1

补充预注册：2026-10-22，真实real_02包采集/隔离落账后、最终修复和验收前。原接入规范和real_01/02包、21处原始差异均保留，不改旧回执。

实证：IC/IM真实正常收盘信号均具资格，隔离StateStore推进至10-22、sequence20；IC原日包公共historical_replay零EvidenceUnavailable、HTTP请求0且原日真实地址5个。但是旧比较器完整字典差异21项，其中原日完整iv_warning在回放被无条件替换为简短N/A，属于真实信息丢失，不准豁免。

修正：新增可选iv.monitor具名存证保存原日完整独立监测输出（包括N/A结构、来源、行情时戳、原日校验时刻），回放严格读取，不重新联网；旧包没有该角色时保留原有N/A兼容提示。监测仍无仓位效果，不放宽20分钟条件。data_notes[0]改成原日/回放均为真的中性来源引导，整个data_notes数组继续严格比较。

独立对抗测试复现的边界一并修复：备用源验证用实际已用时长推进的观察时钟、仍拒绝真正未来报文；单次缓存和录制/回放角色键在命中前共同严格规范化合法年月；新浪4个观察挂牌月份须与当日ETF挂牌形状一致，禁止旧但结构完整的列表漏掉远月改变选约。规则仅校验观察值，不创造实际挂牌。

最终比较同时保留“完整原字典差异”和“正式信号事实字段比较”，不得宣称完整原字典零差异：

- 所有原日信号事实字段及未知字段逐字段、逐类型严格相等；整个iv_warning（包含监测自己的validation_clock）、整个data_notes、市场日期/报价时间、来源/哈希、执行日期/待执行计划、动作/数量/价格/目标等均不豁免。
- 只把根级fetch_time、signal_generated_at、signal_calculation_started_at作为计算运行时间分别验证格式、时区/同日和原日/实际回放顺序，不当作行情时间。
- signal_generation_timing仅允许原日close_confirmed→回放retrospective_replay这一明确变换，双方close_confirmed=True、market_date相同；其他取值或转换拒绝。
- 回放只可新增original_evidence、historical_evidence_supplements两项。前者必须逐字段精确绑定所读manifest SHA、decision_at、closed_at/replay_runtime、输入revision、原包annotation和空unrecorded_inputs；后者必须空列表。其他新增/删除字段或非空补证均拒绝本轮“原日事实完整重建”验收。
- HTTP被禁止、所有reader角色/HTTP未命中计数为0、请求地址集合为原日地址子集，这些独立于字段比较同时验证。原日包必须真实且同日、含独立核心Put，原日结果来自正常隔离StateStore落账，不能用比较器补造原结果。

这是对现实回放审计元数据的显式契约，不是把21项差异全部忽略。新增工具须用价格、动作、数量、行情时戳、非首条/首条说明、IV和未知元数据等负例证明严格拒绝。旧比较器和旧失败结论不改写。
