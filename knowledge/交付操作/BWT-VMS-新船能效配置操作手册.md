# BWT-VMS 新船能效配置操作手册(SOP)

> 更新日期:2026-09-04(修改本文档后请同步更新此日期,并到 Dify 知识库重新上传该文件)

> 本手册沉淀自 XT GLORY(兴通开荣,化学品船,ship_id=35701001)的能效配置实战。以后配新船,按此流程 + 改参数即可。重点标注了**踩过的坑**,避免重复。
>
> 适用:在现有 BWT-VMS 系统里配置一条新船的 7 个能效功能(能耗分布/能效报告/油水存量/ROB/ECA/排放报告/MRV-DCS)。

---

## 1. 前置资料清单(配船前先凑齐)

| 资料 | 来源 | 必须? | 影响 |
|---|---|---|---|
| **AMS 自动化清册**(工况点清单) | 船厂 | ✅ 必须 | tab_engine_param 数据源,没它什么都做不了 |
| **船舶基础信息**(主机/发电机额定、设计航速等) | 船厂/船舶模板 | ✅ 必须 | 额定参数,CII/EEXI 计算基础 |
| **DWT 载重量** | 设备铭牌/船级社/Equasis 公开数据 | ✅ 必须 | CII/EEXI 计算分母;船舶模板里常空,要单独找 |
| 油舱舱容曲线(height/%↔体积) | 船厂舱容表 | ⚠️ 油水算吨必需 | 没有则油水存量只能显示 %,算不出吨 |
| SFOC POPT(a,b,c) | 主机试航报告 | ⚠️ 能效偏差率必需 | 没有则能效报告只有实测 SFOC,无理论对比 |
| EEXI 计算书 | 船厂 | 可选 | EEXI 精算;系统现成参数可先用 |
| ECA 围栏坐标 | 系统级 | 一次 | ECA 监控,所有船共用 |

---

## 2. 配置流程(7 步,按依赖顺序)

### 第 0 步:船舶基础 + 额定参数

**表**:`tab_ship_info`(船名/MMSI/IMO/船型/船长)+ `tab_ship_info_extend`(额定参数,键值)

**关键额定参数**(param_id):
| param_id | 含义 | 示例(XT GLORY) |
|---|---|---|
| 27 | 主机额定功率(KW) | 3570 |
| 28 | 主机额定转速(rpm) | 118.4 |
| 70 | 发电机额定功率(KW) | 620 |
| 15 | 设计航速(kn) | 13.5 |
| 98 | **DWT 载重量** | 13000 |
| 104 | SFOC POPT(a,b,c) | (试航数据,常缺) |
| 107 | 轴发额定功率 | (船厂) |

> ⚠️ **坑**:`tab_ship_info_extend` 的值列是 **`param_value`**(不是 `value`)!INSERT 用 param_value。

```sql
-- 幂等 INSERT 模板
DELETE FROM tab_ship_info_extend WHERE ship_id = ? AND param_id IN (15,27,28,70,98);
INSERT INTO tab_ship_info_extend (ship_id, param_id, param_value) VALUES
  (?, 27, '3570'), (?, 28, '118.4'), (?, 70, '620'), (?, 15, '13.5'), (?, 98, '13000');
```

### 第 1 步:工况点导入(tab_engine_param)

**来源**:AMS 自动化清册(船厂提供,通常 Excel/xlsm,含 Item No./名称/类型/量程/报警/信号/传感器)

**映射规则**:
| AMS 列 | tab_engine_param 字段 |
|---|---|
| Item No. / 通道号 | device_key(全船唯一) |
| Item Name(中/英) | device_name / device_name_en |
| Symbol + Sig.Type(PI/TI 模拟量→1;Binary 开关量→0;Alarm→2) | device_type |
| Range(0~1.6 MPa) | min_value/max_value + unit(拆数值和单位) |
| Alarm Set Point | low/low_low/high/high_high |
| modbus 地址 | modbus_address + order_id(>100,采集解码用) |
| Item 项目层级 | 机舱菜单树 |

**入口**:配置页「IcommanderConfig」(`/config/visual/engine/backend/save2` 批量)或 xlsx 导入(`/config/visual/engine/upload`,船端)。AMS 表整理成导入模板格式。

> 这步通常**船端配置人员**做(机舱图配置时一起导入)。岸端接手时这步往往已完成。

### 第 2 步:信号路由(tab_statistics_param)— **能效核心**

把工况点标成"系统认的语义",决定能效计算用什么点。

**device_type 语义**:
| device_type | 含义 |
|---|---|
| 1 | 主机功率 |
| 2 | 主机转速 |
| 3 | 航速 SOG(来自 AIS,不在此表) |
| 13 | 发电机功率 |
| 16 | 轴发功率 |
| 40 | 燃油密度(用默认可不配) |
| 41 | 流量计(group_desc 必填) |

**流量计 group_desc 格式**:`{设备}_{编号}_{油种}_{IN/OUT}`,油种:2=轻油 3=重油 9=LNG。例:`mainframe_1_3_IN`(主机1号重油进口)。

