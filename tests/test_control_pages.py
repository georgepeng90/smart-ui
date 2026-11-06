"""验证控件实例的静态布局、预览与依赖记录。"""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from smartui_mcp.page_builds import build_page, verify_build
from smartui_mcp.page_dependencies import matches_unity_controls, page_source_issues
from smartui_mcp.page_layouts import resolve_definition, validate_definition
from smartui_mcp.page_variants import preview_layout_variants
from smartui_mcp.projects import spec_sha256
from smartui_mcp.unity import UnityStore
from smartui_mcp.unity_builds import UnityBuildStore
from test_compositions import text_node
from test_page_layouts import exported_project, image_node, label, project_font, save, typography_project, typography_spec
from test_review_workflow import project
from test_unity import button_definition


def control(instance_id, control_id="button.primary", **fields):
    return {"id": instance_id, "kind": "control", "control_id": control_id,
            "width": 44, "height": 34, "parameters": {}, **fields}


@pytest.fixture
def control_state(typography_project, label):
    _, context = typography_project
    state = context["data"]
    state["unity_controls"] = {
        "button.primary": {
            "width": 44, "height": 34,
            "nodes": [
                image_node("background", x=0, y=0, width=44, height=34),
                {**label, "x": 0, "y": 2, "width": 44, "wrap": False, "text": "A"},
            ],
            "slots": {"title": {"node_id": "label", "component": "text"}},
            "parameters": {"caption": {"slot_id": "title", "property": "text", "value_type": "string",
                                       "required": False, "description": "按钮标题"}},
            "dependencies": {"button.primary": "1" * 64},
        }
    }
    return context["project_root"], state


def test_same_control_has_separate_preview_text_and_keeps_static_instance_tree(control_state):
    _, state = control_state
    nodes = [{"id": "actions", "kind": "group", "x": 4, "y": 3, "width": 94, "height": 38,
              "layout": {"direction": "row", "padding": 2, "gap": 2},
              "children": [control("accept", parameters={"caption": "A A"}), control("cancel", width="fill")]}]
    definition = validate_definition(110, 48, "#123456", nodes)
    before = deepcopy(definition)
    resolved = resolve_definition(state, definition, include_layout_tree=True)
    assert definition == before
    assert [(node["id"], node["x"], node["y"]) for node in resolved["nodes"]] == [
        ("accept/background", 6, 5), ("accept/label", 6, 7),
        ("cancel/background", 52, 5), ("cancel/label", 52, 7),
    ]
    assert [node["text"] for node in resolved["nodes"] if node["kind"] == "text"] == ["A A", "A"]
    actions = resolved["layout_tree"][0]
    assert actions["children"] == [
        control("accept", x=6, y=5, parameters={"caption": "A A"}),
        control("cancel", x=52, y=5),
    ]
    assert "control_dependencies" in resolved


def test_nested_control_uses_instance_namespace_and_transitive_snapshot_dependencies(control_state):
    root, state = control_state
    dependencies = {"button.primary": "1" * 64, "card.actions": "2" * 64}
    state["unity_controls"]["card.actions"] = {
        "width": 48, "height": 38, "nodes": [control("button", x=2, y=2, parameters={"caption": "A A"})],
        "slots": {}, "parameters": {}, "dependencies": dependencies,
    }
    nodes = [control("card", "card.actions", x=2, y=2, width=48, height=38)]
    definition = validate_definition(64, 48, "#123456", nodes)
    build = build_page(root, "screen", "screen", spec_sha256(definition), definition, state)
    verify_build(build)
    assert build["control_dependencies"] == dependencies
    assert [node["id"] for node in build["nodes"]] == ["card/button/background", "card/button/label"]
    assert build["renderer"]["warnings"] == []
    inputs = json.loads(Path(build["files"]["inputs"]["path"]).read_text(encoding="utf-8"))
    assert inputs["control_dependencies"] == dependencies
    assert matches_unity_controls(state, build)
    state["unity_controls"]["button.primary"]["dependencies"]["button.primary"] = "3" * 64
    assert not matches_unity_controls(state, build)
    verify_build(build)


