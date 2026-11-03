"""SlimeBlock 项目的七层亮面按钮绘制程序示例。"""

import math
import re


ROLES = ["shadow", "outline", "side", "rim", "face", "lower_band", "gloss"]
_COLORS = {"face", "lower_band", "side", "outer_edge", "inner_edge", "gloss", "shadow"}
_GEOMETRY = {
    "radius", "outline", "inset", "depth", "rim_width", "lower_band_ratio",
    "gloss_width", "gloss_height", "gloss_inset_x", "gloss_inset_y",
    "shadow_offset", "shadow_opacity",
}


def _fields(value, required, optional, field):
    if not isinstance(value, dict):
        raise ValueError(f"{field} 必须是对象")
    missing = set(required) - value.keys()
    extra = value.keys() - set(required) - set(optional)
    if missing:
        raise ValueError(f"{field} 缺少必要字段：{', '.join(sorted(missing))}")
    if extra:
        raise ValueError(f"{field} 包含不支持的字段：{', '.join(sorted(map(str, extra)))}")


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 必须是非空字符串")


def _number(value, field, *, zero=False):
    if type(value) not in (int, float) or not math.isfinite(value) or (value < 0 if zero else value <= 0):
        raise ValueError(f"{field} 必须是{'非负' if zero else '正'}有限数值")


