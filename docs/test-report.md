# 成员 A 验证报告

更新日期：2026-10-27（Asia/Shanghai）。本报告记录 A 的后端验证及其后的集成复核。以下按受测版本区分证据；B 的黑盒/HTTP 和 A 的权限窗口/RSS 已完成实测，录屏、统一版本及最终干净环境验收待完成。历史段落保留当时状态，不视为最新任务列表。

## 2026-10-27 网页现场任务入口

真实 DeepSeek 补充（`d6a9223` / `c1cd5f6`）：按用户指定的模型和官方地址实际完成自定义文件任务，6 轮响应、8 条执行记录、11 条审计事件、累计 7767 Token，文件与审计核验通过。前端 31 项及 lint/build 通过；WSL2 客户端 16/demo 43/代理 13 项全过，之后增加的两项模型边界回归包含在 Windows/WSL2 各 10 项专项通过记录中。自动回归使用可控模型回复并调用真实 Rust，区别于这次真实 API 调用；不声称完成目标程序编译/运行或任意任务语义验证。配置、限制和真实实跑范围见 [模型说明](deepseek-agent.md)。

另有后续交互展示补充：`30711b0` / `4cd2680` 实现网页现场启动固定 Agent 任务。前端 29 项、lint/build 及 WSL2 demo 38 项/代理 13 项均通过；浏览器实点启动、暂停/继续和最终 13/13 文件及审计核验通过。此功能不改变下述已归档性能结果的原始受测版本，也未将新交互接口记作历史只读浏览器验收的一部分。入口和限制见 [演示说明](../demo/README.md)。

## 2026-10-27 权限窗口与 RSS 补充实测

受测提交为 `44489cb826042f734be03dc9a563756feced1f06`，产品 Rust 树仍为 `0f34e6290f7819a0a8ebd2086e1c5a0675be60c6`。WSL2 正式权限实验得到 PERMISSION_PASS：三轮四场景、24 个独立实例、30 区间及 12 组比较。完整黑盒负载 RSS 得到 RSS_PASS：34/34、0 跳过、35 个进程、4806 个样本，观测峰值 5372 KiB。

验证器已检查现场夹具/审计，并对公开导出离线重算。WSL2 中 bench 首轮 22 项、后续加强后的权限窗口 8 项/RSS 5 项及 demo 33 项均通过；release 产品/实验程序构建通过。两份 manifest 原样保留 dirty=true，Git 状态查询超时及 151 个源码条目匹配 Git blob 的核对说明见 [实测报告](member-a-permission-rss-report.md) 与 [归档](../results/member-a/permission-rss/wsl2-44489cb/README.md)。这些是本地实测，不代替最终干净环境和远程 CI 核验。

B 的先行结果分别见 [34 场景报告](member-b-blackbox-report.md) 和 [HTTP 配对报告](member-b-http-bench-report.md)，各自保留原始受测版本。下方 `41a2357` 及更早记录不改写为本轮重测结果。

## 2026-10-27 集成复核（较早记录）

核对基点为 `41a2357e6155f34997dd627b3225714f723bde20`。该版本的 `crates/` 树为 `0f34e6290f7819a0a8ebd2086e1c5a0675be60c6`，与下节 `a3f334b` 的验证树完全相同；`crates/core` 树也与最初纯策略采样版本相同。本次文档收尾核对版本关系并独立复核保留的 Demo 结果，没有重新执行整套 Rust 门禁或生成新性能样本。

| 检查范围 | 集成版本实际结果与边界 |
| --- | --- |
| 接口联调 | B 已于 2026-10-27 确认当前客户端、代理和页面消费的响应形状；契约、设计说明及 OpenAPI 已同步 |
| 前端 | 前一轮集成复跑 25 项测试、lint 及含类型检查的 build 全部通过；Node.js 24.16.0、npm 11.13.0 |
| Windows Python | Python 3.14.5；客户端 13、代理 12、demo/文档 24 项通过，共 49 项；代理外部符号链接测试因 OS 权限跳过 1 项 |
| WSL2 Python | Ubuntu 24.04 / Python 3.12.3；客户端 13、代理 13、demo/文档 24 项全部通过，共 50 项，包含符号链接复验 |
| release 与最小演示 | Windows/WSL2 均成功构建 Rust release，实跑 MINIMAL_DEMO_PASS；两套结果均记录干净的 41a2357 代码，13 个流程检查、8 次工具调用、5 次拒绝、17 条审计事件 |
| 保留结果复核 | 文档收尾时再次对上述两套独立夹具执行 demo.verify，均得到 MINIMAL_VERIFY_PASS；核对文件、租约及 API/磁盘事件一致性 |
| 持续看板 | 公开 dist 入口完成 DASHBOARD_FLOW_PASS 并保持服务；4173/5173 查询均返回 200，实际页面显示 1 个任务、3 次成功、5 次拒绝、0 次执行/输入失败 |
| Rust 检查记录 | 使用相同 crates 树的既有 77/81/85 项记录，分别对应 Windows 默认/Windows 全 feature/WSL2 全 feature；不将其计为本次重新跑测 |

