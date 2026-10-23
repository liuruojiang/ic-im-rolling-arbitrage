# IC v1.3 → v1.4最终联合升级闸门

## Data Snapshot

真实挂牌510500期权层2022-09-19至2026-08-14；理论延展层2015-04-16至2026-08-14。

## Full Results

| candidate                | segment   | start      | end        |   rows |   total_return |   ann_return |   ann_vol |   sharpe_repo |    max_dd | scope   |
|:-------------------------|:----------|:-----------|:-----------|-------:|---------------:|-------------:|----------:|--------------:|----------:|:--------|
| real_current_v13         | full      | 2022-09-19 | 2026-08-14 |    946 |        1.44513 |     0.26893  |  0.166715 |       1.51051 | -0.110656 | real    |
| real_profit3x_only       | full      | 2022-09-19 | 2026-08-14 |    946 |        1.54188 |     0.282115 |  0.169843 |       1.54657 | -0.110656 | real    |
| real_fixed_short95_only  | full      | 2022-09-19 | 2026-08-14 |    946 |        1.70727 |     0.303826 |  0.150874 |       1.83268 | -0.119365 | real    |
| real_final_joint         | full      | 2022-09-19 | 2026-08-14 |    946 |        1.70727 |     0.303826 |  0.150874 |       1.83268 | -0.119365 | real    |
| model_current_v13        | full      | 2015-04-16 | 2026-08-14 |   2756 |        5.46914 |     0.186155 |  0.164602 |       1.11854 | -0.195776 | model   |
| model_profit3x_only      | full      | 2015-04-16 | 2026-08-14 |   2756 |        6.20611 |     0.197914 |  0.163066 |       1.18805 | -0.121241 | model   |
| model_fixed_short95_only | full      | 2015-04-16 | 2026-08-14 |   2756 |        6.42968 |     0.201265 |  0.162596 |       1.20805 | -0.195776 | model   |
| model_final_joint        | full      | 2015-04-16 | 2026-08-14 |   2756 |        7.27608 |     0.213174 |  0.161037 |       1.27946 | -0.121241 | model   |

## Promotion Gates

| scope   | segment   |   current_ann_return |   joint_ann_return |   joint_minus_current_ann_pp |   current_sharpe |   joint_sharpe |   joint_minus_current_sharpe |   current_max_dd |   joint_max_dd |   joint_mdd_abs_worsening_pp | baseline_gate   | best_single_gate   | gate   |
|:--------|:----------|---------------------:|-------------------:|-----------------------------:|-----------------:|---------------:|-----------------------------:|-----------------:|---------------:|-----------------------------:|:----------------|:-------------------|:-------|
| real    | full      |             0.26893  |           0.303826 |                      3.48958 |          1.51051 |        1.83268 |                     0.32217  |       -0.110656  |     -0.119365  |                     0.870911 | False           | True               | False  |
| real    | last_3y   |             0.349631 |           0.377852 |                      2.82206 |          1.73496 |        2.00181 |                     0.266854 |       -0.110656  |     -0.119365  |                     0.870911 | False           | True               | False  |
| real    | last_1y   |             0.462664 |           0.4525   |                     -1.01645 |          2.45372 |        3.2153  |                     0.761584 |       -0.0660318 |     -0.0390979 |                    -2.69338  | True            | True               | True   |
| model   | full      |             0.186155 |           0.213174 |                      2.70192 |          1.11854 |        1.27946 |                     0.160922 |       -0.195776  |     -0.121241  |                    -7.45358  | True            | True               | True   |
| model   | last_3y   |             0.345169 |           0.414582 |                      6.94131 |          1.64659 |        1.89923 |                     0.252641 |       -0.114657  |     -0.0889087 |                    -2.57482  | True            | True               | True   |
| model   | last_1y   |             0.449176 |           0.449176 |                      0       |          2.25323 |        2.25323 |                     0        |       -0.0660318 |     -0.0660318 |                     0        | True            | True               | True   |

## Integrity

| scope   |   max_reference_parity_error |   min_cash_weight |   duplicate_route_profit_days |   momentum_short_put_events |   route_cycles |   core_profit_restrikes |
|:--------|-----------------------------:|------------------:|------------------------------:|----------------------------:|---------------:|------------------------:|
| real    |                  9.97466e-17 |          0.554775 |                             0 |                           0 |             16 |                       0 |
| model   |                  9.97466e-17 |          0.386328 |                             0 |                           0 |              3 |                      11 |

## Prior Robustness Carry-forward

- Cost and relative decay selection gate: False.
- Theoretical cycle concentration gate: False.
- The concentration limit remains a disclosed residual risk; it is not repaired by this replay.

## Decision

ic_v14_final_joint_gate_failed_keep_v13

## Stability

final_joint_gate_failed
