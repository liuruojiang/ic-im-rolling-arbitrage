# 中断审计证据独立复核

结论：**BLOCKED_PROTECTED_DATA_CHANGED_AND_DELIVERY_INCOMPLETE**。代码与历史测试证据匹配；旧报告的“正式 runtime 全部未变”声明不能成立，最终交付文件未落地。没有修改生产文件或旧审计证据。

## 已验证

- `cumulative_02`：3333 项，0 失败/错误/跳过；1178 个绑定文件仍匹配；22 个既有排除项与登记表一致。
- `daily_gate_03`：1193 项，0 失败/错误/跳过；175 个绑定文件仍匹配；5 个既有排除项与发布清单一致。
- 六组独立审计合计 148 项：XML 用例身份与 collection/executed 一致，退出码为 0。存在 artifact 绑定的回执全部匹配；独立源码快照和报告中的源码哈希均与当前源码一致。29 文件发布 manifest 一致。
- `real_recovery_02`：保存的 2026-10-22 IC/IM 包，其规范化 body SHA-256 和所有 typed/raw blob 通过生产 `_load_one` 验证；恢复驱动和生产源码哈希匹配。两个正常恢复和三个原子拒绝场景的历史回执有效。
- 当前正式 `StateStore.load_latest()` 只读校验通过：seq0 至 seq20 哈希链接、逐交易日推进及 latest 指向一致。未调用 initialize/append。

## 阻塞与纠正

1. 基线 16916 文件中受保护的 `runtime/ic_im_v1_4_r1/latest.json` 已变；新增 `runtime/ic_im_v1_4_r1/journal/000020-2026-10-22.json`。基线 latest 为 seq19，当前 latest 与新增 journal20 字节相同。原有正式输出、冻结文档、旧审计被保护的已存在文件未改变。
2. `outputs/` 新增 12 个 `__pycache__/*.pyc`，精确路径见 `verification.json`，未删除。它们不是历史收益数据改写，但“无新增 outputs 文件”同样不能通过。
3. 当前正式 seq20 的完整 products 与本轮 `real_recovery_02/automatic_recovery/recovered_record.json` 相同；signals 排除三个计算时钟后仍有大量字段差异。不能将此正式 seq20 冒称为本轮隔离恢复产物或新源码发布证据。逐字段差异保留在 JSON。
4. 旧目录缺 `final_verification.json`、`commit_repairs.ps1`、`verify_staged.py`、`commit_hashes.json`、`commit_paths.nul`、`commit_preparation.json` 六件交付物。旧 record 已引用其中未存在的文件，须由新的接续报告澄清；不可补造旧时间点 PASS。

## 写入归属边界

latest 与 journal20 的文件时间为北京时间 2026-10-23 14:54:51；内容 `updated_at` 为 2026-10-22 20:02:41，source 为 `automatic_close_replay`。该文件变化早于恢复采集器 14:57:41 的启动，采集器首回执已经读到 seq20。时间线支持“采集器启动前发生变更”，不足以确定写入者，也不足以单凭内嵌时间确认来自远端。未回滚或重写账本。

验证脚本 `verify_evidence.py` 可复核原始 XML、哈希、排除项、真实包和只读账本。机器结果为 `verification.json`。所有写入仅限本新目录；未发送消息/邮件、无网络调用、未修改生产源码或冻结输出。
