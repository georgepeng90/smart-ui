"""执行项目自带的 Python 绘制程序，并在调用 Krita 前校验绘制计划。

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
    if not isinstance(value, dict):
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
    if not isinstance(value, str) or not value.strip():
        raise ProgramError(f"{path} 必须是非空字符串")


def _identifier(value, path):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ProgramError(f"{path} 必须是以字母或数字开头、仅包含字母、数字、点、下划线和连字符的标识")


def _vector(value, count, path, integer=False):
    if not isinstance(value, list) or len(value) != count:
        raise ProgramError(f"{path} 必须包含 {count} 个数值")
    for index, item in enumerate(value):
        _number(item, f"{path}[{index}]", integer=integer)


def _properties(properties, kind, path):
    booleans = ("visible", "locked", "inherit_alpha", "alpha_locked")
    _fields(properties, (), (*booleans, "opacity", "blending_mode"), path)
    for key, value in properties.items():
        if key in booleans and type(value) is not bool:
            raise ProgramError(f"{path}.{key} 必须是布尔值")
        if key == "alpha_locked" and kind != "paintlayer":
            raise ProgramError(f"{path}.alpha_locked 只适用于 paintlayer")
        if key == "opacity":
            _number(value, f"{path}.opacity", 0, 255, integer=True)
        if key == "blending_mode" and value not in ("normal", "multiply", "screen", "overlay"):
            raise ProgramError(f"{path}.blending_mode 只允许 normal / multiply / screen / overlay")


def _stroke(stroke, path):
    if not isinstance(stroke, dict):
        raise ProgramError(f"{path} 必须是对象")
    kind = stroke.get("kind")
    if kind == "line":
        _fields(stroke, ("kind", "from", "to"), ("pressure_from", "pressure_to"), path)
        for key in ("from", "to"):
            _vector(stroke[key], 2, f"{path}.{key}", integer=True)
        for key in ("pressure_from", "pressure_to"):
            stroke.setdefault(key, 1.0)
            _number(stroke[key], f"{path}.{key}", 0, 1)
        return
    if kind not in ("path", "rectangle", "ellipse"):
        raise ProgramError(f"{path}.kind 不支持：{kind}")
    _fields(stroke, ("kind", "commands" if kind == "path" else "rect"), ("fill",), path)
    stroke.setdefault("fill", "none")
    if stroke["fill"] not in ("none", "foreground"):
        raise ProgramError(f"{path}.fill 只允许 none 或 foreground")
    if kind != "path":
        _vector(stroke["rect"], 4, f"{path}.rect")
        for index in (2, 3):
            _number(stroke["rect"][index], f"{path}.rect[{index}]", 0.01)
        return
    commands = stroke["commands"]
    if not isinstance(commands, list) or not commands or not isinstance(commands[0], list) or not commands[0] or commands[0][0] != "M":
        raise ProgramError(f"{path}.commands 路径必须从 M 命令开始")
    counts = {"M": 2, "L": 2, "C": 6, "Q": 4, "Z": 0}
    for index, command in enumerate(commands):
        label = f"{path}.commands[{index}]"
        if not isinstance(command, list) or not command or not isinstance(command[0], str) or command[0] not in counts:
            raise ProgramError(f"{label} 是无效的路径命令")
        _vector(command[1:], counts[command[0]], label)


def validate_plan(plan: dict) -> dict:
    """严格预检并返回独立计划副本，补齐 bridge 的可选默认值。

    图层属性补齐 Krita 新节点的默认值，笔刷不透明度和线段压力默认 1.0，
    形状填充默认 none。显式默认值使局部重绘可以复位已删除的属性设置。
    locked 等属性由调用方在绘制完成后应用；这里允许最终图层被锁定。
    """
    _strict_json(plan, "plan")
    _fields(plan, ("width", "height", "nodes", "paint"), (), "plan")
    plan = deepcopy(plan)
    for key in ("width", "height"):
        _number(plan[key], f"plan.{key}", 1, 16384, integer=True)
    if not isinstance(plan["nodes"], list):
        raise ProgramError("plan.nodes 必须是数组")
    nodes, names = {}, set()
    for index, node in enumerate(plan["nodes"]):
        path = f"plan.nodes[{index}]"
        _fields(node, ("key", "name", "kind", "parent"), ("properties", "rects"), path)
        key, kind, parent = node["key"], node["kind"], node["parent"]
        _identifier(key, f"{path}.key")
        if key == "root" or key in nodes:
            raise ProgramError(f"{path}.key 重复或使用保留标识 root：{key}")
        _text(node["name"], f"{path}.name")
        if node["name"] in names:
            raise ProgramError(f"{path}.name 重复：{node['name']}")
        if kind not in ("paintlayer", "grouplayer", "transparencymask"):
            raise ProgramError(f"{path}.kind 不支持：{kind}")
        if not isinstance(parent, str) or (parent != "root" and parent not in nodes):
            raise ProgramError(f"{path}.parent 必须是 root 或此前声明的节点 key")
        parent_kind = "grouplayer" if parent == "root" else nodes[parent]
        if kind == "transparencymask":
            if parent == "root" or parent_kind not in ("paintlayer", "grouplayer"):
                raise ProgramError(f"{path}.parent：透明度蒙版必须挂在普通图层或分组下")
            if "rects" not in node or not isinstance(node["rects"], list):
                raise ProgramError(f"{path}.rects 必须是数组；空数组表示全透明")
            for rect_index, rect in enumerate(node["rects"]):
                label = f"{path}.rects[{rect_index}]"
                _vector(rect, 5, label, integer=True)
                _number(rect[2], label + ".width", 1, integer=True)
                _number(rect[3], label + ".height", 1, integer=True)
                _number(rect[4], label + ".alpha", 0, 255, integer=True)
        elif parent_kind != "grouplayer":
            raise ProgramError(f"{path}.parent：绘画图层和分组的父节点必须是分组")
        elif "rects" in node:
            raise ProgramError(f"{path}.rects 只适用于 transparencymask")
        node.setdefault("properties", {})
        _properties(node["properties"], kind, f"{path}.properties")
        defaults = {"visible": True, "locked": False, "opacity": 255,
                    "blending_mode": "normal", "inherit_alpha": False}
        if kind == "paintlayer":
            defaults["alpha_locked"] = False
        node["properties"] = defaults | node["properties"]
        nodes[key] = kind
        names.add(node["name"])
    if not isinstance(plan["paint"], list):
        raise ProgramError("plan.paint 必须是数组")
    for index, paint in enumerate(plan["paint"]):
        path = f"plan.paint[{index}]"
        _fields(paint, ("node", "brush", "color", "size", "strokes"), ("opacity",), path)
        if not isinstance(paint["node"], str) or nodes.get(paint["node"]) != "paintlayer":
            raise ProgramError(f"{path}.node 必须引用 paintlayer 的 key")
        _text(paint["brush"], f"{path}.brush")
        if not isinstance(paint["color"], str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", paint["color"]):
            raise ProgramError(f"{path}.color 必须使用 #RRGGBB")
        _number(paint["size"], f"{path}.size", 0.01, 10000)
        paint.setdefault("opacity", 1.0)
        _number(paint["opacity"], f"{path}.opacity", 0, 1)
        if not isinstance(paint["strokes"], list) or not paint["strokes"]:
            raise ProgramError(f"{path}.strokes 必须是非空数组")
        for stroke_index, stroke in enumerate(paint["strokes"]):
            _stroke(stroke, f"{path}.strokes[{stroke_index}]")
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
