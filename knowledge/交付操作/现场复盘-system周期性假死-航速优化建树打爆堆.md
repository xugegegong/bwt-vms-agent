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

删除该船在 `tab_ship_info_extend` 的转速规划参数（`Planning_rpm_min`/`Planning_rpm_max`/`RPM_planning_step`），`generateRpmPlanningList` 返回空，任务在**建树之前**秒退。

**判据（修复后日志）**：
```
11:31:00.003 航速优化功能定时任务运行：
11:31:00.008 规划转速列表为空，航速优化功能无法计算。
11:31:00.026 航速优化功能定时任务运行结束。      ← 全程 23 毫秒
```

**修复后验证（20 分钟）**：
- `jmap -histo` 里 `SpeedPlanningNaryTree$TreeNode` 归零；
- `jstat` FGC=0，老年代稳定在 60% 左右（启动加载的静态数据）；
- `rediscovery` 计数从每分钟 10+ 降到 0。

**风险/注意**：
- 清空转速参数后"航速优化建议"功能停用，需业务确认；
- 这是绕行——其他船只要配了参数+有 ETA 就会复发，必须向研发提单（缺陷单见 knowledge/待确认/20261127-*）；
- 清 ETA 是下策（船员下次填航次又会触发），转速参数才是稳定开关。

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
