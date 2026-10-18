# IC / IM 1.4 收盘确认账本

## ⚠️ 月度Put维护日历提醒

**⚠️ IC 月度Put展期换仓提醒：明日（2026-10-16）为维护日，需在 2026-10-16 当天收盘完成换仓，不顺延至下一交易日开盘。**

具体换仓合约及数量需由用户在维护日盘尾按当时行情和策略配置自行计算判断；本提醒不提前锁定新合约，盘中候选仍须盘尾复核。对应Put配置为零时核对无需换仓，不因日历提醒新增仓位。即使常规仓位显示HOLD，也须单独核对这项月度维护。这是研究规则的日历提醒，不代表已换仓或账户成交。

**⚠️ IM 月度Put展期换仓提醒：明日（2026-10-16）为维护日，需在 2026-10-16 当天收盘完成换仓，不顺延至下一交易日开盘。**

具体换仓合约及数量需由用户在维护日盘尾按当时行情和策略配置自行计算判断；本提醒不提前锁定新合约，盘中候选仍须盘尾复核。对应Put配置为零时核对无需换仓，不因日历提醒新增仓位。即使常规仓位显示HOLD，也须单独核对这项月度维护。这是研究规则的日历提醒，不代表已换仓或账户成交。

本记录信号构建：`v1.4-20261008-r1-ic-csi500-abs40-fix11`；当前程序构建：`v1.4-20261008-r1-ic-csi500-abs40-fix11`；网格参数版本：`ic_im_or_fear25_paired_exit50_20261008_v1`；IC动量版本：`ic_csi500_ma105_mom24_w16_abs40_static_navdd6_20261008_v1`；IM/MOM120防抖版本：`ic_im_mom120_abs20_2d_plus1_20260915_v1`。
卖Put展期规则：2026-09-25及以前信号最多成功展期一次；2026-09-26及以后信号每次满足条件可继续展期。本记录以逐腿信号版本为准。
IM卖Call规则：2026-09-25及以前信号保留旧版；2026-09-26及以后信号目标为空仓，若模型账本有旧仓只输出平仓目标。本记录以逐腿信号版本为准。
自2026-09-16信号日起，IC与IM的核心Put在MOM120<0时立即保护，连续两日均>+1%才解除；IM Abs20恢复仍须连续两日均>+1%；Score不变。
IC动量自2026-10-08信号日起用MA105/Mom24/W1.6、Abs40>0即时恢复；Abs两日恢复防抖关闭，6%基础NAV回撤门沿单条旧新拼接路径承接。
网格新版本：2026-10-08信号日起，估值或恐慌≤25入单个0.5倍网格；按冻结的入场来源分别在估值退出线或恐慌≥50退出。
策略字段来自已通过SHA-256日志链校验的持久账本；9圈参考在报告采集时补充，不参与信号重算。
它是研究审计记录，不是账户持仓，也不会自动下单。

- 已核验日期：`2026-10-15`
- 账本序号：`15`
- 账本摘要：`ea18004690504420ce43a69aad809b005c9663e57a50f6ee78a75b6f631971a3`
- IC 恐贪 29.47（2026-10-14，close_previous_session）；网格来源 `none` → `none`；CSV SHA-256 `27adb14865dc51f740d42a302e9150693fb51b1c762c22a904790373b377540a`。
- **百分位取数提示：百分位来源验证状态以原始记录为准。**
- 9圈恐慌贪婪指数：暂未取得；仅供参考，不参与网格信号。

## IC
- 实际信号生成：2026-10-15T20:02:38+08:00；时点语义：close_confirmed。

- 恐慌输入：29.47；真实数据日 2026-10-14；状态 close_previous_session；**沿用上一交易日（当日未发布）**。
IV预警无法判断；N/A（期权报价超出IV监测20分钟时间容忍范围）；参考合约 510500P2612M07500；行情日 2026-10-15；时间 20261015 162902；来源 上交所510500当日持仓Put报价反解IV。仅预警，不改变仓位。


**真实PE/PB：乐咕VIP，数据日期 2026-10-15。国债利率：中债官方10年期 1.6601%，数据日期 2026-10-15（估值与期权定价共用）。股息和相对估值阈值沿用原冻结口径。**

