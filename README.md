# AgentFence

AgentFence Lite 是一个基于 Rust 的 Agent 工具调用权限控制与审计原型，前端采用 Vue 3、Vite 和 Vue Router。

已完成需求与接口设计，并在 `front/` 创建 Vue 前端工程。Rust 后端与只读代理尚待实现。

## 运行前端

```bash
cd front
npm ci
npm run dev
```

访问 http://127.0.0.1:5173。项目结构与构建说明见 [前端 README](front/README.md)。

## 项目文档

- [需求与实施方案](docs/AgentFence_Lite_5Day_Requirements_Detailed_v2.docx)
- [HTTP 接口文档](docs/api-contract.md)：业务接口、字段、鉴权、错误码、事件分页和联调示例。
- [OpenAPI 描述](docs/openapi.json)：用于导入支持 OpenAPI 3.1 的接口工具。

