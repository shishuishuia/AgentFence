# AgentFence Lite 接口设计与浏览器边界

更新：2026-10-27，已按实际 Rust 和只读代理联调。Rust 字段、状态、错误优先级与示例以 [实际 HTTP 契约](api-contract.md) 为准；[OpenAPI](openapi.json) 同步描述 Rust 和浏览器只读接口。旧稿中的 tool_intent/tool_result、字符串事件 ID、包装 grant、409 游标错误和扩展健康字段已撤换。

## 调用关系

开发时浏览器 5173 → Vite /api → Python 4173 → Rust；构建展示时浏览器 4173 → Python 静态托管和 /api → Rust。Rust 默认回环 8080；自动演示使用操作系统分配的回环端口。Admin Token 从服务进程环境注入，浏览器无管理凭证。

## 浏览器查询

| 浏览器 GET | Rust GET | 参数 |
| --- | --- | --- |
| /api/health | /healthz | 无 |
| /api/tasks | /v1/admin/tasks | limit 默认 50，1～100 |
| /api/tasks/{task_id} | /v1/admin/tasks/{task_id} | ID 为 1～128 个 ASCII 字母、数字、下划线、连字符 |
| /api/events | /v1/admin/events | task_id、after、limit（默认 100，1～500） |

代理精确校验 Host 和存在时的 Origin，默认允许实际本机 4173/5173 地址。正常同源 GET 可以没有 Origin。未知 API 为 404 ROUTE_NOT_FOUND，已知 API 非 GET 为 405 METHOD_NOT_ALLOWED，非法或重复 query 为 400 INVALID_REQUEST。前端 result 筛选不传入代理。浏览器 Authorization、Cookie 和转发头均不向 Rust 透传；健康调用不添加代理的 Admin Token。

上游合法状态与 JSON 保留；代理连接失败 502 BROKER_UNAVAILABLE，5 秒超时 504 BROKER_TIMEOUT，异常 JSON/形状/截断为 502 BROKER_INVALID_RESPONSE。代理自身错误包含 request_id、instance_id=null、reason_code、message。API 不缓存、不自动重试、不跟随重定向；不提供任意上游 URL、管理写操作和任意来源 CORS。

## 实际响应要点

- 健康正常只有 status=ok、instance_id；审计失效返回 503 错误结构，已有任务/事件仍允许查询。
- 任务创建返回一次性 agent_token；查询不返回 Token、宿主机根路径或文件内容。列表 total 为当前实例总数，truncated 表示仅有最近任务，没有 offset 分页。
- 详情为扁平任务、allowed_paths、grants、server_time、instance_id。租约只有实际快照字段；不补造 created_at、ttl_seconds 或 max_uses。
- 发放和撤销返回扁平租约快照及 request_id；不含 grant 包装或 instance_id。
- 工具成功要求 reason_code=OK 且 executed=true。结果审计失败时，HTTP 503 也可能 executed=true，并保留实际 data，写请求不得盲目重试。
- 错误至少有四个公共错误字段；Rust 错误还包含 allowed、executed、duration_us、decision_us、data。代理不丢弃这些字段。
- 事件名为 intent/result；event_id 是 JSON 整数。管理事件和 intent 不参与完成统计。阶段事件 phase 是变更后的阶段。
- next_cursor 视为不透明字符串，包括空页也更新。非法、超前或跨实例游标为 400 INVALID_REQUEST；错误带当前 instance_id。
- UTC RFC3339 可使用 Z 或 +00:00，客户端不按展示倒计时决定租约是否有效。

## Vue 页面及请求生命周期

| 页面 | 查询与行为 |
| --- | --- |
| /dashboard | 健康、最近任务、当前加载范围的工具结果统计，各资源独立呈现错误 |
| /tasks | 最近任务、total/truncated、阶段和时间 |
| /tasks/:taskId | 任务详情、服务端租约状态、关联事件；TASK_NOT_FOUND 为业务 404 |
| /audit | taskId/result 存路由 query；taskId 映射 task_id，result 仅本地筛选 |

进入立即查询，上一轮完成后再等待 1 秒。同资源不堆积；手动刷新、切任务及离开取消旧请求。generation 防止旧响应/错误/finally 覆盖新页面；epoch 防止旧实例响应将状态切回。确认实例变化后清空旧状态并重载；同实例非法游标及不存在任务停止自动重试，可手动重载。断线保留数据并显示最后成功时间。

事件串行分页，每批最多 5 页、每页 100 条，按实例和事件 ID 去重，最多保留 2000 条并显示范围。只对 result 分类：OK 且 executed=true 为成功；INVALID_STAGE、PATH_DENIED、实际 LEASE_* 为策略拒绝；身份、输入、I/O、审计等为失败。未看到 result 的 intent 标为结果未确认；不把管理事件计入工具统计。

## 构建托管与验收

Python 托管 front/dist，仅页面导航回退 index.html；API、缺失静态资源和非法路径返回相应错误。详见 [代理说明](../web_proxy/README.md)、[展示入口](../demo/README.md)、[前端说明](../front/README.md)。开发和 dist 的 F01～F04 浏览器记录见 [B 验证报告](member-b-test-report.md)。

独立实验入口及 enforced/baseline 的差异见 [实验契约](http-experiment.md)；它不向正式服务增加策略关闭参数。完整安全黑盒、性能、权限窗口和 RSS 报告单独交付，不由看板统计代替。
