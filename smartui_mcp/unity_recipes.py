"""校验控件制作定义，解析继承和静态预览输入。"""

from copy import deepcopy
from graphlib import CycleError, TopologicalSorter

from .page_layouts import iter_layout_nodes, validate_definition
from .projects import ProjectError, _identifier, spec_sha256
from .unity_contract import validate_catalog


_SLOT_COMPONENTS = {"text": "text", "image": "image", "content": "rect", "event": "button", "data": "rect"}
_PROPERTIES = {"text": "text", "asset_id": "image", "text_style_id": "text"}
_PARAMETER_TYPES = {"text": "string", "asset_id": "asset_id"}
_BUTTON_STATES = {"normal", "highlighted", "pressed", "selected", "disabled"}


def _fields(value, fields, label):
    if not isinstance(value, dict) or value.keys() != fields:
        raise ProjectError(f"{label} 必须恰好包含 {', '.join(sorted(fields))}")


def _value(value, property_name, label):
    if property_name == "text":
        if not isinstance(value, str) or not value:
            raise ProjectError(f"{label} 必须是非空字符串")
        if any(ord(char) < 32 and char != "\n" for char in value):
            raise ProjectError(f"{label} 含不支持的控制字符；只允许换行")
    else:
        _identifier(value, label)


def _validated_nodes(width, height, nodes):
    try:
        return validate_definition(width, height, "#000000", nodes)["nodes"]
    except ValueError as error:
        raise ProjectError(str(error)) from error


def _node_index(nodes):
    return {node["id"]: node for node in iter_layout_nodes(nodes)}


def parse_recipe(value):
    """校验制作定义的结构，返回独立于调用方的字典。"""
    kind = value.get("kind") if isinstance(value, dict) else None
    if kind == "variant":
        _fields(value, {"kind", "overrides"}, "变体制作定义")
        if not isinstance(value["overrides"], list):
            raise ProjectError("变体 overrides 必须是列表")
        seen = set()
        for override in value["overrides"]:
            _fields(override, {"node_id", "property", "value"}, "变体属性覆盖")
            node_id, property_name = override["node_id"], override["property"]
            _identifier(node_id, "变体覆盖的 node_id")
            if not isinstance(property_name, str) or property_name not in _PROPERTIES:
                raise ProjectError(f"变体覆盖属性不受支持：{property_name!r}")
            if (node_id, property_name) in seen:
                raise ProjectError(f"变体覆盖重复声明 {node_id}.{property_name}")
            seen.add((node_id, property_name))
            _value(override["value"], property_name, f"变体覆盖 {node_id}.{property_name}")
        return deepcopy(value)

    if kind not in ("base", "external"):
        raise ProjectError("控件制作定义的 kind 必须是 base、external 或 variant")
    _fields(value, {"kind", "width", "height", "nodes", "slots", "button"}, "控件制作定义")
    nodes = _validated_nodes(value["width"], value["height"], value["nodes"])
    if "root" in _node_index(nodes):
        raise ProjectError("控件内部节点不得使用保留的 root ID")
    if not isinstance(value["slots"], dict):
        raise ProjectError("控件 slots 必须是按槽位 ID 组织的对象")
    for slot_id, target in value["slots"].items():
        _identifier(slot_id, "控件槽位 ID")
        _fields(target, {"node_id", "component"}, f"控件槽位 {slot_id}")
        _identifier(target["node_id"], f"控件槽位 {slot_id}.node_id")
        if target["component"] not in ("rect", "image", "text", "button"):
            raise ProjectError(f"控件槽位 {slot_id}.component 必须是 rect、image、text 或 button")

    button = value["button"]
    if button is not None:
        _fields(button, {"target_graphic", "states"}, "控件 button")
        _identifier(button["target_graphic"], "控件 button.target_graphic")
        states = button["states"]
        if not isinstance(states, dict) or states.keys() - _BUTTON_STATES:
            raise ProjectError("控件 button.states 只接受标准 Button 状态")
        for state, asset_id in states.items():
            _identifier(asset_id, f"控件 button.states.{state}")
    return deepcopy(value)


