"""页面分组的局部定位、排列和对齐。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    width: int
    height: int

    def contains(self, other):
        return (self.x <= other.x and self.y <= other.y
                and other.x + other.width <= self.x + self.width
                and other.y + other.height <= self.y + self.height)

    def values(self):
        return [self.x, self.y, self.width, self.height]


def validate_group_layout(layout, label):
    if not isinstance(layout, dict):
        raise ValueError(f"{label}.layout 必须是对象")
    direction = layout.get("direction")
    if direction not in ("absolute", "row", "column", "overlay"):
        raise ValueError(f"{label}.layout.direction 必须是 absolute、row、column 或 overlay")
    allowed = {"direction", "padding"}
    if direction != "absolute":
        allowed |= {"align", "justify"}
    if direction in ("row", "column"):
        allowed.add("gap")
    extra = layout.keys() - allowed
    if extra:
        raise ValueError(f"{label}.layout 有不适用于 {direction} 的字段：{sorted(extra, key=str)}")
    align = ("start", "center", "end", "baseline") if direction == "row" else ("start", "center", "end")
    justify = ("start", "center", "end", "space-between") if direction in ("row", "column") else ("start", "center", "end")
    if layout.get("align", "start") not in align:
        raise ValueError(f"{label}.layout.align 必须是 {'、'.join(align)}")
    if layout.get("justify", "start") not in justify:
        raise ValueError(f"{label}.layout.justify 必须是 {'、'.join(justify)}")
    padding = layout.get("padding", 0)
    if isinstance(padding, dict):
        validate_insets(padding, f"{label}.layout.padding")
    else:
        _nonnegative(padding, f"{label}.layout.padding")
    _nonnegative(layout.get("gap", 0), f"{label}.layout.gap")
    return direction


def validate_insets(insets, field):
    if not isinstance(insets, dict) or insets.keys() != {"top", "right", "bottom", "left"}:
        raise ValueError(f"{field} 必须完整包含 top、right、bottom、left")
    for side, value in insets.items():
        _nonnegative(value, f"{field}.{side}")


def _nonnegative(value, field):
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} 必须是非负整数像素")


def _inner_box(node, box):
    padding = node["layout"].get("padding", 0)
    top, right, bottom, left = (
        [padding[side] for side in ("top", "right", "bottom", "left")]
        if isinstance(padding, dict) else [padding] * 4
    )
    inner = Box(box.x + left, box.y + top, box.width - left - right, box.height - top - bottom)
    if min(inner.width, inner.height) < 1:
        raise ValueError(f"分组 {node['id']} 的 padding 未留下可用内容区域")
    return inner


def _root_box(definition, node):
    region = node.get("region")
    if region == "canvas":
        return Box(0, 0, definition["width"], definition["height"])
    if region == "safe":
        insets = definition["safe_area"]
        return Box(insets["left"], insets["top"], definition["width"] - insets["left"] - insets["right"],
                   definition["height"] - insets["top"] - insets["bottom"])
    return Box(node["x"], node["y"], node["width"], node["height"])


def _aligned_start(free_space, alignment):
    if alignment == "center":
        return free_space // 2
    return free_space if alignment == "end" else 0


def _centered_start(node, axis, free_space, alignment):
    offset = node.get("visual_offset", {}).get(axis, 0)
    if offset and alignment != "center":
        raise ValueError(f"节点 {node['id']}.visual_offset.{axis} 仅适用于居中对齐的轴")
    return _aligned_start(free_space, alignment) - offset


def _size(value, available):
    return available if value == "fill" else value


def _share(pixels, slots, index):
    whole, remainder = divmod(pixels, slots)
    return whole + (index < remainder)


def _flow_boxes(node, inner):
    layout, children = node["layout"], node["children"]
    horizontal = layout["direction"] == "row"
    main, cross = ("width", "height") if horizontal else ("height", "width")
    main_axis, cross_axis = ("x", "y") if horizontal else ("y", "x")
    available, cross_space = getattr(inner, main), getattr(inner, cross)
    gap = layout.get("gap", 0)
    align, justify = layout.get("align", "start"), layout.get("justify", "start")
    fill_count = sum(child[main] == "fill" for child in children)
    fixed = sum(child[main] for child in children if child[main] != "fill")
    gap_count = len(children) - 1
    free = available - fixed - gap * gap_count
    if free < fill_count:
        raise ValueError(f"分组 {node['id']} 的 {main} 无法容纳子节点尺寸及 gap")
    cursor = _aligned_start(0 if fill_count else free, justify)
    fills_used = 0
    for index, child in enumerate(children):
        main_size = child[main]
        if main_size == "fill":
            main_size = _share(free, fill_count, fills_used)
            fills_used += 1
        cross_size = _size(child[cross], cross_space)
        cross_start = _centered_start(child, cross_axis, cross_space - cross_size, align)
        if child.get("visual_offset", {}).get(main_axis, 0):
            raise ValueError(f"节点 {child['id']}.visual_offset.{main_axis} 不适用于排列轴")
        if horizontal:
            yield Box(inner.x + cursor, inner.y + cross_start, main_size, cross_size)
        else:
            yield Box(inner.x + cross_start, inner.y + cursor, cross_size, main_size)
        cursor += main_size + gap
        if not fill_count and justify == "space-between" and index < gap_count:
            cursor += _share(free, gap_count, index)


def _group_boxes(node, inner):
    layout = node["layout"]
    if layout["direction"] in ("row", "column"):
        yield from _flow_boxes(node, inner)
        return
    for child in node["children"]:
        if layout["direction"] == "absolute":
            yield Box(inner.x + child["x"], inner.y + child["y"], child["width"], child["height"])
        else:
            width, height = _size(child["width"], inner.width), _size(child["height"], inner.height)
            x = _centered_start(child, "x", inner.width - width, layout.get("justify", "start"))
            y = _centered_start(child, "y", inner.height - height, layout.get("align", "start"))
            yield Box(inner.x + x, inner.y + y, width, height)


def arrange_definition(definition):
    """由尺寸已确定的布局树生成按绘制顺序排列的节点及分组信息。"""
    placed, groups, baseline_groups = [], [], []

    def place(node, box):
        if node["kind"] != "group":
            leaf = {key: value for key, value in node.items() if key != "visual_offset"}
            placed.append(dict(leaf, x=box.x, y=box.y, width=box.width, height=box.height))
            return
        inner = _inner_box(node, box)
        child_ids = [child["id"] for child in node["children"]]
        layout = node["layout"]
        group = {"id": node["id"], "direction": layout["direction"], "box": box.values(),
                 "content_box": inner.values(), "node_ids": child_ids}
        if layout["direction"] != "absolute":
            group.update(align=layout.get("align", "start"), justify=layout.get("justify", "start"))
        if layout["direction"] in ("row", "column"):
            group["gap"] = layout.get("gap", 0)
        if "component_id" in node:
            group["component_id"] = node["component_id"]
        if "region" in node:
            group["region"] = node["region"]
        groups.append(group)
        if node["layout"].get("align") == "baseline":
            baseline_groups.append({"id": node["id"], "y": inner.y, "height": inner.height,
                                    "node_ids": child_ids})
        for child, child_box in zip(node["children"], _group_boxes(node, inner), strict=True):
            if not inner.contains(child_box):
                raise ValueError(f"节点 {child['id']} 的布局内容区域超出分组 {node['id']} 的内边距边界")
            place(child, child_box)

    for node in definition["nodes"]:
        place(node, _root_box(definition, node))
    return placed, groups, baseline_groups
