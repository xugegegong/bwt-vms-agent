# BWT-VMS 产品功能设计与实现 · 大屏看板域(D09)

> 更新日期:2026-09-04(修改本文档后请同步更新此日期,并到 Dify 知识库重新上传该文件)

> 遵循模板。本域是系统的"可视化指挥中心"。

---

## 1. 域概述

| 项 | 内容 |
|---|---|
| 定位 | 全屏大屏式展示:船队看板(首页)、单船数据看板、单船入口;聚合航行/油耗/排放/装载/报警指标 |
| 核心价值 | 管理层一屏掌握船队与单船全貌 |
| 边界 | 只管"看板展示";数据生产在各域;Conning 罗经在 D03 |
| 四个前端页面 | FleetKanBan(`/` 船队)、DataBoard(`/Databoard` 单船分析)、SingleExc(`/SingleExc` 单船入口)、Conning(D03) |
| ⚠️ 重要发现 | 后端老大屏 `/screen/*`、新大屏 `/newscreen/*` 的**单船维度接口前端完全未消费**(字典有定义、无组件调用),属预留/未启用;实际消费的是 `/screen/fleet/*`、`/sys/shipFleet/*`、`/sys/singleShip/*` |

---

## 2. 功能点清单

```
F09 大屏看板
├─ F09-1 船队看板 FleetKanBan(首页)
├─ F09-2 数据看板 DataBoard(单船分析)
├─ F09-3 单船入口 SingleExc
├─ F09-4 老大屏 BigScreen(/screen/*,部分启用)
├─ F09-5 新大屏 NewBigScreen(/newscreen/*,预留)
└─ F09-6 Conning 罗经(引用 D03)
```

---

## 3. 逐功能点详述

### F09-1 船队看板 FleetKanBan(首页)

**(1) 功能设计**:全屏地图 + 四角浮动卡片的暗色大屏,展示船队级聚合指标。登录岸端后默认首页。

**(2) 布局**:底图 OpenLayers 6(自建高德纠偏瓦片 `map/roadmap/{z}/{x}/{y}.png`);左上(船舶总数 + 3 饼图:船型/在航率/滞留率);底部中央(3 折线:油耗/排放/单位运输功排放);地图层每船 Overlay 按 status 切图标。

**(3) 指标与接口**:

| 区域 | 指标 | 接口 |
|---|---|---|
| 左上 | 船舶/船员总数 | `/sys/screen/fleet/baseData` |
| 左上 | 船型饼图 | `/sys/screen/fleet/shipData` |
| 左上 | 在航率 | `/sys/shipFleet/totalShipsSailingAndPort` |
| 左上 | 滞留率 | `/sys/shipFleet/totalShips` |
| 底部 | 油耗/排放/单位排放 | `/sys/shipFleet/energyMutiEmission` |

**(4) 刷新**:REST 每 10 分钟全量刷新;WebSocket over_all 订阅 → Worker → window.warn → 每 1s 刷新船位 marker。

**(5) 交互**:点船 → 写 localStorage → 跳 `/SingleExc`。

**(6) ⚠️ 预留未渲染**:满载率图、公告、BDI 指数代码存在但 render 注释。

---

### F09-2 数据看板 DataBoard(单船分析)

**(1) 功能设计**:全屏地图 + 左侧船舶/航次/Conning + 底部/右侧图表,单船深度分析。

**(2) 布局**:底图(目标船/起讫港/历史轨迹图层);左侧(船图+船舶参数 paramId 98载货量/24空载吃水/25满载吃水 + 航次摘要 + **Conning SVG 仪表**);底部(平均航速/每日里程/里程统计 3 折线);右侧(燃油存量/油耗统计)。

**(3) 接口**(`/sys/singleShip/*`):fuelInventory/fuelConsumption/mileageStatistics/dailyMileage/dailyAverageSpeed/crewInformation/loadingRate(未渲染)/violationStatistics(未渲染)等;+ 船参数 `/sys/ship/info/detail`、航段 `/sys/segment/current`、航次 `/sys/flight/summary`、轨迹 `/sys/route/history`。

**(4) 刷新**:WS focus(Conning 全量+目标船,1s);REST 船信息 1h/航次 1min/ECharts 10min。

---

### F09-3 单船入口 SingleExc

**(1) 功能设计**:**过渡页**(文件头注释明确),全屏地图 + 右下角船舶信息浮窗,提供"监控/分析"两个跳转入口。

