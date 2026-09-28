# BWT-VMS 产品功能设计与实现 · 机舱健康评估域(D05)

> 更新日期:2026-09-04(修改本文档后请同步更新此日期,并到 Dify 知识库重新上传该文件)

> 遵循模板。本域**新旧两套并存**:health v1(海大推送+报警监控)与 mmha v2(健康分计算+通义千问 AI 诊断)。

---

## 1. 域概述

| 项 | 内容 |
|---|---|
| 定位 | 评估机舱设备健康度:接收外部算法推送(v1)+ 自主计算健康分与 AI 缺陷诊断(v2) |
| 核心价值 | 从被动报警升级到主动健康评估与 AI 辅助诊断 |
| 边界 | 只管"健康度";机舱工况展示在 D04;工况采集在 D02 |
| 新旧并存 | health v1(2019,db2 旧表)与 mmha v2(2024,db1 mmha_* + AI) |
| 关键能力 | 健康分计算引擎、通义千问缺陷诊断、AI 部件结构树、Nacos 动态阈值 |

---

## 2. 功能点清单

```
F05 机舱健康评估
├─ F05-1 health v1 海大推送与健康报警
├─ F05-2 health v1 缺陷知识库(db2)
├─ F05-3 mmha v2 健康分计算引擎(部件树+模型+汇总+运行条件)
├─ F05-4 mmha v2 AI 缺陷诊断(通义千问)
├─ F05-5 mmha v2 AI 部件结构树生成
├─ F05-6 mmha v2 Nacos 动态阈值配置
└─ F05-7 新旧并存与切换路径
```

---

## 3. 逐功能点详述

### F05-1 health v1 海大推送与健康报警

**(1) 功能设计**:接收海大(外部算法)推送的机舱健康值,三路并行处理(发 MQ/更新 Redis/落库),并对实时工况做报警监控。

**(2) inHealthAnalysis 流程**(`ShipController` POST `/ship/inHealthAnalysis`,入参 utc/mmsi/engine_id/health_value/fault_lists):
- mmsi→shipId(Feign SystemClient `/engine/shipInfoByMmsi` + 内存缓存)
- **三路并行**(各自线程池):
  - sendMq:发 Kafka `topicNameTemplate`(模板 `ship-{id}-...`)
  - update2redis:Redis hash `vms.engine-health.redis-key` hSet(engineId, "utc\tvalue\tfaultLists");统计 <60 数写 `list:ship:{shipId}:healthAlarmTotal`
  - writeLog:对象池(GenericObjectPool 复用 EngineHealthRecord/StringBuilder)insert `tab_engine_health_record`

**(3) EngineHealthAlarmListener**(@KafkaListener):
- 按船分线程池(`shipId % threadPoolNumber`)
- seekToEnd(只处理启动后新消息)
- healthValue < 阈值(默认 60)→ 写 Redis `engine:alarm:health:{shipId}`;恢复 → hDel
- 启动时 reloadEngineHealthParams 重载报警工况点配置

**(4) 接口**:POST `/feedback`(告警反馈→tab_alarm_feedback)、GET `/integrated/query/engine/health`(24h 趋势,Redis 优先 TTL 60s)、POST `/ship/deviceAbnormal/list`(缺陷库,见 F05-2)。

---

### F05-2 health v1 缺陷知识库(db2)

**(1) 数据源**:db2 旧表 `ship_device_defect`(双数据源 AOP `@DataSource` 切换)。

**(2) 4 级结构**:defect(故障 vchshipdevice_def)→ impact(影响)→ why(原因)→ repair(修复)。返回 `{uuid:{tip, impact[], why[{why_base, whyRepair[]}]}}`。

**(3) 方法签名**:`selectRecord(shipId, uuids)`(v1 带 shipId 隔离;healthDBMigrated=true 时必填 shipId)。

---

### F05-3 mmha v2 健康分计算引擎(核心)

**(1) 功能设计**:每分钟基于部件结构树递归计算各部件健康分,叶子按模型映射、非叶子按方式汇总。

**(2) 定时**:`@Scheduled cron="0 1/1 * * * ?"` 每分钟计算;`cron="0 1/10"` + @PostConstruct 每 10 分钟重载 mmha_component 三表缓存。

**(3) 部件结构树**:mmha_component 父子层级(parent_component_key),叶子(is_leaf='1')关联工况点(mmha_component_device_relation)。

