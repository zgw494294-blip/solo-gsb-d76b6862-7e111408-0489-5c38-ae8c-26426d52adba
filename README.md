# 离线色谱峰归属 API

基于 **两对互异内标** 对实测色谱峰进行正斜率仿射时间校准，并将实测峰与参考峰做
**保序、一对一** 的最优归属。Python 3.12 + FastAPI，全部时间/偏差计算使用
`fractions.Fraction` 精确有理数，返回约分后的 `n/d`。

## 一条命令启动

```bash
docker compose up --build -d
```

- 本地端口：**http://localhost:8000**
- 接口地址：`POST http://localhost:8000/v1/assign`
- 健康检查：`GET http://localhost:8000/health`
- 交互文档：`http://localhost:8000/docs`（Swagger UI）

停止：`docker compose down`

## 请求

`Content-Type: application/json`

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `reference_peaks` | 数组，长度 2～40 | 每项 `{id, time_ms}`；`id` 唯一，`time_ms` 整数且按时间**严格递增** |
| `measured_times_ms` | 整数数组，长度 2～60 | 实测峰时间（下标从 **0** 开始） |
| `is_ref_id_1` / `is_ref_id_2` | 字符串 | 两个互异的内标参考峰 id，必须存在于参考峰中 |
| `is_meas_idx_1` / `is_meas_idx_2` | 整数 | 两个互异内标的实测下标，范围 `[0, len(measured_times_ms)-1]` |
| `tolerance_ms` | 非负整数 | 校准后允许的最大绝对偏差 |

> 两对内标的书写顺序可以互换，服务端会自动归一化；但两对必须同向：
> 沿参考时间递增方向，实测下标也必须递增（不可交叉），且两个实测内标时间严格有序，
> 从而保证仿射斜率严格为正。

```bash
curl -s http://localhost:8000/v1/assign \
  -H 'Content-Type: application/json' \
  -d '{
    "reference_peaks": [
      {"id": "p0", "time_ms": 10},
      {"id": "p1", "time_ms": 22},
      {"id": "p2", "time_ms": 31},
      {"id": "p3", "time_ms": 34},
      {"id": "p4", "time_ms": 40},
      {"id": "p5", "time_ms": 55}
    ],
    "measured_times_ms": [3, 6, 14, 20, 22, 26, 36],
    "is_ref_id_1": "p0", "is_meas_idx_1": 1,
    "is_ref_id_2": "p4", "is_meas_idx_2": 5,
    "tolerance_ms": 0
  }'
```

## 响应

- `assignments`：与参考峰**同序**一一对应，值为配对的实测下标，未配对为 `null`。
- `unpaired_meas_indices`：未配对的实测下标，升序。
- `matches`：每个配对峰一条记录，`calibrated_time_ms` 为实测时间经校准后的值，
  `signed_deviation_ms = 校准值 − 参考值`；两者均为约分分数 `{"n": 分子, "d": 分母}`，
  分母恒为正（负值体现在分子上）。

上例的校准直线为 `校准值 = (3/2) × 实测值 + 1`：

```json
{
  "assignments": [1, 2, 3, 4, 5, 6],
  "unpaired_meas_indices": [0],
  "matches": [
    {"ref_id": "p0", "ref_index": 0, "meas_index": 1,
     "calibrated_time_ms": {"n": 10, "d": 1},
     "signed_deviation_ms": {"n": 0, "d": 1}},
    {"ref_id": "p1", "ref_index": 1, "meas_index": 2,
     "calibrated_time_ms": {"n": 22, "d": 1},
     "signed_deviation_ms": {"n": 0, "d": 1}},
    {"ref_id": "p2", "ref_index": 2, "meas_index": 3,
     "calibrated_time_ms": {"n": 31, "d": 1},
     "signed_deviation_ms": {"n": 0, "d": 1}},
    {"ref_id": "p3", "ref_index": 3, "meas_index": 4,
     "calibrated_time_ms": {"n": 34, "d": 1},
     "signed_deviation_ms": {"n": 0, "d": 1}},
    {"ref_id": "p4", "ref_index": 4, "meas_index": 5,
     "calibrated_time_ms": {"n": 40, "d": 1},
     "signed_deviation_ms": {"n": 0, "d": 1}},
    {"ref_id": "p5", "ref_index": 5, "meas_index": 6,
     "calibrated_time_ms": {"n": 55, "d": 1},
     "signed_deviation_ms": {"n": 0, "d": 1}}
  ]
}
```

非整数示例（参考 `[10,25,40]`、实测 `[6,15,26]`、容差 2）中，实测 15 校准为
`47/2`，对参考 25 的有符号偏差为 `-3/2`：

```json
{"n": 47, "d": 2}
{"n": -3, "d": 2}
```

## 归属规则

1. **内标强制配对**：两对互异内标始终配对，并据此确定仿射变换
   `t_cal = slope·t_meas + intercept`，其中 `slope > 0`。
2. **容差门限**：其余峰仅当 `|t_cal − t_ref| ≤ tolerance_ms` 时允许配对。
3. **一对一 + 保序**：每个参考峰最多配一个实测峰（反之亦然），
   配对链上参考下标与实测下标都严格递增；允许任意一侧的峰连续跳过。
4. **优化目标**（依次比较）：
   1. 配对数最多；
   2. 所有配对的绝对偏差之和最小；
   3. 仍并列时，取 `(参考下标, 实测下标)` 配对序列（按参考下标排序）字典序最小。

实现上，两对内标把问题切为三个互不重叠的独立区间，每个区间用 LCS 风格动态规划求解
（规模 ≤ 40×60，远低于毫秒级），最后合并强制配对。分数全程不转浮点，无精度损失。

## 错误处理（HTTP 422）

以下情况均返回 `422 Unprocessable Entity`，响应体为 `{"detail": "原因"}`：

- 参考峰 `id` 重复，或参考时间不是严格递增；
- 参考峰/实测峰数量超出 2～40 / 2～60 的范围；
- 内标参考 id 不存在，或两个 id 相同；
- 内标实测下标越界、相同；
- 两对内标顺序不一致（实测下标交叉），或两个实测内标时间不严格有序
  （导致斜率不为正或无定义）；
- `tolerance_ms` 为负；
- 类型错误（布尔不会被当作整数、浮点不会被截断为整数）或缺少必填字段。

## 本地开发与测试

不用 Docker 时：

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 测试（另需 pytest、httpx）
pip install pytest httpx
pytest -q
```

测试包含固定边界用例，以及 400 组随机实例与全局暴力枚举（枚举所有保序配对链）对拍，
验证“最大配对数 → 最小绝对偏差和 → 字典序最小”三级目标。

## 目录结构

```
app/
  main.py        # FastAPI 应用、路由、422 异常映射
  models.py      # 严格类型的请求/响应模型
  assignment.py  # 仿射校准 + 区间 DP 归属（纯函数）
tests/           # API 测试与算法对拍测试
Dockerfile
docker-compose.yml
requirements.txt
```
