---
name: "bwt-vms-faq"
description: "BWT-VMS 交付排错 FAQ(21条):机舱健康没分数、机舱健康切英文空白(三层诊断)、通导航向/航速/船位不显示(GPS/GYRO路由)、油水填报死循环、SVG开关量红色(已结案)、全船报警反色、账号登录不了、围栏不生效、ECA不提醒、tcp server offline重连、JsonString 208变221、姐妹船通导/EIM无数据(TKA2501实战3条)等,按症状给定位命令和复制即执行的修复 SQL。用户提到 BWT-VMS 排错/交付问题/机舱健康/油水/报警/围栏/账号/采集异常/通导时触发。"
---

# BWT-VMS 船舶管理系统交付排错 FAQ(21 条)

你是 BWT-VMS(内河船舶管理系统)的交付排错助手。用户描述症状,按知识库匹配:**症状 → 原因 → 定位命令 → 修复(复制即执行的 SQL)→ 验证**。

## 环境速查

| 项 | 值 |
|---|---|
| system 服务 | 8085(/sys 前缀;curl 直连去前缀) |
| mmha(健康) | 8088 |
| Redis | 密码 <Redis密码>,db0;健康 in=`list:ship:{id}:other` out=`list:ship:{id}:health` |
| 账号 | spadmin 在 Nacos;admin/<密码>;密码=MD5(Base64(明文)) |
| 报警判定 | device_type='0' + tab_alarm_mapping 归一化==0;阈值列不参与 |
| 采集 | MOXA→datacap(读iscu)→PNG→transplatform→Kafka;LocalGateway.xml 是通道 IP/端口唯一真源 |

## FAQ 条目

### 1. 油水填报:保存后再点「添加油水」没反应(StackOverflow)
- 原因:tank_id 与 tank_device_type 数值碰撞自引用
- 定位:gateway 日志搜 `Infinite recursion`
- 修复:执行 XT_GLORY_fix_tank_id_collision.sql(tank_menu_id=type,tank_id 从 4 起;新船直接用 4~14)

### 2. 油水填报:存量/加量单位显示 m
- 修复:`UPDATE tab_tank_device SET unit='t' WHERE ship_id=?; UPDATE tab_tank_device_menu SET unit='t' WHERE ship_id=?;`

### 3. 油水填报:昨天的改不了
- 系统设计只允许改当天;过了午夜新建今天的

### 4. 机舱健康:页面没分数(最常见)
- 原因:mmha 每分钟自算,读 Redis other;无实时值空转
- 定位:redis GET other 是否 nil;mmha 日志 `未获取到实时数据`
- 修复:simulate_health_80.sh 注入;验证 HGETALL health 出 92.5(85~100 是 boundaryScore=85 正常结果)

### 5. 机舱健康:中文正常英文空白(三层缺失,最易漏第 3 层)
- 原因:**中英文是两套独立字段/记录,不会自动同步**。英文空白是三层缺失:
  1. `mmha_component.component_name_en` 空(部件英文名)
  2. `mmha_component.svg_url_en` 空(英文整图地址;前端 svgDom 内嵌渲染时可暂不填)
  3. **`tab_engine_param_menu_mimic_config` 里英文(language=1)的机舱健康(menu_type='2')配置 0 条**——组态图+绑点全在 language=1 这套记录里,没有就整页空白(真正的根因)
- 定位:
  ```sql
  SELECT language, menu_type, COUNT(*) FROM tab_engine_param_menu_mimic_config WHERE ship_id=? AND menu_type='2' GROUP BY language, menu_type;
  -- 中文(0)有、英文(1)为 0 → 命中第 3 层
  SELECT component_key, component_name, component_name_en FROM mmha_component WHERE ship_id=?;
  ```
- 修复(两步):
  1) 填部件英文名:
  ```sql
  UPDATE mmha_component SET component_name_en='Main Engine' WHERE ship_id=? AND component_key='000000';
  UPDATE mmha_component SET component_name_en='NO.1 Generator' WHERE ship_id=? AND component_key='010000';
  -- 其余:主机本体 Main Engine Body / 主机SCR Main Engine SCR System / 主机润滑 ME Lubrication System / 020000·030000 NO.2/3 Generator / 040000 Shaft Generator,子部件同理加 Body/SCR System
  ```
  2) 补英文组态配置(绑点原样保留):读 `language=0 AND menu_type='2'` 的记录 → `groupNameEn` 填英文、`svgDom` 里 **`<text>` 内的中文**翻译成英文 → 写回 `language=1`。**只翻译 `<text>` 显示文字,图层名(Sketch 残留"编组/矩形/机舱监控"等)别动**,否则中英文版结构不一致、绑点跑偏
- 生效:mmha 缓存10分钟/重启 + Ctrl+F5
- 预防:配中文名时英文名一起填;组态图**切英文界面再保存一次**(前端按当前语言存 language=1)

### 6. 数据库:Illegal mix of collations (1267)
- ALTER TABLE 转 utf8_general_ci(utf8mb4_bin 保留)

### 7. 新船配置防错:列名/类型坑
- param_value/cabin/tank_id INT

### 8. 虚拟机时间多 8 小时
- esxcli system time set + **esxcli hardware clock set**(UTC=北京-8h)

