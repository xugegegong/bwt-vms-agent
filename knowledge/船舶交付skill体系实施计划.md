# 船舶交付 Skill 体系实施计划

> 基于对老系统全套源码的阅读(datacap 采集 + transplatform 转发 + 可视化配置中心 + 岸端 Java),输出面向"新船交付配置"的 skill/Agent 体系实施计划。
> 供用户确认后再逐个开工。

---

## 一、认知基线(本轮代码阅读沉淀,是设计前提)

1. **采集链路全通**:`AMS/NMEA → datacap(读 iscu 编码 PNG)→ TCP:5165 → transplatform(转 HEX)→ Kafka ship-{id}-data-org-makeup → dataCleanV2(按 order_id 查 tab_engine_param)→ Redis`。
2. **源码在手**:datacap 与 transplatform 均为 C++ 本地源码,不再是黑盒;iscu 库是 datacap 的"点表配置"。
3. **可视化配置中心 = 配置需求权威入口**:`ConfigurationManagement`(spadmin→系统配置)。每个配置模块 = 一组表的 CRUD,与手动 SQL **同源**。
4. **两条黄金通道**(skill 优先引导):
   - **配置导出克隆**:`configExport(newShipId=母船)` 可把一条船全量配置克隆成新船 → 新船 = 克隆 + 针对性调整。
   - **机舱 Excel 导入导出**:`engineConfigExport/importEngineParam`(列序固定,sheet=shipId)。
5. **采集配置的特殊性**:`/config/visual/engine/datacap/*` **仅船端版可用**(岸端抛"岸端无需配置采集")。岸端配 iscu 只能靠导出的模板带到现场,或直接 SQL/JsonString。
6. **硬约束**:`tab_engine_param.order_id` ↔ `iscu.hldkeymap.orderkey` 一一对应;公共导航段(orderkey -2~39)每条船照抄;`ways/ShipId` 不动。
7. **船型差异**:机舱健康(S4)、能效(S5)大多数船没有 → skill 必须**先问"本船要不要这功能"**再走对应流程,不能一股脑全配。
8. **船端两阶段实施**:船上实际流程是 **MOXA→iscu→datacap 出数验证** 通过后,**才配 vms_sys_db**。因为 `tab_engine_param.order_id` 与 `hldkeymap.orderkey` 强绑定,两边同时改费时费力易返工 → 拆成 S1(船端出数)+ S2(岸端库表)两个 skill,中间以"datacap 日志确认数据正常"为闸门。

---

## 二、Skill 体系蓝图

```
S0「配置新船」= 总编排(入口)
 ├── S1 ISCU 采集配置+串口服务器(船端先把数据采进来并验证出数)
 ├── S2 vms_sys_db 库表配置(闸门通过后才配 tab_engine_param 等岸端表)
 ├── S3 mimic 图(SVG 生成 + 平台绑定)→ 已有 svg-generation-rules,补绑定流程
 ├── S4 机舱健康配置(问:船要不要?→ mmha 树/关系/模型)
 ├── S5 能效系统配置(问:船要不要?→ 信号路由/设备/油舱/舱容/CII)
 └── S6 交付排错 FAQ(已建 bwt-vms-faq,兜底)
```

**S1→S2 闸门**:S1 结束时 vms_sys_db 仍是初始状态(只有初始化数据);必须 datacap 日志确认采集/解码出数无误,才进入 S2 配岸端表。

原则:
- **子 skill 只讲本领域**,流程判断交给 S0。
- **每条 skill 内嵌两套路径**:① 可视化配置优先(无 bug 时)② SQL/脚本兜底(有 bug/可视化不可用)。
- **验证步骤强制内置**(Redis key/接口/SQL 验证查询),交付人员跑完能自证。

---

## 三、各 Skill 规格

### S1 ISCU 采集配置 + 串口服务器(船端出数,核心)

> **范围**:只做船端"能采到数"。结束时 **vms_sys_db 不配**(仍初始状态)。岸端表配置在 **S2**。

- **触发**:来了条新船要配采集 / "AMS 点表怎么转成采集配置"
- **输入**(前置资料,两份都要):
  1. **MOXA 串口规划表**(物理层):哪些 RS485 设备接哪个串口、波特率/校验、映射 TCP 端口、模式(通导 NMEA 推送 / AMS Modbus 主站轮询)。参考 XT GLORY 两份 MOXA 导出(NP5650I-8-DT 1.233/1.234)→ 决定 datacap 通道与 deviceinfo
  2. **AMS Modbus 点表**:从站/寄存器/功能码/数据类型/中文名 + 附加点(LOAD/BTP/EIM/流量计/功率)+ 采集部署环境(船端)
