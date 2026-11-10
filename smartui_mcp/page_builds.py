"""从当前布局和资产构建可离线打开的浏览器页面。"""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from .image_assets import _file_path, _replace_directory
from .layout import ProjectLayout
from .page_layouts import _identifier, resolve_definition
from .references import _sha256, _temporary_directory

LAYOUT_METADATA = ("layout_groups", "baseline_groups", "safe_area", "component_usage", "control_dependencies",
                   "checks", "unity_adaptation")


def snapshot_key(root, node, source):
    """用图像资产 ID 或工程内字体路径命名预览资源。"""
    suffix = source.suffix.lower()
    if node["kind"] == "image":
        return f"image-{node['asset_id']}{suffix}"
    font_path = source.relative_to(root).as_posix()
    # 文件名编码工程内字体路径；字体内容摘要单独校验。
    font_key = hashlib.sha256(font_path.encode("utf-8")).hexdigest()
    return f"font-{font_key}{suffix}"


def build_page(project_root, build_id, layout_id, layout_sha256, definition, state, *,
               commit=None, annotate=False, annotation_device=None):
    """从解析后的输入创建可独立读取的 HTML、PNG、字体及图像证据。"""
    from .browser_preview import render_page

    _identifier(layout_id, "layout_id")
    if build_id != layout_id:
        raise ValueError("build_id 必须与 layout_id 一致")
    root = _file_path(str(project_root), "project_root")
    layout = ProjectLayout(root, state["paths"])
    resolved = resolve_definition(state, definition)
    if annotation_device is not None:
        from .annotation import validate_device

        if not annotate:
            raise ValueError("annotation_device 需要启用 annotate")
        resolved["annotation_device"] = validate_device(annotation_device, resolved)
    destination = layout.persistent(f"previews/pages/{layout_id}")
    with TemporaryDirectory(dir=_temporary_directory(layout.root)) as temporary:
        staging = Path(temporary) / "current"
        staging.mkdir()
        files = {}
        for node in resolved["nodes"]:
            field = "source" if node["kind"] == "image" else "font_path"
            hash_field = "sha256" if node["kind"] == "image" else "font_sha256"
            source = _file_path(node[field], f"节点 {node['id']} 来源")
            name = snapshot_key(root, node, source)
            shared = node["kind"] == "text"
            target = (destination.parent.parent / "fonts" / f"{node[hash_field]}.font"
                      if shared else destination / name)
            layout._check_managed_path(target)
            copy = staging / name
            if name not in files:
                copy.write_bytes(source.read_bytes())
                if _sha256(copy) != node[hash_field]:
                    raise ValueError(f"节点 {node['id']} 在保存快照时来源摘要变化：{source}")
                if shared:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.is_file() and _sha256(target) == node[hash_field]:
                        copy.unlink()
                    else:
                        copy.replace(target)
                files[name] = {"path": str(target), "sha256": node[hash_field]}
            elif files[name]["sha256"] != node[hash_field]:
                raise ValueError(f"节点 {node['id']} 的同一快照资源摘要不一致：{source}")
            node[field] = str(target if shared else copy)
            node["snapshot_key"] = name
        rendered = (render_page(staging, resolved, annotate=True, annotation_device=annotation_device)
                    if annotate else render_page(staging, resolved))
        for key in ("html", "overview", "annotated") if annotate else ("html", "overview"):
            item = deepcopy(rendered[key])
            item["path"] = str(destination / Path(item["path"]).name)
            files[key] = item
        snapshot = deepcopy(resolved)
        for node in snapshot["nodes"]:
            node["source" if node["kind"] == "image" else "font_path"] = (
                Path(os.path.relpath(files[node["snapshot_key"]]["path"], destination)).as_posix()
            )
        inputs = staging / "inputs.json"
        inputs.write_text(json.dumps(snapshot, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
        files["inputs"] = {"path": str(destination / inputs.name), "sha256": _sha256(inputs)}
        for node in resolved["nodes"]:
            node["source" if node["kind"] == "image" else "font_path"] = files[node["snapshot_key"]]["path"]
        record = {"id": build_id, "layout_id": layout_id, "layout_sha256": layout_sha256,
                  "width": definition["width"], "height": definition["height"], "background": definition["background"],
                  "nodes": resolved["nodes"], "files": files, "renderer": rendered["renderer"]}
        record.update({key: deepcopy(resolved[key]) for key in LAYOUT_METADATA if key in resolved})
        return _replace_directory(layout, staging, destination,
                                  lambda: commit(record) if commit is not None else record)


def validate_build_record(record):
    """校验快照必需文件集合与节点引用，防止漏掉证据而仍声称校验通过。"""
    label = record["id"]
    files, nodes = record.get("files"), record.get("nodes")
    if not isinstance(files, dict) or not isinstance(nodes, list) or not nodes:
        raise ValueError(f"页面构建缺少 files 或 nodes：{label}")
    if "control_dependencies" in record:
        dependencies = record["control_dependencies"]
        if not isinstance(dependencies, dict) or not dependencies:
            raise ValueError(f"页面构建的 control_dependencies 必须是非空对象：{label}")
        for control_id, digest in dependencies.items():
            _identifier(control_id, f"页面构建 {label} 的控件 ID")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError(f"页面构建 {label} 的控件 {control_id} 缺少有效依赖摘要")
    inputs = files.get("inputs")
    if not isinstance(inputs, dict) or not isinstance(inputs.get("path"), str):
        raise ValueError(f"页面构建缺少有效 inputs 路径：{label}")
    font_directory = Path(inputs["path"]).parent.parent.parent / "fonts"
    required = {"html", "overview", "inputs"}
    if "annotated" in files:
        required.add("annotated")
    for node in nodes:
        if not isinstance(node, dict) or node.get("kind") not in ("image", "text"):
            raise ValueError(f"页面构建节点无效：{label}")
        if node["kind"] == "text":
            node_label = f"页面构建 {label} 的文字节点 {node.get('id')}"
            _identifier(node.get("text_style_id"), f"{node_label}.text_style_id")
            digest = node.get("text_style_sha256")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError(f"{node_label} 缺少有效 text_style_sha256")
        key = node.get("snapshot_key")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", key) or key in {
                "html", "overview", "inputs", "annotated", "preview.html", "overview.png", "inputs.json",
                "annotated.png"}:
            raise ValueError(f"页面构建节点缺少有效 snapshot_key：{label}")
        required.add(key)
        field = "source" if node["kind"] == "image" else "font_path"
        hash_field = "sha256" if node["kind"] == "image" else "font_sha256"
        item = files.get(key)
        path = Path(item["path"]) if isinstance(item, dict) and isinstance(item.get("path"), str) else None
        shared_font = node["kind"] == "text" and path == font_directory / f"{node.get(hash_field)}.font"
        if (not isinstance(item, dict) or not isinstance(node.get(field), str) or
                (not shared_font and Path(node[field]).name != key) or node[field] != item.get("path") or
                not isinstance(node.get(hash_field), str) or not re.fullmatch(r"[0-9a-f]{64}", node[hash_field]) or
                node[hash_field] != item.get("sha256")):
            raise ValueError(f"页面构建节点与快照文件不一致：{label}/{key}")
    if set(files) != required:
        raise ValueError(f"页面构建快照文件集合不完整：{label}；需要 {sorted(required)}")
    for key, item in files.items():
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("sha256"), str):
            raise ValueError(f"页面构建快照文件记录无效：{label}/{key}")


def verify_build(record):
    validate_build_record(record)
    for label, item in record["files"].items():
        path = _file_path(item["path"], f"页面快照 {label}")
        if _sha256(path) != item["sha256"]:
            raise ValueError(f"页面快照 SHA-256 不符：{path}")
    expected = {field: deepcopy(record[field]) for field in ("width", "height", "background", "nodes")}
    expected.update({key: deepcopy(record[key]) for key in LAYOUT_METADATA if key in record})
    inputs_path = Path(record["files"]["inputs"]["path"])
    for node in expected["nodes"]:
        field = "source" if node["kind"] == "image" else "font_path"
        node[field] = Path(os.path.relpath(node[field], inputs_path.parent)).as_posix()
    actual = json.loads(inputs_path.read_text(encoding="utf-8"))
    if actual != expected:
        raise ValueError(f"页面构建元数据与 inputs 快照不一致：{record['id']}")
