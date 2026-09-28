# BWT-VMS 交付排错手册

> 更新日期:2026-09-24(条目5升级为三层诊断+新增条目13通导路由)(修改本文档后请同步更新此日期,并到 Dify 知识库重新上传该文件)

> 给交付/实施人员的排错手册:**不用看代码**,按症状找到条目,复制命令执行。
> 每条格式:症状 → 原因(一句话) → 定位 → 修复 → 验证。
> 翻不到的条目,把症状+日志发给会查的人(或 AI)。
>
> 船舶示例:XT GLORY(ship_id=35701001)。其他船把 ship_id 换掉即可。

---

## 0. 环境速查

| 项 | 值 |
|---|---|
| system 服务(用户/配置/菜单/油水) | 端口 **8085** |
| health 服务(海大推送 v1) | 端口 8087(当前环境未用) |
| mmha 服务(机舱健康 v2 计算) | 端口 **8088** |
| 前端 API 前缀规则 | 前端走 `/sys/**`、`/mmha/**`;**直接 curl 服务端口时要去掉前缀**。例:前端 `/sys/engine/param/health/allmenu` → `curl http://localhost:8085/engine/param/health/allmenu?shipId=35701001` |
| Redis 密码(测试环境) | `<Redis密码>`,db0 |
| 健康链路 Redis Key | 输入 `list:ship:{shipId}:other`,输出 `list:ship:{shipId}:health`,趋势 `health_cache:{shipId}:data:*` |
| 常用命令 | `redis-cli -a <Redis密码> GET "list:ship:35701001:other"`;`docker logs --tail 200 <容器>` |

---

## 1. 油水填报:保存成功后,再点「添加油水」没反应

- **症状**:填报保存正常,再点添加按钮无响应;gateway 日志有 `Infinite recursion (StackOverflowError)`,引用链全是 `TabOilWater["child"]`
- **原因**:`tab_tank_device.tank_id`(子舱)与 `tank_device_type`(油种分类 1/2/3)数值碰撞——同一个记录既被当父舱又被当子舱,自己挂进了自己的 child 列表
- **定位**:gateway 日志搜 `Infinite recursion`
- **修复**:执行 `XT_GLORY_fix_tank_id_collision.sql`(原则:**tank_menu_id = tank_device_type,tank_id 从 4 开始**编号)
- **验证**:重新填报 → 保存 → 再点「添加油水」,能正常进入页面
- **新船预防**:配油舱时 tank_id 直接从 4 开始(4~14),别用 1/2/3

## 2. 油水填报:存量/加量单位显示 m

- **症状**:页面显示 `存量(m)` `加量(m)`
- **原因**:`tab_tank_device.unit` 填成了传感器的原始单位 m;这个字段是**页面显示单位**,不是传感器单位
- **修复**:
  ```sql
  UPDATE tab_tank_device      SET unit='t' WHERE ship_id=35701001;
  UPDATE tab_tank_device_menu SET unit='t' WHERE ship_id=35701001;
  ```
- **验证**:刷新页面,显示 `存量(t)`;新填报的数据存 t

## 3. 油水填报:昨天的数据改不了(提示请添加油水后再修改)

- **症状**:过了午夜 0 点后进编辑页,日期自动变成今天,保存报"请添加油水后再修改"
- **原因**:系统设计只允许修改**当天**数据(按 `time=当天零点` 查重),不是 bug
- **处理**:直接点「添加油水」新建今天的数据,昨天的已存好不会丢。以后当天完成填报,别卡午夜

## 4. 机舱健康:页面没有分数(全部空/--)——最常见

- **症状**:健康页面无值;`redis-cli GET "list:ship:35701001:other"` 返回 `(nil)`
- **原因**:健康分是 mmha 服务**每分钟自算**的:读 Redis `other`(实时工况值)→ 按部件树模型算分 → 写 `health`。环境无采集数据时输入为空,引擎空转
- **定位**:① `redis-cli -a <Redis密码> GET "list:ship:35701001:other"` 是否 (nil);② mmha 日志搜 `未获取到实时数据`
- **修复**:执行 `simulate_health_80.sh`(往 `other` 注入 108 个工况点的"中点值",全链路仿真)
- **验证**:等 1 分钟,`HGETALL "list:ship:35701001:health"` 出现 `000000 → "<utc>\t92.5\t{}"`,页面显示分数
- **想恢复正常无数据**:`DEL "list:ship:35701001:other"`
- **分数知识**:线性模型 normal值→100 分,超界→边界分,中间线性。**本环境 Nacos 配了边界分=85,所以分数只能落 85~100**(注入中点值=92.5)。要出现 80 分,需把 Nacos `health.shipScoreParam` 的 boundaryScore 改回 60
- **新船预防**:机舱健康要出分,三样必须配齐:`mmha_component`(树)、`mmha_component_device_relation`(叶子绑工况点+normal/max/min 参数)、`mmha_component_model_info`(模型 01=线性);运行条件公式里的工况点(如 40793)也要有实时值

