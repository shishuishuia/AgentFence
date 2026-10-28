# v2 Rust 工作区与 MCP 接入

A 实现目录扫描/快照、不可升级的只读任务、任务身份查询和 MCP stdio 服务。主验收环境是原生 Windows。目录选择器、浏览器会话、Vue 管理页面、真实模型审查和录屏由 B 对接；离线协议重放不等于真实模型审查。

## 构建与离线重放

在仓库根目录执行，使用独立 target 避免覆盖仍运行的 v1 文件：

```powershell
cargo build --workspace --release --locked --target-dir target/v2
cargo run --release --locked --target-dir target/v2 -p agentfence-mcp-server --example readonly_smoke -- target/v2/release/agentfence-mcp-server.exe
```

Linux 去掉 `.exe`。第二条命令创建两个不同的三文件项目、自有 Rust Broker 和真实 MCP 子进程，由官方 rmcp 客户端列工具、读取与核验文件、执行固定越权请求，再推进 verify/done。它不调用模型或付费 API，只管理自己的服务，不停止现有看板。成功输出 `V2_MCP_REPLAY_PASS`。

`results/local-v2-*/evidence.json` 记录受测提交、dirty 标志、SDK、任务/快照、文件哈希及 request_id；`audit.jsonl` 是实际日志，`events-project-a.json`、`events-project-b.json` 是 API 审计导出。重放核对 API 与磁盘事件逐项相等、调用与结果关联、源文件未改变。结果中仅有生成的测试项目及模拟私有文件，无实际凭证。

## B 的对接顺序

1. 原生助手弹目录选择器，给浏览器返回短时、单次、绑定会话的 selection_id。浏览器不能任意指定 source_path。
2. 助手持 Admin Bearer 向 `POST /v1/admin/workspace-previews` 提交 `{source_path}`；Rust 返回 preview_id、完整允许文件清单、排除摘要、统计及 manifest_sha256。当排除清单被截断时，助手用 `GET /v1/admin/workspace-previews/{id}/excluded?offset=0&limit=100` 循 `next_offset` 分页取全，供管理员确认。
3. 人确认后，助手向 `POST /v1/admin/workspaces` 提交 `{preview_id,display_name,profile:"readonly_snapshot"}`；不要重新接收路径。
4. 向 `POST /v1/admin/tasks` 提交 `{workspace_id,goal,permission_profile:"readonly"}`。一次性 agent_token 仅交可信启动器，禁止返回浏览器。
5. 启动器以白名单环境启动 MCP 子进程，连接选定的 MCP Client。模型读的是固定版本快照，不是活跃源目录。
6. 读取结束后，由可信启动器/管理员使用已有 phase 接口推进 verify，核对读取和审计，再推进 done。MCP 没有阶段管理工具。done 表示受控访问关闭，不证明模型建议正确。

`GET /v1/admin/workspaces` 返回每个工作区的摘要和计数，不重复发送完整清单。预览与确认响应包含最多 1000 条完整允许路径：极长路径下可能超过旧 Python 客户端的 1 MiB 限制，B 的可信助手应为这两个响应使用有界的 2 MiB 接收上限；不要为所有旧接口取消大小限制。

新 Broker 可设置 `AGENTFENCE_EMPTY_REGISTRY=1`，从没有文件授权的实例开始。仍需随机 `AGENTFENCE_ADMIN_TOKEN`、回环 `AGENTFENCE_BIND` 和工作区外的 `AGENTFENCE_AUDIT`。不设置空注册表模式时仍使用现有配置，兼容 v1。重启后所有注册、快照、任务和 Token 失效，需重新导入；不承诺持久恢复。

## MCP 启动与工具

