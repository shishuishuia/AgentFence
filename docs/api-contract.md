# AgentFence Lite v2.1 HTTP 契约

状态：成员 A 已实现的对接候选，待成员 B 联调确认。日期：2026-10-26。尚不声称已经获得成员 B 确认。以下示例 ID、时间和耗时用于说明格式，非实验数据。

## 公共约定

- Broker 默认 `http://127.0.0.1:8080`，仅允许回环地址；不托管 Vue，不提供任意来源 CORS。
- 业务请求使用 JSON，带 `Content-Type: application/json`。未知 JSON 字段、未知查询参数、类型/范围错误返回 `400 INVALID_REQUEST`；DELETE 撤销接口要求空 body。
- 身份在 `Authorization: Bearer <token>` 中传递。Admin Token 从服务环境读取，要求 32～256 个可见 ASCII 字节；推荐 32 字节系统随机数的十六进制表示。不得使用示例测试凭证部署。
- Agent Token 为 32 字节系统随机数的十六进制表示，具有 256 位随机熵，只在创建响应展示一次。内存中只保存凭证的 SHA-256 摘要。它与单个任务绑定，不能调用管理接口。
- 非法凭证是 `401 INVALID_TOKEN`；有效凭证角色错误是 `403 ROLE_DENIED`。Agent 指向另一个或不存在的任务统一是 `403 TASK_TOKEN_MISMATCH`。
- 任务、Token、租约及在线事件均为实例内存状态。重启后全部旧 Token 失效；JSONL 仅保留历史，不在线恢复旧任务/游标。
- ID 参数为 1～128 个 ASCII 字母、数字、`_` 或 `-`。服务生成 `task_<32hex>`、`grant_<32hex>`、`inst_<32hex>`；客户端应视为不透明字符串。
- UTC 时间使用 RFC3339 字符串，接受标准的 `Z` 或 `+00:00` 表示。TTL 判断只使用服务端单调时钟。

## 路由

| 方法 | 路径 | 身份 | 成功状态 |
| --- | --- | --- | --- |
| GET | `/healthz` | 无 | 200；审计已失效时 503 |
| POST | `/v1/admin/tasks` | Admin | 201 |
| GET | `/v1/admin/tasks` | Admin | 200 |
| GET | `/v1/admin/tasks/{id}` | Admin | 200 |
| POST | `/v1/admin/tasks/{id}/phase` | Admin | 200 |
| POST | `/v1/admin/tasks/{id}/grants` | Admin | 201 |
| DELETE | `/v1/admin/tasks/{id}/grants/{gid}` | Admin | 200 |
| POST | `/v1/tool/execute` | Agent | 200 |
| GET | `/v1/admin/events` | Admin | 200 |

未知路由返回 JSON `404 NOT_FOUND`；不支持的方法返回 JSON `405 METHOD_NOT_ALLOWED`。管理前缀和工具路径先鉴权，再进入具体路由。所有错误响应包含下面的统一结构和 `request_id`。

## 健康与任务

`GET /healthz` 正常响应：

```json
{"status":"ok","instance_id":"inst_example"}
```

健康表示审计启动检查通过且尚未发生审计失败。它不承诺预知下一次磁盘写入是否成功。任何审计写入失败都会使健康状态永久变为 503，直到修复日志问题并重启。查询已有任务/事件仍可用于排查。

`POST /v1/admin/tasks`：

```json
{"workspace_id":"demo"}
```

响应示例：

```json
{
  "task_id":"task_example",
  "workspace_id":"demo",
  "phase":"inspect",
  "created_at":"2026-10-26T00:00:00Z",
  "updated_at":"2026-10-26T00:00:00Z",
  "agent_token":"<仅此一次返回的临时凭证>",
  "request_id":"req_example",
  "instance_id":"inst_example"
}
```

`workspace_id` 必须在配置登记。客户端不能提交宿主机路径或自选 Token。

`GET /v1/admin/tasks?limit=50`：limit 为 1～100，默认 50，没有 offset。按 `created_at` 倒序；时间相同时按创建顺序倒序。每个 item 仅包含 `task_id`、`workspace_id`、`phase`、`created_at`、`updated_at`。

