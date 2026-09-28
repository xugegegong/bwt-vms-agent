# BWT-VMS Agent 跨智能体平台最佳组合实施文档

> 更新日期:2026-09-22  
> 适用项目:BWT-VMS 新船交付、ISCU 采集配置、机舱健康、能效配置及后续交付自动化  
> 目标:让 Codex、Claude、DeepSeek Harness、Dify 及其他 Agent 共享同一套知识、记忆和执行能力,同时避免绑定单一客户端。

---

# 0. 结论先行

如果只看操作步骤,直接跳到本文最后的「18. 按章落地操作」。本章给出整体原则,第 18 章把每一章拆成文件、命令、产出物和验收标准。

不要统一某一个聊天客户端,也不要依赖某个平台的隐藏记忆。

最佳组合:

```text
Git 仓库：共享知识、Skill、项目记忆和版本
HTTP API：共享确定性执行能力
MCP Server：共享 Agent 工具接口
DeepSeek Harness：团队推荐交互入口
Codex / Claude / Dify：通过适配层接入
```

核心原则:

```text
业务规则放 Git
执行逻辑放工具服务
客户端只负责交互
高风险操作必须人工确认
```

---

# 1. 为什么采用这个组合

## 1.1 只统一 DeepSeek Harness 的问题

优点:

- 团队界面一致。
- 终端、插件和自定义能力集中。
- 培训成本低。

风险:

- 厂商和版本锁定。
- 插件协议变化影响全组。
- 其他 Agent 无法直接复用插件。
- 业务逻辑容易散落在插件里。

结论:

DeepSeek Harness 可以作为推荐入口,但不能成为唯一执行载体。

## 1.2 只使用 GitHub Skill 的问题

优点:

- 文档和规则可以版本化。
- Codex、Claude、DeepSeek Harness 等可读取同一份知识。
- 方便代码评审和变更追踪。

限制:

- Skill 支持程度因客户端不同。
- Skill 不能替代真正的数据库操作。
- Skill 不能可靠共享每个客户端的实时会话。
- Skill 不能自动同步各 Agent 的内部隐藏记忆。

结论:

Skill 用于共享知识和流程,执行能力必须由 HTTP/MCP 服务提供。

## 1.3 最佳职责拆分

| 层 | 负责什么 | 不负责什么 |
|---|---|---|
| Git 仓库 | 规则、Skill、项目记忆、审计样板 | 不持有数据库密码 |
| HTTP API | 参数校验、生成、查询、落库、回滚 | 不负责自然语言对话 |
| MCP Server | 把 API 暴露为 Agent 工具 | 不重复实现业务逻辑 |
| Agent 客户端 | 理解用户、选择工具、展示结果 | 不直接连 MySQL |
| Dify | 团队问答、审批入口、流程展示 | 不作为唯一业务执行层 |

---

# 2. 目标架构

```text
┌─────────────────────────────────────────────────────┐
│ Agent 客户端层                                      │
│ Codex | Claude | DeepSeek Harness | Dify | 未来客户端 │
└───────────────────────┬─────────────────────────────┘
                        │ MCP / HTTP
┌───────────────────────▼─────────────────────────────┐
│ BWT Agent Gateway                                   │
│ 鉴权 | 审批 | 审计 | 限流 | 参数校验                 │
└───────────────────────┬─────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────┐
│ ISCU Agent Service                                  │
│ precheck | generate | snapshot | verify             │
│ apply | rollback | datacap_verify                   │
└───────────────────────┬─────────────────────────────┘
                        │ SSH + MySQL
┌───────────────────────▼─────────────────────────────┐
│ 7.61 / 船端服务器 / 测试环境                         │
└─────────────────────────────────────────────────────┘
```

推荐先部署在:

```text
192.168.7.106
```

原因:

- Dify 已在 7.106。
- 7.106 可访问 7.61。
- 工具服务可以同时给 Dify、Codex、DeepSeek Harness 使用。

---

# 3. 推荐仓库结构

建议新建 Git 仓库:

