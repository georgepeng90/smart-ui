"""验证分组的边界、流式排列和显式视觉中心。"""

import pytest

from smartui_mcp.layout_constraints import arrange_definition
from smartui_mcp.page_layouts import validate_definition


def image(identifier, width, height, **fields):
    return {"id": identifier, "kind": "image", "asset_id": "icon", "width": width, "height": height, **fields}


def text(identifier, width, height, **fields):
    return {"id": identifier, "kind": "text", "text": "A", "text_style_id": "label",
            "width": width, "height": height, "align": "left", "wrap": False, "overflow": "error", **fields}


def group(identifier, width, height, direction, children, **options):
    return {"id": identifier, "kind": "group", "width": width, "height": height,
            "layout": {"direction": direction, **options}, "children": children}


def definition(root):
    return validate_definition(200, 100, "#123456", [{**root, "x": 5, "y": 4}])


def test_page_checks_reference_current_nodes_and_reject_ambiguous_settings():
    nodes = [dict(image("background", 40, 20), x=0, y=0), dict(text("label", 30, 20), x=2, y=0)]
    checks = {"min_font_size": 16, "allow_overlap": [["background", "label"]], "visual_alignment_tolerance": 2}
    current = validate_definition(100, 60, "#123456", nodes, checks=checks)
    assert current["checks"] == checks and current["checks"] is not checks
    for invalid in ({"min_font_size": True}, {"min_font_size": 0}, {"visual_alignment_tolerance": -1},
                    {"allow_overlap": [["background", "missing"]]},
                    {"allow_overlap": [["background", "label"], ["label", "background"]]},
                    {"allow_overlap": [["label", "label"]]}, {"unknown": 1}):
        with pytest.raises(ValueError, match="checks"):
            validate_definition(100, 60, "#123456", nodes, checks=invalid)
    row = group("row", 50, 30, "row", [text("one", 20, 20), text("two", 20, 20)])
    nodes.append(dict(row, x=50, y=0))
    for pair in (["one", "two"], ["background", "one"]):
        with pytest.raises(ValueError, match="同级节点"):
            validate_definition(100, 60, "#123456", nodes, checks={"allow_overlap": [pair]})


def boxes(nodes):
    return {node["id"]: [node[field] for field in ("x", "y", "width", "height")] for node in nodes}


def test_nested_column_and_row_share_padding_edges_and_elastic_space():
    row = group("row", "fill", 20, "row", [image("icon", 11, 8), image("primary", "fill", "fill"),
                                          image("secondary", "fill", 6)], gap=3, align="center")
    root = group("screen", 100, 60, "column", [image("banner", 30, "fill"), row], padding=4, gap=5, align="end")
    leaves, groups, baselines = arrange_definition(definition(root))
    assert boxes(leaves) == {"banner": [71, 8, 30, 27], "icon": [9, 46, 11, 8],
                            "primary": [23, 40, 38, 20], "secondary": [64, 47, 37, 6]}
    assert groups[0]["content_box"] == [9, 8, 92, 52]
    assert groups[0]["node_ids"] == ["banner", "row"]
    assert groups[1]["box"] == [9, 40, 92, 20]
    assert not baselines


def test_equal_distribution_reflows_after_a_child_changes_size():
    row = group("row", 74, 20, "row", [image("left", 9, 6), image("middle", 10, 6), image("right", 11, 6)],
                gap=3, padding={"left": 5, "right": 4, "top": 2, "bottom": 2},
                align="end", justify="space-between")
    leaves, _, _ = arrange_definition(definition(row))
    assert boxes(leaves) == {"left": [10, 16, 9, 6], "middle": [37, 16, 10, 6], "right": [64, 16, 11, 6]}
    row["children"][1]["width"] = 22
    reflowed, _, _ = arrange_definition(definition(row))
    assert boxes(reflowed) == {"left": [10, 16, 9, 6], "middle": [31, 16, 22, 6], "right": [64, 16, 11, 6]}


def test_overlay_uses_explicit_visual_offset_without_changing_layer_order():
    overlay = group("button", 40, 28, "overlay",
                    [image("background", "fill", "fill"),
                     image("icon", 8, 6, visual_offset={"x": 2, "y": -1})],
                    align="center", justify="center")
    leaves, _, _ = arrange_definition(definition(overlay))
    assert [node["id"] for node in leaves] == ["background", "icon"]
    assert boxes(leaves) == {"background": [5, 4, 40, 28], "icon": [19, 16, 8, 6]}


