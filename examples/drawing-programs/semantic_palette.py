"""用共用颜色用途制作操作背景及状态变体的合成示例。

将 example_parameters(已核对的 Krita 笔刷名称) 的结果存为风格参数。
组件规格示例：{"program_id": "semantic-palette", "theme": "lagoon",
              "state": "available", "width": 180, "height": 52}。
两个主题和三个状态仅用于演示参数引用与几何复用。
"""

import re


def example_parameters(brush):
    """返回可直接保存为项目风格参数的示例数据。"""
    return {
        "brush": brush,
        "themes": {
            "lagoon": {
                "outline": "#173C42",
                "action": "#2C817C",
                "inactive": "#56696A",
                "selection": "#F0F9B6",
            },
            "ember": {
                "outline": "#522D23",
                "action": "#B35331",
                "inactive": "#74645F",
                "selection": "#FFF0C6",
            },
        },
        "states": {
            "available": {"surface": "action"},
            "selected": {"surface": "action", "marker": "selection"},
            "disabled": {"surface": "inactive"},
        },
    }


def _required(mapping, key, path, expected_type):
    field_path = f"{path}.{key}"
    if key not in mapping:
        raise ValueError(f"缺少配置项 {field_path}")
    value = mapping[key]
    if type(value) is not expected_type:
        raise ValueError(f"{field_path} 必须为 {expected_type.__name__}")
    if expected_type is str and not value.strip():
        raise ValueError(f"{field_path} 必须为非空字符串")
    return value


def _rounded_rect(x, y, width, height, radius):
    right, bottom = x + width, y + height
    return {
        "kind": "path",
        "fill": "foreground",
        "commands": [
            ["M", x + radius, y],
            ["L", right - radius, y],
            ["Q", right, y, right, y + radius],
            ["L", right, bottom - radius],
            ["Q", right, bottom, right - radius, bottom],
            ["L", x + radius, bottom],
            ["Q", x, bottom, x, bottom - radius],
            ["L", x, y + radius],
            ["Q", x, y, x + radius, y],
            ["Z"],
        ],
    }


def build(parameters, spec, component_id):
    width = _required(spec, "width", "spec", int)
    height = _required(spec, "height", "spec", int)
    if not 40 <= width <= 16384 or not 24 <= height <= 16384:
        raise ValueError("spec.width 必须在 40–16384，spec.height 必须在 24–16384，以容纳操作背景与状态标记")

    brush = _required(parameters, "brush", "parameters", str)
    theme_id = _required(spec, "theme", "spec", str)
    themes = _required(parameters, "themes", "parameters", dict)
    theme = _required(themes, theme_id, "parameters.themes", dict)
    theme_path = f"parameters.themes.{theme_id}"
    state_id = _required(spec, "state", "spec", str)
    states = _required(parameters, "states", "parameters", dict)
    state = _required(states, state_id, "parameters.states", dict)
    state_path = f"parameters.states.{state_id}"
    unknown = state.keys() - {"surface", "marker"}
    if unknown:
        raise ValueError(f"{state_path} 不支持的字段：{sorted(unknown)}")

    roles = {"outline": "outline", "surface": _required(state, "surface", state_path, str)}
    if "marker" in state:
        roles["marker"] = _required(state, "marker", state_path, str)

    geometry = {
        "outline": _rounded_rect(2, 2, width - 4, height - 4, 8),
        "surface": _rounded_rect(5, 5, width - 10, height - 10, 5),
        "marker": {"kind": "ellipse", "rect": [width - 22, (height - 8) / 2, 8, 8], "fill": "foreground"},
    }
    nodes = [
        {"key": key, "name": f"{component_id}/{key}", "kind": "paintlayer", "parent": "root"}
        for key in geometry
    ]
    paint = []
    for node, role in roles.items():
        color = _required(theme, role, theme_path, str)
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise ValueError(f"{theme_path}.{role} 必须使用 #RRGGBB")
        paint.append({"node": node, "brush": brush, "color": color, "size": 1, "strokes": [geometry[node]]})
    return {"width": width, "height": height, "nodes": nodes, "paint": paint}
