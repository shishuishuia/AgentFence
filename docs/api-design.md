> 本文为前期接口设计稿，部分字段和错误行为与后续实现存在差异。Rust 后端已实现的契约以 [api-contract.md](api-contract.md) 为准；openapi.json 对应本设计稿，尚待联调对齐。

# AgentFence Lite 接口文档

接口版本：v1.0.0  
更新时间：2026-10-26  
适用方案：五天竞赛需求 v2.1，Vue 3 + Vite + Vue Router 前端，Rust + Axum 后端。

本文定义待实现的 HTTP 契约，用于分工、Mock、开发和联调；当前不表示服务已经实现或测试通过。与需求文档相比，本文件进一步固定字段、默认值、错误优先级和游标行为。机器可读描述见 [openapi.json](openapi.json)。示例 ID、令牌和耗时仅用于说明。

## 1 接口范围与取舍

### 1.1 从业务流程确定接口

| 业务动作 | 必需接口 | 为什么需要 |
| --- | --- | --- |
| 管理员建立受控任务 | POST /v1/admin/tasks | 绑定已配置工作区，签发任务专用 Agent Token |
| 前端任务列表和总览 | GET /v1/admin/tasks | 获取当前实例真实任务状态，不能由分页日志推断 |
| 前端任务详情和租约展示 | GET /v1/admin/tasks/{task_id} | 一次返回阶段、允许路径及全部租约快照 |
| 管理员推进任务阶段 | POST /v1/admin/tasks/{task_id}/phase | 执行 inspect → edit → verify → done |
| 管理员授予短期写权限 | POST /v1/admin/tasks/{task_id}/grants | 固定文件、TTL 和次数 |
| 管理员撤销写权限 | DELETE /v1/admin/tasks/{task_id}/grants/{grant_id} | 使后续请求无法继续使用该租约 |
| Agent 受控读写文件 | POST /v1/tool/execute | 统一执行 read_file 或 write_file，并审计 |
| 前端查看调用和状态变更 | GET /v1/admin/events | 按任务过滤、游标分页和增量轮询 |
| 脚本及前端探测可用性 | GET /healthz | 区分服务可访问和审计可用状态 |

以上为 9 个 Rust 操作。浏览器另使用 4 个 GET 代理接口：`/api/tasks`、`/api/tasks/{task_id}`、`/api/events`、`/api/health`。它们转发查询，不新增业务权限。

### 1.2 本期不单独增加的接口

| 候选接口 | 本期处理 | 后续增加条件 |
| --- | --- | --- |
| 登录、注册、刷新 Token、用户管理 | 管理员使用本地配置 Token，浏览器只读 | 面向多人或远程部署时设计认证与会话 |
| 浏览器创建任务、切阶段和发放租约 | 管理员 CLI 直接调用 Rust；代理不转发 | 有明确的管理界面需求及服务端认证后再做 |
| 独立租约列表、单租约详情 | 复用任务详情中的 grants | 租约历史规模增长需要分页时拆分 |
| 工作区管理、策略修改、文件浏览 | 从受信任的 policy.json 读取；CLI 已知 workspace_id | 引入受控配置管理流程时增加 |
| 删除任务、回退阶段、重置任务 | done 终结任务；重新演示创建新任务 | 先明确历史保留与授权失效规则 |
| WebSocket、SSE 推送 | 使用事件游标每秒轮询 | 轮询开销或实时性出现实测需求后增加 |
| 统计报表、实验结果上传 | 前端统计已加载事件，实验结果由离线脚本生成 | 需要跨运行汇总和持久化存储时增加 |
| 任意 Shell、网络代理、文件下载上传 | 超出本期工具范围 | 另行定义资源策略与隔离边界 |

## 2 地址与通用约定

### 2.1 服务地址

| 服务 | 本地地址 | 调用方 |
| --- | --- | --- |
| Rust Broker | http://127.0.0.1:8080 | 管理员 CLI、Agent 适配器、只读代理 |
| 只读代理及构建后的 Vue 页面 | http://127.0.0.1:4173 | 浏览器；托管 front/dist |
| Vite 开发服务 | http://127.0.0.1:5173 | 开发浏览器；将 /api 原样代理到 4173 |

浏览器请求相对地址 `/api/...`；不要在 Vue 页面中直连管理员 API。Vue 页面路由 `/tasks/:taskId` 与 HTTP 接口 `/api/tasks/{task_id}` 是不同概念。

### 2.2 身份与请求头

```http
Authorization: Bearer <admin_token 或 agent_token>
Content-Type: application/json
Accept: application/json
```

- `/v1/admin/*` 只接受 Admin Token；`/v1/tool/execute` 只接受绑定任务的 Agent Token。有效令牌角色不符返回 `403 ROLE_DENIED`，包括 Admin Token 调用工具接口。
- 缺失、格式错误、多条 Authorization、无效或重启后失效的 Agent Token 返回 `401 INVALID_TOKEN`。Token 采用不可预测随机值，不要求 JWT；不得放入 URL。
- `/healthz` 不要求身份。浏览器 `/api/*` 不接收或转发客户端令牌，代理自行注入 Admin Token。
- Admin Token 来自服务端环境变量；Agent Token 只在创建任务时返回一次。查询、日志、前端构建产物和浏览器存储中不出现 Token。
- JSON 请求必须使用 `application/json`，允许 charset 参数；其他媒体类型返回 `415 UNSUPPORTED_MEDIA_TYPE`。无请求体的 GET、DELETE 不要求 Content-Type。
- 所有 API 响应返回 `Content-Type: application/json`、`Cache-Control: no-store`、`X-Request-Id`；响应头 ID 与响应体一致。401 同时返回 `WWW-Authenticate: Bearer`，405 返回 `Allow`。

