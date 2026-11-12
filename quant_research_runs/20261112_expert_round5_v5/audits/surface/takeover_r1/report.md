# 第五轮 surface 接续审计结论

状态：已确认的 1 项 P2 在候选 r2 上关闭，未确认其它产品缺陷。只在本审计的新目录保存文件，未修改产品源码或旧证据。

## 实际发现与关闭

`ic_im_evidence_adapter_v1.py::_keyed.collect_listing_result` 在 SSE 挂牌请求失败而 Sina 成功时，曾用公开月份 list 重建 `failures=[]` 的成功封套。公共查询缓存实际保留 SSE Timeout，归档值却丢失该来源失败；采集包仍显示 complete。中断前留下的原反例现已独立执行，`pending_r1` 为 9 通过、该 1 失败，213 项源绑定前后未变。

根协调者修订 adapter 后，原反例通过。新增独立采集与公共查询两种作用域，各验证 SSE Timeout、结构错误、403 后 Sina 成功时保留完整失败分类，且实时与回放结果一致、回放不联网。独立采集缺省 cache 的异常退出恢复、已存证双消费者重复消费、原始 blob 删除/篡改拒绝也通过。另以公开 list 与 cache 的月份不一致、以及成功 list 与可验证不可用 outcome 冲突两项控制验证硬拒绝；失败存证不完整且 cache 恢复。

| 验证 | 通过 | 失败/错误/跳过 | 证据 |
| --- | ---: | --- | --- |
| r2 受影响补验及原反例 | 15 | 0/0/0 | `affected_r2/regression.xml` |
| r2 公开值/来源结果不一致控制 | 2 | 0/0/0 | `mismatch_r2/regression.xml` |
| r1 文字入口后续控制，按未变依赖复用 | 9 | 0/0/0 | `pending_r1/regression.xml`，另有原 P2 红例 |

r2 共 17 项受影响验收通过。每进程 60 秒 watchdog、独立系统 TEMP、隔离账本和禁止外网均开启；XML 实际 testcase 节点、退出码、源前后哈希与 network_guard 已核，均无外部 HTTP/socket 尝试。不把这些与原完整库存相加。

## 原 surface 词法失败的处理

原 `surface/r1/run` 的 55 通过、1 失败中，失败用例错误期待显式绩效查询忽略未消费月份。冻结合同明确保留显式绩效的严格日期解析，真实入口拒绝该输入是正确行为。旧原文和回执均保留；9 项后续控制分别验证正常显式绩效优先级、三种月份词在显式绩效中仍严格拒绝、旧坏小数/年份拒绝，以及此前真实 snapshot 回归。

r1→r2 主程序字节完全相同；adapter 的变化仅影响 capture 的结果收集。故文字入口 9 项及其余未受影响的原 surface 检查复用，未为文案或哈希变化重跑全部用例。capture 双消费者和原始 blob 拒绝已针对受影响范围实际重验。

## 精确绑定与限制

最终绑定 `candidate_source_pin_r2.json`，SHA-256 `05ae4d5a238ae619b90cef7755085a1ec55043da32bba8571ad95601944dc0b6`；213 项源文件在两次 r2 检查前后完全一致。主程序 SHA-256 `5db134c87075e9241ac380fa59d07bfb83d275e040ddbba387b7fdc1dc05deef`，adapter SHA-256 `c98327b6bd0cf773a17a649dc517dbec43b8eaa751f3fc41381b9563eedbeaaf`。实际节点和工件哈希见 `summary.json`。

新政策日期、源错误和行情结构均为受控素材，不冒称 11 月 13 日真实市场验证。最终全库存回归由根协调者独占执行；本角色未部署、发送邮件、推进正式账本或下单。