```json
{"items":[],"total":0,"truncated":false,"instance_id":"inst_example"}
```

`total` 为列表取样时的当前实例任务总数，`truncated = total > limit`。界面必须标明“最近任务”，不能声称完整分页历史。`updated_at` 在成功阶段变更、发放/撤销租约、成功文件写入时更新，读取、查询及拒绝不更新；重复成功撤销也更新。

`GET /v1/admin/tasks/{id}` 使用**扁平结构**：

```json
{
  "task_id":"task_example",
  "workspace_id":"demo",
  "phase":"edit",
  "created_at":"2026-10-26T00:00:00Z",
  "updated_at":"2026-10-26T00:00:01Z",
  "allowed_paths":["Cargo.toml","src/main.rs"],
  "grants":[
    {
      "grant_id":"grant_example","task_id":"task_example",
      "tool":"write_file","path":"src/main.rs",
      "expires_at":"2026-10-26T00:01:01Z",
      "remaining_uses":1,"revoked":false,"status":"active"
    }
  ],
  "server_time":"2026-10-26T00:00:02Z",
  "instance_id":"inst_example"
}
```

阶段、租约和状态在同一任务锁下生成，所有租约使用同一次时钟采样。`allowed_paths` 按字典顺序，`grants` 保留发放顺序及历史失效租约。`status` 优先级为：阶段非 edit → `stage_inactive`；已撤销 → `revoked`；到期 → `expired`；次数为零 → `used_up`；否则 `active`。前端倒计时仅用于展示，不决定权限。

查询响应不包含 Token、绝对根目录或文件内容。合法管理员查询不存在的任务返回 `404 TASK_NOT_FOUND`。

## 阶段和租约

`POST /v1/admin/tasks/{id}/phase`：

```json
{"phase":"edit"}
```

仅允许 `inspect → edit → verify → done`。重复、回退、跳跃返回 `409 INVALID_TRANSITION`；未知枚举返回 400。成功返回 `request_id`、`task_id`、`phase`、`updated_at`、`instance_id`。

`POST /v1/admin/tasks/{id}/grants`：

```json
{"tool":"write_file","path":"src/main.rs","ttl_seconds":60,"max_uses":1}
```

`tool`、`path` 必填；只支持 write_file；ttl 为整数 1～300，省略时 60；次数为整数 1～10，省略时 1。仅 edit 可发放，且路径必须精确匹配允许列表。同任务同工具同路径已存在 active 租约时返回 `409 GRANT_CONFLICT`。允许文件可暂时缺失，发放不创建文件。

成功返回上面详情中单个 grant 的全部字段，另含 `request_id`。撤销、到期或用尽后可以重新发放不同 ID 的新租约。旧租约不恢复。

`DELETE /v1/admin/tasks/{id}/grants/{gid}` 无 body；成功返回该租约快照及 `request_id`。重复撤销返回 200，次数不恢复。撤销成功响应后开始执行的新请求不能使用该租约。先前已进入执行区的请求可能完成；撤销会等待其结束。

租约查找仅限当前任务，外部任务的 grant_id 视为 `LEASE_NOT_FOUND`；本任务租约与工具/路径不符才返回 `LEASE_MISMATCH`。

## 文件工具

读请求只允许这三个字段，连 `content: null` 或 `grant_id: null` 也不接受：

```json
{"task_id":"task_example","tool":"read_file","path":"src/main.rs"}
```

写请求：

```json
{
  "task_id":"task_example","tool":"write_file","path":"src/main.rs",
  "grant_id":"grant_example","content":"fn main() { println!(\"fixed\"); }\n"
}
```

content 必须是字符串；空字符串合法。缺 content 是 `400 INVALID_REQUEST`，缺 grant_id 是 `403 LEASE_REQUIRED`。同时缺失时先返回 INVALID_REQUEST。

统一响应结构：