```text
bwt-vms-agent-kit/
├── README.md
├── AGENTS.md
├── CHANGELOG.md
├── contracts/
│   ├── iscu-agent-openapi.yaml
│   ├── error-codes.md
│   └── approval-flow.md
├── skills/
│   ├── iscu-config/
│   │   ├── SKILL.md
│   │   ├── references/
│   │   └── scripts/
│   ├── sysdb-config/
│   └── health-config/
├── server/
│   ├── iscu_agent_api.py
│   ├── iscu_generator.py
│   ├── iscu_verify.py
│   ├── iscu_apply.py
│   ├── ssh_client.py
│   ├── db_repo.py
│   └── audit.py
├── mcp/
│   ├── server.py
│   └── tools.yaml
├── adapters/
│   ├── deepseek-harness/
│   ├── codex/
│   ├── claude/
│   └── dify/
├── memory/
│   ├── projects/
│   │   ├── tka2501.md
│   │   └── tka2502-2504.md
│   ├── decisions/
│   ├── run-log.jsonl
│   └── current-context.md
└── logs/
    └── .gitkeep
```

Git 仓库中禁止提交:

```text
SSH 私钥
MySQL 密码
Redis 密码
生产数据库 dump
船端敏感配置文件原文件
```

---

# 4. 统一执行接口

## 4.1 API 版本

统一使用:

```text
/v1/iscu/*
```

旧接口可以暂时保留:

```text
/api/iscu/*
```

新实现优先使用 `/v1`。

## 4.2 接口清单

| 方法 | 路径 | 风险 | 说明 |
|---|---|---|---|
| GET | `/health` | 无 | 健康检查 |
| POST | `/v1/iscu/precheck` | 无 | 参数预检 |
| POST | `/v1/iscu/generate` | 低 | 生成 JSON/TXT 文件 |
| POST | `/v1/iscu/snapshot` | 低 | 备份 iscu/XML |
| POST | `/v1/iscu/verify` | 低 | 只读校验四表 |
| POST | `/v1/iscu/apply` | 高 | 写四表,必须确认 |
| POST | `/v1/iscu/rollback` | 高 | 回放备份,必须确认 |
| POST | `/v1/iscu/datacap/verify` | 高 | 重启 datacap,必须确认 |

## 4.3 统一错误格式

```json
{
  "ok": false,
  "error_code": "ISCU_PARAM_INVALID",
  "message": "client_no 缺失或非法",
  "details": {
    "field": "client_no"
  },
  "request_id": "req_20260914_001"
}
```

错误码建议:

```text
ISCU_PARAM_INVALID
ISCU_SSH_FAILED
ISCU_MYSQL_FAILED
ISCU_CONFIG_MISMATCH
ISCU_APPROVAL_REQUIRED
ISCU_APPROVAL_EXPIRED
ISCU_BACKUP_FAILED
ISCU_APPLY_FAILED
ISCU_ROLLBACK_FAILED
ISCU_DATACAP_FAILED
```

---

# 5. 共享 Skill 规范

## 5.1 Skill 的目标

Skill 只描述:

- 什么时候触发。
- 需要什么输入。
- 执行顺序。
- 禁止事项。
- 应该调用哪个工具。
- 如何验收。

Skill 不保存密码,也不直接执行数据库操作。

## 5.2 Skill 模板

```markdown
---
name: bwt-vms-iscu-config
description: BWT-VMS ISCU 采集配置。生成、校验、落库和 datacap 出数验证。
version: 1.0.0
updated: 2026-09-14
---

# 适用场景

用户提到 ISCU、采集、Modbus、JsonString、hldkeymap、client、datacap 时使用。

# 输入

- ship_id
- client_no
- comm_type
- slave_id
- fun_code
- start_addr
- end_addr
- point_type
- coefficient

# 必须调用的工具

1. iscu_precheck
2. iscu_generate
3. iscu_verify
4. iscu_apply,必须人工确认
5. iscu_datacap_verify,必须人工确认

# 禁止事项

- 不直接拼 SQL。
- 不把密码写入回答。
- 不在未备份时写库。
- S1 未出数前不执行 S2。
```

## 5.3 跨客户端适配

同一个 Skill 通过适配层暴露给:

| 客户端 | 推荐方式 |
|---|---|
| Codex | 读取 `AGENTS.md` + MCP |
| Claude | 读取项目说明 + MCP |
| DeepSeek Harness | 插件或终端调用 MCP/HTTP |
| Dify | 知识库 + HTTP Request 工具 |

适配层只保存连接方式,不复制 Skill 内容。

---

