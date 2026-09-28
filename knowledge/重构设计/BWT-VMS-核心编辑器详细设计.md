# BWT-VMS 配置中心核心编辑器详细设计(健康/能效/油舱)

> 把《配置中心详细设计》第 7/8/9 节的三个高复杂度编辑器展开到**可开发**级别:交互细节、数据模型、校验、API、实现要点、技术选型。
>
> 这三个都含"曲线/图表编辑",可复用同一套曲线编辑器组件;健康部件树还含"树编辑器 + 公式编辑器"。

---

## 一、机舱健康部件树编辑器

对应 D05。替代手填 mmha_component/_device_relation/_model_info 三表。

### 1.1 交互详设

**布局**:左侧部件树 + 右侧节点属性面板 + 顶部工具栏(AI 辅助/校验/预览/保存)。

**部件树(左)**:
- 基于 **dnd-kit** 实现树形拖拽(拖动节点改父子关系,防止成环)
- 节点右键:新增子部件/删除/复制/上移下移
- 图标区分:非叶子(汇总,显示汇总方式)、叶子(显示绑定的工况点数)
- 搜索/展开全部/收起

**节点属性面板(右)**:
- 通用:component_key(自动生成)/component_name(中英)/weight_score/is_leaf(切换;切到叶子显示绑定区)
- **非叶子**:汇总方式下拉(MAX/MIN/AVERAGE/WEIGHT/RADAR,带说明 tooltip)
- **叶子**:
  - 绑定工况点:多选 device_key(从工况点配置来,支持搜索)
  - 计算模型:LINE_MODEL / NON_LINE_MODEL 单选
  - 模型参数(见 1.2,曲线编辑器)
  - 运行条件公式(见 1.3,条件编辑器)
- SVG 预览:该部件在机舱图上的位置(可选,链接到机舱图元)

**工具栏**:
- AI 辅助:① 一键生成全船结构树(通义千问,从工况点自动归类);② 为选中部件生成关联工况点;③ 批量生成缺陷建议
- 校验:跑校验引擎,错误/警告列表
- 预览:输入模拟工况值,显示各部件健康分(递归计算演示)
- 保存:生成版本

### 1.2 计算模型参数编辑(曲线编辑器组件)

**LINE_MODEL(线性)**:
- 三点定义:normal_value→normalScore(默认100)、normal_value_max→boundaryScore(默认60)、normal_value_min→boundaryScore(默认60)
- 图表:横轴工况值,纵轴健康分;三点拖动调整;实时显示线性插值曲线
- 边界外直接 boundaryScore

**NON_LINE_MODEL(非线性)**:
- 抛物线 y=ax²+bx+c,三点构造(正常点为顶点 + 两个边界点)
- 图表拖点 + 自动拟合;或直接调系数

**实时预览**:输入工况值,显示该叶子健康分

### 1.3 运行条件公式编辑器(复用组件)

- 变量(工况点 device_key)从左面板拖入
- 运算符按钮:>/</>=/<=/==/!=/&&/||
- 预览:输入各 device_key 值,显示 true/false
- 用 **mathjs** 求值,校验语法
- 存 operating_condition_eval,如 `{deviceKey1}>0 && {deviceKey2}<100`

### 1.4 数据模型

沿用:mmha_component(component_key/parent_component_key/model_summary_type_code/operating_condition_eval/is_leaf/weight_score)+ mmha_component_device_relation(component_key/device_key/normal_value/max/min)+ mmha_component_model_info(device_key/model_type_code)。加 version/last_operator。

### 1.5 校验规则
- 树无环(parent 链不成环)
- 叶子节点必须绑定 ≥1 个工况点
- 叶子模型参数完整(normal/max/min)
- 非叶子必须有子节点
- RADAR 汇总:子节点数 ≥3
- 运行条件公式语法可解析(mathjs)

### 1.6 API
`GET/POST /config/health/{shipId}/tree`(整树存取)、`/component`(单节点)、`/model`(模型参数)、`/ai-generate`(通义)、`/condition/preview`、`/health-preview`(传工况值返回各部件分)

