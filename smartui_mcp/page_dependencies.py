"""从当前记录查询页面引用，并识别尚未更新的组件导出。"""

from .comparisons import RUN_FIELDS
from .component_usage import check_component_states
from .page_layouts import iter_layout_nodes
from .projects import ProjectError, spec_sha256, style_sha256


def component_needs_review(state, component, current_style_sha256):
    latest = component["latest_run"]
    program = state["programs"][component["spec"]["program_id"]]
    return (latest is None or latest["style_sha256"] != current_style_sha256 or
            latest["spec_sha256"] != spec_sha256(component["spec"]) or
            latest.get("program_id") != program["id"] or latest.get("program_sha256") != program["sha256"])


def asset_source_issues(state):
    """检查当前图像记录的导出来源，不依赖原始产物继续存在。"""
    current_style = style_sha256(state["style"])
    issues = {}
    for asset_id, asset in state["images"].items():
        provenance = asset["provenance"]
        if provenance["kind"] != "component_export":
            continue
        source = provenance["source"]
        component_id = source["component_id"]
        component = state["components"].get(component_id)
        latest = component["latest_run"] if component is not None else None
        latest_run_id = latest["run_id"] if latest is not None else None
        export_run_id = source["run"]["run_id"]
        if latest is None or latest["status"] not in ("drawn", "verified"):
            reason = "component_has_no_successful_run"
            action = "先绘制组件，再重新导出资产"
        elif component_needs_review(state, component, current_style):
            reason = "component_inputs_changed"
            action = "先按当前输入重新绘制组件，再重新导出资产"
        elif (any(latest.get(key) != source["run"][key] for key in RUN_FIELDS) or
              latest.get("artifact_hashes") != source["artifact_hashes"]):
            reason = "export_run_changed"
            action = "从组件的当前产物重新导出资产"
        else:
            continue
        issues[asset_id] = {"asset_id": asset_id, "component_id": component_id, "reason": reason,
                            "action": action, "export_run_id": export_run_id, "latest_run_id": latest_run_id}
    return issues


def page_source_issues(state, nodes, asset_issues):
    asset_ids = dict.fromkeys(node["asset_id"] for node in iter_layout_nodes(nodes) if node["kind"] == "image")
    return [asset_issues[asset_id] if asset_id in asset_issues else
            {"asset_id": asset_id, "reason": "asset_missing", "action": "登记或导出该资产"}
            for asset_id in asset_ids if asset_id in asset_issues or asset_id not in state["images"]]


def matches_component_usage(state, record, asset_issues):
    """比较分组采用的规格和构建时记录的正式导出状态。"""
    for usage in record.get("component_usage", {}).values():
        component_id = usage["component_id"]
        component = state["components"].get(component_id)
        if component is None or "usage" not in component["spec"]:
            return False
        if (spec_sha256(component["spec"]["usage"]) != usage["usage_sha256"] or
                check_component_states(state, component_id, asset_issues) != usage["states"]):
            return False
    return True


def require_current_page_sources(state, page, asset_issues):
    issues = page_source_issues(state, page["definition"]["nodes"], asset_issues)
    if issues:
        def describe(issue):
            asset = issue["asset_id"]
            if "component_id" in issue:
                asset += f"（组件 {issue['component_id']}）"
            return f"{asset}：{issue['action']}"

        detail = "；".join(map(describe, issues))
        raise ProjectError(f"页面 {page['id']} 的资产来源未就绪：{detail}")


def page_dependencies(context):
    """报告从组件和文字样式到当前布局节点的引用关系。"""
    state = context["data"]
    asset_issues = asset_source_issues(state)
    components = {
        key: {"asset_ids": [], "uses": [], "needs_review": component["needs_review"],
              "latest_run_id": component["latest_run"]["run_id"] if component["latest_run"] is not None else None}
        for key, component in state["components"].items()
    }
    assets = {}
    for key, asset in state["images"].items():
        provenance = asset["provenance"]
        component_id = provenance["source"]["component_id"] if provenance["kind"] == "component_export" else None
        if component_id in components:
            components[component_id]["asset_ids"].append(key)
        assets[key] = {"component_id": component_id, "uses": [], "source_issue": asset_issues.get(key)}
    pages = {}
    for key, page in state["layouts"].items():
        nodes = page["definition"]["nodes"]
        for node in iter_layout_nodes(nodes):
            location = {"layout_id": key, "node_id": node["id"]}
            if node["kind"] == "image" and node["asset_id"] in assets:
                assets[node["asset_id"]]["uses"].append(location)
            elif node["kind"] == "group" and node.get("component_id") in components:
                components[node["component_id"]]["uses"].append(location)
        source_issues = page_source_issues(state, nodes, asset_issues)
        build_status = state["page_status"].get(key)
        reasons = []
        if build_status is None:
            reasons.append("not_built")
        else:
            for field, reason in (("matches_layout", "layout_changed"), ("matches_images", "image_bindings_changed"),
                                  ("matches_typography", "text_styles_changed"), ("matches_sources", "sources_stale"),
                                  ("matches_component_usage", "component_usage_changed")):
                if not build_status[field]:
                    reasons.append(reason)
        if source_issues and "sources_stale" not in reasons:
            reasons.append("sources_stale")
        pages[key] = {"status": "not_built" if build_status is None else "stale" if reasons else "current",
                      "stale_reasons": reasons, "source_issues": source_issues}
    return {"project_root": context["project_root"], "revision": context["revision"], "components": components,
            "assets": assets, "text_styles": state["typography_usage"], "pages": pages}
