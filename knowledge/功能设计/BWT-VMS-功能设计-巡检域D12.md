# BWT-VMS 产品功能设计与实现 · 巡检管理域(D12)

> 更新日期:2026-09-04(修改本文档后请同步更新此日期,并到 Dify 知识库重新上传该文件)

> 遵循模板。机舱巡视打卡,设备事件驱动的状态机。

---

## 1. 域概述

| 项 | 内容 |
|---|---|
| 定位 | 管理船端轮机巡视打卡:点位配置、巡视顺序、打卡日志、超时告警 |
| 核心价值 | 保障轮机员按时按点巡视,异常自动告警 |
| 边界 | 只管"巡视打卡流程";设备工况采集在 D02 |
| 架构特点 | **设备事件驱动状态机**(非人工页面触发):船端 IoT 设备经 Kafka 上报打卡事件 |
| ⚠️ 发现 | 前端页面**不在 icommander-web 仓库**(全仓 grep patrol 零命中),可能在船端 Web 或独立工程 |

---

## 2. 功能点清单

```
F12 巡检管理
├─ F12-1 巡视点位与顺序配置
├─ F12-2 设备事件驱动状态机(PatrolDeviceMessageListener)
├─ F12-3 倒计时与三级告警升级
├─ F12-4 查询接口(当前巡视/日志/告警分析)
└─ F12-5 导出
```

---

## 3. 逐功能点详述

### F12-1 巡视点位与顺序配置

**(1) 接口**:

| method | path | 用途 |
|---|---|---|
| GET | `/patrol/position/list` | 所有启用巡视点位(shipId) |
| GET | `/patrol/order/list` | 按模式取巡视顺序(shipId,modelValue) |
| POST | `/patrol/order/save` | 保存巡视顺序(差量增删改) |

**(2) 模式 modelValue**:1 航行 / 2 靠泊 / 3 锚泊 / 4 自定义 / 5 关闭。

**(3) 点位配置**:每个点位 start_device_id/end_device_id(打卡设备)、code、position、include_place。巡视顺序每条含 arrive_alert_mins(到达超时)/proc_alert_mins(处理超时)/sort_num。

---

### F12-2 设备事件驱动状态机(PatrolDeviceMessageListener)

**(1) 数据来源**:Kafka topic `ship-patrol-device-msg`,船端 IoT 设备上报。

**(2) 状态机流程**:
```
船端IoT设备 → Kafka(ship-patrol-device-msg) → Listener
  ├─ 模式开关消息(value=1/2/3/4 开启新巡视;5 关闭)
  │   ├─ 开启:建 PatrolJob + 装配顺序 → 写 Redis & DB(tab_patrol_config 镜像)
  │   │        启动"巡视确认倒计时"(type=2)
  │   └─ 关闭:结束 Job(normal=0/1);非正常关闭告警(type=6);
  │           24h≥6次→全点位告警;启动"未按时巡视倒计时"(type=1)
  └─ 点位设备消息
      ├─ 记录 PatrolLog
      ├─ 推进 pointIndex:监控室→点位1→...→末点位→回监控室关闭
      └─ 每跳转按 arrive/proc_alert_mins 启新倒计时;归零未打卡→告警
```

**(3) 当前巡视状态**:Redis key `shippatrol:current:info`(船端),镜像到 tab_patrol_config(type=shippatrol:current:info)供岸端查。ShipPatrolCurrentInfoUtil 按部署环境选源(船端 Redis / 岸端 DB)。

---

### F12-3 倒计时与三级告警升级

**(1) 倒计时器**:PatrolCountDownTask(Runnable,每分钟 sleep-1),到 0 发 Kafka topic `ship-patrol-alarm-msg` 向硬件发告警指令 + 落 PatrolAlarm;普通告警后自动启动三级告警倒计时。

**(2) 告警类型**(PatrolAlarmTypeEnum):1 未按时巡视 / 2 巡视确认超时 / 3 到达点位超时 / 4 点位检查超时 / 5 三级报警 / 6 非正常关闭。

**(3) 告警升级**:非正常关闭 24h 累计 ≥6 次 → 全点位硬件告警(仅轮机长可清除,`/patrol/alarm/cleanAllAlarm` 发 Kafka 通知硬件复位)。

**(4) Kafka 告警指令枚举**:0 无/1 普通/2 关闭普通/3 三级/4 关闭三级。

---

### F12-4 查询接口

| method | path | 用途 |
|---|---|---|
| GET | `/patrol/job/current` | 当前进行中巡视(含节点进度/告警) |
| GET | `/patrol/job/currentList` | 最近一次巡视日志列表 |
| GET | `/patrol/job/list` | 巡视流程分页 |
| GET | `/patrol/log/list` | 巡视日志分页 |
| GET | `/patrol/alarm/list` | 告警分页(按 type) |
| GET | `/patrol/alarm/analysis` | 告警类型统计 |
| GET | `/patrol/alarm/analysisChart` | 月度图表(12 月×6 类) |
| GET | `/patrol/alarm/analysisByYear` | 按年统计 |
| GET | `/patrol/alarm/cleanAllAlarm` | 清除全点位告警 |

---

### F12-5 导出

`POST /export/file/patrolAlarm`(moduleCode ZNXJGJ 智能巡检告警)、`/export/file/patrolLog`(ZNXJRZ 智能巡检日志)。

---

## 4. 数据表

| 表 | 用途 | 关键字段 |
|---|---|---|
| tab_patrol_position | 打卡点位 | ship_id/start_device_id/end_device_id/code/position/status |
| tab_patrol_order | 巡视顺序(按模式) | ship_id/position_code/arrive_alert_mins/proc_alert_mins/sort_num/model_value |
| tab_patrol_job | 一次完整巡视 | ship_id/model_value/normal(0 异常/1 正常)/start_time/end_time |
| tab_patrol_log | 打卡日志 | ship_id/job_uid/order_uid/device_id/code/value |
| tab_patrol_alarm | 告警记录 | ship_id/job_uid/type(1-6)/device_id/code |
| tab_patrol_config | 配置+当前状态镜像 | ship_id/type/type_desc/value;type 含 ship_model_device_id/ship_check_interval(默认120 分)/ship_level3_alert_time/shippatrol:current:info |

---

## 5. 风险与改造建议

| 问题 | 诊断 ID | 建议 |
|---|---|---|
| 前端不在本仓库 | —(归属) | 确认前端仓库,补文档引用 |
| 当前状态双写(Redis+DB 镜像) | —(一致性) | 明确主从,加一致性校验 |
| 倒计时用线程 sleep | —(可扩展) | 评估改延迟队列(如 Redis ZSet) |
| 告警升级逻辑硬编码(6 次/24h) | —(配置) | 阈值移配置 |
| 设备事件丢消息无补偿 | DIAG-P0-D-01 类 | 至少一次 + 死信 |
