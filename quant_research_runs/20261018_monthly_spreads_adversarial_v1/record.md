# 月度价差多智能体对抗审计记录，2026-10-18

范围及结论见 `docs/ic_im_month_spread_adversarial_20261018.md`。三个独立角色：calendar_adversary（日历/期限），data_adversary（真实解析链/异常数值），delivery_adversary（JSON/本地及双格式邮件）。各角色保留修前失败证据，主代理修复，原角色独立复验。

## 确认问题与修复

- P2：有限正价可导致新月差年化溢出，JSON变null后阻断完整报告/邮件。已在生产诊断计算与展示两侧修复，单组N/A；另一组、完整日报继续。
- 直接helper的超大整数转换/布尔输入、消费者错日期/产品/算术/期限等防御边界同步加固。Python/NumPy布尔在诊断直调中被拒绝，不代表原上游转换为1.0的既有局限已修复。
- P2历史适用边界：2016/2018/2024四个产品交易日受旧年度节假日日历缺口影响，选错腿；当前2026独立验证通过。未修改历史、共用日历或交易规则，具体输入/正确答案在calendar证据。

## 验证

- 原角色独立6项数值反例修前6失败、修后6通过；官方CSV解析→路由→月差→JSON→完整报告的离线路径通过。
- 消费者14项矩阵（1正常、13坏字段）两端通过；真实payload正常/坏历史元数据的完整报告、纯文本、HTML均正常隔离。
- 日历角色2026全年484个产品交易日、12个到期节点、484次缺最近月验证通过；2026保存真实298产品日/596组价差独立核验通过。
- 全量3742保存产品交易日两端内容一致、信号不改，7480成功组减法一致；此结论不覆盖历史日历正确性，四个历史错腿另列。
- Oct09归档标题、正文、HTML三个SHA独立重渲染保持原样。
- 本地完整39文件922项通过，零失败/错误/跳过，pytest17.55秒；云端打包器本地16项通过。中间复制脚本曾引入局部import缩进错误，独立审查及本地检查均拒绝，修复后才继续发布。
- 补齐永久CI中的独立6项后，本地40文件928项通过。主代理继续审查发现缺source字段可绕过第一轮数值guard，补上13个展示必需字段及source类型检查；最终本地40文件941项通过，零失败/错误/跳过，pytest17.58秒。独立交付角色新增1248项键删除/JSON类型矩阵，修后零异常、零两端判定差异；保存真实前一提交缺source反例，不把修补过程中的已修代码当修前证据。
- 445个冻结/历史/账本内容文件逐字节不变，账本JSON变更0；一个运行锁文件因读验证更新，单独记录。季度交易日程5个函数AST一致。

## 发布状态

策略PR56/57均已合并，最终执行pin `dc81478f0552f9ac685588d79679f2258caaca19`。云端PR157最终消费者提交 `bf7bcc5ea95c98789f932dd2b3b77b6e5eef0b5a`，合并为 `c6e6a7bd49421cca709ff474ff3261cbccd39c03`；合并后main工作流读回唯一固定上述策略pin，workflow Git blob `03b7a4e7eae358abfffca89239cf20da43230f97`。未合并云端中间版本。

最终CI [39123053963](https://github.com/liuruojiang/codex-daily-automation-probe/actions/runs/39123053963)全部成功：自动化1137项通过、IC/IM999项通过，共用微盘门禁成功。各范围分别计数，不相加冒称单次全量。10个执行/测试/工作流文件逐一读回Git blob，与本地LF源码全部匹配，见 `source_manifest.json`。本地最终40文件941项通过；邮件专项30项通过。

交付角色最终补审1434项矩阵无异常、无漏拦/双端差异，摘要和原始JSON哈希在 `delivery/formatter_all_read_keys_summary.json`。完整大JSON及两份固定ref基线源码保存在本地审计目录；仓库只归档摘要、关键失败/成功证据及复现脚本。`delivery/source_guard_pre_b82c29d.json`确认第一轮策略提交和未启用的云端PR快照确有source删除KeyError，不把它说成已发生的生产故障。

未发送邮件、未续写正式账本；本轮只修改诊断、展示及对应测试。无发送回归/代码读回不等于下一交易日日报已实际生成或投递。跨覆盖历史日历及上游布尔转换局限仍未在本轮修改，不能称全历史/全数据门禁无问题。

## 独立证据

- calendar：`audit_calendar.py`、`results.json`、`audit_historical_calendar_gap.py`、`historical_calendar_gap_results.json`。
- data：`audit_findings.md`、`test_independent_numeric_isolation.py`、`pre_fix_pytest_six.txt`、`post_fix_pytest_six.txt`、`probe_official_overflow_pipeline.py`及其修前/修后JSON。
- delivery：`findings.md`、`evidence_before_fix.json`、`evidence_after_fix.json`、`consumer_boundary_after_fix.json`，以及可重跑独立脚本。
- 主代理：`protected_before.json`、`protected_verification.json`、`regression.xml/txt`，初始全历史同回调oracle明确不当成独立日历证据。