# 6. 共享记忆设计

## 6.1 不要追求“实时共享隐藏会话”

不同 Agent 的会话机制不通用。正确做法是把可共享部分写入 Git:

- 项目状态。
- 已完成事项。
- 未决问题。
- 关键决策。
- 上次执行结果。
- 风险和回滚路径。

## 6.2 项目记忆模板

```markdown
# TKA2501

> 更新时间: 2026-09-14 18:00
> 更新人: Codex

## 当前阶段

S1

## 已完成

- AMS/BTP/LOAD 配置已核对。

## 待办

- 等待 MOXA 网络。

## 关键参数

- ship_id: 35701001
- iscu ShipId: 1

## 最近一次验证

- verify: 通过
- apply: 未执行

## 风险

- 不得在 S1 出数前修改 tab_engine_param。
```

## 6.3 Run Log

每次执行追加一条 JSON:

```json
{
  "time": "2026-09-14T18:00:00+08:00",
  "agent": "Codex",
  "tool": "iscu_verify",
  "ship_id": "35701001",
  "client_no": 4,
  "result": "passed",
  "request_id": "req_20260914_001"
}
```

Run Log 只记录摘要,不记录密码和完整敏感配置。

---

# 7. MCP Server 设计

## 7.1 MCP 工具

```text
iscu_precheck
iscu_generate
iscu_snapshot
iscu_verify
iscu_apply
iscu_rollback
iscu_datacap_verify
```

## 7.2 工具描述示例

```yaml
name: iscu_verify
description: 只读校验 BWT-VMS ISCU 四表。不得写库。
input_schema:
  type: object
  required: [client_no]
  properties:
    ship_id:
      type: string
    client_no:
      type: integer
    topic_name:
      type: string
```

## 7.3 MCP 与 HTTP 的关系

```text
MCP Server 不直接连 MySQL
MCP Server 调用本地 ISCU Agent Service
ISCU Agent Service 负责 SSH/MySQL
```

这样 Dify、Codex、DeepSeek Harness 都通过同一条执行链。

---

# 8. 客户端适配

## 8.1 DeepSeek Harness

定位:

```text
团队推荐交互入口
```

插件要求:

1. 能调用 MCP 或 HTTP。
2. 能配置终端命令。
3. 能展示审批提示。
4. 能输出 request_id。
5. 不保存 MySQL/SSH 密码。

推荐命令:

```text
iscu-health
iscu-precheck --client 4 --type single_float_badc
iscu-generate ...
iscu-verify --ship-id 35701001 --client 4
iscu-apply --confirm <token>
iscu-datacap-verify --confirm <token>
```

## 8.2 Codex

入口:

```text
AGENTS.md + MCP
```

职责:

- 读取 Git 记忆。
- 调用 MCP 工具。
- 修改文档和代码。
- 生成实施计划。

Codex 不直接保存数据库密码。写操作仍走服务。

## 8.3 Claude

入口:

```text
项目说明 + MCP
```

适配内容与 Codex 类似。

## 8.4 Dify

Dify 保留三种用法:

1. 知识库问答。
2. Chatflow 调 `/v1/iscu/verify` 和 `/v1/iscu/generate`。
3. Agent + MCP/HTTP 工具执行受限操作。

Dify 不直接持有 SSH/MySQL 凭据。

---

# 9. 7.106 部署步骤

## 9.1 创建目录

```bash
sudo mkdir -p /opt/bwt-agent/{app,backups,logs,data}
sudo chown -R root:root /opt/bwt-agent
```

## 9.2 上传服务

从 Windows:

```powershell
scp "D:\gitlab\bwt-inland-feature\bwt-inland-feature\S1信号清单工具\iscu_agent_api.py" root@192.168.7.106:/opt/bwt-agent/app/iscu_agent_api.py
```

## 9.3 环境文件

```bash
sudo tee /etc/bwt-agent.env >/dev/null <<'EOF'
BWT_SSH_HOST=192.168.7.61
BWT_SSH_PORT=22
BWT_SSH_USER=root
BWT_SSH_KEY=/root/.ssh/id_rsa
BWT_MYSQL_CONTAINER=mysql
BWT_MYSQL_USER=root
BWT_MYSQL_PASSWORD=<MySQL密码>
BWT_BIND_HOST=0.0.0.0
BWT_BIND_PORT=18000
BWT_BACKUP_DIR=/opt/bwt-agent/backups
BWT_LOG_DIR=/opt/bwt-agent/logs
EOF

sudo chmod 600 /etc/bwt-agent.env
```

