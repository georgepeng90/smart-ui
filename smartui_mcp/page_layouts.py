"""当前页面定义、字体校验和资产解析。"""

from copy import deepcopy
from io import BytesIO
import math
import re

from fontTools.ttLib import TTFont, TTLibError

from .comparisons import _check_output_size
from .image_assets import _file_path, verify_image_record
from .references import _sha256
from .slicing import image_patches


MAX_PAGE_NODES = 512


def _integer(value, field, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{field} 必须是大于等于 {minimum} 的整数")


def _identifier(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ValueError(f"{field} 必须是有效的字母数字标识")


def _color(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        raise ValueError(f"{field} 必须是 #RRGGBB 颜色")


def _font(node):
    label = f"文字节点 {node['id']}"
    path = _file_path(node["font_path"], f"{label}.font_path")
    if path.suffix.lower() not in (".ttf", ".otf"):
        raise ValueError(f"{label} 只支持 TTF/OTF 字体：{path}")
    digest = _sha256(path)
    if "font_sha256" in node and node["font_sha256"] != digest:
        raise ValueError(f"{label} 字体 SHA-256 不符：{path}；请核对字体并重新保存布局")
    try:
        with TTFont(BytesIO(path.read_bytes())) as font:
            cmap = font.getBestCmap() or {}
            missing = sorted({char for char in node["text"] if char != "\n" and
                              cmap.get(ord(char), ".notdef") == ".notdef"})
    except (OSError, TTLibError) as error:
        raise ValueError(f"{label} 无法读取字体：{path}；{error}") from error
    if missing:
        detail = ", ".join(f"{char!r} U+{ord(char):04X}" for char in missing)
        raise ValueError(f"{label} 字体 {path} 缺少字符：{detail}")
    return str(path), digest


def validate_definition(width, height, background, nodes):
    """保存时校验节点和实际字体，文本字体绑定其确切摘要。"""
    _integer(width, "width", 1)
    _integer(height, "height", 1)
    _check_output_size((width, height), "页面")
    _color(background, "background")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= MAX_PAGE_NODES:
        raise ValueError(f"nodes 必须包含 1 至 {MAX_PAGE_NODES} 项")
    result, identifiers = [], set()
    for index, item in enumerate(nodes):
        if not isinstance(item, dict):
            raise ValueError(f"nodes[{index}] 必须是对象")
        node = deepcopy(item)
        _identifier(node.get("id"), f"nodes[{index}].id")
        label = f"节点 {node['id']}"
        if node["id"] in identifiers:
            raise ValueError(f"重复节点 ID：{node['id']}")
        identifiers.add(node["id"])
        required = {"id", "kind", "x", "y", "width", "height"}
        if node.get("kind") == "image":
            required.add("asset_id")
            optional = set()
        elif node.get("kind") == "text":
            required |= {"text", "font_path", "font_size", "line_height", "align", "wrap", "overflow", "color"}
            optional = {"stroke_width", "stroke_color", "font_sha256"}
        else:
            raise ValueError(f"{label}.kind 必须是 image 或 text")
        if not required <= node.keys() or node.keys() - required - optional:
            raise ValueError(f"{label} 字段不符合 {node['kind']} 节点要求")
        for field in ("x", "y", "width", "height"):
            _integer(node[field], f"{label}.{field}", 1 if field in ("width", "height") else 0)
        if node["x"] + node["width"] > width or node["y"] + node["height"] > height:
            raise ValueError(f"{label} 的布局内容区域超出画布")
        if node["kind"] == "image":
            _identifier(node["asset_id"], f"{label}.asset_id")
        else:
            if not isinstance(node["text"], str) or not node["text"]:
                raise ValueError(f"{label}.text 必须是非空字符串")
            if any(ord(char) < 32 and char != "\n" for char in node["text"]):
                raise ValueError(f"{label}.text 含不支持的控制字符；只允许换行")
            if type(node["wrap"]) is not bool or node["align"] not in ("left", "center", "right"):
                raise ValueError(f"{label} 的 wrap 或 align 无效")
            if node["overflow"] not in ("error", "clip"):
                raise ValueError(f"{label}.overflow 必须明确为 error 或 clip")
            for field in ("font_size", "line_height"):
                _integer(node[field], f"{label}.{field}", 1)
            node.setdefault("stroke_width", 0)
            node.setdefault("stroke_color", "#000000")
            _integer(node["stroke_width"], f"{label}.stroke_width")
            _color(node["color"], f"{label}.color")
            _color(node["stroke_color"], f"{label}.stroke_color")
            node["font_path"], node["font_sha256"] = _font(node)
        result.append(node)
    return {"width": width, "height": height, "background": background, "nodes": result}


def image_geometry(record):
    # 独立图像使用整图等比显示，正式导出使用显式几何。
    return record.get("geometry", {"content_rect": {"x": 0, "y": 0, "width": record["width"],
                                                   "height": record["height"]}, "mode": "contain"})


def resolve_definition(state, definition):
    """按资产 ID 解析当前文件，保留构建输入的摘要。"""
    result = deepcopy(definition)
    verified = set()
    for node in result["nodes"]:
        if node["kind"] == "text":
            _font(node)
            continue
        record = state.get("images", {}).get(node["asset_id"])
        if record is None:
            raise ValueError(f"节点 {node['id']} 引用不存在的资产：{node['asset_id']}")
        if node["asset_id"] not in verified:
            verify_image_record(record)
            verified.add(node["asset_id"])
        geometry = image_geometry(record)
        try:
            patches = image_patches(geometry, (record["width"], record["height"]), (node["width"], node["height"]))
            _check_output_size(tuple(math.ceil(value) for value in patches["image_size"]), f"节点 {node['id']} 完整图像")
        except ValueError as error:
            raise ValueError(f"节点 {node['id']} 资产 {node['asset_id']}：{error}") from error
        node.update(source=record["path"], sha256=record["sha256"],
                    geometry=deepcopy(geometry), patch_plan=patches)
    return result


def definition_for_storage(definition, layout):
    result = deepcopy(definition)
    for node in result["nodes"]:
        if node["kind"] == "text":
            node["font_path"] = layout.relative(node["font_path"])
    return result
