# BWT-VMS 全库数据字典与表关系(232+表)

> 更新日期:2026-09-04(修改本文档后请同步更新此日期,并到 Dify 知识库重新上传该文件)
> 来源:192.168.8.142 实库 information_schema 全量盘点 + 全库 DDL 解析 + 代码引用核对。
> 覆盖:vms_sys_db 全部 245 张表(含视图)+ iscu 7 表 + 按船分库 + nacos。XT GLORY 环境(35701001)。
> 用途说明:标 ★ 的表交付必配(有专门手册);标(空)=表存在但 0 行=该功能本环境未启用;copy1/copy2/old/bak 系列=**现场手工备份表,非业务表**。

---

## 0. 四库职责总览

| 库 | 表数 | 职责 | 谁读写 |
|---|---|---|---|
| **iscu** | 7 | 船端采集点表(datacap 的大脑) | datacap(启动读)、可视化采集配置页(船端) |
| **vms_sys_db** | 245 | 岸端业务主库(本字典主体) | system/mmha/realTimeData 等所有 Java 服务 |
| **vms_{shipId}_base_db** | 1 | 按船分库:tab_data_org(PNG 原始二进制留底,按月分区到 2030) | dataCleanV2 动态建连写入 |
| vms_nacos_config | 11 | Nacos 配置中心自身存储(服务注册/配置) | Nacos |
| vms_energy_db | 0 | 空库(预留能效库,现版本未用) | — |

**全局数据主链**(理解一切表关系的基础):
```
MOXA→datacap(读iscu)→PNG→transplatform→Kafka→dataCleanV2
  ├─ 解码查 tab_engine_param.order_id → 写 Redis 实时值(list:ship:{id}:other)
  ├─ 写 vms_{shipId}_base_db.tab_data_org(PNG留底)
  ├─ 写 tab_res_clean(5min清洗统计)/tab_res_route(航迹)/tab_res_status(开关量状态)
  ├─ 流量计→tab_flowmeter_record_v2;液位→tab_liquid_level_measuring_record
  └→ realTimeData 加工(trans_data公式/能效负载率[查tab_ship_info_extend额定])→前端WS
```

---

## 1. 工况点核心域:表关系(交付 S2)

**关系**:`tab_engine_param.device_id` ↔ `tab_engine_param_relationship.engine_device_id`;`relationship.menu_device_id` ↔ `tab_engine_param_menu.device_id`(menu_type 1=监控/2=健康);`tab_statistics_param.device_key` = param.device_key;状态量映射按 device_key 关联。详细操作见《工况点增删改全表影响手册》。

## 1b. 工况点核心域:表清单

| 表 | 行数 | 用途 | 关键列 |
|---|---|---|---|
| ★tab_engine_param | 1530 | 工况点主表(一切前提) | device_key/order_id(=hldkeymap.orderkey)/device_type(0报警1数值2开关)/device_id(uuid32)/量程报警7列/trans_data |
| ★tab_engine_param_menu | 28 | 菜单树(监控+健康两套) | menu_id(=ship_id+2位序号)/device_id/identification/action_path/menu_type |
| ★tab_engine_param_relationship | 1476 | 点↔菜单绑定(不挂=前端看不到) | menu_device_id↔engine_device_id |
| ★tab_engine_state_quantity_cfg | 163 | 状态量(type=2)值→文本/颜色 | device_key/state_data(JSON) |
| ★tab_statistics_param | 38 | 信号路由(能效语义) | device_type(1主机功率/2转速/13发电机/16轴发/40密度/41流量计)/group_desc/equipment_uuid |
| tab_statistics_param_type | 42 | 信号路由类型字典 | type/名称 |
| tab_alarm_mapping | 2 | 报警归一化(dds_value→consistency_value,0=报警) | 全局2行,装机初始化 |
| tab_export_config | 37 | 配置导出清单(驱动 Excel 导入导出) | db/table/add_type(0=全量型,导入时整表DELETE!)/compare_column |
| tab_engine_param_menu_mimic_config | 36 | mimic 图布局配置(JSON,3.5MB) | identification/mimic_config/language/menu_type |
| view_tab_engine_param / ship_all_view | 视图 | 数据库视图(查询用) | — |
| copy1-3/bak/old/front 系列(12张) | — | **现场手工备份表,勿当业务表** | 命名规律:改库前 CREATE TABLE xx AS SELECT |

## 2. 机舱健康域(交付 S4,详见 bwt-vms-health-config)

