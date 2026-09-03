# POE 1.3 持久化服务运维手册

适用构建：`v1.3-20260903-r6`
状态：用户已批准本地、Poe与GitHub/Gmail研究信号发布；不生成订单，不代表账户持仓，不替代冻结V2交易权限边界。

## 首次启用

1. 保留并验证当前v1.3-r5账本，运行离线迁移：

   ```powershell
   python -X utf8 migrate_ic_im_v1_3_r5_to_r6_state.py --old-state-dir runtime/ic_im_v1_3_r5 --new-state-dir runtime/ic_im_v1_3_r6
   ```

   迁移会先校验r5完整哈希链，再在相同核验日创建r6独立创世记录；IC和IM核心/Call/网格/动量锚点原样继承，IM新增动量Put从0仓开始。不得复制、改写或删除r5链。

2. 在Modal控制台创建Secret `poe-ic-im-v1-3`，仅填写`POE_ACCESS_KEY`；本地执行：

   ```powershell
   python -m pip install -r requirements-modal-deploy.txt
   modal setup
   modal volume create poe-ic-im-v1-3-r6-ledger
   modal deploy modal_poe_ic_im_v1_3.py
   ```

3. 把已验证的`runtime/ic_im_v1_3_r6`内容导入预先创建的Volume。服务会要求匹配的`migration_record.json`，不会在空卷静默创世。访问`/healthz`，确认`status=ok`、`strategy_revision=r6`、核验日、序号和摘要均正确。
4. GitHub/Gmail工作流必须先恢复`ic-im-v1-3-r6-ledger`；仅首次切换时允许恢复r5工件并调用上述迁移器。Poe与邮件标题必须显示r6构建号。

## 不可突破的边界

- `latest.json`和日志目录必须位于独立持久卷`ICIM_STATE_DIR`。不得手工改JSON、源码日期或聊天内容续接状态。
- Modal定时函数是唯一写入者，Web实例只读；不得并行启用服务内定时器。
- IC无Call；IM救援期限为严格晚于旧到期日的最近实际挂牌期限`rescue_next_listed`。
- 跨日补账逐交易日历史重放。指数、期货、MO及既有510500 Put必须对应待补日；只允许HTTPS，不得以查询日行情替代历史行情。
- IC/IM动量收盘确认必须有完整有效OHLCV；IM盘中累计量只允许显式占位，不能写账。

## 验证与巡检

部署前执行：

```powershell
python -X utf8 -m pytest -q test_ic_mainline_v1_3.py test_im_mainline_v1_3.py test_ic_im_mainline_v1_3.py test_poe_ic_im_mainline_v1_3_bot.py test_poe_ic_im_v1_3_state.py test_migrate_ic_im_v1_3_r5_to_r6_state.py test_run_ic_im_v1_3_github_digest.py
python -X utf8 verify_workspace.py
```

每日收盘后确认：`verified_day`推进到最近完成交易日；序号只增不减；IC/IM同日；构建号为`v1.3-20260903-r6`；IM同时记录核心Put、独立动量Put与合计；重复重试为空操作。任何缺源、单腿成功、摘要异常或日期落后都保留上一快照并停止新增/调整信号。

回滚代码时可重新部署上一镜像，但r5与r6账本必须保持独立，不得让账本倒退或交叉覆盖。
