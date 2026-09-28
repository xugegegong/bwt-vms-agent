# BWT-VMS 项目问题诊断清单(改造第一步)

> 更新日期:2026-09-04(修改本文档后请同步更新此日期,并到 Dify 知识库重新上传该文件)

> 本文件是改造优化方案的**第一步:现状诊断**。只列问题、证据、影响和建议方向,不给详细改造设计(待你确认后,在《改造优化方案》中按优先级细化方案、工作量、里程碑)。
>
> 严重度:**P0** 高危(安全/数据丢失/线上稳定性,建议优先);**P1** 重要(架构一致性/性能/体验);**P2** 改进(可维护性/技术债)。
>
> 维度:**S**安全 **D**数据可靠性 **A**架构一致性 **P**性能 **F**前端 **DB**数据库 **M**可维护性 **OPS**部署运维。

---

## 汇总

| 维度 | P0 | P1 | P2 | 小计 |
|---|---|---|---|---|
| 安全 S | 5 | 1 | 0 | 6 |
| 数据可靠性 D | 3 | 1 | 0 | 4 |
| 架构一致性 A | 0 | 4 | 2 | 6 |
| 性能 P | 0 | 2 | 0 | 2 |
| 前端 F | 0 | 2 | 2 | 4 |
| 数据库 DB | 0 | 1 | 1 | 2 |
| 可维护性 M | 0 | 0 | 4 | 4 |
| 部署运维 OPS | 0 | 0 | 2 | 2 |
| **合计** | **8** | **11** | **11** | **30** |

---

## 一、P0 高危(8 项)

### DIAG-P0-S-01 · JWT 密钥硬编码且 claims 含密码
- **现状**:`TokenService.SIGNKEY` 写死在源码;`createToken` 生成的 JWT claims 里包含 `password` 字段(base64 可逆)。
- **证据**:`bwt-vms-security/.../service/TokenService.java`
- **影响**:密钥随源码泄露即可伪造任意用户 token;JWT 内含密码,泄露用户凭证。
- **建议方向**:SIGNKEY 改为 Nacos 注入并定期轮换;claims 移除 password,只放 userId/loginName;接入 HTTPS 全链路。

### DIAG-P0-S-02 · 网关不校验 token,鉴权下沉且依赖人工注解
- **现状**:网关 `TokenFilter` 的 token 校验逻辑被注释,只做计时;鉴权靠各业务服务的 `@PreAuthorize` AOP 切面。任一 Controller 方法漏加注解即等于无鉴权。
- **证据**:`bwt-vms-gateway/.../filter/TokenFilter.java`(校验代码注释)、`bwt-vms-security/.../aspect/PreAuthorizeAspect.java`
- **影响**:攻击者可直接访问未加注解的接口;权限策略分散难审计。
- **建议方向**:网关层统一校验 token 有效性(白名单放登录/健康检查);业务层只做细粒度授权;补全接口鉴权扫描。

### DIAG-P0-S-03 · FastJSON 1.2.56 存在历史反序列化漏洞
- **现状**:全栈统一用 fastjson 1.2.56(2019 版),Redis 序列化/Result/Feign 均用。
- **证据**:`bwt-vms-parent/pom.xml` fastjson.version=1.2.56
- **影响**:已知多个反序列化 RCE 漏洞(CVE),内网也可能被横向利用。
- **建议方向**:升级到 fastjson2 或最新 1.2.x;评估 autotype 关闭;Redis 序列化换 Jackson。

### DIAG-P0-S-04 · 船岸同步直接执行对端 SQL 文本
- **现状**:`shipToShoreCommunication` 把拼好的 insert/update SQL 文本经 Kafka 传到对端,`jdbcTemplate.execute(sql)` 直接执行。
- **证据**:`bwt-vms-shipToShoreCommunication/.../InfoMonitoringService` 相关
- **影响**:一旦 Kafka 被伪造或船端被攻破,可对岸端库执行任意 SQL(删除/篡改)。
- **建议方向**:改为结构化变更事件(表名+主键+字段+值),参数化执行;Kafka 开启鉴权与加密;白名单表。

### DIAG-P0-S-05 · 第三方与 AI 凭证硬编码
- **现状**:thirdPartyCall 里长航 PLAT_KEY/token、水科院 Key、遗留 SOAP 账号、mmha 通义千问 apiKey(fallback `sk-****...`)均硬编码在 Java 中。
- **证据**:`bwt-vms-thirdPartyCall/.../job/*`、`bwt-vms-mmha/.../ShipDeviceDefectServiceImpl`
- **影响**:凭证随源码/反编译泄露,产生调用费用或被滥用。
- **建议方向**:全部迁到 Nacos 配置中心(加密);轮换已泄露 key;异常不吞、加调用量监控。

