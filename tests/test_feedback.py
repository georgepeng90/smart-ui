"""验证偏差记录和处理历史关联到实际对照使用的版本。"""

from copy import deepcopy
from datetime import datetime

import pytest

from smartui_mcp.feedback import add_feedback, update_feedback
from smartui_mcp.projects import ProjectError


@pytest.fixture
def state():
    return {
        "comparisons": {
            "original": {
                "id": "original", "component_id": "sample", "run_index": 1,
                "reference": {"id": "reference", "sha256": "a" * 64, "region_source_xywh": [0, 5, 30, 20]},
                "run": {"run_id": "draw-1", "status": "verified", "style_version": 1, "spec_sha256": "b" * 64,
                        "program_id": "drawing", "program_version": 1, "program_sha256": "c" * 64},
                "node_keys": ["outline"], "rule_ids": ["outline-weight"],
            },
        },
        "style": {"version": 2, "rules": [{"id": "current-rule"}], "user_confirmation": None},
        "components": {"sample": {"needs_review": True, "last_run": {"status": "verified"}}},
    }


def add_sample_feedback(state, **overrides):
    arguments = {"feedback_id": "outline-too-thick", "comparison_id": "original", "observation": "描边偏厚",
                 "suggested_change": "减小描边宽度", "target_nodes": ["outline"], "rule_ids": ["outline-weight"],
                 "source": "model"}
    add_feedback(state, **(arguments | overrides))
    return state["feedback"][arguments["feedback_id"]]


def add_followup(state, run_status="drawn"):
    followup = deepcopy(state["comparisons"]["original"])
    followup.update(id="followup", run_index=2, rule_ids=["current-rule"], node_keys=["new-outline"])
    followup["run"].update(run_id="draw-2", status=run_status, style_version=2, program_version=2)
    state["comparisons"]["followup"] = followup
    return followup


@pytest.mark.parametrize("run_status", ["drawn", "verified"])
def test_feedback_preserves_original_versions_and_processing_history(state, run_status):
    add_followup(state, run_status)
    before = deepcopy(state)
    targets = ["outline"]
    rules = ["outline-weight"]
    record = add_sample_feedback(state, target_nodes=targets, rule_ids=rules)
    targets.clear()
    rules.clear()
    original = deepcopy(record)
    assert original == {
        "id": "outline-too-thick", "comparison_id": "original", "component_id": "sample",
        "observation": "描边偏厚", "suggested_change": "减小描边宽度", "target_nodes": ["outline"],
        "rule_ids": ["outline-weight"], "source": "model", "status": "open",
        "created_at": original["created_at"], "history": [],
    }
    changes = [
        ("resolved", "新产物描边符合参考", "followup", "model"),
        ("open", "复查发现左边仍偏厚", "followup", "user"),
        ("dismissed", "左边厚度属于已知的设计差异", "original", "user"),
    ]
    for status, comment, comparison_id, source in changes:
        update_feedback(state, original["id"], status, comment, comparison_id, source)
        assert record["status"] == status
    assert record == original | {"status": "dismissed", "history": record["history"]}
    assert [(item["status"], item["comment"], item["comparison_id"], item["source"])
            for item in record["history"]] == changes
    assert all(datetime.fromisoformat(item["at"]).tzinfo for item in record["history"])
    assert {field: state[field] for field in before} == before


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


@pytest.mark.parametrize(("path", "value", "message"), [
    (("component_id",), "other", "同一组件"),
    (("reference", "id"), "other", "同一参考及源图区域"),
    (("reference", "sha256"), "d" * 64, "同一参考及源图区域"),
    (("reference", "region_source_xywh"), [0, 0, 30, 20], "同一参考及源图区域"),
    (("run", "run_id"), "draw-1", "原始运行之后成功绘制"),
    (("run", "status"), "failed", "原始运行之后成功绘制"),
    (("run_index",), 0, "原始运行之后成功绘制"),
    (("run_index",), 1, "原始运行之后成功绘制"),
])
def test_resolving_requires_a_different_successful_run_with_the_same_reference(state, path, value, message):
    followup = add_followup(state)
    target = followup[path[0]] if len(path) == 2 else followup
    target[path[-1]] = value
    record = add_sample_feedback(state)
    before = deepcopy(state)
    with pytest.raises(ProjectError, match=message):
        update_feedback(state, record["id"], "resolved", "已经复查", "followup", "model")
    assert state == before


def test_dismissing_requires_a_reason(state):
    record = add_sample_feedback(state)
    before = deepcopy(state)
    with pytest.raises(ProjectError, match="comment.*非空"):
        update_feedback(state, record["id"], "dismissed", " ", "original", "model")
    assert state == before
