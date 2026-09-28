---
name: "bwt-vms-iscu-config"
description: "BWT-VMS ISCU 采集配置(S1):新船船端采集配置——MOXA串口服务器+AMS/BTP/LOAD/第三方信号清单→生成JsonString/hldkeymap→落库→通导/EIM从参照船复制→datacap重启出数验证(闸门)。含隐性规则(模拟量报警状态页不采、按当事人确认)、SpeedLog与SatelliteSpeedLog二选一优先SpeedLog、DataTypeRegister以datacap C++枚举为准(2=无符号/8=有符号,iscu字典表标反)、FC04基址30001、LocalGateway.xml字段对应。用户提到\"配采集/ISCU/iscu/信号清单/datacap/串口服务器/MOXA/JsonString/hldkeymap\"时触发。"
---

# BWT-VMS ISCU 采集配置(S1):船端出数

为一条新船配置船端采集(串口服务器→iscu 库→datacap 出数验证)。用户提到"配采集/ISCU/iscu 库/信号清单/工况点采集/datacap/串口服务器/MOXA/JsonString/hldkeymap"时触发。

**范围**:只做船端"能采到数"。结束时 vms_sys_db 不配(仍初始状态)——岸端表(tab_engine_param)是 S2,必须等本 skill 的"datacap 出数验证"通过后才做。两者强绑定(order_id↔orderkey),刻意分开防返工。

**参照船**:XT GLORY 船端 iscu 库(如 192.168.8.142, root/<设备密码>, mysql 容器 root/<MySQL密码>)。同系列新船的通导/EIM 配置直接从参照船复制(见④b),不要手工拼。

**最优先**:如果手上有**同船/同系列的实船备份**(iscu 库 + `LocalGateway.xml` + `system_config.xml` + `transplatform/config.xml`),**一切以它为准逐项对齐**,不要靠推断或记忆。

## 链路 30 秒

```
RS485 设备 → MOXA 串口服务器 → datacap(读 iscu 点表) → 按 orderkey 编码 PNG
           → TCP:5165 → transplatform(转HEX) → Kafka ship-{id}-data-org-makeup → dataCleanV2 → Redis
```
iscu 库是 datacap 的"大脑"。**配采集 = 写 iscu 库**(hldkeymap 点表 + channelsdataprotocol 通道 JsonString + deviceinfo/topicinfo + ways) + **配 `LocalGateway.xml`**(★ 通道 IP/端口的唯一来源,库里不存 IP)。

## ① 收前置资料(缺哪个问哪个)
1. **MOXA 串口规划**:哪些 RS485 设备接哪个口、波特率/校验、TCP 端口、模式(通导 NMEA 推送 / AMS Modbus 轮询)。参考样例:XT GLORY 两份 NP5650I 导出
   > ⚠️ **口述/凭记忆写的规划经常有错**(实测过:阀控位置错位、计程仪端口差 1)。**有实船 `LocalGateway.xml` 就一律以它为准。**
2. **船厂信号清单**(按实际格式选工具,见③):
   - 老格式:AMS **FLOAT sheet**(40001 起步进 2)+ **INT sheet**(45001 起步进 1)
   - 新格式:AMS 单表(40001 起**步进1** 16位整型+系数)+ 可能的"模拟量报警状态"页 + "开关量状态"页
   - BTP:序号/内容/地址/"40001 第0位"
   - LOAD(装载仪):"LCS →CCS" sheet,Modbus Address 列 + REAL32 ABCD
   - 第三方设备:供应商 docx/xls,含 Holding register 列
3. 特殊设备(EIM 倾斜仪等自定义 NMEA)**不在工具范围**——同系列船从参照船复制(④b)

### ⚠️ AMS 清单哪几张表要采——**必须问当事人,不要假设,不要当成团队规则**