**关键决策**(从 AMS 挑点):
- **主机功率**:AMS 无直接主机功率点时,用**轴功率**(实测,最准,比转速³估算靠谱)
- **流量计**:找出主/辅/炉 进油/回油 共 8 个点;**注意 AMS 可能多台设备共用一组流量计**(只能算总量)
- **密度**:用默认(重油0.95/轻油0.85)可不配 device_type=40

```sql
-- 模板(见 XT_GLORY_tab_statistics_param.sql,改 ship_id + device_key)
INSERT INTO tab_statistics_param (ship_id, device_key, device_type, group_desc, equipment_uuid) VALUES
  (?, '轴功率device_key', 1, NULL, 'mainframe_1'),
  (?, '轴转速device_key', 2, NULL, 'mainframe_1'),
  (?, '主机进油', 41, 'mainframe_1_3_IN', 'mainframe_1'),
  (?, '主机回油', 41, 'mainframe_1_3_OUT', 'mainframe_1'),
  ...;
```

> ⚠️ **equipment_uuid 要和 tab_equipment 对齐**(见第 3 步)。

### 第 3 步:能效设备(tab_equipment)

```sql
-- 模板(见 XT_GLORY_tab_equipment.sql)
INSERT INTO tab_equipment (ship_id, uuid, type, number, name) VALUES
  (?, 'mainframe_1', 'mainframe', 1, '主机'),
  (?, 'dynamo_1', 'dynamo', 1, '1号发电机'),
  (?, 'dynamo_2', 'dynamo', 2, '2号发电机'),
  (?, 'dynamo_3', 'dynamo', 3, '3号发电机'),
  (?, 'shaftgenerator_1', 'shaftGenerator', 1, '轴发'),
  (?, 'boiler_1', 'boiler', 1, '锅炉');
```

> **执行顺序**:先跑 tab_equipment(建设备),再跑 tab_statistics_param(信号路由引用 equipment_uuid)。
> `tab_equipment.uuid` 是 **varchar**(接受字符串如 mainframe_1,与油舱表的 int 不同)。

### 第 4 步:油舱配置(tab_tank_device_menu + tab_tank_device)— 油水存量

从 AMS 找油舱液位点(H.F.O./M.G.O./淡水舱),绑 device_key。

> ⚠️ **大坑**:`tab_tank_device.tank_id` / `tank_menu_id` / `tank_menu_father_id` 是 **INT**(不能用字符串如 'HFO')!与 tab_equipment.uuid(varchar)相反。
> 油舱液位 AMS 常是 **%(百分比)**,不是高度 m → maximum 暂填 100(%量程),算吨要舱容曲线。

```sql
-- 模板(见 XT_GLORY_tab_tank_device.sql,tank_menu_id/tank_id 用整数)
INSERT INTO tab_tank_device_menu (ship_id, tank_menu_id, tank_menu_father_id, tank_name, tank_name_en, tank_type, unit, sort, status) VALUES
  (?, 1, 0, '重油舱', 'H.F.O. Tank', 3, '%', 1, 1),
  (?, 2, 0, '轻油舱', 'M.G.O. Tank', 2, '%', 2, 1),
  (?, 3, 0, '淡水舱', 'F.W. Tank', 1, '%', 3, 1);
INSERT INTO tab_tank_device (ship_id, tank_id, tank_device_type, tank_name, tank_name_en, device_key, maximum, minimum, density, unit, tank_menu_id, sort, status) VALUES
  (?, 1, 3, 'No.1 燃料油舱(左)', '...', '液位device_key', 100, 0, 0.95, '%', 1, 1, 1), ...;
```

油种编码:1=淡水 2=轻油 3=重油 5=滑油 9=LNG。

### 第 5 步:排放 / CII / EEXI(大部分现成)

**现成通用(系统初始化已装,不用配)**:
- `tab_eexi_emission_factor`:排放因子 Cf(HFO 3.114 / MGO 3.206 / LNG 2.75)。**先查 `SELECT * FROM tab_eexi_emission_factor` 确认有**
- `tab_cii_reference_lines`:CII 参考线(按 ship_type,如化学品船=8)
- `tab_cii_rating`:CII 评级阈值 d1~d4(按 ship_type)

**本船参数(要配)**:
```sql
-- tab_cii_calculate 本船行(DWT + 船基础)
INSERT INTO tab_cii_calculate (ship_id, ship_name, imo_no, ship_type, dwt, fuel_type, fuel_consumption, distance) VALUES
  (?, '船名', 'IMO', '8', 13000, '3', NULL, NULL);
```

**核对**:`tab_eexi_calculate` 本船的 vref(参考航速)——现成值可能错(如 XT GLORY 填了 23.7,实际设计航速 13.5),改成设计航速或 EEXI 计算书值。

### 第 6 步:ECA 围栏(系统级,一次)

`tab_ais_electric_fence`(+info/extend),配 ECA 区域坐标(多边形/圆),所有船共用。绿色航速限速配置。

---

