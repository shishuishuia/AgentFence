# 成员 A 权限窗口与 RSS 实测报告

2026-10-27（Asia/Shanghai），在 Ubuntu 24.04 WSL2 上完成正式权限窗口和完整黑盒负载 RSS 采样，分别得到 `PERMISSION_PASS` 与 `RSS_PASS`。B 的原有黑盒及 HTTP 延迟结果未被替换，本报告只补充两项独立实验。

## 来源与环境

受测提交为 `44489cb826042f734be03dc9a563756feced1f06`，Rust `crates/` 树为 `0f34e6290f7819a0a8ebd2086e1c5a0675be60c6`。RSS 实现提交为 `c2ca53c`，权限窗口实现及方法说明提交为 `44489cb`。本次是本地实际运行，不是远程 CI 结果。

环境为 x86_64 Ubuntu 24.04 / WSL2，内核 `6.18.33.2-microsoft-standard-WSL2`、glibc 2.39、Intel Core i7-12700H（20 个可见逻辑 CPU）、Python 3.12.3、Rust/Cargo 1.98.1。使用 release 产品服务和启用 `bench-harness` 的 release `http_experiment`；二进制、源码和配置哈希分别保存在各自 manifest。

两个原始 manifest 均记录 `working_tree_dirty=true`，本次归档保留原值。继承的 manifest 工具在 5 秒内无法取得 `git status --porcelain` 时也会写入 true；本机已复现查询超时约 5.012 秒，随后 Git 状态为空。独立逐字节核对两个 manifest 的 151 个源码条目，均匹配上述提交的 Git blob。此项说明支持源码来源核对，不将本次运行记为最终干净环境验收。

公开数据见 [归档索引](../results/member-a/permission-rss/wsl2-44489cb/README.md) 和 [provenance.json](../results/member-a/permission-rss/wsl2-44489cb/provenance.json)。128 个原始公开产物保留字节 SHA-256；索引和本报告是采样后的说明文档，不在该原始产物列表内。归档不包含私有夹具或凭证。

## 权限窗口

正式运行 `local-permission-20261027T110416Z-926566e9`：4 场景 × 2 模式 × 3 轮，24 个独立 Rust 实例，30 个授权区间、12 组比较。每次观察窗口固定为 6 秒，文件 4096 字节，TTL 场景每次租约为 2 秒。两模式共用 Rust HTTP、文件及同步审计实现、请求计划、文件大小与 ACL；基线忽略阶段/租约，动态模式实际执行这些约束。

下表由 [permission_summary.json](../results/member-a/permission-rss/wsl2-44489cb/permission/permission_summary.json) 直接整理。所有静态授权时长均为 6 秒；动态时长先对授权区间求并集。时长保留 6 位小数，百分比保留 3 位，原始 JSON/CSV 保留整数纳秒。

| 场景 | 轮次 | 动态时长估计（秒） | 动态观测界限（秒） | 缩减率估计 | 缩减率观测界限 |
| --- | --- | --- | --- | --- | --- |
| TTL 到期 | 1 | 4.000000 | 3.965390～4.034610 | 33.333% | 32.756%～33.910% |
| TTL 到期 | 2 | 4.000000 | 3.982776～4.017224 | 33.333% | 33.046%～33.620% |
| TTL 到期 | 3 | 4.000000 | 3.961268～4.038732 | 33.333% | 32.688%～33.979% |
| 离开 edit | 1 | 1.800924 | 1.793003～1.808845 | 69.985% | 69.853%～70.117% |
| 离开 edit | 2 | 1.799885 | 1.788183～1.811586 | 70.002% | 69.807%～70.197% |
| 离开 edit | 3 | 1.799342 | 1.792914～1.805769 | 70.011% | 69.904%～70.118% |
| 撤销/再授权后离开 edit | 1 | 3.299652 | 3.284666～3.314638 | 45.006% | 44.756%～45.256% |
| 撤销/再授权后离开 edit | 2 | 3.300631 | 3.282852～3.318410 | 44.989% | 44.693%～45.286% |
| 撤销/再授权后离开 edit | 3 | 3.300541 | 3.285100～3.315982 | 44.991% | 44.734%～45.248% |
| 次数耗尽 | 1 | 1.912221 | 1.880419～1.944022 | 68.130% | 67.600%～68.660% |
| 次数耗尽 | 2 | 1.913129 | 1.880773～1.945486 | 68.115% | 67.575%～68.654% |
| 次数耗尽 | 3 | 1.917606 | 1.884807～1.950405 | 68.040% | 67.493%～68.587% |

