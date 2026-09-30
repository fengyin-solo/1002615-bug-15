"""航油加注接口：维护加油任务，覆盖开始加油、完成加注、签收确认等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult, SettlementPayload, SettlementView
from app.services.fueling import (
    SETTLEMENT_COLUMNS,
    SettlementAborted,
    FuelingService,
)

router = APIRouter(prefix="/api/fueling", tags=["航油加注"])

service = FuelingService()

# 与结算模板完全一致的表头顺序，列表页/导出/结算快照共用，杜绝错位。
LIST_FIELDS = SETTLEMENT_COLUMNS
STATUSES = ["待加油", "加油中", "已加注", "已签收"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按加油编号检索"),
    status: str | None = Query(default=None, description="待加油、加油中、已加注、已签收"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按加油编号与状态过滤航油加注列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/settlement", response_model=SettlementView)
def settlement_state() -> SettlementView:
    """读取最新一版结算清单与实时对账条数（历史条数每次重算）。"""
    return SettlementView(**service.settlement_state())


@router.post("/settlement", response_model=SettlementView)
def generate_settlement(payload: SettlementPayload) -> SettlementView:
    """生成结算清单：先自检三端条数，再逐行产出；失败可带 resume 从失败行续算。"""
    try:
        result = service.generate_settlement(resume=payload.resume, fail_after_line=payload.fail_after_line)
    except SettlementAborted as exc:
        raise HTTPException(status_code=409, detail=exc.discrepancy or {"message": str(exc)})
    return SettlementView(**result)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出已发布的结算清单快照；尚未成功生成过清单时给出可读说明，不导出半份。"""
    state = service.settlement_state()
    snapshot = state.get("latest")
    if snapshot is None:
        return {
            "module": "fueling",
            "total": 0,
            "columns": SETTLEMENT_COLUMNS,
            "items": [],
            "message": state["message"],
        }
    return {"module": "fueling", "total": snapshot["total"], **snapshot}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条加油任务明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"加油任务 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条加油任务，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="加油任务已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条加油任务执行开始加油、完成加注、签收确认；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
