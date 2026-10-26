# AgentFence HTTP 客户端

Python 3.10+，仅使用标准库；实际验证版本见 B 的运行记录。此处只负责 HTTP，不直接读取或修改目标文件。

```python
import os
from integrations import AdminClient, AgentClient, BrokerHttp

http = BrokerHttp("http://127.0.0.1:8080", timeout=5)
http.health().require(200)
admin = AdminClient(http, os.environ["AGENTFENCE_ADMIN_TOKEN"])
created = admin.create_task("demo").require(201)
agent = AgentClient(http, created["task_id"], created["agent_token"])
result = agent.read_file("src/main.rs")
result.require(200)
```

`ApiResponse` 保留 HTTP 状态和原始 JSON。拒绝响应也是正常返回值，便于检查 `reason_code/allowed/executed`；`require(expected_status)` 可对状态做断言，异常不输出响应内容。创建响应含一次性 Agent Token，不应打印或保存完整创建响应。

连接地址只接受数字回环 HTTP 地址。传输禁用重定向、环境代理和自动重试，限制响应大小。写入超时/断连可能已经执行，调用方必须核对文件、租约和审计后决定下一步，不能直接重试。

测试：在仓库根目录执行 `python -m unittest discover -s integrations/tests -v`。完整真实 Rust 联调由 `demo/` 脚本完成。
