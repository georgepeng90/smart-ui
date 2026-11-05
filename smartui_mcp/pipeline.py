"""执行 PhotoCraft 计划，核验候选源文件后发布组件产物。"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from time import perf_counter
from uuid import uuid4

from PIL import Image

from .client import PhotoCraftClient
from .layout import ProjectLayout
from .photocraft import apply_plan, read_document
from .projects import ProjectError, ProjectStore, spec_sha256
from .programs import run_program


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    """执行项目 Python 程序并检查计划，不启动绘图进程。"""
    context, program, plan = _prepare(project_root, component_id, store or ProjectStore())
    return {"project_root": context["project_root"], "revision": context["revision"],
            "component_id": component_id, "program_id": program["id"],
            "program_version": program["version"], "program_sha256": program["sha256"], "plan": plan}


def _structure(plan):
    return [{key: node[key] for key in ("key", "name", "kind", "parent")} for node in plan["nodes"]]


def _check_update(plan, previous, layers):
    if [plan["width"], plan["height"]] != [previous["width"], previous["height"]] or _structure(plan) != _structure(previous):
        raise ValueError("更新不能改变画布或图层结构；请使用新组件标识制作尺寸或结构变体")
    if layers is not None:
        selected = set(layers)
        others = lambda value: [node for node in value["nodes"] if node["key"] not in selected]
        if others(plan) != others(previous):
            raise ValueError("新计划还改变了未选择的节点；请选择所有受影响节点或完整绘制")


def _node_map(inspection):
    return {node["id"]: node for node in inspection["nodes"]}


def _check_identity(inspection, plan, layer_ids):
    nodes = _node_map(inspection)
    if set(layer_ids) != {node["key"] for node in plan["nodes"]} or set(layer_ids.values()) != set(nodes):
        raise ValueError("源文件图层 ID 与组件记录或计划不一致")
    for node in plan["nodes"]:
        actual = nodes[layer_ids[node["key"]]]
        parent = "root" if node["parent"] == "root" else layer_ids[node["parent"]]
        if actual["name"] != node["name"] or actual["kind"] != node["kind"] or actual["parent"] != parent:
            raise ValueError("源文件节点名称、类型或父子关系与计划不一致：" + node["key"])


def _summary(inspection):
    return {"width": inspection["width"], "height": inspection["height"],
            "manifest_sha256": inspection["manifest_sha256"],
            "document_state": inspection["document_state"],
            "nodes": [{key: node[key] for key in ("id", "name", "kind", "parent", "sha256")}
                      for node in inspection["nodes"]]}


def _pixels(path):
    with Image.open(path) as image:
        image.load()
        return image.size, image.convert("RGBA").tobytes()


def draw_component(project_root, expected_revision, component_id, layers=None, *, client=None, store=None):
    """制作候选文件，校验原生模型与 PNG 后发布；制作检查失败不覆盖原源文件。"""
    store = store or ProjectStore()
    context, program, plan = _prepare(project_root, component_id, store, expected_revision)
    data = context["data"]
    component, style = data["components"][component_id], data["style"]
    keys = {node["key"] for node in plan["nodes"]}
    if layers is not None and (not isinstance(layers, list) or not layers or
                              any(not isinstance(key, str) or key not in keys for key in layers) or
                              len(set(layers)) != len(layers)):
        raise ValueError("layers 必须是计划中不重复的节点 key 非空列表；省略表示完整绘制")
    latest = component["latest_run"]
    if layers is not None and latest is None:
        raise ValueError("组件尚无源文件，不能进行局部修改")
    root = Path(context["project_root"])
    sources = ProjectLayout(root, data["paths"]).directory("sources")
    document = sources / (component_id + ".pcraft")
    if latest and Path(latest["document"]).resolve() != document:
        raise ValueError("已登记源文件路径与组件 PhotoCraft 路径不同；请使用新组件标识制作")
    before = None
    if latest:
        if not document.is_file():
            raise ValueError("组件源文件缺失：" + str(document))
        if _sha(document) != latest["artifact_hashes"]["document"]:
            raise ValueError("源文件已在工具外修改，不能按旧记录覆盖：" + str(document))
        plan_bytes = Path(latest["plan_path"]).read_bytes()
        if hashlib.sha256(plan_bytes).hexdigest() != latest["artifact_hashes"]["plan_path"]:
            raise ValueError("历史绘制计划已被修改，不能据此更新组件")
        _check_update(plan, json.loads(plan_bytes), layers)
        before = read_document(document)
        _check_identity(before, plan, latest["layer_ids"])
    elif document.exists():
        raise ValueError("目标源文件已存在但没有对应成功记录，不能覆盖：" + str(document))

    sources.mkdir(parents=True, exist_ok=True)
    run_id = uuid4().hex
    run_dir = root / ".smartui" / "runs" / run_id
    run_dir.mkdir(parents=True)
    snapshot, operations_path = run_dir / "style-snapshot.json", run_dir / "operations.json"
    program_path, plan_path = run_dir / "program.py", run_dir / "plan.json"
    output, reopened_path = run_dir / "output" / document.name, run_dir / "reopened" / document.name
    output.parent.mkdir()
    reopened_path.parent.mkdir()
    before_preview, preview = run_dir / "before-reopen.png", run_dir / "preview.png"
    program_path.write_bytes(program["source"].encode("utf-8"))
    _write(plan_path, plan)
    _write(snapshot, {"style": style, "component_id": component_id, "spec": component["spec"]})
    calls = []
    _write(operations_path, calls)
    record = {"run_id": run_id, "started_at": datetime.now(timezone.utc).isoformat(),
              "style_version": style["version"], "spec_sha256": spec_sha256(component["spec"]),
              "document": str(document), "operations_path": str(operations_path),
              "style_snapshot_path": str(snapshot), "edit_layers": layers,
              "program_id": program["id"], "program_version": program["version"],
              "program_sha256": program["sha256"], "program_snapshot_path": str(program_path),
              "plan_path": str(plan_path)}

    def trace(entry):
        calls.append(entry)
        _write(operations_path, calls)

    def relative(path):
        return path.relative_to(run_dir).as_posix()

    started = perf_counter()
    try:
        client = client or PhotoCraftClient()
        record["backend"] = {"name": "photocraft", **client.status()}
        with client.session(run_dir, trace=trace) as session:
            ids = None
            if latest:
                source = run_dir / "input.pcraft"
                shutil.copyfile(document, source)
                session.call("doc.open", {"path": relative(source)})
                ids = latest["layer_ids"]
            layer_ids = apply_plan(session, plan, layer_ids=ids, selected=layers)
            record["layer_ids"] = layer_ids
            session.call("doc.render", {"path": relative(before_preview), "maxSide": 0})
            session.call("doc.save", {"path": relative(output)})
            saved = read_document(output)
            _check_identity(saved, plan, layer_ids)
            if layers is not None:
                selected_ids = {layer_ids[key] for key in layers}
                old_nodes, new_nodes = _node_map(before), _node_map(saved)
                if any(old_nodes[key]["sha256"] != new_nodes[key]["sha256"]
                       for key in old_nodes if key not in selected_ids):
                    raise RuntimeError("局部修改影响了非目标节点；候选文件已保留，原源文件未覆盖")
                if before["document_state"] != saved["document_state"]:
                    raise RuntimeError("局部修改改变了文档级状态；候选文件已保留，原源文件未覆盖")
            session.call("doc.close")
            session.call("doc.open", {"path": relative(output)})
            session.call("doc.render", {"path": relative(preview), "maxSide": 0})
            session.call("doc.save", {"path": relative(reopened_path)})
            reopened = read_document(reopened_path)
        saved_nodes, reopened_nodes = _node_map(saved), _node_map(reopened)
        verification = {"reopened": True,
                        "stable_node_ids": list(saved_nodes) == list(reopened_nodes),
                        "stable_pixels": _pixels(before_preview) == _pixels(preview),
                        "stable_properties": saved["document_state"] == reopened["document_state"] and
                        {key: node["sha256"] for key, node in saved_nodes.items()} ==
                        {key: node["sha256"] for key, node in reopened_nodes.items()}}
        if layers is not None:
            verification["unchanged_other_nodes"] = True
            verification["target_content_changed"] = any(
                _node_map(before)[layer_ids[key]]["sha256"] != saved_nodes[layer_ids[key]]["sha256"] for key in layers)
        if not all(verification[key] for key in ("reopened", "stable_node_ids", "stable_pixels", "stable_properties")):
            raise RuntimeError("源文件保存重开检查失败；候选产物已保留，原源文件未覆盖")
        record.update(status="verified", preview=str(preview), source_snapshot_path=str(output),
                      verification=verification, inspection={"before": _summary(saved), "reopened": _summary(reopened)},
                      drawing_elapsed_ms=(perf_counter() - started) * 1000, user_confirmation=None)
        _write(run_dir / "result.json", record)
    except Exception as error:
        record.update(status="failed", error=str(error))
        _write(run_dir / "result.json", record)
        try:
            store.record_run(project_root, expected_revision, component_id, record)
        except Exception as recording_error:
            raise RuntimeError(f"制作失败：{error}；项目记录也未能更新：{recording_error}；现场记录：{run_dir}") from error
        raise RuntimeError(f"制作失败，已写入组件记录：{error}；现场记录：{run_dir}") from error
    try:
        updated = store.publish_run(project_root, expected_revision, component_id, record)
    except Exception as error:
        record.update(status="failed", error=str(error))
        _write(run_dir / "result.json", record)
        raise RuntimeError(f"候选产物已生成，但发布或项目登记失败：{error}；结果文件：{run_dir / 'result.json'}") from error
    return {"project_root": str(root), "revision": updated["revision"], "component_id": component_id,
            "run_id": run_id, "document": str(document), "preview": str(preview),
            "style_version": style["version"], "verification": verification, "backend": record["backend"],
            "program_id": program["id"], "program_version": program["version"], "program_sha256": program["sha256"],
            "drawing_elapsed_ms": record["drawing_elapsed_ms"], "result_path": str(run_dir / "result.json")}
