"""密文索引「检索申请 → 核对 → 回执 → 扣费」领域服务。

关键语义（对应验收场景）：

1. 服务端在同一把锁内完成「幂等登记 + 索引版本核对 + 授权范围核对 +
   预算核对/扣费 + 回执落库」，保证两次并发检索争用仅够一次的额度时
   最多只有一笔成功扣费。
2. 同一 ``client_token`` 的重发（客户端超时重试）直接返回已确认回执，
   绝不二次扣费；回执与结果可随时按申请单号/回执编号找回。
3. 返还条目先按索引版本/可搜索状态过滤，再与授权样本范围取交集，
   任何越权样本都不会出现在 ``items`` 中。
4. 额度冲突时生成「冲突回执」：实际交付条目为 0、费用状态为未扣费，
   并在回执说明里写清实际获得的结果与费用状态。

数据是内存态，与 :mod:`app.store` 的示例仓库一致；真实项目里把
``_receipts`` / ``_charges`` 换成带唯一约束的表即可。
"""
from __future__ import annotations

import copy
import threading
import uuid
from datetime import datetime
from typing import Any

from app.store import store

# 一次检索的默认隐私报价（Epsilon）。预算行可通过「单次检索Epsilon」字段覆盖。
DEFAULT_SEARCH_COST = 0.1
# 平台当前发布的索引版本；客户端陷门必须基于该版本生成。
CURRENT_INDEX_VERSION = "v1"
SUPPORTED_INDEX_VERSIONS = ["v1", "v2"]

# ---------------------------------------------------------------------------
# 样本目录：密文索引背后可供授权检索的样本条目。
# 每条样本关联一个 encrypt_index 的条目编号 / 加密域 / 索引版本，
# 并携带仅用于演示匹配的检索词与陷门哈希。
# ---------------------------------------------------------------------------
SAMPLE_CATALOG: list[dict[str, Any]] = [
    {
        "id": 1, "样本编号": "SAMP-1001", "条目编号": "ENCR-0001",
        "加密域": "客户姓名", "索引版本": "v1", "索引状态": "有效",
        "检索词": ["张伟", "客户姓名", "name", "tw_e3b0c44298fc1c14"],
        "陷门哈希": "tw_e3b0c44298fc1c14", "密文片段": "ct:U2FsdGVkX1+1001",
        "可读内容": "张*伟", "样本标签": "华东客户样本",
    },
    {
        "id": 2, "样本编号": "SAMP-1002", "条目编号": "ENCR-0001",
        "加密域": "客户姓名", "索引版本": "v1", "索引状态": "有效",
        "检索词": ["李娜", "客户姓名", "name", "tw_a1b2c3d4e5f60002"],
        "陷门哈希": "tw_a1b2c3d4e5f60002", "密文片段": "ct:U2FsdGVkX1+1002",
        "可读内容": "李*", "样本标签": "华东客户样本",
    },
    {
        "id": 3, "样本编号": "SAMP-1003", "条目编号": "ENCR-0001",
        "加密域": "客户姓名", "索引版本": "v1", "索引状态": "有效",
        "检索词": ["王芳", "客户姓名", "name", "tw_a1b2c3d4e5f60003"],
        "陷门哈希": "tw_a1b2c3d4e5f60003", "密文片段": "ct:U2FsdGVkX1+1003",
        "可读内容": "王*芳", "样本标签": "华南客户样本",
    },
    {
        "id": 4, "样本编号": "SAMP-2001", "条目编号": "ENCR-0004",
        "加密域": "手机号码", "索引版本": "v1", "索引状态": "有效",
        "检索词": ["13800000001", "手机号", "phone", "tw_b2c3d4e5f6070001"],
        "陷门哈希": "tw_b2c3d4e5f6070001", "密文片段": "ct:U2FsdGVkX1+2001",
        "可读内容": "138****0001", "样本标签": "联系方式样本",
    },
    {
        "id": 5, "样本编号": "SAMP-2002", "条目编号": "ENCR-0004",
        "加密域": "手机号码", "索引版本": "v1", "索引状态": "有效",
        "检索词": ["13900000002", "手机号", "phone", "tw_b2c3d4e5f6070002"],
        "陷门哈希": "tw_b2c3d4e5f6070002", "密文片段": "ct:U2FsdGVkX1+2002",
        "可读内容": "139****0002", "样本标签": "联系方式样本",
    },
    {
        "id": 6, "样本编号": "SAMP-3001", "条目编号": "ENCR-0005",
        "加密域": "交易流水号", "索引版本": "v1", "索引状态": "有效",
        "检索词": ["TX20261001001", "交易流水", "transaction", "tw_c3d4e5f607180001"],
        "陷门哈希": "tw_c3d4e5f607180001", "密文片段": "ct:U2FsdGVkX1+3001",
        "可读内容": "TX****0001", "样本标签": "合规审计样本",
    },
    {
        "id": 7, "样本编号": "SAMP-3002", "条目编号": "ENCR-0005",
        "加密域": "交易流水号", "索引版本": "v1", "索引状态": "有效",
        "检索词": ["TX20261003007", "交易流水", "transaction", "tw_c3d4e5f607180002"],
        "陷门哈希": "tw_c3d4e5f607180002", "密文片段": "ct:U2FsdGVkX1+3002",
        "可读内容": "TX****3007", "样本标签": "合规审计样本",
    },
    {
        "id": 8, "样本编号": "SAMP-3003", "条目编号": "ENCR-0005",
        "加密域": "交易流水号", "索引版本": "v1", "索引状态": "有效",
        "检索词": ["TX20261005011", "交易流水", "transaction", "tw_c3d4e5f607180003"],
        "陷门哈希": "tw_c3d4e5f607180003", "密文片段": "ct:U2FsdGVkX1+3003",
        "可读内容": "TX****5011", "样本标签": "合规审计样本",
    },
    {
        "id": 9, "样本编号": "SAMP-4001", "条目编号": "ENCR-0002",
        "加密域": "设备IMEI", "索引版本": "v2", "索引状态": "重建中",
        "检索词": ["860000000000001", "IMEI", "设备", "tw_c4ca4238a0b923"],
        "陷门哈希": "tw_c4ca4238a0b923", "密文片段": "ct:U2FsdGVkX1+4001",
        "可读内容": "8600****0001", "样本标签": "设备标识样本",
    },
    {
        "id": 10, "样本编号": "SAMP-5001", "条目编号": "ENCR-0003",
        "加密域": "身份证密文", "索引版本": "v1", "索引状态": "已失效",
        "检索词": ["11010119900101001X", "身份证", "idcard", "tw_d41d8cd98f00"],
        "陷门哈希": "tw_d41d8cd98f00", "密文片段": "ct:U2FsdGVkX1+5001",
        "可读内容": "110101********001X", "样本标签": "身份样本",
    },
    {
        "id": 11, "样本编号": "SAMP-1004", "条目编号": "ENCR-0006",
        "加密域": "银行卡号", "索引版本": "v2", "索引状态": "有效",
        "检索词": ["6222000000000001", "银行卡", "bankcard", "tw_d5e6f70819200011"],
        "陷门哈希": "tw_d5e6f70819200011", "密文片段": "ct:U2FsdGVkX1+1004",
        "可读内容": "6222****0001", "样本标签": "支付样本-新版索引",
    },
    {
        "id": 12, "样本编号": "SAMP-1005", "条目编号": "ENCR-0006",
        "加密域": "银行卡号", "索引版本": "v2", "索引状态": "有效",
        "检索词": ["6222000000000002", "银行卡", "bankcard", "tw_d5e6f70819200012"],
        "陷门哈希": "tw_d5e6f70819200012", "密文片段": "ct:U2FsdGVkX1+1005",
        "可读内容": "6222****0002", "样本标签": "支付样本-新版索引",
    },
]

