"""校验组件使用规格，解析页面实例的范围并检查所需导出状态。"""

from copy import deepcopy


_SIDES = ("top", "right", "bottom", "left")
_FIELDS = {"padding", "text_insets", "hit_insets", "pivot", "required_states"}


def validate_usage(usage):
    """校验显式声明的 spec.usage，并返回独立副本。"""
    if not isinstance(usage, dict) or usage.keys() != _FIELDS:
        raise ValueError("spec.usage 必须恰好包含 padding、text_insets、hit_insets、pivot 和 required_states")
    for field in ("padding", "text_insets", "hit_insets"):
        insets = usage[field]
        label = f"spec.usage.{field}"
        signed = field == "hit_insets"
        if not isinstance(insets, dict) or insets.keys() != set(_SIDES):
            raise ValueError(f"{label} 必须恰好包含 top、right、bottom、left")
        for side in _SIDES:
            value = insets[side]
            if type(value) is not int or (not signed and value < 0):
                kind = "整数" if signed else "非负整数"
                raise ValueError(f"{label}.{side} 必须是{kind}像素")

    pivot = usage["pivot"]
    if not isinstance(pivot, dict) or pivot.keys() != {"x", "y"}:
        raise ValueError("spec.usage.pivot 必须恰好包含 x、y")
    for axis in ("x", "y"):
        value = pivot[axis]
        if type(value) not in (int, float) or not 0 <= value <= 1:
            raise ValueError(f"spec.usage.pivot.{axis} 必须是 0 到 1 之间的有限数值")

    required = usage["required_states"]
    if (not isinstance(required, list) or not required
            or any(not isinstance(value, str) or not value.strip() for value in required)
            or len(required) != len(set(required))):
        raise ValueError("spec.usage.required_states 必须是非空列表，且每个状态是互不重复的非空字符串")
    return deepcopy(usage)


def get_usage(state, component_id):
    """读取已存在组件的使用规格；该组件未声明使用规格时返回 None。"""
    component = state["components"].get(component_id)
    if component is None:
        raise ValueError(f"组件不存在：{component_id}")
    spec = component["spec"]
    if "usage" in spec:
        try:
            return validate_usage(spec["usage"])
        except ValueError as error:
            raise ValueError(f"组件 {component_id}：{error}") from error
    return None


def _inset_rect(box, insets, component_id, field):
    x, y, width, height = box
    top, right, bottom, left = (insets[side] for side in _SIDES)
    inner_width, inner_height = width - left - right, height - top - bottom
    if min(inner_width, inner_height) <= 0:
        raise ValueError(f"组件 {component_id} 的 spec.usage.{field} 用于分组 {box} 时未留下正宽高区域")
    return [x + left, y + top, inner_width, inner_height]


def resolve_usage(state, component_id, group_box):
    """按分组的绝对 [x, y, width, height] 计算使用范围。"""
    usage = get_usage(state, component_id)
    if usage is None:
        raise ValueError(f"组件 {component_id} 未声明 spec.usage，不能用于页面分组")
    if (not isinstance(group_box, list) or len(group_box) != 4
            or any(type(value) is not int for value in group_box) or min(group_box[2:]) <= 0):
        raise ValueError(f"组件 {component_id} 的 group_box 必须是四个整数 [x, y, width, height]，且宽高为正")
    x, y, width, height = group_box
    _inset_rect(group_box, usage["padding"], component_id, "padding")
    return {
        "padding": usage["padding"],
        "text_rect": _inset_rect(group_box, usage["text_insets"], component_id, "text_insets"),
        "hit_rect": _inset_rect(group_box, usage["hit_insets"], component_id, "hit_insets"),
        "pivot": {"x": x + width * usage["pivot"]["x"], "y": y + height * usage["pivot"]["y"]},
        "required_states": usage["required_states"],
    }


def check_component_states(state, component_id, asset_issues):
    """区分已登记且来源有效、来源过期、以及未登记的状态。"""
    usage = get_usage(state, component_id)
    required = usage["required_states"] if usage is not None else []
    registered, available = set(), set()
    for asset_id, image in state["images"].items():
        provenance = image["provenance"]
        if provenance["kind"] == "component_export" and provenance["source"]["component_id"] == component_id:
            registered.add(image["state"])
            if asset_id not in asset_issues:
                available.add(image["state"])
    return {"required": required, "missing": [item for item in required if item not in registered],
            "stale": [item for item in required if item in registered and item not in available],
            "available": sorted(available)}
