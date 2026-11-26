# 数据安全计算与隐私保护平台

面向数据脱敏、差分隐私、安全聚合与访问审计的数据安全计算与隐私保护平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 脱敏规则 | `mask_rule` | 脱敏规则 | 规则编号、目标字段、脱敏算法 |
| 隐私预算 | `privacy_budget` | 隐私预算 | 预算编号、数据集名称、总预算Epsilon |
| 噪声引擎 | `noise_engine` | 噪声配置 | 配置编号、噪声机制、Epsilon值 |
| 安全聚合 | `secure_agg` | 聚合任务 | 任务编号、数据源列表、聚合函数 |
| 访问审计 | `access_audit` | 审计记录 | 记录编号、访问主体、目标资源 |
| 授权审批 | `grant_approval` | 授权申请 | 申请编号、申请人、目标数据 |
| 分级分类 | `classify_tag` | 分级标签 | 标签编号、数据资源、安全等级 |
| 联邦训练 | `federated_train` | 训练任务 | 任务编号、参与节点、模型类型 |
| 梯度加密 | `gradient_encrypt` | 加密配置 | 配置编号、加密方案、密钥长度 |
| 假名化 | `pseudonym` | 假名映射 | 映射编号、原始标识、假名标识 |
| K匿名检查 | `k_anonymity` | 匿名检查 | 检查编号、数据集名称、准标识符列 |
| 差分查询 | `differential_sql` | 查询记录 | 查询编号、查询语句、查询用户 |
| 密文索引 | `encrypt_index` | 索引条目 | 条目编号、加密域、索引类型 |
| 数据水印 | `watermark` | 水印记录 | 水印编号、目标数据集、水印算法 |
| 同意登记 | `consent_registry` | 同意记录 | 记录编号、数据主体、处理目的 |
| 风险扫描 | `risk_scan` | 扫描任务 | 任务编号、扫描范围、扫描规则 |
| 策略下发 | `deploy_policy` | 策略分发 | 分发编号、策略内容、目标节点 |
| 异常检测 | `anomaly_detect` | 异常规则 | 规则编号、检测维度、检测算法 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 密文索引：检索申请 → 核对 → 回执 → 扣费

密文索引页（`/encrypt_index`）顶部的「密文检索申请」面板承载按获准样本范围检索的完整闭环；
隐私预算页（`/privacy_budget`）顶部展示「检索扣费明细」。

- 服务端在同一把锁内完成 **索引版本 / 授权范围 / 隐私预算** 核对、扣费与回执落库。
- 返还条目先按索引版本与可搜索状态过滤，再与授权样本范围取交集，**绝不越过核准范围**。
- 每次申请带客户端幂等号（`client_token`，也支持 `Idempotency-Key` 请求头）。
  超时后用同一幂等号重发直接返回原回执（`idempotent_replay: true`），**不会二次扣费**。
- 两笔检索争用仅够一次的额度时，胜者 `status=delivered` 并扣费；败者得到
  `status=conflict` 的回执：`items=[]`、`charged=false`，文案写清实际获得 0 条、费用未扣。
- 回执可按申请单号或 `RCPT-*` 回执编号找回，也支持 `?receipt=` / `?token=` 深链。

主要接口（同一能力挂了若干路径别名，如 `/api/index_search`、`/api/search_requests`）：

```text
POST /api/encrypt_index/searches        # 发起/重发检索申请
GET  /api/encrypt_index/searches        # 回执列表（?client_token= 过滤）
GET  /api/encrypt_index/searches/{ref}  # 按申请单号或回执编号找回
GET  /api/privacy_budget/charges        # 全部检索扣费明细
GET  /api/privacy_budget/{id}/charges   # 某笔预算的扣费明细
GET  /api/index_search/options          # 授权、预算、版本、加密域选项
POST /api/index_search/scenario/prepare # 准备“额度只够一次”的验收场景
```

请求体兼容 snake/camel/中文键与 `{"values": {...}}` 嵌套；可用 `delay_ms`
（或 `simulate_timeout`）让服务端在提交完成后延迟响应，配合页面的
「模拟首次响应超时」「一键演练：交错提交 + 超时重试 + 额度争用」复现验收脚本。


领域：数据工程
子方向：数据分析与数据应用
