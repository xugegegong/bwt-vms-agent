# BWT-VMS 产品功能设计与实现 · 船岸数据同步域(D14)

> 更新日期:2026-09-04(修改本文档后请同步更新此日期,并到 Dify 知识库重新上传该文件)

> 遵循模板。船端↔岸端数据同步,两套互补方案。技术细节见后端架构库第 7-8 节。

---

## 1. 域概述

| 项 | 内容 |
|---|---|
| 定位 | 把船端业务库数据同步到岸端(及反向),供岸端展示与分析 |
| 核心价值 | 解决船岸网络隔离下的数据一致性 |
| 边界 | 只管"数据库级同步";实时工况走 Kafka(PNG);业务库变更走本域 |
| 两套并存 | shipShoreSynchronizer(CDC 准实时,船→岸单向)+ shipToShoreCommunication(SQL 回放,双向分钟级) |
| 不接入 Nacos | 两模块均本地配置(部署在船端,船端通常无 Nacos) |

---

## 2. 功能点清单

```
F14 船岸数据同步
├─ F14-1 CDC 同步(shipShoreSynchronizer,binlog/WAL)
├─ F14-2 SQL 回放同步(shipToShoreCommunication,时间水位线)
├─ F14-3 双数据库方言(MySQL/PostgreSQL)
├─ F14-4 船/岸角色切换(deployment.env)
└─ F14-5 同步表配置控制流
```

---

## 3. 逐功能点详述

### F14-1 CDC 同步(shipShoreSynchronizer,准实时)

**(1) 机制**:**自研 CDC 框架**,监听船端数据库日志(MySQL binlog / PG WAL),捕获行变更 → Kafka → 岸端回放 SQL。船→岸单向(配置控制流岸→船)。

**(2) 数据流**:
```
船端业务库(PG WAL/MySQL binlog)
  → MysqlExtractor/PostgreSQLExtractor(实时监听位点)
  → MySQLLogListener/PostgreSQLLogListener(组装 RowChangedEvent)
  → 船端 Kafka topic "ship-sql"
  → ShipService + Ship2ShoreService(@Retryable 无限重试,at-least-once)
  → 岸端 Kafka topic "shore-sql"
  → ShoreService → 拼 SQL(主键加 ss___ 前缀 + 补 ship_id)→ jdbcTemplate.execute()
  → 岸端业务库
```

**(3) CDC 框架**(`database/` 包 ~130 文件,署名 AE86):Connector 层(Factory + Mysql/PostgreSQL/Kafka)、Extractor 层(shyiko mysql-binlog-connector 0.21.0 / PG pgoutput 逻辑复制)、SqlBuilder(按方言)、ValueMapper(20+ 类型映射)。

**(4) 位点管理**:MySQL 存 sync_binlog_info 表(30s 刷新);PG 靠 replication slot 的 restart_lsn。**PG WAL 过期自愈**:捕获 "requested WAL segment has already been removed" → drop slot → 自动重新全量(DbFullExtrator.fullSyncByDbName)。

**(5) 配置**:端口 18104,active=pg,无 Nacos。两套 Kafka:船端 kafka.hbluewhale.com:9092、岸端 shore.hbluewhale.com:9092(均手动提交)。

---

### F14-2 SQL 回放同步(shipToShoreCommunication,分钟级)

**(1) 机制**:**基于时间水位线的双向 SQL 回放**。不传业务 DTO,传拼好的 insert/update SQL 文本,对端 jdbcTemplate.execute。同一份代码船/岸各自部署,`deployment.env` 切角色,`spring.profiles.active` 切方言。

**(2) 通信协议(双向对称,纯 Kafka 驱动,0 个 @RestController)**:
```
@Scheduled(cron="0 0/1 * * * ?") 每分钟
  → 读 tab_sync_table_info(同步清单) + tab_ship_info(活跃船)
  → KafkaTemplate.send("ship-{id}-send-data", infoType=1 请求)

@KafkaListener(topicPattern="ship-[0-9]+-accept-data")
  infoType=1(请求) → 查本地增量行 → 拼 insert/update SQL → 分包(65536 字节) → send(infoType=2)
  infoType=2(数据) → 过滤防重 → jdbcTemplate.execute(sql) → 更新 tab_sync_table_info 水位线
```
Topic 命名(船视角):`ship-{id}-send-data`(下行)/`ship-{id}-accept-data`(上行)。

**(3) ⚠️ 安全**:直接 execute 对端传来的 SQL 文本,Kafka 鉴权与 tab_sync_table_info 写权限是关键(DIAG-P0-S-04)。

---

### F14-3 双数据库方言

**(1)**:mysql/pg 与角色正交(可组合)。`connector_type: PostgreSQL|Mysql` 选 Extractor。

**(2) 配置**:
- application-pg.yaml:`jdbc:postgresql://db.hbluewhale.com:12921/`,dbudev
- application-mysql.yaml:`jdbc:mysql://db.hbluewhale.com:13306/`,root

**(3) 代码层最小适配**:`utc_time` 字段 mysql 加反引号、pg 不加。shipToShoreCommunication 用 DriverManagerDataSource(**无连接池**)。

---

### F14-4 船/岸角色切换

`deployment.env: shipSide|shoreSide`(默认 shoreSide)。shipToShoreCommunication 的 InfoMonitoringService 用工厂方法按 env 选 6 个 Service 实现(Ship/Shore 各 3:DataPackage/DataStorage/DataSyncRequest)。shipShoreSynchronizer 同代码切 Extractor/Replayer 角色。

---

### F14-5 同步表配置控制流(岸→船)

shipShoreSynchronizer:`SyncTableInfo2KafkaTask` 每 15min 把 `tab_sync_table_info` 广播到 topic `shore-sync-table-infos`,船端 ShipListenShoreSyncTableInfo 消费后增删本地同步表配置。

---

## 4. 两套方案对比

| | shipShoreSynchronizer | shipToShoreCommunication |
|---|---|---|
| 机制 | 日志级 CDC(binlog/WAL) | 时间水位线轮询(每分钟) |
| 实时性 | 准实时(秒级) | 分钟级 |
| 方向 | 船→岸单向(配置控制流岸→船) | 双向对称 |
| 复杂度 | 高(自研 CDC 框架 130 文件) | 低(~20 文件) |
| 数据载体 | RowChangedEvent(结构化) | SqlExecuteInfo(SQL 文本) |
| 风险 | WAL 过期需自愈 | 直接 execute SQL(注入风险) |

---

## 5. 风险与改造建议

| 问题 | 诊断 ID | 建议 |
|---|---|---|
| 直接 execute SQL 文本 | DIAG-P0-S-04 | 改结构化变更事件 + 参数化 |
| 无连接池 + synchronized | DIAG-P1-P-02 | 换 HikariCP + 批量 |
| 自研 CDC 维护成本高 | —(评估) | 评估 Canal/Debezium |
| 同步无冲突解决 | —(数据一致性) | 双向同步需冲突策略(版本/时间戳) |
| WAL 过期靠自愈 | —(可靠性) | 加监控告警 + 调 slot 保留 |