**(4) 计算流程**:
1. 从 Redis `list:ship:{shipId}:other` 取实时值 Map<deviceKey,value>
2. 叶子节点:按计算模型算分
3. 运行条件公式继承:父有 operatingConditionEval 子无则继承
4. 非叶子节点:按汇总方式聚合子节点

**(5) 叶子计算模型**(HealthModelTypeEnum):
- **LINE_MODEL(01 线性)**:超出 [min,max] → boundaryScore;区间内线性插值
- **NON_LINE_MODEL(02 非线性)**:抛物线 `y=ax²+bx+c`(以正常点/边界点为顶点构造)
- 参数:normalValue→normalScore(默认100)、normalValueMax/Min→boundaryScore(默认60)

**(6) 非叶子汇总**(ComponentHealthSummaryTypeEnum):
- MAX(01)/MIN(02)/AVERAGE(03):基础统计
- WEIGHT(04):加权平均 `Σ(value×weight)/Σweight`
- **RADAR(05 雷达图面积法)**:N 个子分值映射正 N 边形顶点,算实际面积/满分面积×100(要求子节点≥3)

**(7) 运行条件公式 operatingConditionEval**:`{deviceKey1}>0 && {deviceKey2}<100`,正则替换 deviceKey 为实时值,**ScriptEngine JavaScript eval**;结果 false 跳过该部件(用于"停机时不扣分")。

**(8) 输出**:
- Redis `list:ship:{shipId}:health`(hash:component_key→utc\tscore\t缺陷uuid)
- Redis `health_cache:{shipId}:data:{startUtc}`(5 分钟快照,TTL 3600)
- 分值 ≤ alarmScore(默认70)→ 关联缺陷 UUID

---

### F05-4 mmha v2 AI 缺陷诊断(通义千问)

**(1) 功能设计**:调阿里通义千问(QwenParam QWEN_PLUS)自动生成缺陷/原因/影响/修复 4 级建议。

**(2) callWithPrompt**(`ShipDeviceDefectServiceImpl`):SDK `com.alibaba.dashscope`,topP=0.8、enableSearch=true、resultFormat=MESSAGE;apiKey `health.tongYiApiKey`(⚠️ 默认硬编码 fallback `sk-97a5d55b...`)。