- 季月价差（近季－远季）：IC2612 6638.20 − IC2703 6476.80 = +161.40点（+2.43%；按到期间隔折算+9.75%/年，非保证收益）；行情 2026-10-15，新浪财经
- 常规仓位（动量／网格）：今日收盘形成目标；仅在目标变化时，于 2026-10-16 开盘执行。
- 季度期货展期：当前无执行；下一计划日为 2026-12-15 收盘，当前仍为 IC2612。
- Put／Call：独立于季度期货展期，按各自维护日历处理。
- 期货总仓：0.5 → 0.5
- 核心动作：`HOLD`；动量动作：`HOLD`；网格动作：`HOLD`
- v1.4独立生命周期核心Put：510500P2612M07500；数量：10；兑现状态：N/A；模型生命周期：model_future_route_active
- 普通核心保护Put目标：510500P2612M07500；数量：10（普通保护规则，独立于上列生命周期字段）
- 买Put时点：3倍兑现自2026-09-28起T收盘预选、T+1开盘核价；普通核心/动量Put自2026-09-29起同样执行。月度维护按原定当日收盘，季度期货展期时钟不变。
- 信号边界：本报告只发布策略参考信号；实际成交、行权、结算、交割和账户持仓由用户自行处理。

- Put动作：`HOLD`；Call动作：`HOLD`

<details><summary>完整逐腿JSON</summary>

