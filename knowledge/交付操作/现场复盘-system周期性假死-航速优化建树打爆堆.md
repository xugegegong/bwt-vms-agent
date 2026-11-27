# 现场复盘：system 服务周期性假死——一个算法缺陷伪装成六个故障

> 知识层级：**实战验证**（2026-11-26~27 客户现场完整复现并解决，机器实测，未经 C 审核）
> 适用：iCommander 船端整栈；凡是"system 正常一阵就出问题"的现象都先看本文
> 价值：本文记录了从"以为是 nacos 问题 / 以为是内存泄漏 / 以为是 Kafka 问题"一路误判、直到抓到真凶的完整过程，含每一步的判读依据和踩坑。

## 零、一句话结论

**system 的航速优化定时任务每 10 分钟用全枚举算法构建百万级节点树，把 2G 堆打爆 → Full GC 死循环 → 所有线程停摆**。由此伪装出 Kafka 重平衡风暴、nacos 注册消失、登录卡死、CPU 100% 四种"不同故障"。数据库慢查询（缺索引）是并存的另一个独立问题。

## 一、症状全家桶（同一根因的六个分身）

| 表象 | 容易误判为 |
|---|---|
| Web 登录失败、8085 无响应 | system 服务挂了 |
| nacos 服务列表里 system 实例消失/健康抖动 | nacos 假死（曾因此做了 nacos 看门狗） |
| Kafka 日志每分钟 10~12 条 `Group coordinator ... unavailable`、`rebalanced ... max.poll.interval.ms` | Kafka/broker 故障 |
| system 容器 CPU 100%、load 高 | 资源不足 |
| jstat 老年代 100%、FGC 上万次 | 内存泄漏（曾误判为泄漏） |
| 重启 system 好 1~2 小时又坏 | "过一段时间就出问题"的玄学 |

## 二、正确诊断路径（按顺序，每步给判据）

### 1. 区分"进程死"还是"进程活但卡死"

```bash
docker ps --format '{{.Names}}\t{{.Status}}' | grep system    # Up = 活着
docker exec system sh -c 'jstat -gcutil $(pgrep java) 2000 3' # 关键
```

判据：**O（老年代）接近 100% 且 FGC 计数持续增长 = GC 死循环**。此时连 `jcmd/jmap` 都会 attach 失败（`AttachNotSupportedException: target process not responding`），这是"JVM 已经焊死"的铁证，不要再等它自愈。

### 2. 抓堆直方图要趁早（重要踩坑）

- **别等堆到 90% 再抓**——那时 attach 已经失败。**在 O≥40% 时抓**。
- **别用 `jcmd GC.class_histogram`**——它本身会触发一次 Full GC，在病态 JVM 上等于雪上加霜。
- **用 `jmap -histo`（不带 :live）**——只遍历堆统计，不触发 GC：

```bash
docker exec system sh -c 'J=$(pgrep java); JP=$(readlink -f /proc/$J/exe); $(dirname $JP)/jmap -histo $J | head -40'
```

判据：排第一的业务类就是元凶。本例中 `SpeedPlanningNaryTree$TreeNode` 4,753,115 个实例，且 `Long/Double/ArrayList` 以 1:1:1 同步增长。

### 3. 确认增长趋势（区分"泄漏"与"周期性打爆"）

隔 10 分钟抓两次直方图，做增量对比。注意：**jstat 的 O 列如果每 10 分钟（整十分钟的第 1 分钟）出现一次尖峰然后回落**，不是泄漏，是定时任务周期性分配风暴。

### 4. 对照代码找触发点

```
SpeedOptimizeSeriveImpl.speedOptimialTask()      // @Scheduled(cron = "0 1/10 * * * ?")
 → speedOptimial(shipId)
   → generateRpmPlanningList()                    // 读 tab_ship_info_extend 的转速参数
   → 检查工作航线 / 最新航次 ETA
   → optimal() → SpeedPlanningNaryTree.buildTree() // 全枚举建树
```

