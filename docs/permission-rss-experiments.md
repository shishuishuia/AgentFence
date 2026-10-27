# 权限窗口与 Rust 服务 RSS 实验

这两个入口复用现有 Rust 实验程序和 B 的完整 HTTP 黑盒套件。权限时长来自实际授权过程及客户端单调时钟，RSS 来自自有 Rust 进程的 procfs 计数器；均独立于 HTTP 延迟样本。原有 B 的 34 场景与配对 HTTP 结果保留自己的受测版本。

2026-10-27 已在 `44489cb` 完成本地 WSL2 正式采样，两项验证均通过。数据和限制见 [实测报告](member-a-permission-rss-report.md) 及 [归档索引](../results/member-a/permission-rss/wsl2-44489cb/README.md)。

## 环境与构建

正式运行目标是 Ubuntu 24.04 / WSL2，Python 3.10+ 标准库、Rust/Cargo 1.98.1。需要 Linux procfs 和真实符号链接能力。RSS 入口在其他系统明确失败；不会用 Windows 跳过的场景充当完整资源验收。

在仓库根目录构建两个 release 程序：

```sh
cargo build --release --locked -p agentfence-server
cargo build --release --locked -p agentfence-server --features bench-harness --example http_experiment
python3 -m unittest discover -s bench/tests -v
```

如使用 CARGO_TARGET_DIR，两种入口沿用该目录，不需要复制二进制。源码/二进制哈希、Git commit/dirty、OS、Python、Rust/Cargo、配置均保存在本次 manifest。RSS 额外记录可见逻辑 CPU 和 CPU 型号；权限入口沿用 HTTP benchmark 的硬件记录。

## 权限窗口

```sh
python3 -m bench.run_windows
# 或 bash bench/run_windows.sh
```

默认每种策略、每种失效场景运行三轮：4 场景 × 2 模式 × 3 轮 = 24 个独立 Rust 实例，顺序运行，模式先后按轮次交替，场景顺序按轮次轮换。每次窗口为 6 秒，TTL 场景使用 2 秒租约；它们是实际配置，需求中的 120 秒/30 秒仅是示例。可用 `--window-seconds 120 --ttl-seconds 30` 复跑较长窗口，不能用示例数值代替实测。

先在窗口外创建任务并进入 edit，通过 HTTP 写入同样的 4096 字节初始内容，再显式撤销准备租约。窗口内请求序列及输入在两模式保持相同，均用同一 release 程序、固定路径 ACL、文件实现、任务锁、HTTP 和同步审计。静态模式忽略阶段/租约，成功写入不消费次数；动态模式按现有授权规则执行。不同授权结果造成的文件操作差异是本实验的观察对象，不被当作纯 CPU 开销。

| 场景 | 窗口内实际操作与证明 |
| --- | --- |
| ttl | 发放短租约，观察 active 后通过真实查询等待 expired 并拒写；再次发放并重复。产生两个授权区间 |
| phase | 长租约尚未到期、次数尚未耗尽时离开 edit，后续写入返回 INVALID_STAGE |
| revoke | 撤销第一个租约后拒写；发放第二个租约，随后离开 edit。验证多个授权区间及不同结束原因 |
| uses | 两次成功写入耗尽两次额度，随后写入返回 LEASE_USED_UP |

所有场景先验证“发放前无租约”，再验证有效期内成功和失效后拒绝。静态基线对相同探针均成功，持续到同长度观察窗口结束。每次成功/拒绝均核对真实文件、非目标文件、HTTP 租约快照和审计；多页 API 事件与停止后磁盘 JSONL 一致。没有自动重试、补选或剔除失败的探针。

### 时间定义和不确定性

