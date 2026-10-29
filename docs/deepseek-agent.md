# DeepSeek 实时 Agent

入口使用用户指定的 `https://api.deepseek.com` 和 `deepseek-flash`，通过官方 Chat Completions 工具调用协议执行。模型根据输入任务生成实际工具调用和文件内容，控制器执行工具后把结果返回模型继续决策；不把固定脚本包装成模型输出。协议依据：[DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/) 与 [Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)。本实现关闭 thinking，不记录或展示隐藏推理，只展示公开回复、工具结果和最终说明。

## 启动

需要已构建的 Rust release、Python 3.10+、前端依赖与可访问 DeepSeek API 的网络。适配器只使用 Python 标准库，无需安装模型 SDK。API 地址固定为上述 HTTPS 官方端点，不跟随重定向，不读取系统代理，不自动重试请求。

在本地 PowerShell 安全输入密钥，避免把密钥写进命令历史、仓库或 VITE 环境变量：

```powershell
$secret = Read-Host 'DeepSeek API Key' -AsSecureString
$env:DEEPSEEK_API_KEY = [Net.NetworkCredential]::new('', $secret).Password
$env:DEEPSEEK_MODEL = 'deepseek-flash'
python -m demo.run_live_dashboard --mode dev --agent deepseek --step-delay 0
# Ctrl+C 结束后清除当前终端环境变量
Remove-Item Env:DEEPSEEK_API_KEY
```

另一个终端运行 `npm --prefix front run dev`，访问 http://127.0.0.1:5173/tasks。输入任务，点击开始；模型轮次、实际 HTTP 记录、阶段、Token 用量、模型执行说明和任务/审计链接实时更新。完成后可以修改任务再次运行。

托管生产构建：`npm --prefix front run build` 后使用 `--mode dist`，访问 http://127.0.0.1:4173/tasks。两种入口不能同时占用 4173。缺少密钥时页面禁用启动；认证失败、余额不足、限流、超时、响应截断均显示真实失败，不切换到固定脚本冒充成功。密钥只留在服务端进程环境和内存，本项目不会把它写入配置文件；服务重启后需重新提供环境变量。

不传 `--agent deepseek` 时保留原来的确定性演示，供离线展示和固定安全场景回归。

## 权限与完成条件

- 浏览器仅提交任务文字（最多 1000 字符/4000 UTF-8 字节）和固定控制动作；不能提供 API 密钥、模型名、任意 URL、工作区或工具参数。模型名仅从服务端 DEEPSEEK_MODEL 配置读取，默认 deepseek-flash。
- 模型只能使用 read_file、request_write_access、write_file、begin_verification、finish_task。文件工具调用使用任务绑定身份，必须经过真实 Rust Broker，没有 Shell、网络工具或绕过网关的文件操作。API Key、Admin Token、Agent Token 不进入模型消息或浏览器。
- 控制器自动批准的范围仅为本次临时夹具：可读取 src/main.rs、Cargo.toml；最多发放一次 src/main.rs 写租约，TTL 300 秒、成功次数 1。模型不能自行续租、额外授权或回退阶段。此规则是固定的本地演示审批策略，不是任意项目的授权面板。
- 修改任务须先读文件、申请授权、写入，再进入 verify 并读回。只读任务可以跳过申请/写入，但仍须进入 verify 并读取目标。用户可改变任务和输出内容；操作顺序、请求次数和审计数量由实际模型调用决定，不再固定为 13 步/17 条。
- finish_task 只有在 verify 已实际读回 src/main.rs、读回哈希与成功写入哈希一致（有写入时）、现场文件一致、API 分页审计与磁盘 JSONL 一致后才允许完成。成功表示这些执行证据通过，不等于任务语义的形式化证明；不执行编译/运行，也不保证生成程序的业务正确性。
- 最多 12 轮模型响应、24 次模型工具请求、每次最多 2048 个输出 Token、任务三分钟上限、请求超时 20 秒、响应 1 MiB/上下文 256 KiB 上限。重复工具调用 ID 会停止；未知工具和非法参数不执行。真实策略拒绝返回给模型，允许它在已有权限范围内调整方案。
- 暂停在当前模型或工具请求返回后阻止后续调用；已经执行的请求不回滚。暂停不延长租约和任务时限。

所有工作都发生在独立演示夹具；重复任务会由控制器在开始前重置目标文件。当前实现不提供仓库任意文件编辑，也不是 OS 沙箱。任务文本及临时文件内容会发送给 DeepSeek；不要在任务中粘贴实际凭证或敏感资料。页面/结果中的模型公开回复可能引用临时目标文件内容；不导出原始模型对话、工具参数、API 凭证或隐藏推理。历史任务不代表独立保留了一份当前目标文件。

## 本次验证

2026-10-27，使用用户提供的凭证，浏览器实际输入“把 src/main.rs 改为一个 Rust 程序，输出指定的 DeepSeek 完成提示，然后通过工具读回验证”。真实 deepseek-flash 经 6 轮响应完成，8 条执行记录、3 次成功文件工具请求、11 条审计事件，文件/审计核验通过。API 报告累计 7767 Token；这是本次所有请求的累计用量，不是性能基线或单轮上下文大小。模型最终说明明确未编译或运行；没有据此宣称通过 Rust 程序功能验证。结果保留在本地 results/local-live-*/run_*/，不将其当作既有确定性 34 场景/性能实验的替代证据。

自动回归使用可控模型回复，文件操作仍启动真实 Rust；不会消耗模型 API。覆盖内容由模型工具调用决定、真实区外拒绝及恢复、只读任务、重复工具 ID、不完整流程/接口失败不能记为成功、未知工具不能执行，以及浏览器任务参数不能更改端点或凭证。传输层检查指定模型/端点、工具消息、认证错误脱敏及截断响应。自动回归和上述真实付费 API 实跑分开标注。

```sh
python -m unittest integrations.tests.test_deepseek demo.tests.test_llm_live -v
npm --prefix front run test:unit
```