节点数 = 转速档位数 ^ 航段数。触发三条件：配置了转速参数 + 最新航次 ETA 在未来 + 航段数较多。

## 三、修复

### 3.1 运维层规避（已上船验证，不改代码）

**原理**：`speedOptimial()` 在**建树之前**先调 `generateRpmPlanningList(shipId)`，该函数从 `tab_ship_info_extend` 读取三个转速规划参数；任一缺失 → 返回空列表 → 日志打出"规划转速列表为空"并直接 return。所以**删掉这三个参数 = 任务秒退、不建树**。

三个参数（`tab_ship_param_model.param_name_en`）：
| param_name_en | 含义 |
|---|---|
| `Planning_rpm_min` | 航速优化最小规划转速 |
| `Planning_rpm_max` | 航速优化最大规划转速 |
| `RPM_planning_step` | 航速优化转速规划粒度 |

#### 步骤 1：找到参数（先查、先看，确认再动）

```sql
-- 连库方式（船端）：docker exec mysql mysql -uroot -p<密码> <库名>
-- 1) 查该船这三个参数是否配置、配置了什么值
SELECT i.id, i.ship_id, m.param_name, m.param_name_en, i.param_value
FROM vms_sys_db.tab_ship_info_extend i
LEFT JOIN vms_sys_db.tab_ship_param_model m ON i.param_id = m.id
WHERE i.ship_id = <船号>
  AND (m.param_name_en IN ('Planning_rpm_min','Planning_rpm_max','RPM_planning_step')
       OR m.param_name IN ('航速优化最小规划转速','航速优化最大规划转速','航速优化转速规划粒度'));

-- 2) 顺带确认最新航次 ETA 是不是未来（第二个触发条件）
SELECT id, flight_number, estimated_end_time,
       FROM_UNIXTIME(estimated_end_time) AS eta时间
FROM vms_sys_db.tab_flight_record
WHERE ship_id = <船号>
ORDER BY flight_start_time DESC LIMIT 3;
```

判读：第 1 条**有返回行**且第 2 条最新航次 ETA 在未来 → 这颗雷是活的，需要处理。

#### 步骤 2：备份（必须先做，可回滚）

```sql
CREATE TABLE IF NOT EXISTS tab_ship_info_extend_bak_<YYYYMMDD> AS
SELECT * FROM vms_sys_db.tab_ship_info_extend WHERE ship_id = <船号>;
-- 校验备份行数
SELECT COUNT(*) FROM tab_ship_info_extend_bak_<YYYYMMDD>;
```

#### 步骤 3：删除三个参数（精确按 join 条件删，不会误删其他参数）

```sql
DELETE i FROM vms_sys_db.tab_ship_info_extend i
LEFT JOIN vms_sys_db.tab_ship_param_model m ON i.param_id = m.id
WHERE i.ship_id = <船号>
  AND (m.param_name_en IN ('Planning_rpm_min','Planning_rpm_max','RPM_planning_step')
       OR m.param_name IN ('航速优化最小规划转速','航速优化最大规划转速','航速优化转速规划粒度'));

-- 验证:应返回 0 行
SELECT i.id, m.param_name_en, i.param_value
FROM vms_sys_db.tab_ship_info_extend i
LEFT JOIN vms_sys_db.tab_ship_param_model m ON i.param_id = m.id
WHERE i.ship_id = <船号>
  AND m.param_name_en IN ('Planning_rpm_min','Planning_rpm_max','RPM_planning_step');
```

**不用重启服务**——任务每 10 分钟重读数据库，下一个整十分钟点自动生效。

#### 步骤 4：验证（等下一个 10 分钟任务点后，约 12 分钟）