### DIAG-P0-D-01 · 工况数据"先提交 offset 再处理",异常即丢
- **现状**:`dataCleanV2` 的 `CollectedDataListener.acknowledge()` 在方法首行调用(至多一次),处理异常数据丢失且无补偿。
- **证据**:`bwt-vms-dataCleanV2/.../mq/CollectedDataListener.java`
- **影响**:机舱工况/油耗关键数据可能静默丢失,影响能效与计费。
- **建议方向**:改为处理成功后再提交 offset(至少一次)+ 死信队列;补 dataFlow 的补齐链路。

### DIAG-P0-D-02 · 补齐链路全禁用 + 内存状态无持久化
- **现状**:dataFlow 的 makeupMessageListener 全注释;窗口航速/上一航点等状态存内存 `ConcurrentHashMap`,重启丢失。
- **证据**:`bwt-vms-dataFlow/.../mq/ais/BaseListener.java`、各 Controller 内存状态
- **影响**:服务重启后断点丢失、数据无法回填,航程/状态计算断层。
- **建议方向**:关键状态落 Redis/DB;启用或重做补齐链路;消费位点持久化。

### DIAG-P0-D-03 · WebSocket 不自动重连
- **现状**:`Socket.js` 的 `reStart()` 重连逻辑被注释;`onmessage/onclose/onerror` 也被注释(真实处理在 Header)。网络抖动后 WS 不恢复,实时数据停。
- **证据**:`icommander-web-feature/src/lib/Socket/Socket.js`
- **影响**:船端网络波动后大屏/监控变空白,用户需手动刷新。
- **建议方向**:实现指数退避重连;onclose 触发重连;连接状态可视化提示。

---

## 二、P1 重要(11 项)

### DIAG-P1-A-01 · 实时数据双链路并存互相覆盖
- **现状**:`realTimeData`(2021,Kafka 消费已清洗 JSON)与 `dataCleanV2`(2024,自带 PNG 解析)都计算能效并写同一批 `energy:*` Redis key;两者同时部署会互相覆盖。
- **证据**:两模块的 `EnergyServiceImpl` / `RealTimeDataListener`
- **建议方向**:明确主线(建议 dataCleanV2),下线另一条;或拆分为采集层与计算层。

### DIAG-P1-A-02 · dataFlow 与 realTimeData 功能重叠
- **现状**:两者都有电子围栏、舱容换算、能效分布、Haversine 算法;dataFlow profile=loc(本地态),realTimeData profile=prod。
- **证据**:见前端/后端架构库「数据处理核心」章
- **建议方向**:厘清边界(AIS 分析 vs 实时展示),合并去重,避免改一处忘另一处。

### DIAG-P1-A-03 · 机舱健康新旧并存且数据不互通
- **现状**:`health`(v1)读 db2 旧表 `ship_device_defect`(人工维护);`mmha`(v2)读 db1 新表 `mmha_advice_*`(AI 生成)。两者 `selectRecord` 签名几乎一致但表不互通。
- **建议方向**:确定 v2 为主线,迁移 db2 缺陷数据,下线 v1。

### DIAG-P1-A-04 · AIS 接入三套代码并存
- **现状**:SOAP(整类注释)/ Boloomo(弃用)/ 长航 OpenAPI(主用)三套并存,清理时易误删主链路。
- **证据**:`bwt-vms-thirdPartyCall/.../job/ChangHangServiceJob.java` 等
- **建议方向**:删除 SOAP/Boloomo 死代码,只保留长航 OpenAPI。

### DIAG-P1-S-06 · Redis 工具类静默吞异常
- **现状**:`RedisService` 几乎所有方法 try-catch 返回 false/null/空集,排障时静默失败。
- **证据**:`bwt-vms-redis/.../service/RedisService.java`
- **建议方向**:区分业务异常与系统异常,系统异常记日志并抛出;加 Redis 可观测。

### DIAG-P1-P-01 · 能效报表无预聚合,内存计算成瓶颈
- **现状**:`EnergyV2ServiceImpl`(3868 行)直接查 `tab_res_clean`+`tab_flow_meter_record`,在 Service 层按 interval 内存聚合;`tab_speed_oil_statistics` 已基本弃用。
- **建议方向**:引入预聚合(小时/天/航次级汇总表)+ 定时物化;长 Service 拆分。

### DIAG-P1-P-02 · 船岸 SQL 同步无连接池
- **现状**:`shipToShoreCommunication` 用 `DriverManagerDataSource`(无池)+ synchronized,高并发或大数据量时是瓶颈。
- **建议方向**:换 HikariCP;评估批量回放。

### DIAG-P1-DB-01 · tab_res_clean 分区需手动管理
- **现状**:按 `start_utc` RANGE 分区(月级,2022-01~2030+),插超范围数据报错;联合主键 (id,start_utc) 跨分区不连续。
- **建议方向**:定时任务自动 ADD PARTITION;或改按需分区策略。