def _check_interface(control_id, definition, item, nodes):
    slots = item["slots"]
    if slots.keys() != definition["slots"].keys():
        raise ProjectError(f"控件 {control_id} 的制作槽位必须与公开槽位一致")
    button = item["button"]
    for slot_id, target in slots.items():
        component, node_id = target["component"], target["node_id"]
        node_kind = nodes.get(node_id, {}).get("kind")
        valid_targets = {
            "rect": node_id == "root" or node_kind == "group",
            "image": node_kind == "image",
            "text": node_kind == "text",
            "button": node_id == "root" and button is not None,
        }
        if component != _SLOT_COMPONENTS[definition["slots"][slot_id]["kind"]] or not valid_targets[component]:
            raise ProjectError(f"控件 {control_id} 的槽位 {slot_id} 无法映射到 {node_id} 的 {component} 组件")

    targets = set()
    for parameter_id, parameter in item["parameters"].items():
        target = slots[parameter["slot_id"]]
        property_name = parameter["property"]
        label = f"控件 {control_id} 的公开参数 {parameter_id}"
        if property_name not in _PARAMETER_TYPES or (
            _PARAMETER_TYPES[property_name] != parameter["value_type"]
            or _PROPERTIES[property_name] != target["component"]
        ):
            raise ProjectError(f"{label} 必须是文字槽位的 text 字符串或图像槽位的 asset_id")
        property_target = (target["node_id"], property_name)
        if property_target in targets:
            raise ProjectError(f"{label} 与其他参数指向同一个节点属性")
        targets.add(property_target)

    expected_states = {}
    if button is not None:
        image_slots = [
            slot_id for slot_id, target in slots.items()
            if target["component"] == "image" and target["node_id"] == button["target_graphic"]
        ]
        if len(image_slots) != 1:
            raise ProjectError(f"控件 {control_id} 的 Button target_graphic 必须对应一个公开图像槽位")
        expected_states = {state: {image_slots[0]: asset} for state, asset in button["states"].items()}
    if definition["states"] != expected_states:
        raise ProjectError(f"控件 {control_id} 的 Button 状态必须与公开状态一致")


def _check_sources(nodes, images, styles, label):
    for node in nodes:
        if node["kind"] == "image" and node["asset_id"] not in images:
            raise ProjectError(f"{label} 的节点 {node['id']} 引用了未登记的图像 {node['asset_id']}")
        if node["kind"] == "text" and node["text_style_id"] not in styles:
            raise ProjectError(f"{label} 的节点 {node['id']} 引用了未登记的文字样式 {node['text_style_id']}")


