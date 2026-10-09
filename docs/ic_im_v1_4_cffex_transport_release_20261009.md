# IC/IM v1.4-r1 CFFEX 传输与失败诊断修复（2026-10-09）

本次为用户批准的研究日报脚本修复及远端同步，不改变 fix11 策略参数、身份映射、生效日或历史账本。

## 修复内容

1. 中金所月包连接/超时保留 requests 传输异常类型；流内 ProtocolError 转成的 ChunkedEncodingError 明确归入连接异常，沿用每个失败品种最多一次的有限重试。
2. 流式 next 前后及最终组装时检查共享联网预算。超时字节不得返回或进入缓存；HTTP、解码、长度、格式及数据校验错误仍失败关闭。该检查不等于操作系统级强制准点中止阻塞读取。
3. 月包响应在 finally 关闭，成功释放连接、异常关闭未完成的流。
4. 协调器每轮清除旧诊断并保存本轮完整逐腿查询及真实回放日。正式 runner 失败时自动写 result.json、failure.txt、diagnostic_report.md，禁止残留旧诊断。

## 独立对抗验证

- 传输独立基线 19 项中 6 项失败，修复后 19 passed；正式 runner 独立复测 10 passed。
- 既有累计 21 文件加新测试 `test_adversarial_cffex_transport_20261009.py`、`test_adversarial_cffex_close_runner_20261009.py`，共 **23 文件、400 passed、0 failed/errors/skipped**。北京时间 16:54:24.819–16:54:36.140，外层 11.319 秒、pytest 10.02 秒、exit0，60 秒硬限。
- 命令形态为 `python -B -X utf8 -m pytest -q -p no:cacheprovider <累计23文件>`，完整既有清单见运维手册。环境副本设置新建隔离 ICIM_STATE_DIR、ICIM_REQUIRE_MIGRATION=0、PYTHONDONTWRITEBYTECODE=1；正式 runner 必须保持生产迁移开关 1。
- 故障、ZIP、时钟和测试链首明确为合成夹具，执行真实 requests.Response、正式下载/重试/协调器及 main。覆盖成功组件不重跑、错日不重试、二次失败传播、嵌套预算不延长、当前月不取旧缓存、无部分产品/日写链、诊断原文/日期及旧文件隔离。

## 状态与版本边界

本地修复前后全部 12 个生产账本业务 JSON 相同，10 条 journal 全链和迁移证明一致，仍为 seq9、verified_day=2026-09-30、digest=f279667c5fd2cb39c0410ab62a5d026bdde69582f6968a417c17d3d47621f450。没有写回旧账本或改动冻结规格/首次输出。

本地 10/09 原失败首先发生在 10/08 historical_replay；失败 result 的 market_date=10/09 是最终报告目标，不能作为已取得10/09完整行情的证据。离线回归不证明真实源恢复、正式补账、Gmail发送或收件箱投递；远端发布与无发送 readiness 按实际运行证据另外登记。

BUILD_ID=`v1.4-20261008-r1-ic-csi500-abs40-fix11`，RULE_REVISION=`ic_im_v1_4_ic_csi500_ma105_w16_abs40_static_20261008_v1`，DELIVERY_REVISION=`20261008-v14-ic-csi500-abs40-fix11`。三者与10/08前向边界不变。

备份为 `.codex_backups/20261009_164450`、`.codex_backups/20261009_164927`；原始基线、完整命令、源 SHA、日志、独立报告与最终只读回读在本地 `runs/20261009_cffex_transport_adversarial/`。回滚只回滚代码，不降级/覆盖业务链；实际发布 SHA 以运维手册及云端固定 pin 为准。
