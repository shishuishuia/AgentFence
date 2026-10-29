# Demo projects

Three small projects used by the live demo. Each is imported as a read-only
workspace; the last two are intentionally denied by the Broker.

| Directory | Workspace | Purpose |
| --- | --- | --- |
| `python-demo/` | Python 演示项目 | Read-only review of a small project with three planted bugs |
| `escape-demo/` | 越权读取演示 | `secrets.txt` exceeds the 64 KiB import limit; a read attempt is denied with `PATH_DENIED` |
| `write-demo/` | 越权写入演示 | Creating `backdoor.py` in an isolated edit copy is denied with `PATH_DENIED` |

The denial workspaces do not contain any real secret: `secrets.txt` is a
generated placeholder used only to exceed the import limit.
