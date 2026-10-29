# 外部 DeepSeek Harness 接入进度

本功能仍在开发：任务界面可以选择外部 Harness，但默认服务尚未配置运行器，选择后会返回“外部 Agent 未完成安全配置”，不会退回内置运行器。当前仅考虑只读任务；外部 Agent 的修改任务和管理员确认应用流程还没有接入。

## 已验证的实验

使用 Windows、Node.js 24 和官方 npm 包 `@deepseek-ai/dsh@0.2.1-rc.2`，在独立临时目录和合成项目上进行了探索性验证：

- 官方 `acp` profile 能响应 ACP `initialize` 和 `session/new`；实验配置将模型设为 `deepseek-flash`。
- 在临时 profile 中注册 `ctx.tools.guard`，只允许 AgentFence 的三个只读 MCP 工具。直接测试中，Harness 自带的 `read` 和 `pwsh` 工具可见但被拒绝。
- 使用用户授权的 DeepSeek API，官方 Harness 通过 ACP 完成一次合成项目任务，并通过 Rust MCP 读取文件；Broker 的任务审计中存在成功的 `read_file` 结果。
- 这些是实验结果，并非可直接用于生产的安全保证：还需要自动化验证任务作用域、守卫持续生效、启动失败、进程树清理、审计关联以及工具绕过等情况。

安装该固定版本后，可以在 PowerShell 中运行不需要 API 密钥的真实 Harness 守卫冒烟测试：

```powershell
$env:AGENTFENCE_DSH_ENTRY = 'Harness 安装目录\node_modules\@deepseek-ai\dsh\lib\bin.js'
python -m unittest integrations.tests.test_harness_installed -v
```

测试会创建临时 profile，检查实际 Harness 的本地 `read` 和 `pwsh` 是否被守卫拒绝；它不调用模型。未配置 `AGENTFENCE_DSH_ENTRY` 时，该测试会跳过。

## 当前代码与边界

`harness_acp.py` 只负责有限的 ACP 通信、超时和权限请求拒绝；它本身不会限制 Harness 内置工具。`harness_pipe.py` 和 `harness_pipe_client.py` 通过仅允许当前 Windows 用户访问的命名管道，将 MCP 标准输入输出中继到持有任务凭证的 Rust 子进程。两个管道的客户端会话使用命名互斥锁串行化，以避免并发连接时输入和输出串线。`requirements-harness.txt` 列出实验依赖。

这种中继的信任边界是当前 Windows 用户：同一用户下的不可信进程仍可能访问本机资源或干扰运行，它不是操作系统沙箱。Harness 默认带有本地文件、命令等工具，不能只依靠提示词来保证只读。不能将任务 Token 放进 ACP 声明、Agent 参数或模型提示词，也不能把管理员凭证交给外部 Agent。

## 正式启用前

需要将固定版本 Harness 和只读配置接入可信启动器，在发送任务前确认守卫确实加载；验证外部工具调用与当前任务、快照、实例及 Broker 审计关联，缺少真实成功读取时不报告完成；覆盖取消、超时和子进程清理；完成真实端到端和负向绕过测试，并明确同用户信任边界。只有这些检查通过后，才应配置 `external_review_factory` 对用户开放运行。
