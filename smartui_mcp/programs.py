"""执行项目自带的 Python 绘制程序，并在调用 PhotoCraft 前校验绘制计划。

项目程序实现 ``build(parameters, spec, component_id)``，返回 JSON 绘制计划。
独立进程提供运行时隔离和 60 秒超时，并非安全沙箱：程序以本机可信代码
运行，可以使用标准库、访问文件和网络。只应执行已信任的项目程序。
"""

from copy import deepcopy
import json
import math
import re
import subprocess
import sys


class ProgramError(ValueError):
    """项目绘制程序或其返回的计划不符合执行约定。"""


_RUNNER = r'''
import contextlib
import io
import json
import math
import sys
import traceback

def strict_json(value, path="plan"):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for index, item in enumerate(value):
            strict_json(item, f"{path}[{index}]")
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for key, item in value.items():
            strict_json(item, f"{path}.{key}")
        return
    raise ValueError(f"{path} 必须只包含标准 JSON 类型和有限数值")

try:
    request = json.load(sys.stdin)
    namespace = {"__name__": "__smartui_program__", "__file__": "<project-program>"}
    with contextlib.redirect_stdout(io.StringIO()):
        exec(compile(request["source"], "<project-program>", "exec"), namespace)
        build = namespace.get("build")
        if not callable(build):
            raise ValueError("绘制程序必须定义 build(parameters, spec, component_id)")
        plan = build(request["parameters"], request["spec"], request["component_id"])
        strict_json(plan)
        output = json.dumps(plan, ensure_ascii=False, allow_nan=False)
    sys.stdout.write(output)
except BaseException:
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)
'''


def validate_source(source: str) -> None:
    """只检查 Python 语法，不运行项目代码。"""
    if not isinstance(source, str) or not source.strip():
        raise ProgramError("绘制程序源码必须是非空字符串")
    try:
        compile(source, "<project-program>", "exec")
    except (SyntaxError, ValueError) as exc:
        raise ProgramError(f"绘制程序语法错误：{exc}") from exc


def _strict_json(value, path):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _strict_json(item, f"{path}[{index}]")
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for key, item in value.items():
            _strict_json(item, f"{path}.{key}")
        return
    raise ProgramError(f"{path} 必须只包含标准 JSON 类型和有限数值")


def _fields(value, required, optional, path):
    if type(value) is not dict:
        raise ProgramError(f"{path} 必须是 JSON 对象")
    missing = set(required) - value.keys()
    unknown = value.keys() - set(required) - set(optional)
    if missing or unknown:
        raise ProgramError(f"{path} 字段错误：缺少 {sorted(missing)}，未知 {sorted(unknown)}")


def _number(value, path, low=None, high=None, integer=False):
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or (integer and type(value) is not int):
        raise ProgramError(f"{path} 必须是有限{'整数' if integer else '数值'}")
    if (low is not None and value < low) or (high is not None and value > high):
        raise ProgramError(f"{path} 超出允许范围 [{low}, {high}]")


def _text(value, path):
    if type(value) is not str or not value.strip():
        raise ProgramError(f"{path} 必须是非空字符串")


def _identifier(value, path):
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ProgramError(f"{path} 必须是字母、数字、下划线、点或短横线组成的标识")


def _boolean(value, path):
    if type(value) is not bool:
        raise ProgramError(f"{path} 必须是布尔值")


def _choice(value, choices, path):
    if type(value) is not str or value not in choices:
        raise ProgramError(f"{path} 必须是 {', '.join(choices)} 之一")


def _vector(value, count, path, low=None, high=None):
    if type(value) is not list or len(value) != count:
        raise ProgramError(f"{path} 必须是包含 {count} 个数值的数组")
    for index, number in enumerate(value):
        _number(number, f"{path}[{index}]", low, high)


