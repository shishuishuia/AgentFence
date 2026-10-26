# AgentFence Lite 前端

项目目录为 `front`，基于官方 create-vue 初始化，使用 Vue 3、Vite、TypeScript 和 Vue Router。已提供路由、页面骨架、统一只读 API 封装及健康连接检测。

## 启动

验证环境：Node.js 24.14.1、npm 11.11.0。依赖由 package-lock.json 固定；jsdom 固定 26.1.0，以兼容当前 Node.js。

```bash
cd front
npm ci
npm run dev
```

打开 http://127.0.0.1:5173。端口被占用时直接报错，不自动切换。

## 路由

| 地址 | 页面 |
| --- | --- |
| / | 重定向到 /dashboard |
| /dashboard | 总览、真实健康连接检测 |
| /tasks | 任务列表骨架 |
| /tasks/:taskId | 任务详情骨架 |
| /audit | 审计页面骨架 |
| 其他路径 | 404 页面 |

任务、详情、审计页面尚未接入业务数据，不提供模拟任务或伪造统计。Rust Broker 已合入仓库，只读代理仍待实现；代理未启动时，检测连接会提示不可用，不影响页面运行。

## 接口联调

浏览器请求 `/api`，Vite 转发到 `http://127.0.0.1:4173`。只读代理再访问 `http://127.0.0.1:8080` 的 Rust 服务。接口约定见 [接口文档](../docs/api-contract.md)。

`.env.example` 的 `VITE_API_BASE=/api` 是公开配置；不在前端源码、VITE_ 环境变量或浏览器存储中保存 Admin Token。

`src/api/` 提供 getHealth、getTasks、getTask、getEvents；`src/types/api.ts` 定义对应类型。调用方可以传入 AbortSignal，在页面卸载或路由切换时取消请求。封装没有写操作，也不自动重试。

当前客户端类型来自 [前期接口设计稿](../docs/api-design.md)。新合入后端的健康响应、事件 ID 类型、事件名称等存在差异，接入业务数据前需要按后端契约对齐。

## 检查与构建

```bash
npm run lint
npm run test:unit
npm run build
```

build 包含类型检查，输出 `front/dist`。`npm run test:watch` 监听测试；`npm run format` 格式化代码。

停止开发服务后，可以运行 `npm run preview` 在同一 5173 端口检查构建结果；该模式只用于本地预览。实际演示由只读代理托管 dist 并处理 /api，Vue 页面深链接需回退到 index.html，排除 API 和缺失静态资源。

## 目录

```text
src/
  api/          只读请求与错误处理
  assets/       全局样式
  components/   公共页面骨架
  router/       页面路由及标题
  types/        接口类型
  views/        页面组件
```