```bash
# ① 任务日志:应在"规划转速列表为空"处秒退,全程毫秒级
docker logs --since 12m system 2>&1 | grep -E '航速优化功能定时任务|规划转速列表|规划航段数量' | tail -5
# 期望:出现"规划转速列表为空"和"定时任务运行结束";不应出现"规划航段数量"

# ② 堆:TreeNode 归零、FGC 不再增长
docker exec system sh -c 'J=$(pgrep java); JP=$(readlink -f /proc/$J/exe); $(dirname $JP)/jmap -histo $J | grep -E "SpeedPlanningNaryTree"'
docker exec system sh -c 'jstat -gcutil $(pgrep java) 5000 3'

# ③ 重平衡消停
docker logs --since 10m system 2>&1 | grep -c 'will attempt rediscovery'
```

#### 回滚（如需恢复航速优化功能）

```sql
INSERT INTO vms_sys_db.tab_ship_info_extend
SELECT * FROM tab_ship_info_extend_bak_<YYYYMMDD>;
```

### 3.1b 备选：清最新航次的 ETA（临时手段，不推荐作长期开关）

**原理**：`speedOptimial()` 在取到最新航次后检查 `estimated_end_time`，为空则打 warn 直接 return（同样在建树之前）。也能起到熔断效果，但**船员下次给新航次填 ETA 就会复发**，只适合"马上要停机/来不及改转速参数"的应急。

```sql
-- 1) 备份
CREATE TABLE IF NOT EXISTS tab_flight_record_bak_<YYYYMMDD> AS
SELECT * FROM vms_sys_db.tab_flight_record WHERE ship_id = <船号>;

-- 2) 清空最新航次的预计到达时间(注意:按 flight_start_time 取最新,与代码口径一致)
UPDATE vms_sys_db.tab_flight_record
SET estimated_end_time = NULL
WHERE ship_id = <船号>
ORDER BY flight_start_time DESC
LIMIT 1;

-- 3) 验证
SELECT id, flight_number, estimated_end_time
FROM vms_sys_db.tab_flight_record
WHERE ship_id = <船号>
ORDER BY flight_start_time DESC LIMIT 1;
```

**注意**：
- 会影响页面上"预计到达时间"的显示（字段变空），需业务知会；若不想显示为空，可改成过去的 UTC 时间戳（代码同样判定"该航次已完成"而跳过），但页面会显示历史时间；
- **这不是长期方案**——船员重填 ETA 即复发，稳定开关是 3.1 的转速参数；
- 两个开关可以叠加使用（转速参数删掉后，ETA 填不填都无所谓，熔断在前者已经生效）。

### 3.2 代码层修复（研发）

- 分支限界剪枝：`etd + costTime > eta` 的分支直接 return；
- 避免每个节点 `new ArrayList<>(parent...)` 全量拷贝，中间节点只存当前档位，叶子再物化序列。

## 四、并存的独立问题：MySQL 慢查询打满 CPU

排查过程中确认 MySQL 长期 99% CPU 是另一个真实问题，与内存无关：

1. **`tab_res_status` 缺索引**：realTimeData 的绿色航速逻辑（`ElectricFenceController.saveGreenSpeed`）**每 2 秒**执行一次"取该点最新状态"，8.4G 表无索引 → 每次全表扫+排序。
   修复：`ALTER TABLE tab_res_status ADD INDEX idx_ship_dev_start (ship_id, device_key, start_utc), ALGORITHM=INPLACE, LOCK=NONE;`
   验证：`EXPLAIN` 的 key 列 = idx_ship_dev_start、rows=1。
2. **单表数据量失控**：`tab_data_org` 约 300G（16 个月分区），`tab_res_clean` 约 100G（14 个月分区），无归档策略。清理 3 个月前分区（`TRUNCATE PARTITION`，秒级、保留分区定义）后磁盘 19%→8%。
3. **报表慢查询无时间范围**：`count(1) FROM tab_res_clean WHERE ship_id.. AND device_name.. AND avg_value!=0` 扫全部 100G 数据，单次 392 秒，曾把 TRUNCATE 的 MDL 锁堵死 20 分钟。需要研发：加时间范围/加索引/加速缓存。

