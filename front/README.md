# AgentFence Lite 前端

目录 front，Vue 3 + Vite + TypeScript + Vue Router，基于官方 create-vue。实际验证 Node.js 24.14.1、npm 11.11.0，依赖由 package-lock.json 固定。

## 开发与展示

```sh
npm --prefix front ci
# 终端一（先构建 release Rust）
python -m demo.run_live_dashboard --mode dev
# 终端二
npm --prefix front run dev
```

访问 http://127.0.0.1:5173，端口固定并启用 strictPort。Vite 将同源 /api 转给 4173；代理地址和凭证只在服务端配置。默认浏览器请求 /api，VITE_API_BASE 仅用于公开 API 前缀，严禁加入 Token。

真实模型模式将终端一改为 `python -m demo.run_live_dashboard --mode dev --agent deepseek --step-delay 0`，并预先在服务端配置 DEEPSEEK_API_KEY。任务列表会出现演示任务输入框，显示实际 DeepSeek 工具调用与动态记录数；不使用固定 13 步百分比。模型调用和凭证均在服务端，页面只提交任务文字。详见 [DeepSeek Agent](../docs/deepseek-agent.md)。

```sh
npm --prefix front run lint
npm --prefix front run test:unit
npm --prefix front run build
# 停止上一套展示入口后：
python -m demo.run_live_dashboard --mode dist
```

build 含类型检查；正式展示访问 http://127.0.0.1:4173，由 Python 托管 front/dist。npm run preview 仅供本地预览，不替代该托管验收。Ctrl+C 关闭展示终端的自有服务；完整参数见 [演示说明](../demo/README.md)。

## 页面

| 路由 | 内容 |
| --- | --- |
| /、/dashboard | 重定向到 /workspaces |
| /workspaces | 导入项目快照，创建并启动任务 |
| /tasks | 最近任务、详情入口；启用时提供现场演示任务 |
| /tasks/:taskId | 阶段、允许路径、租约快照、关联审计 |
| /audit | taskId/result 路由筛选，事件增量加载和请求关联 |
| 其他 | 404 页面 |

服务连接状态、当前实例和刷新操作位于主菜单下方。查询页面不发放权限；交互演示入口启用后，任务列表允许启动/暂停/继续演示任务，管理和工具操作仍由服务端 Agent 执行。旧 `demo.run_dashboard` 和独立代理不会显示该演示入口。租约状态以 Rust 快照为准。

进入立即加载；完成后等待 1 秒再查询，取消和 generation/epoch 保护旧响应。断线保留数据及更新时间，实例变化清空旧数据。事件按实例/ID 去重，每批最多 5 页、每页 100，最多保留 2000 条并提示范围；result 仅本地筛选。健康失败不阻止其他资源查询。TASK_NOT_FOUND 是业务错误，与路由 404 区分。

## 结构与验证

api/ 负责查询及固定演示控制请求和形状检查；composables/ 管理轮询、实例和事件；utils/audit.ts 定义结果分类；components/ 提供状态、任务及事件展示；views/ 与 router/ 管理页面。外部内容以文本渲染。

Vitest 覆盖超时、取消、竞争、重启、分页、分类及页面；两种模式的浏览器验收见 [B 报告](../docs/member-b-test-report.md)。实际接口见 [HTTP 契约](../docs/api-contract.md)，代理边界见 [代理说明](../web_proxy/README.md)。
