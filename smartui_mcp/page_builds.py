"""以明确布局修订和资产版本构建可独立复查的浏览器快照。"""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from .image_assets import _file_path
from .layout import ProjectLayout
from .page_layouts import resolve_definition
from .references import _sha256


def build_page(project_root, build_id, layout_id, layout_version, definition, state):
    """从解析后的输入创建可独立读取的 HTML、PNG、字体及图像证据。"""
    from .browser_preview import render_page

    root = Path(project_root)
    layout = ProjectLayout(root, state["paths"])
    resolved = resolve_definition(state, definition)
    destination = root / ".smartui" / "page-builds" / build_id
    layout._check_managed_path(destination)
    if destination.exists():
        raise FileExistsError(f"页面构建已存在：{destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent) as temporary:
        staging = Path(temporary)
        files, copies = {}, {}
        for index, node in enumerate(resolved["nodes"]):
            field = "source" if node["kind"] == "image" else "font_path"
            hash_field = "sha256" if node["kind"] == "image" else "font_sha256"
            source = _file_path(node[field], f"节点 {node['id']} 来源")
            if _sha256(source) != node[hash_field]:
                raise ValueError(f"节点 {node['id']} 在保存快照时来源摘要变化：{source}")
            key = (node["kind"], node[hash_field])
            name = copies.get(key)
            if name is None:
                name = f"input-{index}{source.suffix.lower()}"
                copy = staging / name
                copy.write_bytes(source.read_bytes())
                if _sha256(copy) != node[hash_field]:
                    raise ValueError(f"节点 {node['id']} 在保存快照时来源摘要变化：{source}")
                copies[key] = name
            copy = staging / name
            node[field] = str(copy)
            node["snapshot_key"] = name
            files[name] = {"path": str(destination / name), "sha256": node[hash_field]}
        rendered = render_page(staging, resolved)
        for key in ("html", "overview"):
            item = deepcopy(rendered[key])
            item["path"] = str(destination / Path(item["path"]).name)
            files[key] = item
        snapshot = deepcopy(resolved)
        for node in snapshot["nodes"]:
            node["source" if node["kind"] == "image" else "font_path"] = node["snapshot_key"]
        inputs = staging / "inputs.json"
        inputs.write_text(json.dumps(snapshot, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
        files["inputs"] = {"path": str(destination / inputs.name), "sha256": _sha256(inputs)}
        for node in resolved["nodes"]:
            node["source" if node["kind"] == "image" else "font_path"] = str(destination / node["snapshot_key"])
        record = {"id": build_id, "layout_id": layout_id, "layout_version": layout_version,
                  "width": definition["width"], "height": definition["height"], "background": definition["background"],
                  "nodes": resolved["nodes"], "files": files, "renderer": rendered["renderer"]}
        layout._check_managed_path(destination)
        staging.rename(destination)
    return record


def validate_build_record(record):
    """校验快照必需文件集合与节点引用，防止漏掉证据而仍声称校验通过。"""
    label = record["id"]
    files, nodes = record.get("files"), record.get("nodes")
    if not isinstance(files, dict) or not isinstance(nodes, list) or not nodes:
        raise ValueError(f"页面构建缺少 files 或 nodes：{label}")
    required = {"html", "overview", "inputs"}
    for node in nodes:
        if not isinstance(node, dict) or node.get("kind") not in ("image", "text"):
            raise ValueError(f"页面构建节点无效：{label}")
        key = node.get("snapshot_key")
        if not isinstance(key, str) or not key.startswith("input-") or Path(key).name != key:
            raise ValueError(f"页面构建节点缺少有效 snapshot_key：{label}")
        required.add(key)
        field = "source" if node["kind"] == "image" else "font_path"
        hash_field = "sha256" if node["kind"] == "image" else "font_sha256"
        item = files.get(key)
        if (not isinstance(item, dict) or not node.get(field) or not node.get(hash_field) or
                node[field] != item.get("path") or node[hash_field] != item.get("sha256")):
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
    for node in expected["nodes"]:
        node["source" if node["kind"] == "image" else "font_path"] = node["snapshot_key"]
    actual = json.loads(Path(record["files"]["inputs"]["path"]).read_text(encoding="utf-8"))
    if actual != expected:
        raise ValueError(f"页面构建元数据与 inputs 快照不一致：{record['id']}")
