"""核心配对逻辑测试：解析用例 + 随机小规模暴力枚举一致性。"""

from __future__ import annotations

from fractions import Fraction

import pytest

from app.main import AssignRequest, assign_peaks


def req(ref_times, meas_times, istd, tolerance_ms=0, ids=None):
    """构造 AssignRequest。

    istd: [(参考id, 实测下标), (参考id, 实测下标)]，按输入顺序。
    """
    if ids is None:
        ids = list(range(len(ref_times)))
    return AssignRequest(
        reference_peaks=[
            {"id": i, "time_ms": t} for i, t in zip(ids, ref_times)
        ],
        measured_peaks=[{"time_ms": t} for t in meas_times],
        internal_standards=[
            {"reference_id": rid, "measured_index": mi} for rid, mi in istd
        ],
        tolerance_ms=tolerance_ms,
    )


def err_frac(ref_times, meas_times, slope, intercept, ri, mi):
    return slope * meas_times[mi] + intercept - ref_times[ri]


def brute_force(request: AssignRequest):
    """独立的参考实现：枚举全部合法匹配并按题意选优。"""
    refs = request.reference_peaks
    meas = request.measured_peaks
    ref_t = [p.time_ms for p in refs]
    mea_t = [p.time_ms for p in meas]
    id2i = {p.id: i for i, p in enumerate(refs)}
    a1 = request.internal_standards[0]
    a2 = request.internal_standards[1]
    ar1, am1 = id2i[a1.reference_id], a1.measured_index
    ar2, am2 = id2i[a2.reference_id], a2.measured_index
    if ar1 > ar2:
        ar1, ar2 = ar2, ar1
        am1, am2 = am2, am1

    slope = Fraction(ref_t[ar2] - ref_t[ar1], mea_t[am2] - mea_t[am1])
    intercept = Fraction(ref_t[ar1]) - slope * mea_t[am1]
    tol = Fraction(request.tolerance_ms)

    anchor = {ar1: am1, ar2: am2}
    free_refs = [i for i in range(len(refs)) if i not in anchor]

    used_m = {am1, am2}
    best = None  # (-count, cost, sequence, mapping)

    def evaluate(extra: dict[int, int]):
        nonlocal best
        mapping = dict(anchor)
        mapping.update(extra)
        seq = tuple(sorted(mapping.items()))  # 按参考下标升序
        # 一一对应 + 保持顺序（实测下标也严格递增）
        ms = [m for _, m in seq]
        if ms != sorted(ms) or len(set(ms)) != len(ms):
            return
        cost = Fraction(0)
        for ri, mi in seq:
            e = abs(err_frac(ref_t, mea_t, slope, intercept, ri, mi))
            if ri not in anchor and e > tol:
                return
            cost += e
        key = (-len(seq), cost, seq)
        if best is None or key < best[0]:
            best = (key, mapping)

    # 枚举自由参考峰是否配对、配给谁
    available = [m for m in range(len(meas)) if m not in used_m]

    def rec(k, chosen):
        if k == len(free_refs):
            evaluate(dict(chosen))
            return
        rec(k + 1, chosen)  # 该参考峰不配对
        ri = free_refs[k]
        for mi in available:
            if any(m == mi for _, m in chosen):
                continue
            chosen.append((ri, mi))
            rec(k + 1, chosen)
            chosen.pop()

    rec(0, [])
    assert best is not None
    return best[1], slope, intercept, ar1, ar2, am1, am2


def mapping_of(response):
    return {
        i: a["measured_index"]
        for i, a in enumerate(response["assignments"])
        if a is not None
    }


# ---------------------------------------------------------------------------
# 基础解析用例
# ---------------------------------------------------------------------------


def test_identity_affine_all_paired():
    # slope = 1, intercept = 0；所有峰精确对应
    r = req(
        [0, 100, 200, 300],
        [0, 100, 200, 300],
        [(0, 0), (3, 3)],
    )
    resp = assign_peaks(r)
    assert mapping_of(resp) == {0: 0, 1: 1, 2: 2, 3: 3}
    assert resp["unpaired_measured_indices"] == []
    assert resp["calibration"]["slope"] == {"n": 1, "d": 1}
    assert resp["calibration"]["intercept_ms"] == {"n": 0, "d": 1}
    for a in resp["assignments"]:
        assert a["deviation_ms"] == {"n": 0, "d": 1}
    assert resp["assignments"][0]["is_internal_standard"] is True
    assert resp["assignments"][1]["is_internal_standard"] is False


