"""密文索引检索申请：版本/授权/预算核对、幂等重发、并发争用与扣费明细。

本文件的每个用例前后都会清空检索账本并还原预算行，避免向共享内存仓库
遗留夹具行，保证同一会话里的其它测试仍看到原始种子状态。
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services.encrypt_index import EncryptIndexService
from app.services.index_search import index_search_service


@pytest.fixture(autouse=True)
def clean_ledger():
    index_search_service.reset()
    yield
    index_search_service.reset()


def _one_shot_payload(token: str, **overrides):
    # PRIV-0002：总预算 5.0、已消耗 4.7，剩余 0.3；单次费用报 0.3
    # 时恰好只够一次检索。范围显式收敛到 2 个客户姓名样本。
    payload = {
        "client_token": token,
        "budget_id": 2,
        "cost": 0.3,
        "index_version": "v1",
        "keyword": "客户姓名",
        "scope": "SAMP-1001,SAMP-1002",
    }
    payload.update(overrides)
    return payload


class TestHappyPath:
    def test_service_delegation_exists(self):
        svc = EncryptIndexService()
        assert hasattr(svc, "submit_search_request")
        assert hasattr(svc, "get_search_receipt")
        assert callable(svc.list_charge_records)

    def test_delivered_receipt_and_scoped_items(self):
        receipt = EncryptIndexService().submit_search_request(_one_shot_payload("T-OK-1"))
        assert receipt["ok"] is True
        assert receipt["status"] == "delivered"
        assert receipt["receipt_no"].startswith("RCPT-")
        assert receipt["count"] == 2
        assert {i["样本编号"] for i in receipt["items"]} == {"SAMP-1001", "SAMP-1002"}
        assert receipt["charged"] is True
        assert abs(float(receipt["charged_epsilon"]) - 0.3) < 1e-9
        assert "已扣费" in receipt["charge_status"]

    def test_scope_is_hard_boundary(self):
        receipt = index_search_service.submit_search(
            _one_shot_payload("T-SCOPE", scope="SAMP-1003", keyword="客户姓名"),
        )
        assert receipt["count"] == 1
        assert receipt["items"][0]["样本编号"] == "SAMP-1003"

    def test_out_of_scope_never_returned(self):
        # 关键词为空 + 只授权一个手机号样本：不得带出其它任何样本。
        receipt = index_search_service.submit_search(
            _one_shot_payload("T-SCOPE-2", scope=["SAMP-2001"], keyword="", cost=0.1),
        )
        assert [i["样本编号"] for i in receipt["items"]] == ["SAMP-2001"]

    def test_invalid_index_version_rejected_without_charge(self):
        receipt = index_search_service.submit_search(
            _one_shot_payload("T-VER", index_version="v9"),
        )
        assert receipt["ok"] is False
        assert receipt["code"] == "index_version_unsupported"
        assert receipt["charged"] is False
        assert index_search_service.list_charges() == []

    def test_rebuilding_index_not_searchable(self):
        # 设备 IMEI 样本挂在 v2/重建中 的 ENCR-0002 上，v2 下也不可交付。
        receipt = index_search_service.submit_search(
            _one_shot_payload("T-IMEI", scope="SAMP-4001", index_version="v2",
                              keyword="", cost=0.1),
        )
        assert receipt["count"] == 0

    def test_unapproved_grant_rejected(self):
        receipt = index_search_service.submit_search(
            _one_shot_payload("T-GRANT", grant_id=1),  # GRAN-0001 待审批
        )
        assert receipt["ok"] is False
        assert receipt["code"] == "grant_not_approved"
        assert index_search_service.list_charges() == []


class TestIdempotency:
    def test_retry_same_token_does_not_double_charge(self):
        first = index_search_service.submit_search(_one_shot_payload("T-IDEM-1"))
        retry = index_search_service.submit_search(_one_shot_payload("T-IDEM-1"))
        assert first["receipt_no"] == retry["receipt_no"]
        assert retry["idempotent_replay"] is True
        charges = index_search_service.list_charges(budget_id=2)
        assert len(charges) == 1
        assert abs(sum(c["amount_epsilon"] for c in charges) - 0.3) < 1e-9

    def test_receipt_can_be_found_by_token_and_no(self):
        first = index_search_service.submit_search(_one_shot_payload("T-FIND-1"))
        by_token = index_search_service.get_receipt("T-FIND-1")
        by_no = index_search_service.get_receipt(first["receipt_no"])
        assert by_token is not None and by_token["receipt_no"] == first["receipt_no"]
        assert by_no is not None and len(by_no["items"]) == 2


class TestConcurrencyConflict:
    def test_two_interleaved_searches_single_budget(self):
        start = threading.Barrier(2)

        def run(token):
            start.wait()
            return index_search_service.submit_search(_one_shot_payload(token))

        with ThreadPoolExecutor(max_workers=2) as pool:
            fa = pool.submit(run, "T-RACE-A")
            fb = pool.submit(run, "T-RACE-B")
            ra, rb = fa.result(timeout=10), fb.result(timeout=10)

        outcomes = [ra, rb]
        delivered = [r for r in outcomes if r["ok"]]
        conflicts = [r for r in outcomes if r.get("conflict")]
        assert len(delivered) == 1, outcomes
        assert len(conflicts) == 1, outcomes

        winner, loser = delivered[0], conflicts[0]
        assert winner["count"] == 2
        assert winner["charged"] is True
        # 冲突回执必须说明实际结果（0 条）与费用状态（未扣费）。
        assert loser["count"] == 0
        assert loser["items"] == []
        assert loser["charged"] is False
        assert "未扣费" in loser["charge_status"]
        assert "0 条" in loser["message"]

        charges = index_search_service.list_charges(budget_id=2)
        assert len(charges) == 1
        assert charges[0]["receipt_no"] == winner["receipt_no"]

        # 让第一次（获胜的）请求“超时”，用同一 token 重试：返回原回执，不多扣费。
        retry = index_search_service.submit_search(_one_shot_payload(winner["client_token"]))
        assert retry["receipt_no"] == winner["receipt_no"]
        assert retry["idempotent_replay"] is True
        assert len(index_search_service.list_charges(budget_id=2)) == 1

    def test_conflict_replay_is_still_conflict_and_not_charged(self):
        payload = _one_shot_payload("T-CONF-REPLAY")
        index_search_service.submit_search(payload)
        second = index_search_service.submit_search(_one_shot_payload("T-OTHER"))
        assert second["conflict"] is True
        replay = index_search_service.submit_search(_one_shot_payload("T-OTHER"))
        assert replay["receipt_no"] == second["receipt_no"]
        assert replay["idempotent_replay"] is True
        assert len(index_search_service.list_charges(budget_id=2)) == 1
