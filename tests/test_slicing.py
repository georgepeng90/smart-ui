"""验证资产几何契约及独立的切片像素效果，不依赖浏览器或项目数据。"""

from copy import deepcopy

from PIL import Image
import pytest

from smartui_mcp.slicing import image_patches, validate_geometry


def _geometry(mode="nine_slice"):
    result = {"content_rect": {"x": 24, "y": 24, "width": 272, "height": 96}, "mode": mode}
    if mode in {"nine_slice", "horizontal", "vertical"}:
        result["borders"] = {"left": 68, "top": 68, "right": 68, "bottom": 68}
    return result


def _render(source, plan):
    # 这里只用 Pillow 验证补丁；生产函数本身不承担位图或页面渲染。
    result = Image.new("RGBA", tuple(plan["image_size"]))
    for patch in plan["patches"]:
        sx, sy, sw, sh = patch["source"]
        tx, ty, tw, th = patch["target"]
        piece = source.crop((sx, sy, sx + sw, sy + sh))
        result.paste(piece.resize((tw, th), Image.Resampling.NEAREST), (tx, ty))
    return result


def test_normalization_returns_independent_objects():
    supplied = _geometry()
    result = validate_geometry(supplied, 320, 144)
    assert result == supplied
    result["content_rect"]["x"] = 0
    result["borders"]["left"] = 0
    assert supplied == _geometry()


@pytest.mark.parametrize("target_width", [100, 272, 450])
@pytest.mark.parametrize("target_height", [96, 200])
def test_nine_slice_preserves_rgba_corners_edges_and_center(target_width, target_height):
    source = Image.new("RGBA", (320, 144))
    # 九个区域拥有独立颜色；角内另有不同透明度的像素，避免只验证尺寸。
    colors = [(31 * column, 73 * row, 29 * (row + column), 40 + 20 * (row * 3 + column))
              for row in range(3) for column in range(3)]
    for row, (y1, y2) in enumerate(((0, 68), (68, 76), (76, 144))):
        for column, (x1, x2) in enumerate(((0, 68), (68, 252), (252, 320))):
            source.paste(colors[row * 3 + column], (x1, y1, x2, y2))
    for point, color in [((3, 5), (1, 2, 3, 0)), ((319, 0), (4, 5, 6, 127)),
                         ((0, 143), (7, 8, 9, 255)), ((300, 140), (10, 11, 12, 1))]:
        source.putpixel(point, color)
    plan = image_patches(_geometry(), source.size, (target_width, target_height))
    image_width, image_height = target_width + 48, target_height + 48
    assert plan["image_size"] == [image_width, image_height]
    assert plan["content_offset"] == [-24, -24]
    assert len(plan["patches"]) == 9
    result = _render(source, plan)
    for sx, sy, tx, ty in ((0, 0, 0, 0), (252, 0, image_width - 68, 0),
                           (0, 76, 0, image_height - 68),
                           (252, 76, image_width - 68, image_height - 68)):
        assert result.crop((tx, ty, tx + 68, ty + 68)).tobytes() == source.crop((sx, sy, sx + 68, sy + 68)).tobytes()
    # 顶边、底边、左边、右边与中央的尺寸及像素均来自对应源区域。
    for area, color in [((68, 0, image_width - 68, 68), colors[1]),
                        ((68, image_height - 68, image_width - 68, image_height), colors[7]),
                        ((0, 68, 68, image_height - 68), colors[3]),
                        ((image_width - 68, 68, image_width, image_height - 68), colors[5]),
                        ((68, 68, image_width - 68, image_height - 68), colors[4])]:
        region = result.crop(area)
        assert region.getcolors() == [(region.width * region.height, color)]
    if (target_width, target_height) == (272, 96):
        assert result.tobytes() == source.tobytes()


def test_asymmetric_content_and_borders_keep_original_expansion():
    geometry = {"content_rect": {"x": 7, "y": 11, "width": 70, "height": 40},
                "mode": "nine_slice", "borders": {"left": 19, "top": 17, "right": 29, "bottom": 13}}
    result = image_patches(geometry, [100, 60], [120, 80])
    assert result["image_size"] == [150, 100]
    assert result["content_offset"] == [-7, -11]
    assert result["patches"][4] == {"source": [19, 17, 52, 30], "target": [19, 17, 102, 70]}
    assert result["patches"][-1] == {"source": [71, 47, 29, 13], "target": [121, 87, 29, 13]}


@pytest.mark.parametrize("mode,target_size,expected", [
    ("horizontal", [400, 96], [448, 144]), ("vertical", [272, 200], [320, 248]),
])
def test_single_axis_modes_have_three_nonempty_patches(mode, target_size, expected):
    geometry = _geometry(mode)
    for side in (("top", "bottom") if mode == "horizontal" else ("left", "right")):
        geometry["borders"][side] = 0
    result = image_patches(geometry, [320, 144], target_size)
    assert result["image_size"] == expected
    assert len(result["patches"]) == 3
    for patch in result["patches"]:
        axis = 3 if mode == "horizontal" else 2
        assert patch["source"][axis] == patch["target"][axis]