def resolve_recipes(definitions, recipes, state):
    """解析公开控件目录与制作定义，返回具有制作定义的控件。"""
    effective = validate_catalog(definitions, state)
    if not isinstance(recipes, dict):
        raise ProjectError("控件制作定义必须是按控件 ID 组织的对象")
    parsed, graph = {}, TopologicalSorter()
    for control_id, recipe in recipes.items():
        if control_id not in effective:
            raise ProjectError(f"制作定义引用了未登记的控件 {control_id}")
        parsed[control_id] = parse_recipe(recipe)

    for control_id, recipe in parsed.items():
        definition = effective[control_id]
        base_id = definition["base_control_id"]
        if (recipe["kind"] == "variant") != (base_id is not None):
            raise ProjectError(f"控件 {control_id} 的制作类型与目录继承关系不一致")
        if recipe["kind"] == "variant" and definitions[control_id]["dependencies"]:
            raise ProjectError(f"变体 {control_id} 的 dependencies 必须为空；嵌套控件由基础预制体继承，不能新增或重复声明")
        if recipe["kind"] == "variant" and parsed.get(base_id, {}).get("kind") == "external":
            raise ProjectError(f"变体 {control_id} 当前只能继承工具生成的控件；外部控件 {base_id} 的预览节点不代表实际对象映射")
        dependencies = {base_id} if base_id is not None else {
            node["control_id"] for node in iter_layout_nodes(recipe["nodes"]) if node["kind"] == "control"
        }
        for dependency in sorted(dependencies):
            if dependency not in parsed:
                raise ProjectError(f"控件 {control_id} 依赖的控件 {dependency} 缺少制作定义")
        graph.add(control_id, *sorted(dependencies))
    try:
        order = list(graph.static_order())
    except CycleError as error:
        raise ProjectError(f"控件制作定义形成循环：{' -> '.join(error.args[1])}") from error

    images = state["images"]
    styles = state["typography"]["styles"] if state["typography"] is not None else {}
    resolved = {}
    for control_id in order:
        recipe, definition = parsed[control_id], effective[control_id]
        base_id = definition["base_control_id"]
        source = resolved[base_id] if base_id is not None else recipe
        item = {field: deepcopy(source[field]) for field in ("width", "height", "nodes", "slots", "button")}
        item["parameters"] = deepcopy(definition["parameters"])
        nodes = _node_index(item["nodes"])
        if base_id is not None:
            if definition["parameters"].keys() != source["parameters"].keys():
                raise ProjectError(f"变体 {control_id} 不得增加公开参数")
            for override in recipe["overrides"]:
                node_id, property_name = override["node_id"], override["property"]
                if nodes.get(node_id, {}).get("kind") != _PROPERTIES[property_name]:
                    raise ProjectError(f"变体 {control_id} 无法覆盖节点 {node_id} 的 {property_name}")
                nodes[node_id][property_name] = override["value"]
            _validated_nodes(item["width"], item["height"], item["nodes"])

        for axis in ("width", "height"):
            minimum, maximum = (definition["layout"][f"{limit}_{axis}"] for limit in ("min", "max"))
            if item[axis] < minimum or (maximum is not None and item[axis] > maximum):
                raise ProjectError(f"控件 {control_id} 的制作尺寸 {axis} 超出公开布局范围")
        _check_interface(control_id, definition, item, nodes)
        _check_sources(nodes.values(), images, styles, f"控件 {control_id}")

        nested = [node for node in nodes.values() if node["kind"] == "control"]
        nested_ids = {node["control_id"] for node in nested}
        if recipe["kind"] != "external" and nested_ids != set(definition["dependencies"]):
            raise ProjectError(f"控件 {control_id} 的嵌套节点与公开 dependencies 不一致")
        dependencies = dict(source["dependencies"]) if base_id is not None else {}
        for node in nested:
            preview = control_preview(resolved, node["control_id"], node["parameters"])
            _check_sources(iter_layout_nodes(preview["nodes"]), images, styles, f"控件 {control_id} 的嵌套节点 {node['id']}")
            dependencies.update(preview["dependencies"])
        semantic = {**item, "layout": definition["layout"], "kind": recipe["kind"], "base_control_id": base_id}
        semantic["parameters"] = {
            key: {field: value for field, value in parameter.items() if field != "description"}
            for key, parameter in item["parameters"].items()
        }
        if base_id is not None:
            semantic["overrides"] = sorted(recipe["overrides"], key=lambda change: (change["node_id"], change["property"]))
        dependencies[control_id] = spec_sha256(semantic)
        item["dependencies"] = dependencies
        resolved[control_id] = item
    return resolved


def control_preview(controls, control_id, parameters):
    """应用当前实例的显式参数，保留内部控件引用供调用者展开。"""
    if control_id not in controls:
        raise ProjectError(f"控件 {control_id} 缺少制作定义")
    if not isinstance(parameters, dict):
        raise ProjectError(f"控件 {control_id} 的实例参数必须是对象")
    item = controls[control_id]
    nodes = deepcopy(item["nodes"])
    index = _node_index(nodes)
    for parameter_id, value in parameters.items():
        if parameter_id not in item["parameters"]:
            raise ProjectError(f"控件 {control_id} 未公开参数 {parameter_id}")
        parameter = item["parameters"][parameter_id]
        property_name = parameter["property"]
        _value(value, property_name, f"控件 {control_id} 的参数 {parameter_id}")
        target = item["slots"][parameter["slot_id"]]
        index[target["node_id"]][property_name] = value
    for parameter_id, parameter in item["parameters"].items():
        if parameter["required"] and parameter_id not in parameters:
            raise ProjectError(f"控件 {control_id} 缺少必需参数 {parameter_id}")
    _validated_nodes(item["width"], item["height"], nodes)
    return {"width": item["width"], "height": item["height"], "nodes": nodes,
            "dependencies": dict(item["dependencies"])}