### 2.3 字段和输入规则

| 项目 | 约定 |
| --- | --- |
| 命名 | JSON 和 HTTP 参数使用 snake_case；Vue 路由的 taskId 转换成 task_id |
| 标识符 | task_id、grant_id、workspace_id、instance_id、request_id 均为 1～64 个 ASCII 字符，匹配 `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$` |
| 时间 | UTC RFC3339 字符串，如 `2026-10-26T02:00:00Z`；服务端生成 |
| 数值 | 次数、TTL、limit 必须是整数；JSON 不接受字符串、布尔值或浮点写法；query 不接受符号、小数和前导零 |
| 未知字段 | 请求 JSON 未声明字段、重复键、未知 query、重复 query 均返回 `400 INVALID_REQUEST` |
| null | 输入字段不接受 null；可选参数通过省略使用默认值，after 的空值有专门约定 |
| 路径 | 相对路径，长度 1～1024 字符；路径安全判断按第 5 节执行 |
| 请求体 | 最大 512 KiB，即 524288 字节；超过返回 413，不执行工具 |
| 文件内容 | UTF-8 编码后不超过 64 KiB，即 65536 字节；空字符串写入合法 |
| 无请求体接口 | GET 和 DELETE 若携带非空请求体，返回 400 |
| 字段稳定性 | 响应中本版声明的字段均返回；可空字段明确返回 null。客户端应容忍未来新增响应字段 |

ID 语法错误为 400；语法合法但资源不存在，按各接口定义返回。`phase` 拼写错误为 400，已知阶段但迁移非法为 409。请求示例里的固定 ID 不代表生产生成规则。

### 2.4 响应结构

管理和查询成功响应采用扁平对象，各自包含 `request_id`、`instance_id` 和业务字段；不强行套用工具的 `allowed/executed`。

通用错误 `ErrorResponse`：

```json
{
  "request_id": "req-001",
  "instance_id": "inst-demo",
  "reason_code": "INVALID_REQUEST",
  "message": "limit 必须位于 1 至 100"
}
```

Rust 生成的 `instance_id` 始终为当前实例 ID。代理自行生成错误时 `instance_id=null`；上游响应原样转发时保留上游 ID。代理生成的 request_id 建议使用 `proxyreq-` 前缀。

已匹配 `/v1/tool/execute` 的 POST 响应使用 `ToolResponse`，包括该操作的输入、鉴权和体积错误：

```json
{
  "request_id": "req-002",
  "instance_id": "inst-demo",
  "allowed": false,
  "executed": false,
  "reason_code": "LEASE_USED_UP",
  "message": "该租约可用次数已耗尽",
  "duration_us": 210,
  "data": null
}
```

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| allowed | boolean | 策略是否作出允许决定；尚未完成策略判断时为 false |
| executed | boolean | 工具业务操作是否成功完成；不是“是否曾触碰磁盘” |
| reason_code | string | 稳定结果码，正常成功为 OK；不能按 message 判断结果 |
| duration_us | integer ≥ 0 | 服务端总处理耗时，包含等待、策略、I/O 和审计；不是纯策略耗时 |
| data | object 或 null | 成功读取或写入的数据；未完成工具操作时为 null |

读取文件但 UTF-8 检查失败时 `executed=false`；写入已替换成功、仅结果日志失败时 `executed=true`。HTTP 非 2xx 不一定表示写入未发生，见第 5.4 节。

## 3 数据模型和状态

### 3.1 TaskSummary 与 TaskDetail

| 字段 | 类型 | 出现位置与约定 |
| --- | --- | --- |
| task_id | ID | 任务唯一 ID |
| workspace_id | ID | 已登记工作区的逻辑名称 |
| phase | Phase | inspect、edit、verify、done |
| created_at | 时间 | 任务创建时间 |
| updated_at | 时间 | 阶段、租约状态或成功写入导致任务状态更新的时间 |
| allowed_paths | string[] | 详情及创建响应；仅相对路径，按字典序返回 |
| grants | Grant[] | 仅详情；包含该任务本实例内全部租约及失效历史 |
| server_time | 时间 | 仅详情；用于显示倒计时，不替代服务端单调时钟判断 |

列表项只包含前五个字段。详情另有 `request_id`、`instance_id`。读取、被拒请求、重复撤销不更新 updated_at；自然到期不触发内存写操作，因此也不更新该字段。查询时仍实时计算 status。

列表按 created_at 降序，同时间按 task_id 升序；grants 按 created_at 降序，同时间按 grant_id 升序。详情从同一任务锁内读取一致快照，不返回工作区绝对根路径或文件内容。

