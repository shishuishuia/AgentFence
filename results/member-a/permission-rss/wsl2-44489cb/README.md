# 权限窗口与 RSS 原始结果索引

来源：2026-10-27 本地 Ubuntu 24.04 WSL2 实测，受测提交 `44489cb826042f734be03dc9a563756feced1f06`。完整结论、环境、观测界限与限制见 [成员 A 实测报告](../../../../docs/member-a-permission-rss-report.md)。

| 数据 | 实际结果 | 入口 |
| --- | --- | --- |
| 权限窗口 | 正式三轮、四场景、24 个实例、30 个区间、12 组比较 | [CSV](permission/permission_windows.csv)、[统计](permission/permission_summary.json)、[清单](permission/run_manifest.json)、[探针/快照](permission/runs.json)、[响应](permission/responses/)、[事件](permission/events/) |
| Rust 服务 RSS | 34/34 场景通过、无跳过；35 个进程、4806 个样本；峰值 5372 KiB | [原始样本](rss/rss_samples.csv)、[汇总](rss/rss_summary.json)、[清单](rss/run_manifest.json)、[完整负载证据](rss/blackbox/) |
| 正式运行日志 | 真实运行输出，仓库绝对前缀替换为 `[REPO]` | [权限日志](permission-formal-20261027.log)、[RSS 日志](rss-formal-20261027.log) |
| 来源核对 | 128 个公开产物的原始字节哈希、151 个源码条目与 Git blob 匹配、完整及离线验证结果 | [provenance.json](provenance.json) |

保留原始 run_id、时间和 `working_tree_dirty=true`。该标志的 Git 查询超时原因见 provenance 和报告；不宣称本批为干净环境验收或远程 CI。公开目录不含私有夹具和凭证；离线复核仅核对导出的证据，不能替代现场文件核验，也不提供防篡改存证。

在仓库根目录执行：

```sh
python -m bench.verify_windows results/member-a/permission-rss/wsl2-44489cb/permission --evidence-only
python -m bench.verify_resources results/member-a/permission-rss/wsl2-44489cb/rss --evidence-only
```

完整复跑和夹具核验命令见 [实验方法](../../../../docs/permission-rss-experiments.md)。`.gitattributes` 保留归档原始字节；此索引是采样后新增说明，不在 provenance 的原始产物哈希列表中。
