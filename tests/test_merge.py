"""三方合并服务的端到端测试。"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def post(body):
    return client.post("/merge", json=body)


# ---------- 成功合并 ----------

def test_no_changes():
    base = {"a": 1, "b": {"c": 2}}
    r = post({"base": base, "left": base, "right": base})
    assert r.status_code == 200
    assert r.json()["merged"] == base


def test_one_sided_change_each_side():
    # left 改 a，right 改 b，互不冲突
    r = post({
        "base": {"a": 1, "b": 2, "c": 3},
        "left": {"a": 10, "b": 2, "c": 3},
        "right": {"a": 1, "b": 20, "c": 3},
    })
    assert r.status_code == 200
    assert r.json()["merged"] == {"a": 10, "b": 20, "c": 3}


def test_both_sides_same_change():
    r = post({
        "base": {"a": 1},
        "left": {"a": 9},
        "right": {"a": 9},
    })
    assert r.status_code == 200
    assert r.json()["merged"] == {"a": 9}


def test_both_sides_same_delete():
    r = post({
        "base": {"a": 1, "b": 2},
        "left": {"b": 2},
        "right": {"b": 2},
    })
    assert r.status_code == 200
    assert r.json()["merged"] == {"b": 2}


def test_one_side_add_one_side_edit_sibling():
    r = post({
        "base": {"a": 1},
        "left": {"a": 1, "x": 7},
        "right": {"a": 2},
    })
    assert r.status_code == 200
    assert r.json()["merged"] == {"a": 2, "x": 7}


def test_one_side_delete_other_edits_sibling():
    r = post({
        "base": {"a": 1, "b": 2},
        "left": {"b": 2},            # 删除 a
        "right": {"a": 1, "b": 22},  # 改 b
    })
    assert r.status_code == 200
    assert r.json()["merged"] == {"b": 22}


def test_nested_merge_and_json_pointer_paths():
    r = post({
        "base": {"o": {"a": 1, "b": 2}},
        "left": {"o": {"a": 10, "b": 2}},
        "right": {"o": {"a": 1, "b": 20}},
    })
    assert r.status_code == 200
    assert r.json()["merged"] == {"o": {"a": 10, "b": 20}}


def test_explicit_null_is_a_value_and_merges():
    # 两侧都改成 null => 合并为 null（不是缺失）
    r = post({"base": {"a": 1}, "left": {"a": None}, "right": {"a": None}})
    assert r.status_code == 200
    body = r.json()
    assert body["merged"] == {"a": None}
    assert "a" in body["merged"]


def test_null_vs_value_is_change_not_conflict_when_other_side_idle():
    r = post({"base": {"a": 1}, "left": {"a": None}, "right": {"a": 1}})
    assert r.status_code == 200
    assert r.json()["merged"] == {"a": None}


def test_root_level_scalars():
    r = post({"base": 1, "left": 1, "right": 2})
    assert r.status_code == 200
    assert r.json()["merged"] == 2


def test_arrays_compared_whole_identical():
    arr = [1, 2, 3]
    r = post({"base": {"a": arr}, "left": {"a": arr}, "right": {"a": arr}})
    assert r.status_code == 200
    assert r.json()["merged"] == {"a": [1, 2, 3]}


# ---------- 冲突 ----------

def test_conflict_same_path_different_values():
    r = post({
        "base": {"a": 1},
        "left": {"a": 10},
        "right": {"a": 20},
    })
    assert r.status_code == 409
    conflicts = r.json()["conflicts"]
    assert conflicts == [{"path": "/a", "base": 1, "left": 10, "right": 20}]


def test_conflict_delete_vs_modify_descendant():
    # left 删除整个 obj，right 修改 obj 的后代 x
    r = post({
        "base": {"obj": {"x": 1}},
        "left": {},
        "right": {"obj": {"x": 2}},
    })
    assert r.status_code == 409
    conflict = r.json()["conflicts"][0]
    assert conflict["path"] == "/obj"
    assert conflict["base"] == {"x": 1}
    assert "left" not in conflict          # 缺失 => 省略字段
    assert conflict["right"] == {"x": 2}


def test_conflict_delete_vs_scalar_change():
    r = post({
        "base": {"a": 5},
        "left": {},
        "right": {"a": 6},
    })
    assert r.status_code == 409
    conflict = r.json()["conflicts"][0]
    assert conflict == {"path": "/a", "base": 5, "right": 6}


def test_conflict_null_vs_other_value_distinguishes_missing():
    # base 中键缺失（无 base 字段）；left 置 null，right 置 1
    r = post({
        "base": {},
        "left": {"a": None},
        "right": {"a": 1},
    })
    assert r.status_code == 409
    conflict = r.json()["conflicts"][0]
    assert conflict["path"] == "/a"
    assert "base" not in conflict           # base 缺失
    assert conflict["left"] is None         # left 为显式 null
    assert conflict["right"] == 1


def test_conflict_root_scalar():
    r = post({"base": 1, "left": 2, "right": 3})
    assert r.status_code == 409
    assert r.json()["conflicts"] == [{"path": "", "base": 1, "left": 2, "right": 3}]


def test_conflict_bool_vs_int_not_equal():
    # true 与 1 按 JSON 类型不同
    r = post({"base": {"a": 0}, "left": {"a": True}, "right": {"a": 1}})
    assert r.status_code == 409


def test_conflicts_sorted_by_json_pointer():
    r = post({
        "base": {"z": 1, "a": 1, "m": 1},
        "left": {"z": 2, "a": 2, "m": 2},
        "right": {"z": 3, "a": 3, "m": 3},
    })
    assert r.status_code == 409
    paths = [c["path"] for c in r.json()["conflicts"]]
    assert paths == ["/a", "/m", "/z"]


def test_multiple_conflicts_listed_with_unaffected_siblings():
    # /a 真冲突；/b 仅 right 改；/c 两侧同改 —— 后两者不应出现在冲突里
    r = post({
        "base": {"a": 1, "b": 2, "c": 3},
        "left": {"a": 10, "b": 2, "c": 30},
        "right": {"a": 20, "b": 22, "c": 30},
    })
    assert r.status_code == 409
    paths = [c["path"] for c in r.json()["conflicts"]]
    assert paths == ["/a"]


def test_array_whole_compare_conflict():
    # 两侧对同一数组做了不同改动 => 视为叶子冲突，不逐元素合并
    r = post({
        "base": {"a": [1, 2]},
        "left": {"a": [1, 2, 3]},
        "right": {"a": [1, 9]},
    })
    assert r.status_code == 409
    conflict = r.json()["conflicts"][0]
    assert conflict == {"path": "/a", "base": [1, 2], "left": [1, 2, 3], "right": [1, 9]}


def test_array_change_one_side_merges():
    r = post({
        "base": {"a": [1, 2]},
        "left": {"a": [9]},
        "right": {"a": [1, 2]},
    })
    assert r.status_code == 200
    assert r.json()["merged"] == {"a": [9]}


def test_escaped_pointer_for_special_keys():
    r = post({
        "base": {"a/b": 1, "~x": 1},
        "left": {"a/b": 2, "~x": 2},
        "right": {"a/b": 3, "~x": 3},
    })
    assert r.status_code == 409
    paths = [c["path"] for c in r.json()["conflicts"]]
    assert paths == ["/a~1b", "/~0x"]


# ---------- 请求校验 ----------

def test_missing_request_field_is_422():
    r = client.post("/merge", json={"base": {}, "left": {}})
    assert r.status_code == 422


def test_explicit_null_fields_are_accepted():
    r = post({"base": None, "left": None, "right": None})
    assert r.status_code == 200
    assert r.json()["merged"] is None


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