### 3.2 Grant

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| grant_id、task_id | ID | 精确绑定，不得跨任务复用 |
| tool | string | 固定 write_file |
| path | string | 已允许的相对普通文件路径 |
| created_at、expires_at | 时间 | 服务端生成 |
| ttl_seconds | integer | 1～300 |
| max_uses | integer | 1～10 |
| remaining_uses | integer | 0～max_uses |
| revoked | boolean | 撤销后保持 true |
| status | GrantStatus | 以下优先级计算，返回快照 |

`status` 的判定优先级：

1. 任务不处于 edit：`stage_inactive`。
2. revoked=true：`revoked`。
3. 单调时钟 now ≥ deadline：`expired`。
4. remaining_uses=0：`used_up`。
5. 其余情况：`active`。

只允许 `inspect → edit → verify → done`，不可跳跃、回退或重复推进。只在 edit 发放租约；同一任务、工具、路径最多一个 active 租约。离开 edit 后旧租约不能恢复。

## 4 管理员接口

以下接口需要 Admin Token。通用 400、401、403、413、415、500 和审计故障规则见第 9 节；各小节列出有业务意义的返回。

### 4.1 创建任务

`POST /v1/admin/tasks`

```json
{
  "workspace_id": "demo-rust"
}
```

workspace_id 必填，引用启动配置中的工作区；未登记返回 `400 INVALID_REQUEST`。不能传入根目录、允许列表、初始阶段或自定义 Token。

成功 `201`，`CreateTaskResponse`：

```json
{
  "request_id": "req-create",
  "instance_id": "inst-demo",
  "task_id": "task-001",
  "workspace_id": "demo-rust",
  "phase": "inspect",
  "created_at": "2026-10-26T02:00:00Z",
  "updated_at": "2026-10-26T02:00:00Z",
  "allowed_paths": ["Cargo.toml", "src/main.rs"],
  "agent_token": "EXAMPLE_ONLY_NOT_A_REAL_AGENT_TOKEN"
}
```

创建操作不是幂等操作，重复请求创建不同任务；超时后不得自动重试。Agent Token 至少包含 128 位随机熵，调用方负责保存在自己的进程环境中。审计失败返回 503，不创建任务。

### 4.2 查询最近任务

`GET /v1/admin/tasks?limit=50`

| query | 必填 | 默认 | 约束 |
| --- | --- | --- | --- |
| limit | 否 | 50 | 整数 1～100；空值、超限返回 400 |

成功 `200`，`TaskListResponse`：

```json
{
  "request_id": "req-list",
  "instance_id": "inst-demo",
  "items": [
    {
      "task_id": "task-001",
      "workspace_id": "demo-rust",
      "phase": "inspect",
      "created_at": "2026-10-26T02:00:00Z",
      "updated_at": "2026-10-26T02:00:00Z"
    }
  ],
  "total": 1,
  "truncated": false
}
```

total 是当前实例任务总数；`truncated = total > items.length`。无任务时 items=[]、total=0、truncated=false。本期不支持任务列表分页、排序或过滤参数；页面标注最近 N 个任务，不把列表项数当作总数。

### 4.3 查询任务详情

`GET /v1/admin/tasks/{task_id}`

成功 `200`，`TaskDetailResponse`：

```json
{
  "request_id": "req-detail",
  "instance_id": "inst-demo",
  "task_id": "task-001",
  "workspace_id": "demo-rust",
  "phase": "edit",
  "created_at": "2026-10-26T02:00:00Z",
  "updated_at": "2026-10-26T02:00:10Z",
  "server_time": "2026-10-26T02:00:15Z",
  "allowed_paths": ["Cargo.toml", "src/main.rs"],
  "grants": [
    {
      "grant_id": "grant-001",
      "task_id": "task-001",
      "tool": "write_file",
      "path": "src/main.rs",
      "created_at": "2026-10-26T02:00:10Z",
      "expires_at": "2026-10-26T02:01:10Z",
      "ttl_seconds": 60,
      "max_uses": 1,
      "remaining_uses": 1,
      "revoked": false,
      "status": "active"
    }
  ]
}
```

任务不存在为 `404 TASK_NOT_FOUND`。无租约时 grants=[]；本期不截断或分页租约历史。不返回任何 Token，也不单独提供恢复或查询 Agent Token 的接口。

### 4.4 推进阶段

`POST /v1/admin/tasks/{task_id}/phase`

```json
{
  "phase": "edit"
}
```

phase 必填，是目标阶段，不是客户端声明的当前阶段。成功 `200`，`PhaseChangeResponse`：

```json
{
  "request_id": "req-phase",
  "instance_id": "inst-demo",
  "task_id": "task-001",
  "previous_phase": "inspect",
  "phase": "edit",
  "updated_at": "2026-10-26T02:00:09Z"
}
```

未知枚举值返回 `400 INVALID_REQUEST`；任务不存在返回 404；已知但非相邻前进返回 `409 INVALID_TRANSITION`。重复提交相同目标阶段同样返回 409。阶段变更与工具执行使用同一任务互斥边界；离开 edit 后下一次校验即拒绝写入。

### 4.5 发放写租约