```json
{
  "call_action": "HOLD",
  "call_current": "无",
  "call_current_contract": null,
  "call_target": "无（IC 1.3明确禁止Call）",
  "call_target_contract": null,
  "call_target_qty_normalized": 0.0,
  "close_confirmed": true,
  "core_action": "HOLD",
  "core_current": "IC2612",
  "core_eod_contract": "IC2612",
  "core_put_current_delta": 0.25,
  "core_put_driver": "MOM120负动量下限",
  "core_put_target_delta": 0.25,
  "core_target": "IC2612",
  "core_units_current": 0.5,
  "core_units_target": 0.5,
  "current_core_put_driver": "MOM120负动量下限",
  "current_momentum_put_driver": "动量袖空仓，按规则Put归零",
  "data_notes": [
    "本次请求重新抓取中金所/上交所/指数行情",
    "真实PE/PB：乐咕VIP，数据日期 2026-10-15。国债利率：中债官方10年期 1.6601%，数据日期 2026-10-15（估值与期权定价共用）。股息和相对估值阈值沿用原冻结口径。",
    "MOM120沿用价格指数动量口径",
    "IC期货源：新浪财经，as-of 2026-10-15",
    "账本下一交易日桥接：当前腿固定取2026-10-14已核验收盘锚点；2026-10-15行情只形成下一交易日目标",
    "恐贪来源：close_previous_session，数据日 2026-10-14，分数 29.47；网页更新 2026-10-15T15:02+08:00；CSV SHA-256 27adb14865dc51f740d42a302e9150693fb51b1c762c22a904790373b377540a",
    "行情源回退/不可用汇总：中金所官方: ConnectionError: HTTPSConnectionPool(host='www.cffex.com.cn', port=443): Max retries exceeded with url: /quote_IC.txt (Caused by NewConnectionError(\"HTTPSConnection(host='www.cffex.com.cn', port=443): Failed to establish a new connection: [Errno 101] Networ",
    "挂牌合约包含2027年到期日，但脚本尚未载入该年度官方休市表；这些到期日暂按周一至周五回退，须在交易所发布年度休市安排后更新"
  ],
  "etf_price": 6.802,
  "etf_quote_date": "20261015",
  "etf_quote_time": "162902",
  "fear_close_policy_revision": "ic_im_close_fear_previous_session_20261015_v1",
  "fear_csv_sha256": "27adb14865dc51f740d42a302e9150693fb51b1c762c22a904790373b377540a",
  "fear_data_date": "2026-10-14",
  "fear_data_status": "close_previous_session",
  "fear_fallback_applied": true,
  "fear_fallback_reason": "same_day_not_published",
  "fear_fallback_timing": "same_day_post_close",
  "fear_fetch_started_at": "2026-10-15T20:02:19+08:00",
  "fear_greed_index": 29.47,
  "fear_latest_available_date": "2026-10-14",
  "fear_page_data_date": "2026-10-14",
  "fear_page_updated_at": "2026-10-15T15:02+08:00",
  "fear_previous_exchange_date": "2026-10-14",
  "fear_retrieved_at": "2026-10-15T20:02:19+08:00",
  "fear_source_url": "https://baifenwei.com/data/fear-greed/fear-greed-full.csv",
  "fetch_time": "2026-10-15 20:02:16",
  "future_ask": 6639.4,
  "future_bid": 6636.0,
  "future_expiry": "2026-12-18",
  "future_last": 6638.2,
  "future_quote_date": "2026-10-15",
  "future_quote_source": "新浪财经",
  "grid_action": "HOLD",
  "grid_current": 0.0,
  "grid_entry_source_current": "none",
  "grid_entry_source_target": "none",
  "grid_policy": {
    "entry": 0.5,
    "exit": 1.0,
    "fear_entry": 25.0,
    "fear_exit": 50.0,
    "units": 0.5
  },
  "grid_policy_revision": "ic_im_or_fear25_paired_exit50_20261008_v1",
  "grid_target": 0.0,
  "grid_transition_reason": "hold_flat",
  "index_price": 6769.251,
  "iv_monitor_option_price": 0.8894,
  "iv_warning": {
    "comparison": "strictly_greater",
    "contract": "510500P2612M07500",
    "iv": null,
    "level": "unavailable",
    "market_date": "2026-10-15",
    "position_effect": "none",
    "snapshot_time": "20261015 162902",
    "source": "上交所510500当日持仓Put报价反解IV",
    "text": "IV预警无法判断；N/A（期权报价超出IV监测20分钟时间容忍范围）；参考合约 510500P2612M07500；行情日 2026-10-15；时间 20261015 162902；来源 上交所510500当日持仓Put报价反解IV。仅预警，不改变仓位。",
    "thresholds": [
      0.4,
      0.5
    ],
    "validation_clock": "2026-10-15T20:02:36.258664+08:00"
  },
  "market_date": "2026-10-15",
  "market_phase": "收盘后",
  "mode": "close",
  "mom120_floor_delta": 0.5,
  "momentum_120": -0.17247149845197152,
  "momentum_abs": -0.15860905297620598,
  "momentum_abs20": -0.1285841104639921,
  "momentum_abs_days": 40,
  "momentum_abs_debounce_active": false,
  "momentum_abs_on": false,
  "momentum_action": "HOLD",
  "momentum_base_dd": -0.14942425457996367,
  "momentum_base_nav": 10.604601679254227,
  "momentum_current_source_date": "2026-10-14",
  "momentum_current_weight": 0.0,
  "momentum_nav_defense": true,
  "momentum_next_weight": 0.0,
  "momentum_ohlcv_source": "Sina",
  "momentum_ohlcv_source_audit": [],
  "momentum_ohlcv_source_failures": [],
  "momentum_ohlcv_source_volume_scale": null,
  "momentum_put_current_delta": 0.0,
  "momentum_put_driver": "动量袖空仓，按规则Put归零",
  "momentum_put_target_delta": 0.0,
  "momentum_score": -39.44152264890218,
  "momentum_score_hot": false,
  "momentum_signal_date": "2026-10-15",
  "momentum_units_change": 0.0,
  "momentum_units_current": 0.0,
  "momentum_units_target": 0.0,
  "momentum_volume_pass": true,
  "momentum_volume_placeholder": false,
  "momentum_volume_ratio": null,
  "next_core": "IC2703",
  "next_trade_date": "2026-10-16",
  "option_monthly_reset_due": false,
  "option_monthly_reset_execution_date": "2026-10-16",
  "option_monthly_reset_preview": true,
  "option_reference_contract": "IC2610",
  "product": "IC",
  "put_action": "HOLD",
  "put_current": "共10张（裸滚核心袖10张 + 动量指引袖0张 + 网格0张） 510500P2612M07500",
  "put_current_contract": "510500P2612M07500",
  "put_current_core_qty": 10,
  "put_current_grid_qty": 0,
  "put_current_momentum_qty": 0,
  "put_current_total_qty": 10,
  "put_market": "`510500P2612M07500`，最新 0.8894",
  "put_selection_note": "2026-10-14已核验账本续接；保护档未变，沿用既有合约与张数",
  "put_target": "共10张（裸滚核心袖10张 + 动量指引袖0张 + 网格0张） 510500P2612M07500",
  "put_target_contract": "510500P2612M07500",
  "put_target_core_qty": 10,
  "put_target_grid_qty": 0,
  "put_target_momentum_qty": 0,
  "put_target_security_id": "10012099",
  "put_target_total_qty": 10,
  "quarter_spread": {
    "annualized": 0.09752246488108597,
    "convention": "near_minus_far",
    "expiry_gap_days": 91,
    "far_contract": "IC2703",
    "far_expiry": "2027-03-19",
    "far_price": 6476.8,
    "near_contract": "IC2612",
    "near_expiry": "2026-12-18",
    "near_price": 6638.2,
    "points": 161.39999999999964,
    "price_type": "same_snapshot_lastprice",
    "ratio": 0.02431382001144883,
    "source": "新浪财经",
    "source_date": "2026-10-15",
    "status": "ok"
  },
  "roll_confirmed": false,
  "roll_date": "2026-12-18",
  "roll_execution_date": "2026-12-15",
  "roll_policy": {
    "effective_date": "2026-09-04",
    "execution": "close",
    "options": "original_independent_monthly_maintenance",
    "tenor": "strict_quarter",
    "trading_days_before_expiry": 3
  },
  "scheduled_roll_completed": false,
  "score": 1.300897194163861,
  "signal_calculation_started_at": "2026-10-15T20:02:34+08:00",
  "signal_generated_at": "2026-10-15T20:02:38+08:00",
  "signal_generation_timing": "close_confirmed",
  "sse_time": "20261015 162902",
  "stage": "最近已完成交易日 2026-10-15 的收盘信号",
  "state_anchor_day": "2026-10-14",
  "strategy_revision": "r1",
  "strategy_version": "1.4",
  "total_put_current_delta": 0.25,
  "total_put_target_delta": 0.25,
  "total_units_change": 0.0,
  "total_units_current": 0.5,
  "total_units_target": 0.5,
  "v14_account_execution_status": "not_observed_out_of_scope",
  "v14_action": "HOLD",
  "v14_action_reason": "mom120_negative",
  "v14_build_id": "v1.4-20261008-r1-ic-csi500-abs40-fix11",
  "v14_core_put_contract": "510500P2612M07500",
  "v14_core_put_current_contract": "510500P2612M07500",
  "v14_core_put_entry_premium": null,
  "v14_core_put_mark": 0.8894,
  "v14_core_put_profit3x_eligible": false,
  "v14_core_put_qty": 10,
  "v14_core_put_security_id": "10012099",
  "v14_core_put_target_entry_premium": null,
  "v14_cycle_id": null,
  "v14_early_roll_used": false,
  "v14_effective_signal_date": "2026-09-18",
  "v14_last_event_id": null,
  "v14_lifecycle_evidence_note": "策略参考路径；实际执行由用户自行完成。",
  "v14_lifecycle_evidence_status": "model_future_route_active",
  "v14_ordinary_put_pending": null,
  "v14_profit_execution_day": null,
  "v14_profit_old_contract": null,
  "v14_profit_old_qty": 0.0,
  "v14_profit_old_security_id": null,
  "v14_profit_pending": false,
  "v14_profit_reentry_contract": null,
  "v14_profit_reentry_qty": 0.0,
  "v14_profit_reentry_security_id": null,
  "v14_profit_trigger_day": null,
  "v14_recovery_net_pnl": 0.0,
  "v14_roll_pending": false,
  "v14_roll_trigger_day": null,
  "v14_roll_wait_reason": null,
  "v14_route_state": "future",
  "v14_rule_revision": "ic_im_v1_4_ic_csi500_ma105_w16_abs40_static_20261008_v1",
  "v14_seller_permission": false,
  "v14_seller_permission_reason": "mom120_negative",
  "v14_settlement_pending": false,
  "v14_settlement_trigger_day": null,
  "v14_short_put_candidate": {
    "contract": "510500P2611M06500",
    "decision_known_entry_abs_delta": 0.3353946833749432,
    "expiry": "2026-11-25",
    "iv": 0.3803311997706673,
    "premium": 0.2067,
    "qty_normalized": 14.548812127514086,
    "quantity_rule": "q_delta05_continuous_research",
    "quote_date": "20261015",
    "quote_source": "上交所行情接口",
    "quote_time": "162902",
    "reason": "ok",
    "security_id": "10012550",
    "strike": 6.5,
    "tradable": true
  },
  "v14_short_put_contract": null,
  "v14_short_put_entry_premium": null,
  "v14_short_put_expiry": null,
  "v14_short_put_qty_normalized": 0.0,
  "v14_short_put_security_id": null,
  "v14_signal_scope": "research_model_signal_only",
  "valuation_provenance": {
    "auxiliary_inputs": "股息和相对估值阈值沿用原冻结口径",
    "fetched_at": "2026-10-15T19:35:52.554424+08:00",
    "gov10y": {
      "fetched_at": "2026-10-15T20:02:15.899367+08:00",
      "mode": "official_actual",
      "observed_date": "2026-10-15",
      "page_sha256": "591fd7e346eb0fa9b40b28648d21fef9095f238d90a995b9c2f1a5a63ef23917",
      "reason": "当日中债官方10年期数据",
      "revision": "chinabond_10y_daily_v1",
      "source": "https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/more?locale=cn_zh",
      "used_date": "2026-10-15",
      "yield_decimal": 0.016600999999999998
    },
    "market_date": "2026-10-15",
    "mode": "vip_actual",
    "proxy_anchor_date": "2026-08-14",
    "real_data_date": "2026-10-15",
    "reason": "当日完整VIP估值校验通过",
    "revision": "legulegu_daily_v1",
    "snapshot_sha256": "1519a891d1c20cafa50bb0a7d454a992de6fff463158a8dd6d43ade6a000ab9d"
  },
  "valuation_put_delta": 0.0,
  "valuation_tier": 0,
  "valuation_tier_label": "基础观察档（低于1.900）"
}
```

