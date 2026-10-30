# 外部 DeepSeek Harness 接入

外部 Harness 由可信启动器**显式配置**，运行时不扫描 PATH，也不会因为机器上装了某个 Agent 就自动启用。配置存在时，只读审查和隔离修改都可以选择外部 Agent；未配置时接口返回“外部 Agent 未完成安全配置”，不会静默退回内置运行器。内置执行器只保留两件事：隔离修改的 DeepSeek 在线路径，以及断网演示的离线重放。

## 配置参数

启动 `demo.run_v2_dashboard` 时提供，全部为显式值；凭证只走环境变量，不进命令行和浏览器。

| 参数 | 环境变量 | 必需 | 说明 |
| --- | --- | --- | --- |
| `--harness-entry` | `AGENTFENCE_DSH_ENTRY` | 是 | 固定版本 Harness 的 `lib/bin.js` 绝对路径；只接受 `@deepseek-ai/dsh@0.2.1-rc.2` |
| `--harness-node` | `AGENTFENCE_DSH_NODE` | 是 | `node` 可执行文件绝对路径，不依赖 PATH 解析 |
| `--harness-home` | `AGENTFENCE_DSH_HOME` | 是（实际运行） | 已准备好的 `DSH_HOME`，其 `profiles/acp` 已安装 bundle 依赖；临时 seed 的 profile 缺少依赖时无法启动 |
| `--harness-model` | `AGENTFENCE_DSH_MODEL` | 否 | 默认 `deepseek-flash`；写入 profile patch 的 `agent-default-model` |
| `--harness-timeout` | `AGENTFENCE_DSH_TIMEOUT` | 否 | 单次运行超时，1～600 秒，默认 600 |
| （无） | `DEEPSEEK_API_KEY` | 真实模型运行需要 | 只注入 Harness 子进程；也可改用 `DSH_HOME/.credentials.yaml` 的托管凭证，不进入 MCP 子进程或浏览器 |
| `--bin-dir` | （无） | 是 | 包含 `agentfence-mcp-server.exe` 的目录 |

可用性以“是否配置”为准：`GET /api/admin/agents` 的 `deepseek_harness` 只反映启动器是否注入了 factory。启动前可用 `--harness-check` 做一次不调模型的启动探针：

```powershell
target\v2-venv\Scripts\python.exe -m demo.run_v2_dashboard --harness-check ^
  --harness-entry <...>\@deepseek-ai\dsh\lib\bin.js ^
  --harness-node 'C:\Program Files\nodejs\node.exe' ^
  --harness-home <prepared-dsh-home>
```

成功输出 `harness_ready`；失败返回非零并说明是版本、路径还是 profile 未准备。

## 页面填写模型凭证

工作区页面底部有「外部 Agent 凭证」入口。它只写不读回：

- 输入框类型为密码，提交后立即清空；页面不显示明文，不写入 localStorage/sessionStorage。
- 提交走 `POST /api/admin/credentials`，要求同源 Origin、管理会话和 CSRF 头；`DELETE` 清除。
- 服务端把密钥写到当前用户目录的 `AgentFence/harness.key`（Windows 为 `%LOCALAPPDATA%\AgentFence\harness.key`，Unix 为 `$XDG_CONFIG_HOME/AgentFence/harness.key` 或 `~/.config/...`），尽力设置 0600。
- 状态接口只返回 `{configured, source, editable}`；密钥不进响应、审计、日志或 MCP 子进程。
- 环境变量优先：如果启动环境已有 `DEEPSEEK_API_KEY`，页面显示“由服务端环境变量配置”且不可编辑。
- 保存后下一次审查即时生效，不需要重启看板；`--credentials-file` 可覆盖存储路径。

## 隔离修改（外部 Agent）

controlled_edit 任务的快照是可编辑副本，MCP 只在该 profile 下暴露 agentfence_write_file：

1. 启动器先把任务从 inspect 推进到 edit，再发送 prompt。
2. 守卫允许的工具增加 agentfence_write_file，其余仍被拒绝。
3. 模型调用写工具时只给 path 和 content，不提供 grant_id。
   管道中继在转发前按路径向 Broker 申请一次性租约（TTL 120 秒、1 次），把 grant_id 注入 JSON-RPC 帧；模型自己编造的 grant_id 会被覆盖。
