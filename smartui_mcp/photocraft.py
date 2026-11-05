"""将当前计划执行为原生图层，并验证 .pcraft 的完整图层和内容引用。"""

from copy import deepcopy
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
import zipfile

import blake3
import zstandard

from .programs import ProgramError, validate_plan


class NativeDocumentError(ValueError):
    """原生源文件结构或其引用内容不完整。"""


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise NativeDocumentError(f"原生 manifest 包含重复字段：{key}")
        result[key] = value
    return result


def read_document(path):
    """校验 ZIP、BLAKE3 对象、引用和图层树；组摘要单独记录有序子 ID。"""
    path = Path(path)
    if path.suffix.lower() != ".pcraft":
        raise NativeDocumentError(f"源文件必须是 .pcraft：{path}")
    data = path.read_bytes()
    references = set()
    object_hashes = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(set(names)) != len(names):
                raise NativeDocumentError(f"{path} 包含重名 ZIP 条目")
            for info in infos:
                parts = PurePosixPath(info.filename)
                if parts.is_absolute() or ".." in parts.parts or "\\" in info.filename:
                    raise NativeDocumentError(f"{path} 包含非法条目路径：{info.filename}")
                if info.file_size > 128 * 1024 * 1024:
                    raise NativeDocumentError(f"{path} 条目超过 128 MiB：{info.filename}")
            if "manifest.json" not in names:
                raise NativeDocumentError(f"{path} 缺少 manifest.json")
            raw_manifest = archive.read("manifest.json")
            if len(raw_manifest) > 16 * 1024 * 1024:
                raise NativeDocumentError(f"{path} manifest.json 超过 16 MiB")
            manifest = json.loads(raw_manifest, object_pairs_hook=_json_object,
                                  parse_constant=lambda value: (_ for _ in ()).throw(NativeDocumentError(f"非法 JSON 数值：{value}")))
            if type(manifest) is not dict or type(manifest.get("format_version")) is not int or manifest["format_version"] != 1:
                raise NativeDocumentError(f"{path} 不支持的原生格式版本")
            total = 0
            for name in names:
                match = re.fullmatch(r"(tiles|blobs)/([0-9a-f]{64})\.zst", name)
                if name.startswith(("tiles/", "blobs/")) and not match:
                    raise NativeDocumentError(f"{path} 非法对象路径：{name}")
                if match:
                    compressed = archive.read(name)
                    with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(compressed)) as stream:
                        payload = stream.read(128 * 1024 * 1024 + 1)
                    total += len(payload)
                    if len(payload) > 128 * 1024 * 1024 or total > 2 * 1024 * 1024 * 1024:
                        raise NativeDocumentError(f"{path} 解压后的原生对象超过验证上限")
                    digest = blake3.blake3(payload).hexdigest()
                    if digest != match[2]:
                        raise NativeDocumentError(f"{path} 内容与 BLAKE3 摘要不一致：{name}")
                    object_hashes[name] = {"blake3": digest, "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
                else:
                    # 读取所有条目验证 ZIP CRC，而不将任何文件解压到宿主目录。
                    archive.read(name)
            for key in ("thumbnail", "composite"):
                if manifest.get(key) is not None and (type(manifest[key]) is not str or manifest[key] not in names):
                    raise NativeDocumentError(f"{path} 缺少 {key} 预览引用：{manifest[key]}")

            def reference(digest, kind, label):
                if type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest):
                    raise NativeDocumentError(f"{path} {label} 摘要无效：{digest!r}")
                name = f"{kind}/{digest}.zst"
                if name not in object_hashes:
                    raise NativeDocumentError(f"{path} {label} 引用缺失：{name}")
                references.add(name)

            def inspect_refs(value, label="document", depth=0):
                if depth > 128:
                    raise NativeDocumentError(f"{path} 原生结构嵌套超过 128 层")
                if type(value) is dict:
                    for key, item in value.items():
                        child = label + "." + key
                        if key in ("psd_raw", "icc_profile", "exif", "blob", "svg") and item is not None:
                            reference(item, "blobs", child)
                        elif key == "hash" and "tx" in value and "ty" in value:
                            reference(item, "tiles", child)
                        elif key in ("psd_blocks", "psd_resources", "psd_global_blocks"):
                            if type(item) is not list:
                                raise NativeDocumentError(f"{path} {child} 必须是数组")
                            for entry in item:
                                if type(entry) is not list or len(entry) not in (2, 3):
                                    raise NativeDocumentError(f"{path} {child} 引用结构错误")
                                reference(entry[-1], "blobs", child)
                        else:
                            inspect_refs(item, child, depth + 1)
                    if set(("format", "default", "tiles")) <= value.keys():
                        fmt = value["format"]
                        if type(fmt) is not dict or type(value["tiles"]) is not list:
                            raise NativeDocumentError(f"{path} {label} 像素表面结构错误")
                        channels = {"Rgb": 3, "Gray": 1, "Cmyk": 4, "Lab": 3, "Indexed": 1, "Bitmap": 1}.get(fmt.get("mode"))
                        sample_bytes = {"U8": 1, "U16": 2, "F32": 4}.get(fmt.get("sample"))
                        if channels is None or sample_bytes is None or type(fmt.get("alpha")) is not bool:
                            raise NativeDocumentError(f"{path} {label} 不支持的像素格式：{fmt}")
                        pixel_bytes = (channels + int(fmt["alpha"])) * sample_bytes
                        if type(value["default"]) is not str or not re.fullmatch(r"[0-9a-fA-F]{%d}" % (pixel_bytes * 2), value["default"]):
                            raise NativeDocumentError(f"{path} {label} 默认像素编码错误")
                        coords = set()
                        for tile in value["tiles"]:
                            if type(tile) is not dict or set(tile) != {"tx", "ty", "hash"} or type(tile["tx"]) is not int or type(tile["ty"]) is not int:
                                raise NativeDocumentError(f"{path} {label} tile 引用错误")
                            coord = (tile["tx"], tile["ty"])
                            if coord in coords:
                                raise NativeDocumentError(f"{path} {label} tile 坐标重复：{coord}")
                            coords.add(coord)
                            if object_hashes[f"tiles/{tile['hash']}.zst"]["bytes"] != 256 * 256 * pixel_bytes:
                                raise NativeDocumentError(f"{path} {label} tile 长度与像素格式不一致")
                elif type(value) is list:
                    for index, item in enumerate(value):
                        inspect_refs(item, f"{label}[{index}]", depth + 1)
                elif type(value) is float and not math.isfinite(value):
                    raise NativeDocumentError(f"{path} {label} 包含非有限数值")
            document = manifest.get("document")
            if type(document) is not dict or type(document.get("layers")) is not list:
                raise NativeDocumentError(f"{path} 缺少原生 document/layers")
            inspect_refs(document)
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError, zstandard.ZstdError, RecursionError, TypeError) as exc:
        raise NativeDocumentError(f"无法验证原生文件 {path}：{exc}") from exc
    size = document.get("size")
    if type(size) is not dict or any(type(size.get(k)) is not int or size[k] <= 0 for k in ("width", "height")):
        raise NativeDocumentError(f"{path} 画布尺寸无效")
    if type(document.get("id")) is not int or document["id"] <= 0:
        raise NativeDocumentError(f"{path} 文档 ID 无效")
    nodes = []
    seen = set()
    def walk(layers, parent, depth=0):
        if depth > 128:
            raise NativeDocumentError(f"{path} 图层嵌套超过 128 层")
        for layer in layers:
            if type(layer) is not dict or type(layer.get("id")) is not int or layer["id"] <= 0 or layer["id"] in seen:
                raise NativeDocumentError(f"{path} 图层 ID 缺失或重复")
            if type(layer.get("name")) is not str or not layer["name"].strip() or type(layer.get("content")) is not dict:
                raise NativeDocumentError(f"{path} 图层名称或内容结构错误")
            seen.add(layer["id"])
            content = layer["content"]
            kind = {"raster": "pixel", "shape": "shape", "group": "group", "text": "text", "fill": "fill", "adjustment": "adjustment", "smart": "smart"}.get(content.get("kind"))
            if kind is None:
                raise NativeDocumentError(f"{path} 不支持的原生图层类型：{content.get('kind')}")
            own = deepcopy(layer)
            if kind == "group":
                children = content.get("children")
                if type(children) is not list or any(type(c) is not dict or type(c.get("id")) is not int for c in children):
                    raise NativeDocumentError(f"{path} 分组 children 结构错误")
                own["content"]["children"] = [c["id"] for c in children]
            nodes.append({"id": layer["id"], "name": layer["name"], "kind": kind, "parent": parent,
                          "manifest": own, "sha256": hashlib.sha256(_json_bytes(own)).hexdigest()})
            if kind == "group":
                walk(content["children"], layer["id"], depth + 1)
    walk(document["layers"], "root")
    return {"width": size["width"], "height": size["height"], "document": document,
            "document_state": {k: deepcopy(v) for k, v in document.items() if k not in ("name", "layers")},
            "manifest": manifest, "manifest_sha256": hashlib.sha256(raw_manifest).hexdigest(),
            "sha256": hashlib.sha256(data).hexdigest(), "nodes": nodes,
            "objects": object_hashes, "referenced_objects": sorted(references)}


