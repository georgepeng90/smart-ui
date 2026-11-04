"""正式资产与页面渲染共用的内容区域、外扩和切片几何。"""

import math


_MODES = {"contain", "stretch", "nine_slice", "horizontal", "vertical"}
_SLICED = {"nine_slice", "horizontal", "vertical"}


def _keys(value, expected, field):
    if not isinstance(value, dict) or value.keys() != expected:
        raise ValueError(f"{field} 必须完整声明且只能包含：{'、'.join(sorted(expected))}")


def _integer(value, field, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{field} 必须是大于等于 {minimum} 的整数像素，不能使用布尔值")
    return value


def _size(value, field, *, integer):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{field} 必须是包含宽、高的二元素数组")
    result = []
    for axis, number in zip(("width", "height"), value):
        if integer:
            _integer(number, f"{field}.{axis}", 1)
        else:
            try:
                valid = type(number) in (int, float) and math.isfinite(number) and number > 0
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError(f"{field}.{axis} 必须是有限正数，不能使用布尔值")
        result.append(number)
    return result


def validate_geometry(geometry: dict, width: int, height: int) -> dict:
    """校验源 PNG 几何，返回独立的规范字典；非法输入抛出 ValueError。"""
    width, height = _size([width, height], "source_size", integer=True)
    if not isinstance(geometry, dict):
        raise ValueError("geometry 必须是对象")
    mode = geometry.get("mode")
    if not isinstance(mode, str) or mode not in _MODES:
        raise ValueError("geometry.mode 必须是 contain、stretch、nine_slice、horizontal 或 vertical")
    expected = {"content_rect", "mode"} | ({"borders"} if mode in _SLICED else set())
    _keys(geometry, expected, "geometry")
    rect = geometry["content_rect"]
    _keys(rect, {"x", "y", "width", "height"}, "geometry.content_rect")
    rect = {key: _integer(rect[key], f"geometry.content_rect.{key}",
                          1 if key in {"width", "height"} else 0)
            for key in ("x", "y", "width", "height")}
    if rect["x"] + rect["width"] > width or rect["y"] + rect["height"] > height:
        raise ValueError("geometry.content_rect 必须完整位于源 PNG 内")
    result = {"content_rect": rect, "mode": mode}
    if mode not in _SLICED:
        return result

    borders = geometry["borders"]
    _keys(borders, {"left", "top", "right", "bottom"}, "geometry.borders")
    borders = {key: _integer(borders[key], f"geometry.borders.{key}")
               for key in ("left", "top", "right", "bottom")}
    if borders["left"] + borders["right"] >= width:
        raise ValueError("geometry.borders.left 与 right 之和必须小于源 PNG 宽度，保留正中心宽度")
    if borders["top"] + borders["bottom"] >= height:
        raise ValueError("geometry.borders.top 与 bottom 之和必须小于源 PNG 高度，保留正中心高度")
    if mode == "horizontal" and (borders["top"] or borders["bottom"]):
        raise ValueError("horizontal 的 geometry.borders.top 与 bottom 必须为 0")
    if mode == "vertical" and (borders["left"] or borders["right"]):
        raise ValueError("vertical 的 geometry.borders.left 与 right 必须为 0")
    # 伸缩轴上的外扩属于固定边，避免光晕随中心拉伸而移动内容框。
    for enabled, first, last, before, after in (
        (mode != "vertical", "left", "right", rect["x"], width - rect["x"] - rect["width"]),
        (mode != "horizontal", "top", "bottom", rect["y"], height - rect["y"] - rect["height"]),
    ):
        if enabled and (borders[first] < before or borders[last] < after):
            raise ValueError(f"geometry.borders.{first} 与 {last} 必须分别覆盖内容区域两侧的外扩边距")
    result["borders"] = borders
    return result


def image_patches(geometry: dict, source_size, target_size) -> dict:
    """将目标内容框换算为完整图像尺寸、位置偏移和无取整的绘制补丁。"""
    width, height = _size(source_size, "source_size", integer=True)
    target_width, target_height = _size(target_size, "target_size", integer=False)
    geometry = validate_geometry(geometry, width, height)
    rect, mode = geometry["content_rect"], geometry["mode"]
    x, y, content_width, content_height = (rect[key] for key in ("x", "y", "width", "height"))
    if mode in {"contain", "stretch"}:
        scale_x, scale_y = target_width / content_width, target_height / content_height
        if mode == "contain":
            scale_x = scale_y = min(scale_x, scale_y)
        image_width, image_height = width * scale_x, height * scale_y
        _size([image_width, image_height], "image_size", integer=False)
        return {
            "image_size": [image_width, image_height],
            "content_offset": [(target_width - content_width * scale_x) / 2 - x * scale_x,
                               (target_height - content_height * scale_y) / 2 - y * scale_y],
            "patches": [{"source": [0, 0, width, height],
                         "target": [0, 0, image_width, image_height]}],
        }

    if mode == "horizontal" and target_height != content_height:
        raise ValueError("horizontal 的 target_size.height 必须等于源内容区域高度")
    if mode == "vertical" and target_width != content_width:
        raise ValueError("vertical 的 target_size.width 必须等于源内容区域宽度")
    image_width, image_height = (target_width + width - content_width,
                                target_height + height - content_height)
    borders = geometry["borders"]
    left, top, right, bottom = (borders[key] for key in ("left", "top", "right", "bottom"))
    if image_width <= left + right or image_height <= top + bottom:
        raise ValueError("target_size 加原始外扩后的完整图像必须大于固定边之和，保留正中心宽高")
    source_x, source_y = [0, left, width - right, width], [0, top, height - bottom, height]
    target_x = [0, left, image_width - right, image_width]
    target_y = [0, top, image_height - bottom, image_height]
    patches = []
    for row in range(3):
        for column in range(3):
            source_width = source_x[column + 1] - source_x[column]
            source_height = source_y[row + 1] - source_y[row]
            # 合法的零边距不生成空补丁；所有实际补丁都有正面积。
            if source_width == 0 or source_height == 0:
                continue
            patches.append({
                "source": [source_x[column], source_y[row], source_width, source_height],
                "target": [target_x[column], target_y[row],
                           target_x[column + 1] - target_x[column],
                           target_y[row + 1] - target_y[row]],
            })
    return {"image_size": [image_width, image_height], "content_offset": [-x, -y], "patches": patches}
