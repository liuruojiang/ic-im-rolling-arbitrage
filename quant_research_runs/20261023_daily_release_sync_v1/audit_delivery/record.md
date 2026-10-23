# 独立发送审计（2026-10-23）

范围：自动化发布 PR161 的发送分类、正式账本恢复、intent/marker 去重、SMTP 与无发送预检。读取主线登记表、冻结规范和首次记录。未改生产源码、未写正式账本、未发邮件或触发工作流。

## 结论

本次未确认新的重复发送或陈旧信号穿透问题。5 个相关测试文件隔离执行：100 passed，另 18 subtests passed，XML 为 regression.xml，退出状态为 exit.json。测试采用模拟 SMTP，不代表真实投递。

- 正常成功信号须先保存 send-intent，SMTP 步骤依赖 intent 成功；出现 intent 而无 completion marker 时，重试阻断并要求人工对账。两次日期门禁分别检查运行日和实际信号日，防假日重发前日信号。显式 correction 才能绕过。
- 同一正式工作流采用固定 concurrency group 且不取消在途任务。正式账本恢复仅接受 main 分支、指定正式日报 workflow、completed/success run 的最新未过期账本；归档路径、重复成员、CRC、链及迁移身份另有检查。
- readiness 使用 readiness-state，正式账本恢复后在副本运行；不调用 send_report，不上传正式 ledger/intent/delivered marker。SMTP probe 调用 TLS、认证、MAIL、逐收件人 RCPT、RSET；不调用 DATA/sendmail/send_message。
- 失败日报走失败正文；成功标记及账本晋级依赖成功信号与校验。源码 pin/manifest 校验失败时不能把旧结果作为成功信号投递。

## 实际回执核对与纠正

10-22 正常实际发送 run 为 **39774840955**，send job **119811477362**：北京时间 20:02:52 intent 成功、20:03:09 Send Gmail 成功、20:03:11 ledger 成功、20:03:12 delivered marker 成功。父代理确认 Gmail id `1a1637e2477558cd` 同时具有 INBOX/SENT，邮件信号日为 10-22。此为旧代码投递证据。

run **39812222968** 的 send job **119941629096** 虽为 success，但 restore、build、intent、Send Gmail、marker 均 skipped；这是已投递后的去重空跑，不能作为 SMTP 实发凭据。

新版无发送 readiness run **39892433587**，job **120215258822** 已 success。实际完成正式账本恢复、producer、29 文件 source inventory、正文构建及 SMTP 认证。10:59:24 输出 status=ok、recipients_accepted=1、message_sent=false；部分布尔字段被 GitHub secret masking 显示为 ***，不能照抄为未遮罩字段凭证。完整成功步骤及 probe 源码共同证明无 DATA 认证预检通过。上传 artifact 12475312545，日志报告 ZIP SHA-256 `2cd4804dca9d5560fd4570eee600ebf23876c6397ab151b9216dc939d80c4b06`。

## 证据边界

正式 Send Gmail 当前未传 send_report.py 已支持的 --receipt，因此不会单独产出含 Message-ID、metadata SHA 的 SMTP acceptance receipt。现有工作流 SMTP 步骤成功、delivered marker 和 Gmail INBOX 可共同核验实际送达；不能声称存在独立 SMTP receipt。此次未扩展发送行为。

新版 10-23 正常 20:00 发送尚未发生。无发送预检通过不能替代该次 SMTP/INBOX 验收，也不应为了验收重发 10-22 正常日报。
