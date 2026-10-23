# Scope组：旧Call、候选研究及证据门禁独立对抗审计

2026-10-21。本轮有效独立阶段 `scope/final_01`：**25项全部通过，0失败/错误/跳过，9.08秒**；902份源码/规范/夹具/受控收集配置绑定前后一致，并再次核对当前字节。strict receipt核对真实XML、进程退出码、collection清单、工件哈希。尚待根累计成功后逐项对齐共同源码；不将定向成绩冒充累计验收。

## 两类证据保证缺陷、四个攻击向量

| 类别 | 攻击与首失败 | 修复后证据 |
| --- | --- | --- |
| 选择集及声明未完整控制 | PYTEST_ADDOPTS的-k或--deselect可隐藏显式请求的失败测试，子进程0退出/1PASS/1deselected。`scope/initial_01`两例首败 | 清除隐式环境参数；child必须实际出现2项/1失败，不能用启动失败冒充拒绝 |
| 选择集及声明未完整控制 | pytest.ini/pyproject.toml的python_functions隐藏同文件失败函数，新增collection仍自洽；`scope/config_selection_02`两例首败 | 明确-c本轮受控配置、固定收集约定及rootdir，配置纳入源码；child仍实际2项/1失败 |
| 选择集及声明未完整控制 | collection/test_run自洽排除test_two，receipt却可声明空集或别的case；`scope/declared_exclusions_03`两反例失败、声明一致正例通过 | 完整collected/selected/deselected/executed逐项一致，最终receipt排除声明也必须与实际集合相同 |
| 执行源码绑定遗漏 | 新独立包装器只匹配裸文件名；./、绝对及./node漏绑。真实child测试执行中改写自身，仍取得strict PASS；`scope/initial_01`五个首败（3路径/2实链） | resolve规范化被请求路径并加入绑定，运行前保存测试源；child确认1PASS后因source changed拒绝且不留下accepted receipt |

这些是审计工具的证据保证缺陷，不是已证明的策略仓位污染。PYTEST_ADDOPTS同类风险在旧runner亦存在，但无证据证明旧1652项验收的环境实际启用过隐藏选择器；不得反推旧成绩失真。路径漏绑来自本轮新统一独立包装器，不能冒称旧所有收据均缺失测试绑定。

首轮源码/记录保留：`scope/initial_01` 7FAIL/12PASS、source_equal=true；原19项测试源经原SHA核对保存为test_source.py，真实复制runner从尚存隔离临时项目恢复到runner_source（每份SHA与原绑定相同，带provenance）。`config_selection_02`两配置反例首败的真实runner也已保存；`declared_exclusions_03`保留门禁修复前源码。没有回写旧审计收据、source maps、XML或首次输出。公共包装器随后新增test_sources自动快照。

## 旧Call与候选研究边界

- 只读核验本地19个journal节点，最新seq18/2026-10-20，全部verified_call_contract=None、verified_call_contracts_normalized=0；有旧baseline Call字段，证明不能用它推断库存。journal SHA前后一致。这不是经纪商或云端账户仓位证明。
- 合成合法恢复旧Call的真实public builder在正价/持仓量/双边有效时只生成CLOSE_CALL，目标0；缺last、非正/非有限OI、交叉或无限报价均拒绝。新开/救援Call选择器为tripwire，没有被调用。原ask-only受限仍记录为无本地已核验受阻存量下的潜在恢复限制，不建议本轮改规则。
- 生产15模块AST完整导入闭包不包含候选模块，也没有动态exec/importlib/runpy入口通向候选；覆盖14个候选脚本名。四个候选目录及其相关规范共81文件与本轮修改前保护基线一致。
- 动态选择对照：当前Oct21的IC/IM最近三月目标在实际列出的Dec26/Mar27中仍选择Dec26（期限低于严格三个月），未变为等待新挂牌/最少三个月；未知候选环境标记不改变正式选择。没有执行研究引擎或改变正式维护/下单时点。

## 跨组审读

日历helper补丁对ASCII语法失败显式拒绝；只对实际positive目标/changed新腿检查到期消费，月维护market_date、ordinary next_trade_date和profit execution_day继续分别使用既有语义，未引入研究最少三月。旧short到期PUBLISH与既有pending等待不应当作新开仓拒绝。recovery需校对derive产物与journal.products而非只验证信号后丢掉结果；OHLCV迟到应保留等待类型并不遮蔽合法备用源。这些意见已交主线程及对应审计组，生产由主线程集中修改，本组未改生产或旧测试。

## 最终采用范围

本组25项含证据负面探针及正例，不等同25个生产漏洞。修后`scope/repaired_04`25PASS/9.21秒，再在统一冻结后`scope/final_01`25PASS/9.08秒。有效终态以最终累计共同字节对齐结果追加说明为准。

## 统一源码更新后的最终采用阶段

根随后仅对server唯一刷新任务的普通前置异常增加兜底及300秒后重试；取消异常仍逃逸，未把日历失败变成可执行会话。故保留final_01原凭据，并在新server字节上重新执行原25项：**scope/final_02，25PASS、0失败/错误/跳过，9.82秒，902份绑定前后相同且当前核对一致，strict receipt通过**。本组最终采用final_02，等待与真正成功的累计阶段对齐；final_01仅为此前字节的历史观察。

## 真正最终源码与累计对齐

首累计accepted_adversarial因额外期货expiry比较导致45项旧边界回归失败，严格拒绝，不采用。根仅撤掉此额外期货比较（期货year/语法与实际消费约束保留，Put到期/计划日期/recovery仍核）；本组按该最新bot重新跑原25项，**scope/final_03：25PASS、0失败/错误/跳过，9.39秒，902绑定前后一致，strict receipt通过**。此前final_01/final_02保留，均不能冒称当前最终bot字节。

本组独立再验真正最终accepted_adversarial_2收据：1767PASS、0失败/错误/跳过，906份累计源绑定。final_03全部902份映射均被累计清单收录，逐项独立/累计/当前SHA相同；不存在未纳入累计的本组源码。完整collected/selected/deselected/executed与XML计数再验一致，1767项选中并执行；实际、请求和receipt声明的排除集恰好同一3项，无新增排除。证明在scope/final_03/accepted_adversarial_2_reconciliation.json。25项已含于1767，不重复相加。本组最终采用final_03，记录收尾。
