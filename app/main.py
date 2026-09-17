"""JSON three-way merge service.

POST /merge accepts {"base": ..., "left": ..., "right": ...} and merges the
changes that left and right each made relative to base:

* a change made on only one side is taken;
* identical changes on both sides are taken;
* objects are merged key by key, recursively;
* arrays are compared as a whole (never merged element-wise);
* conflicting changes (same path, different values, or one side deleting a
  subtree the other side modified) produce a 409 response listing every
  conflict, sorted by JSON Pointer (RFC 6901).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

app = FastAPI(title="JSON Three-Way Merge", version="1.0.0")


class _Missing:
    """Sentinel for an absent key, kept distinct from a JSON null value."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "MISSING"


MISSING = _Missing()


def json_equal(a: Any, b: Any) -> bool:
    """Structural JSON equality.

    Unlike plain ``==`` this keeps the JSON types distinct that Python folds
    together (``True`` is not ``1``), while still treating ``1`` and ``1.0``
    as the same JSON number.
    """
    if a is MISSING or b is MISSING:
        return a is b
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(json_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(json_equal(x, y) for x, y in zip(a, b))
    return a == b


def escape_pointer(token: str) -> str:
    """Escape one reference token as per RFC 6901."""
    return token.replace("~", "~0").replace("/", "~1")


def merge3(base: Any, left: Any, right: Any, path: str, conflicts: list) -> Any:
    """Merge left and right against base, collecting conflicts.

    Returns the merged value (or MISSING for a deleted/absent key). Every
    conflict found is appended to ``conflicts`` as a dict with the JSON
    Pointer ``path`` and the ``base``/``left``/``right`` values at that path.
    """
    # Both sides agree (includes: unchanged on both, deleted on both).
    if json_equal(left, right):
        return left
    # Only the right side changed.
    if json_equal(left, base):
        return right
    # Only the left side changed.
    if json_equal(right, base):
        return left
    # Both sides changed in different ways. Objects merge key by key; a key
    # absent from base but present as an object on both sides is merged
    # against an empty object. Anything else (scalars, arrays, type changes,
    # delete-vs-modify) is a conflict.
    if isinstance(left, dict) and isinstance(right, dict):
        if isinstance(base, dict):
            base_dict = base
        elif base is MISSING:
            base_dict = {}
        else:
            base_dict = None
        if base_dict is not None:
            merged = {}
            keys = sorted(base_dict.keys() | left.keys() | right.keys())
            for key in keys:
                value = merge3(
                    base_dict.get(key, MISSING),
                    left.get(key, MISSING),
                    right.get(key, MISSING),
                    f"{path}/{escape_pointer(key)}",
                    conflicts,
                )
                if value is not MISSING:
                    merged[key] = value
            return merged
    conflicts.append({"path": path, "base": base, "left": left, "right": right})
    return MISSING


def side_repr(value: Any) -> dict:
    """Represent one side of a conflict, distinguishing missing from null."""
    if value is MISSING:
        return {"present": False, "value": None}
    return {"present": True, "value": value}


class MergeRequest(BaseModel):
    base: Any = Field(...)
    left: Any = Field(...)
    right: Any = Field(...)


@app.post("/merge")
def merge_endpoint(request: MergeRequest):
    conflicts: list = []
    merged = merge3(request.base, request.left, request.right, "", conflicts)
    if conflicts:
        conflicts.sort(key=lambda c: c["path"])
        return JSONResponse(
            status_code=409,
            content={
                "detail": "merge conflict",
                "conflicts": [
                    {
                        "path": c["path"],
                        "base": side_repr(c["base"]),
                        "left": side_repr(c["left"]),
                        "right": side_repr(c["right"]),
                    }
                    for c in conflicts
                ],
            },
        )
    return {"merged": merged}


@app.get("/health")
def health():
    return {"status": "ok"}
