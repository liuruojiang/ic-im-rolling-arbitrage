# MO Call 官方历史数据构建 v1 规格

冻结日期：2026-08-19（Asia/Shanghai）  
状态：数据构建；不包含策略收益；未批准实盘。

## 1. 目的

从本地已冻结复用的中金所官方月度历史压缩包中解析中证1000股指期权 Call，供后续固定1倍滚IM + 主线Put上的卖Call研究使用。不得从现有Put文件反号、复制或模拟Call。

## 2. 输入

- 原始目录：`data/ic_monthly_discount_roll_v1/cffex_raw/`；
- 只读取覆盖 2022-07-22—2026-08-14 的官方日文件 `YYYYMMDD_1.csv`；
- 合约正则：`^MO\d{4}-C-\d+$`；
- 解码函数复用 `ic_monthly_discount_roll_v1._decode_cffex_csv`；
- 全程只读原始月包，不联网、不刷新、不覆盖缓存。

## 3. 输出字段

`contract/date/open/high/low/volume/turnover/open_interest/close/settle/pre_settle/strike`。数值字段中的`--`和`null`转为缺失；不得前向填充报价、成交量或持仓量。

## 4. 强制验证

1. 与真实IM交易日一一覆盖，共986日，首尾为2022-07-22和2026-08-14；
2. `date+contract`唯一；结算价无缺失；
3. 合约均为Call且行权价可从合约代码无歧义解析；
4. 输出官方月包逐文件SHA-256、样本统计、年度统计和每日可交易链宽度；
5. 可交易定义为收盘价、成交量、持仓量均大于0，只作审计，不删除非流动行；
6. 首次输出目录不可覆盖。

## 5. 正式路径

- 数据：`data/im_mo_call_data_build_v1/cffex_mo_calls.csv`；
- 数据清单：`data/im_mo_call_data_build_v1/data_manifest.json`；
- 审计输出：`outputs/im_mo_call_data_build_v1/`。

本规格不选择期限、Delta、估值门控或卖Call规则；任何策略结论必须另建冻结版本。