def test_control_images_participate_in_current_export_checks(control_state):
    _, state = control_state
    issue = {"asset_id": "button", "reason": "export_run_changed"}
    assert page_source_issues(state, [control("accept", x=0, y=0)], {"button": issue}) == [issue]


def test_control_requires_declared_design_size_and_keeps_business_bindings_separate(control_state):
    _, state = control_state
    with pytest.raises(ValueError, match="accept.*bindings"):
        validate_definition(64, 48, "#123456", [control("accept", x=0, y=0, bindings={})])
    definition = validate_definition(64, 48, "#123456", [control("accept", x=0, y=0, width=40)])
    with pytest.raises(ValueError, match="accept.*40×34.*44×34"):
        resolve_definition(state, definition)
    definition = validate_definition(64, 48, "#123456", [control("accept", x=0, y=0, parameters={"unused": "A"})])
    with pytest.raises(ValueError, match="accept.*unused"):
        resolve_definition(state, definition)


def test_project_uses_current_recipe_without_changing_saved_page_or_snapshot(typography_project, label):
    store, context = typography_project
    root = context["project_root"]
    definition = button_definition()
    definition["text_style_ids"] = ["button-label"]
    definition["parameters"]["caption"]["required"] = False
    unity = UnityStore(store)
    unity.define_control(root, None, "button.primary", definition)
    builder = UnityBuildStore(unity)
    caption = {**label, "x": 0, "y": 2, "width": 44, "wrap": False, "text": "A"}
    recipe = {"kind": "base", "width": 44, "height": 34, "button": None,
              "slots": {"label": {"node_id": "label", "component": "text"}},
              "nodes": [image_node("background", x=0, y=0, width=44, height=34), caption]}
    construction = builder.define_recipe(root, None, "button.primary", recipe)

    saved = save(store, context, [control("accept", x=2, y=2)])
    built = store.build_layout(root, saved["revision"], "screen")
    assert built["is_current"] and built["matches_unity_controls"]
    layout = Path(root) / "smart-ui/layouts/screen.json"
    layout_bytes = layout.read_bytes()
    index = json.loads((Path(root) / "smart-ui/project.json").read_bytes())
    assert "unity_controls" not in index
    dependencies = store.page_dependencies(root)
    assert dependencies["unity_controls"]["button.primary"]["uses"] == [{"layout_id": "screen", "node_id": "accept"}]
    assert dependencies["text_styles"]["button-label"] == [{"layout_id": "screen", "node_id": "accept/label"}]
    assert dependencies["assets"]["button"]["uses"] == [{"layout_id": "screen", "node_id": "accept/background"}]

    caption["text"] = "A A"
    builder.define_recipe(root, construction["revision"], "button.primary", recipe)
    stale = store.get_page_build(root, "screen")
    assert stale["revision"] == built["revision"]
    assert stale["matches_layout"] and not stale["matches_unity_controls"] and not stale["is_current"]
    assert stale["page_build"] == built["page_build"]
    assert layout.read_bytes() == layout_bytes
    assert store.page_dependencies(root)["pages"]["screen"]["stale_reasons"] == ["unity_controls_changed"]
    rebuilt = store.build_layout(root, stale["revision"], "screen")
    assert rebuilt["is_current"]
    assert rebuilt["page_build"]["nodes"][1]["text"] == "A A"
    preview = preview_layout_variants(store, root, rebuilt["revision"], "screen", [
        {"id": "wide", "width": 80, "height": 48, "texts": {}},
    ])
    report = json.loads(Path(preview["scenes"][0]["report"]).read_bytes())
    assert report["control_dependencies"] == rebuilt["page_build"]["control_dependencies"]
    assert report["definition"]["nodes"] == saved["layout"]["definition"]["nodes"]