## 5. 机舱健康:中文正常,切英文页面空白

- **症状**:中文环境菜单/部件名正常,英文环境一片空白
- **原因**:**中英文是两套独立字段/记录,不会自动同步**。英文空白是**三层缺失**,最易漏的是第 3 层:
  1. `mmha_component.component_name_en` 空(部件英文名)
  2. `mmha_component.svg_url_en` 空(英文整图地址;前端 svgDom 内嵌渲染时可暂不填)
  3. **`tab_engine_param_menu_mimic_config` 里英文(language=1)的机舱健康(menu_type='2')配置是 0 条**——组态图+绑点全在 language=1 这套记录里,没有就整页空白
- **定位**:
  ```sql
  SELECT language, menu_type, COUNT(*) FROM tab_engine_param_menu_mimic_config WHERE ship_id=? AND menu_type='2' GROUP BY language, menu_type;
  -- 中文(language=0)有、英文(language=1)为 0 → 命中第 3 层
  SELECT component_key, component_name, component_name_en FROM mmha_component WHERE ship_id=?;
  ```
- **修复**(两步):
  1) 填部件英文名:
  ```sql
  UPDATE mmha_component SET component_name_en='Main Engine' WHERE ship_id=? AND component_key='000000';
  UPDATE mmha_component SET component_name_en='NO.1 Generator' WHERE ship_id=? AND component_key='010000';
  -- 其余:主机本体 Main Engine Body / 主机SCR Main Engine SCR System / 主机润滑 ME Lubrication System / 020000·030000 NO.2/3 Generator / 040000 Shaft Generator,子部件同理加 Body/SCR System
  ```
  2) 补英文组态配置(语言层,绑点原样保留):读 `language=0 AND menu_type='2'` 的 7 条 → `groupNameEn` 填英文、`svgDom` 里 **`<text>` 内的中文**翻译成英文 → 写回 `language=1` 的 7 条。**只翻译 `<text>` 显示文字,图层名(Sketch 残留的"编组/矩形/机舱监控"等)别动**,否则中英文版结构不一致、绑点跑偏

## 5b. 机舱健康英文空白:生效与验证

- mmha 配置缓存 **10 分钟**刷新(急就重启 mmha);浏览器 **Ctrl+F5**(菜单缓存在 localStorage)
- 验证:切英文页面菜单/部件名/组态图都显示英文
- **新船预防**:所有中文名配置,**英文名一起填**;组态图**切英文界面再保存一次**(前端按当前语言存 language=1)

## 6. 数据库报错:Illegal mix of collations (1267)

- **症状**:JOIN/查询报 `Illegal mix of collations (utf8_unicode_ci,IMPLICIT) and (utf8_general_ci,IMPLICIT)`
- **原因**:表之间排序规则不一致
- **定位**:
  ```sql
  SELECT table_name, table_collation FROM information_schema.tables
  WHERE table_schema = DATABASE() AND table_collation NOT IN ('utf8_general_ci','utf8mb4_bin');
  ```
- **修复**:把 utf8_unicode_ci 的表统一成主流的 general_ci(**utf8mb4_bin 的第三方表保留不动**):
  ```sql
  ALTER TABLE 表名 CONVERT TO CHARACTER SET utf8 COLLATE utf8_general_ci;
  ```
- **验证**:原报错 SQL 重跑通过

## 7. 新船配置防错:列名/类型坑(报错对照)

| 报错 | 原因 | 正确写法 |
|---|---|---|
| `Unknown column 'value'` | `tab_ship_info_extend` 值列不叫 value | 列名是 **`param_value`** |
| `Unknown column 'capacity'` | `tab_height_cabin` 体积列不叫 capacity | 列名是 **`cabin`** |
| `Incorrect integer value: 'HFO'` | `tab_tank_device` 的 tank_id/tank_menu_id 是 **INT** | 用整数 4~14(只有 `tab_equipment.uuid` 是 varchar 可用 mainframe_1 这种) |
| `Incorrect integer value: 'HFO'` | `tab_tank_device_menu` 同上 | tank_menu_id 用 1/2/3 且**必须等于 tank_type** |

详见《BWT-VMS新船能效配置操作手册》第 4 节"常见坑"。

## 8. 服务器时间:虚拟机时间比真实时间多 8 小时

- **症状**:VM 里 date 比北京时间快 8 小时;ESXi 显示的 UTC 和北京时间数字一样(16:xx UTC——错的,UTC 应是 08:xx)
- **原因**:ESXi 把北京时间当成 UTC 存了,VM 继承后再加时区 +8
- **修复**(ESXi 上执行,时间=当时北京时间减 8 小时):
  ```bash
  esxcli system time    set -y 2026 -M 8 -d 8 -H 8 -m 30 -s 0   # 软件时钟
  esxcli hardware clock set -y 2026 -M 8 -d 8 -H 8 -m 30 -s 0   # 硬件时钟(重启不丢,必做)
  ```