`POST /v1/admin/tasks/{task_id}/grants`

```json
{
  "tool": "write_file",
  "path": "src/main.rs",
  "ttl_seconds": 60,
  "max_uses": 1
}
```

| 字段 | 必填 | 默认 | 约束 |
| --- | --- | --- | --- |
| tool | 是 | 无 | 只能为 write_file；其他值为 400 INVALID_REQUEST |
| path | 是 | 无 | 允许列表中的已有普通文件，无符号链接 |
| ttl_seconds | 否 | 60 | 整数 1～300 |
| max_uses | 否 | 1 | 整数 1～10 |

成功 `201`，`GrantResponse` 为 request_id、instance_id 和 grant：

```json
{
  "request_id": "req-grant",
  "instance_id": "inst-demo",
  "grant": {
    "grant_id": "grant-001",
    "task_id": "task-001",
    "tool": "write_file",
    "path": "src/main.rs",
    "created_at": "2026-10-26T02:00:10Z",
    "expires_at": "2026-10-26T02:01:10Z",
    "ttl_seconds": 60,
    "max_uses": 1,
    "remaining_uses": 1,
    "revoked": false,
    "status": "active"
  }
}
```

按任务存在、阶段、路径、租约冲突顺序判断：任务缺失 404；非 edit 为 `403 INVALID_STAGE`；路径禁止为 `403 PATH_DENIED`；允许文件缺失为 `404 FILE_NOT_FOUND`；同资源已有 active 租约为 `409 GRANT_CONFLICT`。文件元数据读取失败为 500 IO_ERROR。

服务端依据单调时钟设置有效期；expires_at 供展示使用。不要根据浏览器倒计时决定是否能写，也不要把客户端时间提交给服务端。

### 4.6 撤销租约

`DELETE /v1/admin/tasks/{task_id}/grants/{grant_id}`，无请求体。

成功 `200`，响应使用相同 `GrantResponse`，grant.revoked=true；status 根据第 3.2 节计算，因此非 edit 阶段会显示 stage_inactive。remaining_uses 保留当前值，不重置为零。

语法合法但任务不存在为 404；该任务下找不到租约，包括提供其他任务的租约 ID，为 `403 LEASE_NOT_FOUND`。允许撤销已到期或已耗尽的租约；重复撤销同一租约成功返回当前快照，不更新 updated_at、不重复产生状态变更事件。

撤销等待已进入同任务临界区的执行完成。成功返回后的新校验必须拒绝；不能取消撤销前已经开始执行的 I/O。客户端若需要重试，先查询任务详情确认当前状态。

## 5 Agent 工具接口

`POST /v1/tool/execute`，需要任务专用 Agent Token。

### 5.1 读取文件

请求 `ReadFileRequest`：

```json
{
  "task_id": "task-001",
  "tool": "read_file",
  "path": "src/main.rs"
}
```

三个字段均必填，额外传入 grant_id 或 content 返回 400。inspect、edit、verify 阶段可读，done 拒绝。成功 `200`：

```json
{
  "request_id": "req-read",
  "instance_id": "inst-demo",
  "allowed": true,
  "executed": true,
  "reason_code": "OK",
  "message": "读取成功",
  "duration_us": 350,
  "data": {
    "content": "fn main() {}\n",
    "bytes_read": 13
  }
}
```

策略允许但文件不存在：404 FILE_NOT_FOUND；内容超限：413 PAYLOAD_TOO_LARGE；不是 UTF-8：422 NON_UTF8_CONTENT；读取失败：500 IO_ERROR。这些情况为 allowed=true、executed=false、data=null。请求体自身超限在策略判断前拒绝，allowed=false。

### 5.2 写入文件

请求 `WriteFileRequest`：

```json
{
  "task_id": "task-001",
  "tool": "write_file",
  "path": "src/main.rs",
  "grant_id": "grant-001",
  "content": "fn main() { println!(\"fixed\"); }\n"
}
```

task_id、tool、path、content 必填。grant_id 在成功调用时必须提供；专门允许先解析省略 grant_id 的写请求，再按策略顺序返回 LEASE_REQUIRED。显式 null、空字符串或非法 ID 格式属于 400。content 可以为空字符串。

成功 `200`：

```json
{
  "request_id": "req-write",
  "instance_id": "inst-demo",
  "allowed": true,
  "executed": true,
  "reason_code": "OK",
  "message": "写入成功",
  "duration_us": 620,
  "data": {
    "bytes_written": 33,
    "grant_id": "grant-001",
    "remaining_uses": 0
  }
}
```

### 5.3 路径、校验顺序和次数

路径必须精确匹配允许列表。拒绝绝对路径、驱动器前缀、反斜线、空字节、空路径段、`.` 和 `..` 段、符号链接及非普通文件；不用字符串前缀判断安全，不重复 URL 解码。只覆盖已有文件，不支持追加、创建目录、删除或重命名。

已认证工具请求按以下顺序检查，返回第一项失败：

