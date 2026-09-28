---
name: "bwt-vms-energy-config"
description: "BWT-VMS 能效系统配置(S5):为一条船配置能效功能——能效设备(tab_equipment)+信号路由(tab_statistics_param)+油舱(menu/tank_device)+舱容(tab_height_cabin)+CII/EEXI。用户提到\"能效配置/信号路由/能效设备/油水配置/舱容/ROB/CII/EEXI/排放因子/流量计\"时触发。"
---

# BWT-VMS 能效系统配置(S5)

为一条船配置 BWT-VMS 的能效相关功能(能耗分布/能效报告/油水存量/ROB/排放报告/CII/EEXI/MRV-DCS/ECA)。用户提到"能效配置/信号路由/能效设备/油水配置/舱容/ROB/CII/EEXI/排放因子/流量计/加装新船能效"时触发。

## 第一步:先问"本船要不要能效"

很多船(尤其非合同要求)没有能效功能。确认船东/合同是否要求:能耗监控、CII 碳强度、排放报告、油水报告?按需做,不要全配。

## 前置条件(S2 已完成)

`tab_engine_param` 工况点已配好。能效 = 给已有工况点打"语义标签"(这是主机功率、那是流量计、那是液位),+ 建设备 + 建油舱 + 配 CII 参数。

## 需要船厂哪些资料

- AMS 工况点清单(含设备归属)→ 已在 tab_engine_param
- 主机/发电机额定功率、转速、设计航速
- DWT 载重量(paramId 98)
- 燃油舱/淡水舱**舱容表**(高度↔体积)——没有则油水只能显示液位,算不出吨
- SFOC POPT(试航,可选,算能效偏差率)
- ECA 围栏(系统级一次,所有船共用)

## 流程(严格按依赖顺序)

### ① 能效设备(tab_equipment)
先建设备,后面信号路由的 equipment_uuid 引用它。**uuid 是 varchar**(可读字符串 mainframe_1/dynamo_1...)。
```sql
INSERT INTO tab_equipment (ship_id, uuid, type, number, name) VALUES
  ({shipId}, 'mainframe_1', 'mainframe', 1, '主机'),
  ({shipId}, 'dynamo_1', 'dynamo', 1, '1号发电机');
-- type: mainframe/dynamo/shaftGenerator/boiler
```

### ② 信号路由(tab_statistics_param)——能效核心
给工况点标语义 device_type。**device_type 语义**(能效口径):
1=主机功率、2=主机转速、13=发电机功率、16=轴发功率、40=燃油密度(可省,用默认)、41=流量计(group_desc 必填)。
```sql
INSERT INTO tab_statistics_param (ship_id, device_key, device_type, group_desc, equipment_uuid) VALUES
  ({shipId}, '主机功率点', 1,  NULL, 'mainframe_1'),
  ({shipId}, '主机转速点', 2,  NULL, 'mainframe_1'),
  ({shipId}, '主机进油',  41, 'mainframe_1_3_IN',  'mainframe_1'),
  ({shipId}, '主机回油',  41, 'mainframe_1_3_OUT', 'mainframe_1'),
  ({shipId}, '1号发电功率', 13, NULL, 'dynamo_1');
-- 流量计 group_desc 格式严格:{设备}_{编号}_{油种}_{IN/OUT} 油种:2=轻油 3=重油 9=LNG
```
要点:AMS 无主机功率点时用**轴功率实测**(最准);辅机多台共用流量计只能算总量;主机烧重油、辅机烧柴油。

### ③ 油水舱(tab_tank_device_menu + tab_tank_device)——油水存量/ROB
```sql
-- 菜单分类(⚠️ tank_menu_id 必须 = tank_device_type:1淡水 2轻油 3重油)
INSERT INTO tab_tank_device_menu (ship_id, tank_menu_id, tank_menu_father_id, tank_name, tank_name_en, tank_type, unit, sort, status) VALUES
  ({shipId}, 3, 0, '重油舱', 'H.F.O. Tank', 3, 't', 1, 1),
  ({shipId}, 2, 0, '轻油舱', 'M.G.O. Tank', 2, 't', 2, 1),
  ({shipId}, 1, 0, '淡水舱', 'F.W. Tank',   1, 't', 3, 1);

-- 子舱(⚠️ tank_id 从 4 开始编号,别用 1/2/3 防与 device_type 碰撞;unit='t' 页面显示吨)
INSERT INTO tab_tank_device (ship_id, tank_id, tank_device_type, tank_name, tank_name_en, device_key, maximum, minimum, density, unit, tank_menu_id, sort, status) VALUES
  ({shipId}, 4, 3, 'No.1 燃料油舱(左)', 'No.1 H.F.O. Tank (P)', '液位点', 9.32, 0, 0.98, 't', 3, 1, 1);
-- density 用舱容表实测:HFO 0.98 / MGO 0.90 / FW 1.00
-- maximum = 满舱高度(m),从舱容表取
```