def test_fractional_slope_exact_arithmetic():
    # 参考 0..6 与实测 0,2,4（含噪声峰），slope = (6-0)/(4-0) = 3/2
    # calibrated = 3/2 * measured
    r = req(
        [0, 3, 6],
        [0, 1, 2, 3, 4],  # 0->0, 4->2；1 校 1.5、3 校 4.5 无对应参考
        [(0, 0), (2, 4)],
        tolerance_ms=0,
    )
    resp = assign_peaks(r)
    m = mapping_of(resp)
    # measured 2 -> calibrated 3/1 精确等于参考 3
    assert m == {0: 0, 1: 2, 2: 4}
    assert resp["assignments"][1]["calibrated_time_ms"] == {"n": 3, "d": 1}
    assert resp["assignments"][1]["deviation_ms"] == {"n": 0, "d": 1}
    assert resp["unpaired_measured_indices"] == [1, 3]
    assert resp["calibration"]["slope"] == {"n": 3, "d": 2}


def test_tolerance_allows_offset_peak():
    # slope 3/2；实测自由峰时间 1、3 -> 校准 1.5、4.5，对参考 3 偏差均为 1.5
    r = req(
        [0, 3, 6],
        [0, 1, 3, 4],
        [(0, 0), (2, 3)],
        tolerance_ms=1,
    )
    resp = assign_peaks(r)
    # 容差 1：1.5 > 1，参考 3 无法配对
    assert mapping_of(resp) == {0: 0, 2: 3}
    assert resp["unpaired_measured_indices"] == [1, 2]

    r = req(
        [0, 3, 6],
        [0, 1, 3, 4],
        [(0, 0), (2, 3)],
        tolerance_ms=2,
    )
    resp = assign_peaks(r)
    m = mapping_of(resp)
    # 容差 2：两个候选偏差同为 1.5，只能配一个；字典序取 (1,1)
    assert m == {0: 0, 1: 1, 2: 3}
    assert resp["unpaired_measured_indices"] == [2]
    assert resp["assignments"][1]["calibrated_time_ms"] == {"n": 3, "d": 2}
    assert resp["assignments"][1]["deviation_ms"] == {"n": -3, "d": 2}


def test_string_ids_and_reversed_istd_order():
    # 内标输入顺序与参考顺序相反时同样合法（自动归一）
    r = req(
        [10, 20, 30],
        [100, 200, 300],
        [("c", 2), ("a", 0)],  # slope = (30-10)/(300-100) = 1/10
        ids=["a", "b", "c"],
    )
    resp = assign_peaks(r)
    assert mapping_of(resp) == {0: 0, 1: 1, 2: 2}
    assert resp["calibration"]["slope"] == {"n": 1, "d": 10}
    assert resp["calibration"]["intercept_ms"] == {"n": 0, "d": 1}


def test_nonzero_intercept():
    # measured 5->ref 0, measured 25 -> ref 100：slope 5, intercept -25
    r = req(
        [0, 50, 100],
        [5, 15, 25],
        [(0, 0), (2, 2)],
    )
    resp = assign_peaks(r)
    assert mapping_of(resp) == {0: 0, 1: 1, 2: 2}
    assert resp["calibration"]["intercept_ms"] == {"n": -25, "d": 1}
    assert resp["assignments"][1]["calibrated_time_ms"] == {"n": 50, "d": 1}


# ---------------------------------------------------------------------------
# 非法输入 -> 422
# ---------------------------------------------------------------------------


def _expect_422(**kwargs):
    with pytest.raises(Exception) as ei:  # HTTPException
        assign_peaks(AssignRequest(**kwargs))
    assert ei.value.status_code == 422


def test_invalid_ref_times_not_strict():
    _expect_422(
        reference_peaks=[{"id": 1, "time_ms": 0}, {"id": 2, "time_ms": 0}],
        measured_peaks=[{"time_ms": 0}, {"time_ms": 10}],
        internal_standards=[
            {"reference_id": 1, "measured_index": 0},
            {"reference_id": 2, "measured_index": 1},
        ],
        tolerance_ms=0,
    )


def test_invalid_meas_times_not_strict():
    _expect_422(
        reference_peaks=[{"id": 1, "time_ms": 0}, {"id": 2, "time_ms": 10}],
        measured_peaks=[{"time_ms": 5}, {"time_ms": 5}],
        internal_standards=[
            {"reference_id": 1, "measured_index": 0},
            {"reference_id": 2, "measured_index": 1},
        ],
        tolerance_ms=0,
    )


def test_invalid_duplicate_ref_id():
    _expect_422(
        reference_peaks=[{"id": "x", "time_ms": 0}, {"id": "x", "time_ms": 10}],
        measured_peaks=[{"time_ms": 0}, {"time_ms": 10}],
        internal_standards=[
            {"reference_id": "x", "measured_index": 0},
            {"reference_id": "x", "measured_index": 1},
        ],
        tolerance_ms=0,
    )