如果实际密码或密钥不同,修改后再启动。

## 9.4 systemd

```bash
sudo tee /etc/systemd/system/bwt-agent.service >/dev/null <<'EOF'
[Unit]
Description=BWT VMS Agent Service
After=network.target

[Service]
Type=simple
EnvironmentFile=/etc/bwt-agent.env
ExecStart=/usr/bin/python3 /opt/bwt-agent/app/iscu_agent_api.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now bwt-agent
sudo systemctl status bwt-agent --no-pager
```

## 9.5 Dify SSRF 放行

编辑 Dify `.env`:

```text
SSRF_PROXY_ALLOW_PRIVATE_IPS=192.168.0.0/16
```

重启:

```bash
cd /opt/dify/docker
docker compose up -d
```

## 9.6 健康检查

```bash
curl -s http://192.168.7.106:18000/health
```

期望:

```json
{"ok": true}
```

---

# 10. 分阶段上线

## 10.1 第一阶段:只读能力

范围:

```text
health
precheck
verify
```

验收:

- 7.61 查询正常。
- Dify SSRF 正常。
- Dify HTTP Body 变量正确。
- `ShipId=1` 语义正确。

## 10.2 第二阶段:生成能力

范围:

```text
generate
snapshot
```

验收:

- 生成文件路径正确。
- JSON/TXT MD5 正确。
- 大点数配置走外部服务。
- 生成分支不写 MySQL。

## 10.3 第三阶段:受控写库

范围:

```text
apply
rollback
datacap_verify
```

验收:

- 每次写库前自动备份。
- apply 必须有确认 token。
- 四表写入成功。
- MD5 与文件一致。
- 失败能回滚。
- datacap 验证闸门正常。

---

# 11. 安全与权限

## 11.1 凭据管理

凭据只允许放在:

```text
/etc/bwt-agent.env
Vault 或系统密钥服务
受限权限的部署配置
```

禁止:

```text
SKILL.md
Dify 提示词
Git commit
日志
聊天记录
```

## 11.2 写操作审批

写操作必须具备:

- request_id
- 操作人
- 目标 ship_id
- 目标 client_no
- 参数摘要
- 备份路径
- 确认 token
- 过期时间

## 11.3 审计

每次执行记录:

```json
{
  "request_id": "...",
  "agent": "DeepSeek Harness",
  "action": "iscu_apply",
  "result": "success",
  "backup_path": "...",
  "duration_ms": 1200
}
```

---

# 12. 运行规范和团队约定

## 12.1 开始任务前

每个 Agent 必须先读:

```text
AGENTS.md
memory/current-context.md
memory/projects/<project>.md
对应 Skill
```

## 12.2 结束任务后

必须写回:

```text
memory/projects/<project>.md
memory/run-log.jsonl
```

涉及决定时追加:

```text
memory/decisions/YYYYMMDD-topic.md
```

## 12.3 提交规则

```text
文档和 Skill 变更:常规 commit
高风险 API 变更:单独 PR
数据库逻辑变更:必须附测试结果
```

---

# 13. 首次导入 ISCU 的操作

## 13.1 只读检查

```bash
curl -s -X POST http://192.168.7.106:18000/v1/iscu/verify \
  -H 'Content-Type: application/json' \
  -d '{"ship_id":"35701001","client_no":4,"topic_name":"AMS4"}'
```

## 13.2 生成配置

```bash
curl -s -X POST http://192.168.7.106:18000/v1/iscu/generate \
  -H 'Content-Type: application/json' \
  -d '{
    "ship_id":"35701001",
    "client_no":4,
    "comm_type":"TCP",
    "slave_id":1,
    "fun_code":3,
    "start_addr":40001,
    "end_addr":40010,
    "point_type":"单浮点数(badc)",
    "coefficient":[0,1,0]
  }'
```

## 13.3 写库前预览

必须向用户展示:

```text
目标环境
ship_id
client_no
TopicName
协议
SlaveId
FC
地址范围
工况点类型
DataTypeRegister
点数
orderkey 范围
备份路径
```

## 13.4 apply

