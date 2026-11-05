"""项目文字规范、字体身份及页面文字样式解析。"""

from copy import deepcopy
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import re
from struct import error as StructError

from fontTools.ttLib import TTFont, TTLibError
from PIL import ImageFont

from .image_assets import _file_path
from .layout import ProjectLayout
from .references import _size, _source_rect


_STYLE_FIELDS = {"name", "usage", "size_id", "font_path", "line_height",
                 "letter_spacing", "fill", "stroke", "shadow", "evidence"}
_RENDER_FIELDS = ("font_path", "font_sha256", "line_height", "letter_spacing", "fill", "stroke", "shadow")


def _fields(value, required, label, optional=()):
    if not isinstance(value, dict):
        raise ValueError(f"{label} 必须是对象")
    missing = required - value.keys()
    unknown = value.keys() - required - set(optional)
    if missing or unknown:
        raise ValueError(f"{label} 字段不符合要求，缺少 {sorted(missing)}，未知 {sorted(map(repr, unknown))}")


def _string(value, label, *, allow_empty=False):
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{label} 必须是{'字符串' if allow_empty else '非空字符串'}")


def _identifier(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ValueError(f"{label} 必须是以英文字母或数字开头的标识：{value!r}")


def _identifiers(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} 必须是对象")
    seen = set()
    for identifier in value:
        _identifier(identifier, f"{label} 的 ID")
        if identifier.casefold() in seen:
            raise ValueError(f"{label} 含有仅大小写不同的重复 ID：{identifier}")
        seen.add(identifier.casefold())


def _positive_integer(value, label):
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} 必须是正整数")


def _number(value, label, *, minimum=-math.inf, maximum=math.inf):
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or not minimum <= value <= maximum:
        raise ValueError(f"{label} 必须是范围 [{minimum}, {maximum}] 内的有限数值")
    return int(value) if isinstance(value, float) and value.is_integer() else value


