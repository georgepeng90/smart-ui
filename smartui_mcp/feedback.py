"""记录对照发现的偏差及其处理过程。"""

from datetime import datetime, timezone

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
        "id": feedback_id, "comparison_id": comparison_id, "component_id": comparison["component_id"],
        "observation": observation, "suggested_change": suggested_change, "target_nodes": list(target_nodes),
        "rule_ids": list(rule_ids), "source": source, "status": "open",
        "created_at": datetime.now(timezone.utc).isoformat(), "history": [],
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
    comparisons = _collection(state, "comparisons")
    original = _comparison(comparisons, record["comparison_id"])
    comparison = _comparison(comparisons, comparison_id)
    if comparison["component_id"] != original["component_id"]:
        raise ProjectError(f"对照 {comparison_id} 与反馈 {feedback_id} 的原始对照必须使用同一组件")
    if any(comparison["reference"][field] != original["reference"][field]
           for field in ("id", "sha256", "region_source_xywh")):
        raise ProjectError(f"对照 {comparison_id} 与反馈 {feedback_id} 的原始对照必须使用同一参考及源图区域")
    if status == "resolved":
        run = comparison["run"]
        if (run["status"] not in ("drawn", "verified") or run["run_id"] == original["run"]["run_id"]
                or comparison["run_index"] <= original["run_index"]):
            raise ProjectError(f"解决反馈 {feedback_id} 需要关联原始运行之后成功绘制的产物对照")

    entry = {"at": datetime.now(timezone.utc).isoformat(), "status": status, "comment": comment,
             "comparison_id": comparison_id, "source": source}
    record["history"].append(entry)
    record["status"] = status
