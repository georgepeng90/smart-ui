"""保存对照发现的偏差和当前处理结论。"""

from .projects import ProjectError, _identifier, _text


def _collection(state: dict, field: str) -> dict:
    records = state.get(field, {})
    if not isinstance(records, dict):
        raise ProjectError(f"project.json 的 {field} 必须是 JSON 对象")
    return records


def _comparison(comparisons: dict, comparison_id: str) -> dict:
    _identifier(comparison_id, "comparison_id")
    if comparison_id not in comparisons:
        raise ProjectError(f"对照记录不存在：{comparison_id}")
    return comparisons[comparison_id]


def _validate_source(source: str) -> None:
    if source not in ("model", "user"):
        raise ProjectError("source 必须是 model 或 user")


def _validate_targets(values: list[str], field: str, available: list[str], comparison_id: str) -> None:
    if not isinstance(values, list):
        raise ProjectError(f"{field} 必须是字符串列表")
    for value in values:
        _text(value, field)
        if value not in available:
            raise ProjectError(f"对照 {comparison_id} 的 {field} 中不存在：{value}")


def add_feedback(state: dict, feedback_id: str, comparison_id: str, observation: str,
                 suggested_change: str, target_nodes: list[str], rule_ids: list[str], source: str) -> None:
    _identifier(feedback_id, "feedback_id")
    _text(observation, "observation")
    _text(suggested_change, "suggested_change")
    _validate_source(source)
    feedback = _collection(state, "feedback")
    if feedback_id in feedback:
        raise ProjectError(f"反馈标识已存在：{feedback_id}")
    comparison = _comparison(_collection(state, "comparisons"), comparison_id)
    _validate_targets(target_nodes, "target_nodes", comparison["node_keys"], comparison_id)
    _validate_targets(rule_ids, "rule_ids", comparison["rule_ids"], comparison_id)
    record = {
        "id": feedback_id, "comparison_id": comparison_id,
        "observation": observation, "suggested_change": suggested_change, "target_nodes": list(target_nodes),
        "rule_ids": list(rule_ids), "source": source, "status": "open",
        "reviewed_artifact_sha256": comparison["artifact"]["sha256"],
        "reference_sha256": comparison["reference"]["sha256"],
        "reference_region_source_xywh": list(comparison["reference"]["region_source_xywh"]),
    }
    feedback[feedback_id] = record
    state["feedback"] = feedback


def update_feedback(state: dict, feedback_id: str, status: str, comment: str,
                    comparison_id: str, source: str) -> None:
    _identifier(feedback_id, "feedback_id")
    if status not in ("open", "resolved", "dismissed"):
        raise ProjectError("status 必须是 open、resolved 或 dismissed")
    _text(comment, "comment")
    _validate_source(source)
    feedback = _collection(state, "feedback")
    if feedback_id not in feedback:
        raise ProjectError(f"反馈记录不存在：{feedback_id}")
    record = feedback[feedback_id]
    comparison = _comparison(_collection(state, "comparisons"), comparison_id)
    if comparison_id != record["comparison_id"]:
        raise ProjectError(f"复查反馈 {feedback_id} 必须使用原组件和参考的当前对照：{record['comparison_id']}")
    reference = comparison["reference"]
    if (reference["sha256"] != record["reference_sha256"]
            or reference["region_source_xywh"] != record["reference_region_source_xywh"]):
        raise ProjectError(f"复查反馈 {feedback_id} 必须使用相同的参考图和源图区域")
    artifact_sha256 = comparison["artifact"]["sha256"]
    if status == "resolved" and artifact_sha256 == record["reviewed_artifact_sha256"]:
        raise ProjectError(f"解决反馈 {feedback_id} 需要复查预览图 SHA-256 已变化的当前产物")
    record.update(status=status, source=source, comment=comment, reviewed_artifact_sha256=artifact_sha256)