### 1.7 实现要点
- 树拖拽:**dnd-kit**(可排序树形,React 主流;备选 react-beautiful-dnd/@atlaskit/tree)
- 曲线编辑:**ECharts** 自定义拖点 + 最小二乘拟合(复用,见通用章)
- 公式求值:**mathjs**(安全 eval,限定变量域)
- AI:后端调通义(PoC 已有),前端异步轮询生成进度
- 性能:树大时虚拟化(react-window)

### 1.8 技术选型
dnd-kit + ECharts + mathjs + react-window(大树)

---

## 二、能效配置编辑器(SFOC 曲线)

对应 D06。替代手填 tab_equipment/tab_statistics_param/tab_ship_info_extend。

### 2.1 交互详设

**Tab 化**:① 能效设备 ② 流量计 ③ SFOC 曲线 ④ 额定参数。

**① 能效设备**:
- 设备表:主机/发电机/锅炉(type/number/uuid),增删改
- 每设备绑工况点(信号路由):下拉选 device_key 并标 device_type(1 主机功率/2 主机转速/13 发电机功率/16 轴发/3 SOG 等)
- 一个设备多工况点绑定(功率/转速/排温...)

**② 流量计配置**:
- device_type=41(流量计)+ 40(密度)
- **group_desc 可视化拼装器**(关键,替代手写 `mainframe_1_2_IN`):
  - 设备类型下拉(mainframe/dynamo/boiler)+ 编号输入 + 油种下拉(字典 2 轻/3 重/9 LNG)+ 进出口(IN/OUT)
  - 自动拼成 `mainframe_1_2_IN`,实时预览
  - 校验格式

**③ SFOC 曲线编辑器**(核心,见 2.2)

**④ 额定参数表单**:
- paramId 枚举化表单:27 主机额定功率/28 额定转速/70 发电机额定功率/107 轴发额定/15 设计航速/98 DWT/53 台数/106 MCR 限制
- 每项:值 + 单位提示 + 是否必填(校验驱动)
- 存 tab_ship_info_extend

### 2.2 SFOC 曲线编辑器

- 横轴:主机负荷率 0-100%;纵轴:SFOC 理论值
- 公式:`y = a*load² + b*load + c`
- **两种编辑方式**:
  - 拖点:图上拖 2-3 个点(负荷率, SFOC),最小二乘拟合 a/b/c,实时显示曲线
  - 直接输入 a/b/c,显示曲线
- 参考线:额定负荷区间(标灰带)
- 保存:value=`"a,b,c"` → tab_ship_info_extend param_id=104
- 预览:输入负荷率,显示理论 SFOC

### 2.3 数据模型
tab_equipment(uuid/type/number)+ tab_statistics_param(device_key/device_type/group_desc/equipment_uuid)+ tab_ship_info_extend(param_id/value)。加 version。

### 2.4 校验规则
- 主机绑定功率(type 1)或转速(type 2)工况点(否则警告"将降级估算")
- 流量计 group_desc 格式正确 + 油种在字典
- SFOC param_id=104 三数字逗号分隔(否则警告"偏差率无意义")
- 额定功率/转速(paramId 27/28)齐全(否则警告"螺旋桨定律估算无基准")
- 设计航速/DWT(15/98)齐全(否则 EEXI/CII 警告)

### 2.5 API
`GET/POST /config/energy/{shipId}/equipment`、`/statistics-param`、`/sfoc-curve`、`/rated-params`、`/group-desc/preview`、`/sfoc/fit`(传点返回 a/b/c)

### 2.6 实现要点
- SFOC 曲线:**ECharts** 拖点 + 后端或前端最小二乘拟合(二次多项式)
- group_desc 拼装器:下拉联动 + 字符串生成 + 正则校验
- 额定参数:paramId 枚举常量化(ShipParamModelConstant 已有基础,对齐)

### 2.7 技术选型
ECharts(拖点拟合)+ 前端拟合或后端计算

---

## 三、油舱与舱容曲线编辑器

对应 D07。替代手填 tab_tank_device/_menu/tab_height_cabin。

### 3.1 交互详设

**布局**:左侧舱位树 + 右侧舱属性 + 舱容曲线编辑器。

**舱位树(左)**:
- 主舱(menu,油种分类:淡水舱/轻油舱/重油舱/滑油舱) + 子舱(device)
- dnd-kit 拖拽组织(子舱拖到不同主舱改分类)
- 油种编码下拉驱动主舱类型

