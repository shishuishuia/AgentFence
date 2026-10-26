# 确定性最小演示

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

本步骤产物为 `minimal_demo.json` 和 `demo_events.jsonl`。当前流程检查成功标志为 `MINIMAL_FLOW_PASS`；完整审计一致性验证与最终 `MINIMAL_DEMO_PASS` 在后续验收步骤加入。任何失败均返回非零并保存已完成的检查。
