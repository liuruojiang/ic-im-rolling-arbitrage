# IC / IM v1.4-r1 本地正式研究信号发布记录

日期：2026-09-17  
状态：本地正式研究信号版本已建立；外部发布和下单未执行。

## 用户决策

用户明确接受IC回撤增加0.87个百分点的收益/风险取舍，并接受IM q3只有3个真实周期及67.7%单周期贡献集中度，要求按原完整方案升级1.4，而不是只升级q3。

## 版本身份

- strategy version：`1.4`
- revision：`r1`
- schema：`4`
- build：`v1.4-20260917-r1-coreput3x-fixedshort95`
- rule revision：`ic_im_v1_4_coreput3x_fixed_short95_20260917_v1`
- effective signal date：`2026-09-18`

## 本地账本迁移

迁移前先按原v1.3规则把本地r7账本从2026-09-15、sequence 8补至2026-09-16、sequence 9；父digest为`11246f587b8df106ea669bf5f25aaa985f2e0ceff3c10c54dea0671faca0fd0e`。

v1.4创世账本：2026-09-16、sequence 0、digest `d512f89c670ba5b9b10226bda01c27f95ddc52bd7460f900c39ef343a0c74b19`。迁移证明位于`runtime/ic_im_v1_4_r1/migration_record.json`。

存量核心Put没有被赋予虚构成本；IC/IM的三倍兑现资格均为false，等待下一次正常月度重置记录新入场权利金。

## 验证

- 新模块编译通过。
- v1.4纯策略、状态边界与迁移测试10项通过；其中到期结算必须先确认价外失效或被行权，禁止把所有到期腿一律视为被行权；恢复期货保留0.5倍经济暴露直至整轮含成本回本。
- v1.4服务以`ICIM_REQUIRE_MIGRATION=1`启动验证通过，健康状态`ok`，账本日期与最近已完成交易日同为2026-09-16。
- 176项既有v1.3/季度换仓/状态/日报回归与10项v1.4新增测试全部通过，共186项；仅有第三方Pydantic弃用警告。
- 从迁移账本安装运行时锚点后的2026-09-17盘中只读实测，IC与IM均返回strategy `1.4/r1`、正确build及`LEGACY_V13_FORWARD_ONLY`；盘中`close_confirmed=false`，未写账本。
- 正式规则首个生效信号日为2026-09-18；截至本记录日期尚未发生，不能把预演或盘中快照冒充首个已验证正式信号。

## 未执行

未推送GitHub，未修改GitHub Actions，未发送Gmail，未部署Poe/Modal，未切换Server URL，未创建自动或人工下单指令。