def test_invalid_same_reference_id_istd():
    _expect_422(
        reference_peaks=[{"id": 1, "time_ms": 0}, {"id": 2, "time_ms": 10}],
        measured_peaks=[{"time_ms": 0}, {"time_ms": 10}],
        internal_standards=[
            {"reference_id": 1, "measured_index": 0},
            {"reference_id": 1, "measured_index": 1},
        ],
        tolerance_ms=0,
    )


def test_invalid_same_measured_index_istd():
    _expect_422(
        reference_peaks=[{"id": 1, "time_ms": 0}, {"id": 2, "time_ms": 10}],
        measured_peaks=[{"time_ms": 0}, {"time_ms": 10}],
        internal_standards=[
            {"reference_id": 1, "measured_index": 0},
            {"reference_id": 2, "measured_index": 0},
        ],
        tolerance_ms=0,
    )


def test_invalid_unknown_reference_id():
    _expect_422(
        reference_peaks=[{"id": 1, "time_ms": 0}, {"id": 2, "time_ms": 10}],
        measured_peaks=[{"time_ms": 0}, {"time_ms": 10}],
        internal_standards=[
            {"reference_id": 99, "measured_index": 0},
            {"reference_id": 2, "measured_index": 1},
        ],
        tolerance_ms=0,
    )


def test_invalid_measured_index_out_of_range():
    _expect_422(
        reference_peaks=[{"id": 1, "time_ms": 0}, {"id": 2, "time_ms": 10}],
        measured_peaks=[{"time_ms": 0}, {"time_ms": 10}],
        internal_standards=[
            {"reference_id": 1, "measured_index": 0},
            {"reference_id": 2, "measured_index": 7},
        ],
        tolerance_ms=0,
    )


def test_invalid_order_mismatch_negative_slope():
    _expect_422(
        reference_peaks=[{"id": 1, "time_ms": 0}, {"id": 2, "time_ms": 10}],
        measured_peaks=[{"time_ms": 0}, {"time_ms": 10}],
        internal_standards=[
            {"reference_id": 1, "measured_index": 1},
            {"reference_id": 2, "measured_index": 0},
        ],
        tolerance_ms=0,
    )


def test_invalid_negative_tolerance_schema():
    with pytest.raises(Exception):
        AssignRequest(
            reference_peaks=[{"id": 1, "time_ms": 0}, {"id": 2, "time_ms": 10}],
            measured_peaks=[{"time_ms": 0}, {"time_ms": 10}],
            internal_standards=[
                {"reference_id": 1, "measured_index": 0},
                {"reference_id": 2, "measured_index": 1},
            ],
            tolerance_ms=-1,
        )


# ---------------------------------------------------------------------------
# 字典序并列打破
# ---------------------------------------------------------------------------


def test_lexicographic_tie_break():
    # 两个孤立参考峰 r=1,3 各只有一种配法，偏差同为 0，但验证序列按 (r,m) 升序
    r = req(
        [0, 10, 20, 30, 40],
        [0, 10, 30, 40],  # 内标 (0,0) 与 (40,3)；r=10/r=30 精确，r=20 缺失
        [(0, 0), (4, 3)],
    )
    resp = assign_peaks(r)
    assert mapping_of(resp) == {0: 0, 1: 1, 3: 2, 4: 3}
    assert resp["unpaired_measured_indices"] == []
    assert resp["assignments"][2] is None  # 参考 20 未配对


def test_lexicographic_tie_two_choices():
    # 构造两个配对数、偏差和相同但配对不同的方案，须取字典序小者。
    # slope=1, intercept=0；内标固定 r0<->m0, r400<->m5。
    # 自由实测 9/11 对参考 10 偏差同为 1，29/31 对参考 30 偏差同为 1。
    r = req(
        [0, 10, 20, 30, 400],
        [0, 9, 11, 29, 31, 400],
        [(0, 0), (4, 5)],
        tolerance_ms=1,
    )
    resp = assign_peaks(r)
    m = mapping_of(resp)
    # 最大配对数 4（含两内标，参考 20 无容差内候选）。
    # r10 配 9 或 11、r30 配 29 或 31，偏差和恒为 2；字典序最小取各对的前者
    assert m == {0: 0, 1: 1, 3: 3, 4: 5}
    assert resp["unpaired_measured_indices"] == [2, 4]
    assert resp["assignments"][1]["deviation_ms"] == {"n": -1, "d": 1}
    assert resp["assignments"][3]["deviation_ms"] == {"n": -1, "d": 1}


def test_free_peaks_in_all_three_regions():
    # 内标 (ref10<->m10)、(ref20<->m20)，两侧三段都有自由峰
    r = req(
        [0, 10, 20, 30],
        [0, 10, 20, 30],
        [(1, 1), (2, 2)],
    )
    resp = assign_peaks(r)
    assert mapping_of(resp) == {0: 0, 1: 1, 2: 2, 3: 3}
    assert resp["unpaired_measured_indices"] == []
    assert resp["assignments"][3]["is_internal_standard"] is False
    assert resp["assignments"][3]["deviation_ms"] == {"n": 0, "d": 1}


