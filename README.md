# AgentFence Lite

基于 Rust 的任务感知型 AI Agent 工具调用权限控制与审计原型。

目标是在 `inspect → edit → verify → done` 的任务流程中，通过阶段权限、明确的文件白名单、限时限次写入租约和审计日志，控制经过网关的文件工具操作。

本项目是 **Tool Broker（工具调用网关）**，安全边界只覆盖经过网关的操作。它不能阻止 Agent 绕过网关直接访问宿主机，也不提供操作系统级沙箱隔离。

## 当前进度

已建立 Cargo workspace、四阶段类型及其顺序、HTTP 服务入口和 `/healthz` 存活检查。认证、文件工具、租约和审计尚未实现；目前不能用于执行 Agent 文件操作。

成员 A 负责 `crates/`、后续的 `config/` 和 `docs/api-contract.md`。成员 B 负责 Agent 集成、演示、看板和黑盒实验。本次先完成成员 A 的工程基础。

## 环境依赖

- Rust stable，支持 Rust 2024 edition；workspace 声明的最低版本为 1.85，实际验证版本见开发记录。后续依赖的最低版本仍需单独验证。
- Cargo、rustfmt、Clippy。
- 首次构建需要网络下载 crates.io 依赖；`Cargo.lock` 随仓库提交。
- 推荐 Linux / WSL2。Windows 可以使用 MSVC 工具链配 Visual C++ 构建工具，或 GNU 工具链配 MinGW-w64。

检查工具链：

```text
rustc --version
cargo --version
rustup component add rustfmt clippy
```

本机默认的 MSVC 工具链缺少 `link.exe`，但已有 GNU 工具链和 MSYS2 GCC，因此已仅对此项目设置 GNU 工具链覆盖：

```powershell
rustup override set stable-x86_64-pc-windows-gnu
rustup show active-toolchain
```

此设置保存在本机 Rustup 配置中，不进入 Git，也不影响其他目录。其他开发者应使用自己已配好的工具链，无需照搬；执行 `rustup override unset` 可以取消项目覆盖。

## 最小运行用例

在仓库根目录执行：

```text
cargo run --locked -p agentfence-server
```

服务默认监听 `127.0.0.1:8080`。在另一个 PowerShell 终端中检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8080/healthz
```

或者在 Linux / WSL2 中执行：

```sh
curl -i http://127.0.0.1:8080/healthz
```

预期 HTTP 状态码为 `200`，响应为 `{"status":"ok"}`。原终端按 `Ctrl+C` 停止服务。

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
cargo build --workspace --release --locked
cargo doc --workspace --no-deps --locked
```

需要修复格式时执行 `cargo fmt --all`。公共接口使用 `///` 文档，模块使用 `//!` 说明；文档中的 Rust 示例随 `cargo test` 运行。workspace 禁止 `unsafe` 代码。

## 目录与下一步

```text
Cargo.toml                 workspace 和统一依赖版本
Cargo.lock                 可复现的依赖解析结果
crates/core/               阶段类型；后续实现策略、路径校验与租约
crates/server/             Axum 路由、进程入口、HTTP 测试
docs/api-contract.md       HTTP 接口草案及实现状态
docs/development.md        分阶段开发与验证记录
```

下一步先确定管理员与 Agent 的认证方式、任务创建接口和统一响应结构，再实现管理接口。未经认证的 mock 接口不会执行文件操作。

## 第三方依赖

本阶段使用 Axum、Tokio、Serde，测试另用 serde_json、Tower、http-body-util；它们采用 MIT 许可证，部分同时提供 Apache-2.0 选项。具体解析版本见 `Cargo.lock`，完整依赖与许可证可通过 `cargo metadata --format-version 1 --locked` 查看。项目自身许可证尚待维护者确定。

