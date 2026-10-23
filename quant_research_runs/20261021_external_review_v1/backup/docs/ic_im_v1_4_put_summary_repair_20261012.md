# IC/IM Put 摘要字段修复（2026-10-12）

## 故障与范围

真实无发送预检 run `38236987937` 的 JSON 中，IM 独立生命周期核心 Put 为合约空、数量 0，普通保护核心 Put 目标为 `MO2612-P-7600`、标准化数量 1.5。原报告摘要用普通保护合约补空生命周期合约，拼出了“MO2612-P-7600、数量 0”的错误组合。详细表和原始 JSON 没有该串线。

报告现在分别读取并显示生命周期合约/数量与普通保护核心合约/数量。合约缺失时说明缺失或当前无合约，不用其他类别回填。IC 同样分列。仅修正呈现，不修改计算、策略参数、fix11 身份、result.json、既有账本、发送记录或去重状态。

## 验证

- 使用保存的 10/12 IC、IM 真实字段投影添加可执行反例，定向 9 项通过，4.429 秒、退出码 0。
- 原累计 24 文件专项 416 项通过，零失败/错误/跳过；2026-10-12 18:45:50 至 18:46:02，外层 12.152 秒、退出码 0，60 秒硬限。独立测试状态，`ICIM_REQUIRE_MIGRATION=0`，`-B`、`no:cacheprovider`；正式生产开关仍为 1。
- 只用已保存的成功 result.json 重生成报告，不重复 producer、网络取数、账本操作或 SMTP。源 JSON SHA-256 保持 `ba517d443e568432b7f93a31affd485776f6486b19ac674a9829ed805ec86c0c`；修订 Markdown SHA-256 为 `3f63db8ffd72e7817af4dff54468e5cd3901911d8d6462b0b831523b9badb7d2`。
- canonical 账本仍为 seq12、2026-10-12、digest `6bfb74ddb3d43b783e3cde7e9ee61119619f1d4369509a8384e42bad107b0bf4`。预检的独立 seq12 digest 不替换 canonical。

源码备份在 `.codex_backups/20261012_184238`；完整命令、start/end、日志、前后报告 diff 在 `D:\Codex\home\automations\ic-im\runs\20261012\recovery_review\report_put_summary_fix_20261012`。修复 runner SHA-256 为 `99c50c35b2bc51fe15b50fc5bb8784b5db4657b97b3f94fc18b2c986ee133b7e`，对应测试为 `2a54822f7911d50a5c9998ed3049336a2708724169a68893ba904ebc7edbd7d7`。

此前 Tencent 修复与真实行情验证见 `ic_im_v1_4_tencent_history_repair_20261012.md`。无发送预检只证明当次生成和 TLS/认证/信封接受，`message_sent=false`；本报告修订不等于重新发送或收件箱验收。