**各人做法不一样**(2026-09-24 经确认)。已知至少两种做法:
- 有人三张表全采(含"模拟量报警状态"的 41001.x 段)
- 有人**不采**"模拟量报警状态"(理由:本系统不支持模拟量告警配置)
  - 例:远东吉祥实船就是采 `模拟量通讯数据` + `开关量状态` 两张

**动手前必须问本船的交付负责人/责任交付"哪几张表要采"**,并以**同船实船库**核对。

> 反面教材:远东吉祥首次按"三张全采"配了 971 点,实船库是 777 点(两张表),差 194 点。**差异本身不说明谁对——以本船实船库和负责人为准**。

## ② 配 MOXA 串口服务器
按规划设串口(波特率/数据位/校验/停止位)+ 工作模式(Modbus TCP 或 TCP Server)+ TCP 端口。现场用串口助手逐口确认能收到数据,再往下走。
⚠️ 注意区分两种模式(对应 LocalGateway 的 `net_mode`):
- **通导 = 被动接收**:MOXA 把 NMEA 推给 datacap(`net_mode=2`,datacap 监听 `0.0.0.0:端口`)
- **机舱 = 主动轮询**:datacap 作 TCP client 连 MOXA(`net_mode=4`,填 MOXA 的 IP+端口)

## ③ 生成 iscu 配置(工具)

工具位置:工作目录 `S1信号清单工具/`,另有配套新工具在 `bwt-vms-agent/tools/`:

| 清单格式 | 工具 | 参数要点 |
|---|---|---|
| 老格式 AMS/BTP/LOAD | `make_jsonstring.py` | `--ams_start 101 --btp_start 1579 --load_start 1590` |
| 新格式 AMS(16位步进1) | `tools/make_ams_jsonstring_16bit.py` | `--orderkey_start`、`--dtr_analog`、`--coeff_override` |
| 第三方 docx/xls(Holding register) | `tools/make_docx_register_jsonstring.py` | `--ranges`、`--dtr_map`、`--addr_offset` |

```bash
# 落库到船端(经 SSH,自动备份 iscu → /tmp/iscu_backup_*.sql)
python3 apply_to_iscu.py --gen_dir <输出目录> --mode ssh --host <船端IP> \
  --ssh_user root --ssh_pass '<密码>'   # mysql 容器默认 root/<MySQL密码>
```

**orderkey 规范(随船不同,以实船为准)**:
- 公共通导段固定 **-2~39**
- XT GLORY:AMS **101**-1576 | EIM 1577/1578 | BTP 1579-1589 | LOAD 1590-1601
- 远东吉祥(无 EIM/BTP/LOAD):AMS1 **51**-827 | AMS2 828-834 | AMS3 835-841 | AMS4 842-963
- ⚠️ **起点非固定**:按"本船各块不冲突"定,并**优先照抄同船实船值**

依赖:python3 + openpyxl + paramiko + xlrd + python-docx。

**必看核对报告**——工具自动查连续性/重复/单位缺失。人工核对重点:
- 单位错/缺 → 改清单单位列重跑
- 系数没给(读值偏大偏小整数倍) → ANALOG 类型的点要标系数
- 1/0 语义反 → INT/开关量 sheet 的状态列核对
- **地址差 1** → 整体平移。**FC04 基址是 30001**;第三方清单常需 **30000+清单地址**
- **系数不全是规则推的**:远东吉祥 220 个模拟量里,规则推对 211 个,9 个是**现场人工标定**的(如艏吃水 mm 量程 1000 却用 0.001)。对不上时用 `--coeff_override` 覆盖并记录

## ④a 工具落库(EngineData)
apply_to_iscu 幂等:hldkeymap 只清 AMS/BTP/LOAD(>100)重写,不动 EIM/公共段;channelsdataprotocol 按 WayUid+ChanCode 先删后插;同步 deviceinfo/topicinfo。落库后按④c 验证。

> ⚠️ **手工落库注意事务**:DELETE + INSERT 后必须 commit;任一步抛异常(如 SQL 缺库名)会整段回滚,出现"hldkeymap 有点但 channelsdataprotocol 没通道"的半截状态。**落库后务必查 `channelsdataprotocol` / `hldkeymap` / `topicinfo` 三者齐全**(远东吉祥踩过)。

