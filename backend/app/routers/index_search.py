"""检索申请 / 检索回执 / 扣费明细接口。

为兼容不同的调用习惯，同一组领域能力挂了多组路径别名
（``/api/encrypt_index/searches``、``/api/index_search``、
``/api/search_requests`` 等），业务语义只在
:class:`app.services.index_search.IndexSearchService` 中维护一份。

注意：本路由必须先于通用 ``resource`` 路由注册，避免
``/api/{module}/{entry_id}`` 把 ``searches`` / ``charges`` 当成数字 id。
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services.index_search import (
    DELAY_KEYS,
    TOKEN_KEYS,
    index_search_service,
)

router = APIRouter(prefix="/api", tags=["密文检索申请与回执"])


async def _read_payload(request: Request) -> dict[str, Any]:
    """宽容解析请求体：JSON 对象 / query 参数 / 表单都接受。"""
    payload: dict[str, Any] = {}
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            raw = await request.json()
            if isinstance(raw, dict):
                payload.update(raw)
        except Exception:
            pass
    elif "form" in content_type:
        try:
            form = await request.form()
            payload.update({key: value for key, value in form.items()})
        except Exception:
            pass
    # 兼容 {"values": {...}} / {"payload": {...}} / {"data": {...}} 嵌套写法，
    # 外层字段优先。
    for nested_key in ("values", "payload", "data"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            merged = {**nested, **payload}
            payload = merged
            break
    # query 参数作为兜底（也允许 ?delay_ms= 这种调试写法）。
    for key, value in request.query_params.items():
        payload.setdefault(key, value)
    # 标准幂等头：Idempotency-Key / X-Request-Id。
    header_token = request.headers.get("idempotency-key") or request.headers.get("x-request-id")
    if header_token and not any(k in payload and payload[k] for k in TOKEN_KEYS):
        payload["client_token"] = header_token
    return payload


def _delay_ms(payload: dict[str, Any], replay: bool) -> int:
    if replay:
        return 0  # 重发必须立刻拿回已确认回执，不再模拟首次响应延迟。
    for key in DELAY_KEYS:
        if key in payload and payload[key] not in (None, ""):
            try:
                return max(0, min(10_000, int(float(str(payload[key])))))
            except (TypeError, ValueError):
                return 0
    if payload.get("simulate_timeout") in (True, "true", "True", 1, "1"):
        return 4_000
    if payload.get("slow") in (True, "true", "True", 1, "1"):
        return 2_000
    return 0


async def _submit_response(request: Request, entry_id: int | None = None) -> JSONResponse:
    payload = await _read_payload(request)
    if entry_id is not None and "entry_id" not in payload:
        # 从 /api/encrypt_index/{entry_id}/search 进来时，用条目补全上下文。
        from app.services import service_for

        service = service_for("encrypt_index")
        entry = service.get_entry(entry_id) if service else None
        if entry is not None:
            payload.setdefault("domain", entry.get("加密域"))
            payload.setdefault("index_version", entry.get("索引版本"))
            payload["entry_id"] = entry_id

    body = await asyncio.to_thread(index_search_service.submit_search, payload)
    delay = _delay_ms(payload, bool(body.get("idempotent_replay")))
    if delay:
        await asyncio.sleep(delay / 1000.0)
    # 业务结果（含额度冲突）统一 200 + ok 字段，与全站 ActionResult 约定一致；
    # 完整回执放在 body.receipt，同时字段平铺在顶层方便断言。
    return JSONResponse(_wrap_submit(body))


def _wrap_submit(body: dict[str, Any]) -> dict[str, Any]:
    return {
        **body,
        "receipt": body,
        "data": body,
        "entry": body,
        "result": body if body.get("ok") else None,
    }


# ---------------------------------------------------------------------------
# 选项 / 目录（静态路径必须在动态 {ref} 之前注册）
# ---------------------------------------------------------------------------
@router.get("/index_search/options")
@router.get("/encrypt_index/options")
@router.get("/encrypt_index/search-options")
def search_options() -> dict[str, Any]:
    return index_search_service.options()


@router.post("/index_search/scenario/prepare")
@router.post("/encrypt_index/scenario/prepare")
def prepare_scenario(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return index_search_service.prepare_scenario(payload or {})


@router.post("/index_search/reset")
@router.post("/encrypt_index/reset")
def reset_search() -> dict[str, Any]:
    return index_search_service.reset()


# ---------------------------------------------------------------------------
# 提交检索申请（多路径别名）
# ---------------------------------------------------------------------------
@router.post("/encrypt_index/searches")
@router.post("/encrypt_index/search")
@router.post("/encrypt_index/requests")
@router.post("/encrypt_index/search-requests")
@router.post("/encrypt_index/search_applications")
@router.post("/encrypt_index/retrievals")
@router.post("/index_searches")
@router.post("/index_searches/")
@router.post("/index_search")
@router.post("/index_search/")
@router.post("/search_requests")
@router.post("/search_requests/")
@router.post("/search-requests")
@router.post("/search_request")
@router.post("/search_applications")
@router.post("/retrieval_requests")
async def submit_search(request: Request) -> JSONResponse:
    return await _submit_response(request)


@router.post("/encrypt_index/{entry_id}/search")
async def submit_search_for_entry(request: Request, entry_id: str) -> JSONResponse:
    try:
        eid: int | None = int(entry_id)
    except ValueError:
        # 与 /encrypt_index/searches 等同级静态路径不会进到这里（静态路由先注册）。
        eid = None
    return await _submit_response(request, eid)


# ---------------------------------------------------------------------------
# 回执查询 / 找回
# ---------------------------------------------------------------------------
def _list_receipts_payload(request: Request) -> dict[str, Any]:
    token = request.query_params.get("token") or request.query_params.get("client_token")
    receipts = index_search_service.list_receipts(token=token)
    return {
        "items": receipts,
        "receipts": receipts,
        "results": receipts,
        "total": len(receipts),
        "page": 1,
        "size": max(len(receipts), 1),
    }


@router.get("/encrypt_index/searches")
@router.get("/encrypt_index/search")
@router.get("/encrypt_index/requests")
@router.get("/encrypt_index/search_requests")
@router.get("/encrypt_index/search-requests")
@router.get("/encrypt_index/receipts")
@router.get("/index_searches")
@router.get("/index_search")
@router.get("/search_requests")
@router.get("/search-requests")
@router.get("/search_request")
def list_searches(request: Request) -> dict[str, Any]:
    return _list_receipts_payload(request)


@router.get("/encrypt_index/searches/{ref}")
@router.get("/encrypt_index/search/{ref}")
@router.get("/encrypt_index/receipts/{ref}")
@router.get("/index_searches/{ref}")
@router.get("/index_search/receipts/{ref}")
@router.get("/index_search/{ref}")
@router.get("/search_requests/{ref}")
@router.get("/search-requests/{ref}")
@router.get("/search_request/{ref}")
def get_search(ref: str, request: Request) -> JSONResponse:
    receipt = index_search_service.get_receipt(ref)
    if receipt is None:
        # 兼容 /index_search/receipt?token=… 这类被 {ref} 路由抢先匹配的写法。
        qp = request.query_params
        fallback = qp.get("token") or qp.get("client_token") or qp.get("receipt_no")
        if fallback:
            receipt = index_search_service.get_receipt(fallback)
    if receipt is None:
        return JSONResponse(
            status_code=404,
            content={"ok": False, "message": f"检索回执 {ref} 不存在，可能服务已重启或申请单号有误"},
        )
    return JSONResponse({**receipt, "receipt": receipt, "data": receipt, "entry": receipt})


@router.post("/encrypt_index/receipts/lookup")
@router.post("/index_search/receipts/lookup")
async def lookup_receipt(request: Request) -> JSONResponse:
    payload = await _read_payload(request)
    ref = (
        payload.get("ref") or payload.get("receipt_no") or payload.get("id")
        or next((payload[k] for k in TOKEN_KEYS if k in payload), None)
    )
    receipt = index_search_service.get_receipt(str(ref or "")) if ref else None
    if receipt is None:
        return JSONResponse(status_code=404, content={"ok": False, "message": "未找到已确认回执"})
    return JSONResponse({**receipt, "receipt": receipt})


@router.get("/encrypt_index/receipt")
@router.get("/index_search/receipt")
@router.get("/search_request/receipt")
def get_receipt_by_query(
    ref: str | None = None,
    token: str | None = None,
    client_token: str | None = None,
    receipt_no: str | None = None,
) -> JSONResponse:
    lookup = ref or receipt_no or token or client_token
    receipt = index_search_service.get_receipt(str(lookup)) if lookup else None
    if receipt is None:
        return JSONResponse(status_code=404, content={"ok": False, "message": "未找到已确认回执"})
    return JSONResponse({**receipt, "receipt": receipt, "entry": receipt})


# ---------------------------------------------------------------------------
# 隐私预算页：检索扣费明细
# ---------------------------------------------------------------------------
@router.get("/privacy_budget/charges")
@router.get("/privacy_budget/charges/")
def list_all_charges(request: Request) -> dict[str, Any]:
    budget_id = request.query_params.get("budget_id")
    charges = index_search_service.list_charges(budget_id=budget_id)
    summary = index_search_service.charge_summary()
    return {
        "items": charges,
        "charges": charges,
        "total": len(charges),
        "page": 1,
        "size": max(len(charges), 1),
        **summary,
    }


@router.get("/privacy_budget/{budget_id}/charges")
def list_budget_charges(budget_id: int) -> dict[str, Any]:
    charges = index_search_service.list_charges(budget_id=budget_id)
    total_eps = round(sum(float(c["amount_epsilon"]) for c in charges), 6)
    return {
        "items": charges,
        "charges": charges,
        "budget_id": budget_id,
        "total": len(charges),
        "total_charged_epsilon": total_eps,
    }
