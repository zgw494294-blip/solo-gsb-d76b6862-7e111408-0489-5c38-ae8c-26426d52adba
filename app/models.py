"""请求/响应 Pydantic 模型。

严格类型：布尔值不会被当作整数，浮点不会被静默截断为整数，
类型层拒绝统一产生 HTTP 422。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, StrictInt, StrictStr


class ReferencePeak(BaseModel):
    id: StrictStr
    time_ms: StrictInt


class AssignRequest(BaseModel):
    reference_peaks: list[ReferencePeak] = Field(min_length=2, max_length=40)
    measured_times_ms: list[StrictInt] = Field(min_length=2, max_length=60)
    is_ref_id_1: StrictStr
    is_meas_idx_1: StrictInt
    is_ref_id_2: StrictStr
    is_meas_idx_2: StrictInt
    tolerance_ms: StrictInt = Field(ge=0)


class FractionOut(BaseModel):
    n: int  # 约分后的分子（可负）
    d: int  # 约分后的分母（恒正）


class MatchOut(BaseModel):
    ref_id: str
    ref_index: int
    meas_index: int
    calibrated_time_ms: FractionOut
    signed_deviation_ms: FractionOut  # 校准值 - 参考值


class AssignResponse(BaseModel):
    # 与参考峰输入顺序一一对应：配对给出实测下标，否则 null
    assignments: list[int | None]
    unpaired_meas_indices: list[int]
    matches: list[MatchOut]