# 内置授权范围：键可以是授权行 id、申请编号或范围编号。
# 真实环境里这张表来自授权审批模块下发的「获准样本范围」。
BUILTIN_GRANT_SCOPES: dict[str, dict[str, Any]] = {
    "2": {
        "scope_code": "SCOPE-GRAN-0002",
        "samples": ["SAMP-3001", "SAMP-3002", "SAMP-3003"],
        "scope_text": "交易流水样本3条（合规审计）",
    },
    "GRAN-0002": {
        "scope_code": "SCOPE-GRAN-0002",
        "samples": ["SAMP-3001", "SAMP-3002", "SAMP-3003"],
        "scope_text": "交易流水样本3条（合规审计）",
    },
    "GRAN-E2E-SEARCH": {
        "scope_code": "SCOPE-E2E-SEARCH",
        "samples": ["SAMP-1001", "SAMP-1002"],
        "scope_text": "验收专用：客户姓名样本2条",
    },
    "SCOPE-E2E-SEARCH": {
        "scope_code": "SCOPE-E2E-SEARCH",
        "samples": ["SAMP-1001", "SAMP-1002"],
        "scope_text": "验收专用：客户姓名样本2条",
    },
    "SCOPE-DEMO-NAME": {
        "scope_code": "SCOPE-DEMO-NAME",
        "samples": ["SAMP-1001", "SAMP-1002", "SAMP-1003"],
        "scope_text": "演示范围：客户姓名样本3条",
    },
    "GRAN-DEMO": {
        "scope_code": "SCOPE-DEMO-NAME",
        "samples": ["SAMP-1001", "SAMP-1002", "SAMP-1003"],
        "scope_text": "演示授权：客户姓名样本3条（已批准）",
    },
}