**关系**:`mmha_component` 父子树(parent_component_key)→ 叶子绑 `mmha_component_device_relation`(component_key+device_key+normal/min/max)→ 每点配 `mmha_component_model_info`(model_type_code 01=线性);`mmha_advice_*` 四张为 AI 生成的缺陷知识(advice_defect 为源,cause/impact/repair 按 defect 关联);历史分在 `tab_engine_health_record`。**v1 海大链路的 tab_engine_health_relationship 已废弃(空)**。

| 表 | 行数 | 用途 |
|---|---|---|
| ★mmha_component | 15 | 部件树(component_key 编码:根XX0000/叶XXYY,汇总02=MIN) |
| ★mmha_component_device_relation | 115 | 叶子绑工况点+模型参数(normal_value/max/min) |
| ★mmha_component_model_info | 95 | 每点计算模型(01=LINE 线性) |
| mmha_advice_defect/cause/impact/repair | 106/106/106/95 | AI 缺陷建议知识库(名称含"缺陷/原因/影响/维修") |
| tab_engine_health_record | 79427 | 健康 v1 历史记录(海大推送时代;mmha v2 分数在 Redis) |
| tab_engine_log_model | 275 | 轮机日志模板 |
| tab_diagnosis_info(空)/tab_engine_compare(空)/tab_engine_custom_grouping(空)/tab_engine_overlay_cfg(空)/tab_engine_log(空)/tab_engine_health_relationship(空,废弃) | 0 | 未启用/废弃 |

## 3. 能效/碳强度域(交付 S5,详见 bwt-vms-energy-config)

**关系**:`tab_equipment.uuid` ↔ `tab_statistics_param.equipment_uuid`;流量计消耗 `tab_flowmeter_record_v2`(device_num=group_desc)喂 CII/EEXI;`tab_cii_calculate`(本船行)引用 `tab_ship_info_extend` paramId 98=DWT;评级查 `tab_cii_rating`(d1-d4 阈值)+`tab_cii_reference_lines`(按 ship_type)。

| 表 | 行数 | 用途 |
|---|---|---|
| ★tab_equipment | 11 | 能效设备(mainframe/dynamo/shaftGenerator/boiler,uuid varchar) |
| ★tab_flowmeter_record_v2 | 1232 | 流量计3分钟消耗(能耗分布数据源)consume kg |
| ★tab_cii_calculate(空,本船未建)/tab_cii_rating 10/tab_cii_reference_lines 10 | CII 计算与评级 |
| tab_eexi_emission_factor 8 / tab_eexi_limits 27 / tab_eexi_reference_lines 8 / tab_eexi_calculate(空) | EEXI 排放因子(Cf)/限值/参考线/本船计算 |
| tab_carbon_intensity ×5(全空) | 碳强度计划(3年计划/年度指标/实施计划/更新历史)——功能未启用 |
| tab_emission_mrv(空) | MRV/DCS 排放报告 |
| tab_noon_report_model 29 / tab_noon_report(空) | 午报模板与数据 |
| tab_consume/tab_consume_param/tab_consume_rebuild(空) | 油耗消耗计算(v1 遗留) |
| tab_energy_month_data/tab_fuel_consumption×2/tab_fueloil_flight_monthly_summary/tab_gas_temp_statistics/tab_power_*/tab_speed_oil_statistics/tab_speed_optimization/tab_flowmeter_sog_*(空) | 能效扩展分析(月度能源/燃耗模型/航速优化/气缸温度)——未启用 |

## 4. 油水舱/液位域(交付 S5)

**关系**:`tab_tank_device_menu`(menu_id=tank_type!)→`tab_tank_device`(tank_id 从4起,device_key绑工况点)→`tab_height_cabin`(tank_id→height↔cabin 体积曲线);填报 `tab_oil_water`(父子舱各一行)→`tab_oil_water_consume`(定时算存量差);采集液位→`tab_liquid_level_measuring_record`(插值后)。

| 表 | 行数 | 用途 |
|---|---|---|
| ★tab_tank_device_menu 3 / ★tab_tank_device 11 / ★tab_height_cabin 646 | 油舱三级配置(见能效手册) |
| ★tab_oil_water 28 / ★tab_oil_water_consume 60 | 人工填报/每日消耗 |
| tab_liquid_level_measuring_record 1859 | 液位测量记录(自动采集路径) |
| tab_oilwater/tab_oilwater_consume/tab_liquid_level_record/tab_height_cabin_copy1/tab_tank_*/tab_oil_water_file/tab_tank_param/tab_trim_relation/tab_hydrostatic_trimmed/tab_load_info/tab_load_management(空) | v1 遗留或未启用(装载/纵倾相关预留) |

