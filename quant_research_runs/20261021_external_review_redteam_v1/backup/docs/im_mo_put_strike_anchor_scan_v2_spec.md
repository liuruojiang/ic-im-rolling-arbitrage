# IM / MO Put 95% 行权价基准复测 v2：决策器纠错

预注册日期：2026-08-23（Asia/Shanghai）  
状态：v1机械决策错误的独立纠错重跑；未批准实盘；冻结主线与v1首次输出不得改写。

## 1. 纠错原因

`im_mo_put_strike_anchor_scan_v1`的真实回测、选约、收益、成本和窗口指标已经成功生成，但自动决策器错误地把“完整年化允许最多落后50bp的watchlist条件”用于`promote_candidate`。v1中`csi1000_spot_m95`完整年化低于`active_im_m95`，因此不满足v1规格第7.3条“完整年化和最大回撤均不差”的晋升条件。

v1正式输出原样保留，状态解释为`decision_classifier_failed_metrics_valid_rerun_required`。v2必须从相同真实输入重新执行全部候选，不得只改v1的decision字段。

## 2. 完整继承

除决策器外，完整继承 `docs/im_mo_put_strike_anchor_scan_v1_spec.md`：

- 同一IM v1.1 Put目标0至4张；
- 同一T收盘/T+1收盘、约3个月MO、月度重置；
- 同一三种参考资产 `active_im / csi1000_spot / matched_expiry_im`；
- 同一90%/95%/100%虚值度；
- 同一真实IM/MO样本2022-07-22至2026-08-14；
- 同一 `core_1x` 与 `im12_core_put` 两个观察层；
- 同一成本、现金、流动性过滤、延迟和无未来数据规则；
- Call和网格继续关闭。

v2逐日收益、选中合约、交易日和指标必须与v1最大误差不超过`1e-14`，否则说明不只是决策器纠错，正式失败。

## 3. 纠正后的决策顺序

主比较仍为真实 `im12_core_put` 的 `active_im_m95` 与 `csi1000_spot_m95`：

1. `promote_candidate`：指数95%的完整年化不低于活动IM95%，完整最大回撤不更深，近3年和近1年最大回撤均不恶化超过1个百分点，并且指数90%或100%至少一个邻点相对同虚值度活动IM呈同方向；
2. `watchlist`：指数95%的完整最大回撤必须严格更浅，完整年化落后不超过0.50个百分点，近3年和近1年最大回撤均不恶化超过1个百分点；
3. `keep_default`：不满足以上条件，或改善只集中于单一近期窗口；
4. 稳定性标签：若指数95%只在近1年改善、完整样本未改善，则固定为`recent_only`，即使90%/100%邻点有部分支持，也不得提升决策等级。

样本不足5年，任何结果仍只属于研究证据；即使满足`promote_candidate`，也不得自动修改IM v1.2。

## 4. 强制验证

1. v1与v2逐日候选收益、Put损益、费用、市值、合约、交易日和窗口指标逐项一致；
2. 活动IM95未修改引擎复现误差、完整IM无Put误差、50:50无Put误差均不超过`1e-14`；
3. 选约最近行权价匹配率100%；
4. 决策器用单元测试明确覆盖“年化略差、回撤相同、近期改善”的`keep_default/recent_only`情形；
5. v1输出、冻结V2、IM v1.1、IM v1.2、Poe和登记表保持不变。

## 5. 产物

- 脚本：`im_mo_put_strike_anchor_scan_v2.py`；
- 测试：`test_im_mo_put_strike_anchor_scan_v2.py`；
- 输出：`outputs/im_mo_put_strike_anchor_scan_v2/`；
- 标准扫描工件：`quant_param_scan_runs/20260823_ic_im_im_v1_2_strike_anchor_diagnostic_v2_im_core_mo_put_strike_reference_asset_x_moneyness/`；
- 本规格及SHA-256：`docs/im_mo_put_strike_anchor_scan_v2_spec.md`、`docs/im_mo_put_strike_anchor_scan_v2_spec.md.sha256`。