- **输出**:① MOXA 配置核对表 ② 可执行 SQL(iscu 四表)③ JsonString 生成脚本(Excel 点表→JSON)④ datacap 部署&验证步骤
- **流程**:
  1. 判定采集形态:通导常规(GPS/AIS/GYRO/回波/计程仪/风速/舵=标配)→ AMS Modbus → 特殊接入(EIM/LOAD/BTP/流量计/功率)?→ 对照 MOXA 串口规划逐项要清单
  2. **配串口服务器**(MOXA):按规划设串口参数(设备/波特率/校验/停止位)+ 工作模式 + TCP 端口/目标主机 → 现场用串口助手确认每个口能出数据
  3. 备公共段:`hldkeymap` orderkey -2~39 导航段 + `ways` 原样;通导段(orderkey 9~39)按标配抄,特殊设备追加
  4. 生成 AMS 点表:`channelsdataprotocol.JsonString`(RTU 段:FunCode03/RegisterBegin/每点 Addr/Alias/DataTypeRegister/系数)+ `hldkeymap`(orderkey 从 101 递增、KeyValue=寄存器尾00、desc_cn)
  5. 生成 `deviceinfo`(Topic/SlaveId,对照 MOXA 端口)+ `topicinfo.IndexSet`
  6. **datacap 验证**(闸门):灌 iscu → 重启 datacap → 看日志确认采集/编码正常 → 岸端 Redis `list:ship:{id}:other` 或 datacap 本地日志出数。**确认无误才算 S1 完成,才进 S2**
- **可视化路径**:CollectConfig(船端 datacap 版)有的话用;没有就 SQL
- **校验规则**:order_id 无重复、KeyValue 无重复、AMS 点数=JsonString Results 数、DataTypeRegister 合法、MOXA 端口与 deviceinfo/LocalGateway 对齐
- **关键脚本**:`make_jsonstring.py`(读 Excel 列:寄存器起止/类型/描述 → 输出 JsonString 片段 + hldkeymap INSERT)

### S2 vms_sys_db 库表配置(岸端,闸门通过后才做)

> **范围**:等 S1 出数验证通过后,把采集点同步成岸端主库表,**使岸端能解码展示/供 S3~S5 使用**。与 iscu 强绑定(order_id↔orderkey),所以刻意独立、不在 S1 同做。

- **触发**:S1 已验证出数,现在配岸端工况点库
- **输入**:S1 定稿的 hldkeymap(iscu)+ 船厂 AMS 点表(含量程/报警/单位)+ 菜单规划(机舱监控树层级)
- **输出**:① 可执行 SQL(tab_engine_param + menu + relationship + 联动表)② 机舱监控菜单树结构 ③ 验证查询
- **核心表**:
  1. `tab_engine_param`:每采集点一行 —— `device_key`(惯例=KeyValue 去尾00,仅要求本列唯一)、**`order_id` = hldkeymap.orderkey(硬约束)**,`device_name`/`device_name_en`(两套!),`device_type`(0报警/1数值/2开关),`min_value/max_value`,报警 `low/high/low_low/high_high`,`unit`,`trans_data`(公式)
  2. `tab_engine_param_menu`:机舱监控/健康菜单树(父节点+子节点+叶子挂 device_key),`menuType` 区分
  3. `tab_engine_param_relationship`:工况点关系
  4. 联动表(如挂菜单/改类型触发):`tab_statistics_param`、`tab_engine_state_quantity_cfg`
- **流程**:
  1. 从 iscu.hldkeymap 拉全量(orderkey/KeyValue/desc_cn)→ 生成 tab_engine_param 主数据(补量程/报警/单位/中英文名/device_type)
  2. 按船厂 AMS 目录建 tab_engine_param_menu 树(参考 XT GLORY 菜单层级),叶子挂 device_key
  3. 校验:order_id 与 hldkeymap 全量一一对应、device_key 唯一、菜单叶子都有对应 param
  4. 验证:dataCleanV2 消费后 Redis `list:ship:{id}:other` 出现**带中文名/量程的完整点**;前端机舱监控能搜到工况点
- **可视化路径**:EngineParam/EngineParamMenu(IcommanderConfig backend/menu 接口);大量点时用 `importEngineParam` 机舱 Excel 导入(sheet=shipId,列序固定)
- **坑**:device_type 别搞混(0报警/1数值/2开关 vs 采集 DataTypeRegister);中英文名两套必填(否则英文空白);有公式点(trans_data)的 device_key 要在 relation 里正确关联

### S3 mimic 图(SVG + 平台绑定)

- **已有** `svg-generation-rules`(SVG 代码生成规范)。
- **补**:平台绑定流程 = 上传 SVG(`uploadSvgList`)→ 在 DefineHealthPage 拖点位绑定 device_key → 中英文两套图(`svgUrl/svgUrlEn`)→ 保存版本(`submitMimicCon`)→ 验证。做成 S3 的"绑定编排",SVG 生成交给 svg skill。
- **依赖**:需 S2 完成(图绑定的 device_key 来自 tab_engine_param)

