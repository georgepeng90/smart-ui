"""执行项目绘制程序，将通用计划交给 Krita 并登记实际产物。"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from uuid import uuid4

from .client import BridgeClient
from .layout import ProjectLayout
from .projects import ProjectError, ProjectStore, spec_sha256
from .programs import run_program


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def _nodes(inspection):
    result = {}
    def visit(node, parent):
        for child in node["children"]:
            result[child["id"]] = {**{k: v for k, v in child.items() if k != "children"},
                                   "parent": parent, "children": [n["id"] for n in child["children"]]}
            visit(child, child["id"])
    visit(inspection["root"], "root")
    return result


def _properties(nodes):
    return {key: {k: v for k, v in node.items() if k != "pixel_sha256"} for key, node in nodes.items()}


def _prepare(project_root, component_id, store, expected_revision=None):
    context = store.context(project_root)
    if expected_revision is not None and context["revision"] != expected_revision:
        raise ProjectError("项目版本已变化，请重新读取 project_context")
    if component_id not in context["data"]["components"]:
        raise ProjectError("尚未定义组件：" + component_id)
    spec = context["data"]["components"][component_id]["spec"]
    program = store.get_program(project_root, spec["program_id"])
    if program["sha256"] != context["data"]["programs"][spec["program_id"]]["sha256"]:
        raise ProjectError("读取期间绘制程序已变化，请重新读取 project_context")
    plan = run_program(program["source"], context["data"]["style"]["parameters"], spec, component_id)
    return context, program, plan


def plan_component(project_root, component_id, *, store=None):
    """执行项目 Python 程序并检查计划，不向 Krita 发送请求。"""
    context, program, plan = _prepare(project_root, component_id, store or ProjectStore())
    return {"project_root": context["project_root"], "revision": context["revision"],
            "component_id": component_id, "program_id": program["id"],
            "program_version": program["version"], "program_sha256": program["sha256"], "plan": plan}


def _layout(plan):
    return [{key: node[key] for key in ("key", "name", "kind", "parent")} for node in plan["nodes"]]


def _check_local(plan, previous, layers):
    if [plan["width"], plan["height"]] != [previous["width"], previous["height"]] or _layout(plan) != _layout(previous):
        raise ValueError("局部修改不能改变画布或图层结构；请完整绘制尺寸和结构匹配的组件")
    selected = set(layers)
    other_nodes = lambda value: [node for node in value["nodes"] if node["key"] not in selected]
    other_paint = lambda value: [paint for paint in value["paint"] if paint["node"] not in selected]
    if other_nodes(plan) != other_nodes(previous) or other_paint(plan) != other_paint(previous):
        raise ValueError("新计划还改变了未选择的节点；请选择所有受影响节点或完整绘制")


def _match_nodes(inspection, plan):
    """按完整名称、类型、父子关系及顺序匹配，不猜测节点。"""
    present = _nodes(inspection)
    by_name = {}
    for node in present.values():
        if node["name"] in by_name:
            raise ValueError("源文件图层名称不唯一：" + node["name"])
        by_name[node["name"]] = node
    if set(by_name) != {node["name"] for node in plan["nodes"]}:
        raise ValueError("源文件图层结构与计划不同；当前版本请用新组件标识创建改变结构的资产")
    ids = {node["key"]: by_name[node["name"]]["id"] for node in plan["nodes"]}
    children = {"root": []}
    for node in plan["nodes"]:
        actual = by_name[node["name"]]
        parent = "root" if node["parent"] == "root" else ids[node["parent"]]
        if actual["type"] != node["kind"] or actual["parent"] != parent:
            raise ValueError("源文件节点类型或父子关系与计划不同：" + node["key"])
        children.setdefault(parent, []).append(ids[node["key"]])
        children.setdefault(ids[node["key"]], [])
    actual_children = {"root": [node["id"] for node in inspection["root"]["children"]],
                       **{key: node["children"] for key, node in present.items()}}
    if children != actual_children:
        raise ValueError("源文件节点排列顺序与计划不同；请核对图层结构")
    return ids


def draw_component(project_root, expected_revision, component_id, layers=None, *, client=None, store=None):
    store = store or ProjectStore()
    client = client or BridgeClient()
    context, program, compiled = _prepare(project_root, component_id, store, expected_revision)
    data = context["data"]
    if component_id not in data["components"]:
        raise ProjectError("尚未定义组件：" + component_id)
    component = data["components"][component_id]
    style = data["style"]
    keys = {node["key"] for node in compiled["nodes"]}
    if layers is not None and (not isinstance(layers, list) or not layers or
                              any(not isinstance(key, str) or key not in keys for key in layers) or
                              len(set(layers)) != len(layers)):
        raise ValueError("layers 必须是计划中不重复的节点 key 非空列表；省略表示完整绘制")
    latest = component["latest_run"]
    if layers and latest is None:
        raise ValueError("组件尚无源文件，不能进行局部修改")
    if layers:
        if "plan_path" not in latest:
            raise ValueError("历史产物没有绘制计划记录；请先使用项目程序完整绘制")
        plan_bytes = Path(latest["plan_path"]).read_bytes()
        if hashlib.sha256(plan_bytes).hexdigest() != latest["artifact_hashes"]["plan_path"]:
            raise ValueError("历史绘制计划已被修改，不能据此判断局部更新范围")
        _check_local(compiled, json.loads(plan_bytes), layers)

    root = Path(context["project_root"])
    asset_dir = ProjectLayout(root, data["paths"]).directory("sources")
    asset_dir.mkdir(parents=True, exist_ok=True)
    document = asset_dir / (component_id + ".kra")
    if latest and Path(latest["document"]).resolve() != document:
        raise ValueError("已登记源文件路径与组件默认路径不同，请先核对组件记录")
    if document.exists():
        if latest is None:
            raise ValueError("目标源文件已存在但没有对应成功记录，不能覆盖：" + str(document))
        if hashlib.sha256(document.read_bytes()).hexdigest() != latest["artifact_hashes"]["document"]:
            raise ValueError("源文件已在工具外修改，请先核对现有内容，不能按旧记录覆盖：" + str(document))
    elif latest:
        raise ValueError("组件源文件缺失：" + str(document))

    run_id = uuid4().hex
    run_dir = root / ".smartui" / "runs" / run_id
    run_dir.mkdir(parents=True)
    snapshot = run_dir / "style-snapshot.json"
    operations_path = run_dir / "operations.json"
    program_path = run_dir / "program.py"
    plan_path = run_dir / "plan.json"
    program_path.write_bytes(program["source"].encode("utf-8"))
    _write(plan_path, compiled)
    _write(snapshot, {"style": style, "component_id": component_id, "spec": component["spec"]})
    calls = []
    record = {"run_id": run_id, "started_at": datetime.now(timezone.utc).isoformat(),
              "style_version": style["version"], "spec_sha256": spec_sha256(component["spec"]),
              "document": str(document), "operations_path": str(operations_path),
              "style_snapshot_path": str(snapshot), "edit_layers": layers,
              "program_id": program["id"], "program_version": program["version"],
              "program_sha256": program["sha256"], "program_snapshot_path": str(program_path),
              "plan_path": str(plan_path)}

    def call(method, params):
        entry = {"method": method, "params": params}
        calls.append(entry)
        try:
            result = client.call(method, params)
            entry["result"] = result
            return result
        except Exception as error:
            entry["error"] = str(error)
            raise
        finally:
            _write(operations_path, calls)

    try:
        status = call("status", {})
        for brush in sorted({paint["brush"] for paint in compiled["paint"]}):
            if brush not in call("brushes", {"query": brush}):
                raise ValueError("Krita 未安装指定笔刷：" + brush)
        opened = {str(Path(p).resolve()).casefold() for p in status["documents"] if p}
        existing = latest is not None
        if existing:
            if str(document).casefold() not in opened:
                call("execute", {"operations": [{"op": "document.open", "path": str(document)}]})
            before_edit = call("inspect", {"document": str(document)})
            if before_edit["modified"]:
                raise ValueError("源文件在 Krita 中有未保存修改，不能覆盖：" + str(document))
            if [before_edit["width"], before_edit["height"]] != [compiled["width"], compiled["height"]]:
                raise ValueError("组件尺寸改变导致画布不一致，请使用新组件标识生成尺寸变体")
            shutil.copyfile(document, run_dir / "input.kra")
            present = _nodes(before_edit)
            if any(node_id not in present for node_id in latest["layer_ids"].values()):
                raise ValueError("源文件中的组件图层 UUID 与记录不一致")
            layer_ids = _match_nodes(before_edit, compiled)
            if layers:
                unchanged = {key: value for key, value in present.items() if key not in {layer_ids[k] for k in layers}}
        else:
            if str(document).casefold() in opened:
                raise ValueError("同路径文档已在 Krita 中打开但尚未登记，请检查之前失败的制作尝试")
            setup = [{"op": "document.create", "path": str(document), "width": compiled["width"],
                      "height": compiled["height"], "name": component["name"]}]
            names = {node["key"]: node["name"] for node in compiled["nodes"]}
            for node in compiled["nodes"]:
                operation = {"document": str(document), "name": node["name"],
                             "parent": "root" if node["parent"] == "root" else "name:" + names[node["parent"]]}
                if node["kind"] == "transparencymask":
                    operation.update(op="mask.create", rects=node["rects"])
                else:
                    operation.update(op="layer.create", kind=node["kind"])
                setup.append(operation)
            result = call("execute", {"operations": setup})
            layer_ids = {node["key"]: result["completed"][index + 1]["result"]["node"]
                         for index, node in enumerate(compiled["nodes"])}
        record["layer_ids"] = layer_ids
        operations = []
        selected = set(layers or keys)
        if existing:
            # 项目程序可能在绘制后锁定节点；暂时解锁，最后恢复声明的属性。
            for node in compiled["nodes"]:
                properties = {"locked": False}
                if node["kind"] == "paintlayer":
                    properties["alpha_locked"] = False
                operations.append({"op": "layer.update", "document": str(document), "node": layer_ids[node["key"]],
                                   "properties": properties})
        for node in compiled["nodes"]:
            if node["key"] not in selected:
                continue
            if existing and node["kind"] == "paintlayer":
                operations.append({"op": "layer.clear", "document": str(document), "node": layer_ids[node["key"]]})
            if existing and node["kind"] == "transparencymask":
                operations.append({"op": "mask.update", "document": str(document), "node": layer_ids[node["key"]],
                                   "rects": node["rects"]})
        operations += [{**paint, "op": "paint", "document": str(document), "node": layer_ids[paint["node"]]}
                       for paint in compiled["paint"] if paint["node"] in selected]
        for node in compiled["nodes"]:
            operations.append({"op": "layer.update", "document": str(document), "node": layer_ids[node["key"]],
                               "properties": node["properties"]})
        operations.append({"op": "document.save", "document": str(document)})
        result = call("execute", {"operations": operations})
        saved = call("inspect", {"document": str(document)})
        saved_nodes = _nodes(saved)
        if layers and {key: saved_nodes[key] for key in unchanged} != unchanged:
            raise RuntimeError("局部修改影响了非目标节点；保留现场与输入快照，请检查该次操作记录")
        preview_result = call("preview", {"document": str(document)})
        preview_path = run_dir / "preview.png"
        shutil.copyfile(preview_result["path"], preview_path)
        shutil.copyfile(document, run_dir / "output.kra")
        call("execute", {"operations": [{"op": "document.close", "document": str(document)},
                                         {"op": "document.open", "path": str(document)}]})
        reopened = call("inspect", {"document": str(document)})
        reopened_nodes = _nodes(reopened)
        verification = {"reopened": True, "stable_node_ids": saved_nodes.keys() == reopened_nodes.keys(),
                        "stable_pixels": {k: v.get("pixel_sha256") for k, v in saved_nodes.items()} ==
                                         {k: v.get("pixel_sha256") for k, v in reopened_nodes.items()},
                        "stable_properties": _properties(saved_nodes) == _properties(reopened_nodes)}
        if layers:
            verification["unchanged_other_nodes"] = True
            verification["target_pixels_changed"] = any(
                present[layer_ids[key]].get("pixel_sha256") != saved_nodes[layer_ids[key]].get("pixel_sha256") for key in layers)
        if not all(verification[key] for key in ("reopened", "stable_node_ids", "stable_pixels", "stable_properties")):
            raise RuntimeError("源文件重新打开后与保存前不一致，请检查运行记录")
        record.update(status="verified", preview=str(preview_path), source_snapshot_path=str(run_dir / "output.kra"),
                      verification=verification, inspection={"before": saved, "reopened": reopened},
                      drawing_elapsed_ms=result["elapsed_ms"], user_confirmation=None)
        _write(run_dir / "result.json", record)
    except Exception as error:
        record.update(status="failed", error=str(error))
        _write(run_dir / "result.json", record)
        try:
            store.record_run(project_root, expected_revision, component_id, record)
        except Exception as recording_error:
            raise RuntimeError(f"制作失败：{error}；项目记录也未能更新：{recording_error}；现场记录：{run_dir}") from error
        raise RuntimeError(f"制作失败，已写入组件记录：{error}；现场记录：{run_dir}") from error

    # 版本冲突时保留真实产物，明确报告记录失败，不重试覆盖新项目状态。
    try:
        updated = store.record_run(project_root, expected_revision, component_id, record)
    except Exception as error:
        raise RuntimeError(f"资产已生成，但项目记录未更新：{error}；结果文件：{run_dir / 'result.json'}") from error
    return {"project_root": str(root), "revision": updated["revision"], "component_id": component_id,
            "run_id": run_id, "document": str(document), "preview": str(preview_path),
            "style_version": style["version"], "verification": verification,
            "program_id": program["id"], "program_version": program["version"], "program_sha256": program["sha256"],
            "drawing_elapsed_ms": record["drawing_elapsed_ms"], "result_path": str(run_dir / "result.json")}