```json
{
  "request_id":"req_example",
  "allowed":true,
  "executed":true,
  "reason_code":"OK",
  "message":"operation completed",
  "duration_us":210,
  "decision_us":1,
  "data":{"bytes_written":32,"grant_id":"grant_example","remaining_uses":0},
  "instance_id":"inst_example"
}
```

读取 data 为 `{"content":"...","bytes_read":32}`；写入 data 为 `bytes_written`、`grant_id`、`remaining_uses`。UTF-8 字节数与字符数不同。错误通常 `data:null`；执行成功后结果审计失败会保留实际 data，同时返回 503 和 `executed:true`。

| 情况 | allowed | executed | 结果 |
| --- | --- | --- | --- |
| 成功 | true | true | 200 OK |
| 策略拒绝 | false | false | 对应 403 |
| 允许路径缺失/非 UTF-8/读取超限/I/O 失败 | true | false | 对应 404/422/413/500 |
| 提交内容超限 | false | false | 413 PAYLOAD_TOO_LARGE |
| intent 审计失败 | false | false | 503 AUDIT_UNAVAILABLE |
| 文件已执行而 result 审计失败 | true | true | 503 AUDIT_UNAVAILABLE |

HTTP body 上限 512 KiB，包含 JSON 编码开销。工作区内容上限默认 64 KiB，可配置为更小的正整数。读取最多获取上限加一字节用于判断，不会无限读取。

`duration_us` 是从收到请求到构建响应的服务端耗时，包含排队、认证、I/O 和最终审计，不含响应序列化及网络发送。`decision_us` 只累计纯阶段/路径字符串/租约规则，排除锁、文件元数据、实际读写和日志，整数微秒分辨率下可能为 0。独立 core benchmark 另保留浮点微秒原始样本。

### 错误顺序与资源规则

1. 先限制传输大小；再认证和检查角色。无效凭证不能探测任务。
2. 检查 JSON 结构、工具名、字段格式与内容字节上限，然后检查 Token 与任务绑定。
3. 检查阶段，再检查相对路径语法与精确允许列表。
4. 先确认 pending intent 日志，再检查文件系统中的路径组成部分；符号链接/非普通文件为 PATH_DENIED。
5. 租约依次检查：显式 ID、任务内存在性、绑定、撤销、到期、次数。
6. 最后执行 I/O。允许路径缺失在此返回 FILE_NOT_FOUND，不伪装成 PATH_DENIED。

因此 verify 使用到期租约仍返回 INVALID_STAGE；写请求 content 缺失先于阶段失败；缺失文件且没有写租约先返回 LEASE_REQUIRED。

路径只接受 `/` 分隔的明确相对文件名。拒绝空路径/空段、`.`、`..`、反斜线、驱动器/ADS 冒号、控制字符、通配符、Windows 设备别名及尾随点/空格。JSON 解码后不做 URL 解码。对每级目录和最终文件拒绝符号链接/重解析点，再按路径组成部分检查规范路径仍在根目录内。只覆盖已有文件，不创建目录、追加、删除或重命名资源。

每任务锁覆盖阶段、发放、撤销、执行和消费；同 workspace_id 的工具操作另行串行。多个任务引用同一工作区意味着共享文件内容，按实际替换顺序生效，不提供任务间内容隔离。管理员不应将同一物理目录以多个 ID 重复登记；本期不承诺跨不同注册 ID 的文件事务。

TTL 在进入受控临界区时采样，`now >= deadline` 为过期。已进入且通过判定的 I/O 可完成。只有替换成功才扣次；拒绝和确认替换前失败不扣次。客户端断连或超时不取消已排入 blocking executor 的写操作，**不能自动重试写请求**。

## 事件查询与看板映射

`GET /v1/admin/events?task_id=task_example&after=0&limit=100`。

- task_id 可选，省略时查询当前实例全部事件；指定不存在的任务返回 TASK_NOT_FOUND。
- limit 为 1～500，默认 100。
- after 为不透明字符串：省略、空值或 `0` 表示实例起点；后续原样使用 next_cursor。格式为 `<instance_id>:<event_id>`。其他实例、非法数字或超过当前末尾的游标返回 `400 INVALID_REQUEST`，错误仍带当前 instance_id。
- event_id 是从 1 开始的实例内递增整数。过滤时游标可以越过其他任务事件，不遗漏后续匹配记录；数据耗尽时 next_cursor 推进到当前全局末尾。