### S4 机舱健康配置

- **触发**:配置机舱健康 / 健康页没分数要排查
- **输入**:设备清单(主机/发电机/轴发)+ 每设备工况点(来自已配 tab_engine_param)
- **流程**:
  1. 建 `mmha_component` 树(根设备 → 叶子部件,modelSummaryTypeCode=02 MIN 汇总;运行条件 operatingConditionEval 绑功率点)
  2. 绑 `mmha_component_device_relation`(叶子绑工况点 device_key + normal_value/min/max)
  3. 配 `mmha_component_model_info`(model_type_code=01 线性)
  4. 中英文名(component_name_en + tab_engine_param_menu.device_name_en 两套,避免英文空白坑)
  5. 验证:注入 Redis `list:ship:{id}:other` 中点值 → 1 分钟 → HGETALL health → 前端出分
- **可视化路径**:EngineHealthConfig + HealthPointConfig(mmha 接口);generateStructureTree 可 AI 生成
- **坑**:边界分在 Nacos(boundaryScore),分数范围 85~100 是正常配置不是 bug

### S5 能效系统配置

- **触发**:配置能效(信号路由/设备/油水/舱容/CII)
- **流程**(按依赖顺序):
  1. `tab_engine_param` 工况点已就绪(S2 产出)
  2. `tab_equipment`(能效设备 uuid/类型)→ 3. `tab_statistics_param`(信号路由 device_type 1/2/13/16/40/41 + group_desc 流量计格式)
  3. 油水:`tab_tank_device_menu`(menu_id=type!)+ `tab_tank_device`(tank_id 从 4 起,unit='t')+ `tab_height_cabin`(舱容,列名 cabin)
  4. CII/EEXI:`tab_cii_calculate`/DWT(param_value)/排放因子核对
- **可视化路径**:EnergyEngineParam/EnergyEffciencyDevice/OilTankParam/flowmeterParam
- **坑集合**:tank_menu_id=type、tank_id 不碰 1/2/3(防油水死循环)、unit 填 t、cabin 列名、流量计 group_desc 格式、中英文两套

### S0 配置新船(总编排)

- **触发**:一句话"新船 XXX 要配,船厂给了资料"
- **流程**:问船名/ship_id/船型/DWT/资料齐全度 → 逐项判定(要不要健康?要不要能效?)→ 按依赖顺序调 S1(船端出数)→ 闸门通过 → S2(岸端库表)→ S3(图,可选)/S4/S5(按需)→ 每步给可视化入口 + SQL 兜底 → 汇总验证清单
- **产出**:一张"这船配置进度核对表"

---

## 四、分阶段实施顺序(建议)

| 阶段 | 内容 | 理由 |
|---|---|---|
| **P1(先做)** | S4 机舱健康 + S5 能效 | 素材最厚、可直接产出、刚实战过(XT GLORY),立即能用 |
| **P2** | S2 vms_sys_db 库表配置 | 承接健康/能效的 tab_engine_param 基础,和 P1 表强相关 |
| **P3** | S1 ISCU 采集配置 | 需要补 Excel→JsonString 生成器 + MOXA 规划核对,验证闭环在船端 |
| **P4** | S3 mimic 绑定补全 | 依赖 svg skill 现状确认 + S2 完成 |
| **P5** | S0 总编排 + 更新 bwt-vms-faq | 全部就绪后串总流程,FAQ 同步新坑 |

---

## 五、待确认问题(2026-09 答复状态)

1. **skill 载体**:用户倾向 **Dify**(组内学 AI Agent,准备用 Dify 做内部知识库)。→ 通用 skill markdown 可导入 Dify 知识库/工作流;Claude 生态继续,后续可出 Dify 版。状态:**定 Dify,继续通用 markdown 写**
2. **测试船**:142(XT GLORY)✅ 可作测试环境(iscu/DB 已备份可随意动)。状态:**通过**
3. **S1 前置样板**:已给三份船厂原始清单(AMS FLOAT/INT 两 sheet、BTP 极简、LOAD 装载协议),已读懂并归档 `新船信号清单样例_XTGLORY/`。状态:**齐**
4. **可视化 bug 清表**:已排查代码,根因 = 配置页"整船 DELETE+重插、多数无事务"。风险清单见 `配置中心数据安全风险清单.md`。状态:**已定位,交付注意备份**
5. **健康/能效要不要**:有项目技术协议清单,交付人员提前查;skill 直接提问即可。状态:**S0/S4/S5 保持"先问"**
6. **S2 数据源**(tab_engine_param 量程/报警/单位/中英文):船厂清单一般有;类型靠单位/名称/AMS 标记判断;功能码段+数据类型 0-8 规则已吸收。状态:**规则齐,可指导 S1 生成器**

---

确认这份计划(尤其 P1 先做 S4/S5)后,我按 S4 开始落地;若有调整直接说。
