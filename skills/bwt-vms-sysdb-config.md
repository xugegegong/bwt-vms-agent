---
name: "bwt-vms-sysdb-config"
description: "BWT-VMS vms_sys_db 岸端工况点库配置(S2):整船批量(make_engine_param.py)与单点增删改(代码级全表影响)。含device_type从清单报警否推导、菜单归类是业务判定(关键词仅65%)、action_path是前端路由、两套菜单约定(uuid/shipseq)、流量计岸端key另编、通导与业务点字段规则。用户提到\"配工况点库/tab_engine_param/新增工况点/修改工况点/删除工况点\"时触发。"
---

# BWT-VMS vms_sys_db 库表配置(S2):岸端工况点库

S1(ISCU 采集)完成后,把采集点同步成岸端工况点库,使岸端能解码展示。用户提到"配工况点库/tab_engine_param/工况点配置/岸端配置/S2/新增工况点/修改工况点/删除工况点"时触发。

**前置**:S1 完成,拿到 `device_map.csv`。**order_id 必须与 iscu.hldkeymap.orderkey 一一对应**(硬约束)。

**最优先**:有**同船实船备份**时,一切以它为准逐项比对(五张表 + 三个 XML),别靠推断或记忆。

## 数据模型(30 秒)

```
tab_engine_param               每采集点一行
  device_key   通导=110xxxx / AMS=别名(4000100) / 流量计=岸端另编(30016800)
  order_id     = iscu.hldkeymap.orderkey  ★硬约束
  device_id    uuid32hex(后端生成)
  device_type  见下表(★ 从清单元数据推,不是猜的)
  device_name_en / attention_level / is_cycle / min_value / max_value  见"字段规则"
  device_father_id  仅 shipseq 约定下设为菜单的 device_id
tab_engine_param_menu          分类(menu_type 1=监控/2=健康)
tab_engine_param_relationship  menu.device_id ↔ param.device_id(不挂=前端看不到)
tab_engine_state_quantity_cfg  开关量(type=2)状态样式
tab_statistics_param           信号路由(能效)
```

## ★ 字段规则(远东吉祥实测,954 点 × 8 字段全对齐)

### device_type —— 从清单元数据推导,已验证 535/535 全中
| 类别 | device_type |
|---|---|
| **通导(公共导航段)** | **NULL**(且 `attention_level=1`、`is_cycle=0`、**`device_name_en`=中文名**) |
| AMS 模拟量 | **1**(数值) |
| AMS 开关量位 | 清单「报警否」=**否→2**(开关量)、=**是→0**(报警);无名位→0 |
| 流量计 | 1 |
| 阀控 | 阀位段→1(量程 0~100);报警段→0 |

**业务点**(通导以外的全部):`attention_level=NULL`、`is_cycle=NULL`、`device_name_en` 留空。

### 量程 min/max —— 清单只是参考,现场会标定修正
实测 15 个点与清单不符:压力类对称化([0,10]→[-10,10])、电流按实际额定([0,300]→[0,600])、
个别清单值明显有误(齿轮箱滑油压力[0,1]→[-10,10])。**以实船/现场值为准,工具支持覆盖。**

## ★ 菜单归类是业务判定,算法推不出来
关键词规则实测**命中率仅 64.6%**(913 点)。反例:「油泥过滤器前压力」归**主机**、
「DG1频率」归**配电板**、「1#液货深井泵压力」归**液位**。
→ **正式交付必须用交付团队给的菜单规划**(工具 `--assign_csv` 支持显式归类),别靠关键词自动归类。

## ★ action_path 是前端路由,不是 identification 的机械变形
实船实例:`Gearbox`→`/engineMonitor/gearbox`(小写)、`CabinLevel`→`/engineMonitor/cabinLevel`、
`BallastWaterPipingSystem`→`/engineMonitor/WB`、`LiquidCargoAndTankSweepingPipeSystem`→`/engineMonitor/CT`、
`DeckSprinklerSystem`→`/engineMonitor/DF`、`OTHER`→`/engineMonitor/other`
→ **菜单规划里必须显式给出**(工具优先读规划里的 `action_path`)。

## ★ 两套菜单约定(不同船不同,别混用)
| | XT GLORY(35701001) | 远东吉祥(31002001) |
|---|---|---|
| menu.device_id | 随机 uuid32hex | **`{ship_id}{序号:04d}`**(总览=0000) |
| param.device_father_id | 不设(NULL) | **设为该菜单 device_id** |
| menu_id | `{ship_id}{两位:02d}`(总览=00) | 同 |
| resource_sort | 流水 '0','1'… | **`10202{key:02d}00`**(总览=102020100) |
| key | 0,1,2… | **1,2,3…**(总览=1) |
| svg_old_new | — | 总览=0,分类=2 |

工具:`make_engine_param.py --menu_scheme {uuid|shipseq}`