## 五、踩坑记录（都是真金白银）

1. **`jcmd GC.class_histogram` 会触发 Full GC**，在病态 JVM 上必须用 `jmap -histo`。
2. **attach 失败 ≠ 工具缺失**：`AttachNotSupportedException` 是 JVM 卡死的信号，换成"更早阈值 + 不触发 GC 的工具"。
3. **DDL 被长查询堵住**：`TRUNCATE PARTITION` 显示 `Waiting for table metadata lock` 时，先 `SELECT ID,TIME,STATE,LEFT(INFO,60) FROM information_schema.PROCESSLIST` 找出 TIME 大且持锁的 SELECT，`KILL <id>` 后 DDL 立即继续；期间该表所有 INSERT 也会被堵，别误判为写入故障。
4. **查 DDL 是否还在跑，别让查询匹配到自己**：`WHERE INFO LIKE '%ALTER TABLE%'` 会命中查询自身，要用 `WHERE COMMAND='Query' AND INFO LIKE 'ALTER%'`；索引是否建成用 `SHOW INDEX` 确认。
5. **jstat 的 M 列高（90%+）不是问题**：Metaspace committed 自适应跟随使用量，看 MU 绝对值（≤ MaxMetaspaceSize）才有意义。
6. **服务器时区是 UTC**：日志里 02:xx 是北京时间 10:xx，别把时间戳误判为异常。
7. **容器内存限额与 JVM 堆必须成对改**：只改 start.sh 的 Xmx 不改 container memory（或反之）等于没改。改 `docker update --memory` 时必须同时给 `--memory-swap`。
8. **加索引/删数据都建议先备份行/做分区副本**，并选低峰执行。
9. **老年代高 ≠ 泄漏**:判据是一次强制 Full GC 前后对比——
   `jcmd <pid> GC.run` 后 `jstat -gcutil` 再看 O 列:
   掉回低位(如 90%→37%)= 垃圾堆积,JVM 正常锯齿,不用管;
   掉不下来(如 100%→100%)= 活对象撑满,真泄漏(航速优化那次就是这种)。
   另:活对象少的时候 Full GC 只需约 1 秒;要 5~30 秒的都是病态。

## 五b、次要问题：trim 模块每 10 分钟全表 reload

`TrimOptimizeServiceImpl.reloadTrimConfig()`（`@Scheduled(cron="0 0/10 * * * *")`）
每 10 分钟对 `tab_trim_relation` / `tab_hydrostatic_trimmed` 做一次 `selectAll()`
全表加载（覆盖式 put 到内存 Map，旧对象变垃圾）。实测每轮产生约 9 千个
TabTrimRelation 垃圾对象+对应 DB 全表查询。**不导致故障**（垃圾可回收，
Full GC 1 秒清完），但属于不必要的周期开销，建议研发改为按船/按变更 reload，
或加大间隔。此条已附在缺陷单"次要问题"节。

## 六、给交付的检查清单（新船/存量船排查）

```sql
-- ① 有风险的船:配了转速参数 且 最新航次ETA在未来
SELECT i.ship_id, i.param_value, m.param_name_en
FROM tab_ship_info_extend i JOIN tab_ship_param_model m ON i.param_id=m.id
WHERE m.param_name_en IN ('Planning_rpm_min','Planning_rpm_max','RPM_planning_step');

-- ② 表规模
SELECT table_name, ROUND(data_length/1048576) mb
FROM information_schema.tables
WHERE table_schema IN ('vms_sys_db') AND table_name IN ('tab_res_status','tab_res_clean','tab_data_org');

-- ③ 慢查询TOP
SELECT DIGEST_TEXT, COUNT_STAR, ROUND(SUM_TIMER_WAIT/1e12,1) total_s
FROM performance_schema.events_statements_summary_by_digest
ORDER BY SUM_TIMER_WAIT DESC LIMIT 10;
```