```bash
curl -s -X POST http://192.168.7.106:18000/v1/iscu/apply \
  -H 'Content-Type: application/json' \
  -d '{
    "ship_id":"35701001",
    "client_no":4,
    "topic_name":"AMS4",
    "confirm_token":"一次性token"
  }'
```

## 13.5 datacap verify

```bash
curl -s -X POST http://192.168.7.106:18000/v1/iscu/datacap/verify \
  -H 'Content-Type: application/json' \
  -d '{
    "ship_id":"35701001",
    "client_no":4,
    "topic_name":"AMS4",
    "confirm_token":"一次性token"
  }'
```

---

# 14. 验收清单

## 14.1 平台验收

- [ ] Git 仓库结构建立。
- [ ] Skill 可被至少两个客户端读取。
- [ ] MCP Server 能被至少两个客户端调用。
- [ ] API 统一使用 request_id。
- [ ] 日志不含密码。
- [ ] 7.106 服务开机自启。
- [ ] Dify SSRF 已放行。

## 14.2 ISCU 功能验收

- [ ] precheck 能拦截缺参。
- [ ] generate 能生成正确文件和 MD5。
- [ ] verify 能查四表。
- [ ] apply 必须 token。
- [ ] apply 前有备份。
- [ ] rollback 可恢复。
- [ ] datacap verify 能输出 Redis 结果。

## 14.3 跨客户端验收

- [ ] DeepSeek Harness 能调用 generate 和 verify。
- [ ] Codex 能读取同一 Skill。
- [ ] Claude 能读取同一项目记忆。
- [ ] Dify 能调用 verify 和 generate。
- [ ] 所有客户端看到相同的 orderkey 和规则。

---

# 15. 常见问题

## 15.1 为什么不让 Agent 直接连 MySQL

因为每个 Agent 的密钥管理、审计和权限都不可控。服务层集中处理更安全。

## 15.2 为什么同时要 MCP 和 HTTP

- HTTP 适合 Dify、脚本和运维。
- MCP 适合支持工具协议的 Agent。
- 两者的底层都调用同一个执行服务。

## 15.3 为什么需要 Git

Agent 会话可能丢失,人员会变化。Git 是团队可读、可审、可回滚的共同记忆。

## 15.4 Skill 是否等于记忆

不等于。Skill 是操作手册,记忆是项目状态和执行记录。

## 15.5 统一 DeepSeek Harness 是否足够

不够。它应该是团队推荐入口,而不是唯一执行平台。

---

# 16. 推荐实施排期

## 第 1 天

- 建 Git 仓库。
- 整理 Skill 和项目记忆模板。
- 确认 7.106 服务端口和 SSH 权限。

## 第 2 天

- 完成 precheck、generate、verify。
- 配置 Dify SSRF。
- 跑通 Dify verify。

## 第 3 天

- 增加 MCP Server。
- 接入 Codex。
- 接入 DeepSeek Harness。

## 第 4 天

- 增加 snapshot、apply、rollback。
- 加入人工确认和审计日志。

## 第 5 天

- 做一次 7.61 试验落库。
- 做一次 rollback。
- 做一次 datacap verify。
- 编写团队使用手册。

---

# 17. 最终建议

立即执行:

```text
1. 建 Git 仓库 bwt-vms-agent-kit
2. 把 ISCU API 服务从单脚本拆成 server 模块
3. 先做 MCP 的 precheck/generate/verify
4. DeepSeek Harness 作为推荐入口
5. Codex 负责平台建设和数据库实验
6. Dify 后续只作为 UI 和知识问答入口
```

不要做:

```text
1. 不要把所有业务逻辑塞进 Dify Chatflow
2. 不要只依赖一个 Agent 客户端
3. 不要把密码写进 Skill
4. 不要让模型生成任意 SQL
5. 不要在未确认、未备份时写数据库
```

最终形态:

```text
任何 Agent 都能读取同一套 Skill
任何 Agent 都能调用同一套 MCP/HTTP 工具
任何执行都有 request_id、权限、备份和审计
任何项目状态都能在 Git 中接续
```

---

# 18. 按章落地操作

本节回答一个实际问题:

```text
前面每一章,到底要建什么文件、执行什么命令、由谁做、怎么算完成？
```

推荐执行顺序:

