# 成员 B：真实数据看板四步实施方案

日期：2026-10-27。方案基于 `zxc` 分支 `0114a7e` 制定，四步现已实现并检查。实际证据及限制见 `docs/member-b-test-report.md`。本文保留实施拆分，命令已可使用。

最终截止：2026-10-29 24:00（Asia/Shanghai，即 10 月 30 日 00:00）。这四步优先在 27 日完成，为 28 日黑盒与实验、29 日复验及材料整理留出时间。

## 目标与现状

完成后，从一个展示入口启动独立 Rust 服务和只读代理，浏览器能够持续看到真实任务、阶段、租约及审计变化；开发模式和构建后的页面均可使用，并正确处理断线、重启和路由切换。

当前可以复用：Vue/Vite/Router 工程、四类查询 API 与类型、Python Admin/Agent 客户端、独立夹具、Rust 子进程管理、最小流程及证据核验。规划时缺少的 `web_proxy/`、持续展示入口、业务页面、共用轮询及浏览器记录，现已在本轮实现。

规划时发现并在本轮处理的边界：

- `run_minimal` 跑完会停止 Rust；任务、租约和在线事件存在内存，结果文件不能直接成为浏览器的在线数据源。
- `execute_flow` 很快推进到 done；仅延长服务存活仍看不到租约 active → used_up 的动态过程。
- Python 的 `ClientError` 尚未区分超时、连接和协议错误，代理不能靠匹配异常消息区分 502/504。
- 前端 `ApiError` 丢弃了错误响应的 `instance_id`，影响旧游标和旧任务的重启恢复。
- 健康 503 表示审计失效，但已有任务和事件仍可查询；不能用健康失败阻止所有页面加载。
- `docs/api-design.md`、`docs/openapi.json` 仍有旧事件名、分类和游标错误定义，实施以 `docs/api-contract.md` 及实际 Rust 响应为准。

## 总体结构

```text
开发：浏览器 :5173 → Vite /api 转发 → Python :4173 → Rust 回环端口
展示：浏览器 :4173 → Python 托管 front/dist + /api → Rust 回环端口
操作：Python 展示流程 → Rust 管理接口 / Agent 工具接口
```

浏览器只查询；管理凭证由 Rust、代理及展示流程使用。已有四个查询接口足以完成这批页面。沿用 Vue Composition API、原生 fetch、Python 标准库和现有客户端。

## 第一步：只读代理与静态托管

### 文件与复用

建议新增 `web_proxy/__init__.py`、`__main__.py`、`config.py`、`server.py`、`tests/test_proxy.py` 和 `README.md`。规模较小时静态托管可先放在 `server.py`，无需过度拆分。

使用 Python 标准库 HTTP 服务，复用 `BrokerHttp` 和 `AdminClient`。先在 `integrations/client.py` 增加继承自 `ClientError` 的超时、连接、协议异常，保持现有调用者兼容；识别 `URLError.reason` 包装的超时。协议异常覆盖非 JSON、响应截断、超限与意外响应结构。

代理响应保留上游 JSON 语义、状态码及有效的请求 ID；若透传 `X-Request-Id`，在 `ApiResponse` 增加可选字段，只采集该头，不透传任意上游头。

### 固定路由

| 浏览器请求 | Rust 请求 | 参数 |
| --- | --- | --- |
| GET /api/health | GET /healthz | 无 |
| GET /api/tasks | GET /v1/admin/tasks | limit：1～100，默认 50 |
| GET /api/tasks/:id | GET /v1/admin/tasks/{id} | ID：1～128 个 ASCII 字母、数字、下划线或连字符 |
| GET /api/events | GET /v1/admin/events | task_id、after、limit；limit：1～500，默认按 Rust 契约 |

路径先匹配，再判定方法、解析参数。已知 API 上非 GET 返回 405 和 `Allow: GET`；未知 API 返回 404。未知、重复或越界查询参数返回 400，包括前端专用的 `result`。游标只解码一次并重新编码转发，保持不透明，不按事件 ID 自行拼接。

### 配置与错误

