"""校验当前页面定义，解析资产和项目文字样式。"""

from copy import deepcopy
import math
import re

from . import typography
from .comparisons import _check_output_size
from .image_assets import verify_image_record
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


def validate_definition(width, height, background, nodes):
    """校验页面结构与节点字段，保留文案和布局信息。"""
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
        elif node.get("kind") == "text":
            required |= {"text", "text_style_id", "align", "wrap", "overflow"}
        else:
            raise ValueError(f"{label}.kind 必须是 image 或 text")
        missing, extra = sorted(required - node.keys()), sorted(node.keys() - required, key=str)
        if missing or extra:
            raise ValueError(f"{label} 字段不符合 {node['kind']} 节点要求，缺少 {missing}，多余 {extra}")
        for field in ("x", "y", "width", "height"):
            _integer(node[field], f"{label}.{field}", 1 if field in ("width", "height") else 0)
        if node["x"] + node["width"] > width or node["y"] + node["height"] > height:
            raise ValueError(f"{label} 的布局内容区域超出画布")
        if node["kind"] == "image":
            _identifier(node["asset_id"], f"{label}.asset_id")
        else:
            _identifier(node["text_style_id"], f"{label}.text_style_id")
            if not isinstance(node["text"], str) or not node["text"]:
                raise ValueError(f"{label}.text 必须是非空字符串")
            if any(ord(char) < 32 and char != "\n" for char in node["text"]):
                raise ValueError(f"{label}.text 含不支持的控制字符；只允许换行")
            if type(node["wrap"]) is not bool or node["align"] not in ("left", "center", "right"):
                raise ValueError(f"{label} 的 wrap 或 align 无效")
            if node["overflow"] not in ("error", "clip"):
                raise ValueError(f"{label}.overflow 必须明确为 error 或 clip")
        result.append(node)
    return {"width": width, "height": height, "background": background, "nodes": result}


def image_geometry(record):
    # 独立图像使用整图等比显示，正式导出使用显式几何。
    return record.get("geometry", {"content_rect": {"x": 0, "y": 0, "width": record["width"],
                                                   "height": record["height"]}, "mode": "contain"})


def resolve_definition(state, definition):
    """解析当前资产和文字样式，校验构建输入使用的真实文件。"""
    result = deepcopy(definition)
    verified = set()
    for node in result["nodes"]:
        if node["kind"] == "text":
            label = f"文字节点 {node['id']}"
            style_id = node["text_style_id"]
            spec = state["typography"]
            if spec is None:
                raise ValueError(f"{label} 引用样式 {style_id}，请先保存项目文字规范")
            if style_id not in spec["styles"]:
                raise ValueError(f"{label} 引用不存在的文字样式：{style_id}")
            style = typography.resolve_style(spec, style_id)
            typography.validate_font(style["font_path"], style["font_sha256"], text=node["text"],
                                     label=f"{label} 的样式 {style_id}")
            node.update(style, text_style_sha256=typography.style_digest(spec, style_id))
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
