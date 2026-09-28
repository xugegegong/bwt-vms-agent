# BWT-VMS 产品功能设计与实现 · CCTV 视频监控域(D10)

> 更新日期:2026-09-04(修改本文档后请同步更新此日期,并到 Dify 知识库重新上传该文件)

> 遵循模板。本域对接海康/公司 CCTV,提供实时预览、回放、云台控制。

---

## 1. 域概述

| 项 | 内容 |
|---|---|
| 定位 | 集成多种视频方案,在 Web 端实时预览/回放船端摄像头,支持云台控制与抓拍 |
| 核心价值 | 远程可视化监控船端现场(驾驶台/机舱/甲板) |
| 边界 | 只管视频接入与展示;行为识别(视频 AI 分析)在 D11 |
| 三方案互斥 | 由 `window.cctvHK` 决定:1 公司 CCTV / 2 海康综合安防平台 / 3 海康开放平台 NVR 直连(当前默认 3) |
| ⚠️ 发现 | 本 Java 后端**无独立摄像头配置表**;方案 3 不经 Java 后端直连 NVR |

---

## 2. 功能点清单

```
F10 CCTV 视频监控
├─ F10-1 三种视频接入方案(cctvHK 1/2/3)
├─ F10-2 实时预览与历史回放(方案 1 HTTP-FLV)
├─ F10-3 PTZ 云台控制
├─ F10-4 HikvisionController 代理(方案 2)
└─ F10-5 前端 CCTV 组件(摄像头树/分屏/甲板视图)
```

---

## 3. 逐功能点详述

### F10-1 三种视频接入方案

**(1) 方案对比**:

| 维度 | 方案 1 公司 CCTV | 方案 2 海康综合安防 | 方案 3 海康 NVR 直连(默认) |
|---|---|---|---|
| 播放技术 | flv.js + MSE(HTTP-FLV) | iframe 嵌海康平台 | webVideoCtrl.js + OCX 插件 |
| 协议 | HTTP-FLV | 海康平台自有 | 海康私有 SDK(TCP 直连 NVR) |
| 渲染 | 16 个 Video 组件 + video 标签 | iframe hikvision_video.html | iframe NVRVideo.html |
| 摄像头列表 | cctvBaseURL/Video/CameraList | /sys/hiki/camera/search | iframe 内登录 NVR 枚举 |
| 需装插件 | 否 | 否 | 是(HCWebSDKPlugin.exe) |
| 经 Java 后端 | 否(公司自建服务) | 是(/hiki 代理) | 否 |

**(2) 切换**:`public/config/urlConfig/urlConfig.js` 的 `window.cctvHK`,前端 cctv 组件按值走不同分支。

---

### F10-2 实时预览与历史回放(方案 1)

**(1) 实时预览流程**:
1. 校验船在线(`fetch /Video/ShipStatus`)
2. 建立 WebSocket `/tvws/VideoChannel`(session_id + 3s 心跳 + 船状态/抓拍推送)
3. `POST /Video/PlayVideo`(session_id + 摄像头列表)→ 返回 mediaurl(HTTP-FLV 地址)
4. flv.js:`createPlayer({type:'flv',isLive:true})` → attachMediaElement → load → play
5. 40s 未触发 onPlaying 自动 unload + 通知后端停流

**(2) 历史回放**:选时间区间 → `POST /Video/VideoPlayBack`(start_time/end_time)→ flv.js 播放回放流;进度条每秒+1。

**(3) 抓拍**:`POST /Video/CaptureImage`,按 camera.interval 分钟轮询,图片经 WS imgurl 异步推送。

**(4) 资源释放**:页面关闭 onbeforeunload 发 Video/CanelVideo(实时)或 ControlVideoPlayBack(历史)。

---

### F10-3 PTZ 云台控制

方向/变焦/聚焦/光圈按钮 → `POST /Video/VideoControl`(session_id/dir/zoom/focus/aperture/ip/channel)。方案 2/3 由 iframe 内海康 UI 处理。

---

### F10-4 HikvisionController `/hiki/*`(方案 2 代理)

**(1) 设计**:Java 后端作海康 artemis API 代理——接收前端请求 → HmacSHA256 签名 → 转发海康平台 → 原样回传。配置 HikvisionConfig(url/appKey/appSecret)。

**(2) 接口**:

| method | path | 转发目标 | 用途 |
|---|---|---|---|
| POST | `/hiki/camera/search` | /artemis/api/resource/v2/camera/search | 按船名/区域搜索监控点 |
| POST | `/hiki/camera/previewURLs` | /artemis/api/video/v2/cameras/previewURLs | 取预览流地址 |
| POST | `/hiki/regionTree/search` | region/nodesByParams + regions/regionIndexCode/cameras | 按区域名(船名)查摄像头树 |

**(3) 签名**:Header X-Ca-Key/X-Ca-Timestamp/X-Ca-Signature(HmacSHA256 Base64)。

---

### F10-5 前端 CCTV 组件

**(1) 结构**(`src/containers/cctv/`):index.jsx(3318 行)+ data.js(mock)+ Deck/DeckOne~Four(甲板视图)。

**(2) 摄像头树**:按船/区域/甲板三种维度分组(顶部 groupBy 切换);antd Tree + onCheck。

**(3) 分屏宫格**:gridType 1/4/9/16;方案 1 用 16 个 Video 组件 CSS 布局 + 轮播;方案 2/3 发 layout 给 iframe。

**(4) 甲板视图**(Deck 系列):⚠️ 不是分屏,是**船舶甲板俯视图导航**,按绝对坐标放 DeckCamera(点击弹抓拍图)。

**(5) 行为识别 tab**:hk==1||3 显示,内嵌 BehaviorTable 调 /behaviorRecognition/realDataList。

---

## 4. 数据流

```
方案1: 前端 → 公司CCTV服务(/Video/* + WS /tvws/VideoChannel) → flv.js播放
方案2: 前端 → /sys/hiki/*(Java HmacSHA256签名代理) → 海康artemis → iframe播放
方案3: 前端 iframe → webVideoCtrl.js → HCWebSDKPlugin.exe → NVR直连
```

---

## 5. 风险与改造建议

| 问题 | 诊断 ID | 建议 |
|---|---|---|
| 方案 3 NVR 配置硬编码 | DIAG-P0-S-05 类 | nvrUrl(ip/user/password)移配置 |
| 方案 3 需装 exe 插件 | —(体验) | 评估迁方案 2 或 WebRTC |
| Video 组件硬编码旧地址 | —(bug) | 修 cctv.hbluewhale.com:8081 硬编码 |
| 无统一摄像头配置表 | —(架构) | 评估引入摄像头元数据表 |
| cctv/index.jsx 3318 行 | —(可维护性) | 按方案/功能拆分 |