TTL 与撤销场景各验证两个授权区间；撤销场景第二段由离开 edit 结束。次数耗尽取实际成功写入的 HTTP 观测边界；执行前的文件和租约核对会影响实际发生时刻，未用计划时间替代观测时间。各场景均验证授权前、有效期内、失效后及窗口末尾的写入，并核对文件、租约和完整审计。原始数据见 [permission_windows.csv](../results/member-a/permission-rss/wsl2-44489cb/permission/permission_windows.csv)、[runs.json](../results/member-a/permission-rss/wsl2-44489cb/permission/runs.json)、[响应](../results/member-a/permission-rss/wsl2-44489cb/permission/responses/) 和 [事件](../results/member-a/permission-rss/wsl2-44489cb/permission/events/)。

时间来自 `perf_counter_ns`，激活和失效发生在相应 HTTP 请求与响应之间，以中点估计并传播保守上下界。这些界限描述观测分辨率，不是精确服务端提交时刻、统计置信区间或攻击概率。结论限于本次 6 秒窗口和工作负载；更长窗口应重新运行。

## 完整黑盒负载 RSS

正式运行 `local-rss-20261027T110718Z-b7032667` 复用 B 的严格 34 场景：34 通过、0 失败、0 跳过。D23 包含重启，因此监测 35 个 Rust 进程，共 4806 个 procfs 样本，目标采样间隔为 5 ms。监测从进程启动后开始，覆盖 readiness、准备、场景请求和最终审计收集。

观测峰值为 **5372 KiB（5.246 MiB，约 5.25 MiB）**，取各进程观测峰值的最大值，不把顺序运行的实例相加。只统计测试创建的 Rust 服务，不包含 Python、Node 或整机内存。原始 VmRSS/VmHWM 数值保留在 [rss_samples.csv](../results/member-a/permission-rss/wsl2-44489cb/rss/rss_samples.csv)，逐进程结果见 [rss_summary.json](../results/member-a/permission-rss/wsl2-44489cb/rss/rss_summary.json)，本次负载的完整证据见 [blackbox/](../results/member-a/permission-rss/wsl2-44489cb/rss/blackbox/)。

procfs 是近似观测，VmHWM 也可能小幅波动，未修成单调序列；5 ms 是请求的采样间隔，不保证每个周期都被准时调度，也不声称捕获了所有瞬时峰值。本结果不是生产内存上限保证。

## 验证与复现

实现验证在 WSL2 完成：bench 首轮 22 项通过；加强验证器后权限窗口 8 项、RSS 5 项、既有 demo 33 项通过且无跳过；release 产品与实验例程均构建成功。没有改动 Rust 产品源码，也未把这些检查描述为最终统一版本的全部门禁重跑。

归档前完整验证检查了实际夹具、文件及磁盘审计，两项均通过。公开导出再次离线重算通过；离线复核不再读取未发布的私有夹具，因此标记 `files_checked=false` / `fixture_files_checked=false`。

从仓库根目录执行公开归档复核（Python 3.10+，Windows/Linux 均可）：

```sh
python -m bench.verify_windows results/member-a/permission-rss/wsl2-44489cb/permission --evidence-only
python -m bench.verify_resources results/member-a/permission-rss/wsl2-44489cb/rss --evidence-only
```

在支持的 Ubuntu/WSL 环境重新采样并验证自己的完整结果：

```sh
cargo build --release --locked -p agentfence-server
cargo build --release --locked -p agentfence-server --features bench-harness --example http_experiment
python3 -m bench.run_windows
python3 -m bench.run_resources
# 使用上述命令各自打印的运行目录替换占位符
python3 -m bench.verify_windows results/local-permission-<编号>
python3 -m bench.verify_resources results/local-rss-<编号>
```

完整 RSS 验证应留在 Linux/WSL 执行，以便按原环境核对符号链接夹具。方法和参数详见 [实验说明](permission-rss-experiments.md)。CI 已配置正式入口，但本批更改的远程结果尚未核验。项目最终仍需统一交付版本复验、干净环境复现、录屏与材料核对。
