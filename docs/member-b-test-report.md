# 角色 B：四步最小演示验证记录

日期：2026-10-26。开发分支：`zxc`。本轮覆盖前端接口修正、Python HTTP 客户端、真实 Rust 最小流程及文件/租约/审计核验。只读代理、业务页面和完整 34 场景黑盒/实验仍属于后续工作。

## 环境与来源

- Windows x86_64；Rust/Cargo 1.98.1，`x86_64-pc-windows-gnu`；release 构建。
- Python 3.12.14，客户端和演示仅使用标准库。
- 前端验证环境 Node.js 24.14.1、npm 11.11.0。
- 开始时 fetch 的 `origin/main` 为 `57bf047`，没有 A 提到的新修正提交。因此本轮按现有 Rust 实现修正前端；不将本轮改动归为 A 的提交。
- 本机原先没有 Rust/WSL 可用环境，工具下载在忽略的 tmp/ 中：官方 Rust 1.98.1 GNU、自带链接工具和 MSYS2 官方 binutils；构建进程显式指定工具路径及 `-C link-self-contained=yes`，未修改系统 PATH。没有运行 Linux/WSL2，尚不声称目标 Linux 环境已复验。

## 分步检查与提交

| 步骤 | 提交 | 检查结果 |
| --- | --- | --- |
| 1：同步检查及契约修正 | `c60b7ae` | lint、8 项 Vitest、含类型检查的生产构建均通过；正常健康响应、503 及卸载取消有测试 |
| 2：HTTP 客户端 | `8b1b2e8` | 初次 12 项真实本机 HTTP 测试通过；管理员/Agent 请求分开、读字段不含 null、DELETE 空 body、超时不重试、重定向不转发凭证 |
| 3：独立最小流程 | `fe03ea9` | 6 项夹具/真实进程测试通过；从仓库外目录运行 setup.sh 与 run_minimal.sh 成功；原源码保持不变 |
| 4：完整核验 | 本文件所在提交 | 19 项 demo 测试、13 项客户端测试通过；真实流程及独立复核成功；新增篡改反例和完整结果清单 |

本轮检查发现并修复：HTTP 内容被截断时，即使剩余字节碰巧能解析为 JSON，也不能视为成功；增加 Content-Length 一致性检查及回归。另固定 *.sh 为 LF，避免 Windows 检出 CRLF 后破坏 Bash 入口。

## 真实最小演示

13 个流程检查：创建任务、inspect 读取、inspect 拒写、进入 edit、发放一次性租约、成功写入、耗尽拒写、区外读取拒绝、进入 verify、verify 读回、verify 拒写、进入 done、done 拒读。

其中 8 次工具调用、5 次拒绝；每次拒绝均核对文件、任务和租约快照不变。成功写入仅修改 `workspace/src/main.rs`，UTF-8 字节数正确，租约次数由 1 变 0。检查用独立模拟私密文件，不使用真实私密数据。

每页 2 条查询累计导出 17 条事件，按 request_id 核对阶段、工具、资源、原因、allowed/executed，检查成功调用的 intent 先于 result；与磁盘 JSONL 逐条一致。导出不含本次 Admin/Agent Token、Authorization 或文件内容。服务退出得到确认；不结束其他进程。

## 反向检查

对真实运行产物故意修改：目标文件、区外夹具、租约消费证明、非目标写入、磁盘事件缺失、JSONL 尾行、重复 ID、错误原因、成功调用缺 intent、UTF-8 字节数、凭证泄漏和 content 字段，均会使验证失败。测试证明校验器能发现这些已覆盖错误，不宣称防篡改存证或不存在其他缺陷。

## 复现与结果

```sh
bash demo/setup.sh
cargo build --release --locked -p agentfence-server
python -m unittest discover -s integrations/tests -v
python -m unittest discover -s demo/tests -v
bash demo/run_minimal.sh
python -m demo.verify results/local-<本次运行编号>
```

成功标志分别为 `SETUP_PASS`、`MINIMAL_DEMO_PASS`、`MINIMAL_VERIFY_PASS`。结果目录包含 `minimal_demo.json`、`demo_events.jsonl`、`run_manifest.json` 及独立 fixture。

`run_manifest.json` 的 commit 指向采样时 HEAD；提交前采样会明确记录 `working_tree_dirty=true`，并保存实际源码逐文件哈希和二进制哈希，不能将其冒称为当时 HEAD 未修改的构建。提交后可在干净工作区重新运行，获取对应最终 commit 的清单。本轮保留一组脱敏样本在 `results/member-b/minimal/`；它不包含夹具，完整复核应使用本次本地运行目录或重新运行。

当前 32 项 Python 测试及最小流程不是完整 A01～A10、D01～D24 黑盒 CSV，也不包含 HTTP 性能、公平基线、权限窗口和 RSS 实验；这些后续单独实施。Rust 源码本轮未修改，A 的后端测试记录保留独立归属。

## 合并 A 后续提交的回归

推送前同步到远端 main `b3b02ff`，合入 A 的后端异步阻塞修正、独立实验程序与前端取消处理。前端重叠修正保留实例显示、卸载取消和最简健康响应测试；新增保留 A 的响应体 AbortError 回归。合并后 9 项前端测试、lint、类型检查及生产构建均成功；Rust release 重新构建成功，13 项客户端和 19 项 demo 测试全部通过，真实最小演示再次输出 `MINIMAL_DEMO_PASS`。该集成验证没有重新运行 A 的全部 Rust 用例或性能实验。

## 看板第一步：只读代理（2026-10-27）

