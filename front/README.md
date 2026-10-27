# AgentFence Lite 前端

目录 front，Vue 3 + Vite + TypeScript + Vue Router，基于官方 create-vue。实际验证 Node.js 24.14.1、npm 11.11.0，依赖由 package-lock.json 固定。

## 开发与展示

```sh
npm --prefix front ci
# 终端一（先构建 release Rust）
python -m demo.run_dashboard --mode dev
# 终端二
npm --prefix front run dev
```

访问 http://127.0.0.1:5173，端口固定并启用 strictPort。Vite 将同源 /api 转给 4173；代理地址和凭证只在服务端配置。默认浏览器请求 /api，VITE_API_BASE 仅用于公开 API 前缀，严禁加入 Token。

```sh
npm --prefix front run lint
npm --prefix front run test:unit
npm --prefix front run build
# 停止上一套展示入口后：
python -m demo.run_dashboard --mode dist
```

build 含类型检查；正式展示访问 http://127.0.0.1:4173，由 Python 托管 front/dist。npm run preview 仅供本地预览，不替代该托管验收。Ctrl+C 关闭展示终端的自有服务；完整参数见 [演示说明](../demo/README.md)。

## 页面

| 路由 | 内容 |
| --- | --- |
| / | 重定向 /dashboard |
| /dashboard | 服务健康、实例、最近任务、当前加载范围的结果统计 |
| /tasks | 最近任务，total/truncated 和详情入口 |
| /tasks/:taskId | 阶段、允许路径、租约快照、关联审计 |
| /audit | taskId/result 路由筛选，事件增量加载和请求关联 |
| 其他 | 404 页面 |

只提供查询。管理和工具操作由展示脚本执行，页面不发放权限。租约状态以 Rust 快照为准。统计仅包含 result：成功、策略拒绝、输入/执行失败；intent 和管理事件不算完成。

进入立即加载；完成后等待 1 秒再查询，取消和 generation/epoch 保护旧响应。断线保留数据及更新时间，实例变化清空旧数据。事件按实例/ID 去重，每批最多 5 页、每页 100，最多保留 2000 条并提示范围；result 仅本地筛选。健康失败不阻止其他资源查询。TASK_NOT_FOUND 是业务错误，与路由 404 区分。

## 结构与验证

api/ 负责只读 fetch 和必要形状检查；composables/ 管理轮询、实例和事件；utils/audit.ts 定义结果分类；components/ 提供状态、任务及事件展示；views/ 与 router/ 管理页面。外部内容以文本渲染。

Vitest 覆盖超时、取消、竞争、重启、分页、分类及页面；两种模式的浏览器验收见 [B 报告](../docs/member-b-test-report.md)。实际接口见 [HTTP 契约](../docs/api-contract.md)，代理边界见 [代理说明](../web_proxy/README.md)。
