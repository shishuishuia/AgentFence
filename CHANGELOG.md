# 变更记录

本项目采用类似 Keep a Changelog 的格式；版本按实际提交与标签记录。

## v2.0.0 — 2026-10-30（tag `ccf2026-final`）

### 新增

- Linux 外部 Agent 中继：Unix domain socket 承载 MCP 帧，保留启动器签发写租约、模型不接触 `grant_id` 的边界；Windows 命名管道实现不变
- 服务器路径导入工作区：无图形环境时可从配置的导入目录直接导入项目
- 三个演示项目与 [演示用例](docs/demo-cases.md)：正常只读审查、越权读取拒绝、越权写入拒绝
- 参赛技术文档（10 页 PDF/DOCX）与开源、诚信承诺书

### 修复

- MCP 写工具的 `grant_id` 改为可选：写请求被策略拒绝时可以到达 Broker 并记录 `PATH_DENIED`，而不是在参数校验阶段被当作非法请求丢弃
- 策略拒绝标签改为高对比度红色，审计中更容易识别
- 外部 Agent 的 ACP 预检改为真实 `initialize` 握手

## v2.0.0-rc.2 — 2026-10-29（tag `ccf2026-initial`）

### 新增

- 外部 DeepSeek Harness 接入：ACP stdio、任务级 MCP、只读与隔离副本修改
- 模型凭证只写存储、管理 API 与控制台输入面板
- 控制台改版：欢迎页、架构图、审计并入任务详情、运行状态与轮询

## v2.0.0-rc.1 — 2026-10-27

### 新增

- 工作区快照导入、不可升级的 readonly 任务、隔离副本上的 controlled_edit
- Rust MCP stdio 三工具与代理查询接口
- 任务/事件分页、实例恢复与审计关联

## v1.0.0 — 2026-10-26

### 新增

- 四阶段任务状态机：inspect → edit → verify → done
- 精确路径白名单、限时限次写租约、原子替换
- intent/result 双审计与 JSONL 持久化
- HTTP 契约、最小 TCP 用例与确定性演示

早期每日实现细节见 [开发记录](docs/development-log.md)。
