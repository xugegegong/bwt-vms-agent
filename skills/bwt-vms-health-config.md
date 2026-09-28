---
name: "bwt-vms-health-config"
description: "BWT-VMS 机舱健康配置(S4):为一条船配置 mmha 机舱健康——部件树(mmha_component)+叶子绑工况点(relation)+计算模型(model_info)+中英文+运行条件+Redis 注入验证。用户提到\"机舱健康配置/健康没分数/健康分/mmha 部件树/健康工况点\"时触发。"
---

# BWT-VMS 机舱健康配置(S4)

给一条船配置 BWT-VMS 的"机舱健康"功能(mmha v2 自算链路)。用户提到"机舱健康配置/健康没分数/健康分/m mha 部件树/健康工况点"时触发。

## 核心概念(30 秒讲清)

健康分**不是存的数据,是算出来的**:mmha 服务每分钟跑一次 → 从 Redis `list:ship:{shipId}:other` 读实时工况值 → 按"部件树 + 每叶子工况点模型参数"算每个部件的分 → 父部件按汇总方式聚合 → 结果写 Redis `list:ship:{shipId}:health`(hash: componentKey → `"utc\t分值\t{缺陷json}"`)→ 前端 WS 展示。

三张表决定一切(都在 `vms_sys_db`):
| 表 | 作用 | 关键列 |
|---|---|---|
| `mmha_component` | 部件树(根设备→叶子部件) | component_key、parent_component_key、model_summary_type_code(汇总)、is_leaf、operating_condition_eval(运行条件)、component_name(_en)、svg_url(_en)、identification |
| `mmha_component_device_relation` | 叶子绑工况点 + 模型参数 | component_key、device_key、weight_score、normal_value、normal_value_max、normal_value_min |
| `mmha_component_model_info` | 每个工况点用哪种计算模型 | device_key、model_type_code(01=线性) |

## 第一步:先问"本船要不要机舱健康"

很多船没有此功能。向用户确认:船东/合同是否要求机舱健康、设备健康评估?不要一股脑配。如不需要,结束。

## 前置条件(必须先满足)

1. S2 已完成:`tab_engine_param` 已配好,本船工况点(主机/发电机/轴发相关 device_key)已存在(查:`SELECT device_key, device_name FROM tab_engine_param WHERE ship_id={shipId}`)
2. 有设备清单:主机?几台发电机?轴发?每台设备的工况点清单(温度/压力/转速/功率等)

## component_key 编码规则(XT GLORY 样例,新船照此生成)

- **根设备**:`XX0000` 6位 —— 主机 `000000`、1号发电机 `010000`、2号 `020000`、3号 `030000`、轴发 `040000`(前两位:00=主机,01/02/03=发电机,04=轴发)
- **叶子**:`XXYY` 4位 —— 根前两位 + 两位序号。如主机 `0001`本体/`0002`SCR/`0005`润滑;1号发 `0101`/`0102`
- 根 is_leaf='0'、有 identification(MG/DG1/DG2/DG3/ZDDG)与可选 svg 健康总览图;叶子 is_leaf='1'
- 叶子实际由 relation 表绑多个工况点(如 0001 绑几十个温度/压力点)

**XT GLORY 树参考**:
```
000000 主机(M.E.)          汇总02 MIN
├ 0001 主机本体 ME        { 40793 } >= 0
├ 0002 主机SCR系统 SCR    { 40793 } >= 0
└ 0005 主机润滑系统 L.O.  { 40793 } >= 0
010000 1号发电机(NO.1 D.G.)
├ 0101 1号发电机本体 DG1   { 45889 } >= 0
└ 0102 1号发电机SCR系统    { 45889 } >= 0
020000/030000 2、3号发电机(同构,条件 { 45892 }/{ 45895 } >= 0)
040000 轴带发电机(SHAFT GEN.) └ 0401 轴发本体 { 41005 } >= 0
```

## 流程

### ① 建部件树(mmha_component)
```sql
-- 每设备一条根 + 每设备若干叶子,示例(主机):
INSERT INTO mmha_component
  (ship_id, component_key, component_name, component_name_en, model_summary_type_code, parent_component_key, identification, operating_condition_eval, is_leaf)
VALUES
  ({shipId}, '000000', '主机', 'M.E.',      '02', NULL,  'MG', NULL, '0'),
  ({shipId}, '0001',   '主机本体',   'ME',           '02', '000000', 'ME', '{ 40793 } >= 0', '1'),
  ({shipId}, '0002',   '主机SCR系统','SCR SYSTEM',   '02', '000000', 'SCR','{ 40793 } >= 0', '1');
-- 发电机/轴发同理。汇总方式 02=MIN(子部件取最差)。运行条件=叶子需该设备在运行(绑功率点)。
```