四条 GET 查询、固定回环上游、Host/Origin、参数及方法限制、凭证隔离、上游错误保留和 dist 托管已实现。客户端 13 项测试通过；代理 13 项测试中 12 项通过、1 项外部符号链接测试因 Windows 权限跳过，不能计为通过。包含真实 Rust 的健康、任务列表、详情和事件查询验证；未将此结果计作浏览器验收。连接失败和超时分别验证为 502/504。

## 看板第二步：持续展示（2026-10-27）

新增持续展示入口，共享原最小演示的断言流程，并在检查点提供展示节奏。3 项真实 Rust/代理子进程测试验证租约 active/used_up/stage_inactive、完成后继续查询、端口冲突、异常清理及新运行隔离；与原 19 项 demo 测试合计 22 项通过。保留原最小演示的文件、租约和审计核验，不将常驻展示成功冒称为完整黑盒验收。

## 看板第三步基础：轮询与实例恢复（2026-10-27）

API 错误保留 instance_id，并校验四类成功响应的必要字段。共用轮询使用完成后计时、AbortController 和 generation/epoch；事件状态不自行创建轮询，支持不透明游标、分页去重、2000 条缓存范围提示及非法游标暂停。前端 20 项测试、lint、类型检查和生产构建通过，新增覆盖取消、超时、迟到响应/错误、实例恢复、分页和分类。业务页面接入及真实浏览器验收在后续提交中记录。

## 看板第三步页面：真实数据接入（2026-10-27）

总览、最近任务、任务详情及审计接入真实查询，支持服务端租约状态、结果筛选、请求关联、截断范围及刷新失败保留数据。23 项前端测试通过，新增覆盖健康 503 不阻断其他资源、业务 404、本地筛选不重取、任务列表截断及文本渲染。lint、类型检查和生产构建通过。已通过浏览器实际查看 Rust 的 17 条事件、3 次成功和 5 次拒绝，详情深链接刷新正常；完整双模式记录随后补齐。

## 看板第四步：双模式验收与接口同步（2026-10-27）

使用 Codex 内置浏览器分别访问 Vite 5173 和 Python 托管 dist 4173，后端为真实 release Rust、独立夹具及随机回环端口。浏览器接口未提供版本号，未将结果表述为某特定 Chrome 版本通过。

| 项目 | 实际验证及证据 |
| --- | --- |
| F01 | 两种模式的页面导航、详情直接访问和刷新、前进后退恢复审计筛选、根路由和页面 404；任务不存在保持任务详情及返回入口 |
| F02 | 真实浏览器观察空实例、初次失败、断线保留数据及更新时间、任务不存在；受控 HTTP 测试覆盖 502/504、坏 JSON 和 POST 405，Vue 测试覆盖审计 503 不阻断其他查询 |
| F03 | 两种模式观察实例重启后清空旧任务/事件，重复轮询保持 17/18 条真实事件无重复；假计时器及可控 Promise 验证慢请求、取消、迟到响应/错误/finally 和分页 |
| F04 | 代理边界保留的最近最多 5000 条实际请求中，API 方法只有 GET，Authorization/Cookie 均未出现；实际临时凭证不在构建产物中；生产源码没有浏览器存储写入 |

浏览器未通过存储面板逐项读取已有用户存储；F04 的存储判断来自本项目生产源码无写入路径，配合真实凭证构建扫描和实际请求边界检查。503、超时等受控故障没有冒称为真实 Rust 的磁盘故障注入。

验收期间修复：旧任务审计 404 停止自动重试、网络断线中文错误、异常 instance_id 形状、非有限数 JSON 拒绝、未知 HTTP 方法统一 405、静态根页面及回退入口的路径检查、Windows 浏览器取消连接产生的 ConnectionAbortedError。Rust 源码未修改。

最终检查：25 项前端测试通过，lint、类型检查和生产构建通过；Python 客户端 13 项、代理 12 项、demo/文档 24 项通过，合计 49 项通过、1 项 Windows 外部符号链接测试跳过。真实最小演示输出 `MINIMAL_DEMO_PASS`，公开 dist 展示命令输出 `DASHBOARD_FLOW_PASS`。OpenAPI 本地引用及代表性真实 Rust 响应形状已验证，未声称运行完整 JSON Schema 标准验证器。

接口设计、OpenAPI、根 README 及前端/代理/演示说明已同步；新增 Dashboard CI，远端结果须以实际推送运行记录为准。本轮未执行完整 Linux 干净环境验收、34 场景黑盒或性能实验。

验收清单及实际源码/二进制/构建哈希见 [browser_acceptance.json](../results/member-b/dashboard/browser_acceptance.json)，凭证边界汇总见 [credential_check.json](../results/member-b/dashboard/credential_check.json)。该记录来自提交前工作区，明确标记 dirty 并保留源码哈希。四张主要截图：[总览](../results/member-b/dashboard/dist-dashboard.png)、[任务列表](../results/member-b/dashboard/dist-tasks.png)、[任务详情](../results/member-b/dashboard/dist-task.png)、[审计](../results/member-b/dashboard/dist-audit.png)；另保留开发、空实例、断线及重启截图。

可复现浏览器夹具：`python -m demo.tests.browser_fixture --mode dist`，向终端打印的 control.txt 写入单个 `seed`、`play`、`restart` 或 `stop` 指令。它是验收辅助程序；`play` 每个新夹具仅执行一次，不增加浏览器写接口或在页面放入凭证。通常使用者直接运行 `demo.run_dashboard` 即可。