</details>
- IM 恐贪 29.47（2026-10-14，close_previous_session）；网格来源 `none` → `valuation_only`；CSV SHA-256 `27adb14865dc51f740d42a302e9150693fb51b1c762c22a904790373b377540a`。
- **百分位取数提示：百分位来源验证状态以原始记录为准。**
- 9圈恐慌贪婪指数：暂未取得；仅供参考，不参与网格信号。

## IM
- 实际信号生成：2026-10-15T20:03:07+08:00；时点语义：close_confirmed。

- 恐慌输入：29.47；真实数据日 2026-10-14；状态 close_previous_session；**沿用上一交易日（当日未发布）**。
IV预警无法判断；N/A（期权报价超出IV监测20分钟时间容忍范围）；参考合约 MO2612-P-6200；行情日 2026-10-15；时间 N/A；来源 东方财富。仅预警，不改变仓位。
核心Put保留估值与MOM120取高；动量Put仅MOM120<0时按3×0.5×动量执行权重配置；两条Put新选约均为当前持有IM价格的102%

**真实PE/PB：乐咕VIP，数据日期 2026-10-15。国债利率：中债官方10年期 1.6601%，数据日期 2026-10-15（估值与期权定价共用）。股息和相对估值阈值沿用原冻结口径。**

- 季月价差（近季－远季）：IM2612 6553.60 − IM2703 6387.80 = +165.80点（+2.53%；按到期间隔折算+10.15%/年，非保证收益）；行情 2026-10-15，新浪财经
- 常规仓位（动量／网格）：今日收盘形成目标；仅在目标变化时，于 2026-10-16 开盘执行。
- 季度期货展期：当前无执行；下一计划日为 2026-12-17 收盘，当前仍为 IM2612。
- Put／Call：独立于季度期货展期，按各自维护日历处理。
- 期货总仓：0.5 → 1.0
- 核心动作：`HOLD`；动量动作：`HOLD`；网格动作：`ADD_GRID`
- v1.4独立生命周期核心Put：N/A（当前无独立生命周期合约）；数量：0.0；兑现状态：N/A；模型生命周期：model_future_route_active
- 普通核心保护Put目标：MO2612-P-7600；数量：1.5（普通保护规则，独立于上列生命周期字段）
- 买Put时点：3倍兑现自2026-09-28起T收盘预选、T+1开盘核价；普通核心/动量Put自2026-09-29起同样执行。月度维护按原定当日收盘，季度期货展期时钟不变。
- 信号边界：本报告只发布策略参考信号；实际成交、行权、结算、交割和账户持仓由用户自行处理。

