# 成员 B：Rust HTTP 配对性能实验

正式入口使用同一个 release Rust `http_experiment`，分别运行 `enforced` 与 `baseline`。HTTP、认证、锁、文件读写、路径 ACL 和同步审计均由 Rust 共用实现提供。仅实验夹具切换阶段/租约策略，产品服务不提供关闭授权的开关。实验入口契约见 [说明](../docs/http-experiment.md)。

## 运行

从仓库根目录运行，Python 3.10+，只依赖标准库：

```sh
cargo build --release --locked -p agentfence-server --features bench-harness --example http_experiment
python -m unittest discover -s bench/tests -v
python -m bench.run_bench
# 或 bash bench/run_bench.sh
```

默认每种模式运行 3 轮，每轮 read/write/deny 各 100 个正式样本和各 20 个预热样本，总计 **1800 个正式样本、360 个预热样本、6 个独立进程**。`--iterations`、`--rounds`、`--warmup`、`--file-bytes` 可调整；正式模式禁止少于 3 轮或每组少于 100 次。`--experiment-bin` 或 `CARGO_TARGET_DIR` 可指定构建产物。

先快速检查链路可运行 `python -m bench.run_bench --smoke`。它只输出 `BENCH_SMOKE_PASS`，`qualified_sampling=false`，不能代替正式结果。测试中的小样本也仅用于验证程序。

每次新建 `results/local-bench-<UTC>-<随机标识>/`。夹具和私有审计在 `fixtures/`，提交材料在 `public/`。脚本不会覆盖现有夹具，不占用展示服务端口，只停止自己启动的子进程。Token 临时生成、只通过子进程环境和 HTTP 使用，不写入公开证据。失败时非零退出，不重试、不补选样本，汇总标为失败。

## 同条件与计时口径

- 两模式各轮使用相同初始夹具、policy 哈希、文件内容序列、请求形状、准备流程和同一二进制哈希。每轮交替模式先后顺序，场景顺序按轮次轮换；顺序运行，客户端并发数为 1。
- 先创建任务、切到 edit，并通过 HTTP 写入初始 4096 字节内容。读写目标均为 `src/main.rs`，交替写两种等长内容；拒绝为两模式都会返回 `403 PATH_DENIED` 的 `unlisted.txt` 读取。拒绝资源保留固定夹具大小，CSV 的 `resource_bytes` 如实记录它，`target_file_bytes` 记录读写目标大小。
- 两边都按最多 10 次一批发放 300 秒 TTL 租约，查询批前/批后快照并显式撤销。动态模式必须逐次扣完，基线必须保持未消费；显式撤销避免基线未消费租约与下一批冲突。两模式均提交本批自己的 grant_id。
- `time.perf_counter_ns()` 测量客户端序列化、HTTP 请求、完整响应体读取和 JSON 解码。urllib 每个请求使用新 TCP 连接（`Connection: close`），不复用连接、不重试。批次准备、预热、文件/租约/审计核验、脱敏和统计均在计时外。
- 逐条验证状态码、原因码、执行标志、字节数、读回哈希和租约消费；每批检查真实文件与其他文件未变；结束后对齐 HTTP 审计与停止后的磁盘 JSONL，关联每个 request_id 的 intent/result。
- 原始时间保留整数纳秒；P50/P95/P99 使用 nearest-rank `ceil(n*p/100)`。汇总保存每轮每模式每场景、合并分布及每轮 P50 的有符号差值，不裁掉负数、不剔除慢样本。服务端 `decision_us` / `duration_us` 仅是附加字段，不替代端到端耗时。

## 产物和复核

`public/` 包括正式 `bench_samples.csv`、独立的 `warmup_samples.csv`、`bench_summary.json`、`run_manifest.json`、`runs.json`、脱敏响应及审计 JSONL。manifest 记录源码 commit/dirty/文件哈希、Rust/Cargo/Python/OS/CPU、二进制哈希、配置与计时口径。文件内容只保留字节数和 SHA-256，Token 为 `[REDACTED]`。

```sh
# 原运行目录：重新核验 CSV、统计、配对输入、实际文件和磁盘审计
python -m bench.verify results/local-bench-<标识>
# 已导出的 public 证据：重新核验响应、审计关联、CSV 和统计
python -m bench.verify results/member-b/http-bench/<已归档目录> --evidence-only
```

导出复核无法再次访问 CI 已销毁的私有夹具，`files_checked=false`，与采样现场完整核验区分。CI 构建真实例程后运行全部 9 项基准测试，再采集正式数据并上传公开 artifact；未构建例程时 6 项真实进程测试会明确跳过。

该实验报告开发/CI 主机上观测到的端到端差异，包含网络、磁盘和同步审计噪声，不能解释成纯策略 CPU 开销、稳定性能承诺或生产吞吐量。三轮仅作描述性对照，不提供统计显著性结论。权限窗口与 RSS 属于后续独立实验。