def test_baseline_group_reserves_inner_y_range_for_browser_measurement():
    root = group("caption", 100, 40, "row", [text("title", 25, 30), text("units", 15, 15)],
                 padding=4, gap=5, align="baseline", justify="center")
    leaves, _, baselines = arrange_definition(definition(root))
    assert boxes(leaves) == {"title": [32, 8, 25, 30], "units": [62, 8, 15, 15]}
    assert baselines == [{"id": "caption", "y": 8, "height": 32, "node_ids": ["title", "units"]}]


@pytest.mark.parametrize("direction,children,options,match", [
    ("row", [image("duplicate", 10, 10), group("nest", 15, 15, "row", [image("duplicate", 5, 5)])],
     {}, "重复节点 ID：duplicate"),
    ("row", [image("icon", 10, 10, x=1)], {}, "多余.*x"),
    ("column", [image("icon", 10, 10)], {"align": "baseline"}, "layout.align"),
    ("row", [text("title", "content", 10, wrap=True)], {}, "content.*单行"),
    ("row", [text("title", 10, 10, text="A\nA")], {"align": "baseline"}, "基线分组.*单行"),
    ("overlay", [image("icon", 10, 10)], {"gap": 2}, "不适用于 overlay.*gap"),
    ("absolute", [image("icon", "fill", 10, x=0, y=0)], {}, "icon.width"),
])
def test_group_schema_rejects_ambiguous_or_invalid_constraints(direction, children, options, match):
    with pytest.raises(ValueError, match=match):
        definition(group("parent", 50, 30, direction, children, **options))


@pytest.mark.parametrize("node,match", [
    (group("parent", 10, 20, "row", [image("first", 7, 10), image("second", "fill", 10)],
           gap=2, padding=1), "parent.*width.*gap"),
    (group("parent", 20, 20, "absolute", [image("child", 18, 10, x=0, y=0)], padding=2), "child.*parent.*内边距"),
    (group("parent", 20, 20, "row", [image("child", 10, 10)], padding=10), "parent.*padding"),
    (group("parent", 20, 20, "overlay", [image("child", 8, 8, visual_offset={"x": 0, "y": 10})],
           align="center"), "child.*parent.*内边距"),
])
def test_infeasible_geometry_reports_the_affected_node_or_group(node, match):
    with pytest.raises(ValueError, match=match):
        arrange_definition(definition(node))


def test_root_safe_region_reflows_at_new_viewport_without_moving_background_into_safe_area():
    backdrop = {"id": "backdrop", "kind": "group", "region": "canvas", "layout": {"direction": "overlay"},
                "children": [image("art", "fill", "fill")]}
    content = {"id": "content", "kind": "group", "region": "safe", "layout": {"direction": "column", "gap": 2},
               "children": [image("header", "fill", 10), image("body", "fill", "fill"), image("footer", "fill", 8)]}
    safe_area = {"left": 6, "top": 8, "right": 4, "bottom": 3}

    def resolve(width, height):
        page = validate_definition(width, height, "#123456", [backdrop, content], safe_area)
        leaves, groups, _ = arrange_definition(page)
        assert page["safe_area"] == safe_area
        assert page["nodes"][1] == content
        assert groups[1]["region"] == "safe"
        assert groups[1]["content_box"] == [6, 8, width - 10, height - 11]
        return boxes(leaves)

    assert resolve(70, 100) == {"art": [0, 0, 70, 100], "header": [6, 8, 60, 10],
                               "body": [6, 20, 60, 67], "footer": [6, 89, 60, 8]}
    assert resolve(100, 70) == {"art": [0, 0, 100, 70], "header": [6, 8, 90, 10],
                               "body": [6, 20, 90, 37], "footer": [6, 59, 90, 8]}


def test_safe_region_requires_declared_insets_and_cannot_be_nested_or_given_coordinates():
    child = image("art", "fill", "fill")
    safe = {"id": "safe", "kind": "group", "region": "safe", "layout": {"direction": "overlay"}, "children": [child]}
    with pytest.raises(ValueError, match="safe.*显式声明 safe_area"):
        validate_definition(30, 40, "#123456", [safe])
    insets = {"left": 2, "top": 20, "right": 2, "bottom": 20}
    with pytest.raises(ValueError, match="safe_area.*正宽高"):
        validate_definition(30, 40, "#123456", [safe], insets)
    with pytest.raises(ValueError, match="safe_area.*完整"):
        validate_definition(30, 40, "#123456", [safe], {"top": 1})
    with pytest.raises(ValueError, match="多余.*x"):
        validate_definition(30, 50, "#123456", [{**safe, "x": 1}], insets)
    canvas = {**safe, "id": "root", "region": "canvas",
              "children": [{**safe, "width": "fill", "height": "fill"}]}
    with pytest.raises(ValueError, match="多余.*region"):
        validate_definition(30, 50, "#123456", [canvas], insets)
