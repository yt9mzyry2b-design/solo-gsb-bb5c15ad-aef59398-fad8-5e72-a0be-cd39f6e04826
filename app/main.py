"""JSON 三方合并（three-way merge）服务。"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# 表示键 / 节点缺失的哨兵；显式的 JSON null 用 Python 的 None 表示，
# 二者在冲突报告中必须区分开。
_MISSING = object()


class MergeRequest(BaseModel):
    # 必填：请求体中缺字段会得到 422；显式传 null 才表示 JSON null
    base: Any
    left: Any
    right: Any


def _same(a: Any, b: Any) -> bool:
    """按 JSON 语义比较两个值。

    缺失哨兵彼此相等；bool 不与 int 混淆；数字按数值比较
    （1 == 1.0）；数组作为整体比较，不逐元素合并。
    """
    if a is _MISSING or b is _MISSING:
        return a is b
    if a is None or b is None:
        return a is None and b is None
    # bool 是 int 的子类，需先排除
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, str) or isinstance(b, str):
        return isinstance(a, str) and isinstance(b, str) and a == b
    if isinstance(a, list) or isinstance(b, list):
        return (
            isinstance(a, list)
            and isinstance(b, list)
            and len(a) == len(b)
            and all(_same(x, y) for x, y in zip(a, b))
        )
    if isinstance(a, dict) or isinstance(b, dict):
        if not (isinstance(a, dict) and isinstance(b, dict)):
            return False
        if set(a.keys()) != set(b.keys()):
            return False
        return all(_same(a[k], b[k]) for k in a)
    return a == b


def _escape(token: str) -> str:
    """转义 JSON Pointer 的单个 reference token。"""
    return token.replace("~", "~0").replace("/", "~1")


def _three_way(path: str, base: Any, left: Any, right: Any,
               conflicts: list[dict]) -> Any:
    """递归执行三方合并，返回合并结果；无法合并时把冲突追加到 conflicts。

    返回 _MISSING 表示该节点在合并结果中被删除（或从未存在）。
    """
    if isinstance(base, dict) and isinstance(left, dict) and isinstance(right, dict):
        merged: dict = {}
        for key in sorted(set(base) | set(left) | set(right)):
            child_path = f"{path}/{_escape(key)}"
            sub = _three_way(
                child_path,
                base.get(key, _MISSING),
                left.get(key, _MISSING),
                right.get(key, _MISSING),
                conflicts,
            )
            if sub is not _MISSING:
                merged[key] = sub
        return merged

    # 叶子节点（数组同样落到这里：整体比较，不逐元素合并）
    if _same(left, right):
        # 两侧相同：都未改、都改成同值、同时删除
        return left
    if _same(left, base):
        # 仅右侧变化（含右侧删除）
        return right
    if _same(right, base):
        # 仅左侧变化（含左侧删除）
        return left

    # 同路径异值（含一侧删除、另一侧改动该节点或其后代的情形）。
    # 缺失的一侧直接省略对应字段，以此与显式 null 区分。
    conflict: dict[str, Any] = {"path": path}
    if base is not _MISSING:
        conflict["base"] = base
    if left is not _MISSING:
        conflict["left"] = left
    if right is not _MISSING:
        conflict["right"] = right
    conflicts.append(conflict)
    return _MISSING


def merge_documents(base: Any, left: Any, right: Any) -> tuple[Any, list[dict]]:
    """合并三份 JSON 文档，返回 (merged, conflicts)，冲突按 JSON Pointer 排序。"""
    conflicts: list[dict] = []
    merged = _three_way("", base, left, right, conflicts)
    conflicts.sort(key=lambda c: c["path"])
    return merged, conflicts


app = FastAPI(
    title="JSON 三方合并服务",
    description="基于 base 的 left / right 三方合并；冲突时返回 409 及 JSON Pointer 路径。",
    version="1.0.0",
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/merge")
def merge(req: MergeRequest):
    merged, conflicts = merge_documents(req.base, req.left, req.right)
    if conflicts:
        return JSONResponse(status_code=409, content={"conflicts": conflicts})
    return {"merged": merged}
