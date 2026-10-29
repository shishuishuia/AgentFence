# 确定性最小演示

## 真实 DeepSeek Agent

已支持服务端环境变量 DEEPSEEK_API_KEY / DEEPSEEK_MODEL（默认 deepseek-flash）。配置后运行 `python -m demo.run_live_dashboard --mode dev --agent deepseek --step-delay 0`，在总览输入任务，模型自行选择工具并通过 Rust 执行。真实 API 实跑、密钥输入方法、工具权限和验证限制见 [DeepSeek Agent](../docs/deepseek-agent.md)。下面的固定 13 步流程仍作为离线模式保留，不混同于模型模式。

## 网页交互任务演示

推荐现场展示使用交互入口：先构建 release Rust，再在两个终端分别运行：

```sh
python -m demo.run_live_dashboard --mode dev
npm --prefix front run dev
```

访问 http://127.0.0.1:5173/tasks，在“现场执行一个 Agent 任务”点击“开始执行任务”。确定性 Agent 通过真实 Rust API 完成 13 步：检查文件、阶段外拒写、一次性租约写入、重复写/区外路径拦截、读回验证及结束后拒读。每步默认停留 2.5 秒；页面每秒查询进度，提供暂停/继续、任务租约和本次审计入口。预期拒绝属于通过的检查，不是流程失败。最终文件与 API/磁盘审计核验通过后才标记成功。

该入口无需在线模型或 API Key，不接受自由任务、路径、文件内容、URL 或 Token。固定任务只修改本次创建的临时工作区中的 src/main.rs，不修改仓库源码。再运行会新建任务，并在开始前由演示控制器重置专用夹具；旧任务和审计仍保留，历史任务不代表独立保留了一份当前文件。只允许一个任务执行；重复请求键去重，旧 run_id 不能暂停新任务。暂停在当前 HTTP 请求完成后生效，租约仍按真实的 60 秒有效期到期，暂停过久应重新运行。

Ctrl+C 停止自有服务；后台启动也可在终端输出的 stop_file 路径创建空文件以请求正常退出。不要直接重复启动占用同一 4173 的入口。已构建前端后，可使用 `python -m demo.run_live_dashboard --mode dist` 并访问 http://127.0.0.1:4173/tasks。`--port` 指定本地端口，`--step-delay` 范围 0～10 秒。前端开发代理仍固定指向 4173。

结果位于 results/local-live-*/：fixture 为本次专用夹具，每个 run_*/ 保存 dashboard_demo.json/events.json，失败保存仅含错误类型及安全快照的 failure.json。凭证仅在内存中，公开进度不含文件内容、绝对工作区路径或 Token。本入口只在显式启动时安装固定演示控制能力；独立 `python -m web_proxy` 和原 `demo.run_dashboard` 保持只读。

此功能的回归：`python -m unittest demo.tests.test_live -v`；前端 LiveDemo.spec.ts 验证按钮提交、只读退化及失败后的请求键复用。历史浏览器只读验收和实验数据保留原受测版本，不作为此交互入口的重测证据。

## 34 场景独立 HTTP 黑盒

```sh
cargo build --release --locked -p agentfence-server
python -m demo.e2e_cases
python -m demo.e2e_verify results/local-e2e-<本次运行编号>
```

完整覆盖 A01～A10、D01～D24，每条独立夹具及真实 TCP 服务。CSV、脱敏响应、事件、逐条证明和 manifest 在本次目录的 public/，完整夹具在 fixtures/。34 条全过才输出 BLACKBOX_PASS，失败退出 1，平台跳过退出 2，不能把 Windows 无符号链接权限计为通过。支持 `bash demo/e2e_cases.sh` 及直接 Python 文件入口。

详细范围、变体、脱敏和离线核验见 [独立黑盒报告](../docs/member-b-blackbox-report.md)。该结果与最小演示、Rust 单元/路由测试各自独立；本次不包含性能及权限窗口实验。

## 持续运行的 Vue 展示

先构建 release Rust。开发模式在两个终端运行：

```sh
python -m demo.run_dashboard --mode dev
npm --prefix front run dev
```

浏览器访问 `http://127.0.0.1:5173`。dist 展示先执行 `npm --prefix front run build`，再运行 `python -m demo.run_dashboard --mode dist`，访问 `http://127.0.0.1:4173`。也可用 `bash demo/run_dashboard.sh`，支持与最小演示相同的 `PYTHON` 环境变量。

