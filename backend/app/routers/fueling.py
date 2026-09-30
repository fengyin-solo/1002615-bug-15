"""航油加注接口：维护加油任务，覆盖开始加油、完成加注、签收确认与结算清单出单。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.fueling import FuelingService

router = APIRouter(prefix="/api/fueling", tags=["航油加注"])

service = FuelingService()

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


@router.get("/settlement")
def get_settlement() -> dict[str, Any]:
    """读取最近一次结算清单出单状态：最新一版结果、最近中止现场与版本计数。"""
    return service.latest_settlement()


@router.post("/settlement/generate")
def generate_settlement(resume: bool = Query(default=False, description="从上一版失败行继续自检")) -> dict[str, Any]:
    """生成结算清单：出单前自检字段、油量与三处条数，不通过即中止且不落半份清单。"""
    return service.generate_settlement(resume=resume)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出航油加注结算清单：已出单时返回最新一版，未出单时按结算模板即时自检生成。"""
    latest = service.latest_settlement()
    if latest["published"] is not None:
        return latest["published"]
    return service.generate_settlement()


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


@router.patch("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """补录修正被结算自检拦下的行（车辆、油量、操作人员等），修正后可从失败行续作。"""
    entry, message = service.update_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=f"加油任务 {entry_id} 已补录修正", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条加油任务执行开始加油、完成加注、签收确认；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