**舱属性(右上)**:
- tank_name(中英)/tank_device_type(油种字典)/maximum/minimum/density/unit
- 液位遥测舱:绑 device_key(从工况点选,液位通道)
- sort/status

**舱容曲线编辑器(右下,核心,见 3.2)**

### 3.2 舱容曲线编辑器

- 横轴:液位高度(m);纵轴:舱容体积(m³)
- **两种视图**:
  - 图表视图:点击图区添加点(height,cabin),拖动调整位置,右键删除;实时显示**线性插值曲线**
  - 表格视图:行编辑(height,cabin 对),批量增删
- **实时插值预览**:鼠标在图上移动,显示当前高度对应的体积(线性插值);用于核对
- 校验:≥5 点、height 单调递增、cabin ∈ [0, maximum]
- 保存:每点一行 → tab_height_cabin

### 3.3 数据模型
tab_tank_device_menu(tank_menu_id/tank_type)+ tab_tank_device(tank_id/tank_device_type/device_key/maximum/density)+ tab_height_cabin(tank_id/height/cabin)。加 version。

### 3.4 校验规则
- 舱容曲线点数 ≥5
- height 单调递增
- cabin ∈ [0, maximum],maximum>0
- 液位遥测舱(tank_device_type 液位类)必须绑 device_key

### 3.5 API
`GET/POST /config/oil-tank/{shipId}/menu`、`/tank`、`/height-cabin`、`/interpolate/preview`(传高度返回体积)

### 3.6 实现要点
- 曲线编辑器:复用通用曲线组件(见通用章)
- 插值:前端线性插值预览(与后端 heightConvertCubic 一致,校对算法一致)

### 3.7 技术选型
ECharts(拖点 + 插值曲线)

---

## 四、通用技术点(跨编辑器复用)

### 4.1 曲线编辑器组件(复用三处)
健康模型参数(LINE/NON_LINE)+ SFOC 曲线 + 油舱舱容曲线 —— 同一组件,配置:横纵轴含义/拖点/拟合方式(线性插值/二次拟合)/校验。

技术:**ECharts** 自定义 series + graphic 拖拽 + 最小二乘(mathjs 或自实现)。封装成 `<CurveEditor config={} value={} onChange={} />`。

### 4.2 树编辑器组件(复用三处)
健康部件树 + 舱位树 + 工况点菜单树 —— 同一组件,配置:节点类型/拖拽规则/右键菜单/属性面板。

技术:**dnd-kit** sortable tree(自封装,或用 @atlaskit/tree)。封装成 `<TreeEditor config={} nodes={} onChange={} />`。

### 4.3 公式/条件编辑器组件(复用两处)
trans_data(工况点换算)+ operating_condition_eval(健康运行条件)—— 同一组件,配置:可用变量/运算符/函数。

技术:变量拖入 + mathjs 求值 + 预览。封装成 `<FormulaEditor variables={} value={} onChange={} />`。

### 4.4 字典驱动
油种/设备类型/汇总方式/信号路由 device_type 等全部从 tab_sys_dict 或常量加载,下拉统一。

### 4.5 AI 集成(健康用)
通义千问调用(后端,已有),前端异步任务 + 进度轮询(复用 mmha 的 judgmentUseOfTy 机制)。

---

## 五、实施顺序与依赖

| 编辑器 | 阶段 | 依赖 | 工时(前端) |
|---|---|---|---|
| 油舱舱容曲线 | R2 随油水域 | 工况点配置 + 曲线组件 | 1pw |
| 能效 SFOC | R2 随能效域 | 工况点配置 + 曲线组件 + group_desc 拼装 | 1.5pw |
| 健康部件树 | R2 末/R3 随健康域 | 工况点配置 + 树组件 + 公式组件 + AI | 2pw |
| 通用组件(曲线/树/公式) | R2 先行 | — | 2pw(一次开发三处复用) |

**建议**:R2 先做"通用组件(曲线/树/公式编辑器)",三个编辑器即可快速拼装。

---

## 六、与配置中心主文档的关系

校验规则、API、版本/下发/模板等通用能力见《配置中心详细设计》;本文件聚焦三个编辑器的**开发级实现细节**(交互/数据模型/实现要点/技术选型)。两个文档配合开发使用。