## 3. 关键决策(默认值,改前确认)

| 决策点 | 默认 | 说明 |
|---|---|---|
| 主机功率来源 | 轴功率实测 | AMS 无直接主机功率点时,用轴功率(最准) |
| 主机油种 | 重油(3) | group_desc 油种段 |
| 辅机油种 | 柴油(2) | |
| 锅炉油种 | 重油(3) | 实际烧轻油则改 2 |
| 燃油密度 | 默认(重油0.95/轻油0.85/淡水1.0) | AMS 无密度点时用默认 |
| equipment_uuid | 可读字符串(mainframe_1) | tab_equipment 和 tab_statistics_param 对齐 |

---

## 4. 常见坑(踩过,务必避开)

| 坑 | 现象 | 解决 |
|---|---|---|
| **列名错** | `Unknown column 'value'` | `tab_ship_info_extend` 值列是 **`param_value`** |
| **油舱 id 类型错** | `Incorrect integer value: 'HFO'` | `tab_tank_device` 的 tank_id/tank_menu_id/tank_menu_father_id 是 **INT**(不是字符串) |
| **collation 不一致** | `Illegal mix of collations ... 1267` | utf8_unicode_ci 的表统一成 utf8_general_ci(主流);utf8mb4_bin 的第三方表保留 |
| **equipment_uuid 不对齐** | JOIN 找不到设备 | 先建 tab_equipment,再配 tab_statistics_param,两边 uuid 一致 |
| **id 类型不统一** | 各表不同(tab_equipment.uuid 是 varchar,tab_tank_device.tank_id 是 int) | 每张表 DESC 确认类型,别假设 |
| **流量计 group_desc 格式** | 算不出油耗(NumberFormatException) | 严格 `{设备}_{编号}_{油种}_{IN/OUT}` |
| **AMS 液位是 %** | 油水算不出吨 | 配 tab_height_cabin 舱容曲线(%↔体积);没资料则只显示 % |

---

## 5. SQL 模板清单(已生成,改参数复用)

配 XT GLORY 生成的 SQL 文件(在工作目录),新船改 `ship_id` + `device_key`(对应新船 AMS)+ 额定参数复用:

| 文件 | 内容 |
|---|---|
| `XT_GLORY_tab_statistics_param.sql` | 信号路由(改 device_key) |
| `XT_GLORY_tab_equipment.sql` | 设备 + 额定参数(改额定值) |
| `XT_GLORY_tab_tank_device.sql` | 油舱(改 device_key/油种) |
| `XT_GLORY_DWT_CII.sql` | DWT + CII 本船 + vref 核对(改 DWT/船型) |
| `XT_GLORY_信号路由模板.csv` | 信号路由 Excel 模板 |

**复用步骤**:复制这些 SQL → 全局替换 ship_id(35701001 → 新船)→ 替换 device_key(对应新船 AMS 点位)→ 核对额定参数/油种/DWT → 跑。

---

## 6. 7 功能验证矩阵(配完怎么看对不对)

| 功能 | 系统页面 | 验证点 |
|---|---|---|
| 能耗分布 | 「能耗分布监控」(实时) | 4 个图块有数据(油耗分布/排温/功率/发电机) |
| 能效报告 | 「主机负荷」/「能效报告」 | SFOC 值出来(偏差率待 SFOC POPT) |
| 油水存量 | 「油水填报」/「油水存量查询」 | 各舱液位 % 显示(吨待舱容) |
| 排放报告 | 「排放报告」(/emissionReport) | CO2 趋势 + 每运输功排放 + CII 评级字母 |
| CII/EEXI | 「排放报告」+「碳强度计划」 | CII A-F 评级;碳强度三年计划 |
| MRV/DCS | 「能耗分析」(/EnergyEfficiencyAnalysis) | 导出 MRV/DCS 年度报告 |
| ECA | 「航行监控」地图 | 船进 ECA 触发提示 |

---

## 7. 资料获取渠道(船厂不给时)

| 资料 | 渠道 |
|---|---|
| DWT | 设备铭牌 / 船级社(CCS)证书 / Equasis(免费船舶数据库) / 船讯网 |
| 主机试航 SFOC POPT | 主机厂试航报告 / 船厂调试记录 |
| 油舱舱容曲线 | 船厂舱容表 / 船舶稳性计算书 |
| EEXI 计算书 | 船厂(IMO EEXI 规定 2023 后新船必备) |

---

## 附录:XT GLORY 配置示例(参考船)

- 船:兴通开荣 / XT GLORY,ship_id=35701001,MMSI=563338500,IMO=1115395
- 船型:化学品船(ship_type=8),DWT=13000
- 主机:GDF UEC35LSE-C1-LPSCR,3570KW / 118.4rpm,1 台 6 缸
- 发电机:3 台 5DK-20e / 620KW
- AMS 工况点:1576 个
- 油舱:4 重油 + 5 轻油 + 2 淡水 = 11 个
- collation:tab_equipment 统一成 utf8_general_ci(原 utf8_unicode_ci)
