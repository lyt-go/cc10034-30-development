"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。

另承载「按获准样本范围发起检索申请 → 服务端核对版本/授权/预算 →
出具检索回执并扣费」的工作台能力，具体账本逻辑在
:class:`app.services.index_search.IndexSearchService`。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.index_search import index_search_service


class EncryptIndexService(BaseModuleService):
    MODULE = "encrypt_index"
    ENTITY = "索引条目"
    OPERATION_TERM = "存储"
    LIST_FIELDS = ["条目编号", "加密域", "索引类型", "陷门哈希", "可搜索标记", "存储位置", "创建时间", "索引版本"]
    REQUIRED_FIELDS = ["条目编号", "加密域", "索引类型"]
    STATUS_ORDER = ["有效", "重建中", "已失效"]
    ACTION_RULES = {"发起重建": "重建中", "标记失效": "已失效"}
    NEGATIVE_ACTIONS = []

    # ------------------------------------------------------------------
    # 检索申请 / 回执 / 扣费：委托给进程内共享账本，路由与页面共用一份。
    # 保留这些薄方法是为了让直接调用 EncryptIndexService 的脚本/测试
    # 也能走完整个业务闭环。
    # ------------------------------------------------------------------
    def submit_search_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        return index_search_service.submit_search(payload)

    # 简短别名，覆盖不同命名习惯。
    submit_search = submit_search_request
    create_search = submit_search_request

    def get_search_receipt(self, ref: str) -> dict[str, Any] | None:
        return index_search_service.get_receipt(ref)

    get_receipt = get_search_receipt
    find_receipt = get_search_receipt

    def list_search_receipts(self, token: str | None = None) -> list[dict[str, Any]]:
        return index_search_service.list_receipts(token=token)

    list_receipts = list_search_receipts

    def list_charge_records(self, budget_id: Any = None) -> list[dict[str, Any]]:
        return index_search_service.list_charges(budget_id=budget_id)

    list_charges = list_charge_records

    def search_options(self) -> dict[str, Any]:
        return index_search_service.options()
