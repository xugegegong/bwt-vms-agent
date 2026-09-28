# 技能索引

交付技能 S0–S6 + FAQ + SVG 规则，已转为**工具无关的 markdown**（codex / claude / workBuddy / zcode 都能直接读取）。

| 技能 | 触发场景 | 状态 |
|---|---|---|
| S0 配新船（bwt-vms-new-ship.md） | 新船要配 / 交付总编排 | ✅ 已整合 |
| S1 采集（bwt-vms-iscu-config.md） | ISCU / datacap / JsonString / hldkeymap / MOXA | ✅ 已整合 |
| S2 库表（bwt-vms-sysdb-config.md） | 工况点库 / tab_engine_param 增删改 | ✅ 已整合 |
| S3 mimic 绑定（bwt-vms-mimic-binding.md） | SVG 上传 / 绑定工况点 | ✅ 已整合 |
| S4 机舱健康（bwt-vms-health-config.md） | mmha 部件树 / 健康分 | ✅ 已整合 |
| S5 能效（bwt-vms-energy-config.md） | 能效设备 / 信号路由 / 油舱 / 舱容 / CII | ✅ 已整合 |
| FAQ（bwt-vms-faq.md） | 交付排错 / 症状定位 | ✅ 已整合 |
| SVG 生成规则（svg-generation-rules.md） | 画监控图 SVG | ✅ 已整合 |

> 技能里提到的工具（如 iscu_verify / apply / make_jsonstring）目前对应 `tools/` 下的脚本；后续按架构文档拆成 HTTP API / MCP Server 后，再把技能里的调用方式改为调工具接口。
