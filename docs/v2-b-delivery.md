# B：v2 交付、验证与现场演示

## 本轮实现

1. 原生系统目录选择器；异步预览、完整允许清单、排除项分页、确认快照。
2. 会话/CSRF、短时单次 selection、只读任务创建；凭证留在可信助手。
3. 官方 MCP Python SDK 1.30.0 对接 Rust stdio；复用 DeepSeek Harness；任务、
   快照、内容哈希与审计核验；Vue 显示进度、调用关联及纯文本报告。
4. 双项目合成夹具、独立管理 HTTP + MCP 验收、两平台 CI 与启动/演示说明。

A 的 Rust 接口与实现来自 `feat/v2-rust-mcp` 的 `fd4e086`，通过 merge 集成，
没有在 B 的 Python 工具中重新实现权限策略或直接读取用户项目文件。
实际接口见 [A 契约](v2-a-contract.md)、[B 契约](v2-b-contract.md)。

## 本地启动

使用支持本机原生目录选择的 Python 3.12（含 tkinter），安装可选 MCP 依赖。
Linux/WSL 的纯协议验收不需要图形环境；Windows 选择助手与 Rust 应能访问同一
源路径，不能假定跨环境路径字符串转换已经提供可信导入。

```powershell
python -m pip install -r integrations/requirements-mcp.txt
cargo build --workspace --release --locked --target-dir target/v2
npm --prefix front ci
npm --prefix front run build
python -m demo.run_v2_dashboard
```

打开 `http://127.0.0.1:4174/workspaces`。默认独占自己的随机端口 Broker，
网页端口 4174；不停止或修改已有 4173/5173 服务。Ctrl+C 或向该次运行输出的
stop_file 创建空文件可停止自己的进程。需要换端口时追加 `--port 4175`。

默认 agent=offline，是真实 MCP 读文件和审计重放，不输出模型建议。
在可信服务环境配置 DEEPSEEK_API_KEY 后，使用：

```powershell
python -m demo.run_v2_dashboard --agent deepseek
```

环境密钥不写在命令参数里。模型选择沿用 DEEPSEEK_MODEL；未设置时使用现有
deepseek-flash。模型和文件内容均不是权限判断依据，readonly 由 Rust 执行。

## 每一步检查及修复

| 步骤 | 检查 | 已修复问题 |
|---|---|---|
| 目录选择/导入 | 取消、会话、CSRF、过期/跨会话/重放、确认失败 | 重复打开页面耗尽会话；超时后重发导入；退出遗漏自有对话框进程；Windows 子进程遗漏系统路径变量 |
| 只读任务 | 参数、凭证边界、成功跳转、重复提交、详情/审计 | 旧字段校验遗漏 profile；旧错误分类不计 READONLY_TASK；结果未知时再次创建 |
| MCP | 官方 SDK 与真实 Rust 子进程、三文件哈希和审计 | 对齐 A 实际路由/字段、2 MiB 管理响应、完整预览与排除项；明确区分离线与模型模式 |
| 集成/交付 | 双项目、固定攻击、旧版回归、CI | 原项目/快照/实例关联、关闭清理、错误及模型报告按纯文本显示 |

早期本地前端 41 项通过；代理当时 29 项中 28 项通过、1 项因 Windows 符号链接
权限跳过。当时启动 MCP exe 曾遇到 WinError 4551，因此没有把该次本地 stdio
和双项目验收记为通过。后续本机验证结果见下节；未更改系统执行策略。