- 默认绑定 `127.0.0.1:4173`；上游从进程启动配置读取并校验为数字回环 HTTP 地址。浏览器不能指定上游 URL、端口或路径。
- Admin Token 仅从进程环境读取并在启动时校验；只加到三个管理查询，健康查询不带令牌。忽略浏览器 Authorization、Cookie 和转发头。
- 精确检查 Host，以及存在时的 Origin。允许实际使用的 `127.0.0.1:5173`、`127.0.0.1:4173`；如需 localhost，显式配置。Vite 目前保留原 Host，必须覆盖 5173。缺少 Origin 的同源 GET 正常放行。
- 合法上游错误原样保留状态和 `reason_code/request_id/instance_id` 等字段；不把 401、403、503 改成连接失败。对四种查询做必要的响应形状检查，异常响应不作为成功数据放行。
- 连接失败：502 `BROKER_UNAVAILABLE`；5 秒上游超时：504 `BROKER_TIMEOUT`；损坏、截断或不符合接口形状的响应：502 `BROKER_INVALID_RESPONSE`。
- 本地错误统一 JSON，含随机 request_id、`instance_id: null` 和安全消息。API 添加 `Cache-Control: no-store`。不跟随重定向，不自动重试，不输出 Token、上游响应全文或宿主机路径。

### front/dist 托管

静态根固定在 `front/dist`。URL 解码、规范化和实际路径解析后检查仍在根内；覆盖编码穿越、Windows 分隔符、外部符号链接/重解析路径。只读取普通文件，不列目录。

已存在资源按正确 MIME 返回。仅页面导航回退到 `index.html`，让 Vue Router 处理页面 404；`/api`、缺失 JS/CSS/图片和非法路径不能回退 HTML。静态 HEAD 可以按 GET 头部语义支持，API HEAD 仍为 405。dist 模式缺少 `index.html` 时明确启动失败，dev 模式允许尚未构建页面。

### 验收与提交

用真实本地 HTTP 测试覆盖四映射、参数、浏览器凭证剥离、Host/Origin、400/404/405、上游 403/503、502/504、坏 JSON、重定向、静态路径与深链接；校验被拒请求没有访问上游。超时测试使用可配置的短超时，默认仍为 5 秒。

再使用真实 Rust 验证代理与直接查询的稳定字段一致。`server_time` 和租约到期状态会随时间变化，比较时不能机械要求两次响应逐字相同。

通过后提交：`feat(proxy): add read-only broker queries and frontend hosting`。同时回归原客户端测试。

## 第二步：持续运行且能观察过程的展示入口

### 文件与流程复用

新增 `demo/run_dashboard.py`、`run_dashboard.sh` 和针对启动/清理的测试。复用 `prepare_fixture`、`ManagedBroker`、AdminClient、AgentClient。

优先将现有确定性业务流程提取到共享模块，例如 `demo/workflow.py`，增加不暴露凭证的创建任务/阶段检查点回调。最小演示使用默认无等待回调，继续自动核验并退出；展示入口在检查点控制节奏。提取后先跑原最小演示测试，保证原有断言和产物不变。

### 启动顺序

1. 检查 release 二进制和所选模式依赖；默认 dist 模式需要构建好的 `front/dist`，dev 模式由使用者另开 Vite。
2. 在独立 `results/local-*` 下准备夹具，生成本次随机 Admin Token。
3. 启动本次 Rust，使用操作系统分配的回环端口并等待健康就绪。
4. 启动本次代理子进程，将实际 Rust 地址和 Token 通过其环境传入，代理固定 4173。
5. 确认代理子进程仍存活、健康返回本次实例 ID，且任务列表查询成功；健康查询本身不验证代理的 Admin Token。
6. 创建 inspect 任务，打印不含凭证的页面地址和任务 ID，然后开始展示交互。

4173 被占用时失败并清理本次子进程，不能终止已有服务，也不能自动换端口导致 Vite 连错。日志管道持续排空，防止子进程阻塞。

### 展示交互

建议首版只提供简洁的终端控制：打开页面后按回车播放一遍；每个关键检查点默认停留约 3 秒；完成后继续保留服务，Ctrl+C 退出。重新演示时重新启动入口，使用新的独立夹具；不能仅创建新任务就复用已写过的文件，否则现有流程的初始内容断言会失败。浏览器不承担写操作。

检查点依次展示：

1. inspect：读取成功，写入被拒。
2. edit：签发一次性租约，页面能看见 active 和剩余 1 次。
3. 成功写入后：剩余 0 次，再写被拒，页面能看见 used_up。
4. verify：读回成功，写入被拒；租约状态由服务端显示为 stage_inactive。
5. done：读请求被拒，保留任务及关联审计供检查。

演示仍对真实响应做断言。签发租约后不无限等待，节奏要留在 TTL 内；进程暂停等意外导致过期时如实报告，允许重新开始新任务，不自动重试写请求或修改权限规则。

### 生命周期与验收

启动失败、Ctrl+C、正常结束都通过 `finally` 关闭代理和 Rust，并等待退出，只操作自己创建的进程。等待输入期间也要观察子进程是否意外退出，避免显示服务仍可用。Vite 单独启动，展示入口不接管其进程。

