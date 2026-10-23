# POE 1.2 持久化服务运维手册

适用构建：`v1.2-20260902-r18`
状态：研究候选查询面；不生成订单，不代表账户持仓，不替代冻结V2主线。

## 不可突破的运行边界

- Poe长期入口是`poe_ic_im_v1_2_server.py`；`poe_ic_im_mainline_v1_2_bot.py`单文件模式只用于本地兼容测试。
- `latest.json`及日志目录必须位于持久卷`ICIM_STATE_DIR`。不得通过改源码日期、删除账本、手工改JSON或复制聊天内容续接状态。
- Modal部署中，定时函数是唯一写入者；Web实例只读。不得同时启用服务内定时器和Modal定时函数。
- IC与IM必须同一交易日整体成功后才写入。缺源、跳日、摘要错误或并发冲突均保持旧快照并停止新增/调整信号。
- 跨日漏跑补账必须进入显式历史重放模式：指数、期货、MO和既有510500 Put都要与待补交易日同日；不得用查询日实时行情回拨时钟伪造历史状态。
- 当待补日恰好是查询当日且该交易日已经完成时，允许走同日完整收盘链写账，以规避历史接口晚一个交易日入库；该例外不得用于更早交易日，IC/IM仍须同日、完整、收盘确认后才可写入。
- IC始终禁止Call；IM救援期限仍按`rescue_next_listed`选择严格晚于旧期限的最近实际挂牌到期日。

## 首次部署

1. 在Modal控制台创建Secret `poe-ic-im-v1-2`，只在网页中填写`POE_ACCESS_KEY`。
2. 本地执行：

   ```powershell
   python -m pip install -r requirements-modal-deploy.txt
   modal setup
   modal volume create poe-ic-im-v1-2-ledger
   modal deploy modal_poe_ic_im_v1_2.py
   ```

3. 把Poe Server Bot的Server URL指向部署返回的Web地址根路径。
4. 访问`/healthz`，确认`status=ok`，并记录`verified_day`、`sequence`和`digest_prefix`。
5. 在Poe查询“信号”，标题必须包含当前构建号；否则仍连接旧服务。

环境变量以`.env.schema`为准：`POE_ACCESS_KEY`必须由密钥管理注入；`ICIM_STATE_DIR`必须指向持久卷；`PORT`仅供容器入口使用。Modal Web实例由部署文件自动设置`ICIM_DISABLE_INTERNAL_REFRESH=1`。

## 每日巡检

工作日北京时间19:00后检查：

1. `/healthz`为`ok`，且`verified_day`已推进到最近完成的官方交易日。
2. `sequence`只递增，不回退；`digest_prefix`非空。
3. Poe“信号”中的IC与IM核验日期一致，当前仓位与下一交易日目标分开显示。
4. 若账本当天已经成功，后续重试应为空操作，不应生成第二条同日序号。
5. 至少演练一次“锚点落后两日”的恢复测试，确认日志按交易日逐日推进，而不是直接跳到最新日。

节假日不要求日期推进；2027年及以后必须先补入官方休市表并通过回归测试，否则服务按设计停止续接。

## 故障处置

- `status=degraded`或日期落后：先保留现场，查看最近一次定时任务日志和数据源失败摘要；允许后续定时重试，不得清空Volume。
- 中金所历史月包先走HTTPS；若交易所HTTPS握手异常，程序只回退到同一官方主机的HTTP下载地址，并继续执行ZIP安全检查、字段校验和逐腿完整性检查。两个传输均失败时保持fail-closed，不推进账本。
- `digest`、序号或前序链校验失败：立即停止写入，保全整个持久卷副本，再用最后一份已验证备份恢复；不得跳过损坏记录继续写。
- 只有一个品种成功：不写账。修复缺失数据源后从上一共同核验日整体重试。
- Poe输出不是当前构建号：核对Server URL及Modal最新部署，不修改账本。
- 密钥失效：只在Modal控制台轮换Secret并重新部署；不得把密钥放入命令、日志或Git。

任何需要人工重建状态、跳过交易日或修改已核验记录的情况，都应视为新审计任务，而不是普通运维恢复。

## 升级与回滚

升级前执行：

```powershell
python -m pytest -q test_poe_ic_im_mainline_v1_2_bot.py test_poe_ic_im_v1_2_state.py
python verify_workspace.py
```

然后备份持久卷，并部署新镜像。升级只能向前兼容现有`schema_version`和哈希链；如需迁移格式，必须用新版本预注册并提供离线迁移校验。

代码回滚可以重新部署上一Git提交，但不得回滚或覆盖已经合法前进的持久账本。回滚后再次核对`/healthz`与Poe构建号。
