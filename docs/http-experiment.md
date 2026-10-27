# Rust HTTP 公平对照入口

此入口供 `bench/` 性能与权限窗口脚本调用，完成需求 10.2/10.3 中“共享 HTTP、文件和日志，仅改变授权策略”的 A 端依赖。B 已完成 [HTTP 采样与统计](../bench/README.md)，真实结果见 [HTTP 性能报告](member-b-http-bench-report.md)；A 已补齐三轮权限窗口 CSV 和完整负载 RSS，见 [实验方法](permission-rss-experiments.md) 与 [实测报告](member-a-permission-rss-report.md)。入口测试与性能实验结果分开报告。

## 构建和启动

在仓库根目录构建一次，所有正式采样均直接启动同一个 release 可执行文件：

```sh
cargo build --release --locked -p agentfence-server --features bench-harness --example http_experiment
```

程序位置为 Linux/WSL 的 `target/release/examples/http_experiment`，Windows 为同目录的 `http_experiment.exe`。如果设置了 `CARGO_TARGET_DIR`，在该目录下查找。它需要外部注入 `AGENTFENCE_ADMIN_TOKEN`，凭证要求与产品服务一致，不打印凭证。

Linux / WSL，先创建实验父目录并注入本轮随机 Token：

```sh
mkdir -p results/local-http
export AGENTFENCE_ADMIN_TOKEN=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
target/release/examples/http_experiment enforced results/local-http/enforced-r1 127.0.0.1:0
```

在另一个终端或由 B 的驱动程序启动基线，使用同样配置的凭证与夹具准备过程：

```sh
target/release/examples/http_experiment baseline results/local-http/baseline-r1 127.0.0.1:0
```

PowerShell 对应启动命令（在已设置 `AGENTFENCE_ADMIN_TOKEN` 的环境中）：

```powershell
New-Item -ItemType Directory -Force results/local-http | Out-Null
& .\target\release\examples\http_experiment.exe enforced results/local-http/enforced-r1 127.0.0.1:0
```

`0` 端口由操作系统分配，避免占用演示中的 8080。绑定仅允许回环 IP。启动成功后 stdout 第一行是 JSON，驱动程序应解析它而不是猜端口：

```json
{"event":"experiment_ready","mode":"enforced","base_url":"http://127.0.0.1:12345","directory":"<本次夹具绝对路径>"}
```

端口和路径只是格式示例。程序没有交互输入；Ctrl+C 或停止该子进程结束服务。脚本应只终止自己启动的子进程。

## 固定夹具与接口

目录参数必须不存在，父目录必须存在。重复使用现有目录会失败，不覆盖用户文件。每轮分别新建两套内容相同的模拟夹具，不使用仓库源码作为受测文件。启动失败后若留下目录，下一次选择新的目录名。

- `workspace_id` 固定为 `demo`。
- 白名单是 `src/main.rs`、`Cargo.toml`、`missing.rs`，其中 `missing.rs` 故意不存在，用于 I/O 错误检查。
- `workspace/unlisted.txt` 是模拟的白名单外文件。
- `audit.jsonl` 与 `experiment.json` 在 workspace 外。manifest 保存模式与共享执行组件，不保存 Token。
- 路由、认证方式及请求 JSON 与 [HTTP 契约](api-contract.md) 完全相同；先 `POST /v1/admin/tasks` 创建任务，保存一次性返回的 Agent Token。
- 例程不读取 `AGENTFENCE_POLICY`、`AGENTFENCE_AUDIT`、`AGENTFENCE_BIND`，目录和端口通过显式参数传入。

| 路径中的步骤 | enforced | baseline |
| --- | --- | --- |
| Axum `app`、body 上限、JSON、身份、角色、任务绑定 | 相同 | 相同 |
| 任务锁、工作区锁、字节数限制 | 相同 | 相同 |
| 精确路径白名单、路径语法、物理路径检查 | 相同 | 相同 |
| `LocalFiles` 真实读取、临时文件替换和同步 | 相同 | 相同 |
| `FileSink` JSONL、`sync_data`、审计故障关闭执行 | 相同 | 相同 |
| 阶段授权 | 按 inspect/edit/verify/done | 静态 ACL，整个观察窗口允许读写 |
| 写租约授权、成功后消费次数 | 检查并消费 | 不检查、不消费 |

baseline 对应需求中的静态权限对照，不将 Token 校验、路径保护或 fsync 一并删除。它的成功写响应仍包含 `bytes_written`、`grant_id`、`remaining_uses` 三个键，后两个值为 null；审计字段结构保持一致。若请求带有已有租约 ID，事件可能显示该租约快照，但 baseline 不以其状态授权，也不扣次。

产品二进制 `agentfence-server` 和公开的 `Broker::open`、`Broker::with_dependencies` 始终使用动态策略，即使编译时启用了 `bench-harness`。产品没有选择 baseline 的环境变量、配置字段或 HTTP 参数。基线构造只存在于显式启用 feature 的实验模块，且只能新建固定模拟夹具。

## B 的采样约定

1. 使用同一 commit、工具链和 release 程序，保持文件大小、内容、连接复用、并发、审计落盘条件相同。两模式按轮次交替运行，避免将同时运行造成的磁盘争用算入策略开销。
2. 正式测读写前，两边都创建任务并推进到 edit。动态写入每批最多 10 次，使用 1～300 秒 TTL 的租约，及时续发；基线可发送同形请求及自己的有效 grant_id，但不消费次数。记录批次和准备方式，排除创建、切阶段、发放租约及预热耗时。
3. 至少 3 轮，每轮读、写、拒绝各至少 100 次。客户端用单调时钟计完整响应时间，分别保存模式、场景、状态码、reason_code、request_id、耗时和原始响应。按需求产出 `bench_samples.csv`、`bench_summary.json` 及 `run_manifest.json`。
4. `decision_us` 只测纯策略，整数微秒可能为 0。端到端包含 HTTP、身份、I/O 和最终审计；审计事件自己的 duration_us 不包含自身 append，不能替代客户端端到端计时。
5. 对称拒绝样本可选择两模式都拒绝的白名单外路径。动态租约拒绝而基线成功写入属于行为对照，应分别报告；二者执行了不同的 I/O，延迟差不能解释为纯策略开销。
6. 权限窗口实验让同一相对资源和同内容夹具经历相同任务窗口。静态权限贯穿窗口，动态按 TTL 到期、离开 edit、撤销、次数耗尽的最早点结束；多个区间取并集。记录真实开始/结束时间和原因，不用延迟样本推算授权时长。
7. manifest 至少补充实际 commit、Rust/Cargo/OS/硬件、文件字节数、连接与并发、样本量、预热、轮次、TTL/批次及统计方法。删除 Token、文件敏感内容和不必要宿主路径后再交付结果。

## A 端验证

```sh
cargo test -p agentfence-server --features bench-harness --test experiment --locked
cargo test -p agentfence-server --features bench-harness --test tcp --locked
```

前者验证真实文件与审计、共同的身份/路径/字节限制、策略差异、基线不扣次、已有目录拒绝覆盖，以及产品构造不受 feature 影响。后者通过真实产品子进程确认阶段和租约仍生效。独立例程另以真实 TCP 启动检查验证，不将这些检查称为 B 的三轮性能结果。