```text
第 1 步:建仓库和目录
第 2 步:写 API 契约
第 3 步:写 Skill 和项目记忆
第 4 步:拆分 ISCU 服务
第 5 步:做 MCP Server
第 6 步:接 DeepSeek Harness / Codex / Claude / Dify
第 7 步:部署 7.106
第 8 步:只读上线
第 9 步:生成上线
第 10 步:受控写库上线
```

## 18.1 第 0-2 章怎么落地

对应内容:

```text
结论先行
为什么采用这个组合
目标架构
```

需要产出的不是代码,而是一份决策记录:

```text
memory/decisions/20260922-agent-platform-architecture.md
```

建议内容:

```markdown
# 决策:采用 Git + HTTP + MCP + 多客户端适配架构

## 背景

团队使用的 Agent 客户端不同,业务逻辑不能绑定在一个客户端。

## 决策

- Git:知识、Skill、项目记忆
- HTTP API:确定性执行能力
- MCP:跨 Agent 工具接口
- DeepSeek Harness:推荐入口
- Codex/Claude/Dify:适配接入

## 不做

- 不把业务逻辑写进 Dify Chatflow
- 不让 Agent 直接持有 MySQL/SSH 密码
- 不依赖某个客户端的隐藏会话

## 验收

至少两个客户端能读取同一 Skill,并调用同一 verify 工具。
```

完成标准:

```text
架构图已确认
接口清单已确认
客户端范围已确认
高风险动作确认必须人工审批
```

## 18.2 第 3 章:建立 Git 仓库

执行机器:

```text
开发电脑或 7.106 的代码目录
```

命令:

```bash
mkdir -p bwt-vms-agent-kit
cd bwt-vms-agent-kit
git init

mkdir -p contracts skills/iscu-config/references skills/iscu-config/scripts
mkdir -p server mcp adapters/deepseek-harness adapters/codex adapters/claude adapters/dify
mkdir -p memory/projects memory/decisions logs

touch README.md AGENTS.md CHANGELOG.md
touch contracts/iscu-agent-openapi.yaml
touch contracts/error-codes.md
touch skills/iscu-config/SKILL.md
touch server/iscu_agent_api.py
touch mcp/server.py
touch memory/current-context.md
touch memory/run-log.jsonl

git add .
git commit -m "chore: bootstrap BWT agent platform"
```

产出物:

```text
仓库目录
初始 commit
空白 contracts/skills/server/mcp/memory 目录
```

完成标准:

```text
git status 干净
目录结构和文档第 3 章一致
仓库中没有密码和私钥
```

## 18.3 第 4 章:建立 API 契约

先写契约,后写代码。

创建:

```text
contracts/iscu-agent-openapi.yaml
```

最小内容:

```yaml
openapi: 3.1.0
info:
  title: BWT ISCU Agent API
  version: 1.0.0
servers:
  - url: http://192.168.7.106:18000
paths:
  /health:
    get:
      summary: health check
  /v1/iscu/precheck:
    post:
      summary: validate parameters
  /v1/iscu/generate:
    post:
      summary: generate JSON and TXT files
  /v1/iscu/verify:
    post:
      summary: read-only verify four tables
  /v1/iscu/apply:
    post:
      summary: write four tables with confirmation
  /v1/iscu/rollback:
    post:
      summary: restore backup with confirmation
  /v1/iscu/datacap/verify:
    post:
      summary: restart datacap and verify Redis
```

具体实现顺序:

```text
1. /health
2. /v1/iscu/precheck
3. /v1/iscu/verify
4. /v1/iscu/generate
5. /v1/iscu/snapshot
6. /v1/iscu/apply
7. /v1/iscu/rollback
8. /v1/iscu/datacap/verify
```

每个接口必须写:

```text
请求 JSON 示例
响应 JSON 示例
错误码
是否需要 confirm_token
是否写数据库
是否可回滚
```

完成标准:

```text
OpenAPI 文件可被至少一个工具解析
每个接口都有请求和响应示例
高风险接口明确标记 approval_required=true
```

## 18.4 第 5 章:编写 Skill

创建:

```text
skills/iscu-config/SKILL.md
```

内容直接采用第 5.2 节模板。

Skill 编写步骤:

