# AgentFence Lite

基于 Rust 的任务感知型 AI Agent 工具调用权限控制与审计原型。

目标是在 `inspect → edit → verify → done` 的任务流程中，通过阶段权限、明确的文件白名单、限时限次写入租约和审计日志，控制经过网关的文件工具操作。

本项目是 **Tool Broker（工具调用网关）**，安全边界只覆盖经过网关的操作。它不能阻止 Agent 绕过网关直接访问宿主机，也不提供操作系统级沙箱隔离。

## 当前进度

成员 A 的后端已实现：管理员/任务 Token 隔离、四阶段状态机、工作区配置、精确路径限制、UTF-8 文件读写、单调时钟写租约、原子替换和消费、intent/result 审计，以及 v2.1 的任务列表/详情/事件查询。

已合入成员 A 的 `crates/`、`config/`、Rust 测试及接口说明；`front/` 已有 Vue/Vite/Router 前端骨架及契约类型修正，`integrations/` 和 `demo/` 已实现 HTTP 客户端与独立最小演示。只读代理、页面业务数据、完整黑盒回归及实验采样仍待实现。对接入口见 [成员 A 交接](docs/member-a-handoff.md)、[API 契约](docs/api-contract.md) 和 [B 的验证记录](docs/member-b-test-report.md)。

## 环境依赖

- Rust **1.98.1**，由 `rust-toolchain.toml` 固定；Cargo **1.98.1**，Rust 2024 edition。
- Cargo、rustfmt、Clippy。
- 首次构建需要网络下载 crates.io 依赖；`Cargo.lock` 随仓库提交。
- 支持验收环境为 **Ubuntu 24.04 LTS / WSL2**，`x86_64-unknown-linux-gnu`。Windows GNU 工具链也做了辅助检查，但不据此承诺原生跨平台支持。
- Linux 构建需要 C 编译器/链接器。可用发行版的 build-essential；后端运行不依赖 Python、Node.js 或在线模型。

检查工具链：

```text
rustup toolchain install 1.98.1 --profile minimal --component rustfmt,clippy
rustc -Vv
cargo -V
```

成员 A 的 Windows 辅助验证环境中，默认 MSVC 缺少 `link.exe`，改用已安装的 GNU 工具链和 MSYS2 GCC，并仅为其本地项目设置了覆盖：

```powershell
rustup override set 1.98.1-x86_64-pc-windows-gnu
rustup show active-toolchain
```

此设置保存在本机 Rustup 配置中，不进入 Git，也不影响其他目录。其他开发者应使用自己已配好的工具链，无需照搬；执行 `rustup override unset` 可以取消项目覆盖。

## 后端最小运行用例

在仓库根目录执行：

```text
cargo test -p agentfence-server --test tcp --locked -- --nocapture
```

成功会打印 `BACKEND_TCP_MINIMAL_PASS`。测试启动真实 Rust 子进程，通过 TCP 完成读取、阶段外拒写、一次性租约写入、再次拒写、区外路径拒绝、verify 读回、done 拒读，并核对文件与 JSONL。夹具和 Token 临时生成，仅停止测试自身进程；不修改仓库源码。

这提供 A 端独立可运行证据。B 的独立最小演示入口如下，详细行为见 [demo 说明](demo/README.md)：

```sh
bash demo/setup.sh
cargo build --release --locked -p agentfence-server
bash demo/run_minimal.sh
```

要求 Python 3.10+，仅使用标准库。全部断言通过输出 `MINIMAL_DEMO_PASS`；结果保存在 `results/local-<run_id>/`，包括文件/租约证据、审计导出和源码/环境清单。脚本使用新夹具和随机端口，仅停止自身子进程。也可执行 `python -m demo.setup`、`python -m demo.run_minimal`。

## 单独启动服务

首先创建全新开发夹具；目录已存在时命令拒绝覆盖，请使用新的目录名并调整配置路径：

```text
cargo run --locked -p agentfence-server --example prepare_fixture -- .agentfence
```

Linux / WSL2 设置环境并启动：

```sh
export AGENTFENCE_POLICY=.agentfence/policy.json
export AGENTFENCE_AUDIT=.agentfence/audit.jsonl
# 推荐由密码管理器或管理员环境注入新随机凭证；以下使用系统随机源。
export AGENTFENCE_ADMIN_TOKEN=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
cargo run --release --locked -p agentfence-server
```

Windows PowerShell 可使用已安装的 GNU 工具链：

```powershell
$env:AGENTFENCE_POLICY = ".agentfence/policy.json"
$env:AGENTFENCE_AUDIT = ".agentfence/audit.jsonl"
$taskSecret = [byte[]]::new(32)
$taskRng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$taskRng.GetBytes($taskSecret)
$taskRng.Dispose()
$env:AGENTFENCE_ADMIN_TOKEN = -join ($taskSecret | ForEach-Object { $_.ToString("x2") })
cargo run --release --locked -p agentfence-server
```

服务默认监听 `127.0.0.1:8080`。在另一个终端检查无认证健康接口：

```powershell
Invoke-RestMethod http://127.0.0.1:8080/healthz
```

Linux / WSL2：

