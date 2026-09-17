# JSON 三方合并服务（FastAPI）

基于 `base` 基线，对 `left`、`right` 两份修改进行 **三方合并（three-way merge）** 的 HTTP 服务。

## 一条命令启动

依赖（FastAPI、Uvicorn、Pydantic）会在镜像构建时自动安装，无需本地准备 Python 环境：

```bash
docker compose up --build
```

启动后服务监听 **http://localhost:8000**

- 健康检查：http://localhost:8000/health
- 交互式文档（Swagger UI）：http://localhost:8000/docs

> 也可用 `docker-compose up --build`（旧版 Compose）。

## 接口

### `POST /merge`

请求体：

```json
{
  "base":  { "a": 1, "b": 2, "c": { "x": 1 } },
  "left":  { "a": 10, "b": 2, "c": { "x": 1 } },
  "right": { "a": 1, "b": 20, "c": { "x": 1 } }
}
```

`base` / `left` / `right` 均为必填字段，可以是任意 JSON 值（对象、数组、标量、`null`）。
请求体里**缺字段**会返回 `422`，这与显式传 `null` 是两回事。

#### 合并规则

- **两侧都未改** → 取基线值；
- **仅一侧变化**（修改、置为 `null`、新增键、删除键）→ 采用该侧结果；
- **两侧做了相同的变化**（含同时删除、同时改成同值）→ 合并为该结果；
- **同一路径两侧改成不同值** → `409` 冲突；
- **一侧删除某节点，另一侧修改该节点或其后代** → `409` 冲突（删除与修改不兼容）；
- **数组按整体比较**，不逐元素合并：两边数组只有完全相同或仅一侧变化时才合并，否则冲突；
- 兄弟键互不影响：不同子路径的单侧变化会分别合并；存在任意冲突时整体返回 `409`，并列出全部冲突。

#### 成功响应 `200`

```json
{ "merged": { "a": 10, "b": 20, "c": { "x": 1 } } }
```

#### 冲突响应 `409`

冲突按 **JSON Pointer（RFC 6901）路径字典序** 升序排列，每条包含 `path` 及分歧的三方值：

```json
{
  "conflicts": [
    { "path": "/a", "base": 1, "left": 10, "right": 20 },
    { "path": "/c/x", "base": 1, "left": null }
  ]
}
```

**键缺失与显式 `null` 的区分**：某一方在该路径上没有该键（或该节点已被删除）时，对应字段直接**省略**；
若该方的值是 JSON `null`，字段存在且值为 `null`。上例中 `/c/x` 没有 `right` 字段，表示 right 侧该键缺失。

根节点本身冲突时路径为 `""`。键名中的 `~` 转义为 `~0`、`/` 转义为 `~1`。

## 示例

```bash
curl -s http://localhost:8000/merge \
  -H 'Content-Type: application/json' \
  -d '{"base":{"a":1},"left":{"a":10},"right":{"a":1}}'
# {"merged":{"a":10}}

curl -i http://localhost:8000/merge \
  -H 'Content-Type: application/json' \
  -d '{"base":{"a":1},"left":{"a":10},"right":{"a":20}}'
# HTTP/1.1 409 ...
# {"conflicts":[{"path":"/a","base":1,"left":10,"right":20}]}
```

## 本地开发（不使用 Docker）

需要 Python 3.11+：

```bash
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000   # 启动服务，访问 http://localhost:8000
pytest -v                                   # 运行测试
```

## 项目结构

```
app/main.py            # FastAPI 应用与三方合并核心逻辑
tests/test_merge.py    # 端到端测试（成功合并 / 冲突 / 路径转义 / 422 校验）
Dockerfile             # python:3.12-slim，构建时自动安装依赖
docker-compose.yml     # 映射 localhost:8000:8000，含健康检查
requirements.txt       # 运行依赖（固定版本）
requirements-dev.txt   # 测试依赖
```
