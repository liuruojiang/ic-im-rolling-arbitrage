# 云端运行兼容修复：独立来源闭包复核

**限定修复的源码闭包核验通过，未发现新增策略或来源变更。** 本审计只做源码/清单绑定和两个直接异常对象控制，没有重跑根协调者负责的原失败节点或完整门禁。

| 核验项 | 结果 |
| --- | --- |
| 整个核心模块AST | 以compatibility_backup原字节为基线，剔除SignalTransportError唯一新增的__init__后，整个模块AST完全一致。策略、日历、价格、数量、选约及_source_failure_error失败分类均未改变。 |
| 初始化的精确范围 | 新__init__仅执行requests.RequestException.__init__(self, *args, **kwargs)。类的原RuntimeError与requests.ConnectionError两基类保持。 |
| request/response与异常身份 | 从真实源码只编译该异常类，离线控制确认response.request推导与显式request覆盖均保留对象身份，response和args保持；同一实例仍可被RuntimeError、ConnectionError及RequestException捕获。 |
| Pandas与typed证据 | requirements唯一变更为pandas>=2.1→pandas==2.3.3。typed存证codec和adapter在旧/新发布清单与当前实际文件中均逐字节相同，没有修改回放等值断言、日期/类型身份或做兼容性宽免。 |
| 冻结发布边界 | 旧v5-r2清单保留；新v5-r3清单的31执行文件中，唯一不同的执行模块是主程序。34项执行及支持文件在本审计前后均精确匹配新清单。另有验证runner变更，不包含在“核心仅初始化变更”的结论内。 |

本地对象控制运行于Python **3.14.4**。这不能证明真实Python3.11云端已修复；仍须由实际云端运行原反例与完整门禁验收。固定Pandas2.3.3是在已验证typed格式上锁定依赖，**不宣称支持Pandas3，也不放宽codec合同**。

`verify_source_closure.py`实际退出码0，精确新旧manifest SHA、整模块AST哈希及前后绑定见本目录 `summary.json`、`binding_before.json`和`binding_after.json`。未导入完整生产bot、联网、发送、写正式账本或修改任何产品/旧证据。
