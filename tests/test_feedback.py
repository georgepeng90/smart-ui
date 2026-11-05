"""验证偏差记录和当前处理结论关联到实际检查的产物。"""

from copy import deepcopy

import pytest

from smartui_mcp.feedback import add_feedback, update_feedback
from smartui_mcp.projects import ProjectError


@pytest.fixture
def state():
    return {
        "comparisons": {
            "original": {
                "id": "original", "component_id": "sample",
                "reference": {"id": "reference", "sha256": "a" * 64, "region_source_xywh": [0, 5, 30, 20]},
                "artifact": {"run_id": "draw-1", "sha256": "b" * 64},
                "node_keys": ["outline"], "rule_ids": ["outline-weight"],
            },
        },
    }


def add_sample_feedback(state, **overrides):
    arguments = {"feedback_id": "outline-too-thick", "comparison_id": "original", "observation": "描边偏厚",
                 "suggested_change": "减小描边宽度", "target_nodes": ["outline"], "rule_ids": ["outline-weight"],
                 "source": "model"}
    add_feedback(state, **(arguments | overrides))
    return state["feedback"][arguments["feedback_id"]]


def test_feedback_saves_only_the_current_review(state):
    targets = ["outline"]
    rules = ["outline-weight"]
    record = add_sample_feedback(state, target_nodes=targets, rule_ids=rules)
    targets.clear()
    rules.clear()
    assert record == {
        "id": "outline-too-thick", "comparison_id": "original",
        "observation": "描边偏厚", "suggested_change": "减小描边宽度", "target_nodes": ["outline"],
        "rule_ids": ["outline-weight"], "source": "model", "status": "open",
        "reviewed_artifact_sha256": "b" * 64,
        "reference_sha256": "a" * 64, "reference_region_source_xywh": [0, 5, 30, 20],
    }
    original = deepcopy(record)
    state["comparisons"]["original"]["artifact"].update(run_id="draw-2", sha256="c" * 64)
    comparisons = deepcopy(state["comparisons"])
    update_feedback(state, record["id"], "resolved", "新产物描边符合参考", "original", "model")
    assert record == original | {"status": "resolved", "comment": "新产物描边符合参考",
                                 "reviewed_artifact_sha256": "c" * 64}
    update_feedback(state, record["id"], "open", "复查发现左边仍偏厚", "original", "user")
    assert record["status"] == "open"
    update_feedback(state, record["id"], "dismissed", "左边厚度属于设计差异", "original", "user")
    assert record == original | {"status": "dismissed", "source": "user", "comment": "左边厚度属于设计差异",
                                 "reviewed_artifact_sha256": "c" * 64}
    assert state["comparisons"] == comparisons


@pytest.mark.parametrize(("overrides", "message"), [
    ({"comparison_id": "missing"}, "对照记录不存在"),
    ({"target_nodes": ["missing"]}, "target_nodes.*missing"),
    ({"rule_ids": ["current-rule"]}, "rule_ids.*current-rule"),
    ({"target_nodes": "outline"}, "target_nodes.*列表"),
    ({"rule_ids": [None]}, "rule_ids.*字符串"),
    ({"observation": " "}, "observation.*非空"),
    ({"suggested_change": ""}, "suggested_change.*非空"),
    ({"source": "system"}, "source"),
])
def test_add_rejects_invalid_input_without_creating_records(state, overrides, message):
    before = deepcopy(state)
    with pytest.raises(ProjectError, match=message):
        add_sample_feedback(state, **overrides)
    assert state == before


def test_duplicate_feedback_keeps_original_record(state):
    add_sample_feedback(state, target_nodes=[], rule_ids=[])
    before = deepcopy(state)
    with pytest.raises(ProjectError, match="反馈标识已存在"):
        add_sample_feedback(state, observation="另一条观察")
    assert state == before


@pytest.mark.parametrize("field", ["feedback", "comparisons"])
def test_present_collections_must_be_objects(state, field):
    state[field] = None
    with pytest.raises(ProjectError, match=f"{field}.*JSON 对象"):
        add_sample_feedback(state)


def test_missing_comparison_collection_means_no_records():
    state = {}
    with pytest.raises(ProjectError, match="对照记录不存在"):
        add_sample_feedback(state)
    assert state == {}


def test_resolving_requires_changed_artifact_bytes_for_the_current_comparison(state):
    record = add_sample_feedback(state)
    state["comparisons"]["original"]["artifact"]["run_id"] = "draw-2"
    state["comparisons"]["other"] = {"id": "other", "artifact": {"run_id": "draw-3", "sha256": "c" * 64}}
    before = deepcopy(state)
    with pytest.raises(ProjectError, match="预览图 SHA-256 已变化"):
        update_feedback(state, record["id"], "resolved", "已经复查", "original", "model")
    with pytest.raises(ProjectError, match="原组件和参考的当前对照"):
        update_feedback(state, record["id"], "resolved", "已经复查", "other", "model")
    assert state == before


def test_resolving_requires_the_same_reference_crop(state):
    record = add_sample_feedback(state)
    comparison = state["comparisons"]["original"]
    comparison["artifact"]["sha256"] = "c" * 64
    comparison["reference"]["region_source_xywh"][1] = 0
    before = deepcopy(state)
    with pytest.raises(ProjectError, match="相同的参考图和源图区域"):
        update_feedback(state, record["id"], "resolved", "已经复查", "original", "model")
    assert state == before


def test_dismissing_requires_a_reason(state):
    record = add_sample_feedback(state)
    before = deepcopy(state)
    with pytest.raises(ProjectError, match="comment.*非空"):
        update_feedback(state, record["id"], "dismissed", " ", "original", "model")
    assert state == before
