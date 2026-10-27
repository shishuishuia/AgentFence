# 真实数据看板验收证据

2026-10-27，本机 Windows、真实 Rust release、独立夹具，分别验证 5173 Vite 与 4173 Python dist 托管。范围和限制见 `docs/member-b-test-report.md`。

- `browser_acceptance.json`：检查范围、版本信息、提交前 dirty 标记、源码/构建/二进制及截图哈希。
- `credential_check.json`：最近最多 5000 条代理边界请求的脱敏汇总，头部只记录是否存在，不保存凭证内容。
- `dist-dashboard.png`、`dist-tasks.png`、`dist-task.png`、`dist-audit.png`：四个主要页面。
- 其余 PNG：开发模式、空数据、断线与重启。

截图里的随机实例/任务 ID 来自实际运行；不同场景使用不同实例，不作为性能数据或全量安全验收。原始夹具和凭证不提交。浏览器报告与自动化故障测试共同支撑 F01～F04，不能以截图代替完整黑盒、Linux 复验或实验交付。
