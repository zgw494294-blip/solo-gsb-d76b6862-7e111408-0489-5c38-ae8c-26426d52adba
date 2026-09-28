# 离线色谱峰归属 API（Python / FastAPI）

用两对内标确定一个**正斜率仿射变换**，把实测峰时间校准到参考时间域，
再对其余峰做**保持顺序的一对一最优配对**。全程使用精确分数运算，
所有时间与偏差均以约分后的 `n/d` 返回，无浮点误差。

## 一条命令启动

```bash
docker compose up --build
```

服务监听在本机 **http://localhost:8000**：

- 接口：`POST http://localhost:8000/v1/assign`
- 健康检查：`GET http://localhost:8000/health`
- 交互式文档（Swagger UI）：http://localhost:8000/docs

停止：`docker compose down`（后台运行用 `docker compose up -d --build`）。

## 请求

`POST /v1/assign`，`Content-Type: application/json`

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `reference_peaks` | 数组，长度 **2～40** | 元素含唯一 `id`（整数或字符串）、整数 `time_ms`；时间必须**严格递增** |
| `measured_peaks` | 数组，长度 **2～60** | 元素含整数 `time_ms`；时间必须**严格递增** |
| `internal_standards` | 数组，**恰好 2 对** | 每对含 `reference_id`（必须存在于参考峰）与 `measured_index`（从 **0** 开始） |
| `tolerance_ms` | 非负整数 | 校准后允许的最大绝对偏差 |

内标规则：

- 两对的参考 id 必须互异，实测下标必须互异，下标必须在有效范围内；
- 两对内标在参考侧与实测侧的**顺序必须一致**（等价于仿射变换斜率严格为正）；
  两对在 JSON 中的书写先后不限，服务内部会归一化。

任一约束不满足均返回 **HTTP 422**。

### 请求示例

```json
{
  "reference_peaks": [
    {"id": "A", "time_ms": 0},
    {"id": "B", "time_ms": 10},
    {"id": "C", "time_ms": 20}
  ],
  "measured_peaks": [
    {"time_ms": 0},
    {"time_ms": 5},
    {"time_ms": 10},
    {"time_ms": 20}
  ],
  "internal_standards": [
    {"reference_id": "A", "measured_index": 0},
    {"reference_id": "C", "measured_index": 3}
  ],
  "tolerance_ms": 0
}
```

## 响应

- `assignments`：与 `reference_peaks` **等长且同序**；未配对的参考峰对应位置为 `null`。
  每个配对包含：
  - `measured_index`：实测峰下标（从 0 开始）；
  - `calibrated_time_ms`：该实测峰校准后的时间 `{n, d}`；
  - `deviation_ms`：**有符号偏差 = 校准值 − 参考值** `{n, d}`；
  - `is_internal_standard`：是否为强制配对的内标峰。
- `unpaired_measured_indices`：未配对的实测峰下标（升序）。
- `calibration`：校准参数（分数）`slope`、`intercept_ms` 及归一化后的两对内标位置。

分数对象 `{"n": 整数, "d": 正整数}` 已约分；有符号量的符号在 `n` 上，零为 `{"n":0,"d":1}`。

### 响应示例

```json
{
  "assignments": [
    {"measured_index": 0, "calibrated_time_ms": {"n": 0, "d": 1},
     "deviation_ms": {"n": 0, "d": 1}, "is_internal_standard": true},
    {"measured_index": 2, "calibrated_time_ms": {"n": 10, "d": 1},
     "deviation_ms": {"n": 0, "d": 1}, "is_internal_standard": false},
    {"measured_index": 3, "calibrated_time_ms": {"n": 20, "d": 1},
     "deviation_ms": {"n": 0, "d": 1}, "is_internal_standard": true}
  ],
  "unpaired_measured_indices": [1],
  "calibration": {
    "slope": {"n": 1, "d": 1},
    "intercept_ms": {"n": 0, "d": 1},
    "internal_standards": [
      {"reference_index": 0, "measured_index": 0},
      {"reference_index": 2, "measured_index": 3}
    ]
  },
  "tolerance_ms": 0
}
```

curl 试一下：

```bash
curl -s -X POST http://localhost:8000/v1/assign \
  -H 'Content-Type: application/json' \
  -d @- <<'JSON'
{
  "reference_peaks": [{"id":"A","time_ms":0},{"id":"B","time_ms":10},{"id":"C","time_ms":20}],
  "measured_peaks": [{"time_ms":0},{"time_ms":5},{"time_ms":10},{"time_ms":20}],
  "internal_standards": [{"reference_id":"A","measured_index":0},{"reference_id":"C","measured_index":3}],
  "tolerance_ms": 0
}
JSON
```

## 算法说明

设两对内标的参考时间为 `r₁ < r₂`、实测时间为 `m₁ < m₂`：

```
slope     = (r₂ - r₁) / (m₂ - m₁)        # 严格正分数
intercept = r₁ - slope · m₁
calibrated(m) = slope · m + intercept
deviation(r,m) = calibrated(m) - r       # 有符号
```

- 两对内标**强制配对**（偏差恒为 0），把参考序列与实测序列各自切成三段；
- 段内用 LCS 式动态规划选择其余配对：仅当 `|deviation| ≤ tolerance_ms` 才允许配对，
  且天然保证**一对一、保持顺序**（两侧下标均严格递增）；
- 优化目标依次为：
  1. **配对数最多**；
  2. **绝对偏差之和最小**；
  3. 仍并列时，取配对序列 `((参考下标, 实测下标), …)` 的**字典序最小**者。
- 容差比较使用整数（`|偏差分子| ≤ tolerance · 分母`），不引入浮点。

三段彼此独立，最优解拼接后仍为全局最优（顺序保持保证不存在跨段配对）。

## 本地开发（不使用 Docker）

需要 Python 3.11+：

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000
pytest -q
```

测试包含解析用例、各类 422 非法输入，以及 60 组随机小规模用例与
**独立暴力枚举参考实现**的结果一致性校验（配对数 / 偏差和 / 字典序三级目标、
未配对下标、校准值与偏差分数全部核对）。

## 项目结构

```
app/main.py          # FastAPI 应用、输入模型、校验与 DP 配对核心
tests/test_assign.py # 单元测试 + 随机暴力枚举一致性
Dockerfile           # 容器镜像（python:3.12-slim + uvicorn）
compose.yaml         # 单命令编排，映射本机 8000 端口
requirements.txt     # 运行依赖（版本锁定）
```
