# 本地只读代理

Python 3.10+，仅用标准库。默认监听 `127.0.0.1:4173`。

独立启动保持以下只读边界。若要从页面启动固定 Agent 任务，使用显式的 `python -m demo.run_live_dashboard`，见 [交互演示](../demo/README.md)。它额外安装 GET `/api/demo/state` 和 POST `/api/demo/control`；控制仅接受固定 start/pause/resume 与请求键，不透传 Rust 写接口。POST 强制本地 Origin、自定义请求头、JSON 和 256 字节上限；不支持跨域预检，凭证不进入浏览器。

额外指定 `--agent deepseek` 时，start 可带 task 文本，控制体上限为 8192 字节；任务最多 1000 字符/4000 UTF-8 字节。其他控制仍不可带任务，未知字段（模型、URL、密钥、路径等）均拒绝。无论模式，控制动作均不是任意 Rust 写接口的映射。配置与真实模型边界见 [DeepSeek Agent](../docs/deepseek-agent.md)。

启动前在当前终端设置 `AGENTFENCE_ADMIN_TOKEN`（与 Rust 一致，32～256 个可见 ASCII 字符），以及可选 `AGENTFENCE_BROKER_URL`（默认 `http://127.0.0.1:8080`，仅数字回环 HTTP 地址）。不要将凭证放入命令参数、源码、`VITE_` 环境变量或浏览器。

```sh
python -m web_proxy --mode dev
# 或先构建前端，再托管 front/dist：
npm --prefix front run build
python -m web_proxy --mode dist
```

只提供 GET `/api/health`、`/api/tasks`、`/api/tasks/:id`、`/api/events`。浏览器 Authorization/Cookie 不转发。Host 和有值的 Origin 限实际本机 4173/5173；使用 `127.0.0.1`，不要使用未配置的 localhost。Vite 5173 转发 /api 至本服务。

已知 API 非 GET 返回 405，未知 API 返回 404，参数错误 400。Rust 错误保持原状态与 JSON；连接失败 502 BROKER_UNAVAILABLE，5 秒超时 504 BROKER_TIMEOUT，异常响应 502 BROKER_INVALID_RESPONSE。API 不缓存，不自动重试，不跟随重定向。

dist 模式支持 HTML 页面深链接；缺失静态资源和 API 错误不回退首页。Ctrl+C 关闭本服务。自动启动真实服务及展示夹具见 `python -m demo.run_dashboard` 和 `demo/README.md`。默认固定 4173；`--port 0` 仅供隔离测试，Vite 仍指向 4173。

```sh
python -m unittest discover -s web_proxy/tests -v
```