### DIAG-P1-F-01 · 实时数据走 window.warn 全局变量
- **现状**:WS 消息经 Worker 写入 `window.warn`,页面用 `setInterval` 拉取;数据流难追踪、不利于排查与测试。
- **建议方向**:纳入 MobX/状态管理或 React Context;统一数据通道。

### DIAG-P1-F-02 · 登录超时只清 storage 不跳转
- **现状**:30 分钟无操作 `localStorage.clear()`,但不主动跳 login,用户可能停在空白/报错页。
- **证据**:`src/App.js`
- **建议方向**:超时后主动路由到 /login 并提示。

### DIAG-P1-D-04 · 单设备登录与会话策略需复核
- **现状**:同账号登录踢旧会话(`clearPreRedisToken`);token 6h 滑动过期。
- **建议方向**:确认是否符合多端/多角色场景(船端+岸端同账号?)。

---

## 三、P2 改进(11 项)

### DIAG-P2-A-05 · 实体类多份拷贝
- **现状**:同一张表在 core/system/dataCleanV2 各有 entity,以 dataCleanV2(带 @TableName)最权威,易不一致。
- **建议方向**:统一到 core 或独立 model 模块。

### DIAG-P2-A-06 · db2 数据源预留未用
- **现状**:三个数据模块的 db2 数据源当前无 Mapper(health 的 db2 除外)。
- **建议方向**:清理无用配置或明确规划。

### DIAG-P2-M-01 · 拼写错误遍布
- **现状**:groupId `*-patterntong`(应 pattern)、配置项 `off-line-detector-corn`/`reload-electric-fence-corn`(应 cron)、方法名 `updateExhaustGasTempareture`(少 t)、共享配置 `reids.yml`(应 redis)。
- **建议方向**:批量修正(注意兼容性,改 groupId 会让 Kafka 消费组重置)。

### DIAG-P2-M-02 · 前端无统一错误提示
- **现状**:ask.js 只处理 401,其余 code 各页面自判,无统一 toast。
- **建议方向**:响应拦截器统一 message 提示。

### DIAG-P2-M-03 · 前端工具链老旧
- **现状**:Webpack 3 + node-sass 4.14 + React 17 + antd 3;对高版本 Node 不友好。
- **建议方向**:评估升级 Webpack5/Vite + antd 4/5(大工程,需排期)。

### DIAG-P2-M-04 · 能效独立 baseURL 硬编码
- **现状**:`energyBaseURL=http://192.168.7.209:18888/` 未走网关。
- **建议方向**:纳入网关统一或 Nacos 配置。

### DIAG-P2-OPS-01 · 关键配置不在代码,新人难上手
- **现状**:网关路由表、Redis/数据源/Kafka 连接全在 Nacos;本地 yml 多被注释。
- **建议方向**:补本地开发 bootstrap样例 + 文档(本系列文档已在补)。

### DIAG-P2-OPS-02 · 缺少可观测性体系
- **现状**:异常多 log.info 吞掉,无统一监控/告警/链路追踪。
- **建议方向**:接入 Prometheus + Grafana + 链路追踪(SkyWalking 等)。

### DIAG-P2-DB-02 · 新老油水表并存
- **现状**:`tab_oil_water`(新,tank_id bigint,生效)与 `tab_oilwater`(老,废弃)共存。
- **建议方向**:确认无引用后 drop 老表。

### DIAG-P2-M-05 · 缺少自动化测试
- **现状**:前端 jest 配置存在但用例稀少;后端无明显单测;核心算法(SFOC/DBSCAN/Douglas-Peucker)无回归保护。
- **建议方向**:对核心计算补单测,防回归。

### DIAG-P2-M-06 · 油水附件上传接口混淆
- **现状**:FillOil 里 `uploadfile` 指向 `/sys/shipConfig/upload`(船配置导入),真正油水附件在子组件调 `/file/upload`,易误解。
- **建议方向**:前端接口名语义化,统一到油水附件入口。

---

## 四、改造优先级建议(待《改造优化方案》细化)

1. **立即处理(P0,1-2 周内)**:DIAG-P0-S-01/02/03/05(凭证与鉴权)、DIAG-P0-D-01(数据丢失)、DIAG-P0-S-04(船岸 SQL)。这些是安全与数据底线。
2. **近期推进(P1,1-2 月)**:新老链路收敛(A-01/02/03)、能效预聚合(P-01)、WS 重连(D-03)、Redis 可观测(S-06)。
3. **持续改进(P2,排期)**:拼写/实体统一/工具链升级/测试/可观测。

> 下一步:你确认本清单后,我在《BWT-VMS-改造优化方案.md》里对每条给出**改造步骤、影响文件、工作量、风险、验收标准**。
