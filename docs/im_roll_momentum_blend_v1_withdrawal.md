# IM 裸滚与多头动量组合 v1 撤回说明

日期：2026-08-22

用户澄清：动量策略不是与裸滚 IM 进行资本配比，而是唯一的 IM 仓位信号。只有动量目标大于0时才持有并滚动 IM；目标为0时全部转为现金。

因此撤回以下旧结论：

- `滚IM/动量IM = 100/0、75/25、50/50、25/75、0/100` 的资本分袖比较；
- `1倍裸滚 + 动量叠加` 的杠杆诊断；
- 从上述比较中选择组合比例的建议。

旧规格、脚本和输出保留为错误解释的审计证据，不覆盖、不删除，不得继续作为策略结论引用。

正确版本：

- 规格：`docs/im_momentum_gated_roll_v1_spec.md`；
- 脚本：`im_momentum_gated_roll_v1.py`；
- 输出：`outputs/im_momentum_gated_roll_v1/`；
- 决定：`corrected_interpretation_retain_single_gated_roll_path`；
- 状态：研究用途，未批准实盘。
