"""验证 Chromium 的基线、有限缩字、布局提示及截图。"""

from copy import deepcopy
from io import BytesIO
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright
import pytest

from smartui_mcp.browser_preview import render_page
from smartui_mcp.layout_constraints import arrange_definition
from test_browser_preview import _sha, image_node, page, text_node


def test_different_font_sizes_align_on_the_rendered_baseline(tmp_path, page, text_node):
    small = text_node | dict(id="small", x=10, y=30, width=36, height=34, text="A", wrap=False,
                             font_size=16, line_height=24)
    large = small | dict(id="large", x=64, height=40, font_size=26, line_height=36)
    group = {"id": "labels", "y": 30, "height": 55, "node_ids": ["small", "large"]}
    page.update(background="transparent", nodes=[small, large], baseline_groups=[group])

    result = render_page(tmp_path / "aligned", page)
    a, b = result["renderer"]["nodes"]
    assert a["baseline"] == b["baseline"]
    assert a["box"][1] > a["layout_box"][1] == b["box"][1] == 30
    assert a["line_count"] == b["line_count"] == 1
    with Image.open(result["overview"]["path"]) as image:
        short = image.crop((0, 0, 50, image.height)).getbbox()
        tall = image.crop((50, 0, image.width, image.height)).getbbox()
        assert short[3] == tall[3]
        assert short[1] > tall[1]
    group["height"] = 40
    with pytest.raises(ValueError, match="small.*超出基线分组 labels"):
        render_page(tmp_path / "no-room", page)


def test_wrap_then_shrink_uses_the_largest_fitting_integer_font(tmp_path, page, text_node):
    text_node.update(text="A A", width=32, height=70, max_lines=1, overflow="shrink", min_font_size=16)
    page.update(nodes=[text_node], checks={"min_font_size": 19})
    original = deepcopy(page)

    result = render_page(tmp_path / "shrink", page)["renderer"]
    measured = result["nodes"][0]
    assert measured["font_size"] == 17
    assert measured["line_height"] == pytest.approx(23.8)
    assert measured["line_count"] == 1 and measured["overflow"] is False
    assert result["warnings"] == [{"type": "font_size_below_minimum", "nodes": [{"id": "中文文案", "kind": "text"}],
                                   "font_size": 17, "min_font_size": 19}]
    assert page == original
    text_node["min_font_size"] = 18
    with pytest.raises(ValueError, match="中文文案.*max_lines=1.*实际 2 行.*min_font_size=18"):
        render_page(tmp_path / "too-small", page)


def test_max_lines_errors_or_clips_in_accordance_with_policy(tmp_path, page, text_node):
    text_node.update(text="A\nA\nA", width=50, height=120, max_lines=1)
    page.update(background="transparent", nodes=[text_node])
    with pytest.raises(ValueError, match="中文文案.*max_lines=1.*实际 3 行"):
        render_page(tmp_path / "error", page)
    text_node["overflow"] = "clip"
    result = render_page(tmp_path / "clipped", page)
    measured = result["renderer"]["nodes"][0]
    assert measured["line_count"] == 3
    assert measured["line_overflow"] is measured["overflow"] is True
    assert measured["visible_box_source"] == "estimated_text_ink"
    visible_top, visible_height = measured["visible_box"][1::2]
    effects_top, effects_height = measured["effects_bounds"][1::2]
    assert visible_top + visible_height <= text_node["y"] + text_node["line_height"]
    assert effects_top + effects_height >= text_node["y"] + 3 * text_node["line_height"]
    with Image.open(result["overview"]["path"]) as clipped:
        _, png_top, _, png_bottom = clipped.getbbox()
        assert png_top == pytest.approx(visible_top, abs=1)
        assert png_bottom == pytest.approx(visible_top + visible_height, abs=1)
    text_node["line_height"] = 12
    stacked = render_page(tmp_path / "overlapping-lines", page)
    text_node["text"] = "A"
    first = render_page(tmp_path / "first-line", page)
    assert stacked["overview"]["sha256"] == first["overview"]["sha256"]
    assert stacked["renderer"]["nodes"][0]["visible_box"] == first["renderer"]["nodes"][0]["visible_box"]


def test_measured_content_insets_fit_a_real_glyph_overhang(tmp_path, page, text_node):
    text_node.update(text="J", width=16, height=28, wrap=False, content_insets={"left": 4, "right": 0})
    page["nodes"] = [text_node]
    result = render_page(tmp_path / "glyph", page)["renderer"]["nodes"][0]
    assert result["overflow"] is False
    assert result["content_box"][0] == text_node["x"] + 4
    assert result["effects_bounds"][0] >= text_node["x"]