```sh
curl -i http://127.0.0.1:8080/healthz
```

预期 HTTP 200，包含 `status:"ok"` 和本次 `instance_id`。按 `Ctrl+C` 停止前台服务。凭证只通过本地管理员环境传递；另开终端不会自动继承先前终端新设置的变量，不要把 Token 写进源码、浏览器或截图。管理员创建/切阶段/发放租约和 Agent 调用的分步请求见 [API 契约](docs/api-contract.md)。

默认配置 `config/policy.json` 指向 `.agentfence/workspace`；prepare_fixture 同时生成可单独指定的 policy JSON。根目录相对配置文件解析。审计必须在工作区外。仅提供 read_file/write_file 文本操作，内容上限 64 KiB，HTTP body 上限 512 KiB；只覆盖已有允许文件。

可通过 `AGENTFENCE_BIND` 改端口。当前仅允许回环地址；非法地址或端口被占用时，程序返回错误退出。

```powershell
$env:AGENTFENCE_BIND = "127.0.0.1:8081"
cargo run --locked -p agentfence-server
```

## 构建与质量检查

```text
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test --workspace --locked
cargo test --workspace --doc --locked
cargo doc --workspace --no-deps --locked
cargo build --workspace --release --locked
```

需要修复格式时执行 `cargo fmt --all`。公共接口使用 `///` 文档，模块使用 `//!` 说明；文档中的 Rust 示例随 `cargo test` 运行。workspace 禁止 `unsafe` 代码。

## 纯策略性能

```sh
cargo run --release -p agentfence-core --example policy_bench --locked -- 1000 > policy_samples.json
```

三轮、每轮允许/拒绝各 1000 次，先排除 256 次预热，输出原始样本与 nearest-rank P50/P95/P99。排除 HTTP、文件元数据、内容 I/O、日志和锁。真实采样见 [成员 A 数据](results/member-a/policy_summary.json)，不将此结果代替成员 B 的端到端、公平基线或权限窗口实验。

## 目录与交接

```text
Cargo.toml                 workspace 和统一依赖版本
Cargo.lock                 可复现的依赖解析结果
rust-toolchain.toml         固定 Rust 与组件
crates/core/               阶段、策略、路径、租约、纯策略计时
crates/server/             HTTP、认证、文件、审计、集成与 TCP 测试
config/policy.json         不含密钥的工作区配置
docs/api-contract.md       A 端契约候选，待 B 联调确认
docs/member-a-handoff.md   前端与适配器对接说明
docs/development-log.md    真实开发及验证记录
results/member-a/          脱敏验证日志与纯策略样本
```

## 已知限制与故障处理

- 工作区必须是受信任静态夹具。路径检查不解决恶意并发替换、硬链接和宿主机直接访问；详见 [安全边界](docs/threat-model.md)。
- 重启后所有旧任务、Token 和租约失效，JSONL 保留但不在线恢复。游标实例改变时重新建任务。
- AUDIT_UNAVAILABLE 会关闭后续工具执行。若响应 executed=true，文件已经变化且次数已扣减；管理员检查文件和 intent/result，修复日志后重启。写入超时或断连不能推断“未写入”，不要自动重试。
- 缺少 Admin Token、错误配置或工作区不存在会阻止启动；先运行 prepare_fixture 并检查环境变量。审计文件尾行不完整时启动失败，保留故障文件供检查并指定新的专用日志路径。
- Windows 报 link.exe 缺失时使用已配好的 GNU 工具链/MinGW，或改在 WSL2 中开发。端口占用时设置其他回环端口；服务不接受非回环 IP。
- [测试报告](docs/test-report.md) 区分后端验证与 B 尚待完成的黑盒、前端及实验验收。远程 GitHub CI 只有推送并实际运行后才能确认，本地通过不等于远程已通过。

## 第三方依赖

本项目采用 [MIT](LICENSE)。第三方依赖声明与引用/AI 辅助说明见 [来源说明](docs/provenance.md) 和 [依赖许可证索引](docs/dependency-licenses.json)；Rust 依赖由 Cargo.lock 固定，前端依赖由 front/package-lock.json 固定。B 已验证 Node.js 24.14.1、npm 11.11.0、Python 3.12.14；Python 客户端与演示仅用标准库。代理运行环境和实验入口将在后续接入时补充。

## 运行前端

验证环境为 Node.js 24.14.1、npm 11.11.0。当前提供路由、页面骨架和 API 客户端；只读代理及业务数据联调待实现。

```bash
cd front
npm ci
npm run dev
```

访问 http://127.0.0.1:5173。项目结构与构建说明见 [前端 README](front/README.md)。

## 项目文档

- [需求与实施方案](docs/AgentFence_Lite_5Day_Requirements_Detailed_v2.docx)
- [HTTP 接口文档](docs/api-contract.md)：业务接口、字段、鉴权、错误码、事件分页和联调示例。
- [前期接口设计稿](docs/api-design.md)：保留详细设计和代理方案；与后端实现有差异，联调以 HTTP 接口文档为准。
- [OpenAPI 设计稿](docs/openapi.json)：对应前期设计稿，可导入支持 OpenAPI 3.1 的接口工具，尚待与后端契约对齐。

