# 成员 A：v2 验证与交接

## 受测版本

双项目重放与性能实验的受测提交为 `77d3c8e28540c8c683d8179507730c4db652f56a`，采集时工作区干净。此前 Windows 和 WSL 的 Rust 测试针对 `f1245a5`；`77d3c8e` 只增加独立证据验证器和文档，未改变 Rust 或 Cargo 源码。此报告及归档是在受测提交之后增加的资料，可用 `--source-match .` 核验当前 Rust 和 Cargo 文件与受测提交一致。

## 已执行的检查

- Windows：`cargo fmt --all -- --check`、全目标全 feature 的 Clippy（`-D warnings`）、workspace 默认及全部 feature 测试、文档测试与文档构建、Release 构建通过。Rust 工具链为 `rustc 1.98.1`。
- WSL Ubuntu 24.04：workspace 全部 feature 测试通过，包含实际符号链接测试；Windows 测试包含实际目录 junction。
- 使用独立 `target/v2` Release 后端：Python integrations 16 项通过；proxy 测试运行 13 项，其中 1 项因 Windows 符号链接权限跳过；demo 45 项通过。
- 现有前端 lint、31 项测试及 build 通过。这是现有界面的检查，不代表 v2 管理界面已经完成。
- 双项目离线 MCP 重放成功，覆盖各 3 个文件、拒绝操作、任务阶段及 API/磁盘审计一致性。该重放使用官方 rmcp 客户端和独立 stdio 子进程，不调用模型。

## 本机性能观察

实验在 Windows 上针对相同的 Rust Broker、文件和同步审计路径，比较直接 HTTP 与 MCP stdio 的读取。4 KiB 合成文件、并发 1，每种方式预热 60 次，随后 3 轮各 100 次，共每种方式 300 个正式样本。分位数使用 nearest-rank。

| 路径 | P50 | P95 | P99 |
| --- | ---: | ---: | ---: |
| 直接 HTTP | 4.3103 ms | 5.2609 ms | 6.0035 ms |
| MCP stdio | 5.6314 ms | 6.8714 ms | 8.2913 ms |

这是单机端到端观察；差异包含 JSON-RPC、进程通信和带行号输出等处理，不能称为纯协议开销或统计显著性结论，也不包含模型调用、真实网络、CPU 或 RSS。

## 证据和后续联调

原始证据位于 `results/member-a/v2-77d3c8e/`，包括合成项目、请求样本、API 和磁盘审计、汇总以及 `SHA256SUMS`。未归档模拟 `.env` 文件。可以在仓库根目录执行：

```powershell
python bench/verify_mcp_v2.py results/member-a/v2-77d3c8e/replay --self-test --source-match .
python bench/verify_mcp_v2.py results/member-a/v2-77d3c8e/benchmark --self-test --source-match .
```

B 仍需完成原生目录选择器、v2 管理界面、真实模型审查和录屏，并依照 `docs/v2-a-contract.md` 对接。离线重放和测试通过不证明模型建议正确，也不表示整个 Agent 已被操作系统沙箱隔离。