入口创建独立夹具和临时凭证，启动自己的 Rust 和代理，在终端输出任务 ID。打开页面后回车播放；检查点默认停留 3 秒，能观察有效租约、次数耗尽、阶段退出及审计。完成后服务保持运行，Ctrl+C 退出并清理自有进程。重新演示请重新启动入口，得到新夹具；4173 被占用时明确失败，不结束已有服务。

自动检查可用 `--auto --step-delay 0 --hold-seconds 0`，正常展示省略这些选项。等待输入或保持期间会检查子进程是否存活。强制终止父进程（例如 Windows taskkill /F）不能保证执行 Python finally，应使用终端 Ctrl+C。

结果保存在 `results/local-dashboard-*`，包含脱敏流程报告、事件及夹具。它与自动退出的最小演示职责不同；以下最小演示命令继续可独立使用。

使用真实 release Rust 服务，不依赖在线模型或浏览器。Python 3.10+，仅使用标准库；Rust/Cargo 固定 1.98.1。正式验收目标为 Ubuntu/WSL2，Windows 可直接运行 Python 模块或使用 Git Bash。

在仓库根目录运行：

```sh
bash demo/setup.sh
cargo build --release --locked -p agentfence-server
bash demo/run_minimal.sh
```

也可执行 `python -m demo.setup`、`python -m demo.run_minimal`。Python 不在默认 PATH 时，设置 `PYTHON` 为其可执行文件。自定义 Cargo target 目录可使用 `CARGO_TARGET_DIR`，或通过 `--server-bin /path/to/agentfence-server` 指定构建产物。脚本不会自动下载依赖或自动重试写操作。

setup 检查工具版本并创建一个全新示例夹具，不覆盖已有目录；`--check-only` 只检查环境。最小演示另建 `results/local-<UTC时间>-<随机后缀>/fixture`，不会使用或修改仓库实际源码及已有服务。

演示覆盖 inspect 可读不可写、edit 一次性授权/写入/用尽、区外文件读取拒绝、verify 读回与拒写、done 拒读。服务使用操作系统分配的回环端口，Admin/Agent Token 仅保存在本次进程内存和子进程环境；结束时仅停止自身创建的服务。结果目录保留，便于核查。

全部检查通过时输出 `MINIMAL_DEMO_PASS`，退出 0。任何失败均返回非零并保存已完成的检查。13 个流程检查覆盖 8 次工具请求；其中 5 次拒绝均保存调用前后的文件哈希、任务和租约快照，必须保持不变。成功写入必须只改变目标文件且恰好消费一次租约。

事件使用每页 2 条实际分页导出，校验顺序、去重、实例、任务和 request_id，并逐条与磁盘 JSONL 比较。成功工具调用必须有先 intent 后 result；intent 的 PENDING 不能算完成。导出前检查本次临时 Token、授权头和文件内容未泄漏到报告或审计。

每次运行保存：

- `minimal_demo.json`：逐步状态、拒绝前后快照、文件哈希、最终租约及校验汇总。
- `demo_events.jsonl`：经核对和敏感信息检查的 API 审计导出。
- `run_manifest.json`：采样时 Git commit、工作区是否有修改、实际源码文件哈希、二进制哈希、Python/Rust/Cargo 版本、操作系统和配置摘要。
- `fixture/`：本次独立文件、policy 和磁盘原始审计，便于本地复核；不包含 Token。

结果与夹具保留后，可独立复核：

```sh
python -m demo.verify results/local-<本次运行编号>
```

成功输出 `MINIMAL_VERIFY_PASS`。它检查已有证据，不重新调用服务；它不是签名或防篡改存证。真实凭证的泄漏检查在初次演示的进程内完成，复核不需要也不保存凭证。

回归测试：

```sh
python -m unittest discover -s integrations/tests -v
python -m unittest discover -s demo/tests -v
```

后者在 release 二进制存在时实际启动 Rust，检查失败清理和重复运行，并对真实产物进行故意篡改测试。无二进制时相关测试明确跳过；正式验收必须先构建，且不能把跳过记为通过。32 项 Python 测试不替代后续 A01～A10、D01～D24 的完整 HTTP 黑盒 CSV 报告。
