# BWT-VMS 产品功能设计与实现 · 系统配置与基础域(D16)

> 更新日期:2026-09-04(修改本文档后请同步更新此日期,并到 Dify 知识库重新上传该文件)

> 遵循模板。本域是"配置驱动"的汇总:系统各能力的配置入口。各业务域配置接口已在对应域详述,本文件做总览与串联。

---

## 1. 域概述

| 项 | 内容 |
|---|---|
| 定位 | 系统的配置中心:船舶/设备/工况点/油舱/流量计/通导/能效/机舱图/健康部件的配置,加资源菜单与字典 |
| 核心价值 | BWT-VMS 是**强配置驱动**系统——配错则算不出/展不出。本域是"配对了才能跑"的总入口 |
| 边界 | 配置类接口与页面;具体业务计算在各域 |
| 配置中心入口 | 前端 `/ConfigurationManagement`(独立窗口,带 token+deploymentEnv 参数打开) |

---

## 2. 功能点清单

```
F16 系统配置与基础
├─ F16-1 可视化配置体系(/config/visual/*)
│   ├─ 船舶配置(ShipInfoConfigController)
│   ├─ 机舱图配置(ShipEngineConfigController,详见 D04)
│   ├─ 能效设备配置(EnergyEfficiencyConfigController,详见 D06)
│   ├─ 油舱配置(OilCabinConfigController,详见 D07)
│   ├─ 流量计配置(ShipFlowmeterController,详见 D07)
│   └─ 通导配置(ShipNavigationConfigController)
├─ F16-2 资源菜单管理(tab_resource / identification)
├─ F16-3 字典管理(tab_sys_dict)
├─ F16-4 船舶参数扩展(tab_ship_info_extend,paramId 键值)
├─ F16-5 前端配置中心(ConfigurationManagement)
└─ F16-6 运行时配置(urlConfig/engine_config/global.js,详见前端架构库)
```

---

## 3. 逐功能点详述

### F16-1 可视化配置体系(`/config/visual/*`)

**(1) 设计**:按设备维度组织配置接口,每个 Controller 管一类设备/参数。配置写入对应表,驱动后端计算与前端展示。

| 子配置 | Controller | 写入表 | 详见 |
|---|---|---|---|
| 船舶配置 | ShipInfoConfigController(`/config/visual/ship/*`) | tab_ship_info / tab_ship_info_extend | 本域 |
| 机舱图/工况点 | ShipEngineConfigController(`/config/visual/engine/*`,16 接口) | tab_engine_param / tab_engine_param_menu_config / tab_engine_param_menu_mimic_config | D04 |
| 能效设备/SFOC | EnergyEfficiencyConfigController(`/config/visual/energyefficiency/*`) | tab_equipment / tab_statistics_param / tab_ship_info_extend | D06 |
| 油舱设备 | OilCabinConfigController(`/config/visual/oilCabin/*`) | tab_tank_device / tab_tank_device_menu / tab_height_cabin | D07 |
| 流量计 | ShipFlowmeterController(`/config/visual/flowmeter/*`) | tab_statistics_param(41流量计/40密度) | D07 |
| 通导 | ShipNavigationConfigController(`/config/visual/navigation/*`) | tab_engine_param(order_id≤100)+tab_sys_dict(⚠️代码核实:实际写 param,不是 tab_navigation_param;后者为废弃老表,仅 realTimeData 读它做'--'补位) | 本域 |
| mimic 模板 | MimicFileController(`/mimic/file/*`) + ConfigController(`/config/visual/menu/mimic/*`) | tab_mimic_file / tab_mimic_template_file / tab_engine_param_menu_mimic_config | D04 |

**(2) 配置驱动总表(关键)**:配置缺失 → 对应计算/展示失败的映射:

| 配置项 | 表 | 缺失影响 |
|---|---|---|
| 工况点 device_key/type/order_id/trans_data | tab_engine_param | 工况采集/清洗/展示全断 |
| 统计参数 device_type/group_desc | tab_statistics_param | 流量计/液位/能效算不出 |
| 油舱 tank_id/device_key/maximum/density | tab_tank_device | 油水填报/液位舱容算不出 |
| 高度舱容曲线 | tab_height_cabin | 液位→体积插值失败 |
| 能效设备/信号路由 | tab_equipment + tab_statistics_param(type 1-16) | SFOC/能耗分布算不出 |
| 船舶额定参数 | tab_ship_info_extend(paramId 27/28/70/104/15/98) | 功率推算/SFOC理论/EEXI/CII 算不出 |
| 健康部件树 | mmha_component(+relation/model) | 健康分算不出 |
| 报警归一化映射 | tab_alarm_mapping | 报警判定失效(0=正常) |
| 权限资源 | tab_resource(+tab_role_resource) | 菜单/路由/按钮显隐错乱 |