```json
{"events":[],"next_cursor":"inst_example:0","has_more":false,"instance_id":"inst_example"}
```

所有事件包含：`event_id`、`instance_id`、`request_id`、`task_id`、`timestamp`、`phase`、`event_type`、`tool`、`path`、`grant_id`、`allowed`、`executed`、`reason_code`、`duration_us`、`decision_us`、`remaining_uses`、`expires_at`、`revoked`。不适用/未知字段为 null。未解析、未认证或任务绑定失败不记录客户端声称的资源；非法 path 最多保留 256 个 Unicode 字符，由 JSON 转义控制字符。

`event_type` 固定为 `intent`、`result`、`task_created`、`phase_changed`、`grant_issued`、`grant_revoked`。

- intent 的 reason_code 为仅审计使用的 `PENDING`，allowed/executed 均为 false；它记录尝试，不代表最终授权。缺少 result 时显示“执行结果未确认”。可能发生于后续路径/租约检查拒绝，不能将 intent 计为完成或失败。
- 每个已完成工具请求对应一条 result；按照 request_id 与 intent 关联。早期拒绝可能只有 result。
- 管理事件的 allowed/executed 表示该管理变更获得授权并提交；它们不参与工具调用统计。
- 事件 duration_us 在生成该事件时采样，不能包含自己尚未完成的那次 append。响应 duration_us 包含最终 append，两者可能有差异。
- 当前实例保留全部在线事件；五天演示无在线淘汰策略，不适合长期无界运行。历史跨实例查询使用脱敏导出 JSONL。

只对 `event_type=result` 分类：OK 且 executed=true 为成功；INVALID_STAGE、PATH_DENIED 及所有 LEASE_* 为 denied；IO_ERROR、AUDIT_UNAVAILABLE、FILE_NOT_FOUND、NON_UTF8_CONTENT 为 failed；INVALID_TOKEN、ROLE_DENIED、TASK_TOKEN_MISMATCH、INVALID_REQUEST、UNKNOWN_TOOL、PAYLOAD_TOO_LARGE、METHOD_NOT_ALLOWED 为 failed（输入/身份/传输异常，不计入策略越权拦截率）。前端的 result=allowed 表示成功分类；统计标明“当前加载范围”。

浏览器接口由 B 的只读代理映射：`/api/tasks` → 管理任务列表，`/api/tasks/:id` → 详情，`/api/events` → 事件，`/api/health` → 健康。代理限定 GET，持有环境变量中的 Admin Token，不转发浏览器凭证。Rust 不读取前端筛选参数 result，不支持代理任意 URL。

## 错误码汇总

| HTTP | reason_code |
| --- | --- |
| 400 | INVALID_REQUEST、UNKNOWN_TOOL |
| 401 | INVALID_TOKEN |
| 403 | ROLE_DENIED、TASK_TOKEN_MISMATCH、INVALID_STAGE、PATH_DENIED、LEASE_REQUIRED、LEASE_NOT_FOUND、LEASE_MISMATCH、LEASE_REVOKED、LEASE_EXPIRED、LEASE_USED_UP |
| 404 | TASK_NOT_FOUND、FILE_NOT_FOUND、NOT_FOUND |
| 405 | METHOD_NOT_ALLOWED |
| 409 | INVALID_TRANSITION、GRANT_CONFLICT |
| 413 | PAYLOAD_TOO_LARGE |
| 422 | NON_UTF8_CONTENT |
| 500 | IO_ERROR |
| 503 | AUDIT_UNAVAILABLE |

消息为安全的可读说明，不带宿主机绝对路径、凭证或文件内容。客户端按 reason_code 判断，不依赖 message 文本。代理专属 502 BROKER_UNAVAILABLE、504 BROKER_TIMEOUT 由 B 实现，不属于 Rust 路由。
