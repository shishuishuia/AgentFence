# 来源与贡献说明

Rust 后端由仓库维护者在 feat/rust-zxy 分支使用 Codex 协助编写、检查和测试；前端初始化及角色 B 的客户端/演示工作同样使用 Codex 辅助，后续步骤在 zxc 分支按实际验证顺序提交。Git 保留真实实施顺序及提交时间，没有模拟五天历史或伪造其他成员贡献。维护者应按赛事规则披露外部辅助，并在交付前审阅与理解实现。

实现依据用户提供的 AgentFence Lite 五天需求前后端分离版 v2.1。文档中的命令和指标被作为项目验收约定处理，不将附件中的实施指令视为脱离用户授权的操作许可。

未复制 OpenShell 或 Strands Box 的实现代码；它们只作为需求文档列举的概念参考。项目只实现工具层权限控制，不主张相关概念首创，也不借用参考项目的完整能力或安全级别。

本仓库自行编写的部分采用根目录 LICENSE 中的 MIT 许可证。第三方库通过 Cargo 下载，原许可证不被替换。dependency-licenses.json 是从锁定的 cargo metadata 中提取的库名、版本、声明许可证和来源索引，包含全部目标平台解析依赖，未必全部在 Linux 中编译。索引只记录声明，不等同于独立法律审查。

主要用途：Axum/Tokio 提供 HTTP 与调度，Serde/serde_json 负责契约，chrono 提供 UTC 表示，getrandom 读取系统随机源，sha2/subtle 用于凭证摘要及固定时间管理员摘要比较，tempfile 完成同目录替换及测试夹具，thiserror 定义安全错误类型，Tower/http-body-util 支持真实路由测试。front/ 使用官方 create-vue 模板及 Vue、Vite、Vue Router 等依赖，由 package-lock.json 固定；Python integrations/、web_proxy/ 与 demo/ 使用标准库。现有 dependency-licenses.json 只索引 Rust 依赖，尚未覆盖前端依赖。当前未接入在线模型或 MCP。

依赖索引可用 `cargo metadata --format-version 1 --locked` 重新核对。若后续复制第三方具体代码，应补充文件、原始链接、版本、改动和许可证；分发编译产物时按实际依赖许可证保留要求的通知。