1. 写 frontmatter。
2. 写触发场景。
3. 写输入字段。
4. 写必须调用的工具。
5. 写禁止事项。
6. 写验收标准。
7. 把详细规则拆到 `references/`。
8. 把脚本或示例放到 `scripts/`。

建议文件:

```text
skills/iscu-config/
├── SKILL.md
├── references/
│   ├── datatype-map.md
│   ├── four-table-rules.md
│   ├── orderkey-rules.md
│   └── troubleshooting.md
└── scripts/
    └── example-verify.sh
```

跨客户端验证:

```text
Codex 能读取 SKILL.md
DeepSeek Harness 能读取 SKILL.md
Claude 能读取 SKILL.md
Dify 能检索对应 knowledge chunk
```

完成标准:

```text
至少两个 Agent 能按同一 Skill 说出相同的 DataTypeRegister 和四表规则
Skill 中没有密码
Skill 不包含任意 SQL 执行器
```

## 18.5 第 6 章:建立共享记忆

创建:

```text
memory/current-context.md
memory/projects/tka2501.md
memory/run-log.jsonl
memory/decisions/
```

开始任务时,Agent 必须读:

```text
AGENTS.md
memory/current-context.md
memory/projects/<project>.md
skills/<skill>/SKILL.md
```

结束任务时,Agent 必须写:

```text
memory/projects/<project>.md
memory/run-log.jsonl
```

运行日志追加示例:

```bash
python3 - <<'PY'
import json
from datetime import datetime

entry = {
    "time": datetime.now().astimezone().isoformat(),
    "agent": "Codex",
    "tool": "iscu_verify",
    "ship_id": "35701001",
    "client_no": 4,
    "result": "passed",
    "request_id": "req_20260922_001",
}

with open("memory/run-log.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
PY
```

完成标准:

```text
新 Agent 只读 Git 就能知道项目当前状态
任务结束后能追溯到谁、用什么工具、做了什么
不记录密码和完整敏感配置
```

## 18.6 第 7 章:实现 MCP Server

推荐先实现只读工具:

```text
iscu_precheck
iscu_verify
```

再实现:

```text
iscu_generate
```

最后实现:

```text
iscu_snapshot
iscu_apply
iscu_rollback
iscu_datacap_verify
```

MCP Server 实现原则:

```text
MCP Server 只做协议适配
不直接 SSH
不直接连 MySQL
所有执行请求转给 ISCU Agent Service
```

每个工具必须具备:

```text
name
description
input_schema
approval_required
returns_request_id
```

DeepSeek Harness 测试:

```text
1. 连接 MCP Server
2. 列出工具
3. 调用 iscu_verify(client_no=4, topic_name=AMS4)
4. 确认返回 status_code=200
5. 确认返回 ok=true
```

Codex 测试:

```text
1. 读取 AGENTS.md
2. 发现 MCP Server
3. 调用 iscu_verify
4. 根据返回生成校验报告
```

完成标准:

```text
至少两个 Agent 调用同一个 MCP 工具成功
工具的输入输出与 OpenAPI 一致
高风险工具不允许自动执行
```

## 18.7 第 8 章:客户端适配

### DeepSeek Harness

在插件中只配置:

```text
MCP Server 地址
ISCU Agent Service URL
命令名称
鉴权
审批提示
```

不要在插件里写:

```text
SQL
JsonString 生成逻辑
SSH 密码
MySQL 密码
```

### Codex

在项目 `AGENTS.md` 中写明:

```text
开始任务先读 memory/current-context.md 和对应 Skill
执行 ISCU 操作必须调用 MCP/HTTP
写库前必须读取确认 token
任务结束必须更新 memory
```

### Claude

在项目说明中写明相同规则,并配置 MCP Server。

### Dify

配置:

```text
知识库:BWT-VMS交付知识库
HTTP 工具:/v1/iscu/verify 和 /v1/iscu/generate
SSRF 放行:192.168.0.0/16
高风险 apply:默认不接,Dify 只展示人工操作计划
```

完成标准:

```text
DeepSeek Harness 能直接调工具
Codex 能读 Skill 并调用 MCP
Claude 能读同一记忆
Dify 能调 verify
没有任何客户端直接保存 MySQL/SSH 密码
```

## 18.8 第 9 章:部署到 7.106

直接按第 9 章执行,顺序不能变:

