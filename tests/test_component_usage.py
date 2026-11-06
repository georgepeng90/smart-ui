"""验证组件使用规格、页面实例几何及正式导出的状态覆盖。"""

import pytest

from smartui_mcp.component_usage import check_component_states, resolve_usage, validate_usage


@pytest.fixture
def usage():
    return {
        "padding": {"top": 4, "right": 9, "bottom": 6, "left": 5},
        "text_insets": {"top": 3, "right": 6, "bottom": 4, "left": 7},
        "hit_insets": {"top": -2, "right": -3, "bottom": 1, "left": -4},
        "pivot": {"x": 0.25, "y": 0.75},
        "required_states": ["pressed", "normal", "disabled"],
    }


@pytest.fixture
def project(usage):
    return {"components": {"button": {"spec": {"usage": usage}}, "panel": {"spec": {}}},
            "images": {}}


@pytest.mark.parametrize("path,value,reason", [
    ("", None, "spec.usage"),
    ("padding", {"left": 2, "right": 2}, "padding.*top"),
    ("padding.left", True, "padding.left.*非负整数"),
    ("text_insets.bottom", -1, "text_insets.bottom.*非负整数"),
    ("hit_insets.top", -1.5, "hit_insets.top.*整数"),
    ("pivot.x", float("inf"), "pivot.x.*有限数值"),
    ("pivot.y", 1.1, "pivot.y.*有限数值"),
    ("required_states", [], "required_states"),
    ("required_states", ["normal", "normal"], "required_states"),
    ("required_states", ["normal", "  "], "required_states"),
])
def test_usage_rejects_invalid_declared_fields(usage, path, value, reason):
    if not path:
        usage = value
    elif "." in path:
        field, child = path.split(".")
        usage[field][child] = value
    else:
        usage[path] = value
    with pytest.raises(ValueError, match=reason):
        validate_usage(usage)


def test_validated_usage_is_an_independent_copy(usage):
    copy = validate_usage(usage)
    assert copy == usage
    copy["padding"]["top"] = 0
    copy["required_states"].append("selected")
    assert usage["padding"]["top"] == 4
    assert "selected" not in usage["required_states"]


def test_usage_geometry_uses_current_instance_box(project):
    first = resolve_usage(project, "button", [100, 50, 80, 40])
    assert first == {
        "padding": {"top": 4, "right": 9, "bottom": 6, "left": 5},
        "text_rect": [107, 53, 67, 33],
        "hit_rect": [96, 48, 87, 41],
        "pivot": {"x": 120, "y": 80},
        "required_states": ["pressed", "normal", "disabled"],
    }
    larger = resolve_usage(project, "button", [100, 50, 120, 64])
    assert larger["text_rect"] == [107, 53, 107, 57]
    assert larger["hit_rect"] == [96, 48, 127, 65]
    assert larger["pivot"] == {"x": 130, "y": 98}


@pytest.mark.parametrize("field", ["padding", "text_insets", "hit_insets"])
def test_usage_rejects_an_empty_resolved_region(project, usage, field):
    usage[field].update(left=6, right=4)
    for key in {"padding", "text_insets", "hit_insets"} - {field}:
        usage[key].update(left=0, right=0)
    with pytest.raises(ValueError, match=rf"组件 button.*spec.usage.{field}.*正宽高"):
        resolve_usage(project, "button", [0, 0, 10, 40])


def test_group_requires_existing_component_with_usage(project):
    for component_id, reason in (("absent", "组件不存在：absent"), ("panel", "panel 未声明 spec.usage")):
        with pytest.raises(ValueError, match=reason):
            resolve_usage(project, component_id, [0, 0, 20, 20])


def test_state_check_tracks_only_current_exports_from_this_component(project):
    def export(state, component_id):
        return {"state": state, "provenance": {"kind": "component_export", "source": {"component_id": component_id}}}

    images = project["images"]
    images.update({
        "button-main": export("normal", "button"),
        "button-decoration": export("normal", "button"),
        "panel-press": export("pressed", "panel"),
        "independent": {"state": "disabled", "provenance": {"kind": "generated"}},
    })
    assert check_component_states(project, "button", {"button-decoration": {}}) == {
        "required": ["pressed", "normal", "disabled"], "missing": ["pressed", "disabled"], "stale": [],
        "available": ["normal"],
    }
    assert check_component_states(project, "panel", {}) == {
        "required": [], "missing": [], "stale": [], "available": ["pressed"]}

    images["button-main"] = export("pressed", "button")
    images["button-decoration"] = export("disabled", "button")
    assert check_component_states(project, "button", {"button-main": {}}) == {
        "required": ["pressed", "normal", "disabled"], "missing": ["normal"], "stale": ["pressed"],
        "available": ["disabled"],
    }