### 9. ESXi SSH 自动关/账号锁定
- chkconfig sshd on;锁定等15分钟或 DCUI Restart Management Agents

### 10.【已结案】SVG 开关量显示红色非配置绿
- 根因:device_type 错配 '0' → alarm_mapping 归一化 1→0 → 红;SVG 颜色不读 state_quantity_cfg(绑定快照,报警元素硬编码 0红1绿)
- 修复:device_type 改 '2' → **重启 realTimeData** → 强刷;验证 Redis other="1.0"

### 11. 全船报警反色
- tab_alarm_mapping 极性配反(每船2行;报警时DDS发1配 (1→0),(0→1))

### 12. 报警统计里有不报警的点
- 统计 job 不过滤 device_type;修后次日正确

### 13. 客户账号登录不了(只有 spadmin 能登)
- tab_user_info 被清过/别名表 tab_login_user_info 没插(登录走它 JOIN)
- 修复:用户权限手册路线B冷启动 SQL

### 14. 船进港不提示/围栏不生效
- 纯 SQL 配围栏没 reload(调 realTimeData /electricFence/reload);顶点错被静默丢弃(radius 非空必须仅1顶点)

### 15. ECA 不提醒
- ECA 围栏装机自带(type=4);告警 Redis list:ship:{id}:eca-alert 仓库内无消费方(小程序端消费)

### 16. 想隐藏导航下拉分类
- 别动 menu status;改打包后 index.html 注入 CSS

### 17. 健康分原理
- 每分钟叶子线性(normal=100,超界=边界分)→父MIN;≥70绿/60-70黄/<60红

### 18.【TKA2501 实战】datacap 日志刷 `tcp server offline` 重连不停
- 原因:datacap 连不上下游(transplatform/岸端)或 MOXA;**LocalGateway.xml 是通道 IP/端口的唯一真源**(XML 与 iscu 库 deviceinfo 不一致时以 XML 为准)
- 排查:① 按逐通道核对 XML 里 ip/port/net_mode 与 MOXA 实际监听 ② 船端 telnet/nc 测端口连通 ③ 确认 transplatform server 起了没(5165)

### 19.【TKA2501 实战】JsonString 手工插库后长度 208 变 221(被改写)
- 原因:手工 INSERT 的转义处理与程序写入格式不一致(mysql 客户端对 \n 等转义的静默处理差异)
- 修复:**mysqldump 回放法**——参照船 mysqldump 导出→改→回放,保证字节级一致
- ⚠️ 陷阱:查询验证时某些 mysql 客户端对 longtext 静默返回空结果(不报错), LENGTH() 与 SELECT 内容分别验证

### 20.【TKA2501 实战】同系列姐妹船通导/EIM 无数据而 AMS 正常
- 原因:生成工具(make_jsonstring)只覆盖 AMS/BTP/LOAD;**通导 9 通道、EIM 点表、LocalGateway.xml 是工具盲区**——从参照船复制时漏了哪项哪项就没数据
- 排查:逐项比对参照船(SailingData 9 通道 deviceinfo/topicinfo/hldkeymap 通导段、EIM ch9/50001·50002、XML 端口)

### 21. 通导:航向/航速/船位不显示(路由缺 GPS/GYRO)
- 症状:通导页面航向、航速、船位(经纬度)不显示,其他工况点正常
- 原因:`tab_statistics_param` 只绑了 AIS 通道(1103xxx),缺 GPS(1101xxx)和 GYRO(1105xxx)设备的通道路由,数据上来了也路由不到显示
- 定位:
  ```sql
  SELECT device_key, device_type FROM tab_statistics_param WHERE ship_id=? AND device_key LIKE '11%' ORDER BY device_key;
  -- 只有 1103xxx(AIS),缺 1101001/1101002/1101022/1101025(GPS)、1105001/1105004(GYRO)
  -- 确认真实通道号:SELECT KeyValue, desc_cn, TopicName FROM iscu.hldkeymap WHERE KeyValue LIKE '11%';
  ```
- 修复(补 GPS+GYRO 路由,device_type 语义见 tab_statistics_param 表注释):
  ```sql
  INSERT INTO tab_statistics_param (ship_id, device_key, device_type, device_number) VALUES
  (?, '1101001', '9',  0),  -- GPS纬度
  (?, '1101002', '8',  0),  -- GPS经度
  (?, '1101022', '21', 0),  -- GPS对地航向
  (?, '1101025', '3',  0),  -- GPS航速
  (?, '1105001', '20', 0),  -- GYRO艏向
  (?, '1105004', '23', 0);  -- GYRO回转率
  ```
- 验证:Redis `list:ship:{shipId}:navigation` 出值;前端通导页显示
- 注意:device_key 前缀 11+设备号(GPS=01/GYRO=05/AIS=03/风速仪=06/测深仪=04/舵=13);1103001~1103010 是 AIS 通道(AIS_OWN_*),别和 GPS/GYRO 混淆

## 三板斧(无匹配条目时)

1. 页面空白→Redis key→服务日志
2. 日志关键词:Infinite recursion/未获取到实时数据/tcp server offline/账号船舶无效
3. 改库后:mmha 缓存10分钟;**realTimeData 报警映射要重启**;datacap 改 iscu 后要重启;前端 Ctrl+F5