## 流量计岸端 device_key —— 两台表同寄存器,岸端另编
```
shore_key = "300" + f"{寄存器 - 30000 + 表号:03d}" + "00"     # NO.1表号0, NO.2表号1
例: 船端两台都是 3016800 → NO.1=30016800 / NO.2=30016900
```
命名 `NO.1流量计质量流量` / `NO.2流量计xxx`(**不是重油/轻油**)。
其余类别岸端 device_key = **船端 hldkeymap.KeyValue 原样**。

## 单点增删改(高频!必须按《工况点增删改全表影响手册.md》回答)

**新增**:param INSERT + relationship INSERT(必挂菜单)+ type=2 另配 state_cfg + 参与能效另配 statistics_param。
**修改**:⚠️ 后端 order_id/device_id 锁死不可改;7 个量程列无条件覆盖(可视化漏传=写NULL,改单列用手工UPDATE);每次保存都会删插 statistics_param/state_cfg/relationship(无事务,中断即脏);对非 type=2 调 more/save 会静默删状态映射。
**删除**:四表级联 relationship→statistics_param→state_cfg→param(物理删);mimic 绑定与船端采集不级联。
**遗留表不用动**:tab_md_orderkey(废弃勿写)/tab_navigation_param(仅'--'补位)/tab_alg_para+tab_alg_script(死表)。
**能效静默依赖**:tab_ship_info_extend paramId 27主机额定功率/28额定转速/70发电机额定/107轴发额定,缺失=负载率/CII 不出数(值列 param_value)。
完整列级 SQL/分类型注意事项/可视化操作对照表/12条坑 → 见《工况点增删改全表影响手册.md》。

## 整船批量流程

① 菜单规划(★ 由交付团队给,含 menu_name/en/identification/action_path/keywords)
② 造 `device_map.csv`:`device_key,order_id,cn,en,unit,dev_type_suggest[,min_value,max_value]`
   —— device_key/order_id 来自 iscu;名称/量程/类型来自船厂清单(device_type 按上面规则推)
③ 核对:与实船(若有)逐点比对 device_key/名称/英文名/类型/单位/量程
④ 生成 + 落库:
   ```bash
   python3 make_engine_param.py --device_map ... --menu_plan ... [--assign_csv ...] \
       --menu_scheme {uuid|shipseq} --ship_id <id> --out engine_param.sql
   ```
   落库前备份三张表;SQL 自带 DELETE 本船幂等(**旧船人工补的量程会被清!**)
   ⚠️ **执行生成的 SQL 时,别按 `--` 过滤整块**——会把紧跟在注释后的第一条语句
   (如 `SET @ship=…`)一起丢掉,导致 `@ship` 为 NULL、**所有行插成 ship_id=NULL**。
   **正确做法:按 `;` 切分后逐条剥掉行内注释行再执行。**
⑤ 验证:order_id 与 iscu 对齐=0 差异;954 点各字段与实船比对;前端菜单出树;实时值正常

## 坑

| 坑 | 说明 |
|---|---|
| **靠关键词归类菜单** | 命中率仅 65%,菜单归类是业务判定,必须用交付给的规划 |
| **action_path 机械拼** | 不是 `/engineMonitor/<identification>`,有短码(WB/CT/DF)和大小写差异,要规划给 |
| **菜单约定混用** | XT GLORY 用 uuid、远东吉祥用 shipseq;混了关系表就挂不上 |
| **总览菜单重复** | 工具会自动生成"所有工况总览",菜单规划里不要再列 |
| **menu 序号从 0 起** | 总览占序号 0,分类从 1 起;算错会整体错位 |
| **清单笔误没发现** | 实测 41129 bit1 误写成"舵机1#电源指示"(与 bit0 重复),实船改成了 2#。**名称对不上先怀疑清单** |
| **量程照抄清单** | 清单只是参考,现场会标定修正(压力对称化/电流按额定) |
| **通导与业务点字段规则相反** | 通导:英文名=中文名、attention=1、cycle=0;业务点:英文名空、两者 NULL |
| **流量计岸端 key 照抄船端** | 两台表船端别名相同,岸端必须另编(300xxx00 加表号) |
| **落库 SQL 丢 SET 语句** | 见上,按 `;` 切分逐条剥注释,别整块过滤 |
| **order_id 对不上** | 硬约束,必须 = iscu orderkey;实船某些版本表里甚至没有 order_id 列,注意版本差异 |

## 验证清单(S2 完成 = 全勾)

- [ ] device_map 的 device_key/order_id 来自 S1 最终 hldkeymap(同批)
- [ ] 菜单规划由交付团队确认(含 action_path)
- [ ] device_type 按规则推导并抽查(开关量易被判错)
- [ ] 落库前三张表已备份
- [ ] order_id ↔ iscu orderkey 差异 = 0
- [ ] 与实船逐点比对 点数/名称/英文名/类型/单位/量程 = 0 差异(有实船时)
- [ ] 菜单 16 行、关系 913 行与实船一致(有实船时)
- [ ] 前端机舱监控菜单出树、实时值正常
- [ ] device_map.csv 存档给 S4/S5