```text
创建目录
上传服务
写环境文件
启动 systemd
Dify SSRF 放行
健康检查
```

每一步的验收命令:

```bash
curl -s http://192.168.7.106:18000/health
curl -s -X POST http://192.168.7.106:18000/v1/iscu/verify \
  -H 'Content-Type: application/json' \
  -d '{"client_no":4,"topic_name":"AMS4"}'
```

不通过时检查:

```text
服务是否 running
SSH 到 7.61 是否免密
MySQL 容器名是否正确
18000 端口是否监听
Dify SSRF 是否放行
```

## 18.9 第 10 章:分阶段上线

### 第一阶段只读

交付:

```text
/health
/v1/iscu/precheck
/v1/iscu/verify
```

验收:

```text
7.61 能查
Dify 能调
Codex 能调
DeepSeek Harness 能调
没有写库
```

### 第二阶段生成

交付:

```text
/v1/iscu/generate
/v1/iscu/snapshot
```

验收:

```text
JSON/TXT 文件生成成功
MD5 与文件一致
数据点数和 orderkey 预期正确
不写 MySQL
```

### 第三阶段受控写库

交付:

```text
/v1/iscu/apply
/v1/iscu/rollback
/v1/iscu/datacap/verify
```

验收:

```text
无确认 token 时拒绝执行
写库前备份
写库后四表校验
可以回滚
datacap 验证输出 Redis 结果
```

## 18.10 第 11 章:安全与权限

具体操作:

```bash
sudo chmod 600 /etc/bwt-agent.env
sudo chown root:root /etc/bwt-agent.env
```

确认日志不包含密码:

```bash
grep -RiE "password|passwd|_PW=" /opt/bwt-agent/logs /etc/bwt-agent.env
```

确认 Git 不包含凭据:

```bash
git grep -nE "password|passwd|BEGIN .* PRIVATE KEY"
```

高风险接口加入:

```text
request_id
operator
confirm_token
expire_at
backup_path
```

## 18.11 第 12 章:运行规范

每个 Agent 的固定开始动作:

```text
读取当前项目记忆
读取对应 Skill
检查环境和服务健康
确认本次操作风险级别
```

固定结束动作:

```text
写项目状态
追加 run-log
记录 request_id
记录未决问题
必要时提交 Git
```

## 18.12 第 13 章:首次 ISCU 导入

执行顺序固定为:

```text
precheck
generate
verify
apply
datacap verify
```

每一步都不能跳:

```text
precheck 失败,不生成
generate 失败,不落库
verify 失败,不重启 datacap
apply 无确认,不执行
datacap 不出数,不进 S2
```

## 18.13 第 14 章:验收

平台验收、功能验收、跨客户端验收全部用复选框执行。

建议单独建立:

```text
memory/acceptance/agent-platform-20260922.md
```

每条验收记录:

```text
测试项
执行人
执行时间
请求 ID
结果
证据路径
问题
```

## 18.14 第 15 章:常见问题处理

遇到“为什么不能直接连 MySQL”之类问题,统一回答:

```text
因为凭据、审计、审批和回滚需要集中在服务层。
Agent 只调用工具,不持有数据库密码。
```

遇到“Skill 为什么不能直接执行”:

```text
Skill 是操作规范,执行必须通过 HTTP/MCP 工具。
```

## 18.15 第 16 章:实施排期

把排期转成任务清单:

```text
Day 1:仓库、契约、Skill
Day 2:服务拆分、只读 API
Day 3:MCP、Codex、DeepSeek Harness
Day 4:generate、snapshot、verify
Day 5:apply、rollback、datacap
```

每天结束必须提交:

```text
代码 commit
测试记录
memory/run-log.jsonl
```

## 18.16 第 17 章:立即执行

第一个迭代只做这 6 件事:

1. 建 `bwt-vms-agent-kit` 仓库。
2. 写 `contracts/iscu-agent-openapi.yaml`。
3. 写 `skills/iscu-config/SKILL.md`。
4. 拆分 `server/` 中的服务。
5. 先实现 `health / precheck / verify`。
6. 在 DeepSeek Harness 和 Codex 各接一次 MCP。

不要先做:

```text
Dify 全套 Chatflow
apply 自动写库
datacap 自动重启
跨 Agent 实时会话同步
```

完成上面 6 件事后,再进入 generate 和受控 apply。
