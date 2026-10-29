> 说明：本文为只读接口基线；隔离读写任务的新流程见 [隔离修改任务](v2-controlled-edit.md)。

# v2 成员 A 接口与两日交付约定

基线为 main 的 `2d31b74`，A 分支为 `feat/v2-rust-mcp`。主验收运行环境是原生 Windows；Rust Broker 是唯一受控文件扫描、导入、读取和审计执行者。B 的原生助手负责选目录及浏览器会话，不自行读取项目文件。以下接口是本轮实现目标；测试结果另行记录，不能将历史实验当作 v2 验收。

## 两日顺序

第一天交付工作区预览/快照、readonly 规则、任务身份查询接口及 Rust 回归。第二天交付官方 rmcp stdio 服务、真实进程协议测试、Release 包和 B 对接说明。每天先完成 P0；MCP 计时仅在主链路通过后补充。

## 可信管理接口

所有 `/v1/admin/` 接口由 Rust 校验 Admin Bearer。B 本机代理不得透明转发浏览器指定的任意路径。浏览器只能向助手发选目录/确认动作，助手以短时、单次、绑定会话的 selection_id 找回真实路径。Admin Token 不返回浏览器、不交给 MCP。

| 方法与路径 | 请求 | 结果 |
| --- | --- | --- |
| POST /v1/admin/workspace-previews | `{source_path}`，仅可信助手提交原生绝对路径 | preview_id、源路径、允许文件/排除清单、字节数、manifest_sha256、到期时间 |
| GET /v1/admin/workspace-previews/{id}/excluded | offset/limit，默认 100，上限 200 | 管理员分页查看完整排除清单；不消耗 preview_id |
| POST /v1/admin/workspaces | `{preview_id,display_name,profile:"readonly_snapshot"}` | 导入确认过的内容；workspace_id、snapshot_id、manifest 与统计 |
| GET /v1/admin/workspaces | 无 | 已注册工作区摘要及计数，不重复传输全量清单；源路径仅此管理面可见 |
| POST /v1/admin/tasks | `{workspace_id,goal,permission_profile:"readonly"}` | 任务概要和一次性 agent_token，仅可信启动器保存 |

预览有效期 300 秒、最多保留 16 个；确认时单次消费。重新扫描与预览哈希不一致返回 SNAPSHOT_CHANGED，必须重新预览。最多 1000 个导入文件、每文件 65536 UTF-8 字节、总计 20 MiB、最多遍历 20000 个条目。只读快照只能创建 readonly 任务。源目录/快照路径不进入 Agent 响应或审计。

默认允许 rs/toml/md/txt/json/yaml/yml。排除版本库、构建/依赖目录、隐藏目录、env/凭证常见文件名、链接/重解析点、特殊文件和非 UTF-8。超出扫描/文件/总量预算返回明确错误。排除项明确列出；过多时管理预览标明截断并保留总数。快照和任务在 Broker 实例生命周期内有效，重启须重新导入创建；不宣称持久恢复或 OS 沙箱。

## 任务身份接口

| 方法与路径 | 请求 | 结果 |
| --- | --- | --- |
| GET /v1/agent/tasks/{id} | Agent Bearer | 自己的任务概要、profile、snapshot_id、允许工具；没有宿主路径和其他任务信息 |
| GET /v1/agent/tasks/{id}/files | prefix/cursor/limit，默认 100，上限 200 | 已授权相对路径、字节数和 SHA-256 的分页清单；不透明游标绑定任务/实例/快照/筛选条件 |
| POST /v1/tool/execute | 保留现有 read_file/write_file 结构 | read_file 增加 SHA-256；readonly 写入无条件拒绝 |

readonly 合法阶段是 inspect→verify→done。可信启动器/管理员使用已有 phase 接口推进，MCP 不获得阶段管理工具。readonly 的 edit、grant、write 拒绝均产生任务关联审计，错误码 READONLY_TASK。旧请求省略 profile 时保持 controlled_edit，旧阶段/租约测试必须继续通过。导入快照要求显式 readonly，不能通过省略字段得到写权限。

## MCP stdio

服务端只持 `AGENTFENCE_BROKER_URL`（127.0.0.1 HTTP）、`AGENTFENCE_TASK_ID` 和 `AGENTFENCE_AGENT_TOKEN`。可信启动器注入环境，不把凭证写进 MCP 参数、输出、配置示例或日志。服务启动时核对任务为 readonly。

工具固定为 agentfence_list_files(prefix?,cursor?,limit?)、agentfence_read_file(path)、agentfence_task_status()。输入不接受工作区根、Token、task_id 或额外字段。读取返回原始内容、带行号内容、SHA-256、request_id 和 snapshot_id。stdout 仅 MCP 协议，诊断写 stderr。拒绝/网络故障返回安全错误，不自动重试写入，不提供 Shell 或管理工具。

## 交接验收

至少两个不同多文件项目可预览/导入；确认范围和实际读取哈希一致；至少 12 项新负向场景；真实 rmcp 客户端通过 stdio 握手、列工具、读三文件、错误调用、重连；新旧 Rust/文档测试、fmt、Clippy、Release 构建通过。B 接手原生选择器、UI/CSRF 会话、真实模型审查与录屏。A 提供可执行文件和无付费模型的最小调用重放，不把它冒充真实 LLM 审查。