保留独立夹具和脱敏结果，不把环境变量、凭证响应或 Token 写入文件。演示完成时只声明已执行流程通过，不把常驻服务正常启动称为完整比赛验收通过。

验收包括：持续查询同一实例、关键阶段可观察、重新运行创建新夹具、端口冲突清理、启动中断清理、子进程异常、Ctrl+C，以及原最小演示继续通过。

重启验证可保持浏览器不动，停止后重新启动整套展示入口。代理仍为 4173，并从新环境接收新的 Rust 随机端口。仅杀死 Rust 然后换端口重开、却不更新代理，不构成恢复测试。

通过后提交：`feat(demo): add persistent dashboard demonstration`。

## 第三步：请求基础与四个真实页面

### 先完成共用逻辑

| 文件建议 | 责任 |
| --- | --- |
| src/api/client.ts | ApiError 保留 instanceId，继续保留 AbortError，区分错误响应 |
| src/api/index.ts / types/api.ts | 四类查询及必要字段检查，保持实际契约 |
| src/composables/usePolling.ts | 请求、超时、取消、刷新、计时器与 generation |
| src/composables/useBrokerSession.ts | 少量共享的 instanceId/epoch，协调实例切换 |
| src/composables/useAuditEvents.ts | 游标、分页、去重、事件缓存；不自行另开重复轮询 |
| src/utils/audit.ts | 结果分类、统计、intent/result 关联纯函数 |
| src/components/ | 少量共用状态提示、租约标签及事件表 |

每个页面拥有明确的轮询入口，事件分页由它驱动。进入立即请求，一轮完成后用 `setTimeout` 安排下一轮，默认间隔 1 秒。独立资源可以并发查询，但同一资源不重叠。

路由参数改变、离开页面或手动刷新时，取消旧请求并更新 generation；响应写入、错误提示、finally 和下一轮计时器均检查 generation。仅调用 abort 不足以阻止已返回响应覆盖新页面。浏览器请求设置略长于代理 5 秒的超时，例如 6 秒；路由离开造成取消不展示为连接失败，超时需要展示。

状态区分首次加载、空数据、正常数据、首次失败和刷新失败。刷新失败保留上次数据与更新时间，标明快照可能过时。

### 实例切换与事件分页

- 从成功及错误响应观察 instance_id。确认实例变化后递增 epoch，取消旧轮次，清空旧任务、事件和游标，再读取新实例；旧 epoch 响应不能将实例切回。
- 旧游标实际返回 `400 INVALID_REQUEST`。只有返回的新实例 ID 确实变化才执行重启恢复；同实例非法游标应显示错误并停止该错误游标的自动重试，提供明确重载入口；不存在的任务同样停止自动重试。
- 详情 `404 TASK_NOT_FOUND` 显示“当前实例不存在该任务”，保留返回列表入口；与 Vue 未知路由页面区分。
- 事件沿 `next_cursor` 串行读取，空页也保存服务端游标；按 `(instance_id, event_id)` 去重。`has_more` 为真继续读，但每批设置页数上限并让出执行机会，避免长时间追历史阻碍取消或健康刷新。
- 事件缓存建议最多保留 2000 条，并明确显示截断和保留范围。统计仅针对当前保留数据；被截断的 intent/result 不能推断为执行失败。
- 改变 taskId 清空该事件源并重取；改变 result 仅改变本地筛选，不重新请求。所有请求只发送后端支持的参数。

### 页面实施顺序

| 顺序 | 页面 | 内容及验收重点 |
| --- | --- | --- |
| 1 | 任务列表 | 最近任务、阶段、创建/更新时间、详情入口；区分 total 与显示条数，truncated 提示最近范围 |
| 2 | 任务详情 | 阶段、允许路径、完整租约快照、关联审计；切任务不串数据，404 清楚 |
| 3 | 审计 | taskId/result 路由筛选、原因码、执行状态、request_id 关联、增量分页 |
| 4 | 总览 | 健康及实例、最近任务、当前加载范围内的工具结果统计；各资源分别呈现查询状态 |

租约使用服务端 `status` 和 `remaining_uses`；倒计时可依据 `server_time/expires_at` 展示，但不能决定 active 或是否允许写入。断线后倒计时不应让旧快照看起来仍实时可信。

只对 `event_type=result` 统计：`OK && executed=true` 为成功；实际契约列出的阶段、路径、租约策略错误为拒绝；身份、输入、I/O 和审计等错误为失败。保留 executed 字段，尤其审计失败可能发生在文件已执行之后。管理事件不进入工具统计；只有 intent 而当前加载范围未见 result 时标为“执行结果未确认”。所有外部文字以文本渲染，不使用 v-html。