## 5. 航行监控域:表关系与核心表(内河特色)

**关系**:电子围栏 `tab_ais_electric_fence`(16个:type 1港口/2锚地/3限速区/4ECA)→`tab_ais_electric_fence_info`(1114顶点坐标)→`tab_ais_electric_fence_type`(类型字典);船进围栏事件→`tab_ais_distance_record`;航迹 `tab_res_route`(经纬度轨迹)与 `tab_res_clean` 同源;`tab_port_info`(港口字典)↔`tab_flight_record`(航次:始发/目的港名+eta);`tab_channel_coordinates`(2714行航道坐标,内河航道图);`tab_gate_management_model`(三峡闸口调度模板);`tab_ch_ship_info`(148条周边船舶静态信息);地理字典 city 3673/province 120/country 145。配置操作见《航行监控域操作手册》。

## 5b. 航行监控域:表清单

| 表 | 行数 | 用途 |
|---|---|---|
| tab_ais_electric_fence 16 / _info 1114 / _type 7 / _extend(空) | 电子围栏(ECA/港口/限速区配置,系统级) |
| tab_ais_distance_record 4304 | 船与围栏/他船距离记录(进港提醒) |
| tab_ais_ship_status_record 9 | 船舶状态变更记录 |
| tab_res_route 4336 / tab_res_clean 94071 / tab_res_status 32766 | 航迹/5min清洗统计/开关量状态(核心时序三表,按月分区) |
| tab_res_fit_route / tab_res_mainehx_predict / tab_res_route_rtz(空) | 拟合航迹/主机功率预测/RTZ导出——未启用 |
| tab_port_info 33 / tab_ports 36 / tab_port_water_regime(空) | 港口字典(内河港口)/港口水情 |
| tab_flight_record(空) | 航次记录(始发/目的港/离港时间) |
| tab_channel_coordinates 2714 | 长江航道坐标(航道测绘) |
| tab_channel_maintenance_scale/navigation_information/navigation_legend/notice/water_level_information_release(空) | 航道维护尺度/通航信息/图例/通告/水位发布——未启用 |
| tab_gate_management_model 94 / tab_gate_type 6 / tab_gate_name/announcement/management_extend(空) | 三峡/闸口管理(模板已装,数据未启用) |
| tab_ch_ship_info 148 | 周边船舶静态信息(AIS 他船) |
| tab_city/province/country_info 3673/120/145 | 地理字典 |
| tab_weather_data/forecast(空) | 气象——未启用 |
| tab_report_composite_nav 2 / tab_report_nav_menu 28 | 综合查询导航配置 |
| tab_information_delivery×2 / tab_traffic_flow / tab_sail_log(空) | 信息推送/流量/航行日志——未启用 |

## 6. mimic 图/文件域(S3)

**关系**:`tab_engine_param_menu_mimic_config`(布局JSON,见域1)↔`tab_mimic_file`(SVG 实例 blob,menu_mimic_config 里的 /sys/mimic/file/download?id= 指向它)↔`tab_mimic_template_file`(图元模板库);sys_engine_mimic_* 三张为旧版绑定表(空,废弃)。

| 表 | 行数 | 用途 |
|---|---|---|
| ★tab_mimic_file 65 | mimic SVG 实例(blob,9.5MB) |
| ★tab_mimic_template_file 74 | SVG/JS 图元模板库 |
| sys_picture/sys_picture_menu/tab_picture_file/tab_ship_picture(空) | 图片管理——未启用 |
| sys_engine_mimic×3(空) | 旧版绑定——废弃 |
| copy/old 系列(9张) | 备份表 |

## 7. 用户/权限/船员域(D01)

**关系**:`tab_resource`(菜单/按钮资源树)↔`tab_role_resource`(角色授权)↔`tab_user_role`↔用户;`tab_login_user_info` 登录日志;`tab_seaman_info` 船员档案→`tab_ship_user_info`(船员-船关系)→`tab_ship_user_onduty`(值班)/`tab_ship_user_holiday`。