## ④b 通导段 + LocalGateway.xml(工具不生成)

### ★ 通导通道数因船而异,别照搬
参照船是 9 通道(含 EIM);远东吉祥只有 8 个 `deviceinfo` 通道、7 个实际接线。**以本船实船配置/技术协议为准。**

### ★ SpeedLog 与 SatelliteSpeedLog 二选一,但都要保留
船上**可能同时存在** `SatelliteSpeedLog`(卫星计程仪)和 `SpeedLog`(速度计程仪),**本系统同时只支持一个**:

> **优先用 `SpeedLog`;没有 `SpeedLog` 时才用 `SatelliteSpeedLog`。**

→ **两个通道定义都要保留**(deviceinfo/channelsdataprotocol 里都有),**只在 `LocalGateway.xml` 里接实际用的那个**。
→ 所以"deviceinfo 有 8 个通道、XML 只接 7 个(缺 channel_code=5)"是**正常且刻意的**,**不是漏接,不要去补齐**。
→ `ways.ChannelsNum` 记的是 **XML 里实际接了几条**,不是 deviceinfo 行数。

### 要复制的项
1. **channelsdataprotocol SailingData**:每通道 DataProtocol=1,JsonString 含 `NMEA.NMEADevType`(设备前缀+000,如 GPS=1101000、AIS=1102000、GYRO=1105000、EchoSounder=1104000、SatelliteSpeedLog=1114000、SpeedLog=1107000、Anemometer=1106000、Rudder=1113000),约 208 字节
2. **EIM**(若有):DataProtocol=5,自定义解析(BeginCh `$`/EndCh `\r\n`/SplitCh `,`,语句 IMHRM→横倾角 Alias 50001、IMHPM→纵倾角 50002),orderkey 1577/1578 固定
3. **hldkeymap 公共通导段**:orderkey -2~39,41 条,每船照抄。(列名是 **desc_cn** 不是 DescriptionCn!)
4. **deviceinfo**:通导各通道(SlaveId=0, DataProto=1) + 机舱各通道(SlaveId 按实际, DataProto=2)
5. **topicinfo**:通导各 topic(TopicDesc=NULL,DataType 是 `{"键":"string"}` 形 JSON,IndexSet=键逗号串按**数值排序**) + 机舱各 topic
6. **ways**:两行(SailingData / EngineData),`ChannelNum` 按实际接线数

### LocalGateway.xml(datacap 的通道 IP/端口配置)
| XML | iscu 库 |
|---|---|
| `gateway_uid` | `ways.WayUid`(注意:**模板固定值**,如 1575875275/1575876387,不是每船新编) |
| `gateway.name` / `way_index` | `ways.Name` / `ways.WayIndex` |
| `channel_detail.channel_code` | `deviceinfo.ChanCode` |
| `channel_detail.channel_name` | `deviceinfo.TopicName` |
| `channel_detail.channel_type` | `deviceinfo.ChanType`(固定 7) |
| `ip`/`port`/`net_mode`/`reply` | **库中无,只在这里** |

- `net_mode`: **2=监听收推送**(通导,ip 填 `0.0.0.0`,reply=0)、**4=作 TCP client 主动连**(机舱,填设备 IP,reply=1)
- ⚠️ **模板出厂值(通导 10001+ 端口、机舱 `192.168.7.89:502`)是错的,别信**
- ⚠️ `way_type`:实船 XML 里 EngineData=3 但 iscu.ways 存 13,**两处不一致且原因不明——各保持原值,不要"统一"**

