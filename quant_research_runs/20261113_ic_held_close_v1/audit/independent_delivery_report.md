# 独立交付与诊断保留对抗审计

审计者：audit_delivery。范围仅为本轮失败诊断产物、邮件渲染与原正常发送边界；未修改产品文件，未请求行情，未发送邮件，未写正式账本。

## 结论

最终独立用例32项通过，失败/错误/跳过均0，退出码0。6个绑定文件在测试前后SHA-256相同。两项P2问题已修复且原反例复验通过；本范围无剩余已观察阻断项。完整源码发布、行情整包、远端CI和真实补发需要主协调另行验收，本报告不能替代。

## 发现与关闭

1. 原候选接受2099年的诊断生成时刻，即使实际运行在2026年。当前校验拒绝未来时刻。
2. 原候选直接比较时间戳自身日期，接受`2026-11-13T00:15:00+14:00`，该时刻在北京时间仍为11月12日。当前先转换Asia/Shanghai再核对目标行情日。

同时核验真正的历史警告可以晚于目标日生成，及源时区日期为前一天、北京时间已为目标日的合法情形不被误拒。

## 对抗覆盖

- 原生producer直接写失败产物，再交给consumer校验；status保持failed，无signals、sequence或digest。
- Markdown多重代码围栏、HTML脚本/标签、Unicode换行和伪造正式标题均作为原文展示；不转成机器目标。
- 固定文件名、UNC/ADS/空字节/相对路径别名、重复或冲突标题字段、内容篡改、文件过大、新旧release错配均拒绝。
- 失败payload即使额外塞入伪造signals，也不进入成功渲染，不能产生正常delivery marker。
- 正常去重分类、delivery gate、marker三份控制脚本逐字节保持。到审计时点，正常daily workflow与基线仅expected-source-release字面值不同，原intent/SMTP/完成marker条件未改。

## 证据

- `final_receipt.json`：完整命令、32项JUnit属性、6文件前后SHA。
- `final.xml`、`final.log`：最终真实执行回执。
- `test_independent_delivery_adversaries.py`：独立设计测试。
- `independent_r2.xml`：两项P2原红回执（25通过、2失败、1系统不支持原生symlink）。
- `static_scope_receipt.json`：AST与正常发送控制原字节审计。

当前producer SHA为`a95ff29826b0da42cec4feed05ed32bd2fc4c27819894e4ff92543b798fffbe7`，renderer SHA为`3f925201b8ddc63a7faa298048555d50756a7ba195aa4963e7ce01dfbbc7632a`。

## 环境与证据限制

首次测试因系统TEMP访问拒绝产生28项setup错误，未当作产品失败。随后使用独立D盘临时目录。中间新增历史正控有一次审计自身LF/CRLF预期不一致，实际产品已接受合法报告；审计按原字节比对修正后通过，旧回执保留。

本账户不能创建原生symlink。最终用例通过局部mock触发真实`path.is_symlink()`拒绝分支，属于该分支的单元验证，不是原生Windows reparse-point端到端证据。报告不宣称完成后者。
