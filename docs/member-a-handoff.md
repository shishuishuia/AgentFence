# 成员 A 后端交接

更新日期：2026-10-27（Asia/Shanghai）。早期集成复核基点为 `41a2357`；现已合入 B 的黑盒和 HTTP 实验交付，并在 `44489cb` 补齐 A 的权限窗口/RSS 实测。后端按需求前后端分离版 v2.1 实现。

[HTTP 契约](api-contract.md) 已记录 B 于 2026-10-27 通过真实 Rust 客户端、只读代理和页面确认本批消费的响应形状。后续联调以该契约及实际响应为准；接口变更应同步文档、客户端和回归。查询展示、34 场景黑盒、三轮 HTTP 配对实验及权限窗口/RSS 已有实际证据，最终交付验收仍待完成。

当前 `crates/` 树为 `0f34e6290f7819a0a8ebd2086e1c5a0675be60c6`，与 A 的 `a3f334b` 补充验证版本一致。历史测试和纯策略数据保留各自原始版本；本次集成复核见 [验证报告](test-report.md) 和 [结果索引](../results/member-a/integration-20261027/README.md)。

## 可用交付

| 内容 | 位置 |
| --- | --- |
| 阶段、路径、租约和错误码 | crates/core/src/ |
| 角色认证、任务及查询、审计、原子文件替换 | crates/server/src/ |
| 严格启动配置样例 | config/policy.json |
| 不覆盖现有目录的独立夹具初始化 | crates/server/examples/prepare_fixture.rs |
| A01～A10、D01～D24、故障和并发检查 | crates/server/tests/api.rs |
| 启动真实服务并经 TCP 完成最小链路 | crates/server/tests/tcp.rs |
| 三轮纯策略计时及原始样本 | crates/core/examples/policy_bench.rs |
| 共用 HTTP/文件/审计的独立静态对照入口 | crates/server/examples/http_experiment.rs、docs/http-experiment.md |
| 健康/审计响应形状及异步锁回归 | crates/server/tests/contract.rs、runtime_liveness.rs |
| 三轮四场景权限窗口、完整 34 场景 Rust RSS | bench/run_windows.py、bench/run_resources.py、[实测报告](member-a-permission-rss-report.md) |
| 用户追加的网页现场固定 Agent 任务 | demo/run_live_dashboard.py、总览执行面板；启动/暂停/继续、实时 13 步及文件/审计核验，见 [演示说明](../demo/README.md) |

运行 `cargo test -p agentfence-server --test tcp --locked -- --nocapture` 可独立验证 A 的真实 HTTP 链路，成功打印 BACKEND_TCP_MINIMAL_PASS。B 已实现独立的 `demo/run_minimal.sh` / `python -m demo.run_minimal`，成功打印 MINIMAL_DEMO_PASS，并核对 13 个流程检查、5 次拒绝及 17 条审计事件。两种入口均可复现；最小流程尚不覆盖全部 34 条 HTTP 黑盒场景。

## 接口对接要点

1. 创建和发放租约都是 201。read_file 的 data 含 content、bytes_read；write_file 含 bytes_written、grant_id、remaining_uses。
2. 写入必须带 grant_id。Token 至少 128 位随机熵的需求实际采用 256 位实现，不能替换为单个 UUID v4。
3. 详情是扁平 JSON，allowed_paths 字段名固定；租约 status 由 Rust 按单次时钟快照计算。
4. 列表仅返回最近任务，total/truncated 的含义见契约。事件游标是带实例 ID 的不透明字符串，原样传回 after；重启后清空前端状态。
5. intent 为 PENDING，不计作完成；只对 result 做工具统计。错误分类及管理事件排除规则已写入契约。
6. 查询不会返回 Token、工作区绝对根目录或文件内容。代理只能映射四个 GET 查询，不得将 Agent/管理员写接口开放给浏览器。
7. 健康成功仅含 status、instance_id，不依赖 audit_available/message；事件用 intent/result，event_id 是数字。前端类型和健康判断现已对齐，并有真实响应形状回归。代理应透传上游状态及 JSON 语义；其他差异见契约开头的联调表。

## 运行变量

