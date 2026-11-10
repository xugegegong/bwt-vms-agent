# 现场排错复盘:整栈 docker compose 冷启动后 CPU/负载被打满

> 知识层级:**实战验证**(2026-11-09 客户现场复盘,机器实测取证,未经 C 审核;命令均可直接复用)
> 适用:iCommander 整栈 docker-compose 部署(deploy-standalone.sh + docker-compose.yml,20+ 容器),尤其是小内存物理机
> 排错手册条目 14 的详细版,按 what/why/how 整理,供学习与培训用

## 一、症状

现场整栈 `docker compose up` 后,没有任何船端数据进来,但服务器 load average 从 20+ 一路飙到 100+,SSH 登录都变卡。

机器画像:4 核 / 7.4G 内存 / 4G swap,跑 22 个容器(iCommander 全栈 + prometheus 监控全家桶)。

## 二、排查过程(按时间线)

### 1. 定位是谁在烧 CPU

**What**:`docker stats --no-stream` 按 CPU 排序。
**Why**:先分清是"哪个容器"还是"宿主机进程",避免瞎猜。
**How**:两轮采样出现反转——第一轮 kafka 128% + dataflow 102%,几分钟后变成 system 97%。**CPU 大户在轮换 = 崩溃-重启循环的典型特征**(谁在启动谁就是大户)。

### 2. 确认重启循环

**What**:`docker ps` 看 STATUS,`docker inspect -f '{{.RestartCount}} {{.HostConfig.RestartPolicy.Name}}' <容器>`。
**Why**:`restart: always` 的服务起不来会无限重启,每次都是一整轮 JVM 冷启动(单核吃满几分钟),几个服务一起循环就是持续高负载。
**How**:dataflow 重启 3 次、system 处于 `Restarting (1)`、kafka 重启 2 次。附带发现:kafka 每次启动加载历史日志段要 68 秒,本身就是一段 CPU 高峰。

### 3. 抓崩溃根因(第一层真相:报错)

**What**:`docker logs` 搜 `APPLICATION FAILED` / `Caused by`。
**Why**:循环只是表象,要拿每个服务自己的死因。
**How**:
- **system**:`Failed to configure a DataSource: 'url' attribute is not specified`,且"WebApplicationContext initialization completed in **1154375 ms**"——启动 19 分钟。
- **dataflow**:`RedisCommandTimeoutException: Command timed out after 1 minute(s)`,一条 Redis DEL 等了 60 秒。

此时极易误判为"配置缺失要补 Nacos 配置"。**先别动手——19 分钟的启动耗时说明机器本身已经病了。**

### 4. 关键转折:看 CPU 构成(第二层真相:磁盘风暴)

**What**:`top -b -n1` + `iostat -x 1 2`。
**Why**:load 106 但 top 里 `%CPU: 65.2 wa`、us 只有 23.5%——**这不是算力问题,是 IO 等待**。
**How**:磁盘 util 83%+、单次 IO 延迟 183ms、swap 已用 3.1G;redis-server、python3 都在 D 状态(不可中断地等磁盘)。结论:**内存耗尽 → 疯狂换页 → nacos/redis/mysql 全部超时 → 服务启动失败 → 无限重启 → 更多内存 churn → 死亡螺旋**。

### 5. 找内存元凶(第三层真相:一个堆参数)

**What**:怀疑某服务内存配置错误。
**Why**:机器 7.4G,compose 各服务限额加起来约 15G,本来就超卖;但"没数据也爆"说明有服务启动即申请巨量内存。
**How**:`grep -H 'Xmx\|Xms' /opt/iCommander/*/start.sh`——**只有 system 一个是 `-Xms4096m -Xmx4096m`(4G 堆,Xms=Xmx 启动即占满)**,其余服务都是 256~750M 的正常值。system 的容器限额只有 2560M,4G 堆 > 限额,而 `memswap_limit: -1` 允许超限随便进 swap。这是从大内存机器拷贝启动脚本模板没改留下的坑。

## 三、修复动作(按顺序)

1. **止血**:`docker update --restart=no --cpus 0.5 system dataflow`——先关掉循环,防止继续空烧。
2. **释放内存**:`docker stop system dataflow`(配置没修好前它们注定起不来,停掉即释放约 2G)。
3. **根治**:
   ```bash
   cp /opt/iCommander/system/start.sh /opt/iCommander/system/start.sh.bak
   sed -i 's/-Xms4096m -Xmx4096m/-Xms348m -Xmx1024m/' /opt/iCommander/system/start.sh
   ```
4. **验证启动**:`docker start system dataflow` → dataflow Tomcat 8102 启动成功(**证明它之前纯属被连带拖死,配置从头到尾没动过**);system Tomcat 8085 启动成功(启动早期短暂 Nacos Connection refused 属正常重试)。
5. **恢复自启**:`docker update --restart=always system dataflow`。
6. **验证 Kafka 客户端通路**(顺手检查):`kafka-broker-api-versions.sh --bootstrap-server kafka.hbluewhale.com:9092` 应返回正确的 broker 地址;若返回的是别的机器 IP,说明 `server.properties` 的 `advertised.listeners` 是从别的环境拷来的残留,要改成 `PLAINTEXT://<本机IP>:9092` 后重启 kafka。本案验证正常。

最终:load 106 → 7.6,全部容器 Up,CPU 最高 5.6%,swap 残留会自然换回。

## 四、经验沉淀

1. **"没数据还占满 CPU"先查重启循环**:`docker ps` 看 STATUS 有没有 Restarting/秒级 Up;CPU 大户轮换 = 循环启动。
2. **load 高 ≠ CPU 不够**:先看 `top` 的 wa 列。wa 60%+ 就是磁盘/swap 风暴,加 CPU 没用,查内存。
3. **JVM 容器三大坑**:`-Xmx` 超过容器限额、`Xms=Xmx`、`memswap_limit: -1`。三者叠加 = swap 风暴预定。修复原则:**Xmx ≤ 容器限额一半**(给 metaspace/栈/堆外留量),Xms 调小。
4. **依赖超时的"假根因"**:服务报数据源缺失/Redis 超时,不一定是真配置问题——先把机器救活再复测。判据:超长启动耗时本身就是机器被拖死的铁证。
5. **部署脚本要逐项核对机器画像**:deploy-standalone.sh 里 JVM 参数、容器限额、advertised.listeners 都是"按某台模板机写的",换小内存机器部署时必须改齐三处:start.sh 堆参数、compose 限额、kafka 广播地址。
6. **docker compose 的 `deploy.resources.limits` 在非 swarm 下也生效**(docker stats 显示 `/1.25GiB` 即证据),不需要额外再加限制;真正要查的是 JVM 堆和限额是否对齐。

## 五、遗留事项(该环境的长期风险)

- 各服务内存限额总和 ~15G > 物理内存 7.4G,属于永久性内存超卖,swap 长期在用。数据量上来后(PNG 解析 + Kafka + MySQL 写入)会再次紧张,建议换 16G 机器或把监控全家桶挪走。
- `system/start.sh.bak` 为修改前备份,现场验证稳定几天后可删。
