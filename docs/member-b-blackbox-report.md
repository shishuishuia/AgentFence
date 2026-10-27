# 成员 B：独立 HTTP 黑盒验收

日期：2026-10-27。覆盖需求 A01～A10、D01～D24；每条通过实际 TCP 请求独立的 release Rust 子进程，不调用 Rust 内部接口，不使用模拟 HTTP 响应。

## 运行与复核

```sh
cargo build --release --locked -p agentfence-server
python -m demo.e2e_cases
# 或 bash demo/e2e_cases.sh
python -m demo.e2e_verify results/local-e2e-<本轮编号>
```

Python 3.10+，标准库。也支持 `python demo/e2e_cases.py`、`--server-bin`、`--results-dir`。入口从任意当前目录启动时，默认仍以仓库目录定位二进制和结果；Shell 入口支持 `PYTHON`。不会接管或改动现有 8080/4173 展示服务。

只有 34 条全部通过才输出 `BLACKBOX_PASS` 并返回 0；出现失败返回 1；仅有平台缺能力的跳过返回 2 和 `BLACKBOX_PARTIAL`。D17/D18 无法创建真正符号链接时明确跳过，不使用硬链接、普通文件或预制返回值替代。

每次运行生成 `results/local-e2e-<UTC时间>-<随机编号>`，包含独立 `fixtures/` 与可导出的 `public/`。Ctrl+C 后停止本次自有进程，保存已经获得的结果；不按进程名终止服务。每条用例独立夹具，D23 在同一夹具中重启真实服务，核对旧 Token 失效和实例变化。

## 检查范围

- 每条固定案例及必需变体检查 HTTP 状态和原因。D01 同时测缺失/无效 Token，D08 测跳阶段/回退/重复，D20 测反斜线/空字节，D21 分别测 65536 字节文件上限和 512 KiB HTTP body 上限。
- 请求前后查询真实任务/租约，并对自有目标、非目标和区外模拟文件计算哈希。拒绝/读取/查询必须保持文件、阶段、updated_at 和租约不变；A05 只改变目标并精确扣减一次。
- A04 从实际 updated_at/expires_at 核对 60 秒租约；D10 等待 1.15 秒后检查 1 秒租约已到期，不将其冒称为纳秒级精确边界验证。精确时钟边界和内部故障注入仍属于 A 的测试证据。
- D17/D18 的请求路径本身在白名单中，且实际文件/父目录是指向区外模拟文件的符号链接，避免用普通“未列入白名单”冒充符号链接防护。
- A10 使用真实多页、不透明游标及任务过滤，与完整事件逐条比对，验证无重复、遗漏或混入另一任务。
- 工具请求按 request_id 关联 intent/result，并检查原因、allowed/executed、资源和顺序；管理成功关联管理事件。管理失败不伪造审计事件；A10 成功查询按契约不返回 request_id，在 CSV 中明确说明。
- 每个实例的事件 ID 连续，分页 API 与磁盘原始 JSONL 相等；D23 按实例划分事件序列。核验结束后确认所有自有子进程退出。

## 产物及脱敏

`public/security_cases.csv` 每条固定案例一行，含实际各变体前置状态、预期/实际状态和原因、文件/租约/审计检查、request_id、变体数、pass 和失败原因。只有全部变体通过，该案例才通过。

`public/responses/*.jsonl` 保留收到的响应 JSON 结构和对应请求元数据，Token/content 的值替换为 `[REDACTED]`；另记录内容 SHA-256、UTF-8 字节数或凭证格式验证结果。记录的是脱敏解析 JSON，不声称原始 HTTP 字节完全保留。实际凭证仅留在进程内存及自有服务环境，不写文件、不打印。

`public/events/*.jsonl` 保留脱敏事件，绝对夹具前缀替换为 `[FIXTURE]`。`security_report.json` 保存逐条副作用证明；`run_manifest.json` 保存实际 commit、dirty 标记、源码/二进制哈希、环境和配置。记录不能证明防篡改或不存在未覆盖漏洞。

完整本地目录可用 `demo.e2e_verify` 重新核对证据、CSV、当前夹具文件及磁盘审计。只导出 public 时使用：

```sh
python -m demo.e2e_verify /path/to/public --evidence-only
```

此模式输出 `BLACKBOX_EVIDENCE_PASS`，只核验证据内部一致性，不宣称重新调用服务或复查缺失的实际夹具。CI 只上传 public，不上传原始夹具和带宿主路径的磁盘审计。

## 本轮验证状态

Windows 当前真实运行已覆盖其余 32 条，D17/D18 因符号链接权限跳过，严格入口返回 partial；这是平台限制，不记为完整 34 条通过。新增反向测试覆盖凭证/内容泄漏、错误执行标记、错误扣次、非目标文件变化、漏测变体、重复分页、缺失审计、夹具/磁盘审计篡改及 CSV 不一致。

Dashboard CI 已增加 Ubuntu 下严格运行全部 34 条及上传脱敏证据的门禁。完整 Linux 结果将在真实运行完成后补充，当前不预填通过率或成功记录。
