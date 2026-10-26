# 成员 A 验证报告

日期：2026-10-26（Asia/Shanghai）。本报告只覆盖成员 A 的后端，不声称 Vue、只读代理、Python 适配器或团队完整 Demo 已验收。

受测源码提交为 `cf502f8406ce0b9309654b176ef71d2c770c6c26`，crates 树为 `20e6ce2b892d0f3347d82735eb6754fd304d4106`。后续文档/结果提交不改这棵源码树；可用 `git rev-parse HEAD:crates` 核对，原始数据清单也保留这两个引用。

## 环境和质量门禁

| 环境 | 工具链 | cargo test --workspace 计数 |
| --- | --- | --- |
| Ubuntu 24.04 LTS，WSL2，x86_64-unknown-linux-gnu | Rust/Cargo 1.98.1 | 77 项全部通过，含 4 项文档测试 |
| Windows GNU 辅助环境，x86_64-pc-windows-gnu | Rust/Cargo 1.98.1，MSYS2 GCC | 73 项全部通过，含 4 项文档测试 |

Linux 比 Windows 多 2 项服务符号链接测试、1 项 core 符号链接测试和 1 项 Unix 权限测试。Linux 为项目支持的验收环境；不将 Windows 的 cfg 排除用例记为通过。

两个环境均实际成功执行以下六条命令，退出码在 results/member-a/{linux,windows}-verification.json 中逐条保留：

```text
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test --workspace --locked
cargo test --workspace --doc --locked
cargo doc --workspace --no-deps --locked
cargo build --workspace --release --locked
```

单独重复 doc 门禁的 4 项不再次累加到测试总数。GitHub CI 已配置相同固定版本和命令，但本次没有推送或查看远程运行，不宣称远程已绿。

## 功能与证据映射

| 需求 | 实现与自动验证 |
| --- | --- |
| FR01 身份与任务 | getrandom 32 字节；仅返回一次；摘要存储；A01、D01～D03、D23、查询脱敏、真实 JSONL 重启用例 |
| FR02 阶段 | A03、A07、A09、D05～D08；顺序、重复、回退、done 终态与优先级 |
| FR03 路径 | D14～D20、core path 测试；符号链接目标及父目录、非普通文件、精确允许列表、静态边界 |
| FR04 租约 | A04、A05、D09～D13、D24、core grant；有效期、撤销、次数、绑定、冲突和重新发放 |
| 并发与时间 | C01 同时 10 个请求恰好一次成功；C02 可注入时钟在到期前 1ns、等于、后 1ns；C03 及暂停写入测试证明撤销等待、过期后进行中操作可完成 |
| FR05 文件 | C04、C06、D21、D22；空内容、UTF-8 字节限制、缺失文件、非 UTF-8、失败不扣次；临时文件完成后注入失败并检查清理及原文件；Unix mode 保留 |
| FR06 审计 | C05a/b/c、拒绝日志失败、发放/撤销失败用例；intent 前失败不执行、result 失败报告 executed=true、不回滚次数、健康失效、管理内存不先提交 |
| v2.1 查询 | A10 及查询测试；列表截断和稳定排序、详情快照、租约 status、当前实例分页、不暴露 Token/根路径/内容 |
| 客户端断连 | 暂停执行中的文件操作后取消 HTTP waiter，仍完成替换、消费和 result；不得盲目重试 |
| 独立真实链路 | tcp.rs 启动真实子进程、随机端口、HTTP 收发、文件内容和 JSONL 比对；仅结束自身进程 |

api.rs 直接命名 A01～A10、D01～D24，并补充 C01～C06 和额外边界。读写测试帮助函数自动核对拒绝后的文件和租约不变、可用审计中的 result；并发请求另外检查成功者内容和最终次数。路由测试未经过网络的部分不伪称为 HTTP 黑盒实验。

TCP 最小用例复现：

```text
cargo test -p agentfence-server --test tcp --locked -- --nocapture
```

Windows 和 Linux 均实际看到 BACKEND_TCP_MINIMAL_PASS。覆盖 inspect 读取/拒写、edit 一次性授权/成功/用尽、区外私密夹具拒绝、verify 读回/拒写、done 拒读、审计关联。

额外启动检查见 startup-checks.json：缺 Admin Token、非回环地址、配置缺失、审计位于工作区内部均非零退出；夹具 initializer 拒绝覆盖已有目录且保留内容。

## 纯策略实测

硬件：Intel Core i7-12700H；Ubuntu WSL2；release；单并发。三个独立采样轮次，各先排除 256 次预热，再测允许/拒绝各 1000 次，共 6000 条。使用 Instant，百分位为 nearest-rank。

| 场景 | P50 三轮范围（微秒） | P95 三轮范围（微秒） | P99 三轮范围（微秒） |
| --- | --- | --- | --- |
| edit 有效显式写租约 | 0.091～0.093 | 0.094～0.112 | 0.105～0.115 |
| verify 阶段拒绝写入 | 0.024～0.025 | 0.025～0.026 | 0.026 |

表格仅对显示作舍入，原始浮点样本没有改写。测量包含计时器和 black_box 调用成本，不包括 HTTP、锁、文件元数据、文件读写和日志。该结果只覆盖这两个特定场景，不能用来表示完整 Broker 的延迟，也不代表全部拒绝原因的性能。

`/usr/bin/time -v` 观察到该独立微基准进程峰值 RSS 为 2276 KiB；它不是完整服务或完整测试流程的峰值 RSS，不替代 B 的资源实验。端到端基线、读/写/拒绝各至少 100 次、权限窗口对照和服务 RSS 仍由 B 实测。

## 原始文件与复现

- linux-checks.log、windows-checks.log：完整命令输出；对应 verification JSON 记录 commit、源码树、工具链和逐项退出码。
- policy_samples.json、policy_samples.csv：全部 6000 个样本；policy_summary.json：环境及每轮百分位；policy_resource_usage.txt：独立进程资源原始输出。
- startup-checks.json：额外进程启动/夹具检查的退出码与断言结果；不保存令牌。

复跑原始策略样本：

```sh
cargo run --release -p agentfence-core --example policy_bench --locked -- 1000 > policy_samples.json
```

提交的这些结果是成员 A 的真实运行快照。它们不是 B 的 security_cases.csv、HTTP bench_samples.csv 或 permission_windows.csv，不以现有记录替代尚未运行的团队验收。源码变更后必须重新检查受影响的测试和基准，并记录新的版本引用。