合并基线 `c5e9685` 的 [Rust 检查](https://github.com/shishuishuia/AgentFence/actions/runs/40735702213)、
[Dashboard 检查](https://github.com/shishuishuia/AgentFence/actions/runs/40735702258) 和
[v2 双平台验收](https://github.com/shishuishuia/AgentFence/actions/runs/40735702455)
均成功。本次进度查询修复的本地检查另行记录，不能把基线 CI 作为新改动的 CI 结果。

## 2026-10-29 本机进度修复与演示复验

MCP 进度 GET 失败后按 1、2、4 秒最多重试三次；连续失败会暂停自动查询，并提供
“重新查询”。每个请求 10 秒超时，卸载取消请求和定时器，禁止并发查询。会话
失效后可手动重新建立；POST 启动不自动重发，启动结果未知时只查询状态。
启动被拒绝的提示与查询错误分开保存，避免一次成功 GET 把拒绝原因清掉。

本轮前端 53 项、Lint、类型检查、生产构建通过，包含网络恢复、连续失败、
手动恢复、超时、卸载和明确启动拒绝回归。正常本机执行环境下，官方 SDK +
真实 Rust MCP 的 3 项测试全部通过；双项目验收也通过，每个项目读取至少三个
文件、共 22 次负向请求，源项目哈希保持一致。本次未重现此前的 WinError 4551。
受限执行环境中曾出现 Broker 健康检查失败，正常本机执行复验成功。

原生对话框由用户确认已显示并选目录；随后在网页实际预览并确认导入
`demo/fixtures/v2-project-a`，创建 readonly 任务。页面自动从 running 更新为
completed，显示 5 次 MCP 调用、3 个文件及对应 request_id，任务进入 done，
显示 12 条关联审计。该流程使用真实原生选择和 Rust 服务，模式为离线重放。

记录见 [本机验收摘要](../results/member-b/v2-demo-20261029/verification.json)。
受测工作区以 `c5e9685` 为基线且 dirty=true，源码哈希和原始运行标识如实保留；
不是干净环境安装验收。在线 DeepSeek 报告和完整演示录像仍待完成。

## 可复验的离线验收

```powershell
python -m unittest integrations.tests.test_mcp_review -v
python -m unittest discover -s web_proxy/tests -v
python -m demo.run_v2_acceptance --bin-dir target/v2/release
```

客户端测试使用官方 SDK 和真实 Rust MCP；模型分支使用明确标记的受控模型
回复，以验证模型函数调用真正转为 MCP 调用，且上下文不含 Token。它不算真实
在线模型审查。测试读取 AGENTFENCE_V2_BIN_DIR，缺 SDK/二进制会跳过真实 stdio
用例；CI 安装并构建依赖，另外强制运行验收脚本以防跳过被误当通过。

验收使用两个结构不同的三文件合成项目，经真实管理 HTTP 创建快照/任务，
每个通过 MCP 读取至少三个文件。覆盖错误 Origin/CSRF、任意 root、无效会话、
前端提升 profile/注入 Token、选择凭据重放、只读拒写/拒 grant/拒 edit、路径
穿越、绝对路径与排除文件；Rust 拒绝需找到对应审计，源项目哈希保持一致。

结果存于 `results/local-v2-b-*/`：evidence.json、manifest.json 和真实 audit.jsonl。
manifest 包含源码/可执行文件哈希、受测提交及 dirty 标记。目录选择使用固定
夹具替身，**不将其描述为原生对话框实测**。CI 上传上述文件供复核；不含真实
模型密钥、管理员或任务 Token。

真实模型验收可在配置密钥后执行 `python -m demo.run_v2_acceptance --model`。
它会调用在线 API；当前开发进程未配置该密钥，也未把受控模型测试算成在线结果。

## 3～5 分钟现场流程

1. 选择 `demo/fixtures/v2-project-a`，核对允许清单，确认导入；说明“导入时快照”。
2. 输入审查目标，创建 readonly 任务，展示 snapshot_id 和任务详情。
3. 启动 MCP 审查，展示三个工具和至少三文件读取；在线模式展示路径/行号建议。
4. 打开审计，按 request_id 关联读取、固定越界与只读拒绝证据。
5. 换 `v2-project-b` 重复；没有源码/白名单常量改动。解释权限边界仅覆盖 Broker。

API 不稳定时使用离线模式及已保存的真实 MCP 证据，明确标注未调用模型。
本机原生对话框至离线 MCP 的现场流程已按上节复验；真实在线模型报告和完整
录屏仍待完成。双项目脚本中的选择替身不代替另一次原生操作记录。
