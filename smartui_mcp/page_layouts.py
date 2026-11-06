"""校验当前页面定义，解析资产和项目文字样式。"""

from copy import deepcopy
import math
import re

from . import typography
from .comparisons import _check_output_size
from .component_usage import check_component_states, get_usage, resolve_usage
from .image_assets import verify_image_record
from .layout_constraints import arrange_definition, validate_group_layout, validate_insets
from .slicing import image_patches


MAX_PAGE_NODES = 512
MAX_LAYOUT_DEPTH = 32


def _integer(value, field, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{field} 必须是大于等于 {minimum} 的整数")


def _identifier(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ValueError(f"{field} 必须是有效的字母数字标识")


def _color(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        raise ValueError(f"{field} 必须是 #RRGGBB 颜色")


def iter_layout_nodes(nodes):
    """按页面定义顺序遍历分组和叶节点。"""
    for node in nodes:
        yield node
        if node["kind"] == "group":
            yield from iter_layout_nodes(node["children"])


def validate_definition(width, height, background, nodes, safe_area=None, checks=None):
    """校验页面结构与节点字段，保留文案和布局信息。"""
    _integer(width, "width", 1)
    _integer(height, "height", 1)
    _check_output_size((width, height), "页面")
    _color(background, "background")
    if safe_area is not None:
        validate_insets(safe_area, "safe_area")
        if safe_area["left"] + safe_area["right"] >= width or safe_area["top"] + safe_area["bottom"] >= height:
            raise ValueError("safe_area 的四边内缩量必须为画布留出正宽高")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= MAX_PAGE_NODES:
        raise ValueError(f"nodes 必须包含 1 至 {MAX_PAGE_NODES} 项")
    identifiers, parents = set(), {}

    def validate_node(node, path, depth, parent_direction=None, parent_align=None, parent_id=None):
        if depth > MAX_LAYOUT_DEPTH:
            raise ValueError(f"{path} 超出页面最多 {MAX_LAYOUT_DEPTH} 层的限制")
        if not isinstance(node, dict):
            raise ValueError(f"{path} 必须是对象")
        _identifier(node.get("id"), f"{path}.id")
        label = f"节点 {node['id']}"
        if node["id"] in identifiers:
            raise ValueError(f"重复节点 ID：{node['id']}")
        identifiers.add(node["id"])
        parents[node["id"]] = (parent_id, parent_direction)
        if len(identifiers) > MAX_PAGE_NODES:
            raise ValueError(f"分组和子节点合计不得超过 {MAX_PAGE_NODES} 项")
        relative = parent_direction not in (None, "absolute")
        kind = node.get("kind")
        has_region = parent_direction is None and kind == "group" and "region" in node
        required = {"id", "kind"}
        optional = {"visual_offset"} if relative else set()
        if has_region:
            required.add("region")
        else:
            required |= {"width", "height"}
            if not relative:
                required |= {"x", "y"}
        if kind == "image":
            required.add("asset_id")
        elif kind == "text":
            required |= {"text", "text_style_id", "align", "wrap", "overflow"}
            optional |= {"max_lines", "min_font_size"}
        elif kind == "control":
            required |= {"control_id", "parameters"}
        elif kind == "group":
            required |= {"layout", "children"}
            optional.add("component_id")
        else:
            raise ValueError(f"{label}.kind 必须是 image、text、control 或 group")
        missing = sorted(required - node.keys())
        extra = sorted(node.keys() - required - optional, key=str)
        if missing or extra:
            raise ValueError(f"{label} 字段不符合 {kind} 节点要求，缺少 {missing}，多余 {extra}")
        if has_region:
            if node["region"] not in ("canvas", "safe"):
                raise ValueError(f"{label}.region 必须是 canvas 或 safe")
            if node["region"] == "safe" and safe_area is None:
                raise ValueError(f"{label}.region 使用 safe 时必须显式声明 safe_area")
        else:
            for field in ("width", "height"):
                value = node[field]
                if relative and (value == "fill" or (value == "content" and field == "width" and kind == "text")):
                    continue
                _integer(value, f"{label}.{field}", 1)
            if not relative:
                _integer(node["x"], f"{label}.x")
                _integer(node["y"], f"{label}.y")
            if parent_direction is None and (node["x"] + node["width"] > width or node["y"] + node["height"] > height):
                raise ValueError(f"{label} 的布局内容区域超出画布")
        if "visual_offset" in node:
            offset = node["visual_offset"]
            if (not isinstance(offset, dict) or offset.keys() != {"x", "y"}
                    or any(type(value) is not int for value in offset.values())):
                raise ValueError(f"{label}.visual_offset 必须包含整数像素 x、y")
        if parent_align == "baseline" and (kind != "text" or node["wrap"] is not False
                                          or not isinstance(node["text"], str) or "\n" in node["text"]):
            raise ValueError(f"{label} 所在的基线分组只接受不换行的单行文字")
        if kind == "image":
            _identifier(node["asset_id"], f"{label}.asset_id")
        elif kind == "control":
            _identifier(node["control_id"], f"{label}.control_id")
            if not isinstance(node["parameters"], dict):
                raise ValueError(f"{label}.parameters 必须是按参数 ID 组织的对象")
            for parameter, value in node["parameters"].items():
                _identifier(parameter, f"{label}.parameters 的参数 ID")
                if type(value) not in (str, int, float, bool) or (type(value) is float and not math.isfinite(value)):
                    raise ValueError(f"{label}.parameters.{parameter} 必须是字符串、有限数值或布尔值")
        elif kind == "text":
            _identifier(node["text_style_id"], f"{label}.text_style_id")
            if not isinstance(node["text"], str) or not node["text"]:
                raise ValueError(f"{label}.text 必须是非空字符串")
            if any(ord(char) < 32 and char != "\n" for char in node["text"]):
                raise ValueError(f"{label}.text 含不支持的控制字符；只允许换行")
            if type(node["wrap"]) is not bool or node["align"] not in ("left", "center", "right"):
                raise ValueError(f"{label} 的 wrap 或 align 无效")
            if node["overflow"] not in ("error", "clip", "shrink"):
                raise ValueError(f"{label}.overflow 必须明确为 error、clip 或 shrink")
            if node["overflow"] == "shrink":
                _integer(node.get("min_font_size"), f"{label}.min_font_size", 1)
                if node["width"] == "content":
                    raise ValueError(f"{label}.width 使用 content 时不能将 overflow 设为 shrink")
            elif "min_font_size" in node:
                raise ValueError(f"{label}.min_font_size 只适用于 overflow: shrink")
            if node["width"] == "content" and (node["wrap"] or "\n" in node["text"]):
                raise ValueError(f"{label}.width 使用 content 时只接受不换行的单行文字")
            if "max_lines" in node:
                _integer(node["max_lines"], f"{label}.max_lines", 1)
        else:
            if "component_id" in node:
                _identifier(node["component_id"], f"{label}.component_id")
            direction = validate_group_layout(node["layout"], label)
            children = node["children"]
            if not isinstance(children, list) or not 1 <= len(children) <= MAX_PAGE_NODES:
                raise ValueError(f"{label}.children 必须包含 1 至 {MAX_PAGE_NODES} 项")
            for index, child in enumerate(children):
                validate_node(child, f"{path}.children[{index}]", depth + 1, direction,
                              node["layout"].get("align"), node["id"])

    for index, node in enumerate(nodes):
        validate_node(node, f"nodes[{index}]", 1)
    definition = {"width": width, "height": height, "background": background, "nodes": deepcopy(nodes)}
    if safe_area is not None:
        definition["safe_area"] = dict(safe_area)
    if checks is not None:
        if not isinstance(checks, dict) or checks.keys() - {"min_font_size", "allow_overlap", "visual_alignment_tolerance"}:
            raise ValueError("checks 只能包含 min_font_size、allow_overlap 和 visual_alignment_tolerance")
        if "min_font_size" in checks:
            _integer(checks["min_font_size"], "checks.min_font_size", 1)
        if "visual_alignment_tolerance" in checks:
            _integer(checks["visual_alignment_tolerance"], "checks.visual_alignment_tolerance")
        if "allow_overlap" in checks:
            pairs = checks["allow_overlap"]
            if not isinstance(pairs, list):
                raise ValueError("checks.allow_overlap 必须是节点 ID 对的列表")
            seen = set()
            for pair in pairs:
                if (not isinstance(pair, list) or len(pair) != 2 or
                        any(not isinstance(key, str) or key not in identifiers for key in pair) or pair[0] == pair[1]):
                    raise ValueError(f"checks.allow_overlap 必须引用两个不同的现存节点：{pair!r}")
                key = tuple(sorted(pair))
                if parents[pair[0]] != parents[pair[1]] or parents[pair[0]][1] not in (None, "absolute"):
                    raise ValueError(f"checks.allow_overlap 只允许顶层或 absolute 分组的同级节点：{pair}")
                if key in seen:
                    raise ValueError(f"checks.allow_overlap 含重复节点对：{pair}")
                seen.add(key)
        definition["checks"] = deepcopy(checks)
    return definition


def image_geometry(record):
    # 独立图像使用整图等比显示，正式导出使用显式几何。
    return record.get("geometry", {"content_rect": {"x": 0, "y": 0, "width": record["width"],
                                                   "height": record["height"]}, "mode": "contain"})


def _apply_component_padding(state, node, usages):
    component_id = node["component_id"]
    if component_id not in usages:
        usage = get_usage(state, component_id)
        if usage is None:
            raise ValueError(f"组件 {component_id} 未声明 spec.usage，不能用于页面分组 {node['id']}")
        usages[component_id] = usage
    padding = usages[component_id]["padding"]
    if "padding" in node["layout"]:
        declared = node["layout"]["padding"]
        if type(declared) is int:
            declared = dict.fromkeys(padding, declared)
        if declared != padding:
            raise ValueError(f"分组 {node['id']}.layout.padding 与组件 {component_id} 的 spec.usage.padding 不一致；"
                             "请调整共享使用规范")
    node["layout"]["padding"] = padding


def _resolve_component_usages(state, groups, usages):
    from .page_dependencies import asset_source_issues
    from .projects import spec_sha256

    asset_issues = asset_source_issues(state)
    component_info = {
        key: (spec_sha256(usage), check_component_states(state, key, asset_issues)) for key, usage in usages.items()
    }
    result = {}
    for group in groups:
        if "component_id" not in group:
            continue
        component_id = group["component_id"]
        digest, states = component_info[component_id]
        result[group["id"]] = {
            "component_id": component_id, "usage_sha256": digest,
            **resolve_usage(state, component_id, group["box"]), "states": states,
        }
    return result


def _expand_preview_controls(state, nodes):
    dependencies, fixed_sizes = {}, {}
    count = 0

    def expand(node, scope, depth):
        nonlocal count
        count += 1
        if depth > MAX_LAYOUT_DEPTH or count > MAX_PAGE_NODES:
            raise ValueError(f"控件展开后的布局不得超过 {MAX_LAYOUT_DEPTH} 层或 {MAX_PAGE_NODES} 个节点")
        node_id = f"{scope}/{node['id']}" if scope else node["id"]
        if node["kind"] == "group":
            return {**node, "id": node_id,
                    "children": [expand(child, scope, depth + 1) for child in node["children"]]}
        if node["kind"] != "control":
            return {**node, "id": node_id}

        from .projects import ProjectError
        from .unity_recipes import control_preview

        control_id = node["control_id"]
        try:
            preview = control_preview(state.get("unity_controls", {}), control_id, node["parameters"])
        except (ValueError, ProjectError) as error:
            raise ValueError(f"控件实例 {node_id}（{control_id}）：{error}") from error
        dependencies.update(preview["dependencies"])
        fixed_sizes[node_id] = (preview["width"], preview["height"])
        group = {key: value for key, value in node.items() if key not in ("control_id", "parameters")}
        return dict(group, id=node_id, kind="group", layout={"direction": "absolute"},
                    children=[expand(child, node_id, depth + 1) for child in preview["nodes"]])

    return [expand(node, None, 1) for node in nodes], dependencies, fixed_sizes


def iter_page_preview_nodes(state, nodes):
    """按当前控件定义遍历浏览器使用的分组及图文，无需测量布局。"""
    expanded, _, _ = _expand_preview_controls(state, nodes)
    yield from iter_layout_nodes(expanded)


def _layout_tree(nodes, placed, groups):
    boxes = {node["id"]: {field: node[field] for field in ("x", "y", "width", "height")}
             for node in placed}
    boxes.update({group["id"]: dict(zip(("x", "y", "width", "height"), group["box"], strict=True))
                  for group in groups})

    def place(node):
        item = {key: deepcopy(value) for key, value in node.items() if key not in ("visual_offset", "children")}
        item.update(boxes[node["id"]])
        if node["kind"] == "group":
            item["children"] = [place(child) for child in node["children"]]
        return item

    return [place(node) for node in nodes]


def resolve_definition(state, definition, *, include_layout_tree=False):
    """解析当前资产和文字样式，校验构建输入使用的真实文件。"""
    result = deepcopy(definition)
    result["nodes"], dependencies, fixed_sizes = _expand_preview_controls(state, result["nodes"])
    if dependencies:
        result["control_dependencies"] = dependencies
    content_text, usages = [], {}
    for node in iter_layout_nodes(result["nodes"]):
        if node["kind"] == "group" and "component_id" in node:
            _apply_component_padding(state, node, usages)
        if node["kind"] != "text":
            continue
        label = f"文字节点 {node['id']}"
        style_id = node["text_style_id"]
        spec = state["typography"]
        if spec is None:
            raise ValueError(f"{label} 引用样式 {style_id}，请先保存项目文字规范")
        if style_id not in spec["styles"]:
            raise ValueError(f"{label} 引用不存在的文字样式：{style_id}")
        style = typography.resolve_style(spec, style_id)
        if node["overflow"] == "shrink" and node["min_font_size"] > style["font_size"]:
            raise ValueError(f"{label}.min_font_size 不得大于角色样式 {style_id} 的字号 {style['font_size']}")
        typography.validate_font(style["font_path"], style["font_sha256"], text=node["text"],
                                 label=f"{label} 的样式 {style_id}")
        node.update(style, text_style_sha256=typography.style_digest(spec, style_id))
        if node["width"] == "content":
            content_text.append(node)
    if content_text:
        from .browser_preview import measure_text_widths

        widths = measure_text_widths(content_text)
        for node in content_text:
            measured = widths[node["id"]]
            _integer(measured["width"], f"文字节点 {node['id']} 测得的内容宽度", 1)
            node.update(width=measured["width"], content_insets=measured["content_insets"])
    result["nodes"], groups, baseline_groups = arrange_definition(result, fixed_sizes=fixed_sizes)
    if include_layout_tree:
        result["layout_tree"] = _layout_tree(definition["nodes"], result["nodes"], groups)
    for group in groups:
        if group["id"] in fixed_sizes:
            # 控件内部图文按定义叠放；页面仍检查控件实例与同级节点的重叠。
            group["direction"] = "overlay"
    if groups:
        result["layout_groups"] = groups
    if baseline_groups:
        result["baseline_groups"] = baseline_groups
    if usages:
        result["component_usage"] = _resolve_component_usages(state, groups, usages)
    verified = set()
    for node in result["nodes"]:
        if node["kind"] != "image":
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
