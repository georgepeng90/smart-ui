"""检验控件制作定义、覆盖和预览参数的纯数据约定。"""

from copy import deepcopy

import pytest

from smartui_mcp.projects import ProjectError
from smartui_mcp.unity_recipes import control_preview, parse_recipe, resolve_recipes


def definition(**changes):
    return {
        "name": "控件", "description": "", "control_type": "button",
        "base_control_id": None, "dependencies": [], "source_component_ids": [],
        "asset_ids": [], "text_style_ids": [], "preview_asset_id": None,
        "layout": {"min_width": 1, "min_height": 1, "max_width": None, "max_height": None},
        "slots": {}, "parameters": {}, "states": {},
        **changes,
    }


def slot(kind):
    return {"kind": kind, "component_type": kind, "required": True, "description": ""}


def node(kind, identifier, **fields):
    return {"kind": kind, "id": identifier, "x": 0, "y": 0, "width": 100, "height": 20, **fields}


def recipe(*nodes, slots=None, button=None, kind="base"):
    return {"kind": kind, "width": 100, "height": 20, "nodes": list(nodes),
            "slots": slots or {}, "button": button}


@pytest.fixture
def button_inputs():
    state = {"typography": {"styles": {"label": {}}}, "images": {"bg": {}, "pressed": {}, "alt": {}},
             "components": {}}
    definitions = {
        "button": definition(
            slots={"title": slot("text"), "background": slot("image"), "click": slot("event")},
            parameters={
                "caption": {"slot_id": "title", "property": "text", "value_type": "string",
                            "required": True, "description": ""},
            },
            states={"pressed": {"background": "pressed"}},
        ),
        "alt": definition(base_control_id="button"),
        "wrapper": definition(dependencies=["alt"]),
        "unbuilt": definition(),
    }
    recipes = {
        "button": recipe(
            node("image", "bg", asset_id="bg"),
            node("text", "title", text="默认", text_style_id="label", align="center", wrap=False, overflow="error"),
            slots={
                "title": {"node_id": "title", "component": "text"},
                "background": {"node_id": "bg", "component": "image"},
                "click": {"node_id": "root", "component": "button"},
            },
            button={"target_graphic": "bg", "states": {"pressed": "pressed"}},
        ),
        "alt": {"kind": "variant", "overrides": [{"node_id": "bg", "property": "asset_id", "value": "alt"}]},
        "wrapper": recipe(node("control", "inner", control_id="alt", parameters={"caption": "嵌套"})),
    }
    return definitions, recipes, state


def test_resolves_nested_variant_and_applies_preview_parameters(button_inputs):
    definitions, recipes, state = button_inputs
    before = deepcopy(recipes)
    resolved = resolve_recipes(definitions, recipes, state)
    assert set(resolved) == {"button", "alt", "wrapper"}
    assert resolved["button"]["nodes"][0]["asset_id"] == "bg"
    preview = control_preview(resolved, "alt", {"caption": "实例"})
    assert (preview["width"], preview["height"]) == (100, 20)
    assert preview["nodes"][0]["asset_id"] == "alt"
    assert preview["nodes"][1]["text"] == "实例"
    assert set(preview["dependencies"]) == {"button", "alt"}
    assert set(resolved["wrapper"]["dependencies"]) == {"wrapper", "alt", "button"}
    assert control_preview(resolved, "wrapper", {})["nodes"][0]["kind"] == "control"
    assert resolved["alt"]["nodes"][1]["text"] == "默认"
    assert recipes == before


def test_preserves_explicit_equal_variant_intent_in_semantic_digest(button_inputs):
    definitions, recipes, state = button_inputs
    recipes["alt"]["overrides"][0]["value"] = "bg"
    explicit = resolve_recipes(definitions, recipes, state)["alt"]
    recipes["alt"]["overrides"] = []
    inherited = resolve_recipes(definitions, recipes, state)["alt"]
    assert explicit["nodes"] == inherited["nodes"]
    assert explicit["dependencies"]["alt"] != inherited["dependencies"]["alt"]