def _color(value, path):
    if type(value) is str and re.fullmatch(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?", value):
        return value.lower()
    if type(value) is list:
        _vector(value, 4, path, 0, 1)
        return value
    raise ProgramError(f"{path} 必须是 #RRGGBB、#RRGGBBAA 或四项 0..1 RGBA 数组")


BLENDS = ("Normal", "Multiply", "Screen", "Overlay", "Darken", "Lighten", "ColorDodge",
          "ColorBurn", "LinearDodge", "LinearBurn", "SoftLight", "HardLight", "VividLight",
          "LinearLight", "PinLight", "HardMix", "Difference", "Exclusion", "Subtract", "Divide",
          "Hue", "Saturation", "Color", "Luminosity", "Dissolve", "DarkerColor", "LighterColor", "PassThrough")
GRADIENT_STYLES = ("linear", "radial", "angle", "reflected", "diamond")


def _blend(value, path, group=False):
    _text(value, path)
    normalized = re.sub(r"[ _()-]", "", value).lower()
    match = next((item for item in BLENDS if item.lower() == normalized), None)
    if match is None or (match == "PassThrough" and not group):
        raise ProgramError(f"{path} 不支持的混合模式：{value}")
    return match


def _stops(value, path, opacity=False):
    if type(value) is not list or not 2 <= len(value) <= 256:
        raise ProgramError(f"{path} 必须包含 2..256 个渐变色标")
    previous = -1
    for index, stop in enumerate(value):
        if type(stop) is not list or len(stop) != 2:
            raise ProgramError(f"{path}[{index}] 必须是 [位置, 值]")
        _number(stop[0], f"{path}[{index}].位置", 0, 1)
        if stop[0] <= previous:
            raise ProgramError(f"{path} 位置必须严格递增")
        previous = stop[0]
        if opacity:
            _number(stop[1], f"{path}[{index}].透明度", 0, 1)
        else:
            stop[1] = _color(stop[1], f"{path}[{index}].颜色")


def _fill(value, path):
    if value is None:
        return None
    if type(value) in (str, list):
        return _color(value, path)
    if type(value) is dict and set(value) == {"solid"}:
        return _color(value["solid"], path + ".solid")
    _fields(value, ("gradient",), (), path)
    g = value["gradient"]
    _fields(g, ("stops",), ("angle", "scale", "style", "reverse", "opacityStops", "midpoints", "offset", "dither", "align"), path + ".gradient")
    defaults = {"angle": 90, "scale": 100, "style": "linear", "reverse": False,
                "opacityStops": [], "midpoints": [], "offset": [0, 0], "dither": False, "align": True}
    g = defaults | g
    _stops(g["stops"], path + ".gradient.stops")
    _choice(g["style"], GRADIENT_STYLES, path + ".gradient.style")
    _number(g["angle"], path + ".gradient.angle", -360, 360)
    _number(g["scale"], path + ".gradient.scale", 1, 1000)
    _vector(g["offset"], 2, path + ".gradient.offset", -1000, 1000)
    for key in ("reverse", "dither", "align"):
        _boolean(g[key], path + ".gradient." + key)
    if g["opacityStops"]:
        _stops(g["opacityStops"], path + ".gradient.opacityStops", opacity=True)
    elif type(g["opacityStops"]) is not list:
        raise ProgramError(path + ".gradient.opacityStops 必须是数组")
    if type(g["midpoints"]) is not list or (g["midpoints"] and len(g["midpoints"]) != len(g["stops"]) - 1):
        raise ProgramError(path + ".gradient.midpoints 数量必须与渐变区间一致，或为空数组")
    for midpoint in g["midpoints"]:
        _number(midpoint, path + ".gradient.midpoints", 0.05, 0.95)
    return {"gradient": g}


def _path(value, path):
    _fields(value, ("subpaths",), ("fillRule", "inverted"), path)
    value.setdefault("fillRule", "nonzero")
    value.setdefault("inverted", False)
    _choice(value["fillRule"], ("nonzero", "evenodd"), path + ".fillRule")
    _boolean(value["inverted"], path + ".inverted")
    if type(value["subpaths"]) is not list or not value["subpaths"]:
        raise ProgramError(path + ".subpaths 必须是非空数组")
    for i, subpath in enumerate(value["subpaths"]):
        label = f"{path}.subpaths[{i}]"
        _fields(subpath, ("knots",), ("closed", "op"), label)
        subpath.setdefault("closed", True)
        subpath.setdefault("op", "combine")
        _boolean(subpath["closed"], label + ".closed")
        _choice(subpath["op"], ("combine", "subtract", "intersect", "exclude"), label + ".op")
        if type(subpath["knots"]) is not list or len(subpath["knots"]) < 2:
            raise ProgramError(label + ".knots 至少需要两个点")
        for j, knot in enumerate(subpath["knots"]):
            kpath = f"{label}.knots[{j}]"
            if type(knot) is list:
                _vector(knot, 2, kpath, -300000, 300000)
            else:
                _fields(knot, ("anchor",), ("in", "out", "smooth"), kpath)
                knot.setdefault("in", list(knot["anchor"]) if type(knot["anchor"]) is list else knot["anchor"])
                knot.setdefault("out", deepcopy(knot["anchor"]))
                knot.setdefault("smooth", False)
                for key in ("anchor", "in", "out"):
                    _vector(knot[key], 2, kpath + "." + key, -300000, 300000)
                _boolean(knot["smooth"], kpath + ".smooth")
    return value


def _stroke(value, path):
    if value is None:
        return None
    _fields(value, ("width", "color"), ("opacity", "align", "cap", "join", "miterLimit", "dashes", "dashOffset"), path)
    value = {"opacity": 100, "align": "center", "cap": "butt", "join": "miter",
             "miterLimit": 10, "dashes": [], "dashOffset": 0} | value
    value["color"] = _fill(value["color"], path + ".color")
    if value["color"] is None:
        raise ProgramError(path + ".color 不能是 null；禁用描边应使用 stroke:null")
    for key, low, high in (("width", 0.01, 5000), ("opacity", 0, 100), ("miterLimit", 1, 1000), ("dashOffset", -10000, 10000)):
        _number(value[key], path + "." + key, low, high)
    for key, choices in (("align", ("inside", "center", "outside")), ("cap", ("butt", "round", "square")), ("join", ("miter", "round", "bevel"))):
        _choice(value[key], choices, path + "." + key)
    if type(value["dashes"]) is not list or len(value["dashes"]) > 32:
        raise ProgramError(path + ".dashes 必须是最多 32 项的数组")
    for number in value["dashes"]:
        _number(number, path + ".dashes", 0.001, 10000)
    return value


def _shape(value, path):
    _fields(value, ("kind",), ("rect", "radii", "sides", "starRatio", "cornerRadius", "from", "to", "weight", "arrowStart", "arrowEnd", "arrowWidth", "arrowLength", "arrowConcavity", "path", "fill", "stroke"), path)
    kind = value["kind"]
    _choice(kind, ("rect", "roundedRect", "ellipse", "polygon", "star", "line", "path"), path + ".kind")
    geometry = {"rect": {"rect"}, "roundedRect": {"rect", "radii"}, "ellipse": {"rect"},
                "polygon": {"rect", "sides", "cornerRadius"}, "star": {"rect", "sides", "starRatio", "cornerRadius"},
                "line": {"from", "to", "weight", "arrowStart", "arrowEnd", "arrowWidth", "arrowLength", "arrowConcavity"}, "path": {"path"}}[kind]
    extra = set(value) - geometry - {"kind", "fill", "stroke"}
    if extra:
        raise ProgramError(f"{path}: {kind} 不支持字段 {sorted(extra)}")
    if kind == "path":
        if "path" not in value:
            raise ProgramError(path + " 缺少 path")
        value["path"] = _path(value["path"], path + ".path")
    elif kind == "line":
        for key in ("from", "to"):
            if key not in value:
                raise ProgramError(path + " 缺少 " + key)
            _vector(value[key], 2, path + "." + key, -300000, 300000)
        if value["from"] == value["to"]:
            raise ProgramError(path + " 线段起终点不能相同")
        value = {"weight": 1, "arrowStart": False, "arrowEnd": False, "arrowWidth": 52, "arrowLength": 50, "arrowConcavity": 0} | value
        for key in ("arrowStart", "arrowEnd"):
            _boolean(value[key], path + "." + key)
        for key, low, high in (("weight", .01, 5000), ("arrowWidth", 0, 1000), ("arrowLength", 0, 1000), ("arrowConcavity", 0, 100)):
            _number(value[key], path + "." + key, low, high)
    else:
        if "rect" not in value:
            raise ProgramError(path + " 缺少 rect")
        _vector(value["rect"], 4, path + ".rect", -300000, 300000)
        for number in value["rect"][2:]:
            _number(number, path + ".rect 尺寸", .01, 300000)
        if kind == "roundedRect":
            value.setdefault("radii", 10)
            if type(value["radii"]) is list:
                _vector(value["radii"], 4, path + ".radii", 0, 300000)
            else:
                _number(value["radii"], path + ".radii", 0, 300000)
        if kind in ("polygon", "star"):
            value.setdefault("sides", 5)
            value.setdefault("cornerRadius", 0)
            _number(value["sides"], path + ".sides", 3, 100, integer=True)
            _number(value["cornerRadius"], path + ".cornerRadius", 0, 300000)
        if kind == "star":
            value.setdefault("starRatio", .5)
            _number(value["starRatio"], path + ".starRatio", .001, 1)
    value["fill"] = _fill(value.get("fill", "#000000"), path + ".fill")
    value["stroke"] = _stroke(value.get("stroke"), path + ".stroke")
    return value


EFFECT_DEFAULTS = {
    "dropShadow": {"color": "#000000", "opacity": 75, "blend": "Multiply", "angle": 120, "useGlobalLight": False, "distance": 5, "spread": 0, "size": 5, "contour": "Linear", "antiAlias": False, "noise": 0, "knocksOut": True},
    "innerShadow": {"color": "#000000", "opacity": 75, "blend": "Multiply", "angle": 120, "distance": 5, "choke": 0, "size": 5, "contour": "Linear", "antiAlias": False, "noise": 0},
    "outerGlow": {"color": "#ffffff", "opacity": 75, "blend": "Screen", "technique": "softer", "spread": 0, "size": 5, "range": 50, "contour": "Linear", "antiAlias": False, "noise": 0},
    "innerGlow": {"color": "#ffffff", "opacity": 75, "blend": "Screen", "technique": "softer", "source": "edge", "choke": 0, "size": 5, "range": 50, "contour": "Linear", "antiAlias": False, "noise": 0},
    "stroke": {"size": 1, "position": "outside", "color": "#000000", "opacity": 100, "blend": "Normal"},
    "colorOverlay": {"color": "#000000", "opacity": 100, "blend": "Normal"},
    "gradientOverlay": {"from": "#000000", "to": "#ffffff", "style": "linear", "angle": 90, "scale": 100, "reverse": False, "opacity": 100, "blend": "Normal"},
    "bevelEmboss": {"style": "inner", "technique": "smooth", "contour": False, "contourName": "Linear", "contourRange": 50, "contourAntiAlias": False, "glossContour": "Linear", "glossAntiAlias": False, "depth": 100, "direction": "up", "size": 5, "soften": 0, "angle": 120, "altitude": 30},
    "satin": {"color": "#000000", "opacity": 50, "blend": "Multiply", "angle": 19, "distance": 11, "size": 14, "contour": "Linear", "antiAlias": True, "invert": True},
}
CONTOURS = ("Linear", "Cone", "Cone - Inverted", "Cove - Deep", "Cove - Shallow", "Gaussian", "Half Round", "Ring", "Ring - Double", "Rolling Slope - Descending", "Rounded Steps", "Sawtooth 1")


def _effects(value, path):
    if type(value) is not list or len(value) > 32:
        raise ProgramError(path + " 必须是最多 32 项的效果数组")
    for index, effect in enumerate(value):
        label = f"{path}[{index}]"
        _fields(effect, ("kind", "params"), (), label)
        _choice(effect["kind"], tuple(EFFECT_DEFAULTS), label + ".kind")
        defaults = EFFECT_DEFAULTS[effect["kind"]]
        _fields(effect["params"], (), tuple(defaults), label + ".params")
        p = deepcopy(defaults) | effect["params"]
        for key, item in p.items():
            loc = label + ".params." + key
            if key in ("color", "from", "to"):
                color = _color(item, loc)
                if (type(color) is list and color[3] != 1) or (type(color) is str and len(color) == 9 and not color.endswith("ff")):
                    raise ProgramError(loc + " 必须是不透明颜色；请使用效果 opacity 设置透明度")
                p[key] = color
            elif key == "blend":
                p[key] = _blend(item, loc)
            elif type(defaults[key]) is bool:
                _boolean(item, loc)
            elif key in ("contour", "contourName", "glossContour"):
                _choice(item, CONTOURS, loc)
            elif key == "style":
                _choice(item, ("inner", "outer", "emboss", "pillow", "stroke") if effect["kind"] == "bevelEmboss" else GRADIENT_STYLES, loc)
            elif key == "technique":
                _choice(item, ("smooth", "chiselHard", "chiselSoft") if effect["kind"] == "bevelEmboss" else ("softer", "precise"), loc)
            elif key == "position":
                _choice(item, ("inside", "outside", "center"), loc)
            elif key == "direction":
                _choice(item, ("up", "down"), loc)
            elif key == "source":
                _choice(item, ("edge", "center"), loc)
            else:
                bounds = {"angle": (-360, 360), "altitude": (0, 90), "size": (0, 1000), "soften": (0, 1000), "distance": (0, 1000), "depth": (1, 1000), "scale": (10, 150), "range": (1, 100), "contourRange": (1, 100)}
                _number(item, loc, *bounds.get(key, (0, 100)))
        effect["params"] = p
    return value


def _pixel_strokes(value, path):
    if type(value) is not list:
        raise ProgramError(path + " 必须是数组；空数组表示空白像素层")
    for index, stroke in enumerate(value):
        label = f"{path}[{index}]"
        _fields(stroke, ("color", "size", "opacity", "points"), ("brush", "seed"), label)
        stroke["color"] = _color(stroke["color"], label + ".color")
        _number(stroke["size"], label + ".size", .5, 5000)
        _number(stroke["opacity"], label + ".opacity", 0, 1)
        stroke.setdefault("seed", 0)
        _number(stroke["seed"], label + ".seed", 0, 2**64 - 1, integer=True)
        if type(stroke["points"]) is not list or not stroke["points"]:
            raise ProgramError(label + ".points 必须是非空数组")
        for j, point in enumerate(stroke["points"]):
            _vector(point, 3, f"{label}.points[{j}]")
            for xy in point[:2]:
                _number(xy, label + ".points 坐标", -300000, 300000)
            _number(point[2], label + ".points 压力", 0, 1)
        brush = stroke.get("brush", {})
        defaults = {"hardness": 1, "spacing": .1, "flow": 1, "pressureSize": False, "pressureOpacity": False, "angle": 0, "roundness": 1, "aliased": False}
        _fields(brush, (), tuple(defaults), label + ".brush")
        brush = defaults | brush
        for key, item in brush.items():
            loc = label + ".brush." + key
            if type(defaults[key]) is bool:
                _boolean(item, loc)
            else:
                bounds = {"angle": (-360, 360), "roundness": (.01, 1), "spacing": (.01, 10)}
                _number(item, loc, *bounds.get(key, (0, 1)))
        stroke["brush"] = brush


def validate_plan(plan):
    """校验当前原生绘图计划，并返回独立、已显式补全合法默认值的副本。"""
    _strict_json(plan, "plan")
    _fields(plan, ("schema_version", "width", "height", "nodes"), (), "plan")
    plan = deepcopy(plan)
    _number(plan["schema_version"], "plan.schema_version", 1, 1, integer=True)
    for key in ("width", "height"):
        _number(plan[key], "plan." + key, 1, 16384, integer=True)
    if type(plan["nodes"]) is not list or not plan["nodes"]:
        raise ProgramError("plan.nodes 必须是非空数组")
    nodes, names = {}, set()
    for index, node in enumerate(plan["nodes"]):
        label = f"plan.nodes[{index}]"
        _fields(node, ("key", "name", "kind", "parent"), ("shape", "strokes", "properties", "effects", "mask"), label)
        key, kind, parent = node["key"], node["kind"], node["parent"]
        _identifier(key, label + ".key")
        if key == "root" or key in nodes:
            raise ProgramError(label + ".key 重复或使用保留标识 root")
        _text(node["name"], label + ".name")
        if node["name"] in names:
            raise ProgramError(label + ".name 重复")
        _choice(kind, ("shape", "pixel", "group"), label + ".kind")
        if type(parent) is not str or (parent != "root" and nodes.get(parent) != "group"):
            raise ProgramError(label + ".parent 必须是 root 或此前声明的 group key")
        if kind == "shape":
            if "shape" not in node or "strokes" in node:
                raise ProgramError(label + " shape 节点必须有 shape，不能有 strokes")
            node["shape"] = _shape(node["shape"], label + ".shape")
        elif kind == "pixel":
            if "strokes" not in node or "shape" in node:
                raise ProgramError(label + " pixel 节点必须有 strokes，不能有 shape")
            _pixel_strokes(node["strokes"], label + ".strokes")
        elif "shape" in node or "strokes" in node:
            raise ProgramError(label + " group 节点不能有 shape 或 strokes")
        props = node.get("properties", {})
        _fields(props, (), ("visible", "opacity", "blend"), label + ".properties")
        props = {"visible": True, "opacity": 1, "blend": "PassThrough" if kind == "group" else "Normal"} | props
        _boolean(props["visible"], label + ".properties.visible")
        _number(props["opacity"], label + ".properties.opacity", 0, 1)
        props["blend"] = _blend(props["blend"], label + ".properties.blend", group=kind == "group")
        node["properties"] = props
        node["effects"] = _effects(node.get("effects", []), label + ".effects")
        mask = node.get("mask")
        if mask is not None:
            if kind == "shape":
                raise ProgramError(label + " shape 自身路径已是矢量蒙版；请用带 mask 的父 group 裁剪 shape")
            _fields(mask, ("path",), ("enabled", "linked", "density", "feather"), label + ".mask")
            mask = {"enabled": True, "linked": True, "density": 100, "feather": 0} | mask
            mask["path"] = _path(mask["path"], label + ".mask.path")
            for field in ("enabled", "linked"):
                _boolean(mask[field], label + ".mask." + field)
            _number(mask["density"], label + ".mask.density", 0, 100)
            _number(mask["feather"], label + ".mask.feather", 0, 1000)
        node["mask"] = mask
        nodes[key] = kind
        names.add(node["name"])
    return plan


def run_program(source: str, parameters: dict, spec: dict, component_id: str) -> dict:
    """用当前 Python 的独立进程执行可信项目程序，60 秒内返回经过校验的计划。"""
    validate_source(source)
    for label, value in (("parameters", parameters), ("spec", spec)):
        if not isinstance(value, dict):
            raise ProgramError(f"{label} 必须是 JSON 对象")
        _strict_json(value, label)
    _identifier(component_id, "component_id")
    payload = json.dumps({"source": source, "parameters": parameters, "spec": spec,
                          "component_id": component_id}, ensure_ascii=False, allow_nan=False)
    try:
        result = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", _RUNNER],
                                input=payload, capture_output=True, text=True,
                                encoding="utf-8", timeout=60, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ProgramError("项目绘制程序执行超时：超过 60 秒") from exc
    except OSError as exc:
        raise ProgramError(f"无法启动项目绘制程序：{exc}") from exc
    if result.returncode:
        raise ProgramError(f"项目绘制程序执行失败（退出码 {result.returncode}）：{result.stderr.strip()}")
    try:
        plan = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise ProgramError(f"项目绘制程序未返回有效 JSON：{exc}") from exc
    return validate_plan(plan)