def _ratio(value, field, *, inclusive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or not (0 <= value <= 1 if inclusive else 0 < value < 1):
        raise ValueError(f"{field} 必须位于{'[0, 1]' if inclusive else '(0, 1)'}")


def _color(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        raise ValueError(f"{field} 必须是 #RRGGBB 颜色")


def _roundrect(x, y, width, height, radius):
    return {"kind": "path", "fill": "foreground", "commands": [
        ["M", x + radius, y], ["L", x + width - radius, y],
        ["Q", x + width, y, x + width, y + radius],
        ["L", x + width, y + height - radius],
        ["Q", x + width, y + height, x + width - radius, y + height],
        ["L", x + radius, y + height], ["Q", x, y + height, x, y + height - radius],
        ["L", x, y + radius], ["Q", x, y, x + radius, y], ["Z"],
    ]}


def _lower_band(x, y, width, height, radius, ratio):
    split = y + height * ratio
    bottom = y + height
    # 底角沿用面板半径；上角只使用底角上方的剩余高度，左右边不会回折。
    top_radius = min(radius, height * (1 - ratio) - radius)
    return {"kind": "path", "fill": "foreground", "commands": [
        ["M", x + top_radius, split], ["L", x + width - top_radius, split],
        ["Q", x + width, split, x + width, split + top_radius],
        ["L", x + width, bottom - radius],
        ["Q", x + width, bottom, x + width - radius, bottom],
        ["L", x + radius, bottom], ["Q", x, bottom, x, bottom - radius],
        ["L", x, split + top_radius], ["Q", x, split, x + top_radius, split], ["Z"],
    ]}


def _glint(x, y, width, height, mirror=False):
    def point(px, py):
        return [x + (1 - px if mirror else px) * width, y + py * height]

    return {"kind": "path", "fill": "foreground", "commands": [
        ["M", *point(0, 0.4)], ["Q", *point(0.05, 0), *point(0.35, 0)],
        ["L", *point(1, 0)], ["Q", *point(0.95, 0.6), *point(0.65, 1)],
        ["L", *point(0, 1)], ["Z"],
    ]}


def _scaled_stroke(stroke, scale):
    if stroke["kind"] == "path":
        return {**stroke, "commands": [[command[0], *[value * scale for value in command[1:]]]
                                      for command in stroke["commands"]]}
    return {**stroke, "rect": [value * scale for value in stroke["rect"]]}


def compile_button(component_id: str, spec: dict, parameters: dict) -> dict:
    """编译七个可独立编辑的按钮图层；width/height 含透明留边。

    封闭路径和椭圆使用 Krita 几何填色，边缘使用指定原生笔刷描边。
    高光覆盖项省略时保持色板颜色、完全不透明和原始高光尺寸。
    """
    _text(component_id, "component_id")
    _fields(spec, {"width", "height", "palette", "geometry", "scale"}, {"program_id", "overrides"}, "spec")
    _fields(parameters, {"brush", "palettes", "geometry_profiles"}, set(), "parameters")
    for field in ("width", "height"):
        if type(spec[field]) is not int or spec[field] <= 0:
            raise ValueError(f"spec.{field} 必须是正整数")
    _number(spec["scale"], "spec.scale")
    _text(spec["palette"], "spec.palette")
    _text(spec["geometry"], "spec.geometry")
    _text(parameters["brush"], "parameters.brush")
    for mapping in ("palettes", "geometry_profiles"):
        if not isinstance(parameters[mapping], dict) or not parameters[mapping]:
            raise ValueError(f"parameters.{mapping} 必须是非空对象")
    for name, palette in parameters["palettes"].items():
        _text(name, "parameters.palettes 的名称")
        _fields(palette, _COLORS, set(), f"parameters.palettes.{name}")
        for field in _COLORS:
            _color(palette[field], f"parameters.palettes.{name}.{field}")
    for name, profile in parameters["geometry_profiles"].items():
        _text(name, "parameters.geometry_profiles 的名称")
        _fields(profile, _GEOMETRY, set(), f"parameters.geometry_profiles.{name}")
        for field in _GEOMETRY - {"lower_band_ratio", "shadow_opacity"}:
            _number(profile[field], f"parameters.geometry_profiles.{name}.{field}",
                    zero=field in {"shadow_offset", "gloss_inset_x", "gloss_inset_y"})
        _ratio(profile["lower_band_ratio"], f"parameters.geometry_profiles.{name}.lower_band_ratio")
        _ratio(profile["shadow_opacity"], f"parameters.geometry_profiles.{name}.shadow_opacity", inclusive=True)
    if spec["palette"] not in parameters["palettes"]:
        raise ValueError(f"spec.palette 指定的色板不存在：{spec['palette']}")
    if spec["geometry"] not in parameters["geometry_profiles"]:
        raise ValueError(f"spec.geometry 指定的几何配置不存在：{spec['geometry']}")
    overrides = spec["overrides"] if "overrides" in spec else {}
    _fields(overrides, set(), {"gloss_color", "gloss_opacity", "gloss_scale"}, "spec.overrides")
    if "gloss_color" in overrides:
        _color(overrides["gloss_color"], "spec.overrides.gloss_color")
    if "gloss_opacity" in overrides:
        _ratio(overrides["gloss_opacity"], "spec.overrides.gloss_opacity", inclusive=True)
    if "gloss_scale" in overrides:
        _number(overrides["gloss_scale"], "spec.overrides.gloss_scale")

    g = parameters["geometry_profiles"][spec["geometry"]]
    palette = parameters["palettes"][spec["palette"]]
    width, height, scale = spec["width"], spec["height"], spec["scale"]
    radius, outline, inset, depth, rim_width = (g[field] for field in ("radius", "outline", "inset", "depth", "rim_width"))
    face_height = height - depth
    if radius <= inset or inset <= outline or rim_width >= inset - outline:
        raise ValueError("几何配置必须满足 radius > inset > outline，且 rim_width < inset - outline")
    if face_height <= 2 * radius or width <= 2 * radius:
        raise ValueError(f"组件 {component_id} 尺寸过小：width 和 height-depth 必须大于 radius 的两倍")
    fw, fh, fr = width - 2 * inset, face_height - 2 * inset, radius - inset
    if fh * (1 - g["lower_band_ratio"]) < fr:
        raise ValueError("lower_band_ratio 使下部色带高度小于面板圆角半径，请调整几何配置")

    gloss_scale = overrides.get("gloss_scale", 1)
    gw, gh = g["gloss_width"] * gloss_scale, g["gloss_height"] * gloss_scale
    gx, gy = g["gloss_inset_x"], g["gloss_inset_y"]
    gloss_brush = rim_width * gloss_scale
    # 两侧斜亮斑和小圆点全部位于面板内；尺寸不合法时直接拒绝，不裁切。
    extent = gx + gw + gh * 0.9
    if 2 * (extent + gloss_brush / 2) >= fw or gy - gloss_brush / 2 < 0 or gy + gh + gloss_brush / 2 >= fh * g["lower_band_ratio"]:
        raise ValueError(f"组件 {component_id} 无法容纳高光：请检查 gloss_width/gloss_height/gloss_inset_x/gloss_inset_y/gloss_scale")
    corner_x, corner_y = gx - gloss_brush / 2, gy - gloss_brush / 2
    if corner_x < 0 or (corner_x < fr and corner_y < fr and (fr - corner_x) ** 2 + (fr - corner_y) ** 2 > fr ** 2):
        raise ValueError(f"组件 {component_id} 的高光超出面板圆角范围，请调整 gloss_inset_x/gloss_inset_y")

    padding = math.ceil(g["shadow_offset"] + outline)
    x = y = padding
    fx, fy = x + inset, y + inset

    def paint(color, size, strokes, opacity=1):
        return {"brush": parameters["brush"], "color": color, "size": size * scale,
                "opacity": opacity, "strokes": [_scaled_stroke(stroke, scale) for stroke in strokes]}

    gloss_left = fx + gx
    gloss_right = fx + fw - gx - gw
    dot_size = gh * 0.3
    gloss_strokes = [
        _glint(gloss_left, fy + gy, gw, gh),
        _glint(gloss_right, fy + gy, gw, gh, mirror=True),
        {"kind": "ellipse", "rect": [gloss_left + gw + gh * 0.6, fy + gy + gh * 0.15, dot_size, dot_size], "fill": "foreground"},
        {"kind": "ellipse", "rect": [gloss_right - gh * 0.9, fy + gy + gh * 0.15, dot_size, dot_size], "fill": "foreground"},
    ]
    paints = {
        "shadow": [paint(palette["shadow"], outline, [_roundrect(x, y + g["shadow_offset"], width, height, radius)])],
        "outline": [paint(palette["outer_edge"], outline, [_roundrect(x, y, width, height, radius)])],
        "side": [paint(palette["side"], rim_width, [_roundrect(x + outline, y + outline, width - 2 * outline, height - 2 * outline, radius - outline)])],
        "rim": [paint(palette["inner_edge"], rim_width, [_roundrect(x + outline, y + outline, width - 2 * outline, face_height - 2 * outline, radius - outline)])],
        "face": [paint(palette["face"], rim_width, [_roundrect(fx, fy, fw, fh, fr)])],
        "lower_band": [paint(palette["lower_band"], rim_width, [_lower_band(fx, fy, fw, fh, fr, g["lower_band_ratio"])])],
        "gloss": [paint(overrides.get("gloss_color", palette["gloss"]), gloss_brush, gloss_strokes, overrides.get("gloss_opacity", 1))],
    }
    return {
        "width": math.ceil((width + 2 * padding) * scale),
        "height": math.ceil((height + 2 * padding) * scale),
        "roles": list(ROLES), "paint": paints,
        "properties": {role: {"opacity": math.floor(g["shadow_opacity"] * 255 + 0.5)} if role == "shadow" else {} for role in ROLES},
    }


def build(parameters: dict, spec: dict, component_id: str) -> dict:
    """把项目按钮规格转换为组件分组、七个绘画图层及原生绘制计划。"""
    compiled = compile_button(component_id, spec, parameters)
    return {
        "width": compiled["width"],
        "height": compiled["height"],
        "nodes": [
            {"key": "component", "name": component_id, "kind": "grouplayer", "parent": "root"},
            *[
                {"key": role, "name": f"{component_id}/{role}", "kind": "paintlayer",
                 "parent": "component", "properties": compiled["properties"][role]}
                for role in compiled["roles"]
            ],
        ],
        "paint": [
            {"node": role, **paint}
            for role in compiled["roles"]
            for paint in compiled["paint"][role]
        ],
    }