1. 字段结构和长度、tool 名称、content 字节上限；未知工具返回 UNKNOWN_TOOL。
2. Agent Token 绑定的 task_id 与请求一致；指向任意其他任务均为 TASK_TOKEN_MISMATCH，不泄露目标是否存在。
3. 当前阶段允许该工具；inspect 或 verify 写入优先返回 INVALID_STAGE。
4. 路径规则和允许列表检查。
5. 写请求的 grant_id：缺失、查找、绑定、撤销、到期、次数依次检查。
6. 受控 I/O 和结果记录。

同一任务内检查、文件替换和扣次串行完成。成功替换后扣一次；拒绝和替换前失败不扣次。一份有效的一次性租约收到 10 个无故障并发写请求时，恰好一次成功，其余 LEASE_USED_UP。

TTL 在进入执行临界区时判定；now=deadline 已过期，已通过检查且开始执行的 I/O 可完成。使用同目录临时文件写完再替换；本期不承诺抵御恶意并发文件系统修改或断电事务恢复。

### 5.4 执行结果与审计故障

| 情况 | HTTP | allowed | executed | data 与次数 |
| --- | --- | --- | --- | --- |
| 正常读取或写入 | 200 OK | true | true | 返回结果；写入扣一次 |
| 策略拒绝 | 403 或 UNKNOWN_TOOL 的 400 | false | false | null；不扣次 |
| 策略通过后的 I/O 失败 | 404/413/422/500 | true | false | null；写入未替换时不扣次 |
| 服务已审计不可用，尚未判断策略 | 503 AUDIT_UNAVAILABLE | false | false | null；不执行 |
| 策略允许，但 intent 写入失败 | 503 AUDIT_UNAVAILABLE | true | false | null；不执行 |
| 文件成功写入，但 result 写入失败 | 503 AUDIT_UNAVAILABLE | true | true | 保留 WriteResult；次数已扣 |
| 读取已完成，但 result 写入失败 | 503 AUDIT_UNAVAILABLE | true | true | 不返回文件内容，data=null |

拒绝事件落盘失败时返回 503 AUDIT_UNAVAILABLE，替代原拒绝码，allowed=false、executed=false。任何审计故障使服务进入 degraded；管理员修复日志后重启，旧任务和 Agent Token 失效。

任务创建、阶段变更、租约发放和撤销在成功写入管理事件后提交内存状态，日志失败则不改变状态。degraded 时只读查询仍可用，工具执行及管理变更返回 503；健康检查返回 degraded。

不提供通用幂等键；创建任务、推进阶段、发放租约、工具请求均不得默认自动重试。超时、断连和 503 executed=true 都需要查明状态后由管理员决定下一步。

## 6 审计事件查询

`GET /v1/admin/events`，Admin Token。

### 6.1 参数与游标

| query | 必填 | 默认 | 约定 |
| --- | --- | --- | --- |
| task_id | 否 | 全部任务 | 当前实例的合法任务 ID；不存在返回 404 |
| after | 否 | 当前实例起点 | 不透明游标；after= 与省略等价 |
| limit | 否 | 100 | 整数 1～500 |

游标由服务端返回，客户端原样保存。格式固定为 `<instance_id>:<sequence>`，例如 `inst-demo:12`；sequence 为无前导零的十进制整数，可为 0，最大 2^64−1。event_id 为同实例内从 1 开始递增的十进制字符串，避免 JavaScript 整数精度损失。

- after 是排他下界，只查询 event_id 大于该位置的事件，按 event_id 数值升序。
- 先在日志锁内确定本次快照最大序号，再过滤 task_id。若还有符合筛选的事件未返回，has_more=true，next_cursor 指向最后一条返回事件。
- 若本次快照中没有更多符合筛选的事件，has_more=false，next_cursor 指向快照最大序号；空页也如此，避免重复扫描其他任务的事件。
- 空日志的 next_cursor 为 `<instance_id>:0`。has_more 只描述当前快照，新事件可能随后到来。
- 游标格式错误或序号超出当前实例末尾为 `400 INVALID_CURSOR`；实例不匹配优先返回 `409 CURSOR_INSTANCE_MISMATCH`，响应携带当前 instance_id，不能静默沿用旧游标。
- 校验次序为参数语法、游标实例和范围、任务存在性。旧任务和旧游标同时失效时先返回游标实例不匹配。
- task_id 改变时客户端清空游标。`result`、时间区间、排序字段都不是本期 API 参数，传入返回 400。

### 6.2 AuditEvent

所有字段都存在，不适用时为 null；event_id、instance_id、request_id、timestamp、event_type 始终非空。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| event_id | 十进制字符串 | 本实例递增序号，1～2^64−1 |
| instance_id、request_id | ID | 服务实例与操作关联 ID |
| task_id | ID 或 null | 未认证或无法可靠绑定任务时为 null |
| timestamp | 时间 | UTC 事件时间，不依赖时钟顺序排序 |
| event_type | EventType | task_created、phase_changed、grant_issued、grant_revoked、tool_intent、tool_result |
| phase | Phase 或 null | 当前阶段；管理事件记录变更后的阶段 |
| tool | string 或 null | 支持记录未知工具名称，最多 64 字符 |
| path | string 或 null | 转义后的相对或拒绝输入路径，最多 1024 字符 |
| grant_id | ID 或 null | 租约关联；无可靠值时为 null |
| allowed、executed | boolean 或 null | 工具结果使用布尔值；管理事件为 null |
| reason_code | string 或 null | 结果码；tool_intent 为 null |
| duration_us、decision_us | 非负整数或 null | 总耗时、纯策略耗时；未知时为 null |
| remaining_uses | 0～10 或 null | 已知租约的事件后快照 |
| expires_at | 时间或 null | 已知租约的到期展示时间 |
| revoked | boolean 或 null | 已知租约的撤销快照 |
| from_phase、to_phase | Phase 或 null | 仅 phase_changed 使用，其余为 null |