**(3) 异步批量**(`AsyncServiceImpl.generateSuggestionAsync` @Async):设备按 5 个一组拆分 → 逐组调通义 → JSON 清洗(去 ```json 标记)→ 批量插 mmha_advice_*。

**(4) Prompt 模板**:给定工况点 JSON,让通义填 defectEn/defectImpact/defectCause/defectRepair,返回同结构 JSON。

**(5) 4 级表关系**:
```
mmha_advice_defect(故障,uuid 主键,关联 device_key)
  ├─ mmha_advice_impact(影响,defect_uuid 外键)
  └─ mmha_advice_cause(原因,defect_uuid 外键)
        └─ mmha_advice_repair(修复,cause_uuid 外键)
```

**(6) judgmentUseOfTy**:ReentrantLock 检查通义是否占用(异步任务 apiLock/apiUnLock);前端 `/mmha/judgmentUseOfTy` 轮询判断可否操作。

**(7) 已知问题**:DIAG-P0-S-05(apiKey 硬编码)、DIAG-P2-M-05(AI 输出无校验)。

---

### F05-5 mmha v2 AI 部件结构树生成

**(1) 功能设计**:AI 从工况点列表自动归类设备部件层级,免人工配结构树。

**(2) 全船生成**(`GET /mmha/structureTree/generate`):从 tab_engine_param 取模拟量工况点 + 一级菜单分类 → Prompt(船舶有监测点 X,现有分类 JSON,筛选关联,最多两级,返回 JSON)→ 异步调通义 → 解析递归生成 mmha_component + relation + model_info(默认 LINE_MODEL)。

**(3) 单部件重生成**(`GET /mmha/structureTree/generateComponentDevice`):拼接父部件全路径名(如"A 中 B 中 C")→ Prompt 筛选关联监测点 → 删旧 relation/model/advice → 重生成。

**(4) 人工调整**:`GET /structureTree/query`、`POST /structureTree/save`(拖拽排序)、`POST /component/update`(同步 system 一级菜单)。

---

### F05-6 mmha v2 Nacos 动态阈值配置

**(1) HealthValueLimitConfig**:`@ConfigurationProperties("health")` + `@RefreshScope`,字段 `Map<shipId, Map<scoreKey,Double>>` shipScoreParam(normalScore/boundaryScore/alarmScore),Nacos 变更自动刷新。

**(2) UpdateNacosServiceImpl**(`POST /mmha/configuration/healthScore`):读 Nacos 现配置 → SnakeYAML 解析改 shipScoreParam → publishConfig → 触发 @RefreshScope。

---

### F05-7 新旧并存与切换路径

| 维度 | health v1 | mmha v2 |
|---|---|---|
| 缺陷数据源 | db2 ship_device_defect(人工) | db1 mmha_advice_*(AI) |
| 健康分 | 海大推送(外部算) | 本系统每分钟算(部件树+模型) |
| selectRecord 签名 | `selectRecord(shipId, uuids)` | `selectRecord(uuids)`(⚠️ 不一致,替换易踩坑) |
| 24h 趋势 | `/integrated/query/engine/health` | `/mmha/engine/health`(同源 tab_engine_health_record) |

**切换建议**:前端逐步把 `/ship/deviceAbnormal/list` 切 `/mmha/deviceAbnormal/list`;海大推送若停用需确保 mmha 实时数据源(Redis list:ship:id:other)有独立写入;迁移 db2 缺陷数据后下线 v1。

---

## 4. 数据表

| 表 | 数据源 | 用途 | 关键字段 |
|---|---|---|---|
| tab_engine_health_record | db1 | 健康记录(海大推送) | ship_id/utc_time/engine_id/value/fault_list |
| tab_alarm_feedback | db1 | 告警反馈 | ship_id/alarm/feedback |
| ship_device_defect | **db2** | 旧缺陷库 4 级 | vchuuid/vchshipdevice_def_chn/ehn + _imp_/_why_/_rep_ |
| mmha_component | db1 | 部件结构树 | component_key/parent_component_key/model_summary_type_code(01-05)/operating_condition_eval/is_leaf |
| mmha_component_device_relation | db1 | 部件↔工况点(叶子) | component_key/device_key/normal_value/max/min |
| mmha_component_model_info | db1 | 计算模型 | device_key/model_type_code(01线/02非线) |
| mmha_advice_defect | db1 | 故障(AI) | uuid/device_key/defect_info(En) |
| mmha_advice_cause | db1 | 原因(AI) | defect_uuid/cause_info |
| mmha_advice_impact | db1 | 影响(AI) | defect_uuid/impact_info |
| mmha_advice_repair | db1 | 修复(AI) | cause_uuid/repair_info |

---

## 5. 数据流

```mermaid
flowchart TB
    subgraph v1[health v1]
        HD[海大推送] -->|POST /ship/inHealthAnalysis| SC[ShipController 三路并行]
        SC --> MQ[Kafka] & RH[(Redis health值+alarmTotal)] & HR[(tab_engine_health_record)]
        RT[工况实时值] --> EAL[EngineAlarmListener <60] --> RA[(Redis engine:alarm:health)]
    end
    subgraph v2[mmha v2]
        RO[(Redis list:ship:id:other)] -->|每分钟| CALC[HealthValueCalculateService\n部件树递归]
        CALC --> LEAF[叶子 LINE/NON_LINE 模型+eval公式]
        CALC --> NONLEAF[非叶子 MAX/MIN/AVG/WEIGHT/RADAR]
        LEAF & NONLEAF --> RHC[(Redis list:ship:id:health)]
        RHC -.分值≤alarm.--> DEF[关联缺陷 UUID]
        FAULT[故障] -->|Prompt| TY[通义千问] --> ADVICE[(mmha_advice_* 4级)]
    end
```

---

## 6. 风险与改造建议

| 问题 | 诊断 ID | 建议 |
|---|---|---|
| 通义 apiKey 硬编码 | DIAG-P0-S-05 | 移 Nacos 加密 + 轮换 |
| 新旧并存数据不互通 | DIAG-P1-A-03 | 确定 v2 主线,迁移 db2,下线 v1 |
| selectRecord 签名不一致 | —(切换坑) | 统一接口契约 |
| JS eval 运行条件公式 | —(安全/性能) | 评估改表达式引擎(aviator/spel) |
| AI 输出无校验 | DIAG-P2-M-05 | 加结果校验 + 人工审核入口 |
| 健康分计算无测试 | DIAG-P2-M-05 | 对模型/汇总算法补单测 |