**(2) ⚠️ 现状**:render 实际只渲染地图 + 右下浮窗(船名/图片/航向/吃水/MMSI/航速/状态/船宽/船长 + 监控→`/NavigateMonitor`/分析→`/DataBoard` 两按钮)。代码中大量 conning/ECharts/气象/图鉴逻辑是从旧版 Map 迁移的**残留死代码**,render 未输出。

**(3) 真正的单船看板**:`/DataBoard`(分析)与 `/NavigateMonitor`(监控),SingleExc 仅作地图入口。

---

### F09-4 老大屏 BigScreen(`/screen/*`)

**(1) 接口**(12 个:单船 8 + 船队 4):baseData/alarmData/fuelConsumption/mileageStatistics/violationStatistics/watersNotice/voyageStatistics/shipOperations/loadingInformation + fleet/baseData + fleet/shipData + fleet/voyageStatistics + fleet/shipOperations。

**(2) ⚠️ 消费现状**:仅 `fleet/baseData`、`fleet/shipData` 被 FleetKanBan 消费;**单船维度 8 接口前端无调用方**(预留)。

**(3) 数据来源**:读 Redis `list:ship:{id}:online/ais`、tab_alarm_statistics、tab_oil_water_consume(优先)+tab_fuel_consumption(降级)、tab_ais_ship_segment、tab_behavior_recognition(违章)、tab_channel_notice(水域通告,thirdPartyCall 同步)。

---

### F09-5 新大屏 NewBigScreen(`/newscreen/*`)

**(1) 接口**(5 个,船队聚合):loadingInformation(月度装载率+货种 Top5)、mileageStatistics、fuelConsumption、**fuelRate(千吨公里耗柴油)**、fuelConsumptionLng(LNG)。

**(2) ⚠️ 消费现状**:**全部前端无调用方**(预留)。

**(3) 与老大屏区别**:船队视角为主;装载来源 tab_load_management(结构化,老大屏用 tab_sail_log paramUuid);油耗统一 tab_fuel_consumption;不读 Redis。

**(4) 新增指标**:
- 千吨公里耗柴油:`总油耗(t)×1000 / Σ(航次装载(kg)/1000 × 航次里程(海里)×knotFactor)`,单位 kg/(千吨·公里)
- LNG 耗量:tab_fueloil_flight_monthly_summary 的 oil_consume_type='5'

---

## 4. 实时与历史数据混合

| 看板 | WS 实时 | REST 历史 |
|---|---|---|
| FleetKanBan | 船位/船艏/状态(1s 轮询 window.warn) | 船队总数/饼图/折线(10min) |
| DataBoard | Conning 全量+目标船(1s) | 船信息(1h)/航次(1min)/ECharts(10min) |
| SingleExc | 船位/状态/围栏/油位/机舱报警(1s) | 船参数/图片/航段(按需) |

**核心机制**:WS 单连接全局复用(window.WS,5s 重连),订阅 `{a:'subscribe', t:'over_all'|'focus'}`,二进制帧交 Worker 解析回写 window.warn,各屏 1s 轮询驱动渲染(避免高频 setState)。

---

## 5. 数据表与 Redis

**表**:tab_ship_info、tab_ship_type、tab_flight_record、tab_ais_ship_segment(航距/航时/油耗核心)、tab_ais_distance_record、tab_oil_water_consume(大屏油耗真源,XxlJob 每日聚合)、tab_fuel_consumption、tab_load_management(新大屏装载)、tab_fueloil_flight_monthly_summary(LNG)、tab_alarm_statistics、tab_behavior_recognition、tab_channel_notice、tab_ship_fleet。

**Redis**:list:ship:{id}:online/ais/navigation/alarmTotal(实时)、thirdpartycall:weather:forecast/port:water:regime、trafficFlow:realTimeFlow、oil:sumStock:{shipId}。

---

## 6. 风险与改造建议

| 问题 | 诊断 ID | 建议 |
|---|---|---|
| 老大屏/新大屏单船接口未消费 | —(清理) | 确认是否废弃,废弃则删;或对接独立大屏工程 |
| SingleExc 大量死代码 | —(可维护性) | 清理未 render 的残留逻辑 |
| FleetKanBan/DataBoard 注释代码多 | —(可维护性) | 清理裁剪痕迹 |
| window.warn 全局池 | DIAG-P1-F-01 | 纳入状态管理 |
| WS 重连仅 5s 固定 | DIAG-P0-D-03 | 指数退避 |
| 满载率/违章图未渲染 | —(功能) | 确认是否启用 |