### ② 给叶子绑工况点 + 模型参数(mmha_component_device_relation)
每个叶子把该部件下所有相关工况点都绑进来,并给每个点 `normal_value`(正常值,得100分)/`normal_value_max`/`normal_value_min`(超界得边界分)。**参数从哪拿:船厂 AMS 量程/正常范围,或现场 AMS 界面读额定值**。
```sql
INSERT INTO mmha_component_device_relation
  (ship_id, component_key, device_key, weight_score, normal_value, normal_value_max, normal_value_min)
VALUES
  -- 例:排温正常470、上限600、下限-50(样例,按实际点)
  ({shipId}, '0001', '40769', 1, 800, 1000, 0),
  ({shipId}, '0001', '40001', 1, 32000, 40000, 0);
```

### ③ 给每个工况点配计算模型(mmha_component_model_info)
凡在 relation 里出现的 device_key 都要在此登记模型:
```sql
INSERT INTO mmha_component_model_info (ship_id, device_key, model_type_code, model_code)
SELECT {shipId}, device_key, '01', '01' FROM mmha_component_device_relation WHERE ship_id={shipId}
  AND device_key NOT IN (SELECT device_key FROM mmha_component_model_info WHERE ship_id={shipId});
```
(01=线性模型:normal→100,超[min,max]→边界分,中间线性。)

### ④ 中英文名(必做,否则英文页面空白)
上一步 INSERT 已带 component_name_en。若只建了中文,补:
```sql
UPDATE mmha_component SET component_name_en='M.E.' WHERE ship_id={shipId} AND component_key='000000';
```
同时健康菜单在 `tab_engine_param_menu` 的 device_name_en 也要填(健康页左侧菜单读它)。

### ⑤ 运行条件(operating_condition_eval)
叶子在设备停机时不扣分。表达式 `{ 功率点device_key } >= 0`,值为字符串存。功率点从 `tab_statistics_param` 查本船各设备功率:主机 type=1、发电机 type=13、轴发 type=16。
```sql
SELECT equipment_uuid, device_key FROM tab_statistics_param WHERE ship_id={shipId} AND device_type IN (1,13,16);
```

### ⑥ 配置生效
mmha 服务每 10 分钟重载一次配置缓存;等不及就重启 mmha 容器。

## 验证(必做,交付自证)

1. 有实时值时直接看:等 1 分钟计算周期后
```bash
redis-cli -a <Redis密码> HGETALL "list:ship:{shipId}:health"
# 预期出现各 component_key → "<utc>\t分数\t{}"
```
2. 无实时值(测试环境)模拟:往 Redis 注入"中点值"(normal 与 max 均值 = 分数恰为 (100+边界)/2)
```bash
# 用查到的 relation 生成注入 JSON,例:
redis-cli -a <Redis密码> SET "list:ship:{shipId}:other" '{"40001":"36000","40769":"900", ...}'
# 运行条件功率点也要给(如 40793:"2000")
```
等 1 分钟 → HGETALL health 出分 → 前端机舱健康页显示分数。
3. 颜色规则:≥70 绿(正常)、60~70 黄、<60 红(前端按 60/70 阈值)。

## 可视化路径(优先,无 bug 时)

- EngineHealthConfig 页面(EngineHealthConfig 容器):调 `/mmha/mmha/structureTree/*` 建树/存树;`generateStructureTree` 可让 AI 按工况点自动生成树(异步)
- HealthPointConfig 页面:调 `/mmha/mmha/configuration/*` 维护叶子工况点/模型/建议
- 保存一级部件时会 feign 同步 `tab_engine_param_menu`
- 若可视化页有 bug 或大批量数据,退回 SQL(上面模板)

## 坑(交付高频)

| 坑 | 现象 | 解决 |
|---|---|---|
| **英文空白** | 切英文健康页空白 | component_name_en + tab_engine_param_menu.device_name_en **两套都要填**,系统不自动翻译 |
| **没分数** | 页面 -- | 查 Redis `list:ship:{id}:other` 是否空(无实时值则注入);relation/model_info 是否配齐;mmha 日志 `未配置相关的工况/未获取到实时数据` |
| **运行条件卡死** | 有实时值也不算分 | 条件里的功率点必须在 realtime JSON 里(>0),否则叶子被跳过 |
| **分数范围只有 85~100** | 注入中点只得 92.5,不是 80 | 边界分在 Nacos `health.shipScoreParam`(默认60,被覆盖成85)。要 80 分需把 boundaryScore 改回 60。85~100 是"边界分=85"的正常结果,不是 bug |
| **计算模型缺失** | relation 有值但日志 `未配置计算模型` | mmha_component_model_info 没给该 device_key 登记,补 ③ |
| 配置不生效 | 改了树/参数还是旧结果 | mmha 缓存 10 分钟/重启;Ctrl+F5 清前端 localStorage |