- **验证**:重启 VM 后 `date` 显示正确北京时间

## 9. ESXi 的 SSH 隔一段时间自动关闭

- **修复**:ESXi Web → Manage → Services → TSM-SSH → Policy 选 **Start and stop with host**;或 `chkconfig sshd on`
- **账号被锁**(密码明明正确却提示错误):连续失败触发锁定,等 15 分钟,或 DCUI 物理控制台 → Troubleshooting Options → Restart Management Agents

## 10.【已结案】机舱监控 SVG:开关型设备显示红色,不是配置的绿色

- **症状**:`tab_engine_state_quantity_cfg` 配了 1=绿色,值=1 显示 RUN 文本但颜色红
- **根因**(代码级结案):该点 **device_type 被错配成 '0'(报警量)**→ dataCleanV2 按 tab_alarm_mapping(dds 1→0)归一化成 0 → 前端判红。SVG 颜色不读 state_quantity_cfg(那是列表视图用的),报警元素硬编码 0红/1绿。device_type='2' 的点永远不归一化
- **修复**:`UPDATE tab_engine_param SET device_type='2' WHERE ship_id=? AND device_key=?;` → **重启 realTimeData**(报警映射只在启动加载)+ 等 dataCleanV2 缓存 5 分钟 → 前端强刷
- **验证**:Redis `GET list:ship:{id}:other` 该点="1.0";`HKEYS engine:alarm:device:{id}` 不含该点
- 详见《报警链路域操作手册》第 0 节

## 11. 导航下拉想隐藏部分分类——别动菜单 status

- **症状/风险**:直接 `UPDATE tab_engine_param_menu SET status='0'` 隐藏分类,会**连带隐藏该分类下所有工况点和 SVG 绑定**(全局副作用)
- **正确做法**:改打包后的前端 index.html 注入 CSS/JS 隐藏(nginx 无 http_sub_module,改 index.html 最稳)

## 12. 机舱健康分数原理(知识条目,给客户解释用)

- 引擎每分钟算一次:叶子部件按线性模型打分 → 父部件按 MIN 汇总 → 全船
- 线性模型:`正常值=100 分,超出上下限=边界分,中间线性`。例:排温正常 470(100 分)、上限 600(边界分),实测 535 → 恰好中点 → (100+边界)/2 分
- 颜色规则:**≥70 绿(正常)、60~70 黄(预警)、<60 红(报警)**

## 13. 通导:航向/航速/船位不显示

- **症状**:通导页面航向、航速、船位(经纬度)不显示,其他工况点正常
- **原因**:`tab_statistics_param` 只绑了 AIS 通道(1103xxx),缺 GPS(1101xxx)和 GYRO(1105xxx)设备的通道路由,数据上来了也路由不到显示
- **定位**:
  ```sql
  SELECT device_key, device_type FROM tab_statistics_param WHERE ship_id=? AND device_key LIKE '11%' ORDER BY device_key;
  -- 只有 1103xxx(AIS),缺 1101001/1101002/1101022/1101025(GPS)、1105001/1105004(GYRO)
  -- 确认真实通道号:SELECT KeyValue, desc_cn, TopicName FROM iscu.hldkeymap WHERE KeyValue LIKE '11%';
  ```
- **修复**(补 GPS+GYRO 路由,device_type 语义见 tab_statistics_param 表注释):
  ```sql
  INSERT INTO tab_statistics_param (ship_id, device_key, device_type, device_number) VALUES
  (?, '1101001', '9',  0),  -- GPS纬度
  (?, '1101002', '8',  0),  -- GPS经度
  (?, '1101022', '21', 0),  -- GPS对地航向
  (?, '1101025', '3',  0),  -- GPS航速
  (?, '1105001', '20', 0),  -- GYRO艏向
  (?, '1105004', '23', 0);  -- GYRO回转率
  ```
- **验证**:Redis `list:ship:{shipId}:navigation` 出值;前端通导页显示
- **注意**:device_key 前缀 11+设备号(GPS=01/GYRO=05/AIS=03/风速仪=06/测深仪=04/舵=13);1103001~1103010 是 AIS 通道(AIS_OWN_*),别和 GPS/GYRO 混淆

---

## 附:排错通用三板斧

1. **先看现象属于哪条链路**:页面空白 → 先查对应 Redis key 有没有数据 → 再查服务日志
2. **日志关键词速查**:`Infinite recursion`(油水死循环)、`未获取到实时数据`(健康无输入)、`collation`(排序规则)、`请添加油水后再修改`(跨午夜)
3. **改完数据库必做两件事**:对应服务配置缓存是否需要刷新(mmha 是 10 分钟/重启)、浏览器 Ctrl+F5(localStorage 缓存)

> 手册会持续更新。新问题解决后照格式补条目:症状 → 原因 → 定位 → 修复 → 验证。