def test_composite_variant_inherits_nested_dependencies_without_new_source_links(button_inputs):
    definitions, recipes, state = button_inputs
    definitions["wrapper-alt"] = definition(base_control_id="wrapper")
    recipes["wrapper-alt"] = {"kind": "variant", "overrides": []}
    resolved = resolve_recipes(definitions, recipes, state)
    assert resolved["wrapper-alt"]["nodes"] == resolved["wrapper"]["nodes"]
    assert set(resolved["wrapper-alt"]["dependencies"]) == {"wrapper-alt", "wrapper", "alt", "button"}

    definitions["wrapper-alt"]["dependencies"] = ["alt"]
    with pytest.raises(ProjectError, match="wrapper-alt.*dependencies 必须为空"):
        resolve_recipes(definitions, recipes, state)


def test_generated_variant_rejects_external_preview_as_an_editable_source(button_inputs):
    definitions, recipes, state = button_inputs
    recipes["button"]["kind"] = "external"
    with pytest.raises(ProjectError, match="alt.*只能继承工具生成.*button"):
        resolve_recipes(definitions, recipes, state)


@pytest.mark.parametrize("parameters, reason", [
    ({}, "缺少必需参数"), ({"extra": "不支持"}, "未公开参数"),
    ({"caption": 5}, "非空字符串"), ({"caption": ""}, "非空字符串"),
    ({"caption": "A\tB"}, "控制字符"),
])
def test_rejects_invalid_instance_parameters(button_inputs, parameters, reason):
    with pytest.raises(ProjectError, match=reason):
        control_preview(resolve_recipes(*button_inputs), "button", parameters)


@pytest.mark.parametrize("mutate, reason", [
    (lambda definitions, recipes: recipes["button"]["slots"].pop("title"), "制作槽位"),
    (lambda definitions, recipes: recipes["button"]["nodes"][0].update(asset_id="unknown"), "未登记的图像"),
    (lambda definitions, recipes: recipes["button"]["nodes"][1].update(text_style_id="unknown"), "未登记的文字样式"),
    (lambda definitions, recipes: recipes["button"]["button"]["states"].clear(), "公开状态一致"),
    (lambda definitions, recipes: recipes["alt"]["overrides"][0].update(node_id="title"), "无法覆盖节点"),
    (lambda definitions, recipes: definitions["wrapper"].update(dependencies=[]), "dependencies 不一致"),
    (lambda definitions, recipes: definitions["alt"]["layout"].update(max_width=99), "超出公开布局范围"),
    (lambda definitions, recipes: recipes.pop("button"), "缺少制作定义"),
])
def test_rejects_inconsistent_recipe_catalog(button_inputs, mutate, reason):
    definitions, recipes, state = button_inputs
    mutate(definitions, recipes)
    with pytest.raises(ProjectError, match=reason):
        resolve_recipes(definitions, recipes, state)


def test_external_preview_does_not_imply_actual_prefab_dependencies(button_inputs):
    definitions, recipes, state = button_inputs
    recipes["wrapper"]["kind"] = "external"
    definitions["wrapper"]["dependencies"] = ["unbuilt"]
    resolved = resolve_recipes(definitions, recipes, state)
    assert set(resolved["wrapper"]["dependencies"]) == {"wrapper", "alt", "button"}
    recipes["wrapper"]["nodes"][0]["control_id"] = "wrapper"
    with pytest.raises(ProjectError, match="形成循环"):
        resolve_recipes(definitions, recipes, state)


def test_parse_recipe_rejects_unknown_fields_reserved_id_and_duplicate_overrides(button_inputs):
    _, recipes, _ = button_inputs
    with pytest.raises(ProjectError, match="恰好包含"):
        parse_recipe({**recipes["button"], "layout": {}})
    with pytest.raises(ProjectError, match="保留的 root"):
        parse_recipe(recipe(node("image", "root", asset_id="bg")))
    overrides = recipes["alt"]["overrides"]
    with pytest.raises(ProjectError, match="重复声明"):
        parse_recipe({"kind": "variant", "overrides": overrides * 2})