| 表 | 行数 | 用途 |
|---|---|---|
| tab_resource 150 / tab_role_resource 42 | 权限资源树/角色-资源 |
| tab_login_user_info 6 | 登录日志 |
| tab_user_type 5 | 用户类型字典 |
| tab_role_info/tab_user_info/tab_user_role/tab_user_extend/tab_user_permanent_token/tab_user_role_remind(空) | 用户主数据(本环境用 Nacos/网关侧账号体系,表预留) |
| tab_seaman_info(空) | 船员档案——未启用 |

## 8. 船舶档案域(交付 S0 建船)

**关系**:`tab_ship_info`(船主表,id=ship_id)→`tab_ship_info_extend`(param_id/param_value KV,**param_id 语义由 tab_ship_param_model 定义**:27主机额定功率/28额定转速/70发电机额定/98 DWT/104 SFOC等100项);船型 `tab_ship_type`→`tab_ship_type_param_relation`(1704行:船型默认参数模板,建船初始化用);公司/船队 company_info↔fleet_relation↔ship_fleet;`tab_ch_ship_info` 归域5。

| 表 | 行数 | 用途 |
|---|---|---|
| ★tab_ship_info(注:本环境 0 行,船在 Nacos/库切换环境差异,142 为测试库) | 船舶主表 |
| ★tab_ship_info_extend 92 | 船级参数 KV(值列 **param_value**) |
| tab_ship_param_model 100 | 参数模板(定义 param_id 含义与默认值) |
| tab_ship_type 16 / tab_ship_type_param_relation 1704 | 船型字典/船型默认参数 |
| tab_ship_fleet_relation 1 / tab_ship_fleet(空) / tab_company_info(空) / tab_company_fleet_relation / tab_company_param_model(空) | 公司/船队组织 |
| tab_factory_info(空) | 工厂信息 |
| tab_ship_daily_info(空) | 船舶日报——未启用 |
| tab_sync_table_info(空) | 船岸同步表配置——未启用 |

## 9. 设备/巡检域(D12,未启用)

`tab_patrol_config`(3)/`tab_patrol_order`(4)/`tab_patrol_position`(17)巡检基础配置已装;job/log/alarm 空。`tab_device*` 五张为设备监控预留(空)。`tab_ship_device`(空)。

## 10. 行为识别/CCTV 域(D10/D11,未启用)

`tab_behavior_recognition_violation_dict`(16 违章字典已装),behavior 主表/extend/camera_info 空。

## 11. 微信小程序域(未启用)

`tab_app_wx_*` 七张+`tab_wx_*` 两张:小程序用户/订阅/消息/机舱重点报警映射。仅 `tab_app_wx_remind` 4 行。

## 12. 系统/字典/日志域

| 表 | 行数 | 用途 |
|---|---|---|
| tab_sys_dict 44 | 全局字典(油种 tank_device_type/通导 navigation_param 等) |
| tab_specification 24 | 帮助文档(spadmin 配置页的"帮助") |
| tab_alarm_statistics 3955 | 报警统计(历史) |
| tab_log_export_report_record 17 | 导出记录(Excel 异步导出) |
| xxl_job_* 8张 | XXL-Job 定时任务框架(油水消耗 job 在此调度) |
| flyway_schema_history 28 | 数据库版本迁移历史 |
| tab_sys_run_log(空) | 运行日志 |
| **遗留死表**(勿动):tab_alg_para/tab_alg_script/tab_alg_clean_param/tab_md_orderkey/tab_md_lolist/tab_navigation_param/tab_ship_navigation_param/tab_param_device/tab_param_pass/tab_front_param_mapping/tab_fit_type/tab_engine_param_front | 详见《工况点增删改手册》第5节 |
| 其余空表:tab_accident_alarm/tab_alarm_feedback/tab_daily_record/tab_engine_statistics/tab_record_*/tab_integrated_query_shortcut/tab_flow_param/tab_data_org(主库,数据在按船库)/tab_user_flight_relation | 事故报警/报警反馈/日报/工况统计/补录/综合查询快捷/流量参数——未启用 |

---

## 13. 交付视角三条铁律

1. **空表 ≠ 没用**:空表分三类——功能未启用(启用时才填)、按船数据(本环境该船没用此功能)、预留死表(勿动,见手册)。判断方法:代码引用(死表零引用)+装机初始化数据。
2. **copy/old/bak 表是现场备份**:交付改库前的习惯产物,不是业务表;**新环境建库后应清理**,避免导出/克隆时把垃圾带过去(整船克隆 configExport 会连它们一起导!)。
3. **改任何 ★ 表前**:mysqldump 备份 + 对照《工况点增删改全表影响手册》/《配置中心数据安全风险清单》确认级联影响。