集成复跑来自前一轮本地开发会话；本次整理时保留报告及版本索引至 [integration-20261027](../results/member-a/integration-20261027/README.md)。测试命令的会话输出与保存的 Demo 结果分开标注。复制的最小演示报告、事件和 manifest 来自既有实际运行，保留其原始 run_id、时间、源码哈希和二进制哈希，没有补写新采样日期。

B 的双模式浏览器验收与截图仍归属 [B 报告](member-b-test-report.md) 及 results/member-b/dashboard/。A 的本轮页面查看只确认当前总览显示真实数据，没有重复执行 B 的完整 F01～F04 浏览器矩阵。运行中的展示实例和端口是观测快照，重启后会变化。

完整黑盒 CSV、HTTP 公平对照、权限窗口及服务 RSS 仍由 B 实测，A 配合定位 Rust 问题。双方在最终集成版本确定后统一运行六项 Rust 门禁及完整集成复验；目前的 WSL2 缓存环境复跑不代表干净环境验收，远程 CI 本次也未核验。

## 2026-10-26 接口与实验入口补充验证（历史记录）

该轮新增健康/审计契约回归、认证与健康锁等待的异步调度回归，以及 feature 隔离的 HTTP 静态基线。Windows 默认模式 77 项、启用 bench-harness 后 81 项；WSL2 启用该 feature 后 85 项，均含 4 项文档测试，全部通过。

Windows 的默认/全 feature Clippy 和测试、WSL2 的全 feature Clippy 和测试通过（Clippy 均为 -D warnings）；两环境的 fmt、全 feature 文档构建、产品与实验例程 release 构建通过。WSL2 另显式执行全 feature 文档测试。默认产品的真实 TCP 测试也在全 feature 构建下通过，确认没有因实验 feature 放开授权。CI 已增加实验门禁，远程结果仍须实际推送运行后查看。

独立 release 例程用真实 TCP 分别启动 enforced/baseline，验证健康、读取、inspect 写入、一次租约写入、重复写、白名单外拒绝、文件内容和 intent/result 落盘。动态模式 inspect/重复写均 403，基线均 200，两者白名单外读取均 403；另验证非回环绑定在创建夹具前被拒绝。这些是启动与正确性检查，没有生成或冒充三轮性能采样。

前端用 Node.js 24.16.0、npm 11.13.0 运行 8 项单元测试、lint 与包含类型检查的构建，全部通过。健康测试只提供真实后端的 status/instance_id，并覆盖 AUDIT_UNAVAILABLE 503；API 测试保留响应体取消错误。

B 仍需按 [HTTP 实验约定](http-experiment.md) 完成采样与统计。补充验证源码引用见 results/member-a/compatibility-followup.json。

## 原始后端交付验证（历史记录）

以下原始样本和日志的受测源码为 `cf502f8406ce0b9309654b176ef71d2c770c6c26`，crates 树为 `20e6ce2b892d0f3347d82735eb6754fd304d4106`。它们保持原始引用，不能当作当前集成版本的重测性能结果。core 策略算法未改，HTTP 服务代码已有后续变更。

### 环境和质量门禁

| 环境 | 工具链 | cargo test --workspace 计数 |
| --- | --- | --- |
| Ubuntu 24.04 LTS，WSL2，x86_64-unknown-linux-gnu | Rust/Cargo 1.98.1 | 77 项全部通过，含 4 项文档测试 |
| Windows GNU 辅助环境，x86_64-pc-windows-gnu | Rust/Cargo 1.98.1，MSYS2 GCC | 73 项全部通过，含 4 项文档测试 |

Linux 比 Windows 多 2 项服务符号链接测试、1 项 core 符号链接测试和 1 项 Unix 权限测试。Linux 为项目支持的验收环境；不将 Windows 的 cfg 排除用例记为通过。

两个环境均实际成功执行以下六条命令，退出码在 results/member-a/{linux,windows}-verification.json 中逐条保留：

```text
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test --workspace --locked
cargo test --workspace --doc --locked
cargo doc --workspace --no-deps --locked
cargo build --workspace --release --locked
```

单独重复 doc 门禁的 4 项不再次累加到测试总数。GitHub CI 已配置相同固定版本和命令，但本次没有推送或查看远程运行，不宣称远程已绿。

### 功能与证据映射