def test_count_beats_cost():
    # 容差内的小偏差峰必须配对：配对数优先于偏差和
    r = req(
        [0, 100, 200],
        [0, 60, 200],
        [(0, 0), (2, 2)],
        tolerance_ms=100,
    )
    resp = assign_peaks(r)
    # m=60 校准为 60，对参考 100 偏差 -40（容差内），必须配对
    assert mapping_of(resp) == {0: 0, 1: 1, 2: 2}
    assert resp["assignments"][1]["deviation_ms"] == {"n": -40, "d": 1}


# ---------------------------------------------------------------------------
# 随机暴力枚举一致性
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(60))
def test_matches_brute_force(seed):
    import random

    rng = random.Random(seed)
    n_ref = rng.randint(2, 6)
    n_mea = rng.randint(2, 7)

    # 以一条正斜率直线生成带噪声数据，保证存在合理内标
    slope_true = rng.choice([1, 2, 3, Fraction(1, 2), Fraction(3, 2)])
    base = rng.choice([0, 5, -7])
    ref_t = sorted({round(i * rng.choice([7, 10, 13])) for i in range(n_ref + 4)})
    ref_t = ref_t[:n_ref] if len(ref_t) >= n_ref else list(range(n_ref))
    # 实测时间：取参考的线性反像 + 噪声，再插入杂峰
    true_pairs = rng.sample(
        list(range(len(ref_t))), k=min(2, len(ref_t))
    )
    if len(true_pairs) < 2:  # pragma: no cover
        pytest.skip("not enough refs")
    i1, i2 = sorted(true_pairs)

    def back(rt):
        return Fraction(rt - base, slope_true)

    mea_set = []
    for rt in ref_t:
        x = back(rt)
        if x.denominator == 1:
            noise = rng.choice([0, 0, 0, 1, -1, 2])
            mea_set.append(int(x) + noise)
    # 插入若干杂峰
    for _ in range(rng.randint(0, 2)):
        mea_set.append(rng.randint(-5, max(mea_set + [10]) + 10))
    mea_t = sorted(set(mea_set))
    if len(mea_t) < 2:
        pytest.skip("not enough measured")

    # 在内标附近找实测下标（容差映射）
    def nearest_meas(rt, avoid):
        x = float(back(rt))
        cands = sorted(
            (abs(t - x), j) for j, t in enumerate(mea_t) if j not in avoid
        )
        return cands[0][1] if cands else None

    m1 = nearest_meas(ref_t[i1], set())
    m2 = nearest_meas(ref_t[i2], {m1})
    if m1 is None or m2 is None or m1 == m2:
        pytest.skip("cannot pick anchors")

    ids = list(range(len(ref_t)))
    # 一半概率反序输入内标
    if rng.random() < 0.5:
        istd = [(ids[i2], m2), (ids[i1], m1)]
    else:
        istd = [(ids[i1], m1), (ids[i2], m2)]
    tolerance_ms = rng.choice([0, 1, 2, 5, 1000])

    request = req(ref_t, mea_t, istd, tolerance_ms=tolerance_ms, ids=ids)
    # 仅当输入满足正斜率约束时才测试（否则应 422）
    r1, r2 = i1, i2
    a1, a2 = m1, m2
    if r1 > r2:
        r1, r2 = r2, r1
        a1, a2 = a2, a1
    if (r2 - r1) * (a2 - a1) <= 0:
        with pytest.raises(Exception) as ei:
            assign_peaks(request)
        assert ei.value.status_code == 422
        return

    resp = assign_peaks(request)
    expected, *_ = brute_force(request)
    assert mapping_of(resp) == expected

    # 未配对实测下标正确
    paired = set(expected.values())
    assert resp["unpaired_measured_indices"] == [
        j for j in range(len(mea_t)) if j not in paired
    ]
    # 偏差/校准时间与映射一致
    for ri, a in enumerate(resp["assignments"]):
        if a is None:
            continue
        mi = a["measured_index"]
        s = Fraction(
            resp["calibration"]["slope"]["n"], resp["calibration"]["slope"]["d"]
        )
        c = Fraction(
            resp["calibration"]["intercept_ms"]["n"],
            resp["calibration"]["intercept_ms"]["d"],
        )
        cal = s * mea_t[mi] + c
        assert a["calibrated_time_ms"] == {
            "n": cal.numerator,
            "d": cal.denominator,
        }
        dev = cal - ref_t[ri]
        assert a["deviation_ms"] == {"n": dev.numerator, "d": dev.denominator}