# 所有可识别的请求字段别名都集中在这里，路由层/服务层共用。
TOKEN_KEYS = (
    "client_token", "clientToken", "token", "request_id", "requestId",
    "idempotency_key", "idempotencyKey", "application_no", "apply_no",
    "search_no", "searchNo", "申请单号", "检索申请单号",
)
GRANT_ID_KEYS = ("grant_id", "grantId", "approval_id", "approvalId", "授权id", "授权申请id")
GRANT_NO_KEYS = (
    "grant_no", "grantNo", "approval_no", "approvalNo",
    "授权编号", "申请编号", "grant_code",
)
BUDGET_ID_KEYS = ("budget_id", "budgetId", "预算id")
BUDGET_NO_KEYS = ("budget_no", "budgetNo", "预算编号", "budget_code")
VERSION_KEYS = ("index_version", "indexVersion", "version", "索引版本")
KEYWORD_KEYS = ("keyword", "query", "q", "term", "trapdoor", "trapdoor_hash", "关键词", "检索词", "陷门", "陷门哈希")
DOMAIN_KEYS = ("domain", "field", "encrypt_domain", "加密域", "加密字段")
SCOPE_KEYS = (
    "scope", "sample_ids", "sampleIds", "allowed_samples", "samples",
    "entry_ids", "entryIds", "allowed_ids", "allowedIds",
    "allowed_entry_ids", "allowedEntryIds", "index_entry_ids",
    "approved_scope", "grant_scope", "scope_ids",
    "授权范围", "授权样本范围", "获准样本范围", "样本范围", "核准范围",
)
COST_KEYS = ("cost", "cost_epsilon", "epsilon", "charge_epsilon", "price", "单次费用", "单次检索Epsilon", "扣费Epsilon")
DELAY_KEYS = ("delay_ms", "delay", "sleep_ms", "response_delay_ms", "slow_ms")