4. 租约申请冲突时复用该路径仍然有效的租约；申请失败则原样转发，由 Broker 返回 LEASE_REQUIRED。
5. 运行结束后核对审计：读取至少 1 个文件、写入必须属于本任务快照；推进 verify → done。
6. 管理员在任务页逐文件预览差异，确认后才写回原项目（原有 apply 流程）。

模型全程拿不到租约内容，只拿到被围栏约束的写工具；写入的路径、快照和次数仍由 Rust Broker 判定。

## 从零准备固定版本 Harness

外部 Agent 是第三方 MIT 依赖，**不随仓库分发**；它安装在 `target/harness`，该目录已被 `.gitignore` 忽略。

```powershell
pwsh scripts/setup_harness.ps1          # Windows
```

```sh
sh scripts/setup_harness.sh             # Linux / WSL2
```

脚本依次执行：

1. `npm install @deepseek-ai/dsh@0.2.1-rc.2` 到 `target/harness`（首次需要联网，约 690 MB）
2. 以 `DSH_HOME=target/harness/home` 运行一次 `--profile acp --help`，生成 profile
3. 若 `target/v2` 的 release 二进制已构建，自动执行 `--harness-check`

常用参数：`-HarnessDir`（安装目录）、`-HarnessHome`（DSH_HOME）、`-Node`（node 绝对路径）、`-Entry`（已有运行时的入口）、`-BinDir`、`-CheckOnly`（只检查不安装）。

`--harness-check` 会向 Harness 发送一次真实的 ACP `initialize` 并等待响应，成功输出 `harness_ready`；它不再只判断进程是否存活，因此能区分“启动即退出”和“正常握手”。

发布或评审时不要把 `target/harness` 或 `home` 提交到仓库：运行时是 690 MB 的 `node_modules`，`home` 里还包含会话记录。仓库只保留固定版本号、脚本和本文档。

## 一次运行的闭环

1. 启动器把管道名交给 Harness 的 MCP 声明，任务 Token 只由 `TaskPipeRelay` 持有；Agent 看不到 Token。
2. 临时 profile patch 插入 `ctx.tools.guard`，只允许 `agentfence_list_files`、`agentfence_read_file`、`agentfence_task_status` 三个 MCP 工具。
3. ACP `session/prompt` 跑完后取回报告文本，超长或为空直接失败。
4. 启动器推进 `verify`，读取 Broker 审计：至少 3 个成功 `read_file`、全部属于本任务快照与本实例、没有成功写入，否则整次失败；通过后推进 `done`。
5. 返回 `{"mode":"external_agent","report":...,"calls":[...]}`，`calls` 只来自 Broker 审计，不采信 Agent 自述。

同一时刻只允许一个审查任务运行，两个进程共享同一个准备的 profile。该 home 会保留 Harness 的会话与存储文件，不要把无关凭证放进去。

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

## 已验证与未验证

已验证：

- 配置校验（版本、路径、超时、模型名）与 `--harness-check` 启动探针：已准备 profile 能在 6 秒内保持运行，未准备 profile 会明确失败。
- 审计核对逻辑的单元测试：三文件快照读取通过；成功写入、跨快照读取、只有 intent 的记录被拒绝。
- 官方 ACP SDK 客户端、管道中继和 MCP 三工具的历史实验证据仍然有效。
- 2026-10-29 真实 DeepSeek API 闭环：`@deepseek-ai/dsh@0.2.1-rc.2` + `deepseek-flash`，任务 `task_079014faf73e1efba45b9e9d58c57088` 读取 13 个文件并生成 9426 字符报告；运行标识、脱敏审计与报告见 `results/external-harness/20261029-real-review/`。
- 2026-10-29 真实模型隔离修改：任务 `task_7bfd077a8612091cbad93ddecbf045ad` 读取 14 个文件后只写入 `day2/contact_manager_v2.py` 并读回；租约由启动器签发，模型未接触凭证；脱敏审计见 `results/external-harness/20261029-real-edit/`。
- 真实模型下的路径拒绝：`task_7fc5...` 读取快照外 `secrets.txt`、`task_a338...` 新建 `backdoor.py`，Broker 均返回 `PATH_DENIED`（allowed=false、executed=false）；脱敏审计见 `results/external-harness/20261029-denials/`。原生工具拦截有单次演示与守卫启动日志，但未做多轮统计。

未验证：

- 取消、超时、进程树清理在真实运行中的表现。

同用户信任边界不变：管道 ACL 只限制当前 Windows 用户，不是操作系统沙箱；Harness 自带的本地工具若被绕过，Broker 之外的文件访问不在保护范围内。