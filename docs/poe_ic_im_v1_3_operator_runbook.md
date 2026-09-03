# POE 1.3 持久化服务运维手册

适用构建：`v1.3-20260903-r5`  
状态：研究候选查询面；不生成订单，不代表账户持仓，不替代冻结V2主线。

## 首次启用

1. 保留并验证v1.2账本，运行离线迁移：

   ```powershell
   python -X utf8 migrate_ic_im_v1_2_to_v1_3_state.py --old-state-dir runtime/ic_im_v1_2 --new-state-dir runtime/ic_im_v1_3
   ```

   迁移会新建v1.3 r5账本并按IC/IM两腿新动量规则逐日重放；不得复制、改写或删除v1.2链。r1至r4账本必须另目录只读归档，不能作为r5父账本。

2. 在Modal控制台创建Secret `poe-ic-im-v1-3`，仅填写`POE_ACCESS_KEY`；本地执行：

   ```powershell
   python -m pip install -r requirements-modal-deploy.txt
   modal setup
   modal volume create poe-ic-im-v1-3-ledger
   modal deploy modal_poe_ic_im_v1_3.py
   ```

3. 把已验证的`runtime/ic_im_v1_3`内容导入预先创建的Volume。服务会要求匹配的`migration_record.json`，不会在空卷静默创世。访问`/healthz`，确认`status=ok`、`strategy_revision=r5`、核验日、序号和摘要均正确。
4. 只有在用户再次明确确认切换后，才把Poe Server Bot URL或自动化工作流指向v1.3；切换前v1.2继续原样运行。

## 不可突破的边界

- `latest.json`和日志目录必须位于独立持久卷`ICIM_STATE_DIR`。不得手工改JSON、源码日期或聊天内容续接状态。
- Modal定时函数是唯一写入者，Web实例只读；不得并行启用服务内定时器。
- IC无Call；IM救援期限为严格晚于旧到期日的最近实际挂牌期限`rescue_next_listed`。
- 跨日补账逐交易日历史重放。指数、期货、MO及既有510500 Put必须对应待补日；只允许HTTPS，不得以查询日行情替代历史行情。
- IC/IM动量收盘确认必须有完整有效OHLCV；IM盘中累计量只允许显式占位，不能写账。

## 验证与巡检

部署前执行：

```powershell
python -X utf8 -m pytest -q test_ic_mainline_v1_3.py test_im_mainline_v1_3.py test_ic_im_mainline_v1_3.py test_poe_ic_im_mainline_v1_3_bot.py test_poe_ic_im_v1_3_state.py test_run_ic_im_v1_3_github_digest.py
python -X utf8 verify_workspace.py
```

每日收盘后确认：`verified_day`推进到最近完成交易日；序号只增不减；IC/IM同日；构建号为`v1.3-20260903-r5`；重复重试为空操作。任何缺源、单腿成功、摘要异常或日期落后都保留上一快照并停止新增/调整信号。

回滚代码时可重新部署上一镜像，但不得让v1.3账本倒退，也不得用v1.2账本覆盖v1.3。切回v1.2服务只读其原有独立账本。
