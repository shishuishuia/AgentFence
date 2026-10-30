# 贡献指南

感谢参与 AgentFence Lite。提交前请先阅读 [README](README.md)、[安全边界](docs/threat-model.md) 和 [演示用例](docs/demo-cases.md)。

## 开发环境

- Rust **1.98.1**，由 `rust-toolchain.toml` 固定；包含 rustfmt 与 Clippy
- Node.js **24.14.1** / npm **11.11.0**（前端）
- Python **3.12**（集成、代理与演示仅使用标准库）
- 验收环境：Ubuntu 24.04 LTS / WSL2，`x86_64-unknown-linux-gnu`

## 提交前检查

`@bash
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test --workspace --locked
cargo test -p agentfence-server --test tcp --locked -- --nocapture   # 最小用例

npm --prefix front run lint
npm --prefix front run test:unit
npm --prefix front run build

python -m unittest discover -s integrations/tests -v
python -m unittest discover -s web_proxy/tests -v
python -m unittest discover -s demo/tests -v
`@

最小用例应输出 `BACKEND_TCP_MINIMAL_PASS`。完整说明见 README 的「后端最小运行用例」。

## 提交与分支

- 使用 Conventional Commits，例如 `feat(mcp): ...`、`fix(proxy): ...`、`docs: ...`
- 从 `main` 开功能分支，保持提交小而可回滚；一个提交只做一件事
- 推送后确认 GitHub Actions 的 **Dashboard / Rust / V2 B** 三条流水线通过
- PR 描述写清：改了什么、为什么、如何验证、影响的安全边界

## 不要提交

- `target/`、`front/node_modules/`、`front/dist/` 等构建产物
- `.agentfence/`、`results/` 中的私有夹具、运行日志与凭证
- 任何 API Key、Admin/Agent Token、DSH 运行时（约 690 MB）
- 真实用户数据或未脱敏的项目快照

密钥只通过环境变量或服务端凭证文件提供，不进入源码、浏览器存储或截图。

## 安全

发现安全问题请不要开公开 Issue 描述可利用细节；通过仓库维护者的私下渠道联系，并附上最小复现。安全边界与已知限制以 [threat-model.md](docs/threat-model.md) 为准。

## 文档

行为、接口或演示流程变化时，同步更新：

- `README.md`（用户入口）
- `docs/` 下对应的契约、设计或演示文档
- `CHANGELOG.md`（面向评审的变更摘要）
