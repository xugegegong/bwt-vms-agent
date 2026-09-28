# BWT-VMS 产品功能设计与实现 · 第三方数据采集域(D15)

> 更新日期:2026-09-04(修改本文档后请同步更新此日期,并到 Dify 知识库重新上传该文件)

> 遵循模板。定时拉取外部第三方数据入库/缓存。技术细节见后端架构库第 7.2 节。

---

## 1. 域概述

| 项 | 内容 |
|---|---|
| 定位 | 纯后台 Job 服务,定时拉取外部第三方数据(航道/AIS/气象/水情/交通流量)入库与缓存 |
| 核心价值 | 补充船端采集覆盖不到的外部环境与 AIS 数据 |
| 边界 | 只管"拉取与入库";数据消费在 D03(航行)/D09(大屏) |
| 部署 | 仅岸端(deployment.env: shoreSide) |
| ⚠️ 发现 | AIS 三套代码并存(SOAP 注释/Boloomo 弃用/长航 OpenAPI 主用);大量凭证硬编码 |

---

## 2. 功能点清单

```
F15 第三方数据采集
├─ F15-1 长航公共服务平台(电子航道图:航道/航标/水位)
├─ F15-2 长航 AIS OpenAPI(当前主用)
├─ F15-3 气象预报
├─ F15-4 港口水情
└─ F15-5 交通流量
```

---

## 3. 逐功能点详述

### F15-1 长航公共服务平台(电子航道图)

**(1) 接入**:HTTP POST form,地址 `http://www.cjienc.com:1001`,token 硬编码。

**(2) 入口**:`ChangHangFairwayBuoyJob`,各自 cron(Nacos)。

**(3) 数据**:tab_channel_notice(航道通告)、maintenance_scale(维护尺度)、navigation_information(航行信息)、navigation_legend(航行图例)、water_level_information_release(水位信息)。

### F15-2 长航 AIS OpenAPI(当前主用)

**(1) 接入**:HTTPS POST JSON,地址 `${cmft.open-api.url}`(Nacos),apiKey。

**(2) 定时任务**(ChangHangServiceJob):
- `shipPositionInterface` cron ~1min:`/sp/ships/getShipIdByMMSI` + `/sp/ships/position/latest`(100 条并行)→ 封装 AisMessage 发 Kafka `ship-{id}-data-org-realtime` + 写 Redis `list:ship:{id}:ais/navigation/online/utc` + 批量插 tab_res_route(resource=changhang)
- `shipAisStatusInfoInterface` cron `0 0/30 * * * ?`:取 AIS 静态信息+位置 → 批量插 tab_ais_ship_status_record

**(3) 下游**:realTimeData 模块消费 Kafka(见 D03 AIS 链路)。

**(4) ⚠️ 三套 AIS 代码并存**:
- SOAP WebService(`org/tempuri/WebService.java` 整类注释,地址 27.17.33.252:14071)— 废弃
- 水科院 Boloomo(`singleShipMethon/multiShipMethon`,HTTP GET Base64,shippingcgi.boloomo.com)— 弃用
- 长航 OpenAPI(`shipPositionInterface` 等)— **当前主用**

### F15-3 气象预报

**(1) 接入**:HTTP GET 分页,地址 `http://8.140.127.229:8084`(硬编码)。入口 `MeteorologicalWaterLevelJob.meteorologicalCache`,cron 3h。

**(2) 数据**:tab_weather_forecast + Redis `thirdpartycall:weather:forecast`(只留最新)。

### F15-4 港口水情

**(1) 接入**:HTTP GET 分页,地址 `http://8.140.127.229:8083`(硬编码)。入口 `MeteorologicalWaterLevelJob.portWaterRegimeCache`,cron 3h。

**(2) 数据**:tab_port_water_regime + Redis `thirdpartycall:port:water:regime`。

### F15-5 交通流量

**(1) 接入**:HTTP POST JSON,地址 `http://112.6.236.187:30001`(硬编码)。入口 `TrafficFlowServiceJob.trafficFlowJob`,cron 30min。

**(2) 数据**:tab_traffic_flow + Redis `trafficFlow:realTimeFlow`。

---

## 4. 数据流

```
第三方API(HTTP/HTTPS) → @Scheduled Job → 写 MySQL(11 表) + Redis(最新) + Kafka(实时船位)
  → D03 航行监控读 tab_res_route/tab_ais_ship_status_record + Redis 实时船位
  → D09 大屏读 tab_channel_notice(水域通告)/气象/水情/交通流量
```

---

## 5. 数据表与 Redis

**表**:tab_channel_notice、maintenance_scale、navigation_information、navigation_legend、water_level_information_release(长航航道)、tab_res_route、tab_ais_ship_status_record(AIS)、tab_weather_forecast、tab_port_water_regime、tab_traffic_flow。

**Redis**:list:ship:{id}:ais/navigation/online/utc(实时船位)、thirdpartycall:weather:forecast、thirdpartycall:port:water:regime、trafficFlow:realTimeFlow、Kafka ship-{id}-data-org-realtime(供 realTimeData)。

---

## 6. 风险与改造建议

| 问题 | 诊断 ID | 建议 |
|---|---|---|
| 第三方凭证硬编码 | DIAG-P0-S-05 | 移 Nacos 加密 + 轮换 |
| AIS 三套代码并存 | DIAG-P1-A-04 | 删 SOAP/Boloomo 死代码 |
| 异常处理弱(log.info 吞) | —(可靠性) | 加重试 + 告警 + 调用量监控 |
| 地址硬编码 | —(配置) | 第三方地址全移 Nacos |
| 仅岸端,无降级 | —(可靠性) | 第三方不可用时加缓存兜底 |
| 多数据源 dynamicdb 实际只用 db1 | DIAG-P2-A-06 | 清理 db2 配置 |