### ④ 舱容曲线(tab_height_cabin)——算吨必需
```sql
-- ⚠️ 列名:cabin(体积),不是 capacity;tank_id 是 bigint
INSERT INTO tab_height_cabin (ship_id, tank_id, height, cabin) VALUES
  ({shipId}, 4, 0.00, 0.10),
  ({shipId}, 4, 0.10, 0.35), ...; -- 每 10cm 一行,舱容表逐行抄
```
没有舱容表时:油水只显示液位/吨算不出,可先跳过(影响"算吨"一个子功能)。

### ⑤ CII/EEXI/排放
- 排放因子 `tab_eexi_emission_factor`、CII 参考线/评级 都是**系统级现成**,先查确认有,不用配
- 本船:paramId 98 = DWT(**值列 param_value 不是 value!**);`tab_cii_calculate` 本船行(DWT/船型);核对 `tab_eexi_calculate` vref(常被填错,化学品船 13.5 节左右,不是 23.7)
```sql
INSERT INTO tab_ship_info_extend (ship_id, param_id, param_value) VALUES ({shipId}, 98, '13000');  -- DWT
DELETE FROM tab_cii_calculate WHERE ship_id={shipId};
INSERT INTO tab_cii_calculate (ship_id, ship_name, imo_no, ship_type, dwt, fuel_type) VALUES ({shipId}, '船名','IMO','船型编码', 13000, '3');
```

### ⑥ ECA(可选,系统级)
`tab_ais_electric_fence` 配 ECA 多边形,所有船共用,一般不用每船配。

## 验证(交付自证)

- **能耗分布**:页面出现油耗分布/功率/排温图块
- **能效报告**:SFOC 有值(偏差率待 SFOC POPT)
- **油水**:各舱显示吨(配了舱容)或液位;填一条看 `tab_oil_water` 有数据、单位是 t
- **排放/CII**:排放报告出 CO2 趋势 + CII 评级字母
- SQL 自检:`SELECT device_type, COUNT(*) FROM tab_statistics_param WHERE ship_id={shipId} GROUP BY device_type;` 预期 type1/2/13/16/41 各有点

## 可视化路径(优先)

EnergyEngineParam(能效参数)、EnergyEffciencyDevice(设备)、OilTankParam(油舱)、flowmeterParam(流量计),都在 ConfigurationManagement(spadmin→系统配置)→ 选中船。接口前缀 `/config/visual/energyefficiency/*`、`/oilCabin/*`、`/flowmeter/*`。有 bug 或大批量退回 SQL。

## 坑(交付高频)

| 坑 | 现象 | 解决 |
|---|---|---|
| tab_ship_info_extend 列名 | Unknown column 'value' | 值列是 **`param_value`** |
| 油舱 id 用字符串 | Incorrect integer value: 'HFO' | tank_id/tank_menu_id 是 **INT**;equipment.uuid 才是 varchar |
| tank_menu_id 顺序错 | 油水填报保存后点添加无反应(StackOverflow) | tank_menu_id **必须 = tank_device_type**(重油3/轻油2/淡水1);tank_id 从 4 起不碰 1/2/3 |
| 单位显示 m | 油水页存量/加量单位是 m | tab_tank_device/menu 的 unit 改 **'t'**(页面显示单位) |
| 舱容列名 | Unknown column 'capacity' | tab_height_cabin 体积列是 **`cabin`** |
| 流量计 group_desc 错 | 算不出油耗 | 严格 `{设备}_{编号}_{油种}_{IN/OUT}` |
| AMS 液位是 % | 算不出吨 | 确认 AMS 报的是高度 m 还是 %;报 % 需换算或另配 |
| 额定参数配错表 | CII/EEXI 算错 | tab_ship_info_extend 用 param_value;vref 用设计航速 |

## 复用模板

工作目录有 XT GLORY 全套 SQL(改 ship_id/device_key 复用):`XT_GLORY_tab_statistics_param.sql`、`XT_GLORY_tab_equipment.sql`、`XT_GLORY_tab_tank_device.sql`、`XT_GLORY_tab_height_cabin.sql`、`XT_GLORY_DWT_CII.sql`。配新船:全局替换 ship_id → 换 device_key(对应新船 AMS)→ 核对额定参数/油种/密度 → 跑。

