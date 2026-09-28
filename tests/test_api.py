"""HTTP 层测试：FastAPI 路由、状态码（含 422）。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

BASE_BODY = {
    "reference_peaks": [
        {"id": "A", "time_ms": 0},
        {"id": "B", "time_ms": 10},
        {"id": "C", "time_ms": 20},
    ],
    "measured_peaks": [
        {"time_ms": 0},
        {"time_ms": 5},
        {"time_ms": 10},
        {"time_ms": 20},
    ],
    "internal_standards": [
        {"reference_id": "A", "measured_index": 0},
        {"reference_id": "C", "measured_index": 3},
    ],
    "tolerance_ms": 0,
}


def test_health_and_root():
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/").status_code == 200


def test_assign_ok():
    r = client.post("/v1/assign", json=BASE_BODY)
    assert r.status_code == 200
    data = r.json()
    assert [a["measured_index"] if a else None for a in data["assignments"]] == [
        0,
        2,
        3,
    ]
    assert data["unpaired_measured_indices"] == [1]
    # 分数始终约分、分母为正
    for a in data["assignments"]:
        assert a["calibrated_time_ms"]["d"] >= 1
        assert a["deviation_ms"]["d"] >= 1


def test_schema_and_semantic_422s():
    def post(mut):
        body = {
            "reference_peaks": [dict(p) for p in BASE_BODY["reference_peaks"]],
            "measured_peaks": [dict(p) for p in BASE_BODY["measured_peaks"]],
            "internal_standards": [
                dict(p) for p in BASE_BODY["internal_standards"]
            ],
            "tolerance_ms": BASE_BODY["tolerance_ms"],
        }
        mut(body)
        return client.post("/v1/assign", json=body)

    # schema 层：容差为负 / 峰数不足 / 类型错误
    assert post(lambda b: b.update(tolerance_ms=-1)).status_code == 422
    assert post(
        lambda b: b.update(reference_peaks=[{"id": "A", "time_ms": 0}])
    ).status_code == 422
    assert post(lambda b: b.update(tolerance_ms="x")).status_code == 422

    # 语义层
    assert post(
        lambda b: b["reference_peaks"].__setitem__(1, {"id": "A", "time_ms": 10})
    ).status_code == 422  # 重复 id
    assert post(
        lambda b: b["reference_peaks"].__setitem__(2, {"id": "C", "time_ms": 10})
    ).status_code == 422  # 参考时间非严格递增
    assert post(
        lambda b: b["measured_peaks"].__setitem__(2, {"time_ms": 5})
    ).status_code == 422  # 实测时间非严格递增
    assert post(
        lambda b: b["internal_standards"].__setitem__(
            0, {"reference_id": "A", "measured_index": 3}
        )
    ).status_code == 422  # 顺序不一致（负斜率）
    assert post(
        lambda b: b["internal_standards"].__setitem__(
            0, {"reference_id": "ZZZ", "measured_index": 0}
        )
    ).status_code == 422  # 未知参考 id
    assert post(
        lambda b: b["internal_standards"].__setitem__(
            1, {"reference_id": "C", "measured_index": 99}
        )
    ).status_code == 422  # 实测下标越界
    assert post(
        lambda b: b["internal_standards"].__setitem__(
            1, {"reference_id": "C", "measured_index": 0}
        )
    ).status_code == 422  # 实测下标互异