def test_contain_centers_content_and_keeps_subpixels():
    geometry = {"content_rect": {"x": 1, "y": 2, "width": 3, "height": 2}, "mode": "contain"}
    result = image_patches(geometry, [8, 7], [5, 8])
    assert result["image_size"] == pytest.approx([40 / 3, 35 / 3])
    assert result["content_offset"] == pytest.approx([-5 / 3, -1])
    assert result["patches"][0]["target"] == pytest.approx([0, 0, 40 / 3, 35 / 3])


def test_stretch_scales_expansion_independently_on_each_axis():
    result = image_patches(_geometry("stretch"), [320, 144], [544, 288])
    assert result == {"image_size": [640, 432], "content_offset": [-48, -72],
                      "patches": [{"source": [0, 0, 320, 144], "target": [0, 0, 640, 432]}]}


@pytest.mark.parametrize("path,value", [
    ((), []), (("mode",), "other"), (("mode",), []), (("unknown",), 1),
    (("content_rect", "unknown"), 1), (("content_rect", "width"), 0),
    (("content_rect", "x"), -1), (("content_rect", "x"), True),
    (("content_rect", "width"), 272.0), (("content_rect", "height"), float("inf")),
    (("content_rect", "height"), float("nan")), (("content_rect", "y"), 50),
    (("borders", "left"), 252), (("borders", "top"), 76),
    (("borders", "right"), 1), (("borders", "left"), False),
    (("borders", "left"), 1.5), (("borders", "unknown"), 0),
])
def test_invalid_geometry_is_rejected(path, value):
    geometry = _geometry()
    if not path:
        geometry = value
    else:
        destination = geometry
        for key in path[:-1]:
            destination = destination[key]
        destination[path[-1]] = value
    with pytest.raises(ValueError, match="geometry"):
        validate_geometry(geometry, 320, 144)


@pytest.mark.parametrize("mode", ["contain", "stretch", "nine_slice"])
def test_missing_or_unexpected_borders_are_rejected(mode):
    geometry = _geometry(mode)
    if mode == "nine_slice":
        del geometry["borders"]
    else:
        geometry["borders"] = {"left": 0, "top": 0, "right": 0, "bottom": 0}
    with pytest.raises(ValueError, match="geometry"):
        validate_geometry(geometry, 320, 144)


@pytest.mark.parametrize("mode", ["horizontal", "vertical"])
def test_single_axis_modes_reject_borders_on_other_axis(mode):
    with pytest.raises(ValueError, match=mode):
        validate_geometry(_geometry(mode), 320, 144)


@pytest.mark.parametrize("target", [[88, 96], [100, 88], [87, 96], [100, 87]])
def test_minimum_size_includes_expansion_and_requires_positive_center(target):
    with pytest.raises(ValueError, match="正中心"):
        image_patches(_geometry(), [320, 144], target)


@pytest.mark.parametrize("mode,target", [("horizontal", [400, 97]), ("vertical", [273, 200])])
def test_single_axis_modes_reject_change_to_fixed_content_dimension(mode, target):
    geometry = _geometry(mode)
    for side in (("top", "bottom") if mode == "horizontal" else ("left", "right")):
        geometry["borders"][side] = 0
    with pytest.raises(ValueError, match=mode):
        image_patches(geometry, [320, 144], target)


@pytest.mark.parametrize("source,target", [
    ([320], [272, 96]), ([320, 144, 1], [272, 96]), ([True, 144], [272, 96]),
    ([320.0, 144], [272, 96]), ([320, 0], [272, 96]),
    ([320, 144], [0, 96]), ([320, 144], [True, 96]),
    ([320, 144], [272, float("inf")]), ([320, 144], [float("nan"), 96]),
    ([320, 144], [10 ** 1000, 96]),
    ([320, 144], {"width": 272, "height": 96}),
])
def test_invalid_sizes_are_rejected(source, target):
    with pytest.raises(ValueError, match="size"):
        image_patches(_geometry(), source, target)


def test_fractional_nine_slice_target_does_not_round_geometry():
    result = image_patches(_geometry(), [320, 144], [272.5, 96.25])
    assert result["image_size"] == [320.5, 144.25]
    assert result["patches"][4]["target"] == [68, 68, 184.5, 8.25]


def test_finite_target_that_overflows_complete_image_is_rejected():
    with pytest.raises(ValueError, match="image_size"):
        image_patches(_geometry("stretch"), [320, 144], [1.7e308, 96])


def test_zero_borders_are_legal_when_content_fills_source():
    geometry = {"content_rect": {"x": 0, "y": 0, "width": 10, "height": 8},
                "mode": "nine_slice", "borders": dict.fromkeys(("left", "top", "right", "bottom"), 0)}
    before = deepcopy(geometry)
    assert image_patches(geometry, [10, 8], [20, 16]) == {
        "image_size": [20, 16], "content_offset": [0, 0],
        "patches": [{"source": [0, 0, 10, 8], "target": [0, 0, 20, 16]}],
    }
    assert geometry == before
