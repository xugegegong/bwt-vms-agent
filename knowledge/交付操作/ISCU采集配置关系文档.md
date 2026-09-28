# ISCU 采集配置关系文档(基于 192.168.8.142 实测)

> 更新日期:2026-09-10(补充 4# 客户端四表联动与全部工况点类型定标;修改本文档后请同步更新此日期,并到 Dify 知识库重新上传该文件)

> 目的:理清船端采集程序(HldDataCap)与 iscu 库、XML 配置文件、岸端工况点表(tab_engine_param)之间的完整对应关系,作为"配置新船采集"的知识基础。
> 环境:192.168.8.142(test.hbluewhale.com),船 = XT GLORY(IMO 1115395 / MMSI 563338500 / ship_id 35701001)。

---

## 1. 一句话全景

**船端传感器/AMS(RS485)→ MOXA 串口服务器(转 TCP)→ datacap(HldDataCap,读 iscu 点表采值)→ 按 orderkey 编码 PNG → 岸端 transplatform(转 HEX)→ Kafka → dataCleanV2 按 order_id 解码 → Redis/MySQL**。

`iscu` 库就是 datacap 的"大脑",告诉它:有哪些网关/通道、每个通道用什么协议从哪读、读到的每个点叫什么名、按什么顺序塞进 PNG。**配新船采集 = 往 iscu 库写这一整套对应关系**。
> 源码均已本地化:datacap = `datacap-feature/`(采集+PNG 编码),transplatform = `trans_platform-feature/`(转发网关)。见 9.1。

---

## 2. 物理部署

```
192.168.8.142(test.hbluewhale.com)
├── /home/hlddatacap/            采集程序运行目录(源码在本地仓库 datacap-feature/)
│   ├── HldDataCap               C++ 可执行(编译产物,源码见 datacap-feature/)
│   ├── start.sh                 启动脚本(kill 旧进程 + LD_LIBRARY_PATH 启动)
│   ├── lib/                     boost / libhld_cbb.so / libHldDDSLib.so 等动态库
│   ├── config/
│   │   ├── LocalGateway.xml     网关+通道定义(与 deviceinfo/ways 表对应)
│   │   ├── system_config.xml    全局配置:连 iscu 库、船信息、PNG 参数、Kafka
│   │   ├── flowmeterconfig.xml  流量计 Modbus 网关列表(当前 num=0 未启用)
│   │   ├── pldconfig.xml        配电板(?)协议配置
│   │   ├── ConfigNMEA.xml       NMEA 相关配置
│   │   ├── storyconfig.xml
│   │   └── system_config_patten.xml / *.bak
│   ├── logs/                    运行日志(HldDataCap.log)
│   └── Dockerfile / deploy.sh   容器化部署用
└── docker 容器
    ├── mysql      0.0.0.0:13306→3306(mysql:5.7,含 iscu / vms_sys_db 等库)
    ├── redis      0.0.0.0:16379→6379
    ├── kafka
    ├── datacap / datacleanv2 / transplatform / mmha / websocket / ...
```

**注意**:`system_config.xml` 里数据库写的是 `192.168.1.101:13306`(应为历史内网 IP),实际 142 本机 `0.0.0.0:13306` 是 docker-proxy 映射,采集程序连的是本机 13306 的 mysql 容器。***（数据库从现场1.101服务器上直接拷贝的目录，可忽略此IP，由于我们测试环境没有采集程序接入所以不用修改后端即可使用，把他当成192.168.8.142是一样的）***

---

## 3. 配置文件与数据库表对应关系

### 3.1 system_config.xml(全局配置)

```xml
<system_config>
  <version>0</version>                       <!-- other 0, pld 1 -->
  <database_info> ip=192.168.1.101 port=13306 dbname=iscu </database_info>  <!-- 连 iscu 库 -->
  <ship_info> IMO=1115395 MMSI=563338500 other_ship_max=50 ... </ship_info>
  <png_config>
    MonitorRate=2  MonitorTimeout=20  width=120  height=120
    orderkey_version=1.0  png_version=1.0
    <clients> subfolder type=2 ip=192.168.1.101 serverport=5165 </clients>  <!-- PNG 发给岸端 TCP 5165 -->
    <Fire enable=1 fire_type=2/>
    <PNGSave enable=0/>
  </png_config>
  <PT100> coefficient=0.0012,2.3036,-242.94 </PT100>
  <kafka> brokerlist=192.168.1.101:9092
          icomintopic=ship-35701001-accept-data
          icomouttopic=ship-35701001-send-data
          algorithmtopic=behavior_detection </kafka>
</system_config>
```

**要点**:数据库名固定 `iscu`;PNG 参数(尺寸 120×120、orderkey 版本);采集数据既走 Kafka 也 TCP 发给岸端 5165。

### 3.2 LocalGateway.xml(网关/通道)

```xml
<gateway> gateway_uid=1575875275  name=SailingData  way_index=11  way_type=3  channel_num=7(实际8) </gateway>
  channel 1:4007 GPS | 2:4001 AIS | 3:4003 GYRO | 4:4005 EchoSounder
  channel 5:4002 SpeedLog | 6:4004 Anemometer | 7:4006 Rudder | 8:5007 FM
<gateway> gateway_uid=1575876387  name=EngineData  way_index=13  way_type=3 </gateway>
  channel 1:5001 AMS(net_mode=4=TCP 主动连 192.168.1.234) | 2:5003 LOAD | 3:5005 BTP
```

**对应表**:
| XML | iscu 表 | 关联键 |
|---|---|---|
| `<gateway gateway_uid>` | `ways.WayUid` | gateway_uid |
| `<gateway name>` | `ways.Name` | |
| `<way_index>` | `ways.WayIndex` | |
| `<channel_detail channel_code>` | `deviceinfo.ChanCode` | channel_code |
| `<channel_detail channel_name>` | `deviceinfo.TopicName` | 通道主题名 |
| (通道级联的从站) | `deviceinfo.SlaveId` | AMS=1, BTP=3 |

---

## 4. iscu 库 8 张表详解与关联

### 4.1 表清单(实测数据量)

| 表 | 行数 | 作用 |
|---|---|---|
| `ways` | 2 | 网关定义(SailingData/EngineData) |
| `deviceinfo` | 12 | 通道→主题映射(含从站号、协议) |
| `channelsdataprotocol` | 12 | **通道协议点表(JsonString)**,采集核心 |
| `hldkeymap` | 1542 | **全局点表**(PNG 编码顺序 + 变量号 + 中文名) |
| `topicinfo` | 12 | 主题变量集合(IndexSet) |
| `dict` | 221 | 变量号编码体系字典树 |
| `registerdatatype` | 9 | Modbus 数据类型字典 |
| 另库 `iscu-13k` | ? | 疑似另一船/模板,待确认（***之前备份库不用管***） |

### 4.2 ways —— 网关

| 列 | 例 | 说明 |
|---|---|---|
| ShipId | 1 | 船号(iscu 内编号,非 35701001!) |
| WayUid | 1575875275 | 网关唯一ID = LocalGateway.xml gateway_uid |
| IP/ServerIP | (空) | |
| WayIndex | 11/13 | 对应 XML way_index |
| WayType | 3 | |
| ChannelsNum | 9/3 | 通道数 |

***此表不用理会，不需要修改保持原样即可***

### 4.3 deviceinfo —— 通道主题映射

| 列 | 例(SailingData) | 例(EngineData) | 说明 |
|---|---|---|---|
| GatewayId | 1575875275 | 1575876387 | = ways.WayUid |
| ChanType | 7 | 7 | 通道类型 |
| ChanCode | 1..9 | 1=AMS 2=LOAD 3=BTP | = XML channel_code |
| TopicName | GPS/AIS/.../EIM | AMS/LOAD/BTP | 逻辑主题名 |
| SlaveId | 0 | 1/1/3 | Modbus 从站号(NMEA 无=0) |
| DataProto | 1/5 | 2 | 协议:1=NMEA 2=ModbusRTU 5=自定义NMEA |

### 4.4 channelsdataprotocol —— 通道点表(采集核心!)

| 列 | 说明 |
|---|---|
| ShipId | 1***（无需理会，保持1）*** |
| WayUid | 网关 UID***（无需理会，保持）*** |
| ChanCode | 通道号(1..9) |
| DataProtocol | 协议编号 |
| JsonString | **该通道完整的采集点表(JSON)**,AMS 通道达 362KB |

**JsonString 结构(Modbus RTU 通道 AMS 示例)**:
```json
{
  "ChanCode": "1", "ChanDesc": "", "ChanType": "7", "DataProtocol": "2", "WayUid": "1575876387",
  "RTU": [
    {
      "DevName": "1301",          // 从站/设备名(AMS 都叫 1301)
      "FunCode": "3",             // Modbus 功能码:03=读保持寄存器
      "RegisterBegin": "40001",   // 起始 PLC 寄存器地址
      "DataLen": "1021",          // 该段读取长度
      "Results": [
        { "Addr": "40001",        // PLC 寄存器地址(逻辑)
          "Alias": "4000100",     // 变量号 = hldkeymap.KeyValue(重要!)
          "Coefficient": "0,1,0", // 系数(偏移,倍率,?)
          "DataLen": "2",         // 占寄存器个数
          "DataTypeRegister": "4" // 数据类型编号 → registerdatatype
        }, ...
      ]
    },
    { "DevName": "1301", "FunCode": "3", "RegisterBegin": "45001", "DataLen": "965",
      "Results": [ 965 个点, DataTypeRegister 多为 1(布尔开关量) ] }
  ]
}
```

**工况点类型权威映射(2026-09-10 在 7.61 用 client-4 全类型实测)**:

| 工具选项 | DataProtocol 场景 | DataTypeRegister | 点步进 | 点DataLen | 结果 |
|---|---|---:|---:|---:|---|
| 数字量 / 数字量# | TCP=6,RTU=2 | 0 | 16位点/1寄存器 | 1 | Addr=`40001.01`~`.16`,Alias=`4000101`~`4000116`;bit0→`.01`,+1偏移 |
| 开关量 | TCP/RTU | 1 | 1寄存器/点 | 1 | Addr=40001~40010,Alias=4000100~4001000(步进100) |
| 模拟量(无符号) | TCP/RTU | 2 | 1寄存器/点 | 1 | uint16 |
| 单浮点数(abcd) | TCP/RTU | 3 | 2寄存器/点 | 2 | float abcd;40001~40010 只能生成5点 |
| 单浮点数(badc) | TCP/RTU | 4 | 2寄存器/点 | 2 | float badc,AMS 常用 |
| 单浮点数(dcba) | TCP/RTU | 7 | 2寄存器/点 | 2 | float dcba |
| 模拟量(有符号) | TCP/RTU | 8 | 1寄存器/点 | 1 | int16;32767置空逻辑在 datacap 解析层,配置中无额外字段 |

固定生成规律:`Coefficient="a,b,c"` 表示 `a*x^2+b*x+c`,系数1为`"0,1,0"`;段`DataLen=结束地址-起始地址+1`;TCP 用 `NetModTcp[]`,RTU 用 `RTU[]`。

**client-4 及以后的新增客户端坑(实测)**:初始化模板只有 AMS1-AMS3。第一次给 client-4 入库时,工具只写 `hldkeymap`,不自动补 `channelsdataprotocol/deviceinfo/topicinfo`,会造成“有点无协议”的孤儿点。必须先按 AMS1-3 规则补齐四表,后续同 Topic 覆盖入库才会正常。正确联动如下:

| 表 | client-4 正确行 |
|---|---|
| channelsdataprotocol | `ShipId=1/WayUid=1575876387/ChanType=7/ChanCode=4/DataProtocol=6(TCP)或2(RTU)/ChanDesc=''`,JsonString=本地 `ModbusTCP_4.json` 原文 |
| deviceinfo | `GatewayId=1575876387/ChanType=7/ChanCode=4/TopicName=AMS4/SlaveId=1/DataProto=6或2` |
| topicinfo | `TopicName=AMS4`,IndexSet=本地 `ModbusTCP_4.txt` 原文;TopicDesc 不自动更新 |
| hldkeymap | 按生成点数整体替换 AMS4 旧点,orderkey 接排且全量校验 |

注意:被工具按 ChanCode 读取配置时,同 ChanCode 不能留下 DP1 模板行干扰真实行;JsonString/IndexSet 落库后要用 MD5 与本地文件比对。工具的“工况点描述”不会更新 `topicinfo.TopicDesc` 与 `hldkeymap.desc_cn`,展示名称主要在 S2 `tab_engine_param` 补齐。

**AMS 通道实测**:2 个 RTU 段,共 **1476 个点** = hldkeymap 里 TopicName='AMS' 的 1476 行,完全吻合!
- 段1:40001 起,511 点,类型 4(单浮点 badc)—— 模拟量(温度/压力/转速等)
- 段2:45001 起,965 点,类型 1(布尔)—— 开关/报警量(对应 XT GLORY 的 45xxx 报警点)

**自定义 NMEA 通道(EIM,DataProtocol=5)**:
```json
{ "ChanCode": "9", "DataProtocol": "5", "Custom": {
    "BeginCh": "$", "DevName": "EIM", "EndCh": "\\r\\n",
    "DataCh": [{"SplitCh": ","}],
    "Results": [
      {"Addr": "1", "Alias": "50001", "DataDesc": "横倾角", "DataType": "float", "SentenceName": "IMHRM"},
      {"Addr": "1", "Alias": "50002", "DataDesc": "纵倾角", "DataType": "float", "SentenceName": "IMHPM"}
    ]}}
```
即从 `$IMHRM,..,..` / `$IMHPM,..` NMEA 句子逗号分列解析,映射到变量号 50001/50002。

### 4.5 hldkeymap —— 全局点表(PNG 编码顺序,最核心)

| 列 | 说明 | 例 |
|---|---|---|
| orderkey | **PNG 编码顺序索引**(全船唯一,负值为本船计算量) | 611 |
| KeyValue | 变量号 = channelsdataprotocol 的 Alias | 4102100 |
| desc_cn | 中文描述 | "9103 轴功率/指示" |
| unit | 单位(此船多为空) | NULL |
| TopicName | 所属主题 | AMS |

**数据分布**(XT GLORY):
| TopicName | 行数 | orderkey 范围 |
|---|---|---|
| (NULL/本船AIS) | 10 | -2 ~ 8(AIS_OWN_* 及 DEST/ETA) |
| GPS/AIS/回波/罗经/风速/计程仪/舵 | ~40 | 9 ~ 39 |
| **AMS** | **1476** | **101 ~ 1576** |
| EIM | 2 | 1577 ~ 1578 |
| BTP | 11 | 1579 ~ 1589 |
| LOAD | 12 | 1590 ~ 1601 |

> 注:orderkey 101 之前是导航段(-2~39),AMS 从 101 开始;中间 40~100 可能留给扩展。orderkey 尾部(1577+)放 EIM/BTP/LOAD。

### 4.6 topicinfo —— 主题变量集合

| 列 | 说明 |
|---|---|
| TopicName | GPS/AMS/... |
| IndexSet | 该主题下所有变量号(KeyValue)的逗号串,AMS 有 1476 个 |

IndexSet 应 = hldkeymap 该 TopicName 全部 KeyValue(顺序即采集/编码顺序)。

### 4.7 dict —— 变量号编码体系字典(树)

```
1000000 DataProtocol(根)
├── 1100000 NMEA
│   ├── 1101000 GPS ├ 1102000 AIS ├ 1103000 BowSide ├ 1104000 EchoSounder
│   ├── 1105000 GYRO ├ 1106000 Anemometer ├ 1107000 SpeedLog ├ 1108000 Rudder
│   ├── 1109000 MainEngine ├ 1110000 Rudder(舵角反馈) ├ 1111000 气象仪
│   ├── 1113000 Autopilot └ 1114000 SatelliteSpeedLog
├── 1200000 Modbus_RTU
├── 1300000 模拟量
├── 1400000 开关量
├── 1500000 自定义
└── 1600000 NMEA450
```

**编码规则**:变量号前 4 位 = 设备组(如 1101=GPS、4001=AMS 40001 寄存器、4501=AMS 45001 段、5000=自定义 EIM)。**4xxxxx 区间 = AMS Modbus 保持寄存器(Alias 尾接 00);45xxxxx = AMS 45001 起寄存器段**。

### 4.8 registerdatatype —— 数据类型字典

| type_code | 含义 |
|---|---|
| 0 | 数字量/信号量/开关量/报警量 |
| 1 | 布尔类型 |
| 2 | 有符号模拟量 |
| 3 | 单浮点 abcd |
| 4 | **单浮点 badc(AMS 模拟量用)** |
| 5/6 | 双浮点 abcdefgh / hgfedcba |
| 7 | 单浮点 dcba |
| 8 | 无符号模拟量 |

---

## 5. 核心关联:iscu ↔ 岸端 tab_engine_param

**这是配船最关键的一条线**:采集变量最终要靠岸端 `vms_sys_db.tab_engine_param` 才能解码展示。

| iscu.hldkeymap | vms_sys_db.tab_engine_param | 关系 |
|---|---|---|
| `orderkey` | `order_id` | **相等**(PNG 解码顺序) |
| `KeyValue`(如 4000100) | `device_key`(如 40001) | KeyValue 去尾 "00" = device_key(≈寄存器地址) |
| `desc_cn` | `device_name` | 相等(如 "9103 轴功率/指示") |
| — | `device_type` | 0=报警 1=数值 2=开关(channelsdataprotocol DataTypeRegister 映射过来) |

**实测验证**(船 35701001,共 1542 条,一一对应):

```
device_key=40001   ↔ hldkeymap.KeyValue=4000100  ↔ order_id=101  ↔ desc "1703 主机增压器转速/指示,高报警"
device_key=40257   ↔ KeyValue=4025700            ↔ order_id=229  ↔ "9203 主机燃油进口流量/指示"
device_key=41017   ↔ KeyValue=4101700            ↔ order_id=609  ↔ "9101 轴转速/指示"
device_key=41021   ↔ KeyValue=4102100            ↔ order_id=611  ↔ "9103 轴功率/指示"
device_key=45189   ↔ KeyValue=4518900            ↔ order_id=800  ↔ "2423 SG水冷柜综合报警"
```
**结论**:`tab_engine_param` 是 `hldkeymap` 的岸端业务投影(加了量程/报警/unit/菜单树/设备类型等业务字段)。改 iscu 采集点必须同步改 tab_engine_param,否则岸端解码对不上。

> ⚠️ 需确认:device_key 是否**一定等于** Alias 去掉尾 "00"?对 BTP 的 `Addr=40001.01 → Alias=4000101` 这类带小数的点,device_key 是多少?(见"待确认问题"。)  
>
> ***答：不需要，只需保证tab_engine_param的order_id和hldkeymap.orderkey一一对应即可，之前老版本有一个表为tab_md_orderkey，他的order_id和hldkeymap.orderkey对应，tab_md_orderkey.key_name和 tab_engine_param.device_key对应，后面废弃掉了tab_md_orderkey表，在tab_engine_param表中添加了order_id字段。***
>
> ***tab_md_orderkey表结构：***
>
> ***CREATE TABLE `tab_md_orderkey` (***
>   ***`id` int(11) NOT NULL AUTO_INCREMENT COMMENT '主键',***
>   ***`ship_id` int(11) DEFAULT NULL COMMENT '船舶ID',***
>   ***`order_id` int(11) DEFAULT NULL COMMENT '映射编号',***
>   ***`key_name` varchar(225) DEFAULT NULL COMMENT '通道号',***
>   ***`desc_cn` varchar(225) DEFAULT NULL COMMENT '中文描述',***
>   ***`unit` varchar(225) DEFAULT NULL COMMENT '单位',***
>   ***`topic_name` varchar(225) DEFAULT NULL COMMENT '主题名称',***
>   ***`create_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,***
>   ***PRIMARY KEY (`id`) USING BTREE***
> ***) ENGINE=InnoDB DEFAULT CHARSET=utf8 ROW_FORMAT=DYNAMIC COMMENT='png解析对应表';***

---

## 6. 完整数据流(端到端)

```mermaid
flowchart LR
    subgraph 船端
        S[AMS/装载/侧推<br/>RS485 Modbus RTU]
        N[NMEA 通导<br/>GPS/AIS/GYRO/回波/计程仪/风速/舵 + EIM]
        M[MOXA 串口服务器<br/>1.233 通导 4001-4007 / 1.234 AMS 5001/5003/5005 EIM 5007]
        C[datacap HldDataCap<br/>船端采集 + PNG 编码]
        DB[(iscu 库<br/>ways/deviceinfo/channelsdataprotocol<br/>hldkeymap/topicinfo)]
        X[LocalGateway.xml<br/>system_config.xml]
        S -->|RS485| M
        N -->|RS485| M
        M -->|TCP| C
        DB -->|读点表| C
        X --> C
        C -->|编码 PNG 按 orderkey| P[(120x120 PNG)]
    end
    subgraph 岸端
        T[transplatform<br/>转发网关:TCP 5165 收 PNG → HEX]
        K[Kafka<br/>ship-{id}-data-org-makeup]
        D[dataCleanV2<br/>PNG 解码<br/>按 order_id 查 device_key]
        E[(vms_sys_db.tab_engine_param<br/>device_key/order_id/量程)]
        R[(Redis 实时值<br/>list:ship:35701001:other)]
        P -->|TCP/QUIC client :5165| T
        T --> K
        K --> D
        E --> D
        D --> R
    end
```

**一句话**:船端传感器经 MOXA 串口服务器转 TCP 进 **datacap(HldDataCap)**;datacap 读 iscu 库点表从 AMS/NMEA 采集,按 `hldkeymap.orderkey` 顺序编码 PNG,作为 TCP 客户端发给岸端 **transplatform**;transplatform 把 PNG 转 HEX 发 Kafka;dataCleanV2 消费后按像素顺序(对应 `order_id`)查 `tab_engine_param` 得 `device_key`,解码写 Redis。

> ⚠️ 本节为修正版(2026-08 读源码后);旧版把 HldDataCap 当黑盒、且误以为 transplatform 编码 PNG,已废弃。详见 9.1。

---

## 7. "配新船 ISCU"要做的事(草案,待细化)

1. **拿船厂资料**:AMS Modbus 点表(从站/寄存器/类型/量程/中文名)、NMEA 句子清单、负载/吃水/轴发等附加点表。
2. **配 ways*****（不用配置）***:新增网关行(如有独立网关);一般沿用 SailingData/EngineData 结构。
3. **配 deviceinfo**:给每个通道定ChanCode、TopicName、SlaveId、DataProto。
4. **配 channelsdataprotocol.JsonString**(最重):
   - 按 AMS 从站/寄存器连续段组织 RTU 段(DevName/FunCode/RegisterBegin/DataLen)
   - 逐点写 Results:Addr(寄存器)→ Alias(变量号)→ 数据类型 → 系数
   - 模拟量/开关量分开段(参考 AMS 40001 段 + 45001 段)
   - NMEA/自定义通道按句子配置
5. **同步 hldkeymap**:把新船所有 Alias 汇总,分配 orderkey(导航段/AMS 段/附加段),写 desc_cn/unit/TopicName。**orderkey 从 101 开始留导航段**。
6. **配 topicinfo**:每个 Topic 的 IndexSet = 该主题 KeyValue 集合。
7. **同步岸端 tab_engine_param**:device_key/order_id/device_name/device_type/量程/unit/菜单树(挂 tab_engine_param_menu)。
8. **重启 HldDataCap** → 看日志是否按新配置采集 → 岸端确认 Redis 有值。

> ⚠️ 关键疑问:这个 iscu 库的 ship_id=1 如何与岸端 ship_id=35701001 绑定?同一台 iscu 能否配多条船(是否每条船一套 iscu 库,像 iscu-13k 那样)?见待确认问题。
>
> ***答：ship_id=1 不用管保持1即可***

---

## 8. 待确认问题(请用户纠正/补充)

1. **iscu-13k 库是什么**?另一条船的采集配置模板?ship_id 命名(13k≈13000 DWT?) 

   ***备份数据库不用理会***

2. **iscu.ShipId=1** 与岸端 **ship_id=35701001** 的映射在哪?采集程序一次只服务一条船,还是多船?

​	***iscu里面的ShipId保持1即可  不用理会***

1. **BTP 位点**(Addr=40001.01, Alias=4000101)在 tab_engine_param 里的 device_key 存什么(40001?还是别的)?BTP 11 个点与 45241 这类开关量如何对应?

​	***Alias=4000101在 tab_engine_param 里的 device_key 存4000101，只要和本列没有重复即可，上面已经说了这个表的对应关系。***

1. **hldkeymap.TopicName=NULL 的 10 行**(orderkey -2~8, AIS_OWN_*)由谁生成?采集程序自算/合并?

   ***这个表中的orderkey值-2到39数据需要和目前保持一致，每条船都一样。***

2. **device_key 是否恒 = Alias 去尾 00**?有 Alias 不带 00 后缀的特例吗(如 EIM 50001 → device_key=50001?)?

   ***表的对应关系上面已说明，直须保持一一对应即可，最好是“device_key 是否恒 = Alias 去尾 00”***

3. **channelsdataprotocol 的 ShipId** 为何是 1 而非 35701001?

   ***保持1即可，不必深究***

4. **重启 HldDataCap 后是否重新读库**,还是启动时加载一次?改配置生效方式?

​	***每次重启都会加载一次***

1. tab_engine_param 的 **device_type(0/1/2)** 与 channelsdataprotocol 的 **DataTypeRegister(0/1/2/3-8)** 是否自动映射,还是人工维护?

   device_type(0/1/2)是值这些数据的类型，表结构有写：“通道号类型；0：报警；1：数值；2：开关”

   channelsdataprotocol 的 **DataTypeRegister(0/1/2/3-8)**是数据类型，参考下面的表格：

   DataTypeRegister(数据类型)：

   	0.bit16(ab): 数字量/信号量/开关量/状态量/报警量，寄存器个数为1
   	1.bool: 布尔类型: 寄存器个数为1
   	2.int16_t(ab)有符号模拟量(ab),寄存器个数为1
   	3.float(abcd)单浮点数(abcd)，寄存器个数为2
   	4.float(badc)单浮点数(badc)，寄存器个数为2
   	5.double(abcdefgh)双浮点数，寄存器个数为4
   	6.double 双浮点数(hgfedcba)，寄存器个数为4
   	7.float(dcba)单浮点数(dcba)，寄存器个数为2
   	8.uint16_t(ab)无符号模拟量(ab)，寄存器个数为1
   	9.int32_t(abcd)
   	10.uint32_t(abcd)
   	11.bit16(ba)
   	12.uint16_t(ba)
   	13.int16_t(ba)
   	14.float(cdab)
   	15.int32_t(cdab)
   	16.int32_t(dcba)
   	17.int32_t(badc)
   	18.uint32_t(cdab)
   	19.uint32_t(dcba)
   	20.uint32_t(badc)

2. 岸端 tab_engine_param 全量 1542 条是否 = iscu.hldkeymap 全量?新增采集点时要同时 insert 两张表,有没有现成导入流程/xlsx 模板?

   ***这俩表是全量对应的。没有模板。***	

---

## 9. 源码级修正 + 可视化配置中心(2026-08 补)

### 9.1 重要修正:采集程序有源码,且分两个程序

此前以为 HldDataCap 是黑盒。实际上**源码在本地仓库**,且"压缩 PNG"的不是 transplatform:

| 程序 | 源码目录 | 角色 |
|---|---|---|
| **datacap** | `datacap-feature/datacap-feature/`(C++) | **船端采集 + PNG 编码**。读 iscu 库(channelsdataprotocol 定"读什么寄存器"、hldkeymap 定"编码顺序")→ 从 AMS Modbus TCP(:502)/NMEA UDP 采集 → 每 2s 按 orderkey 编码 PNG 帧 → 作为 TCP/QUIC 客户端连岸端 transplatform:5165 |
| **transplatform** | `trans_platform-feature/trans_platform-feature/`(C++) | **岸端转发网关,不编码 PNG**。TCP server 收 datacap 上送的 PNG/AIS → byteToHex 转大写 HEX 字符串 → 发 Kafka(type2 实时 PNG→`ship-{id}-data-org-makeup`、type6 历史、type3/7 AIS)。另做船岸同步双向中继 |

**修正后的数据流**:
```
AMS/NMEA → datacap(读 iscu,编码 PNG) → TCP:5165 → transplatform(转 HEX) → Kafka
   ship-{id}-data-org-makeup → dataCleanV2(按 order_id 查 tab_engine_param) → Redis
```

**datacap 源码关键文件**:main.cpp、read_system_config.cpp(读 system_config.xml)、InterfaceManager(load_topic_info 读 deviceinfo+topicinfo)、ConfigurationBridge(loadDataProtocol 读 channelsdataprotocol 的 JsonString,启动一次,无热重载)、DataMana(modbus 解析,写缓存 `[通道][Alias]`)、topicdatahandler(load_hldmapkey 读 hldkeymap 建 orderkey 映射 + PNGToBinary::PutData(orderkey,值))。

**transplatform 关键文件**:main.cpp、config.cpp(TinyXML)、TcpServer/tcpSession(收 datacap)、handle_png_data(纯转发)、kafkaUtils(发 Kafka)。

### 9.2 可视化配置中心 = 配置需求权威入口

页面:`ConfigurationManagement`(spadmin → 右上角"系统配置"),前端容器 `icommander-web-feature/src/containers/ShipConfiguration/`,后端 controller 在 `bwt-vms-system/.../controller/config/`。**各配置功能 = 一张/组表的 CRUD**:

| 配置功能(前端模块) | 后端 API 前缀 | 操作的表 | 备注 |
|---|---|---|---|
| **采集配置** CollectConfig | `/config/visual/engine/datacap/*` | iscu:`hldkeymap`→重生成 `deviceinfo/topicinfo/channelsdataprotocol`;反向同步 `tab_engine_param`/`tab_statistics_param`/`tab_engine_state_quantity_cfg` | **仅船端版可用**(岸端抛"岸端无需配置采集") |
| 基础信息 ShipBasicInfo | `/config/visual/ship/*` | `tab_ship_info`+`tab_ship_info_extend`+company/fleet;加船自动建 `vms_{shipId}_base_db` | |
| 工况点/菜单 EngineParam(+Menu/IcommanderConfig) | `/config/visual/engine/backend/*`、`/engine/menu/*` | `tab_engine_param`(+relationship、联动统计/状态量表);菜单 `tab_engine_param_menu` | 健康菜单保存 feign 同步 `mmha_component` |
| 油水 OilTankParam | `/config/visual/oilCabin/*` | `tab_tank_device`+`tab_height_cabin` | 校验 deviceKey 在 tab_engine_param |
| 能效参数 EnergyEngineParam | `/config/visual/energyefficiency/config` | `tab_statistics_param`+`tab_ship_info_extend`(SFOC) | |
| 能效设备 EnergyEffciencyDevice | `/config/visual/energyefficiency/device` | `tab_equipment`+`tab_statistics_param` | |
| 流量计 flowmeterParam | `/config/visual/flowmeter/*` | `tab_statistics_param` | |
| 机舱健康 EngineHealthConfig | `/mmha/mmha/structureTree/*` | `mmha_component`(一级 feign 同步 `tab_engine_param_menu`) | |
| 健康工况点 HealthPointConfig | `/mmha/mmha/configuration/*` | `mmha_component_device_relation`/`model_info`/`advice_*` | |
| **配置导出** Exportimport | `/export/file/configExport`、`/engineConfigExport` | `tab_log_export_report_record`+各表 | **带 newShipId 可克隆全船到新船**;engineConfigExport 导机舱 Excel(sheet=shipId,与 import 模板同构) |
| 通导 ConductivityConfiguration(实为通导) | `/config/visual/navigation/*` | `tab_engine_param`+船端 iscu | |
| 特殊信号 OtherSet/Otherconfigurations | `/config/signal/special/*` | `tab_statistics_param`+`tab_sys_dict` | |

**结论**:可视化配置覆盖的"配置需求"与手动 SQL 完全同源。**新船配置推荐路径** = `configExport(newShipId=母船)` 克隆 → 再逐模块针对性调整;可视化有 bug 时才落 SQL。skill 化时应优先引导用户用可视化/导出克隆,SQL 作兜底。

---

### 9.3 物理接线层:MOXA 串口服务器(配船前置资料)

所有通导/AMS/其他系统信号先经 **RS485 接入 MOXA NPort 5650 串口服务器**再转 TCP 给 datacap。这条船的 MOXA 配置已导出(两份 .txt),是"新船接线规划"的模板。

**MOXA 1.233(通导)** = TCP Server/多主机推送(推 192.168.1.101/102/188):
| 串口 | 设备 | 波特率 | TCP | datacap Topic |
|---|---|---|---|---|
| P1 | AIS | 38400 | 4001 | AIS |
| P2 | SL 计程仪 | 4800 | 4002 | SpeedLog |
| P3 | GYRO 罗经 | 4800 | 4003 | GYRO |
| P4 | 风速风向 | 4800 | 4004 | Anemometer |
| P5 | ECHO 回声 | 4800 | 4005 | EchoSounder |
| P6 | RA | 4800 | 4006 | Rudder? |
| P7 | GPS | 4800 | 4007 | GPS |
| P8 | (空) | 4800 | 4008 | — |

**MOXA 1.234(AMS/装载/侧推/倾斜仪)**:
| 串口 | 设备 | 波特率 | 模式 | TCP | datacap 采集 |
|---|---|---|---|---|---|
| P1 | AMS(Modbus RTU) | 9600 | Modbus | 5001 | AMS1(datacap TCP 主动轮询) |
| P3 | 装载 LOAD | 9600 | Modbus | 5003 | LOAD |
| P5 | 侧推 BTP | 9600 | Modbus | 5005 | BTP |
| P7 | 倾斜仪 EIM(NMEA) | 4800 | TCP Server 推送 | 5007 | EIM |
| P2/4/6/8 | 备用 | 115200 | 禁用 | — | — |

**对应关系**:MOXA 端口 ↔ datacap `LocalGateway.xml` 通道(EngineData 主动连 .234:5001/5003/5005 = 1.234 的 P1/P3/P5;SailingData 4001-4007 = 1.233 的 P1-P7)。**配新船物理层 = 规划串口分配(设备/波特率/校验)→ 决定 datacap 通道与 iscu deviceinfo。** 通导常规设备(GPS/AIS/GYRO/回波/计程仪/风速/舵)是标配,特殊接入(倾斜仪 EIM、LOAD、BTP)按船加。

---

## 附:本轮实测用的关键命令

```bash
# SSH 服务器
ssh root@192.168.8.142

# 连 iscu 库(服务器上,经 docker mysql 容器)
docker exec mysql mysql -uroot -p<MySQL密码>

# 查表
USE iscu; SHOW TABLES; SELECT COUNT(*) FROM hldkeymap;

# 查看 JsonString 全量
SELECT JsonString FROM channelsdataprotocol WHERE WayUid=1575876387 AND ChanCode=1\G

# 岸端工况点表对照
USE vms_sys_db;
SELECT device_key, order_id, device_name FROM tab_engine_param WHERE ship_id=35701001 AND order_id IS NOT NULL;
```