tool_intent 的 allowed=true、executed=null、reason_code=null、duration_us=null；tool_result 使用最终布尔值和结果码。管理事件 allowed/executed/decision_us 均为 null、reason_code=OK。没有 result 的 intent 显示“结果未确认”，不能当作拒绝或成功。事件绝不含 Token 或文件内容。

### 6.3 成功响应

成功 `200`，`EventListResponse`：

```json
{
  "request_id": "req-events",
  "instance_id": "inst-demo",
  "events": [
    {
      "event_id": "12",
      "instance_id": "inst-demo",
      "request_id": "req-write",
      "task_id": "task-001",
      "timestamp": "2026-10-26T02:00:11Z",
      "event_type": "tool_result",
      "phase": "edit",
      "tool": "write_file",
      "path": "src/main.rs",
      "grant_id": "grant-001",
      "allowed": true,
      "executed": true,
      "reason_code": "OK",
      "duration_us": 620,
      "decision_us": 25,
      "remaining_uses": 0,
      "expires_at": "2026-10-26T02:01:10Z",
      "revoked": false,
      "from_phase": null,
      "to_phase": null
    }
  ],
  "next_cursor": "inst-demo:12",
  "has_more": false
}
```

本期仅查询当前实例事件。重启后历史 JSONL 仍保留，但不通过当前实例接口重放。事件接口自身的 GET 查询不产生审计事件，避免轮询无限生成新事件。

## 7 健康检查

`GET /healthz`，不需要 Token，无参数。

健康时 `200`，`HealthResponse`：

```json
{
  "request_id": "req-health",
  "instance_id": "inst-demo",
  "status": "ok",
  "audit_available": true,
  "reason_code": "OK",
  "message": "服务可用"
}
```

审计不可用时仍返回 HealthResponse，但 HTTP=503、status=degraded、audit_available=false、reason_code=AUDIT_UNAVAILABLE。这是进程存活及本期执行前提检查，不表示所有业务功能已通过测试。不返回令牌、路径或配置详情。

## 8 Vue 前端只读代理

### 8.1 固定映射

| 浏览器接口 | Rust 上游 | 参数与成功响应 |
| --- | --- | --- |
| GET /api/tasks | GET /v1/admin/tasks | limit；TaskListResponse |
| GET /api/tasks/{task_id} | GET /v1/admin/tasks/{task_id} | path task_id；TaskDetailResponse |
| GET /api/events | GET /v1/admin/events | task_id、after、limit；EventListResponse |
| GET /api/health | GET /healthz | 无参数；HealthResponse |

代理不包装成功响应，保留上游 HTTP 状态、JSON、X-Request-Id。Admin Token 仅加入前三种上游请求；浏览器 Authorization 被忽略并剥离，不能替代代理身份。

### 8.2 代理验证和失败

代理按 Host、存在时的 Origin、路径、方法、参数、固定上游调用的顺序处理。Host 按显式允许的主机名和端口比较，不做子串匹配。默认允许实际使用的 `127.0.0.1:5173`、`127.0.0.1:4173`；如使用 localhost，应在配置中逐项列出。

Origin 存在时须精确匹配已配置的本机 origin；正常同源 GET 不带 Origin 可通过。拒绝任意来源 CORS，不开放公网。该模式是本地只读展示边界，不是多用户认证系统。

- 已映射路径上的非 GET 方法（包括 HEAD、OPTIONS）返回 `405 METHOD_NOT_ALLOWED`，Allow 为 GET，不调用 Rust。路径先于方法判定，POST /api/unknown 返回 404。
- 未映射 `/api`、`/api/*` 为 `404 ROUTE_NOT_FOUND`；不存在的任务仍是上游 `TASK_NOT_FOUND`。
- 参数非法为 `400 INVALID_REQUEST`；不允许任意 URL、上游地址、路径编码绕过、重复 query 或浏览器传来的 result 参数。
- Host 或 Origin 不匹配为 `403 HOST_DENIED` 或 `403 ORIGIN_DENIED`。
- 无法连接 Rust 为 `502 BROKER_UNAVAILABLE`；5 秒上游请求超时为 `504 BROKER_TIMEOUT`。
- 上游返回非 JSON、损坏 JSON 或不符合响应契约的主体，为 `502 BROKER_INVALID_RESPONSE`；不得转发上游 HTML 错误页。

代理自身错误使用 ErrorResponse，instance_id=null；不得暴露网络堆栈、管理令牌或宿主机路径。代理错误 message 可读，客户端依赖 HTTP 和 reason_code。

### 8.3 Vite 与 Router 对接

