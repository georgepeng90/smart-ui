"""当前布局、资产解析、字体校验与工程位置变更。"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from smartui_mcp import typography
from smartui_mcp.page_layouts import resolve_definition, validate_definition
from smartui_mcp.projects import ProjectError, spec_sha256
from test_compositions import text_node
from test_export_workflow import export_item
from test_review_workflow import project, record_sample_run


@pytest.fixture
def exported_project(project):
    store, context = project
    exported = store.export_assets(context["project_root"], context["revision"], "sample", [export_item()])
    return store, store.context(exported["project_root"])


@pytest.fixture
def project_font(text_node, exported_project):
    _, context = exported_project
    font = Path(context["project_root"]) / "Assets/Fonts/fixture.ttf"
    font.parent.mkdir(parents=True)
    shutil.copyfile(text_node["font_path"], font)
    return font


@pytest.fixture
def typography_spec(project_font):
    return {"description": "测试页面中的操作文字", "design_size": {"width": 64, "height": 48},
            "size_scale": {"standard": 20}, "styles": {
                "button-label": {"name": "操作文字", "usage": "用于短按钮文案", "size_id": "standard",
                                 "font_path": str(project_font), "line_height": 24, "letter_spacing": 0,
                                 "fill": {"color": "#FF0000", "opacity": 1}, "stroke": None, "shadow": None,
                                 "evidence": []}}}


@pytest.fixture
def typography_project(exported_project, typography_spec):
    store, context = exported_project
    return store, store.update_typography(context["project_root"], context["revision"], typography_spec)


@pytest.fixture
def label():
    return {"id": "label", "kind": "text", "text": "A A", "text_style_id": "button-label",
            "x": 2, "y": 2, "width": 48, "height": 30, "align": "center", "wrap": True, "overflow": "error"}


def image_node(identifier="button-instance", **changes):
    return {"id": identifier, "kind": "image", "asset_id": "button", "x": 2, "y": 2,
            "width": 18, "height": 12, **changes}


def save(store, context, nodes, **changes):
    arguments = {"layout_id": "screen", "name": "测试页面", "width": 64, "height": 48,
                 "background": "#123456", "nodes": nodes, **changes}
    return store.save_layout(context["project_root"], context["revision"], **arguments)


def project_bytes(context):
    return (Path(context["project_root"]) / "smart-ui/project.json").read_bytes()


def test_instances_resolve_the_current_asset_without_mutating_the_layout(exported_project):
    store, context = exported_project
    root = context["project_root"]
    saved = save(store, context, [image_node("first"), image_node("second", x=25)])
    original = deepcopy(saved["layout"]["definition"])
    first = resolve_definition(store.context(root)["data"], original)
    second_run = record_sample_run(store, store.context(root), "second", (20, 140, 240, 255))
    store.export_assets(root, second_run["revision"], "sample", [export_item()])
    current = store.context(root)
    state_before = deepcopy(current["data"])
    resolved = resolve_definition(current["data"], original)
    digest = current["data"]["images"]["button"]["sha256"]
    assert [node["sha256"] for node in resolved["nodes"]] == [digest, digest]
    assert digest != first["nodes"][0]["sha256"]
    assert original == saved["layout"]["definition"]
    assert current["data"] == state_before
    assert store.get_layout(root, "screen")["layout"]["definition"] == original


def test_style_reference_roundtrip_allows_editing_and_resaving_the_returned_definition(typography_project, label):
    store, context = typography_project
    saved = save(store, context, [image_node(), label])
    fetched = store.get_layout(context["project_root"], "screen")
    definition = fetched["layout"]["definition"]
    text = definition["nodes"][1]
    assert text == label
    assert definition == saved["layout"]["definition"]
    text["text"] = "A\nA"
    updated = store.save_layout(context["project_root"], fetched["revision"], "screen", "测试页面", **definition)
    assert updated["layout"]["sha256"] != saved["layout"]["sha256"]
    assert updated["layout"]["definition"]["nodes"][1]["text_style_id"] == "button-label"
    assert updated["layout"]["definition"]["nodes"][1]["text"] == "A\nA"
    assert store.get_layout(context["project_root"], "screen")["layout"] == updated["layout"]


def test_pages_share_a_role_style_without_copying_its_appearance_into_the_layout(typography_project, label, project_font):
    store, context = typography_project
    first = save(store, context, [label])
    second = save(store, first, [{**label, "text": "AA", "align": "right"}], layout_id="second")
    state = store.context(context["project_root"])["data"]
    style = typography.resolve_style(state["typography"], "button-label")
    style_digest = typography.style_digest(state["typography"], "button-label")
    for saved in (first, second):
        definition = saved["layout"]["definition"]
        original = deepcopy(definition)
        resolved = resolve_definition(state, definition)["nodes"][0]
        assert resolved == {**original["nodes"][0], **style, "text_style_sha256": style_digest}
        assert definition == original
        assert not style.keys() & original["nodes"][0].keys()
        assert resolved["font_sha256"] == hashlib.sha256(project_font.read_bytes()).hexdigest()


def test_moving_shared_instances_updates_the_layout_and_preserves_the_assets(exported_project):
    store, context = exported_project
    nodes = [image_node("first"), image_node("second", x=25)]
    saved = save(store, context, nodes)
    assets = deepcopy(store.context(context["project_root"])["data"]["images"])
    changed = deepcopy(nodes)
    changed[0]["x"] = 4
    changed[1]["y"] = 20
    updated = save(store, saved, changed)
    assert updated["layout"]["sha256"] != saved["layout"]["sha256"]
    current = store.context(context["project_root"])
    assert current["data"]["images"] == assets
    assert current["data"]["layouts"]["screen"] == updated["layout"]
    assert set(current["data"]["layouts"]["screen"]) == {"id", "name", "definition", "sha256"}
    assert saved["layout"]["definition"]["nodes"] == nodes
    assert updated["layout"]["definition"]["nodes"] == changed


def test_identical_save_reuses_project_revision(typography_project, label):
    store, context = typography_project
    saved = save(store, context, [image_node(), label])
    before = project_bytes(saved)
    definition = store.get_layout(context["project_root"], "screen")["layout"]["definition"]
    again = store.save_layout(context["project_root"], saved["revision"], "screen", "测试页面", **definition)
    assert again == saved
    assert project_bytes(again) == before


def test_layout_rename_updates_current_name_without_changing_definition(exported_project):
    store, context = exported_project
    first = save(store, context, [image_node()], name="背包页面")
    second = save(store, first, [image_node()], name="物品详情页面")
    assert second["layout"]["name"] == "物品详情页面"
    assert second["layout"]["sha256"] == first["layout"]["sha256"]
    assert store.get_layout(context["project_root"], "screen")["layout"] == second["layout"]


def test_layout_accepts_512_nodes_without_the_static_composition_limit(exported_project):
    store, context = exported_project
    nodes = [image_node(f"instance-{index}") for index in range(512)]
    saved = save(store, context, nodes)
    assert len(saved["layout"]["definition"]["nodes"]) == 512
    assert len(store.get_layout(context["project_root"], "screen")["layout"]["definition"]["nodes"]) == 512
    before = project_bytes(saved)
    with pytest.raises(ValueError, match="512"):
        save(store, saved, [*nodes, image_node("too-many")])
    assert project_bytes(saved) == before


@pytest.mark.parametrize("change,message", [
    ({"kind": "component"}, "kind"), ({"id": "../invalid"}, "id"),
    ({"unknown": True}, "字段"), ({"x": True}, "x"), ({"width": 0}, "width"),
    ({"x": 63}, "超出画布"), ({"binding": {"kind": "fixed", "version": 1}}, "字段"),
    ({"asset_id": "../invalid"}, "asset_id"),
])
def test_invalid_image_schema_never_saves_a_partial_layout(exported_project, change, message):
    store, context = exported_project
    before = project_bytes(context)
    with pytest.raises(ValueError, match=message):
        save(store, context, [image_node(), image_node("invalid", **change)])
    assert project_bytes(context) == before
    assert "screen" not in store.context(context["project_root"])["data"].get("layouts", {})


@pytest.mark.parametrize("change,message", [
    ({"overflow": "shrink"}, "min_font_size"), ({"wrap": 1}, "wrap"),
    ({"max_lines": 0}, "max_lines"), ({"min_font_size": 12}, "min_font_size"),
    ({"align": "justify"}, "align"), ({"text_style_id": "../invalid"}, "text_style_id"),
    ({"text": ""}, "text"), ({"text": "A\tA"}, "控制字符"),
    ({"unknown": 1}, "字段"), ({"font_path": "fixture.ttf"}, "font_path"),
    ({"font_size": 20}, "font_size"), ({"line_height": 24}, "line_height"),
    ({"color": "#FF0000"}, "color"), ({"fill": {"color": "#FF0000", "opacity": 1}}, "fill"),
    ({"stroke": None}, "stroke"), ({"shadow": None}, "shadow"),
])
def test_invalid_text_policy_is_rejected(exported_project, label, change, message):
    store, context = exported_project
    before = project_bytes(context)
    with pytest.raises(ValueError, match=message):
        save(store, context, [{**label, **change}])
    assert project_bytes(context) == before


def test_text_layout_requires_a_project_typography_spec(exported_project, label):
    store, context = exported_project
    before = project_bytes(context)
    with pytest.raises(ValueError, match="label.*button-label.*文字规范"):
        save(store, context, [label])
    assert project_bytes(context) == before


def test_unknown_text_style_is_rejected(typography_project, label):
    store, context = typography_project
    before = project_bytes(context)
    with pytest.raises(ValueError, match="label.*不存在的文字样式.*unknown"):
        save(store, context, [{**label, "text_style_id": "unknown"}])
    assert project_bytes(context) == before


def test_shrink_minimum_is_bounded_by_the_project_role_size(typography_project, label):
    store, context = typography_project
    with pytest.raises(ValueError, match="min_font_size.*字号 20"):
        save(store, context, [{**label, "overflow": "shrink", "min_font_size": 21}])


@pytest.mark.parametrize("failure", ["missing", "missing_character"])
def test_font_errors_identify_the_node_and_source(typography_project, label, project_font, failure):
    store, context = typography_project
    if failure == "missing":
        project_font.unlink()
    else:
        label["text"] = "B"
    before = project_bytes(context)
    with pytest.raises((ValueError, FileNotFoundError)) as error:
        save(store, context, [label])
    assert "fixture.ttf" in str(error.value)
    if failure == "missing_character":
        assert "label" in str(error.value)
        assert "U+0042" in str(error.value)
    assert project_bytes(context) == before


def test_font_changed_since_typography_update_cannot_be_accepted_silently(typography_project, label, project_font):
    store, context = typography_project
    saved = save(store, context, [label])
    definition = deepcopy(saved["layout"]["definition"])
    project_font.write_bytes(project_font.read_bytes() + b"changed font bytes")
    before = project_bytes(saved)
    with pytest.raises(ValueError, match="字体 SHA-256 不符"):
        store.save_layout(context["project_root"], saved["revision"], "screen", "测试页面", **definition)
    assert project_bytes(saved) == before


@pytest.mark.parametrize("width,height", [(1, 12), (18, 1), (2, 2)])
def test_nine_slice_minimum_dimensions_are_enforced_before_save(exported_project, width, height):
    store, context = exported_project
    before = project_bytes(context)
    with pytest.raises(ValueError, match="固定边之和"):
        save(store, context, [image_node(width=width, height=height)])
    assert project_bytes(context) == before


def test_missing_asset_and_duplicate_instance_ids_are_explicit_errors(exported_project):
    store, context = exported_project
    before = project_bytes(context)
    with pytest.raises(ValueError, match="不存在的资产"):
        save(store, context, [image_node(asset_id="missing")])
    with pytest.raises(ValueError, match="重复节点 ID"):
        save(store, context, [image_node(), image_node()])
    assert project_bytes(context) == before


def test_changed_asset_file_is_rejected_by_resolution_and_save(exported_project):
    store, context = exported_project
    saved = save(store, context, [image_node()])
    state = store.context(context["project_root"])["data"]
    path = Path(state["images"]["button"]["path"])
    path.write_bytes(b"manual asset edit")
    before = project_bytes(saved)
    with pytest.raises(ValueError, match="SHA-256"):
        resolve_definition(state, saved["layout"]["definition"])
    with pytest.raises(ValueError, match="SHA-256"):
        save(store, saved, [image_node(x=4)])
    assert project_bytes(saved) == before


def test_project_move_preserves_relative_font_path_and_layout_digest(typography_project, label, tmp_path):
    store, context = typography_project
    root = Path(context["project_root"])
    saved = save(store, context, [image_node(), label])
    disk = json.loads(project_bytes(saved))
    path = root / disk["layouts"]["screen"]
    assert path == root / "smart-ui/layouts/screen.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["definition"]["nodes"][1] == label
    spec = json.loads((root / "smart-ui/typography.json").read_text(encoding="utf-8"))
    assert spec["styles"]["button-label"]["font_path"] == "Assets/Fonts/fixture.ttf"
    previous = resolve_definition(context["data"], saved["layout"]["definition"])
    moved = tmp_path / "移动后的布局工程"
    shutil.move(root, moved)
    restored = store.get_layout(str(moved), "screen")
    assert restored["layout"]["definition"]["nodes"][1] == label
    assert restored["layout"]["sha256"] == saved["layout"]["sha256"]
    current = store.context(str(moved))
    resolved = resolve_definition(current["data"], restored["layout"]["definition"])
    assert Path(resolved["nodes"][0]["source"]).is_relative_to(moved)
    assert Path(resolved["nodes"][1]["font_path"]) == moved / "Assets/Fonts/fixture.ttf"
    assert resolved["nodes"][1]["text_style_sha256"] == previous["nodes"][1]["text_style_sha256"]
    before = project_bytes(restored)
    repeat = store.save_layout(str(moved), restored["revision"], "screen", "测试页面", **restored["layout"]["definition"])
    assert repeat["layout"] == restored["layout"]
    assert repeat["revision"] == restored["revision"]
    assert project_bytes(repeat) == before


@pytest.mark.parametrize(("location", "message"), [
    ("outside", "越出工程根目录"), ("local", r"不能依赖 \.smartui/"),
])
def test_static_composition_rejects_nonpersistent_fonts(exported_project, text_node, location, message):
    store, context = exported_project
    root = Path(context["project_root"])
    parent = root.parent if location == "outside" else root / ".smartui"
    font = parent / "untracked.ttf"
    shutil.copyfile(text_node["font_path"], font)
    before = project_bytes(context)
    with pytest.raises(ValueError, match=message):
        store.create_composition(str(root), context["revision"], "composition", 64, 48, "#123456",
                                 [{**text_node, "font_path": str(font)}])
    assert project_bytes(context) == before


def test_layout_and_composition_reject_ids_that_differ_only_in_case(exported_project, project_font, text_node):
    store, context = exported_project
    root = context["project_root"]
    saved = save(store, context, [image_node()])
    node = {**text_node, "font_path": str(project_font)}
    composed = store.create_composition(root, saved["revision"], "composition", 64, 48, "#123456", [node])
    before = project_bytes(composed)
    with pytest.raises(ProjectError, match="layout_id"):
        save(store, composed, [image_node(x=6)], layout_id="Screen")
    with pytest.raises(ProjectError, match="composition_id"):
        store.create_composition(root, composed["revision"], "Composition", 64, 48, "#123456", [node])
    assert project_bytes(composed) == before


def test_revision_conflict_never_writes_the_attempted_definition(exported_project):
    store, context = exported_project
    saved = save(store, context, [image_node()])
    before = project_bytes(saved)
    with pytest.raises(ProjectError, match="revision"):
        save(store, context, [image_node(x=8)])
    assert project_bytes(saved) == before
    assert store.get_layout(context["project_root"], "screen")["layout"] == saved["layout"]


def test_validation_preserves_the_input_objects(label):
    nodes = [image_node(), label]
    before = deepcopy(nodes)
    definition = validate_definition(64, 48, "#123456", nodes)
    assert nodes == before
    assert definition["nodes"] == before
    assert definition["nodes"][1] is not label


def test_group_relative_geometry_roundtrips_and_moves_as_a_unit(exported_project):
    store, context = exported_project
    group = {"id": "panel", "kind": "group", "x": 2, "y": 4, "width": 37, "height": 26,
             "layout": {"direction": "absolute", "padding": 2},
             "children": [image_node("first", x=1, y=1), image_node("second", x=15, y=10)]}
    saved = save(store, context, [group])
    definition = store.get_layout(context["project_root"], "screen")["layout"]["definition"]
    assert definition["nodes"] == [group]
    state = store.context(context["project_root"])["data"]
    before = resolve_definition(state, definition)
    moved = {**definition["nodes"][0], "x": 7, "y": 6}
    updated = save(store, saved, [moved])
    after = resolve_definition(state, updated["layout"]["definition"])
    assert after["layout_groups"][0]["content_box"] == [9, 8, 33, 22]
    assert [(node["id"], node["x"], node["y"]) for node in before["nodes"]] == [("first", 5, 7), ("second", 19, 16)]
    assert [(node["id"], node["x"], node["y"]) for node in after["nodes"]] == [("first", 10, 9), ("second", 24, 18)]
    assert [node["sha256"] for node in after["nodes"]] == [node["sha256"] for node in before["nodes"]]


def test_nested_asset_reference_and_impossible_layout_are_checked_before_save(exported_project):
    store, context = exported_project
    node = image_node("action", width="fill")
    node.pop("x")
    node.pop("y")
    group = {"id": "buttons", "kind": "group", "x": 2, "y": 2, "width": 40, "height": 16,
             "layout": {"direction": "row", "gap": 3, "padding": 2},
             "children": [node]}
    before = project_bytes(context)
    with pytest.raises(ValueError, match="action.*不存在的资产"):
        save(store, context, [{**group, "children": [{**node, "asset_id": "missing"}]}])
    with pytest.raises(ValueError, match="buttons.*width.*gap"):
        save(store, context, [{**group, "width": 20,
                               "children": [node, {**node, "id": "close", "width": 18}]}])
    assert project_bytes(context) == before


def test_chromium_content_width_reflows_adjacent_icon_and_elastic_region(typography_project, label):
    _, context = typography_project
    caption = {**label, "text": "A", "width": "content", "wrap": False}
    icon = image_node("icon")
    elastic = image_node("elastic", width="fill")
    for child in (caption, icon, elastic):
        del child["x"], child["y"]
    group = {"id": "action", "kind": "group", "x": 2, "y": 2, "width": 90, "height": 42,
             "layout": {"direction": "row", "padding": 2, "gap": 3, "align": "center"},
             "children": [caption, icon, elastic]}

    def resolve():
        definition = validate_definition(96, 48, "#123456", [group])
        return resolve_definition(context["data"], definition)["nodes"]

    short_label, short_icon, short_elastic = resolve()
    caption["text"] = "AAA"
    long_label, long_icon, long_elastic = resolve()
    # 测试字体 A 的字宽为字号的 60%，字号为 20 px。
    assert long_label["width"] - short_label["width"] == 24
    assert long_icon["x"] - short_icon["x"] == 24
    assert short_elastic["width"] - long_elastic["width"] == 24
    assert short_elastic["x"] + short_elastic["width"] == long_elastic["x"] + long_elastic["width"] == 90
    assert short_icon["sha256"] == long_icon["sha256"]


def test_component_group_uses_current_shared_padding_and_exposes_usage_geometry(exported_project):
    _, context = exported_project
    state = deepcopy(context["data"])
    usage = {
        "padding": {"top": 2, "right": 3, "bottom": 4, "left": 5},
        "text_insets": {"top": 3, "right": 4, "bottom": 5, "left": 6},
        "hit_insets": {"top": -1, "right": -2, "bottom": -1, "left": -2},
        "pivot": {"x": 0.5, "y": 0.5}, "required_states": ["normal", "pressed"],
    }
    state["components"]["sample"]["spec"]["usage"] = usage
    group = {"id": "action", "kind": "group", "component_id": "sample",
             "x": 2, "y": 4, "width": 40, "height": 34, "layout": {"direction": "absolute"},
             "children": [image_node("face", x=0, y=0)]}
    definition = validate_definition(64, 48, "#123456", [group])
    resolved = resolve_definition(state, definition)
    assert definition["nodes"][0]["layout"] == {"direction": "absolute"}
    assert resolved["layout_groups"][0]["content_box"] == [7, 6, 32, 28]
    assert resolved["nodes"][0]["x"] == 7 and resolved["nodes"][0]["y"] == 6
    assert resolved["component_usage"]["action"] == {
        "component_id": "sample", "usage_sha256": spec_sha256(usage), "padding": usage["padding"],
        "text_rect": [8, 7, 30, 26], "hit_rect": [0, 3, 44, 36], "pivot": {"x": 22, "y": 21},
        "required_states": ["normal", "pressed"],
        "states": {"required": ["normal", "pressed"], "missing": ["pressed"], "stale": ["normal"], "available": []},
    }
    definition["nodes"][0]["layout"]["padding"] = 1
    with pytest.raises(ValueError, match="action.layout.padding.*sample.*共享使用规范"):
        resolve_definition(state, definition)