- Put动作：`HOLD`；Call动作：`HOLD`

<details><summary>完整逐腿JSON</summary>

```json
{
  "absolute_valuation_tier": 0,
  "absolute_valuation_tier_label": "基础观察档（低于2.450）",
  "call_action": "HOLD",
  "call_current": "规范化0张（已核验空仓）",
  "call_current_contract": null,
  "call_expiry": null,
  "call_has_position": false,
  "call_market": "无（已核验Call仓位为0）",
  "call_otm": null,
  "call_target": "目标空仓（fix6停止卖Call）",
  "call_target_contract": null,
  "call_target_expiry": null,
  "call_target_qty_normalized": 0.0,
  "call_target_strike": null,
  "call_target_threat_roll_count": 0,
  "close_confirmed": true,
  "core_action": "HOLD",
  "core_current": "IM2612",
  "core_eod_contract": "IM2612",
  "core_put_action": "HOLD",
  "core_put_current_contract": "MO2612-P-7600",
  "core_put_current_qty_normalized": 1.5,
  "core_put_driver": "MOM120负动量下限",
  "core_put_target_contract": "MO2612-P-7600",
  "core_put_target_qty_normalized": 1.5,
  "core_target": "IM2612",
  "core_units_current": 0.5,
  "core_units_target": 0.5,
  "data_notes": [
    "本次请求重新抓取中金所/上交所/指数行情",
    "真实PE/PB：乐咕VIP，数据日期 2026-10-15。国债利率：中债官方10年期 1.6601%，数据日期 2026-10-15（估值与期权定价共用）。股息和相对估值阈值沿用原冻结口径。",
    "MOM120沿用价格指数动量口径",
    "IM期货源：新浪财经，as-of 2026-10-15",
    "中证1000 OHLCV源：Sina；成交量已通过日期、OHLC关系、正值与数量级校验",
    "账本下一交易日桥接：当前腿固定取2026-10-14已核验收盘锚点；2026-10-15行情只形成下一交易日目标",
    "恐贪来源：close_previous_session，数据日 2026-10-14，分数 29.47；网页更新 2026-10-15T15:02+08:00；CSV SHA-256 27adb14865dc51f740d42a302e9150693fb51b1c762c22a904790373b377540a",
    "行情源回退/不可用汇总：中金所官方: ConnectionError: HTTPSConnectionPool(host='www.cffex.com.cn', port=443): Max retries exceeded with url: /quote_IM.txt (Caused by NewConnectionError(\"HTTPSConnection(host='www.cffex.com.cn', port=443): Failed to establish a new connection: [Errno 101] Networ",
    "挂牌合约包含2027年到期日，但脚本尚未载入该年度官方休市表；这些到期日暂按周一至周五回退，须在交易所发布年度休市安排后更新",
    "MO期权链源：东方财富，as-of 2026-10-15",
    "MO行情源回退/不可用汇总：中金所官方: ConnectionError: HTTPSConnectionPool(host='www.cffex.com.cn', port=443): Max retries exceeded with url: /quote_MO.txt (Caused by NewConnectionError(\"HTTPSConnection(host='www.cffex.com.cn', port=443): Failed to establish a new connection: [Errno 101] Networ"
  ],
  "fear_close_policy_revision": "ic_im_close_fear_previous_session_20261015_v1",
  "fear_csv_sha256": "27adb14865dc51f740d42a302e9150693fb51b1c762c22a904790373b377540a",
  "fear_data_date": "2026-10-14",
  "fear_data_status": "close_previous_session",
  "fear_fallback_applied": true,
  "fear_fallback_reason": "same_day_not_published",
  "fear_fallback_timing": "same_day_post_close",
  "fear_fetch_started_at": "2026-10-15T20:02:40+08:00",
  "fear_greed_index": 29.47,
  "fear_latest_available_date": "2026-10-14",
  "fear_page_data_date": "2026-10-14",
  "fear_page_updated_at": "2026-10-15T15:02+08:00",
  "fear_previous_exchange_date": "2026-10-14",
  "fear_retrieved_at": "2026-10-15T20:02:40+08:00",
  "fear_source_url": "https://baifenwei.com/data/fear-greed/fear-greed-full.csv",
  "fetch_time": "2026-10-15 20:02:16",
  "future_ask": 6553.6,
  "future_bid": 6553.0,
  "future_expiry": "2026-12-18",
  "future_last": 6553.6,
  "future_quote_date": "2026-10-15",
  "future_quote_source": "新浪财经",
  "grid_action": "ADD_GRID",
  "grid_current": 0.0,
  "grid_entry_source_current": "none",
  "grid_entry_source_target": "valuation_only",
  "grid_policy": {
    "entry": 1.6,
    "exit": 2.0,
    "fear_entry": 25.0,
    "fear_exit": 50.0,
    "units": 0.5
  },
  "grid_policy_revision": "ic_im_or_fear25_paired_exit50_20261008_v1",
  "grid_target": 0.5,
  "grid_transition_reason": "enter_valuation_only",
  "im_execution_fix_revision": "im_put_execution_guards_20260908_v2",
  "im_put_calendar_revision": "execution_day_20260911_v1",
  "im_put_execution_revision": "im_monthly_reset_20260907_v1",
  "im_put_policy_description": "核心Put保留估值与MOM120取高；动量Put仅MOM120<0时按3×0.5×动量执行权重配置；两条Put新选约均为当前持有IM价格的102%",
  "im_put_policy_revision": "im_mom120_put102_20260908_v1",
  "im_put_target_moneyness": 1.02,
  "index_price": 6678.649,
  "iv_warning": {
    "comparison": "strictly_greater",
    "contract": "MO2612-P-6200",
    "iv": null,
    "level": "unavailable",
    "market_date": "2026-10-15",
    "position_effect": "none",
    "snapshot_time": null,
    "source": "东方财富",
    "text": "IV预警无法判断；N/A（期权报价超出IV监测20分钟时间容忍范围）；参考合约 MO2612-P-6200；行情日 2026-10-15；时间 N/A；来源 东方财富。仅预警，不改变仓位。",
    "thresholds": [
      0.4,
      0.5
    ],
    "validation_clock": "2026-10-15T20:03:07.546506+08:00"
  },
  "market_date": "2026-10-15",
  "market_phase": "收盘后",
  "mode": "close",
  "mom120_floor_puts_per_full_core": 3,
  "momentum_120": -0.18879353935262588,
  "momentum_abs": -0.12683679187239605,
  "momentum_abs20": -0.12683679187239605,
  "momentum_abs_days": 20,
  "momentum_abs_debounce_active": true,
  "momentum_abs_on": false,
  "momentum_action": "HOLD",
  "momentum_base_dd": null,
  "momentum_base_nav": null,
  "momentum_current_source_date": "2026-10-14",
  "momentum_current_weight": 0.0,
  "momentum_nav_defense": false,
  "momentum_next_weight": 0.0,
  "momentum_ohlcv_source": "Sina",
  "momentum_ohlcv_source_audit": [],
  "momentum_ohlcv_source_failures": [],
  "momentum_ohlcv_source_volume_scale": null,
  "momentum_put_action": "HOLD",
  "momentum_put_current_contract": null,
  "momentum_put_current_qty_normalized": 0.0,
  "momentum_put_parent_qty": 3,
  "momentum_put_selection_note": "MOM120非负或动量执行权重为0，目标空仓",
  "momentum_put_target_contract": null,
  "momentum_put_target_qty_normalized": 0.0,
  "momentum_score": -67.5398037997871,
  "momentum_score_hot": false,
  "momentum_signal_date": "2026-10-15",
  "momentum_units_change": 0.0,
  "momentum_units_current": 0.0,
  "momentum_units_target": 0.0,
  "momentum_volume_pass": false,
  "momentum_volume_placeholder": false,
  "momentum_volume_ratio": 0.7767544363852882,
  "next_core": "IM2703",
  "next_trade_date": "2026-10-16",
  "option_calendar_reset_due": true,
  "option_monthly_reset_due": false,
  "option_monthly_reset_execution_date": "2026-10-16",
  "option_monthly_reset_preview": false,
  "option_reference_contract": "IM2611",
  "product": "IM",
  "put_action": "HOLD",
  "put_current": "核心Put 1.5张 MO2612-P-7600；独立动量Put 0张；合计 1.5张",
  "put_current_contract": "MO2612-P-7600",
  "put_market": "核心：`MO2612-P-7600`，最新 1057，买/卖 1063/1097；动量：无（目标与当前均为空仓）",
  "put_monthly_reset_already_recorded": false,
  "put_monthly_reset_execution_date": "2026-10-16",
  "put_monthly_reset_preview": true,
  "put_pricing_evidence": {
    "core_put_target_contract": {
      "contract": "MO2612-P-7600",
      "market_text": "`MO2612-P-7600`，最新 1057，买/卖 1063/1097",
      "pricing_basis": "source_last_quote"
    },
    "put_current_contract": {
      "contract": "MO2612-P-7600",
      "market_text": "`MO2612-P-7600`，最新 1057，买/卖 1063/1097",
      "pricing_basis": "source_last_quote"
    }
  },
  "put_reference_future": "IM2612",
  "put_reference_price": 6553.6,
  "put_reference_price_date": "2026-10-15",
  "put_selection_note": "2026-10-14已核验账本续接；非月换日沿用既有合约，只按保护档调整张数",
  "put_target": "0.5倍核心袖规范化多 1.5张 MO2612-P-7600（父规则每1倍 3张）；独立动量Put 0张；合计 1.5张",
  "put_target_contract": "MO2612-P-7600",
  "quarter_spread": {
    "annualized": 0.10147430084563885,
    "convention": "near_minus_far",
    "expiry_gap_days": 91,
    "far_contract": "IM2703",
    "far_expiry": "2027-03-19",
    "far_price": 6387.8,
    "near_contract": "IM2612",
    "near_expiry": "2026-12-18",
    "near_price": 6553.6,
    "points": 165.80000000000018,
    "price_type": "same_snapshot_lastprice",
    "ratio": 0.025299072265625028,
    "source": "新浪财经",
    "source_date": "2026-10-15",
    "status": "ok"
  },
  "relative_valuation_tier": 0,
  "relative_valuation_tier_label": "基础观察档（低于1.976）",
  "roll_confirmed": false,
  "roll_date": "2026-12-18",
  "roll_execution_date": "2026-12-17",
  "roll_policy": {
    "effective_date": "2026-09-04",
    "execution": "close",
    "options": "original_independent_monthly_maintenance",
    "tenor": "strict_quarter",
    "trading_days_before_expiry": 1
  },
  "scheduled_roll_completed": false,
  "score": 1.5786079383137401,
  "signal_calculation_started_at": "2026-10-15T20:02:53+08:00",
  "signal_generated_at": "2026-10-15T20:03:07+08:00",
  "signal_generation_timing": "close_confirmed",
  "stage": "最近已完成交易日 2026-10-15 的收盘信号",
  "state_anchor_day": "2026-10-14",
  "strategy_revision": "r1",
  "strategy_version": "1.4",
  "threat_roll_count": 0,
  "total_put_current_qty_normalized": 1.5,
  "total_put_target_qty_normalized": 1.5,
  "total_units_change": 0.5,
  "total_units_current": 0.5,
  "total_units_target": 1.0,
  "v13_current_parent_puts_per_full_core": 3,
  "v13_parent_puts_per_full_core": 3,
  "v14_account_execution_status": "not_observed_out_of_scope",
  "v14_action": "HOLD",
  "v14_action_reason": "mom120_negative",
  "v14_build_id": "v1.4-20261008-r1-ic-csi500-abs40-fix11",
  "v14_core_put_contract": null,
  "v14_core_put_entry_premium": 419.4,
  "v14_core_put_mark": 1057.0,
  "v14_core_put_profit3x_eligible": true,
  "v14_core_put_qty": 0.0,
  "v14_core_put_security_id": null,
  "v14_core_put_target_entry_premium": null,
  "v14_cycle_id": null,
  "v14_early_roll_used": false,
  "v14_effective_signal_date": "2026-09-18",
  "v14_last_event_id": null,
  "v14_lifecycle_evidence_note": "策略参考路径；实际执行由用户自行完成。",
  "v14_lifecycle_evidence_status": "model_future_route_active",
  "v14_ordinary_put_pending": null,
  "v14_profit_execution_day": null,
  "v14_profit_old_contract": null,
  "v14_profit_old_qty": 0.0,
  "v14_profit_old_security_id": null,
  "v14_profit_pending": false,
  "v14_profit_reentry_contract": null,
  "v14_profit_reentry_qty": 0.0,
  "v14_profit_reentry_security_id": null,
  "v14_profit_trigger_day": null,
  "v14_recovery_net_pnl": 0.0,
  "v14_roll_pending": false,
  "v14_roll_trigger_day": null,
  "v14_roll_wait_reason": null,
  "v14_route_state": "future",
  "v14_rule_revision": "ic_im_v1_4_ic_csi500_ma105_w16_abs40_static_20261008_v1",
  "v14_seller_permission": false,
  "v14_seller_permission_reason": "mom120_negative",
  "v14_settlement_pending": false,
  "v14_settlement_trigger_day": null,
  "v14_short_put_candidate": {
    "contract": "MO2611-P-6300",
    "expiry": "2026-11-20",
    "iv": 0.39260007999053004,
    "premium": 163.0,
    "pricing_basis": "source_last_quote",
    "qty_normalized": 1.5,
    "quantity_rule": "q3_delta05_continuous_research",
    "reason": "ok",
    "strike": 6300.0,
    "tradable": true
  },
  "v14_short_put_contract": null,
  "v14_short_put_entry_premium": null,
  "v14_short_put_expiry": null,
  "v14_short_put_qty_normalized": 0.0,
  "v14_short_put_security_id": null,
  "v14_signal_scope": "research_model_signal_only",
  "valuation_provenance": {
    "auxiliary_inputs": "股息和相对估值阈值沿用原冻结口径",
    "fetched_at": "2026-10-15T19:35:52.554424+08:00",
    "gov10y": {
      "fetched_at": "2026-10-15T20:02:15.899367+08:00",
      "mode": "official_actual",
      "observed_date": "2026-10-15",
      "page_sha256": "591fd7e346eb0fa9b40b28648d21fef9095f238d90a995b9c2f1a5a63ef23917",
      "reason": "当日中债官方10年期数据",
      "revision": "chinabond_10y_daily_v1",
      "source": "https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/more?locale=cn_zh",
      "used_date": "2026-10-15",
      "yield_decimal": 0.016600999999999998
    },
    "market_date": "2026-10-15",
    "mode": "vip_actual",
    "proxy_anchor_date": "2026-08-14",
    "real_data_date": "2026-10-15",
    "reason": "当日完整VIP估值校验通过",
    "revision": "legulegu_daily_v1",
    "snapshot_sha256": "1519a891d1c20cafa50bb0a7d454a992de6fff463158a8dd6d43ade6a000ab9d"
  },
  "valuation_puts_per_full_core": 0
}
```

</details>
