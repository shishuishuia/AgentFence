# 成员 B 独立 HTTP 黑盒结果

linux-fd73fbe/ 来自 GitHub Ubuntu CI 的真实运行：34 个固定场景通过、0 失败、0 跳过，包含 41 个实测 HTTP 变体。受测源码 fd73fbe9e10a53dfa58143596134dd8deb372bd6，工作区干净。

CSV 和 JSON 保留原始受测版本；provenance.json 记录 CI 链接、artifact ID 和 archive SHA-256。只提交脱敏 public 证据，不提交原始夹具。完整说明见 docs/member-b-blackbox-report.md。

```sh
python -m demo.e2e_verify results/member-b/blackbox/linux-fd73fbe --evidence-only
```

该命令检查导出证据内部一致性；实际文件及原始磁盘审计已在 CI 的完整运行中复核，不把离线检查冒称为再次启动服务。
