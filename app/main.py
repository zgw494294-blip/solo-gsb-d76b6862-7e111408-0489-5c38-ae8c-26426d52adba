"""离线色谱峰归属 API。

POST /v1/assign 用两对内标确定一个正斜率仿射变换
calibrated = slope * measured + intercept（映射到参考时间域），
随后对其余峰做保持顺序的一对一配对：先最大化配对数，
再最小化绝对偏差之和，并列时取配对序列 ((参考下标, 实测下标), ...)
字典序最小者。

所有时间/偏差均以精确分数（fractions.Fraction）运算，
并序列化为约分后的 n/d（分母恒为正）。
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any, Optional, Union

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, StrictInt, field_validator

# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------

PeakId = Union[StrictInt, str]


class ReferencePeak(BaseModel):
    id: PeakId
    time_ms: StrictInt


class MeasuredPeak(BaseModel):
    time_ms: StrictInt


class InternalStandardPair(BaseModel):
    """一对互相对应的内标：参考峰 id 与实测峰下标（从 0 开始）。"""

    reference_id: PeakId
    measured_index: StrictInt = Field(ge=0)


class AssignRequest(BaseModel):
    reference_peaks: list[ReferencePeak] = Field(min_length=2, max_length=40)
    measured_peaks: list[MeasuredPeak] = Field(min_length=2, max_length=60)
    internal_standards: list[InternalStandardPair] = Field(min_length=2, max_length=2)
    tolerance_ms: StrictInt = Field(ge=0)

    @field_validator("internal_standards")
    @classmethod
    def _exactly_two(cls, v: list[InternalStandardPair]) -> list[InternalStandardPair]:
        # Field(min_length=2, max_length=2) 已在 schema 层保证长度，
        # 这里保留显式校验，语义自文档化。
        if len(v) != 2:
            raise ValueError("exactly two internal standard pairs are required")
        return v


# 动态规划状态：(-配对数, 绝对偏差之和 Fraction, 配对序列 tuple)
# 直接对该三元组取 min 即按题意三级比较（count 取负 -> 越大越优）。
DpState = tuple[int, Fraction, tuple[tuple[int, int], ...]]


def _better(a: Optional[DpState], b: Optional[DpState]) -> Optional[DpState]:
    """返回两个状态中更优者；None 表示不可达。"""
    if a is None:
        return b
    if b is None:
        return a
    return a if a <= b else b


# ---------------------------------------------------------------------------
# 纯函数核心
# ---------------------------------------------------------------------------


def _frac(value: Fraction) -> dict[str, int]:
    """把分数序列化为约分后的 n/d（分母恒为正）。"""
    return {"n": value.numerator, "d": value.denominator}


def _validate_and_index(
    request: AssignRequest,
) -> tuple[
    list[ReferencePeak],
    list[MeasuredPeak],
    tuple[int, int],
    tuple[int, int],
]:
    """语义校验。

    返回 (参考峰列表, 实测峰列表, 参考内标下标(升序), 实测内标下标(升序))。
    任何非法输入抛 422。
    """
    refs = request.reference_peaks
    meas = request.measured_peaks

    for i in range(1, len(refs)):
        if refs[i].time_ms <= refs[i - 1].time_ms:
            raise HTTPException(
                status_code=422,
                detail="reference_peaks time_ms must be strictly increasing",
            )

    for i in range(1, len(meas)):
        if meas[i].time_ms <= meas[i - 1].time_ms:
            raise HTTPException(
                status_code=422,
                detail="measured_peaks time_ms must be strictly increasing",
            )

    seen: set[Any] = set()
    for peak in refs:
        if peak.id in seen:
            raise HTTPException(
                status_code=422, detail="reference peak ids must be unique"
            )
        seen.add(peak.id)

    ist1, ist2 = request.internal_standards

    if ist1.reference_id == ist2.reference_id:
        raise HTTPException(
            status_code=422,
            detail="internal standard reference ids must be distinct",
        )
    if ist1.measured_index == ist2.measured_index:
        raise HTTPException(
            status_code=422,
            detail="internal standard measured indices must be distinct",
        )

    id_to_index = {peak.id: i for i, peak in enumerate(refs)}
    for pair in request.internal_standards:
        if pair.reference_id not in id_to_index:
            raise HTTPException(
                status_code=422,
                detail=f"unknown internal standard reference_id: {pair.reference_id!r}",
            )

    n_meas = len(meas)
    for pair in request.internal_standards:
        if pair.measured_index >= n_meas:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"internal standard measured_index {pair.measured_index} "
                    f"out of range [0, {n_meas - 1}]"
                ),
            )

    r1 = id_to_index[ist1.reference_id]
    r2 = id_to_index[ist2.reference_id]
    m1 = ist1.measured_index
    m2 = ist2.measured_index

    # 两对内标顺序一致 <=> 参考下标差与实测下标差同号 <=> 严格正斜率。
    if (r2 - r1) * (m2 - m1) <= 0:
        raise HTTPException(
            status_code=422,
            detail=(
                "internal standard order must agree on both sides; "
                "the affine transform must have a strictly positive slope"
            ),
        )

    if r1 < r2:  # 此时必有 m1 < m2
        return refs, meas, (r1, r2), (m1, m2)
    return refs, meas, (r2, r1), (m2, m1)


def _match_region(
    ref_idx: list[int],
    meas_idx: list[int],
    ref_times: list[int],
    meas_times: list[int],
    slope: Fraction,
    intercept: Fraction,
    tolerance_num: int,
) -> tuple[tuple[int, int], ...]:
    """对一个不含内标的区域做顺序保持的最优一对一配对。

    LCS 式动态规划。配对 (ri, mi) 的偏差
        err = slope * t_m + intercept - t_r
    仅当 |err| <= tolerance 时允许（整数比较，分母为 slope.d，恒正）。
    优化顺序：配对数最多 -> |err| 之和最小 -> 配对序列字典序最小。
    """
    a = len(ref_idx)
    b = len(meas_idx)
    tol = Fraction(tolerance_num)

    def deviation(ri: int, mi: int) -> Fraction:
        return slope * meas_times[mi] + intercept - ref_times[ri]

    # dp[i][j]: 仅用前 i 个候选参考、前 j 个候选实测时的最优状态
    dp: list[list[Optional[DpState]]] = [
        [None] * (b + 1) for _ in range(a + 1)
    ]
    dp[0][0] = (0, Fraction(0), ())

    for i in range(a + 1):
        for j in range(b + 1):
            if i == 0 and j == 0:
                continue
            best: Optional[DpState] = None
            # 跳过参考点 i-1
            if i > 0:
                best = _better(best, dp[i - 1][j])
            # 跳过实测点 j-1
            if j > 0:
                best = _better(best, dp[i][j - 1])
            # 配对 (ref_idx[i-1], meas_idx[j-1])
            if i > 0 and j > 0 and dp[i - 1][j - 1] is not None:
                ri = ref_idx[i - 1]
                mi = meas_idx[j - 1]
                err = deviation(ri, mi)
                if abs(err) <= tol:
                    neg_cnt, cost, seq = dp[i - 1][j - 1]  # type: ignore[misc]
                    candidate: DpState = (
                        neg_cnt - 1,
                        cost + abs(err),
                        seq + ((ri, mi),),
                    )
                    best = _better(best, candidate)
            dp[i][j] = best

    final = dp[a][b]
    assert final is not None  # 全不配对总是可行
    return final[2]


def assign_peaks(
    request: AssignRequest,
) -> dict[str, Any]:
    """完成校验、校准、配对并构造响应体。"""
    refs, meas, (r_lo, r_hi), (m_lo, m_hi) = _validate_and_index(request)

    ref_times = [p.time_ms for p in refs]
    meas_times = [p.time_ms for p in meas]
    tolerance_num = request.tolerance_ms

    # ---- 由两对内标确定正斜率仿射变换（精确分数）----
    slope = Fraction(
        ref_times[r_hi] - ref_times[r_lo],
        meas_times[m_hi] - meas_times[m_lo],
    )  # 分子分母均为正
    intercept = Fraction(ref_times[r_lo]) - slope * meas_times[m_lo]

    # ---- 内标切成三段，各自独立做顺序保持配对；内标本身强制配对 ----
    region_bounds = [
        ((0, r_lo), (0, m_lo)),
        ((r_lo + 1, r_hi), (m_lo + 1, m_hi)),
        ((r_hi + 1, len(refs)), (m_hi + 1, len(meas))),
    ]
    anchors = ((r_lo, m_lo), (r_hi, m_hi))

    pairs: list[tuple[int, int]] = []
    for k, ((ra, rb), (ma, mb)) in enumerate(region_bounds):
        seg = _match_region(
            list(range(ra, rb)),
            list(range(ma, mb)),
            ref_times,
            meas_times,
            slope,
            intercept,
            tolerance_num,
        )
        pairs.extend(seg)
        if k < len(anchors):
            pairs.append(anchors[k])  # 段末插入强制配对的内标

    # ---- 构造响应 ----
    matched_meas: set[int] = set()
    assignments: list[Optional[dict[str, Any]]] = [None] * len(refs)

    for ri, mi in pairs:
        matched_meas.add(mi)
        calibrated = slope * meas_times[mi] + intercept
        deviation = calibrated - ref_times[ri]
        assignments[ri] = {
            "measured_index": mi,
            "calibrated_time_ms": _frac(calibrated),
            "deviation_ms": _frac(deviation),
            "is_internal_standard": ri in (r_lo, r_hi),
        }

    unpaired_measured = [
        i for i in range(len(meas)) if i not in matched_meas
    ]

    return {
        "assignments": assignments,
        "unpaired_measured_indices": unpaired_measured,
        "calibration": {
            "slope": _frac(slope),
            "intercept_ms": _frac(intercept),
            "internal_standards": [
                {"reference_index": r_lo, "measured_index": m_lo},
                {"reference_index": r_hi, "measured_index": m_hi},
            ],
        },
        "tolerance_ms": request.tolerance_ms,
    }


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------

app = FastAPI(
    title="离线色谱峰归属 API",
    version="1.0.0",
    description=(
        "用两对内标做正斜率仿射校准，并对其余色谱峰做保持顺序的"
        "一对一最优归属。时间与偏差均返回约分分数 n/d。"
    ),
)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "chromatography-peak-assignment", "docs": "/docs"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/assign")
def assign(request: AssignRequest) -> dict[str, Any]:
    return assign_peaks(request)
