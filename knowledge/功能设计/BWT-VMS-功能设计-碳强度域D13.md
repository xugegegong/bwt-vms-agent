# BWT-VMS 产品功能设计与实现 · 碳强度管理域(D13)

> 更新日期:2026-09-04(修改本文档后请同步更新此日期,并到 Dify 知识库重新上传该文件)

> 遵循模板。对应 IMO 碳排放法规(CII/EEXI),算法密集。

---

## 1. 域概述

| 项 | 内容 |
|---|---|
| 定位 | 计算与报送船舶碳强度指标(CII 营运碳强度、EEXI 能效现有船舶指数),管理三年计划与实施措施 |
| 核心价值 | 满足 IMO MEPC 法规合规,评级驱动能效改进 |
| 边界 | 只管"碳强度指标与计划";油耗/航程底层数据在 D02/D03/D07;排放趋势图在 D06 |
| 算法核心 | CarbonIntensityServiceImpl(Required/Attained CII、ABCDEF 评级、EEXI) |

---

## 2. 功能点清单

```
F13 碳强度管理
├─ F13-1 碳强度计划(三年计划 + 目标 CII)
├─ F13-2 实施措施表(减排措施)
├─ F13-3 修改历史
├─ F13-4 CII 算法(Required/Attained/评级)
├─ F13-5 EEXI 算法
└─ F13-6 导出(5 类)
```

---

## 3. 逐功能点详述

### F13-1 碳强度计划

**(1) 接口**:`GET /carbon/intensity/getCurrentYear`(shipId,start_utc)→ CarbonIntensityVo(船信息 + 历史 3 年 + 预测 3 年 + 三年计划);`POST /carbon/intensity/save`(保存三年实施+目标 CII,自动写修改历史)。

**(2) 内容**:船舶基本信息 + 历史 3 年(要求/实际前/实际后/评级)+ 预测 3 年(要求/目标 CII 可编辑/目标评级)+ 三年实施计划文本域 + MEPC 决议说明。

### F13-2 实施措施表

`GET /carbon/implPlan/list`(分页)、`POST /carbon/implPlan/save`(新增/更新措施)。字段:step/influence/impl_items/pre_impl_time/charged_by/hinder/pre_solutions/actual_impl_time/impl_by。

### F13-3 修改历史

`GET /carbon/updateHis/list`(修改前/修改后/操作人)。

### F13-4 CII 算法(核心)

**(1) Required CII(要求营运碳强度)**:`Required = (1-Z) × ReferenceLine`;`ReferenceLine = a × Capacity^(-c)`(a/c 来自 tab_cii_reference_lines 按船型+载货量区间)。

**(2) 衰减系数 Z**(年度):2019:0%/2020:1%/2021:2%/2022:3%/2023:5%/2024:7%/2025:9%/2026:11%(2023 起 2%/年)。

**(3) Attained CII(实际)**:
- 未修正:`Attained = (CO2_total × 1000000) / (Capacity × Distance)`,单位 g/(吨·海里)
- 修正后:CO2 扣减 STS 航次/电力/货物加热/卸货泵等 6 类扣减项,运输功扣减扣减航程

**(4) 评级 ABCDEF**:`ratio = AttainedCII / RequiredCII`;ratio<d1→A / d1≤r<d2→B / d2≤r<d3→C / d3≤r<d4→D / r≥d4→F(d1~d4 来自 tab_cii_rating 按船型+DWT)。ratio 越小越好。

**(5) 载货量选取**:豪华邮轮(5)/滚装货船(20)/滚装客船(14,16)→用 GT(总吨,指标 cgDIST);其他→用 DWT(载重吨,指标 AER)。

### F13-5 EEXI 算法(能效现有船舶指数)

```
主机功率 P_me = min(额定功率×0.75, MCR限制×0.83) × 主机台数
辅机功率 P_ae = (P_me≥10000) ? 0.025×P_me+250 : 0.05×P_me
Attained EEXI = (Fj×SFOC_me×Cf×P_me + SFOC_ae×Cf×P_ae) / (Fi×Fc×Fl×Capacity×Vref×Fw×Fm)
```
参数:tab_eexi_calculate(SFOC/Vref/F 系数)、tab_eexi_emission_factor(Cf 按燃油)、tab_ship_info_extend(额定功率 paramId 27/MCR 106/台数 53/设计航速 15)。

**其他指标**:EEOI = CO2 / Σ(航程×载货量);EEPI = CO2 / (Capacity×cargoSailingDistance);cgDIST(滚装)/cbDIST(邮轮)。

### F13-6 导出

`/export/file/carbonPlan`(TQDJH 计划)、`/carbonImplPlan`(TQDSSJH 实施)、`/carbonPlanUpdateHis`(TQDJHXG 历史)、`/carbonData`(TQDYYSJ 数据)、`/carbonTarget`(TQDZBCS 目标)。

---

## 4. 数据流

```
油耗(tab_consume)+航程(tab_ais_distance_record/flight_record)+排放系数(tab_eexi_emission_factor)
  → tab_carbon_intensity_year_indicator(co2_total/fuel_consume_total/voyage_distance/扣减项)
  → CarbonIntensityServiceImpl 算 CII/EEXI/评级
  → tab_carbon_intensity(required/actual_front/back/goal_cii)+ 计划/实施/历史表
  → 前端 CarbonIntensitySchedule 展示
```

⚠️ year_indicator 的写入触发点(定时任务?D06 提交?)在 Controller 层未体现,建议查 task/job 目录。

---

## 5. 数据表

| 表 | 用途 |
|---|---|
| tab_carbon_intensity | 年度碳强度(required_cii/actual_cii_front 修正前/actual_cii_back 修正后/goal_cii) |
| tab_carbon_intensity_3year_plan | 三年计划(year_plan text/goal_cii/self_assess_improve) |
| tab_carbon_intensity_impl_plan | 实施措施(step/influence/impl_items/...) |
| tab_carbon_intensity_update_his | 修改历史(update_before/update_after) |
| tab_carbon_intensity_year_indicator | 年度指标(co2_total/fuel_consume_total/voyage_distance/sum_deduct_* 6 类扣减)— **与 D06 共享** |
| tab_cii_reference_lines | CII 参考线 a/c 系数(按船型+载货量区间) |
| tab_cii_rating | CII 评级阈值 d1~d4 |
| tab_eexi_calculate / _emission_factor / _reference_lines / _limits | EEXI 计算参数与法规配置 |

---

## 6. 风险与改造建议

| 问题 | 诊断 ID | 建议 |
|---|---|---|
| 法规参数硬编码年份(Z) | —(合规) | Z/参考线/阈值跟随 IMO 更新,加版本管理与提醒 |
| year_indicator 写入机制不明 | —(可追溯) | 梳理定时任务,文档化触发链路 |
| CII/EEXI 计算无单测 | DIAG-P2-M-05 | 对算法补单测(法规合规关键) |
| 与 D06 排放报告数据重叠 | —(边界) | 明确 D06 趋势 vs D13 合规计算职责 |