## ④b2 从参照船复制的操作方法(防转义坑)
- 用 `docker exec mysql sh -c 'mysqldump ... --where="..." --no-create-info --skip-extended-insert --compact'` 导出 INSERT 回放——**不要**用 `mysql -e "SELECT"` 的输出回插:批处理模式会把真换行转义成 `\n`、shell 引号嵌套会静默截断含引号的 --where(返回空!),手工拼必错(实测 208 字节存成 221)
- 回放时剥掉自增 id 避免主键冲突
- --where 内含单引号时经 docker exec sh -c 嵌套会被吃掉——务必校验导出非空

## ④c 落库验证(与参照船/实船逐项对齐)
```
channelsdataprotocol: 通导 N 条 + 机舱 M 条,条数=ways 实际通道数
hldkeymap: 总数 = 公共41 + 各通道点数;orderkey 无重复;KeyValue 通道内唯一(跨通道可重复)
topicinfo: 每个 deviceinfo 的 TopicName 都要有对应行
deviceinfo: 与 channelsdataprotocol 一一对应
ways: ChannelsNum = XML 实际接线数
LocalGateway.xml: 端口是现场值(非模板值)
★ 有实船备份时:逐表 set 比对(忽略自增id)应为完全一致
```

## ⑤ datacap 验证(闸门,S1 完成标志)
```bash
docker restart datacap   # 配置只在启动时读一次;/home/hlddatacap 下没有 stop.sh,别用
sleep 15 && docker logs datacap --since 2m 2>&1 | grep -iE "offline|error" | head
# 正常:Topic offline 仅当 MOXA 网络未通;网络通后自动上线
# firepoint 表缺失报错:参照船也有,无害噪音,忽略
redis-cli -a <Redis密码> GET "list:ship:<shipId>:other"   # 出 JSON 即链路通
```
**出数确认无误才算 S1 完成**,才允许进 S2。S2 用的 device_map.csv 必须与最终 hldkeymap 同批生成(改过 orderkey 就要重出)。

## 翻译规则速查(已对照现库定标)

| 清单 | → iscu |
|---|---|
| AMS FLOAT reg(步进2) | Alias=reg+"00",40001 段,类型 **4**(float badc),点DataLen=2 |
| AMS INT reg(步进1) | Alias=reg+"00",45001 段,类型 **1**(开关量),点DataLen=1 |
| AMS 新格式:模拟量(步进1,16位) | Addr=reg,Alias=reg+"00",**类型 8=有符号 / 2=无符号**,点DataLen=1,系数按船厂规则表 |
| AMS 新格式:开关量位 | Addr=reg.(bit+1 两位),Alias=reg+(bit+1 两位),类型0 |
| BTP "reg 第N位" | Addr=reg.(N+1)(**.01=bit0,有+1偏移!**),Alias=reg+(N+1)两位,类型0,SlaveId=3 |
| LOAD modbus_addr m | 逻辑reg=39999+m,Alias=reg+"00",类型4 |
| **FC04 第三方(流量计等)** | **逻辑地址 = 30000 + 清单地址**(清单 0168 → 30168);类型按实船(远东吉祥流量计用 **14**,超枚举,照抄);点DataLen=2 |
| 公共通导段 | orderkey -2~39 每船照抄 |
| orderkey | 全局唯一;**Alias 可跨通道重复**(两台同类设备同寄存器时必然重复),通道内唯一即可 |

**DataTypeRegister 编码——以 datacap C++ 枚举为准**(`datacap-feature/system_config.h` 的 `enum RegisterType`,真正执行的就是它):

```
0 BITS_VALUE 位点 | 1 SWITCH_VALUE 布尔 | 2 ANALOG_VALUE 2字节【无符号】
3 FLOAT_VALUE abcd | 4 SWAP_FLOAT badc | 5 DOUBLE | 6 SWAP_DOUBLE
7 REVERSE_FLOAT dcba | 8 ANALOG_SIGN 2字节【有符号】 | 9 INT_VALUE | 10 UINT_VALUE
```

> ⚠️ iscu 库里那张 `registerdatatype` 字典表**把 2/8 标反了**。**别信字典,信上面这个枚举。** 曾因误信字典把整船模拟量类型配反。
> ⚠️ 功能码基址:FC03=40001,FC04=**30001**。
> ⚠️ 枚举只到 10,但实船流量计用了 **14** —— **超出枚举的值照抄实船**。

