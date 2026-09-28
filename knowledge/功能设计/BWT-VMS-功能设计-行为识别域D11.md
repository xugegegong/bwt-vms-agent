# BWT-VMS 产品功能设计与实现 · 行为识别域(D11)

> 更新日期:2026-09-04(修改本文档后请同步更新此日期,并到 Dify 知识库重新上传该文件)

> 遵循模板。基于 CCTV 视频的 AI 违规识别与告警。

---

## 1. 域概述

| 项 | 内容 |
|---|---|
| 定位 | 消费外部 AI 视频分析服务的违规识别结果,入库、告警、查询、推送 |
| 核心价值 | 自动发现值班违规(人数少/未动/玩手机/未穿戴/跌倒),辅助安全 |
| 边界 | 只管"识别结果处理";视频采集在 D10;声光报警综合判定在本域涉及 |
| 数据来源 | 外部 AI 服务发 Kafka `behavior_detection`(本工程只消费) |
| ⚠️ 严重发现 | **WebSocket 实时推送链路完全断裂**(webSocketSend 死代码 + @ServerEndpoint 注释 + 前端定时器注释),当前仅靠手动切 tab 触发 HTTP 拉取 |

---

## 2. 功能点清单

```
F11 行为识别
├─ F11-1 数据来源(Kafka behavior_detection)
├─ F11-2 处理流程(Listener batchInsert + Redis 声光报警)
├─ F11-3 查询与管理接口(BehaviorRecognitionController)
├─ F11-4 信息发布(InformationDeliveryController)
└─ F11-5 前端展示(BehaviorHistory + BehaviorTable)
```

---

## 3. 逐功能点详述

### F11-1 数据来源

**(1) Topic**:`behavior_detection`(topicPattern)。生产方:外部 AI 视频分析服务(船端/岸端 AI 盒子)。

**(2) 报文结构**(推断自实体):shipName/recordTime(**秒级时间戳 String**)/recordPlace/detectedPersonNum/**illegalBehaviors(逗号分隔 code 串如"1,3,5")**/image(**Base64**)/cameraIp/status(A 报警/N 解除)。

**(3) 违规 code 字典**(BEHAVIOR_CODE_TEXT):1 值班人数过少 / 2 长时间未动 / 3 频繁玩手机 / 5 未穿戴工作服帽 / 7 人员跌倒。code=10 火焰检测**已注释未实现**。

---

### F11-2 处理流程(BehaviorRecognitionListener,dataFlow 模块)

**(1) 流程**:Kafka 消息 → JSON 解析 → illegalBehaviors 按 "," split(空则 fallback illegalBehaviorType)→ **每个 code 复制成一条记录** → batchInsert tab_behavior_recognition → dealWithSLAlarm(Redis 声光报警)。

**(2) Redis 声光报警**(sl-alarm.switch=true 时):
- `SET sl:alarm:status:{shipId}=1`
- `SET sl:alarm:behavior:{shipId}=1`
- 由 SoundOptoralAlarmServiceImpl 综合判定(行为是 4 输入之一:behavior/device/health/sailing)

**(3) ⚠️ WebSocket 推送失效**:webSocketSend 方法存在但**无调用点**;BehaviorRecognitionWebSocketHandler.sendMessage 无调用方;旧 @ServerEndpoint 注释禁用;前端定时器注释。**实时推送链路完全断裂**。

---

### F11-3 查询与管理接口(BehaviorRecognitionController `/behaviorRecognition/*`)

| method | path | 用途 |
|---|---|---|
| GET | `/list` | 历史列表(船名+时间区间分页,不含 image) |
| GET | `/getImage` | 按需加载单条图片(Base64) |
| GET | `/addBehaviorRecognitionExtend` | 标记已读 |
| GET | `/pushMessage` | Header 未读提示 |
| GET | `/realDataList` | 实时违规(近 1 月,一级+未操作,无图) |
| GET | `/realDataList/paging` | 实时分页(带图) |
| GET | `/allDataList` | 全量历史 + 按日聚合图表 |
| POST | `/updateOperationState` | 批量更新操作状态(0 未操作/1 确认/2 误报) |

**(1) 操作状态**:0 未操作 / 1 报警确认 / 2 非报警(误报)。

**(2) 实时列表固定取近 1 月**(MONTH_FORWARD=-1),只查 violation_level='1' 且 operation_state='0'。

**(3) 字典关联**:`LEFT JOIN tab_behavior_recognition_violation_dict ON FIND_IN_SET(code, illegal_behaviors)`。

---

### F11-4 信息发布(InformationDeliveryController `/informationDelivery/*`)

**(1) 功能**:向指定船或全体发布信息(与行为识别同构:入库 + 前端轮询)。

**(2) 接口**:POST `/add`(title/content/sendAll/mmsi 逗号串)、GET `/list`、`/detail`、`/addInformationDeliveryExtend`(已读)、`/pushMessage`(未读提示)、`/checkMmsi`(校验)。

**(3) ⚠️ 无主动推送**:`add()` 仅落库,无 Kafka producer、无 WS 推送;前端靠轮询 pushMessage。

---

### F11-5 前端展示

**(1) BehaviorHistory 页**(`analyticalStatement/BehaviorHistory/`,路由 `/behavior`):顶部筛选(船名+时间默认近 1 月)+ 模式切换(图表/表格);图表 ECharts 折线(每日违章次数 from allDataList.chartData);表格 BehaviorTable。

**(2) BehaviorTable 组件**:列(船名/地点/行为字典翻译/级别/时间/操作[确认|误报]/图片缩略图);多选批量确认;分页。

**(3) 图片加载**:列表不含 image,前端**逐条调 /getImage**(⚠️ N+1 风险)。

**(4) 统计 Job**:BehaviorRecognitionAlarmStatistics `@Scheduled 每分钟` 遍历船写 Redis `list:ship:{id}:behaviorRecognitionAlarmTotal`(Header 红点计数)。

---

## 4. 数据表

| 表 | 用途 | 关键字段 |
|---|---|---|
| tab_behavior_recognition | 违规主表 | ship_name/record_time(秒 String)/illegal_behaviors/image(**Base64**)/camera_ip/operation_state/user_id |
| tab_behavior_recognition_extend | 已读标记 | behavior_recognition_id/user_id |
| tab_behavior_recognition_violation_dict | 违规字典 | code/violation_level/violation_desc |
| tab_information_delivery | 信息发布 | information_title/content/send_all/mmsi |
| tab_information_delivery_extend | 信息已读 | information_delivery_id/user_id |

⚠️ **image 列存 Base64 字符串**(非文件);两模块(dataFlow 写/system 读)共用表但 resultMap 略异;无建表 DDL(从 mapper 反推)。

---

## 5. 风险与改造建议

| 问题 | 诊断 ID | 建议 |
|---|---|---|
| WS 实时推送完全断裂 | DIAG-P0-D-03 类 | 修复 webSocketSend 调用链或改 Server-Sent Events |
| 图片 Base64 存库 + N+1 查询 | —(性能) | 迁对象存储,列表返回缩略图 URL |
| 信息发布无主动推送 | —(功能) | 加 Kafka/WS 下发 |
| 火焰检测等已注释 | —(功能) | 确认是否启用 |
| 时间戳口径不统一 | —(规范) | 统一秒级 Long |
| 无建表 DDL | —(规范) | 补 Flyway 迁移脚本 |
