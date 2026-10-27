# A 端集成交接复核（2026-10-27）

本目录归档 A 在 B 的看板代码合入后实际运行、随后再次复核的结果。受测代码为 `41a2357e6155f34997dd627b3225714f723bde20`；Windows/WSL2 两个最小演示 manifest 均记录 `working_tree_dirty=false`，并保存实际源码及二进制哈希。

归档不是重新采样。报告中的 run_id、时间、环境、断言和结果保持原值；本次只整理文档、核对代码关系、复核已有结果并复制证据。原始目录在 `results/local-*`，仍保留完整夹具；本目录不包含夹具或凭证。

## 文件与来源

| 文件 | 内容 |
| --- | --- |
| [integration-checks.json](integration-checks.json) | 源码关系、会话测试结果、再次复核结果、看板观测及归档文件索引 |
| [windows/minimal_demo.json](windows/minimal_demo.json) | Windows 真 Rust 最小流程，13 项检查、5 次拒绝、17 条事件 |
| [windows/run_manifest.json](windows/run_manifest.json) | Windows Python 3.14.5、Rust/Cargo 1.98.1、源码/二进制/配置摘要 |
| [windows/demo_events.jsonl](windows/demo_events.jsonl) | Windows 运行导出的审计事件 |
| [wsl2/minimal_demo.json](wsl2/minimal_demo.json) | WSL2 真 Rust 最小流程，同样逐条核验文件、租约及审计 |
| [wsl2/run_manifest.json](wsl2/run_manifest.json) | Ubuntu WSL2 / Python 3.12.3、Rust/Cargo 1.98.1 及实际哈希 |
| [wsl2/demo_events.jsonl](wsl2/demo_events.jsonl) | WSL2 运行导出的审计事件 |
| [dashboard/dashboard_demo.json](dashboard/dashboard_demo.json) | 持续展示流程报告，完成后仍提供只读查询 |
| [dashboard/dashboard_events.jsonl](dashboard/dashboard_events.jsonl) | 持续展示的 17 条事件，与归档时代理查询逐条一致 |

两个最小演示分别来自 `local-20261027T070734Z-183d9430`、`local-20261027T070851Z-620f487b`，持续看板来自 `local-dashboard-20261027T070816Z-3395b137`。归档时两个保留夹具再次运行 `python -m demo.verify results/<run_id>`，均返回 `MINIMAL_VERIFY_PASS`。

源码清单覆盖原生成器指定的 Cargo 配置、crates、integrations 和 demo 文件；归档时逐文件核对当前内容哈希。它不是所有仓库文件的全量快照，前端会话检查另行记录。历史 Rust 全量检查引用 [compatibility-followup.json](../compatibility-followup.json)，因当前 crates 树完全相同而沿用，不记录为新执行。

## 检查结果与边界

- 前一轮会话实跑前端 25 项测试、lint 和含类型检查的 build，全部通过。
- 前一轮会话实跑 Python：Windows 49 项通过/1 项符号链接测试跳过；WSL2 50 项全部通过。
- Windows/WSL2 最小演示均 `MINIMAL_DEMO_PASS`，并检查服务已停止、文件变化和租约消费。
- 持续展示为 `DASHBOARD_FLOW_PASS`；归档时 4173 和 5173 健康查询返回 200，1 个任务、3 次成功、5 次策略拒绝、0 次执行/输入失败。实例 ID 和任务 ID 是该次观测快照。

`integration-checks.json` 明确把开发会话的命令结果与归档时的独立复核分开：原始 npm/unittest 全量控制台日志未保存在本目录，不把结果索引冒充原始命令日志。B 的完整双模式浏览器记录与截图仍在 [results/member-b/dashboard](../../member-b/dashboard/README.md)；A 此轮没有重做整套浏览器矩阵。

文件索引的 `sha256_utf8_lf` 对 CRLF 转 LF 后的 UTF-8 文本求 SHA-256，以兼容 Git 在不同系统上的文本换行转换；文件内的源码、二进制、文件副作用及审计哈希沿用原始定义。保留结果不是签名或防篡改证明。

本目录不包含完整 34 场景黑盒 CSV、HTTP 性能/权限窗口/RSS 样本，也不表示最终干净环境或远程 CI 已通过。重新完整复现需要运行 [demo 入口](../../../demo/README.md) 生成新夹具；只复制这三个导出文件不足以运行需要文件副作用证据的 demo.verify。

本次剩余分工见 [A 交接](../../../docs/member-a-handoff.md) 和 [验证报告](../../../docs/test-report.md)。
