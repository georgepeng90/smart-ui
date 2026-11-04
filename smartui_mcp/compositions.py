"""将明确坐标的组件、图像与文字组合为可追溯的静态预览。"""

from copy import deepcopy
from io import BytesIO
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unicodedata

from fontTools.ttLib import TTFont, TTLibError
from PIL import Image, ImageDraw, ImageFont

from .comparisons import _check_output_size, _open_verified_image
from .layout import ProjectLayout
from .references import _absolute_path, _box, _sha256, _source_rect
from .visual_checks import RUN_FIELDS


MAX_NODES = 64


def _integer(value, field, minimum=0, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{field} 必须是 {minimum} 到 {maximum if maximum is not None else '无上限'} 的整数")
    return value


def _string(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 必须是非空字符串")
    return value


def _color(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        raise ValueError(f"{field} 必须是 #RRGGBB 格式的纯色")
    return value.upper()


def _hash(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError(f"{field} 必须是有效的 SHA-256")


def _json_value(value, field):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if float('-inf') < value < float('inf'):
            return
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _json_value(item, f"{field}[{index}]")
        return
    elif isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for key, item in value.items():
            _json_value(item, f"{field}.{key}")
        return
    raise ValueError(f"{field} 必须是 JSON 数据，数值必须有限")


def _validate_node(node, index, canvas_size):
    field = f"nodes[{index - 1}]"
    if not isinstance(node, dict) or any(not isinstance(key, str) for key in node):
        raise ValueError(f"{field} 必须是使用字符串字段名的对象")
    common = {"id", "kind", "x", "y"}
    image_fields = {"source", "sha256", "width", "height", "region"}
    kind = node.get("kind")
    if kind in ("component", "image"):
        required = common | image_fields | ({"component_id", "run", "spec"} if kind == "component" else {"asset_id", "version"})
        optional = set()
    elif kind == "text":
        required = common | {"text", "font_path", "font_size", "color"}
        optional = {"stroke_width", "stroke_color"}
    else:
        raise ValueError(f"{field}.kind 必须是 component、image 或 text")
    if required - node.keys() or node.keys() - required - optional:
        raise ValueError(f"{field} 字段不符：缺少 {sorted(required - node.keys())}；未知 {sorted(node.keys() - required - optional)}")
    _string(node["id"], f"{field}.id")
    for key in ("x", "y"):
        _integer(node[key], f"{field}.{key}")
    if node["x"] >= canvas_size[0] or node["y"] >= canvas_size[1]:
        raise ValueError(f"{field} 的坐标必须位于画布内")
    result = deepcopy(node)
    if kind in ("component", "image"):
        _string(node["source"], f"{field}.source")
        _hash(node["sha256"], f"{field}.sha256")
        for key in ("width", "height"):
            _integer(node[key], f"{field}.{key}", 1)
        if node["x"] + node["width"] > canvas_size[0] or node["y"] + node["height"] > canvas_size[1]:
            raise ValueError(f"{field} 的目标矩形越出画布边界")
        if kind == "image":
            _string(node["asset_id"], f"{field}.asset_id")
            _integer(node["version"], f"{field}.version", 1)
        else:
            _string(node["component_id"], f"{field}.component_id")
            run = node["run"]
            if not isinstance(run, dict) or set(run) != set(RUN_FIELDS):
                raise ValueError(f"{field}.run 必须恰好包含 {RUN_FIELDS}")
            if run["status"] not in ("drawn", "verified"):
                raise ValueError(f"{field}.run 必须是绘制成功的记录")
            for key in ("run_id", "program_id"):
                _string(run[key], f"{field}.run.{key}")
            _integer(run["style_version"], f"{field}.run.style_version", 0)
            _integer(run["program_version"], f"{field}.run.program_version", 1)
            for key in ("spec_sha256", "program_sha256"):
                _hash(run[key], f"{field}.run.{key}")
            if not isinstance(node["spec"], dict):
                raise ValueError(f"{field}.spec 必须是对象")
            _json_value(node["spec"], f"{field}.spec")
    else:
        text = _string(node["text"], f"{field}.text")
        if any(unicodedata.category(char).startswith("C") or char in "\u2028\u2029" for char in text):
            raise ValueError(f"{field}.text 必须是单行文字，不能包含控制字符")
        _string(node["font_path"], f"{field}.font_path")
        _integer(node["font_size"], f"{field}.font_size", 1, 8192)
        result["color"] = _color(node["color"], f"{field}.color")
        result["stroke_width"] = _integer(node.get("stroke_width", 0), f"{field}.stroke_width", 0)
        result["stroke_color"] = _color(node.get("stroke_color", "#000000"), f"{field}.stroke_color")
    return result


def _draw_image(node, index, staging, overview):
    source = _absolute_path(node["source"], f"nodes[{index - 1}].source")
    with _open_verified_image(source, node["sha256"], f"节点 {node['id']} 的源图") as image:
        _check_output_size(image.size, f"节点 {node['id']} 的源图")
        size = list(image.size)
        rect = [0, 0, *size] if node["region"] is None else _source_rect(node["region"], size, size, "region")
        node["color_metadata"] = {"has_icc_profile": bool(image.info.get("icc_profile")),
                                  "gamma": image.info.get("gamma"), "srgb": image.info.get("srgb")}
        with image.crop(_box(rect)) as crop, crop.convert("RGBA") as snapshot:
            key = f"node-{index}"
            snapshot.save(staging / f"{key}.png", format="PNG")
            sw, sh = snapshot.size
            tw, th = node["width"], node["height"]
            if tw * sh <= th * sw:
                width, height = tw, max(1, (sh * tw * 2 + sw) // (sw * 2))
            else:
                width, height = max(1, (sw * th * 2 + sh) // (sh * 2)), th
            x, y = node["x"] + (tw - width) // 2, node["y"] + (th - height) // 2
            if snapshot.size == (width, height):
                overview.alpha_composite(snapshot, (x, y))
            else:
                with snapshot.resize((width, height), Image.Resampling.LANCZOS) as scaled:
                    overview.alpha_composite(scaled, (x, y))
    node.update(source=str(source), source_size=size, region_source_xywh=rect,
                snapshot_key=key, display_rect=[x, y, width, height])


def _draw_text(node, index, staging, overview):
    source = _absolute_path(node["font_path"], f"nodes[{index - 1}].font_path")
    if source.suffix.lower() not in (".ttf", ".otf"):
        raise ValueError(f"字体只支持 TTF 或 OTF 文件：{source}")
    data = source.read_bytes()
    try:
        with TTFont(BytesIO(data)) as font:
            cmap = font.getBestCmap() or {}
            missing = sorted({ord(char) for char in node["text"] if cmap.get(ord(char), ".notdef") == ".notdef"})
        if missing:
            raise ValueError("字体缺少必要字符：" + ", ".join(f"U+{code:04X}" for code in missing))
        font = ImageFont.truetype(BytesIO(data), node["font_size"], layout_engine=ImageFont.Layout.BASIC)
    except (TTLibError, OSError, ValueError) as error:
        raise ValueError(f"无法使用字体 {source}；{error}") from error
    draw = ImageDraw.Draw(overview)
    bbox = draw.textbbox((node["x"], node["y"]), node["text"], font=font, anchor="lt", stroke_width=node["stroke_width"])
    left, top, right, bottom = bbox
    if left < 0 or top < 0 or right > overview.width or bottom > overview.height:
        raise ValueError(f"文字节点 {node['id']} 的实际绘制范围越出画布边界：{bbox}")
    key = f"font-{index}"
    (staging / f"{key}{source.suffix.lower()}").write_bytes(data)
    draw.text((node["x"], node["y"]), node["text"], font=font, anchor="lt", fill=node["color"],
              stroke_width=node["stroke_width"], stroke_fill=node["stroke_color"])
    node.update(font_path=str(source), font_snapshot_key=key,
                font_sha256=_sha256(staging / f"{key}{source.suffix.lower()}"),
                display_rect=[left, top, right - left, bottom - top], anchor="lt", layout_engine="basic")


def build_composition(project_root: str, composition_id: str, width: int, height: int,
                      background: str, nodes: list[dict]) -> dict:
    """按列表顺序叠加，发布原像素与字体快照及组合图；所有返回路径均为绝对路径。"""
    root = _absolute_path(_string(project_root, "project_root"), "project_root")
    if not root.is_dir():
        raise ValueError(f"project_root 必须是目录：{root}")
    if not isinstance(composition_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", composition_id):
        raise ValueError("composition_id 必须以英文字母或数字开头，且只能包含英文字母、数字、下划线和连字符")
    _integer(width, "width", 1)
    _integer(height, "height", 1)
    _check_output_size((width, height), "组合画布")
    background = _color(background, "background")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= MAX_NODES:
        raise ValueError(f"nodes 必须是包含 1 到 {MAX_NODES} 项的列表")
    entries = [_validate_node(node, index, (width, height)) for index, node in enumerate(nodes, 1)]
    if len({node["id"] for node in entries}) != len(entries):
        raise ValueError("nodes 的 id 必须唯一")
    destination = root / ".smartui" / "compositions" / composition_id
    layout = ProjectLayout(root)
    layout._check_managed_path(destination)
    if destination.exists():
        raise FileExistsError(f"组合输出目录已存在，不能覆盖：{destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent) as temp:
        staging = Path(temp)
        with Image.new("RGBA", (width, height), background) as overview:
            for index, node in enumerate(entries, 1):
                if node["kind"] == "text":
                    _draw_text(node, index, staging, overview)
                else:
                    _draw_image(node, index, staging, overview)
            overview.save(staging / "overview.png", format="PNG")
        files = {path.stem: {"path": str(destination / path.name), "sha256": _sha256(path)} for path in staging.iterdir()}
        layout._check_managed_path(destination)
        staging.rename(destination)
    return {"id": composition_id, "width": width, "height": height, "background": background, "nodes": entries,
            "images": {key: info for key, info in files.items() if not key.startswith("font-")},
            "fonts": {key: info for key, info in files.items() if key.startswith("font-")},
            "color_processing": {"icc_conversion": False, "resampling": "lanczos", "alpha_composite": "source_over",
                                 "description": "保留源图颜色数值，不做 ICC 或 gamma 转换；在纯色背景上按顺序进行 alpha 合成。"}}