可执行文件为 `target/v2/release/agentfence-mcp-server.exe`。官方 [Rust SDK](https://github.com/modelcontextprotocol/rust-sdk) 固定 rmcp 3.8.0，仅使用 stdio。参考重放入口中的 `env_clear()`，子进程只传以下环境，不继承 Admin/模型凭证：

| 环境变量 | 要求 |
| --- | --- |
| AGENTFENCE_BROKER_URL | `http://127.0.0.1:<端口>`，无用户名、额外路径、查询或片段 |
| AGENTFENCE_TASK_ID | 新任务 ID |
| AGENTFENCE_AGENT_TOKEN | 仅绑定该任务的 Token |
| SystemRoot | Windows 必需系统变量，值来自系统 |

启动时校验 readonly profile 和 snapshot_id；继承 AGENTFENCE_ADMIN_TOKEN 或 DEEPSEEK_API_KEY 时直接拒绝。不要把真实 Token 固定在仓库、前端、模型消息、工具参数或截图中。

| 工具 | 参数 | 返回 |
| --- | --- | --- |
| agentfence_list_files | prefix?、cursor?、limit? | 相对路径、大小、SHA-256、分页游标、request_id |
| agentfence_read_file | path | UTF-8 原文、带行号文本、SHA-256、snapshot_id、request_id |
| agentfence_task_status | 空对象 | 本任务阶段、profile、snapshot_id、允许操作及 request_id |

列表/状态走 `/v1/agent/tasks/{id}` 和 `/files`，读取走 `/v1/tool/execute`，均使用任务身份。MCP 不直接打开文件，不提供 Shell、注册工作区或授权管理功能。未知工具和额外参数拒绝。stdout 只写协议，诊断仅写 stderr。

HTTP 禁用代理/重定向，连接超时 2 秒、请求超时 10 秒、响应上限 1 MiB；MCP 输入行上限 16 KiB。Broker/快照身份变化后不自动切换任务。Broker 拒绝返回 MCP `isError:true`，保留安全 reason_code 和 request_id；未进入 Broker 的协议参数错误不伪造 request_id。底层网络错误不转发原始错误体或请求头。

## 导入及安全规则

默认允许 rs/toml/md/txt/json/yaml/yml 普通 UTF-8 文件；排除隐藏项、构建/依赖目录、常见凭证名、链接、Windows reparse point 和特殊文件。名称规则不能保证识别内容里的所有敏感数据，管理端仍须展示并确认实际清单。

最多 1000 文件、每文件 65536 字节、总计 20 MiB、20000 遍历项和 64 层目录，超限明确失败。预览有效 300 秒，在确认入口检查；最多保留 16 个。确认单次消费，清单/内容变化返回 SNAPSHOT_CHANGED，必须重新预览。总工作区上限 32（含旧配置）。排除目录按一个目录项计，不递归计数；预览首包最多显示 200 项，并给出完整 excluded_entry_count 和 excluded_truncated。管理员可以分页取全；预览过期或确认消费后分页失效。

快照复制预览批准的字节，之后原项目变化不更新它。readonly 任务必须绑定快照；快照不能通过省略 profile 或指定 controlled_edit 得到写权限。合法阶段为 inspect→verify→done，edit、grant、write 均由 Rust 返回 READONLY_TASK。旧活跃配置仍支持原 controlled_edit 测试。

游标用 HMAC 绑定实例、任务、快照、前缀及偏移。每次快照读取核对清单哈希；审计沿用故障后关闭执行规则。readonly Agent 尝试访问管理路由时，拒绝会写入任务关联审计；旧 controlled_edit 的拒绝记录方式保持兼容。只读是 Broker 权限保证，不是 OS 不可变存储；不承诺抵御恶意同机同权限进程或无竞态导入。

外部 Harness 的独立 Shell/文件工具须禁用或由外部沙箱约束。只安装 MCP 不会隔离整个 Agent；成功读取、哈希和审计不构成模型建议正确性的证明。

## MCP 适配开销实验

先按上文构建 Release，再在相同版本执行：

```powershell
cargo run --release --locked --target-dir target/v2 -p agentfence-mcp-server --example mcp_overhead -- target/v2/release/agentfence-mcp-server.exe
```

实验创建一个 4096 字节的合成只读文件与真实 Broker，对同一任务分别使用 Rust 直接 HTTP 客户端和真实 MCP stdio 子进程读取。两种路径使用同一个授权、文件和同步审计；顺序交替，并发为 1，HTTP 保持连接。每种方式各 60 次预热，3 轮各 100 次正式样本。任何请求失败或证据不一致则整次失败，不丢弃慢样本。

`results/local-v2-mcp-bench-*/` 保存原始 `samples.jsonl`、`audit.jsonl`、API 导出 `events.json` 和 `summary.json`，包含构建提交、dirty 状态、工具链、二进制哈希、文件哈希、P50/P95/P99 与每轮结果。分位数采用 nearest-rank；脚本检查每个样本的 intent/result 和 API/磁盘审计一致。

这是单机、单文件端到端读操作的观测。直接客户端与 Broker 运行在基准进程中，MCP 为独立进程；差值包括 JSON-RPC、进程通信、带行号输出和适配处理，不能说是纯协议开销。它不包含远程模型、真实网络、CPU/RSS 或统计显著性结论，不能与不同提交的历史实验直接比较。

## 独立核验证据

在仓库根目录使用 Python 3.9 或以上版本，分别对重放目录和基准目录执行：

```powershell
python bench/verify_mcp_v2.py <结果目录> --self-test --source-match .
```

验证器读取原始文件和审计记录，重算文件哈希、请求关联和基准分位数；`--self-test` 检查篡改后的数据会被拒绝，`--source-match .` 比较当前 Cargo 配置和 Rust 源码与证据所记提交。验证结果不能证明模型建议正确，也不能替代真实模型审查。

## 验证

```powershell
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --all-features --locked -- -D warnings
cargo test --workspace --locked
cargo test --workspace --all-features --locked
cargo test --workspace --doc --locked
cargo doc --workspace --no-deps --locked
```

`readonly.rs` 验证 profile 与审计；`workspaces.rs` 验证双项目、源变化、预览过期/重放、排除、预算、链接、身份和游标；MCP `stdio.rs` 用独立进程及官方客户端验证握手、三工具、多文件读取、错误和重连。最终结果另记受测提交；不把 v1 的 34 场景、历史延迟/RSS 当成 v2 新指标。
