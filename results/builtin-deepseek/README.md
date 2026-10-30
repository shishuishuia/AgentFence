# 内置 DeepSeek 执行器运行证据

该目录保存 2026-10-29 的一次**内置** DeepSeek 执行器隔离修改运行，不属于外部
Harness 证据。启动参数 `--agent deepseek` 只影响隔离修改任务；外部 Harness 必须
另行提供 `--harness-entry`、`--harness-node`、`--harness-home` 与 `--bin-dir`。

## 20261029-real-edit

- 执行器：内置 DeepSeek（`--agent deepseek`），模型 `deepseek-flash`
- 任务：`task_7bfd077a8612091cbad93ddecbf045ad`
- 读取 14 个文件，写入 1 个文件（`day2/contact_manager_v2.py`），随后读回确认
- `audit.jsonl` 仅保留该任务的审计事件，不含文件内容、令牌或密钥
- `runtime.json` 中 `agent=deepseek`、`harness=true`：前者是内置执行器，
  后者只表示启动时同时配置了外部 Harness

这次运行用于证明内置执行器同样受 Rust Broker 的阶段、路径与租约约束；外部
Harness 的只读审查与拒绝演示见 `results/external-harness/`。