---

### F16-2 资源菜单管理

**(1) 设计**:tab_resource 定义菜单/按钮/接口三级资源,identification 是权限编码,驱动前端菜单与后端 @PreAuthorize。

**(2) 接口**:ActionResourceController `/action/resource/*`(`POST /privilege/listTree/v2` 所有权限资源树含 isPermission、`GET /authority/distributed/list/v2` 角色已分配、`GET /privilege/undistributed/list` 未分配)。角色-资源关系通过 RoleInfoController `/role/resource/add|modify` 管理(详见 D01)。

**(3) 资源字段**:action_path(路由)、resource_type(0菜单/1按钮/2接口)、identification、is_show、status。

---

### F16-3 字典管理

**(1) tab_sys_dict**:系统字典,按 type 组织。关键字典:
- `tank_device_type`:油种编码(1淡水/2轻油/3重油/4压载/5滑油/6防横摇/7其他/9 LNG/15低硫)
- 设备类型、报警类型、状态类型等

**(2) 字典是油种/设备类型等枚举的权威来源**,各域引用统一编码。

---

### F16-4 船舶参数扩展(tab_ship_info_extend)

**(1) 设计**:key-value 结构(paramId→value),存船舶额定参数,能效计算强依赖。

**(2) 关键 paramId**(详见 D06):
- 15 设计航速、16 总长、17 船宽、24 空载吃水、25 满载吃水、27 主机额定功率、28 额定转速、30 IMO、70 发电机额定功率、98 DWT、104 SFOC POPT(a,b,c)、107 轴发额定功率、200 电池容量

---

### F16-5 前端配置中心(ConfigurationManagement)

**(1) 入口**:`/ConfigurationManagement`,独立窗口打开(带 token+deploymentEnv 参数)。包含子页:
- EngineParamMenu:机舱菜单树(EM/EH,详见 D04)
- IcommanderConfig:工况点后端配置+公式(详见 D04)
- CollectConfig:采集 AMS/Modbus(仅船端,详见 D04)
- HealthPointConfig:健康测点配置(详见 D05)
- EngineHealthConfig:健康部件结构树(拖拽,详见 D05)
- 油舱/流量计/通导/能效配置子页

**(2) deploymentEnv 区分**:部分配置(采集/导入)仅船端可用,岸端调直接抛异常。

---

## 4. 数据表

| 表 | 用途 |
|---|---|
| tab_ship_info / tab_ship_info_extend | 船舶基础 / 额定参数(key-value) |
| tab_engine_param | 工况点(全局键 device_key) |
| tab_engine_param_menu_config / _mimic_config | 机舱菜单 / mimic 图配置 |
| tab_equipment | 能效设备(主机/发电机/锅炉) |
| tab_statistics_param | 统计参数(信号路由 device_type) |
| tab_tank_device / _menu / tab_height_cabin | 油舱设备 / 分类 / 舱容曲线 |
| tab_navigation_param | 老通导配置(废弃:仅 realTimeData 只读补位;新体系=tab_engine_param order_id≤100 + tab_sys_dict) |
| tab_alarm_mapping | 报警归一化(0=正常) |
| tab_resource / tab_role_resource / tab_role_info / tab_user_role | 权限资源/角色(详见 D01) |
| tab_sys_dict | 字典 |
| mmha_component / _device_relation / _model_info | 健康部件树(详见 D05) |
| tab_mimic_file / _template_file | SVG 模板(详见 D04) |

---

## 5. 风险与改造建议

| 问题 | 诊断 ID | 建议 |
|---|---|---|
| 强配置驱动,配置缺失即静默失败 | —(体验) | 增加配置完整性校验接口 + 缺失诊断提示(配了什么、缺什么、影响什么) |
| 配置散落多表多接口 | —(可维护性) | 统一配置中心视图,按船展示配置完整度 |
| tab_sys_dict 无管理界面 | —(功能) | 补字典管理 CRUD |
| 船端/岸端配置权限混淆 | —(安全) | 明确采集类配置仅船端,接口层已拦截,补前端按钮态 |
| ship_info_extend paramId 魔数 | —(可维护性) | paramId 枚举化(已有 ShipParamModelConstant,需对齐) |
