# 成员 B：HTTP 配对性能报告

2026-10-27：第 2 项已完成。实现同一 Rust release 实验入口的配对采样、原始数据导出、统计重算与证据校验，并取得真实 Ubuntu 结果。方法和复现命令见 [bench/README.md](../bench/README.md)。

## 受测版本与环境

受测源码为 `d03037a0fb8ad3b93031f2adedd19e794a1fea0d`，CI 工作区干净。[实际 CI](https://github.com/shishuishuia/AgentFence/actions/runs/40458268772) 构建共享例程、执行 9 项基准回归（全部通过，无跳过），再正式采样并输出 `BENCH_PASS`。原有前端 25 项测试、Python 客户端/代理各 13 项、demo 33 项、34 场景黑盒及前端构建在同次 CI 通过。

运行环境为 GitHub Ubuntu runner，Linux 6.17.0-1022-azure x86_64、glibc 2.39，AMD EPYC 9V45（runner 可见 2 个逻辑 CPU）、Python 3.12.15、Rust/Cargo 1.98.1。同一 release 例程 SHA-256 为 `5abcd076a2ac053decbaf0bbff5712a13c0f08e25f4a4113a87e0f7f3087e2b6`。完整环境、源码和二进制哈希见 [manifest](../results/member-b/http-bench/linux-d03037a/run_manifest.json)。下载后已核对 manifest 中 66 个源码文件均匹配受测 commit 的 Git 原始文件。

本机 Windows 应用程序控制策略阻止 Rust 新构建脚本执行（OS 4551），未修改保护策略。本机仅完成 3 项统计/参数测试及原有 demo 33 项回归；6 项需要实验二进制的测试在本机明确跳过，真实执行结果采用上述 Ubuntu CI。下载 public 后，本机另完成离线证据复核；不把它记为本机 Linux 实测。

## 实验条件与完整性

实际取得 **1800 个正式样本、360 个独立预热样本、6 个独立实例**。每轮每模式 read/write/deny 各 100 次正式请求、各 20 次预热。准备操作不计时，没有失败样本、重试或慢样本剔除。每个模式/场景合并 3 轮后为 300 次，原始时间以整数纳秒保存。

两边均为 edit 阶段，读写目标 4096 字节；deny 读取相同固定夹具的 24 字节白名单外文件，均为 `403 PATH_DENIED`，并非两边执行不同 I/O 的租约拒绝对比。写操作按最多 10 次/批、TTL 300 秒的同形准备流程执行，两模式每批均查询快照并显式撤销；准备、核验和导出耗时排除。连接模式为 urllib 每次新建 TCP 连接、`Connection: close`、并发 1。

采样现场完成实际文件哈希、非目标文件不变、动态逐次扣减、基线不扣减、完整分页审计与停止后磁盘 JSONL 一致检查。6 个自有服务全部退出，配对输入一致，CSV 可由脱敏响应逐条重建，P50/P95/P99 可精确重算。公开证据保留 2334 条请求记录、3780 条审计事件和 78 个写入批次（含准备）。任一不一致均使整次运行不合格。

## 实测延迟

下表为各场景三轮合并分布，单位毫秒（由原始 ns 换算），P50/P95/P99 使用 nearest-rank。计时包含客户端序列化、HTTP、完整响应与 JSON 解码，以及服务端实际文件和同步审计。

| 模式 | 场景 | 正式样本 | P50 (ms) | P95 (ms) | P99 (ms) |
| --- | --- | ---: | ---: | ---: | ---: |
| enforced | read | 300 | 0.865934 | 0.971794 | 1.023401 |
| enforced | write | 300 | 1.346467 | 1.465046 | 2.121966 |
| enforced | deny | 300 | 0.615557 | 0.722127 | 0.738051 |
| baseline | read | 300 | 0.858243 | 0.977902 | 1.047287 |
| baseline | write | 300 | 1.336442 | 1.522512 | 3.766702 |
| baseline | deny | 300 | 0.603288 | 0.722648 | 0.889930 |

各轮 P50 相对差值 `(enforced / baseline - 1)` 如下，保留正负号；不是单次请求配对的同时测量。

| 轮次 | read | write | deny |
| --- | ---: | ---: | ---: |
| 1 | +2.72% | -2.23% | +2.86% |
| 2 | +1.38% | +1.12% | +4.05% |
| 3 | -1.33% | +3.06% | -1.47% |

本轮可报告上述观测值；各轮差值有正有负，且 baseline 写样本包含 24.713283 ms 的最大值，未剔除。CI 主机非独占硬件，结果包含调度、磁盘、网络和审计波动，不能把差值解释为纯策略 CPU 成本，也不能由三轮数据声称显著更快、固定开销比例或生产性能承诺。纯策略微基准仍单独引用 A 的结果。

## 原始证据与复核

归档目录：[linux-d03037a](../results/member-b/http-bench/linux-d03037a/)。

- [bench_samples.csv](../results/member-b/http-bench/linux-d03037a/bench_samples.csv)：1800 个正式样本，含模式/轮次/场景、状态码、原因、request_id、耗时及字节数。
- [warmup_samples.csv](../results/member-b/http-bench/linux-d03037a/warmup_samples.csv)：360 个预热样本，未混入正式统计。
- [bench_summary.json](../results/member-b/http-bench/linux-d03037a/bench_summary.json)：18 组每轮分布、6 组合并分布及每轮比较。
- runs.json、responses/、events/：准备流程、租约与文件哈希证明、脱敏响应和审计；内容与 Token 值已脱敏。
- [provenance.json](../results/member-b/http-bench/linux-d03037a/provenance.json)：CI/artifact 来源、下载压缩包哈希、17 个原始产物哈希和离线核验结果。

```sh
python -m bench.verify results/member-b/http-bench/linux-d03037a --evidence-only
```

本机结果为 `BENCH_EVIDENCE_PASS`，确认 formal=true、measured=1800、warmup=360、runs=6。现场完整验证在 CI 内执行；导出的 public 不含私有夹具，离线检查明确为 files_checked=false。结果引用保留实际受测 commit，后续整理文档不改写来源，也不宣称这些记录具有防篡改能力。

本项不包含权限窗口和峰值 RSS；两者不得由本项延迟样本推算。
