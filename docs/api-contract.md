# AgentFence Lite HTTP 接口草案

此文件由成员 A 维护，供成员 B 编写适配器。当前是草案，只有 `/healthz` 已实现；以下待实现接口须在双方确认 JSON 字段、认证及错误码后冻结。

## 已实现

`GET /healthz` 不需要认证，用于判断服务进程是否存活，不表示完整权限功能已就绪。

成功响应：HTTP `200`，`Content-Type: application/json`。

```json
{"status":"ok"}
```

未知路由返回 HTTP `404`：

```json
{"reason_code":"NOT_FOUND","message":"endpoint not found"}
```

## 待实现的需求接口

| 方法 | 路径 | 认证 | 用途 |
| --- | --- | --- | --- |
| POST | `/v1/admin/tasks` | Admin Token | 创建任务，返回任务 ID 和 Agent Token |
| POST | `/v1/admin/tasks/{id}/phase` | Admin Token | 按顺序推进任务阶段 |
| POST | `/v1/admin/tasks/{id}/grants` | Admin Token | 发放限定路径、有效期和次数的写租约 |
| DELETE | `/v1/admin/tasks/{id}/grants/{grant_id}` | Admin Token | 撤销租约 |
| POST | `/v1/tool/execute` | Agent Token | 执行白名单文件读写 |
| GET | `/v1/admin/events?task_id=...` | Admin Token | 获取审计事件 |

认证拟使用 `Authorization: Bearer <token>`。管理员凭证不能交给 Agent 或嵌入浏览器静态资源。Agent Token 必须绑定单个任务。

阶段 JSON 名称固定为 `inspect`、`edit`、`verify`、`done`；只允许相邻阶段顺序迁移，`done` 为终态。未知阶段不能反序列化成合法阶段。

需求规定的工具响应字段为 `request_id`、`allowed`、`reason_code`、`message`、`duration_us`；读取成功时如何返回内容，仍需在实现工具接口前确认。

需求规定成功返回 `200`、策略拒绝 `403`、输入非法 `400`、凭证非法 `401`、任务不存在 `404`、内部失败 `500`。至少包含以下拒绝码：`INVALID_TOKEN`、`INVALID_STAGE`、`PATH_DENIED`、`LEASE_REQUIRED`、`LEASE_EXPIRED`、`LEASE_REVOKED`、`LEASE_USED_UP`、`UNKNOWN_TOOL`。

审计最小字段为 `timestamp`、`request_id`、`task_id`、`phase`、`tool`、`path`、`allowed`、`reason_code`、`duration_us`。不记录 Token 或写入文件的完整内容。