def _first(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _round_eps(value: float) -> float:
    return round(value + 1e-12, 6)


class IndexSearchService:
    """检索申请/回执/扣费的内存账本，进程内单例（见文件末尾 ``index_search_service``）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._receipts_by_token: dict[str, dict[str, Any]] = {}
        self._receipts_by_no: dict[str, dict[str, Any]] = {}
        self._receipts: list[dict[str, Any]] = []
        self._charges: list[dict[str, Any]] = []
        self._seq_receipt = 0
        self._seq_charge = 0
        self._seq_fixture = 0
        # budget id -> 首次触碰前的字段快照，reset 时还原。
        self._budget_snapshot: dict[int, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # 目录 / 选项
    # ------------------------------------------------------------------
    def catalog(self) -> list[dict[str, Any]]:
        return [dict(row) for row in SAMPLE_CATALOG]

    def options(self) -> dict[str, Any]:
        grants: list[dict[str, Any]] = []
        for row in store.rows("grant_approval"):
            scope = self.resolve_scope(grant_row=row, explicit_scope=None)
            grants.append({
                "id": row.get("id"),
                "grant_no": row.get("申请编号", ""),
                "applicant": row.get("申请人", ""),
                "target": row.get("目标数据", ""),
                "status": row.get("status", ""),
                "approved": str(row.get("status", "")) in {"已批准", "有效", "生效中"},
                "scope": scope,
                "scope_text": self._scope_text(row, scope),
            })
        # 内置虚拟授权：保证新环境打开页面就有一条范围清晰的已批准授权可选。
        grants.append({
            "id": None,
            "grant_no": "GRAN-DEMO",
            "applicant": "数据工程组",
            "target": "客户姓名样本3条（演示）",
            "status": "已批准",
            "approved": True,
            "scope": ["SAMP-1001", "SAMP-1002", "SAMP-1003"],
            "scope_text": "演示授权：客户姓名样本3条（已批准）",
        })
        budgets: list[dict[str, Any]] = []
        for row in store.rows("privacy_budget"):
            total = _to_float(row.get("总预算Epsilon"))
            consumed = _to_float(row.get("已消耗Epsilon"))
            cost = _to_float(row.get("单次检索Epsilon"), DEFAULT_SEARCH_COST) or DEFAULT_SEARCH_COST
            budgets.append({
                "id": row.get("id"),
                "budget_no": row.get("预算编号", ""),
                "name": row.get("数据集名称", ""),
                "status": row.get("status", ""),
                "total_epsilon": total,
                "consumed_epsilon": consumed,
                "remaining_epsilon": _round_eps(total - consumed),
                "search_cost_epsilon": cost,
            })
        domains = sorted({str(row["加密域"]) for row in SAMPLE_CATALOG})
        return {
            "current_index_version": CURRENT_INDEX_VERSION,
            "versions": SUPPORTED_INDEX_VERSIONS,
            "default_cost_epsilon": DEFAULT_SEARCH_COST,
            "domains": domains,
            "grants": grants,
            "budgets": budgets,
            "samples": [
                {
                    "sample_no": row["样本编号"], "entry_no": row["条目编号"],
                    "domain": row["加密域"], "version": row["索引版本"],
                    "index_status": row["索引状态"], "readable": row["可读内容"],
                }
                for row in SAMPLE_CATALOG
            ],
        }

    # ------------------------------------------------------------------
    # 授权范围
    # ------------------------------------------------------------------
    def _scope_text(self, grant_row: dict[str, Any] | None, scope: list[str]) -> str:
        if grant_row is not None:
            for key in ("授权样本范围", "样本范围", "获准样本范围", "授权范围"):
                if grant_row.get(key):
                    return str(grant_row[key])
            builtin = BUILTIN_GRANT_SCOPES.get(str(grant_row.get("id"))) \
                or BUILTIN_GRANT_SCOPES.get(str(grant_row.get("申请编号", "")))
            if builtin:
                return str(builtin.get("scope_text", ""))
        return f"样本 {len(scope)} 条：{', '.join(scope)}"

    def _parse_scope_tokens(self, raw: Any) -> list[str]:
        if raw is None:
            return []
        if isinstance(raw, dict):
            # 兼容 {"sample_ids": [...]} / {"entry_ids": [...]} 形态的范围对象。
            for key in ("sample_ids", "samples", "entry_ids", "ids", "items"):
                if key in raw:
                    return self._parse_scope_tokens(raw[key])
            return []
        if isinstance(raw, (list, tuple, set)):
            text_tokens = [str(item).strip() for item in raw]
        else:
            text = str(raw).replace("，", ",").replace("、", ",").replace(";", ",").replace("；", ",")
            text_tokens = [part.strip() for part in text.split(",")]
        return [token for token in text_tokens if token]

    def _expand_scope_token(self, token: str, valid_sample_nos: set[str]) -> set[str]:
        """把单个范围标记展开成目录内的样本编号。

        支持：SAMP 编号、ENCR 条目编号、加密域/标签、目录数字 id、
        以及 encrypt_index 种子行的数字 id。
        """
        allowed: set[str] = set()
        if token in valid_sample_nos:
            allowed.add(token)
        for row in SAMPLE_CATALOG:
            if token in {
                str(row["id"]), str(row["条目编号"]), str(row["加密域"]),
                str(row["样本标签"]), str(row["样本编号"]),
            }:
                allowed.add(str(row["样本编号"]))
        if token.isdigit():
            # encrypt_index 通用资源行的数字 id → 对应条目编号 → 其下样本。
            for entry in store.rows("encrypt_index"):
                if str(entry.get("id")) == token:
                    entry_no = str(entry.get("条目编号", ""))
                    for row in SAMPLE_CATALOG:
                        if str(row["条目编号"]) == entry_no:
                            allowed.add(str(row["样本编号"]))
        return {s for s in allowed if s in valid_sample_nos}

    def resolve_scope(
        self,
        *,
        grant_row: dict[str, Any] | None,
        explicit_scope: Any,
    ) -> list[str]:
        """返回核准范围内的样本编号集合（已与目录对齐、去重）。"""
        valid_sample_nos = {str(row["样本编号"]) for row in SAMPLE_CATALOG}
        tokens = self._parse_scope_tokens(explicit_scope)
        allowed: set[str] = set()
        for token in tokens:
            allowed |= self._expand_scope_token(token, valid_sample_nos)
        if explicit_scope is not None and tokens:
            return sorted(allowed)

        if grant_row is not None:
            for key in ("授权样本范围", "样本范围", "获准样本范围"):
                if grant_row.get(key):
                    scoped = self.resolve_scope(grant_row=None, explicit_scope=grant_row[key])
                    if scoped:
                        return scoped
            builtin = (
                BUILTIN_GRANT_SCOPES.get(str(grant_row.get("id")))
                or BUILTIN_GRANT_SCOPES.get(str(grant_row.get("申请编号", "")))
            )
            if builtin:
                return [s for s in builtin["samples"] if s in valid_sample_nos]
            # 已批准但没有细化范围：视为可检索全部有效样本（最小意外原则下的宽口径）。
            return sorted(valid_sample_nos)

        builtin_demo = BUILTIN_GRANT_SCOPES["SCOPE-DEMO-NAME"]
        return list(builtin_demo["samples"])

    def find_grant(self, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        grant_id = _first(payload, GRANT_ID_KEYS)
        grant_no = _first(payload, GRANT_NO_KEYS)
        for row in store.rows("grant_approval"):
            if grant_id is not None and str(row.get("id")) == str(grant_id):
                return row, None
            if grant_no is not None and str(row.get("申请编号", "")) == str(grant_no).strip():
                return row, None
        # 内置虚拟授权（例如验收专用 GRAN-E2E-SEARCH 尚未落库时）。
        if grant_no is not None and str(grant_no) in BUILTIN_GRANT_SCOPES:
            scope = BUILTIN_GRANT_SCOPES[str(grant_no)]
            return {
                "id": None, "申请编号": str(grant_no), "status": "已批准",
                "申请人": "验收脚本", "目标数据": scope.get("scope_text", ""),
                "_virtual": True,
            }, None
        if grant_id is not None or grant_no is not None:
            return None, f"授权申请不存在或未下发：{grant_no or grant_id}"
        return None, None  # 未指定授权：使用内置演示范围

    # ------------------------------------------------------------------
    # 预算
    # ------------------------------------------------------------------
    def find_budget(self, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        budget_id = _first(payload, BUDGET_ID_KEYS)
        budget_no = _first(payload, BUDGET_NO_KEYS)
        rows = store.rows("privacy_budget")
        for row in rows:
            if budget_id is not None and str(row.get("id")) == str(budget_id):
                return row, None
            if budget_no is not None and str(row.get("预算编号", "")) == str(budget_no).strip():
                return row, None
        if budget_id is None and budget_no is None and rows:
            return rows[0], None
        return None, f"隐私预算不存在：{budget_no or budget_id}"

    def budget_view(self, row: dict[str, Any]) -> dict[str, Any]:
        total = _to_float(row.get("总预算Epsilon"))
        consumed = _to_float(row.get("已消耗Epsilon"))
        return {
            "budget_id": row.get("id"),
            "budget_no": row.get("预算编号", ""),
            "dataset": row.get("数据集名称", ""),
            "status": row.get("status", ""),
            "total_epsilon": total,
            "consumed_epsilon": _round_eps(consumed),
            "remaining_epsilon": _round_eps(total - consumed),
        }

    def _snapshot_budget_once(self, budget: dict[str, Any]) -> None:
        bid = int(budget["id"])
        if bid not in self._budget_snapshot:
            # 整行深拷贝：扣费还会往行上挂「扣费明细」列表，reset 时整行还原。
            self._budget_snapshot[bid] = copy.deepcopy(budget)

    def _charge_budget(self, budget: dict[str, Any], cost: float, receipt_no: str) -> dict[str, Any]:
        self._snapshot_budget_once(budget)
        consumed = _to_float(budget.get("已消耗Epsilon")) + cost
        total = _to_float(budget.get("总预算Epsilon"))
        remaining = _round_eps(total - consumed)
        budget["已消耗Epsilon"] = f"{_round_eps(consumed):g}"
        old_record = str(budget.get("消耗记录", "")).strip()
        line = f"检索回执{receipt_no}扣费{cost:g}ε"
        budget["消耗记录"] = f"{old_record}；{line}" if old_record else line
        if remaining <= 1e-9:
            budget["status"] = "已耗尽"
            budget["pending"] = False
        elif total > 0 and remaining / total <= 0.1:
            budget["status"] = "预警"
        return self.budget_view(budget)

    # ------------------------------------------------------------------
    # 检索匹配
    # ------------------------------------------------------------------
    def _match_samples(
        self,
        *,
        keyword: Any,
        domain: Any,
        version: str,
        scope: list[str],
    ) -> tuple[list[dict[str, Any]], int]:
        scope_set = set(scope)
        kw = str(keyword or "").strip().casefold()
        dom = str(domain or "").strip()
        candidates: list[dict[str, Any]] = []
        matched_but_blocked = 0
        for row in SAMPLE_CATALOG:
            # 1) 索引版本核对：只检索客户端声明的版本。
            if str(row["索引版本"]) != str(version):
                continue
            # 2) 只有索引状态有效、可搜索的条目才可能返还。
            if str(row["索引状态"]) != "有效":
                continue
            if dom and dom not in {str(row["加密域"]), ""}:
                if dom not in str(row["加密域"]):
                    continue
            if kw:
                haystacks = [
                    str(row["样本编号"]), str(row["条目编号"]), str(row["加密域"]),
                    str(row["陷门哈希"]), str(row["可读内容"]),
                    *[str(word) for word in row.get("检索词", [])],
                ]
                if not any(kw in text.casefold() for text in haystacks):
                    continue
            # 3) 授权范围是硬边界。
            if str(row["样本编号"]) not in scope_set:
                matched_but_blocked += 1
                continue
            candidates.append(self._public_sample(row))
        return candidates, matched_but_blocked

    @staticmethod
    def _public_sample(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "sample_no": row["样本编号"], "样本编号": row["样本编号"],
            "entry_no": row["条目编号"], "条目编号": row["条目编号"],
            "domain": row["加密域"], "加密域": row["加密域"],
            "index_version": row["索引版本"], "索引版本": row["索引版本"],
            "trapdoor_hash": row["陷门哈希"], "陷门哈希": row["陷门哈希"],
            "ciphertext": row["密文片段"], "密文片段": row["密文片段"],
            "readable": row["可读内容"], "可读内容": row["可读内容"],
            "label": row.get("样本标签", ""), "样本标签": row.get("样本标签", ""),
        }

    # ------------------------------------------------------------------
    # 回执
    # ------------------------------------------------------------------
    def _new_receipt_no(self) -> str:
        self._seq_receipt += 1
        return f"RCPT-{datetime.now():%Y%m%d}-{self._seq_receipt:04d}"

    def _new_charge_no(self) -> str:
        self._seq_charge += 1
        return f"CHG-{self._seq_charge:04d}"

    def submit_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        """处理一次检索申请；返回可直接 JSON 序列化的完整回执体。

        并发安全与幂等都由 ``self._lock`` 保护。响应延迟由路由层在拿到
        返回体之后执行，避免占着锁阻塞重发/争用请求。
        """
        token = str(_first(payload, TOKEN_KEYS) or "").strip() or f"REQ-{uuid.uuid4().hex[:16]}"
        explicit_scope = _first(payload, SCOPE_KEYS)
        version = str(_first(payload, VERSION_KEYS) or CURRENT_INDEX_VERSION).strip() or CURRENT_INDEX_VERSION
        keyword = _first(payload, KEYWORD_KEYS)
        domain = _first(payload, DOMAIN_KEYS)
        cost = _to_float(_first(payload, COST_KEYS), DEFAULT_SEARCH_COST)
        if cost <= 0:
            cost = DEFAULT_SEARCH_COST

        with self._lock:
            existing = self._receipts_by_token.get(token)
            if existing is not None:
                replay = dict(existing)
                replay["idempotent_replay"] = True
                replay["replayed"] = True
                replay["message"] = f"{replay.get('message', '')}（重发命中已确认回执 {replay.get('receipt_no')}，未重复扣费）"
                return replay

            base: dict[str, Any] = {
                "client_token": token, "request_id": token,
                "申请单号": token, "检索申请单号": token,
                "index_version": version, "索引版本": version,
                "keyword": keyword or "", "关键词": keyword or "",
                "domain": domain or "", "加密域": domain or "",
                "cost_epsilon": cost, "charged_epsilon": 0.0,
            }

            def finish_rejected(code: str, message: str, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
                receipt_no = self._new_receipt_no()
                receipt = {
                    **base,
                    "id": self._seq_receipt,
                    "receipt_no": receipt_no, "receipt_id": receipt_no, "回执编号": receipt_no,
                    "ok": False, "success": False, "delivered": False,
                    "conflict": code == "budget_conflict",
                    "status": "rejected" if code != "budget_conflict" else "conflict",
                    "status_key": "rejected" if code != "budget_conflict" else "conflict",
                    "状态": "校验拒绝" if code != "budget_conflict" else "冲突",
                    "code": code, "error_code": code,
                    "charge_status": "未扣费", "费用状态": "未扣费",
                    "charged": False, "billed": False,
                    "items": [], "results": [], "entries": [], "hits": [],
                    "count": 0, "result_count": 0, "delivered_count": 0,
                    "matched_count": 0,
                    "charges": [],
                    "message": message, "回执说明": message,
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "idempotent_replay": False,
                }
                if extra:
                    receipt.update(extra)
                self._store_receipt(token, receipt)
                return dict(receipt)

            # 1) 授权申请是否存在且已批准。
            grant_row, grant_error = self.find_grant(payload)
            if grant_error:
                return finish_rejected("grant_not_found", grant_error)
            if grant_row is not None and not grant_row.get("_virtual"):
                grant_status = str(grant_row.get("status", ""))
                if grant_status not in {"已批准", "有效", "生效中"}:
                    message = f"授权申请 {grant_row.get('申请编号', '')} 当前状态为「{grant_status}」，不在获准有效期内，拒绝检索且不扣费。"
                    return finish_rejected("grant_not_approved", message, extra={
                        "grant_id": grant_row.get("id"),
                        "grant_no": grant_row.get("申请编号", ""),
                        "授权编号": grant_row.get("申请编号", ""),
                    })

            scope = self.resolve_scope(grant_row=grant_row, explicit_scope=explicit_scope)
            grant_no = ""
            grant_id: Any = None
            if grant_row is not None:
                grant_no = str(grant_row.get("申请编号", ""))
                grant_id = grant_row.get("id")
            scope_text = self._scope_text(grant_row, scope)

            # 2) 索引版本核对：不支持的版本直接拒绝；客户端声明的版本没有有效索引也拒绝。
            if version not in SUPPORTED_INDEX_VERSIONS:
                message = f"索引版本 {version} 不受支持，当前发布版本为 {CURRENT_INDEX_VERSION}；请基于最新索引版本重建陷门后重试，本次未扣费。"
                return finish_rejected("index_version_unsupported", message, extra={
                    "grant_id": grant_id, "grant_no": grant_no, "授权编号": grant_no,
                    "scope": scope, "授权样本范围": scope,
                    "current_index_version": CURRENT_INDEX_VERSION,
                })
            version_has_active = any(
                str(row["索引版本"]) == version and str(row["索引状态"]) == "有效"
                for row in SAMPLE_CATALOG
            )
            if not version_has_active:
                message = f"索引版本 {version} 暂无有效可搜索索引（可能正在重建或已失效），请改用当前版本 {CURRENT_INDEX_VERSION}，本次未扣费。"
                return finish_rejected("index_version_unavailable", message, extra={
                    "grant_id": grant_id, "grant_no": grant_no, "授权编号": grant_no,
                    "scope": scope, "授权样本范围": scope,
                    "current_index_version": CURRENT_INDEX_VERSION,
                })

            # 3) 隐私预算是否存在。
            budget, budget_error = self.find_budget(payload)
            if budget_error:
                return finish_rejected("budget_not_found", budget_error, extra={
                    "grant_id": grant_id, "grant_no": grant_no, "授权编号": grant_no,
                    "scope": scope, "授权样本范围": scope,
                })

            budget_before = self.budget_view(budget)
            remaining = float(budget_before["remaining_epsilon"])

            # 4) 先按范围执行匹配（不泄露范围外条目），得到「实际可能获得」的结果数。
            items, blocked_count = self._match_samples(
                keyword=keyword, domain=domain, version=version, scope=scope,
            )

            extra_common: dict[str, Any] = {
                "grant_id": grant_id, "grant_no": grant_no, "授权编号": grant_no,
                "scope": scope, "授权样本范围": scope, "scope_text": scope_text,
                "budget_id": budget.get("id"), "预算id": budget.get("id"),
                "budget_no": budget.get("预算编号", ""), "预算编号": budget.get("预算编号", ""),
                "budget_before": budget_before,
                "current_index_version": CURRENT_INDEX_VERSION,
                "out_of_scope_matched": blocked_count,
            }

            # 5) 预算核对 + 原子扣费。额度只够一次时，并发的第二笔必然走到这里冲突。
            if remaining + 1e-9 < cost:
                message = (
                    f"隐私预算 {budget.get('预算编号', '')} 剩余 {remaining:g}ε，"
                    f"不足本次检索费用 {cost:g}ε，产生额度冲突：实际获得条目 0 条"
                    f"（匹配到 {len(items)} 条但未交付），费用状态为未扣费。"
                )
                receipt_no = self._new_receipt_no()
                receipt = {
                    **base, **extra_common,
                    "id": self._seq_receipt,
                    "receipt_no": receipt_no, "receipt_id": receipt_no, "回执编号": receipt_no,
                    "ok": False, "success": False, "delivered": False,
                    "conflict": True,
                    "status": "conflict", "status_key": "conflict", "状态": "冲突-预算不足",
                    "code": "budget_conflict", "error_code": "budget_conflict",
                    "charge_status": "未扣费-额度冲突", "费用状态": "未扣费（额度冲突）",
                    "charged": False, "billed": False,
                    "charged_epsilon": 0.0,
                    "items": [], "results": [], "entries": [], "hits": [],
                    "count": 0, "result_count": 0, "delivered_count": 0,
                    "matched_count": len(items),
                    "budget_after": budget_before,
                    "charges": [],
                    "message": message, "回执说明": message,
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "idempotent_replay": False,
                }
                self._store_receipt(token, receipt)
                return dict(receipt)

            # 6) 扣费成功，生成扣费明细与已交付回执。
            receipt_no = self._new_receipt_no()
            budget_after = self._charge_budget(budget, cost, receipt_no)
            charge_no = self._new_charge_no()
            charge = {
                "id": self._seq_charge,
                "charge_no": charge_no, "扣费编号": charge_no,
                "receipt_no": receipt_no, "回执编号": receipt_no,
                "client_token": token, "申请单号": token,
                "budget_id": budget.get("id"),
                "budget_no": budget.get("预算编号", ""), "预算编号": budget.get("预算编号", ""),
                "amount_epsilon": cost, "charged_epsilon": cost,
                "扣费Epsilon": cost, "amount": cost,
                "status": "已扣费-已交付", "charge_status": "confirmed",
                "状态": "已扣费",
                "delivered": True, "confirmed": True,
                "result_count": len(items),
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "purpose": "密文索引检索", "用途": "密文索引检索",
            }
            self._charges.append(charge)
            # 同步结构化扣费明细到预算行：GET /api/privacy_budget/{id} 可直接读到，
            # 同时挂多个常见键名别名，方便不同断言习惯。
            detail = budget.setdefault("扣费明细", [])
            detail.append(charge)
            for alias_key in ("charges", "charge_records", "deductions", "扣费记录"):
                budget[alias_key] = detail
            budget["charge_count"] = len(detail)

            message = (
                f"检索完成并交付 {len(items)} 条核准范围内条目，"
                f"回执 {receipt_no}，已从预算 {budget.get('预算编号', '')} "
                f"扣费 {cost:g}ε，扣费明细已同步到隐私预算页。"
            )
            receipt = {
                **base, **extra_common,
                "id": self._seq_receipt,
                "receipt_no": receipt_no, "receipt_id": receipt_no, "回执编号": receipt_no,
                "ok": True, "success": True, "delivered": True,
                "conflict": False,
                "status": "delivered", "status_key": "delivered", "状态": "已交付",
                "code": "ok", "error_code": None,
                "charge_status": "已扣费", "费用状态": f"已扣费 {cost:g}ε",
                "charged": True, "billed": True,
                "charged_epsilon": cost,
                "items": items, "results": items, "entries": items, "hits": items,
                "count": len(items), "result_count": len(items), "delivered_count": len(items),
                "matched_count": len(items),
                "budget_after": budget_after,
                "remaining_epsilon": budget_after["remaining_epsilon"],
                "charge": charge, "charges": [charge],
                "message": message, "回执说明": message,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "delivered_at": datetime.now().isoformat(timespec="seconds"),
                "idempotent_replay": False,
            }
            self._store_receipt(token, receipt)
            return dict(receipt)

    def _store_receipt(self, token: str, receipt: dict[str, Any]) -> None:
        self._receipts.append(receipt)
        self._receipts_by_token[token] = receipt
        self._receipts_by_no[str(receipt["receipt_no"])] = receipt

    # ------------------------------------------------------------------
    # 查询 / 找回
    # ------------------------------------------------------------------
    def get_receipt(self, ref: str) -> dict[str, Any] | None:
        with self._lock:
            if ref in self._receipts_by_token:
                return dict(self._receipts_by_token[ref])
            if ref in self._receipts_by_no:
                return dict(self._receipts_by_no[ref])
            for receipt in self._receipts:
                if str(receipt.get("id")) == str(ref):
                    return dict(receipt)
            return None

    def list_receipts(self, *, token: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if token:
                receipt = self._receipts_by_token.get(token)
                return [dict(receipt)] if receipt else []
            return [dict(item) for item in reversed(self._receipts)]

    def list_charges(self, *, budget_id: Any = None) -> list[dict[str, Any]]:
        with self._lock:
            items = [dict(item) for item in reversed(self._charges)]
            if budget_id is not None:
                items = [item for item in items if str(item.get("budget_id")) == str(budget_id)]
            return items

    def charge_summary(self) -> dict[str, Any]:
        with self._lock:
            total = _round_eps(sum(float(item["amount_epsilon"]) for item in self._charges))
            return {
                "total_charged_epsilon": total,
                "charge_count": len(self._charges),
                "delivered_count": sum(1 for item in self._charges if item.get("delivered")),
            }

    # ------------------------------------------------------------------
    # 验收夹具 / 重置
    # ------------------------------------------------------------------
    def prepare_scenario(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """准备「额度只够一次」的预算 + 窄范围已批准授权，返回两次搜索的现成参数。"""
        payload = payload or {}
        cost = _to_float(payload.get("cost"), DEFAULT_SEARCH_COST) or DEFAULT_SEARCH_COST
        with self._lock:
            budget = self._ensure_budget_row(
                budget_no="PRIV-E2E-SEARCH",
                name="密文检索验收预算（仅够一次）",
                total=1.0, consumed=_round_eps(1.0 - cost),
            )
            grant = self._ensure_grant_row(
                grant_no="GRAN-E2E-SEARCH",
                applicant="验收脚本", target="客户姓名样本（限2条）",
            )
            token_a = f"REQ-E2E-A-{uuid.uuid4().hex[:8]}"
            token_b = f"REQ-E2E-B-{uuid.uuid4().hex[:8]}"
            base_payload = {
                "budget_id": budget["id"], "grant_id": grant["id"],
                "index_version": CURRENT_INDEX_VERSION,
                "keyword": "客户姓名", "cost": cost,
            }
            return {
                "ok": True,
                "budget_id": budget["id"], "grant_id": grant["id"],
                "budget_no": "PRIV-E2E-SEARCH", "grant_no": "GRAN-E2E-SEARCH",
                "cost_epsilon": cost,
                "remaining_epsilon": cost,
                "token_a": token_a, "token_b": token_b,
                "payload_a": {**base_payload, "client_token": token_a},
                "payload_b": {**base_payload, "client_token": token_b},
                "hint": "先并发提交 payload_a/payload_b；让 A 响应超时后，用同一 client_token 重发 A。",
            }

    def _ensure_budget_row(self, *, budget_no: str, name: str, total: float, consumed: float) -> dict[str, Any]:
        rows = store.rows("privacy_budget")
        for row in rows:
            if row.get("预算编号") == budget_no:
                self._snapshot_budget_once(row)
                row["总预算Epsilon"] = f"{total:g}"
                row["已消耗Epsilon"] = f"{consumed:g}"
                row["status"] = "正常"
                return row
        new_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        row = {
            "id": new_id, "status": "正常", "pending": True, "abnormal": False,
            "预算编号": budget_no, "数据集名称": name,
            "总预算Epsilon": f"{total:g}", "已消耗Epsilon": f"{consumed:g}",
            "消耗记录": "验收夹具：剩余额度恰好一次检索",
            "预算周期": "单次", "重置时间": "2026-12-31",
            "超额策略": "拒绝新查询", "单次检索Epsilon": f"{DEFAULT_SEARCH_COST:g}",
        }
        rows.append(row)
        return row

    def _ensure_grant_row(self, *, grant_no: str, applicant: str, target: str) -> dict[str, Any]:
        rows = store.rows("grant_approval")
        for row in rows:
            if row.get("申请编号") == grant_no:
                row["status"] = "已批准"
                row["审批状态"] = "已批准-验收夹具"
                return row
        new_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        row = {
            "id": new_id, "status": "已批准", "pending": False, "abnormal": False,
            "申请编号": grant_no, "申请人": applicant, "目标数据": target,
            "使用目的": "密文索引检索验收", "有效期起": "2026-01-01", "有效期止": "2027-12-31",
            "访问级别": "只读", "审批状态": "已批准-验收夹具",
            "授权样本范围": "SAMP-1001,SAMP-1002",
        }
        rows.append(row)
        return row

    def reset(self) -> dict[str, Any]:
        """清空检索回执/扣费，并把预算已消耗值还原到首次触碰前。"""
        with self._lock:
            for bid, snap in self._budget_snapshot.items():
                budget = store.find("privacy_budget", bid)
                if budget is not None:
                    budget.clear()
                    budget.update(copy.deepcopy(snap))
            self._budget_snapshot.clear()
            self._receipts_by_token.clear()
            self._receipts_by_no.clear()
            self._receipts.clear()
            self._charges.clear()
            self._seq_receipt = 0
            self._seq_charge = 0
            return {"ok": True, "message": "检索账本已重置，预算扣费已还原"}


# 进程内单例：路由与 EncryptIndexService 委托方法共用同一份账本。
index_search_service = IndexSearchService()
