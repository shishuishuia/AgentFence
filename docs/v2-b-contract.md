# v2：B 本机管理接口与 A 对接约定

本文件描述 B 的适配约定；现已集成 A 的 `fd4e086`，实际 Rust 接口以
`docs/v2-a-contract.md` 和 `docs/v2-rust-mcp.md` 为准。HTTP 契约替身的测试结果
不代表 Rust readonly、快照或 MCP 已通过验收，真实调用另行记录。

## 部署与信任边界

本轮 B 入口采用原生本机 Python 目录选择助手，Rust 与助手必须能访问同一
源路径。优先同机 Windows。若改用 WSL，需要另外实现并验证可信导入桥接；
不能直接拼接 `/mnt/c`。普通 MCP 工具只能经 Rust Broker 读取快照。

启动：`python -m web_proxy --mode dev --manage-workspaces`。
沿用环境变量 `AGENTFENCE_BROKER_URL` 和 `AGENTFENCE_ADMIN_TOKEN`；凭证不写入
命令参数、浏览器或响应。默认代理仍仅开放原有查询。

## 浏览器 → B 本机助手

全部为 `/api/admin` 前缀，无任意 root、命令、上游地址或凭证输入。
POST 必须带本机 Origin、JSON，以及会话返回的 `X-AgentFence-CSRF`。
会话 cookie 为 HttpOnly / SameSite=Strict，仅内存保存 CSRF。

| 方法与路径 | 请求 | 结果 |
|---|---|---|
| POST `/session` | `{}` | csrf_token；设置 30 分钟会话 cookie |
| POST `/picker/start` | `{}` | 202，selection_id / pending；系统对话框在独立进程打开 |
| GET `/picker/{selection_id}` | 无 | pending / ready / cancelled / failed / consumed |
| GET `/workspaces` | 无 | items：管理员可见工作区元数据 |
| POST `/workspaces/confirm` | selection_id / display_name / profile=readonly_snapshot | 201，已导入工作区 |

selection_id 随机、绑定会话、有效 5 分钟、确认前单次消费。网络超时也不允许
重放确认。取消不创建工作区。目录选择器一次只打开一个。重启后会话和选择失效。

## B 可信助手 → A Rust

以下请求由助手持 Admin Token 发起。source_path 只能来自系统对话框。

1. POST `/v1/admin/workspace-previews`：`{source_path}`。
   Rust 扫描和过滤，返回 preview_id、manifest_sha256、expires_at、
   allowed_file_count、excluded_entry_count、total_bytes、files、excluded[{path,reason}]。
   助手在浏览器响应中将 excluded_entry_count 映射为 excluded_file_count，reason
   映射为 reason_code。截断时经 `/workspace-previews/{id}/excluded` 分页读取；
   浏览器只能使用绑定会话的 selection_id 请求分页，不能任意提供 preview_id。
2. POST `/v1/admin/workspaces`：`{preview_id,display_name,profile:"readonly_snapshot"}`。
   Rust 必须重新核验源文件、清单和预览有效期；变化返回 SNAPSHOT_CHANGED，
   过期返回 PREVIEW_EXPIRED；禁止按旧清单悄悄导入变动内容。
3. GET `/v1/admin/workspaces`：`{items:[workspace]}`。

浏览器 workspace 字段：workspace_id、snapshot_id、display_name、import_mode（固定
readonly_snapshot）、imported_at、manifest_sha256、allowed_file_count、
excluded_file_count、total_bytes。源绝对路径仅出现在管理员的选择预览，不向
Agent/MCP 返回。

Rust 限额：最多 1000 个 UTF-8 普通文本文件，单文件 64 KiB、合计 20 MiB。
扩展名 .rs/.toml/.md/.txt/.json/.yaml/.yml；排除 .git、target、node_modules、
.env*、凭证/密钥、符号链接和 Windows 重解析点。限额失败必须显式报错。

## 分步检查记录

第一步覆盖本机 HTTP 会话、CSRF、目录选择/取消、跨会话和过期选择、重复确认、
不确定确认结果、会话复用、响应字段过滤。Rust 集成另行记录，不用替身结果代替。

## 只读任务创建

POST `/api/admin/tasks` 接受 `{workspace_id,goal,permission_profile:"readonly"}`。
代理只允许这三个字段，目标最多 1000 字符 / 4000 UTF-8 字节。
助手向 POST `/v1/admin/tasks` 发出同一请求，不会失败后降级创建旧版任务。
Rust 成功响应必须含 task_id、workspace_id、snapshot_id、goal、permission_profile、
phase=inspect、created_at、updated_at、instance_id 和一次性 agent_token。
助手严格检查 profile，并仅把 Token 留在本机内存，浏览器只收任务摘要。

readonly 的阶段为 inspect → verify → done。由可信启动器推进阶段；MCP 三工具
不含管理操作。Rust 必须对直接 HTTP edit/grant/write 返回 READONLY_TASK 并记
`management_denied`（管理拒绝）或 `result`（工具结果）审计，关联 task_id/request_id。
前端已将这两种结果中的 READONLY_TASK 计入策略拒绝。

## MCP 与真实模型审查

安装 `python -m pip install -r integrations/requirements-mcp.txt`，官方 Python
SDK 固定为 1.30.0。启动助手时追加
`--mcp-bin target/v2/release/agentfence-mcp-server.exe`。默认 `--review-agent offline`
只进行真实 MCP 调用和审计核验；`--review-agent deepseek` 使用服务端环境的
DEEPSEEK_API_KEY。Linux 去掉 exe 后缀。

浏览器 POST `/api/admin/tasks/{id}/review/start` 仅接受 `{}`，运行后不可重放；
GET `/api/admin/tasks/{id}/review` 查询进度、结果和安全调用摘要。任务身份只从
本机助手内存取得，不接受前端提供 Token、命令、模型地址、密钥或工具定义。

客户端通过官方 SDK 初始化/发现三个 MCP 工具，实际读取至少三个文件并校验
SHA-256、快照/实例和审计。已有 DeepSeek Harness 的工具执行层改为 MCP
`tools/call`；没有独立文件、Shell 或审批工具。整个运行最多 240 秒，30 次
工具调用，10 轮模型交互。模型上下文中的长文件标注节选，报告以纯文本展示。

可信启动器推进 inspect→verify，核对每次成功调用的 result 审计，再推进 done。
离线结果明确标记 offline_mcp_replay，不生成或冒充在线模型报告。

参考：[官方 SDK stdio 客户端](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/examples/snippets/clients/stdio_client.py)。