Vite 使用固定 5173 端口和 strictPort，server.proxy 将 `/api` 原样转到 4173，不把 Admin Token 写入配置。前端仅使用 `VITE_API_BASE=/api` 等公开配置。

| Vue 页面 | 使用接口 | 注意事项 |
| --- | --- | --- |
| /dashboard | /api/health、/api/tasks、/api/events | 事件统计明确为已加载范围 |
| /tasks | /api/tasks | 显示 total 和 truncated 提示 |
| /tasks/:taskId | /api/tasks/{task_id}、/api/events?task_id=... | 详情中的 grants 为权限展示依据 |
| /audit | /api/events | 路由 taskId 转 query task_id；result 仅本地筛选 |

只统计 tool_result：OK 且 executed=true 为成功；UNKNOWN_TOOL、INVALID_TOKEN、ROLE_DENIED、TASK_TOKEN_MISMATCH、INVALID_STAGE、PATH_DENIED、LEASE_REQUIRED、LEASE_NOT_FOUND、LEASE_MISMATCH、LEASE_REVOKED、LEASE_EXPIRED、LEASE_USED_UP 归 denied；其余输入错误、I/O 和审计故障归 failed。管理事件和 intent 不进入工具成功率分母。

页面离开时取消请求和计时器；切换任务清空游标；旧响应不得覆盖新任务。has_more=true 时继续读取下一页，否则按 1 秒轮询，不重叠请求。遇到 CURSOR_INSTANCE_MISMATCH 清空全部旧实例状态后重载；其他只读网络错误可提示后重新查询，不能转为修改请求。

构建后由只读代理托管 front/dist，Vue Router 的页面深链接可回退到 index.html；`/api` 和缺失静态资源不得回退 HTML。路由守卫只负责导航，Rust 才是权限判断方。

## 9 错误码与校验优先级

### 9.1 完整错误表

| HTTP | reason_code | 使用场景 |
| --- | --- | --- |
| 400 | INVALID_REQUEST | 字段、ID、参数、未知字段或配置工作区引用非法 |
| 400 | UNKNOWN_TOOL | execute 的 tool 不是 read_file 或 write_file |
| 400 | INVALID_CURSOR | 事件游标格式、数值或位置非法 |
| 401 | INVALID_TOKEN | 缺失、格式错误、无效或旧 Agent Token |
| 403 | ROLE_DENIED | 有效令牌调用不属于该角色的接口 |
| 403 | TASK_TOKEN_MISMATCH | Agent Token 与请求 task_id 不一致 |
| 403 | INVALID_STAGE | 工具或发放租约不被当前阶段允许 |
| 403 | PATH_DENIED | 路径不允许、穿越、符号链接或非普通文件 |
| 403 | LEASE_REQUIRED | write_file 省略 grant_id |
| 403 | LEASE_NOT_FOUND | 当前任务下找不到该租约 |
| 403 | LEASE_MISMATCH | 租约工具或路径与写请求不符 |
| 403 | LEASE_REVOKED | 租约被撤销 |
| 403 | LEASE_EXPIRED | 租约到期，包含等于到期点 |
| 403 | LEASE_USED_UP | 租约次数耗尽 |
| 403 | HOST_DENIED / ORIGIN_DENIED | 只读代理拒绝未允许的来源 |
| 404 | ROUTE_NOT_FOUND | 不存在的 API 路径 |
| 404 | TASK_NOT_FOUND | 管理查询或变更指向不存在的任务 |
| 404 | FILE_NOT_FOUND | 允许路径上的目标文件缺失 |
| 405 | METHOD_NOT_ALLOWED | 已知接口使用不支持的方法 |
| 409 | INVALID_TRANSITION | 非相邻前进、回退或重复阶段变更 |
| 409 | GRANT_CONFLICT | 同资源已有 active 租约 |
| 409 | CURSOR_INSTANCE_MISMATCH | after 来自其他服务实例 |
| 413 | PAYLOAD_TOO_LARGE | 请求体或 UTF-8 文件内容超限 |
| 415 | UNSUPPORTED_MEDIA_TYPE | 有 JSON 请求体却不是 application/json |
| 422 | NON_UTF8_CONTENT | 允许读取的文件不是 UTF-8 |
| 500 | IO_ERROR | 文件执行或元数据读取失败 |
| 500 | INTERNAL_ERROR | 未预期的内部错误，不泄露细节 |
| 502 | BROKER_UNAVAILABLE | 代理无法建立上游连接 |
| 502 | BROKER_INVALID_RESPONSE | 上游响应格式或结构错误 |
| 503 | AUDIT_UNAVAILABLE | 审计故障，工具执行和管理变更停止 |
| 504 | BROKER_TIMEOUT | 代理上游请求超过 5 秒 |

代理转发上游错误时不改码。健康接口 503 使用 HealthResponse；已匹配的 execute POST 使用 ToolResponse；其他错误使用 ErrorResponse。未知路由或错误方法在路由层使用 ErrorResponse。

### 9.2 Rust 通用检查次序