def _appearance(value, label, **limits):
    _fields(value, {"color", "opacity", *limits}, label)
    color = value["color"]
    if not isinstance(color, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        raise ValueError(f"{label}.color 必须是 #RRGGBB 颜色")
    value["color"] = color.upper()
    value["opacity"] = _number(value["opacity"], f"{label}.opacity", minimum=0, maximum=1)
    for field, minimum in limits.items():
        value[field] = _number(value[field], f"{label}.{field}", minimum=minimum)


def _digest(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9A-Fa-f]{64}", value):
        raise ValueError(f"{label} 必须是 64 位十六进制 SHA-256 摘要")
    return value.lower()


def _font_path(value, label):
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError(f"{label} 必须是绝对路径：{value!r}")
    path = Path(value)
    if path.suffix.lower() not in (".ttf", ".otf"):
        raise ValueError(f"{label} 只支持 TTF/OTF 字体：{path}")
    return path


def validate_font(path, expected_sha256=None, *, text=None, label="字体"):
    """以同一份文件字节核对字体摘要、格式和文本所需字符。"""
    if text is None:
        text = ""
    _string(text, f"{label}.text", allow_empty=True)
    path = _font_path(str(path) if isinstance(path, Path) else path, f"{label}.font_path")
    try:
        path = _file_path(str(path), f"{label}.font_path")
        data = path.read_bytes()
    except OSError as error:
        raise ValueError(f"{label} 无法读取字体：{path}；{error}") from error
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None and _digest(expected_sha256, f"{label}.font_sha256") != digest:
        raise ValueError(f"{label} 字体 SHA-256 不符：{path}。请恢复文件或明确更新项目文字规范")
    try:
        ImageFont.truetype(BytesIO(data), 16, layout_engine=ImageFont.Layout.BASIC)
        with TTFont(BytesIO(data)) as font:
            cmap = font.getBestCmap()
            if font.flavor is not None or not cmap:
                raise TTLibError("需要包含 Unicode 字符映射的 TTF/OTF 字体")
            glyph_zero = font.getGlyphOrder()[0]
            missing = sorted({char for char in text if char != "\n" and
                              cmap.get(ord(char), glyph_zero) == glyph_zero})
    except (OSError, TTLibError, ValueError, KeyError, IndexError, AssertionError, StructError) as error:
        raise ValueError(f"{label} 无法读取字体：{path}；{error}") from error
    if missing:
        detail = ", ".join(f"{char!r} U+{ord(char):04X}" for char in missing)
        raise ValueError(f"{label} 字体 {path} 缺少字符：{detail}")
    return str(path), digest


def _validate_evidence(evidence, references, label):
    if not isinstance(evidence, list):
        raise ValueError(f"{label} 必须是列表")
    for index, entry in enumerate(evidence):
        location = f"{label}[{index}]"
        _fields(entry, {"reference_id", "region", "coordinate_size", "observation", "decision"}, location)
        identifier = entry["reference_id"]
        _identifier(identifier, f"{location}.reference_id")
        if identifier not in references:
            raise ValueError(f"{location}.reference_id 引用不存在的参考图：{identifier}")
        coordinates = _size(entry["coordinate_size"], f"{location}.coordinate_size")
        _source_rect(entry["region"], coordinates, coordinates, f"{location}.region")
        for field in ("observation", "decision"):
            _string(entry[field], f"{location}.{field}")


def validate_typography(value, project_root, references, *, verify_fonts=True):
    """校验并复制完整文字规范，更新时登记字体的当前内容摘要。"""
    result = deepcopy(value)
    _fields(result, {"description", "design_size", "size_scale", "styles"}, "typography")
    _string(result["description"], "typography.description", allow_empty=True)
    design_size = result["design_size"]
    _fields(design_size, {"width", "height"}, "typography.design_size")
    for field in ("width", "height"):
        _positive_integer(design_size[field], f"typography.design_size.{field}")
    size_scale = result["size_scale"]
    _identifiers(size_scale, "typography.size_scale")
    for identifier, size in size_scale.items():
        _positive_integer(size, f"typography.size_scale.{identifier}")

    styles = result["styles"]
    _identifiers(styles, "typography.styles")
    layout = ProjectLayout(project_root)
    for identifier, style in styles.items():
        label = f"typography.styles.{identifier}"
        if verify_fonts:
            _fields(style, _STYLE_FIELDS, label, {"font_sha256"})
        else:
            _fields(style, _STYLE_FIELDS | {"font_sha256"}, label)
        for field in ("name", "usage"):
            _string(style[field], f"{label}.{field}")
        _identifier(style["size_id"], f"{label}.size_id")
        if style["size_id"] not in size_scale:
            raise ValueError(f"{label}.size_id 引用不存在的字号档位：{style['size_id']}")
        _positive_integer(style["line_height"], f"{label}.line_height")
        style["letter_spacing"] = _number(style["letter_spacing"], f"{label}.letter_spacing")
        _appearance(style["fill"], f"{label}.fill")
        if style["stroke"] is not None:
            _appearance(style["stroke"], f"{label}.stroke", width=0)
        if style["shadow"] is not None:
            _appearance(style["shadow"], f"{label}.shadow", offset_x=-math.inf, offset_y=-math.inf, blur=0)
        _validate_evidence(style["evidence"], references, f"{label}.evidence")

        path_field = f"{label}.font_path"
        path = _font_path(style["font_path"], path_field)
        if not path.is_relative_to(layout.root) or ".." in path.parts:
            raise ValueError(f"{path_field} 必须位于工程内且不能包含 ..：{path}")
        layout._check_managed_path(path)
        if path.is_relative_to(layout.root / ".smartui"):
            raise ValueError(f"{path_field} 不能依赖 .smartui/ 中的本机文件：{path}")
        if verify_fonts:
            digest = _digest(style["font_sha256"], f"{label}.font_sha256") if "font_sha256" in style else None
            style["font_path"], style["font_sha256"] = validate_font(path, digest, label=label)
        else:
            style["font_path"] = str(path)
            style["font_sha256"] = _digest(style["font_sha256"], f"{label}.font_sha256")
    return result


def resolve_style(typography, style_id):
    """解析角色的实际字号及渲染参数，无需访问字体文件。"""
    _identifier(style_id, "text_style_id")
    if typography is None or style_id not in typography["styles"]:
        raise ValueError(f"text_style_id 引用不存在的文字样式：{style_id}")
    style = typography["styles"][style_id]
    result = {field: deepcopy(style[field]) for field in _RENDER_FIELDS}
    result["font_size"] = typography["size_scale"][style["size_id"]]
    return result


def style_digest(typography, style_id):
    """只摘要页面渲染所需的值，字体以内容摘要标识。"""
    fields = resolve_style(typography, style_id)
    del fields["font_path"]
    payload = json.dumps(fields, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _paint_description(effect):
    return f"`{effect['color']}`，不透明度 {effect['opacity']}"


def render_typography(project_name, typography):
    """生成 UTF-8 受管文档。持久化时使用工程相对的字体路径。"""
    lines = [f"# {project_name}的文字设计规范", ""]
    if typography is None:
        lines.append("尚未定义文字设计规范。")
        return ("\n".join(lines) + "\n").encode("utf-8")

    size = typography["design_size"]
    lines.extend([f"设计基准为 {size['width']} × {size['height']} px。", ""])
    if typography["description"]:
        lines.extend([typography["description"], ""])
    lines.extend(["## 字号档位", "", "| 档位 ID | 字号（px） |", "| --- | ---: |"])
    for identifier, pixels in sorted(typography["size_scale"].items()):
        lines.append(f"| `{identifier}` | {pixels} |")
    lines.extend(["", "## 角色样式"])
    if not typography["styles"]:
        lines.extend(["", "尚未定义角色样式。"])
    for identifier, style in sorted(typography["styles"].items()):
        values = resolve_style(typography, identifier)
        stroke, shadow = values["stroke"], values["shadow"]
        stroke_details = "无描边" if stroke is None else (
            f"描边使用 {_paint_description(stroke)}，总宽度 {stroke['width']} px"
        )
        shadow_details = "无投影" if shadow is None else (
            f"投影使用 {_paint_description(shadow)}，偏移 ({shadow['offset_x']}, {shadow['offset_y']}) px，"
            f"模糊 {shadow['blur']} px"
        )
        lines.extend([
            "", f"### {style['name']}（`{identifier}`）", "",
            style["usage"], "",
            f"- 字号为 {values['font_size']} px，使用 `{style['size_id']}` 档位。",
            f"- 字体文件为 `{values['font_path']}`，SHA-256 为 `{values['font_sha256']}`。",
            f"- 行高为 {values['line_height']} px，字距为 {values['letter_spacing']} px。",
            f"- 填色使用 {_paint_description(values['fill'])}。",
            f"- {stroke_details}。",
            f"- {shadow_details}。",
        ])
        for item in style["evidence"]:
            lines.extend([
                "", f"参考图 `{item['reference_id']}`，区域为 `{item['region']}`，"
                f"坐标尺寸为 `{item['coordinate_size']}`。",
                "", f"- 观察结果为 {item['observation']}", f"- 设计决定为 {item['decision']}",
            ])
    return ("\n".join(lines) + "\n").encode("utf-8")
