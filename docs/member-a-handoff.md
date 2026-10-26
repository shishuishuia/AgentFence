# 成员 A 后端交接

后端按需求前后端分离版 v2.1 实现。成员 B 可以只依赖 api-contract.md 开发，无需修改 Rust 策略。契约当前是 A 端候选，B 实际确认后再冻结；未代替 B 作出确认。

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

运行 `cargo test -p agentfence-server --test tcp --locked -- --nocapture` 可独立验证真实 HTTP 链路，成功打印 BACKEND_TCP_MINIMAL_PASS。它与 B 待实现的 demo/run_minimal.sh 不同，不将后端测试冒充团队最终 Demo。

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

B 的 setup/minimal 脚本应新建独立夹具，写入自己的 policy JSON，并把路径传入这三个运行变量。服务不会创建目标资源文件，也不会从客户端接收任意工作区根目录。新建夹具可参考 prepare_fixture example；不要让演示修改 crates/ 下的实际源码。

## 仍由成员 B 实现与验证

front/ 的业务页面、web_proxy/ 的只读代理及 dist 托管、integrations/ 的 Agent 适配器、demo/ 的 setup/minimal/e2e、bench/ 的 HTTP 基准和权限暴露对照、团队最终 README、录屏与截图由 B 负责。本次按用户授权修正了前端接口消费，尚未完成上述集成交付。

Rust API 用例覆盖 34 条固定场景及补充故障/并发行为，但不是 B 的独立 HTTP 黑盒 CSV 报告。B 仍须导出真实原始响应、逐条文件/租约检查、端到端读/写/拒绝各至少 100 次及公平基线、授权窗口和 RSS 观测。

A 的纯策略 benchmark 已排除 HTTP、元数据和文件 I/O、日志、任务锁，返回三轮各允许/拒绝 1000 个样本及 nearest-rank P50/P95/P99。不能将它与 HTTP 耗时混算。HTTP 公平对照依赖已补齐：启用 bench-harness 后，用 http_experiment 的 enforced/baseline 模式在新建夹具中启动同一 Rust HTTP、文件和审计实现，仅改变阶段/租约策略；生产服务始终执行动态授权。B 的构建、启动、请求及采样约定见 [HTTP 实验入口](http-experiment.md)。