| 需求 | 实现与自动验证 |
| --- | --- |
| FR01 身份与任务 | getrandom 32 字节；仅返回一次；摘要存储；A01、D01～D03、D23、查询脱敏、真实 JSONL 重启用例 |
| FR02 阶段 | A03、A07、A09、D05～D08；顺序、重复、回退、done 终态与优先级 |
| FR03 路径 | D14～D20、core path 测试；符号链接目标及父目录、非普通文件、精确允许列表、静态边界 |
| FR04 租约 | A04、A05、D09～D13、D24、core grant；有效期、撤销、次数、绑定、冲突和重新发放 |
| 并发与时间 | C01 同时 10 个请求恰好一次成功；C02 可注入时钟在到期前 1ns、等于、后 1ns；C03 及暂停写入测试证明撤销等待、过期后进行中操作可完成 |
| FR05 文件 | C04、C06、D21、D22；空内容、UTF-8 字节限制、缺失文件、非 UTF-8、失败不扣次；临时文件完成后注入失败并检查清理及原文件；Unix mode 保留 |
| FR06 审计 | C05a/b/c、拒绝日志失败、发放/撤销失败用例；intent 前失败不执行、result 失败报告 executed=true、不回滚次数、健康失效、管理内存不先提交 |
| v2.1 查询 | A10 及查询测试；列表截断和稳定排序、详情快照、租约 status、当前实例分页、不暴露 Token/根路径/内容 |
| 客户端断连 | 暂停执行中的文件操作后取消 HTTP waiter，仍完成替换、消费和 result；不得盲目重试 |
| 独立真实链路 | tcp.rs 启动真实子进程、随机端口、HTTP 收发、文件内容和 JSONL 比对；仅结束自身进程 |

api.rs 直接命名 A01～A10、D01～D24，并补充 C01～C06 和额外边界。读写测试帮助函数自动核对拒绝后的文件和租约不变、可用审计中的 result；并发请求另外检查成功者内容和最终次数。路由测试未经过网络的部分不伪称为 HTTP 黑盒实验。

TCP 最小用例复现：

```text
cargo test -p agentfence-server --test tcp --locked -- --nocapture
```

Windows 和 Linux 均实际看到 BACKEND_TCP_MINIMAL_PASS。覆盖 inspect 读取/拒写、edit 一次性授权/成功/用尽、区外私密夹具拒绝、verify 读回/拒写、done 拒读、审计关联。

额外启动检查见 startup-checks.json：缺 Admin Token、非回环地址、配置缺失、审计位于工作区内部均非零退出；夹具 initializer 拒绝覆盖已有目录且保留内容。

### 纯策略实测

硬件：Intel Core i7-12700H；Ubuntu WSL2；release；单并发。三个独立采样轮次，各先排除 256 次预热，再测允许/拒绝各 1000 次，共 6000 条。使用 Instant，百分位为 nearest-rank。

| 场景 | P50 三轮范围（微秒） | P95 三轮范围（微秒） | P99 三轮范围（微秒） |
| --- | --- | --- | --- |
| edit 有效显式写租约 | 0.091～0.093 | 0.094～0.112 | 0.105～0.115 |
| verify 阶段拒绝写入 | 0.024～0.025 | 0.025～0.026 | 0.026 |

表格仅对显示作舍入，原始浮点样本没有改写。测量包含计时器和 black_box 调用成本，不包括 HTTP、锁、文件元数据、文件读写和日志。该结果只覆盖这两个特定场景，不能用来表示完整 Broker 的延迟，也不代表全部拒绝原因的性能。

`/usr/bin/time -v` 观察到该独立微基准进程峰值 RSS 为 2276 KiB；它不是完整服务或完整测试流程的峰值 RSS，不替代 B 的资源实验。端到端基线、读/写/拒绝各至少 100 次、权限窗口对照和服务 RSS 仍由 B 实测。

### 原始文件与复现

- linux-checks.log、windows-checks.log：完整命令输出；对应 verification JSON 记录 commit、源码树、工具链和逐项退出码。
- policy_samples.json、policy_samples.csv：全部 6000 个样本；policy_summary.json：环境及每轮百分位；policy_resource_usage.txt：独立进程资源原始输出。
- startup-checks.json：额外进程启动/夹具检查的退出码与断言结果；不保存令牌。

复跑原始策略样本：

```sh
cargo run --release -p agentfence-core --example policy_bench --locked -- 1000 > policy_samples.json
```

提交的这些结果是成员 A 的真实运行快照。它们不是 B 的 security_cases.csv、HTTP bench_samples.csv 或 permission_windows.csv，不以现有记录替代尚未运行的团队验收。源码变更后必须重新检查受影响的测试和基准，并记录新的版本引用。