### 验收与提交

Vitest 以可控 Promise 和假计时器覆盖：慢请求无重叠、手动刷新、页面离开、A→B 切换、旧响应迟到、旧错误/finally 迟到、实例切换、空页游标、去重、筛选、结果分类、已有数据时断线。再接真实 Rust 流程观察四页。

第三步建议分两个有意义的提交，分别验收后提交：

- `feat(front): add polling and broker instance recovery`
- `feat(front): connect task and audit views to broker data`

每次运行 `npm --prefix front run lint`、`npm --prefix front run test:unit`、`npm --prefix front run build`；build 已包含类型检查。不以截图替代请求竞争测试。

## 第四步：两种模式验收与文档对齐

### 启动约定

以下命令已实现；实际使用的 Python 命令按本机环境选择：

```sh
# 事先构建 Rust；本机 Python 命令按实际环境选择。
cargo build --release --locked -p agentfence-server

# 开发模式：两个终端分别启动。
python -m demo.run_dashboard --mode dev
npm --prefix front run dev

# dist 模式：停止上一套展示入口后启动。
npm --prefix front run build
python -m demo.run_dashboard --mode dist
```

开发验收访问 5173；正式构建验收访问 Python 托管的 4173。`vite preview` 不替代 dist 托管验收。

### 验收矩阵

| 项目 | 重点检查 |
| --- | --- |
| F01 | 根路由、四页导航、详情直接打开和刷新、前进后退保留筛选、未知页面；业务任务不存在单独呈现 |
| F02 | 空实例、首次失败、保留旧数据的刷新失败、代理/Rust 停止、超时和审计 503；POST 405、未知 API JSON、缺资源 404 |
| F03 | 慢请求切任务、离开页面、手动刷新撞上轮询、重复/空页、分页中切筛选；同入口重启后实例变化、旧任务消失、迟到响应被丢弃 |
| F04 | 构建产物、浏览器网络/存储/页面数据没有真实 Admin/Agent Token；业务请求只向同源 /api 发 GET |

先用自动化测试固定竞争、超时、坏响应和审计故障分支，再在开发与 dist 两种模式逐项实查浏览器行为。真实 Rust 的成功/拒绝、分页、断线与实例重启必须实跑；需要故障注入的响应分支可用受控测试上游，记录来源，不能将模拟 503 写成真实 Rust 故障注入。

验收记录写明 commit、工作区是否有未提交改动、系统/浏览器版本、命令、模式、数据来源、逐项结果及截图路径。凭据检查使用本次实际生成的 Token，但检查结果仅记录通过/失败，不打印 Token 或保存带凭据的网络导出。

### 文档及回归

- 补齐 `web_proxy/README.md`、`demo/README.md`、`front/README.md` 和根 README 的启动、端口、模式、停止、错误排查步骤。
- 根据真实接口同步 `docs/api-design.md` 和 `docs/openapi.json`，区分 Rust 与代理接口，修正事件名、分类、游标 400、响应字段、代理错误；不添加后端不存在的能力。
- 将实际联调结论和浏览器记录加入 `docs/member-b-test-report.md`；标注仍未完成的 34 场景黑盒与实验交付。
- 回归 Python 客户端、代理、demo 测试，真实最小演示，以及前端 lint、单测和 build。若未修改 Rust 源码，继续使用对应版本的 A 检查证据，并在最终干净环境验收时统一运行 Rust 质量检查。

通过后提交：`test(dashboard): verify browser workflows and document startup`。若第四步发现实现缺陷，先单独修复并回归，再记录通过结果；不把失败留给文档解释。

## 执行节奏与完成边界

每个提交单元采用“实现 → 针对性检查 → 修复 → 真实链路复验 → commit”。第三步拆成基础逻辑和页面接入两个提交，其余按步提交，预计至少五个有意义的实现提交；具体随发现的缺陷调整。

27 日已按依赖顺序完成代理、持续展示、页面及本轮 F01～F04 检查。28 日投入独立 34 场景黑盒、HTTP 公平对照、权限窗口与 RSS；29 日复验、录屏及提交材料，目标 20:00 前准备好，保留 4 小时余量。若进度吃紧，优先缩减界面装饰及演示交互，保留四查询、真实数据、异常恢复和必需验收。

这四步交付的是可持续运行、可核验的真实数据看板。完整比赛交付仍需要后续黑盒 CSV、实验数据和干净环境复现，不能把这四步通过等同于所有竞赛要求完成。