系数 Coefficient="a,b,c" 即 a*x²+b*x+c,只对 2/8/9/10 生效,float 不套。

## 坑

| 坑 | 说明 |
|---|---|
| **AMS 采哪几张表靠猜** | 各人做法不一,必须问当事人+"以本船实船库为准" |
| **把 SatelliteSpeedLog 当漏接** | 它与 SpeedLog 二选一(优先 SpeedLog),两个定义都保留、只接一个——见④b |
| **照搬参照船的通导通道数** | 参照船 9 通道含 EIM,别的船未必有;以本船为准 |
| **模拟量只配有名点** | 要**连续区间全覆盖**(含无名备用寄存器) |
| **按 100 拆段** | 实测实船模拟量一段到底(DataLen=220)——datacap 的 `modbus_params.RegistersForSingPack=100` 会自动拆包 |
| **类型 2/8 配反** | 信 C++ 枚举,别信 iscu 的 registerdatatype 字典表 |
| **FC04 用了 4xxxx** | FC04 基址 30001;第三方清单地址常需 30000+地址 |
| **系数以为全靠规则** | 约 5% 的点是现场人工标定的,对不上就覆盖并记录 |
| **手工落库事务回滚** | 一条 SQL 报错会整段回滚,出现"有点无通道"半截状态;落库后三者对齐必查 |
| **BTP 自动衔接占 1577** | make_jsonstring 默认把 BTP 排到 AMS 结束+1,抢占 EIM 位 → **必须 --btp_start 1579 --load_start 1590** |
| **LocalGateway.xml 模板值** | 通道 IP/端口唯一真源在 XML;模板值是错的 |
| **口述的串口规划有误** | 实测过阀控位置错位、计程仪端口差 1;有实船文件就一律以文件为准 |
| **JsonString 手工插库** | mysql -e 批处理输出转义换行,手工回插必错;用 mysqldump 导 INSERT 回放 |
| **docker exec 嵌套引号** | sh -c 内 --where 带单引号会被吃,静默返回空——务必校验非空 |
| **JsonString 格式不必逐字节对齐** | 实船各通道缩进/换行本身就不统一;datacap 按 JSON 解析,**验收以语义一致为准** |
| iscu 里 ShipId/ways 原有字段 | 保持原样不动(ShipId=1) |
| 重启才生效 | datacap 启动时读一次库,改配置必重启 |
| VM 时间偏 8 小时 | 船端 VM 时间常快 8h,影响时间戳,验收前修(见 FAQ) |
| firepoint 表缺失 | 参照船也报,无害,忽略 |
| **船端不存名称** | 实船 iscu.hldkeymap 的 desc_cn 是 `'--'`、unit 空;**中文名在岸端** `vms_sys_db.tab_engine_param.device_name` |
| **岸端 device_key 规则** | AMS/阀控/通导 = 船端 KeyValue 原样;但**两台同类设备(如双流量计)船端别名相同,岸端要另编 key 区分** |

## 验证清单(S1 完成 = 全勾)

- [ ] MOXA 每口串口助手出数;新船服务器能路由到 MOXA IP
- [ ] **已问过当事人"AMS 清单哪几张表要采"**
- [ ] 若有同船实船备份 → iscu 五表 + 三个 XML 已逐项对齐
- [ ] 通导通道按实船(含 SpeedLog/SatelliteSpeedLog 二选一、都保留)
- [ ] 生成工具用了正确起点(实船值优先)
- [ ] 落库后 ④c 全项校验通过
- [ ] `LocalGateway.xml` 端口是现场值(非模板)
- [ ] datacap 重启后日志无配置类报错
- [ ] ★闸门:岸端 Redis list:ship:{id}:other 出 JSON
- [ ] device_map.csv(与最终 orderkey 同批)交付给 S2

