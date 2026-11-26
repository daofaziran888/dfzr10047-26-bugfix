"""统一资源接口：查询、导出、登记和动作按模块 key 分派到领域服务。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, CorrectionPayload, EntryPayload, PageResult
from app.services import service_for
from app.services.parse_metric import ParseMetricService

router = APIRouter(prefix="/api", tags=["业务资源"])


def require_service(module: str):
    service = service_for(module)
    if service is None:
        raise HTTPException(status_code=404, detail=f"未知业务模块：{module}")
    return service


def require_metric_service() -> ParseMetricService:
    service = require_service("parse_metric")
    assert isinstance(service, ParseMetricService)
    return service


# ----------------------------------------------------------------------
# 解析指标复核详情与更正入口：渠道和时间窗筛选、归属校验都在服务端完成
# ----------------------------------------------------------------------
@router.get("/parse_metric/{entry_id}/review_detail")
def metric_review_detail(entry_id: int) -> dict[str, Any]:
    """复核详情：返回真正属于该读数的动作清单、迟到凭据与原账读数。"""
    service = require_metric_service()
    detail = service.review_detail_by_id(entry_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"指标读数 {entry_id} 不存在或已归档")
    return detail


@router.post("/parse_metric/{entry_id}/correction", response_model=ActionResult)
def metric_correction(entry_id: int, payload: CorrectionPayload) -> ActionResult:
    """更正入口：并入迟到凭据重算回退率，原账与证据保留。"""
    service = require_metric_service()
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"指标读数 {entry_id} 不存在或已归档")
    ok, message, reading = service.apply_correction(
        str(entry.get("指标读数编号") or ""),
        payload.decision_nos,
        payload.reviewer or "web-reviewer",
        payload.corrected_at,
    )
    return ActionResult(ok=ok, message=message, entry=reading)


@router.post("/parse_metric/{entry_id}/defer_receipts", response_model=ActionResult)
def metric_defer_receipts(entry_id: int, payload: CorrectionPayload) -> ActionResult:
    """沿用旧证据核对当次发布：迟到凭据转待补算，读数保持原账。"""
    service = require_metric_service()
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"指标读数 {entry_id} 不存在或已归档")
    ok, message, reading = service.defer_late_receipts(
        str(entry.get("指标读数编号") or ""),
        payload.reviewer or "web-reviewer",
        payload.corrected_at,
    )
    return ActionResult(ok=ok, message=message, entry=reading)


@router.get("/{module}", response_model=PageResult[dict])
def list_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, total = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{module}/export")
def export_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, _ = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=1, size=1_000_000)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *service.LIST_FIELDS, "status"])
    for item in items:
        values = [item.get("id"), *(item.get(field, "") for field in service.LIST_FIELDS),
                  item.get("status", "")]
        writer.writerow([
            "'" + str(value) if str(value or "").startswith(("=", "+", "-", "@")) else value
            for value in values
        ])
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{module}.csv"'},
    )


@router.get("/{module}/{entry_id}", response_model=dict)
def get_entry(module: str, entry_id: int) -> dict[str, Any]:
    service = require_service(module)
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"{service.ENTITY} {entry_id} 不存在或已归档")
    return entry


@router.post("/{module}", response_model=ActionResult)
def create_entry(module: str, payload: EntryPayload) -> ActionResult:
    service = require_service(module)
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message=f"{service.ENTITY}已登记", entry=entry)


@router.post("/{module}/{entry_id}/actions", response_model=ActionResult)
def run_action(module: str, entry_id: int, payload: ActionPayload) -> ActionResult:
    service = require_service(module)
    entry, message = service.run_action(entry_id, payload.action.strip())
    return ActionResult(ok=entry is not None, message=message, entry=entry)