1. 匹配路由和方法，未知路由 404、方法错误 405。
2. 限制请求体体积；过大为 413。
3. 鉴权和角色检查；在查看资源是否存在之前完成。
4. 若审计已不可用，工具执行和管理变更直接 503；只读查询继续。
5. 验证媒体类型、JSON/query/ID 和各字段，再按接口业务顺序检查。
6. 在对应锁内完成检查、审计和状态变更。新的审计失败按第 5.4 节覆盖结果。

框架产生的 JSON 解析、缺少字段、错误方法和体积错误必须统一映射，不能返回默认 HTML 或另一套结构。不要使用泛化 `200 + success=false` 表达错误。

## 10 最小联调流程

下面是目标服务实现后可执行的 Linux/WSL Bash 示例，依赖 curl 和 jq。先配置 demo-rust 工作区及 src/main.rs；ADMIN_TOKEN 从环境变量读取，禁止把真实令牌提交到仓库。不要开启 shell 的命令回显调试。

```bash
set -eu
: "${ADMIN_TOKEN:?请先在本地环境中设置管理员令牌}"
BROKER_URL=http://127.0.0.1:8080

# 1. 健康检查与创建任务
curl --fail-with-body -sS "$BROKER_URL/healthz"
TASK_JSON=$(curl --fail-with-body -sS -X POST "$BROKER_URL/v1/admin/tasks" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  --data '{"workspace_id":"demo-rust"}')
TASK_ID=$(printf '%s' "$TASK_JSON" | jq -er '.task_id')
AGENT_TOKEN=$(printf '%s' "$TASK_JSON" | jq -er '.agent_token')

# 2. inspect 读取成功
jq -nc --arg id "$TASK_ID" \
  '{task_id:$id,tool:"read_file",path:"src/main.rs"}' |
  curl --fail-with-body -sS -X POST "$BROKER_URL/v1/tool/execute" \
    -H "Authorization: Bearer $AGENT_TOKEN" -H 'Content-Type: application/json' --data-binary @-

# 3. 管理员切 edit 并授予一次性租约
curl --fail-with-body -sS -X POST "$BROKER_URL/v1/admin/tasks/$TASK_ID/phase" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  --data '{"phase":"edit"}'
GRANT_JSON=$(curl --fail-with-body -sS -X POST "$BROKER_URL/v1/admin/tasks/$TASK_ID/grants" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  --data '{"tool":"write_file","path":"src/main.rs","ttl_seconds":60,"max_uses":1}')
GRANT_ID=$(printf '%s' "$GRANT_JSON" | jq -er '.grant.grant_id')

# 4. 第一次写入成功，第二次返回 403 LEASE_USED_UP
WRITE_JSON=$(jq -nc --arg id "$TASK_ID" --arg gid "$GRANT_ID" \
  '{task_id:$id,tool:"write_file",path:"src/main.rs",grant_id:$gid,content:"fn main() { println!(\"fixed\"); }\n"}')
printf '%s' "$WRITE_JSON" |
  curl --fail-with-body -sS -X POST "$BROKER_URL/v1/tool/execute" \
    -H "Authorization: Bearer $AGENT_TOKEN" -H 'Content-Type: application/json' --data-binary @-
printf '%s' "$WRITE_JSON" |
  curl -sS -i -X POST "$BROKER_URL/v1/tool/execute" \
    -H "Authorization: Bearer $AGENT_TOKEN" -H 'Content-Type: application/json' --data-binary @-

# 5. 查询租约耗尽快照并推进 verify
curl --fail-with-body -sS "$BROKER_URL/v1/admin/tasks/$TASK_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
curl --fail-with-body -sS -X POST "$BROKER_URL/v1/admin/tasks/$TASK_ID/phase" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  --data '{"phase":"verify"}'

# 6. 从浏览器对应代理读取事件；此调用不含 Admin Token
curl --fail-with-body -sS --get 'http://127.0.0.1:4173/api/events' \
  --data-urlencode "task_id=$TASK_ID" --data-urlencode 'limit=100'

# 7. 结束任务，done 下工具读写均应被拒绝
curl --fail-with-body -sS -X POST "$BROKER_URL/v1/admin/tasks/$TASK_ID/phase" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  --data '{"phase":"done"}'
unset AGENT_TOKEN TASK_JSON GRANT_JSON WRITE_JSON
```

上述命令用于人工联调，不替代自动化验收。还应核对 inspect/verify 写入拒绝、撤销后拒绝、过期边界、非法路径、Agent 调管理接口、错误 Origin、代理 POST、游标跨实例及写成功后审计失败。

## 11 接口交付与同步规则

后端由成员 A 实现并维护 Rust 契约；成员 B 实现只读代理和 Vue 调用。修改字段、错误码、默认值或示例时，同时更新本文、OpenAPI、前端类型和相应测试。

联调至少完成：9 个 Rust 操作、4 个代理操作、正常与失败响应形状、字节上限、租约并发、事件增量与重启恢复。OpenAPI 的 JSON Schema 可检查结构，不能替代对 UTF-8 字节数、时钟、并发、权限与副作用的运行时检查。

本期前后端可以独立启动与构建；相同 Git 仓库和同源静态托管不改变其接口边界。后续添加管理页面前必须重新设计浏览器认证，不得将已有只读代理变成任意管理请求转发器。