def test_guides_toggle_and_never_enter_the_exported_png(tmp_path, page, image_node, text_node):
    path = Path(image_node["source"])
    picture = Image.new("RGBA", (6, 6))
    picture.paste((255, 0, 0, 128), (2, 1, 4, 5))
    picture.save(path)
    image_node.update(x=20, y=20, width=8, height=8, sha256=_sha(path), patch_plan={
        "image_size": [6, 6], "content_offset": [1, 1],
        "patches": [{"source": [0, 0, 6, 6], "target": [0, 0, 6, 6]}],
    })
    clear = image_node | {"id": "透明", "x": 180, "source": str(tmp_path / "clear.png")}
    Image.new("RGBA", (6, 6)).save(clear["source"])
    clear["sha256"] = _sha(clear["source"])
    page.update(nodes=[image_node, clear, text_node], safe_area={"top": 5, "right": 7, "bottom": 9, "left": 11})
    result = render_page(tmp_path / "guides", page)
    measured, transparent, text = result["renderer"]["nodes"]
    assert measured["layout_box"] == [20, 20, 8, 8]
    assert measured["visible_box"] == [23, 22, 2, 4]
    assert measured["visible_box_source"] == "canvas_alpha"
    assert transparent["visible_box"] is None
    assert text["visible_box_source"] == "estimated_text_ink"
    assert text["visible_box"][3] < text["effects_bounds"][3]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        tab = browser.new_page(viewport={"width": page["width"], "height": page["height"]})
        tab.goto(Path(result["html"]["path"]).as_uri())
        tab.evaluate("() => window.smartuiReady")
        for label, layer in (("辅助线", "guides"), ("内容框", "content"), ("可见边界", "visible"), ("文字基线", "baselines")):
            group = tab.locator(f".overlay-layer.overlay-{layer}")
            assert not group.is_visible()
            tab.get_by_label(label).check()
            assert group.is_visible()
        assert tab.locator(".overlay-visible [data-id='透明']").count() == 0
        tab.evaluate("() => window.smartuiForScreenshot()")
        assert not tab.locator("#overlay").is_visible()
        captured = tab.screenshot(omit_background=True)
        browser.close()
    with Image.open(result["overview"]["path"]) as normal, Image.open(BytesIO(captured)) as toggled:
        assert normal.tobytes() == toggled.tobytes()


def test_overlap_warnings_respect_declared_intent_and_check_component_usage(tmp_path, page, image_node, text_node):
    image_node.update(x=20, y=30, width=32, height=28)
    text_node.update(text="AA", width=70, height=40)
    node_ids = [image_node["id"], text_node["id"]]
    group = {"id": "button", "direction": "absolute", "box": [10, 20, 90, 70],
             "content_box": [10, 20, 90, 70], "node_ids": node_ids}
    states = {"required": ["normal", "pressed", "disabled"], "available": ["normal"],
              "missing": ["pressed"], "stale": ["disabled"]}
    usage = {"component_id": "button", "text_rect": [50, 35, 40, 20], "hit_rect": [7, 17, 96, 76],
             "pivot": {"x": 55, "y": 55}, "states": states}
    page.update(nodes=[image_node, text_node], layout_groups=[group], component_usage={"button": usage})
    warnings = render_page(tmp_path / "check", page)["renderer"]["warnings"]
    assert warnings == [
        {"type": "content_overlap", "group_id": "button",
         "nodes": [{"id": image_node["id"], "kind": "image"}, {"id": text_node["id"], "kind": "text"}]},
        {"type": "missing_states", "group_id": "button", "component_id": "button", "missing": ["pressed"]},
        {"type": "stale_states", "group_id": "button", "component_id": "button", "stale": ["disabled"]},
        {"type": "text_outside_component_rect", "group_id": "button", "component_id": "button",
         "nodes": [{"id": text_node["id"], "kind": "text"}]},
    ]
    page["checks"] = {"allow_overlap": [node_ids[::-1]]}
    assert render_page(tmp_path / "allowed", page)["renderer"]["warnings"] == warnings[1:]
    del page["checks"]
    group["direction"] = "overlay"
    assert render_page(tmp_path / "overlay", page)["renderer"]["warnings"] == warnings[1:]
    del page["layout_groups"], page["component_usage"]
    assert render_page(tmp_path / "root-overlap", page)["renderer"]["warnings"] == [warnings[0] | {"group_id": None}]
    page["checks"] = {"allow_overlap": [node_ids]}
    assert render_page(tmp_path / "root-allowed", page)["renderer"]["warnings"] == []


@pytest.mark.parametrize("direction,offset,expected", [
    ("row", {"x": 0, "y": -3}, [("y", -3)]),
    ("column", {"x": -3, "y": 0}, [("x", -3)]),
    ("overlay", {"x": -3, "y": -3}, [("x", -3), ("y", -3)]),
])
def test_visual_alignment_reports_alpha_offset_until_calibrated(tmp_path, page, image_node,
                                                               direction, offset, expected):
    picture = Image.new("RGBA", (8, 8))
    path = Path(image_node["source"])
    picture.paste((255, 0, 0, 255), (0, 0, 2, 2))
    picture.save(path)
    image_node.update(width=8, height=8, sha256=_sha(path), patch_plan={
        "image_size": [8, 8], "content_offset": [0, 0],
        "patches": [{"source": [0, 0, 8, 8], "target": [0, 0, 8, 8]}],
    })
    clear = image_node | {"id": "clear", "source": str(tmp_path / "clear.png")}
    Image.new("RGBA", (8, 8)).save(clear["source"])
    clear["sha256"] = _sha(clear["source"])
    group = {"id": "line", "kind": "group", "x": 20, "y": 20, "width": 40, "height": 40,
             "layout": {"direction": direction, "align": "center"}, "children": [image_node, clear]}
    if direction == "overlay":
        group["layout"]["justify"] = "center"
    definition = page | {"nodes": [group], "checks": {"visual_alignment_tolerance": 1}}

    def render(name):
        nodes, groups, _ = arrange_definition(definition)
        return render_page(tmp_path / name, definition | {"nodes": nodes, "layout_groups": groups})["renderer"]

    assert render("uncalibrated")["warnings"] == [
        {"type": "visual_alignment_offset", "group_id": "line", "node_id": image_node["id"], "node_kind": "image",
         "axis": axis, "offset": distance, "tolerance": 1} for axis, distance in expected
    ]
    image_node["visual_offset"] = offset
    assert render("calibrated")["warnings"] == []
