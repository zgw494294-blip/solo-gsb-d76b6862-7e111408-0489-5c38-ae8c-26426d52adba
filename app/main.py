"""FastAPI 入口：POST /v1/assign 离线色谱峰归属。"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .assignment import AssignmentError, assign_peaks
from .models import AssignRequest, AssignResponse

app = FastAPI(
    title="离线色谱峰归属 API",
    version="1.0.0",
    description="基于两对互异内标的正斜率仿射校准与保序一对一峰归属。",
)


@app.exception_handler(AssignmentError)
async def assignment_error_handler(
    request: Request, exc: AssignmentError
) -> JSONResponse:
    # 业务规则不满足与 Pydantic 校验失败一致，统一返回 422。
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/assign", response_model=AssignResponse)
async def assign(request: AssignRequest) -> dict:
    return assign_peaks(
        ref_peaks=[p.model_dump() for p in request.reference_peaks],
        meas_times=list(request.measured_times_ms),
        is_ref_id_1=request.is_ref_id_1,
        is_meas_idx_1=request.is_meas_idx_1,
        is_ref_id_2=request.is_ref_id_2,
        is_meas_idx_2=request.is_meas_idx_2,
        tolerance_ms=request.tolerance_ms,
    )
