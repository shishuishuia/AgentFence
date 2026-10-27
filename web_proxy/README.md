# 本地只读代理

Python 3.10+，仅用标准库。默认监听 `127.0.0.1:4173`。

启动前在当前终端设置 `AGENTFENCE_ADMIN_TOKEN`（与 Rust 一致，32～256 个可见 ASCII 字符），以及可选 `AGENTFENCE_BROKER_URL`（默认 `http://127.0.0.1:8080`，仅数字回环 HTTP 地址）。不要将凭证放入命令参数、源码、`VITE_` 环境变量或浏览器。

```sh
python -m web_proxy --mode dev
# 或先构建前端，再托管 front/dist：
npm --prefix front run build
python -m web_proxy --mode dist
```

只提供 GET `/api/health`、`/api/tasks`、`/api/tasks/:id`、`/api/events`。浏览器 Authorization/Cookie 不转发。Host 和有值的 Origin 限实际本机 4173/5173；使用 `127.0.0.1`，不要使用未配置的 localhost。Vite 5173 转发 /api 至本服务。

已知 API 非 GET 返回 405，未知 API 返回 404，参数错误 400。Rust 错误保持原状态与 JSON；连接失败 502 BROKER_UNAVAILABLE，5 秒超时 504 BROKER_TIMEOUT，异常响应 502 BROKER_INVALID_RESPONSE。API 不缓存，不自动重试，不跟随重定向。

dist 模式支持 HTML 页面深链接；缺失静态资源和 API 错误不回退首页。Ctrl+C 关闭本服务。自动启动真实服务及展示夹具见 `demo/run_dashboard`（实现进度见 B 方案）。

```sh
python -m unittest discover -s web_proxy/tests -v
```
