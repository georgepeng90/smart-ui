"""共用颜色用途、原生形状和组件状态的通用示例。"""


def example_parameters():
    return {
        "themes": {
            "lagoon": {"outline": "#173C42", "action": "#2C817C", "inactive": "#56696A", "selection": "#F0F9B6"},
            "ember": {"outline": "#522D23", "action": "#B35331", "inactive": "#74645F", "selection": "#FFF0C6"},
        },
        "states": {"available": {"surface": "action"},
                   "selected": {"surface": "action", "marker": "selection"},
                   "disabled": {"surface": "inactive"}},
    }


def _required(mapping, key, path, expected):
    field = f"{path}.{key}"
    if key not in mapping:
        raise ValueError(f"缺少配置项 {field}")
    value = mapping[key]
    if type(value) is not expected or (expected is str and not value.strip()):
        raise ValueError(f"{field} 必须为有效的 {expected.__name__}")
    return value


def build(parameters, spec, component_id):
    width = _required(spec, "width", "spec", int)
    height = _required(spec, "height", "spec", int)
    if not 64 <= width <= 16384 or not 32 <= height <= 16384:
        raise ValueError("spec.width 必须在 64–16384，spec.height 必须在 32–16384")
    theme_id = _required(spec, "theme", "spec", str)
    themes = _required(parameters, "themes", "parameters", dict)
    theme_path = f"parameters.themes.{theme_id}"
    theme = _required(themes, theme_id, "parameters.themes", dict)
    state_id = _required(spec, "state", "spec", str)
    states = _required(parameters, "states", "parameters", dict)
    state_path = f"parameters.states.{state_id}"
    state = _required(states, state_id, "parameters.states", dict)
    if state.keys() - {"surface", "marker"}:
        raise ValueError(f"{state_path} 含有不支持的颜色用途")
    surface_role = _required(state, "surface", state_path, str)
    surface = _required(theme, surface_role, theme_path, str)
    outline = _required(theme, "outline", theme_path, str)
    marker = _required(theme, _required(state, "marker", state_path, str), theme_path, str) if "marker" in state else outline
    return {
        "schema_version": 1, "width": width, "height": height,
        "nodes": [
            {"key": "surface", "name": f"{component_id}/底面", "kind": "shape", "parent": "root",
             "shape": {"kind": "roundedRect", "rect": [4, 4, width - 8, height - 8], "radii": 10,
                       "fill": surface, "stroke": {"width": 3, "color": outline, "align": "inside"}}},
            {"key": "marker", "name": f"{component_id}/状态标记", "kind": "shape", "parent": "root",
             "shape": {"kind": "ellipse", "rect": [width - 22, 10, 10, 10], "fill": marker},
             "properties": {"visible": "marker" in state}},
        ],
    }
