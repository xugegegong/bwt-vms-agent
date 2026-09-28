---
name: "bwt-vms-new-ship"
description: "BWT-VMS 配置新船(S0)交付总编排:收资料+技术协议判定功能→建船→S1采集(闸门)→S2库表→S3图/S4健康/S5能效按需→验收,调度子skill并跟踪进度核对表。用户说\"新船要配/新船交付/配船\"时触发。"
---

# BWT-VMS 配置新船(S0):交付总编排

新船交付配置的**总入口/总调度**。用户说"来了一条新船要配置/新船交付/配船"时触发。本 skill 不讲细节,只做**流程判定与调度**——各环节细节在子 skill 里。

## 子 skill 索引(按需调用)

| 阶段 | skill | 内容 |
|---|---|---|
| S1 采集 | bwt-vms-iscu-config | MOXA+清单→iscu→datacap 出数(闸门) |
| S2 库表 | bwt-vms-sysdb-config | tab_engine_param/menu/relationship |
| S3 画图 | svg-generation-rules | SVG 代码生成规范 |
| S3 绑定 | bwt-vms-mimic-binding | 上传/绑定/中英两套 |
| S4 健康 | bwt-vms-health-config | mmha 部件树/模型/验证 |
| S5 能效 | bwt-vms-energy-config | 信号路由/油舱/舱容/CII |
| S6 排错 | bwt-vms-faq | 症状→定位→修复 |

## 总流程

### 第 0 步:收船信息 + 判定功能范围
向交付要齐:
1. **技术协议** → 判定:本船要不要 机舱健康(S4)?能效(S5)?可视化 mimic 图(S3)?——**逐项问,交付去查技术协议**,大多数船没有健康/能效,别多配
2. **基础资料**:船名/IMO/MMSI/ship_id/船型/DWT/主机发电机额定参数
3. **采集资料**(S1 要):MOXA 串口规划、AMS 信号清单(FLOAT/INT sheet)、BTP/LOAD 等特殊设备清单
4. **能效附加**(S5 要,若配):油舱舱容表、DWT、CII 船型
5. **健康附加**(S4 要,若配):设备清单与各设备工况点范围

### 第 1 步:建船(可视化)
spadmin → 系统配置(ConfigurationManagement)→ 船舶列表 → 添加:船名/IMO/MMSI/船型/公司/船队。后端自动建 `vms_{shipId}_base_db` 并初始化(tab_statistics_param 模板/报警映射/EEXI 行)。
⚠️ 用可视化建船,别手工 INSERT(自动建库+初始化逻辑多)。

### 第 2 步:S1 采集(闸门前的一切)
调度 bwt-vms-iscu-config:MOXA 配置 → 清单核对 → make_jsonstring → apply_to_iscu → 重启 datacap → **出数验证**。
⛔ **闸门:datacap 日志确认出数无误前,禁止进入 S2**(order_id 强绑定,两边同时改必返工)。

### 第 3 步:S2 岸端库表
调度 bwt-vms-sysdb-config:菜单规划(人工 CSV)→ dev_type 抽查 → make_engine_param → 备份落库 → 验证(前端机舱监控出菜单/点)。

### 第 4 步(按技术协议):S3 图 / S4 健康 / S5 能效
- 要可视化 → svg-generation-rules 画图 + bwt-vms-mimic-binding 绑定(中英两套!)
- 要健康 → bwt-vms-health-config(部件树→绑点→模型→Redis 注入验证)
- 要能效 → bwt-vms-energy-config(设备→信号路由→油舱→舱容→CII)
S4/S5 都依赖 S2 的 device_map/tab_engine_param。

### 第 5 步:整船验收 + 交付包
跑一遍 S6 FAQ 的验证清单 + 本船核对表(下方)全勾。交付存档:信号清单原件、device_map.csv、menu_plan.csv、各生成 SQL、备份文件路径。

## 新船配置进度核对表(每船复制一份跟踪)

```
船名/ship_id:________  技术协议:健康□ 能效□ 图□
[ ] 0 资料齐(技术协议判定/基础/MOXA/AMS清单[/舱容表])
[ ] 1 建船(可视化,自动建库)
[ ] 1 S1: MOXA 各口出数
[ ] 1 S1: make_jsonstring 核对报告清零
[ ] 1 S1: iscu 落库验证(行数/无重复orderkey)
[ ] 1 S1: ★闸门 datacap 出数(Redis other 有 JSON)
[ ] 2 S2: 菜单规划核对(兜底分类点数合理)
[ ] 2 S2: dev_type 抽查
[ ] 2 S2: 落库(已备份) + order_id 对齐=0 差异
[ ] 2 S2: 前端机舱监控出菜单/实时值
[ ] 3 图: SVG 规范生成 → 绑定 → 中文保存 → 英文保存 → 双语验证(按需)
[ ] 4 健康: 树/绑点/模型/中英文 → Redis 注入出分(按需)
[ ] 5 能效: 设备/路由/油舱/舱容/CII → 各页面验证(按需)
[ ] 6 验收: FAQ 验证清单 + 本表全勾 + 交付包存档
```

## 全局铁律

1. **S1 出数验证是闸门**,过闸门前不碰 vms_sys_db
2. **凡动 vms_sys_db/iscu,先备份**(mysqldump;可视化配置页有整船覆盖风险,见《配置中心数据安全风险清单》)
3. **中英文两套字段处处都要**(menu/param/mmha/svgUrl),系统不自动翻译
4. 新问题解决后 → 补进 bwt-vms-faq + 《BWT-VMS交付排错手册.md》