- `window_start_ns` 是准备完成后的实际 `perf_counter_ns()`。窗口结束边界为该起点加配置时长；程序等待此边界经过，并保存实际返回时刻。全部探针必须在边界内完成；超时则整次运行失败。
- 激活发生在发放租约的 HTTP 请求/响应之间，记录 `start_lower_ns` / `start_upper_ns`。这不是精确的 Rust 内部提交时刻。
- 对每个租约，从所有可能结束点取最早者：TTL 到期、成功阶段变更、撤销、成功写入耗尽次数。前三种操作/写入的发生时刻以相应 HTTP 请求/响应夹住；TTL 截止的界限为发放请求的界限加 TTL，并要求真实查询已看到 expired。
- UTC 时间和审计 UTC 仅用于显示及核对租约配置；时长用单调时钟。发放事件的实际 `expires_at - timestamp` 还需等于请求 TTL。
- 以起止界限中点给出估计，同时计算保守上下界。同一资源的多个区间先求并集，重叠或相接不重复累加。区间合并算法另有嵌套、重叠、相接与空集测试。
- 缩减率为 `1 - 动态授权时长 / 静态授权时长`，上下界按相反端点传播。分母为零返回 null（不适用）；正式配置禁止空窗口。

这些上下界反映 HTTP 观测分辨率，不是统计置信区间；授权时长的缩减也不是攻击概率缩减。默认短窗口只描述本次原型实验条件。

### 结果与复核

每次保存 `results/local-permission-<UTC>-<随机标识>/`，真实夹具在 fixtures，公开导出在 public：

- permission_windows.csv：逐个授权区间、资源、grant_id、实际失效原因、单调时钟界限及中点时长。
- permission_summary.json：逐轮/场景的并集、上下界和缩减率；runs.json：文件/租约快照与探针索引。
- responses/、events/：脱敏 HTTP、单调时钟原始值、内容哈希与完整审计；run_manifest.json：版本及环境。

```sh
python3 -m bench.verify_windows results/local-permission-<编号>
python3 -m bench.verify_windows /path/to/exported/public --evidence-only
```

前者还检查实际夹具和磁盘日志；后者只对导出证据重建区间、CSV 和统计，返回 files_checked=false。`--smoke` 用一轮、4 秒窗口/1 秒 TTL 检查程序，只输出 PERMISSION_SMOKE_PASS 且 qualified_observation=false；正式模式至少三轮才可输出 PERMISSION_PASS。

## 覆盖完整黑盒的 RSS

```sh
python3 -m bench.run_resources
# 或 bash bench/run_resources.sh
```

该入口调用原有完整 A01～A10、D01～D24 测试，每个自有 Rust 子进程一启动就挂接观测，贯穿启动检查、场景准备、真实 HTTP、文件和最终审计采集，完成后在终止前取最后一次样本。D23 的重启前后各有一个监测记录，共 35 个实例。要求 34/34 全过且无跳过，否则 RSS 运行不合格。

默认每 5 ms 读取 `/proc/<owned pid>/status` 的 VmRSS 与 VmHWM（字段标记 kB，按 KiB 计）。只观测被该测试创建的 Rust 子进程；不统计 Python、前端、其他服务或整机内存。汇总是各实例峰值的最大值，不将顺序运行的实例相加。

procfs 指标是近似读数，实际 VmHWM 也可能小幅波动。保留全部原值，取全部观测中最大的 HWM；不人为把读数修成单调，不声称捕获了所有瞬时峰值。采样线程开销位于独立 Python 进程，结果仍受机器调度及负载影响，不作为生产内存保证。

每次输出 `results/local-rss-<UTC>-<随机标识>/public/`：rss_samples.csv、rss_summary.json、run_manifest.json，以及 blackbox/ 下本次完整 34 场景的公开证据。原始夹具保留在该运行目录中套件自己的目录内。

```sh
python3 -m bench.verify_resources results/local-rss-<编号>
python3 -m bench.verify_resources /path/to/exported/public --evidence-only
```

验证器检查 34 场景和两个重启实例的覆盖、采样时间和计数、所有监测进程退出、逐进程/全局峰值重算，以及工作负载的完整黑盒证据。完整模式还核对夹具；离线模式明确返回 fixture_files_checked=false。完成输出 RSS_PASS；异常退出、缺采样或跳过场景均不能记为通过。

## 提交与剩余验收

CI 已配置两项正式实验及公开证据上传；远程是否通过仍以实际运行结果为准。本地发布结果应保留自己的受测 commit、原始样本和采样参数，再执行上述独立验证器。导出没有凭证或明文文件内容，也不包含私有夹具；证据一致性核验不等于防篡改存证。

这两项补足权限时长和服务 RSS；最终比赛交付仍需统一版本复验、干净环境、录屏和材料核查。