| 环境变量 | 约定 |
| --- | --- |
| AGENTFENCE_ADMIN_TOKEN | 必填，32～256 可见 ASCII 字节，使用临时随机值 |
| AGENTFENCE_POLICY | 默认 config/policy.json；根目录相对配置文件所在目录解析 |
| AGENTFENCE_AUDIT | 默认 results/audit.jsonl；必须在所有工作区之外 |
| AGENTFENCE_BIND | 默认 127.0.0.1:8080；只允许回环 IP；测试可使用 :0 |

B 的 setup/minimal/dashboard 脚本现已新建独立夹具，写入自己的 policy JSON，并向子进程传入相应环境变量。服务不会创建目标资源文件，也不会从客户端接收任意工作区根目录。独立运行时可参考 prepare_fixture example；演示使用模拟文件，不修改 crates/ 下的实际源码。

## B 已完成的集成与启动

| 内容 | 当前可用结果 |
| --- | --- |
| front/ | 总览、最近任务、详情、审计查询；轮询、请求取消、断线快照及实例恢复 |
| web_proxy/ | 四个 GET 映射、凭证隔离、边界校验及 front/dist 托管 |
| integrations/ | 管理员/Agent HTTP 客户端，不在适配器中直接执行文件操作 |
| demo/ | setup、自动核验后退出的最小流程、保持服务运行的看板入口 |
| 接口与展示资料 | 契约确认、设计说明和 OpenAPI 同步；B 的双模式浏览器记录及截图 |

B 的实现与原始验收范围见 [B 的验证报告](member-b-test-report.md)。A 在集成版本额外复跑：前端 25 项测试、lint/build 通过；Python 在 Windows 49 项通过、1 项符号链接测试跳过，在 WSL2 全部 50 项通过。两环境最小演示均通过；此处的 WSL2 复跑不等于干净 Linux 环境的最终验收。

持续展示先构建 Rust。开发模式在两个终端分别执行：

```sh
cargo build --release --locked -p agentfence-server
python -m demo.run_dashboard --mode dev
```

```sh
npm --prefix front run dev
```

访问 http://127.0.0.1:5173，按展示终端提示回车播放。dist 模式先运行 `npm --prefix front run build`，再执行 `python -m demo.run_dashboard --mode dist`，访问 http://127.0.0.1:4173。展示入口负责生成凭证、启动自己的 Rust/代理并保持查询可用；其 Rust 使用随机回环端口，与单独启动的默认 8080 不同。按 Ctrl+C 清理自有进程，完整参数见 [演示说明](../demo/README.md)。

## 剩余协作与验收

| 责任 | 下一步及验收条件 |
| --- | --- |
| A | 接收 B 的最小复现，修复 Rust 问题并补受影响回归；集成版本确定后执行六项 Rust 质量门禁，保存源码引用及日志 |
| B（已交付） | 独立 34 场景黑盒及 Ubuntu CI 原始证据，见 [黑盒报告](member-b-blackbox-report.md)；最终版本仍需复验 |
| B（已交付） | 三轮 HTTP 配对采样，1800 正式样本/360 预热样本，见 [HTTP 报告](member-b-http-bench-report.md) |
| A（已交付） | WSL2 三轮权限窗口（24 实例）及完整黑盒 RSS（34/34、35 进程、4806 样本、5372 KiB 峰值），见 [实测报告](member-a-permission-rss-report.md)；本批远程 CI 未核验 |
| 双方 | 干净环境复现，核对同一交付版本的源码、文档、数据及 Git 历史；补录屏和最终材料，确认远程 CI 实际结果 |

Rust API 用例、13 个流程检查的最小演示和独立 34 场景 HTTP 黑盒分别保留证据，不混用测试范围。B 已按每轮读/写/拒绝各 100 次完成配对 HTTP 实验，保持样本量、文件大小、连接、并发和审计条件。A 的权限窗口与 RSS 使用独立样本，不由 HTTP 延迟推算；本轮 WSL2 实测不等于干净环境最终验收。

A 的纯策略 benchmark 已排除 HTTP、元数据和文件 I/O、日志、任务锁，返回三轮各允许/拒绝 1000 个样本及 nearest-rank P50/P95/P99。不能将它与 HTTP 耗时混算。HTTP 公平对照依赖已补齐：启用 bench-harness 后，用 http_experiment 的 enforced/baseline 模式在新建夹具中启动同一 Rust HTTP、文件和审计实现，仅改变阶段/租约策略；生产服务始终执行动态授权。B 的构建、启动、请求及采样约定见 [HTTP 实验入口](http-experiment.md)。
