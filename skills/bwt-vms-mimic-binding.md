---
name: "bwt-vms-mimic-binding"
description: "BWT-VMS mimic 图平台绑定(S3):SVG 生成后上传平台、绑定工况点、保存布局、中英文两套图各存一次。用户提到\"SVG上传/绑定工况点/mimic/定义健康页面/机舱监控自定义图/组态绑定\"时触发;画图本身用 svg-generation-rules。"
---

# BWT-VMS mimic 图平台绑定(S3):SVG 上传/绑定工况点/中英两套

SVG 监控图生成后,把它配到平台上:上传、绑定工况点、保存版本。用户提到"SVG 上传/绑定工况点/mimic/定义健康页面/机舱监控自定义图/组态绑定"时触发。**画图本身用 svg-generation-rules skill,本 skill 管平台绑定**。

## 前置

- S2 已完成(要绑定的 device_key 已在 tab_engine_param)
- 健康页面绑定还需 S4 部件树已建(mmha_component.component_key 是保存锚点)
- SVG 文件已按 svg-generation-rules 规范生成(命名/ID/绑定属性合规)

## 绑定流程(前端操作)

### ① 进入编辑模式
- **机舱健康图**:机舱健康页 → 右上角编辑(路由带 edit=true)→ DefineHealthPage(menuType='2')
- **机舱监控图**:机舱监控对应分类页 → 编辑 → em/DefinePage(menuType='1')

### ② 摆图/上传
拖入 SVG 图元(或整图),调布局。保存时前端自动:
1. 把当前组图导出 SVG Blob → `uploadSvgList` 上传文件服务 → 得到下载 url(`/sys/mimic/file/download?id=xxx`)
2. `saveUrlPage(shipId, componentKey, svgUrl 或 svgUrlEn)` → 写 `mmha_component.svg_url / svg_url_en`(健康图)
3. `submitMimicCon(shipId, identification, mimicConfig JSON, menuType)` → 图元布局/点位绑定存菜单配置表

### ③ 绑定工况点
在图元上绑定 device_key(来自 S2 的 device_map.csv)。健康图的部件绑定 componentKey(树节点)。

### ④ 中英两套图(必做,最大的坑)
**保存时按"当前界面语言"写入不同字段**:中文界面保存 → svgUrl;英文界面保存 → svgUrlEn。
→ **必须切中文保存一次、切英文再保存一次**,否则另一语言页面空白(和健康页英文名空白同根源)。

### ⑤ 验证
- 退出编辑 → 页面正常显示图
- 数据上来后:绑定点位显示实时值;开关量变色(正常绿/报警红)
- 切英文再验证一遍

## 坑

| 坑 | 说明 |
|---|---|
| 只存了一套图 | 按当前语言存 svgUrl/En,中英各存一次 |
| 绑定点无值 | device_key 不在 tab_engine_param(回 S2),或采集未上线(回 S1 闸门) |
| 开关量颜色不对 | 颜色走报警链路(tab_alarm_mapping 拦截状态色),参考 FAQ skill 条目 10;确认 device_type 配置 |
| 健康图保存失败 | mmha_component 无该 componentKey(回 S4 先建树);saveUrlPage 按 componentKey 定位 |
| SVG 不被绑定识别 | 图不符合 svg-generation-rules 规范(ID/绑定属性/pointer-events),回画图 skill 修 |
| mimicConfig 丢布局 | 保存即版本;可视化配置页有"整页覆盖"风险,操作前备份(见配置中心数据安全风险清单) |