def _command(session, command, params=None):
    return session.call("engine.execute", {"command": command, "params": params or {}})


def _live_nodes(inspection):
    result = {}
    def walk(layers, parent):
        for layer in reversed(layers):
            result[layer["id"]] = {"kind": {"Pixel": "pixel", "Shape": "shape", "Group": "group"}.get(layer["kind"]), "parent": parent, "layer": layer}
            if layer["kind"] == "Group":
                walk(layer["children"], layer["id"])
    walk(inspection["layers"], "root")
    return result


def apply_plan(session, plan, layer_ids=None, selected=None):
    """新建或更新选中图层；更新保持 ID、结构及未选图层原样。"""
    plan = validate_plan(plan)
    keys = {n["key"] for n in plan["nodes"]}
    if selected is not None and (type(selected) is not list or not selected or any(type(k) is not str or k not in keys for k in selected) or len(set(selected)) != len(selected)):
        raise ProgramError("selected 必须是计划中不重复的节点 key 非空列表")
    creating = layer_ids is None
    if creating and selected is not None:
        raise ProgramError("新建文档不能只制作部分节点")
    if creating:
        session.call("doc.new", {"width": plan["width"], "height": plan["height"], "background": "transparent"})
        initial = session.call("doc.inspect")
        if len(initial["layers"]) != 1 or initial["layers"][0]["kind"] != "Pixel":
            raise NativeDocumentError("PhotoCraft 新文档没有预期的默认空白像素层")
        anchor = initial["layers"][0]["id"]
        ids = {}
        previous_root = anchor
        for node in plan["nodes"]:
            _command(session, "layer.select", {"layer": anchor})
            if node["kind"] == "shape":
                result = _command(session, "shape.create", node["shape"] | {"name": node["name"]})
            elif node["kind"] == "pixel":
                result = _command(session, "layer.new.layer", {"name": node["name"], "clipToPrevious": False,
                                  "color": "none", "mode": "Normal", "opacity": 1, "fillNeutral": False, "position": "above"})
            else:
                result = _command(session, "layer.new.group", {"name": node["name"], "color": "none",
                                  "mode": "PassThrough", "opacity": 100, "position": "above"})
            native_id = result.get("layer") if type(result) is dict else None
            if type(native_id) is not int or native_id <= 0:
                raise NativeDocumentError(f"PhotoCraft 未返回新节点 {node['key']} 的有效 layer ID：{result}")
            ids[node["key"]] = native_id
            if node["parent"] == "root":
                _command(session, "layer.moveTo", {"layer": native_id, "target": previous_root, "position": "above", "copy": False})
                previous_root = native_id
            else:
                _command(session, "layer.moveTo", {"layer": native_id, "target": ids[node["parent"]], "position": "into", "copy": False})
            if node["kind"] == "group":
                _command(session, "layer.setExpanded", {"layer": native_id, "expanded": False})
        _command(session, "layer.delete", {"layer": anchor})
    else:
        if type(layer_ids) is not dict or set(layer_ids) != keys or any(type(v) is not int or v <= 0 for v in layer_ids.values()) or len(set(layer_ids.values())) != len(layer_ids):
            raise ProgramError("layer_ids 必须完整映射计划 key 到互不重复的原生整数 ID")
        ids = dict(layer_ids)
        current = session.call("doc.inspect")
        if (current["width"], current["height"]) != (plan["width"], plan["height"]):
            raise ProgramError("原位更新不能改变画布尺寸")
        live = _live_nodes(current)
        if set(live) != set(ids.values()):
            raise ProgramError("源文档节点集合与 layer_ids 不一致")
        for node in plan["nodes"]:
            actual = live[ids[node["key"]]]
            parent = "root" if node["parent"] == "root" else ids[node["parent"]]
            if actual["kind"] != node["kind"] or actual["parent"] != parent:
                raise ProgramError(f"原位更新不能改变节点类型或父子关系：{node['key']}")
        expected_order = {}
        actual_order = {}
        for node in plan["nodes"]:
            parent = "root" if node["parent"] == "root" else ids[node["parent"]]
            expected_order.setdefault(parent, []).append(ids[node["key"]])
        for native_id, actual in live.items():
            actual_order.setdefault(actual["parent"], []).append(native_id)
        if actual_order != expected_order:
            raise ProgramError("原位更新不能改变同一父节点内的图层顺序")
    chosen = keys if selected is None else set(selected)
    current = session.call("doc.inspect")
    live = _live_nodes(current)
    # 隐藏目标和祖先会阻止原生绘制；只暂时显示相关路径，结束时恢复未选祖先。
    temporary_visible = []
    for key in chosen:
        native_id = ids[key]
        while native_id != "root":
            layer = live[native_id]
            if not layer["layer"]["visible"] and native_id not in temporary_visible:
                _command(session, "layer.setProps", {"layer": native_id, "visible": True})
                temporary_visible.append(native_id)
            native_id = layer["parent"]
    if current.get("hasSelection"):
        _command(session, "select.deselect")
    for node in plan["nodes"]:
        if node["key"] not in chosen:
            continue
        native_id = ids[node["key"]]
        _command(session, "layer.select", {"layer": native_id})
        before = live[native_id]["layer"]
        if before.get("hasMask"):
            _command(session, "layer.layerMask.delete", {"layer": native_id})
        if before.get("hasVectorMask"):
            _command(session, "layer.vectorMask.delete", {"layer": native_id})
        if node["kind"] == "shape" and not creating:
            shape = dict(node["shape"])
            # 0.20.1 的 shape.edit 用 path 字段替换轮廓；kind:path 只属于创建接口。
            if shape["kind"] == "path":
                del shape["kind"]
            _command(session, "shape.edit", shape | {"layer": native_id, "name": node["name"]})
        elif node["kind"] == "pixel":
            _command(session, "edit.clear", {"layer": native_id, "target": "pixels"})
            for stroke in node["strokes"]:
                _command(session, "paint.stroke", stroke | {"layer": native_id, "target": "pixels", "mode": "normal"})
        _command(session, "layer.layerStyle.replace", {"layer": native_id, "effects": node["effects"]})
        if node["effects"]:
            _command(session, "layer.setEffectsVisible", {"layer": native_id, "visible": True})
        if node["mask"] is not None:
            _command(session, "layer.vectorMask.add", {"layer": native_id, "path": node["mask"]["path"], "hide": False})
            _command(session, "layer.vectorMask.edit", {"layer": native_id, **node["mask"]})
    # 先完成所有内容，再写最终可见性，保证隐藏组仍可完整制作。
    for node in plan["nodes"]:
        if node["key"] in chosen:
            _command(session, "layer.setProps", {"layer": ids[node["key"]], "name": node["name"], **node["properties"]})
    selected_ids = {ids[key] for key in chosen}
    for native_id in temporary_visible:
        if native_id not in selected_ids:
            _command(session, "layer.setProps", {"layer": native_id, "visible": False})
    return ids
